"""
stock_search.py — 개별 종목 검색 및 퀀트 리포트 화면 (독립 메뉴)
"""
import streamlit as st
import html
from datetime import timedelta
import FinanceDataReader as fdr

# quant_core 모듈에서 핵심 데이터 연동 함수들 로드
from quant_core import now_kst, load_screening_result, load_price_from_db

# 💡 [핵심] 기존 stock_quant.py에 있는 검증된 조회/렌더링 함수들을 그대로 재사용(import)합니다!
from stock_quant import (
    load_krx_list_from_db,
    live_evaluate_stock,
    render_detailed_report_content
)

def run_stock_search_page(supabase):
    # 페이지 타이틀
    c1, c2 = st.columns([8.2, 1.8])
    with c1:
        st.title("🔍 Stock Search & Report")
    
    st.caption("원하는 종목명 또는 코드를 콤보박스에서 검색하면 실시간 퀀트 분석 결과를 화면에 출력합니다.")
    st.divider()

    # 1. 종목 리스트 로드
    with st.spinner("KRX 종목 마스터 로드 중..."):
        krx_df = load_krx_list_from_db(supabase)

    if krx_df.empty:
        st.error("⚠️ 종목 마스터 데이터를 불러오지 못했습니다. (DB 캐시 확인 필요)")
        options = [""]
    else:
        options = [""] + krx_df["SearchStr"].tolist()

    # 2. 콤보박스 렌더링
    col_search, _ = st.columns([2, 1])
    with col_search:
        selected_stock_str = st.selectbox("🔎 종목 검색 (종목명 또는 코드 자동완성)", options=options)

    # 3. 데이터 로딩 및 인라인(화면 아래) 리포트 출력
    if selected_stock_str:
        search_query = selected_stock_str.split("(")[-1].replace(")", "").strip()
        stock_name = selected_stock_str.split(" (")[0]
        
        st.markdown("<div style='margin-top: 25px;'></div>", unsafe_allow_html=True)
        
        # 전체 캐시된 스크리닝 결과 가져오기 (비교용)
        c_list, w_list, _ = load_screening_result(supabase)
        all_cached_stocks = {item['symbol']: item for item in c_list + w_list}
        
        # [A] 이미 캐시된 종목일 경우 (API 미호출)
        if search_query in all_cached_stocks:
            with st.spinner(f"'{stock_name}' 캐시 데이터를 분석하여 화면을 구성 중입니다..."):
                sel = all_cached_stocks[search_query]
                
                # 차트 데이터 준비
                if "price_cache" not in st.session_state: st.session_state.price_cache = {}
                if search_query not in st.session_state.price_cache:
                    df_price = load_price_from_db(supabase, search_query)
                    if df_price.empty:
                        df_price = fdr.DataReader(search_query, (now_kst() - timedelta(days=300)).strftime('%Y-%m-%d'))
                    st.session_state.price_cache[search_query] = df_price
                    
                df_price = st.session_state.price_cache[search_query]
                
                if 'ret_1m' not in sel or sel['ret_1m'] == 0:
                    if df_price is not None and len(df_price) >= 21:
                        sel['ret_1m'] = (df_price['Close'].iloc[-1] - df_price['Close'].iloc[-21]) / df_price['Close'].iloc[-21] * 100

            st.success("✅ 일일 배치로 수집된 캐시 데이터를 로드했습니다. (초고속)")
            
            # 💡 [핵심] 팝업이 아니라 화면 탭 제약이 없는 상태이므로 기존처럼 화면 아래에 직접 그려줍니다.
            render_detailed_report_content(sel, df_price=df_price, fund=sel, factor_score=sel.get('factor_score', 0), gates=sel.get('filter_details'))
            
        # [B] 캐시에 없는 새로운 종목일 경우 (실시간 조회 및 DB 저장)
        else:
            with st.spinner(f"'{stock_name}' 실시간 데이터 동기화 및 퀀트 분석 중..."):
                # 여기서 코어의 함수가 돌아가며 DB에 펀더멘털 데이터를 안전하게 신규 Insert 합니다!
                df_price, live_fund, live_score, live_gates, is_newly_saved = live_evaluate_stock(supabase, search_query, stock_name)

            if df_price is None or df_price.empty:
                st.error("해당 종목의 차트 데이터를 찾을 수 없습니다.")
            else:
                sel = {
                    'symbol': search_query, 'name': stock_name,
                    'current_price': df_price['Close'].iloc[-1] if not df_price.empty else 0,
                    'ret_1m': (df_price['Close'].iloc[-1] - df_price['Close'].iloc[-21]) / df_price['Close'].iloc[-21] * 100 if len(df_price)>=21 else 0,
                    'region': 'KR'
                }
                if live_fund: sel.update(live_fund)
                
                if "price_cache" not in st.session_state: st.session_state.price_cache = {}
                st.session_state.price_cache[search_query] = df_price

                # 💡 리포트 메시지를 상황에 맞게 100% 정직하게 출력하도록 분기 처리했습니다.
                if is_newly_saved:
                    st.success("✅ 웹 스크래핑을 통해 기초 데이터를 수집하고 DB에 신규 저장했습니다.")
                else:
                    st.success("✅ DB에 저장된 기초 데이터를 바탕으로 실시간 퀀트 분석을 완료했습니다.")
                
                # 💡 마찬가지로 화면 아래에 직접 그려줍니다.
                render_detailed_report_content(sel, df_price=df_price, fund=live_fund, factor_score=live_score, gates=live_gates)
