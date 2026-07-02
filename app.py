import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client, Client
import re
import real_estate
import stock_quant

# 🛡️ 강력한 보안 암호화 라이브러리 추가
import bcrypt
from cryptography.fernet import Fernet

# 초기 설정 (가장 먼저 실행되어야 함)
st.set_page_config(page_title="QUANT DESK", page_icon="✨", layout="wide", initial_sidebar_state="expanded")

# --- [1. Supabase & 암호화 연동 설정 (네트워크 안정성 강화)] ---
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# [핵심 최적화] DB 연결을 캐싱하여 Connection Lost(연결 끊김) 원천 차단
@st.cache_resource
def init_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase: Client = init_supabase()

# 대칭키 암호화 (API Key 보관용) 마스터 키 세팅
if "ENCRYPTION_KEY" in st.secrets:
    FERNET_KEY = st.secrets["ENCRYPTION_KEY"].encode()
else:
    # 안전을 위한 임시 Fallback 키 (경고: 실제 운영시 반드시 secrets.toml에 고정 키를 넣어야 복호화가 유지됨)
    FERNET_KEY = b'vS-1_z0qL18r-58lXb0jVwFwJpPZ_X-6N1xG8Zk1w0c='
cipher_suite = Fernet(FERNET_KEY)

# 🔐 암복호화 도우미 함수
def encrypt_text(text: str) -> str:
    """API 키 등을 DB에 저장할 때 사용하는 암호화 함수"""
    if not text: return ""
    return cipher_suite.encrypt(text.encode('utf-8')).decode('utf-8')

def decrypt_text(encrypted_text: str) -> str:
    """DB에서 꺼내올 때 메모리 위에서만 해제하는 복호화 함수"""
    if not encrypted_text: return ""
    try:
        return cipher_suite.decrypt(encrypted_text.encode('utf-8')).decode('utf-8')
    except:
        # 기존에 암호화되지 않고 평문으로 저장되었던 데이터 호환 처리
        return encrypted_text

def verify_password(plain_pw: str, hashed_pw: str) -> bool:
    """사용자가 입력한 비밀번호와 DB의 bcrypt 해시를 검증"""
    try:
        return bcrypt.checkpw(plain_pw.encode('utf-8'), hashed_pw.encode('utf-8'))
    except:
        return False

# --- [2. 100% 안전한 브라우저 독립형 세션 (무한 리다이렉트 방지)] ---
# 불안정했던 global_session 코드를 모두 삭제하고, 오직 session_state만 사용합니다.
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "api_keys" not in st.session_state:
    st.session_state.api_keys = {"rtms_key": "", "app_key": "", "app_secret": "", "naver_id": "", "naver_secret": ""}
if "current_view" not in st.session_state:
    st.session_state.current_view = "main"
if "current_menu" not in st.session_state:
    st.session_state.current_menu = "quant"

def update_auth_state(is_logged_in, username, api_keys=None):
    st.session_state.logged_in = is_logged_in
    st.session_state.username = username
    
    if api_keys:
        st.session_state.api_keys = api_keys
    else:
        st.session_state.api_keys = {"rtms_key": "", "app_key": "", "app_secret": "", "naver_id": "", "naver_secret": ""}

