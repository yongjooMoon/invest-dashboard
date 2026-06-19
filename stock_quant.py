"""
quant_screener_ui.py — Streamlit UI (결과만 표시)
quant_cron.py 가 매일 14:30 DB 갱신 → 여기서 읽기만 함
"""
import streamlit as st
import pandas as pd
from quant_core import (
    load_price_from_db, load_screening_result,
    ALL_FILTER_NAMES, TECH_FILTERS, FUNDA_FILTERS,
    now_kst,
)


# ──────────────────────────────────────────
# 공통 컴포넌트
# ──────────────────────────────────────────
def _render_filter_badges(filter_details: dict):
    cols = st.columns(4)
    for i, fname in enumerate(ALL_FILTER_NAMES):
        detail = filter_details.get(fname, {})
        passed = detail.get("pass", False)
        reason = detail.get("reason", "-")
        with cols[i % 4]:
            with st.container(border=True):
                st.markdown(f"**{fname}**")
                st.markdown("🟢 통과" if passed else "🔴 탈락")
                st.caption(reason)


def _render_detail(sel: dict, supabase):
    fs = sel.get("factor_score", 0)
    pc = sel.get("pass_count", 0)
    fs_color = "#00B464" if fs >= 70 else "#E6A23C" if fs >= 50 else "#F04452"

    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.markdown(
            f"**종합 팩터 점수:** "
            f"<span style='font-size:1.3rem;font-weight:bold;color:{fs_color}'>"
            f"{fs}점 / 100점</span>",
            unsafe_allow_html=True,
        )
        st.progress(int(min(fs, 100)))
    with col_b:
        badge = "🏆 ALL-PASS" if pc == 8 else f"👀 {pc}/8 통과"
        st.markdown(f"### {badge}")

    tab1, tab2, tab3 = st.tabs(["✅ 필터 상세", "📊 펀더멘털", "📈 가격 차트"])

    with tab1:
        _render_filter_badges(sel.get("filter_details", {}))

    with tab2:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("영업이익 YoY",
                  f"{sel.get('op_profit_yoy') or 0:+.1f}%"
                  if sel.get("op_profit_yoy") is not None else "-")
        m2.metric("EPS YoY",
                  f"{sel.get('eps_yoy') or 0:+.1f}%"
                  if sel.get("eps_yoy") is not None else "-")
        m3.metric("ROE",
                  f"{sel.get('roe') or 0:.1f}%"
                  if sel.get("roe") is not None else "-")
        m4.metric("부채비율",
                  f"{sel.get('debt_ratio') or 0:.0f}%"
                  if sel.get("debt_ratio") is not None else "-")
        st.caption("출처: DART OpenAPI → 네이버금융 크롤링 (보완)")

    with tab3:
        df_price = load_price_from_db(supabase, sel["symbol"])
        if not df_price.empty:
            st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
            s1, s2, s3, s4 = st.columns(4)
            close_1y = df_price["Close"].tail(252)
            s1.metric("52주 최고", f"₩{int(close_1y.max()):,}")
            s2.metric("52주 최저", f"₩{int(close_1y.min()):,}")
            s3.metric("현재가",    f"₩{int(df_price['Close'].iloc[-1]):,}")
            chg = (df_price["Close"].iloc[-1] - df_price["Close"].iloc[-252]) \
                  / df_price["Close"].iloc[-252] * 100 if len(df_price) >= 252 else 0
            s4.metric("52주 수익률", f"{chg:+.1f}%")
        else:
            st.info("가격 데이터 없음 (DB 미적재)")


def _build_table(results: list, show_pass_count: bool = False) -> pd.DataFrame:
    rows = []
    for idx, r in enumerate(results):
        row = {
            "순위":         idx + 1,
            "종목명":       r["name"],
            "코드":         r["symbol"],
            "시장":         r.get("market", "-"),
            "팩터점수":     f"{r.get('factor_score', 0):.1f}",
            "현재가":       f"₩{r['current_price']:,}",
            "1M수익률":     f"{r['ret_1m']:+.1f}%",
            "12-1모멘텀":   f"{r['momentum_score']:+.1f}%",
            "영업이익YoY":  f"{r.get('op_profit_yoy') or 0:+.1f}%"
                            if r.get("op_profit_yoy") is not None else "-",
            "EPS YoY":      f"{r.get('eps_yoy') or 0:+.1f}%"
                            if r.get("eps_yoy") is not None else "-",
            "ROE":          f"{r.get('roe') or 0:.1f}%"
                            if r.get("roe") is not None else "-",
            "변동성":       f"{r.get('annual_vol') or 0:.1f}%",
            "MDD":          f"{r.get('mdd') or 0:.1f}%",
            "거래대금(억)": f"{r.get('avg_trading_value_억') or 0:.0f}",
        }
        if show_pass_count:
            row["통과수"] = f"{r.get('pass_count', 0)}/8"
        rows.append(row)
    return pd.DataFrame(rows)


