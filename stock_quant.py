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
# [Layer 1] 글로벌 전역 무결성 파서
# ==========================================
_active_threads = {}

def parse_num(txt):
    if not txt: return 0.0
    cleaned = str(txt).replace('₩', '').replace(',', '').replace('주', '').strip()
    m = re.search(r'[-+]?[0-9]+(?:\.[0-9]+)?', cleaned)
    return float(m.group()) if m else 0.0

def is_expired(last_update_str, threshold_seconds):
    if not last_update_str: return True
    try:
        clean_str = last_update_str.replace('T', ' ').split('.')[0].split('+')[0]
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.utcnow() + timedelta(hours=9) 
        return (now - dt).total_seconds() >= threshold_seconds
    except: return True

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
    except: pass

    try:
        df = fdr.StockListing('KRX')
        if not df.empty:
            name_to_code = {}
            for _, r in df.iterrows():
                if pd.isna(r.get('Name')) or pd.isna(r.get('Symbol')): continue
                name_to_code[str(r['Name']).strip()] = str(r['Symbol']).strip()
                
            system_data = {"name_to_code": name_to_code}
            payload = {
                "ticker": "__SYSTEM_KRX_MAP__", "name": "전역 시스템 마스터 원장", "krx_sector": "시스템",
                "bm_summary": json.dumps({"__PACKED_CONTAINER__": True, "data": system_data}, ensure_ascii=False), 
                "last_price_update": now_kst_str
            }
            supabase.table("stock_cache").upsert(payload).execute()
            return system_data
    except: pass
    return {"name_to_code": {}}

# ==========================================
# [Layer 3] KIS 금융망 및 네이버 정식 API 수집기
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
        val_data = {
            'per': 10.0, 'eps': 0.0, 'pbr': 1.0, 'bps': 0.0, 'roe': 5.0, 
            'industry_per': 10.0, 'shares_outstanding': 10000000.0, 'krx_sector': '일반제조업', 'summary': ''
        }
        sector_a = soup.find('a', href=re.compile(r'sise_group_detail\.naver'))
        if sector_a: val_data['krx_sector'] = sector_a.text.strip()

        summary_div = soup.select_one('.summary_info')
        val_data['summary'] = summary_div.text.replace('\n', ' ').strip() if summary_div else ""

        for th in soup.find_all('th'):
            if "상장주식수" in th.text: val_data['shares_outstanding'] = parse_num(th.find_next_sibling('td').text)
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
            val_data['q_revenues'] = [parse_num(td.text) for td in rows[0].select('td')[5:10] if parse_num(td.text) != 0]
            val_data['q_op_profits'] = [parse_num(td.text) for td in rows[1].select('td')[5:10] if parse_num(td.text) != 0]
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
    if not client_id or not client_secret: return 0, 0, "키 누락", []
    url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(f'\"{stock_name}\"')}&display=10&sort=date"
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        items = res.json().get('items', [])
        news_list, pos_count, neg_count = [], 0, 0
        for item in items:
            headline = html.unescape(re.compile('<.*?>').sub('', item['title'])).strip()
            news_list.append({"title": headline, "link": item.get('originallink', item['link'])})
            text = headline.upper()
            for pw in ['수주', '흑자', '돌파', 'AI', '최대', '공급', '계약', '성장']: 
                if pw in text: pos_count += 1
            for nw in ['하락', '적자', '취소', '우려', '부진', '위기', '손실']: 
                if nw in text: neg_count += 1
        return 0, (pos_count - neg_count), "", news_list
    except: return 0, 0, "에러", []

