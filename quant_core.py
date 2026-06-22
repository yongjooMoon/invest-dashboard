"""
coreAi.py
─────────────────────────────────────────────
공용 유틸 + 유니버스 사전필터링 + 11대 필터 + 팩터 점수화

[사전필터 (5단계)]
  1. 시가총액 ≥ 300억
  2. 거래대금 ≥ 50억
  3. 보통주만 (ETF·스팩·리츠·우선주 제거)
  4. 상장 1년 이상
  5. 주가 ≥ 1,000원

[11대 필터]
  기술 7: 모멘텀, 저변동성, MDD, 유동성, 거래량모멘텀, 추세강도, 이평정배열
  펀더멘털 4: 실적모멘텀, 재무건전성, PER밸류, PBR밸류
"""

import json, re, time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import FinanceDataReader as fdr

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


class NumpyEncoder(json.JSONEncoder):
    """numpy 타입 → Python 네이티브 변환 (JSON 직렬화 오류 방지)"""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


# ──────────────────────────────────────────
# 테이블 상수
# ──────────────────────────────────────────
TBL_DAILY   = "stock_daily"
TBL_FUNDA   = "stock_fundamental"
TBL_SCREEN  = "quant_screening_cache"
TBL_WATCH   = "quant_watchlist_cache"

ROLLING_DAYS       = 756
MIN_DAYS           = 252
FUNDA_TTL_SEC      = 86400 * 90   # 90일 캐시
WATCHLIST_MIN_PASS = 8            # 11필터 중 8개 이상 → 워치리스트

# ──────────────────────────────────────────
# 사전 필터링 기준
# ──────────────────────────────────────────
PREFILTER_MARCAP_억        = 300     # 시가총액 300억 이상 (코스닥 포함 확대)
PREFILTER_TVOL_억          = 50      # 거래대금 50억 이상 (유동성 강화)
PREFILTER_MIN_PRICE        = 1000    # 주가 1000원 이상 (동전주 제거)
PREFILTER_MIN_LISTING_DAYS = 252     # 상장 1년 이상 (신규상장 노이즈 제거)

# 종목명 기반 제외 키워드 (ETF·스팩·리츠·ETN 등)
_EXCLUDE_NAME_KEYWORDS = [
    "스팩", "SPAC", "리츠", "REIT", "ETN",
    "인버스", "레버리지", "선물", "합병",
]


# ══════════════════════════════════════════
# [A] 유니버스 사전 필터링
# ══════════════════════════════════════════
def _normalize_listing(raw: pd.DataFrame, market: str) -> pd.DataFrame:
    col  = raw.columns.tolist()
    sym  = next((c for c in ["Symbol", "Code", "Ticker"] if c in col), None)
    name = next((c for c in ["Name", "종목명"]            if c in col), None)
    sec  = next((c for c in ["Sector", "Industry", "업종"] if c in col), None)
    cap  = next((c for c in ["Marcap", "시가총액"]          if c in col), None)
    close_col   = next((c for c in ["Close", "종가"]       if c in col), None)
    listing_col = next((c for c in ["ListingDate", "상장일"] if c in col), None)
    if not sym or not name:
        raise ValueError(f"필수 컬럼 없음: {col}")
    df = pd.DataFrame({
        "Symbol": raw[sym].astype(str).str.zfill(6),
        "Name":   raw[name].astype(str),
        "Market": market,
        "Sector": raw[sec].astype(str) if sec else "-",
        "Marcap": pd.to_numeric(raw[cap], errors="coerce") if cap else 0,
        "Close":  pd.to_numeric(raw[close_col], errors="coerce") if close_col else 0,
    })
    if listing_col:
        df["ListingDate"] = pd.to_datetime(raw[listing_col], errors="coerce")
    else:
        df["ListingDate"] = pd.NaT
    return df


