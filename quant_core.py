"""
quant_core.py
─────────────────────────────────────────────
공용 유틸 + 8대 필터 + 팩터 점수화 스크리닝 엔진

Supabase 필요 테이블 DDL
──────────────────────────────────────────────
CREATE TABLE stock_daily (
    symbol TEXT NOT NULL, name TEXT,
    date DATE NOT NULL, open NUMERIC, high NUMERIC,
    low NUMERIC, close NUMERIC NOT NULL, volume BIGINT,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE stock_fundamental (
    symbol TEXT PRIMARY KEY, name TEXT,
    roe NUMERIC, debt_ratio NUMERIC,
    op_profit_cur NUMERIC, op_profit_prev NUMERIC,
    eps_cur NUMERIC, eps_prev NUMERIC, updated_at TEXT
);
CREATE TABLE quant_screening_cache (
    id INT PRIMARY KEY, results JSONB, updated_at TEXT
);
CREATE TABLE quant_watchlist_cache (
    id INT PRIMARY KEY, results JSONB, updated_at TEXT
);
"""

import json, re, time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ──────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────
def now_kst() -> datetime:
    return datetime.utcnow() + timedelta(hours=9)

def now_kst_str() -> str:
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")

def is_expired(ts_str: str, threshold_sec: int) -> bool:
    if not ts_str:
        return True
    try:
        clean = ts_str.replace("T", " ").split(".")[0].split("+")[0]
        dt = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
        return (now_kst() - dt).total_seconds() >= threshold_sec
    except:
        return True

# ──────────────────────────────────────────
# Supabase 테이블 상수
# ──────────────────────────────────────────
TBL_DAILY  = "stock_daily"
TBL_FUNDA  = "stock_fundamental"
TBL_SCREEN = "quant_screening_cache"
TBL_WATCH  = "quant_watchlist_cache"

ROLLING_DAYS      = 756       # 유지할 최대 거래일 (≈3년)
MIN_DAYS          = 252       # 필터 계산 최소 거래일
FUNDA_TTL_SEC     = 86400 * 90  # 펀더멘털 캐시 90일
WATCHLIST_MIN_PASS = 6        # watchlist 편입 최소 통과 필터 수


# ══════════════════════════════════════════
# [A] 일봉 DB 적재 / 조회
# ══════════════════════════════════════════
def load_price_from_db(supabase, symbol: str) -> pd.DataFrame:
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


def upsert_daily_rows(supabase, symbol: str, name: str, rows: list):
    if not rows:
        return
    payload = [{**r, "symbol": symbol, "name": name} for r in rows]
    supabase.table(TBL_DAILY).upsert(payload, on_conflict="symbol,date").execute()


def trim_old_rows(supabase, symbol: str):
    try:
        res = (supabase.table(TBL_DAILY)
               .select("date")
               .eq("symbol", symbol)
               .order("date", desc=False)
               .execute())
        dates = [r["date"] for r in res.data]
        excess = len(dates) - ROLLING_DAYS
        if excess > 0:
            cutoff = dates[excess - 1]
            supabase.table(TBL_DAILY).delete()\
                .eq("symbol", symbol).lte("date", cutoff).execute()
    except Exception as e:
        print(f"[DB] {symbol} trim 실패: {e}")


# ══════════════════════════════════════════
# [B] 펀더멘털 수집 (DART + 네이버금융)
# ══════════════════════════════════════════
def _parse_num(txt) -> float:
    if not txt:
        return 0.0
    cleaned = re.sub(r"[^\d.\-]", "", str(txt).replace(",", ""))
    try:
        return float(cleaned)
    except:
        return 0.0


def fetch_dart_financial(corp_code: str, dart_api_key: str, year: int = None) -> dict:
    if year is None:
        year = now_kst().year - 1
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    result = {"roe": None, "debt_ratio": None, "op_profit": None, "eps": None}
    for reprt_code in ["11011", "11012"]:
        try:
            params = {
                "crtfc_key": dart_api_key, "corp_code": corp_code,
                "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": "CFS",
            }
            res = requests.get(url, params=params, timeout=10).json()
            if res.get("status") != "000":
                continue
            for item in res.get("list", []):
                acnt    = item.get("account_nm", "")
                val     = _parse_num(item.get("thstrm_amount", "0")) / 1e8
                val_raw = _parse_num(item.get("thstrm_amount", "0"))
                if "영업이익" in acnt and result["op_profit"] is None:
                    result["op_profit"] = val
                if "ROE" in acnt and result["roe"] is None:
                    result["roe"] = val_raw
                if "부채비율" in acnt and result["debt_ratio"] is None:
                    result["debt_ratio"] = val_raw
            # EPS
            url_eps = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
            res_eps = requests.get(url_eps, params={**params, "fs_div": "OFS"}, timeout=10).json()
            for item in res_eps.get("list", []):
                if "주당순이익" in item.get("account_nm", ""):
                    result["eps"] = _parse_num(item.get("thstrm_amount", "0"))
                    break
            if any(v is not None for v in result.values()):
                break
        except:
            pass
    return result


