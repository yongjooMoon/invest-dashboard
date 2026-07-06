import streamlit as st
import re
from datetime import datetime, timedelta

# ==========================================
# 헬퍼 함수: 지역(Region) 스타일 & 시간 변환
# ==========================================
def get_region_style(region):
    r = str(region).upper()
    if "US" in r: return "#F87171", "rgba(248, 113, 113, 0.15)"      # 미국: 레드 
    elif "KR" in r: return "#60A5FA", "rgba(96, 165, 250, 0.15)"    # 한국: 블루 
    elif "JP" in r: return "#34D399", "rgba(52, 211, 153, 0.15)"    # 일본: 그린
    elif "HK" in r or "CN" in r: return "#FBBF24", "rgba(251, 191, 36, 0.15)" # 홍콩/중국: 옐로우
    elif "GLOBAL" in r: return "#A78BFA", "rgba(167, 139, 250, 0.15)"# 글로벌: 퍼플
    else: return "#94A3B8", "rgba(148, 163, 184, 0.15)"             # 기타: 그레이

def get_kst_time(utc_time_str):
    """DB의 UTC 시간을 KST(한국 시간, +9시간)로 정확하게 변환합니다."""
    try:
        ts = str(utc_time_str).split(".")[0][:19].replace("T", " ")
        dt_utc = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return dt_utc + timedelta(hours=9)
    except Exception as e:
        return datetime.utcnow() + timedelta(hours=9)


# ==========================================
# 팝업 상태 컨트롤 콜백 함수 모음 (st.rerun 튕김 방지)
# ==========================================
def change_history_date(delta):
    st.session_state.history_date_input += timedelta(days=delta)

def go_prev_news():
    st.session_state.dialog_news_index -= 1

def go_next_news():
    st.session_state.dialog_news_index += 1

def go_back_to_history():
    st.session_state.modal_view = "history"

def open_detail_from_history(news_list, idx):
    st.session_state.modal_view = "detail"
    st.session_state.dialog_news_list = news_list
    st.session_state.dialog_news_index = idx
    st.session_state.modal_back_visible = True


