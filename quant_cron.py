"""
quant_cron.py — 매일 14:30 KST 로컬 실행 (자동 매매 히스토리 + HTML 이메일 리포트 발송 포함)
"""
import os, time, json
from datetime import datetime, timedelta
import requests
import pandas as pd
from supabase import create_client
import FinanceDataReader as fdr

# 📧 이메일 발송을 위한 라이브러리 추가
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from quant_core import (
    now_kst, now_kst_str, load_filtered_universe,
    upsert_daily_rows, trim_old_rows,
    load_fundamental_from_db, save_fundamental_to_db,
    run_screening_from_db, save_screening_result, get_fundamental,
    PREFILTER_MARCAP_억, PREFILTER_TVOL_억, load_price_from_db, NumpyEncoder
)

# 🔐 [보안 적용 완료] 하드코딩된 위험한 키를 비우고 오직 안전한 환경변수만 바라보게 수정합니다.
# GitHub Secrets에 등록된 값을 실행 시점에만 메모리로 불러옵니다.
SUPABASE_URL     = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY", "")
KIS_APP_KEY      = os.environ.get("KIS_APP_KEY", "")
KIS_APP_SECRET   = os.environ.get("KIS_APP_SECRET", "")
KIS_BASE_URL     = os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
DART_API_KEY     = os.environ.get("DART_API_KEY", "")

# 📧 이메일 발송용 계정 정보도 안전하게 환경변수에서 주입받습니다.
SMTP_EMAIL       = os.environ.get("SMTP_EMAIL", "")
SMTP_PASSWORD    = os.environ.get("SMTP_PASSWORD", "")

SLEEP_SEC = 0.12

# ──────────────────────────────────────────
# DB 연동 없이 캐시 테이블 활용 포트폴리오 로직
# ──────────────────────────────────────────
def load_portfolio_cache(supabase):
    holdings, trades, history = [], [], []
    try:
        r1 = supabase.table("quant_screening_cache").select("results").eq("id", 11).execute()
        if r1.data: holdings = json.loads(r1.data[0]["results"])
        r2 = supabase.table("quant_screening_cache").select("results").eq("id", 12).execute()
        if r2.data: trades = json.loads(r2.data[0]["results"])
        r3 = supabase.table("quant_screening_cache").select("results").eq("id", 13).execute()
        if r3.data: history = json.loads(r3.data[0]["results"])
    except Exception as e:
        print("[알림] 포트폴리오 캐시 로드 중 (초기화 상태):", e)
    return holdings, trades, history

def save_portfolio_cache(supabase, holdings, trades, history):
    ts = now_kst_str()
    supabase.table("quant_screening_cache").upsert([
        {"id": 11, "results": json.dumps(holdings, ensure_ascii=False, cls=NumpyEncoder), "updated_at": ts},
        {"id": 12, "results": json.dumps(trades[-100:], ensure_ascii=False, cls=NumpyEncoder), "updated_at": ts},
        {"id": 13, "results": json.dumps(history[-252:], ensure_ascii=False, cls=NumpyEncoder), "updated_at": ts}
    ]).execute()

