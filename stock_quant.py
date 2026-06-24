"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import numpy as np
import plotly.graph_objects as go
from quant_core import (
    load_price_from_db, load_screening_result,
    HARD_GATES, SOFT_GATES,
    now_kst
)

def _badge(passed: bool) -> str:
    return "🟢 생존" if passed else "🔴 탈락"

def _render_filter_badges(fr: dict):
    st.markdown("**🛡️ 생존 필터 (Survival Filters)** - 6개 중 최소 5개 이상 통과시 확정")
    hcols = st.columns(3)

    gates_ko = {
        "Growth Composite": "성장성 통합 (Growth)",
        "Dynamic MDD": "동적 방어선 (ATR MDD)",
        "Liquidity": "유동성 (20일 평균)",
        "Momentum": "단/장기 모멘텀",
        "Volatility": "저변동성",
        "Trend Strength": "추세 강도"
    }

    for i, (fname, _) in enumerate(HARD_GATES):
        d = fr.get(fname, {})
        display_name = gates_ko.get(fname, fname)
        with hcols[i % 3]:
            with st.container(border=True):
                st.markdown(f"**{display_name}**")
                st.markdown(_badge(d.get("pass", False)))
                st.caption(d.get("reason", "-"))

    st.markdown("**📊 랭킹 스코어 (백분위 점수)** - 생존 종목 대상 상대평가")
    scols = st.columns(5)
    for i, (fname, _) in enumerate(SOFT_GATES):
        d = fr.get(fname, {})
        with scols[i % 5]:
            with st.container(border=True):
                st.markdown(f"**{fname.replace(' Rank', '')}**")
                st.caption(d.get("reason", "-"))

def _render_detail(sel: dict, supabase):
    fs = sel.get("factor_score", 0)
    tp = sel.get("total_pass", 0)
    fs_color = "#00B464" if fs >= 65 else "#E6A23C" if fs >= 40 else "#F04452"

    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.markdown(f"**상위 랭킹 점수:** <span style='font-size:1.4rem;font-weight:bold;color:{fs_color}'>{fs}점</span> (100점 만점)", unsafe_allow_html=True)
        st.progress(int(min(fs, 100)))
    with col_b:
        badge = "🏆 Confirmed" if tp >= 5 else f"👀 WatchList"
        st.markdown(f"### {badge}")
        st.caption(f"생존 조건 {tp}/6 통과")

    tab1, tab2, tab3 = st.tabs(["✅ 퀀트 평가 상세", "📊 펀더멘털 & 수급", "📈 가격 차트"])

    with tab1:
        st.info(f"💡 **권장 진입가 (Entry Cost)**: {sel.get('entry_price', sel['current_price']):,}원 부근 (단기 5일 이평선 지지 기반)")
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
            s1, s2, s3 = st.columns(3)
            s1.metric("현재가", f"₩{int(df_price['Close'].iloc[-1]):,}")
            s2.metric("매수 제안가", f"₩{sel.get('entry_price', 0):,}")
            s3.metric("최근 60일 최고가", f"₩{int(df_price['Close'].tail(60).max()):,}")
        else:
            st.info("가격 데이터 없음")

def _build_table(results: list) -> pd.DataFrame:
    rows = []
    for idx, r in enumerate(results):
        rows.append({
            "순위": idx + 1,
            "종목명": r["name"],
            "시장": r.get("market", "-"),
            "생존필터": f"{r.get('total_pass',0)}/6",
            "랭킹점수": f"{r.get('factor_score',0):.1f}",
            "현재가": f"₩{r['current_price']:,}",
            "보수적진입가(MA5)": f"₩{r.get('entry_price', r['current_price']):,}",
            "1M수익률": f"{r['ret_1m']:+.1f}%",
            "모멘텀": f"{r.get('momentum_score', 0):+.1f}%",
            "순이익YoY": f"{r.get('net_income_yoy') or 0:+.1f}%" if r.get("net_income_yoy") is not None else "-",
        })
    return pd.DataFrame(rows)

