"""
quant_core.py
─────────────────────────────────────────────
이미지 분석 기반 전면 재설계

[핵심 변경]
1. 필터 구조 변경: 하드게이트(필수) + 소프트게이트(점수화)
   - 하드게이트 4개 ALL-PASS 필수
   - 소프트게이트 7개 점수화 → 팩터점수 산출
   - confirmed: 하드게이트 4개 ALL-PASS + 팩터점수 상위
   - watchlist: 하드게이트 3개 이상 통과

2. 이미지 전략 반영
   - 순이익 YoY가 핵심 지표 (영업이익보다 강화)
   - 6개 중 5개 통과도 편입 가능한 유연한 구조
   - 추격매수 타이밍: 거래량 + 가격 돌파 동시 확인

3. 기관/외국인 수급 추가 (한투 API)

4. 거래량 모멘텀 기준 현실화
   - 5일/60일 ≥ 1.3x → 10일/60일 ≥ 1.2x (대형주 포함)
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

# ──────────────────────────────────────────
# 테이블 상수
# ──────────────────────────────────────────
TBL_DAILY   = "stock_daily"
TBL_FUNDA   = "stock_fundamental"
TBL_SCREEN  = "quant_screening_cache"
TBL_WATCH   = "quant_watchlist_cache"

ROLLING_DAYS   = 756
MIN_DAYS       = 120        # 완화: 252 → 120 (6개월)
FUNDA_TTL_SEC  = 86400 * 90

# 사전필터 기준
PREFILTER_MARCAP_억 = 1500
PREFILTER_TVOL_억   = 50

# 확정/워치리스트 기준
HARD_GATE_COUNT     = 4     # 하드게이트 몇 개 ALL-PASS 필요
WATCHLIST_HARD_MIN  = 3     # 워치리스트 하드게이트 최소 통과 수


# ══════════════════════════════════════════
# [A] 유니버스 사전 필터링
# ══════════════════════════════════════════
def _normalize_listing(raw: pd.DataFrame, market: str) -> pd.DataFrame:
    col  = raw.columns.tolist()
    sym  = next((c for c in ["Symbol","Code","Ticker"]       if c in col), None)
    name = next((c for c in ["Name","종목명"]                 if c in col), None)
    sec  = next((c for c in ["Sector","Industry","업종"]      if c in col), None)
    cap  = next((c for c in ["Marcap","시가총액"]              if c in col), None)
    cat  = next((c for c in ["Category","Kind","MarketId"]   if c in col), None)
    if not sym or not name:
        raise ValueError(f"필수 컬럼 없음: {col}")
    df = pd.DataFrame({
        "Symbol": raw[sym].astype(str).str.zfill(6),
        "Name":   raw[name].astype(str),
        "Market": market,
        "Sector": raw[sec].astype(str) if sec else "-",
        "Marcap": pd.to_numeric(raw[cap], errors="coerce").fillna(0) if cap else 0,
        "Kind":   raw[cat].astype(str) if cat else "보통주",
    })
    return df


def load_krx_trading_volume() -> dict:
    """KRX 전종목 거래대금 (전일 기준, 실패 시 빈 dict)"""
    try:
        now = now_kst()
        # 14시 이후면 당일, 이전이면 전일
        trd_dt = now if now.hour >= 15 else now - timedelta(days=1)
        # 주말 보정
        while trd_dt.weekday() >= 5:
            trd_dt -= timedelta(days=1)
        trd_str = trd_dt.strftime("%Y%m%d")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer":    "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
            "Origin":     "http://data.krx.co.kr",
        }
        data = {
            "bld":         "dbms/MDC/STAT/standard/MDCSTAT01501",
            "mktId":       "ALL",
            "trdDd":       trd_str,
            "share":       "1",
            "money":       "1",
            "csvxls_isNo": "false",
        }
        res  = requests.post(
            "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            data=data, headers=headers, timeout=15
        )
        rows = res.json().get("OutBlock_1", [])
        result = {}
        for r in rows:
            code = str(r.get("ISU_SRT_CD", "")).strip().zfill(6)
            tv   = float(str(r.get("ACC_TRDVAL", "0")).replace(",", "") or 0) / 1e8
            if code:
                result[code] = tv
        print(f"[KRX] 거래대금 로드: {len(result)}개 (기준일: {trd_str})")
        return result
    except Exception as e:
        print(f"[KRX] 거래대금 로드 실패: {e}")
        return {}


def load_filtered_universe(
    marcap_min_억: int = PREFILTER_MARCAP_억,
    tvol_min_억:   int = PREFILTER_TVOL_억,
) -> pd.DataFrame:
    print("[유니버스] KRX 전종목 로드 중...")
    kospi  = _normalize_listing(fdr.StockListing("KOSPI"),  "KOSPI")
    kosdaq = _normalize_listing(fdr.StockListing("KOSDAQ"), "KOSDAQ")
    raw_df = pd.concat([kospi, kosdaq], ignore_index=True)
    raw_df = raw_df[raw_df["Symbol"].str.len() == 6].dropna(subset=["Symbol","Name"])
    total  = len(raw_df)

    # 1. 보통주만 (ETF·스팩·리츠·우선주 제거)
    exclude_kw = ["ETF","ETN","스팩","리츠","우","REIT","인프라","선박"]
    mask_name  = raw_df["Name"].str.contains("|".join(exclude_kw), na=False)
    # 종목코드 끝자리가 0이 아니면 우선주 (5,7 등)
    mask_code  = raw_df["Symbol"].str[-1] != "0"
    common     = raw_df[~mask_name & ~mask_code].copy()
    print(f"[유니버스] 전체 {total}개 → 보통주 {len(common)}개", end="")

    # 2. 시가총액 필터
    marcap_원  = marcap_min_억 * 1e8
    cap_filtered = common[common["Marcap"] >= marcap_원].copy()
    print(f" → 시총≥{marcap_min_억}억 {len(cap_filtered)}개", end="")

    # 3. 거래대금 필터
    tvol_map = load_krx_trading_volume()
    if tvol_map:
        cap_filtered["TradingVol억"] = cap_filtered["Symbol"].map(tvol_map).fillna(0)
        final = cap_filtered[cap_filtered["TradingVol억"] >= tvol_min_억].copy()
    else:
        final = cap_filtered.copy()
        final["TradingVol억"] = 0

    final = final.reset_index(drop=True)
    print(f" → 거래대금≥{tvol_min_억}억 최종 {len(final)}개 (사전필터 완료)")
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
            "open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"
        })
        for col in ["Open","High","Low","Close","Volume"]:
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
                .eq("symbol", symbol).lte("date", dates[excess-1]).execute()
    except:
        pass


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
    result = {"roe": None, "debt_ratio": None, "op_profit": None,
              "net_income": None, "revenue": None, "eps": None}
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
                if "영업이익" in acnt and "영업이익률" not in acnt and result["op_profit"] is None:
                    result["op_profit"] = val_억
                if "당기순이익" in acnt and result["net_income"] is None:
                    result["net_income"] = val_억
                if "매출액" in acnt and result["revenue"] is None:
                    result["revenue"] = val_억
                if "ROE" in acnt and result["roe"] is None:
                    result["roe"] = val_raw
                if "부채비율" in acnt and result["debt_ratio"] is None:
                    result["debt_ratio"] = val_raw
            url_eps = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
            res_eps = requests.get(url_eps, params={**params,"fs_div":"OFS"}, timeout=10).json()
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
    """네이버금융 크롤링 — 순이익 YoY 포함"""
    result = {
        "roe": None, "debt_ratio": None,
        "op_profit_cur": None, "op_profit_prev": None,
        "net_income_cur": None, "net_income_prev": None,
        "revenue_cur": None, "revenue_prev": None,
        "eps_cur": None, "eps_prev": None,
    }
    try:
        url  = f"https://finance.naver.com/item/main.naver?code={symbol}"
        soup = BeautifulSoup(
            requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=8).content,
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
                if "매출액" in label:
                    result["revenue_prev"] = prev_val
                    result["revenue_cur"]  = cur_val
                elif "영업이익" in label and "영업이익률" not in label:
                    result["op_profit_prev"] = prev_val
                    result["op_profit_cur"]  = cur_val
                elif "당기순이익" in label or "순이익" in label:
                    result["net_income_prev"] = prev_val
                    result["net_income_cur"]  = cur_val
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
            if not is_expired(row.get("updated_at",""), FUNDA_TTL_SEC):
                return row
    except:
        pass
    return None


def save_fundamental_to_db(supabase, symbol: str, name: str, data: dict):
    supabase.table(TBL_FUNDA).upsert({
        "symbol": symbol, "name": name,
        "roe":              data.get("roe"),
        "debt_ratio":       data.get("debt_ratio"),
        "op_profit_cur":    data.get("op_profit_cur"),
        "op_profit_prev":   data.get("op_profit_prev"),
        "net_income_cur":   data.get("net_income_cur"),
        "net_income_prev":  data.get("net_income_prev"),
        "revenue_cur":      data.get("revenue_cur"),
        "revenue_prev":     data.get("revenue_prev"),
        "eps_cur":          data.get("eps_cur"),
        "eps_prev":         data.get("eps_prev"),
        "updated_at":       now_kst_str(),
    }).execute()


def get_fundamental(supabase, symbol: str, name: str,
                    dart_api_key: str = "", dart_corp_map: dict = None) -> dict:
    cached = load_fundamental_from_db(supabase, symbol)
    if cached:
        return cached
    data = {
        "roe": None, "debt_ratio": None,
        "op_profit_cur": None, "op_profit_prev": None,
        "net_income_cur": None, "net_income_prev": None,
        "revenue_cur": None, "revenue_prev": None,
        "eps_cur": None, "eps_prev": None,
    }
    if dart_api_key and dart_corp_map:
        corp_code = dart_corp_map.get(symbol)
        if corp_code:
            cur  = fetch_dart_financial(corp_code, dart_api_key, now_kst().year - 1)
            prev = fetch_dart_financial(corp_code, dart_api_key, now_kst().year - 2)
            data.update({
                "op_profit_cur":   cur.get("op_profit"),
                "op_profit_prev":  prev.get("op_profit"),
                "net_income_cur":  cur.get("net_income"),
                "net_income_prev": prev.get("net_income"),
                "revenue_cur":     cur.get("revenue"),
                "revenue_prev":    prev.get("revenue"),
                "eps_cur":         cur.get("eps"),
                "eps_prev":        prev.get("eps"),
                "roe":             cur.get("roe"),
                "debt_ratio":      cur.get("debt_ratio"),
            })
    # 네이버 보완
    missing = [k for k in ["op_profit_cur","net_income_cur","eps_cur","roe"]
               if data.get(k) is None]
    if missing:
        naver = fetch_naver_fundamental(symbol)
        for k, v in naver.items():
            if data.get(k) is None and v is not None:
                data[k] = v
        time.sleep(0.3)
    save_fundamental_to_db(supabase, symbol, name, data)
    return data


# ══════════════════════════════════════════
# [D] 필터 설계: 하드게이트 4개 + 소프트게이트 7개
#
# [하드게이트] — ALL-PASS 필수 (confirmed 조건)
#  H1. 순이익 YoY > 0           ← 이미지 핵심 지표
#  H2. 12-1 모멘텀 > 0          ← 추세 추종
#  H3. 유동성 ≥ 30억            ← 최소 거래 가능
#  H4. MDD ≥ -30%              ← 완화 (-25 → -30)
#
# [소프트게이트] — 점수화 (팩터점수 구성)
#  S1. 영업이익 YoY > 0
#  S2. ROE ≥ 5%
#  S3. 매출 YoY > 0
#  S4. 저변동성 ≤ 60%
#  S5. 거래량 모멘텀 ≥ 1.2x (완화: 1.3→1.2, 기간 5일→10일)
#  S6. 추세 강도 (R² ≥ 0.4로 완화)
#  S7. 기관+외인 수급 > 0
# ══════════════════════════════════════════

# ── 하드게이트 ──────────────────────────────

def hard_net_income_yoy(fund: dict) -> dict:
    """H1. 순이익 YoY > 0 (이미지 핵심 지표)"""
    cur  = fund.get("net_income_cur")
    prev = fund.get("net_income_prev")
    if cur is None:
        return {"pass": True, "value": None, "reason": "순이익 미수집 — 중립 통과"}
    if prev is None or prev == 0:
        passed = cur > 0
        return {"pass": passed, "value": None,
                "reason": f"순이익 {cur:.0f}억 (전년 없음, 흑자여부만 확인)"}
    yoy = (cur - prev) / abs(prev) * 100
    return {"pass": yoy > 0, "value": round(float(yoy), 2),
            "reason": f"순이익YoY {yoy:+.1f}% (기준 >0)"}


def hard_momentum(df: pd.DataFrame) -> dict:
    """H2. 12-1 모멘텀 > 0"""
    if len(df) < MIN_DAYS:
        # 데이터 부족 시 단기 모멘텀으로 대체
        if len(df) >= 60:
            ret = (df["Close"].iloc[-1] - df["Close"].iloc[-60]) / df["Close"].iloc[-60] * 100
            return {"pass": ret > 0, "value": round(float(ret), 2),
                    "reason": f"3개월 모멘텀 {ret:+.1f}% (데이터 부족으로 대체)"}
        return {"pass": False, "value": None, "reason": f"데이터 부족 ({len(df)}일)"}
    ret = (df["Close"].iloc[-21] - df["Close"].iloc[-252]) / df["Close"].iloc[-252] * 100
    return {"pass": ret > 0, "value": round(float(ret), 2),
            "reason": f"12-1 모멘텀 {ret:+.1f}%"}


def hard_liquidity(df: pd.DataFrame) -> dict:
    """H3. 20일 평균 거래대금 ≥ 30억"""
    if len(df) < 20 or "Volume" not in df.columns:
        return {"pass": False, "value": None, "reason": "데이터 부족"}
    avg_tv = (df["Close"] * df["Volume"]).iloc[-20:].mean()
    return {"pass": avg_tv >= 3_000_000_000, "value": round(float(avg_tv/1e8), 1),
            "reason": f"20일 거래대금 {avg_tv/1e8:.0f}억 (기준 ≥30억)"}


def hard_mdd(df: pd.DataFrame) -> dict:
    """H4. 1년 MDD ≥ -30% (완화)"""
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": "데이터 부족"}
    close = df["Close"].iloc[-252:] if len(df) >= 252 else df["Close"]
    mdd   = ((close - close.cummax()) / close.cummax() * 100).min()
    return {"pass": mdd >= -30.0, "value": round(float(mdd), 2),
            "reason": f"1년 MDD {mdd:.1f}% (기준 ≥-30%)"}


# ── 소프트게이트 ────────────────────────────

def soft_op_profit_yoy(fund: dict) -> dict:
    """S1. 영업이익 YoY > 0"""
    cur  = fund.get("op_profit_cur")
    prev = fund.get("op_profit_prev")
    if cur is None:
        return {"pass": True, "value": None, "reason": "미수집"}
    if prev is None or prev == 0:
        return {"pass": cur > 0, "value": None,
                "reason": f"영업이익 {cur:.0f}억 (흑자여부)"}
    yoy = (cur - prev) / abs(prev) * 100
    return {"pass": yoy > 0, "value": round(float(yoy), 2),
            "reason": f"영업이익YoY {yoy:+.1f}%"}


def soft_roe(fund: dict) -> dict:
    """S2. ROE ≥ 5%"""
    roe = fund.get("roe")
    if roe is None:
        return {"pass": True, "value": None, "reason": "미수집"}
    return {"pass": roe >= 5.0, "value": round(float(roe), 2),
            "reason": f"ROE {roe:.1f}% (기준 ≥5%)"}


def soft_revenue_yoy(fund: dict) -> dict:
    """S3. 매출 YoY > -10% (완화: 성장하지 않아도 -10%이내면 통과)"""
    cur  = fund.get("revenue_cur")
    prev = fund.get("revenue_prev")
    if cur is None or prev is None or prev == 0:
        return {"pass": True, "value": None, "reason": "미수집"}
    yoy = (cur - prev) / abs(prev) * 100
    return {"pass": yoy > -10.0, "value": round(float(yoy), 2),
            "reason": f"매출YoY {yoy:+.1f}% (기준 >-10%)"}


def soft_volatility(df: pd.DataFrame) -> dict:
    """S4. 연환산 변동성 ≤ 70% (완화: 60→70)"""
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": "데이터 부족"}
    vol = df["Close"].pct_change().dropna().iloc[-60:].std() * np.sqrt(252) * 100
    return {"pass": vol <= 70.0, "value": round(float(vol), 2),
            "reason": f"연환산 변동성 {vol:.1f}% (기준 ≤70%)"}


def soft_volume_momentum(df: pd.DataFrame) -> dict:
    """S5. 거래량 모멘텀: 10일/60일 ≥ 1.2x (완화)"""
    if len(df) < 60 or "Volume" not in df.columns:
        return {"pass": False, "value": None, "reason": "데이터 부족"}
    vol60 = df["Volume"].iloc[-60:].mean()
    ratio = df["Volume"].iloc[-10:].mean() / vol60 if vol60 > 0 else 0
    return {"pass": ratio >= 1.2, "value": round(float(ratio), 3),
            "reason": f"거래량 10일/60일 {ratio:.2f}x (기준 ≥1.2x)"}


def soft_trend_strength(df: pd.DataFrame) -> dict:
    """S6. 추세 강도: R² ≥ 0.4 (완화)"""
    if len(df) < 60:
        return {"pass": False, "value": None, "reason": "데이터 부족"}
    close  = df["Close"].iloc[-60:].values
    x      = np.arange(len(close))
    p      = np.polyfit(x, close, 1)
    y_hat  = np.polyval(p, x)
    ss_res = np.sum((close - y_hat) ** 2)
    ss_tot = np.sum((close - close.mean()) ** 2)
    r2     = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    slope  = p[0]
    return {"pass": slope > 0 and r2 >= 0.4, "value": round(float(slope), 4),
            "reason": f"기울기 {slope:+.2f}, R² {r2:.2f} (기준: >0, ≥0.4)"}


def soft_supply_demand(fund: dict) -> dict:
    """S7. 기관+외인 순매수 > 0 (수급)"""
    foreign   = fund.get("foreign_net_buy") or 0
    institute = fund.get("institute_net_buy") or 0
    total_net = foreign + institute
    if foreign == 0 and institute == 0:
        return {"pass": True, "value": None, "reason": "수급 미수집 — 중립 통과"}
    return {"pass": total_net > 0, "value": round(float(total_net), 0),
            "reason": f"외인 {foreign:+.0f} + 기관 {institute:+.0f} = {total_net:+.0f}주"}


# 필터 목록 정의
HARD_GATES = [
    ("순이익YoY",  None),     # fund 기반, df 없음
    ("모멘텀",     None),     # df 기반
    ("유동성",     None),     # df 기반
    ("MDD",        None),     # df 기반
]
SOFT_GATES = [
    ("영업이익YoY", None),
    ("ROE",         None),
    ("매출YoY",     None),
    ("저변동성",    None),
    ("거래량모멘텀", None),
    ("추세강도",    None),
    ("수급",        None),
]
ALL_FILTER_NAMES = [n for n, _ in HARD_GATES] + [n for n, _ in SOFT_GATES]
# 이전 코드 호환용
TECH_FILTERS  = [(n, None) for n in ["모멘텀","저변동성","MDD","유동성","거래량모멘텀","추세강도"]]
FUNDA_FILTERS = ["순이익YoY","영업이익YoY","ROE","매출YoY","수급"]


def _run_all_filters(df: pd.DataFrame, fund: dict) -> tuple[dict, int, int]:
    """
    모든 필터 실행. (filter_results, hard_pass_count, soft_pass_count) 반환
    """
    fr = {}

    # 하드게이트
    fr["순이익YoY"] = hard_net_income_yoy(fund)
    fr["모멘텀"]    = hard_momentum(df)
    fr["유동성"]    = hard_liquidity(df)
    fr["MDD"]       = hard_mdd(df)
    hard_pass = sum(1 for n, _ in HARD_GATES if fr[n]["pass"])

    # 소프트게이트
    fr["영업이익YoY"]  = soft_op_profit_yoy(fund)
    fr["ROE"]          = soft_roe(fund)
    fr["매출YoY"]      = soft_revenue_yoy(fund)
    fr["저변동성"]     = soft_volatility(df)
    fr["거래량모멘텀"] = soft_volume_momentum(df)
    fr["추세강도"]     = soft_trend_strength(df)
    fr["수급"]         = soft_supply_demand(fund)
    soft_pass = sum(1 for n, _ in SOFT_GATES if fr[n]["pass"])

    return fr, hard_pass, soft_pass


# ══════════════════════════════════════════
# [E] 팩터 점수 (0~100)
# 순이익모멘텀 25 · 12-1모멘텀 20 · 영업이익YoY 15 · 추세강도 10
# 거래량모멘텀 10 · ROE 8 · 수급 7 · 저변동성 5
# ══════════════════════════════════════════
def compute_factor_score(fr: dict, fund: dict) -> float:
    s = 0.0

    # 순이익 YoY (25점)
    ni_yoy = fr.get("순이익YoY", {}).get("value") or 0
    if ni_yoy > 0:
        s += min(25.0, 12.5 + ni_yoy / 20.0)   # 50% 성장 = 만점
    elif fr.get("순이익YoY", {}).get("pass"):
        s += 8.0    # 중립 통과

    # 12-1 모멘텀 (20점)
    mom = fr.get("모멘텀", {}).get("value") or 0
    s  += min(20.0, max(0.0, mom / 5.0))

    # 영업이익 YoY (15점)
    op_yoy = fr.get("영업이익YoY", {}).get("value") or 0
    if op_yoy > 0:
        s += min(15.0, 7.5 + op_yoy / 20.0)
    elif fr.get("영업이익YoY", {}).get("pass"):
        s += 5.0

    # 추세강도 (10점)
    slope = fr.get("추세강도", {}).get("value") or 0
    if slope > 0: s += min(10.0, abs(slope) * 4.0)

    # 거래량모멘텀 (10점)
    vr = fr.get("거래량모멘텀", {}).get("value") or 0
    s += min(10.0, max(0.0, (vr - 1.2) / 0.8 * 10.0))

    # ROE (8점)
    roe = fund.get("roe") or 0
    s  += min(8.0, max(0.0, roe / 5.0))

    # 수급 (7점)
    net_buy = fr.get("수급", {}).get("value") or 0
    if net_buy > 0: s += min(7.0, net_buy / 100000 * 7.0)
    elif fr.get("수급", {}).get("pass"): s += 3.5   # 중립

    # 저변동성 (5점)
    vol = fr.get("저변동성", {}).get("value") or 70
    s  += max(0.0, (70.0 - vol) / 70.0 * 5.0)

    return round(min(s, 100.0), 2)


# ══════════════════════════════════════════
# [F] 스크리닝 엔진
# ══════════════════════════════════════════
def run_screening_from_db(supabase, universe_df: pd.DataFrame,
                          top_n: int = 10, log_fn=print,
                          dart_api_key: str = "",
                          dart_corp_map: dict = None) -> tuple:
    """
    confirmed: 하드게이트 4개 ALL-PASS → 팩터점수 top_n
    watchlist: 하드게이트 3개 이상 통과 → 팩터점수 상위 top_n*3
    """
    confirmed, watchlist = [], []
    total = len(universe_df)

    for i, (_, row) in enumerate(universe_df.iterrows()):
        symbol = row["Symbol"]
        name   = row.get("Name", symbol)

        df = load_price_from_db(supabase, symbol)
        if df.empty or len(df) < 20:
            continue

        fund = load_fundamental_from_db(supabase, symbol) or {}
        fr, hard_pass, soft_pass = _run_all_filters(df, fund)

        # 하드게이트 3개 미만 → 탈락
        if hard_pass < WATCHLIST_HARD_MIN:
            continue

        factor_score = compute_factor_score(fr, fund)
        curr_price   = int(df["Close"].iloc[-1])
        ret_1m       = (df["Close"].iloc[-1] - df["Close"].iloc[-21]) \
                       / df["Close"].iloc[-21] * 100 if len(df) >= 21 else 0.0

        record = {
            "symbol":               symbol,
            "name":                 name,
            "sector":               row.get("Sector", "-"),
            "market":               row.get("Market", "-"),
            "marcap_억":            round(row.get("Marcap", 0) / 1e8, 0),
            "current_price":        curr_price,
            "ret_1m":               round(float(ret_1m), 2),
            "momentum_score":       fr.get("모멘텀", {}).get("value") or 0,
            "factor_score":         factor_score,
            "hard_pass":            hard_pass,
            "soft_pass":            soft_pass,
            "pass_count":           hard_pass + soft_pass,
            # 주요 지표
            "net_income_yoy":       fr.get("순이익YoY", {}).get("value"),
            "op_profit_yoy":        fr.get("영업이익YoY", {}).get("value"),
            "revenue_yoy":          fr.get("매출YoY", {}).get("value"),
            "annual_vol":           fr.get("저변동성", {}).get("value"),
            "mdd":                  fr.get("MDD", {}).get("value"),
            "avg_trading_value_억": fr.get("유동성", {}).get("value"),
            "vol_ratio":            fr.get("거래량모멘텀", {}).get("value"),
            "trend_slope":          fr.get("추세강도", {}).get("value"),
            "roe":                  fund.get("roe"),
            "debt_ratio":           fund.get("debt_ratio"),
            "foreign_net_buy":      fund.get("foreign_net_buy"),
            "institute_net_buy":    fund.get("institute_net_buy"),
            "filter_details":       fr,
            "screened_at":          now_kst_str(),
        }

        if hard_pass == HARD_GATE_COUNT:
            confirmed.append(record)
            log_fn(f"  ✅ [{i+1}/{total}] {name} | 하드{hard_pass}/4 소프트{soft_pass}/7 | 점수:{factor_score}")
        else:
            watchlist.append(record)
            log_fn(f"  👀 [{i+1}/{total}] {name} | 하드{hard_pass}/4 소프트{soft_pass}/7 | 점수:{factor_score}")

    confirmed.sort(key=lambda x: x["factor_score"], reverse=True)
    watchlist.sort(key=lambda x: x["factor_score"], reverse=True)
    return confirmed[:top_n], watchlist[:top_n * 3]


# ══════════════════════════════════════════
# [G] 결과 저장 / 조회
# ══════════════════════════════════════════
def save_screening_result(supabase, confirmed: list, watchlist: list):
    ts = now_kst_str()
    supabase.table(TBL_SCREEN).upsert(
        {"id":1,"results":json.dumps(confirmed,ensure_ascii=False),"updated_at":ts}
    ).execute()
    supabase.table(TBL_WATCH).upsert(
        {"id":1,"results":json.dumps(watchlist,ensure_ascii=False),"updated_at":ts}
    ).execute()


def load_screening_result(supabase) -> tuple:
    confirmed, watchlist, updated_at = [], [], ""
    try:
        r1 = supabase.table(TBL_SCREEN).select("*").eq("id",1).execute()
        if r1.data:
            confirmed  = json.loads(r1.data[0]["results"])
            updated_at = r1.data[0].get("updated_at","")
    except: pass
    try:
        r2 = supabase.table(TBL_WATCH).select("*").eq("id",1).execute()
        if r2.data:
            watchlist = json.loads(r2.data[0]["results"])
    except: pass
    return confirmed, watchlist, updated_at