def load_krx_trading_volume() -> dict:
    """
    KRX 정보데이터시스템에서 전종목 거래대금 조회 (무료, 인증 불필요)
    ★ 주말/공휴일 대응: 당일 데이터 없으면 최근 5영업일 역순 탐색
    """
    url     = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "http://data.krx.co.kr/"}

    for offset in range(0, 7):
        dt = now_kst() - timedelta(days=offset)
        if dt.weekday() >= 5:   # 토·일 스킵
            continue
        trd_dd = dt.strftime("%Y%m%d")
        try:
            data = {
                "bld":          "dbms/MDC/STAT/standard/MDCSTAT01501",
                "mktId":        "ALL",
                "trdDd":        trd_dd,
                "share":        "1",
                "money":        "1",
                "csvxls_isNo":  "false",
            }
            res  = requests.post(url, data=data, headers=headers, timeout=15)
            rows = res.json().get("OutBlock_1", [])
            if not rows:
                continue   # 이 날짜에 데이터 없음 → 이전 영업일 시도
            result = {}
            for r in rows:
                code = str(r.get("ISU_SRT_CD", "")).strip().zfill(6)
                tv   = float(str(r.get("ACC_TRDVAL", "0")).replace(",", "") or 0) / 1e8
                if code:
                    result[code] = tv
            print(f"[KRX] 거래대금 로드: {len(result)}개 (기준일: {trd_dd})")
            return result
        except Exception as e:
            print(f"[KRX] {trd_dd} 조회 실패: {e}")
            continue

    print("[KRX] 거래대금 로드 실패 — 최근 7일 내 유효 데이터 없음")
    return {}


def _is_common_stock(symbol: str, name: str) -> bool:
    """보통주 여부 판별"""
    if not symbol[-1].isdigit() or int(symbol[-1]) != 0:
        return False
    for kw in _EXCLUDE_NAME_KEYWORDS:
        if kw in name:
            return False
    if re.search(r"우[A-C]?$", name):
        return False
    return True


def load_filtered_universe(
    marcap_min_억: int = PREFILTER_MARCAP_억,
    tvol_min_억:   int = PREFILTER_TVOL_억,
) -> pd.DataFrame:
    print(f"[유니버스] KRX 전종목 로드 중...")
    kospi  = _normalize_listing(fdr.StockListing("KOSPI"),  "KOSPI")
    kosdaq = _normalize_listing(fdr.StockListing("KOSDAQ"), "KOSDAQ")
    raw_df = pd.concat([kospi, kosdaq], ignore_index=True)
    raw_df = raw_df[raw_df["Symbol"].str.len() == 6].dropna(subset=["Symbol", "Name"])
    total  = len(raw_df)

    common = raw_df[raw_df.apply(lambda r: _is_common_stock(r["Symbol"], r["Name"]), axis=1)].copy()
    cnt_common = len(common)

    marcap_원 = marcap_min_억 * 1e8
    cap_filtered = common[common["Marcap"] >= marcap_원].copy()
    cnt_cap = len(cap_filtered)

    cutoff_date = now_kst() - timedelta(days=PREFILTER_MIN_LISTING_DAYS)
    if cap_filtered["ListingDate"].notna().any():
        listing_filtered = cap_filtered[
            cap_filtered["ListingDate"].isna() |
            (cap_filtered["ListingDate"] <= cutoff_date)
        ].copy()
    else:
        listing_filtered = cap_filtered.copy()
    cnt_listing = len(listing_filtered)

    if "Close" in listing_filtered.columns and listing_filtered["Close"].sum() > 0:
        price_filtered = listing_filtered[
            (listing_filtered["Close"] <= 0) |
            (listing_filtered["Close"] >= PREFILTER_MIN_PRICE)
        ].copy()
    else:
        price_filtered = listing_filtered.copy()
    cnt_price = len(price_filtered)

    tvol_map = load_krx_trading_volume()
    if tvol_map:
        price_filtered["TradingVol억"] = price_filtered["Symbol"].map(tvol_map).fillna(0)
        final = price_filtered[price_filtered["TradingVol억"] >= tvol_min_억].copy()
    else:
        final = price_filtered.copy()
        final["TradingVol억"] = 0

    final = final.reset_index(drop=True)
    print(f"[유니버스] 전체 {total}개 → 보통주 {cnt_common}개 → "
          f"시총 {cnt_cap}개 → 상장1년 {cnt_listing}개 → "
          f"주가≥{PREFILTER_MIN_PRICE} {cnt_price}개 → "
          f"거래대금≥{tvol_min_억}억 {len(final)}개 (사전필터 완료)")
    return final


