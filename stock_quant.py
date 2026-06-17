import streamlit as st
import requests
import re
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import time
import schedule
import threading

# ==========================================
# [공통 유틸]
# ==========================================
def now_kst():
    return datetime.utcnow() + timedelta(hours=9)

def now_kst_str():
    return now_kst().strftime('%Y-%m-%d %H:%M:%S')

def is_expired(ts_str, threshold_sec):
    if not ts_str:
        return True
    try:
        clean = ts_str.replace('T', ' ').split('.')[0].split('+')[0]
        dt = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
        return (now_kst() - dt).total_seconds() >= threshold_sec
    except:
        return True

# ==========================================
# [Layer 1] 데이터 수집 — KRX 전종목 + 가격 이력
# ==========================================
def _normalize_listing(raw: pd.DataFrame, market: str) -> pd.DataFrame:
    """
    FDR 버전마다 컬럼명이 다르게 내려옴 → 방어적 정규화
    Symbol : Code / Symbol / Ticker
    Name   : Name / 종목명
    Sector : Sector / Industry / 업종
    """
    col = raw.columns.tolist()

    # Symbol
    sym_col = next((c for c in ['Symbol', 'Code', 'Ticker'] if c in col), None)
    # Name
    name_col = next((c for c in ['Name', '종목명'] if c in col), None)
    # Sector
    sec_col = next((c for c in ['Sector', 'Industry', '업종'] if c in col), None)

    if sym_col is None or name_col is None:
        raise ValueError(f"필수 컬럼 없음. 실제 컬럼: {col}")

    df = pd.DataFrame()
    df['Symbol'] = raw[sym_col].astype(str).str.zfill(6)
    df['Name']   = raw[name_col].astype(str)
    df['Market'] = market
    df['Sector'] = raw[sec_col].astype(str) if sec_col else '-'
    return df


def load_krx_universe() -> pd.DataFrame:
    """KRX 전종목 리스트 (KOSPI + KOSDAQ) — FDR 버전 무관 방어 처리"""
    try:
        kospi_raw  = fdr.StockListing('KOSPI')
        kosdaq_raw = fdr.StockListing('KOSDAQ')
        kospi  = _normalize_listing(kospi_raw,  'KOSPI')
        kosdaq = _normalize_listing(kosdaq_raw, 'KOSDAQ')
        df = pd.concat([kospi, kosdaq], ignore_index=True).dropna(subset=['Symbol', 'Name'])
        # ETF·우선주 등 6자리 미만 코드 제외 (선택)
        df = df[df['Symbol'].str.len() == 6].reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"KRX 종목 로드 실패: {e}")
        return pd.DataFrame()


def fetch_price_history(symbol: str, years: int = 3) -> pd.DataFrame:
    """
    일봉 가격 데이터 최근 N년치.
    반환 컬럼: Open, High, Low, Close, Volume
    FDR 버전마다 컬럼 대소문자가 다를 수 있으므로 방어 처리.
    """
    start = (now_kst() - timedelta(days=365 * years)).strftime('%Y-%m-%d')
    try:
        raw = fdr.DataReader(symbol, start=start)
        if raw is None or raw.empty:
            return pd.DataFrame()

        # 컬럼 정규화 (소문자 → 타이틀케이스 매핑)
        col_map = {c.lower(): c for c in raw.columns}
        rename = {}
        for std in ['Open', 'High', 'Low', 'Close', 'Volume']:
            found = col_map.get(std.lower())
            if found and found != std:
                rename[found] = std
        if rename:
            raw = raw.rename(columns=rename)

        needed = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in raw.columns]
        if 'Close' not in needed:
            return pd.DataFrame()

        df = raw[needed].dropna()
        df.index = pd.to_datetime(df.index)
        return df
    except:
        return pd.DataFrame()


