import streamlit as st
import streamlit.components.v1 as components
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

# --- [3. 로그인 화면 (오로라 글래스모피즘 + Yeti 애니메이션 UI)] ---
if not st.session_state.logged_in:
    # 로그인 화면에만 적용되는 최신 트렌드 CSS 강제 주입
    st.markdown("""
    <style>
    /* 전체 배경: 칠흑 같은 우주 공간 */
    .stApp {
        background-color: #030712 !important;
        overflow: hidden !important;
    }
    
    /* 기존 헤더, 사이드바, 푸터 완전 숨김 */
    [data-testid="stSidebar"], [data-testid="stHeader"], footer {
        display: none !important;
    }

    /* 🔥 Flexbox를 활용한 완벽한 세로/가로 정중앙 정렬 🔥 */
    [data-testid="stAppViewContainer"] {
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        width: 100vw !important;
        height: 100vh !important;
    }
    
    [data-testid="stMain"] {
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        width: 100% !important;
        height: 100% !important;
    }

    /* Streamlit 기본 여백 초기화 및 중앙 정렬 */
    .block-container {
        padding: 0 !important;
        margin: 0 !important;
        max-width: 100% !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        align-items: center !important;
    }
    
    [data-testid="stVerticalBlock"] {
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        align-items: center !important;
        gap: 0 !important;
    }
    
    /* 보이지 않는 JS/HTML 컴포넌트가 세로 정렬을 방해하지 못하도록 공간 차지 무효화 */
    div[data-testid="stHtml"] {
        position: absolute !important;
        width: 0 !important;
        height: 0 !important;
        overflow: hidden !important;
        margin: 0 !important;
        padding: 0 !important;
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

    /* 💎 글래스모피즘 로그인 박스 (Flexbox를 통한 완벽한 정중앙 배치) 💎 */
    [data-testid="stForm"] {
        background: rgba(15, 23, 42, 0.4) !important;
        backdrop-filter: blur(30px) !important;
        -webkit-backdrop-filter: blur(30px) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 24px !important;
        padding: 3rem 3rem 2.5rem 3rem !important;
        
        width: 360px !important;
        min-width: 360px !important;
        max-width: 360px !important;
        
        box-shadow: 0 30px 60px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255,255,255,0.1) !important;
        z-index: 10;
        margin: auto !important; /* Flex 항목으로서 자동 중앙 마진 */
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

    /* 모던 로고 및 Yeti 타이포그래피 */
    .logo-container {
        text-align: center;
        margin-bottom: 2rem;
        position: relative;
    }
    
    /* 🙈 진짜 털복숭이 설인(Yeti) CSS 애니메이션 처리 🙈 */
    #yeti-wrap.yeti-hide .paw-l {
        transform: translate(45px, 15px) !important;
    }
    #yeti-wrap.yeti-hide .paw-r {
        transform: translate(-45px, 15px) !important;
    }
    #yeti-wrap.yeti-hide #eye-l, #yeti-wrap.yeti-hide #eye-r,
    #yeti-wrap.yeti-hide #eye-catch-l, #yeti-wrap.yeti-hide #eye-catch-r {
        transform: translateY(3px) !important; /* 가릴때 눈이 살짝 내려감 */
    }

    /* 🔥 타이틀 색상 묻힘 현상 완벽 방지 🔥 */
    .logo-title {
        color: #FFFFFF !important;
        font-size: 28px !important;
        font-weight: 900 !important;
        letter-spacing: 4px !important;
        margin: 10px 0 8px 0 !important;
        font-family: 'Arial Black', sans-serif !important;
        text-shadow: 0 2px 10px rgba(0,0,0,0.5) !important;
    }
    .logo-subtitle {
        color: #20C997 !important;
        font-size: 11px !important;
        font-weight: 700 !important;
        letter-spacing: 4px !important;
        margin: 0 !important;
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
        # 🙈 로봇 느낌 완벽 탈피! 진짜 귀여운 털복숭이 설인(Yeti) SVG
        st.markdown(
            '<div class="logo-container" id="yeti-wrap">'
            '<svg id="yeti" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="130" height="130" style="overflow: visible; margin-bottom: -15px; position: relative; z-index: 20;">'
            '<!-- 눈 덮인 원형 배경 -->'
            '<circle cx="100" cy="100" r="95" fill="#E0F2FE" />'
            '<!-- 털복숭이 몸통 -->'
            '<path d="M 50,180 C 30,140 40,80 100,50 C 160,80 170,140 150,180 Z" fill="#FFFFFF" stroke="#94A3B8" stroke-width="3" />'
            '<!-- 머리 위 삐죽거리는 털 -->'
            '<path d="M 80,60 L 90,40 L 100,55 L 110,40 L 120,60" fill="#FFFFFF" stroke="#94A3B8" stroke-width="3" stroke-linejoin="round" />'
            '<!-- 얼굴 부위 -->'
            '<ellipse cx="100" cy="120" rx="40" ry="30" fill="#F1F5F9" />'
            '<!-- 큰 눈 베이스 -->'
            '<circle cx="85" cy="115" r="12" fill="#FFFFFF" />'
            '<circle cx="115" cy="115" r="12" fill="#FFFFFF" />'
            '<!-- 까만 눈동자 (움직이는 대상) -->'
            '<circle cx="85" cy="115" r="6" fill="#1E293B" id="eye-l" style="transition: transform 0.1s ease;" />'
            '<circle cx="115" cy="115" r="6" fill="#1E293B" id="eye-r" style="transition: transform 0.1s ease;" />'
            '<!-- 눈동자 안의 반짝이는 형광 민트 캐치라이트 -->'
            '<circle cx="87" cy="113" r="2.5" fill="#20C997" id="eye-catch-l" style="transition: transform 0.1s ease;" />'
            '<circle cx="117" cy="113" r="2.5" fill="#20C997" id="eye-catch-r" style="transition: transform 0.1s ease;" />'
            '<!-- 코 -->'
            '<path d="M 96,125 L 104,125 L 100,130 Z" fill="#475569" />'
            '<!-- 입 (귀엽게 벌리고 있는 모습) -->'
            '<path d="M 90,135 C 90,145 110,145 110,135 Z" fill="#EF4444" stroke="#475569" stroke-width="2" />'
            '<rect x="95" y="135" width="10" height="4" fill="#FFFFFF" />'
            '<!-- 왼쪽 앞발 (눈을 가릴 때 올라옴) -->'
            '<g class="paw-l" style="transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1); transform: translate(-15px, 70px);">'
            '<path d="M -10,120 L 60,120 L 50,110 L 60,100 L 50,90 L 60,80 L -10,80 Z" fill="#FFFFFF" stroke="#94A3B8" stroke-width="3" stroke-linejoin="round" />'
            '<path d="M 60,90 L 40,90 M 60,100 L 40,100 M 60,110 L 40,110" stroke="#94A3B8" stroke-width="3" stroke-linecap="round" />'
            '</g>'
            '<!-- 오른쪽 앞발 (눈을 가릴 때 올라옴) -->'
            '<g class="paw-r" style="transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1); transform: translate(15px, 70px);">'
            '<path d="M 210,120 L 140,120 L 150,110 L 140,100 L 150,90 L 140,80 L 210,80 Z" fill="#FFFFFF" stroke="#94A3B8" stroke-width="3" stroke-linejoin="round" />'
            '<path d="M 140,90 L 160,90 M 140,100 L 160,100 M 140,110 L 160,110" stroke="#94A3B8" stroke-width="3" stroke-linecap="round" />'
            '</g>'
            '</svg>'
            '<h2 class="logo-title">QUANT DESK</h2>'
            '<p class="logo-subtitle">SECURE INVESTMENT PLATFORM</p>'
            '</div>', 
            unsafe_allow_html=True
        )
        
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

    # 🔑 자바스크립트 주입: Streamlit 렌더링 후 이벤트 리스너를 결합시켜 설인 애니메이션 동작 + 엔터 키 제어
    components.html("""
    <script>
    function initYetiAnimation() {
        const parent = window.parent.document;
        const pwInput = parent.querySelector('input[type="password"]');
        const idInputs = parent.querySelectorAll('input[type="text"]');
        let idInput = null;
        if(idInputs.length > 0) { idInput = idInputs[0]; }
        
        const yetiWrap = parent.getElementById('yeti-wrap');
        const eyeL = parent.getElementById('eye-l');
        const eyeR = parent.getElementById('eye-r');
        const eyeCatchL = parent.getElementById('eye-catch-l');
        const eyeCatchR = parent.getElementById('eye-catch-r');
        
        // 1. Password 포커스 시: 양쪽 털복숭이 손이 올라와 눈 가리기
        if (pwInput && yetiWrap) {
            pwInput.addEventListener('focus', () => yetiWrap.classList.add('yeti-hide'));
            pwInput.addEventListener('blur', () => yetiWrap.classList.remove('yeti-hide'));
        }
        
        // 2. Username 타이핑 시: 글자 길이에 맞춰 눈동자가 따라가는 애니메이션 (움직임 반경 확장)
        if (idInput && eyeL && eyeR) {
            const trackEyes = (e) => {
                let len = Math.min(e.target.value.length, 25);
                let moveX = (len / 25) * 12 - 6; // 눈동자가 좌우로 넓게 움직이게 폭 증가 (-6px ~ +6px)
                
                eyeL.style.transform = `translateX(${moveX}px)`;
                eyeR.style.transform = `translateX(${moveX}px)`;
                if(eyeCatchL) eyeCatchL.style.transform = `translateX(${moveX}px)`;
                if(eyeCatchR) eyeCatchR.style.transform = `translateX(${moveX}px)`;
            };
            idInput.addEventListener('input', trackEyes);
            idInput.addEventListener('focus', trackEyes);
            idInput.addEventListener('blur', () => {
                eyeL.style.transform = `translateX(0px)`;
                eyeR.style.transform = `translateX(0px)`;
                if(eyeCatchL) eyeCatchL.style.transform = `translateX(0px)`;
                if(eyeCatchR) eyeCatchR.style.transform = `translateX(0px)`;
            });

            // 3. 🎯 ID 입력창에서 Enter 입력 시: 비밀번호가 비었으면 제출 차단하고 포커스 이동
            idInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    if (pwInput && pwInput.value.trim() === '') {
                        e.preventDefault();
                        e.stopPropagation();
                        pwInput.focus();
                    }
                }
            }, true);
        }
    }
    setTimeout(initYetiAnimation, 300);
    setTimeout(initYetiAnimation, 1000);
    </script>
    """, height=0, width=0)

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

# 🚪 로그아웃 Confirm을 위한 모달 다이얼로그 (트렌디한 방식)
@st.dialog("🚪 시스템 로그아웃")
def logout_confirm_dialog():
    st.markdown("정말로 로그아웃 하시겠습니까?")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("로그아웃", use_container_width=True, type="primary"):
            update_auth_state(False, None)
            st.session_state.current_view = "main"
            st.rerun()
    with c2:
        if st.button("취소", use_container_width=True):
            st.rerun()

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
    
    # 클릭 시 다이얼로그 함수 호출로 Confirm 창 띄우기
    if st.button("🔓", help=f"현재 접속자: {st.session_state.username}님\n(클릭 시 로그아웃)", use_container_width=True):
        logout_confirm_dialog()

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
