import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ──────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────
def now_kst() -> datetime:
    return datetime.utcnow() + timedelta(hours=9)

def now_kst_str() -> str:
    return now_kst().strftime('%Y-%m-%d %H:%M:%S')

def is_expired(ts_str: str, threshold_sec: int) -> bool:
    if not ts_str:
        return True
    try:
        clean = ts_str.replace('T', ' ').split('.')[0].split('+')[0]
        dt = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
        return (now_kst() - dt).total_seconds() >= threshold_sec
    except:
        return True

# ──────────────────────────────────────────
# Supabase 테이블 상수
# ──────────────────────────────────────────
TBL_DAILY   = "stock_daily"          # 종목별 일봉 (롤링 756일)
TBL_SCREEN  = "quant_screening_cache"  # 스크리닝 결과

ROLLING_DAYS = 756   # 유지할 최대 거래일 수 (≈3년)
MIN_DAYS     = 252   # 필터 계산 최소 거래일

# ──────────────────────────────────────────
# Supabase 일봉 적재 / 조회
# ──────────────────────────────────────────
def load_price_from_db(supabase, symbol: str) -> pd.DataFrame:
    """
    stock_daily 테이블에서 symbol 의 일봉을 불러와 DataFrame 반환.
    컬럼: date, open, high, low, close, volume
    """
    try:
        res = (supabase.table(TBL_DAILY)
               .select("date,open,high,low,close,volume")
               .eq("symbol", symbol)
               .order("date", desc=False)
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df.rename(columns={
            "open": "Open", "high": "High",
            "low": "Low", "close": "Close", "volume": "Volume"
        })
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["Close"])
    except Exception as e:
        print(f"[DB] {symbol} 조회 실패: {e}")
        return pd.DataFrame()


def upsert_daily_rows(supabase, symbol: str, name: str, rows: list[dict]):
    """
    rows: [{"date": "2024-01-02", "open":..., "high":..., "low":...,
             "close":..., "volume":...}, ...]
    중복 날짜는 upsert 로 덮어쓰기.
    """
    if not rows:
        return
    payload = [
        {**r, "symbol": symbol, "name": name}
        for r in rows
    ]
    # Supabase upsert (date + symbol 복합 PK 필요 → DDL 참고)
    supabase.table(TBL_DAILY).upsert(payload, on_conflict="symbol,date").execute()


def trim_old_rows(supabase, symbol: str):
    """
    ROLLING_DAYS 초과 오래된 행 삭제 (가장 오래된 날짜부터).
    """
    try:
        res = (supabase.table(TBL_DAILY)
               .select("date")
               .eq("symbol", symbol)
               .order("date", desc=False)
               .execute())
        dates = [r["date"] for r in res.data]
        excess = len(dates) - ROLLING_DAYS
        if excess > 0:
            cutoff = dates[excess - 1]   # 삭제할 마지막 날짜
            supabase.table(TBL_DAILY).delete()\
                .eq("symbol", symbol)\
                .lte("date", cutoff)\
                .execute()
    except Exception as e:
        print(f"[DB] {symbol} 오래된 행 삭제 실패: {e}")


# ──────────────────────────────────────────
# 6대 리스크 필터
# ──────────────────────────────────────────
#
# [1] 모멘텀        — Jegadeesh & Titman (1993)
# [2] 저변동성      — Ang et al. (2006)
# [3] MDD          — 실무 리스크 표준
# [4] 유동성        — Amihud (2002)
# [5] 거래량 모멘텀 — Lee & Swaminathan (2000)
# [6] 추세 강도     — Novy-Marx (2013) 변형
# ──────────────────────────────────────────

def filter_momentum(df: pd.DataFrame) -> dict:
    """[1] 12-1 모멘텀: 12개월 전 → 1개월 전 수익률 > 0"""
    if len(df) < MIN_DAYS:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    ret = (df["Close"].iloc[-21] - df["Close"].iloc[-252]) / df["Close"].iloc[-252] * 100
    return {
        "pass": ret > 0,
        "value": round(float(ret), 2),
        "reason": f"12-1 모멘텀 {ret:+.1f}%",
    }


def filter_volatility(df: pd.DataFrame) -> dict:
    """[2] 저변동성: 60일 연환산 표준편차 ≤ 60%"""
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    vol = df["Close"].pct_change().dropna().iloc[-60:].std() * np.sqrt(252) * 100
    return {
        "pass": vol <= 60.0,
        "value": round(float(vol), 2),
        "reason": f"연환산 변동성 {vol:.1f}% (기준 ≤60%)",
    }


def filter_mdd(df: pd.DataFrame) -> dict:
    """[3] MDD: 1년 내 최대낙폭 ≥ -25%"""
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    close = df["Close"].iloc[-252:] if len(df) >= 252 else df["Close"]
    mdd = ((close - close.cummax()) / close.cummax() * 100).min()
    return {
        "pass": mdd >= -25.0,
        "value": round(float(mdd), 2),
        "reason": f"1년 MDD {mdd:.1f}% (기준 ≥-25%)",
    }


