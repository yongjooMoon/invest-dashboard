# 필요 라이브러리 설치:
# pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib supabase

import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from supabase import create_client, Client

# ==========================================
# 1. 환경 변수 및 설정
# ==========================================
# Supabase 설정
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# Google API 설정 (OAuth 2.0 방식)
# 서비스 계정이 아닌 '본인 계정'으로 직접 접근하기 위한 설정입니다.
CLIENT_SECRET_FILE = 'credentials.json'
SCOPES = [
    'https://www.googleapis.com/auth/documents.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

DOCUMENT_NAME_KEYWORD = "Daily AI News Brief"


# ==========================================
# 2. 구글 인증 (OAuth 2.0) 함수
# ==========================================
def get_google_credentials():
    """사용자(본인) 계정으로 인증하고 크레덴셜을 반환합니다."""
    creds = None
    # token.json 파일에는 사용자의 액세스 토큰과 리프레시 토큰이 저장됩니다.
    # 처음 실행 시 로그인하면 생성되며, 이후로는 자동으로 토큰을 갱신합니다.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # 유효한 크레덴셜이 없으면 로그인 진행
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES)
            # 처음 실행 시 웹 브라우저가 열리며 구글 로그인 창이 뜹니다.
            creds = flow.run_local_server(port=0)

        # 다음 실행을 위해 토큰 저장
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

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

        # [수정된 부분] AI가 출력한 헤더(컬럼명) 줄인 경우 건너뛰기
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
# 6. 메인 실행 블록
# ==========================================
if __name__ == '__main__':
    print("인증을 진행합니다 (필요시 브라우저가 열립니다)...")
    creds = get_google_credentials()

    print("\n1. 드라이브에서 가장 최신 뉴스 문서를 찾습니다...")
    latest_doc_id = get_latest_doc_id_from_drive(creds)

    if latest_doc_id:
        print(f"2. 구글 문서({latest_doc_id})에서 데이터를 읽어옵니다...")
        doc_text = get_google_doc_text(latest_doc_id, creds)

        if doc_text:
            print("3. 데이터 파싱 및 DB 저장을 시작합니다...")
            process_and_insert_data(doc_text)
    else:
        print("작업을 종료합니다 (문서 ID를 찾지 못함).")
