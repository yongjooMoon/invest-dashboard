"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
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
        st.info(f"💡 **권장 진입가 (Entry Cost)**: {sel.get('entry_price', sel['current_price']):,}원 부근 (단기 눌림목 지지 기반)")
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
            "💡추천 매수가": f"₩{r.get('entry_price', r['current_price']):,}",
            "1M수익률": f"{r['ret_1m']:+.1f}%",
            "모멘텀": f"{r.get('momentum_score', 0):+.1f}%",
            "순이익YoY": f"{r.get('net_income_yoy') or 0:+.1f}%" if r.get("net_income_yoy") is not None else "-",
        })
    return pd.DataFrame(rows)

def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 정통 퀀트 스크리너 (Filter & Score 분리)")
    st.caption("안정성(Survival) 필터로 1차 선별 후, 상대 우위(Ranking) 점수로 백분위 평가 · 매일 14:30 KST 갱신")

    with st.expander("📚 전략 설계 (강화된 퀀트 아키텍처)", expanded=False):
        st.markdown("""
- **[Filter = Survival (절대 방어선)]**: 종목의 생존력을 담보하는 6가지 강력한 허들입니다.
  - `Growth Composite`: 매출/영업이익/순이익을 가중 평균한 통합 성장성
  - `Volatility Adaptive MDD`: 과거 고정값이 아닌 ATR 기반의 동적 하락 한계선 방어
  - 그 외 유동성, 모멘텀, 저변동성, 추세 강도 조건 적용
- **[Score = Ranking (상대 평가)]**: 생존 필터를 통과한 종목끼리만 백분위(Percentile)를 매겨 최종 랭킹 점수를 도출합니다.
- **[편입 기준]**: 
  - **Confirmed**: 6개의 생존 필터 중 **최소 5개 이상**을 통과한 튼튼한 종목
  - **Watchlist**: 시장이 안 좋을 때를 대비한 예비 종목 (3~4개 통과)
        """)

    confirmed, watchlist, last_updated = load_screening_result(supabase)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("마지막 스크리닝", last_updated or "미실행")
    c2.metric("🏆 확정 편입", f"{len(confirmed)}개 (생존 5+ 통과)")
    c3.metric("👀 관심 종목", f"{len(watchlist)}개 (생존 3~4 통과)")

    now = now_kst()
    c4.metric("다음 갱신", ("오늘" if now.hour < 14 else "내일") + " 14:30 KST")

    st.divider()

    if not confirmed and not watchlist:
        st.info("조건을 통과한 종목이 전혀 없습니다. `quant_cron.py`를 실행하여 캐시 데이터를 갱신해주세요.")
        return

    tab_conf, tab_watch = st.tabs([f"🏆 확정 선별 ({len(confirmed)}개)", f"👀 WatchList ({len(watchlist)}개)"])

    with tab_conf:
        if not confirmed:
            st.warning("현재 생존 조건을 5개 이상 통과한 확정 종목이 없습니다. 장세가 험난할 수 있습니다.")
        else:
            st.subheader("안정성(생존력) 검증 완료 종목")
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
