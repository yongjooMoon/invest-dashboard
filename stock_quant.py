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
    ALL_FILTER_NAMES, HARD_GATES, SOFT_GATES,
    now_kst, PREFILTER_MARCAP_억, PREFILTER_TVOL_억,
)


def _badge(passed: bool) -> str:
    return "🟢 통과" if passed else "🔴 탈락"


def _render_filter_badges(fr: dict):
    st.markdown("**🔒 하드게이트 (필수 4개)**")
    hcols = st.columns(4)
    for i, (fname, _) in enumerate(HARD_GATES):
        d = fr.get(fname, {})
        with hcols[i]:
            with st.container(border=True):
                st.markdown(f"**{fname}**")
                st.markdown(_badge(d.get("pass", False)))
                st.caption(d.get("reason", "-"))

    st.markdown("**📊 소프트게이트 (점수화 7개)**")
    scols = st.columns(4)
    for i, (fname, _) in enumerate(SOFT_GATES):
        d = fr.get(fname, {})
        with scols[i % 4]:
            with st.container(border=True):
                st.markdown(f"**{fname}**")
                st.markdown(_badge(d.get("pass", False)))
                st.caption(d.get("reason", "-"))


def _render_detail(sel: dict, supabase):
    fs = sel.get("factor_score", 0)
    hp = sel.get("hard_pass", 0)
    sp = sel.get("soft_pass", 0)
    fs_color = "#00B464" if fs >= 65 else "#E6A23C" if fs >= 40 else "#F04452"

    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.markdown(
            f"**팩터 점수:** "
            f"<span style='font-size:1.4rem;font-weight:bold;color:{fs_color}'>"
            f"{fs}점</span>",
            unsafe_allow_html=True,
        )
        st.progress(int(min(fs, 100)))
    with col_b:
        badge = "🏆 확정" if hp == 4 else f"👀 하드{hp}/4"
        st.markdown(f"### {badge}")
        st.caption(f"소프트 {sp}/7 통과")

    tab1, tab2, tab3 = st.tabs(["✅ 필터 상세", "📊 펀더멘털 & 수급", "📈 가격 차트"])

    with tab1:
        _render_filter_badges(sel.get("filter_details", {}))

    with tab2:
        st.markdown("**실적 (핵심 지표)**")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("순이익 YoY",
                  f"{sel.get('net_income_yoy') or 0:+.1f}%"
                  if sel.get("net_income_yoy") is not None else "-",
                  help="이미지 전략 핵심 지표")
        m2.metric("영업이익 YoY",
                  f"{sel.get('op_profit_yoy') or 0:+.1f}%"
                  if sel.get("op_profit_yoy") is not None else "-")
        m3.metric("매출 YoY",
                  f"{sel.get('revenue_yoy') or 0:+.1f}%"
                  if sel.get("revenue_yoy") is not None else "-")
        m4.metric("ROE",
                  f"{sel.get('roe') or 0:.1f}%"
                  if sel.get("roe") is not None else "-")

        st.markdown("**수급 (20일 누적)**")
        s1, s2, s3 = st.columns(3)
        f_net = sel.get("foreign_net_buy") or 0
        i_net = sel.get("institute_net_buy") or 0
        s1.metric("외인 순매수", f"{f_net:+,.0f}주" if f_net != 0 else "미수집")
        s2.metric("기관 순매수", f"{i_net:+,.0f}주" if i_net != 0 else "미수집")
        s3.metric("합산 수급",   f"{f_net+i_net:+,.0f}주" if (f_net or i_net) else "미수집")

        st.markdown("**재무 건전성**")
        r1, r2 = st.columns(2)
        r1.metric("부채비율", f"{sel.get('debt_ratio') or 0:.0f}%"
                  if sel.get("debt_ratio") is not None else "-")
        r2.metric("시가총액", f"{sel.get('marcap_억', 0):,.0f}억")
        st.caption("출처: DART OpenAPI → 네이버금융 크롤링 (보완) | 수급: 한투 API 20일 누적")

    with tab3:
        df_price = load_price_from_db(supabase, sel["symbol"])
        if not df_price.empty:
            st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
            s1, s2, s3, s4 = st.columns(4)
            c1y = df_price["Close"].tail(252)
            s1.metric("52주 최고",   f"₩{int(c1y.max()):,}")
            s2.metric("52주 최저",   f"₩{int(c1y.min()):,}")
            s3.metric("현재가",      f"₩{int(df_price['Close'].iloc[-1]):,}")
            chg = (df_price["Close"].iloc[-1] - df_price["Close"].iloc[-252]) \
                  / df_price["Close"].iloc[-252] * 100 if len(df_price) >= 252 else 0
            s4.metric("52주 수익률", f"{chg:+.1f}%")
        else:
            st.info("가격 데이터 없음")


