import streamlit as st
from supabase import create_client, Client
import real_estate
import stock_quant

# --- [1. Supabase 연동 설정] ---
# 💡 본인의 Supabase 프로젝트 URL과 Anon Key를 입력해 두세요.
SUPABASE_URL = "https://unvcqrjzvtgtjovfyvow.supabase.co"
SUPABASE_KEY = "sb_publishable_h6pGCCiC9n71So4ZesW4bQ_MNwKlI60"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="토스 스타일 퀀트 대시보드", page_icon="✨", layout="wide")

# --- [2. 세션 상태 초기화] ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "api_keys" not in st.session_state:
    st.session_state.api_keys = {"rtms_key": "", "naver_id": "", "naver_secret": ""}

# --- [3. 로그인 / 회원가입 UI] ---
if not st.session_state.logged_in:
    st.title("✨ 토스 프랍 데스크 - 로그인")
    tab_login, tab_signup = st.tabs(["🔒 로그인", "📝 회원가입"])

    with tab_login:
        login_email = st.text_input("이메일 주소", key="login_email")
        login_pw = st.text_input("비밀번호", type="password", key="login_pw")
        if st.button("로그인 실행", type="primary"):
            try:
                res = supabase.auth.sign_in_with_password({"email": login_email, "password": login_pw})
                st.session_state.logged_in = True
                st.session_state.user_id = res.user.id
                st.session_state.user_email = res.user.email

                # DB에서 사용자의 고ied API 키 정보 로드
                user_keys = supabase.table("user_api_keys").select("*").eq("user_id", res.user.id).execute()
                if user_keys.data:
                    st.session_state.api_keys = {
                        "rtms_key": user_keys.data[0].get("rtms_key", ""),
                        "naver_id": user_keys.data[0].get("naver_id", ""),
                        "naver_secret": user_keys.data[0].get("naver_secret", "")
                    }
                st.rerun()
            except Exception as e:
                st.error(f"로그인 실패: {str(e)}")

    with tab_signup:
        sign_email = st.text_input("이메일 주소", key="sign_email")
        sign_pw = st.text_input("비밀번호", type="password", key="sign_pw")
        if st.button("신규 회원 등록"):
            try:
                res = supabase.auth.sign_up({"email": sign_email, "password": sign_pw})
                # 가입 시 키 관리 테이블 초기 행 삽입
                supabase.table("user_api_keys").insert(
                    {"user_id": res.user.id, "rtms_key": "", "naver_id": "", "naver_secret": ""}).execute()
                st.success("회원가입이 완료되었습니다! 로그인 탭에서 로그인해 주세요.")
            except Exception as e:
                st.error(f"회원가입 실패: {str(e)}")
    st.stop()

# --- [4. 로그인 성공 후 메인 시스템 가동] ---
st.sidebar.markdown(f"👤 **{st.session_state.user_email}** 님")
if st.sidebar.button("로그아웃", type="secondary"):
    supabase.auth.sign_out()
    st.session_state.logged_in = False
    st.rerun()

# 사이드바 메뉴 메뉴 연동
menu = st.sidebar.radio("원하는 데스크를 선택하세요", ["⚙️ 내 API 키 자산 설정", "🏢 부동산 실거래가 스캔", "📈 주식 포트폴리오 퀀트"])

if menu == "⚙️ 내 API 키 자산 설정":
    st.title("⚙️ 내 API 크레덴셜 관리")
    st.markdown("사용자님의 고유 자산 데이터 연동을 위한 API 키를 안전하게 보관합니다.")

    rtms = st.text_input("1. 국토교통부 실거래 API Key (Decoding)", value=st.session_state.api_keys["rtms_key"],
                         type="password")
    n_id = st.text_input("2. 네이버 오픈 API Client ID", value=st.session_state.api_keys["naver_id"])
    n_sec = st.text_input("3. 네이버 오픈 API Client Secret", value=st.session_state.api_keys["naver_secret"],
                          type="password")

    if st.button("크레덴셜 장부 업데이트", type="primary"):
        try:
            supabase.table("user_api_keys").upsert({
                "user_id": st.session_state.user_id,
                "rtms_key": rtms,
                "naver_id": n_id,
                "naver_secret": n_sec
            }).execute()
            st.session_state.api_keys = {"rtms_key": rtms, "naver_id": n_id, "naver_secret": n_sec}
            st.success("보안 데이터가 Supabase DB에 무결점 암호화 저장되었습니다.")
        except Exception as e:
            st.error(f"저장 실패: {str(e)}")

elif menu == "🏢 부동산 실거래가 스캔":
    # 이전에 제작한 부동산 분석 모듈 함수 실행
    real_estate.run_real_estate_page(st.session_state.api_keys["rtms_key"])

elif menu == "📈 주식 포트폴리오 퀀트":
    # 주식 분석 모듈 함수 실행 (Supabase 클라이언트 객체를 전달하여 사용자별 데이터 적재)
    stock_quant.run_stock_quant_page(supabase, st.session_state.user_id, st.session_state.api_keys["naver_id"],
                                     st.session_state.api_keys["naver_secret"])