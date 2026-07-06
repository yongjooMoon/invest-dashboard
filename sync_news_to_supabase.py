import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from supabase import create_client, Client

# ==========================================
# 1. 환경 변수 및 설정
# ==========================================
# Supabase 설정
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# 파일 대신 st.secrets에서 구글 인증 정보 가져오기
GOOGLE_CLIENT_ID = st.secrets["GOOGLE_OAUTH"]["client_id"]
GOOGLE_CLIENT_SECRET = st.secrets["GOOGLE_OAUTH"]["client_secret"]
GOOGLE_REFRESH_TOKEN = st.secrets["GOOGLE_OAUTH"]["refresh_token"]

SCOPES = [
    'https://www.googleapis.com/auth/documents.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

DOCUMENT_NAME_KEYWORD = "Daily AI News Brief"


# ==========================================
# 2. 구글 인증 (OAuth 2.0 - 메모리 캐싱 방식)
# ==========================================
def get_google_credentials():
    """파일 없이 st.secrets의 값만으로 인증 객체를 생성하고, 만료 시 자동 갱신합니다."""
    token_uri = "https://oauth2.googleapis.com/token"

    # 로컬 파일을 읽고 쓰는 과정 없이, 자격증명(Credentials) 객체를 즉석에서 생성합니다.
    # access_token(token)은 None으로 비워두고 refresh_token만 넣으면, 알아서 새 토큰을 받아옵니다.
    creds = Credentials(
        token=None, 
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri=token_uri,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES
    )

    # 강제로 유효성 검사 후 새 Access Token으로 갱신
    if not creds.valid:
        creds.refresh(Request())

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
        if not files:
            print(f"'{DOCUMENT_NAME_KEYWORD}' 키워드가 포함된 문서를 찾을 수 없습니다.")
            return None

        latest_file = files[0]
        print(f"최신 문서를 찾았습니다: {latest_file['name']} (생성일시: {latest_file['createdTime']})")
        return latest_file['id']

    except Exception as e:
        print(f"[오류] 드라이브에서 문서를 검색하는데 실패했습니다: {e}")
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
        print(f"[오류] 구글 문서를 읽어오는데 실패했습니다: {e}")
        return None


# ==========================================
# 5. 데이터 파싱 및 Supabase Insert 함수
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

        if len(columns) == 5:
            try:
                region = columns[0].strip()
                sector = columns[1].strip()
                title = columns[2].strip()
                summary = columns[3].strip()
                score = int(columns[4].strip())

                existing_data = supabase.table("market_news").select("id").eq("title", title).execute()

                if len(existing_data.data) > 0:
                    print(f"⏩ 중복 건너뜀 (이미 저장됨): {title[:20]}...")
                    skipped_count += 1
                    continue

                data = {
                    "region": region,
                    "sector_asset": sector,
                    "title": title,
                    "summary": summary,
                    "sentiment_score": score
                }

                supabase.table("market_news").insert(data).execute()
                inserted_count += 1
                print(f"✅ 저장 성공: {title[:20]}...")

            except Exception as e:
                print(f"❌ 데이터 Insert 실패 ({line}): {e}")
        else:
            print(f"⚠️ 형식 불일치로 건너뜀 (열 개수: {len(columns)}): {line}")

    print(f"\n🎉 완료! 새로 저장됨: {inserted_count}개 | 중복 스킵: {skipped_count}개")


# ==========================================
# 6. 외부 배치(Scheduler) 호출용 메인 함수
# ==========================================
def run_sync():
    """app.py 스케줄러에서 이 함수를 호출하여 백그라운드로 실행합니다."""
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
