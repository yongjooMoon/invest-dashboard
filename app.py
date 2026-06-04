import streamlit as st
from supabase import create_client, Client
import hashlib
import real_estate
import stock_quant

# --- [1. Supabase 연동 설정] ---
SUPABASE_URL = "https://unvcqrjzvtgtjovfyvow.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVudmNxcmp6dnRndGpvdmZ5dm93Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1NDM5MjEsImV4cCI6MjA5NjExOTkyMX0.XWhOYvFlO3z0lVU57tIjQDbGVUFyHTv3niLsV2ZUeJ4"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="토스 스타일 퀀트 대시보드", page_icon="✨", layout="wide")

# --- [2. 세션 상태 초기화] ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "api_keys" not in st.session_state:
    st.session_state.api_keys = {"rtms_key": "", "naver_id": "", "naver_secret": ""}

# --- [3. 로그인 전용 단일 UI (회원가입 삭제, 이메일 형식 해제)] ---
if not st.session_state.logged_in:
    st.title("✨ 투자 자산 대시보드")
    st.markdown("인증된 계정만 접근 가능한 내부 투자 자산 관리 데스크입니다.")
    
    login_username = st.text_input("사용자 아이디 (Username)")
    login_pw = st.text_input("비밀번호 (Password)", type="password")
    
    if st.button("로그인", type="primary"):
        if login_username.strip() == "" or login_pw.strip() == "":
            st.warning("아이디와 비밀번호를 모두 입력해 주세요.")
        else:            
            try:
                # 커스텀 테이블(custom_users)에서 아이디와 해시 비밀번호가 일치하는지 조회
                user_query = supabase.table("custom_users").select("*").eq("username", login_username).eq("password_hash", login_pw).execute()
                
                if user_query.data:
                    # 로그인 성공 세션 락 가동
                    st.session_state.logged_in = True
                    st.session_state.username = login_username
                    
                    # 해당 계정의 API 크레덴셜 정보 로드
                    user_keys = supabase.table("user_api_keys").select("*").eq("username", login_username).execute()
                    if user_keys.data:
                        st.session_state.api_keys = {
                            "rtms_key": user_keys.data[0].get("rtms_key", ""),
                            "naver_id": user_keys.data[0].get("naver_id", ""),
                            "naver_secret": user_keys.data[0].get("naver_secret", "")
                        }
                    else:
                        # 키 레코드가 없다면 에러 방지를 위해 공백 행 초기 주입
                        supabase.table("user_api_keys").insert({"username": login_username, "rtms_key": "", "naver_id": "", "naver_secret": ""}).execute()
                    
                    st.success(f"🎉 인증 성공! {login_username}님 환영합니다.")
                    st.rerun()
                else:
                    st.error("❌ 불허된 크레덴셜입니다. 아이디 또는 비밀번호를 다시 확인하세요.")
            except Exception as e:
                st.error(f"시스템 데이터베이스 통신 장애: {str(e)}")
    st.stop()

# --- [4. 로그인 성공 후 프레임워크 가동] ---
st.sidebar.markdown(f"👤 데스크 제어권: **{st.session_state.username}**")
if st.sidebar.button("시스템 로그아웃", type="secondary"):
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.api_keys = {"rtms_key": "", "naver_id": "", "naver_secret": ""}
    st.rerun()

menu = st.sidebar.radio("원하는 데스크를 선택하세요", ["⚙️ 내 API 키 자산 설정", "🏢 부동산 실거래가 스캔", "📈 주식 포트폴리오 퀀트"])

if menu == "⚙️ 내 API 키 자산 설정":
    st.title("⚙️ 내 API 크레덴셜 관리")
    st.markdown("사용자님의 고유 자산 데이터 연동을 위한 API 키를 안전하게 보관합니다.")
    
    rtms = st.text_input("1. 국토교통부 실거래 API Key (Decoding)", value=st.session_state.api_keys["rtms_key"], type="password")
    n_id = st.text_input("2. 네이버 오픈 API Client ID", value=st.session_state.api_keys["naver_id"])
    n_sec = st.text_input("3. 네이버 오픈 API Client Secret", value=st.session_state.api_keys["naver_secret"], type="password")
    
    if st.button("크레덴셜 장부 업데이트", type="primary"):
        try:
            supabase.table("user_api_keys").upsert({
                "username": st.session_state.username,
                "rtms_key": rtms,
                "naver_id": n_id,
                "naver_secret": n_sec
            }).execute()
            st.session_state.api_keys = {"rtms_key": rtms, "naver_id": n_id, "naver_secret": n_sec}
            st.success("보안 데이터가 테이블에 무결점 반영되었습니다.")
        except Exception as e:
            st.error(f"저장 실패: {str(e)}")

elif menu == "🏢 부동산 실거래가 스캔":
    real_estate.run_real_estate_page(st.session_state.api_keys["rtms_key"])

elif menu == "📈 주식 포트폴리오 퀀트":
    # 💡 user_id 대신 계정 아이디(username)를 아규먼트로 바인딩
    stock_quant.run_stock_quant_page(supabase, st.session_state.username, st.session_state.api_keys["naver_id"], st.session_state.api_keys["naver_secret"])
