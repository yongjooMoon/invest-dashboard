"""
quant_screener_ui.py — Streamlit UI
quant_cron.py 가 매일 14:30 갱신 → 읽기만 함
"""
import streamlit as st
import pandas as pd
from quant_core import (
    load_price_from_db, load_screening_result,
    TECH_FILTERS, FUNDA_FILTERS, TOTAL_FILTERS, WATCHLIST_MIN_PASS,
    now_kst,
    PREFILTER_MARCAP_억, PREFILTER_TVOL_억,
)

# coreAi.py의 필터명 리스트 동적 생성 (만약 coreAi에 ALL_FILTER_NAMES가 없다면 여기서 자동 병합)
try:
    from quant_core import ALL_FILTER_NAMES
except ImportError:
    ALL_FILTER_NAMES = [f[0] for f in TECH_FILTERS] + FUNDA_FILTERS


def _render_filter_badges(filter_details: dict):
    """11대 필터 통과/탈락 현황을 4열 그리드로 시각화"""
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
    """선택된 종목의 상세 계량 지표 및 탭 렌더링"""
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
        # 11개 필터 수치 동적 반영
        badge = "🏆 ALL-PASS" if pc == TOTAL_FILTERS else f"👀 {pc}/{TOTAL_FILTERS} 통과"
        st.markdown(f"### {badge}")

    tab1, tab2, tab3 = st.tabs(["✅ 필터 상세", "📊 펀더멘털", "📈 가격 차트"])

    with tab1:
        _render_filter_badges(sel.get("filter_details", {}))

    with tab2:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("영업이익",
                  f"{sel.get('filter_details', {}).get('실적성장', {}).get('value') or 0:,.0f} 억"
                  if sel.get("filter_details", {}).get("실적성장", {}).get("value") is not None else "-")
        m2.metric("ROE",
                  f"{sel.get('filter_details', {}).get('재무퀄리티', {}).get('value') or 0:.1f}%"
                  if sel.get("filter_details", {}).get("재무퀄리티", {}).get("value") is not None else "-")
        m3.metric("PER",
                  f"{sel.get('filter_details', {}).get('PER밸류', {}).get('value') or 0:.1f}배"
                  if sel.get("filter_details", {}).get("PER밸류", {}).get("value") is not None else "-")
        m4.metric("PBR",
                  f"{sel.get('filter_details', {}).get('PBR밸류', {}).get('value') or 0:.2f}배"
                  if sel.get("filter_details", {}).get("PBR밸류", {}).get("value") is not None else "-")
        st.caption("출처: DART OpenAPI & 네이버금융 분석 캐시")

    with tab3:
        df_price = load_price_from_db(supabase, sel["symbol"])
        if not df_price.empty:
            st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
            s1, s2, s3, s4 = st.columns(4)
            close_1y = df_price["Close"].tail(252)
            s1.metric("52주 최고",   f"₩{int(close_1y.max()):,}")
            s2.metric("52주 최저",   f"₩{int(close_1y.min()):,}")
            s3.metric("현재가",      f"₩{int(df_price['Close'].iloc[-1]):,}")
            chg = (df_price["Close"].iloc[-1] - df_price["Close"].iloc[-252]) \
                  / df_price["Close"].iloc[-252] * 100 if len(df_price) >= 252 else 0
            s4.metric("52주 수익률", f"{chg:+.1f}%")
        else:
            st.info("가격 데이터 없음")


def _build_table(results: list, show_pass_count: bool = False) -> pd.DataFrame:
    """데이터프레임 출력용 테이블 빌더"""
    rows = []
    for idx, r in enumerate(results):
        fd = r.get("filter_details", {})
        row = {
            "순위":         idx + 1,
            "종목명":       r["name"],
            "코드":         r["symbol"],
            "시가총액(억)": f"{r.get('marcap_억', 0):,.0f}",
            "팩터점수":     f"{r.get('factor_score', 0):.1f}",
            "12-1모멘텀":   f"{r.get('momentum_12_1', 0):+.1f}%",
            "외인수급(억)": f"{r.get('foreign_net_buy', 0):+.1f}",
            "기관수급(억)": f"{r.get('inst_net_buy', 0):+.1f}",
            "변동성":       fd.get("저변동성", {}).get("reason", "-"),
            "MDD":          fd.get("MDD", {}).get("reason", "-"),
            "거래대금":     fd.get("유동성", {}).get("reason", "-"),
        }
        if show_pass_count:
            row["통과수"] = f"{r.get('pass_count', 0)}/{TOTAL_FILTERS}"
        rows.append(row)
    return pd.DataFrame(rows)