def process_virtual_portfolio(supabase, confirmed_list: list):
    """추격매수 퀀트 기반 매수/매도 이탈 및 KOSPI 대비 알파 계산"""
    today_str = now_kst().strftime("%Y-%m-%d")
    print(f"\n{'─'*50}\nSTEP 4. 자동 매매 및 이탈(Alpha) 시뮬레이션\n{'─'*50}")

    holdings, trades, history = load_portfolio_cache(supabase)
    confirmed_dict = {c['symbol']: c for c in confirmed_list}
    daily_returns = []
    new_holdings = []

    # 1. 매도 (Sell) 및 유지 로직
    for h in holdings:
        sym = h['symbol']
        df = load_price_from_db(supabase, sym)
        if df.empty or len(df) < 20:
            new_holdings.append(h)
            continue

        curr_price = int(df['Close'].iloc[-1])
        entry_date = pd.to_datetime(h.get('entry_date', today_str))
        df_held = df[df.index >= entry_date]

        highest_close = df_held['Close'].max() if not df_held.empty else curr_price

        high = df.get('High', df['Close'])
        low = df.get('Low', df['Close'])
        prev_close = df['Close'].shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean().iloc[-1]

        initial_risk = min(0.15, (2.5 * atr20) / h['entry_price'])
        initial_stop = h['entry_price'] * (1 - initial_risk)
        trailing_stop = highest_close - (2.5 * atr20)
        current_stop = max(initial_stop, trailing_stop)

        ma10 = df['Close'].iloc[-10:].mean()
        ma20 = df['Close'].iloc[-20:].mean()
        ma20_prev = df['Close'].iloc[-25:-5].mean()
        trend_broken = (ma10 < ma20) and (curr_price < ma20) and (ma20 < ma20_prev)

        ret = ((curr_price - h['entry_price']) / h['entry_price']) * 100
        daily_returns.append(ret)

        sell_reason = ""
        if curr_price <= current_stop:
            sell_reason = "트레일링/동적손절 이탈 (ATR)"
        elif trend_broken:
            sell_reason = "추세 다중붕괴 (MA10/20 & 기울기 하락)"
        elif ret >= 40.0:
            sell_reason = "목표 수익 달성 (+40% 부분익절)"

        if sell_reason:
            print(f"  [SELL 이탈] {h['name']} | 체결가: {curr_price:,}원 | 누적수익: {ret:+.2f}% ({sell_reason})")
            trades.append({
                "trade_date": today_str, "type": "SELL", "symbol": sym, "name": h['name'],
                "trade_price": curr_price, "return_rate": round(ret, 2), "reason": sell_reason
            })
        else:
            h["current_price"] = curr_price
            h["return_rate"] = round(ret, 2)
            h["stop_price"] = current_stop
            h["recent_30d"] = df["Close"].tail(30).tolist()
            new_holdings.append(h)
            print(f"  [HOLD 유지] {h['name']} | 수익률: {ret:+.2f}% | Stop: {current_stop:,.0f}")

    # 2. 매수 (Buy) 로직
    holding_symbols = {h['symbol'] for h in new_holdings}
    for sym, c in confirmed_dict.items():
        if sym not in holding_symbols:
            buy_price = c.get('entry_price', c['current_price'])
            print(f"  [BUY 진입] {c['name']} | 제안매수가: {buy_price:,}원 (추격매수 조건달성)")
            trades.append({
                "trade_date": today_str, "type": "BUY", "symbol": sym, "name": c['name'],
                "trade_price": buy_price, "return_rate": 0.0, "reason": "추격매수 신호"
            })
            new_holdings.append({
                "symbol": sym, "name": c['name'], "entry_date": today_str,
                "entry_price": buy_price, "current_price": c['current_price'], "return_rate": 0.0
            })

    # 3. KOSPI 알파 기록
    kospi = fdr.DataReader('KS11', (now_kst() - timedelta(days=5)).strftime('%Y-%m-%d'))
    k_ret = kospi['Close'].pct_change().iloc[-1] * 100 if not kospi.empty else 0.0

    avg_daily_ret = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    print(f"  [Alpha] 오늘 포트폴리오 수익: {avg_daily_ret:+.2f}% vs KOSPI: {k_ret:+.2f}%")

    history.append({
        "date": today_str, "portfolio_return": round(avg_daily_ret, 2), "kospi_return": round(k_ret, 2)
    })

    save_portfolio_cache(supabase, new_holdings, trades, history)

    # 이메일 발송을 위해 업데이트된 데이터 반환
    return new_holdings, trades, history


