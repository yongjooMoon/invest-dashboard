"""
quant_screener_ui.py
─────────────────────────────────────────────
Streamlit UI — DB 에 저장된 스크리닝 결과만 표시.
수동 실행 버튼 없음. quant_cron.py 가 매일 14:30 갱신.
"""
import streamlit as st
import pandas as pd
import json
from quant_core import (
    load_price_from_db,
    load_screening_result,
    RISK_FILTERS,
    now_kst,
)


def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 퀀트 추격매수 스크리너")
    st.caption("논문 기반 6대 리스크 필터 ALL-PASS · 매일 14:30 KST 자동 갱신")

    # ── 전략 설명 ──
    with st.expander("📚 적용 퀀트 전략 및 리스크 필터", expanded=False):
        st.markdown("""
| # | 필터명 | 논문 | 기준 | 목적 |
|---|--------|------|------|------|
| 1 | **12-1 모멘텀** | Jegadeesh & Titman (1993) | 수익률 > 0% | 추세 추종 |
| 2 | **저변동성** | Ang et al. (2006) | 연환산 ≤ 60% | 급등락 차단 |
| 3 | **MDD 리스크** | 실무 리스크 표준 | 1년 ≥ -25% | 급락 추격 차단 |
| 4 | **유동성** | Amihud (2002) | 일평균 거래대금 ≥ 50억 | 슬리피지 차단 |
| 5 | **거래량 모멘텀** | Lee & Swaminathan (2000) | 5일/60일 ≥ 1.3x | 추격매수 타이밍 |
| 6 | **추세 강도** | Novy-Marx (2013) 변형 | 기울기>0, R²≥0.5 | 단순 반등 차단 |

> 6개 필터 **ALL-PASS** 종목만 진입 · 12-1 모멘텀 기준 **상위 10개** 선별
        """)

    # ── 결과 로드 ──
    cached_results, last_updated = load_screening_result(supabase)

    # ── 상태 바 ──
    c1, c2, c3 = st.columns(3)
    c1.metric("마지막 스크리닝", last_updated or "미실행")
    c2.metric("선별 종목 수", f"{len(cached_results)}개")
    now = now_kst()
    next_run = f"오늘 14:30" if now.hour < 14 else "내일 14:30"
    c3.metric("다음 자동 갱신", next_run + " KST")

    st.divider()

    if not cached_results:
        st.info("아직 스크리닝 결과가 없습니다.\n\n"
                "로컬에서 `python quant_cron.py` 를 실행하거나 14:30 자동 배치를 기다려주세요.")
        return

    # ── 결과 테이블 ──
    st.subheader("🏆 최종 선별 종목 (6필터 ALL-PASS · 모멘텀 Top 10)")

    rows = []
    for idx, r in enumerate(cached_results):
        rows.append({
            "순위":         idx + 1,
            "종목명":       r["name"],
            "코드":         r["symbol"],
            "시장":         r["market"],
            "업종":         r["sector"],
            "현재가":       f"₩{r['current_price']:,}",
            "1M 수익률":    f"{r['ret_1m']:+.1f}%",
            "12-1 모멘텀":  f"{r['momentum_score']:+.1f}%",
            "연환산변동성": f"{r['annual_vol']:.1f}%" if r['annual_vol'] else "-",
            "MDD":          f"{r['mdd']:.1f}%"        if r['mdd'] else "-",
            "거래대금(억)": f"{r['avg_trading_value_억']:.0f}" if r['avg_trading_value_억'] else "-",
            "거래량비율":   f"{r['vol_ratio']:.2f}x"  if r['vol_ratio'] else "-",
        })

    df_disp = pd.DataFrame(rows)
    selection = st.dataframe(
        df_disp,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
    )

    # ── 종목 드릴다운 ──
    sel_rows = []
    if selection and hasattr(selection, "selection") and selection.selection.rows:
        sel_rows = selection.selection.rows

    if not sel_rows:
        return

    sel = cached_results[sel_rows[0]]
    st.divider()
    st.subheader(f"🔬 [{sel['name']} ({sel['symbol']})] 상세 분석")

    tab1, tab2 = st.tabs(["✅ 필터 상세", "📈 가격 차트"])

    with tab1:
        fd = sel.get("filter_details", {})
        cols = st.columns(3)
        for i, (fname, _) in enumerate(RISK_FILTERS):
            detail = fd.get(fname, {})
            passed = detail.get("pass", False)
            reason = detail.get("reason", "-")
            with cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"**{fname}**")
                    st.markdown("🟢 통과" if passed else "🔴 탈락")
                    st.caption(reason)

        st.divider()
        score_color = "#00B464" if sel["momentum_score"] > 20 \
                      else "#E6A23C" if sel["momentum_score"] > 0 else "#F04452"
        st.markdown(f"""
**📋 추격매수 적합성**
- 모멘텀 강도: <span style='color:{score_color}; font-weight:bold;'>{sel['momentum_score']:+.1f}%</span>
- 거래량 급증 배율: **{sel['vol_ratio']:.2f}x** (1.3x 이상 = 추격 신호)
- 연환산 변동성: **{sel['annual_vol']:.1f}%** (낮을수록 안전)
- MDD: **{sel['mdd']:.1f}%** (손절 기준 참고)
- 스크리닝 시각: {sel['screened_at']}
        """, unsafe_allow_html=True)

    with tab2:
        df_price = load_price_from_db(supabase, sel["symbol"])
        if not df_price.empty:
            chart = df_price[["Close"]].tail(252).rename(columns={"Close": "종가"})
            st.line_chart(chart)

            # 간단 통계
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("52주 최고", f"₩{int(df_price['Close'].tail(252).max()):,}")
            s2.metric("52주 최저", f"₩{int(df_price['Close'].tail(252).min()):,}")
            s3.metric("현재가",    f"₩{int(df_price['Close'].iloc[-1]):,}")
            chg = (df_price['Close'].iloc[-1] - df_price['Close'].iloc[-252]) \
                  / df_price['Close'].iloc[-252] * 100 if len(df_price) >= 252 else 0
            s4.metric("52주 수익률", f"{chg:+.1f}%")
        else:
            st.info("가격 데이터 없음 (DB 미적재)")

    # ── 전체 지표 분포 ──
    with st.expander("📊 선별 종목 지표 분포", expanded=False):
        stat_df = pd.DataFrame([{
            "종목":        r["name"],
            "12-1모멘텀(%)": r["momentum_score"],
            "변동성(%)":   r["annual_vol"],
            "거래량비율x": r["vol_ratio"],
            "MDD(%)":      r["mdd"],
        } for r in cached_results])
        st.dataframe(stat_df, use_container_width=True, hide_index=True)
        st.bar_chart(stat_df.set_index("종목")["12-1모멘텀(%)"])
