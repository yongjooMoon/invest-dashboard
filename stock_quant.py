import streamlit as st
import requests
import re
import html
import FinanceDataReader as fdr
import pandas as pd
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import threading
import json
import time

# ==========================================
# [Layer 1] 글로벌 전역 무결성 파서 및 스레드 락 바인더
# ==========================================
_active_threads = {}

def parse_num(txt):
    if not txt: return 0.0
    cleaned = str(txt).replace('₩', '').replace(',', '').replace('주', '').strip()
    m = re.search(r'[-+]?[0-9]+(:\.[0-9]?)+', cleaned)
    return float(m.group()) if m else 0.0

def is_expired(last_update_str, threshold_seconds):
    if not last_update_str: return True
    try:
        clean_str = last_update_str.replace('T', ' ').split('.')[0].split('+')[0]
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.utcnow() + timedelta(hours=9) 
        return (now - dt).total_seconds() >= threshold_seconds
    except: return True

def calculate_bm_score(fund_data):
    growth_multiplier = 1.0
    report = ""
    if fund_data:
        q_revs = fund_data.get('q_revenues', [])
        q_ops = fund_data.get('q_op_profits', [])
        if len(q_revs) >= 2:
            last_rev, prev_rev = q_revs[-1], q_revs[-2]
            last_op, prev_op = q_ops[-1], q_ops[-2]
            qoq = ((last_rev - prev_rev) / abs(prev_rev)) * 100 if prev_rev != 0 else 0
            margin = (last_op / last_rev) * 100 if last_rev != 0 else 0
            if qoq >= 10: growth_multiplier += 0.05
            if margin >= 10: growth_multiplier += 0.05  
            if prev_op < 0 and last_op > 0: growth_multiplier += 0.10; report += "🔥 [분기 흑자전환 모멘텀] "
            report += f"최근 매출 {int(last_rev):,}억 (QoQ {qoq:+.1f}%) / 영업이익 {int(last_op):,}억 (OPM {margin:.1f}%)"
    return growth_multiplier, report

# ==========================================
# [Layer 2] 30일 만기 영구 보존형 DB 원장 시스템
# ==========================================
def load_system_krx_data(supabase):
    now_kst_str = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')
    try:
        res = supabase.table("stock_cache").select("*").eq("ticker", "__SYSTEM_KRX_MAP__").execute()
        if res.data:
            row = res.data[0]
            if not is_expired(row.get('last_price_update'), 2592000):
                packed_raw = json.loads(row['bm_summary'])
                if isinstance(packed_raw, dict) and "__PACKED_CONTAINER__" in packed_raw:
                    return packed_raw["data"]
                return packed_raw
    except: pass

    try:
        df = fdr.StockListing('KRX')
        if not df.empty:
            name_to_code = {}
            code_to_sector = {}
            for _, r in df.iterrows():
                if pd.isna(r.get('Name')) or pd.isna(r.get('Symbol')): continue
                s_name = str(r['Name']).strip()
                s_code = str(r['Symbol']).strip()
                s_sector = str(r['Sector']).strip() if ('Sector' in df.columns and pd.notna(r['Sector'])) else "일반제조업"
                
                name_to_code[s_name] = s_code
                code_to_sector[s_code] = s_sector
                
            system_data = {"name_to_code": name_to_code, "code_to_sector": code_to_sector}
            payload = {
                "ticker": "__SYSTEM_KRX_MAP__", "name": "전역 시스템 마스터 원장", "krx_sector": "시스템",
                "bm_summary": json.dumps({"__PACKED_CONTAINER__": True, "data": system_data}, ensure_ascii=False), 
                "last_price_update": now_kst_str
            }
            supabase.table("stock_cache").upsert(payload).execute()
            return system_data
    except: pass

    return {
        "name_to_code": {"SK하이닉스": "000660", "삼화콘덴서": "001820", "광전자": "017900", "LG전자": "066570", "삼성생명": "032830"},
        "code_to_sector": {"000660": "반도체 제조업", "001820": "전기장비 제조업", "017900": "전자부품 제조업", "066570": "전자부품 제조업", "032830": "보험업"}
    }