def _build_table(results: list, show_hard: bool = False) -> pd.DataFrame:
    rows = []
    for idx, r in enumerate(results):
        row = {
            "순위":         idx + 1,
            "종목명":       r["name"],
            "코드":         r["symbol"],
            "시장":         r.get("market", "-"),
            "시가총액(억)": f"{r.get('marcap_억',0):,.0f}",
            "팩터점수":     f"{r.get('factor_score',0):.1f}",
            "현재가":       f"₩{r['current_price']:,}",
            "1M수익률":     f"{r['ret_1m']:+.1f}%",
            "12-1모멘텀":   f"{r['momentum_score']:+.1f}%",
            "순이익YoY":    f"{r.get('net_income_yoy') or 0:+.1f}%"
                            if r.get("net_income_yoy") is not None else "-",
            "영업이익YoY":  f"{r.get('op_profit_yoy') or 0:+.1f}%"
                            if r.get("op_profit_yoy") is not None else "-",
            "ROE":          f"{r.get('roe') or 0:.1f}%"
                            if r.get("roe") is not None else "-",
            "외인순매수":   f"{r.get('foreign_net_buy') or 0:+,.0f}"
                            if r.get("foreign_net_buy") is not None else "-",
            "기관순매수":   f"{r.get('institute_net_buy') or 0:+,.0f}"
                            if r.get("institute_net_buy") is not None else "-",
            "변동성":       f"{r.get('annual_vol') or 0:.1f}%",
            "MDD":          f"{r.get('mdd') or 0:.1f}%",
        }
        if show_hard:
            row["하드/소프트"] = f"{r.get('hard_pass',0)}/4 · {r.get('soft_pass',0)}/7"
        rows.append(row)
    return pd.DataFrame(rows)