# ==========================================
# [Layer 2] 6대 리스크 필터 (논문 기반)
# ==========================================
#
# [1] 모멘텀 필터 — Jegadeesh & Titman (1993)
#     "Returns to Buying Winners and Selling Losers"
#     • 12개월 수익률에서 최근 1개월 제외 (12-1 모멘텀)
#     • 상위 30% 이상만 통과
#
# [2] 저변동성 필터 — Ang et al. (2006)
#     "The Cross-Section of Volatility and Expected Returns"
#     • 60일 일간 수익률 표준편차 연환산
#     • 연환산 변동성 60% 이하만 통과 (추격매수 안정성)
#
# [3] 최대낙폭(MDD) 필터 — 실무 리스크 관리 표준
#     • 1년 내 MDD 25% 이내만 통과
#     • 급락 종목 추격매수 차단
#
# [4] 유동성 필터 — Amihud (2002)
#     "Illiquidity and Stock Returns"
#     • 최근 20일 일평균 거래대금 50억 원 이상
#     • 슬리피지 리스크 차단
#
# [5] 거래량 모멘텀 필터 — Lee & Swaminathan (2000)
#     "Price Momentum and Trading Volume"
#     • 최근 5일 평균 거래량 ÷ 60일 평균 거래량 >= 1.3
#     • 거래량 동반 상승: 추격매수 신호
#
# [6] 수익성 필터 — Novy-Marx (2013)
#     "The Other Side of Value: The Gross Profitability Premium"
#     • 60일 가격 추세(선형 회귀 기울기) 양(+)일 것
#     • 추세 없는 단순 반등 종목 차단
#
# ==========================================

def filter_momentum(df: pd.DataFrame) -> dict:
    """
    [필터 1] 12-1 모멘텀 (Jegadeesh & Titman 1993)
    최소 252 거래일 필요
    """
    if len(df) < 252:
        return {"pass": False, "value": None, "reason": "데이터 부족 (<252일)"}
    close = df['Close']
    # 12개월 전 → 1개월 전 수익률 (최근 1개월 제외)
    ret_12_1 = (close.iloc[-21] - close.iloc[-252]) / close.iloc[-252] * 100
    passed = ret_12_1 > 0  # 양의 모멘텀 (분위수 컷은 전체 유니버스 정렬 후 적용)
    return {
        "pass": passed,
        "value": round(ret_12_1, 2),
        "reason": f"12-1 모멘텀 {ret_12_1:+.1f}%"
    }


def filter_volatility(df: pd.DataFrame) -> dict:
    """
    [필터 2] 저변동성 (Ang et al. 2006)
    60일 일간 수익률 표준편차 연환산 60% 이하
    """
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": "데이터 부족 (<60일)"}
    daily_ret = df['Close'].pct_change().dropna()
    vol_60d = daily_ret.iloc[-60:].std() * np.sqrt(252) * 100  # 연환산 %
    passed = vol_60d <= 60.0
    return {
        "pass": passed,
        "value": round(vol_60d, 2),
        "reason": f"연환산 변동성 {vol_60d:.1f}% (기준 60%)"
    }


def filter_mdd(df: pd.DataFrame) -> dict:
    """
    [필터 3] MDD 최대낙폭 리스크
    최근 1년 이내 MDD 25% 이내
    """
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": "데이터 부족"}
    close_1y = df['Close'].iloc[-252:] if len(df) >= 252 else df['Close']
    roll_max = close_1y.cummax()
    drawdown = (close_1y - roll_max) / roll_max * 100
    mdd = drawdown.min()  # 음수
    passed = mdd >= -25.0
    return {
        "pass": passed,
        "value": round(mdd, 2),
        "reason": f"1년 MDD {mdd:.1f}% (기준 -25%)"
    }


def filter_liquidity(df: pd.DataFrame) -> dict:
    """
    [필터 4] 유동성 (Amihud 2002)
    20일 일평균 거래대금 50억 원 이상
    """
    if len(df) < 20:
        return {"pass": False, "value": None, "reason": "데이터 부족 (<20일)"}
    trading_value = (df['Close'] * df['Volume']).iloc[-20:]
    avg_tv = trading_value.mean()
    passed = avg_tv >= 5_000_000_000  # 50억
    return {
        "pass": passed,
        "value": round(avg_tv / 1e8, 1),  # 억 원 단위
        "reason": f"일평균 거래대금 {avg_tv/1e8:.0f}억 (기준 50억)"
    }


def filter_volume_momentum(df: pd.DataFrame) -> dict:
    """
    [필터 5] 거래량 모멘텀 (Lee & Swaminathan 2000)
    최근 5일 평균 거래량 / 60일 평균 거래량 >= 1.3
    """
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": "데이터 부족 (<60일)"}
    vol = df['Volume']
    ratio = vol.iloc[-5:].mean() / vol.iloc[-60:].mean()
    passed = ratio >= 1.3
    return {
        "pass": passed,
        "value": round(ratio, 3),
        "reason": f"거래량 비율 {ratio:.2f}x (기준 1.3x)"
    }