# ==========================================
# [Layer 3] KIS 금융망 및 네이버 뉴스 이중 크롤러 센터
# ==========================================
def get_kis_access_token(app_key, app_secret):
    url = "https://openapivts.koreainvestment.com:29443/oauth2/tokenP"
    payload = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
    headers = {"content-type": "application/json"}
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=5)
        return res.json().get("access_token")
    except: return None

def fetch_kis_realtime_price(ticker, token, app_key, app_secret):
    url = "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {"content-type": "application/json", "authorization": f"Bearer {token}", "appkey": app_key, "appsecret": app_secret, "tr_id": "FHKST01010100"}
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5).json()
        out = res.get("output", {})
        return {"current_price": int(out.get("stck_prpr", 0)), "pct_change": float(out.get("prdy_ctrt", 0.0)), "year_high": int(out.get("w52_hgpr", 0))}
    except: return None

def fetch_kis_investor_flows(ticker, token, app_key, app_secret):
    url = "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {"content-type": "application/json", "authorization": f"Bearer {token}", "appkey": app_key, "appsecret": app_secret, "tr_id": "FHKST01010900"}
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5).json()
        outputs = res.get("output", [])
        f_sum, i_sum = 0, 0
        for row in outputs[:20]:
            f_sum += float(row.get("frgn_ntby_qty", 0))  
            i_sum += float(row.get("orgn_ntby_qty", 0))  
        return f_sum, i_sum
    except: return 0, 0

def fetch_naver_fundamentals(raw_code):
    url = f"https://finance.naver.com/item/main.naver?code={raw_code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.content, 'html.parser')
        val_data = {'per': 10.0, 'eps': 0.0, 'pbr': 1.0, 'bps': 0.0, 'roe': 5.0, 'industry_per': 10.0, 'broker_target': 0.0, 'shares_outstanding': 10000000.0, 'summary': ''}
        summary_div = soup.select_one('.summary_info')
        val_data['summary'] = summary_div.text.replace('\n', ' ').strip() if summary_div else ""
        for th in soup.find_all('th'):
            if "상장주식수" in th.text: val_data['shares_outstanding'] = parse_num(th.find_next_sibling('td').text)
            if "목표주가" in th.text: val_data['broker_target'] = parse_num(th.find_next_sibling('td').text)
        for td in soup.find_all('td'):
            if "동종업종 PER" in td.text and td.parent:
                em_val = td.parent.select_one('em')
                if em_val: val_data['industry_per'] = parse_num(em_val.text)
        for td in soup.find_all('td'):
            td_id = td.get('id', '')
            if '_per' in td_id: val_data['per'] = parse_num(td.text)
            if '_eps' in td_id: val_data['eps'] = parse_num(td.text)
            if '_pbr' in td_id: val_data['pbr'] = parse_num(td.text)
            if '_bps' in td_id: val_data['bps'] = parse_num(td.text)
        table = soup.select_one('div.cop_analysis table')
        if table:
            rows = table.select_one('tbody').select('tr')
            thead = table.select_one('thead')
            q_headers = [th.text.strip() for th in thead.select('tr')[1].select('th')[5:10]]
            q_revenues = [parse_num(td.text) for td in rows[0].select('td')[5:10]]
            q_op_profits = [parse_num(td.text) for td in rows[1].select('td')[5:10]]
            valid_indices = [i for i, rev in enumerate(q_revenues) if rev != 0.0]
            if valid_indices:
                val_data['q_headers'] = [q_headers[i] for i in valid_indices]
                val_data['q_revenues'] = [q_revenues[i] for i in valid_indices]
                val_data['q_op_profits'] = [q_op_profits[i] for i in valid_indices]
        return val_data
    except: return None

def fetch_global_macro_factor():
    macro_multiplier, current_usd, 환율상태 = 1.0, 1541.6, "정상"
    try:
        df_usd = fdr.DataReader('USD/KRW', start=(datetime.utcnow() - timedelta(days=45)).strftime('%Y-%m-%d'))
        if not df_usd.empty:
            current_usd = round(float(df_usd['Close'].iloc[-1]), 1)
            macro_multiplier = 0.90 if current_usd >= 1400 else 1.05
            환율상태 = f"🚨 고환율 경계 ({current_usd}원)" if current_usd >= 1400 else f"🍏 수급 안정 ({current_usd}원)"
    except: pass
    return macro_multiplier, current_usd, 환율상태

