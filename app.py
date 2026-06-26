import streamlit as st
from supabase import create_client, Client
import hashlib
import re
import real_estate
import stock_quant

# --- [1. Supabase 연동 설정] ---
SUPABASE_URL = "https://unvcqrjzvtgtjovfyvow.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVudmNxcmp6dnRndGpvdmZ5dm93Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1NDM5MjEsImV4cCI6MjA5NjExOTkyMX0.XWhOYvFlO3z0lVU57tIjQDbGVUFyHTv3niLsV2ZUeJ4"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 초기 사이드바 상태를 확장으로 두되, CSS로 너비를 강제 축소
st.set_page_config(page_title="토스 스타일 퀀트 대시보드", page_icon="✨", layout="wide", initial_sidebar_state="expanded")

# --- 슬림형 사이드바(Left Menu)를 위한 전역 CSS 주입 ---
st.markdown("""
<style>
    /* 사이드바의 너비를 아주 얇게 강제 고정 (아이콘 전용 사이즈) */
    section[data-testid="stSidebar"] {
        width: 80px !important;
        min-width: 80px !important;
        max-width: 80px !important;
        background-color: #f8f9fa; /* 약간의 배경색 */
    }
    
    /* 사이드바 내부 여백 제거하여 버튼을 중앙으로 */
    section[data-testid="stSidebar"] .block-container {
        padding: 2rem 0.5rem !important;
    }
    
    /* 버튼 텍스트(이모지) 크기 조절 */
    section[data-testid="stSidebar"] .stButton > button {
        font-size: 22px !important;
        height: 50px !important;
        padding: 0 !important;
        border-radius: 12px !important;
    }
    
    /* 상단 빈 여백 축소 */
    .block-container {
        padding-top: 1.5rem !important;
    }
</style>
""", unsafe_allow_html=True)


# --- [2. 글로벌 세션 (서버 재시작 전까지 영구 유지) 설정] ---
@st.cache_resource
def get_global_session():
    # 서버 메모리에 상주하는 글로벌 딕셔너리입니다. (새로고침, 탭 닫힘 방어)
    return {
        "logged_in": False,
        "username": None,
        "api_keys": {
            "rtms_key": "", 
            "app_key": "", 
            "app_secret": "", 
            "naver_id": "", 
            "naver_secret": ""
        }
    }

global_session = get_global_session()

# Streamlit의 휘발성 session_state가 초기화되었더라도, 글로벌 세션에서 값을 복구해 옵니다.
if "logged_in" not in st.session_state:
    st.session_state.logged_in = global_session["logged_in"]
if "username" not in st.session_state:
    st.session_state.username = global_session["username"]
if "api_keys" not in st.session_state:
    st.session_state.api_keys = global_session["api_keys"]
if "current_view" not in st.session_state:
    st.session_state.current_view = "main"
if "current_menu" not in st.session_state:
    st.session_state.current_menu = "quant"

# 헬퍼 함수: 로그인/로그아웃 시 휘발성 세션과 서버 영구 세션을 동시에 업데이트
def update_auth_state(is_logged_in, username, api_keys=None):
    st.session_state.logged_in = is_logged_in
    global_session["logged_in"] = is_logged_in
    
    st.session_state.username = username
    global_session["username"] = username
    
    if api_keys:
        st.session_state.api_keys = api_keys
        global_session["api_keys"] = api_keys
    else:
        empty_keys = {"rtms_key": "", "app_key": "", "app_secret": "", "naver_id": "", "naver_secret": ""}
        st.session_state.api_keys = empty_keys
        global_session["api_keys"] = empty_keys


# --- [3. 로그인 전용 단일 UI] ---
if not st.session_state.logged_in:
    # 로그인 창 타이틀
    st.title("✨ 투자 자산 대시보드")
    st.markdown("인증된 계정만 접근 가능한 내부 투자 자산 관리 데스크입니다.")
    
    # st.form을 사용하여 엔터(Enter) 키로 로그인(Submit) 가능하도록 구현
    with st.form("login_form"):
        login_username = st.text_input("아이디", key="login_id")
        login_pw = st.text_input("비밀번호", type="password")
        
        submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
        
        if submitted:
            # 한글 입력 방지 로직
            if re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', login_username):
                st.error("🚨 아이디에는 한글을 입력할 수 없습니다. 영문과 숫자만 입력해 주세요.")
            elif login_username.strip() == "" or login_pw.strip() == "":
                st.warning("아이디와 비밀번호를 모두 입력해 주세요.")
            else:            
                try:
                    # 커스텀 테이블(custom_users)에서 아이디와 해시 비밀번호가 일치하는지 조회
                    user_query = supabase.table("custom_users").select("*").eq("username", login_username).eq("password_hash", login_pw).execute()
                    
                    if user_query.data:
                        # 개별 유저 키가 아닌 'admin' 공통 마스터 API 크레덴셜 정보 로드
                        admin_keys = supabase.table("user_api_keys").select("*").eq("username", "admin").execute()
                        
                        keys_to_save = {}
                        if admin_keys.data:
                            keys_to_save = {
                                "rtms_key": admin_keys.data[0].get("rtms_key", ""),
                                "app_key": admin_keys.data[0].get("app_key", ""),
                                "app_secret": admin_keys.data[0].get("app_secret", ""),
                                "naver_id": admin_keys.data[0].get("naver_id", ""),
                                "naver_secret": admin_keys.data[0].get("naver_secret", "")
                            }
                        else:
                            # admin 키가 아예 없다면 초기 생성
                            supabase.table("user_api_keys").insert({
                                "username": "admin", 
                                "rtms_key": "", "app_key": "", "app_secret": "", "naver_id": "", "naver_secret": ""
                            }).execute()
                            keys_to_save = {"rtms_key": "", "app_key": "", "app_secret": "", "naver_id": "", "naver_secret": ""}
                        
                        # [핵심] 로그인 성공 시 글로벌 세션까지 튼튼하게 기록
                        update_auth_state(True, login_username, keys_to_save)
                        st.session_state.current_view = "main"
                        st.session_state.current_menu = "quant"
                        
                        st.rerun()
                    else:
                        st.error("❌ 등록되지 않은 계정이거나 비밀번호가 일치하지 않습니다.")
                except Exception as e:
                    st.error(f"시스템 데이터베이스 통신 장애: {str(e)}")
    st.stop()


