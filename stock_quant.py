"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta
import numpy as np
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

def _build_table(results: list, is_watchlist: bool = False) -> pd.DataFrame:
    rows = []
    for idx, r in enumerate(results):
        row = {
            "순위": idx + 1,
            "종목명": r["name"],
            "절대필터": f"{r.get('total_pass',0)}/6",
            "랭킹점수": f"{r.get('factor_score',0):.2f}",
            "현재가": f"₩{r['current_price']:,}",
        }

        # Watchlist에서는 진입 제안가 제거
        if not is_watchlist:
            row["💡진입제안"] = f"₩{r.get('entry_price', r['current_price']):,}"

        row["모멘텀"] = f"{r.get('momentum_score', 0):+.2f}%"
        rows.append(row)
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

def render_portfolio_dashboard(holdings, trades, history):
    # 1. 상단 총 계좌 요약 (이미지 스타일)
    total_capital = sum([h.get("current_price", 0) * 100 for h in holdings]) if holdings else 0 # 100주 가정 더미 로직
    seed_per_stock = total_capital // len(holdings) if holdings else 0

    with st.container(border=True):
        st.caption("Equal-Weight Min Seed")
        st.markdown(f"## {total_capital:,.0f}원")
        st.caption(f"종목당 {seed_per_stock:,.0f}원 기준 × {len(holdings)}종목")

    # 2. 보유 종목 (Holdings) 상세 테이블 (이미지 스타일 완벽 구현)
    st.markdown(f"#### Holdings ({len(holdings)})")

    if holdings:
        h_data = []
        for h in holdings:
            curr = h.get("current_price", 0)
            entry = h.get("entry_price", curr)
            stop = h.get("stop_price", entry * 0.85) # 캐시에 없으면 기본 -15%
            ret = h.get("return_rate", 0.0)

            recent = h.get("recent_30d", [curr]*30)
            day_pct = ((recent[-1] - recent[-2]) / recent[-2] * 100) if len(recent) > 1 else 0.0

            try:
                entry_date = pd.to_datetime(h.get("entry_date", now_kst().strftime("%Y-%m-%d")))
                days_held = (now_kst() - entry_date).days
            except:
                entry_date = now_kst()
                days_held = 0

            # Exit Risk 진행률: (Stop/Current) * 100
            exit_risk = min(100, max(0, int((stop / curr) * 100))) if curr > 0 else 0

            h_data.append({
                "Sector": f"🔴 {h.get('market', 'KOSPI')}",
                "Stock": f"{h['name']} {h.get('market', '')}",
                "Entry Price": entry,
                "Current": curr,
                "Entry Date": entry_date.strftime("%m/%d"),
                "Days": f"D+{days_held}",
                "P&L": ret,
                "Stop": stop,
                "Day %": day_pct,
                "Recent 30d": recent,
                "Fin": "🟢 양호",
                "Exit Risk": exit_risk,
                "Alerts": "—"
            })

        df_h = pd.DataFrame(h_data)

        # Streamlit Column Config를 사용한 시각화 테이블 구성
        st.dataframe(
            df_h,
            column_config={
                "Sector": st.column_config.TextColumn("Sector"),
                "Stock": st.column_config.TextColumn("Stock"),
                "Entry Price": st.column_config.NumberColumn("Entry Price", format="%d"),
                "Current": st.column_config.NumberColumn("Current", format="%d"),
                "P&L": st.column_config.NumberColumn("P&L", format="%.2f%%"),
                "Stop": st.column_config.NumberColumn("Stop", format="%d"),
                "Day %": st.column_config.NumberColumn("Day %", format="%.2f%%"),
                "Recent 30d": st.column_config.LineChartColumn("Recent 30d", y_min=0, y_max=None),
                "Exit Risk": st.column_config.ProgressColumn("Exit Risk", min_value=0, max_value=100, format="%d%%"),
                "Fin": st.column_config.TextColumn("Fin"),
                "Alerts": st.column_config.TextColumn("Alerts")
            },
            hide_index=True,
            use_container_width=True
        )
    else:
        st.info("현재 보유 중인 종목이 없습니다.")