def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 퀀트 기관급 스크리너")
    st.caption(f"기술적 6필터 + 펀더멘털 5필터 ({TOTAL_FILTERS}대 대가 전략) · 매일 14:30 KST 자동 갱신")

    with st.expander("📚 전략 설계 상세 (AQR, JP Morgan 스타일)", expanded=False):
        st.markdown(f"""
**사전 필터링 (유니버스 엄격 정제)**
* 전종목 2,700개 → 보통주 정제 → 시가총액 ≥ **{PREFILTER_MARCAP_억:,}억** + 거래대금 ≥ **{PREFILTER_TVOL_억}억** + 주가 ≥ 1,000원 + 상장 1년 이상 조건으로 노이즈 전면 제거.

**기술적 필터 (6대 계량 지표)**
| # | 필터명 | 기반 논문 / 기준 | 통과 기준 |
|---|---|---|---|
| 1 | 12-1 모멘텀 | Jegadeesh & Titman (1993) | 최근 1달 제외 과거 11개월 수익률 > 0% |
| 2 | 저변동성 | Ang et al. (2006) | 최근 60일 연환산 변동성 ≤ 60% |
| 3 | MDD 관리 | 글로벌 실무 표준 | 1년 내 최대 낙폭(MDD) ≥ -25% |
| 4 | 기관급 유동성 | Amihud (2002) | 최근 20일 평균 거래대금 ≥ 50억 |
| 5 | 거래량 모멘텀 | Lee & Swaminathan (2000) | 5일 평균 거래량 / 60일 평균 거래량 ≥ 1.3배 |
| 6 | 추세 강도 | Novy-Marx (2013) 변형 | 60일 선형회귀 기울기 > 0 및 $R^2 \ge 0.5$ |

**펀더멘털 & 수급 필터 (5대 가치/성장 지표)**
| # | 필터명 | 데이터 출처 | 통과 기준 |
|---|---|---|---|
| 7 | 수급 우위 | KRX 정보데이터시스템 | 최근 20영업일 외국인 순매수 > 0 **OR** 기관 순매수 > 0 |
| 8 | 실적 성장성 | DART OpenAPI / 네이버 | 당기 영업이익 흑자 유지 **AND** 영업이익 YoY > 0% |
| 9 | 재무 건전성 | DART OpenAPI / 네이버 | ROE > 8.0% **AND** 부채비율 < 150% |
| 10 | PER 밸류 | 밸류에이션 하한 | $0 < PER \le 20$ 배 (미수집 시 통과 처리) |
| 11 | PBR 밸류 | 저평가 청산가치 | $0 < PBR \le 3.0$ 배 (미수집 시 통과 처리) |

**종합 팩터 스코어링 (100점 만점):** * 모멘텀(25점) + 실적모멘텀(15점) + 추세선형성(10점) + 거래량(8점) + 저변동성(10점) + ROE(10점) + PER(8점) + PBR(4점) + 수급보너스(10점)

**선별 클래스 분리:**
* **🏆 확정 선별:** 11개 필터 **ALL-PASS** 종목
* **👀 WatchList:** 11개 중 **{WATCHLIST_MIN_PASS}개 이상** 통과 종목 (차기 배치 유력 후보)
        """)

    confirmed, watchlist, last_updated = load_screening_result(supabase)

    # 상단 대시보드 메트릭 상태바
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("마지막 스크리닝", last_updated or "미실행")
    c2.metric("확정 선별 (ALL-PASS)",  f"{len(confirmed)}개")
    c3.metric("관심종목 (WatchList)",   f"{len(watchlist)}개", f"{WATCHLIST_MIN_PASS}~{TOTAL_FILTERS-1}개 통과")
    now = now_kst()
    c4.metric("다음 갱신 예정", ("오늘" if now.hour < 14 or (now.hour == 14 and now.minute < 30) else "내일") + " 14:30 KST")

    st.divider()

    if not confirmed and not watchlist:
        st.info("Supabase 저장소에 스크리닝 결과가 비어있습니다. 로컬 백엔드 크론 배치를 먼저 구동하세요.")
        return

    tab_conf, tab_watch = st.tabs([
        f"🏆 확정 선별 ({len(confirmed)}개)",
        f"👀 WatchList ({len(watchlist)}개)",
    ])

    # ── 확정(ALL-PASS) 탭 ──
    with tab_conf:
        if not confirmed:
            st.info(f"현재 {TOTAL_FILTERS}개 필터를 모두 관통한 ALL-PASS 종목이 없습니다. 아래 WatchList 탭을 분석하세요.")
        else:
            st.subheader(f"종합 팩터 스코어 랭킹 (최대 10개 표시)")
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
                st.subheader(f"🔬 종목 정밀 진단: [{sel['name']} ({sel['symbol']})]")
                _render_detail(sel, supabase)

        if confirmed:
            with st.expander("📊 확정 종목 팩터 분포 차트"):
                stat_df = pd.DataFrame([{
                    "종목":         r["name"],
                    "팩터점수":     r.get("factor_score", 0),
                    "12-1모멘텀":   r.get("momentum_12_1", 0),
                    "시가총액(억)": r.get("marcap_억", 0),
                } for r in confirmed])
                st.dataframe(stat_df, use_container_width=True, hide_index=True)
                st.bar_chart(stat_df.set_index("종목")["팩터점수"])

    # ── WatchList 탭 ──
    with tab_watch:
        st.caption(f"안정 권역 진입 랭킹 ({WATCHLIST_MIN_PASS}개 이상 필터 충족)")
        if not watchlist:
            st.info("조건을 만족하는 관심종목 풀이 비어있습니다.")
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
                st.subheader(f"🔬 종목 정밀 진단: [{sel_w['name']} ({sel_w['symbol']})]")
                _render_detail(sel_w, supabase)

            with st.expander("📋 필터별 취약 영역 (탈락 현황 분석)"):
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
                    st.caption("🚨 탈락 수가 많은 필터 지표가 개선되는 종목을 관찰하면 확정 편입 기회를 선점할 수 있습니다.")