# ──────────────────────────────────────────
# 📧 HTML 이메일 발송 로직 추가 (프리미엄 다크테마 UI)
# ──────────────────────────────────────────
def send_daily_email_report(holdings, trades, history):
    print(f"\n{'─'*50}\nSTEP 5. 리포트 메일 발송 중...\n{'─'*50}")
    today_str = now_kst().strftime("%Y-%m-%d")

    # 🔐 환경변수에 이메일/비밀번호가 세팅되어 있지 않으면 조용히 스킵합니다.
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("  [x] 이메일 발송 취소: GitHub Secrets에 SMTP_EMAIL 또는 SMTP_PASSWORD가 설정되지 않았습니다.")
        return

    # 1. 오늘 발생한 매매 분리
    today_sells = [t for t in trades if t.get('type') == 'SELL' and t.get('trade_date') == today_str]
    today_buys = [t for t in trades if t.get('type') == 'BUY' and t.get('trade_date') == today_str]

    # 2. 당일 포트폴리오 성과
    day_change = history[-1]['portfolio_return'] if history else 0.0
    alpha = history[-1]['portfolio_return'] - history[-1]['kospi_return'] if history else 0.0
    avg_total_ret = sum([h.get('return_rate', 0) for h in holdings]) / len(holdings) if holdings else 0.0

    # 3. HTML 표(Table) 렌더링 헬퍼 (프리미엄 다크테마 - 글자색 강제지정 & table-layout: fixed)
    def render_sells_html():
        if not today_sells:
            return "<div style='color:#787B86; font-size:13px; padding:15px 0; text-align:center;'>오늘 매도된 종목이 없습니다.</div>"
        header = """
        <tr style='border-bottom: 1px solid #2B3139;'>
            <th style='padding:10px 4px; text-align:left; font-size:11px; color:#787B86; font-weight:600; width:25%;'>Stock</th>
            <th style='padding:10px 4px; text-align:right; font-size:11px; color:#787B86; font-weight:600; width:20%;'>Price</th>
            <th style='padding:10px 4px; text-align:right; font-size:11px; color:#787B86; font-weight:600; width:20%;'>P&L</th>
            <th style='padding:10px 4px; text-align:right; font-size:11px; color:#787B86; font-weight:600; width:35%;'>Reason</th>
        </tr>
        """
        rows = ""
        for s in today_sells:
            color = "#F04452" if s['return_rate'] > 0 else "#3182F6"
            rows += f"""
            <tr style='border-bottom: 1px solid #1E222D;'>
                <td style='padding: 12px 4px; text-align:left; font-size:13px; color:#FFFFFF; font-weight:bold;'>{s['name']}</td>
                <td style='padding: 12px 4px; text-align:right; font-size:12px; color:#D1D4DC;'>{s['trade_price']:,}</td>
                <td style='padding: 12px 4px; text-align:right; font-size:12px; color:{color}; font-weight:bold;'>{s['return_rate']:+.2f}%</td>
                <td style='padding: 12px 4px; text-align:right; font-size:11px; color:#787B86;'>{s['reason']}</td>
            </tr>
            """
        return f"<table style='width:100%; border-collapse: collapse; table-layout: fixed;'>{header}{rows}</table>"

    def render_buys_html():
        if not today_buys:
            return "<div style='color:#787B86; font-size:13px; padding:15px 0; text-align:center;'>오늘 신규 진입 종목이 없습니다.</div>"
        header = """
        <tr style='border-bottom: 1px solid #2B3139;'>
            <th style='padding:10px 4px; text-align:left; font-size:11px; color:#787B86; font-weight:600; width:30%;'>Stock</th>
            <th style='padding:10px 4px; text-align:right; font-size:11px; color:#787B86; font-weight:600; width:30%;'>Entry Price</th>
            <th style='padding:10px 4px; text-align:right; font-size:11px; color:#787B86; font-weight:600; width:40%;'>Signal</th>
        </tr>
        """
        rows = ""
        for b in today_buys:
            rows += f"""
            <tr style='border-bottom: 1px solid #1E222D;'>
                <td style='padding: 12px 4px; text-align:left; color:#089981; font-weight:700;'>{b['name']}</td>
                <td style='padding: 12px 0; text-align:right; color:#D1D4DC;'>{b['trade_price']:,}</td>
                <td style='padding: 12px 0; text-align:right; color:#787B86; font-size:12px;'>{b['reason']}</td>
            </tr>
            """
        return f"<table style='width:100%; border-collapse: collapse;'>{header}{rows}</table>"

    def render_holdings_html():
        if not holdings: return "<div style='color:#787B86; font-size:13px; padding:10px 0;'>현재 보유 중인 종목이 없습니다.</div>"

        header = """
        <tr style='border-bottom: 1px solid #2B3139; color:#787B86; font-size:11px; text-transform:uppercase;'>
            <th style='padding:12px 5px; text-align:left; font-weight:600;'>Stock</th>
            <th style='padding:12px 5px; text-align:right; font-weight:600;'>Entry</th>
            <th style='padding:12px 5px; text-align:right; font-weight:600;'>Current</th>
            <th style='padding:12px 5px; text-align:right; font-weight:600;'>P&L</th>
            <th style='padding:12px 5px; text-align:right; font-weight:600;'>Stop</th>
        </tr>
        """
        rows = ""
        for h in holdings:
            ret = h.get('return_rate', 0.0)
            color = "#F04452" if ret > 0 else ("#3182F6" if ret < 0 else "#D1D4DC")
            rows += f"""
            <tr style='border-bottom: 1px solid #1E222D;'>
                <td style='padding: 15px 4px; text-align:left; color:#FFFFFF; font-weight:600; font-size:13px;'>{h['name']}</td>
                <td style='padding: 15px 4px; text-align:right; color:#D1D4DC; font-size:12px;'>{h['entry_price']:,}</td>
                <td style='padding: 15px 4px; text-align:right; color:#D1D4DC; font-size:12px;'>{h['current_price']:,}</td>
                <td style='padding: 15px 4px; text-align:right; color:{color}; font-weight:700; font-size:12px;'>{ret:+.2f}%</td>
                <td style='padding: 15px 4px; text-align:right; color:#F8B12A; font-size:12px;'>{h.get('stop_price', 0):,.0f}</td>
            </tr>
            """
        return f"<table style='width:100%; border-collapse: collapse; table-layout: fixed;'>{header}{rows}</table>"

    # 4. 전체 HTML 이메일 템플릿 조립 (프리미엄 반응형 다크 모드)
    email_body = f"""
    <div style="background-color: #0B0E14; padding: 30px 15px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #D1D4DC; line-height: 1.6; min-height: 100%;">
        <div style="max-width: 600px; margin: 0 auto; width: 100%; box-sizing: border-box;">
            
            <!-- 헤더 타이틀 -->
            <div style="padding: 10px 0 25px 0;">
                <h2 style="margin: 0; color: #FFFFFF; font-size: 24px; font-weight: bold; letter-spacing: 0.5px;">
                    <span style="color: #F8B12A; margin-right: 8px;">⚡</span> QUANT DESK
                </h2>
                <p style="margin: 6px 0 0 0; color: #787B86; font-size: 13px;">일일 포트폴리오 리포트 &nbsp;|&nbsp; {today_str}</p>
            </div>

            <!-- 알림 메시지 -->
            <div style="margin-bottom: 25px;">
                <p style="margin: 0; color: #E2E8F0; font-size: 14px; font-weight: 500;">
                    오늘 발생한 매매 내역과 포트폴리오 현황을 안내해 드립니다.
                </p>
            </div>

            <!-- Portfolio Overview Card -->
            <div style="margin-bottom: 10px; color: #787B86; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">📊 Portfolio Overview (Holdings {len(holdings)})</div>
            <div style="background-color: #151924; border-radius: 12px; padding: 22px; margin-bottom: 30px; border: 1px solid #1E222D; box-sizing: border-box;">
                <div style="color: #787B86; font-size: 12px; margin-bottom: 6px; font-weight: 500;">Cumulative Return (Avg of holdings)</div>
                <div style="color: {'#F04452' if avg_total_ret > 0 else ('#3182F6' if avg_total_ret < 0 else '#FFFFFF')}; font-size: 38px; font-weight: 800; margin-bottom: 22px; line-height: 1;">
                    {avg_total_ret:+.2f}%
                </div>
                
                <table style="width: 100%; border-top: 1px solid #2B3139; padding-top: 18px; border-collapse: collapse; table-layout: fixed;">
                    <tr>
                        <td style="width: 50%; padding: 0;">
                            <div style="color: #787B86; font-size: 11px; margin-bottom: 3px; font-weight: 500;">Day Change</div>
                            <div style="color: {'#F04452' if day_change > 0 else ('#3182F6' if day_change < 0 else '#FFFFFF')}; font-size: 16px; font-weight: bold;">{day_change:+.2f}%</div>
                        </td>
                        <td style="width: 50%; padding: 0; border-left: 1px solid #2B3139; padding-left: 20px;">
                            <div style="color: #787B86; font-size: 11px; margin-bottom: 3px; font-weight: 500;">Alpha (vs KOSPI)</div>
                            <div style="color: {'#20C997' if alpha > 0 else ('#3182F6' if alpha < 0 else '#FFFFFF')}; font-size: 16px; font-weight: bold;">{alpha:+.2f}%</div>
                        </td>
                    </tr>
                </table>
            </div>

            <!-- Current Holdings Card -->
            <div style="margin-bottom: 10px; color: #E2E8F0; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">📁 Holdings ({len(holdings)})</div>
            <div style="background-color: #151924; border-radius: 12px; padding: 15px; margin-bottom: 30px; border: 1px solid #1E222D; box-sizing: border-box; overflow-x: auto;">
                {render_holdings_html()}
            </div>

            <!-- Today's Buys Card -->
            <div style="margin-bottom: 12px; color: #089981; font-size: 14px; font-weight: 600;">⚡ Today's Buys ({len(today_buys)})</div>
            <div style="background-color: #151924; border-radius: 12px; padding: 15px; margin-bottom: 30px; border: 1px solid #1E222D; box-sizing: border-box;">
                {render_buys_html()}
            </div>

            <!-- Today's Sells Card -->
            <div style="margin-bottom: 10px; color: #F04452; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">🚨 Today's Sells ({len(today_sells)})</div>
            <div style="background-color: #151924; border-radius: 12px; padding: 15px; margin-bottom: 30px; border: 1px solid #1E222D; box-sizing: border-box;">
                {render_sells_html()}
            </div>

            <!-- Footer (Clean & Minimal) -->
            <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #1E222D; text-align: center; color: #434651; font-size: 11px; letter-spacing: 0.5px;">
                © 2026 QUANT DESK. All rights reserved.
            </div>
            
        </div>
    </div>
    """

    # 5. SMTP 메일 발송 실행
    try:
        msg = MIMEMultipart()
        # 🔐 환경변수에서 불러온 이메일을 사용합니다.
        msg['From'] = SMTP_EMAIL
        msg['To'] = 'ansdyd23@kakao.com'

        # 메일 제목
        msg['Subject'] = f'[QUANT DESK] ⚡ Avg {avg_total_ret:+.1f}% · 매수{len(today_buys)}·매도{len(today_sells)} · {today_str}'

        msg.attach(MIMEText(email_body, 'html'))

        # 네이버 SMTP 전송
        server = smtplib.SMTP("smtp.naver.com", 587)
        server.starttls()
        # 🔐 환경변수에서 불러온 이메일과 비밀번호를 사용합니다.
        server.login(SMTP_EMAIL, SMTP_PASSWORD)

        to_addrs = ["ansdyd23@kakao.com"]
        server.sendmail(SMTP_EMAIL, to_addrs, msg.as_string())
        server.quit()

        print("  [✓] HTML 리포트 이메일을 성공적으로 전송했습니다.")
    except Exception as e:
        print(f"  [x] 이메일 발송 실패: {e}")


