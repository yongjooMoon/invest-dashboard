import streamlit as st
import re
from datetime import datetime

# ==========================================
# 헬퍼 함수: 지역(Region)별 색상 자동 지정
# ==========================================
def get_region_style(region):
    r = str(region).upper()
    if "US" in r: return "#F87171", "rgba(248, 113, 113, 0.15)"      # 미국: 레드 (변경됨)
    elif "KR" in r: return "#60A5FA", "rgba(96, 165, 250, 0.15)"    # 한국: 블루 (변경됨)
    elif "JP" in r: return "#34D399", "rgba(52, 211, 153, 0.15)"    # 일본: 그린
    elif "HK" in r or "CN" in r: return "#FBBF24", "rgba(251, 191, 36, 0.15)" # 홍콩/중국: 옐로우
    elif "GLOBAL" in r: return "#A78BFA", "rgba(167, 139, 250, 0.15)"# 글로벌: 퍼플
    else: return "#94A3B8", "rgba(148, 163, 184, 0.15)"             # 기타: 그레이


# ==========================================
# 팝업 다이얼로그 (상세 브리핑 모달창)
# ==========================================
@st.dialog("📰 뉴스 상세 브리핑")
def news_detail_dialog():
    # 🌟 팝업창 크기를 화면 꽉 차게 대폭 확장 (90vw, 85vh)
    st.markdown("""
    <style>
    div[data-testid="stModal"] > div[role="dialog"] {
        width: 90vw !important;
        max-width: 1000px !important;
        height: 85vh !important; 
        min-height: 650px !important;
        border-radius: 16px !important;
    }
    div[data-testid="stModal"] div[data-testid="stMarkdownContainer"] {
        padding: 0.5rem 1rem;
    }
    div[data-testid="stModal"] > div[role="dialog"] ::-webkit-scrollbar {
        width: 8px;
    }
    div[data-testid="stModal"] > div[role="dialog"] ::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.2);
        border-radius: 4px;
    }
    </style>
    """, unsafe_allow_html=True)

    news_list = st.session_state.get("dialog_news_list", [])
    idx = st.session_state.get("dialog_news_index", 0)
    
    if not news_list:
        st.error("뉴스를 찾을 수 없습니다.")
        return
        
    news = news_list[idx]

    # 🌟 팝업이 닫히지 않는 콜백 방식의 네비게이션 함수
    def go_prev(): st.session_state.dialog_news_index -= 1
    def go_next(): st.session_state.dialog_news_index += 1

    # 시간 및 지역 스타일 포맷팅
    try:
        dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
        time_str = dt.strftime("%y.%m.%d. %H:%M")
    except:
        time_str = news['created_at']

    region_text = news.get('region', 'Global')
    reg_color, reg_bg = get_region_style(region_text)
    sector = news.get('sector_asset', 'News')
    
    st.markdown(f"""
    <div style="color: #94A3B8; font-size: 15px; margin-bottom: 20px; display:flex; gap: 12px; align-items:center;">
        <span style="background-color: {reg_bg}; color: {reg_color}; padding: 4px 12px; border-radius: 6px; font-weight: 800;">{region_text}</span>
        <span>·</span>
        <span style="font-weight: 700;">{sector}</span>
        <span>·</span>
        <span>{time_str}</span>
    </div>
    <h2 style="color: #F8FAFC; margin-top: 0; margin-bottom: 30px; font-weight: 900; line-height: 1.4; font-size: 32px;">{news['title']}</h2>
    """, unsafe_allow_html=True)
    
    # 3줄 요약 처리
    summary_text = re.sub(r'(\d\.)', r'<br><br>\1', news['summary'])
    if summary_text.startswith('<br><br>'):
        summary_text = summary_text[8:]
        
    st.markdown(f"""
    <div style="background: linear-gradient(145deg, rgba(30,58,138,0.2), rgba(15,23,42,0.6)); border: 1px solid rgba(56,189,248,0.2); padding: 30px; border-radius: 12px; margin-bottom: 35px;">
        <h4 style="color: #38BDF8; margin-top: 0; margin-bottom: 15px; font-size: 18px;">✨ AI 핵심 요약</h4>
        <div style="color: #E2E8F0; line-height: 1.8; font-size: 17px;">
            {summary_text}
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    score = news['sentiment_score']
    if score <= 2:
        color, status = "#EF4444", "Bearish (부정적)"
    elif score == 3:
        color, status = "#F59E0B", "Neutral (중립)"
    else:
        color, status = "#10B981", "Bullish (긍정적)"
        
    st.markdown(f"""
    <div style="border-top: 1px solid rgba(255,255,255,0.1); padding-top: 25px; display: flex; justify-content: space-between; align-items: center;">
        <span style="color: #94A3B8; font-size: 16px; font-weight: 700;">AI Sentiment Score</span>
        <span style="color: {color}; font-weight: 900; background-color: {color}1A; padding: 10px 20px; border-radius: 30px; font-size: 17px;">
            {score} / 5 &nbsp;·&nbsp; {status}
        </span>
    </div>
    """, unsafe_allow_html=True)

    # 🌟 하단 네비게이션 버튼 (이모지만 작고 깔끔하게 배치)
    st.markdown("<div style='margin-top: 30px;'></div>", unsafe_allow_html=True)
    nav_col1, empty_col, nav_col2 = st.columns([1, 8, 1])
    with nav_col1:
        if idx > 0:
            st.button("⬅️", on_click=go_prev, use_container_width=True)
    with nav_col2:
        if idx < len(news_list) - 1:
            st.button("➡️", on_click=go_next, use_container_width=True)


# ==========================================
# 메인 뉴스 페이지
# ==========================================
def run_news_page(supabase):
    st.markdown("""
    <style>
    /* 🌟 1. 검색창 잘림 방지를 위해 상단 여백(padding-top) 대폭 증가 */
    .block-container {
        max-width: 1000px !important; 
        padding-top: 4.5rem !important; 
        padding-bottom: 4rem !important;
        margin: 0 auto !important;
    }
    
    /* 상단 검색바 디자인 */
    div[data-baseweb="input"] {
        border-radius: 12px !important;
        background-color: rgba(255, 255, 255, 0.03) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        padding: 4px;
    }
    
    /* 🌟 2. 투명 오버레이를 이용한 완벽한 클릭형 카드 마법 CSS */
    .clickable-card {
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        background-color: rgba(15, 23, 42, 0.3);
        transition: all 0.3s ease;
        position: relative;
    }
    
    div[data-testid="stVerticalBlock"]:has(.clickable-card) {
        position: relative;
        gap: 0 !important;
    }
    
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] {
        position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 10;
    }
    
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] button {
        width: 100%; height: 100%; opacity: 0; cursor: pointer; background: transparent; border: none;
    }
    
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stButton"] button:hover) .clickable-card {
        background-color: rgba(255, 255, 255, 0.08) !important;
        border-color: rgba(255, 255, 255, 0.3) !important;
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(0,0,0,0.2);
    }
    
    .news-divider { height: 1px; background-color: rgba(255, 255, 255, 0.05); margin: 6px 0; }
    
    /* 탭(Tabs) 글꼴 크기 키우기 */
    button[data-baseweb="tab"] {
        font-size: 16px !important;
        font-weight: 600 !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # 1. 검색바 (여백 확보)
    st.markdown("<div style='margin-bottom: 10px;'></div>", unsafe_allow_html=True)
    search_query = st.text_input("검색어 입력", placeholder="🔍 뉴스 검색 (제목 또는 내용)", label_visibility="collapsed")
    st.markdown("<div style='margin-bottom: 20px;'></div>", unsafe_allow_html=True)
    
    # 2. DB에서 전체 뉴스 데이터 로드
    try:
        res = supabase.table("market_news").select("*").order("created_at", desc=True).limit(100).execute()
        news_list = res.data
    except Exception as e:
        st.error(f"뉴스 데이터를 불러오는 중 오류가 발생했습니다: {e}")
        return
        
    if not news_list:
        st.info("아직 수집된 뉴스가 없습니다.")
        return
        
    # 3. 🌟 공식 st.tabs() 를 활용한 진짜 탭 렌더링
    sectors = ["전체"]
    unique_sectors = set([n['sector_asset'].split('-')[0] if '-' in n['sector_asset'] else n['sector_asset'] for n in news_list])
    sectors.extend(sorted(list(unique_sectors)))
    
    tabs = st.tabs(sectors)
    
    # 각 탭 안에 뉴스 리스트 렌더링
    for i, tab in enumerate(tabs):
        with tab:
            current_sector = sectors[i]
            
            # 해당 탭에 맞는 뉴스 필터링 (검색어 + 섹터)
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

            top_news = tab_filtered_news[:2] 
            list_news = tab_filtered_news[2:] 
            
            # ==========================================
            # 🔥 탭 내부 상단: 주요 뉴스 카드
            # ==========================================
            if len(top_news) > 0:
                st.markdown("<h3 style='margin-top: 10px; margin-bottom: 16px; font-weight: 800;'>🔥 오늘 주요뉴스</h3>", unsafe_allow_html=True)
                cols = st.columns(2, gap="medium")
                
                for idx, news in enumerate(top_news):
                    actual_idx = tab_filtered_news.index(news)
                    
                    with cols[idx % 2]:
                        dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
                        time_str = dt.strftime("%H:%M")
                        
                        region_text = news.get('region', 'Global')
                        reg_color, reg_bg = get_region_style(region_text)
                        
                        with st.container():
                            st.markdown(f"""
                            <div class="clickable-card" style="height: 170px; padding: 22px; display: flex; flex-direction: column; justify-content: space-between;">
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <span style="background-color: {reg_bg}; color: {reg_color}; font-size: 12px; padding: 4px 10px; border-radius: 4px; font-weight: 800;">{region_text}</span>
                                    <span style="color: #64748B; font-size: 13px; font-weight: 600;">{time_str}</span>
                                </div>
                                <div style="font-size: 18px; font-weight: 800; color: #F8FAFC; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                                    {news['title']}
                                </div>
                                <div>
                                    <span style="background-color: rgba(255,255,255,0.05); color: #94A3B8; font-size: 12px; padding: 4px 12px; border-radius: 12px; font-weight: 700;">#{news['sector_asset']}</span>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            # 버튼 클릭 시 해당 탭의 필터링된 리스트와 인덱스를 세션에 저장 후 팝업 호출
                            if st.button(" ", key=f"top_btn_{current_sector}_{news['id']}", use_container_width=True):
                                st.session_state.dialog_news_list = tab_filtered_news
                                st.session_state.dialog_news_index = actual_idx
                                news_detail_dialog()

            st.write("")
            st.write("")
            
            # ==========================================
            # 📄 탭 내부 하단: 일반 뉴스 리스트
            # ==========================================
            st.markdown("<h3 style='margin-bottom: 12px; font-weight: 800;'>최신 뉴스</h3>", unsafe_allow_html=True)
            st.markdown('<div class="news-divider" style="margin-bottom: 16px;"></div>', unsafe_allow_html=True)
            
            for news in list_news:
                actual_idx = tab_filtered_news.index(news)
                
                dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
                time_str = dt.strftime("%H:%M") 
                
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
                        <div style="color: #64748B; font-size: 14px; font-weight: 600; white-space: nowrap; margin-left: 16px;">
                            {time_str}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button(" ", key=f"list_btn_{current_sector}_{news['id']}", use_container_width=True):
                        st.session_state.dialog_news_list = tab_filtered_news
                        st.session_state.dialog_news_index = actual_idx
                        news_detail_dialog()