# ==========================================
# [Layer 4] 7대 자산 결합형 시변 시나리오 엔진
# ==========================================
def calculate_horizon_matrix(row, cache, macro_multiplier=1.0):
    current_price = cache.get('current_price', row['buy_price'])
    shares = max(1.0, cache.get('shares_outstanding', 10000000.0))
    f_flow = cache.get('foreign_20d_flow', 0.0)
    i_flow = cache.get('institution_20d_flow', 0.0)
    
    q_rev = cache.get('q_revenues', [])
    q_op = cache.get('q_op_profits', [])
    eps = cache.get('eps', 0.0)
    bps = cache.get('bps', 0.0)
    per = cache.get('per', 10.0)
    ind_per = cache.get('industry_per', 10.0)
    pbr = cache.get('pbr', 1.0)

    rev_growth = 0.0
    if len(q_rev) >= 2 and q_rev[-2] > 0: rev_growth = (q_rev[-1] - q_rev[-2]) / q_rev[-2]
    rev_impact = min(0.25, max(-0.25, rev_growth * 0.4))

    opm_change = 0.0
    if len(q_rev) >= 2 and len(q_op) >= 2 and q_rev[-1] > 0 and q_rev[-2] > 0:
        opm_change = (q_op[-1] / q_rev[-1]) - (q_op[-2] / q_rev[-2])
    opm_impact = min(0.20, max(-0.20, opm_change * 0.5))

    flow_ratio = (f_flow + i_flow) / shares
    flow_impact = min(0.25, max(-0.25, flow_ratio * 4.0))

    val_impact = 0.0
    if per > 0 and ind_per > 0: val_impact = min(0.20, max(-0.20, (ind_per - per) / ind_per * 0.3))
    macro_impact = 0.05 if macro_multiplier > 1.0 else -0.05

    bull_factor = 1.2 + rev_impact + opm_impact + flow_impact + val_impact + macro_impact
    bull_factor = min(2.0, max(0.8, bull_factor))

    if eps > 0: base_v0 = eps * ind_per
    else: base_v0 = bps * pbr if bps > 0 else current_price

    base_yr0 = max(current_price * 0.70, min(base_v0 * (1.0 + flow_impact), current_price * 1.85))
    compounded_g = min(0.28, max(0.06, rev_growth)) 
    base_yr1 = base_yr0 * (1.0 + compounded_g)
    base_yr2 = base_yr1 * (1.0 + compounded_g)

    matrix = {
        "year0": {"bear": int(base_yr0 * 0.75), "base": int(base_yr0 * 1.00), "bull": int(base_yr0 * bull_factor)},
        "year1": {"bear": int(base_yr1 * 0.75), "base": int(base_yr1 * 1.00), "bull": int(base_yr1 * bull_factor)},
        "year2": {"bear": int(base_yr2 * 0.75), "base": int(base_yr2 * 1.00), "bull": int(base_yr2 * bull_factor)},
        "bull_factor": round(bull_factor, 3),
        "sector_cagr": round(compounded_g * 100, 1)
    }
    return matrix

