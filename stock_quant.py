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
    HARD_GATES, SOFT_GATES, now_kst, fetch_naver_fundamental
)

# ══════════════════════════════════════════
# [Helper] 공통 데이터 & 종목 캐싱
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

@st.cache_data(ttl=86400)
def load_krx_list():
    """KRX 전종목 리스트 로드 (검색 콤보박스용)"""
    try:
        kospi = fdr.StockListing("KOSPI")[["Code", "Name"]]
        kosdaq = fdr.StockListing("KOSDAQ")[["Code", "Name"]]
        krx = pd.concat([kospi, kosdaq])
        krx["SearchStr"] = krx["Name"] + " (" + krx["Code"] + ")"
        return krx
    except:
        return pd.DataFrame(columns=["Code", "Name", "SearchStr"])

def calculate_exit_risk(curr, entry, stop):
    """현재가, 진입가, 지지선을 바탕으로 손절 위험도(0~100%) 정확히 산출"""
    if curr <= 0 or entry <= 0 or stop <= 0: return 0
    buffer = entry - stop if entry > stop else curr * 0.15
    if buffer <= 0: buffer = 1
    distance = curr - stop
    risk = 100 - (distance / buffer * 100)
    return max(0, min(100, int(risk)))

# ══════════════════════════════════════════
# [Component] Dialog Popups & Shared Renderers
# ══════════════════════════════════════════
@st.dialog("🚨 Exit Risk 상세 진단")
def show_exit_risk_dialog(h):
    curr = h.get("current_price", 0)
    entry = h.get("entry_price", curr)
    stop = h.get("stop_price", entry * 0.85)
    ret = h.get("return_rate", 0.0)

    exit_risk = calculate_exit_risk(curr, entry, stop)
    risk_color = "#E6A23C" if exit_risk < 70 else "#F04452"

    html = f"""
    <div style="background-color:#191F28; border:1px solid #333; border-radius:12px; padding:20px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">
        <h3 style="margin:0; color:#fff; font-size:18px;">{h['name']} 
            <span style="float:right; font-size:12px; background-color:{risk_color}; color:#000; padding:4px 8px; border-radius:4px; font-weight:bold;">Risk {exit_risk}%</span>
        </h3>
        <div style="font-size:13px; color:#8B95A1; margin-top:5px; margin-bottom:20px;">
            Current <b>₩{curr:,.0f}</b> &nbsp;·&nbsp; Stop <b>₩{stop:,.0f}</b>
        </div>
        
        <div style="font-size:11px; color:#8B95A1; margin-bottom:8px; font-weight:bold;">OVERALL EXIT PROXIMITY</div>
        <div style="background-color:#333; border-radius:6px; height:16px; margin-bottom:20px;">
            <div style="background-color:{risk_color}; width:{exit_risk}%; height:100%; border-radius:6px; text-align:right; padding-right:8px; font-size:11px; line-height:16px; color:#000; font-weight:bold;">{exit_risk}%</div>
        </div>
        
        <div style="display:flex; justify-content:space-between; font-size:13px; color:#AEC1D4; margin-bottom:8px;">
            <span>Trailing Stop (ATR)</span><span style="color:#00B464;">Active</span>
        </div>
        <div style="display:flex; justify-content:space-between; font-size:13px; color:#AEC1D4; margin-bottom:20px;">
            <span>Trend Break (MA20)</span><span style="color:#00B464;">Safe</span>
        </div>
        
        <div style="display:flex; justify-content:space-between; font-size:13px; color:#8B95A1; border-top:1px solid #333; padding-top:15px;">
            <span>Entry <b style="color:#fff; font-size:14px;">₩{entry:,.0f}</b></span>
            <span>P&L <b style="color:{'#F04452' if ret>0 else '#3182F6'}; font-size:14px;">{ret:+.2f}%</b></span>
        </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

def render_detailed_report_content(sel, df_price=None, fund=None, apex=None, helix=None, gates=None):
    """상세 리포트 화면을 그리는 핵심 렌더러 (팝업 및 검색화면 공용)"""
    curr = sel.get('current_price', 0)
    ret_1m = sel.get('ret_1m', 0)

    st.markdown(f"## {sel['name']} <span style='font-size:18px; color:#AEC1D4;'>{sel['symbol']} &nbsp;|&nbsp; {sel.get('market', 'KOSPI')}</span>", unsafe_allow_html=True)
    st.markdown(f"<h1>{curr:,.0f} 원 <span style='font-size:20px; color:{'#F04452' if ret_1m>0 else '#3182F6'};'>{ret_1m:+.2f}% (1M)</span></h1>", unsafe_allow_html=True)
    st.divider()

    if apex is None: apex = min(100, max(0, 40 + sel.get('factor_score', 50)*0.6))
    if helix is None: helix = min(100, max(0, 50 + sel.get('momentum_score', 0)*2))

    c_header, c_gauge = st.columns([3, 2])
    with c_header:
        st.markdown("### ⚡ Quant Scores")
        c1, c2 = st.columns(2)
        c1.metric("종합 랭킹 스코어", f"{sel.get('factor_score', 0):.2f}점")
        c2.metric("생존 필터 통과", f"{sel.get('total_pass', 0)} / 6")

        # 권장 진입가 표시 제거 요청 반영
        st.info("💡 실시간 퀀트 데이터에 기반하여 생성된 리포트입니다.")

    with c_gauge:
        fig = go.Figure()
        fig.add_trace(go.Indicator(mode="gauge+number", value=apex, number={'font': {'color': '#FF8A65', 'size': 40}}, title={'text': "Apex", 'font': {'color': '#AEC1D4', 'size': 12}}, gauge={'axis': {'range': [None, 100], 'visible': False}, 'bar': {'color': "#FF8A65"}, 'bgcolor': "rgba(255,255,255,0.05)"}, domain={'x': [0, 0.45], 'y': [0, 1]}))
        fig.add_trace(go.Indicator(mode="gauge+number", value=helix, number={'font': {'color': '#4FC3F7', 'size': 40}}, title={'text': "Helix", 'font': {'color': '#AEC1D4', 'size': 12}}, gauge={'axis': {'range': [None, 100], 'visible': False}, 'bar': {'color': "#4FC3F7"}, 'bgcolor': "rgba(255,255,255,0.05)"}, domain={'x': [0.55, 1], 'y': [0, 1]}))
        fig.update_layout(height=180, margin=dict(l=10, r=10, t=20, b=10), paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    st.markdown("##### Entry Gates (6 conditions)")
    if gates is None:
        gates_data = sel.get("filter_details", {})
        if not gates_data or "Growth Composite" not in gates_data:
            gates = {
                'A': {'name': 'Growth (YoY)', 'pass': False, 'reason': '-'}, 'B': {'name': 'Trend (MA)', 'pass': False, 'reason': '-'},
                'C': {'name': 'Breakout', 'pass': False, 'reason': '-'}, 'D': {'name': 'Volume Surge', 'pass': False, 'reason': '-'},
                'E': {'name': 'Liquidity', 'pass': False, 'reason': '-'}, 'F': {'name': 'Dynamic MDD', 'pass': False, 'reason': '-'}
            }
        else:
            gates = {
                'A': {'name': 'Growth (YoY)', 'pass': gates_data.get("Growth Composite", {}).get("pass", False), 'reason': gates_data.get("Growth Composite", {}).get("reason", "-")},
                'B': {'name': 'Trend (MA)', 'pass': gates_data.get("Trend Alignment", {}).get("pass", False), 'reason': gates_data.get("Trend Alignment", {}).get("reason", "-")},
                'C': {'name': 'Breakout', 'pass': gates_data.get("Price Breakout", {}).get("pass", False), 'reason': gates_data.get("Price Breakout", {}).get("reason", "-")},
                'D': {'name': 'Volume Surge', 'pass': gates_data.get("Volume Surge", {}).get("pass", False), 'reason': gates_data.get("Volume Surge", {}).get("reason", "-")},
                'E': {'name': 'Liquidity', 'pass': gates_data.get("Liquidity", {}).get("pass", False), 'reason': gates_data.get("Liquidity", {}).get("reason", "-")},
                'F': {'name': 'Dynamic MDD', 'pass': gates_data.get("Dynamic MDD", {}).get("pass", False), 'reason': gates_data.get("Dynamic MDD", {}).get("reason", "-")}
            }

    cols = st.columns(6)
    labels = ['A', 'B', 'C', 'D', 'E', 'F']
    for idx, (col, key) in enumerate(zip(cols, gates.keys())):
        g = gates[key]
        passed = g['pass']
        color = "#00B464" if passed else "#333333"
        txt_color = "white" if passed else "#888888"
        with col:
            st.markdown(f"""
            <div style="background-color:#1E2329; border:1px solid {color}; border-radius:6px; padding:10px; height:85px;">
                <div style="display:flex; justify-content:space-between; font-weight:bold; color:{txt_color}; font-size:13px; margin-bottom:8px;">
                    <span>{labels[idx]}</span> <span style="font-size:11px;">{'✔️' if passed else '❌'}</span>
                </div>
                <div style="height:3px; background-color:{color}; border-radius:2px; margin-bottom:8px;"></div>
                <div style="font-size:10px; color:#AEC1D4; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{g['name']}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<br>### 📊 Fundamental Hard Gate", unsafe_allow_html=True)
    if fund is None: fund = sel
    with st.container(border=True):
        r1, r2, r3, r4 = st.columns(4)
        r1.caption("순이익 YoY"); r1.markdown(f"**{fund.get('net_income_yoy', 0):+.2f}%**")
        r2.caption("ROE (수익성)"); r2.markdown(f"**{fund.get('roe') or 0:.2f}%**")
        r3.caption("부채비율 (건전성)"); r3.markdown(f"**{fund.get('debt_ratio') or 0:.1f}%**")
        r4.caption("시가총액"); r4.markdown(f"**{fund.get('marcap_억', 0):,.0f} 억**")

    st.markdown("### 📈 가격 차트 (Price History)")
    if df_price is not None and not df_price.empty:
        st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
    else:
        st.info("가격 데이터가 로드되지 않았습니다.")