# ══════════════════════════════════════════
# [B] 일봉 DB 적재 / 조회
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
               .select("date").eq("symbol", symbol)
               .order("date", desc=False).execute())
        dates  = [r["date"] for r in res.data]
        excess = len(dates) - ROLLING_DAYS
        if excess > 0:
            supabase.table(TBL_DAILY).delete()\
                .eq("symbol", symbol).lte("date", dates[excess - 1]).execute()
    except Exception as e:
        print(f"[DB] {symbol} trim 실패: {e}")


# ══════════════════════════════════════════
# [C] 펀더멘털 (DART + 네이버금융)
# ══════════════════════════════════════════
def _parse_num(txt) -> float:
    if not txt:
        return 0.0
    try:
        return float(re.sub(r"[^\d.\-]", "", str(txt).replace(",", "")))
    except:
        return 0.0

def fetch_dart_financial(corp_code: str, dart_api_key: str, year: int = None) -> dict:
    if year is None:
        year = now_kst().year - 1
    url    = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    result = {"roe": None, "debt_ratio": None, "op_profit": None, "eps": None}
    for reprt_code in ["11011", "11012"]:
        try:
            params = {"crtfc_key": dart_api_key, "corp_code": corp_code,
                      "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": "CFS"}
            res = requests.get(url, params=params, timeout=10).json()
            if res.get("status") != "000":
                continue
            for item in res.get("list", []):
                acnt    = item.get("account_nm", "")
                val_raw = _parse_num(item.get("thstrm_amount", "0"))
                val_억  = val_raw / 1e8
                if "영업이익" in acnt and result["op_profit"] is None:
                    result["op_profit"] = val_억
                if "ROE" in acnt and result["roe"] is None:
                    result["roe"] = val_raw
                if "부채비율" in acnt and result["debt_ratio"] is None:
                    result["debt_ratio"] = val_raw
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
        url  = f"https://finance.naver.com/item/main.naver?code={symbol}"
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
    payload = {
        "symbol": symbol, "name": name,
        "roe":            data.get("roe"),
        "debt_ratio":     data.get("debt_ratio"),
        "op_profit_cur":  data.get("op_profit_cur"),
        "op_profit_prev": data.get("op_profit_prev"),
        "eps_cur":        data.get("eps_cur"),
        "eps_prev":       data.get("eps_prev"),
        "updated_at":     now_kst_str(),
    }
    if data.get("per") is not None:
        payload["per"] = data["per"]
    if data.get("pbr") is not None:
        payload["pbr"] = data["pbr"]
    try:
        supabase.table(TBL_FUNDA).upsert(payload).execute()
    except Exception as e:
        payload.pop("per", None)
        payload.pop("pbr", None)
        supabase.table(TBL_FUNDA).upsert(payload).execute()

def save_per_pbr(supabase, symbol: str, per: float | None, pbr: float | None):
    if per is None and pbr is None:
        return
    update = {}
    if per is not None: update["per"] = per
    if pbr is not None: update["pbr"] = pbr
    try:
        supabase.table(TBL_FUNDA).update(update).eq("symbol", symbol).execute()
    except:
        pass

def get_fundamental(supabase, symbol: str, name: str,
                    dart_api_key: str = "", dart_corp_map: dict = None) -> dict:
    cached = load_fundamental_from_db(supabase, symbol)
    if cached:
        return cached

    data = {"roe": None, "debt_ratio": None,
            "op_profit_cur": None, "op_profit_prev": None,
            "eps_cur": None, "eps_prev": None}

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

    if data["op_profit_cur"] is None or data["eps_cur"] is None:
        naver = fetch_naver_fundamental(symbol)
        for k, v in naver.items():
            if data.get(k) is None and v is not None:
                data[k] = v
        time.sleep(0.3)

    save_fundamental_to_db(supabase, symbol, name, data)
    return data


