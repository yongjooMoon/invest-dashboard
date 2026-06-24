"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta
import plotly.graph_objects as go
import FinanceDataReader as fdr
import numpy as np
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
        st.markdown(f"**상대 랭킹 점수:** <span style='font-size:1.4rem;font-weight:bold;color:{fs_color}'>{fs:.2f}점</span>", unsafe_allow_html=True)
    with col_b:
        badge = "🏆 Confirmed" if tp >= 6 else f"👀 WatchList"
        st.markdown(f"### {badge}")
        st.caption(f"절대 조건 {tp}/6 통과")

    tab1, tab2, tab3 = st.tabs(["✅ 퀀트 평가 상세", "📊 펀더멘털 & 수급", "📈 가격 차트"])

    with tab1:
        st.info(f"💡 **권장 진입가 (Entry Cost)**: {sel.get('entry_price', sel['current_price']):,.0f}원 (추격 리스크 완화를 위한 눌림목 지지선)")
        _render_filter_badges(sel.get("filter_details", {}))

    with tab2:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("순이익 YoY", f"{sel.get('net_income_yoy') or 0:+.2f}%" if sel.get("net_income_yoy") is not None else "-")
        m2.metric("ROE", f"{sel.get('roe') or 0:.2f}%" if sel.get("roe") is not None else "-")
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
            "랭킹점수": f"{r.get('factor_score',0):.2f}",
            "현재가": f"₩{r['current_price']:,}",
            "💡진입제안": f"₩{r.get('entry_price', r['current_price']):,}",
            "모멘텀": f"{r.get('momentum_score', 0):+.2f}%",
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
    
    # 1. 무조건 최근 30일치 KOSPI 데이터를 베이스로 가져옵니다.
    end_date = now_kst()
    start_date = end_date - timedelta(days=30)
    
    df_kospi = fdr.DataReader('KS11', start_date.strftime('%Y-%m-%d'))
    if not df_kospi.empty:
        df_kospi['kospi_cum'] = df_kospi['Close'].pct_change().fillna(0).cumsum() * 100
    else:
        df_kospi = pd.DataFrame(columns=['Close', 'kospi_cum'])

    chart_df = pd.DataFrame(index=df_kospi.index)
    chart_df['KOSPI'] = df_kospi['kospi_cum']

    # 2. 포트폴리오 히스토리를 KOSPI 날짜에 매핑 (데이터가 없으면 0%로 시작)
    df_hist = pd.DataFrame(history)
    if not df_hist.empty:
        df_hist['date'] = pd.to_datetime(df_hist['date'])
        df_hist = df_hist.set_index('date')
        df_hist['port_cum'] = df_hist['portfolio_return'].cumsum()
        
        # KOSPI 인덱스에 포트폴리오 수익률 결합 (ffill로 빈 날짜 채움)
        chart_df = chart_df.join(df_hist['port_cum'], how='left')
        chart_df['Portfolio'] = chart_df['port_cum'].fillna(method='ffill').fillna(0)
        
        cum_ret = chart_df['Portfolio'].iloc[-1]
        day_ret = df_hist['portfolio_return'].iloc[-1]
    else:
        chart_df['Portfolio'] = 0.0
        cum_ret = 0.0
        day_ret = 0.0

    k_cum_ret = chart_df['KOSPI'].iloc[-1] if not chart_df['KOSPI'].empty else 0.0
    k_day_ret = df_kospi['Close'].pct_change().iloc[-1] * 100 if not df_kospi.empty else 0.0
    
    # 3. Alpha (초과 수익) 계산
    alpha = cum_ret - k_cum_ret
    chart_df['Alpha'] = chart_df['Portfolio'] - chart_df['KOSPI']
    chart_df['Alpha_Color'] = chart_df['Alpha'].apply(lambda x: '#00B464' if x >= 0 else '#F04452')

    # 상단 요약 지표 (소수점 2자리 강제 적용)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Portfolio 누적", f"{cum_ret:+.2f}%", f"Day {day_ret:+.2f}%")
    with col2:
        st.metric("KOSPI 누적", f"{k_cum_ret:+.2f}%", f"Day {k_day_ret:+.2f}%")
    with col3:
        st.metric("Alpha (초과수익)", f"{alpha:+.2f}%")

    # 4. 커스텀 차트 렌더링 (영역 채우기 + 커스텀 툴팁)
    if not chart_df.empty:
        fig = go.Figure()
        custom_data = np.column_stack((chart_df['KOSPI'], chart_df['Alpha'], chart_df['Alpha_Color']))
        
        # Portfolio 선 (점 포함)
        fig.add_trace(go.Scatter(
            x=chart_df.index, y=chart_df['Portfolio'], mode='lines+markers', name='Portfolio',
            line=dict(color='#F04452', width=2.5), fill='tozeroy', fillcolor='rgba(240, 68, 82, 0.05)', customdata=custom_data,
            hovertemplate=(
                "<span style='color:#AEC1D4; font-size:12px;'>%{x|%Y.%m.%d}</span><br><br>"
                "<span style='color:#3182F6;'>●</span> Portfolio &nbsp;&nbsp;&nbsp;<b>%{y:.2f}%</b><br>"
                "<span style='color:#8B95A1;'>●</span> KOSPI &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>%{customdata[0]:.2f}%</b><br>"
                "─────────────────<br>"
                "<span style='color:#8B95A1;'>α</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b style='color:%{customdata[2]};'>%{customdata[1]:+.2f}%</b><extra></extra>"
            )
        ))
        
        # KOSPI 선 (점선, 툴팁 스킵)
        fig.add_trace(go.Scatter(
            x=chart_df.index, y=chart_df['KOSPI'], mode='lines+markers', name='KOSPI', 
            line=dict(color='#8B95A1', width=1.5, dash='dot'), hoverinfo='skip'
        ))
        
        fig.update_layout(
            hovermode='x', 
            xaxis=dict(showgrid=False, zeroline=False, tickformat="%Y-%m-%d"), 
            yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)', ticksuffix="%"), 
            hoverlabel=dict(bgcolor="#191F28", font_color="white"), 
            margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False
        )
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # 5. 보유 종목 / 매매 내역 탭 (콤마 및 소수점 2자리 포맷 강제)
    st.markdown("#### 자동 매매 내역")
    ptab1, ptab2 = st.tabs(["현재 보유 종목", "매매 이탈/진입 이력"])
    with ptab1:
        if holdings:
            h_df = pd.DataFrame(holdings)[["name", "symbol", "entry_price", "current_price", "return_rate"]]
            h_df.columns = ["종목명", "코드", "매수가", "현재가", "수익률(%)"]
            
            styled_h = h_df.style.map(
                lambda x: "color: #F04452" if x > 0 else "color: #3182F6" if x < 0 else "", 
                subset=["수익률(%)"]
            ).format({
                "매수가": "{:,.0f}",
                "현재가": "{:,.0f}",
                "수익률(%)": "{:,.2f}"
            })
            st.dataframe(styled_h, hide_index=True)
        else:
            st.info("현재 보유 중인 종목이 없습니다. (크론잡이 돌면 조건에 부합하는 종목이 자동 편입됩니다.)")

    with ptab2:
        if trades:
            t_df = pd.DataFrame(trades[::-1])[["trade_date", "type", "name", "trade_price", "return_rate", "reason"]]
            t_df.columns = ["일자", "구분", "종목명", "체결가", "손익(%)", "사유"]
            
            styled_t = t_df.style.map(
                lambda x: 'color: #F04452' if x == 'BUY' else 'color: #3182F6', 
                subset=['구분']
            ).format({
                "체결가": "{:,.0f}",
                "손익(%)": "{:,.2f}"
            })
            st.dataframe(styled_t, hide_index=True)
        else:
            st.info("최근 매매 이력이 없습니다.")

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