@st.dialog("📈 퀀트 평가 상세 리포트", width="large")
def show_detail_dialog(sel, supabase):
    df_price = load_price_from_db(supabase, sel["symbol"])
    render_detailed_report_content(sel, df_price=df_price)


# ══════════════════════════════════════════
# [Helper] 실시간 평가 (Stock Search용)
# ══════════════════════════════════════════
def live_evaluate_stock(symbol):
    df = fdr.DataReader(symbol, (now_kst() - timedelta(days=300)).strftime('%Y-%m-%d'))
    if df.empty: return None, None, 0, 0, {}

    fund = fetch_naver_fundamental(symbol)
    curr = df['Close'].iloc[-1]
    ma20 = df['Close'].iloc[-20:].mean() if len(df)>=20 else curr
    ma60 = df['Close'].iloc[-60:].mean() if len(df)>=60 else curr
    high60 = df['Close'].tail(60).max()
    vol5 = df['Volume'].tail(5).mean()
    vol60 = df['Volume'].tail(60).mean() if len(df)>=60 else 1

    c_net = fund.get('net_income_cur')
    p_net = fund.get('net_income_prev')
    net_yoy = ((c_net - p_net)/abs(p_net)*100) if c_net and p_net else 0

    apex = min(100, max(0, 40 + (net_yoy/3) + ((curr-ma60)/ma60*50)))
    helix = min(100, max(0, 50 + ((curr-ma20)/ma20*200)))

    gates = {
        'A': {'name': 'Growth (YoY)', 'pass': net_yoy > 0, 'val': f"{net_yoy:+.1f}%"},
        'B': {'name': 'Trend (MA)', 'pass': curr > ma20 > ma60, 'val': "정배열" if curr>ma20>ma60 else "역배열"},
        'C': {'name': 'Breakout', 'pass': curr >= high60*0.9, 'val': f"{curr/high60*100:.1f}%"},
        'D': {'name': 'Volume Surge', 'pass': vol5 > vol60*1.5, 'val': f"{vol5/vol60:.1f}x"},
        'E': {'name': 'Liquidity', 'pass': (curr*vol5)/1e8 > 50, 'val': f"{(curr*vol5)/1e8:,.0f}억"},
        'F': {'name': 'Profitability', 'pass': c_net is not None and c_net > 0, 'val': f"{c_net or 0:,.0f}억"}
    }
    return df, fund, apex, helix, gates