# API Helper Functions
def load_dart_corp_map(dart_api_key: str) -> dict:
    if not dart_api_key: return {}
    try:
        import zipfile, io, xml.etree.ElementTree as ET
        res = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": dart_api_key}, timeout=30)
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        root = ET.fromstring(zf.read("CORPCODE.xml").decode("utf-8"))
        corp_map = {}
        for item in root.findall("list"):
            code = (item.findtext("stock_code") or "").strip()
            corp = (item.findtext("corp_code")  or "").strip()
            if len(code) == 6 and corp: corp_map[code] = corp
        return corp_map
    except: return {}

_token_cache = {"token": None, "expires_at": None}
def get_kis_token() -> str | None:
    now = now_kst()
    if _token_cache["token"] and _token_cache["expires_at"] and now < _token_cache["expires_at"]: return _token_cache["token"]
    try:
        res = requests.post(f"{KIS_BASE_URL}/oauth2/tokenP", json={"grant_type":"client_credentials", "appkey":KIS_APP_KEY,"appsecret":KIS_APP_SECRET}, headers={"content-type":"application/json"}, timeout=30)
        token = res.json().get("access_token")
        if token:
            _token_cache["token"] = token
            _token_cache["expires_at"] = now + timedelta(hours=23)
        return token
    except: return None