# ══════════════════════════════════════════
# [D] 11대 필터
# ══════════════════════════════════════════
def filter_momentum(df: pd.DataFrame) -> dict:
    if len(df) < MIN_DAYS:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    ret = (df["Close"].iloc[-21] - df["Close"].iloc[-252]) / df["Close"].iloc[-252] * 100
    return {"pass": bool(ret > 0), "value": round(float(ret), 2), "reason": f"12-1 모멘텀 {ret:+.1f}%"}

def filter_volatility(df: pd.DataFrame) -> dict:
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    vol = df["Close"].pct_change().dropna().iloc[-60:].std() * np.sqrt(252) * 100
    return {"pass": bool(vol <= 60.0), "value": round(float(vol), 2), "reason": f"연환산 변동성 {vol:.1f}%"}

def filter_mdd(df: pd.DataFrame) -> dict:
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    close = df["Close"].iloc[-252:] if len(df) >= 252 else df["Close"]
    mdd   = ((close - close.cummax()) / close.cummax() * 100).min()
    return {"pass": bool(mdd >= -25.0), "value": round(float(mdd), 2), "reason": f"1년 MDD {mdd:.1f}%"}

def filter_liquidity(df: pd.DataFrame) -> dict:
    if len(df) < 20 or "Volume" not in df.columns:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    avg_tv = (df["Close"] * df["Volume"]).iloc[-20:].mean()
    return {"pass": bool(avg_tv >= 5_000_000_000), "value": round(float(avg_tv / 1e8), 1),
            "reason": f"20일 평균 거래대금 {avg_tv/1e8:.0f}억"}

def filter_volume_momentum(df: pd.DataFrame) -> dict:
    if len(df) < 60 or "Volume" not in df.columns:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    vol60 = df["Volume"].iloc[-60:].mean()
    ratio = df["Volume"].iloc[-5:].mean() / vol60 if vol60 > 0 else 0
    return {"pass": bool(ratio >= 1.3), "value": round(float(ratio), 3), "reason": f"거래량 비율 {ratio:.2f}x"}

def filter_trend_strength(df: pd.DataFrame) -> dict:
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    close  = df["Close"].iloc[-60:].values
    x      = np.arange(len(close))
    p      = np.polyfit(x, close, 1)
    y_hat  = np.polyval(p, x)
    ss_res = np.sum((close - y_hat) ** 2)
    ss_tot = np.sum((close - close.mean()) ** 2)
    r2     = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    slope  = p[0]
    return {"pass": bool(slope > 0 and r2 >= 0.5), "value": round(float(slope), 4),
            "reason": f"기울기 {slope:+.2f}, R² {r2:.2f}"}

def filter_ma_alignment(df: pd.DataFrame) -> dict:
    if len(df) < 120:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    close = float(df["Close"].iloc[-1])
    ma20  = float(df["Close"].iloc[-20:].mean())
    ma60  = float(df["Close"].iloc[-60:].mean())
    ma120 = float(df["Close"].iloc[-120:].mean())
    ok    = close > ma20 > ma60 > ma120
    return {"pass": bool(ok), "value": round(close / ma20, 3) if ma20 > 0 else 0,
            "reason": f"{'정배열✅' if ok else '역배열❌'} — 종가/MA20 {close/ma20:.2f}"}

def filter_earnings_momentum(fund: dict) -> dict:
    op_cur   = fund.get("op_profit_cur")
    op_prev  = fund.get("op_profit_prev")
    eps_cur  = fund.get("eps_cur")
    eps_prev = fund.get("eps_prev")

    if op_cur is None and eps_cur is None:
        return {"pass": True, "value": None, "op_yoy": None, "eps_yoy": None, "reason": "펀더멘털 미수집"}

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
        "pass":    bool(op_pass and eps_pass),
        "value":   round(float(op_yoy), 2) if op_yoy is not None else None,
        "op_yoy":  round(float(op_yoy),  2) if op_yoy  is not None else None,
        "eps_yoy": round(float(eps_yoy), 2) if eps_yoy is not None else None,
        "reason":  " | ".join(parts) if parts else "데이터 없음",
    }