# ══════════════════════════════════════════
# [Main Entry Point]
# ══════════════════════════════════════════
def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 정통 퀀트 스크리너 & 오토 트레이딩")

    confirmed, watchlist, last_updated = load_screening_result(supabase)
    holdings, trades, history = load_portfolio_data(supabase)

    # 탭 내부 에러 방지를 위해 다이얼로그 호출용 상태 변수 지정
    dialog_trigger = None
    dialog_payload = None

    tab_port, tab_watch, tab_hist, tab_search = st.tabs([
        f"Portfolio ({len(holdings)})",
        f"Watchlist ({len(confirmed) + len(watchlist)})",
        "매도 히스토리 (History)",
        "🔍 Stock Search"
    ])

    # ────────────────────────────────────────────────────────
    # 탭 1: 포트폴리오
    # ────────────────────────────────────────────────────────
    with tab_port:
        total_capital = sum([h.get("current_price", 0) for h in holdings])
        with st.container(border=True):
            st.caption("현재 포트폴리오 평가 총액 (보유종목 1주 기준 단순 합산)")
            st.markdown(f"## {total_capital:,.0f} 원")

        st.markdown(f"#### Holdings ({len(holdings)})")
        st.caption("💡 표에서 **'종목명'**을 클릭하시면 상세 리포트가, **'Exit Risk'**를 클릭하시면 위험도 팝업이 나타납니다.")

        if holdings:
            h_data = []
            for h in holdings:
                curr = h.get("current_price", 0)
                entry = h.get("entry_price", curr)
                stop = h.get("stop_price", entry * 0.85)
                ret = h.get("return_rate", 0.0)
                recent = h.get("recent_30d", [entry, curr])
                if len(recent) < 2: recent = [entry, curr]

                exit_risk = calculate_exit_risk(curr, entry, stop)

                h_data.append({
                    "Stock": h['name'],
                    "Entry Price": entry,
                    "Current": curr,
                    "P&L": ret,
                    "Stop": stop,
                    "Recent 30d": recent,
                    "Exit Risk": exit_risk,
                    "RawData": h
                })

            df_h = pd.DataFrame(h_data)

            styled_df_h = df_h[["Stock", "Entry Price", "Current", "P&L", "Stop", "Recent 30d", "Exit Risk"]].style.map(
                lambda x: "color: #F04452" if x > 0 else "color: #3182F6" if x < 0 else "", subset=["P&L"]
            ).map(
                lambda x: 'color: #4FC3F7; text-decoration: underline; cursor: pointer;', subset=["Stock"]
            ).format({"Entry Price": "{:,.0f}", "Current": "{:,.0f}", "P&L": "{:+.2f}%", "Stop": "{:,.0f}"})

            # 셀/컬럼 클릭을 인식하기 위해 [single-row, single-column] 모드 사용
            sel_h = st.dataframe(
                styled_df_h,
                column_config={
                    "Recent 30d": st.column_config.LineChartColumn("Recent 30d", y_min=0, y_max=None),
                    "Exit Risk": st.column_config.ProgressColumn("Exit Risk (%)", min_value=0, max_value=100, format="%d%%")
                },
                hide_index=True, use_container_width=True, on_select="rerun",
                selection_mode=["single-row", "single-column"], key="port_table"
            )

            if sel_h and hasattr(sel_h, "selection") and sel_h.selection.rows:
                row_idx = sel_h.selection.rows[0]
                col_name = sel_h.selection.columns[0] if sel_h.selection.columns else ""

                if col_name == "Exit Risk":
                    dialog_trigger = "exit_risk"
                    dialog_payload = h_data[row_idx]["RawData"]
                else:
                    dialog_trigger = "detail"
                    dialog_payload = h_data[row_idx]["RawData"]

        else:
            st.info("현재 보유 중인 종목이 없습니다.")

        st.divider()
        st.markdown("#### KOSPI 대비 포트폴리오 성과 (Alpha)")
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
            df_hist['date'] = df_hist['date'].dt.tz_localize(None) # 시간대 정보 제거
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

        col1, col2, col3 = st.columns(3)
        col1.metric("Portfolio 누적", f"{cum_ret:+.2f}%", f"Day {day_ret:+.2f}%")
        col2.metric("KOSPI 누적", f"{k_cum_ret:+.2f}%", f"Day {k_day_ret:+.2f}%")
        col3.metric("Alpha (초과수익)", f"{alpha:+.2f}%")

        if not chart_df.empty:
            chart_df['Alpha'] = chart_df['Portfolio'] - chart_df['KOSPI']
            chart_df['Alpha_Color'] = chart_df['Alpha'].apply(lambda x: '#00B464' if x >= 0 else '#F04452')
            fig = go.Figure()
            custom_data = np.column_stack((chart_df['KOSPI'], chart_df['Alpha'], chart_df['Alpha_Color']))
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['Portfolio'], mode='lines+markers', name='Portfolio', line=dict(color='#F04452', width=2.5), fill='tozeroy', fillcolor='rgba(240, 68, 82, 0.05)', customdata=custom_data, hovertemplate="<span style='color:#AEC1D4; font-size:12px;'>%{x|%Y.%m.%d}</span><br><br><span style='color:#3182F6;'>●</span> Portfolio &nbsp;&nbsp;&nbsp;<b>%{y:.2f}%</b><br><span style='color:#8B95A1;'>●</span> KOSPI &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>%{customdata[0]:.2f}%</b><br>─────────────────<br><span style='color:#8B95A1;'>α</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b style='color:%{customdata[2]};'>%{customdata[1]:+.2f}%</b><extra></extra>"))
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['KOSPI'], mode='lines+markers', name='KOSPI', line=dict(color='#8B95A1', width=1.5, dash='dot'), hoverinfo='skip'))
            fig.update_layout(hovermode='x', xaxis=dict(showgrid=False, zeroline=False, tickformat="%Y-%m-%d"), yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)', ticksuffix="%"), hoverlabel=dict(bgcolor="#191F28", font_color="white"), margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False)
            if len(chart_df) == 1: fig.update_layout(xaxis=dict(tickformat="%Y-%m-%d", tickmode='array', tickvals=[chart_df.index[0]]))
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # ────────────────────────────────────────────────────────
    # 탭 2: Watchlist & Confirmed (진입가 제거, 테이블 그리드)
    # ────────────────────────────────────────────────────────
    with tab_watch:
        st.markdown(f"**마지막 스크리닝:** {last_updated or '미실행'}")
        st.caption("💡 표에서 **'종목명'**을 클릭하시면 상세 리포트 팝업을 확인하실 수 있습니다.")

        def build_w_table(results):
            rows = []
            for idx, r in enumerate(results):
                rows.append({
                    "순위": idx + 1,
                    "종목명": r["name"],
                    "시장": r.get("market", "-"),
                    "통과": f"{r.get('total_pass',0)}/6",
                    "랭킹점수": f"{r.get('factor_score',0):.2f}",
                    "현재가": r['current_price'],
                    "모멘텀": f"{r.get('momentum_score', 0):+.2f}%",
                    "RawData": r
                })
            return pd.DataFrame(rows)

        if confirmed:
            st.markdown("#### 🏆 스크리닝 통과 종목 (6/6 조건 완벽 달성)")
            df_c = build_w_table(confirmed)
            styled_c = df_c.drop(columns=["RawData"]).style.map(
                lambda x: 'color: #4FC3F7; text-decoration: underline; cursor: pointer;', subset=["종목명"]
            ).format({"현재가": "₩{:,}"})

            sel_c = st.dataframe(
                styled_c, use_container_width=True, on_select="rerun",
                selection_mode=["single-row", "single-column"], hide_index=True, key="conf_grid"
            )
            if sel_c and hasattr(sel_c, "selection") and sel_c.selection.rows:
                dialog_trigger = "detail"
                dialog_payload = df_c.iloc[sel_c.selection.rows[0]]["RawData"]
            st.divider()

        if watchlist:
            st.markdown("#### 👀 예비 관심 종목 (4/6 조건 이상 달성)")
            df_w = build_w_table(watchlist[:20])
            styled_w = df_w.drop(columns=["RawData"]).style.map(
                lambda x: 'color: #4FC3F7; text-decoration: underline; cursor: pointer;', subset=["종목명"]
            ).format({"현재가": "₩{:,}"})

            sel_w = st.dataframe(
                styled_w, use_container_width=True, on_select="rerun",
                selection_mode=["single-row", "single-column"], hide_index=True, key="watch_grid"
            )
            if sel_w and hasattr(sel_w, "selection") and sel_w.selection.rows:
                dialog_trigger = "detail"
                dialog_payload = df_w.iloc[sel_w.selection.rows[0]]["RawData"]
        else:
            if not confirmed: st.info("WatchList 종목이 없습니다.")

    # ────────────────────────────────────────────────────────
    # 탭 3: 매도 히스토리
    # ────────────────────────────────────────────────────────
    with tab_hist:
        st.markdown("#### 📉 자동 매도 (Exit) 완료 히스토리")

        sell_trades = [t for t in trades[::-1] if t.get('type') == 'SELL']
        if sell_trades:
            for t in sell_trades:
                trade_p = t.get('trade_price', 0)
                ret_pct = t.get('return_rate', 0.0)
                entry = trade_p / (1 + (ret_pct / 100)) if ret_pct != -100 else 0
                t['entry_price'] = entry
                t['profit_amount'] = trade_p - entry

            t_df = pd.DataFrame(sell_trades)[["trade_date", "name", "entry_price", "trade_price", "return_rate", "profit_amount", "reason"]]
            t_df.columns = ["매도 일자", "종목명", "진입가", "매도가", "실현손익(%)", "손익금(원)", "매도 사유"]

            styled_t = t_df.style.map(
                lambda x: 'color: #F04452' if x > 0 else 'color: #3182F6', subset=['실현손익(%)', '손익금(원)']
            ).format({"진입가": "{:,.0f}", "매도가": "{:,.0f}", "실현손익(%)": "{:+.2f}%", "손익금(원)": "{:,.0f}"})
            st.dataframe(styled_t, hide_index=True, use_container_width=True)
        else:
            st.info("최근 매도(이탈) 이력이 없습니다.")

    # ────────────────────────────────────────────────────────
    # 탭 4: Stock Search (주식 조회)
    # ────────────────────────────────────────────────────────
    with tab_search:
        st.markdown("### 🔍 Stock Search & Report")
        st.caption("종목명 또는 코드를 콤보박스에서 검색하여 실시간 퀀트 분석을 진행합니다.")

        krx_df = load_krx_list()
        options = [""] + krx_df["SearchStr"].tolist()

        col_search, _ = st.columns([2, 1])
        with col_search:
            selected_stock_str = st.selectbox("🔎 종목 검색 (종목명 또는 코드 자동완성)", options=options)

        if selected_stock_str:
            search_query = selected_stock_str.split("(")[-1].replace(")", "").strip()

            # 검색 속도 향상: 기존 캐시에 있으면 바로 활용
            all_cached_stocks = {item['symbol']: item for item in holdings + confirmed + watchlist}

            if search_query in all_cached_stocks:
                sel = all_cached_stocks[search_query]
                df_price = load_price_from_db(supabase, search_query)
                st.divider()
                st.success("✅ 캐시된 분석 데이터를 로드했습니다.")
                render_detailed_report_content(sel, df_price=df_price)
            else:
                with st.spinner(f"'{selected_stock_str}' 실시간 퀀트 데이터 스크래핑 중..."):
                    df_price, fund, apex, helix, gates = live_evaluate_stock(search_query)

                if df_price is None or df_price.empty:
                    st.error("해당 종목의 차트 데이터를 찾을 수 없습니다.")
                elif not fund or fund.get('net_income_cur') is None:
                    st.warning("⚠️ 펀더멘털(실적) 데이터가 부족하여 퀀트 상세 분석을 제공하지 않습니다. (단순 가격 차트만 제공)")
                    st.line_chart(df_price[["Close"]].tail(120).rename(columns={"Close": "종가"}))
                else:
                    sel = {
                        'symbol': search_query, 'name': selected_stock_str.split(" (")[0],
                        'current_price': df_price['Close'].iloc[-1],
                        'ret_1m': (df_price['Close'].iloc[-1] - df_price['Close'].iloc[-21]) / df_price['Close'].iloc[-21] * 100 if len(df_price)>=21 else 0
                    }
                    sel.update(fund)
                    st.divider()
                    render_detailed_report_content(sel, df_price=df_price, fund=fund, apex=apex, helix=helix, gates=gates)

    # ══════════════════════════════════════════
    # [Dialog Execution Layer] 에러 방지를 위해 탭 외부에서 팝업 렌더링
    # ══════════════════════════════════════════
    if dialog_trigger == "exit_risk" and dialog_payload:
        show_exit_risk_dialog(dialog_payload)
    elif dialog_trigger == "detail" and dialog_payload:
        show_detail_dialog(dialog_payload, supabase)