def fetch_today_price(symbol: str, token: str) -> tuple:
    try:
        res = requests.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price", headers={"content-type":"application/json", "authorization":f"Bearer {token}", "appkey":KIS_APP_KEY,"appsecret":KIS_APP_SECRET, "tr_id":"FHKST01010100"}, params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":symbol}, timeout=5)
        data, rt_cd, msg, out = res.json(), res.json().get("rt_cd",""), res.json().get("msg1",""), res.json().get("output",{})
        if rt_cd != "0": return None, "오류"
        price = int(out.get("stck_prpr",0))
        if price == 0: return None, "가격0"
        return {"close": price, "open": int(out.get("stck_oprc",0)), "high": int(out.get("stck_hgpr",0)), "low": int(out.get("stck_lwpr",0)), "volume": int(out.get("acml_vol",0))}, ""
    except: return None, "예외"

def fetch_supply_demand(symbol: str, token: str) -> tuple:
    try:
        res = requests.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor", headers={"content-type":"application/json", "authorization":f"Bearer {token}", "appkey":KIS_APP_KEY,"appsecret":KIS_APP_SECRET, "tr_id":"FHKST01010900"}, params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":symbol}, timeout=5)
        out = res.json().get("output",[])
        f_sum = sum(float(r.get("frgn_ntby_qty",0) or 0) for r in out[:20])
        i_sum = sum(float(r.get("orgn_ntby_qty",0) or 0) for r in out[:20])
        return f_sum, i_sum
    except: return 0.0, 0.0

