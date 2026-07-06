import streamlit as st
import re
from datetime import datetime

# ==========================================
# 팝업 다이얼로그 (상세 브리핑 모달창 - 사이즈 및 높이 대폭 확대, 네비게이션 추가)
# ==========================================
@st.dialog("📰 뉴스 상세 브리핑")
def news_detail_dialog():
    # 세션 상태에서 현재 뉴스 리스트와 인덱스 가져오기
    news_list = st.session_state.get("filtered_news", [])
    idx = st.session_state.get("current_news_index", 0)
    
    if not news_list:
        st.error("뉴스를 찾을 수 없습니다.")
        return
        
    news = news_list[idx]

    # 🌟 팝업(모달) 창의 높이(height)를 80vh로 강제하고 너비를 키우는 CSS
    st.markdown("""
    <style>
    div[data-testid="stModal"] > div[role="dialog"] {
        width: 85vw !important;
        max-width: 950px !important;
        height: 80vh !important; /* 높이를 화면의 80%로 시원하게 확장 */
        min-height: 600px !important;
        border-radius: 16px !important;
    }
    div[data-testid="stModal"] div[data-testid="stMarkdownContainer"] {
        padding: 0.5rem 0.5rem;
    }
    /* 모달 내부 스크롤바 디자인 */
    div[data-testid="stModal"] > div[role="dialog"] ::-webkit-scrollbar {
        width: 8px;
    }
    div[data-testid="stModal"] > div[role="dialog"] ::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.2);
        border-radius: 4px;
    }
    </style>
    """, unsafe_allow_html=True)

    # 상단 네비게이션 버튼 (이전/다음)
    nav_col1, empty_col, nav_col2 = st.columns([2, 5, 2])
    with nav_col1:
        if idx > 0:
            if st.button("◀ 이전 기사", use_container_width=True):
                st.session_state.current_news_index -= 1
                st.rerun()
    with nav_col2:
        if idx < len(news_list) - 1:
            if st.button("다음 기사 ▶", use_container_width=True):
                st.session_state.current_news_index += 1
                st.rerun()

    st.markdown("<hr style='margin: 10px 0 20px 0; border-color: rgba(255,255,255,0.1);'>", unsafe_allow_html=True)

    # 시간 포맷팅
    try:
        dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
        time_str = dt.strftime("%y.%m.%d. %H:%M")
    except:
        time_str = news['created_at']

    region = news.get('region', 'Global')
    sector = news.get('sector_asset', 'News')
    
    st.markdown(f"""
    <div style="color: #94A3B8; font-size: 14px; margin-bottom: 16px; display:flex; gap: 10px; align-items:center;">
        <span style="background-color: rgba(56, 189, 248, 0.1); color: #38BDF8; padding: 4px 10px; border-radius: 6px; font-weight: 700;">{region}</span>
        <span>·</span>
        <span style="font-weight: 600;">{sector}</span>
        <span>·</span>
        <span>{time_str}</span>
    </div>
    <h2 style="color: #F8FAFC; margin-top: 0; margin-bottom: 25px; font-weight: 900; line-height: 1.4; font-size: 28px;">{news['title']}</h2>
    """, unsafe_allow_html=True)
    
    # 3줄 요약 처리
    summary_text = re.sub(r'(\d\.)', r'<br><br>\1', news['summary'])
    if summary_text.startswith('<br><br>'):
        summary_text = summary_text[8:]
        
    st.markdown(f"""
    <div style="background: linear-gradient(145deg, rgba(30,58,138,0.2), rgba(15,23,42,0.6)); border: 1px solid rgba(56,189,248,0.2); padding: 25px; border-radius: 12px; margin-bottom: 30px;">
        <h4 style="color: #38BDF8; margin-top: 0; margin-bottom: 15px; font-size: 16px;">✨ AI 핵심 요약</h4>
        <div style="color: #E2E8F0; line-height: 1.8; font-size: 16px;">
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
        <span style="color: #94A3B8; font-size: 15px; font-weight: 700;">AI Sentiment Score</span>
        <span style="color: {color}; font-weight: 900; background-color: {color}1A; padding: 10px 20px; border-radius: 30px; font-size: 16px;">
            {score} / 5 &nbsp;·&nbsp; {status}
        </span>
    </div>
    """, unsafe_allow_html=True)