def render_dashboard(supabase):
    st.markdown("### 📈 KOSPI 대비 포트폴리오 성과 (Alpha)")

    has_history = False
    cum_return, day_return, kospi_cum, kospi_day, alpha = 0.0, 0.0, 0.0, 0.0, 0.0
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    df_hist = pd.DataFrame()

    try:
        res = supabase.table("portfolio_history").select("*").order("date").execute()
        if res.data:
            has_history = True
            df_hist = pd.DataFrame(res.data)
            df_hist['date'] = pd.to_datetime(df_hist['date'])
            df_hist = df_hist.set_index('date')
            if df_hist.index.min() < start_date:
                start_date = df_hist.index.min()
    except:
        pass

    df_kospi = fdr.DataReader('KS11', start_date.strftime('%Y-%m-%d'))
    if not df_kospi.empty:
        df_kospi['kospi_cum_return'] = df_kospi['Close'].pct_change().fillna(0).cumsum() * 100
        kospi_cum = df_kospi['kospi_cum_return'].iloc[-1]
        kospi_day = df_kospi['Close'].pct_change().iloc[-1] * 100

    if has_history and not df_hist.empty:
        df_hist['port_cum_return'] = df_hist['daily_return'].cumsum()
        cum_return = df_hist['port_cum_return'].iloc[-1]
        day_return = df_hist['daily_return'].iloc[-1]
        chart_df = pd.DataFrame({'Portfolio': df_hist['port_cum_return'], 'KOSPI': df_kospi['kospi_cum_return']}).fillna(method='ffill').fillna(0)
    else:
        chart_df = pd.DataFrame({'Portfolio': 0.0, 'KOSPI': df_kospi['kospi_cum_return'] if not df_kospi.empty else 0.0}, index=df_kospi.index)

    alpha = cum_return - kospi_cum

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        with st.container(border=True):
            st.markdown("##### CUMULATIVE RETURN")
            c = "#F04452" if cum_return >= 0 else "#3182F6"
            st.markdown(f"<h1 style='color:{c}; margin: 0;'>{cum_return:+.2f}%</h1>", unsafe_allow_html=True)
            st.caption(f"Day <span style='color:{c};'>{day_return:+.2f}%</span>", unsafe_allow_html=True)
    with col2:
        with st.container(border=True):
            st.markdown("##### KOSPI RETURN")
            c = "#E6A23C" if kospi_cum >= 0 else "#3182F6"
            st.markdown(f"<h2 style='color:{c}; margin: 0;'>{kospi_cum:+.2f}%</h2>", unsafe_allow_html=True)
    with col3:
        with st.container(border=True):
            st.markdown("##### ALPHA")
            c = "#00B464" if alpha >= 0 else "#F04452"
            st.markdown(f"<h2 style='color:{c}; margin: 0;'>{alpha:+.2f}%</h2>", unsafe_allow_html=True)

    if not chart_df.empty:
        chart_df['Alpha'] = chart_df['Portfolio'] - chart_df['KOSPI']
        chart_df['Alpha_Color'] = chart_df['Alpha'].apply(lambda x: '#00B464' if x >= 0 else '#F04452')
        fig = go.Figure()
        custom_data = np.column_stack((chart_df['KOSPI'], chart_df['Alpha'], chart_df['Alpha_Color']))

        fig.add_trace(go.Scatter(
            x=chart_df.index, y=chart_df['Portfolio'], mode='lines', name='Portfolio',
            line=dict(color='#F04452', width=2.5), fill='tozeroy', fillcolor='rgba(240, 68, 82, 0.05)', customdata=custom_data,
            hovertemplate="<span style='color:#AEC1D4; font-size:12px;'>%{x|%Y.%m.%d}</span><br><br><span style='color:#3182F6;'>●</span> Portfolio &nbsp;&nbsp;<b>%{y:.2f}%</b><br><span style='color:#8B95A1;'>●</span> KOSPI &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>%{customdata[0]:.2f}%</b><br>─────────────────<br><span style='color:#8B95A1;'>α</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b style='color:%{customdata[2]};'>%{customdata[1]:+.2f}%</b><extra></extra>"
        ))
        fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['KOSPI'], mode='lines', line=dict(color='#8B95A1', width=1.5, dash='dot'), hoverinfo='skip'))

        fig.update_layout(
            hovermode='x', xaxis=dict(showgrid=False, zeroline=False),
            yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)', ticksuffix="%"),
            hoverlabel=dict(bgcolor="#191F28", font_color="white"),
            margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False
        )
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

def render_portfolio_tabs(supabase):
    st.markdown("### 💼 퀀트 자동 매매 포트폴리오")
    tab1, tab2 = st.tabs(["현재 보유 종목 (Holdings)", "매매 이력 (Trade History)"])

    with tab1:
        try:
            holdings = supabase.table("portfolio_holdings").select("*").execute().data or []
            if holdings:
                h_df = pd.DataFrame(holdings)
                h_df = h_df[["name", "symbol", "entry_price", "current_price", "return_rate", "entry_date"]]
                h_df.columns = ["종목명", "코드", "진입가(매수가)", "현재가", "수익률(%)", "편입일"]
                st.dataframe(h_df.style.map(lambda x: "color: #F04452" if x > 0 else "color: #3182F6" if x < 0 else "", subset=["수익률(%)"]), hide_index=True)
            else:
                st.info("현재 보유 중인 종목이 없습니다. (조건 부합 종목 대기 중)")
        except:
            st.warning("포트폴리오 DB(portfolio_holdings) 연동 대기중...")

    with tab2:
        try:
            trades = supabase.table("portfolio_trades").select("*").order("trade_date", desc=True).limit(50).execute().data or []
            if trades:
                t_df = pd.DataFrame(trades)
                t_df = t_df[["trade_date", "type", "name", "trade_price", "return_rate", "reason"]]
                t_df.columns = ["일자", "구분", "종목명", "체결가", "실현수익률(%)", "사유"]

                def color_type(val):
                    return 'color: #F04452' if val == 'BUY' else 'color: #3182F6'

                st.dataframe(t_df.style.map(color_type, subset=['구분']), hide_index=True)
            else:
                st.info("최근 매매 이력이 없습니다.")
        except:
            st.warning("매매 이력 DB(portfolio_trades) 연동 대기중...")

