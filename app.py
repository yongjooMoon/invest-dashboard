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

# 초기 설정
st.set_page_config(page_title="EVAN TRADERS", page_icon="📈", layout="wide", initial_sidebar_state="expanded")

# --- [2. 글로벌 세션 (서버 영구 유지) 설정] ---
@st.cache_resource
def get_global_session():
    return {
        "logged_in": False,
        "username": None,
        "api_keys": {
            "rtms_key": "", "app_key": "", "app_secret": "", "naver_id": "", "naver_secret": ""
        }
    }

global_session = get_global_session()

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

# --- [3. 로그인 화면 (트렌디 & 모던 UI)] ---
if not st.session_state.logged_in:
    # 로그인 화면 전용 커스텀 CSS 강제 주입
    st.markdown("""
    <style>
    /* 배경 화면 아주 어두운 톤으로 */
    .stApp {
        background-color: #0B0E14 !important;
    }
    
    /* 기존 헤더, 사이드바 숨김 */
    [data-testid="stSidebar"], [data-testid="stHeader"] {
        display: none !important;
    }

    /* 화면 가운데 완벽 정렬 */
    [data-testid="stAppViewContainer"] {
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
        padding: 0 !important;
    }
    .block-container {
        padding: 0 !important;
        max-width: 100% !important;
        display: flex;
        justify-content: center;
        align-items: center;
        width: 100%;
    }

    /* 폼 전체 래퍼 박스 설정 (이미지처럼 깔끔한 모서리와 입체감) */
    [data-testid="stForm"] {
        background-color: #12151A !important;
        border: 1px solid #1E2329 !important;
        border-radius: 12px !important;
        padding: 2.5rem 3rem 1.5rem 3rem !important;
        width: 360px !important;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.9) !important;
        position: relative;
        z-index: 10;
        margin: 0 auto;
    }

    /* 입력창 라벨 (USERNAME, PASSWORD) */
    .stTextInput label p {
        color: #6A7381 !important;
        font-size: 10px !important;
        font-weight: 700 !important;
        letter-spacing: 1px;
    }

    /* 입력창 디자인 */
    .stTextInput input {
        background-color: #1A1F26 !important;
        color: #FFFFFF !important;
        border: 1px solid #2A313C !important;
        border-radius: 6px !important;
        padding: 0.8rem 1rem !important;
    }
    .stTextInput input:focus {
        border-color: #20C997 !important;
        box-shadow: 0 0 0 1px #20C997 !important;
    }

    /* 로그인 버튼 */
    [data-testid="stFormSubmitButton"] button {
        background-color: #20C997 !important;
        color: #000000 !important;
        font-weight: 800 !important;
        letter-spacing: 1px !important;
        border-radius: 6px !important;
        border: none !important;
        margin-top: 1.5rem !important;
        padding: 0.6rem !important;
        transition: all 0.3s ease !important;
    }
    [data-testid="stFormSubmitButton"] button:hover {
        background-color: #18A87D !important;
        box-shadow: 0 0 15px rgba(32, 201, 151, 0.4) !important;
    }
    [data-testid="stFormSubmitButton"] button p {
        font-size: 13px !important;
    }

    /* 로고 컨테이너 (EVAN TRADERS) */
    .logo-container {
        text-align: center;
        margin-bottom: 2rem;
    }
    .logo-box {
        width: 64px;
        height: 64px;
        margin: 0 auto 15px auto;
        border: 2px solid #E2E8F0;
        border-radius: 12px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        color: #E2E8F0;
        background-color: #161A20;
    }
    .logo-box .top { font-family: 'Arial Black', sans-serif; font-size: 26px; line-height: 1; letter-spacing: -2px; margin-top: 5px; }
    .logo-box .bottom { font-family: 'Arial', sans-serif; font-size: 7px; font-weight: bold; letter-spacing: 1px; margin-top: -2px; }
    
    .logo-title {
        color: #FFFFFF;
        font-size: 16px;
        font-weight: 800;
        letter-spacing: 1px;
        margin: 0 0 5px 0;
    }
    .logo-subtitle {
        color: #20C997;
        font-size: 9px;
        font-weight: 700;
        letter-spacing: 1.5px;
        margin: 0;
    }

    /* 카피라이트 텍스트 */
    .copyright-text {
        text-align: center;
        color: #3B4252;
        font-size: 9px;
        margin-top: 1.5rem;
        letter-spacing: 0.5px;
    }

    /* 하단 애니메이션 웨이브 (기하학적 네온 라인) */
    .neon-wave {
        position: fixed;
        bottom: 0;
        left: 0;
        width: 200vw;
        height: 40vh;
        background-image: url("data:image/svg+xml,%3Csvg width='1440' height='300' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M0 150 L 300 130 L 700 240 L 1100 160 L 1440 140' stroke='rgba(32,201,151,0.5)' stroke-width='10' fill='none' style='filter: blur(10px);' /%3E%3Cpath d='M0 150 L 300 130 L 700 240 L 1100 160 L 1440 140' stroke='%2320C997' stroke-width='2' fill='none' /%3E%3Cpath d='M0 150 L 300 130 L 700 240 L 1100 160 L 1440 140 L 1440 300 L 0 300 Z' fill='url(%23grad)' opacity='0.15' /%3E%3Cdefs%3E%3ClinearGradient id='grad' x1='0' y1='0' x2='0' y2='1'%3E%3Cstop offset='0%25' stop-color='%2320C997' /%3E%3Cstop offset='100%25' stop-color='%230B0E14' stop-opacity='0' /%3E%3C/linearGradient%3E%3C/defs%3E%3C/svg%3E");
        background-repeat: repeat-x;
        background-size: 1440px 100%;
        animation: moveWave 25s linear infinite;
        pointer-events: none;
        z-index: 0;
    }
    @keyframes moveWave {
        0% { transform: translateX(0); }
        100% { transform: translateX(-1440px); }
    }
    </style>
    
    <!-- 움직이는 백그라운드 웨이브 주입 -->
    <div class="neon-wave"></div>
    """, unsafe_allow_html=True)

    with st.form("login_form", clear_on_submit=True):
        # 폼 내부에 트렌디한 로고 렌더링
        st.markdown("""
        <div class="logo-container">
            <div class="logo-box">
                <div class="top">ER</div>
                <div class="bottom">TRADERS</div>
            </div>
            <h3 class="logo-title">EVAN TRADERS</h3>
            <p class="logo-subtitle">QUANT TRADING SYSTEM</p>
        </div>
        """, unsafe_allow_html=True)
        
        login_username = st.text_input("USERNAME", key="login_id")
        login_pw = st.text_input("PASSWORD", type="password")
        
        submitted = st.form_submit_button("LOGIN", type="primary", use_container_width=True)
        
        # 저작권 텍스트
        st.markdown("<p class='copyright-text'>© 2024 EVAN TRADERS</p>", unsafe_allow_html=True)
        
        if submitted:
            if re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', login_username):
                st.error("🚨 아이디에는 한글을 입력할 수 없습니다. 영문과 숫자만 입력해 주세요.")
            elif login_username.strip() == "" or login_pw.strip() == "":
                st.warning("아이디와 비밀번호를 모두 입력해 주세요.")
            else:            
                try:
                    user_query = supabase.table("custom_users").select("*").eq("username", login_username).eq("password_hash", login_pw).execute()
                    if user_query.data:
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
                            supabase.table("user_api_keys").insert({
                                "username": "admin", "rtms_key": "", "app_key": "", "app_secret": "", "naver_id": "", "naver_secret": ""
                            }).execute()
                            keys_to_save = {"rtms_key": "", "app_key": "", "app_secret": "", "naver_id": "", "naver_secret": ""}
                        
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