# --- [4. 로그인 성공 후 프레임워크 가동 (Slim Left Menu 구조)] ---

# 글자 없이 이모지와 툴팁(help)만 사용하는 슬림한 사이드바
with st.sidebar:
    st.write("") # 상단 여백
    
    # 1. 퀀트 메뉴 버튼
    is_quant = st.session_state.current_menu == "quant" and st.session_state.current_view == "main"
    if st.button("📈", help="주식 포트폴리오 퀀트", use_container_width=True, type="primary" if is_quant else "secondary"):
        st.session_state.current_menu = "quant"
        st.session_state.current_view = "main"
        st.rerun()
        
    st.write("") # 간격 띄우기
    
    # 2. 부동산 메뉴 버튼
    is_real_estate = st.session_state.current_menu == "real_estate" and st.session_state.current_view == "main"
    if st.button("🏢", help="부동산 실거래가 스캔", use_container_width=True, type="primary" if is_real_estate else "secondary"):
        st.session_state.current_menu = "real_estate"
        st.session_state.current_view = "main"
        st.rerun()
        
    # 3. Admin 계정일 경우에만 API 설정 아이콘 노출
    if st.session_state.username == "admin":
        st.write("") # 간격 띄우기
        is_api_view = st.session_state.current_view == "api_settings"
        if st.button("⚙️", help="시스템 공통 API 설정", use_container_width=True, type="primary" if is_api_view else "secondary"):
            st.session_state.current_view = "api_settings"
            st.rerun()
            
    # 하단으로 버튼을 밀어내기 위한 빈 공간 확보
    st.markdown("<div style='height: 50vh;'></div>", unsafe_allow_html=True)
    
    # 4. 로그아웃 버튼
    if st.button("🔓", help=f"현재 접속자: {st.session_state.username}님\n(클릭 시 로그아웃)", use_container_width=True):
        # [핵심] 로그아웃 시 글로벌 세션까지 깨끗하게 삭제
        update_auth_state(False, None)
        st.session_state.current_view = "main"
        st.rerun()


# --- [5. 화면 라우팅 (API 설정 화면 vs 퀀트/부동산 메인 화면)] ---

# 메인 콘텐츠 영역 (사이드바 우측 넓은 공간)
if st.session_state.current_view == "api_settings" and st.session_state.username == "admin":
    # API 키 자산 설정 (Admin 전용 공통 키 관리)
    st.title("⚙️ 시스템 공통 API 크레덴셜 관리")
    st.markdown("전체 시스템이 공통으로 사용하는 마스터 API 키를 설정합니다. (**Admin 전용**)")
    
    rtms = st.text_input("1. 국토교통부 실거래 API Key (Decoding)", value=st.session_state.api_keys["rtms_key"], type="password")
    a_key = st.text_input("2. 한국투자증권 오픈 API App Key (시세/수급용)", value=st.session_state.api_keys["app_key"])
    a_sec = st.text_input("3. 한국투자증권 오픈 API App Secret (시세/수급용)", value=st.session_state.api_keys["app_secret"], type="password")
    n_id = st.text_input("4. 네이버 오픈 API Client ID (뉴스 호재 분석용)", value=st.session_state.api_keys["naver_id"])
    n_sec = st.text_input("5. 네이버 오픈 API Client Secret (뉴스 호재 분석용)", value=st.session_state.api_keys["naver_secret"], type="password")
    
    if st.button("마스터 크레덴셜 업데이트", type="primary"):
        try:
            # 개별 username이 아닌 'admin' 공통 원장에 반영
            supabase.table("user_api_keys").upsert({
                "username": "admin",
                "rtms_key": rtms,
                "app_key": a_key,
                "app_secret": a_sec,
                "naver_id": n_id,
                "naver_secret": n_sec
            }).execute()
            
            # 업데이트 시 글로벌 세션 최신화
            updated_keys = {"rtms_key": rtms, "app_key": a_key, "app_secret": a_sec, "naver_id": n_id, "naver_secret": n_sec}
            st.session_state.api_keys = updated_keys
            global_session["api_keys"] = updated_keys
            
            st.success("✅ 마스터 5대 보안 자산 데이터가 시스템에 무결점 반영되었습니다.")
        except Exception as e:
            st.error(f"저장 실패: {str(e)}")

else:
    # Left Menu 버튼 선택(상태)에 따라 해당 모듈만 깔끔하게 렌더링
    if st.session_state.current_menu == "quant":
        stock_quant.run_stock_quant_page(supabase, st.session_state.username)
    elif st.session_state.current_menu == "real_estate":
        real_estate.run_real_estate_page(st.session_state.api_keys["rtms_key"])