def filter_trend_strength(df: pd.DataFrame) -> dict:
    """
    [필터 6] 추세 강도 (Novy-Marx 2013 변형)
    60일 종가 선형 회귀 기울기가 양(+)
    + R² >= 0.5 (추세 신뢰도)
    """
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": "데이터 부족 (<60일)"}
    close = df['Close'].iloc[-60:].values
    x = np.arange(len(close))
    # 선형 회귀
    p = np.polyfit(x, close, 1)
    slope = p[0]
    # R² 계산
    y_hat = np.polyval(p, x)
    ss_res = np.sum((close - y_hat) ** 2)
    ss_tot = np.sum((close - close.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    passed = slope > 0 and r2 >= 0.5
    return {
        "pass": passed,
        "value": round(slope, 4),
        "reason": f"추세 기울기 {slope:+.2f}, R² {r2:.2f} (기준: 기울기>0, R²≥0.5)"
    }


# ==========================================
# [Layer 3] 전종목 스크리닝 배치 엔진
# ==========================================

RISK_FILTERS = [
    ("모멘텀 (Jegadeesh & Titman)", filter_momentum),
    ("저변동성 (Ang et al.)", filter_volatility),
    ("MDD 리스크", filter_mdd),
    ("유동성 (Amihud)", filter_liquidity),
    ("거래량 모멘텀 (Lee & Swaminathan)", filter_volume_momentum),
    ("추세 강도 (Novy-Marx 변형)", filter_trend_strength),
]

def run_full_screening(supabase, universe_df: pd.DataFrame, top_n: int = 10) -> list:
    """
    전종목 대상 6개 필터 All-Pass 스크리닝.
    통과 종목을 12-1 모멘텀 점수 기준 상위 top_n개 반환.
    """
    results = []
    total = len(universe_df)
    progress = st.progress(0, text="스크리닝 시작...")

    for i, (_, row) in enumerate(universe_df.iterrows()):
        symbol = row['Symbol']
        name = row.get('Name', symbol)
        progress.progress((i + 1) / total, text=f"[{i+1}/{total}] {name} 분석 중...")

        df = fetch_price_history(symbol, years=3)
        if df.empty or len(df) < 60:
            continue

        filter_results = {}
        all_pass = True

        for filter_name, filter_fn in RISK_FILTERS:
            result = filter_fn(df)
            filter_results[filter_name] = result
            if not result["pass"]:
                all_pass = False
                break  # 하나라도 탈락하면 즉시 중단 (효율)

        if not all_pass:
            continue

        # 모멘텀 점수 (최종 랭킹용)
        momentum_score = filter_results["모멘텀 (Jegadeesh & Titman)"]["value"] or 0

        # 현재가 및 기본 지표
        curr_price = int(df['Close'].iloc[-1])
        price_1m_ago = df['Close'].iloc[-21] if len(df) >= 21 else df['Close'].iloc[0]
        ret_1m = (df['Close'].iloc[-1] - price_1m_ago) / price_1m_ago * 100

        results.append({
            "symbol": symbol,
            "name": name,
            "sector": row.get('Sector', '-'),
            "market": row.get('Market', '-'),
            "current_price": curr_price,
            "ret_1m": round(ret_1m, 2),
            "momentum_score": round(momentum_score, 2),
            "vol_ratio": filter_results["거래량 모멘텀 (Lee & Swaminathan)"]["value"],
            "annual_vol": filter_results["저변동성 (Ang et al.)"]["value"],
            "mdd": filter_results["MDD 리스크"]["value"],
            "avg_trading_value_억": filter_results["유동성 (Amihud)"]["value"],
            "trend_slope": filter_results["추세 강도 (Novy-Marx 변형)"]["value"],
            "filter_details": filter_results,
            "screened_at": now_kst_str(),
        })

    progress.empty()

    # 12-1 모멘텀 기준 상위 top_n
    results.sort(key=lambda x: x["momentum_score"], reverse=True)
    return results[:top_n]


# ==========================================
# [Layer 4] Supabase 저장 / 조회
# ==========================================
CACHE_TABLE = "quant_screening_cache"

def save_screening_result(supabase, results: list):
    payload = {
        "id": 1,  # 단일 행 유지 (upsert)
        "results": json.dumps(results, ensure_ascii=False),
        "updated_at": now_kst_str(),
    }
    supabase.table(CACHE_TABLE).upsert(payload).execute()


def load_screening_result(supabase) -> tuple[list, str]:
    """(결과 리스트, 마지막 업데이트 시각) 반환"""
    try:
        res = supabase.table(CACHE_TABLE).select("*").eq("id", 1).execute()
        if res.data:
            row = res.data[0]
            return json.loads(row["results"]), row.get("updated_at", "")
    except:
        pass
    return [], ""


# ==========================================
# [Layer 5] 스케줄러 (매일 14:30 KST 배치)
# ==========================================
_scheduler_started = False

def _batch_job(supabase):
    try:
        universe = load_krx_universe()
        if universe.empty:
            return
        results = run_full_screening(supabase, universe)
        save_screening_result(supabase, results)
    except:
        pass

def start_scheduler(supabase):
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _run():
        schedule.every().day.at("14:30").do(_batch_job, supabase=supabase)
        while True:
            schedule.run_pending()
            time.sleep(30)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ==========================================
# [Layer 6] UI — 퀀트 스크리너 메인 페이지
# ==========================================

def render_filter_badge(passed: bool) -> str:
    return "✅" if passed else "❌"


def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 퀀트 추격매수 스크리너")
    st.caption("논문 기반 6대 리스크 필터 ALL-PASS 종목만 선별 · 매일 14:30 KST 자동 배치")

    # 스케줄러 기동 (세션당 1회)
    if "scheduler_started" not in st.session_state:
        start_scheduler(supabase)
        st.session_state["scheduler_started"] = True

    # ── 전략 설명 expander ──
    with st.expander("📚 적용 퀀트 전략 및 리스크 필터 상세", expanded=False):
        st.markdown("""
| # | 필터명 | 논문 근거 | 기준값 | 목적 |
|---|--------|-----------|--------|------|
| 1 | **12-1 모멘텀** | Jegadeesh & Titman (1993) | 12-1개월 수익률 > 0% | 추세 추종 종목 선별 |
| 2 | **저변동성** | Ang et al. (2006) | 연환산 변동성 ≤ 60% | 급등락 종목 차단 |
| 3 | **MDD 리스크** | 실무 리스크 표준 | 1년 MDD ≥ -25% | 급락 추격매수 차단 |
| 4 | **유동성** | Amihud (2002) | 일평균 거래대금 ≥ 50억 | 슬리피지·유동성 위험 차단 |
| 5 | **거래량 모멘텀** | Lee & Swaminathan (2000) | 5일/60일 거래량 ≥ 1.3x | 추격매수 타이밍 포착 |
| 6 | **추세 강도** | Novy-Marx (2013) 변형 | 기울기>0, R²≥0.5 | 단순 반등 종목 차단 |

> **6개 필터 ALL-PASS** 종목만 최종 후보에 진입하며, 12-1 모멘텀 점수 기준 **상위 10개** 종목을 추출합니다.
        """)

    # ── 현재 캐시 결과 로드 ──
    cached_results, last_updated = load_screening_result(supabase)

    # ── 상단 상태 바 ──
    col_stat1, col_stat2, col_stat3 = st.columns(3)
    with col_stat1:
        st.metric("마지막 스크리닝", last_updated if last_updated else "미실행")
    with col_stat2:
        st.metric("통과 종목 수", f"{len(cached_results)} / 10")
    with col_stat3:
        next_run = "오늘 14:30 KST" if now_kst().hour < 14 else "내일 14:30 KST"
        st.metric("다음 자동 실행", next_run)

    st.divider()

    # ── 수동 즉시 실행 버튼 ──
    col_btn1, col_btn2 = st.columns([1, 3])
    with col_btn1:
        manual_run = st.button("⚡ 지금 즉시 스크리닝 실행", type="primary", use_container_width=True)

    if manual_run:
        universe = load_krx_universe()
        if universe.empty:
            st.error("종목 리스트 로드 실패")
        else:
            with st.status(f"📡 KRX 전종목 {len(universe)}개 대상 6대 필터 스크리닝 중...", expanded=True) as status:
                st.write("▶ 3년치 일봉 데이터 수집 및 리스크 필터 적용 중...")
                results = run_full_screening(supabase, universe)
                save_screening_result(supabase, results)
                status.update(label=f"✅ 스크리닝 완료 — {len(results)}개 종목 ALL-PASS", state="complete")
            st.rerun()

    # ── 결과 없음 안내 ──
    if not cached_results:
        st.info("스크리닝 결과가 없습니다. '지금 즉시 스크리닝 실행' 버튼을 눌러주세요.")
        return

    # ── 결과 요약 테이블 ──
    st.subheader("🏆 최종 선별 종목 (6필터 ALL-PASS · 모멘텀 Top 10)")

    rows = []
    for r in cached_results:
        rows.append({
            "순위": cached_results.index(r) + 1,
            "종목명": r["name"],
            "종목코드": r["symbol"],
            "시장": r["market"],
            "업종": r["sector"],
            "현재가": f"₩{r['current_price']:,}",
            "1개월수익률": f"{r['ret_1m']:+.1f}%",
            "12-1모멘텀": f"{r['momentum_score']:+.1f}%",
            "연환산변동성": f"{r['annual_vol']:.1f}%",
            "MDD": f"{r['mdd']:.1f}%",
            "거래대금(억)": f"{r['avg_trading_value_억']:.0f}",
            "거래량비율": f"{r['vol_ratio']:.2f}x",
            "추세기울기": f"{r['trend_slope']:+.4f}",
        })

    df_display = pd.DataFrame(rows)

    selection = st.dataframe(
        df_display,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
    )

    # ── 종목 상세 드릴다운 ──
    selected_rows = []
    if selection and hasattr(selection, "selection") and selection.selection.rows:
        selected_rows = selection.selection.rows

    if selected_rows:
        sel = cached_results[selected_rows[0]]
        st.divider()
        st.subheader(f"🔬 [{sel['name']} ({sel['symbol']})] 필터 상세 결과")

        fd = sel.get("filter_details", {})

        col1, col2, col3 = st.columns(3)
        filters_list = list(RISK_FILTERS)

        for idx, (fname, _) in enumerate(filters_list):
            col = [col1, col2, col3][idx % 3]
            detail = fd.get(fname, {})
            passed = detail.get("pass", False)
            reason = detail.get("reason", "-")
            val = detail.get("value", "-")
            with col:
                with st.container(border=True):
                    badge = "🟢 통과" if passed else "🔴 탈락"
                    st.markdown(f"**{fname}**")
                    st.markdown(f"{badge}")
                    st.caption(reason)

        st.divider()

        # 가격 차트 (최근 1년)
        st.markdown(f"**📈 {sel['name']} 최근 1년 가격 추이**")
        df_price = fetch_price_history(sel["symbol"], years=1)
        if not df_price.empty:
            chart_df = df_price[['Close']].rename(columns={"Close": "종가"})
            st.line_chart(chart_df)

        # 추격매수 판단 요약
        st.markdown("**📋 추격매수 적합성 판단**")
        score_color = "#00B464" if sel["momentum_score"] > 20 else "#E6A23C" if sel["momentum_score"] > 0 else "#F04452"
        st.markdown(f"""
- **모멘텀 강도:** <span style='color:{score_color}; font-weight:bold;'>{sel['momentum_score']:+.1f}%</span>
- **거래량 급증 배율:** {sel['vol_ratio']:.2f}x (1.3x 이상 = 추격매수 신호)
- **변동성 수준:** 연환산 {sel['annual_vol']:.1f}% (낮을수록 안전)
- **MDD:** {sel['mdd']:.1f}% (손절 기준선 참고)
- **스크리닝 시각:** {sel['screened_at']}
        """, unsafe_allow_html=True)

    st.divider()

    # ── 필터별 통과율 현황 (전체 결과 기준) ──
    with st.expander("📊 필터별 통과 현황", expanded=False):
        st.caption(f"최종 통과 {len(cached_results)}개 종목 기준 각 지표 분포")
        if cached_results:
            mom_vals = [r["momentum_score"] for r in cached_results]
            vol_vals = [r["annual_vol"] for r in cached_results]
            vr_vals = [r["vol_ratio"] for r in cached_results]
            mdd_vals = [r["mdd"] for r in cached_results]

            stat_df = pd.DataFrame({
                "종목": [r["name"] for r in cached_results],
                "12-1 모멘텀(%)": mom_vals,
                "연환산변동성(%)": vol_vals,
                "거래량비율(x)": vr_vals,
                "MDD(%)": mdd_vals,
            })
            st.dataframe(stat_df, use_container_width=True, hide_index=True)

            st.bar_chart(stat_df.set_index("종목")["12-1 모멘텀(%)"])