st.markdown("""
<style>
    /* 메인 화면 슬림 사이드바 CSS */
    section[data-testid="stSidebar"] {
        width: 80px !important;
        min-width: 80px !important;
        max-width: 80px !important;
        background-color: #f8f9fa;
    }
    section[data-testid="stSidebar"] .block-container {
        padding: 2rem 0.5rem !important;
    }
    section[data-testid="stSidebar"] .stButton > button {
        font-size: 22px !important;
        height: 50px !important;
        padding: 0 !important;
        border-radius: 12px !important;
    }
    .block-container {
        padding-top: 1.5rem !important;
    }
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.write("") 
    
    is_quant = st.session_state.current_menu == "quant" and st.session_state.current_view == "main"
    if st.button("📈", help="주식 포트폴리오 퀀트", use_container_width=True, type="primary" if is_quant else "secondary"):
        st.session_state.current_menu = "quant"
        st.session_state.current_view = "main"
        st.rerun()
        
    st.write("") 
    
    is_real_estate = st.session_state.current_menu == "real_estate" and st.session_state.current_view == "main"
    if st.button("🏢", help="부동산 실거래가 스캔", use_container_width=True, type="primary" if is_real_estate else "secondary"):
        st.session_state.current_menu = "real_estate"
        st.session_state.current_view = "main"
        st.rerun()
        
    if st.session_state.username == "admin":
        st.write("")
        is_api_view = st.session_state.current_view == "api_settings"
        if st.button("⚙️", help="시스템 공통 API 설정", use_container_width=True, type="primary" if is_api_view else "secondary"):
            st.session_state.current_view = "api_settings"
            st.rerun()
            
    st.markdown("<div style='height: 50vh;'></div>", unsafe_allow_html=True)
    
    if st.button("🔓", help=f"현재 접속자: {st.session_state.username}님\n(클릭 시 로그아웃)", use_container_width=True):
        update_auth_state(False, None)
        st.session_state.current_view = "main"
        st.rerun()

# --- [5. 화면 라우팅 (API 설정 화면 vs 퀀트/부동산 메인 화면)] ---

if st.session_state.current_view == "api_settings" and st.session_state.username == "admin":
    st.title("⚙️ 시스템 공통 API 크레덴셜 관리")
    st.markdown("전체 시스템이 공통으로 사용하는 마스터 API 키를 설정합니다. (**Admin 전용**)")
    
    rtms = st.text_input("1. 국토교통부 실거래 API Key (Decoding)", value=st.session_state.api_keys["rtms_key"], type="password")
    a_key = st.text_input("2. 한국투자증권 오픈 API App Key (시세/수급용)", value=st.session_state.api_keys["app_key"])
    a_sec = st.text_input("3. 한국투자증권 오픈 API App Secret (시세/수급용)", value=st.session_state.api_keys["app_secret"], type="password")
    n_id = st.text_input("4. 네이버 오픈 API Client ID (뉴스 호재 분석용)", value=st.session_state.api_keys["naver_id"])
    n_sec = st.text_input("5. 네이버 오픈 API Client Secret (뉴스 호재 분석용)", value=st.session_state.api_keys["naver_secret"], type="password")
    
    if st.button("마스터 크레덴셜 업데이트", type="primary"):
        try:
            supabase.table("user_api_keys").upsert({
                "username": "admin",
                "rtms_key": rtms,
                "app_key": a_key,
                "app_secret": a_sec,
                "naver_id": n_id,
                "naver_secret": n_sec
            }).execute()
            
            updated_keys = {"rtms_key": rtms, "app_key": a_key, "app_secret": a_sec, "naver_id": n_id, "naver_secret": n_sec}
            st.session_state.api_keys = updated_keys
            global_session["api_keys"] = updated_keys
            
            st.success("✅ 마스터 5대 보안 자산 데이터가 시스템에 무결점 반영되었습니다.")
        except Exception as e:
            st.error(f"저장 실패: {str(e)}")

else:
    if st.session_state.current_menu == "quant":
        stock_quant.run_stock_quant_page(supabase, st.session_state.username)
    elif st.session_state.current_menu == "real_estate":
        real_estate.run_real_estate_page(st.session_state.api_keys["rtms_key"])