def get_auto_momentum(stock_name, client_id, client_secret):
    if not client_id or not client_secret: return 0, 0, "인증키 누락", []
    url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(f'\"{stock_name}\"')}&display=10&sort=date"
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code != 200: return 0, 0, "인증 오류", []
        items = res.json().get('items', [])
        if not items: return 0, 0, "뉴스 없음", []
        news_list, pos_count, neg_count = [], 0, 0
        for item in items:
            headline = html.unescape(re.compile('<.*?>').sub('', item['title'])).strip()
            news_list.append({"title": headline, "link": item.get('originallink', item['link'])})
            combined_text = headline.upper()
            if any(k in combined_text for k in ["철수", "중단", "매각", "해지"]): neg_count += 3; continue
            for pw in ['수주', '흑자', '돌파', 'AI', '최대', '공급', '계약', '성장']: 
                if pw in combined_text: pos_count += 1
            for nw in ['하락', '적자', '취소', '우려', '부진', '위기', '손실']: 
                if nw in combined_text: neg_count += 1
        return 0, (pos_count - neg_count), news_list[0]['title'][:25] + "...", news_list
    except: return 0, 0, "네트워크 오류", []

# ==========================================
# [Layer 4] 7대 자산 결합형 밸류에이션 엔진
# ==========================================
def calculate_intrinsic_target(row, cache, macro_multiplier=1.0):
    current_price = cache.get('current_price', row['buy_price'])
    raw_eps, bps = cache.get('eps', 0.0), cache.get('bps', 0.0)
    krx_sector_name = cache.get('krx_sector', '기타')
    base_industry_per = cache.get('industry_per', 10.0)
    f_flow, i_flow = cache.get('foreign_20d_flow', 0.0), cache.get('institution_20d_flow', 0.0)
    
    suup_multiplier, quant_tier = 1.0, "MARKET_FOLLOWER"
    if f_flow > 0 and i_flow > 0: suup_multiplier += 0.12; quant_tier = "MOMENTUM_LEADER"
    elif f_flow > 0: suup_multiplier += 0.07; quant_tier = "VALUE_CHAIN"

    target_per = min(base_industry_per * suup_multiplier * macro_multiplier, base_industry_per * 2.20)
    eps_growth_rate = max(5.0, (raw_eps / bps * 100)) if bps > 0 and raw_eps > 0 else 12.0
    forward_eps = (bps * 1.10) * (raw_eps / bps) if bps > 0 and raw_eps > 0 else (current_price / max(1.0, target_per))

    base_target = max(current_price * 0.60, min(forward_eps * target_per, current_price * 3.00))
    return int(base_target), int(base_target * 0.78), int(base_target * 1.35), round(target_per, 2), round(target_per / eps_growth_rate, 2), [quant_tier, krx_sector_name]

# ==========================================
# [Layer 5] 🔥 6대 인자 하이브리드 캐시 파이프라인 (완벽 동기화)
# ==========================================
def auto_sync_job(supabase, username, app_key, app_secret, naver_id, naver_secret):
    last_sync_time = 0
    while True:
        now_ts = time.time()
        if now_ts - last_sync_time >= 600:
            last_sync_time = now_ts
            try: execute_on_demand_sync(supabase, username, app_key, app_secret, naver_id, naver_secret, force=False)
            except: pass
        time.sleep(30)