# ==========================================
# [Layer 5] DB-First 하이브리드 캐시 파이프라인
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

    now_kst_str = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')

    for row in db_res.data:
        ticker = str(row['ticker']).split('.')[0]
        name = row['name']
        
        cache_res = supabase.table("stock_cache").select("*").eq("ticker", ticker).execute()
        db_cache_row = cache_res.data[0] if cache_res.data else {}
        db_cache = {}
        if db_cache_row:
            if db_cache_row.get('bm_summary'):
                try: db_cache.update(json.loads(db_cache_row['bm_summary'])["data"])
                except: pass

        existing_sector = db_cache.get('krx_sector', '일반제조업')
        updated_cache = {"ticker": ticker, "name": name, "krx_sector": existing_sector}

        if is_expired(db_cache.get('last_price_update'), 600) or force:
            p_data = fetch_kis_realtime_price(ticker, token, app_key, app_secret)
            if p_data: updated_cache.update({'current_price': p_data['current_price'], 'pct_change': p_data['pct_change'], 'year_high': p_data['year_high'], 'last_price_update': now_kst_str})

        if is_expired(db_cache.get('last_flow_update'), 1200) or force:
            f_flow, i_flow = fetch_kis_investor_flows(ticker, token, app_key, app_secret)
            updated_cache.update({'foreign_20d_flow': f_flow, 'institution_20d_flow': i_flow, 'last_flow_update': now_kst_str})

        if is_expired(db_cache.get('last_news_update'), 1800) or force:
            _, net_sent, _, n_list = get_auto_momentum(name, naver_id, naver_secret)
            updated_cache.update({'net_sentiment': net_sent, 'news_list': n_list, 'last_news_update': now_kst_str})

        if existing_sector == '일반제조업' or is_expired(db_cache.get('last_fundamental_update'), 86400):
            if not (force and existing_sector != '일반제조업'):
                fund = fetch_naver_fundamentals(ticker)
                if fund:
                    updated_cache.update({
                        'eps': fund['eps'], 'per': fund['per'], 'pbr': fund['pbr'], 'bps': fund['bps'],
                        'industry_per': fund['industry_per'], 'shares_outstanding': fund['shares_outstanding'],
                        'krx_sector': fund.get('krx_sector', existing_sector),
                        'q_headers': fund.get('q_headers', []), 'q_revenues': fund.get('q_revenues', []), 'q_op_profits': fund.get('q_op_profits', []), 'last_fundamental_update': now_kst_str
                    })

        full_cache = {**db_cache, **updated_cache}
        supabase.table("stock_cache").upsert({"ticker": ticker, "name": name, "krx_sector": full_cache.get('krx_sector', '일반제조업'), "bm_summary": json.dumps({"__PACKED_CONTAINER__": True, "data": full_cache}, ensure_ascii=False)}).execute()
        
        matrix = calculate_horizon_matrix(row, full_cache, macro_mult)
        user_cache = {
            'current_price': full_cache.get('current_price', row['buy_price']), 'pct_change': full_cache.get('pct_change', 0.0), 'year_high': full_cache.get('year_high', 0),
            'eps': full_cache.get('eps', 0.0), 'per': full_cache.get('per', 10.0), 'pbr': full_cache.get('pbr', 1.0), 'bps': full_cache.get('bps', 0.0),
            'foreign_20d_flow': full_cache.get('foreign_20d_flow', 0.0), 'institution_20d_flow': full_cache.get('institution_20d_flow', 0.0),
            'target_2026': matrix["year0"]["base"], 'bear_target': matrix["year0"]["bear"], 'bull_target': matrix["year0"]["bull"], 'horizon_matrix': matrix,
            'summary': full_cache.get('summary', ''), 'bm_summary': full_cache.get('bm_summary', ''), 'bm_list': full_cache.get('bm_list', []),
            'q_headers': full_cache.get('q_headers', []), 'q_revenues': full_cache.get('q_revenues', []), 'q_op_profits': full_cache.get('q_op_profits', []), 'news_list': full_cache.get('news_list', []),
            'krx_sector': full_cache.get('krx_sector', '일반제조업')
        }
        supabase.table("user_portfolio").update({"analysis_cache": user_cache}).eq("id", row['id']).execute()

