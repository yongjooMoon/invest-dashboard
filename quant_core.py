"""
quant_core.py
─────────────────────────────────────────────
공용 유틸 + 8대 필터 + 팩터 점수화 스크리닝 엔진

필터 구성 (기술적 6 + 펀더멘털 2)
──────────────────────────────────────────────
[기술적 — 가격/거래량 기반]
 1. 12-1 모멘텀       Jegadeesh & Titman (1993)
 2. 저변동성          Ang et al. (2006)
 3. MDD 리스크        실무 리스크 표준
 4. 유동성            Amihud (2002)
 5. 거래량 모멘텀     Lee & Swaminathan (2000)
 6. 추세 강도         Novy-Marx (2013) 변형

[펀더멘털 — DART API + 네이버금융]
 7. 실적 모멘텀       영업이익 YoY > 0, EPS YoY > 0
 8. 재무 건전성       ROE ≥ 5%, 부채비율 ≤ 200%

필요 API
──────────────────────────────────────────────
- DART OpenAPI  : https://opendart.fss.or.kr  (무료, 재무제표)
- 네이버금융     : 크롤링 보조 (EPS, 동종업종 PER 등)
- 한투 API      : quant_cron.py 에서 당일 가격 수집 (기존)
"""

import json
import re
import time
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
TBL_DAILY      = "stock_daily"            # 종목별 일봉 (롤링 756일)
TBL_SCREEN     = "quant_screening_cache"  # 스크리닝 결과
TBL_FUNDA      = "stock_fundamental"      # 펀더멘털 캐시 (연 1회 갱신)

ROLLING_DAYS   = 756    # 유지할 최대 거래일 수 (≈3년)
MIN_DAYS       = 252    # 필터 계산 최소 거래일
FUNDA_TTL_SEC  = 86400 * 90  # 펀더멘털 캐시 유효기간 90일


# ══════════════════════════════════════════
# [A] Supabase 일봉 적재 / 조회
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
            "low": "Low",   "close": "Close", "volume": "Volume"
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
        print(f"[DB] {symbol} 오래된 행 삭제 실패: {e}")


# ══════════════════════════════════════════
# [B] 펀더멘털 수집 (DART API + 네이버금융)
#
# 필요 Supabase DDL:
# CREATE TABLE stock_fundamental (
#     symbol       TEXT PRIMARY KEY,
#     name         TEXT,
#     roe          NUMERIC,   -- %
#     debt_ratio   NUMERIC,   -- %  (부채비율)
#     op_profit_cur  NUMERIC, -- 당해 영업이익 (억원)
#     op_profit_prev NUMERIC, -- 전년 영업이익 (억원)
#     eps_cur      NUMERIC,   -- 당해 EPS
#     eps_prev     NUMERIC,   -- 전년 EPS
#     updated_at   TEXT
# );
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
    """
    DART OpenAPI — 단일회사 주요재무정보 (영업이익, EPS, ROE, 부채비율)
    corp_code: DART 고유번호 (8자리)
    """
    if year is None:
        year = now_kst().year - 1   # 전년도 확정 재무제표 기준

    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    result = {"roe": None, "debt_ratio": None, "op_profit": None, "eps": None}

    for reprt_code in ["11011", "11012"]:   # 11011=사업보고서, 11012=반기
        try:
            params = {
                "crtfc_key": dart_api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": reprt_code,
                "fs_div": "CFS",   # 연결재무제표 우선, 없으면 OFS
            }
            res = requests.get(url, params=params, timeout=10).json()
            if res.get("status") != "000":
                continue

            for item in res.get("list", []):
                acnt = item.get("account_nm", "")
                val_str = item.get("thstrm_amount", "0")
                val = _parse_num(val_str) / 1e8   # 원 → 억원

                if "영업이익" in acnt and result["op_profit"] is None:
                    result["op_profit"] = val
                if "ROE" in acnt and result["roe"] is None:
                    result["roe"] = _parse_num(item.get("thstrm_amount", "0"))
                if "부채비율" in acnt and result["debt_ratio"] is None:
                    result["debt_ratio"] = _parse_num(item.get("thstrm_amount", "0"))

            # EPS — 주당순이익 항목
            url_eps = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
            res_eps = requests.get(url_eps, params={**params, "fs_div": "OFS"}, timeout=10).json()
            for item in res_eps.get("list", []):
                if "주당순이익" in item.get("account_nm", ""):
                    result["eps"] = _parse_num(item.get("thstrm_amount", "0"))
                    break

            if any(v is not None for v in result.values()):
                break   # 데이터 있으면 중단
        except Exception as e:
            print(f"  [DART] {corp_code} 조회 실패: {e}")

    return result