def fetch_naver_fundamental(symbol: str) -> dict:
    result = {"roe": None, "debt_ratio": None,
              "op_profit_cur": None, "op_profit_prev": None,
              "eps_cur": None, "eps_prev": None}
    try:
        url = f"https://finance.naver.com/item/main.naver?code={symbol}"
        soup = BeautifulSoup(
            requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8).content,
            "html.parser"
        )
        table = soup.select_one("div.cop_analysis table")
        if table:
            for tr in table.select("tbody tr"):
                th = tr.select_one("th")
                if not th:
                    continue
                label = th.text.strip()
                tds   = tr.select("td")
                if len(tds) < 2:
                    continue
                prev_val = _parse_num(tds[-2].text)
                cur_val  = _parse_num(tds[-1].text)
                if "영업이익" in label:
                    result["op_profit_prev"] = prev_val
                    result["op_profit_cur"]  = cur_val
                elif "EPS" in label:
                    result["eps_prev"] = prev_val
                    result["eps_cur"]  = cur_val
                elif "ROE" in label:
                    result["roe"] = cur_val
        for th in soup.find_all("th"):
            if "부채비율" in th.text:
                td = th.find_next_sibling("td")
                if td:
                    result["debt_ratio"] = _parse_num(td.text)
                    break
    except:
        pass
    return result


def load_fundamental_from_db(supabase, symbol: str) -> dict | None:
    try:
        res = supabase.table(TBL_FUNDA).select("*").eq("symbol", symbol).execute()
        if res.data:
            row = res.data[0]
            if not is_expired(row.get("updated_at", ""), FUNDA_TTL_SEC):
                return row
    except:
        pass
    return None


def save_fundamental_to_db(supabase, symbol: str, name: str, data: dict):
    supabase.table(TBL_FUNDA).upsert({
        "symbol": symbol, "name": name,
        "roe":            data.get("roe"),
        "debt_ratio":     data.get("debt_ratio"),
        "op_profit_cur":  data.get("op_profit_cur"),
        "op_profit_prev": data.get("op_profit_prev"),
        "eps_cur":        data.get("eps_cur"),
        "eps_prev":       data.get("eps_prev"),
        "updated_at":     now_kst_str(),
    }).execute()


def get_fundamental(supabase, symbol: str, name: str,
                    dart_api_key: str = "", dart_corp_map: dict = None) -> dict:
    """캐시 우선 → DART → 네이버금융 순서로 펀더멘털 취득"""
    cached = load_fundamental_from_db(supabase, symbol)
    if cached:
        return cached

    data = {"roe": None, "debt_ratio": None,
            "op_profit_cur": None, "op_profit_prev": None,
            "eps_cur": None, "eps_prev": None}

    # DART 우선
    if dart_api_key and dart_corp_map:
        corp_code = dart_corp_map.get(symbol)
        if corp_code:
            cur  = fetch_dart_financial(corp_code, dart_api_key, now_kst().year - 1)
            prev = fetch_dart_financial(corp_code, dart_api_key, now_kst().year - 2)
            data.update({
                "op_profit_cur":  cur.get("op_profit"),
                "op_profit_prev": prev.get("op_profit"),
                "eps_cur":        cur.get("eps"),
                "eps_prev":       prev.get("eps"),
                "roe":            cur.get("roe"),
                "debt_ratio":     cur.get("debt_ratio"),
            })

    # 부족하면 네이버 크롤링 보완
    if data["op_profit_cur"] is None or data["eps_cur"] is None:
        naver = fetch_naver_fundamental(symbol)
        for k, v in naver.items():
            if data.get(k) is None and v is not None:
                data[k] = v
        time.sleep(0.3)

    save_fundamental_to_db(supabase, symbol, name, data)
    return data


# ══════════════════════════════════════════
# [C] 8대 필터
# ══════════════════════════════════════════
# 모든 필터는 {"pass": bool, "value": ..., "reason": str} 반환