# ==========================================
# [Layer 6] UI 관제 센터 (오리지널 v19.8 완벽 봉인본)
# ==========================================
def run_stock_quant_page(supabase, username, app_key, app_secret, naver_id, naver_secret):
    current_yr = (datetime.utcnow() + timedelta(hours=9)).year
    yr0, yr1, yr2 = current_yr, current_yr + 1, current_yr + 2

    macro_mult, current_usd, 환율상태 = fetch_global_macro_factor()
    system_data = load_system_krx_data(supabase)
    name_to_code = system_data.get("name_to_code", {})

    with st.expander("➕ 포트폴리오 신규 자산 편입", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1: s_name = st.selectbox("종목 선택 (한/영 키를 눌러주세요)", list(name_to_code.keys()) if name_to_code else ["삼성전자"])
        with col2: buy_p = st.number_input("매입 평단가(원)", min_value=1, value=10000)
        with col3: qty = st.number_input("보유 수량(주)", min_value=1, value=10)
        if st.button("장부 조율 및 매수 결제", type="primary"):
            try:
                supabase.table("user_portfolio").upsert({"username": username, "ticker": name_to_code.get(s_name, "000000"), "name": s_name, "buy_price": buy_p, "qty": qty, "analysis_cache": {}}).execute()
                st.success(f"[{s_name}] 장부 편입 완료!"); time.sleep(0.3); st.rerun()
            except Exception as e: st.error(f"자산 편입 실패: {str(e)}")

    st.divider()
    tab_port, tab_hist, tab_log = st.tabs(["💼 포트폴리오 자산", "📝 가치 실현 내역", "⚙️ 시스템 가동 로그"])

    with tab_port:
        st.write("⚡ **Forward 멀티 모델 실시간 제어판**")
        col_sync1 = st.columns(1)[0]
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        if col_sync1.button("🔄 퀀트 밸류에이션 장부 커스텀 재연산", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("한투 금융망 및 3개년 시나리오 매트릭스 전면 동기화 중...", expanded=True) as status:
                execute_on_demand_sync(supabase, username, app_key, app_secret, naver_id, naver_secret, force=True)
                status.update(label="3x3 시변 기대수익률 맵핑 완결!", state="complete")
            st.rerun()

        st.divider()
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
            matrix = cache.get('horizon_matrix', {
                "year0": {"bear": curr_price, "base": curr_price, "bull": curr_price},
                "year1": {"bear": curr_price, "base": curr_price, "bull": curr_price},
                "year2": {"bear": curr_price, "base": curr_price, "bull": curr_price}
            })
            
            pnl_amt = (curr_price - row['buy_price']) * row['qty']
            pnl_pct = ((curr_price - row['buy_price']) / row['buy_price']) * 100 if row['buy_price'] > 0 else 0
            
            total_invest += row['buy_price'] * row['qty']
            total_value += curr_price * row['qty']
            
            status_emoji = "🔵 안전마진 확보" if curr_price / max(1, matrix["year0"]["base"]) < 0.75 else "🟢 가치 수렴 중"

            display_rows.append({
                "상태": status_emoji, "종목명": row['name'], "KRX 업종": cache.get('krx_sector', '제조업'),
                "현재가": curr_price, "전일비(%)": day_pct, "내 평단가": row['buy_price'], "보유 수량": row['qty'],
                "실시간 수익률": pnl_pct, "현재 평가손익": pnl_amt,
                f"{yr0}▲": matrix["year0"]["bull"], f"{yr1}▲": matrix["year1"]["bull"], f"{yr2}▲": matrix["year2"]["bull"],
                "외인 20일(주)": cache.get('foreign_20d_flow', 0.0), "기관 20일(주)": cache.get('institution_20d_flow', 0.0),
                "matrix": matrix, "raw_data": row
            })

        c1, c2, c3 = st.columns(3)
        c1.metric("총 투입 자본", f"{total_invest:,} 원")
        c2.metric("현재 평가 자산", f"{total_value:,} 원")
        c3.metric("포트폴리오 수익", f"{total_value - total_invest:,} 원", f"{((total_value - total_invest)/total_invest)*100:+.2f}%" if total_invest > 0 else "0.00%")
        
        df_base = pd.DataFrame(display_rows)
        df_disp = pd.DataFrame()
        
        df_disp["종목명"] = df_base["종목명"]
        df_disp["현재가"] = df_base["현재가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["평단가"] = df_base["내 평단가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["보유수량"] = df_base["보유 수량"].apply(lambda x: f"{int(x):,} 주")
        df_disp["수익률"] = df_base["실시간 수익률"].apply(lambda x: f"{x:+.2f}%")
        df_disp[f"{yr0}▲"] = df_base[f"{yr0}▲"].apply(lambda x: f"₩ {int(x):,}")
        df_disp[f"{yr1}▲"] = df_base[f"{yr1}▲"].apply(lambda x: f"₩ {int(x):,}")
        df_disp[f"{yr2}▲"] = df_base[f"{yr2}▲"].apply(lambda x: f"₩ {int(x):,}")

        selection_event = st.dataframe(df_disp, width="stretch", on_select="rerun", selection_mode="single-row")
        
        selected_indices = []
        if selection_event is not None:
            if hasattr(selection_event, "selection") and selection_event.selection.rows: selected_indices = selection_event.selection.rows
            elif isinstance(selection_event, dict) and selection_event.get("selection", {}).get("rows"): selected_indices = selection_event["selection"]["rows"]
        
        if selected_indices:
            sel = display_rows[selected_indices[0]]
            raw = sel["raw_data"]
            mx = sel["matrix"]
            avg_cost = float(raw['buy_price'])
            s_ticker = str(raw['ticker']).split('.')[0]
            
            st.markdown(f"### 🛠️ [{sel['종목명']}] 퀀트 익절/손절 실전 통제실 (시변 기대수익률)")
            
            col_btn1, col_btn2, col_btn3 = st.columns(3)
            with col_btn1:
                with st.popover("✏️ 장부 평단/수량 수정", use_container_width=True):
                    new_p = st.number_input("수정할 평단가", value=int(raw['buy_price']), key=f"p_ed_{s_ticker}")
                    new_q = st.number_input("수정할 보유수량", value=int(raw['qty']), key=f"q_ed_{s_ticker}")
                    if st.button("수정 장부 인가", key=f"b_ed_{s_ticker}", use_container_width=True):
                        supabase.table("user_portfolio").update({"buy_price": new_p, "qty": new_q}).eq("id", raw['id']).execute()
                        st.success("장부 정보 정정 고시 완료!"); time.sleep(0.3); st.rerun()
            with col_btn2:
                with st.popover("🛒 분할 추가매수", use_container_width=True):
                    add_p = st.number_input("추가 매수가격", value=int(sel['현재가']), key=f"p_add_{s_ticker}")
                    add_q = st.number_input("추가 매수수량", value=10, key=f"q_add_{s_ticker}")
                    if st.button("추가매수 체결", key=f"b_add_{s_ticker}", use_container_width=True):
                        new_qty = raw['qty'] + add_q
                        new_avg = int(((raw['buy_price'] * raw['qty']) + (add_p * add_q)) / new_qty)
                        supabase.table("user_portfolio").update({"buy_price": new_avg, "qty": new_qty}).eq("id", raw['id']).execute()
                        st.success("가중평균 평단가 합성 완료!"); time.sleep(0.3); st.rerun()
            with col_btn3:
                with st.popover("❌ 자산 매도(청산)", use_container_width=True):
                    sell_p = st.number_input("매도 단가", value=int(sel['현재가']), key=f"p_sl_{s_ticker}")
                    sell_q = st.number_input("매도 수량", min_value=1, max_value=int(raw['qty']), value=int(raw['qty']), key=f"q_sl_{s_ticker}")
                    if st.button("🚨 매도 집행", key=f"b_sl_{s_ticker}", use_container_width=True, type="primary"):
                        profit_amt = (sell_p - raw['buy_price']) * sell_q
                        try:
                            supabase.table("user_history").insert({
                                "username": username, "ticker": raw['ticker'], "name": sel['종목명'],
                                "buy_price": raw['buy_price'], "sell_price": sell_p, "qty": sell_q,
                                "profit_amt": profit_amt, "profit_pct": round((sell_p - raw['buy_price'])/raw['buy_price']*100, 2)
                            }).execute()
                        except: pass
                        if sell_q == raw['qty']: supabase.table("user_portfolio").delete().eq("id", raw['id']).execute()
                        else: supabase.table("user_portfolio").update({"qty": raw['qty'] - sell_q}).eq("id", raw['id']).execute()
                        st.error("포지션 청산 오더 집행 완결!"); time.sleep(0.3); st.rerun()

            st.divider()
            
            t1, t2, t3 = st.tabs(["📉 3단계 시나리오 및 평단대비 기대수익률", "📰 전방 사업 명세", "📊 실적 턴어라운드 감지"])
            with t1:
                st.caption(f"**실시간 계량 팩터 수신 정보:** 업종 [{sel['KRX 업종']}] | 동적 연산 진성 Bull Factor: **{mx.get('bull_factor', 1.25)}x** (매출성장속도 복리: {mx.get('sector_cagr', 10.0)}%)")
                
                mc0, mc1, mc2 = st.columns(3)
                with mc0:
                    with st.container(border=True):
                        st.markdown(f"#### 📅 {yr0} 예상 국면")
                        st.markdown(f"**Bear (25%):** ₩{mx['year0']['bear']:,} <br><span style='color:#3182F6'>평단대비: {((mx['year0']['bear']-avg_cost)/avg_cost*100):+.1f}%</span>", unsafe_allow_html=True)
                        st.markdown(f"**Base (50%):** ₩{mx['year0']['base']:,} <br><span style='color:#E6A23C'>평단대비: {((mx['year0']['base']-avg_cost)/avg_cost*100):+.1f}%</span>", unsafe_allow_html=True)
                        st.markdown(f"**Bull (25%):** ₩{mx['year0']['bull']:,} <br><span style='color:#00B464'>평단대비: {((mx['year0']['bull']-avg_cost)/avg_cost*100):+.1f}%</span>", unsafe_allow_html=True)
                with mc1:
                    with st.container(border=True):
                        st.markdown(f"#### 📅 {yr1} 예상 국면")
                        st.markdown(f"**Bear (25%):** ₩{mx['year1']['bear']:,} <br><span style='color:#3182F6'>평단대비: {((mx['year1']['bear']-avg_cost)/avg_cost*100):+.1f}%</span>", unsafe_allow_html=True)
                        st.markdown(f"**Base (50%):** ₩{mx['year1']['base']:,} <br><span style='color:#E6A23C'>평단대비: {((mx['year1']['base']-avg_cost)/avg_cost*100):+.1f}%</span>", unsafe_allow_html=True)
                        st.markdown(f"**Bull (25%):** ₩{mx['year1']['bull']:,} <br><span style='color:#00B464'>평단대비: {((mx['year1']['bull']-avg_cost)/avg_cost*100):+.1f}%</span>", unsafe_allow_html=True)
                with mc2:
                    with st.container(border=True):
                        st.markdown(f"#### 📅 {yr2} 예상 국면")
                        st.markdown(f"**Bear (25%):** ₩{mx['year2']['bear']:,} <br><span style='color:#3182F6'>평단대비: {((mx['year2']['bear']-avg_cost)/avg_cost*100):+.1f}%</span>", unsafe_allow_html=True)
                        st.markdown(f"**Base (50%):** ₩{mx['year2']['base']:,} <br><span style='color:#E6A23C'>평단대비: {((mx['year2']['base']-avg_cost)/avg_cost*100):+.1f}%</span>", unsafe_allow_html=True)
                        st.markdown(f"**Bull (25%):** ₩{mx['year2']['bull']:,} <br><span style='color:#00B464'>평단대비: {((mx['year2']['bull']-avg_cost)/avg_cost*100):+.1f}%</span>", unsafe_allow_html=True)
                
            with t2:
                s_cache = raw.get("analysis_cache", {})
                st.write(f"**📢 기업 개요 및 펀더멘탈 요약:** {s_cache.get('summary', '데이터 정렬 완료')}")
                st.write(f"**• 회계 연산용 지표:** TTM EPS `{s_cache.get('eps', 0.0):,.0f}원` | BPS `{s_cache.get('bps', 0.0):,.0f}원` | 동종업종 PER `{s_cache.get('per', 10.0)}배`")
                st.write(f"**• 실시간 수급 동향 (20일):** 외인 누적 `{int(s_cache.get('foreign_20d_flow', 0.0)):+,}주` | 기관 누적 `{int(s_cache.get('institution_20d_flow', 0.0)):+,}주`")
                    
            with t3:
                s_cache = raw.get("analysis_cache", {})
                q_hd = s_cache.get('q_headers', [])
                q_rev = s_cache.get('q_revenues', [])
                q_op = s_cache.get('q_op_profits', [])
                if q_rev and len(q_rev) >= 2:
                    fig, ax1 = plt.subplots(figsize=(10, 3))
                    ax1.bar(range(len(q_rev)), q_rev, color='#3182F6', alpha=0.7, width=0.2, label="매출액")
                    ax2 = ax1.twinx()
                    ax2.plot(range(len(q_op)), q_op, color='#F04452', marker='o', linewidth=2, label="영업이익")
                    st.pyplot(fig)
                else: st.info("분기 실적 차트 시각화 데이터가 캐시 원장에 존재하지 않습니다.")

    with tab_hist:
        st.subheader("📝 자산 매도(청산) 히스토리")
        hist_res = supabase.table("user_history").select("*").eq("username", username).execute()
        if not hist_res.data: st.info("아직 자산 매도 내역이 없습니다.")
        else:
            df_hist = pd.DataFrame(hist_res.data)
            st.dataframe(df_hist, width="stretch")

    with tab_log:
        st.subheader("⚙️ 시스템 엔진 처리 기록")
        log_res = supabase.table("user_logs").select("*").eq("username", username).execute()
        if not log_res.data: st.info("시스템 처리 기록이 없습니다.")
        else:
            df_log = pd.DataFrame(log_res.data)
            st.dataframe(df_log, width="stretch")
