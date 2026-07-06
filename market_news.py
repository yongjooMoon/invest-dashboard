import streamlit as st
import re
from datetime import datetime

# ==========================================
# 팝업 다이얼로그 (상세 브리핑 모달창)
# ==========================================
@st.dialog("📰 뉴스 상세 브리핑")
def news_detail_dialog(news):
    # 시간 포맷팅
    try:
        dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
        time_str = dt.strftime("%y.%m.%d. %H:%M")
    except:
        time_str = news['created_at']

    # 태그 및 제목
    region = news.get('region', 'Global')
    sector = news.get('sector_asset', 'News')
    
    st.markdown(f"""
    <div style="color: #94A3B8; font-size: 13px; margin-bottom: 12px; display:flex; gap: 8px; align-items:center;">
        <span style="background-color: rgba(56, 189, 248, 0.1); color: #38BDF8; padding: 3px 8px; border-radius: 4px; font-weight: 700;">{region}</span>
        <span>·</span>
        <span>{sector}</span>
        <span>·</span>
        <span>{time_str}</span>
    </div>
    <h3 style="color: #F8FAFC; margin-top: 0; margin-bottom: 20px; font-weight: 800; line-height: 1.4;">{news['title']}</h3>
    """, unsafe_allow_html=True)
    
    # 3줄 요약 처리
    summary_text = re.sub(r'(\d\.)', r'<br><br>\1', news['summary'])
    if summary_text.startswith('<br><br>'):
        summary_text = summary_text[8:]
        
    st.markdown(f"""
    <div style="background-color: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.05); padding: 20px; border-radius: 12px; color: #E2E8F0; line-height: 1.7; margin-bottom: 24px; font-size: 15px;">
        {summary_text}
    </div>
    """, unsafe_allow_html=True)
    
    # AI 긍부정 점수 (하단 고정)
    score = news['sentiment_score']
    if score <= 2:
        color, status = "#EF4444", "Bearish (부정적)"
    elif score == 3:
        color, status = "#F59E0B", "Neutral (중립)"
    else:
        color, status = "#10B981", "Bullish (긍정적)"
        
    st.markdown(f"""
    <div style="border-top: 1px solid rgba(255,255,255,0.1); padding-top: 16px; display: flex; justify-content: space-between; align-items: center;">
        <span style="color: #94A3B8; font-size: 14px; font-weight: 600;">AI Sentiment Score</span>
        <span style="color: {color}; font-weight: 800; background-color: {color}1A; padding: 6px 14px; border-radius: 20px;">
            {score}/5 · {status}
        </span>
    </div>
    """, unsafe_allow_html=True)