# ──────────────────────────────────────────
# [신규 추가] 대시보드 렌더링 함수
# ──────────────────────────────────────────
def render_dashboard(supabase):
    st.markdown("### 📈 Sanbon")

    # 1. 지표 초기값 셋팅
    cum_return = 0.0
    day_return = 0.0
    kospi_cum = 0.0
    kospi_day = 0.0
    alpha = 0.0
    has_history = False

    # 2. 기본적으로 조회할 최근 1개월(30일) 기간 설정
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)

    # 3. DB에서 포트폴리오 히스토리 데이터 로드
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
    except Exception as e:
        st.warning(f"히스토리 데이터를 불러오는 중 오류가 발생했습니다: {e}")

    # 4. KOSPI 데이터 무조건 로드
    df_kospi = fdr.DataReader('KS11', start_date.strftime('%Y-%m-%d'))

    if not df_kospi.empty:
        df_kospi['kospi_cum_return'] = df_kospi['Close'].pct_change().fillna(0).cumsum() * 100
        kospi_cum = df_kospi['kospi_cum_return'].iloc[-1]
        kospi_day = df_kospi['Close'].pct_change().iloc[-1] * 100

    # 5. 포트폴리오 지표 계산 및 차트 데이터 결합
    if has_history and not df_hist.empty:
        df_hist['port_cum_return'] = df_hist['daily_return'].cumsum()

        cum_return = df_hist['port_cum_return'].iloc[-1]
        day_return = df_hist['daily_return'].iloc[-1]

        chart_df = pd.DataFrame({
            'Portfolio': df_hist['port_cum_return'],
            'KOSPI': df_kospi['kospi_cum_return']
        }).fillna(method='ffill').fillna(0)
    else:
        chart_df = pd.DataFrame({
            'Portfolio': 0.0,
            'KOSPI': df_kospi['kospi_cum_return'] if not df_kospi.empty else 0.0
        }, index=df_kospi.index)

    alpha = cum_return - kospi_cum

    # 6. 상단 핵심 지표 영역 UI (기존 다크/레드 톤 유지)
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        with st.container(border=True):
            st.markdown("##### CUMULATIVE RETURN")
            color = "#F04452" if cum_return >= 0 else "#3182F6"
            st.markdown(f"<h1 style='color:{color}; margin: 0;'>{cum_return:+.2f}%</h1>", unsafe_allow_html=True)
            st.caption(f"Day <span style='color:{color};'>{day_return:+.2f}%</span>", unsafe_allow_html=True)

    with col2:
        with st.container(border=True):
            st.markdown("##### KOSPI RETURN")
            k_color = "#E6A23C" if kospi_cum >= 0 else "#3182F6"
            st.markdown(f"<h2 style='color:{k_color}; margin: 0;'>{kospi_cum:+.2f}%</h2>", unsafe_allow_html=True)
            k_day_color = "#E6A23C" if kospi_day >= 0 else "#3182F6"
            st.caption(f"Day <span style='color:{k_day_color};'>{kospi_day:+.2f}%</span>", unsafe_allow_html=True)

    with col3:
        with st.container(border=True):
            st.markdown("##### ALPHA")
            a_color = "#00B464" if alpha >= 0 else "#F04452"
            st.markdown(f"<h2 style='color:{a_color}; margin: 0;'>{alpha:+.2f}%</h2>", unsafe_allow_html=True)
            st.caption("&nbsp;", unsafe_allow_html=True)

    # ─────────────────────────────────────────────────────────
    # 7. 완벽하게 커스텀된 다크 테마 차트 (이미지 100% 반영)
    # ─────────────────────────────────────────────────────────
    st.markdown("**Cumulative Return** vs KOSPI")

    if not chart_df.empty:
        # 매 일자별 알파 계산 (포트폴리오 - 코스피)
        chart_df['Alpha'] = chart_df['Portfolio'] - chart_df['KOSPI']
        # 알파 값에 따른 색상 (양수면 초록, 음수면 빨강)
        chart_df['Alpha_Color'] = chart_df['Alpha'].apply(lambda x: '#00B464' if x >= 0 else '#F04452')

        fig = go.Figure()

        # [메인] Portfolio (Sanbon) 라인 - 그라데이션 영역 칠하기 포함
        custom_data = np.column_stack((chart_df['KOSPI'], chart_df['Alpha'], chart_df['Alpha_Color']))

        fig.add_trace(go.Scatter(
            x=chart_df.index,
            y=chart_df['Portfolio'],
            mode='lines',
            name='Sanbon',
            line=dict(color='#F04452', width=2.5),
            fill='tozeroy',  # 선 아래 영역 칠하기 (이미지 느낌)
            fillcolor='rgba(240, 68, 82, 0.05)',
            customdata=custom_data,
            # <extra></extra>가 옆에 붙는 지저분한 '선/이름' 꼬리표를 완전히 삭제합니다.
            hovertemplate=(
                "<span style='color:#AEC1D4; font-size:12px;'>%{x|%Y.%m.%d}</span><br><br>"
                "<span style='color:#3182F6;'>●</span> Sanbon &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>%{y:.2f}%</b><br>"
                "<span style='color:#8B95A1;'>●</span> KOSPI &nbsp;&nbsp;&nbsp;<b>%{customdata[0]:.2f}%</b><br>"
                "─────────────────<br>"
                "<span style='color:#8B95A1;'>α</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b style='color:%{customdata[2]};'>%{customdata[1]:+.2f}%</b>"
                "<extra></extra>"
            )
        ))

        # [서브] KOSPI 라인 (점선 처리 & 개별 툴팁 스킵)
        fig.add_trace(go.Scatter(
            x=chart_df.index,
            y=chart_df['KOSPI'],
            mode='lines',
            name='KOSPI',
            line=dict(color='#8B95A1', width=1.5, dash='dot'),
            hoverinfo='skip'  # 코스피 선에 마우스를 올려도 따로 툴팁이 뜨지 않게 방지
        ))

        fig.update_layout(
            hovermode='x',  # 세로축 기준 매칭
            xaxis=dict(
                showgrid=False,
                zeroline=False,
                showticklabels=True,
                showspikes=True,  # 마우스 올렸을 때 수직 점선(Spike line) 표시
                spikemode='across',
                spikedash='dot',
                spikecolor='#555555',
                spikethickness=1
            ),
            yaxis=dict(
                showgrid=True,
                gridcolor='rgba(255, 255, 255, 0.05)',  # 아주 옅은 가로선
                zeroline=True,
                zerolinecolor='rgba(255, 255, 255, 0.1)',
                ticksuffix="%"
            ),
            # 툴팁 배경을 다크 톤으로 변경
            hoverlabel=dict(
                bgcolor="#191F28",
                font_size=14,
                font_family="Pretendard, sans-serif",
                font_color="white",
                bordercolor="#333333",
            ),
            margin=dict(l=0, r=0, t=10, b=0),
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            showlegend=False  # 깔끔함을 위해 범례 숨김 (이미 툴팁에 다 나옴)
        )

        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # 8. 안내 메시지
    if not has_history:
        st.info("💡 아직 누적된 포트폴리오 히스토리가 없습니다. (현재 KOSPI 최근 1개월 추이만 표시 중)")