def fetch_with_retry(fn, symbol: str, token: str, max_retry: int = 3):
    for attempt in range(max_retry):
        res = fn(symbol, token)
        if isinstance(res, tuple) and len(res)==2 and isinstance(res[1], str) and res[1]=="API초과":
            time.sleep(5*(attempt+1))
            continue
        return res
    return None, "초과"

# ──────────────────────────────────────────
# UI Stock Search 용 종목 마스터 캐싱 (id: 99)
# ──────────────────────────────────────────
def save_krx_master_cache(supabase, universe: pd.DataFrame):
    ts = now_kst_str()
    krx_list = []
    for _, row in universe.iterrows():
        sym = row["Symbol"]
        name = row.get("Name", sym)
        krx_list.append({
            "Symbol": sym,
            "Name": name,
            "SearchStr": f"{name} ({sym})"
        })
    try:
        supabase.table("quant_screening_cache").upsert([
            {"id": 99, "results": json.dumps(krx_list, ensure_ascii=False), "updated_at": ts}
        ]).execute()
        print(f"  [✓] 종목 마스터(id=99) 캐시 갱신 완료 ({len(krx_list)}건)")
    except Exception as e:
        print(f"  [x] 종목 마스터 캐시 갱신 실패: {e}")

def is_market_open_today(token):
    today = datetime.now().strftime("%Y%m%d")

    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/chk-holiday"

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": "CTCA0903R"    # 실전/모의에 맞는 TR_ID 사용
    }

    params = {
        "BASS_DT": today
    }

    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()

    data = res.json()
    return data["output"][0]["opnd_yn"] == "Y"

