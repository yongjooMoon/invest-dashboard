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

st.set_page_config(page_title="토스 스타일 퀀트 대시보드", page_icon="✨", layout="wide")

# --- [2. 세션 상태 초기화] ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "api_keys" not in st.session_state:
    st.session_state.api_keys = {
        "rtms_key": "", 
        "app_key": "", 
        "app_secret": "", 
        "naver_id": "", 
        "naver_secret": ""
    }
if "current_view" not in st.session_state:
    st.session_state.current_view = "main"
if "current_menu" not in st.session_state:
    st.session_state.current_menu = "quant"

# --- [3. 로그인 전용 단일 UI] ---
if not st.session_state.logged_in:
    st.title("✨ 투자 자산 대시보드")
    st.markdown("인증된 계정만 접근 가능한 내부 투자 자산 관리 데스크입니다.")
    
    # st.form을 사용하여 엔터(Enter) 키로 로그인(Submit) 가능하도록 구현
    with st.form("login_form"):
        # UI에서 지저분한 안내 문구 제거
        login_username = st.text_input("사용자 아이디 (Username)", key="login_id")
        login_pw = st.text_input("비밀번호 (Password)", type="password")
        
        submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
        
        if submitted:
            # 한글 입력 방지 로직 (정규식 검사는 유지)
            if re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', login_username):
                st.error("🚨 아이디에는 한글을 입력할 수 없습니다. 영문과 숫자만 입력해 주세요.")
            elif login_username.strip() == "" or login_pw.strip() == "":
                st.warning("아이디와 비밀번호를 모두 입력해 주세요.")
            else:            
                try:
                    # 커스텀 테이블(custom_users)에서 아이디와 해시 비밀번호가 일치하는지 조회
                    user_query = supabase.table("custom_users").select("*").eq("username", login_username).eq("password_hash", login_pw).execute()
                    
                    if user_query.data:
                        st.session_state.logged_in = True
                        st.session_state.username = login_username
                        st.session_state.current_view = "main"
                        st.session_state.current_menu = "quant"
                        
                        # 개별 유저 키가 아닌 'admin' 공통 마스터 API 크레덴셜 정보 로드
                        admin_keys = supabase.table("user_api_keys").select("*").eq("username", "admin").execute()
                        if admin_keys.data:
                            st.session_state.api_keys = {
                                "rtms_key": admin_keys.data[0].get("rtms_key", ""),
                                "app_key": admin_keys.data[0].get("app_key", ""),
                                "app_secret": admin_keys.data[0].get("app_secret", ""),
                                "naver_id": admin_keys.data[0].get("naver_id", ""),
                                "naver_secret": admin_keys.data[0].get("naver_secret", "")
                            }
                        else:
                            # admin 키가 아예 없다면 초기 생성 (구조적 에러 방지)
                            supabase.table("user_api_keys").insert({
                                "username": "admin", 
                                "rtms_key": "", 
                                "app_key": "", 
                                "app_secret": "",
                                "naver_id": "",
                                "naver_secret": ""
                            }).execute()
                        
                        st.rerun()
                    else:
                        st.error("❌ 등록되지 않은 계정이거나 비밀번호가 일치하지 않습니다.")
                except Exception as e:
                    st.error(f"시스템 데이터베이스 통신 장애: {str(e)}")
    st.stop()

# --- [4. 로그인 성공 후 프레임워크 가동 (Top Menu 구조)] ---

# 상단 헤더 및 네비게이션 레이아웃 (버튼을 탭처럼 활용)
header_cols = st.columns([2.5, 1.5, 1.5, 2, 1, 1])

with header_cols[0]:
    st.subheader("✨ 내부 투자 자산 데스크")

with header_cols[1]:
    # 퀀트 메뉴 버튼 (활성화 시 색상 변경)
    is_quant = st.session_state.current_menu == "quant" and st.session_state.current_view == "main"
    if st.button("📈 주식 퀀트", use_container_width=True, type="primary" if is_quant else "secondary"):
        st.session_state.current_menu = "quant"
        st.session_state.current_view = "main"
        st.rerun()

with header_cols[2]:
    # 부동산 메뉴 버튼 (활성화 시 색상 변경)
    is_real_estate = st.session_state.current_menu == "real_estate" and st.session_state.current_view == "main"
    if st.button("🏢 부동산 스캔", use_container_width=True, type="primary" if is_real_estate else "secondary"):
        st.session_state.current_menu = "real_estate"
        st.session_state.current_view = "main"
        st.rerun()

with header_cols[3]:
    st.markdown(f"<div style='text-align: right; padding-top: 8px;'>👤 <b>{st.session_state.username}</b>님</div>", unsafe_allow_html=True)

with header_cols[4]:
    # admin 계정일 때만 API 설정 버튼 노출
    if st.session_state.username == "admin":
        is_api_view = st.session_state.current_view == "api_settings"
        if st.button("⚙️ API", use_container_width=True, type="primary" if is_api_view else "secondary"):
            st.session_state.current_view = "api_settings" if not is_api_view else "main"
            st.rerun()

with header_cols[5]:
    if st.button("로그아웃", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.username = None
        st.session_state.api_keys = {"rtms_key": "", "app_key": "", "app_secret": "", "naver_id": "", "naver_secret": ""}
        st.session_state.current_view = "main"
        st.rerun()

st.divider()

# --- [5. 화면 라우팅 (API 설정 화면 vs 퀀트/부동산 메인 화면)] ---

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
            st.session_state.api_keys = {"rtms_key": rtms, "app_key": a_key, "app_secret": a_sec, "naver_id": n_id, "naver_secret": n_sec}
            st.success("✅ 마스터 5대 보안 자산 데이터가 시스템에 무결점 반영되었습니다.")
        except Exception as e:
            st.error(f"저장 실패: {str(e)}")

else:
    # Top Menu의 버튼 선택(상태)에 따라 화면 렌더링 분기
    if st.session_state.current_menu == "quant":
        stock_quant.run_stock_quant_page(supabase, st.session_state.username)
    elif st.session_state.current_menu == "real_estate":
        real_estate.run_real_estate_page(st.session_state.api_keys["rtms_key"])