def filter_liquidity(df: pd.DataFrame) -> dict:
    """[4] 유동성: 20일 일평균 거래대금 ≥ 50억"""
    if len(df) < 20 or "Volume" not in df.columns:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    avg_tv = (df["Close"] * df["Volume"]).iloc[-20:].mean()
    return {
        "pass": avg_tv >= 5_000_000_000,
        "value": round(float(avg_tv / 1e8), 1),
        "reason": f"일평균 거래대금 {avg_tv/1e8:.0f}억 (기준 ≥50억)",
    }


def filter_volume_momentum(df: pd.DataFrame) -> dict:
    """[5] 거래량 모멘텀: 5일 평균 거래량 / 60일 평균 거래량 ≥ 1.3"""
    if len(df) < 60 or "Volume" not in df.columns:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    vol60 = df["Volume"].iloc[-60:].mean()
    ratio = df["Volume"].iloc[-5:].mean() / vol60 if vol60 > 0 else 0
    return {
        "pass": ratio >= 1.3,
        "value": round(float(ratio), 3),
        "reason": f"거래량 비율 {ratio:.2f}x (기준 ≥1.3x)",
    }


def filter_trend_strength(df: pd.DataFrame) -> dict:
    """[6] 추세 강도: 60일 선형회귀 기울기 > 0 & R² ≥ 0.5"""
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    close = df["Close"].iloc[-60:].values
    x = np.arange(len(close))
    p = np.polyfit(x, close, 1)
    y_hat = np.polyval(p, x)
    ss_res = np.sum((close - y_hat) ** 2)
    ss_tot = np.sum((close - close.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    slope = p[0]
    return {
        "pass": slope > 0 and r2 >= 0.5,
        "value": round(float(slope), 4),
        "reason": f"기울기 {slope:+.2f}, R² {r2:.2f} (기준: >0, ≥0.5)",
    }


RISK_FILTERS = [
    ("모멘텀 (Jegadeesh & Titman 1993)",    filter_momentum),
    ("저변동성 (Ang et al. 2006)",           filter_volatility),
    ("MDD 리스크",                           filter_mdd),
    ("유동성 (Amihud 2002)",                 filter_liquidity),
    ("거래량 모멘텀 (Lee & Swaminathan 2000)", filter_volume_momentum),
    ("추세 강도 (Novy-Marx 2013 변형)",       filter_trend_strength),
]


# ──────────────────────────────────────────
# 스크리닝 엔진 (DB 데이터 기반)
# ──────────────────────────────────────────
def run_screening_from_db(supabase, universe_df: pd.DataFrame,
                          top_n: int = 10, log_fn=print) -> list:
    """
    universe_df: Symbol, Name, Market, Sector 컬럼 포함
    DB 에 저장된 일봉 데이터로 6필터 실행 → 모멘텀 상위 top_n 반환
    """
    results = []
    total = len(universe_df)

    for i, (_, row) in enumerate(universe_df.iterrows()):
        symbol = row["Symbol"]
        name   = row.get("Name", symbol)
        log_fn(f"[{i+1}/{total}] {name} 분석 중...")

        df = load_price_from_db(supabase, symbol)
        if df.empty or len(df) < 60:
            continue

        filter_results = {}
        all_pass = True
        for fname, ffn in RISK_FILTERS:
            res = ffn(df)
            filter_results[fname] = res
            if not res["pass"]:
                all_pass = False
                break   # 조기 탈락

        if not all_pass:
            continue

        mom_score  = filter_results[RISK_FILTERS[0][0]]["value"] or 0
        curr_price = int(df["Close"].iloc[-1])
        ret_1m     = (df["Close"].iloc[-1] - df["Close"].iloc[-21]) / df["Close"].iloc[-21] * 100 \
                     if len(df) >= 21 else 0.0

        results.append({
            "symbol":               symbol,
            "name":                 name,
            "sector":               row.get("Sector", "-"),
            "market":               row.get("Market", "-"),
            "current_price":        curr_price,
            "ret_1m":               round(float(ret_1m), 2),
            "momentum_score":       round(float(mom_score), 2),
            "annual_vol":           filter_results[RISK_FILTERS[1][0]]["value"],
            "mdd":                  filter_results[RISK_FILTERS[2][0]]["value"],
            "avg_trading_value_억": filter_results[RISK_FILTERS[3][0]]["value"],
            "vol_ratio":            filter_results[RISK_FILTERS[4][0]]["value"],
            "trend_slope":          filter_results[RISK_FILTERS[5][0]]["value"],
            "filter_details":       filter_results,
            "screened_at":          now_kst_str(),
        })

    results.sort(key=lambda x: x["momentum_score"], reverse=True)
    return results[:top_n]


# ──────────────────────────────────────────
# 스크리닝 결과 저장 / 조회
# ──────────────────────────────────────────
import json

def save_screening_result(supabase, results: list):
    import json
    supabase.table(TBL_SCREEN).upsert({
        "id":         1,
        "results":    json.dumps(results, ensure_ascii=False),
        "updated_at": now_kst_str(),
    }).execute()


def load_screening_result(supabase) -> tuple[list, str]:
    try:
        res = supabase.table(TBL_SCREEN).select("*").eq("id", 1).execute()
        if res.data:
            row = res.data[0]
            return json.loads(row["results"]), row.get("updated_at", "")
    except:
        pass
    return [], ""