def fetch_naver_fundamental(symbol: str) -> dict:
    """
    네이버금융 크롤링 — ROE, 영업이익, EPS, 부채비율 보조 수집
    DART 데이터가 없는 소형주 보완용
    """
    result = {"roe": None, "debt_ratio": None,
              "op_profit_cur": None, "op_profit_prev": None,
              "eps_cur": None, "eps_prev": None}
    try:
        url = f"https://finance.naver.com/item/main.naver?code={symbol}"
        headers = {"User-Agent": "Mozilla/5.0"}
        soup = BeautifulSoup(
            requests.get(url, headers=headers, timeout=8).content, "html.parser"
        )

        # 연간 실적 테이블 (cop_analysis)
        table = soup.select_one("div.cop_analysis table")
        if table:
            rows = table.select("tbody tr")
            for tr in rows:
                th = tr.select_one("th")
                if not th:
                    continue
                label = th.text.strip()
                tds   = tr.select("td")

                # 연간 컬럼: 보통 0~3 = 과거 4개년
                # 우측 2개 컬럼이 직전년도 / 당해년도 (E)
                if len(tds) >= 2:
                    prev_val = _parse_num(tds[-2].text)
                    cur_val  = _parse_num(tds[-1].text)
                else:
                    continue

                if "영업이익" in label:
                    result["op_profit_prev"] = prev_val
                    result["op_profit_cur"]  = cur_val
                elif "EPS" in label:
                    result["eps_prev"] = prev_val
                    result["eps_cur"]  = cur_val
                elif "ROE" in label:
                    result["roe"] = cur_val

        # 부채비율 — 기업개요 섹션
        for th in soup.find_all("th"):
            if "부채비율" in th.text:
                td = th.find_next_sibling("td")
                if td:
                    result["debt_ratio"] = _parse_num(td.text)
                    break

    except Exception as e:
        print(f"  [NAVER] {symbol} 크롤링 실패: {e}")

    return result


def load_fundamental_from_db(supabase, symbol: str) -> dict | None:
    """캐시된 펀더멘털 로드. 만료됐으면 None 반환."""
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
        "symbol":         symbol,
        "name":           name,
        "roe":            data.get("roe"),
        "debt_ratio":     data.get("debt_ratio"),
        "op_profit_cur":  data.get("op_profit_cur"),
        "op_profit_prev": data.get("op_profit_prev"),
        "eps_cur":        data.get("eps_cur"),
        "eps_prev":       data.get("eps_prev"),
        "updated_at":     now_kst_str(),
    }
    supabase.table(TBL_FUNDA).upsert(payload).execute()