# ──────────────────────────────────────────
# 메인 페이지 (기존 run_stock_quant_page 함수명 유지)
# ──────────────────────────────────────────
def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 퀀트 추격매수 스크리너")
    st.caption("기술적 6필터 + 펀더멘털 2필터 · 팩터점수 랭킹 · 매일 14:30 KST 자동 갱신")

    with st.expander("📚 8대 필터 설계", expanded=False):
        st.markdown("""
**기술적 필터 (가격·거래량)**

| # | 필터 | 논문 | 기준 |
|---|------|------|------|
| 1 | 12-1 모멘텀 | Jegadeesh & Titman (1993) | 수익률 > 0% |
| 2 | 저변동성 | Ang et al. (2006) | 연환산 ≤ 60% |
| 3 | MDD | 실무 표준 | 1년 ≥ -25% |
| 4 | 유동성 | Amihud (2002) | 일평균 거래대금 ≥ 50억 |
| 5 | 거래량 모멘텀 | Lee & Swaminathan (2000) | 5일/60일 ≥ 1.3x |
| 6 | 추세 강도 | Novy-Marx (2013) 변형 | 기울기>0, R²≥0.5 |

**펀더멘털 필터 (DART API + 네이버금융)**

| # | 필터 | 기준 |
|---|------|------|
| 7 | 실적 모멘텀 | 영업이익·EPS YoY > 0 |
| 8 | 재무 건전성 | ROE ≥ 5%, 부채비율 ≤ 200% |

**팩터 점수 가중치:** 모멘텀 30% · 실적모멘텀 25% · 추세강도 15% · 거래량 10% · 변동성 10% · ROE 10%

**WatchList:** 8개 중 6개 이상 통과 — ALL-PASS 미달 관심 후보군
        """)

    # ── 데이터 로드 ──
    confirmed, watchlist, last_updated = load_screening_result(supabase)

    # ── 상태 바 ──
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("마지막 스크리닝", last_updated or "미실행")
    c2.metric("확정 선별",  f"{len(confirmed)}개 (ALL-PASS)")
    c3.metric("관심종목",   f"{len(watchlist)}개 (6~7필터)")
    now = now_kst()
    c4.metric("다음 갱신", ("오늘" if now.hour < 14 else "내일") + " 14:30 KST")

    st.divider()

    if not confirmed and not watchlist:
        st.info("스크리닝 결과 없음 — 로컬에서 `python quant_cron.py` 실행하세요.")
        return

    # ── 탭 ──
    tab_conf, tab_watch = st.tabs([
        f"🏆 확정 선별  ({len(confirmed)}개)",
        f"👀 WatchList  ({len(watchlist)}개)",
    ])

    # ══ 확정 탭 ══
    with tab_conf:
        if not confirmed:
            st.info("현재 8필터 ALL-PASS 종목이 없습니다. WatchList를 확인하세요.")
        else:
            st.subheader("8필터 ALL-PASS · 팩터점수 Top 10")
            df_conf = _build_table(confirmed)
            sel_conf = st.dataframe(df_conf, use_container_width=True,
                                    on_select="rerun", selection_mode="single-row",
                                    hide_index=True)
            sel_rows = []
            if sel_conf and hasattr(sel_conf, "selection") and sel_conf.selection.rows:
                sel_rows = sel_conf.selection.rows
            if sel_rows:
                st.divider()
                sel = confirmed[sel_rows[0]]
                st.subheader(f"🔬 [{sel['name']} ({sel['symbol']})]")
                _render_detail(sel, supabase)

        if confirmed:
            with st.expander("📊 확정 종목 지표 분포"):
                stat_df = pd.DataFrame([{
                    "종목":        r["name"],
                    "팩터점수":    r.get("factor_score", 0),
                    "12-1모멘텀":  r["momentum_score"],
                    "영업이익YoY": r.get("op_profit_yoy") or 0,
                    "ROE":         r.get("roe") or 0,
                } for r in confirmed])
                st.dataframe(stat_df, use_container_width=True, hide_index=True)
                st.bar_chart(stat_df.set_index("종목")["팩터점수"])

    # ══ WatchList 탭 ══
    with tab_watch:
        st.caption("8필터 중 6개 이상 통과 — 조건 충족 시 다음 배치에서 확정 편입 가능")
        if not watchlist:
            st.info("현재 WatchList 종목이 없습니다.")
        else:
            df_watch = _build_table(watchlist, show_pass_count=True)
            sel_watch = st.dataframe(df_watch, use_container_width=True,
                                     on_select="rerun", selection_mode="single-row",
                                     hide_index=True)
            sel_rows_w = []
            if sel_watch and hasattr(sel_watch, "selection") and sel_watch.selection.rows:
                sel_rows_w = sel_watch.selection.rows
            if sel_rows_w:
                st.divider()
                sel_w = watchlist[sel_rows_w[0]]
                st.subheader(f"🔬 [{sel_w['name']} ({sel_w['symbol']})]")
                _render_detail(sel_w, supabase)

            # 어떤 필터에서 탈락했는지 집계
            with st.expander("📋 필터별 탈락 현황 (어떤 조건이 부족한지)"):
                fail_cnt: dict = {}
                for r in watchlist:
                    fd = r.get("filter_details", {})
                    for fname in ALL_FILTER_NAMES:
                        if not fd.get(fname, {}).get("pass", True):
                            fail_cnt[fname] = fail_cnt.get(fname, 0) + 1
                if fail_cnt:
                    fail_df = pd.DataFrame(
                        sorted(fail_cnt.items(), key=lambda x: -x[1]),
                        columns=["필터명", "탈락 종목 수"]
                    )
                    st.dataframe(fail_df, use_container_width=True, hide_index=True)
                    st.caption("탈락 수가 많은 필터 = 해당 조건 충족 시 다음 배치 확정 편입 가능성 ↑")
