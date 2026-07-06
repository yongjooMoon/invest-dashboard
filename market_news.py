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
    """DB의 시간을 변환 없이 원래 시간대로 파싱하여 가져옵니다."""
    try:
        ts = str(utc_time_str).split(".")[0][:19].replace("T", " ")
        dt_utc = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return dt_utc
    except Exception as e:
        return datetime.utcnow()


# ==========================================
# 상태 컨트롤 콜백 함수
# 팝업 상태 컨트롤 및 콜백 (튕김 현상 완벽 해결)
# ==========================================
def prev_history_day(): 
    st.session_state.history_date -= timedelta(days=1)

def next_history_day(): 
    st.session_state.history_date += timedelta(days=1)

def go_prev_news(): 
    st.session_state.dialog_news_index -= 1
    st.session_state.show_detail_dialog = True # 팝업 유지

def go_next_news(): 
    st.session_state.dialog_news_index += 1
    st.session_state.show_detail_dialog = True # 팝업 유지

# 🌟 캐러셀 전용 콜백: 인덱스 이동 + "이동 방향"을 기록해서 슬라이드 애니메이션에 사용합니다.
def carousel_prev():
    if st.session_state.carousel_idx > 0:
        st.session_state.carousel_idx -= 1
        st.session_state.carousel_dir = "left"

def carousel_next(max_idx):
    if st.session_state.carousel_idx < max_idx:
        st.session_state.carousel_idx += 1
        st.session_state.carousel_dir = "right"


