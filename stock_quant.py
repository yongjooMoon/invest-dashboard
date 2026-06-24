"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta
import plotly.graph_objects as go
import numpy as np
from quant_core import (
    load_price_from_db, load_screening_result,
    HARD_GATES, SOFT_GATES, now_kst,
)

# ══════════════════════════════════════════
# [Helper] 공통 UI 컴포넌트
# ══════════════════════════════════════════
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

# ══════════════════════════════════════════
# [View 1] 클릭형 인라인 요약 리스트 (체크박스 완전 대체)
# ══════════════════════════════════════════
def render_inline_summary(sel: dict):
    """드롭다운 확장 시 나타나는 미니 요약"""
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        st.markdown(f"**시장:** {sel.get('market', 'KOSPI')}")
        st.markdown(f"**모멘텀 강도:** <span style='color:#F04452'>{sel.get('momentum_score', 0):+.2f}%</span>", unsafe_allow_html=True)
    with col2:
        st.markdown(f"**권장 진입가:** {sel.get('entry_price', sel['current_price']):,} 원")
        st.caption(f"절대 생존 조건 {sel.get('total_pass', 0)}/6 통과")
    with col3:
        if st.button("상세 리포트 ➔", key=f"btn_detail_{sel['symbol']}", use_container_width=True, type="primary"):
            st.session_state.current_view = "detail"
            st.session_state.selected_stock = sel
            st.rerun()

def render_custom_list(results, is_watchlist=False):
    if not results:
        st.info("조건에 부합하는 종목이 없습니다.")
        return

    for idx, r in enumerate(results):
        # 마우스 호버 및 클릭 가능한 드롭다운 바 (체크박스 제거)
        title = f"🏆 {idx+1}위. {r['name']} &nbsp;|&nbsp; 랭킹점수: {r.get('factor_score', 0):.2f}점 &nbsp;|&nbsp; 통과: {r.get('total_pass', 0)}/6 &nbsp;|&nbsp; ₩{r['current_price']:,}"

        with st.expander(title):
            render_inline_summary(r)