# ==========================================
# 메인 뉴스 페이지 (리스트 렌더링)
# ==========================================
def run_news_page(supabase):
    st.markdown("""
    <style>
    /* 전체 배경 톤 및 패딩 최적화 */
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 4rem !important;
    }
    
    /* 상단 검색바 디자인 해킹 */
    div[data-baseweb="input"] {
        border-radius: 12px !important;
        background-color: rgba(255, 255, 255, 0.05) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
    }
    
    /* 🔥 혁신적인 필터 탭 (Pill Design) CSS 해킹 🔥 */
    /* Streamlit의 기본 라디오 버튼을 가로형 알약(캡슐) 모양 탭으로 완벽 변신시킵니다. */
    div[role="radiogroup"] {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 20px;
    }
    div[role="radiogroup"] label {
        background-color: rgba(255, 255, 255, 0.05) !important;
        padding: 8px 18px !important;
        border-radius: 20px !important;
        border: 1px solid transparent !important;
        cursor: pointer;
        transition: all 0.2s ease;
    }
    div[role="radiogroup"] label:hover {
        background-color: rgba(255, 255, 255, 0.1) !important;
    }
    div[role="radiogroup"] label[data-checked="true"] {
        background-color: #F8FAFC !important; /* 활성화 시 하얀색 배경 */
        color: #0F172A !important; /* 활성화 시 검은색 글씨 */
        border-color: #F8FAFC !important;
    }
    div[role="radiogroup"] label[data-checked="true"] p {
        color: #0F172A !important;
        font-weight: 800 !important;
    }
    div[role="radiogroup"] div[data-testid="stMarkdownContainer"] p {
        margin: 0 !important;
        font-size: 15px;
        color: #94A3B8;
        font-weight: 600;
    }
    /* 못생긴 라디오 버튼 동그라미 완벽하게 숨기기 */
    div[role="radiogroup"] input, div[role="radiogroup"] div[data-baseweb="radio"] > div {
        display: none !important;
    }
    
    /* 리스트 및 카드 구분선 */
    .news-divider {
        height: 1px;
        background-color: rgba(255, 255, 255, 0.05);
        margin: 16px 0;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # 1. 🔍 검색바
    search_query = st.text_input("검색어 입력", placeholder="🔍 뉴스 검색 (제목 또는 내용)", label_visibility="collapsed")
    
    # 2. DB에서 전체 뉴스 데이터 로드 (최신 100개)
    try:
        res = supabase.table("market_news").select("*").order("created_at", desc=True).limit(100).execute()
        news_list = res.data
    except Exception as e:
        st.error(f"뉴스 데이터를 불러오는 중 오류가 발생했습니다: {e}")
        return
        
    if not news_list:
        st.info("아직 수집된 뉴스가 없습니다. (배치 대기 중)")
        return
        
    # 3. 카테고리(섹터) 동적 추출 및 필터 탭 렌더링
    sectors = ["전체"]
    # '-'로 연결된 세부분류(예: Macro-Economy)에서 앞쪽 대분류(Macro)만 따서 탭 생성
    unique_sectors = set([n['sector_asset'].split('-')[0] if '-' in n['sector_asset'] else n['sector_asset'] for n in news_list])
    sectors.extend(sorted(list(unique_sectors)))
    
    # CSS 해킹이 적용되어 예쁜 캡슐 탭으로 보입니다.
    selected_category = st.radio("카테고리", sectors, horizontal=True, label_visibility="collapsed")
    
    # 검색 및 필터 조건 적용
    filtered_news = []
    for n in news_list:
        n_sector_group = n['sector_asset'].split('-')[0] if '-' in n['sector_asset'] else n['sector_asset']
        
        match_category = selected_category == "전체" or n_sector_group == selected_category
        match_search = not search_query or search_query.lower() in n['title'].lower() or search_query.lower() in n['summary'].lower()
        
        if match_category and match_search:
            filtered_news.append(n)
            
    st.write("") # 간격 띄우기
    
    # ==========================================
    # 🔥 상단: 가로형 주요 뉴스 카드 (Top News)
    # ==========================================
    # TODO: 다음 스텝에서 AI 수합 시 'is_major' 같은 컬럼을 추가하면 그 필터로 대체할 예정입니다.
    # 현재는 디자인 확인을 위해 최신 뉴스 중 맨 위 2개를 꼽아 보여줍니다.
    top_news = filtered_news[:2] 
    list_news = filtered_news[2:] 
    
    if len(top_news) > 0:
        st.markdown("<h3 style='margin-bottom: 15px;'>🔥 오늘 주요뉴스 <span style='font-size: 16px; color: #64748B; font-weight: normal;'>></span></h3>", unsafe_allow_html=True)
        cols = st.columns(2, gap="medium")
        
        for i, news in enumerate(top_news):
            with cols[i % 2]:
                dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
                time_str = dt.strftime("%H:%M")
                
                # 카드 테두리 (border=True)
                with st.container(border=True):
                    st.markdown(f"""
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                        <span style="background-color: rgba(167, 139, 250, 0.1); color: #A78BFA; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: 700;">SAVE · {news['region']}</span>
                        <span style="color: #64748B; font-size: 12px; font-weight: 500;">{time_str}</span>
                    </div>
                    <div style="font-size: 17px; font-weight: 700; color: #F8FAFC; margin-bottom: 15px; line-height: 1.5; height: 50px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;">
                        {news['title']}
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="background-color: rgba(255,255,255,0.05); color: #94A3B8; font-size: 11px; padding: 4px 10px; border-radius: 12px;">#{news['sector_asset']}</span>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # 카드 클릭 효과를 내기 위해 카드 맨 밑에 버튼 배치
                    if st.button("상세 브리핑 보기", key=f"top_btn_{news['id']}", use_container_width=True):
                        news_detail_dialog(news)

    st.write("")
    
    # ==========================================
    # 📄 하단: 세로형 일반 뉴스 리스트 (List News)
    # ==========================================
    st.markdown("<h3 style='margin-bottom: 0;'>뉴스</h3>", unsafe_allow_html=True)
    st.markdown('<div class="news-divider"></div>', unsafe_allow_html=True)
    
    if len(list_news) == 0 and len(top_news) == 0:
        st.warning("조건에 맞는 뉴스가 없습니다.")
    else:
        for news in list_news:
            dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
            time_str = dt.strftime("%H:%M") 
            
            # 리스트 아이템 레이아웃 (본문 5 : 시간/버튼 1 비율)
            col1, col2 = st.columns([5, 1], vertical_alignment="center")
            with col1:
                st.markdown(f"""
                <div style="margin-bottom: 6px;">
                    <span style="color: #F87171; font-weight: 700; font-size: 12px; background-color: rgba(248, 113, 113, 0.1); padding: 2px 6px; border-radius: 4px; margin-right: 8px;">{news['region']}</span>
                    <span style="color: #94A3B8; font-size: 12px; font-weight: 600;">· {news['sector_asset']}</span>
                </div>
                <div style="font-size: 16px; font-weight: 600; color: #E2E8F0; line-height: 1.4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    {news['title']}
                </div>
                """, unsafe_allow_html=True)
                
            with col2:
                st.markdown(f"""
                <div style="text-align: right; color: #64748B; font-size: 12px; margin-bottom: 6px; font-weight: 500;">
                    {time_str}
                </div>
                """, unsafe_allow_html=True)
                
                # 우측 정렬된 작고 세련된 보기 버튼
                if st.button("보기", key=f"list_btn_{news['id']}", use_container_width=True):
                    news_detail_dialog(news)
            
            st.markdown('<div class="news-divider"></div>', unsafe_allow_html=True)