def run_batch(supabase):
    print(f"\n{'='*60}\n배치 시작: {now_kst_str()}\n{'='*60}")
    token = get_kis_token()

    if not is_market_open_today(token):
        print("오늘은 휴장일입니다.")
        return

    print("오늘은 개장일입니다. 배치를 계속 진행합니다.")

    dart_corp_map = load_dart_corp_map(DART_API_KEY)

    universe = load_filtered_universe(PREFILTER_MARCAP_억, PREFILTER_TVOL_억)
    if universe.empty: return
    total = len(universe)

    print(f"\nSTEP 0. 종목 마스터 캐시 갱신")
    save_krx_master_cache(supabase, universe)

    print(f"\nSTEP 1. 펀더멘털 수집")
    for i, (_, row) in enumerate(universe.iterrows()):
        try: get_fundamental(supabase, row["Symbol"], row["Name"], DART_API_KEY, dart_corp_map)
        except: pass
        if (i+1)%50==0: print(f"  [{i+1}/{total}] 수집중...")

    print(f"\nSTEP 2. 당일 가격 + 수급 수집")
    for i, (_, row) in enumerate(universe.iterrows()):
        sym, name = row["Symbol"], row["Name"]
        price, _ = fetch_with_retry(fetch_today_price, sym, token)
        if price and price["close"] > 0:
            upsert_daily_rows(supabase, sym, name, [{"date": now_kst().strftime("%Y-%m-%d"), **price}])
            trim_old_rows(supabase, sym)
            f_net, i_net = fetch_with_retry(fetch_supply_demand, sym, token)
            if f_net != 0 or i_net != 0:
                fund_row = load_fundamental_from_db(supabase, sym) or {}
                fund_row.update({"foreign_net_buy": f_net, "institute_net_buy": i_net})
                save_fundamental_to_db(supabase, sym, name, fund_row)
        time.sleep(SLEEP_SEC)
        if (i+1) % 50 == 0 or (i+1) == total:
            print(f"  [{i+1}/{total}] 처리중...")

    print(f"\nSTEP 3. 스크리닝 (정통 퀀트 추격매수 검증)")
    confirmed, watchlist = run_screening_from_db(supabase, universe)
    save_screening_result(supabase, confirmed, watchlist)

    # 💡 업데이트된 포트폴리오 데이터를 반환받아 이메일로 쏩니다!
    final_holdings, final_trades, final_history = process_virtual_portfolio(supabase, confirmed)

    # 📧 이메일 발송
    send_daily_email_report(final_holdings, final_trades, final_history)

    print(f"\n{'='*60}\n✅ 배치 완료: {now_kst_str()}\n{'='*60}")

    print(f"🏆 신규 추격매수 확정 (총 {len(confirmed)}개) — 6/6 조건 완벽 달성")
    for r in confirmed:
        print(f"  {r['name']} | 점수 {r['factor_score']} | 진입가 {r['entry_price']:,}원")

    print(f"\n👀 예비 관심 종목 (총 {len(watchlist)}개) — 4/6 조건 이상 달성 (상위 15개 출력)")
    for r in watchlist[:15]:
        print(f"  {r['name']} | 통과 {r['total_pass']}/6 | 점수 {r['factor_score']}")

def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    run_batch(supabase)

if __name__ == "__main__":
    main()
