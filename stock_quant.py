"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
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

def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 퀀트 추격매수 스크리너")
    st.caption("하드게이트 4개 + 소프트게이트 7개 · 팩터점수 랭킹 · 매일 14:30 KST 자동 갱신")

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