def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 정통 퀀트 스크리너 & 오토 트레이딩")
    st.caption("안정성(Survival) 방어선 통과 후 상대 우위(Ranking) 평가 · 코스피 대비 Alpha 추적")

    # 1. 대시보드 (KOSPI 대비 성과 및 알파 계산)
    render_dashboard(supabase)
    st.divider()

    # 2. 포트폴리오 관리 탭 (보유 종목 및 자동 매매 이탈 내역)
    render_portfolio_tabs(supabase)
    st.divider()

    # 3. 퀀트 스크리닝 결과
    st.markdown("### 🔎 일간 퀀트 스크리닝 결과 (매일 14:30 갱신)")
    with st.expander("📚 퀀트 아키텍처 설계 (Filter & Score 분리)", expanded=False):
        st.markdown("""
- **[Filter = Survival (절대 방어선)]**: 종목의 생존력을 담보하는 6가지 강력한 허들
  - `Growth Composite`: 매출/영업이익/순이익을 가중 평균한 통합 성장성
  - `Volatility Adaptive MDD`: 과거 고정값이 아닌 ATR 기반의 동적 하락 한계선 방어
  - 그 외 유동성, 모멘텀, 저변동성, 추세 강도 조건 적용
- **[Score = Ranking (상대 평가)]**: 생존 필터를 통과한 종목 대상 백분위(Percentile) 최종 랭킹
- **[이탈 및 진입]**:
  - 진입: 5일 이평선(MA5) 눌림목을 **보수적 진입가**로 설정하여 추격매수 리스크 완화.
  - 이탈: 추세 이탈(MA20 하향) 및 퀀트 조건(Confirmed) 동시 탈락 시 자동 매도.
        """)

    confirmed, watchlist, last_updated = load_screening_result(supabase)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("마지막 스크리닝", last_updated or "미실행")
    c2.metric("🏆 확정 편입", f"{len(confirmed)}개 (생존 5+ 통과)")
    c3.metric("👀 관심 종목", f"{len(watchlist)}개 (생존 3~4 통과)")

    now = now_kst()
    c4.metric("다음 자동매매 갱신", ("오늘" if now.hour < 14 else "내일") + " 14:30")

    if not confirmed and not watchlist:
        st.info("조건을 통과한 종목이 전혀 없습니다. `quant_cron.py`를 실행하여 캐시 데이터를 갱신해주세요.")
        return

    # 백엔드에서 억지로 10개를 줬더라도 프론트에서 필터링 가능하도록 처리.
    # 단, 현재는 5개 이상 통과 종목만 Confirmed에 들어오도록 코어 로직이 수정되었으므로 그대로 노출하되 점수를 명시.
    tab_conf, tab_watch = st.tabs([f"🏆 확정 선별 ({len(confirmed)}개)", f"👀 WatchList ({len(watchlist)}개)"])

    with tab_conf:
        if not confirmed:
            st.warning("현재 생존 조건을 5개 이상 통과한 확정 종목이 없습니다. 장세가 험난할 수 있습니다.")
        else:
            st.caption("💡 랭킹 점수(백분위 통합)순으로 정렬되었습니다. 무지성 매수가 아닌 '보수적 진입가(단기 지지선)'를 참고하세요.")
            df_c = _build_table(confirmed)
            sel_c = st.dataframe(df_c, use_container_width=True, on_select="rerun", selection_mode="single-row", hide_index=True)
            if sel_c and hasattr(sel_c, "selection") and sel_c.selection.rows:
                st.divider()
                _render_detail(confirmed[sel_c.selection.rows[0]], supabase)

    with tab_watch:
        if not watchlist:
            st.info("WatchList 종목이 없습니다.")
        else:
            st.caption("조금만 더 조건이 충족되면 확정 편입될 유망 종목 (총 3~4개 통과)")
            df_w = _build_table(watchlist)
            sel_w = st.dataframe(df_w, use_container_width=True, on_select="rerun", selection_mode="single-row", hide_index=True)
            if sel_w and hasattr(sel_w, "selection") and sel_w.selection.rows:
                st.divider()
                _render_detail(watchlist[sel_w.selection.rows[0]], supabase)
