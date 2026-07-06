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
        # ISO 포맷 변수 통일화 및 파싱
        ts = str(utc_time_str).split(".")[0][:19].replace("T", " ")
        dt_utc = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return dt_utc + timedelta(hours=9)
    except Exception as e:
        return datetime.utcnow() + timedelta(hours=9)


# ==========================================
# 팝업 1: 상세 브리핑 모달창 (가장 위에 뜨는 창)
# ==========================================
@st.dialog("📰 뉴스 상세 브리핑")
def news_detail_dialog():
    st.markdown("""
    <style>
    div[role="dialog"] {
        width: 90vw !important; max-width: 900px !important; height: 85vh !important; min-height: 650px !important; border-radius: 16px !important;
    }
    div[role="dialog"] div[data-testid="stMarkdownContainer"] { padding: 0.5rem 1rem; }
    div[role="dialog"] ::-webkit-scrollbar { width: 8px; }
    div[role="dialog"] ::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.2); border-radius: 4px; }
    
    /* 팝업 내부 버튼 공통 디자인 */
    div[role="dialog"] div[data-testid="stButton"] > button {
        background-color: rgba(30, 41, 59, 1) !important; border: 1px solid rgba(255, 255, 255, 0.15) !important;
        border-radius: 12px !important; height: 45px !important; transition: all 0.2s ease !important;
    }
    div[role="dialog"] div[data-testid="stButton"] > button:hover {
        background-color: rgba(51, 65, 85, 1) !important; border-color: rgba(56, 189, 248, 0.6) !important;
        transform: translateY(-2px) !important; color: white !important;
    }
    </style>
    """, unsafe_allow_html=True)

    news_list = st.session_state.get("dialog_news_list", [])
    idx = st.session_state.get("dialog_news_index", 0)
    
    if not news_list:
        st.error("뉴스를 찾을 수 없습니다.")
        return
        
    news = news_list[idx]

    def go_prev(): st.session_state.dialog_news_index -= 1
    def go_next(): st.session_state.dialog_news_index += 1

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
    
    nav_col1, empty_col, nav_col2 = st.columns([1, 8, 1])
    with nav_col1:
        if idx > 0: st.button("⬅️", on_click=go_prev, use_container_width=True)
    with nav_col2:
        if idx < len(news_list) - 1: st.button("➡️", on_click=go_next, use_container_width=True)


# ==========================================
# 팝업 2: 주요뉴스 전체보기 히스토리 모달창 (날짜 네비게이션)
# ==========================================
@st.dialog("🔥 전체 주요뉴스 모아보기")
def top_news_history_dialog(news_list):
    # 날짜 세션 초기화 (기본값 오늘)
    if "history_date" not in st.session_state:
        st.session_state.history_date = (datetime.utcnow() + timedelta(hours=9)).date()
        
    # 날짜 네비게이션 컨트롤러
    c1, c2, c3, c4 = st.columns([1, 4, 1, 2], vertical_alignment="center")
    with c1:
        if st.button("◀ 이전", use_container_width=True): 
            st.session_state.history_date -= timedelta(days=1); st.rerun()
    with c2:
        st.markdown(f"<h3 style='text-align:center; margin:0; font-weight:800;'>{st.session_state.history_date.strftime('%Y.%m.%d')} 🗓️</h3>", unsafe_allow_html=True)
    with c3:
        if st.button("다음 ▶", use_container_width=True): 
            st.session_state.history_date += timedelta(days=1); st.rerun()
    with c4:
        if st.button("오늘로 이동", use_container_width=True): 
            st.session_state.history_date = (datetime.utcnow() + timedelta(hours=9)).date(); st.rerun()
    
    st.markdown("<hr style='margin: 15px 0 20px 0; border-color: rgba(255,255,255,0.1);'>", unsafe_allow_html=True)
    
    target_date = st.session_state.history_date
    
    # 해당 날짜의 뉴스만 필터링 (일단 모든 뉴스를 띄움 -> 추후 AI 주요뉴스 컬럼으로 필터 예정)
    day_news = [n for n in news_list if get_kst_time(n['created_at']).date() == target_date]
    major_news = day_news[:10] # Mock: 임의로 해당 날짜의 최대 10개 표출
    
    if not major_news:
        st.info(f"{target_date.strftime('%Y년 %m월 %d일')}에 수집된 주요 뉴스가 없습니다.")
        return
        
    for news in major_news:
        actual_idx = news_list.index(news) # 전체 리스트에서의 진짜 인덱스
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
            
            # 🔥 여기서 버튼을 누르면 상세 팝업이 그 위로 중첩되어 뜹니다.
            if st.button(" ", key=f"hist_btn_{news['id']}", use_container_width=True):
                st.session_state.dialog_news_list = news_list
                st.session_state.dialog_news_index = actual_idx
                news_detail_dialog()