def filter_financial_health(fund: dict) -> dict:
    roe        = fund.get("roe")
    debt_ratio = fund.get("debt_ratio")

    if roe is None and debt_ratio is None:
        return {"pass": True, "value": None, "reason": "재무 미수집"}

    roe_pass  = roe        >= 5.0   if roe        is not None else True
    debt_pass = debt_ratio <= 200.0 if debt_ratio is not None else True

    parts = []
    if roe        is not None: parts.append(f"ROE {roe:.1f}%")
    if debt_ratio is not None: parts.append(f"부채비율 {debt_ratio:.0f}%")
    return {"pass": bool(roe_pass and debt_pass),
            "value": round(float(roe), 2) if roe is not None else None,
            "reason": " | ".join(parts)}

def filter_per_value(fund: dict) -> dict:
    per = fund.get("per")
    if per is None: return {"pass": True, "value": None, "reason": "PER 미수집"}
    per = float(per)
    return {"pass": bool(0 < per <= 20), "value": round(per, 2), "reason": f"PER {per:.1f}"}

def filter_pbr_value(fund: dict) -> dict:
    pbr = fund.get("pbr")
    if pbr is None: return {"pass": True, "value": None, "reason": "PBR 미수집"}
    pbr = float(pbr)
    return {"pass": bool(0 < pbr <= 3.0), "value": round(pbr, 2), "reason": f"PBR {pbr:.2f}"}


TECH_FILTERS = [
    ("모멘텀",       filter_momentum),
    ("저변동성",     filter_volatility),
    ("MDD",          filter_mdd),
    ("유동성",       filter_liquidity),
    ("거래량모멘텀", filter_volume_momentum),
    ("추세강도",     filter_trend_strength),
    ("이평정배열",   filter_ma_alignment),
]
FUNDA_FILTERS    = ["실적모멘텀", "재무건전성", "PER밸류", "PBR밸류"]
ALL_FILTER_NAMES = [n for n, _ in TECH_FILTERS] + FUNDA_FILTERS
TOTAL_FILTERS    = len(ALL_FILTER_NAMES)


# ══════════════════════════════════════════
# [E] 팩터 점수화 (0~100점)
# ══════════════════════════════════════════
def compute_factor_score(tech_results: dict, fund: dict) -> float:
    score = 0.0
    mom = tech_results.get("모멘텀", {}).get("value") or 0
    score += min(25.0, max(0.0, mom / 10.0))

    if (fund.get("op_profit_cur") or 0) > (fund.get("op_profit_prev") or 0): score += 10.0
    if (fund.get("eps_cur") or 0)       > (fund.get("eps_prev") or 0):       score += 10.0

    slope = tech_results.get("추세강도", {}).get("value") or 0
    if slope > 0: score += min(12.0, abs(slope) * 4.0)

    vr = tech_results.get("거래량모멘텀", {}).get("value") or 0
    score += min(8.0, max(0.0, (vr - 1.3) / 0.7 * 8.0))

    vol = tech_results.get("저변동성", {}).get("value") or 60
    score += max(0.0, (60.0 - vol) / 60.0 * 10.0)

    roe = fund.get("roe") or 0
    score += min(10.0, max(0.0, float(roe) / 3.0))

    per = fund.get("per")
    if per is not None:
        per = float(per)
        if 0 < per <= 20: score += max(0.0, (20.0 - per) / 20.0 * 8.0)

    pbr = fund.get("pbr")
    if pbr is not None:
        pbr = float(pbr)
        if 0 < pbr <= 3.0: score += max(0.0, (3.0 - pbr) / 3.0 * 4.0)

    if tech_results.get("이평정배열", {}).get("pass"):
        score += 3.0

    return round(score, 2)


