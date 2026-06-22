"""
coreAi.py
─────────────────────────────────────────────
기관급 퀀트 필터 시스템 (AQR, JP Morgan 방식)

[사전필터 (엄격한 정제)]
  1. 보통주만 (스팩, 우선주, ETF, ETN, 리츠 등 완벽 제거)
  2. 시가총액 ≥ 1,500억 (노이즈 소형주 제거)
  3. 거래대금 ≥ 50억 (기관 유동성 하한선)
  4. 상장 1년 이상
  5. 주가 ≥ 1,000원

[퀄리티/모멘텀/수급 필터]
  - 재무: ROE > 8%, 부채비율 < 150%, 영업이익 > 0 (적자 제외), 영업이익 YoY > 0
  - 모멘텀: 12-1 모멘텀 (과거 11개월 누적 수익률, 최근 1개월 제외)
  - 수급: 외국인 또는 기관 20일 순매수 > 0
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
# 사전 필터링 기준 (기관급)
# ──────────────────────────────────────────
PREFILTER_MARCAP_억        = 1500    # 시가총액 1500억 이상
PREFILTER_TVOL_억          = 50      # 거래대금 50억 이상
PREFILTER_MIN_PRICE        = 1000    # 주가 1000원 이상
PREFILTER_MIN_LISTING_DAYS = 252     # 상장 1년 이상

_EXCLUDE_NAME_KEYWORDS = [
    "스팩", "SPAC", "리츠", "REIT", "ETN", "ETF",
    "인버스", "레버리지", "선물", "합병", "홀딩스", "지주"
]


# ══════════════════════════════════════════
# [A] 수급 및 유니버스 필터링
# ══════════════════════════════════════════
def load_krx_trading_volume() -> dict:
    """KRX 당일(또는 최근 영업일) 전종목 거래대금(억원)"""
    url     = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "http://data.krx.co.kr/"}

    for offset in range(0, 7):
        dt = now_kst() - timedelta(days=offset)
        if dt.weekday() >= 5: continue
        trd_dd = dt.strftime("%Y%m%d")
        try:
            data = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT01501", "mktId": "ALL",
                "trdDd": trd_dd, "share": "1", "money": "1", "csvxls_isNo": "false"
            }
            res = requests.post(url, data=data, headers=headers, timeout=15)
            rows = res.json().get("OutBlock_1", [])
            if not rows: continue
            result = {}
            for r in rows:
                code = str(r.get("ISU_SRT_CD", "")).strip().zfill(6)
                tv   = float(str(r.get("ACC_TRDVAL", "0")).replace(",", "") or 0) / 1e8
                if code: result[code] = tv
            print(f"[KRX] 거래대금 로드: {len(result)}개 (기준일: {trd_dd})")
            return result
        except: continue
    return {}

def load_krx_net_buy(start_date: str, end_date: str) -> tuple:
    """KRX 사이트 차단으로 인해 수급 데이터를 임시 비활성화합니다."""
    # 향후 KIS API나 pykrx 업데이트 시 복구 예정
    return {}, {}

def _is_common_stock(symbol: str, name: str) -> bool:
    """보통주 여부 엄격 판별"""
    if not symbol[-1].isdigit() or int(symbol[-1]) != 0:
        return False
    for kw in _EXCLUDE_NAME_KEYWORDS:
        if kw in name:
            return False
    if re.search(r"우[A-C]?$", name) or name.endswith("우"):
        return False
    return True

def _normalize_listing(raw: pd.DataFrame, market: str) -> pd.DataFrame:
    col = raw.columns.tolist()
    sym = next((c for c in ["Symbol", "Code", "Ticker"] if c in col), None)
    name = next((c for c in ["Name", "종목명"] if c in col), None)
    cap = next((c for c in ["Marcap", "시가총액"] if c in col), None)
    close_col = next((c for c in ["Close", "종가"] if c in col), None)
    list_col = next((c for c in ["ListingDate", "상장일"] if c in col), None)
    
    df = pd.DataFrame({
        "Symbol": raw[sym].astype(str).str.zfill(6),
        "Name":   raw[name].astype(str),
        "Market": market,
        "Marcap": pd.to_numeric(raw[cap], errors="coerce") if cap else 0,
        "Close":  pd.to_numeric(raw[close_col], errors="coerce") if close_col else 0,
    })
    if list_col: df["ListingDate"] = pd.to_datetime(raw[list_col], errors="coerce")
    else: df["ListingDate"] = pd.NaT
    return df

def load_filtered_universe(
    marcap_min_억: int = PREFILTER_MARCAP_억,
    tvol_min_억:   int = PREFILTER_TVOL_억,
) -> pd.DataFrame:
    print(f"[유니버스] KRX 전종목 로드 중...")
    kospi  = _normalize_listing(fdr.StockListing("KOSPI"),  "KOSPI")
    kosdaq = _normalize_listing(fdr.StockListing("KOSDAQ"), "KOSDAQ")
    raw_df = pd.concat([kospi, kosdaq], ignore_index=True)
    raw_df = raw_df[raw_df["Symbol"].str.len() == 6].dropna(subset=["Symbol", "Name"])
    total = len(raw_df)

    common = raw_df[raw_df.apply(lambda r: _is_common_stock(r["Symbol"], r["Name"]), axis=1)].copy()
    cnt_common = len(common)

    marcap_원 = marcap_min_억 * 1e8
    cap_filtered = common[common["Marcap"] >= marcap_원].copy()
    cnt_cap = len(cap_filtered)

    cutoff_date = now_kst() - timedelta(days=PREFILTER_MIN_LISTING_DAYS)
    if cap_filtered["ListingDate"].notna().any():
        list_filtered = cap_filtered[
            cap_filtered["ListingDate"].isna() | (cap_filtered["ListingDate"] <= cutoff_date)
        ].copy()
    else: list_filtered = cap_filtered.copy()

    price_filtered = list_filtered[
        (list_filtered["Close"] <= 0) | (list_filtered["Close"] >= PREFILTER_MIN_PRICE)
    ].copy()

    tvol_map = load_krx_trading_volume()
    if tvol_map:
        price_filtered["TradingVol억"] = price_filtered["Symbol"].map(tvol_map).fillna(0)
        final = price_filtered[price_filtered["TradingVol억"] >= tvol_min_억].copy()
    else:
        final = price_filtered.copy()
        final["TradingVol억"] = 0

    # 수급 데이터 (최근 30일치 달력일 기준, 약 20영업일) 병합
    start_dt = now_kst() - timedelta(days=30)
    end_dt   = now_kst()
    f_map, i_map = load_krx_net_buy(start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"))
    final["ForeignNetBuy"] = final["Symbol"].map(f_map).fillna(0)
    final["InstNetBuy"]    = final["Symbol"].map(i_map).fillna(0)

    final = final.reset_index(drop=True)
    print(f"[유니버스] 전체 {total}개 → 보통주 {cnt_common}개 → "
          f"시총≥{marcap_min_억}억 {cnt_cap}개 → "
          f"거래대금≥{tvol_min_억}억 최종 {len(final)}개 (사전필터 완료)")
    return final


# ══════════════════════════════════════════
# [B] DB 적재 / 조회 및 펀더멘털
# ══════════════════════════════════════════
def load_price_from_db(supabase, symbol: str) -> pd.DataFrame:
    try:
        res = supabase.table(TBL_DAILY).select("date,open,high,low,close,volume").eq("symbol", symbol).order("date").execute()
        if not res.data: return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        for col in df.columns: df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["Close"])
    except: return pd.DataFrame()

def upsert_daily_rows(supabase, symbol: str, name: str, rows: list):
    if rows: supabase.table(TBL_DAILY).upsert([{**r, "symbol": symbol, "name": name} for r in rows], on_conflict="symbol,date").execute()

def trim_old_rows(supabase, symbol: str):
    try:
        res = supabase.table(TBL_DAILY).select("date").eq("symbol", symbol).order("date").execute()
        if len(res.data) > ROLLING_DAYS:
            supabase.table(TBL_DAILY).delete().eq("symbol", symbol).lte("date", res.data[len(res.data)-ROLLING_DAYS-1]["date"]).execute()
    except: pass

def _parse_num(txt) -> float:
    try: return float(re.sub(r"[^\d.\-]", "", str(txt).replace(",", "")))
    except: return 0.0

def fetch_dart_financial(corp_code: str, dart_api_key: str, year: int = None) -> dict:
    if year is None: year = now_kst().year - 1
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    result = {"roe": None, "debt_ratio": None, "op_profit": None, "eps": None}
    for rcode in ["11011", "11012"]:
        try:
            res = requests.get(url, params={"crtfc_key":dart_api_key,"corp_code":corp_code,"bsns_year":str(year),"reprt_code":rcode,"fs_div":"CFS"}, timeout=10).json()
            if res.get("status") != "000": continue
            for item in res.get("list", []):
                acnt = item.get("account_nm", "")
                v = _parse_num(item.get("thstrm_amount", "0"))
                if "영업이익" in acnt and result["op_profit"] is None: result["op_profit"] = v/1e8
                if "ROE" in acnt and result["roe"] is None: result["roe"] = v
                if "부채비율" in acnt and result["debt_ratio"] is None: result["debt_ratio"] = v
            eps_res = requests.get("https://opendart.fss.or.kr/api/fnlttSinglAcnt.json", params={"crtfc_key":dart_api_key,"corp_code":corp_code,"bsns_year":str(year),"reprt_code":rcode,"fs_div":"OFS"}, timeout=10).json()
            for item in eps_res.get("list", []):
                if "주당순이익" in item.get("account_nm", ""):
                    result["eps"] = _parse_num(item.get("thstrm_amount", "0")); break
            if any(v is not None for v in result.values()): break
        except: pass
    return result

def fetch_naver_fundamental(symbol: str) -> dict:
    result = {"roe": None, "debt_ratio": None, "op_profit_cur": None, "op_profit_prev": None, "eps_cur": None, "eps_prev": None}
    try:
        soup = BeautifulSoup(requests.get(f"https://finance.naver.com/item/main.naver?code={symbol}", headers={"User-Agent":"Mozilla"}, timeout=8).content, "html.parser")
        tbl = soup.select_one("div.cop_analysis table")
        if tbl:
            for tr in tbl.select("tbody tr"):
                th = tr.select_one("th")
                if not th: continue
                lbl = th.text.strip()
                tds = tr.select("td")
                if len(tds) < 2: continue
                pv, cv = _parse_num(tds[-2].text), _parse_num(tds[-1].text)
                if "영업이익" in lbl: result["op_profit_prev"], result["op_profit_cur"] = pv, cv
                elif "EPS" in lbl: result["eps_prev"], result["eps_cur"] = pv, cv
                elif "ROE" in lbl: result["roe"] = cv
        for th in soup.find_all("th"):
            if "부채비율" in th.text:
                td = th.find_next_sibling("td")
                if td: result["debt_ratio"] = _parse_num(td.text); break
    except: pass
    return result

def load_fundamental_from_db(supabase, symbol: str) -> dict | None:
    try:
        res = supabase.table(TBL_FUNDA).select("*").eq("symbol", symbol).execute()
        if res.data and not is_expired(res.data[0].get("updated_at", ""), FUNDA_TTL_SEC): return res.data[0]
    except: pass
    return None

def save_fundamental_to_db(supabase, symbol: str, name: str, data: dict):
    payload = {"symbol": symbol, "name": name, "roe": data.get("roe"), "debt_ratio": data.get("debt_ratio"), "op_profit_cur": data.get("op_profit_cur"), "op_profit_prev": data.get("op_profit_prev"), "eps_cur": data.get("eps_cur"), "eps_prev": data.get("eps_prev"), "updated_at": now_kst_str()}
    if data.get("per") is not None: payload["per"] = data["per"]
    if data.get("pbr") is not None: payload["pbr"] = data["pbr"]
    try: supabase.table(TBL_FUNDA).upsert(payload).execute()
    except:
        payload.pop("per", None); payload.pop("pbr", None)
        supabase.table(TBL_FUNDA).upsert(payload).execute()

def save_per_pbr(supabase, symbol: str, per: float | None, pbr: float | None):
    update = {}
    if per is not None: update["per"] = per
    if pbr is not None: update["pbr"] = pbr
    if update:
        try: supabase.table(TBL_FUNDA).update(update).eq("symbol", symbol).execute()
        except: pass

def get_fundamental(supabase, symbol: str, name: str, dart_api_key: str = "", dart_corp_map: dict = None) -> dict:
    cached = load_fundamental_from_db(supabase, symbol)
    if cached: return cached
    data = {"roe": None, "debt_ratio": None, "op_profit_cur": None, "op_profit_prev": None, "eps_cur": None, "eps_prev": None}
    if dart_api_key and dart_corp_map and (corp_code := dart_corp_map.get(symbol)):
        cur  = fetch_dart_financial(corp_code, dart_api_key, now_kst().year - 1)
        prev = fetch_dart_financial(corp_code, dart_api_key, now_kst().year - 2)
        data.update({"op_profit_cur": cur.get("op_profit"), "op_profit_prev": prev.get("op_profit"), "eps_cur": cur.get("eps"), "eps_prev": prev.get("eps"), "roe": cur.get("roe"), "debt_ratio": cur.get("debt_ratio")})
    if data["op_profit_cur"] is None or data["eps_cur"] is None:
        naver = fetch_naver_fundamental(symbol)
        for k, v in naver.items():
            if data.get(k) is None and v is not None: data[k] = v
        time.sleep(0.3)
    save_fundamental_to_db(supabase, symbol, name, data)
    return data


# ══════════════════════════════════════════
# [D] 기관급 11대 필터 로직
# ══════════════════════════════════════════
def filter_momentum_12_1(df: pd.DataFrame) -> dict:
    """[1] 12-1 모멘텀: 최근 1개월을 제외한 과거 11개월 누적 수익률"""
    if len(df) < MIN_DAYS:
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    # -252일 종가 대비 -21일 종가 수익률
    ret = (df["Close"].iloc[-21] - df["Close"].iloc[-252]) / df["Close"].iloc[-252] * 100
    return {"pass": bool(ret > 0), "value": round(float(ret), 2), "reason": f"12-1 모멘텀 {ret:+.1f}%"}

def filter_volatility(df: pd.DataFrame) -> dict:
    if len(df) < 60: return {"pass": False, "value": None, "reason": "데이터 부족"}
    vol = df["Close"].pct_change().dropna().iloc[-60:].std() * np.sqrt(252) * 100
    return {"pass": bool(vol <= 60.0), "value": round(float(vol), 2), "reason": f"변동성 {vol:.1f}% (<60%)"}

def filter_mdd(df: pd.DataFrame) -> dict:
    if len(df) < 60: return {"pass": False, "value": None, "reason": "데이터 부족"}
    close = df["Close"].iloc[-252:] if len(df) >= 252 else df["Close"]
    mdd   = ((close - close.cummax()) / close.cummax() * 100).min()
    return {"pass": bool(mdd >= -25.0), "value": round(float(mdd), 2), "reason": f"MDD {mdd:.1f}% (>-25%)"}

def filter_liquidity(df: pd.DataFrame) -> dict:
    if len(df) < 20 or "Volume" not in df.columns: return {"pass": False, "value": None, "reason": "데이터 부족"}
    avg_tv = (df["Close"] * df["Volume"]).iloc[-20:].mean()
    return {"pass": bool(avg_tv >= 5_000_000_000), "value": round(float(avg_tv / 1e8), 1), "reason": f"거래대금 {avg_tv/1e8:.0f}억"}

def filter_volume_momentum(df: pd.DataFrame) -> dict:
    if len(df) < 60 or "Volume" not in df.columns: return {"pass": False, "value": None, "reason": "데이터 부족"}
    vol60 = df["Volume"].iloc[-60:].mean()
    ratio = df["Volume"].iloc[-5:].mean() / vol60 if vol60 > 0 else 0
    return {"pass": bool(ratio >= 1.3), "value": round(float(ratio), 3), "reason": f"거래량 비율 {ratio:.2f}x"}

def filter_trend_strength(df: pd.DataFrame) -> dict:
    if len(df) < 60: return {"pass": False, "value": None, "reason": "데이터 부족"}
    close = df["Close"].iloc[-60:].values
    x = np.arange(len(close)); p = np.polyfit(x, close, 1)
    r2 = 1 - np.sum((close - np.polyval(p, x))**2) / np.sum((close - close.mean())**2) if np.sum((close - close.mean())**2) > 0 else 0
    return {"pass": bool(p[0] > 0 and r2 >= 0.5), "value": round(float(p[0]), 4), "reason": f"추세 R² {r2:.2f}"}

def filter_supply_demand(foreign_buy: float, inst_buy: float) -> dict:
    """[7] 수급 필터: 외국인 또는 기관의 20일 순매수 우위"""
    ok = (foreign_buy > 0) or (inst_buy > 0)
    return {"pass": bool(ok), "value": round(foreign_buy + inst_buy, 1), 
            "reason": f"외국인 {foreign_buy:+.1f}억, 기관 {inst_buy:+.1f}억"}

def filter_earnings_momentum(fund: dict) -> dict:
    """[8] 실적: 영업이익 > 0 (흑자) AND 영업이익 YoY > 0"""
    op_cur = fund.get("op_profit_cur")
    op_prev = fund.get("op_profit_prev")
    if op_cur is None: return {"pass": True, "value": None, "reason": "재무 미수집"}
    
    op_pass = (op_cur > 0) # 적자기업 차단
    op_yoy = None
    if op_prev not in (None, 0):
        op_yoy = (op_cur - op_prev) / abs(op_prev) * 100
        op_pass = op_pass and (op_yoy > 0)
        
    return {"pass": bool(op_pass), "value": round(float(op_cur), 1), 
            "reason": f"영익 {op_cur:.0f}억, YoY {op_yoy if op_yoy else 0:+.1f}%"}

def filter_financial_health(fund: dict) -> dict:
    """[9] 재무 퀄리티: ROE > 8%, 부채비율 < 150%"""
    roe = fund.get("roe")
    debt = fund.get("debt_ratio")
    if roe is None and debt is None: return {"pass": True, "value": None, "reason": "재무 미수집"}
    roe_pass = roe > 8.0 if roe is not None else True
    debt_pass = debt < 150.0 if debt is not None else True
    return {"pass": bool(roe_pass and debt_pass), "value": round(float(roe or 0), 2), 
            "reason": f"ROE {roe or 0:.1f}%, 부채 {debt or 0:.0f}%"}

def filter_per_value(fund: dict) -> dict:
    per = float(fund.get("per") or 0)
    if per == 0: return {"pass": True, "value": None, "reason": "PER 미수집"}
    return {"pass": bool(0 < per <= 20), "value": round(per, 2), "reason": f"PER {per:.1f}"}

def filter_pbr_value(fund: dict) -> dict:
    pbr = float(fund.get("pbr") or 0)
    if pbr == 0: return {"pass": True, "value": None, "reason": "PBR 미수집"}
    return {"pass": bool(0 < pbr <= 3.0), "value": round(pbr, 2), "reason": f"PBR {pbr:.2f}"}


TECH_FILTERS = [
    ("12_1모멘텀",  filter_momentum_12_1),
    ("저변동성",     filter_volatility),
    ("MDD",          filter_mdd),
    ("유동성",       filter_liquidity),
    ("거래량모멘텀", filter_volume_momentum),
    ("추세강도",     filter_trend_strength),
]
FUNDA_FILTERS    = ["수급", "실적성장", "재무퀄리티", "PER밸류", "PBR밸류"]
TOTAL_FILTERS    = len(TECH_FILTERS) + len(FUNDA_FILTERS) # 11


# ══════════════════════════════════════════
# [E] 팩터 점수화
# ══════════════════════════════════════════
def compute_factor_score(tech_results: dict, fund: dict, supply_res: dict) -> float:
    score = 0.0
    mom = tech_results.get("12_1모멘텀", {}).get("value") or 0
    score += min(25.0, max(0.0, mom / 10.0))

    if (fund.get("op_profit_cur") or 0) > 0 and (fund.get("op_profit_cur") or 0) > (fund.get("op_profit_prev") or 0): score += 15.0

    slope = tech_results.get("추세강도", {}).get("value") or 0
    if slope > 0: score += min(10.0, abs(slope) * 4.0)

    vr = tech_results.get("거래량모멘텀", {}).get("value") or 0
    score += min(8.0, max(0.0, (vr - 1.3) / 0.7 * 8.0))

    vol = tech_results.get("저변동성", {}).get("value") or 60
    score += max(0.0, (60.0 - vol) / 60.0 * 10.0)

    roe = fund.get("roe") or 0
    score += min(10.0, max(0.0, float(roe) / 3.0))

    per = fund.get("per"); pbr = fund.get("pbr")
    if per and 0 < float(per) <= 20: score += max(0.0, (20.0 - float(per)) / 20.0 * 8.0)
    if pbr and 0 < float(pbr) <= 3.0: score += max(0.0, (3.0 - float(pbr)) / 3.0 * 4.0)
    
    # 수급 보너스 (최대 10점)
    net_buy = supply_res.get("value") or 0
    if net_buy > 0: score += min(10.0, net_buy / 10.0)

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
        symbol, name = row["Symbol"], row.get("Name", row["Symbol"])
        
        df = load_price_from_db(supabase, symbol)
        if df.empty or len(df) < 60: continue

        tech_results, tech_pass_count = {}, 0
        for fname, ffn in TECH_FILTERS:
            res = ffn(df)
            tech_results[fname] = res
            if res["pass"]: tech_pass_count += 1

        fund = load_fundamental_from_db(supabase, symbol) or {}
        
        supply_res = filter_supply_demand(row.get("ForeignNetBuy", 0), row.get("InstNetBuy", 0))
        em_res     = filter_earnings_momentum(fund)
        fh_res     = filter_financial_health(fund)
        per_res    = filter_per_value(fund)
        pbr_res    = filter_pbr_value(fund)
        
        funda_pass = int(supply_res["pass"]) + int(em_res["pass"]) + int(fh_res["pass"]) + int(per_res["pass"]) + int(pbr_res["pass"])
        total_pass = tech_pass_count + funda_pass
        
        if total_pass < WATCHLIST_MIN_PASS: continue

        factor_score = compute_factor_score(tech_results, fund, supply_res)
        
        record = {
            "symbol": symbol, "name": name,
            "marcap_억": round(row.get("Marcap", 0) / 1e8, 0),
            "factor_score": factor_score,
            "pass_count": total_pass,
            "momentum_12_1": tech_results["12_1모멘텀"]["value"],
            "foreign_net_buy": row.get("ForeignNetBuy", 0),
            "inst_net_buy": row.get("InstNetBuy", 0),
            "filter_details": {
                **tech_results,
                "수급": supply_res, "실적성장": em_res, "재무퀄리티": fh_res, "PER밸류": per_res, "PBR밸류": pbr_res
            },
            "screened_at": now_kst_str(),
        }

        if total_pass == TOTAL_FILTERS:
            confirmed.append(record)
            log_fn(f"  ✅ ALL-PASS [{i+1}/{total}] {name} | 점수: {factor_score}")
        else:
            watchlist.append(record)
            log_fn(f"  👀 WATCH {total_pass}/{TOTAL_FILTERS} [{i+1}/{total}] {name} | 점수: {factor_score}")

    confirmed.sort(key=lambda x: x["factor_score"], reverse=True)
    watchlist.sort(key=lambda x: x["factor_score"], reverse=True)
    return confirmed[:top_n], watchlist[:top_n * 3]

def save_screening_result(supabase, confirmed: list, watchlist: list):
    ts = now_kst_str()
    supabase.table(TBL_SCREEN).upsert({"id": 1, "results": json.dumps(confirmed, ensure_ascii=False, cls=NumpyEncoder), "updated_at": ts}).execute()
    supabase.table(TBL_WATCH).upsert({"id": 1, "results": json.dumps(watchlist, ensure_ascii=False, cls=NumpyEncoder), "updated_at": ts}).execute()