def filter_momentum(df: pd.DataFrame) -> dict:
    """[1] 12-1 모멘텀 (Jegadeesh & Titman 1993): 12개월-1개월 수익률 > 0"""
    if len(df) < MIN_DAYS:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    ret = (df["Close"].iloc[-21] - df["Close"].iloc[-252]) / df["Close"].iloc[-252] * 100
    return {"pass": ret > 0, "value": round(float(ret), 2),
            "reason": f"12-1 모멘텀 {ret:+.1f}%"}


def filter_volatility(df: pd.DataFrame) -> dict:
    """[2] 저변동성 (Ang et al. 2006): 연환산 변동성 ≤ 60%"""
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    vol = df["Close"].pct_change().dropna().iloc[-60:].std() * np.sqrt(252) * 100
    return {"pass": vol <= 60.0, "value": round(float(vol), 2),
            "reason": f"연환산 변동성 {vol:.1f}% (기준 ≤60%)"}


def filter_mdd(df: pd.DataFrame) -> dict:
    """[3] MDD: 1년 내 최대낙폭 ≥ -25%"""
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    close = df["Close"].iloc[-252:] if len(df) >= 252 else df["Close"]
    mdd = ((close - close.cummax()) / close.cummax() * 100).min()
    return {"pass": mdd >= -25.0, "value": round(float(mdd), 2),
            "reason": f"1년 MDD {mdd:.1f}% (기준 ≥-25%)"}


def filter_liquidity(df: pd.DataFrame) -> dict:
    """[4] 유동성 (Amihud 2002): 20일 일평균 거래대금 ≥ 50억"""
    if len(df) < 20 or "Volume" not in df.columns:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    avg_tv = (df["Close"] * df["Volume"]).iloc[-20:].mean()
    return {"pass": avg_tv >= 5_000_000_000, "value": round(float(avg_tv / 1e8), 1),
            "reason": f"일평균 거래대금 {avg_tv/1e8:.0f}억 (기준 ≥50억)"}


def filter_volume_momentum(df: pd.DataFrame) -> dict:
    """[5] 거래량 모멘텀 (Lee & Swaminathan 2000): 5일/60일 ≥ 1.3x"""
    if len(df) < 60 or "Volume" not in df.columns:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    vol60 = df["Volume"].iloc[-60:].mean()
    ratio = df["Volume"].iloc[-5:].mean() / vol60 if vol60 > 0 else 0
    return {"pass": ratio >= 1.3, "value": round(float(ratio), 3),
            "reason": f"거래량 비율 {ratio:.2f}x (기준 ≥1.3x)"}


def filter_trend_strength(df: pd.DataFrame) -> dict:
    """[6] 추세 강도 (Novy-Marx 2013 변형): 60일 기울기>0, R²≥0.5"""
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    close = df["Close"].iloc[-60:].values
    x     = np.arange(len(close))
    p     = np.polyfit(x, close, 1)
    y_hat = np.polyval(p, x)
    ss_res = np.sum((close - y_hat) ** 2)
    ss_tot = np.sum((close - close.mean()) ** 2)
    r2     = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    slope  = p[0]
    return {"pass": slope > 0 and r2 >= 0.5, "value": round(float(slope), 4),
            "reason": f"기울기 {slope:+.2f}, R² {r2:.2f} (기준: >0, ≥0.5)"}


def filter_earnings_momentum(fund: dict) -> dict:
    """[7] 실적 모멘텀: 영업이익 YoY > 0 AND EPS YoY > 0"""
    op_cur  = fund.get("op_profit_cur")
    op_prev = fund.get("op_profit_prev")
    eps_cur  = fund.get("eps_cur")
    eps_prev = fund.get("eps_prev")

    # 데이터 미수집 → 중립 통과
    if op_cur is None and eps_cur is None:
        return {"pass": True, "value": None, "op_yoy": None, "eps_yoy": None,
                "reason": "펀더멘털 미수집 — 중립 통과"}

    op_yoy, eps_yoy = None, None
    op_pass, eps_pass = True, True

    if op_cur is not None and op_prev not in (None, 0):
        op_yoy  = (op_cur - op_prev) / abs(op_prev) * 100
        op_pass = op_yoy > 0

    if eps_cur is not None and eps_prev not in (None, 0):
        eps_yoy  = (eps_cur - eps_prev) / abs(eps_prev) * 100
        eps_pass = eps_yoy > 0

    parts = []
    if op_yoy  is not None: parts.append(f"영업이익YoY {op_yoy:+.1f}%")
    if eps_yoy is not None: parts.append(f"EPS YoY {eps_yoy:+.1f}%")

    return {
        "pass":    op_pass and eps_pass,
        "value":   round(float(op_yoy), 2) if op_yoy is not None else None,
        "op_yoy":  round(float(op_yoy),  2) if op_yoy  is not None else None,
        "eps_yoy": round(float(eps_yoy), 2) if eps_yoy is not None else None,
        "reason":  (" | ".join(parts) if parts else "데이터 없음") + " (기준: 둘 다 >0)",
    }