def render_alpha_history(history):
    st.markdown("#### KOSPI 대비 포트폴리오 성과 (Alpha)")
    import FinanceDataReader as fdr

    end_date = now_kst()
    start_date = end_date - timedelta(days=30)

    df_kospi = fdr.DataReader('KS11', start_date.strftime('%Y-%m-%d'))
    if not df_kospi.empty:
        df_kospi['kospi_cum'] = df_kospi['Close'].pct_change().fillna(0).cumsum() * 100
    else:
        df_kospi = pd.DataFrame(columns=['Close', 'kospi_cum'])

    chart_df = pd.DataFrame(index=df_kospi.index)
    chart_df['KOSPI'] = df_kospi['kospi_cum']

    df_hist = pd.DataFrame(history)
    if not df_hist.empty:
        df_hist['date'] = pd.to_datetime(df_hist['date'])
        df_hist = df_hist.set_index('date')
        df_hist['port_cum'] = df_hist['portfolio_return'].cumsum()

        chart_df = chart_df.join(df_hist['port_cum'], how='left')
        chart_df['Portfolio'] = chart_df['port_cum'].ffill().fillna(0)

        cum_ret = chart_df['Portfolio'].iloc[-1]
        day_ret = df_hist['portfolio_return'].iloc[-1]
    else:
        chart_df['Portfolio'] = 0.0
        cum_ret = 0.0
        day_ret = 0.0

    k_cum_ret = chart_df['KOSPI'].iloc[-1] if not chart_df['KOSPI'].empty else 0.0
    k_day_ret = df_kospi['Close'].pct_change().iloc[-1] * 100 if not df_kospi.empty else 0.0

    alpha = cum_ret - k_cum_ret
    chart_df['Alpha'] = chart_df['Portfolio'] - chart_df['KOSPI']
    chart_df['Alpha_Color'] = chart_df['Alpha'].apply(lambda x: '#00B464' if x >= 0 else '#F04452')

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Portfolio 누적", f"{cum_ret:+.2f}%", f"Day {day_ret:+.2f}%")
    with col2:
        st.metric("KOSPI 누적", f"{k_cum_ret:+.2f}%", f"Day {k_day_ret:+.2f}%")
    with col3:
        st.metric("Alpha (초과수익)", f"{alpha:+.2f}%")

    if not chart_df.empty:
        fig = go.Figure()
        custom_data = np.column_stack((chart_df['KOSPI'], chart_df['Alpha'], chart_df['Alpha_Color']))

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
        if len(chart_df) == 1:
            fig.update_layout(xaxis=dict(tickformat="%Y-%m-%d", tickmode='array', tickvals=[chart_df.index[0]]))

        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 정통 퀀트 스크리너 & 오토 트레이딩")

    confirmed, watchlist, last_updated = load_screening_result(supabase)
    holdings, trades, history = load_portfolio_data(supabase)

    # 탭 구성 (UI 이미지와 동일한 탭 구조)
    tab_port, tab_watch, tab_hist = st.tabs([
        f"Portfolio ({len(holdings)})",
        f"Watchlist ({len(watchlist)})",
        "History"
    ])

    with tab_port:
        render_portfolio_dashboard(holdings, trades, history)
        st.divider()
        render_alpha_history(history)

    with tab_watch:
        st.markdown(f"**마지막 스크리닝:** {last_updated or '미실행'}")

        if confirmed:
            st.success(f"🏆 신규 추격매수 확정 ({len(confirmed)}개)")
            df_c = _build_table(confirmed, is_watchlist=False)
            sel_c = st.dataframe(df_c, use_container_width=True, on_select="rerun", selection_mode="single-row", hide_index=True)
            if sel_c and hasattr(sel_c, "selection") and sel_c.selection.rows:
                st.divider()
                _render_detail(confirmed[sel_c.selection.rows[0]], supabase)
            st.divider()

        st.caption(f"👀 추격매수 예비 돌파 종목 ({len(watchlist)}개) - 금액정보 제외")
        if watchlist:
            df_w = _build_table(watchlist, is_watchlist=True)
            sel_w = st.dataframe(df_w, use_container_width=True, on_select="rerun", selection_mode="single-row", hide_index=True)
            if sel_w and hasattr(sel_w, "selection") and sel_w.selection.rows:
                st.divider()
                _render_detail(watchlist[sel_w.selection.rows[0]], supabase)
        else:
            st.info("WatchList 종목이 없습니다.")

    with tab_hist:
        st.markdown("#### 자동 매매 및 이탈 이력")
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
            st.dataframe(styled_t, hide_index=True, use_container_width=True)
        else:
            st.info("최근 매매 이력이 없습니다.")