# ==========================================
# 팝업: 뉴스 상세 브리핑 (단일 팝업으로 단순화)
# ==========================================
@st.dialog("📰 뉴스 상세 브리핑")
def news_detail_dialog():
    st.markdown("""
    <style>
    div[role="dialog"] {
        width: 80vw !important; max-width: 850px !important; min-height: 600px !important; height: auto !important; max-height: 90vh !important; border-radius: 16px !important;
        overflow-y: auto !important;
    }
    div[role="dialog"] div[data-testid="stMarkdownContainer"] { padding: 0.5rem 1rem; }
    div[role="dialog"] ::-webkit-scrollbar { width: 8px; }
    div[role="dialog"] ::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.2); border-radius: 4px; }

    /* 🌟 모바일: 팝업 사이즈가 내용과 안 맞던 문제 수정 (고정 min-height 해제, 화면 폭에 맞춤, 넘치면 스크롤) */
    @media (max-width: 640px) {
        div[role="dialog"] {
            width: 94vw !important; min-height: unset !important; max-height: 88vh !important;
            border-radius: 14px !important;
        }
        div[role="dialog"] h2 { font-size: 22px !important; margin-bottom: 18px !important; }
        div[role="dialog"] div[data-testid="stMarkdownContainer"] { padding: 0.25rem 0.5rem; }
    }

    /* 하단 이모지 버튼 투명화 디자인 */
    .emoji-btn-container div[data-testid="stButton"] button {
        background: transparent !important; border: none !important; box-shadow: none !important;
        transition: transform 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275) !important; padding: 0 !important;
    }
    .emoji-btn-container div[data-testid="stButton"] button::before, .emoji-btn-container div[data-testid="stButton"] button::after { display: none !important; }
    .emoji-btn-container div[data-testid="stButton"] button:hover { transform: scale(1.3) !important; background: transparent !important; border: none !important; color: inherit !important; }
    .emoji-btn-container div[data-testid="stButton"] p { font-size: 36px !important; margin: 0 !important; text-align: center !important; }
    </style>
    """, unsafe_allow_html=True)

    news_list = st.session_state.get("dialog_news_list", [])
    idx = st.session_state.get("dialog_news_index", 0)
    
    if not news_list:
        st.error("뉴스를 찾을 수 없습니다.")
        return
        
    news = news_list[idx]

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
    <div style="border-top: 1px solid rgba(255,255,255,0.1); padding-top: 25px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center;">
        <span style="color: #94A3B8; font-size: 16px; font-weight: 700;">AI Sentiment Score</span>
        <span style="color: {color}; font-weight: 900; background-color: {color}1A; padding: 10px 20px; border-radius: 30px; font-size: 17px;">
            {score} / 5 &nbsp;·&nbsp; {status}
        </span>
    </div>
    """, unsafe_allow_html=True)
    
    # 하단 이모지 전용 네비게이션
    nav_col1, empty_col, nav_col2 = st.columns([1, 8, 1])
    with nav_col1:
        if idx > 0: 
            st.markdown("<div class='emoji-btn-container'>", unsafe_allow_html=True)
            st.button("⬅️", on_click=go_prev_news, use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)
    with nav_col2:
        if idx < len(news_list) - 1: 
            st.markdown("<div class='emoji-btn-container'>", unsafe_allow_html=True)
            st.button("➡️", on_click=go_next_news, use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)


# ==========================================
# 뉴스 한 줄(리스트형) 카드를 그리는 공용 함수
# 섹터 탭과 검색결과 뷰에서 동일하게 재사용합니다.
# ==========================================
def render_news_row(news, key_prefix, news_list):
    actual_idx = news_list.index(news)
    dt_kst = get_kst_time(news['created_at'])
    time_str = dt_kst.strftime("%m.%d %H:%M")

    region_text = news.get('region', 'Global')
    reg_color, reg_bg = get_region_style(region_text)

    with st.container():
        st.markdown(f"""
        <div class="clickable-card news-row" style="padding: 16px 20px; margin-bottom: 8px;">
            <div class="news-row-left">
                <span class="row-badge" style="background-color: {reg_bg}; color: {reg_color}; font-weight: 800; font-size: 11px; padding: 4px 8px; border-radius: 4px;">{region_text}</span>
                <span class="row-sector" style="color: #94A3B8; font-size: 14px; font-weight: 700;">· {news['sector_asset']}</span>
                <span class="row-title" style="font-size: 17px; font-weight: 700; color: #E2E8F0; margin-left: 5px;">{news['title']}</span>
            </div>
            <div class="row-time" style="color: #64748B; font-size: 13px; font-weight: 600;">
                {time_str}
            </div>
        </div>
        """, unsafe_allow_html=True)

        if st.button(" ", key=f"{key_prefix}_{news['id']}", use_container_width=True):
            st.session_state.show_detail_dialog = True
            st.session_state.dialog_news_list = news_list
            st.session_state.dialog_news_index = actual_idx
            st.rerun()


# ==========================================
# 메인 뉴스 페이지
# ==========================================
def run_news_page(supabase):
    
    if st.session_state.get("show_detail_dialog", False):
        st.session_state.show_detail_dialog = False 
        news_detail_dialog()

    st.markdown("""
    <style>
    /* 공통 레이아웃 */
    .block-container { max-width: 950px !important; padding-top: 4.5rem !important; padding-bottom: 4rem !important; margin: 0 auto !important; }
    div[data-baseweb="input"] { border-radius: 12px !important; background-color: rgba(255, 255, 255, 0.03) !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; padding: 4px; }
    
    /* 투명 클릭 카드 */
    .clickable-card { border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; background-color: rgba(15, 23, 42, 0.3); transition: all 0.3s ease; position: relative; }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) { position: relative; gap: 0 !important; }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 10; }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] button { width: 100%; height: 100%; opacity: 0; cursor: pointer; background: transparent; border: none; }
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stButton"] button:hover) .clickable-card { background-color: rgba(255, 255, 255, 0.08) !important; border-color: rgba(255, 255, 255, 0.3) !important; transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.2); }
    
    .news-divider { height: 1px; background-color: rgba(255, 255, 255, 0.05); margin: 6px 0; }
    button[data-baseweb="tab"] { font-size: 16px !important; font-weight: 600 !important; }
    
    /* 슬라이드 화살표 버튼 커스텀 */
    .slider-btn div[data-testid="stButton"] > button {
        background-color: rgba(255,255,255,0.05) !important; border: 1px solid rgba(255,255,255,0.1) !important;
        border-radius: 8px !important; padding: 0 !important; font-size: 14px !important; height: 35px !important;
    }
    .slider-btn div[data-testid="stButton"] > button:hover { background-color: rgba(56, 189, 248, 0.2) !important; border-color: rgba(56, 189, 248, 0.5) !important; color: white !important;}

    /* 달력 네비게이션 버튼 디자인 */
    .date-nav-btn div[data-testid="stButton"] > button {
        background-color: rgba(30, 41, 59, 1) !important; border: 1px solid rgba(255, 255, 255, 0.15) !important;
        border-radius: 12px !important; height: 45px !important; transition: all 0.2s ease !important;
    }
    .date-nav-btn div[data-testid="stButton"] > button:hover {
        background-color: rgba(51, 65, 85, 1) !important; border-color: rgba(56, 189, 248, 0.6) !important;
        transform: translateY(-2px) !important; color: white !important;
    }

    /* 🌟 캐러셀 슬라이드 애니메이션: 매 rerun마다 카드가 방향에 맞춰 부드럽게 들어오는 것처럼 보이게 처리 */
    @keyframes slideInRight { from { opacity: 0; transform: translateX(36px); } to { opacity: 1; transform: translateX(0); } }
    @keyframes slideInLeft  { from { opacity: 0; transform: translateX(-36px); } to { opacity: 1; transform: translateX(0); } }
    .carousel-card { animation-duration: 0.38s; animation-timing-function: cubic-bezier(0.22, 1, 0.36, 1); animation-fill-mode: both; }
    .carousel-card.dir-right { animation-name: slideInRight; }
    .carousel-card.dir-left  { animation-name: slideInLeft; }

    /* 🌟 섹터별 뉴스 리스트 한 줄 카드 (기본: PC에서는 한 줄 유지) */
    .news-row { display: flex; justify-content: space-between; align-items: center; }
    .news-row-left { display: flex; align-items: center; gap: 14px; flex: 1; overflow: hidden; }
    .row-badge, .row-sector, .row-time { white-space: nowrap; flex: none; }
    .row-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
    .row-time { margin-left: 16px; }

    /* 🌟 모바일: 제목이 통째로 잘리던 문제 수정 — 배지/시간 줄과 제목 줄을 분리하고 제목은 자연스럽게 줄바꿈 */
    @media (max-width: 640px) {
        .news-row { flex-wrap: wrap; row-gap: 6px; }
        .news-row-left { flex-wrap: wrap; width: 100%; order: 2; flex: none; }
        .row-badge, .row-sector { order: 1; flex: none; }
        .row-title {
            display: block !important; white-space: normal !important; overflow: visible !important;
            text-overflow: clip !important; flex: 0 0 100% !important; width: 100% !important;
            font-size: 15px !important; line-height: 1.45 !important; margin-left: 0 !important; margin-top: 4px !important;
            order: 3;
        }
        .row-time { flex: none; width: 100%; text-align: right; font-size: 11px !important; margin-left: 0 !important; order: 0; }

        /* 상단 캐러셀 카드: 고정 높이 대신 컴팩트하게 */
        .carousel-card { height: auto !important; min-height: 118px !important; padding: 14px !important; }
        .carousel-card div[style*="font-size: 18px"] { font-size: 15px !important; }
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown("<div style='margin-bottom: 10px;'></div>", unsafe_allow_html=True)
    search_query = st.text_input("검색어 입력", placeholder="🔍 뉴스 검색 (제목 또는 내용)", label_visibility="collapsed")
    st.markdown("<div style='margin-bottom: 30px;'></div>", unsafe_allow_html=True)
    
    try:
        res = supabase.table("market_news").select("*").order("created_at", desc=True).limit(200).execute()
        news_list = res.data
    except Exception as e:
        st.error(f"뉴스 데이터를 불러오는 중 오류가 발생했습니다: {e}")
        return
        
    if not news_list:
        st.info("아직 수집된 뉴스가 없습니다.")
        return

    # ==========================================
    # 🔥 상단 고정: 오늘 주요뉴스 (자정 기준 오늘 날짜 데이터 전체 조회)
    # ==========================================
    today_date = (datetime.utcnow() + timedelta(hours=9)).date()
    today_news = [n for n in news_list if get_kst_time(n['created_at']).date() == today_date]
    today_major_news = [n for n in today_news if n.get('is_major') == True]
    
    if "carousel_idx" not in st.session_state:
        st.session_state.carousel_idx = 0
    if "carousel_dir" not in st.session_state:
        st.session_state.carousel_dir = "right"

    # 헤더/버튼에서도 동일하게 쓸 수 있도록 max_idx를 미리 계산
    max_idx = max(0, len(today_major_news) - 2)

    # 헤더 및 슬라이드 버튼 (주요 뉴스가 없을 경우 버튼 숨김 처리)
    col_h1, col_h2, col_h3 = st.columns([7.6, 0.7, 0.7], vertical_alignment="bottom")
    with col_h1:
        st.markdown("<h3 style='margin-bottom: 0px; font-weight: 800;'>🔥 오늘 주요뉴스</h3>", unsafe_allow_html=True)
    with col_h2:
        if today_major_news: # 주요 뉴스가 있을 때만 버튼 렌더링
            st.markdown("<div class='slider-btn'>", unsafe_allow_html=True)
            st.button(
                "◀", disabled=(st.session_state.carousel_idx <= 0),
                use_container_width=True, on_click=carousel_prev,
            )
            st.markdown("</div>", unsafe_allow_html=True)
    with col_h3:
        if today_major_news: # 주요 뉴스가 있을 때만 버튼 렌더링
            st.markdown("<div class='slider-btn'>", unsafe_allow_html=True)
            st.button(
                "▶", disabled=(st.session_state.carousel_idx >= max_idx),
                use_container_width=True, on_click=carousel_next, args=(max_idx,),
            )
            st.markdown("</div>", unsafe_allow_html=True)
            
    st.markdown("<div style='margin-bottom: 16px;'></div>", unsafe_allow_html=True)
    
    if today_major_news:
        display_top_news = today_major_news[st.session_state.carousel_idx : st.session_state.carousel_idx + 2]
        cols = st.columns(2)
        dir_class = "dir-right" if st.session_state.carousel_dir == "right" else "dir-left"
        
        for idx, news in enumerate(display_top_news):
            actual_idx = news_list.index(news)
            dt_kst = get_kst_time(news['created_at'])
            time_str = dt_kst.strftime("%H:%M")
            region_text = news.get('region', 'Global')
            reg_color, reg_bg = get_region_style(region_text)
            
            with cols[idx]:
                with st.container():
                    st.markdown(f"""
                    <div class="clickable-card carousel-card {dir_class}" style="height: 160px; padding: 20px; display: flex; flex-direction: column; justify-content: space-between;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="background-color: {reg_bg}; color: {reg_color}; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: 800;">{region_text}</span>
                            <span style="color: #64748B; font-size: 12px; font-weight: 600;">{time_str}</span>
                        </div>
                        <div style="font-size: 18px; font-weight: 800; color: #F8FAFC; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                            {news['title']}
                        </div>
                        <div>
                            <span style="background-color: rgba(255,255,255,0.05); color: #94A3B8; font-size: 11px; padding: 4px 10px; border-radius: 12px; font-weight: 700;">#{news['sector_asset']}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button(" ", key=f"main_top_{news['id']}", use_container_width=True):
                        st.session_state.show_detail_dialog = True
                        st.session_state.dialog_news_list = news_list
                        st.session_state.dialog_news_index = actual_idx
                        st.rerun() 
    else:
        # 🌟 주요 뉴스가 없을 경우 빈 카드 대신 깔끔한 안내문 표출
        st.info("오늘 수집된 새로운 주요 뉴스가 없습니다. (배치 대기 중)")
        
    st.markdown("<hr style='border-color: rgba(255,255,255,0.05); margin: 40px 0 20px 0;'>", unsafe_allow_html=True)
        
    # ==========================================
    # 📄 하단: 탭 영역 (기본 활성 탭: "전체" 설정 완료)
    # 🌟 검색어가 있으면 탭 자체를 숨기고, 항상 "전체" 기준 통합 검색결과를 보여줍니다.
    #    (st.tabs는 어느 탭이 열려있는지를 코드에서 강제로 바꿀 수 없어서,
    #     검색 중에는 탭 UI 대신 통합 뷰로 전환하는 방식으로 처리했습니다.)
    # ==========================================
    st.markdown("<h3 style='margin-bottom: 20px; font-weight: 800;'>📌 섹터별 최신 뉴스</h3>", unsafe_allow_html=True)

    if search_query:
        q = search_query.lower()
        matched = [
            n for n in news_list
            if q in n['title'].lower() or q in n['summary'].lower()
        ]
        st.caption(f"🔍 '{search_query}' 검색결과 {len(matched)}건 · 전체 섹터 기준")

        if not matched:
            st.warning(f"'{search_query}' 검색어에 해당하는 뉴스가 없습니다.")
        else:
            for news in matched:
                render_news_row(news, key_prefix="search", news_list=news_list)

    else:
        # 🌟 "전체" 탭을 가장 첫 번째 위치로 정렬하고 "🔥 주요뉴스" 탭을 가장 마지막으로 정렬!
        sectors = ["전체"]
        unique_sectors = set([n['sector_asset'].split('-')[0] if '-' in n['sector_asset'] else n['sector_asset'] for n in news_list])
        sectors.extend(sorted(list(unique_sectors)))
        sectors.append("🔥 주요뉴스") # 주요 뉴스를 맨 뒤로 배치 완료

        tabs = st.tabs(sectors)

        for i, tab in enumerate(tabs):
            with tab:
                current_sector = sectors[i]

                # ----------------------------------------
                # [탭 A] 주요뉴스 히스토리 및 달력 조회 (가장 뒤에 위치함)
                # ----------------------------------------
                if current_sector == "🔥 주요뉴스":
                    if "history_date" not in st.session_state:
                        st.session_state.history_date = (datetime.utcnow() + timedelta(hours=9)).date()

                    c1, c2, c3 = st.columns([1, 2, 1], vertical_alignment="center")
                    with c1:
                        st.markdown("<div class='date-nav-btn'>", unsafe_allow_html=True)
                        st.button("◀ 이전일", on_click=prev_history_day, key="btn_prev_day", use_container_width=True)
                        st.markdown("</div>", unsafe_allow_html=True)
                    with c2:
                        selected_date = st.date_input("날짜 선택", value=st.session_state.history_date, label_visibility="collapsed")
                        if selected_date != st.session_state.history_date:
                            st.session_state.history_date = selected_date
                            st.rerun()
                    with c3:
                        st.markdown("<div class='date-nav-btn'>", unsafe_allow_html=True)
                        st.button("다음일 ▶", on_click=next_history_day, key="btn_next_day", use_container_width=True)
                        st.markdown("</div>", unsafe_allow_html=True)

                    st.markdown("<hr style='margin: 15px 0 20px 0; border-color: rgba(255,255,255,0.05);'>", unsafe_allow_html=True)

                    target_date = st.session_state.history_date
                    day_news = [n for n in news_list if get_kst_time(n['created_at']).date() == target_date]

                    # DB에서 해당 날짜에 수집된 뉴스 중 is_major가 True인 것만 표출
                    major_news_list = [n for n in day_news if n.get('is_major') == True]

                    if not major_news_list:
                        st.info(f"{target_date.strftime('%Y년 %m월 %d일')}에 수집된 주요 뉴스가 없습니다.")
                    else:
                        for idx_in_day, news in enumerate(major_news_list):
                            actual_idx = news_list.index(news)
                            dt_kst = get_kst_time(news['created_at'])
                            time_str = dt_kst.strftime("%H:%M")
                            region_text = news.get('region', 'Global')
                            reg_color, reg_bg = get_region_style(region_text)

                            with st.container():
                                st.markdown(f"""
                                <div class="clickable-card" style="padding: 18px 24px; margin-bottom: 12px;">
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

                                # 상세 보기 호출 (단일 팝업)
                                if st.button(" ", key=f"hist_card_btn_{news['id']}", use_container_width=True):
                                    st.session_state.show_detail_dialog = True
                                    st.session_state.dialog_news_list = news_list # 네비게이션을 위해 전체 리스트 전달
                                    st.session_state.dialog_news_index = actual_idx
                                    st.rerun()

                # ----------------------------------------
                # [탭 B] 일반 섹터별 뉴스 (검색 중이 아닐 때만 이 분기로 들어옴)
                # ----------------------------------------
                else:
                    tab_filtered_news = []
                    for n in news_list:
                        n_sector_group = n['sector_asset'].split('-')[0] if '-' in n['sector_asset'] else n['sector_asset']
                        if current_sector == "전체" or n_sector_group == current_sector:
                            tab_filtered_news.append(n)

                    if not tab_filtered_news:
                        st.warning("해당 섹터의 뉴스가 없습니다.")
                        continue

                    for news in tab_filtered_news:
                        render_news_row(news, key_prefix=f"list_{current_sector}", news_list=news_list)