# ==========================================
# 🌟 통합 팝업 다이얼로그 (중첩 에러 해결 Router)
# ==========================================
@st.dialog("📰 마켓 뉴스 센터")
def master_news_dialog(news_list_full):
    st.markdown("""
    <style>
    /* 공통 모달 윈도우 사이즈 고정 */
    div[role="dialog"] {
        width: 90vw !important; max-width: 900px !important; height: 85vh !important; min-height: 650px !important; border-radius: 16px !important;
    }
    div[role="dialog"] div[data-testid="stMarkdownContainer"] { padding: 0.5rem 1rem; }
    div[role="dialog"] ::-webkit-scrollbar { width: 8px; }
    div[role="dialog"] ::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.2); border-radius: 4px; }
    
    /* 네비게이션 버튼 공통 테마 (투명 + 호버 애니메이션) */
    .nav-btn-container div[data-testid="stButton"] > button {
        background-color: rgba(30, 41, 59, 1) !important; border: 1px solid rgba(255, 255, 255, 0.15) !important;
        border-radius: 12px !important; height: 45px !important; transition: all 0.2s ease !important;
    }
    .nav-btn-container div[data-testid="stButton"] > button:hover {
        background-color: rgba(51, 65, 85, 1) !important; border-color: rgba(56, 189, 248, 0.6) !important;
        transform: translateY(-2px) !important; color: white !important;
    }
    </style>
    """, unsafe_allow_html=True)

    view = st.session_state.get("modal_view", "detail")

    # ----------------------------------------
    # [뷰 A] 날짜별 전체 주요뉴스 히스토리 화면
    # ----------------------------------------
    if view == "history":
        if "history_date_input" not in st.session_state:
            st.session_state.history_date_input = (datetime.utcnow() + timedelta(hours=9)).date()
            
        c1, c2, c3 = st.columns([1, 2, 1], vertical_alignment="center")
        with c1: 
            st.markdown("<div class='nav-btn-container'>", unsafe_allow_html=True)
            st.button("◀ 이전일", on_click=change_history_date, args=(-1,), use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)
        with c2: 
            # 🌟 완벽한 달력 팝업 연동
            st.date_input("날짜 선택", key="history_date_input", label_visibility="collapsed")
        with c3: 
            st.markdown("<div class='nav-btn-container'>", unsafe_allow_html=True)
            st.button("다음일 ▶", on_click=change_history_date, args=(1,), use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)
        
        st.markdown("<hr style='margin: 15px 0 20px 0; border-color: rgba(255,255,255,0.1);'>", unsafe_allow_html=True)
        
        target_date = st.session_state.history_date_input
        # 선택한 날짜에 맞는 뉴스 필터링
        day_news = [n for n in news_list_full if get_kst_time(n['created_at']).date() == target_date]
        major_news = day_news[:10] # Mock: 일단 해당 날짜 최대 10개 출력 (추후 AI 태그로 필터링)
        
        if not major_news:
            st.info(f"{target_date.strftime('%Y년 %m월 %d일')}에 수집된 주요 뉴스가 없습니다.")
        else:
            for idx_in_day, news in enumerate(major_news):
                dt_kst = get_kst_time(news['created_at'])
                time_str = dt_kst.strftime("%H:%M")
                region_text = news.get('region', 'Global')
                reg_color, reg_bg = get_region_style(region_text)
                
                with st.container():
                    st.markdown(f"""
                    <div class="clickable-card" style="padding: 18px 24px; margin-bottom: 12px; background-color: rgba(255,255,255,0.02); border-radius: 12px; border: 1px solid rgba(255,255,255,0.05);">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                            <div>
                                <span style="background-color: {reg_bg}; color: {reg_color}; font-size: 11px; padding: 4px 8px; border-radius: 4px; font-weight: 800;">SAVE · {region_text}</span>
                                <span style="color: #94A3B8; font-size: 13px; font-weight: 600; margin-left: 8px;">· {news['sector_asset']}</span>
                            </div>
                            <span style="color: #64748B; font-size: 13px; font-weight: 600;">{time_str}</span>
                        </div>
                        <div style="font-size: 18px; font-weight: 800; color: #F8FAFC; line-height: 1.4;">
                            {news['title']}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # 카드 클릭 시, 팝업을 끄지 않고 상세 뷰로 전환하는 콜백 실행
                    if st.button(" ", key=f"hist_btn_{news['id']}", use_container_width=True):
                        open_detail_from_history(major_news, idx_in_day)
                        st.rerun()

    # ----------------------------------------
    # [뷰 B] 특정 뉴스 상세 브리핑 화면
    # ----------------------------------------
    elif view == "detail":
        news_list = st.session_state.get("dialog_news_list", [])
        idx = st.session_state.get("dialog_news_index", 0)
        
        if not news_list:
            st.error("뉴스를 찾을 수 없습니다.")
            return
            
        news = news_list[idx]

        # 🌟 '전체보기(히스토리)'에서 넘어온 경우, 뒤로 가기 버튼 제공
        if st.session_state.get("modal_back_visible", False):
            st.button("🔙 목록으로 돌아가기", on_click=go_back_to_history)
            st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)

        dt_kst = get_kst_time(news['created_at'])
        time_str = dt_kst.strftime("%y.%m.%d. %H:%M")

        region_text = news.get('region', 'Global')
        reg_color, reg_bg = get_region_style(region_text)
        sector = news.get('sector_asset', 'News')
        
        st.markdown(f"""
        <div style="color: #94A3B8; font-size: 15px; margin-bottom: 20px; display:flex; gap: 12px; align-items:center;">
            <span style="background-color: {reg_bg}; color: {reg_color}; padding: 4px 12px; border-radius: 6px; font-weight: 800;">{region_text}</span>
            <span>·</span><span style="font-weight: 700;">{sector}</span><span>·</span><span>{time_str}</span>
        </div>
        <h2 style="color: #F8FAFC; margin-top: 0; margin-bottom: 30px; font-weight: 900; line-height: 1.4; font-size: 32px;">{news['title']}</h2>
        """, unsafe_allow_html=True)
        
        summary_text = re.sub(r'(\d\.)', r'<br><br>\1', news['summary'])
        if summary_text.startswith('<br><br>'): summary_text = summary_text[8:]
            
        st.markdown(f"""
        <div style="background: linear-gradient(145deg, rgba(30,58,138,0.2), rgba(15,23,42,0.6)); border: 1px solid rgba(56,189,248,0.2); padding: 30px; border-radius: 12px; margin-bottom: 35px;">
            <h4 style="color: #38BDF8; margin-top: 0; margin-bottom: 15px; font-size: 18px;">✨ AI 핵심 요약</h4>
            <div style="color: #E2E8F0; line-height: 1.8; font-size: 17px;">{summary_text}</div>
        </div>
        """, unsafe_allow_html=True)
        
        score = news['sentiment_score']
        if score <= 2: color, status = "#EF4444", "Bearish (부정적)"
        elif score == 3: color, status = "#F59E0B", "Neutral (중립)"
        else: color, status = "#10B981", "Bullish (긍정적)"
            
        st.markdown(f"""
        <div style="border-top: 1px solid rgba(255,255,255,0.1); padding-top: 25px; display: flex; justify-content: space-between; align-items: center;">
            <span style="color: #94A3B8; font-size: 16px; font-weight: 700;">AI Sentiment Score</span>
            <span style="color: {color}; font-weight: 900; background-color: {color}1A; padding: 10px 20px; border-radius: 30px; font-size: 17px;">
                {score} / 5 &nbsp;·&nbsp; {status}
            </span>
        </div>
        <div style='margin-top: 40px;'></div>
        """, unsafe_allow_html=True)
        
        # 하단 이모지 전용 네비게이션
        nav_col1, empty_col, nav_col2 = st.columns([1, 8, 1])
        with nav_col1:
            if idx > 0: st.button("⬅️", on_click=go_prev_news, use_container_width=True)
        with nav_col2:
            if idx < len(news_list) - 1: st.button("➡️", on_click=go_next_news, use_container_width=True)


# ==========================================
# 메인 뉴스 페이지 (화면 라우터)
# ==========================================
def run_news_page(supabase):
    st.markdown("""
    <style>
    /* 🌟 공통 CSS (너비 950px 최적화 및 카드 오버레이 클릭 마법) */
    .block-container { max-width: 950px !important; padding-top: 4.5rem !important; padding-bottom: 4rem !important; margin: 0 auto !important; }
    div[data-baseweb="input"] { border-radius: 12px !important; background-color: rgba(255, 255, 255, 0.03) !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; padding: 4px; }
    
    .clickable-card { border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; background-color: rgba(15, 23, 42, 0.3); transition: all 0.3s ease; position: relative; }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) { position: relative; gap: 0 !important; }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 10; }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] button { width: 100%; height: 100%; opacity: 0; cursor: pointer; background: transparent; border: none; }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stButton"] button:hover) .clickable-card { background-color: rgba(255, 255, 255, 0.08) !important; border-color: rgba(255, 255, 255, 0.3) !important; transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.2); }
    
    .news-divider { height: 1px; background-color: rgba(255, 255, 255, 0.05); margin: 6px 0; }
    button[data-baseweb="tab"] { font-size: 16px !important; font-weight: 600 !important; }
    
    /* 🌟 오늘 주요뉴스 전용 가로 스크롤(Slider) 강제 적용 마법 CSS */
    div[data-testid="stHorizontalBlock"]:has(.top-news-card) {
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
        padding-bottom: 15px !important;
        scroll-behavior: smooth;
    }
    div[data-testid="stHorizontalBlock"]:has(.top-news-card) > div[data-testid="column"] {
        min-width: 280px !important;
        max-width: 280px !important;
        flex: 0 0 auto !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.top-news-card)::-webkit-scrollbar { height: 8px; }
    div[data-testid="stHorizontalBlock"]:has(.top-news-card)::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.2); border-radius: 4px; }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown("<div style='margin-bottom: 10px;'></div>", unsafe_allow_html=True)
    search_query = st.text_input("검색어 입력", placeholder="🔍 뉴스 검색 (제목 또는 내용)", label_visibility="collapsed")
    st.markdown("<div style='margin-bottom: 30px;'></div>", unsafe_allow_html=True)
    
    try:
        res = supabase.table("market_news").select("*").order("created_at", desc=True).limit(100).execute()
        news_list = res.data
    except Exception as e:
        st.error(f"뉴스 데이터를 불러오는 중 오류가 발생했습니다: {e}")
        return
        
    if not news_list:
        st.info("아직 수집된 뉴스가 없습니다.")
        return

    # ==========================================
    # 🔥 상단 고정: 오늘 주요뉴스 (가로 슬라이드 스크롤 적용)
    # ==========================================
    now_kst = datetime.utcnow() + timedelta(hours=9)
    cutoff_time = now_kst.replace(hour=8, minute=30, second=0, microsecond=0)
    if now_kst < cutoff_time:
        cutoff_time -= timedelta(days=1)
        
    today_news = [n for n in news_list if get_kst_time(n['created_at']) >= cutoff_time]
    display_top_news = today_news[:5] # 가로 스크롤 테스트를 위해 임의로 최대 5개 추출
    
    col_h1, col_h2 = st.columns([8, 2], vertical_alignment="bottom")
    with col_h1:
        st.markdown("<h3 style='margin-bottom: 0px; font-weight: 800;'>🔥 오늘 주요뉴스</h3>", unsafe_allow_html=True)
    with col_h2:
        if st.button("전체보기 🔍", use_container_width=True):
            st.session_state.modal_view = "history"
            master_news_dialog(news_list)
            
    st.markdown("<div style='margin-bottom: 16px;'></div>", unsafe_allow_html=True)
    
    if display_top_news:
        # 가로 스크롤이 발동하도록 리스트 길이만큼 컬럼 생성 (CSS로 인해 가로로 쭉 나열됨)
        cols = st.columns(len(display_top_news))
        for idx, news in enumerate(display_top_news):
            actual_idx = news_list.index(news)
            dt_kst = get_kst_time(news['created_at'])
            time_str = dt_kst.strftime("%H:%M")
            region_text = news.get('region', 'Global')
            reg_color, reg_bg = get_region_style(region_text)
            
            with cols[idx]:
                with st.container():
                    # 클래스에 top-news-card 를 추가하여 가로 스크롤 CSS의 타겟이 되도록 함
                    st.markdown(f"""
                    <div class="top-news-card clickable-card" style="height: 160px; padding: 20px; display: flex; flex-direction: column; justify-content: space-between;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="background-color: {reg_bg}; color: {reg_color}; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: 800;">{region_text}</span>
                            <span style="color: #64748B; font-size: 12px; font-weight: 600;">{time_str}</span>
                        </div>
                        <div style="font-size: 17px; font-weight: 800; color: #F8FAFC; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                            {news['title']}
                        </div>
                        <div>
                            <span style="background-color: rgba(255,255,255,0.05); color: #94A3B8; font-size: 11px; padding: 4px 10px; border-radius: 12px; font-weight: 700;">#{news['sector_asset']}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button(" ", key=f"main_top_{news['id']}", use_container_width=True):
                        st.session_state.modal_view = "detail"
                        st.session_state.dialog_news_list = news_list
                        st.session_state.dialog_news_index = actual_idx
                        st.session_state.modal_back_visible = False
                        master_news_dialog(news_list)
    else:
        st.info("오늘 오전 8:30 이후 수집된 새로운 주요 뉴스를 대기 중입니다.")
        
    st.markdown("<hr style='border-color: rgba(255,255,255,0.05); margin: 40px 0 20px 0;'>", unsafe_allow_html=True)
        
    # ==========================================
    # 📄 하단: 섹터별 최신 뉴스 탭
    # ==========================================
    st.markdown("<h3 style='margin-bottom: 20px; font-weight: 800;'>📌 섹터별 최신 뉴스</h3>", unsafe_allow_html=True)
    
    sectors = ["전체"]
    unique_sectors = set([n['sector_asset'].split('-')[0] if '-' in n['sector_asset'] else n['sector_asset'] for n in news_list])
    sectors.extend(sorted(list(unique_sectors)))
    
    tabs = st.tabs(sectors)
    
    for i, tab in enumerate(tabs):
        with tab:
            current_sector = sectors[i]
            
            tab_filtered_news = []
            for n in news_list:
                n_sector_group = n['sector_asset'].split('-')[0] if '-' in n['sector_asset'] else n['sector_asset']
                match_category = current_sector == "전체" or n_sector_group == current_sector
                match_search = not search_query or search_query.lower() in n['title'].lower() or search_query.lower() in n['summary'].lower()
                
                if match_category and match_search:
                    tab_filtered_news.append(n)
            
            if not tab_filtered_news:
                st.warning(f"'{search_query}' 검색어에 해당하는 뉴스가 없습니다." if search_query else "해당 섹터의 뉴스가 없습니다.")
                continue

            for news in tab_filtered_news:
                actual_idx = tab_filtered_news.index(news)
                dt_kst = get_kst_time(news['created_at'])
                time_str = dt_kst.strftime("%m.%d %H:%M") 
                
                region_text = news.get('region', 'Global')
                reg_color, reg_bg = get_region_style(region_text)
                
                with st.container():
                    st.markdown(f"""
                    <div class="clickable-card" style="padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <div style="display: flex; align-items: center; gap: 14px; flex: 1; overflow: hidden;">
                            <span style="background-color: {reg_bg}; color: {reg_color}; font-weight: 800; font-size: 11px; padding: 4px 8px; border-radius: 4px; white-space: nowrap;">{region_text}</span>
                            <span style="color: #94A3B8; font-size: 14px; font-weight: 700; white-space: nowrap;">· {news['sector_asset']}</span>
                            <span style="font-size: 17px; font-weight: 700; color: #E2E8F0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-left: 5px;">{news['title']}</span>
                        </div>
                        <div style="color: #64748B; font-size: 13px; font-weight: 600; white-space: nowrap; margin-left: 16px;">
                            {time_str}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button(" ", key=f"list_btn_{current_sector}_{news['id']}", use_container_width=True):
                        st.session_state.modal_view = "detail"
                        st.session_state.dialog_news_list = tab_filtered_news
                        st.session_state.dialog_news_index = actual_idx
                        st.session_state.modal_back_visible = False
                        master_news_dialog(news_list)