# ==========================================
# 메인 뉴스 페이지 (라우터)
# ==========================================
def run_news_page(supabase):
    st.markdown("""
    <style>
    /* 🌟 공통 CSS (너비 최적화 및 카드 클릭 해킹) */
    .block-container { max-width: 950px !important; padding-top: 4.5rem !important; padding-bottom: 4rem !important; margin: 0 auto !important; }
    div[data-baseweb="input"] { border-radius: 12px !important; background-color: rgba(255, 255, 255, 0.03) !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; padding: 4px; }
    .clickable-card { border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; background-color: rgba(15, 23, 42, 0.3); transition: all 0.3s ease; position: relative; }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) { position: relative; gap: 0 !important; }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 10; }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] button { width: 100%; height: 100%; opacity: 0; cursor: pointer; background: transparent; border: none; }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stButton"] button:hover) .clickable-card { background-color: rgba(255, 255, 255, 0.08) !important; border-color: rgba(255, 255, 255, 0.3) !important; transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.2); }
    .news-divider { height: 1px; background-color: rgba(255, 255, 255, 0.05); margin: 6px 0; }
    button[data-baseweb="tab"] { font-size: 16px !important; font-weight: 600 !important; }
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
    # 🔥 상단 고정: 오늘 주요뉴스 (탭 무관하게 무조건 노출, 08:30 기준)
    # ==========================================
    now_kst = datetime.utcnow() + timedelta(hours=9)
    # 배치 시간인 08:30 기준으로 사이클 컷오프 계산
    cutoff_time = now_kst.replace(hour=8, minute=30, second=0, microsecond=0)
    if now_kst < cutoff_time:
        cutoff_time -= timedelta(days=1)
        
    # KST 기준으로 컷오프 이후에 수집된 뉴스만 오늘의 주요 뉴스로 취급
    today_news = [n for n in news_list if get_kst_time(n['created_at']) >= cutoff_time]
    display_top_news = today_news[:5] # TODO: 추후 AI 주요뉴스 플래그 연동 시 조건 추가
    
    col_h1, col_h2 = st.columns([8, 2], vertical_alignment="bottom")
    with col_h1:
        st.markdown("<h3 style='margin-bottom: 0px; font-weight: 800;'>🔥 오늘 주요뉴스</h3>", unsafe_allow_html=True)
    with col_h2:
        if st.button("전체보기 🔍", use_container_width=True):
            top_news_history_dialog(news_list)
            
    st.markdown("<div style='margin-bottom: 16px;'></div>", unsafe_allow_html=True)
    
    if display_top_news:
        # 가로로 최대 4개까지만 화면에 나열하고, 넘어가는 건 '전체보기'에서 보도록 처리
        cols = st.columns(min(4, len(display_top_news)))
        for idx, news in enumerate(display_top_news[:4]):
            actual_idx = news_list.index(news)
            dt_kst = get_kst_time(news['created_at'])
            time_str = dt_kst.strftime("%H:%M")
            region_text = news.get('region', 'Global')
            reg_color, reg_bg = get_region_style(region_text)
            
            with cols[idx]:
                with st.container():
                    st.markdown(f"""
                    <div class="clickable-card" style="height: 150px; padding: 20px; display: flex; flex-direction: column; justify-content: space-between;">
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
                        st.session_state.dialog_news_list = news_list
                        st.session_state.dialog_news_index = actual_idx
                        news_detail_dialog()
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
                actual_idx = news_list.index(news)
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
                        st.session_state.dialog_news_list = news_list
                        st.session_state.dialog_news_index = actual_idx
                        news_detail_dialog()