def get_fundamental(supabase, symbol: str, name: str,
                    dart_api_key: str = "", dart_corp_map: dict = None) -> dict:
    """
    펀더멘털 데이터 취득 우선순위:
      1. DB 캐시 (90일 유효)
      2. DART API (dart_api_key 있을 때)
      3. 네이버금융 크롤링 (폴백)
    """
    cached = load_fundamental_from_db(supabase, symbol)
    if cached:
        return cached

    data = {"roe": None, "debt_ratio": None,
            "op_profit_cur": None, "op_profit_prev": None,
            "eps_cur": None, "eps_prev": None}

    # DART 우선 시도
    if dart_api_key and dart_corp_map:
        corp_code = dart_corp_map.get(symbol)
        if corp_code:
            cur_year  = now_kst().year - 1
            prev_year = cur_year - 1
            cur  = fetch_dart_financial(corp_code, dart_api_key, cur_year)
            prev = fetch_dart_financial(corp_code, dart_api_key, prev_year)
            data.update({
                "op_profit_cur":  cur.get("op_profit"),
                "op_profit_prev": prev.get("op_profit"),
                "eps_cur":        cur.get("eps"),
                "eps_prev":       prev.get("eps"),
                "roe":            cur.get("roe"),
                "debt_ratio":     cur.get("debt_ratio"),
            })

    # 데이터 부족하면 네이버 크롤링 보완
    if data["op_profit_cur"] is None or data["eps_cur"] is None:
        naver = fetch_naver_fundamental(symbol)
        for k, v in naver.items():
            if data.get(k) is None and v is not None:
                data[k] = v
        time.sleep(0.3)   # 크롤링 딜레이

    save_fundamental_to_db(supabase, symbol, name, data)
    return data


# ══════════════════════════════════════════
# [C] 8대 필터
# ══════════════════════════════════════════

# ── 기술적 필터 (1~6) ─────────────────────

def filter_momentum(df: pd.DataFrame) -> dict:
    """[1] 12-1 모멘텀 (Jegadeesh & Titman 1993): 수익률 > 0"""
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
    """[5] 거래량 모멘텀 (Lee & Swaminathan 2000): 5일/60일 거래량 ≥ 1.3x"""
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


# ── 펀더멘털 필터 (7~8) ───────────────────

def filter_earnings_momentum(fund: dict) -> dict:
    """
    [7] 실적 모멘텀 — 한국시장 핵심 팩터
    영업이익 YoY > 0  AND  EPS YoY > 0

    왜 ROE보다 실적 모멘텀이 강력한가:
      시장은 현재 ROE가 아닌 '미래 이익 증가'를 먼저 반영한다.
      SK하이닉스 24~26년 강세: ROE가 아닌 영업이익 급증 기대가 선행.
    """
    op_cur  = fund.get("op_profit_cur")
    op_prev = fund.get("op_profit_prev")
    eps_cur  = fund.get("eps_cur")
    eps_prev = fund.get("eps_prev")

    # 데이터 없으면 중립 통과 (펀더멘털 미수집 종목을 무조건 탈락시키지 않음)
    if op_cur is None and eps_cur is None:
        return {"pass": True, "value": None,
                "reason": "펀더멘털 미수집 — 중립 통과"}

    op_yoy  = None
    eps_yoy = None
    op_pass  = True   # 데이터 없으면 통과
    eps_pass = True

    if op_cur is not None and op_prev not in (None, 0):
        op_yoy  = (op_cur - op_prev) / abs(op_prev) * 100
        op_pass = op_yoy > 0

    if eps_cur is not None and eps_prev not in (None, 0):
        eps_yoy  = (eps_cur - eps_prev) / abs(eps_prev) * 100
        eps_pass = eps_yoy > 0

    passed = op_pass and eps_pass
    reason_parts = []
    if op_yoy  is not None: reason_parts.append(f"영업이익YoY {op_yoy:+.1f}%")
    if eps_yoy is not None: reason_parts.append(f"EPS YoY {eps_yoy:+.1f}%")
    reason = " | ".join(reason_parts) if reason_parts else "데이터 없음"

    return {
        "pass":    passed,
        "value":   round(float(op_yoy), 2) if op_yoy is not None else None,
        "reason":  reason + " (기준: 둘 다 >0)",
        "op_yoy":  round(float(op_yoy),  2) if op_yoy  is not None else None,
        "eps_yoy": round(float(eps_yoy), 2) if eps_yoy is not None else None,
    }