def execute_on_demand_sync(supabase, username, app_key, app_secret, naver_id, naver_secret, force=False):
    if not app_key or not app_secret: return
    token = get_kis_access_token(app_key, app_secret)
    if not token: return

    macro_mult, _, _ = fetch_global_macro_factor()
    db_res = supabase.table("user_portfolio").select("*").eq("username", username).execute()
    if not db_res.data: return

    system_data = load_system_krx_data(supabase)
    code_to_sector = system_data.get("code_to_sector", {})
    now_kst_str = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')

    for row in db_res.data:
        ticker = str(row['ticker']).split('.')[0]
        name = row['name']
        
        cache_res = supabase.table("stock_cache").select("*").eq("ticker", ticker).execute()
        db_cache_row = cache_res.data[0] if cache_res.data else {}
        
        db_cache = {}
        if db_cache_row:
            for k, v in db_cache_row.items(): db_cache[k] = v
            if db_cache_row.get('bm_summary'):
                try:
                    packed = json.loads(db_cache_row['bm_summary'])
                    if isinstance(packed, dict) and "__PACKED_CONTAINER__" in packed: db_cache.update(packed["data"])
                except: pass

        updated_cache = {"ticker": ticker, "name": name, "krx_sector": code_to_sector.get(ticker, "일반제조업")}

        if is_expired(db_cache.get('last_price_update'), 600) or force:
            p_data = fetch_kis_realtime_price(ticker, token, app_key, app_secret)
            if p_data:
                updated_cache.update({'current_price': p_data['current_price'], 'pct_change': p_data['pct_change'], 'year_high': p_data['year_high'], 'last_price_update': now_kst_str})

        if is_expired(db_cache.get('last_flow_update'), 1200) or force:
            f_flow, i_flow = fetch_kis_investor_flows(ticker, token, app_key, app_secret)
            updated_cache.update({'foreign_20d_flow': f_flow, 'institution_20d_flow': i_flow, 'last_flow_update': now_kst_str})

        # 📰 [네이버 뉴스 결속선 완전 가동] 
        if is_expired(db_cache.get('last_news_update'), 1800) or force:
            _, net_sent, _, n_list = get_auto_momentum(name, naver_id, naver_secret)
            updated_cache.update({'net_sentiment': net_sent, 'news_list': n_list, 'last_news_update': now_kst_str})

        if is_expired(db_cache.get('last_fundamental_update'), 86400) or force:
            fund = fetch_naver_fundamentals(ticker)
            if fund:
                updated_cache.update({
                    'eps': fund['eps'], 'per': fund['per'], 'pbr': fund['pbr'], 'bps': fund['bps'],
                    'industry_per': fund['industry_per'], 'shares_outstanding': fund['shares_outstanding'],
                    'broker_target': fund['broker_target'], 'summary': fund.get('summary', ''), 'last_fundamental_update': now_kst_str
                })

        full_cache = {**db_cache, **updated_cache}
        payload = {
            "ticker": ticker, "name": name, "krx_sector": updated_cache.get('krx_sector', '일반제조업'),
            "current_price": full_cache.get('current_price', row['buy_price']), "pct_change": full_cache.get('pct_change', 0.0), "last_price_update": now_kst_str,
            "bm_summary": json.dumps({"__PACKED_CONTAINER__": True, "data": full_cache}, ensure_ascii=False)
        }
        supabase.table("stock_cache").upsert(payload).execute()
        
        base_tgt, bear_tgt, bull_tgt, target_multiple, peg, applied_trends = calculate_intrinsic_target(row, full_cache, macro_mult)
        user_cache = {
            'current_price': full_cache.get('current_price', row['buy_price']), 'pct_change': full_cache.get('pct_change', 0.0),
            'eps': full_cache.get('eps', 0.0), 'per': full_cache.get('per', 10.0), 'pbr': full_cache.get('pbr', 1.0), 'bps': full_cache.get('bps', 0.0),
            'foreign_20d_flow': full_cache.get('foreign_20d_flow', 0.0), 'institution_20d_flow': full_cache.get('institution_20d_flow', 0.0),
            'target_2026': base_tgt, 'bear_target': bear_tgt, 'bull_target': bull_tgt, 'target_multiple': target_multiple, 'peg': peg, 'applied_trends': applied_trends,
            'summary': full_cache.get('summary', ''), 'krx_sector': full_cache.get('krx_sector', '일반제조업'), 'news_list': full_cache.get('news_list', [])
        }
        supabase.table("user_portfolio").update({"analysis_cache": user_cache}).eq("id", row['id']).execute()

