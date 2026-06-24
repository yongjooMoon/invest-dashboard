"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta
import plotly.graph_objects as go
from quant_core import (
    load_price_from_db, load_screening_result,
    HARD_GATES, SOFT_GATES, now_kst,
)

def _badge(passed: bool) -> str:
    return "🟢 충족" if passed else "🔴 미달"

def _render_filter_badges(fr: dict):
    st.markdown("**🛡️ 추격매수 6대 절대 조건 (모두 통과시 확정 편입)**")
    hcols = st.columns(3)
    
    gates_ko = {
        "Growth Composite": "성장성 통합 (Growth)",
        "Dynamic MDD": "동적 방어선 (ATR MDD)",
        "Liquidity": "유동성 (20일 평균 50억↑)",
        "Trend Alignment": "추세 정배열 (Price>20>60)",
        "Price Breakout": "돌파 임박 (60일 고점 90%↑)",
        "Volume Surge": "거래량 폭증 (장기대비 1.5배↑)"
    }

    for i, (fname, _) in enumerate(HARD_GATES):
        d = fr.get(fname, {})
        display_name = gates_ko.get(fname, fname)
        with hcols[i % 3]:
            with st.container(border=True):
                st.markdown(f"**{display_name}**")
                st.markdown(_badge(d.get("pass", False)))
                st.caption(d.get("reason", "-"))

def _render_detail(sel: dict, supabase):
    fs = sel.get("factor_score", 0)
    tp = sel.get("total_pass", 0)
    fs_color = "#00B464" if fs >= 65 else "#E6A23C" if fs >= 40 else "#F04452"

    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.markdown(f"**상대 랭킹 점수:** <span style='font-size:1.4rem;font-weight:bold;color:{fs_color}'>{fs}점</span>", unsafe_allow_html=True)
    with col_b:
        badge = "🏆 Confirmed" if tp >= 6 else f"👀 WatchList"
        st.markdown(f"### {badge}")
        st.caption(f"절대 조건 {tp}/6 통과")

    tab1, tab2, tab3 = st.tabs(["✅ 퀀트 평가 상세", "📊 펀더멘털 & 수급", "📈 가격 차트"])

    with tab1:
        st.info(f"💡 **권장 진입가 (Entry Cost)**: {sel.get('entry_price', sel['current_price']):,}원 (추격 리스크 완화를 위한 눌림목 지지선)")
        _render_filter_badges(sel.get("filter_details", {}))

    with tab2:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("순이익 YoY", f"{sel.get('net_income_yoy') or 0:+.1f}%" if sel.get("net_income_yoy") is not None else "-")
        m2.metric("ROE", f"{sel.get('roe') or 0:.1f}%" if sel.get("roe") is not None else "-")
        f_net = sel.get("foreign_net_buy") or 0
        i_net = sel.get("institute_net_buy") or 0
        m3.metric("수급 합산", f"{f_net+i_net:+,.0f}주" if (f_net or i_net) else "미수집")
        m4.metric("시가총액", f"{sel.get('marcap_억', 0):,.0f}억")

    with tab3:
        df_price = load_price_from_db(supabase, sel["symbol"])
        if not df_price.empty:
            st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
        else:
            st.info("가격 데이터 없음")

def _build_table(results: list) -> pd.DataFrame:
    rows = []
    for idx, r in enumerate(results):
        rows.append({
            "순위": idx + 1,
            "종목명": r["name"],
            "절대필터": f"{r.get('total_pass',0)}/6",
            "랭킹점수": f"{r.get('factor_score',0):.1f}",
            "현재가": f"₩{r['current_price']:,}",
            "💡진입제안": f"₩{r.get('entry_price', r['current_price']):,}",
            "모멘텀": f"{r.get('momentum_score', 0):+.1f}%",
        })
    return pd.DataFrame(rows)

def load_portfolio_data(supabase):
    holdings, trades, history = [], [], []
    try:
        r1 = supabase.table("quant_screening_cache").select("results").eq("id", 11).execute()
        if r1.data: holdings = json.loads(r1.data[0]["results"])
        r2 = supabase.table("quant_screening_cache").select("results").eq("id", 12).execute()
        if r2.data: trades = json.loads(r2.data[0]["results"])
        r3 = supabase.table("quant_screening_cache").select("results").eq("id", 13).execute()
        if r3.data: history = json.loads(r3.data[0]["results"])
    except:
        pass
    return holdings, trades, history