def filter_financial_health(fund: dict) -> dict:
    """
    [8] 재무 건전성
    ROE ≥ 5%        — 최소 수익성 (Fama & French 1992)
    부채비율 ≤ 200%  — 한국 제조업 현실 기준
                       (150%는 우량 대형주 기준으로 중소형 제조업에 부적절)
    """
    roe        = fund.get("roe")
    debt_ratio = fund.get("debt_ratio")

    # 데이터 없으면 중립 통과
    if roe is None and debt_ratio is None:
        return {"pass": True, "value": None,
                "reason": "재무 미수집 — 중립 통과"}

    roe_pass   = roe        >= 5.0   if roe        is not None else True
    debt_pass  = debt_ratio <= 200.0 if debt_ratio is not None else True
    passed     = roe_pass and debt_pass

    parts = []
    if roe        is not None: parts.append(f"ROE {roe:.1f}% (기준 ≥5%)")
    if debt_ratio is not None: parts.append(f"부채비율 {debt_ratio:.0f}% (기준 ≤200%)")

    return {
        "pass":   passed,
        "value":  round(float(roe), 2) if roe is not None else None,
        "reason": " | ".join(parts),
    }


# ── 필터 목록 (기술 6 + 펀더멘털 2) ────────
TECH_FILTERS = [
    ("모멘텀 (Jegadeesh & Titman 1993)",      filter_momentum),
    ("저변동성 (Ang et al. 2006)",             filter_volatility),
    ("MDD 리스크",                             filter_mdd),
    ("유동성 (Amihud 2002)",                   filter_liquidity),
    ("거래량 모멘텀 (Lee & Swaminathan 2000)", filter_volume_momentum),
    ("추세 강도 (Novy-Marx 2013 변형)",         filter_trend_strength),
]

FUNDA_FILTERS = [
    "실적 모멘텀 (영업이익·EPS YoY)",
    "재무 건전성 (ROE·부채비율)",
]

ALL_FILTER_NAMES = [n for n, _ in TECH_FILTERS] + FUNDA_FILTERS


# ══════════════════════════════════════════
# [D] 팩터 점수화 (최종 랭킹)
#
# ALL-PASS 이후 점수로 순위를 매긴다.
# 단순 모멘텀 랭킹보다 멀티팩터 복합 점수가
# 안정적인 초과수익을 낸다 (Asness et al. 2013).
# ══════════════════════════════════════════
def compute_factor_score(filter_results: dict, fund: dict) -> float:
    """
    팩터별 정규화 점수 합산 (0 ~ 100점)

    가중치 설계 근거:
      모멘텀 30%  — 단기 추격매수 목적이므로 최고 비중
      실적모멘텀 25% — 한국시장 핵심 팩터
      추세강도 15% — 추격매수 타이밍 보조
      거래량 10%  — 수급 확인
      변동성 10%  — 리스크 패널티
      재무건전성 10% — 최소 퀄리티 확인
    """
    score = 0.0

    # 1. 모멘텀 점수 (30점) — 0~300% 범위를 0~30으로 정규화
    mom = filter_results.get(TECH_FILTERS[0][0], {}).get("value") or 0
    score += min(30.0, max(0.0, mom / 10.0))

    # 2. 실적 모멘텀 점수 (25점)
    op_yoy  = (fund.get("op_profit_cur", 0) or 0) - (fund.get("op_profit_prev", 0) or 0)
    eps_yoy = (fund.get("eps_cur", 0) or 0)       - (fund.get("eps_prev", 0) or 0)
    em_score = 0.0
    if op_yoy  > 0: em_score += 12.5
    if eps_yoy > 0: em_score += 12.5
    score += em_score

    # 3. 추세 강도 (15점) — R² 기반
    slope = filter_results.get(TECH_FILTERS[5][0], {}).get("value") or 0
    score += min(15.0, max(0.0, abs(slope) * 5.0)) if slope > 0 else 0

    # 4. 거래량 모멘텀 (10점)
    vr = filter_results.get(TECH_FILTERS[4][0], {}).get("value") or 0
    score += min(10.0, max(0.0, (vr - 1.3) / 0.7 * 10.0))

    # 5. 저변동성 패널티/보너스 (10점) — 낮을수록 점수 높음
    vol = filter_results.get(TECH_FILTERS[1][0], {}).get("value") or 60
    score += max(0.0, (60.0 - vol) / 60.0 * 10.0)

    # 6. 재무 건전성 (10점)
    roe = fund.get("roe") or 0
    score += min(10.0, max(0.0, roe / 3.0))   # ROE 30% = 만점

    return round(score, 2)


