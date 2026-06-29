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
st.set_page_config(page_title="QUANT DESK", page_icon="✨", layout="wide", initial_sidebar_state="expanded")

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

# --- [3. 로그인 화면 (오로라 글래스모피즘 UI)] ---
if not st.session_state.logged_in:
    # 로그인 화면에만 적용되는 최신 트렌드 CSS 강제 주입
    st.markdown("""
    <style>
    /* 전체 배경: 칠흑 같은 우주 공간 */
    .stApp {
        background-color: #030712 !important;
        overflow: hidden !important;
    }
    
    /* 기존 헤더, 사이드바 완전 숨김 */
    [data-testid="stSidebar"], [data-testid="stHeader"] {
        display: none !important;
    }

    /* 화면 가운데 정렬 */
    .block-container {
        padding: 0 !important;
        max-width: 100% !important;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        min-height: 100vh;
    }

    /* 🌟 오로라 메쉬 그라디언트 애니메이션 🌟 */
    .aurora-bg {
        position: fixed;
        top: 0; left: 0; width: 100vw; height: 100vh;
        z-index: 0;
        overflow: hidden;
        background-color: #030712;
    }
    .orb {
        position: absolute;
        border-radius: 50%;
        filter: blur(100px);
        opacity: 0.6;
        animation: float 20s infinite ease-in-out alternate;
    }
    .orb-1 {
        width: 50vw; height: 50vw;
        background: #20C997; /* 몽환적인 민트 */
        top: -20%; left: -10%;
        animation-delay: 0s;
    }
    .orb-2 {
        width: 40vw; height: 40vw;
        background: #3B82F6; /* 깊은 블루 */
        bottom: -20%; right: -10%;
        animation-delay: -5s;
    }
    .orb-3 {
        width: 40vw; height: 40vw;
        background: #8B5CF6; /* 신비로운 퍼플 */
        top: 30%; left: 40%;
        animation-delay: -10s;
    }
    @keyframes float {
        0% { transform: translate(0, 0) scale(1) rotate(0deg); }
        33% { transform: translate(5vw, -5vh) scale(1.1) rotate(10deg); }
        66% { transform: translate(-5vw, 5vh) scale(0.9) rotate(-10deg); }
        100% { transform: translate(0, 0) scale(1) rotate(0deg); }
    }

    /* 💎 글래스모피즘 로그인 박스 💎 */
    [data-testid="stForm"] {
        background: rgba(15, 23, 42, 0.4) !important;
        backdrop-filter: blur(30px) !important;
        -webkit-backdrop-filter: blur(30px) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 24px !important;
        padding: 3.5rem 3rem 2.5rem 3rem !important;
        width: 400px !important;
        box-shadow: 0 30px 60px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255,255,255,0.1) !important;
        position: relative;
        z-index: 10;
        margin: 0 auto;
    }

    /* 폼 내부 텍스트 및 라벨 (USERNAME, PASSWORD) */
    .stTextInput label p {
        color: #94A3B8 !important;
        font-size: 11px !important;
        font-weight: 700 !important;
        letter-spacing: 2px !important;
        text-transform: uppercase !important;
    }

    /* 입력창 디자인 (투명한 유리 느낌) */
    .stTextInput input {
        background-color: rgba(0, 0, 0, 0.25) !important;
        color: #FFFFFF !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 12px !important;
        padding: 0.9rem 1.2rem !important;
        transition: all 0.3s ease !important;
    }
    .stTextInput input:focus {
        border-color: #20C997 !important;
        background-color: rgba(0, 0, 0, 0.4) !important;
        box-shadow: 0 0 0 1px rgba(32, 201, 151, 0.5) !important;
    }

    /* 트렌디한 그라디언트 로그인 버튼 */
    [data-testid="stFormSubmitButton"] button {
        background: linear-gradient(135deg, #20C997 0%, #007BFF 100%) !important;
        color: #ffffff !important;
        font-size: 14px !important;
        font-weight: 800 !important;
        letter-spacing: 3px !important;
        border-radius: 12px !important;
        border: none !important;
        margin-top: 2rem !important;
        padding: 0.8rem !important;
        transition: all 0.3s ease !important;
        box-shadow: 0 10px 20px rgba(32, 201, 151, 0.25) !important;
    }
    [data-testid="stFormSubmitButton"] button:hover {
        transform: translateY(-2px);
        box-shadow: 0 15px 30px rgba(32, 201, 151, 0.4) !important;
    }

    /* 모던 로고 타이포그래피 */
    .logo-container {
        text-align: center;
        margin-bottom: 2.5rem;
    }
    .glass-icon {
        font-size: 32px;
        line-height: 1;
        margin-bottom: 15px;
        display: inline-block;
        padding: 16px;
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 18px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.2);
    }
    .logo-title {
        color: #FFFFFF;
        font-size: 24px;
        font-weight: 900;
        letter-spacing: 3px;
        margin: 0 0 8px 0;
        font-family: 'Arial Black', sans-serif;
    }
    .logo-subtitle {
        color: #20C997;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 4px;
        margin: 0;
    }
    
    /* 오류/경고 메시지 배경 투명화 */
    [data-testid="stAlert"] {
        background: rgba(0,0,0,0.4) !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        backdrop-filter: blur(10px);
        color: white !important;
    }
    </style>
    
    <!-- 움직이는 오로라 백그라운드 HTML 주입 -->
    <div class="aurora-bg">
        <div class="orb orb-1"></div>
        <div class="orb orb-2"></div>
        <div class="orb orb-3"></div>
    </div>
    """, unsafe_allow_html=True)

    # 폼 영역
    with st.form("login_form", clear_on_submit=True):
        # 트렌디한 로고 렌더링
        st.markdown("""
        <div class="logo-container">
            <div class="glass-icon">✨</div>
            <h2 class="logo-title">QUANT DESK</h2>
            <p class="logo-subtitle">SECURE INVESTMENT PLATFORM</p>
        </div>
        """, unsafe_allow_html=True)
        
        login_username = st.text_input("USERNAME", key="login_id")
        login_pw = st.text_input("PASSWORD", type="password")
        
        submitted = st.form_submit_button("ENTER SYSTEM", type="primary", use_container_width=True)
        
        if submitted:
            if re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', login_username):
                st.error("🚨 영문과 숫자만 입력해 주세요.")
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
                        st.error("❌ 등록되지 않은 계정이거나 비밀번호가 불일치합니다.")
                except Exception as e:
                    st.error(f"시스템 장애: {str(e)}")
    st.stop()


# --- [4. 로그인 성공 후 프레임워크 가동 (Slim Left Menu 구조)] ---
# 로그인 성공 시, 위의 몽환적인 다크 테마/오로라 배경은 렌더링되지 않으므로 
# 자연스럽게 원래 원하시던 '깔끔한 흰색 배경'의 대시보드로 돌아옵니다.

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