def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 퀀트 추격매수 스크리너")
    st.caption("하드게이트 4개 + 소프트게이트 7개 · 팩터점수 랭킹 · 매일 14:30 KST 자동 갱신")

    render_dashboard(supabase)

    with st.expander("📚 전략 설계", expanded=False):
        st.markdown(f"""
**사전 필터링** — 시가총액 ≥ {PREFILTER_MARCAP_억}억 + 거래대금 ≥ {PREFILTER_TVOL_억}억 + 보통주 → **~400개** 대상

**🔒 하드게이트 (4개 ALL-PASS 필수)**

| # | 게이트 | 기준 | 근거 |
|---|--------|------|------|
| H1 | **순이익 YoY** | > 0% | 이미지 전략 핵심 · 시장이 미래이익을 선반영 |
| H2 | **12-1 모멘텀** | > 0% | Jegadeesh & Titman (1993) |
| H3 | **유동성** | 20일 거래대금 ≥ 30억 | Amihud (2002) |
| H4 | **MDD** | 1년 ≥ -30% | 급락종목 추격 차단 |

**📊 소프트게이트 (7개 점수화)**

| # | 게이트 | 기준 | 가중치 |
|---|--------|------|--------|
| S1 | 영업이익 YoY | > 0% | 15점 |
| S2 | ROE | ≥ 5% | 8점 |
| S3 | 매출 YoY | > -10% | 완화기준 |
| S4 | 저변동성 | ≤ 70% | 5점 |
| S5 | 거래량모멘텀 | 10일/60일 ≥ 1.2x | 10점 |
| S6 | 추세강도 | 기울기>0, R²≥0.4 | 10점 |
| S7 | **기관+외인 수급** | 20일 순매수 > 0 | 7점 |

**팩터점수:** 순이익모멘텀 25점 · 12-1모멘텀 20점 · 영업이익YoY 15점 · 추세강도 10점 · 거래량 10점 · ROE 8점 · 수급 7점 · 저변동성 5점

**WatchList:** 하드게이트 3개 이상 통과
        """)

    confirmed, watchlist, last_updated = load_screening_result(supabase)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("마지막 스크리닝", last_updated or "미실행")
    c2.metric("확정 선별",  f"{len(confirmed)}개 (하드4 ALL-PASS)")
    c3.metric("관심종목",   f"{len(watchlist)}개 (하드3 통과)")
    now = now_kst()
    c4.metric("다음 갱신", ("오늘" if now.hour < 14 else "내일") + " 14:30 KST")

    st.divider()

    if not confirmed and not watchlist:
        st.info("스크리닝 결과 없음 — `python quant_cron.py` 실행하세요.")
        return

    tab_conf, tab_watch = st.tabs([
        f"🏆 확정 선별  ({len(confirmed)}개)",
        f"👀 WatchList  ({len(watchlist)}개)",
    ])

    with tab_conf:
        if not confirmed:
            st.info("현재 하드게이트 ALL-PASS 종목이 없습니다. WatchList를 확인하세요.")
        else:
            st.subheader("하드게이트 4개 ALL-PASS · 팩터점수 Top 10")
            df_c = _build_table(confirmed)
            sel_c = st.dataframe(df_c, use_container_width=True,
                                 on_select="rerun", selection_mode="single-row",
                                 hide_index=True)
            sel_rows = []
            if sel_c and hasattr(sel_c, "selection") and sel_c.selection.rows:
                sel_rows = sel_c.selection.rows
            if sel_rows:
                st.divider()
                sel = confirmed[sel_rows[0]]
                st.subheader(f"🔬 [{sel['name']} ({sel['symbol']})]")
                _render_detail(sel, supabase)

        if confirmed:
            with st.expander("📊 확정 종목 지표 분포"):
                stat_df = pd.DataFrame([{
                    "종목":       r["name"],
                    "팩터점수":   r.get("factor_score", 0),
                    "모멘텀":     r["momentum_score"],
                    "순이익YoY":  r.get("net_income_yoy") or 0,
                    "ROE":        r.get("roe") or 0,
                    "시총(억)":   r.get("marcap_억", 0),
                } for r in confirmed])
                st.dataframe(stat_df, use_container_width=True, hide_index=True)
                st.bar_chart(stat_df.set_index("종목")["팩터점수"])

    with tab_watch:
        st.caption("하드게이트 3개 통과 — 나머지 1개 조건 충족 시 다음 배치 확정 편입 가능")
        if not watchlist:
            st.info("WatchList 종목이 없습니다.")
        else:
            df_w = _build_table(watchlist, show_hard=True)
            sel_w = st.dataframe(df_w, use_container_width=True,
                                 on_select="rerun", selection_mode="single-row",
                                 hide_index=True)
            sel_rows_w = []
            if sel_w and hasattr(sel_w, "selection") and sel_w.selection.rows:
                sel_rows_w = sel_w.selection.rows
            if sel_rows_w:
                st.divider()
                sw = watchlist[sel_rows_w[0]]
                st.subheader(f"🔬 [{sw['name']} ({sw['symbol']})]")
                _render_detail(sw, supabase)

            with st.expander("📋 하드게이트별 탈락 현황"):
                fail_cnt: dict = {}
                for r in watchlist:
                    fd = r.get("filter_details", {})
                    from quant_core import HARD_GATES
                    for fname, _ in HARD_GATES:
                        if not fd.get(fname, {}).get("pass", True):
                            fail_cnt[fname] = fail_cnt.get(fname, 0) + 1
                if fail_cnt:
                    fail_df = pd.DataFrame(
                        sorted(fail_cnt.items(), key=lambda x: -x[1]),
                        columns=["하드게이트", "탈락 종목 수"]
                    )
                    st.dataframe(fail_df, use_container_width=True, hide_index=True)
                    st.caption("이 게이트 조건이 충족되면 다음 배치에서 확정 편입")