# ══════════════════════════════════════════
# [E] 스크리닝 엔진
# ══════════════════════════════════════════
def run_screening_from_db(supabase, universe_df: pd.DataFrame,
                          top_n: int = 10, log_fn=print,
                          dart_api_key: str = "",
                          dart_corp_map: dict = None) -> list:
    """
    1단계: 기술적 6필터 ALL-PASS (빠른 조기 탈락)
    2단계: 펀더멘털 2필터 ALL-PASS
    3단계: 팩터 점수화 → 상위 top_n 반환
    """
    results = []
    total   = len(universe_df)

    for i, (_, row) in enumerate(universe_df.iterrows()):
        symbol = row["Symbol"]
        name   = row.get("Name", symbol)
        log_fn(f"[{i+1}/{total}] {name} ({symbol})")

        # ── 1단계: 기술적 필터 ──
        df = load_price_from_db(supabase, symbol)
        if df.empty or len(df) < 60:
            continue

        tech_results = {}
        tech_pass = True
        for fname, ffn in TECH_FILTERS:
            res = ffn(df)
            tech_results[fname] = res
            if not res["pass"]:
                tech_pass = False
                break   # 조기 탈락

        if not tech_pass:
            continue

        # ── 2단계: 펀더멘털 필터 ──
        fund = get_fundamental(supabase, symbol, name, dart_api_key, dart_corp_map)

        em_result = filter_earnings_momentum(fund)
        fh_result = filter_financial_health(fund)

        if not em_result["pass"] or not fh_result["pass"]:
            log_fn(f"  → 펀더멘털 탈락")
            continue

        # ── 3단계: 팩터 점수 ──
        all_filter_results = {
            **tech_results,
            FUNDA_FILTERS[0]: em_result,
            FUNDA_FILTERS[1]: fh_result,
        }
        factor_score = compute_factor_score(tech_results, fund)

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
            "momentum_score":       tech_results[TECH_FILTERS[0][0]]["value"] or 0,
            "factor_score":         factor_score,
            "annual_vol":           tech_results[TECH_FILTERS[1][0]]["value"],
            "mdd":                  tech_results[TECH_FILTERS[2][0]]["value"],
            "avg_trading_value_억": tech_results[TECH_FILTERS[3][0]]["value"],
            "vol_ratio":            tech_results[TECH_FILTERS[4][0]]["value"],
            "trend_slope":          tech_results[TECH_FILTERS[5][0]]["value"],
            "op_profit_yoy":        em_result.get("op_yoy"),
            "eps_yoy":              em_result.get("eps_yoy"),
            "roe":                  fund.get("roe"),
            "debt_ratio":           fund.get("debt_ratio"),
            "filter_details":       all_filter_results,
            "screened_at":          now_kst_str(),
        })
        log_fn(f"  ✅ 통과 | 팩터점수: {factor_score}")

    # 팩터 점수 기준 정렬
    results.sort(key=lambda x: x["factor_score"], reverse=True)
    return results[:top_n]


# ══════════════════════════════════════════
# [F] 스크리닝 결과 저장 / 조회
# ══════════════════════════════════════════
def save_screening_result(supabase, results: list):
    supabase.table(TBL_SCREEN).upsert({
        "id":         1,
        "results":    json.dumps(results, ensure_ascii=False),
        "updated_at": now_kst_str(),
    }).execute()


def load_screening_result(supabase) -> tuple:
    try:
        res = supabase.table(TBL_SCREEN).select("*").eq("id", 1).execute()
        if res.data:
            row = res.data[0]
            return json.loads(row["results"]), row.get("updated_at", "")
    except:
        pass
    return [], ""