# ==========================================
# 메인 뉴스 페이지
# ==========================================
def run_news_page(supabase):
    st.markdown("""
    <style>
    /* 🌟 1. 전체 화면 너비 제한 및 중앙 정렬 */
    .block-container {
        max-width: 950px !important; /* 너비를 살짝 줄여서 중앙 집중도를 높임 */
        padding-top: 2rem !important;
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
    
    /* 🌟 2. 혁신적인 필터 탭 (Pill Design) */
    div[role="radiogroup"] { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 25px; }
    div[role="radiogroup"] label {
        background-color: rgba(255, 255, 255, 0.05) !important; padding: 8px 18px !important;
        border-radius: 20px !important; border: 1px solid transparent !important;
        cursor: pointer; transition: all 0.2s ease;
    }
    div[role="radiogroup"] label:hover { background-color: rgba(255, 255, 255, 0.1) !important; }
    div[role="radiogroup"] label[data-checked="true"] { background-color: #F8FAFC !important; color: #0F172A !important; }
    div[role="radiogroup"] label[data-checked="true"] p { color: #0F172A !important; font-weight: 800 !important; }
    div[role="radiogroup"] div[data-testid="stMarkdownContainer"] p { margin: 0 !important; font-size: 15px; color: #94A3B8; font-weight: 600; }
    div[role="radiogroup"] input, div[role="radiogroup"] div[data-baseweb="radio"] > div { display: none !important; }
    
    /* 🌟 3. 버튼 텍스트 숨기고 투명 오버레이로 만드는 마법의 CSS */
    .clickable-card {
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        background-color: rgba(15, 23, 42, 0.3);
        transition: all 0.3s ease;
        position: relative;
    }
    
    /* Streamlit 컨테이너 레이아웃 틈새 제거 */
    div[data-testid="stVerticalBlock"]:has(.clickable-card) {
        position: relative;
        gap: 0 !important;
    }
    
    /* 투명 버튼을 카드 전체에 덮어씌우기 */
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] {
        position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 10;
    }
    div[data-testid="stVerticalBlock"]:has(.clickable-card) > div[data-testid="stButton"] button {
        width: 100%; height: 100%; opacity: 0; cursor: pointer; background: transparent; border: none;
    }
    
    /* 마우스를 올렸을 때 카드 효과 (버튼의 hover 상태를 카드로 전달) */
    div[data-testid="stVerticalBlock"]:has(> div[data-testid="stButton"] button:hover) .clickable-card {
        background-color: rgba(255, 255, 255, 0.08) !important;
        border-color: rgba(255, 255, 255, 0.3) !important;
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(0,0,0,0.2);
    }
    
    .news-divider { height: 1px; background-color: rgba(255, 255, 255, 0.05); margin: 6px 0; }
    </style>
    """, unsafe_allow_html=True)
    
    # 1. 검색바
    search_query = st.text_input("검색어 입력", placeholder="🔍 뉴스 검색 (제목 또는 내용)", label_visibility="collapsed")
    
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
        
    # 3. 카테고리 필터
    sectors = ["전체"]
    unique_sectors = set([n['sector_asset'].split('-')[0] if '-' in n['sector_asset'] else n['sector_asset'] for n in news_list])
    sectors.extend(sorted(list(unique_sectors)))
    selected_category = st.radio("카테고리", sectors, horizontal=True, label_visibility="collapsed")
    
    filtered_news = []
    for n in news_list:
        n_sector_group = n['sector_asset'].split('-')[0] if '-' in n['sector_asset'] else n['sector_asset']
        match_category = selected_category == "전체" or n_sector_group == selected_category
        match_search = not search_query or search_query.lower() in n['title'].lower() or search_query.lower() in n['summary'].lower()
        if match_category and match_search:
            filtered_news.append(n)

    # 상태 관리를 위해 필터링된 뉴스를 세션에 저장 (모달창 네비게이션 용도)
    st.session_state.filtered_news = filtered_news
            
    st.write("")
    
    # ==========================================
    # 🔥 상단: 가로형 주요 뉴스 카드
    # ==========================================
    top_news = filtered_news[:2] 
    list_news = filtered_news[2:] 
    
    if len(top_news) > 0:
        st.markdown("<h3 style='margin-bottom: 16px; font-weight: 800;'>🔥 오늘 주요뉴스 <span style='font-size: 16px; color: #64748B; font-weight: normal; margin-left: 8px;'>></span></h3>", unsafe_allow_html=True)
        cols = st.columns(2, gap="medium")
        
        for i, news in enumerate(top_news):
            # 필터링된 전체 리스트에서의 실제 인덱스 찾기
            actual_idx = filtered_news.index(news)
            
            with cols[i % 2]:
                dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
                time_str = dt.strftime("%H:%M")
                
                with st.container():
                    st.markdown(f"""
                    <div class="clickable-card" style="height: 160px; padding: 20px; display: flex; flex-direction: column; justify-content: space-between;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="background-color: rgba(167, 139, 250, 0.15); color: #A78BFA; font-size: 11px; padding: 4px 8px; border-radius: 4px; font-weight: 700;">SAVE · {news['region']}</span>
                            <span style="color: #64748B; font-size: 12px; font-weight: 500;">{time_str}</span>
                        </div>
                        <div style="font-size: 18px; font-weight: 800; color: #F8FAFC; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                            {news['title']}
                        </div>
                        <div>
                            <span style="background-color: rgba(255,255,255,0.05); color: #94A3B8; font-size: 12px; padding: 4px 10px; border-radius: 12px; font-weight: 600;">#{news['sector_asset']}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button(" ", key=f"top_btn_{news['id']}", use_container_width=True):
                        st.session_state.current_news_index = actual_idx
                        news_detail_dialog()

    st.write("")
    st.write("")
    
    # ==========================================
    # 📄 하단: 세로형 일반 뉴스 리스트
    # ==========================================
    st.markdown("<h3 style='margin-bottom: 12px; font-weight: 800;'>뉴스</h3>", unsafe_allow_html=True)
    st.markdown('<div class="news-divider" style="margin-bottom: 16px;"></div>', unsafe_allow_html=True)
    
    if len(list_news) == 0 and len(top_news) == 0:
        st.warning("조건에 맞는 뉴스가 없습니다.")
    else:
        for news in list_news:
            actual_idx = filtered_news.index(news)
            
            dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
            time_str = dt.strftime("%H:%M") 
            
            with st.container():
                st.markdown(f"""
                <div class="clickable-card" style="padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div style="display: flex; align-items: center; gap: 14px; flex: 1; overflow: hidden;">
                        <span style="background-color: rgba(248, 113, 113, 0.15); color: #F87171; font-weight: 700; font-size: 11px; padding: 4px 8px; border-radius: 4px; white-space: nowrap;">{news['region']}</span>
                        <span style="color: #94A3B8; font-size: 13px; font-weight: 600; white-space: nowrap;">· {news['sector_asset']}</span>
                        <span style="font-size: 16px; font-weight: 700; color: #E2E8F0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{news['title']}</span>
                    </div>
                    <div style="color: #64748B; font-size: 13px; font-weight: 500; white-space: nowrap; margin-left: 16px;">
                        {time_str}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                if st.button(" ", key=f"list_btn_{news['id']}", use_container_width=True):
                    st.session_state.current_news_index = actual_idx
                    news_detail_dialog()