# --- [3. 로그인 화면 (오로라 글래스모피즘 + 찐 설인 애니메이션 UI)] ---
if not st.session_state.logged_in:
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
    
    /* 🙈 오리지널 털복숭이 두 손(양팔) 애니메이션 및 까꿍(Peeking) 🙈 */
    #yeti-wrap .armL, #yeti-wrap .armR {
        transition: transform 0.45s cubic-bezier(0.25, 0.46, 0.45, 0.94);
        transform-box: fill-box; /* SVG 요소 기준 관절(축) 고정 핵심 속성 */
    }
    
    /* 기본 상태: 아래에 숨어 있음 (-93px 로 정확히 양쪽 눈을 덮을 수 있도록 X축 당김) */
    #yeti-wrap .armL {
        transform-origin: top left;
        transform: translate(-93px, 180px) rotate(105deg);
    }
    #yeti-wrap .armR {
        transform-origin: top right;
        transform: translate(-93px, 180px) rotate(-105deg);
    }
    
    /* 눈 가리기 상태 (비밀번호 포커스) */
    #yeti-wrap.yeti-hide .armL {
        transform: translate(-93px, 10px) rotate(0deg);
    }
    #yeti-wrap.yeti-hide .armR {
        transform: translate(-93px, 10px) rotate(0deg);
        transition-delay: 0.05s; /* 오른쪽 손이 살짝 늦게 올라오는 디테일 */
    }

    /* 두 손가락(까꿍용) */
    #yeti-wrap .twoFingers {
        transform-origin: bottom left;
        transform-box: fill-box;
        transition: transform 0.3s ease-in-out;
    }
    
    /* 지울 때 손가락 사이로 까꿍 (Peeking) */
    #yeti-wrap.yeti-peek .twoFingers {
        transform: translate(-9px, -2px) rotate(30deg);
    }

    /* 🔥 타이틀 색상 가독성 극대화 (화이트 + 빛나는 네온 효과) 🔥 */
    .logo-title {
        color: #FFFFFF !important;
        font-size: 30px !important;
        font-weight: 900 !important;
        letter-spacing: 5px !important;
        margin: 15px 0 8px 0 !important;
        font-family: 'Arial Black', sans-serif !important;
        text-shadow: 0 0 15px rgba(255, 255, 255, 0.8), 0 0 30px rgba(32, 201, 151, 0.4) !important;
    }
    .logo-subtitle {
        color: #20C997 !important;
        font-size: 11px !important;
        font-weight: 700 !important;
        letter-spacing: 4px !important;
        margin: 0 !important;
        text-shadow: 0 0 5px rgba(32, 201, 151, 0.5) !important;
    }
    
    /* 🚨 오류/경고 메시지 박스 - 글래스모피즘 테마에 어울리는 톤으로 수정 🚨 */
    [data-testid="stAlert"] {
        background: rgba(239, 68, 68, 0.1) !important; /* 투명한 붉은 유리 느낌 */
        border: 1px solid rgba(239, 68, 68, 0.4) !important;
        backdrop-filter: blur(10px) !important;
        -webkit-backdrop-filter: blur(10px) !important;
        border-radius: 12px !important;
        color: #FECACA !important; /* 밝은 핑크빛 텍스트 */
        margin-top: 1rem !important;
    }
    [data-testid="stAlert"] p {
        color: #FECACA !important;
        font-weight: 600 !important;
        font-size: 13px !important;
    }
    [data-testid="stAlert"] svg {
        fill: #FCA5A5 !important; /* 아이콘 색상도 맞춤 */
    }
    </style>
    
    <!-- 움직이는 오로라 백그라운드 HTML 주입 -->
    <div class="aurora-bg">
        <div class="orb orb-1"></div>
        <div class="orb orb-2"></div>
        <div class="orb orb-3"></div>
    </div>
    """, unsafe_allow_html=True)

    # 폼 영역 (clear_on_submit=False 로 변경하여 실패 시에도 입력값 유지)
    with st.form("login_form", clear_on_submit=False):
        # 🙈 원본 코드를 그대로 활용한 찐 털복숭이 설인(Yeti) SVG
        st.markdown(
            '<div class="logo-container" id="yeti-wrap">'
            '<svg id="yeti" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="140" height="140" style="overflow: visible; margin-bottom: -15px; position: relative; z-index: 20;">'
            '<defs><circle id="armMaskPath" cx="100" cy="100" r="100"/></defs>'
            '<clipPath id="armMask"><use href="#armMaskPath" overflow="visible"/></clipPath>'
            '<circle cx="100" cy="100" r="100" fill="#E0F2FE"/>'
            '<g class="body">'
            '<path stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="#FFFFFF" d="M200,158.5c0-20.2-14.8-36.5-35-36.5h-14.9V72.8c0-27.4-21.7-50.4-49.1-50.8c-28-0.5-50.9,22.1-50.9,50v50H35.8C16,122,0,138,0,157.8L0,213h200L200,158.5z"/>'
            '<path fill="#DDF1FA" d="M100,156.4c-22.9,0-43,11.1-54.1,27.7c15.6,10,34.2,15.9,54.1,15.9s38.5-5.8,54.1-15.9C143,167.5,122.9,156.4,100,156.4z"/>'
            '</g>'
            '<g class="earL">'
            '<g class="outerEar" fill="#ddf1fa" stroke="#3a5e77" stroke-width="2.5"><circle cx="47" cy="83" r="11.5"/><path d="M46.3 78.9c-2.3 0-4.1 1.9-4.1 4.1 0 2.3 1.9 4.1 4.1 4.1" stroke-linecap="round" stroke-linejoin="round"/></g>'
            '<g class="earHair"><rect x="51" y="64" fill="#FFFFFF" width="15" height="35"/><path d="M53.4 62.8C48.5 67.4 45 72.2 42.8 77c3.4-.1 6.8-.1 10.1.1-4 3.7-6.8 7.6-8.2 11.6 2.1 0 4.2 0 6.3.2-2.6 4.1-3.8 8.3-3.7 12.5 1.2-.7 3.4-1.4 5.2-1.9" fill="#fff" stroke="#3a5e77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></g>'
            '</g>'
            '<g class="earR">'
            '<g class="outerEar" fill="#DDF1FA" stroke="#3A5E77" stroke-width="2.5"><circle cx="153" cy="83" r="11.5"/><path stroke-linecap="round" stroke-linejoin="round" d="M153.7,78.9c2.3,0,4.1,1.9,4.1,4.1c0,2.3-1.9,4.1-4.1,4.1"/></g>'
            '<g class="earHair"><rect x="134" y="64" fill="#FFFFFF" width="15" height="35"/><path fill="#FFFFFF" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" d="M146.6,62.8c4.9,4.6,8.4,9.4,10.6,14.2c-3.4-0.1-6.8-0.1-10.1,0.1c4,3.7,6.8,7.6,8.2,11.6c-2.1,0-4.2,0-6.3,0.2c2.6,4.1,3.8,8.3,3.7,12.5c-1.2-0.7-3.4-1.4-5.2-1.9"/></g>'
            '</g>'
            '<path class="chin" d="M84.1 121.6c2.7 2.9 6.1 5.4 9.8 7.5l.9-4.5c2.9 2.5 6.3 4.8 10.2 6.5 0-1.9-.1-3.9-.2-5.8 3 1.2 6.2 2 9.7 2.5-.3-2.1-.7-4.1-1.2-6.1" fill="none" stroke="#3a5e77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />'
            '<path class="face" fill="#DDF1FA" d="M134.5,46v35.5c0,21.815-15.446,39.5-34.5,39.5s-34.5-17.685-34.5-39.5V46" />'
            '<path class="hair" fill="#FFFFFF" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" d="M81.457,27.929c1.755-4.084,5.51-8.262,11.253-11.77c0.979,2.565,1.883,5.14,2.712,7.723c3.162-4.265,8.626-8.27,16.272-11.235c-0.737,3.293-1.588,6.573-2.554,9.837c4.857-2.116,11.049-3.64,18.428-4.156c-2.403,3.23-5.021,6.391-7.852,9.474"/>'
            '<g class="eyebrow">'
            '<path fill="#FFFFFF" d="M138.142,55.064c-4.93,1.259-9.874,2.118-14.787,2.599c-0.336,3.341-0.776,6.689-1.322,10.037c-4.569-1.465-8.909-3.222-12.996-5.226c-0.98,3.075-2.07,6.137-3.267,9.179c-5.514-3.067-10.559-6.545-15.097-10.329c-1.806,2.889-3.745,5.73-5.816,8.515c-7.916-4.124-15.053-9.114-21.296-14.738l1.107-11.768h73.475V55.064z"/>'
            '<path fill="#FFFFFF" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" d="M63.56,55.102c6.243,5.624,13.38,10.614,21.296,14.738c2.071-2.785,4.01-5.626,5.816-8.515c4.537,3.785,9.583,7.263,15.097,10.329c1.197-3.043,2.287-6.104,3.267-9.179c4.087,2.004,8.427,3.761,12.996,5.226c0.545-3.348,0.986-6.696,1.322-10.037c4.913-0.481,9.857-1.34,14.787-2.599"/>'
            '</g>'
            '<!-- 눈을 더 크고 선명하게 (r=5, r=1.5) -->'
            '<g id="eyes" style="transition: transform 0.1s ease-out;">'
            '<g class="eyeL"><circle cx="85.5" cy="78.5" r="5" fill="#3a5e77"/><circle cx="84" cy="76" r="1.5" fill="#fff"/></g>'
            '<g class="eyeR"><circle cx="114.5" cy="78.5" r="5" fill="#3a5e77"/><circle cx="113" cy="76" r="1.5" fill="#fff"/></g>'
            '</g>'
            '<g class="mouth">'
            '<path class="mouthBG" fill="#617E92" d="M100.2,101c-0.4,0-1.4,0-1.8,0c-2.7-0.3-5.3-1.1-8-2.5c-0.7-0.3-0.9-1.2-0.6-1.8 c0.2-0.5,0.7-0.7,1.2-0.7c0.2,0,0.5,0.1,0.6,0.2c3,1.5,5.8,2.3,8.6,2.3s5.7-0.7,8.6-2.3c0.2-0.1,0.4-0.2,0.6-0.2 c0.5,0,1,0.3,1.2,0.7c0.4,0.7,0.1,1.5-0.6,1.9c-2.6,1.4-5.3,2.2-7.9,2.5C101.7,101,100.5,101,100.2,101z" />'
            '<path class="mouthOutline" fill="none" stroke="#3A5E77" stroke-width="2.5" stroke-linejoin="round" d="M100.2,101c-0.4,0-1.4,0-1.8,0c-2.7-0.3-5.3-1.1-8-2.5c-0.7-0.3-0.9-1.2-0.6-1.8 c0.2-0.5,0.7-0.7,1.2-0.7c0.2,0,0.5,0.1,0.6,0.2c3,1.5,5.8,2.3,8.6,2.3s5.7-0.7,8.6-2.3c0.2-0.1,0.4-0.2,0.6-0.2 c0.5,0,1,0.3,1.2,0.7c0.4,0.7,0.1,1.5-0.6,1.9c-2.6,1.4-5.3,2.2-7.9,2.5C101.7,101,100.5,101,100.2,101z" />'
            '</g>'
            '<path class="nose" d="M97.7 79.9h4.7c1.9 0 3 2.2 1.9 3.7l-2.3 3.3c-.9 1.3-2.9 1.3-3.8 0l-2.3-3.3c-1.3-1.6-.2-3.7 1.8-3.7z" fill="#3a5e77" />'
            '<g class="arms" clip-path="url(#armMask)">'
            '<g class="armL">'
            '<polygon fill="#DDF1FA" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" stroke-miterlimit="10" points="121.3,98.4 111,59.7 149.8,49.3 169.8,85.4" />'
            '<path fill="#DDF1FA" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" stroke-miterlimit="10" d="M134.4,53.5l19.3-5.2c2.7-0.7,5.4,0.9,6.1,3.5v0c0.7,2.7-0.9,5.4-3.5,6.1l-10.3,2.8" />'
            '<path fill="#DDF1FA" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" stroke-miterlimit="10" d="M150.9,59.4l26-7c2.7-0.7,5.4,0.9,6.1,3.5v0c0.7,2.7-0.9,5.4-3.5,6.1l-21.3,5.7" />'
            '<g class="twoFingers">'
            '<path fill="#DDF1FA" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" stroke-miterlimit="10" d="M158.3,67.8l23.1-6.2c2.7-0.7,5.4,0.9,6.1,3.5v0c0.7,2.7-0.9,5.4-3.5,6.1l-23.1,6.2" />'
            '<path fill="#A9DDF3" d="M180.1,65l2.2-0.6c1.1-0.3,2.2,0.3,2.4,1.4v0c0.3,1.1-0.3,2.2-1.4,2.4l-2.2,0.6L180.1,65z" />'
            '<path fill="#DDF1FA" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" stroke-miterlimit="10" d="M160.8,77.5l19.4-5.2c2.7-0.7,5.4,0.9,6.1,3.5v0c0.7,2.7-0.9,5.4-3.5,6.1l-18.3,4.9" />'
            '<path fill="#A9DDF3" d="M178.8,75.7l2.2-0.6c1.1-0.3,2.2,0.3,2.4,1.4v0c0.3,1.1-0.3,2.2-1.4,2.4l-2.2,0.6L178.8,75.7z" />'
            '</g>'
            '<path fill="#A9DDF3" d="M175.5,55.9l2.2-0.6c1.1-0.3,2.2,0.3,2.4,1.4v0c0.3,1.1-0.3,2.2-1.4,2.4l-2.2,0.6L175.5,55.9z" />'
            '<path fill="#A9DDF3" d="M152.1,50.4l2.2-0.6c1.1-0.3,2.2,0.3,2.4,1.4v0c0.3,1.1-0.3,2.2-1.4,2.4l-2.2,0.6L152.1,50.4z" />'
            '<path fill="#FFFFFF" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" d="M123.5,97.8 c-41.4,14.9-84.1,30.7-108.2,35.5L1.2,81c33.5-9.9,71.9-16.5,111.9-21.8" />'
            '<path fill="#FFFFFF" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" d="M108.5,60.4 c7.7-5.3,14.3-8.4,22.8-13.2c-2.4,5.3-4.7,10.3-6.7,15.1c4.3,0.3,8.4,0.7,12.3,1.3c-4.2,5-8.1,9.6-11.5,13.9 c3.1,1.1,6,2.4,8.7,3.8c-1.4,2.9-2.7,5.8-3.9,8.5c2.5,3.5,4.6,7.2,6.3,11c-4.9-0.8-9-0.7-16.2-2.7" />'
            '<path fill="#FFFFFF" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" d="M94.5,103.8 c-0.6,4-3.8,8.9-9.4,14.7c-2.6-1.8-5-3.7-7.2-5.7c-2.5,4.1-6.6,8.8-12.2,14c-1.9-2.2-3.4-4.5-4.5-6.9c-4.4,3.3-9.5,6.9-15.4,10.8 c-0.2-3.4,0.1-7.1,1.1-10.9" />'
            '<path fill="#FFFFFF" stroke="#3A5E77" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" d="M97.5,63.9 c-1.7-2.4-5.9-4.1-12.4-5.2c-0.9,2.2-1.8,4.3-2.5,6.5c-3.8-1.8-9.4-3.1-17-3.8c0.5,2.3,1.2,4.5,1.9,6.8c-5-0.6-11.2-0.9-18.4-1 c2,2.9,0.9,3.5,3.9,6.2" />'
            '</g>'
            '<g class="armR">'
            '<path fill="#ddf1fa" stroke="#3a5e77" stroke-linecap="round" stroke-linejoin="round" stroke-miterlimit="10" stroke-width="2.5" d="M265.4 97.3l10.4-38.6-38.9-10.5-20 36.1z" />'
            '<path fill="#ddf1fa" stroke="#3a5e77" stroke-linecap="round" stroke-linejoin="round" stroke-miterlimit="10" stroke-width="2.5" d="M252.4 52.4L233 47.2c-2.7-.7-5.4.9-6.1 3.5-.7 2.7.9 5.4 3.5 6.1l10.3 2.8M226 76.4l-19.4-5.2c-2.7-.7-5.4.9-6.1 3.5-.7 2.7.9 5.4 3.5 6.1l18.3 4.9M228.4 66.7l-23.1-6.2c-2.7-.7-5.4.9-6.1 3.5-.7 2.7.9 5.4 3.5 6.1l23.1 6.2M235.8 58.3l-26-7c-2.7-.7-5.4.9-6.1 3.5-.7 2.7.9 5.4 3.5 6.1l21.3 5.7" />'
            '<path fill="#a9ddf3" d="M207.9 74.7l-2.2-.6c-1.1-.3-2.2.3-2.4 1.4-.3 1.1.3 2.2 1.4 2.4l2.2.6 1-3.8zM206.7 64l-2.2-.6c-1.1-.3-2.2.3-2.4 1.4-.3 1.1.3 2.2 1.4 2.4l2.2.6 1-3.8zM211.2 54.8l-2.2-.6c-1.1-.3-2.2.3-2.4 1.4-.3 1.1.3 2.2 1.4 2.4l2.2.6 1-3.8zM234.6 49.4l-2.2-.6c-1.1-.3-2.2.3-2.4 1.4-.3 1.1.3 2.2 1.4 2.4l2.2.6 1-3.8z" />'
            '<path fill="#fff" stroke="#3a5e77" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M263.3 96.7c41.4 14.9 84.1 30.7 108.2 35.5l14-52.3C352 70 313.6 63.5 273.6 58.1" />'
            '<path fill="#fff" stroke="#3a5e77" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M278.2 59.3l-18.6-10 2.5 11.9-10.7 6.5 9.9 8.7-13.9 6.4 9.1 5.9-13.2 9.2 23.1-.9M284.5 100.1c-.4 4 1.8 8.9 6.7 14.8 3.5-1.8 6.7-3.6 9.7-5.5 1.8 4.2 5.1 8.9 10.1 14.1 2.7-2.1 5.1-4.4 7.1-6.8 4.1 3.4 9 7 14.7 11 1.2-3.4 1.8-7 1.7-10.9M314 66.7s5.4-5.7 12.6-7.4c1.7 2.9 3.3 5.7 4.9 8.6 3.8-2.5 9.8-4.4 18.2-5.7.1 3.1.1 6.1 0 9.2 5.5-1 12.5-1.6 20.8-1.9-1.4 3.9-2.5 8.4-2.5 8.4" />'
            '</g>'
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
                    user_query = supabase.table("custom_users").select("*").eq("username", login_username).execute()
                    if user_query.data:
                        stored_hash = user_query.data[0].get("password_hash", "")
                        
                        login_success = False
                        # 1. 🛡️ bcrypt 해시 암호 검증
                        if verify_password(login_pw, stored_hash):
                            login_success = True
                        # 2. 🪄 기존 사용자 평문 -> bcrypt 자동 마이그레이션
                        elif login_pw == stored_hash:
                            login_success = True
                            # 입력받은 평문 비밀번호를 즉시 해싱하여 DB 덮어쓰기
                            new_secure_hash = bcrypt.hashpw(login_pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                            supabase.table("custom_users").update({"password_hash": new_secure_hash}).eq("username", login_username).execute()
                            
                        if login_success:
                            admin_keys = supabase.table("user_api_keys").select("*").eq("username", "admin").execute()
                            keys_to_save = {}
                            if admin_keys.data:
                                # DB에서 가져올 때 복호화하여 세션에 할당 (안전한 평문)
                                keys_to_save = {
                                    "rtms_key": decrypt_text(admin_keys.data[0].get("rtms_key", "")),
                                    "app_key": decrypt_text(admin_keys.data[0].get("app_key", "")),
                                    "app_secret": decrypt_text(admin_keys.data[0].get("app_secret", "")),
                                    "naver_id": decrypt_text(admin_keys.data[0].get("naver_id", "")),
                                    "naver_secret": decrypt_text(admin_keys.data[0].get("naver_secret", ""))
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
                    else:
                        st.error("❌ 등록되지 않은 계정이거나 비밀번호가 불일치합니다.")
                except Exception as e:
                    st.error(f"시스템 장애: {str(e)}")

    # 🔑 자바스크립트 주입: Streamlit 렌더링 후 이벤트 리스너를 결합시켜 설인 애니메이션 동작 + 엔터 키 제어 + 까꿍 + 암호 보이기
    components.html("""
    <script>
    function initYetiAnimation() {
        const parent = window.parent.document;
        // Streamlit에서 렌더링한 인풋 필드들 모두 가져오기
        const textInputs = parent.querySelectorAll('.stTextInput input');
        if (textInputs.length < 2) return;
        
        const idInput = textInputs[0];
        const pwInput = textInputs[1];
        
        const yetiWrap = parent.getElementById('yeti-wrap');
        const eyes = parent.getElementById('eyes');
        if (!yetiWrap || !eyes) return;
        
        let pwLength = 0;
        let peekTimeout;

        // 1. 눈동자가 입력된 텍스트 길이에 따라 움직이는 공통 함수
        const trackEyes = (e) => {
            let len = Math.min(e.target.value.length, 25);
            let moveX = (len / 25) * 12 - 6; // 눈동자가 더 시원하게 좌우로 이동 (-6px ~ +6px)
            eyes.style.transform = `translateX(${moveX}px)`;
        };

        // 2. ID 필드 이벤트
        idInput.addEventListener('input', trackEyes);
        idInput.addEventListener('focus', trackEyes);
        idInput.addEventListener('blur', () => {
            eyes.style.transform = `translateX(0px)`;
        });

        // 🎯 ID 입력창에서 Enter 입력 시: 비밀번호가 비었으면 제출 차단하고 포커스 이동
        idInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                if (pwInput && pwInput.value.trim() === '') {
                    e.preventDefault();
                    e.stopPropagation();
                    pwInput.focus();
                }
            }
        }, true);

        // 3. Password 필드 상태 및 텍스트/패스워드 노출 판단 함수
        const handlePwState = () => {
            if (parent.activeElement === pwInput) {
                // 타입이 'password'이면 눈을 가린다
                if (pwInput.type === 'password') {
                    yetiWrap.classList.add('yeti-hide');
                    eyes.style.transform = `translateX(0px)`; // 눈동자는 중앙으로
                } 
                // 타입이 'text'(비밀번호 노출버튼 눌림)이면 손을 치우고 글자를 쳐다본다
                else {
                    yetiWrap.classList.remove('yeti-hide');
                    yetiWrap.classList.remove('yeti-peek');
                    trackEyes({target: pwInput}); // 글자 길이에 맞춰 눈동자 이동
                }
            }
        };

        // 패스워드 필드 포커스/아웃
        pwInput.addEventListener('focus', () => {
            pwLength = pwInput.value.length;
            handlePwState();
        });
        pwInput.addEventListener('blur', () => {
            yetiWrap.classList.remove('yeti-hide');
            yetiWrap.classList.remove('yeti-peek'); // 까꿍 상태도 해제
            eyes.style.transform = `translateX(0px)`;
        });
        
        // 💡 비밀번호 타이핑 중 '까꿍(Peeking)' 로직 및 눈동자 트래킹 통합
        pwInput.addEventListener('input', (e) => {
            // 눈 모양 아이콘이 활성화되어 텍스트가 노출되는 상태면 눈동자를 굴림
            if (pwInput.type === 'text') {
                trackEyes(e);
            } 
            // 가려진 상태일 때는 지울 때 까꿍 애니메이션 재생
            else {
                const currentLength = e.target.value.length;
                if (currentLength < pwLength) {
                    // 글자를 지우는 중 -> 손가락을 벌려 까꿍!
                    yetiWrap.classList.add('yeti-peek');
                    clearTimeout(peekTimeout);
                    // 1.2초 뒤에 포커스가 유지중이고 여전히 암호모드면 다시 가리기
                    peekTimeout = setTimeout(() => {
                        if (parent.activeElement === pwInput && pwInput.type === 'password') {
                            yetiWrap.classList.remove('yeti-peek');
                        }
                    }, 1200);
                } else {
                    // 글자를 입력하는 중 -> 손가락 닫고 철저히 가리기!
                    yetiWrap.classList.remove('yeti-peek');
                }
                pwLength = currentLength;
            }
        });

        // 4. 우측 눈 모양 아이콘(비밀번호 노출 버튼) 클릭 감지
        // Streamlit의 눈 아이콘은 input 컨테이너 안에 존재하므로 부모 컨테이너에 클릭 이벤트를 위임
        const pwContainer = pwInput.closest('[data-baseweb="input"]');
        if (pwContainer) {
            pwContainer.addEventListener('click', () => {
                // Streamlit이 클릭 이벤트를 받아 type='text'로 전환하는 짧은 시간을 기다렸다가 상태 판단
                setTimeout(() => {
                    handlePwState();
                }, 50);
            });
        }
    }
    
    // 컴포넌트 렌더링 딜레이를 고려하여 두 번 초기화 시도
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
        /* 다크모드/라이트모드 자동 대응을 위해 하드코딩된 색상 제거 후 변수 사용 */
        background-color: var(--secondary-background-color) !important;
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
    
    # st.text_input의 기본값에 암호화되지 않은 세션키를 그대로 넘겨 수정 가능하게 합니다.
    rtms = st.text_input("1. 국토교통부 실거래 API Key (Decoding)", value=st.session_state.api_keys["rtms_key"], type="password")
    a_key = st.text_input("2. 한국투자증권 오픈 API App Key (시세/수급용)", value=st.session_state.api_keys["app_key"])
    a_sec = st.text_input("3. 한국투자증권 오픈 API App Secret (시세/수급용)", value=st.session_state.api_keys["app_secret"], type="password")
    n_id = st.text_input("4. 네이버 오픈 API Client ID (뉴스 호재 분석용)", value=st.session_state.api_keys["naver_id"])
    n_sec = st.text_input("5. 네이버 오픈 API Client Secret (뉴스 호재 분석용)", value=st.session_state.api_keys["naver_secret"], type="password")
    
    if st.button("마스터 크레덴셜 업데이트", type="primary"):
        try:
            # 🛡️ DB 저장 시에는 강력한 대칭키 암호화(Fernet) 처리
            supabase.table("user_api_keys").upsert({
                "username": "admin",
                "rtms_key": encrypt_text(rtms),
                "app_key": encrypt_text(a_key),
                "app_secret": encrypt_text(a_sec),
                "naver_id": encrypt_text(n_id),
                "naver_secret": encrypt_text(n_sec)
            }).execute()
            
            # 세션 메모리에는 원래 평문(Plain text)을 그대로 보관하여 앱 내부에서 정상 작동하게 함
            updated_keys = {"rtms_key": rtms, "app_key": a_key, "app_secret": a_sec, "naver_id": n_id, "naver_secret": n_sec}
            st.session_state.api_keys = updated_keys
            # global_session["api_keys"] = updated_keys 삭제됨
            
            st.success("✅ 마스터 5대 보안 자산 데이터가 암호화되어 무결점 반영되었습니다.")
        except Exception as e:
            st.error(f"저장 실패: {str(e)}")

else:
    if st.session_state.current_menu == "quant":
        stock_quant.run_stock_quant_page(supabase, st.session_state.username)
    elif st.session_state.current_menu == "real_estate":
        real_estate.run_real_estate_page(st.session_state.api_keys["rtms_key"])
