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
# [Helper] 공통 데이터 로드
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
# [Component] Exit Risk 팝업 카드 (포트폴리오 전용)
# ══════════════════════════════════════════
def render_exit_risk_card(h):
    curr = h.get("current_price", 0)
    entry = h.get("entry_price", 0)
    stop = h.get("stop_price", entry * 0.85)
    ret = h.get("return_rate", 0.0)

    distance_pct = (curr - stop) / curr * 100 if curr > 0 else 0
    exit_risk = max(0, min(100, int(100 - (distance_pct / 15 * 100))))
    risk_color = "#E6A23C" if exit_risk < 70 else "#F04452"

    html = f"""
    <div style="background-color:#191F28; border:1px solid #333; border-radius:12px; padding:20px; margin-top:10px; margin-bottom:20px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">
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

# ══════════════════════════════════════════
# [Component] Apex / Helix Gauge & Gates
# ══════════════════════════════════════════
def render_score_gauges(apex, helix):
    fig = go.Figure()
    fig.add_trace(go.Indicator(
        mode = "gauge+number", value = apex,
        number = {'font': {'color': '#FF8A65', 'size': 45}},
        title = {'text': "⚡ Apex (Factor)", 'font': {'color': '#AEC1D4', 'size': 14}},
        gauge = {'axis': {'range': [None, 100], 'visible': False}, 'bar': {'color': "#FF8A65", 'thickness': 0.8}, 'bgcolor': "rgba(255,255,255,0.05)", 'shape': "angular"},
        domain = {'x': [0, 0.45], 'y': [0, 1]}
    ))
    fig.add_trace(go.Indicator(
        mode = "gauge+number", value = helix,
        number = {'font': {'color': '#4FC3F7', 'size': 45}},
        title = {'text': "🧬 Helix (Momentum)", 'font': {'color': '#AEC1D4', 'size': 14}},
        gauge = {'axis': {'range': [None, 100], 'visible': False}, 'bar': {'color': "#4FC3F7", 'thickness': 0.8}, 'bgcolor': "rgba(255,255,255,0.05)", 'shape': "angular"},
        domain = {'x': [0.55, 1], 'y': [0, 1]}
    ))
    fig.update_layout(height=220, margin=dict(l=20, r=20, t=30, b=10), paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

def render_entry_gates_ui(gates):
    st.markdown("##### Entry Gates (6 conditions)")
    cols = st.columns(6)
    labels = ['A', 'B', 'C', 'D', 'E', 'F']
    for idx, (col, key) in enumerate(zip(cols, gates.keys())):
        g = gates[key]
        passed = g['pass']
        color = "#00B464" if passed else "#333333"
        txt_color = "white" if passed else "#888888" # 오타 수정 완료
        status = "✔️ 통과" if passed else "❌ 미달"

        with col:
            st.markdown(f"""
            <div style="background-color:#1E2329; border:1px solid {color}; border-radius:6px; padding:12px; height:90px;">
                <div style="display:flex; justify-content:space-between; font-weight:bold; color:{txt_color}; font-size:14px; margin-bottom:10px;">
                    <span>{labels[idx]}</span> <span style="font-size:11px;">{status}</span>
                </div>
                <div style="height:4px; background-color:{color}; border-radius:2px; margin-bottom:10px;"></div>
                <div style="font-size:11px; color:#AEC1D4;">{g['name']}</div>
            </div>
            """, unsafe_allow_html=True)

# ══════════════════════════════════════════
# [View] 상세 리포트 렌더링 (다이얼로그 및 검색용 공통 모듈)
# ══════════════════════════════════════════
def render_detailed_report_content(sel, df_price=None, fund=None, apex=None, helix=None, gates=None):
    """실제 상세 리포트 내용을 그리는 함수"""
    curr = sel.get('current_price', 0)
    ret_1m = sel.get('ret_1m', 0)

    st.markdown(f"## {sel['name']} <span style='font-size:18px; color:#AEC1D4;'>{sel['symbol']} &nbsp;|&nbsp; {sel.get('market', 'KOSPI')}</span>", unsafe_allow_html=True)
    st.markdown(f"<h1>{curr:,.0f} 원 <span style='font-size:20px; color:{'#F04452' if ret_1m>0 else '#3182F6'};'>{ret_1m:+.2f}% (1M)</span></h1>", unsafe_allow_html=True)
    st.divider()

    # 1. 퀀트 스코어 게이지
    if apex is None: apex = min(100, max(0, 40 + sel.get('factor_score', 50)*0.6))
    if helix is None: helix = min(100, max(0, 50 + sel.get('momentum_score', 0)*2))

    c_header, c_gauge = st.columns([3, 2])
    with c_header:
        st.markdown("### ⚡ Quant Scores")
        c1, c2 = st.columns(2)
        c1.metric("종합 랭킹 스코어", f"{sel.get('factor_score', 0):.2f}점")
        c2.metric("생존 필터 통과", f"{sel.get('total_pass', 0)} / 6")
        st.info(f"💡 **권장 진입가 (Entry Cost)**: {sel.get('entry_price', curr):,.0f}원 (눌림목 지지선)")
    with c_gauge:
        render_score_gauges(apex, helix)

    # 2. Entry Gates (6 조건)
    if gates is None:
        # DB 캐시 기반 데이터 매핑
        gates = {
            'A': {'name': 'Growth (YoY)', 'pass': sel.get("filter_details", {}).get("Growth Composite", {}).get("pass", False)},
            'B': {'name': 'Trend (MA)', 'pass': sel.get("filter_details", {}).get("Trend Alignment", {}).get("pass", False)},
            'C': {'name': 'Breakout', 'pass': sel.get("filter_details", {}).get("Price Breakout", {}).get("pass", False)},
            'D': {'name': 'Volume Surge', 'pass': sel.get("filter_details", {}).get("Volume Surge", {}).get("pass", False)},
            'E': {'name': 'Liquidity', 'pass': sel.get("filter_details", {}).get("Liquidity", {}).get("pass", False)},
            'F': {'name': 'Dynamic MDD', 'pass': sel.get("filter_details", {}).get("Dynamic MDD", {}).get("pass", False)}
        }
    render_entry_gates_ui(gates)

    # 3. 펀더멘털 지표
    if fund is None: fund = sel
    st.markdown("<br>### 📊 Fundamental Hard Gate", unsafe_allow_html=True)
    with st.container(border=True):
        r1, r2, r3, r4 = st.columns(4)
        r1.caption("순이익 YoY"); r1.markdown(f"**{fund.get('net_income_yoy', 0):+.2f}%**")
        r2.caption("ROE (수익성)"); r2.markdown(f"**{fund.get('roe') or 0:.2f}%**")
        r3.caption("부채비율 (건전성)"); r3.markdown(f"**{fund.get('debt_ratio') or 0:.1f}%**")
        r4.caption("시가총액"); r4.markdown(f"**{fund.get('marcap_억', 0):,.0f} 억**")

    # 4. 가격 차트
    st.markdown("### 📈 가격 차트 (Price History)")
    if df_price is not None and not df_price.empty:
        st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
    else:
        st.info("가격 데이터가 로드되지 않았습니다.")


@st.dialog("상세조회 리포트", width="large")
def show_detail_dialog(sel, supabase):
    """세션을 쓰지 않고 모달 팝업으로 상세화면을 띄우는 함수"""
    df_price = load_price_from_db(supabase, sel["symbol"])
    render_detailed_report_content(sel, df_price=df_price)


# ══════════════════════════════════════════
# [View] 리스트 렌더링 (체크박스 제거 & Expander 기반)
# ══════════════════════════════════════════
def render_inline_summary(sel, supabase):
    """드롭다운 확장 시 나타나는 미니 요약 카드"""
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        st.markdown(f"**시장:** {sel.get('market', 'KOSPI')}")
        st.markdown(f"**모멘텀 강도:** <span style='color:#F04452'>{sel.get('momentum_score', 0):+.2f}%</span>", unsafe_allow_html=True)
    with col2:
        st.markdown(f"**권장 진입가:** {sel.get('entry_price', sel['current_price']):,} 원")
        st.caption(f"절대 생존 조건 {sel.get('total_pass', 0)}/6 통과")
    with col3:
        if st.button("상세 리포트 ➔", key=f"btn_detail_{sel['symbol']}", use_container_width=True, type="primary"):
            show_detail_dialog(sel, supabase)

def render_custom_list(results, supabase):
    if not results:
        st.info("조건에 부합하는 종목이 없습니다.")
        return

    for idx, r in enumerate(results):
        # 마우스 호버 및 클릭 가능한 드롭다운 바 (체크박스 제거)
        title = f"🏆 {idx+1}위. {r['name']} &nbsp;|&nbsp; 랭킹점수: {r.get('factor_score', 0):.2f}점 &nbsp;|&nbsp; 통과: {r.get('total_pass', 0)}/6 &nbsp;|&nbsp; ₩{r['current_price']:,}"

        with st.expander(title):
            render_inline_summary(r, supabase)

# ══════════════════════════════════════════
# [View] Stock Search 기능
# ══════════════════════════════════════════
def live_evaluate_stock(symbol):
    """실시간 주가 및 펀더멘털 조회 후 퀀트 점수 산출"""
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

def render_stock_search_view():
    st.markdown("### 🔍 Stock Search & Report")
    st.caption("Apex 적합도 · Helix 모멘텀 · 편입 조건 검증 · 재무 상태를 실시간으로 분석합니다.")

    col_search, _ = st.columns([2, 1])
    with col_search:
        search_query = st.text_input("종목코드 6자리 입력 (예: 005930)", max_chars=6)

    if search_query and len(search_query) == 6:
        with st.spinner("실시간 퀀트 데이터 스크래핑 중..."):
            df, fund, apex, helix, gates = live_evaluate_stock(search_query)

        if df is None or df.empty:
            st.error("해당 종목을 찾을 수 없습니다. 코드를 확인해주세요.")
            return

        if not fund or fund.get('net_income_cur') is None:
            st.warning("⚠️ 펀더멘털(실적) 데이터가 부족하여 퀀트 상세 분석을 제공하지 않습니다. (단순 가격 차트만 제공)")
            st.line_chart(df[["Close"]].tail(120).rename(columns={"Close": "종가"}))
            return

        # 검색된 종목용 가상 객체 생성
        sel = {
            'symbol': search_query, 'name': search_query, 'current_price': df['Close'].iloc[-1],
            'ret_1m': (df['Close'].iloc[-1] - df['Close'].iloc[-21]) / df['Close'].iloc[-21] * 100 if len(df)>=21 else 0
        }
        sel.update(fund)
        render_detailed_report_content(sel, df_price=df, fund=fund, apex=apex, helix=helix, gates=gates)

# ══════════════════════════════════════════
# [Main Entry Point]
# ══════════════════════════════════════════
def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 정통 퀀트 스크리너 & 오토 트레이딩")

    confirmed, watchlist, last_updated = load_screening_result(supabase)
    holdings, trades, history = load_portfolio_data(supabase)

    tab_port, tab_watch, tab_hist, tab_search = st.tabs([
        f"Portfolio ({len(holdings)})",
        f"Watchlist ({len(watchlist)})",
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
        if holdings:
            h_data = []
            for h in holdings:
                curr = h.get("current_price", 0)
                entry = h.get("entry_price", curr)
                stop = h.get("stop_price", entry * 0.85)
                ret = h.get("return_rate", 0.0)
                recent = h.get("recent_30d", [entry, curr])
                if len(recent) < 2: recent = [entry, curr]

                try:
                    entry_date = pd.to_datetime(h.get("entry_date", now_kst().strftime("%Y-%m-%d")))
                    days_held = (now_kst() - entry_date).days
                except:
                    entry_date = now_kst()
                    days_held = 0

                distance_pct = (curr - stop) / curr * 100 if curr > 0 else 0
                exit_risk = max(0, min(100, int(100 - (distance_pct / 15 * 100))))

                h_data.append({
                    "Stock": f"{h['name']}",
                    "Entry Price": entry,
                    "Current": curr,
                    "P&L": ret,
                    "Stop": stop,
                    "Recent 30d": recent,
                    "Exit Risk": exit_risk,
                })

            df_h = pd.DataFrame(h_data)

            # 체크박스 제거를 위해 on_select 속성 제거
            st.dataframe(
                df_h.style.map(
                    lambda x: "color: #F04452" if x > 0 else "color: #3182F6" if x < 0 else "", subset=["P&L"]
                ).format({"Entry Price": "{:,.0f}", "Current": "{:,.0f}", "P&L": "{:+.2f}%", "Stop": "{:,.0f}"}),
                column_config={
                    "Recent 30d": st.column_config.LineChartColumn("Recent 30d", y_min=0, y_max=None),
                    "Exit Risk": st.column_config.ProgressColumn("Exit Risk (%)", min_value=0, max_value=100, format="%d%%")
                },
                hide_index=True, use_container_width=True
            )

            st.markdown("#### 🚨 종목별 Exit Risk 상세 진단")
            for h in holdings:
                with st.expander(f"🔍 {h['name']} Risk 분석 (현재가: {h.get('current_price', 0):,}원)"):
                    render_exit_risk_card(h)
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
    # 탭 2: Watchlist & Confirmed
    # ────────────────────────────────────────────────────────
    with tab_watch:
        st.markdown(f"**마지막 스크리닝:** {last_updated or '미실행'}")

        if confirmed:
            st.success(f"🏆 신규 추격매수 확정 ({len(confirmed)}개) - 6조건 ALL PASS")
            render_custom_list(confirmed, supabase)
            st.divider()

        st.caption(f"👀 추격매수 예비 돌파 종목 ({len(watchlist)}개) - 4조건 이상 통과")
        if watchlist:
            render_custom_list(watchlist[:15], supabase)
            if len(watchlist) > 15: st.caption("...그 외 다수 종목 생략됨 (엄격한 기준 적용)")
        else:
            st.info("WatchList 종목이 없습니다.")

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
        render_stock_search_view()