def filter_financial_health(fund: dict) -> dict:
    """[8] 재무 건전성: ROE ≥ 5%, 부채비율 ≤ 200%"""
    roe        = fund.get("roe")
    debt_ratio = fund.get("debt_ratio")

    if roe is None and debt_ratio is None:
        return {"pass": True, "value": None, "reason": "재무 미수집 — 중립 통과"}

    roe_pass  = roe        >= 5.0   if roe        is not None else True
    debt_pass = debt_ratio <= 200.0 if debt_ratio is not None else True

    parts = []
    if roe        is not None: parts.append(f"ROE {roe:.1f}% (기준 ≥5%)")
    if debt_ratio is not None: parts.append(f"부채비율 {debt_ratio:.0f}% (기준 ≤200%)")

    return {
        "pass":   roe_pass and debt_pass,
        "value":  round(float(roe), 2) if roe is not None else None,
        "reason": " | ".join(parts),
    }


# 필터 목록 — 순서 중요 (인덱스로 참조)
TECH_FILTERS = [
    ("모멘텀",       filter_momentum),
    ("저변동성",     filter_volatility),
    ("MDD",          filter_mdd),
    ("유동성",       filter_liquidity),
    ("거래량모멘텀", filter_volume_momentum),
    ("추세강도",     filter_trend_strength),
]
FUNDA_FILTERS = ["실적모멘텀", "재무건전성"]
ALL_FILTER_NAMES = [n for n, _ in TECH_FILTERS] + FUNDA_FILTERS


# ══════════════════════════════════════════
# [D] 팩터 점수화 (0~100점)
# 모멘텀 30 · 실적모멘텀 25 · 추세강도 15 · 거래량 10 · 변동성 10 · ROE 10
# ══════════════════════════════════════════
def compute_factor_score(tech_results: dict, fund: dict) -> float:
    score = 0.0

    # 1. 모멘텀 (30점)
    mom = tech_results.get("모멘텀", {}).get("value") or 0
    score += min(30.0, max(0.0, mom / 10.0))

    # 2. 실적 모멘텀 (25점)
    op_diff  = (fund.get("op_profit_cur") or 0) - (fund.get("op_profit_prev") or 0)
    eps_diff = (fund.get("eps_cur") or 0)       - (fund.get("eps_prev") or 0)
    if op_diff  > 0: score += 12.5
    if eps_diff > 0: score += 12.5

    # 3. 추세 강도 (15점)
    slope = tech_results.get("추세강도", {}).get("value") or 0
    score += min(15.0, max(0.0, abs(slope) * 5.0)) if slope > 0 else 0

    # 4. 거래량 모멘텀 (10점)
    vr = tech_results.get("거래량모멘텀", {}).get("value") or 0
    score += min(10.0, max(0.0, (vr - 1.3) / 0.7 * 10.0))

    # 5. 저변동성 (10점) — 낮을수록 높은 점수
    vol = tech_results.get("저변동성", {}).get("value") or 60
    score += max(0.0, (60.0 - vol) / 60.0 * 10.0)

    # 6. ROE (10점)
    roe = fund.get("roe") or 0
    score += min(10.0, max(0.0, roe / 3.0))

    return round(score, 2)


