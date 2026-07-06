import streamlit as st
import os
import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from supabase import create_client, Client
from datetime import datetime, timedelta

# 🌟 Streamlit Cloud의 잘못된 프록시 환경 변수 간섭을 원천 차단하여 에러 해결
for env_key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if env_key in os.environ:
        del os.environ[env_key]

# ==========================================
# 1. 환경 변수 및 설정
# ==========================================
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# 구조 변경된 1차원 st.secrets 키 적용 완료
GOOGLE_CLIENT_ID = st.secrets["client_id"]
GOOGLE_CLIENT_SECRET = st.secrets["client_secret"]
GOOGLE_REFRESH_TOKEN = st.secrets["refresh_token"]

SCOPES = [
    'https://www.googleapis.com/auth/documents.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

DOCUMENT_NAME_KEYWORD = "Daily AI News Brief"


# ==========================================
# 2. 구글 인증 (OAuth 2.0 - 메모리 캐싱 방식)
# ==========================================
def get_google_credentials():
    token_uri = "https://oauth2.googleapis.com/token"
    creds = Credentials(
        token=None, 
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri=token_uri,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES
    )

    if not creds.valid:
        # 🌟 시스템 프록시 환경변수를 완전히 무시하도록 빈 세션을 강제 바인딩 (에러 방어용)
        session = requests.Session()
        session.trust_env = False
        creds.refresh(Request(session=session))
    return creds


# ==========================================
# 3. 구글 드라이브에서 가장 최신 문서 ID 찾기
# ==========================================
def get_latest_doc_id_from_drive(creds):
    try:
        drive_service = build('drive', 'v3', credentials=creds)
        query = f"name contains '{DOCUMENT_NAME_KEYWORD}' and mimeType='application/vnd.google-apps.document' and trashed=false"

        results = drive_service.files().list(
            q=query, orderBy="createdTime desc", pageSize=1, fields="files(id, name, createdTime)"
        ).execute()

        files = results.get('files', [])
        if not files: return None
        return files[0]['id']
    except Exception as e:
        print(f"[오류] 드라이브 검색 실패: {e}")
        return None


# ==========================================
# 4. 구글 문서 텍스트 추출 함수
# ==========================================
def get_google_doc_text(doc_id, creds):
    try:
        service = build('docs', 'v1', credentials=creds)
        doc = service.documents().get(documentId=doc_id).execute()

        text_content = ""
        for element in doc.get('body').get('content'):
            if 'paragraph' in element:
                elements = element.get('paragraph').get('elements')
                for elem in elements:
                    if 'textRun' in elem:
                        text_content += elem.get('textRun').get('content')
        return text_content
    except Exception as e:
        print(f"[오류] 문서 읽기 실패: {e}")
        return None


# ==========================================
# 5. 데이터 파싱 및 Supabase Insert 함수 (신규 포맷 7컬럼 적용)
# ==========================================
def process_and_insert_data(raw_text):
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    lines = raw_text.split('\n')
    inserted_count = 0
    skipped_count = 0

    for line in lines:
        line = line.strip()
        if not line or '|' not in line:
            continue

        columns = line.split('|')

        if columns[0].strip().lower() == 'region':
            continue

        if len(columns) == 7:
            try:
                region = columns[0].strip()
                sector = columns[1].strip()
                title = columns[2].strip()
                summary = columns[3].strip()
                score = int(columns[4].strip())
                
                is_major = True if 'true' in columns[5].strip().lower() else False
                
                time_str = columns[6].strip()
                try:
                    parsed_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
                    created_at_iso = parsed_time.isoformat()
                except Exception as e:
                    print(f"시간 파싱 오류 (현재시간으로 대체): {time_str}")
                    created_at_iso = datetime.utcnow().isoformat()

                # DB 중복 체크 (제목 기준)
                existing_data = supabase.table("market_news").select("id").eq("title", title).execute()

                if len(existing_data.data) > 0:
                    print(f"⏩ 중복 건너뜀: {title[:20]}...")
                    skipped_count += 1
                    continue

                data = {
                    "region": region,
                    "sector_asset": sector,
                    "title": title,
                    "summary": summary,
                    "sentiment_score": score,
                    "is_major": is_major,
                    "created_at": created_at_iso
                }

                supabase.table("market_news").insert(data).execute()
                inserted_count += 1
                print(f"✅ 저장 성공: {title[:20]}... (Major: {is_major})")

            except Exception as e:
                print(f"❌ 데이터 Insert 실패 ({line}): {e}")
        else:
            print(f"⚠️ 형식 불일치로 건너뜀 (열 개수: {len(columns)}): {line}")

    print(f"\n🎉 완료! 새로 저장됨: {inserted_count}개 | 중복 스킵: {skipped_count}개")


# ==========================================
# 6. 외부 배치(Scheduler) 및 수동 호출용 메인 함수
# ==========================================
def run_sync():
    print("\n[배치 실행] 구글 드라이브 문서 수합을 시작합니다...")
    creds = get_google_credentials()
    
    if creds:
        doc_id = get_latest_doc_id_from_drive(creds)
        if doc_id:
            doc_text = get_google_doc_text(doc_id, creds)
            if doc_text:
                process_and_insert_data(doc_text)

if __name__ == '__main__':
    run_sync()