# ══════════════════════════════════════════
# [F] 스크리닝 엔진
# ══════════════════════════════════════════
def run_screening_from_db(supabase, universe_df: pd.DataFrame,
                          top_n: int = 10, log_fn=print,
                          dart_api_key: str = "",
                          dart_corp_map: dict = None) -> tuple:
    confirmed, watchlist = [], []
    total = len(universe_df)

    for i, (_, row) in enumerate(universe_df.iterrows()):
        symbol = row["Symbol"]
        name   = row.get("Name", symbol)

        df = load_price_from_db(supabase, symbol)
        if df.empty or len(df) < 60:
            continue

        tech_results    = {}
        tech_pass_count = 0
        for fname, ffn in TECH_FILTERS:
            res = ffn(df)
            tech_results[fname] = res
            if res["pass"]:
                tech_pass_count += 1

        fund       = load_fundamental_from_db(supabase, symbol) or {}
        em_result  = filter_earnings_momentum(fund)
        fh_result  = filter_financial_health(fund)
        per_result = filter_per_value(fund)
        pbr_result = filter_pbr_value(fund)
        funda_pass = (int(em_result["pass"]) + int(fh_result["pass"])
                      + int(per_result["pass"]) + int(pbr_result["pass"]))

        total_pass = tech_pass_count + funda_pass
        if total_pass < WATCHLIST_MIN_PASS:
            continue

        factor_score = compute_factor_score(tech_results, fund)
        curr_price   = int(df["Close"].iloc[-1])
        ret_1m       = (df["Close"].iloc[-1] - df["Close"].iloc[-21]) \
                       / df["Close"].iloc[-21] * 100 if len(df) >= 21 else 0.0

        record = {
            "symbol":               symbol,
            "name":                 name,
            "sector":               row.get("Sector", "-"),
            "market":               row.get("Market", "-"),
            "marcap_억":            round(row.get("Marcap", 0) / 1e8, 0) if row.get("Marcap") else 0,
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
            "ma_aligned":           bool(tech_results["이평정배열"]["pass"]),
            "op_profit_yoy":        em_result.get("op_yoy"),
            "eps_yoy":              em_result.get("eps_yoy"),
            "roe":                  fund.get("roe"),
            "debt_ratio":           fund.get("debt_ratio"),
            "per":                  fund.get("per"),
            "pbr":                  fund.get("pbr"),
            "filter_details": {
                **tech_results,
                "실적모멘텀": em_result,
                "재무건전성": fh_result,
                "PER밸류":    per_result,
                "PBR밸류":    pbr_result,
            },
            "screened_at": now_kst_str(),
        }

        if total_pass == TOTAL_FILTERS:
            confirmed.append(record)
            log_fn(f"  ✅ ALL-PASS [{i+1}/{total}] {name} | 팩터점수: {factor_score}")
        else:
            watchlist.append(record)
            log_fn(f"  👀 WATCH {total_pass}/{TOTAL_FILTERS} [{i+1}/{total}] {name} | 팩터점수: {factor_score}")

    confirmed.sort(key=lambda x: x["factor_score"], reverse=True)
    watchlist.sort(key=lambda x: x["factor_score"], reverse=True)
    return confirmed[:top_n], watchlist[:top_n * 3]


# ══════════════════════════════════════════
# [G] 결과 저장 / 조회
# ══════════════════════════════════════════
def save_screening_result(supabase, confirmed: list, watchlist: list):
    ts = now_kst_str()
    supabase.table(TBL_SCREEN).upsert(
        {"id": 1, "results": json.dumps(confirmed, ensure_ascii=False, cls=NumpyEncoder), "updated_at": ts}
    ).execute()
    supabase.table(TBL_WATCH).upsert(
        {"id": 1, "results": json.dumps(watchlist, ensure_ascii=False, cls=NumpyEncoder), "updated_at": ts}
    ).execute()

def load_screening_result(supabase) -> tuple:
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