# ══════════════════════════════════════════
# [E] 스크리닝 엔진
# ══════════════════════════════════════════
def run_screening_from_db(supabase, universe_df: pd.DataFrame,
                          top_n: int = 10, log_fn=print,
                          dart_api_key: str = "",
                          dart_corp_map: dict = None) -> tuple:
    """
    전종목 8필터 실행.
    반환: (confirmed_list, watchlist)
      confirmed : 8/8 ALL-PASS → 팩터점수 상위 top_n
      watchlist : WATCHLIST_MIN_PASS(6~7)개 통과 → 최대 top_n*3
    """
    confirmed = []
    watchlist = []
    total     = len(universe_df)

    for i, (_, row) in enumerate(universe_df.iterrows()):
        symbol = row["Symbol"]
        name   = row.get("Name", symbol)

        # 가격 데이터 로드
        df = load_price_from_db(supabase, symbol)
        if df.empty or len(df) < 60:
            continue

        # ── 기술적 6필터 (전부 계산, 조기탈락 없음) ──
        tech_results = {}
        tech_pass_count = 0
        for fname, ffn in TECH_FILTERS:
            res = ffn(df)
            tech_results[fname] = res
            if res["pass"]:
                tech_pass_count += 1

        # ── 펀더멘털 2필터 (DB 캐시만 사용, 실시간 호출 없음) ──
        fund = load_fundamental_from_db(supabase, symbol) or {}
        em_result = filter_earnings_momentum(fund)
        fh_result = filter_financial_health(fund)
        funda_pass_count = int(em_result["pass"]) + int(fh_result["pass"])

        total_pass = tech_pass_count + funda_pass_count  # 최대 8

        # 6개 미만 → 완전 탈락
        if total_pass < WATCHLIST_MIN_PASS:
            continue

        # 팩터 점수 계산
        factor_score = compute_factor_score(tech_results, fund)

        # 결과 레코드 구성
        curr_price = int(df["Close"].iloc[-1])
        ret_1m = (df["Close"].iloc[-1] - df["Close"].iloc[-21]) / df["Close"].iloc[-21] * 100 \
                 if len(df) >= 21 else 0.0

        filter_details = {
            **tech_results,
            "실적모멘텀": em_result,
            "재무건전성": fh_result,
        }

        record = {
            "symbol":               symbol,
            "name":                 name,
            "sector":               row.get("Sector", "-"),
            "market":               row.get("Market", "-"),
            "current_price":        curr_price,
            "ret_1m":               round(float(ret_1m), 2),
            "momentum_score":       tech_results["모멘텀"]["value"] or 0,
            "factor_score":         factor_score,
            "pass_count":           total_pass,
            "annual_vol":           tech_results["저변동성"]["value"],
            "mdd":                  tech_results["MDD"]["value"],
            "avg_trading_value_억": tech_results["유동성"]["value"],
            "vol_ratio":            tech_results["거래량모멘텀"]["value"],
            "trend_slope":          tech_results["추세강도"]["value"],
            "op_profit_yoy":        em_result.get("op_yoy"),
            "eps_yoy":              em_result.get("eps_yoy"),
            "roe":                  fund.get("roe"),
            "debt_ratio":           fund.get("debt_ratio"),
            "filter_details":       filter_details,
            "screened_at":          now_kst_str(),
        }

        if total_pass == 8:
            confirmed.append(record)
            log_fn(f"[{i+1}/{total}] ✅ ALL-PASS {name} ({symbol}) | 팩터점수: {factor_score}")
        else:
            watchlist.append(record)
            log_fn(f"[{i+1}/{total}] 👀 WATCH {total_pass}/8 {name} ({symbol}) | 팩터점수: {factor_score}")

    confirmed.sort(key=lambda x: x["factor_score"], reverse=True)
    watchlist.sort(key=lambda x: x["factor_score"], reverse=True)
    return confirmed[:top_n], watchlist[:top_n * 3]


# ══════════════════════════════════════════
# [F] 결과 저장 / 조회
# ══════════════════════════════════════════
def save_screening_result(supabase, confirmed: list, watchlist: list):
    ts = now_kst_str()
    supabase.table(TBL_SCREEN).upsert({
        "id": 1, "results": json.dumps(confirmed, ensure_ascii=False), "updated_at": ts,
    }).execute()
    supabase.table(TBL_WATCH).upsert({
        "id": 1, "results": json.dumps(watchlist, ensure_ascii=False), "updated_at": ts,
    }).execute()


def load_screening_result(supabase) -> tuple:
    """(confirmed, watchlist, updated_at) 반환"""
    confirmed, watchlist, updated_at = [], [], ""
    try:
        r1 = supabase.table(TBL_SCREEN).select("*").eq("id", 1).execute()
        if r1.data:
            confirmed  = json.loads(r1.data[0]["results"])
            updated_at = r1.data[0].get("updated_at", "")
    except:
        pass
    try:
        r2 = supabase.table(TBL_WATCH).select("*").eq("id", 1).execute()
        if r2.data:
            watchlist = json.loads(r2.data[0]["results"])
    except:
        pass
    return confirmed, watchlist, updated_at