# ══════════════════════════════════════════
# [View 2] 전체 화면 상세 리포트 (Stock Search 뷰)
# ══════════════════════════════════════════
def render_detailed_report_screen(supabase):
    sel = st.session_state.selected_stock

    # 상단 네비게이션
    if st.button("⬅️ 목록으로 돌아가기", type="secondary"):
        st.session_state.current_view = "main"
        st.rerun()

    st.markdown(f"## {sel['name']} <span style='font-size:18px; color:#AEC1D4;'>{sel['symbol']} &nbsp;|&nbsp; {sel.get('market', 'KOSPI')}</span>", unsafe_allow_html=True)
    st.markdown(f"<h1>{sel['current_price']:,} 원 <span style='font-size:20px; color:#F04452;'>{sel.get('ret_1m', 0):+.2f}% (1M)</span></h1>", unsafe_allow_html=True)

    st.divider()

    # 1. 퀀트 스코어
    st.markdown("### ⚡ Quant Scores")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("종합 랭킹 스코어", f"{sel.get('factor_score', 0):.2f}점")
    with c2:
        st.metric("생존 필터 통과", f"{sel.get('total_pass', 0)} / 6")
    with c3:
        st.metric("모멘텀 강도", f"{sel.get('momentum_score', 0):+.2f}%")
    with c4:
        st.metric("권장 진입가(지지선)", f"{sel.get('entry_price', sel['current_price']):,.0f}원")

    st.markdown("<br>", unsafe_allow_html=True)

    # 2. Entry Gates 시각화
    st.markdown("### 🛡️ Entry Gates (Survival Conditions)")
    gates_list = [
        ("Growth Composite", "성장성 통합"), ("Dynamic MDD", "동적 방어선"),
        ("Liquidity", "유동성 (50억↑)"), ("Trend Alignment", "추세 정배열"),
        ("Price Breakout", "고점 돌파 임박"), ("Volume Surge", "거래량 폭증")
    ]

    cols = st.columns(6)
    for col, (gate_key, gate_name) in zip(cols, gates_list):
        gate_data = sel.get("filter_details", {}).get(gate_key, {})
        passed = gate_data.get("pass", False)
        color = "#00B464" if passed else "#333333"
        text_color = "white" if passed else "#888888"
        status = "✔️ 통과" if passed else "❌ 미달"
        reason = gate_data.get("reason", "-")

        with col:
            st.markdown(f"""
            <div style="background-color: {color}; padding: 12px; border-radius: 6px; color: {text_color}; height: 110px;">
                <div style="font-size:12px; margin-bottom:5px;">{status}</div>
                <div style="font-weight:bold; font-size:14px; margin-bottom:8px; line-height:1.2;">{gate_name}</div>
                <div style="font-size:11px; opacity:0.8;">{reason}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 3. 불필요 수급 제거 및 핵심 펀더멘털만 노출
    st.markdown("### 📊 Fundamental & Financials")
    with st.container(border=True):
        st.markdown("**재무 실적 및 핵심 지표**")
        st.divider()
        r1, r2, r3, r4 = st.columns(4)
        r1.caption("순이익 YoY"); r1.markdown(f"**{sel.get('net_income_yoy') or 0:+.2f}%**")
        r2.caption("ROE (수익성)"); r2.markdown(f"**{sel.get('roe') or 0:.2f}%**")
        r3.caption("부채비율 (건전성)"); r3.markdown(f"**{sel.get('debt_ratio') or 0:.1f}%**")
        r4.caption("시가총액"); r4.markdown(f"**{sel.get('marcap_억', 0):,.0f} 억**")

    st.markdown("### 📈 가격 차트 (Price History)")
    df_price = load_price_from_db(supabase, sel["symbol"])
    if not df_price.empty:
        st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
    else:
        st.info("가격 데이터 없음")

# ══════════════════════════════════════════
# [View 3] 메인 대시보드 화면
# ══════════════════════════════════════════
def render_main_dashboard(supabase):
    confirmed, watchlist, last_updated = load_screening_result(supabase)
    holdings, trades, history = load_portfolio_data(supabase)

    # 탭 완벽 분리
    tab_port, tab_watch, tab_hist = st.tabs([
        f"Portfolio ({len(holdings)})",
        f"Watchlist ({len(watchlist)})",
        "매도 히스토리 (History)"
    ])

    # ────────────────────────────────────────────────────────
    # 탭 1: 포트폴리오 (Holdings & Alpha) - 마스크 포맷 및 툴팁 팝업 추가
    # ────────────────────────────────────────────────────────
    with tab_port:
        total_capital = sum([h.get("current_price", 0) for h in holdings])
        with st.container(border=True):
            st.caption("현재 포트폴리오 평가 총액 (보유종목 1주 기준 단순 합산)")
            st.markdown(f"## {total_capital:,.0f} 원")

        st.markdown(f"#### Holdings ({len(holdings)})")
        if holdings:
            h_data = []
            for h in holdings:
                curr = h.get("current_price", 0)
                entry = h.get("entry_price", curr)
                stop = h.get("stop_price", entry * 0.85)
                ret = h.get("return_rate", 0.0)

                recent = h.get("recent_30d", [])
                # 30일 데이터가 없거나 평탄한 경우, 스파크라인 표출을 위한 보정 (진입가~현재가 선)
                if len(recent) < 2:
                    recent = [entry, curr]

                day_pct = ((recent[-1] - recent[-2]) / recent[-2] * 100) if len(recent) > 1 else 0.0

                try:
                    entry_date = pd.to_datetime(h.get("entry_date", now_kst().strftime("%Y-%m-%d")))
                    days_held = (now_kst() - entry_date).days
                except:
                    entry_date = now_kst()
                    days_held = 0

                # Exit Risk의 정확한 논리: 현재가가 지지선(Stop)에 얼마나 근접했는가?
                # 거리가 15% 이상 떨어져있으면 위험 0%, 거리가 0%면 위험 100%
                distance_pct = (curr - stop) / curr * 100 if curr > 0 else 0
                exit_risk = max(0, min(100, int(100 - (distance_pct / 15 * 100))))

                # 툴팁용 팝업 정보
                risk_info = f"현재가({curr:,}원)가 손절/익절 지지선({stop:,.0f}원) 대비 {distance_pct:.1f}% 여유가 있습니다. (위험도: {exit_risk}%)"

                h_data.append({
                    "Stock": f"{h['name']}",
                    "Entry Price": entry,
                    "Current": curr,
                    "P&L": ret,
                    "Stop": stop,
                    "Recent 30d": recent,
                    "Exit Risk": exit_risk,
                    "Risk Info": risk_info
                })

            df_h = pd.DataFrame(h_data)

            # Mask 포맷팅 완벽 적용 (%,d 및 %.2f)
            st.dataframe(
                df_h,
                column_config={
                    "Stock": st.column_config.TextColumn("Stock"),
                    "Entry Price": st.column_config.NumberColumn("Entry Price", format="%,d"),
                    "Current": st.column_config.NumberColumn("Current", format="%,d"),
                    "P&L": st.column_config.NumberColumn("P&L", format="%.2f%%"),
                    "Stop": st.column_config.NumberColumn("Stop", format="%,d"),
                    "Recent 30d": st.column_config.LineChartColumn("Recent 30d", y_min=0, y_max=None),
                    "Exit Risk": st.column_config.ProgressColumn("Exit Risk (%)", min_value=0, max_value=100, format="%d%%"),
                    # 툴팁 형태의 인포메이션 (마우스 호버 시 상세 내용 노출)
                    "Risk Info": st.column_config.TextColumn("🔍 위험도 상세 (Hover)", help="셀에 마우스를 올리시면 왜 위험도가 산정되었는지 표시됩니다.")
                },
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("현재 보유 중인 종목이 없습니다.")

        st.divider()
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

            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['Portfolio'], mode='lines+markers', name='Portfolio', line=dict(color='#F04452', width=2.5), fill='tozeroy', fillcolor='rgba(240, 68, 82, 0.05)', customdata=custom_data, hovertemplate="<span style='color:#AEC1D4; font-size:12px;'>%{x|%Y.%m.%d}</span><br><br><span style='color:#3182F6;'>●</span> Portfolio &nbsp;&nbsp;&nbsp;<b>%{y:.2f}%</b><br><span style='color:#8B95A1;'>●</span> KOSPI &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>%{customdata[0]:.2f}%</b><br>─────────────────<br><span style='color:#8B95A1;'>α</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b style='color:%{customdata[2]};'>%{customdata[1]:+.2f}%</b><extra></extra>"))
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['KOSPI'], mode='lines+markers', name='KOSPI', line=dict(color='#8B95A1', width=1.5, dash='dot'), hoverinfo='skip'))
            fig.update_layout(hovermode='x', xaxis=dict(showgrid=False, zeroline=False, tickformat="%Y-%m-%d"), yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)', ticksuffix="%"), hoverlabel=dict(bgcolor="#191F28", font_color="white"), margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False)
            if len(chart_df) == 1:
                fig.update_layout(xaxis=dict(tickformat="%Y-%m-%d", tickmode='array', tickvals=[chart_df.index[0]]))
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # ────────────────────────────────────────────────────────
    # 탭 2: Watchlist & Confirmed (체크박스 완전 제거)
    # ────────────────────────────────────────────────────────
    with tab_watch:
        st.markdown(f"**마지막 스크리닝:** {last_updated or '미실행'}")

        if confirmed:
            st.success(f"🏆 신규 추격매수 확정 ({len(confirmed)}개)")
            render_custom_list(confirmed, is_watchlist=False)
            st.divider()

        st.caption(f"👀 추격매수 예비 돌파 종목 ({len(watchlist)}개)")
        if watchlist:
            render_custom_list(watchlist, is_watchlist=True)
        else:
            st.info("WatchList 종목이 없습니다.")

    # ────────────────────────────────────────────────────────
    # 탭 3: 매도 히스토리 (History) - 실제 손익금액(원) 추가
    # ────────────────────────────────────────────────────────
    with tab_hist:
        st.markdown("#### 자동 매도 (이탈) 완료 히스토리")

        # BUY를 제외하고 SELL(매도) 데이터만 필터링
        sell_trades = [t for t in trades[::-1] if t.get('type') == 'SELL']

        if sell_trades:
            # 진입가 기준 정확한 손익금(원) 산출
            for t in sell_trades:
                trade_p = t.get('trade_price', 0)
                ret_pct = t.get('return_rate', 0.0)

                # 역산하여 Entry Price 도출
                if ret_pct != -100:
                    entry = trade_p / (1 + (ret_pct / 100))
                else:
                    entry = 0

                t['entry_price'] = entry
                t['profit_amount'] = trade_p - entry # 실제 손익금(원)

            t_df = pd.DataFrame(sell_trades)[["trade_date", "name", "entry_price", "trade_price", "return_rate", "profit_amount", "reason"]]
            t_df.columns = ["매도 일자", "종목명", "진입가", "매도가", "실현손익(%)", "손익금(원)", "매도 사유"]

            styled_t = t_df.style.map(
                lambda x: 'color: #F04452' if x > 0 else 'color: #3182F6',
                subset=['실현손익(%)', '손익금(원)']
            ).format({
                "진입가": "{:,.0f}",
                "매도가": "{:,.0f}",
                "실현손익(%)": "{:,.2f}",
                "손익금(원)": "{:,.0f}" # 마스크(콤마) 포맷 반영
            })
            st.dataframe(styled_t, hide_index=True, use_container_width=True)
        else:
            st.info("최근 매도(이탈) 이력이 없습니다.")

# ══════════════════════════════════════════
# [Main Entry Point]
# ══════════════════════════════════════════
def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    # Session State 초기화 (상세 화면 라우팅용)
    if "current_view" not in st.session_state:
        st.session_state.current_view = "main"
    if "selected_stock" not in st.session_state:
        st.session_state.selected_stock = None

    if st.session_state.current_view == "main":
        st.title("📡 정통 퀀트 스크리너 & 오토 트레이딩")
        render_main_dashboard(supabase)
    elif st.session_state.current_view == "detail":
        render_detailed_report_screen(supabase)