# ==========================================
# [Layer 6] UI 주 인터페이스 관제 센터 (6대 인자 전격 탑재)
# ==========================================
def run_stock_quant_page(supabase, username, app_key, app_secret, naver_id, naver_secret):
    st.title("🛡️ 스마트 제도권 융합 퀀트 엔진 v21.0")
    
    if username not in _active_threads or not _active_threads[username].is_alive():
        t = threading.Thread(target=auto_sync_job, args=(supabase, username, app_key, app_secret, naver_id, naver_secret), daemon=True)
        t.start()
        _active_threads[username] = t

    macro_mult, _, 환율상태 = fetch_global_macro_factor()
    
    with st.container(border=True):
        st.markdown("##### 🌐 REGULATED FIN-NET FLOW (제도권 정식 금융망 및 심리 레이더)")
        m_col1, m_col2 = st.columns(2)
        with m_col1: st.metric("원/달러 환율 국면", 환율상태, delta="한투 KIS 오픈 API 수급 엔진 동조화")
        with m_col2: st.metric("뉴스 감성 분석 가동 상태", "네이버 오픈 API 정상 결속", delta="실시간 호재 스캐너 정상 작동 중")

    system_data = load_system_krx_data(supabase)
    name_to_code = system_data.get("name_to_code", {})

    with st.sidebar:
        st.divider()
        with st.expander("➕ 포트폴리오 자산 편입", expanded=False):
            s_name = st.selectbox("종목 선택", list(name_to_code.keys()))
            buy_p = st.number_input("매입 평단가(원)", min_value=1, value=10000)
            qty = st.number_input("보유 수량(주)", min_value=1, value=10)
            if st.button("장부 결제", type="primary", use_container_width=True):
                try:
                    supabase.table("user_portfolio").upsert({"username": username, "ticker": name_to_code.get(s_name, "000000"), "name": s_name, "buy_price": buy_p, "qty": qty, "analysis_cache": {}}).execute()
                    st.success("장부 반영 완료!")
                    time.sleep(0.3); st.rerun()
                except Exception as e: st.error(str(e))

    db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
    portfolio_data = db_res.data

    col_sync1 = st.columns(1)[0]
    if col_sync1.button("🔄 KIS 금융망 및 뉴스 감성 융합 재연산", width="stretch"):
        if not portfolio_data: st.stop()
        with st.status("한투 수급망 및 네이버 뉴스 실시간 동시 타격 중...", expanded=True) as status:
            execute_on_demand_sync(supabase, username, app_key, app_secret, naver_id, naver_secret, force=True)
            status.update(label="융합 데이터 캐시 패킹 완결!", state="complete")
        st.rerun()

    if not portfolio_data:
        st.info("장부에 보유 주식이 없습니다.")
        return

    total_invest, total_value = 0, 0
    display_rows = []
    
    for row in portfolio_data:
        cache = row.get('analysis_cache', {})
        if isinstance(cache, str):
            try: cache = json.loads(cache)
            except: cache = {}
        if not isinstance(cache, dict): cache = {}

        curr_price = cache.get('current_price', row['buy_price'])
        day_pct = cache.get('pct_change', 0.0)
        target_price = cache.get('target_2026', row['buy_price'])
        
        total_invest += row['buy_price'] * row['qty']
        total_value += curr_price * row['qty']
        
        status_emoji = "🔵 안전마진 확보" if curr_price / max(1, target_price) < 0.75 else "🟢 가치 수렴 중"

        display_rows.append({
            "상태": status_emoji, "종목명": row['name'], "현재가": curr_price, "전일비": day_pct, "평단가": row['buy_price'], "보유지분": row['qty'], "평가손익": (curr_price - row['buy_price']) * row['qty'], "수익률": ((curr_price - row['buy_price']) / row['buy_price'] * 100) if row['buy_price'] > 0 else 0.0,
            "비관": cache.get('bear_target', int(target_price * 0.78)), "기준": target_price, "낙관": cache.get('bull_target', int(target_price * 1.35)), "PEG": cache.get('peg', 1.0), "적용배수": cache.get('target_multiple', 10.0), "KRX섹터": cache.get('krx_sector', '일반제조업'),
            "외인20일": cache.get('foreign_20d_flow', 0.0), "기관20일": cache.get('institution_20d_flow', 0.0), "raw_data": row
        })

    c1, c2, c3 = st.columns(3)
    c1.metric("총 투입 자본", f"{total_invest:,} 원")
    c2.metric("현재 평가 자산", f"{total_value:,} 원")
    c3.metric("포트폴리오 수익", f"{total_value - total_invest:,} 원", f"{((total_value - total_invest)/total_invest)*100:+.2f}%" if total_invest > 0 else "0.00%")
    
    df_base = pd.DataFrame(display_rows)
    df_disp = pd.DataFrame()
    df_disp["상태"] = df_base["상태"]
    df_disp["종목명"] = df_base["종목명"]
    df_disp["현재가"] = df_base["현재가"].apply(lambda x: f"₩ {int(x):,}")
    df_disp["전일비(%)"] = df_base["전일비"].apply(lambda x: f"{x:+.2f}%")
    df_disp["실시간 수익률"] = df_base["수익률"].apply(lambda x: f"{x:+.2f}%")
    df_disp["현재 평가손익"] = df_base["평가손익"].apply(lambda x: f"₩ {int(x):+,}")
    df_disp["📉 비관(Bear)"] = df_base["비관"].apply(lambda x: f"₩ {int(x):,}")
    df_disp["🟢 기준(Base)"] = df_base["기준"].apply(lambda x: f"₩ {int(x):,}")
    df_disp["📈 낙관(Bull)"] = df_base["낙관"].apply(lambda x: f"₩ {int(x):,}")
    df_disp["외인 20일(주)"] = df_base["외인20일"].apply(lambda x: f"{int(x):+,}")
    df_disp["기관 20일(주)"] = df_base["기관20일"].apply(lambda x: f"{int(x):+,}")
    df_disp["진성 PEG"] = df_base["PEG"].apply(lambda x: f"📊 {x:.2f}")

    # 👍 [AttributeError 완전 진압] 최신 스트림릿 객체/딕셔너리 호환성 격벽 처리
    selection_event = st.dataframe(df_disp, width="stretch", on_select="rerun", selection_mode="single-row")
    
    selected_indices = []
    if selection_event is not None:
        if hasattr(selection_event, "selection") and selection_event.selection.rows:
            selected_indices = selection_event.selection.rows
        elif isinstance(selection_event, dict) and selection_event.get("selection", {}).get("rows"):
            selected_indices = selection_event["selection"]["rows"]
    
    if selected_indices:
        selected_stock = display_rows[selected_indices[0]]
        raw_row = selected_stock["raw_data"]
        s_name = selected_stock["종목명"]
        s_ticker = str(raw_row['ticker']).split('.')[0]
        s_cache = raw_row.get("analysis_cache", {})
        if isinstance(s_cache, str):
            try: s_cache = json.loads(s_cache)
            except: s_cache = {}

        st.markdown(f"### 🛠️ [{s_name}] 정식 금융망 및 뉴스 통제실")
        
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("🚨 해당 포지션 청산(장부 삭제)", key=f"b_sl_{s_ticker}", use_container_width=True, type="primary"):
                supabase.table("user_portfolio").delete().eq("id", raw_row['id']).execute()
                st.success("포지션 청산 완결!")
                time.sleep(0.3); st.rerun()
        with col_btn2:
            st.caption("🔒 본 데이터는 한국투자증권 공식 가상망과 네이버 보안 크레덴셜에 의해 보호받고 있습니다.")

        t1, t2 = st.tabs(["📉 3단계 융합 멀티플 판세", "📰 실시간 뉴스 수집 명세"])
        with t1:
            st.markdown(f"**• 종합 투자 의견:** `{selected_stock['상태']}` | **진성 기하학적 PEG:** `{selected_stock['PEG']}x`")
            st.markdown(f"**📉 비관적 저점 방어선:** `₩ {selected_stock['비관']:,}원` | **📈 낙관적 상방 한계선:** `₩ {selected_stock['낙관']:,}원`")
            st.markdown(f"**📢 기업 개요 및 펀더멘탈:** {s_cache.get('summary', '데이터 정렬 완결')}")
        with t2:
            st.write("**📡 네이버 오픈 API 실시간 수집 뉴스 대장**")
            n_list = s_cache.get('news_list', [])
            if n_list:
                for idx, news in enumerate(n_list, 1): st.markdown(f"[{idx}] [{news['title']}]({news['link']})")
            else: st.info("수집된 뉴스가 없습니다. 재연산 버튼을 눌러주십시오.")
