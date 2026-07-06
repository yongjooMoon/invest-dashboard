import streamlit as st
import re
from datetime import datetime

def run_news_page(supabase):
    st.markdown("""
    <style>
    /* 스크롤바 디자인 숨기기 및 부드럽게 만들기 */
    .st-emotion-cache-1y4p8pa {
        padding-top: 1rem !important;
    }
    .st-emotion-cache-16txtl3 {
        padding: 2rem 1.5rem !important;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("### 📰 글로벌 매크로 & 주도주 뉴스 데스크")

    # DB에서 최신 뉴스 50개 가져오기
    try:
        res = supabase.table("market_news").select("*").order("created_at", desc=True).limit(50).execute()
        news_list = res.data
    except Exception as e:
        st.error(f"뉴스 데이터를 불러오는 중 오류가 발생했습니다: {e}")
        return

    if not news_list:
        st.info("아직 수집된 뉴스가 없습니다. (8:32 / 15:32 / 22:32 배치 대기 중)")
        return

    # 선택된 뉴스를 세션에 저장 (최초 접속 시 가장 최근 뉴스 1번 선택)
    if "selected_news" not in st.session_state:
        st.session_state.selected_news = news_list[0]

    # 화면 분할 (좌측: 좁은 타임라인, 우측: 넓은 상세 정보)
    col1, col2 = st.columns([1, 1.4], gap="large")

    with col1:
        st.markdown("#### ⏳ 실시간 타임라인")
        # 높이를 고정하여 자체 스크롤이 가능하게 컨테이너 생성
        with st.container(height=650):
            for news in news_list:
                # 시간 포맷팅 (예: 24.07.06 15:32)
                dt = datetime.strptime(news['created_at'].split(".")[0][:19], "%Y-%m-%dT%H:%M:%S")
                time_str = dt.strftime("%y.%m.%d %H:%M")
                
                # 선택된 카드 시각적 하이라이트 (테두리 및 배경색 변화)
                is_selected = st.session_state.selected_news['id'] == news['id']
                border_color = "#20C997" if is_selected else "rgba(255,255,255,0.1)"
                bg_color = "rgba(32, 201, 151, 0.1)" if is_selected else "rgba(15, 23, 42, 0.4)"

                with st.container():
                    st.markdown(f"""
                    <div style="
                        border: 1px solid {border_color};
                        border-radius: 12px;
                        padding: 16px;
                        background: {bg_color};
                        margin-bottom: 5px;
                        transition: 0.3s;
                    ">
                        <div style="font-size: 11px; color: #94A3B8; margin-bottom: 8px;">
                            <span style="border: 1px solid #475569; padding: 2px 8px; border-radius: 12px; margin-right: 5px;">🌍 {news['region']}</span>
                            <span style="color: #38BDF8; font-weight: 600;">{news['sector_asset']}</span>
                            <span style="float: right;">{time_str}</span>
                        </div>
                        <div style="font-size: 15px; font-weight: 700; color: #F8FAFC; margin-bottom: 12px; line-height: 1.4;">
                            {news['title']}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # 꼼수: HTML div 자체는 클릭이 안 되므로 바로 아래에 투명감 있는 상세보기 버튼 배치
                    if st.button("내용 보기 ➡️", key=f"btn_{news['id']}", use_container_width=True, type="primary" if is_selected else "secondary"):
                        st.session_state.selected_news = news
                        st.rerun()
                st.markdown("<hr style='margin: 0px 0 15px 0; border-color: rgba(255,255,255,0.05);'>", unsafe_allow_html=True)

    with col2:
        sel = st.session_state.selected_news
        st.markdown("#### 📄 상세 브리핑")
        
        with st.container(border=True):
            st.markdown(f"""
            <div style="margin-bottom: 15px;">
                <span style="background-color: #1E293B; padding: 6px 12px; border-radius: 6px; font-size: 13px; color: #38BDF8; font-weight: 700; margin-right: 10px;">
                    🌍 {sel['region']}
                </span>
                <span style="background-color: #1E293B; padding: 6px 12px; border-radius: 6px; font-size: 13px; color: #A78BFA; font-weight: 700;">
                    🏷️ {sel['sector_asset']}
                </span>
            </div>
            <h2 style="color: #F8FAFC; margin-bottom: 25px; font-size: 26px; line-height: 1.4; font-weight: 800;">{sel['title']}</h2>
            """, unsafe_allow_html=True)
            
            # AI의 '1. 2. 3.' 형식 요약을 줄바꿈으로 예쁘게 파싱
            st.markdown("##### 📝 AI 3-Line Summary")
            summary_text = re.sub(r'(\d\.)', r'<br><br>\1', sel['summary'])
            if summary_text.startswith('<br><br>'):
                summary_text = summary_text[8:] # 맨 앞 줄바꿈 제거
                
            st.info(summary_text)
            
            st.markdown("---")
            
            # 긍부정 점수 시각화
            score = sel['sentiment_score']
            color = "#EF4444" if score <= 2 else "#F59E0B" if score == 3 else "#10B981"
            status = "부정적 (Bearish)" if score <= 2 else "중립 (Neutral)" if score == 3 else "긍정적 (Bullish)"
            
            st.markdown("##### 📊 AI Market Sentiment")
            
            st.markdown(f"""
            <div style="display: flex; align-items: center; gap: 20px; margin-top: 10px; margin-bottom: 15px;">
                <h1 style="margin: 0; color: {color}; font-size: 40px; text-shadow: 0 0 10px {color}40;">{score} <span style="font-size: 20px; color: #64748B;">/ 5</span></h1>
                <div style="font-size: 20px; font-weight: 800; color: {color}; background-color: {color}20; padding: 8px 16px; border-radius: 8px;">{status}</div>
            </div>
            """, unsafe_allow_html=True)
            
            # 프로그레스 바로 직관성 극대화
            st.progress(score / 5.0)
