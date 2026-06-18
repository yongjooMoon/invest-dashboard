"""
quant_screener_ui.py — Streamlit UI (결과만 표시, 버튼 없음)
"""
import streamlit as st
import pandas as pd
from quant_core import (
    load_price_from_db, load_screening_result,
    ALL_FILTER_NAMES, TECH_FILTERS, FUNDA_FILTERS,
    now_kst,
)


def run_quant_screener_page(supabase, username: str = "admin"):
    st.title("📡 퀀트 추격매수 스크리너")
    st.caption("기술적 6필터 + 펀더멘털 2필터 ALL-PASS · 팩터 점수 랭킹 · 매일 14:30 KST 자동 갱신")

    with st.expander("📚 8대 필터 및 팩터 점수 설계", expanded=False):
        st.markdown("""
**[기술적 필터 — 가격/거래량]**

| # | 필터 | 논문 | 기준 |
|---|------|------|------|
| 1 | 12-1 모멘텀 | Jegadeesh & Titman (1993) | 수익률 > 0% |
| 2 | 저변동성 | Ang et al. (2006) | 연환산 ≤ 60% |
| 3 | MDD | 실무 표준 | 1년 ≥ -25% |
| 4 | 유동성 | Amihud (2002) | 일평균 거래대금 ≥ 50억 |
| 5 | 거래량 모멘텀 | Lee & Swaminathan (2000) | 5일/60일 ≥ 1.3x |
| 6 | 추세 강도 | Novy-Marx (2013) 변형 | 기울기>0, R²≥0.5 |

**[펀더멘털 필터 — DART API + 네이버금융]**

| # | 필터 | 기준 | 근거 |
|---|------|------|------|
| 7 | 실적 모멘텀 | 영업이익·EPS YoY > 0 | 한국시장 핵심 팩터 |
| 8 | 재무 건전성 | ROE ≥ 5%, 부채비율 ≤ 200% | Fama & French (1992) + 한국 현실 기준 |

**[팩터 점수 가중치]** 모멘텀 30% · 실적모멘텀 25% · 추세강도 15% · 거래량 10% · 변동성 10% · ROE 10%

> 부채비율 기준을 200%로 설정한 이유: 한국 KOSPI 제조업 평균 부채비율이 100~200% 수준으로, 150% 기준은 우량 제조업체 대다수를 탈락시키는 과도한 기준입니다.
        """)

    cached_results, last_updated = load_screening_result(supabase)

    c1, c2, c3 = st.columns(3)
    c1.metric("마지막 스크리닝", last_updated or "미실행")
    c2.metric("선별 종목 수", f"{len(cached_results)}개")
    now = now_kst()
    c3.metric("다음 갱신", ("오늘" if now.hour < 14 else "내일") + " 14:30 KST")

    st.divider()

    if not cached_results:
        st.info("스크리닝 결과 없음 — 로컬에서 `python quant_cron.py` 실행하세요.")
        return

    # ── 결과 테이블 ──
    st.subheader("🏆 최종 선별 종목 (8필터 ALL-PASS · 팩터점수 Top 10)")

    rows = []
    for idx, r in enumerate(cached_results):
        rows.append({
            "순위":         idx + 1,
            "종목명":       r["name"],
            "코드":         r["symbol"],
            "시장":         r["market"],
            "팩터점수":     f"{r.get('factor_score', 0):.1f}점",
            "현재가":       f"₩{r['current_price']:,}",
            "1M 수익률":    f"{r['ret_1m']:+.1f}%",
            "12-1 모멘텀":  f"{r['momentum_score']:+.1f}%",
            "영업이익YoY":  f"{r.get('op_profit_yoy', 0) or 0:+.1f}%" if r.get('op_profit_yoy') is not None else "-",
            "EPS YoY":      f"{r.get('eps_yoy', 0) or 0:+.1f}%"      if r.get('eps_yoy') is not None else "-",
            "ROE":          f"{r.get('roe', 0) or 0:.1f}%"            if r.get('roe') is not None else "-",
            "부채비율":     f"{r.get('debt_ratio', 0) or 0:.0f}%"     if r.get('debt_ratio') is not None else "-",
            "변동성":       f"{r.get('annual_vol', 0) or 0:.1f}%",
            "MDD":          f"{r.get('mdd', 0) or 0:.1f}%",
            "거래대금(억)": f"{r.get('avg_trading_value_억', 0) or 0:.0f}",
        })

    df_disp = pd.DataFrame(rows)
    selection = st.dataframe(df_disp, use_container_width=True,
                             on_select="rerun", selection_mode="single-row",
                             hide_index=True)

    sel_rows = []
    if selection and hasattr(selection, "selection") and selection.selection.rows:
        sel_rows = selection.selection.rows

    if not sel_rows:
        return

    sel = cached_results[sel_rows[0]]
    st.divider()
    st.subheader(f"🔬 [{sel['name']} ({sel['symbol']})] 상세")

    # 팩터 점수 게이지
    fs = sel.get("factor_score", 0)
    fs_color = "#00B464" if fs >= 70 else "#E6A23C" if fs >= 50 else "#F04452"
    st.markdown(
        f"**종합 팩터 점수:** "
        f"<span style='font-size:1.4rem; font-weight:bold; color:{fs_color}'>{fs}점 / 100점</span>",
        unsafe_allow_html=True
    )
    st.progress(int(fs))

    tab1, tab2, tab3 = st.tabs(["✅ 필터 상세", "📊 펀더멘털", "📈 가격 차트"])

    with tab1:
        fd = sel.get("filter_details", {})
        all_names = [n for n, _ in TECH_FILTERS] + FUNDA_FILTERS
        cols = st.columns(3)
        for i, fname in enumerate(all_names):
            detail = fd.get(fname, {})
            passed = detail.get("pass", False)
            reason = detail.get("reason", "-")
            with cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"**{fname}**")
                    st.markdown("🟢 통과" if passed else "🔴 탈락")
                    st.caption(reason)

    with tab2:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("영업이익 YoY",
                  f"{sel.get('op_profit_yoy') or 0:+.1f}%" if sel.get('op_profit_yoy') is not None else "-")
        m2.metric("EPS YoY",
                  f"{sel.get('eps_yoy') or 0:+.1f}%"      if sel.get('eps_yoy') is not None else "-")
        m3.metric("ROE",
                  f"{sel.get('roe') or 0:.1f}%"            if sel.get('roe') is not None else "-")
        m4.metric("부채비율",
                  f"{sel.get('debt_ratio') or 0:.0f}%"     if sel.get('debt_ratio') is not None else "-")

        st.caption("※ 펀더멘털 데이터 출처: DART OpenAPI (기본) → 네이버금융 크롤링 (보완)")

    with tab3:
        df_price = load_price_from_db(supabase, sel["symbol"])
        if not df_price.empty:
            st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("52주 최고", f"₩{int(df_price['Close'].tail(252).max()):,}")
            s2.metric("52주 최저", f"₩{int(df_price['Close'].tail(252).min()):,}")
            s3.metric("현재가",    f"₩{int(df_price['Close'].iloc[-1]):,}")
            chg = (df_price['Close'].iloc[-1] - df_price['Close'].iloc[-252]) \
                  / df_price['Close'].iloc[-252] * 100 if len(df_price) >= 252 else 0
            s4.metric("52주 수익률", f"{chg:+.1f}%")
        else:
            st.info("가격 데이터 없음")

    with st.expander("📊 선별 종목 비교", expanded=False):
        stat_df = pd.DataFrame([{
            "종목":           r["name"],
            "팩터점수":       r.get("factor_score", 0),
            "12-1모멘텀(%)":  r["momentum_score"],
            "영업이익YoY(%)": r.get("op_profit_yoy") or 0,
            "ROE(%)":         r.get("roe") or 0,
            "변동성(%)":      r.get("annual_vol") or 0,
        } for r in cached_results])
        st.dataframe(stat_df, use_container_width=True, hide_index=True)
        st.bar_chart(stat_df.set_index("종목")["팩터점수"])