def render_portfolio_and_alpha(supabase):
    st.markdown("### 💼 퀀트 포트폴리오 & Alpha")
    
    holdings, trades, history = load_portfolio_data(supabase)
    
    # 1. 알파 차트 렌더링
    df_hist = pd.DataFrame(history)
    if not df_hist.empty:
        df_hist['date'] = pd.to_datetime(df_hist['date'])
        df_hist = df_hist.set_index('date')
        df_hist['port_cum'] = df_hist['portfolio_return'].cumsum()
        df_hist['kospi_cum'] = df_hist['kospi_return'].cumsum()
        df_hist['Alpha'] = df_hist['port_cum'] - df_hist['kospi_cum']
        
        cum_ret = df_hist['port_cum'].iloc[-1]
        k_cum_ret = df_hist['kospi_cum'].iloc[-1]
        alpha = cum_ret - k_cum_ret
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Portfolio 누적", f"{cum_ret:+.2f}%", f"Day {df_hist['portfolio_return'].iloc[-1]:+.2f}%")
        with col2:
            st.metric("KOSPI 누적", f"{k_cum_ret:+.2f}%", f"Day {df_hist['kospi_return'].iloc[-1]:+.2f}%")
        with col3:
            st.metric("Alpha (초과수익)", f"{alpha:+.2f}%")

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_hist.index, y=df_hist['port_cum'], name='Portfolio', line=dict(color='#F04452', width=2.5)))
        fig.add_trace(go.Scatter(x=df_hist.index, y=df_hist['kospi_cum'], name='KOSPI', line=dict(color='#8B95A1', width=1.5, dash='dot')))
        fig.update_layout(hovermode='x', margin=dict(l=0,r=0,t=10,b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("오늘 14:30 KST 크론이 돌면 첫 포트폴리오 알파 데이터가 생성됩니다.")

    # 2. 보유 종목 / 매매 내역 탭
    st.markdown("#### 자동 매매 내역")
    ptab1, ptab2 = st.tabs(["현재 보유 종목", "매매 이탈/진입 이력"])
    with ptab1:
        if holdings:
            h_df = pd.DataFrame(holdings)[["name", "symbol", "entry_price", "current_price", "return_rate"]]
            h_df.columns = ["종목명", "코드", "매수가", "현재가", "수익률(%)"]
            st.dataframe(h_df.style.map(lambda x: "color: #F04452" if x>0 else "color: #3182F6", subset=["수익률(%)"]), hide_index=True)
        else:
            st.info("현재 보유 중인 종목이 없습니다. (조건 부합 종목 매수 대기 중)")

    with ptab2:
        if trades:
            t_df = pd.DataFrame(trades[::-1])[["trade_date", "type", "name", "trade_price", "return_rate", "reason"]]
            t_df.columns = ["일자", "구분", "종목명", "체결가", "손익(%)", "사유"]
            st.dataframe(t_df.style.map(lambda x: 'color: #F04452' if x=='BUY' else 'color: #3182F6', subset=['구분']), hide_index=True)
        else:
            st.info("매매 이력이 없습니다.")

def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 퀀트 스크리너 (Strict Chase Momentum)")
    st.caption("돌파 및 거래량 급증 확인 후 추격매수 · 20일선 이탈 시 자동 매도 시뮬레이션")

    with st.expander("📚 전략 설계: 10개 강제 추출 폐지 및 진짜 추격매수 필터", expanded=False):
        st.markdown("""
- **[무조건 10개 추출 폐지]**: 강력한 추격매수 기준에 부합하지 않으면 **단 1개의 종목도 추천하지 않습니다.**
- **[절대 방어선 6종목]**: 
  - 기본 생존: 성장성 / MDD 방어 / 유동성
  - **추격매수(Chase)**: `정배열 (현재가 > 20MA > 60MA)`, `고점돌파 (최근 두달 고점의 90% 이상)`, `거래량폭증 (장기 대비 1.5배 이상)`
- **[이탈 로직]**:
  - `포트폴리오(History)` 탭에서 매일 14:30 기준 20일 이평선(추세)이 깨지거나 리스크(-15%) 초과 시 자동 매도로 기록됩니다.
        """)

    confirmed, watchlist, last_updated = load_screening_result(supabase)

    # 1. 탭 구성 (WatchList 옆에 포트폴리오 히스토리 탭 추가)
    tab_conf, tab_watch, tab_port = st.tabs([
        f"🏆 확정 선별 ({len(confirmed)}개)", 
        f"👀 WatchList ({len(watchlist)}개)", 
        "💼 포트폴리오 & 히스토리"
    ])

    with tab_conf:
        st.markdown(f"**마지막 스크리닝:** {last_updated or '미실행'}")
        if not confirmed:
            st.warning("현재 추격매수(Chase Momentum) 6대 조건을 모두 통과한 종목이 0개입니다. 무리한 진입을 피하세요.")
        else:
            st.success(f"엄격한 추격매수 조건을 통과한 {len(confirmed)}개 종목이 선별되었습니다.")
            df_c = _build_table(confirmed)
            sel_c = st.dataframe(df_c, use_container_width=True, on_select="rerun", selection_mode="single-row", hide_index=True)
            if sel_c and hasattr(sel_c, "selection") and sel_c.selection.rows:
                st.divider()
                _render_detail(confirmed[sel_c.selection.rows[0]], supabase)

    with tab_watch:
        if not watchlist:
            st.info("WatchList 종목이 없습니다.")
        else:
            st.caption("추격매수 6조건 중 4개 이상 충족 (예비 돌파 종목)")
            df_w = _build_table(watchlist)
            sel_w = st.dataframe(df_w, use_container_width=True, on_select="rerun", selection_mode="single-row", hide_index=True)
            if sel_w and hasattr(sel_w, "selection") and sel_w.selection.rows:
                st.divider()
                _render_detail(watchlist[sel_w.selection.rows[0]], supabase)

    with tab_port:
        # 포트폴리오와 알파 차트를 이 탭에 렌더링
        render_portfolio_and_alpha(supabase)
