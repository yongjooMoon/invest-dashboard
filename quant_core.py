"""
quant_core.py
─────────────────────────────────────────────
정통 퀀트 아키텍처 재설계 (필터 ↔ 스코어 분리)

[핵심 변경]
1. Survival Filter (생존): 6개 절대 조건으로 필터링
   - Growth Composite (매출/영업익/순이익 통합) > 0
   - Volatility Adaptive MDD (ATR 기반 동적 한계선 방어)
   - Liquidity, Momentum, Volatility, Trend Strength
2. Ranking Score (상대평가): 살아남은 종목 대상 Cross-Sectional 백분위(Percentile) 점수화
3. Confirmed: 생존 필터 6개 ALL-PASS 종목 중 랭킹 상위
   Watchlist: 생존 필터 4개 이상 통과 종목
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
MIN_DAYS       = 120
FUNDA_TTL_SEC  = 86400 * 90

PREFILTER_MARCAP_억 = 1500
PREFILTER_TVOL_억   = 50

# 확정/워치리스트 필터 기준 (Survival Filters)
CONFIRM_FILTER_MIN  = 5     # 완화: 생존 필터 6개 중 5개 이상 통과
WATCHLIST_FILTER_MIN= 3     # 완화: 생존 필터 6개 중 3개 이상 통과

# UI 호환용 Gate 정의
HARD_GATES = [
    ("Growth Composite", None), ("Dynamic MDD", None), ("Liquidity", None),
    ("Momentum", None), ("Volatility", None), ("Trend Strength", None)
]
SOFT_GATES = [
    ("Growth Rank", None), ("Momentum Rank", None), ("Trend Rank", None),
    ("Volume Mom Rank", None), ("Supply Rank", None)
]

# ══════════════════════════════════════════
# [A] 유니버스 사전 필터링 & [B] 일봉 DB (유지)
# ══════════════════════════════════════════
def _normalize_listing(raw: pd.DataFrame, market: str) -> pd.DataFrame:
    col = raw.columns.tolist()
    sym = next((c for c in ["Symbol", "Code", "Ticker"] if c in col), None)
    name = next((c for c in ["Name", "종목명"] if c in col), None)
    cap = next((c for c in ["Marcap", "시가총액"] if c in col), None)
    close_col = next((c for c in ["Close", "종가"] if c in col), None)
    vol_col = next((c for c in ["Volume", "거래량"] if c in col), None)
    amt_col = next((c for c in ["Amount", "거래대금"] if c in col), None)

    df = pd.DataFrame({
        "Symbol": raw[sym].astype(str).str.zfill(6), "Name": raw[name].astype(str),
        "Market": market,
        "Marcap": pd.to_numeric(raw[cap], errors="coerce") if cap else 0,
        "Close": pd.to_numeric(raw[close_col], errors="coerce") if close_col else 0,
    })
    if amt_col: df["Amount"] = pd.to_numeric(raw[amt_col], errors="coerce").fillna(0)
    elif vol_col and close_col: df["Amount"] = pd.to_numeric(raw[vol_col], errors="coerce").fillna(0) * pd.to_numeric(raw[close_col], errors="coerce").fillna(0)
    else: df["Amount"] = 0
    return df

def load_filtered_universe(marcap_min_억: int = PREFILTER_MARCAP_억, tvol_min_억: int = PREFILTER_TVOL_억) -> pd.DataFrame:
    print("[유니버스] KRX 전종목 로드 중...")
    kospi = _normalize_listing(fdr.StockListing("KOSPI"), "KOSPI")
    kosdaq = _normalize_listing(fdr.StockListing("KOSDAQ"), "KOSDAQ")
    raw_df = pd.concat([kospi, kosdaq], ignore_index=True)
    raw_df = raw_df[raw_df["Symbol"].str.len() == 6].dropna(subset=["Symbol","Name"])

    exclude_kw = ["ETF","ETN","스팩","리츠","우","REIT","인프라","선박"]
    mask_name = raw_df["Name"].str.contains("|".join(exclude_kw), na=False)
    mask_code = raw_df["Symbol"].str[-1] != "0"
    common = raw_df[~mask_name & ~mask_code].copy()

    marcap_원 = marcap_min_억 * 1e8
    cap_filtered = common[common["Marcap"] >= marcap_원].copy()
    cap_filtered["TradingVol억"] = cap_filtered["Amount"] / 1e8 if "Amount" in cap_filtered.columns else 0
    final = cap_filtered[cap_filtered["TradingVol억"] >= tvol_min_억].copy().reset_index(drop=True)
    print(f"[유니버스] 최종 대상 {len(final)}개 (사전필터 완료)")
    return final

def load_price_from_db(supabase, symbol: str) -> pd.DataFrame:
    try:
        res = supabase.table(TBL_DAILY).select("date,open,high,low,close,volume").eq("symbol", symbol).order("date", desc=False).execute()
        if not res.data: return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index().rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        for col in ["Open","High","Low","Close","Volume"]:
            if col in df.columns: df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["Close"])
    except: return pd.DataFrame()

def upsert_daily_rows(supabase, symbol: str, name: str, rows: list):
    if rows: supabase.table(TBL_DAILY).upsert([{**r, "symbol": symbol, "name": name} for r in rows], on_conflict="symbol,date").execute()

def trim_old_rows(supabase, symbol: str):
    try:
        res = supabase.table(TBL_DAILY).select("date").eq("symbol", symbol).order("date", desc=False).execute()
        dates = [r["date"] for r in res.data]
        if len(dates) > ROLLING_DAYS: supabase.table(TBL_DAILY).delete().eq("symbol", symbol).lte("date", dates[len(dates) - ROLLING_DAYS - 1]).execute()
    except: pass

# ══════════════════════════════════════════
# [C] 펀더멘털 (유지)
# ══════════════════════════════════════════
def _parse_num(txt) -> float | None:
    if not txt: return None
    try:
        clean = re.sub(r"[^\d.\-]", "", str(txt).replace(",", "").strip())
        return float(clean) if clean and clean != "-" else None
    except: return None

def fetch_dart_financial(corp_code: str, dart_api_key: str, year: int = None) -> dict:
    if year is None: year = now_kst().year - 1
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    result = {"roe": None, "debt_ratio": None, "op_profit": None, "net_income": None, "revenue": None, "eps": None}
    for reprt_code in ["11011", "11012"]:
        try:
            res = requests.get(url, params={"crtfc_key": dart_api_key, "corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": "CFS"}, timeout=10).json()
            if res.get("status") != "000": continue
            for item in res.get("list", []):
                acnt, val_raw = item.get("account_nm", ""), _parse_num(item.get("thstrm_amount", "0"))
                val_억 = val_raw / 1e8
                if "영업이익" in acnt and "영업이익률" not in acnt and result["op_profit"] is None: result["op_profit"] = val_억
                if "당기순이익" in acnt and result["net_income"] is None: result["net_income"] = val_억
                if "매출액" in acnt and result["revenue"] is None: result["revenue"] = val_억
                if "ROE" in acnt and result["roe"] is None: result["roe"] = val_raw
                if "부채비율" in acnt and result["debt_ratio"] is None: result["debt_ratio"] = val_raw
            break
        except: pass
    return result

def fetch_naver_fundamental(symbol: str) -> dict:
    result = {"roe": None, "debt_ratio": None, "op_profit_cur": None, "op_profit_prev": None, "net_income_cur": None, "net_income_prev": None, "revenue_cur": None, "revenue_prev": None}
    try:
        soup = BeautifulSoup(requests.get(f"https://finance.naver.com/item/main.naver?code={symbol}", headers={"User-Agent": "Mozilla/5.0"}, timeout=8).content, "html.parser")
        table = soup.select_one("div.cop_analysis table")
        if table:
            for tr in table.select("tbody tr"):
                th = tr.select_one("th")
                if not th: continue
                label, tds = th.text.strip(), tr.select("td")
                valid_vals = [v for v in [_parse_num(td.text) for td in (tds[:4] if len(tds) >= 4 else tds)] if v is not None]
                if not valid_vals: continue
                cur_val, prev_val = valid_vals[-1], valid_vals[-2] if len(valid_vals) >= 2 else None
                if "매출액" in label: result["revenue_prev"], result["revenue_cur"] = prev_val, cur_val
                elif "영업이익" in label and "영업이익률" not in label: result["op_profit_prev"], result["op_profit_cur"] = prev_val, cur_val
                elif "당기순이익" in label or "순이익" in label: result["net_income_prev"], result["net_income_cur"] = prev_val, cur_val
                elif "ROE" in label: result["roe"] = cur_val
        for th in soup.find_all("th"):
            if "부채비율" in th.text:
                valid_vals = [_parse_num(td.text) for td in th.find_parent("tr").select("td")[:4]]
                if valid_vals: result["debt_ratio"] = valid_vals[-1]
                break
    except: pass
    return result

def load_fundamental_from_db(supabase, symbol: str) -> dict | None:
    try:
        res = supabase.table(TBL_FUNDA).select("*").eq("symbol", symbol).execute()
        if res.data and not is_expired(res.data[0].get("updated_at",""), FUNDA_TTL_SEC): return res.data[0]
    except: pass
    return None

def save_fundamental_to_db(supabase, symbol: str, name: str, data: dict):
    supabase.table(TBL_FUNDA).upsert({
        "symbol": symbol, "name": name,
        "roe": data.get("roe"), "debt_ratio": data.get("debt_ratio"),
        "op_profit_cur": data.get("op_profit_cur"), "op_profit_prev": data.get("op_profit_prev"),
        "net_income_cur": data.get("net_income_cur"), "net_income_prev": data.get("net_income_prev"),
        "revenue_cur": data.get("revenue_cur"), "revenue_prev": data.get("revenue_prev"),
        "foreign_net_buy": data.get("foreign_net_buy"), "institute_net_buy": data.get("institute_net_buy"),
        "updated_at": now_kst_str(),
    }).execute()

def get_fundamental(supabase, symbol: str, name: str, dart_api_key: str = "", dart_corp_map: dict = None) -> dict:
    cached = load_fundamental_from_db(supabase, symbol)
    if cached: return cached
    data = {}
    if dart_api_key and dart_corp_map and dart_corp_map.get(symbol):
        c_code = dart_corp_map.get(symbol)
        cur, prev = fetch_dart_financial(c_code, dart_api_key, now_kst().year - 1), fetch_dart_financial(c_code, dart_api_key, now_kst().year - 2)
        data.update({"op_profit_cur": cur.get("op_profit"), "op_profit_prev": prev.get("op_profit"),
                     "net_income_cur": cur.get("net_income"), "net_income_prev": prev.get("net_income"),
                     "revenue_cur": cur.get("revenue"), "revenue_prev": prev.get("revenue"),
                     "roe": cur.get("roe"), "debt_ratio": cur.get("debt_ratio")})

    naver = fetch_naver_fundamental(symbol)
    for k, v in naver.items():
        if data.get(k) is None and v is not None: data[k] = v
    time.sleep(0.3)
    save_fundamental_to_db(supabase, symbol, name, data)
    return data

# ══════════════════════════════════════════
# [D] 퀀트 지표 통합 계산 (Metrics)
# ══════════════════════════════════════════
def calc_quant_metrics(df: pd.DataFrame, fund: dict) -> dict:
    """종목별 로우 데이터 기반 퀀트 지표 산출"""
    metrics = {}
    close = df["Close"]

    # 1. Growth Composite (매출 20%, 영업익 30%, 순이익 50% 통합 가중치)
    def safe_yoy(c, p):
        if c is None or p is None or p == 0: return 0.0
        return (c - p) / abs(p) * 100

    net_yoy = safe_yoy(fund.get("net_income_cur"), fund.get("net_income_prev"))
    op_yoy = safe_yoy(fund.get("op_profit_cur"), fund.get("op_profit_prev"))
    rev_yoy = safe_yoy(fund.get("revenue_cur"), fund.get("revenue_prev"))

    metrics["net_yoy"] = net_yoy
    metrics["growth_composite"] = (net_yoy * 0.5) + (op_yoy * 0.3) + (rev_yoy * 0.2)

    # 2. Volatility Adaptive MDD (ATR 기반)
    if len(df) >= 60:
        # 단기/중기 생존 방어선: 52주 고점 대신 최근 60일 고점을 기준으로 변경하여 억울한 탈락 방지
        roll_max = close.tail(60).cummax()
        metrics["mdd"] = ((close.tail(60) - roll_max) / roll_max * 100).min()

        # Calculate ATR (Average True Range)
        high, low, prev_close = df.get("High", close), df.get("Low", close), close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean().iloc[-1]
        atr_pct = (atr20 / close.iloc[-1]) * 100

        # 동적 MDD 제한: ATR의 3배수를 한계로 잡되, 기본 -15% 보장, 최대 -40%까지만 허용
        metrics["dynamic_mdd_limit"] = max(-40.0, min(-15.0, -(atr_pct * 3.0)))
        metrics["volatility"] = close.pct_change().dropna().iloc[-60:].std() * np.sqrt(252) * 100
    else:
        metrics["mdd"], metrics["dynamic_mdd_limit"], metrics["volatility"] = -99.9, -15.0, 999.0

    # 3. Liquidity (20일 평균 거래대금)
    metrics["liquidity_20d"] = (close * df.get("Volume", 0)).iloc[-20:].mean() / 1e8 if len(df) >= 20 else 0

    # 4. Momentum (12-1 모멘텀)
    if len(df) >= 252: metrics["momentum"] = (close.iloc[-21] - close.iloc[-252]) / close.iloc[-252] * 100
    elif len(df) >= 60: metrics["momentum"] = (close.iloc[-1] - close.iloc[-60]) / close.iloc[-60] * 100
    else: metrics["momentum"] = -999.0

    # 5. Trend Strength
    if len(df) >= 60:
        c60 = close.iloc[-60:].values
        x = np.arange(len(c60))
        p = np.polyfit(x, c60, 1)
        y_hat = np.polyval(p, x)
        ss_res = np.sum((c60 - y_hat) ** 2)
        ss_tot = np.sum((c60 - c60.mean()) ** 2)
        metrics["trend_slope"] = p[0]
        metrics["trend_r2"] = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    else:
        metrics["trend_slope"], metrics["trend_r2"] = -1.0, 0.0

    # 6. Volume Momentum
    if len(df) >= 60:
        v60 = df["Volume"].iloc[-60:].mean()
        metrics["volume_momentum"] = df["Volume"].iloc[-10:].mean() / v60 if v60 > 0 else 0
    else:
        metrics["volume_momentum"] = 0.0

    # 7. Supply / Demand
    metrics["supply_demand"] = (fund.get("foreign_net_buy") or 0) + (fund.get("institute_net_buy") or 0)

    return metrics

# ══════════════════════════════════════════
# [E] 분리된 스크리닝 엔진 (Survival Filter -> Score Ranking)
# ══════════════════════════════════════════
def run_screening_from_db(supabase, universe_df: pd.DataFrame, top_n: int = 10, log_fn=print, dart_api_key: str = "", dart_corp_map: dict = None) -> tuple:
    candidates = []

    # ── Phase 1. Survival Filters (생존 필터링) ──
    for _, row in universe_df.iterrows():
        symbol, name = row["Symbol"], row.get("Name", row["Symbol"])
        df = load_price_from_db(supabase, symbol)
        if df.empty or len(df) < 60: continue

        fund = load_fundamental_from_db(supabase, symbol) or {}
        metrics = calc_quant_metrics(df, fund)

        # 절대 방어선 (6 Filters) - 현실적인 장세에 맞춰 기준 완화
        f_growth = metrics["growth_composite"] >= 0 # 0 포함 (데이터 부족으로 인한 탈락 방지)
        f_mdd = metrics["mdd"] >= metrics["dynamic_mdd_limit"]
        f_liq = metrics["liquidity_20d"] >= 30
        f_mom = metrics["momentum"] > 0
        f_vol = metrics["volatility"] <= 70.0
        f_trend = metrics["trend_slope"] > 0 # R2 조건 제거 (상승 추세면 일단 통과)

        pass_count = sum([f_growth, f_mdd, f_liq, f_mom, f_vol, f_trend])

        # 생존 기준 미달 즉시 폐기 (Watchlist 최소 조건 3개로 완화)
        if pass_count < WATCHLIST_FILTER_MIN:
            continue

        curr_price = int(df["Close"].iloc[-1])
        ma5 = int(df["Close"].iloc[-5:].mean())
        entry_price = min(curr_price, ma5) # 지지선 매수 제안

        candidates.append({
            "symbol": symbol, "name": name, "market": row.get("Market", "-"),
            "marcap_억": round(row.get("Marcap", 0) / 1e8, 0),
            "current_price": curr_price, "entry_price": entry_price,
            "ret_1m": round(float((curr_price - df["Close"].iloc[-21]) / df["Close"].iloc[-21] * 100) if len(df) >= 21 else 0.0, 2),
            "metrics": metrics, "pass_count": pass_count,
            "roe": fund.get("roe"), "debt_ratio": fund.get("debt_ratio"),
            "filter_details": {
                "Growth Composite": {"pass": f_growth, "reason": f"Comp {metrics['growth_composite']:+.1f}%"},
                "Dynamic MDD": {"pass": f_mdd, "reason": f"MDD {metrics['mdd']:.1f}% (Limit: {metrics['dynamic_mdd_limit']:.1f}%)"},
                "Liquidity": {"pass": f_liq, "reason": f"{metrics['liquidity_20d']:.0f}억"},
                "Momentum": {"pass": f_mom, "reason": f"Mom {metrics['momentum']:+.1f}%"},
                "Volatility": {"pass": f_vol, "reason": f"Vol {metrics['volatility']:.1f}%"},
                "Trend Strength": {"pass": f_trend, "reason": f"R² {metrics['trend_r2']:.2f}"}
            }
        })

    # ── Phase 2. Scoring & Ranking (크로스섹셔널 백분위 평가) ──
    if not candidates:
        return [], []

    c_df = pd.DataFrame([c["metrics"] for c in candidates])

    # 살아남은 종목들끼리의 상대 랭킹(Percentile, 상위일수록 1.0)
    s_growth = c_df["growth_composite"].rank(pct=True, na_option='bottom') * 30
    s_mom    = c_df["momentum"].rank(pct=True, na_option='bottom') * 30
    s_trend  = c_df["trend_r2"].rank(pct=True, na_option='bottom') * 20
    s_volmom = c_df["volume_momentum"].rank(pct=True, na_option='bottom') * 10
    s_sd     = c_df["supply_demand"].rank(pct=True, na_option='bottom') * 10

    factor_score = s_growth + s_mom + s_trend + s_volmom + s_sd

    confirmed, watchlist = [], []
    for i, c in enumerate(candidates):
        c["factor_score"] = round(factor_score.iloc[i], 2)
        c["net_income_yoy"] = round(c["metrics"]["net_yoy"], 2)
        c["momentum_score"] = round(c["metrics"]["momentum"], 2)
        c["screened_at"] = now_kst_str()

        # UI 호환을 위해 값 셋팅
        c["total_pass"] = c["pass_count"]
        c["hard_pass"]  = c["pass_count"]
        c["soft_pass"]  = len(SOFT_GATES)

        # Ranking 결과를 Filter Details에 주입 (UI 표시용)
        c["filter_details"]["Growth Rank"] = {"pass": True, "reason": f"상위 {100 - (s_growth.iloc[i]/30*100):.1f}%"}
        c["filter_details"]["Momentum Rank"] = {"pass": True, "reason": f"상위 {100 - (s_mom.iloc[i]/30*100):.1f}%"}
        c["filter_details"]["Trend Rank"] = {"pass": True, "reason": f"상위 {100 - (s_trend.iloc[i]/20*100):.1f}%"}
        c["filter_details"]["Volume Mom Rank"] = {"pass": True, "reason": f"상위 {100 - (s_volmom.iloc[i]/10*100):.1f}%"}
        c["filter_details"]["Supply Rank"] = {"pass": True, "reason": f"상위 {100 - (s_sd.iloc[i]/10*100):.1f}%"}

        if c["pass_count"] >= CONFIRM_FILTER_MIN:
            confirmed.append(c)
            log_fn(f"  ✅ [Confirm] {c['name']} | Score: {c['factor_score']} | 진입가: {c['entry_price']:,}")
        elif c["pass_count"] >= WATCHLIST_FILTER_MIN:
            watchlist.append(c)

    confirmed.sort(key=lambda x: x["factor_score"], reverse=True)
    watchlist.sort(key=lambda x: x["factor_score"], reverse=True)

    return confirmed[:top_n], watchlist[:top_n * 3]

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, (np.bool_,)): return bool(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        return super().default(obj)

def save_screening_result(supabase, confirmed: list, watchlist: list):
    ts = now_kst_str()
    supabase.table(TBL_SCREEN).upsert({"id": 1, "results": json.dumps(confirmed, ensure_ascii=False, cls=NumpyEncoder), "updated_at": ts}).execute()
    supabase.table(TBL_WATCH).upsert({"id": 1, "results": json.dumps(watchlist, ensure_ascii=False, cls=NumpyEncoder), "updated_at": ts}).execute()

def load_screening_result(supabase) -> tuple:
    c, w, ts = [], [], ""
    try:
        r1 = supabase.table(TBL_SCREEN).select("*").eq("id",1).execute()
        if r1.data: c, ts = json.loads(r1.data[0]["results"]), r1.data[0].get("updated_at","")
        r2 = supabase.table(TBL_WATCH).select("*").eq("id",1).execute()
        if r2.data: w = json.loads(r2.data[0]["results"])
    except: pass
    return c, w, ts
