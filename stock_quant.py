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
import time

# 전역 백그라운드 스레드 관리 바인더
_active_threads = {}

# ==========================================
# [Layer 1] 유틸리티 및 오차 보정 파서 엔진
# ==========================================
def parse_num(txt):
    if not txt: return 0.0
    m = re.search(r'[-+]?[0-9,]+(?:\.[0-9]+)?', txt)
    return float(m.group().replace(',', '')) if m else 0.0

def is_expired(last_update_str, threshold_seconds):
    if not last_update_str: return True
    try:
        clean_str = last_update_str.replace('T', ' ').split('.')[0].split('+')[0]
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.utcnow() + timedelta(hours=9) 
        return (now - dt).total_seconds() >= threshold_seconds
    except: return True

def format_date_clean(date_str):
    if not date_str: return "기록 없음"
    try:
        clean_str = date_str.replace('T', ' ').split('.')[0].split('+')[0]
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except: return str(date_str)

# ==========================================
# [Layer 2] 원천 데이터 인스티튜셔널 크롤러
# ==========================================
def fetch_global_macro_factor():
    macro_multiplier = 1.0
    current_usd = 1541.6  
    환율상태 = "정상"
    try:
        df_usd = fdr.DataReader('USD/KRW', start=(datetime.utcnow() - timedelta(days=45)).strftime('%Y-%m-%d'))
        if not df_usd.empty:
            current_usd = round(float(df_usd['Close'].iloc[-1]), 1)
            usd_ma20 = round(float(df_usd['Close'].rolling(20).mean().iloc[-1]), 1) if len(df_usd) >= 20 else current_usd
            if current_usd >= 1400:
                macro_multiplier = 0.90 
                환율상태 = f"🚨 매크로 유동성 축소 ({current_usd}원)"
            elif current_usd > usd_ma20:
                macro_multiplier = 0.95  
                환율상태 = f"⚠️ 변동성 경계 ({current_usd}원)"
            else:
                macro_multiplier = 1.05  
                환율상태 = f"🍏 매크로 훈풍 ({current_usd}원)"
    except: 환율상태 = "⚠️ 센서 지연"
    return macro_multiplier, current_usd, 환율상태

def fetch_investor_flows(raw_code):
    url = f"https://finance.naver.com/item/frgn.naver?code={raw_code}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.content, 'html.parser')
        table = soup.select_one('table.type2')
        if not table: return 0.0, 0.0
        rows = table.select('tr')
        f_sum, i_sum = 0, 0
        count = 0
        for row in rows:
            tds = row.select('td')
            if len(tds) >= 7 and tds[0].text.strip():
                try:
                    inst = float(tds[5].text.replace(',','').strip())
                    fore = float(tds[6].text.replace(',','').strip())
                    i_sum += inst
                    f_sum += fore
                    count += 1
                    if count >= 20: break
                except: pass
        return f_sum, i_sum
    except: return 0.0, 0.0

def fetch_naver_fundamentals(raw_code):
    url = f"https://finance.naver.com/item/main.naver?code={raw_code}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.content, 'html.parser')
        val_data = {
            'per': 10.0, 'eps': 0.0, 'pbr': 1.0, 'bps': 0.0, 'roe': 5.0, 
            'industry_per': 10.0, 'broker_target': 0.0, 'shares_outstanding': 10000000.0,
            'fwd_eps_2025': 0.0, 'fwd_eps_2026': 0.0, 'summary': ''
        }
        summary_div = soup.select_one('.summary_info')
        val_data['summary'] = summary_div.text.replace('\n', ' ').strip() if summary_div else ""
        
        for th in soup.find_all('th'):
            if "상장주식수" in th.text:
                td_val = th.find_next_sibling('td')
                if td_val: val_data['shares_outstanding'] = parse_num(td_val.text)
            if "목표주가" in th.text:
                td_val = th.find_next_sibling('td')
                if td_val: val_data['broker_target'] = parse_num(td_val.text)

        for td in soup.find_all('td'):
            if "동종업종 PER" in td.text:
                parent_tr = td.parent
                if parent_tr:
                    em_val = parent_tr.select_one('em')
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
            
            try:
                yr_headers = [th.text.strip() for th in thead.select('tr')[0].select('th')[1:5]]
                target_eps_row = None
                for tr in rows:
                    th_title = tr.select_one('th')
                    if th_title and "EPS(원)" in th_title.text:
                        target_eps_row = tr.select('td')[1:5]
                        break
                if target_eps_row:
                    for idx, yr in enumerate(yr_headers):
                        if "2025" in yr: val_data['fwd_eps_2025'] = parse_num(target_eps_row[idx].text)
                        if "2026" in yr: val_data['fwd_eps_2026'] = parse_num(target_eps_row[idx].text)
            except: pass
        return val_data
    except: return None

def fetch_dynamic_company_bm(raw_code):
    url = f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?gicode=A{raw_code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.content, 'html.parser')
        bm_list = []
        for table in soup.find_all('table'):
            if "매출비중" in table.text or "제품/서비스명" in table.text:
                for tr in table.find_all('tr')[1:]:
                    tds = [td.text.strip() for td in tr.find_all(['td', 'th'])]
                    if len(tds) >= 3 and tds[0]:
                        bm_list.append([tds[0], tds[1], "매출비중", tds[2]])
                if bm_list: return bm_list
    except: pass
    return [["기반사업부", "주요 제품/서비스", "공시분석", "-"]]

def get_auto_momentum(stock_name, client_id, client_secret):
    if not client_id or not client_secret: return 0, 0, "인증키 누락", []
    exact_query = f'"{stock_name}"'
    url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(exact_query)}&display=10&sort=date"
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code != 200: return 0, 0, "인증 대기", []
        items = res.json().get('items', [])
        if not items: return 0, 0, "뉴스 없음", []
        
        news_list, pos_count, neg_count = [], 0, 0
        for item in items:
            headline = html.unescape(re.compile('<.*?>').sub('', item['title'])).strip()
            news_list.append({"title": headline, "link": item.get('originallink', item['link'])})
            combined_text = headline.upper()
            if any(abort_kw in combined_text for abort_kw in ["철수", "중단", "매각", "계약해지"]):
                neg_count += 3
                continue
            for pw in ['수주', '흑자', '돌파', 'AI', '최대', '공급', '계약', '성장', '수혜', '외인매수', '기관매집']:
                if pw in combined_text: pos_count += 1
            for nw in ['하락', '적자', '취소', '우려', '부진', '위기', '손실', '외인매도']:
                if nw in combined_text: neg_count += 1
                
        net_sentiment = pos_count - neg_count
        return 0, net_sentiment, news_list[0]['title'][:25] + "...", news_list
    except: return 0, 0, "네트워크 오류", []

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
# [Layer 3] 진성 7대 알파 팩터 밸류에이션 결합 엔진
# ==========================================
def calculate_intrinsic_target(row, cache, macro_multiplier=1.0):
    ticker = row['ticker']
    current_price = cache.get('current_price', row['buy_price'])
    raw_eps = cache.get('eps', 0.0)
    bps = cache.get('bps', 0.0)
    krx_sector_name = cache.get('krx_sector', '기타')
    base_industry_per = cache.get('industry_per', 10.0)

    theme_premium = 1.0
    quant_tier = "MARKET_FOLLOWER"
    
    try:
        end_date = datetime.utcnow() + timedelta(hours=9)
        start_date = end_date - timedelta(days=35)
        df_stock = fdr.DataReader(ticker, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
        df_kospi = fdr.DataReader('KS11', start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
        
        if len(df_stock) >= 2 and len(df_kospi) >= 2:
            stock_return = ((df_stock['Close'].iloc[-1] - df_stock['Close'].iloc[-20]) / df_stock['Close'].iloc[-20]) * 100
            kospi_return = ((df_kospi['Close'].iloc[-1] - df_kospi['Close'].iloc[-20]) / df_kospi['Close'].iloc[-20]) * 100
            alpha_momentum = stock_return - kospi_return
            
            if alpha_momentum >= 20.0:
                theme_premium = 1.15  
                quant_tier = "MOMENTUM_LEADER"
            elif alpha_momentum >= 5.0:
                theme_premium = 1.08
                quant_tier = "VALUE_CHAIN"
            else:
                theme_premium = 1.00
                quant_tier = "MARKET_SATELLITE"
    except:
        alpha_momentum = 0.0

    f_flow, i_flow = cache.get('foreign_20d_flow', 0.0), cache.get('institution_20d_flow', 0.0)
    suup_multiplier = 1.0
    if f_flow > 0 and i_flow > 0: suup_multiplier += 0.12 
    elif f_flow > 0: suup_multiplier += 0.07              
    elif i_flow > 0: suup_multiplier += 0.05              

    interest_rate_adj = 1.0
    if "보험" in krx_sector_name or "생명" in krx_sector_name: interest_rate_adj = 1.05  
    elif any(k in krx_sector_name for k in ["증권", "금융", "건설", "창업투자"]): interest_rate_adj = 0.90  

    max_per_cap = base_industry_per * 1.40
    if alpha_momentum >= 20.0 or f_flow > 0: max_per_cap = base_industry_per * 2.20 

    calculated_per = base_industry_per * theme_premium * suup_multiplier * interest_rate_adj * macro_multiplier
    target_per = min(calculated_per, max_per_cap) 

    eps_2025 = cache.get('fwd_eps_2025', 0.0)
    eps_2026 = cache.get('fwd_eps_2026', 0.0)
    
    if eps_2025 > 0 and eps_2026 > 0:
        eps_growth_rate = ((eps_2026 - eps_2025) / eps_2025) * 100
        forward_eps = eps_2026
    else:
        if bps > 0:
            implied_roe = raw_eps / bps if bps > 0 and raw_eps > 0 else 0.08
            normalized_roe = max(0.06, min(implied_roe, 0.18))
            eps_growth_rate = normalized_roe * 100
            forward_eps = (bps * 1.10) * normalized_roe
        else:
            eps_growth_rate = 12.0
            forward_eps = cache.get('year_high', current_price) / target_per

    if eps_growth_rate <= 0: eps_growth_rate = 5.0 
    peg_ratio = target_per / eps_growth_rate

    base_target = forward_eps * target_per
    base_target = max(current_price * 0.60, min(base_target, current_price * 3.00))
    
    bear_ratio, bull_ratio = 0.80, 1.25 
    if "보험" in krx_sector_name or "생명" in krx_sector_name: bear_ratio, bull_ratio = 0.88, 1.10  
    elif "증권" in krx_sector_name or "금융" in krx_sector_name: bear_ratio, bull_ratio = 0.82, 1.15
    elif "반도체" in krx_sector_name: bear_ratio, bull_ratio = 0.75, 1.30  
    elif any(k in krx_sector_name or k in row['name'] for k in ["로봇", "로보", "기계", "소프트"]): bear_ratio, bull_ratio = 0.65, 1.55  

    bear_target = int(base_target * bear_ratio)
    bull_target = int(base_target * bull_ratio)
    
    return int(base_target), bear_target, bull_target, round(target_per, 2), round(peg_ratio, 2), [quant_tier, krx_sector_name]

# ==========================================
# [Layer 4] 스레드 수급 루프 & 타임 마디 이중 캐시 파이프라인
# ==========================================
def auto_sync_job(supabase, username, naver_id, naver_secret):
    last_sync_time = 0
    while True:
        now_ts = time.time()
        now_kst = datetime.utcnow() + timedelta(hours=9)
        if 8 <= now_kst.hour <= 18 and (now_ts - last_sync_time >= 600):
            last_sync_time = now_ts
            try:
                execute_on_demand_sync(supabase, username, naver_id, naver_secret, force=False)
                insert_log(supabase, username, "BACKGROUND_ENGINE", "자동 백그라운드 수급 동기화 스레드 수렴 완료", "10분 마디 주기 오토 락 가동")
            except Exception as e:
                insert_log(supabase, username, "BACKGROUND_ERROR", "백그라운드 동기화 중 예외 발생", str(e))
        time.sleep(30)

def execute_on_demand_sync(supabase, username, naver_id, naver_secret, force=False):
    macro_mult, current_usd, _ = fetch_global_macro_factor()
    db_res = supabase.table("user_portfolio").select("*").eq("username", username).execute()
    portfolio_data = db_res.data
    if not portfolio_data: return

    try:
        df_k = fdr.StockListing('KRX')
        krx_db = {row['Symbol']: row['Sector'] for _, row in df_k.iterrows() if 'Sector' in row and row['Sector']}
    except: krx_db = {}

    now_kst_str = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')

    for row in portfolio_data:
        ticker = row['ticker']
        name = row['name']
        
        cache_res = supabase.table("stock_cache").select("*").eq("ticker", ticker).execute()
        db_cache = cache_res.data[0] if cache_res.data else {}
        
        updated_cache = {"ticker": ticker, "name": name}
        if 'krx_sector' not in db_cache or not db_cache['krx_sector'] or "차단" in db_cache['krx_sector']:
            updated_cache['krx_sector'] = krx_db.get(ticker, "일반제조업")

        # [A그룹: 10분 가격 캐시] 
        if is_expired(db_cache.get('last_price_update'), 600) or force:
            df_p = fdr.DataReader(ticker, start=(datetime.utcnow()-timedelta(days=7)).strftime('%Y-%m-%d'))
            if not df_p.empty:
                updated_cache['current_price'] = int(df_p['Close'].iloc[-1])
                prev_close = float(df_p['Close'].iloc[-2]) if len(df_p) >= 2 else df_p['Close'].iloc[-1]
                updated_cache['pct_change'] = round(((updated_cache['current_price'] - prev_close) / prev_close) * 100, 2)
                updated_cache['year_high'] = int(df_p['High'].max())
                updated_cache['last_price_update'] = now_kst_str

        # [B그룹: 30분 뉴스 캐시]
        if is_expired(db_cache.get('last_news_update'), 1800) or force:
            _, net_sent, _, n_list = get_auto_momentum(name, naver_id, naver_secret)
            updated_cache['net_sentiment'] = net_sent
            updated_cache['news_list'] = n_list
            updated_cache['last_news_update'] = now_kst_str

        # [C그룹: 1시간 매집 수급 캐시] 
        if is_expired(db_cache.get('last_flow_update'), 3600) or force:
            f_flow, i_flow = fetch_investor_flows(ticker)
            updated_cache['foreign_20d_flow'] = f_flow
            updated_cache['institution_20d_flow'] = i_flow
            updated_cache['last_flow_update'] = now_kst_str

        # [D그룹: 1일 재무제표 캐시] 
        if is_expired(db_cache.get('last_fundamental_update'), 86400) or force:
            fund = fetch_naver_fundamentals(ticker)
            if fund:
                updated_cache.update({
                    'eps': fund['eps'], 'per': fund['per'], 'pbr': fund['pbr'], 'bps': fund['bps'],
                    'industry_per': fund['industry_per'], 'shares_outstanding': fund['shares_outstanding'],
                    'broker_target': fund['broker_target'], 'fwd_eps_2025': fund['fwd_eps_2025'], 'fwd_eps_2026': fund['fwd_eps_2026'],
                    'q_headers': fund.get('q_headers', []), 'q_revenues': fund.get('q_revenues', []), 'q_op_profits': fund.get('q_op_profits', []), 'summary': fund.get('summary', '')
                })
                updated_cache['last_fundamental_update'] = now_kst_str

        # [E그룹: 30일 BM 캐시]
        if is_expired(db_cache.get('last_bm_update'), 2592000) or force:
            bm_list = fetch_dynamic_company_bm(ticker)
            mock_fund = {**db_cache, **updated_cache}
            growth_factor, bm_summary = calculate_bm_score(mock_fund)
            updated_cache.update({
                'bm_list': bm_list, 'bm_growth_factor': growth_factor, 'bm_summary': bm_summary, 'last_bm_update': now_kst_str
            })

        full_cache = {**db_cache, **updated_cache}
        supabase.table("stock_cache").upsert(full_cache).execute()
        
        base_tgt, bear_tgt, bull_tgt, target_multiple, peg, applied_trends = calculate_intrinsic_target(row, full_cache, macro_mult)
        
        user_cache = {
            'current_price': full_cache.get('current_price', row['buy_price']),
            'pct_change': full_cache.get('pct_change', 0.0), 'year_high': full_cache.get('year_high', 0),
            'eps': full_cache.get('eps', 0.0), 'per': full_cache.get('per', 10.0), 'pbr': full_cache.get('pbr', 1.0), 'bps': full_cache.get('bps', 0.0),
            'foreign_20d_flow': full_cache.get('foreign_20d_flow', 0.0), 'institution_20d_flow': full_cache.get('institution_20d_flow', 0.0),
            'broker_target': full_cache.get('broker_target', 0.0), 'news_list': full_cache.get('news_list', []),
            'target_2026': base_tgt, 'bear_target': bear_tgt, 'bull_target': bull_tgt, 'target_multiple': target_multiple, 'peg': peg, 'applied_trends': applied_trends,
            'summary': full_cache.get('summary', ''), 'bm_summary': full_cache.get('bm_summary', ''), 'bm_list': full_cache.get('bm_list', []),
            'q_headers': full_cache.get('q_headers', []), 'q_revenues': full_cache.get('q_revenues', []), 'q_op_profits': full_cache.get('q_op_profits', [])
        }
        supabase.table("user_portfolio").update({"analysis_cache": user_cache}).eq("id", row['id']).execute()

# ==========================================
# [Layer 5] UI 주 인터페이스 제어 센터 (원복 완결)
# ==========================================
def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    st.title("🛡️ 스마트 프랍 퀀트 포트폴리오 엔진 v18.5")
    
    if username not in _active_threads or not _active_threads[username].is_alive():
        t = threading.Thread(target=auto_sync_job, args=(supabase, username, naver_id, naver_secret), daemon=True)
        t.start()
        _active_threads[username] = t

    macro_mult, current_usd, 환율상태 = fetch_global_macro_factor()
    
    with st.container(border=True):
        st.markdown("##### 🌐 GLOBAL MACRO FLOW (매크로 유동성 레이더)")
        m_col1, m_col2 = st.columns(2)
        with m_col1:
            st.metric("원/달러 환율 국면", 환율상태, delta="외국인 패시브 수급 불안" if current_usd >= 1400 else "수급 안정 구역", delta_color="inverse")
        with m_col2:
            st.metric("시장 기본 PER 멀티플 보정률", f"{int(macro_mult*100)}%", delta="v12 데몬 스레드 및 v14 이중 격벽 캐시 완전 정상화")

    # 💡 [원복 1] 신규 자산 추가 컴포넌트 오리지널 selectbox 형태로 전면 복원
    try:
        df_k_map = fdr.StockListing('KRX')
        krx_map = {row['Name']: row['Symbol'] for _, row in df_k_map.iterrows() if 'Name' in row}
    except:
        try:
            df_k_map = fdr.StockListing('KRX-DESC')
            krx_map = {row['Name']: row['Symbol'] for _, row in df_k_map.iterrows() if 'Name' in row}
        except:
            krx_map = {"삼화콘덴서": "001820", "광전자": "006220", "SK하이닉스": "000660", "삼성전자": "005930"}

    with st.expander("➕ 포트폴리오 신규 자산 편입", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1: s_name = st.selectbox("종목 선택 (한/영 키를 눌러주세요)", list(krx_map.keys()))
        with col2: buy_p = st.number_input("매입 평단가(원)", min_value=1, value=10000)
        with col3: qty = st.number_input("보유 수량(주)", min_value=1, value=10)
        if st.button("장부 조율 및 매수 결제", type="primary"):
            ticker = krx_map[s_name]
            try:
                supabase.table("user_portfolio").upsert({
                    "username": username, "ticker": ticker, "name": s_name, "buy_price": buy_p, "qty": qty, "analysis_cache": {}
                }).execute()
                insert_log(supabase, username, "신규 편입", f"[{s_name}] 매수 편입 성공", f"단가 {buy_p}원, 수량 {qty}주")
                st.success(f"[{s_name}] 장부 합성 성공!")
                time.sleep(0.3)
                st.rerun()
            except Exception as e:
                st.error(f"자산 편입 실패: {str(e)}")

    st.divider()

    tab_port, tab_hist, tab_log = st.tabs(["💼 포트폴리오 자산", "📝 가치 실현 내역", "⚙️ 시스템 가동 로그"])

    with tab_port:
        st.write("⚡ **Forward 멀티 모델 실시간 제어판**")
        col_sync1 = st.columns(1)[0]
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        if col_sync1.button("🔄 가치 밸류에이션 전면 강제 재연산", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("v14.0 하이브리드 격벽 무력화 강제 동기화 중...", expanded=True) as status:
                execute_on_demand_sync(supabase, username, naver_id, naver_secret, force=True)
                status.update(label="전역 공용 캐시 및 밸류에이션 리레이팅 리셋 완결!", state="complete")
            st.rerun()

        st.divider()
        if not portfolio_data:
            st.info("장부에 보유 주식이 없습니다.")
            return

        total_invest, total_value = 0, 0
        display_rows = []
        
        for row in portfolio_data:
            cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
            curr_price = cache.get('current_price', row['buy_price'])
            day_pct = cache.get('pct_change', 0.0)
            target_price = cache.get('target_2026', row['buy_price'])
            bear_target = cache.get('bear_target', int(target_price * 0.78))
            bull_target = cache.get('bull_target', int(target_price * 1.25))
            target_multiple = cache.get('target_multiple', 10.0)
            peg = cache.get('peg', 1.0)
            broker_target = cache.get('broker_target', 0.0)
            
            raw_trends = cache.get('applied_trends', ["MARKET_SATELLITE", "일반제조업"])
            quant_tier = raw_trends[0]
            krx_sector = cache.get('krx_sector', raw_trends[1])
            engine_model = "PER" if cache.get('eps', 0.0) > 0 else "PBR"
            
            pnl_amt = (curr_price - row['buy_price']) * row['qty']
            pnl_pct = ((curr_price - row['buy_price']) / row['buy_price']) * 100 if row['buy_price'] > 0 else 0
            safe_target_price = int(target_price * 0.95)
            
            cut_loss_price = int(cache.get('cut_loss_price', row['buy_price'] * 0.85))
            expected_loss_amt = (cut_loss_price - row['buy_price']) * row['qty']
            
            total_invest += row['buy_price'] * row['qty']
            total_value += curr_price * row['qty']
            
            val_ratio = curr_price / target_price if target_price > 0 else 1.0
            if val_ratio < 0.5: status_emoji = "🛒 멀티플 극저평가"
            elif val_ratio < 0.75: status_emoji = "🔵 안전마진 확보"
            elif val_ratio < 0.95: status_emoji = "🟢 가치 수렴 중"
            else: status_emoji = "🎯 사이클 고점 도달"

            display_rows.append({
                "밸류에이션 상태": status_emoji, "종목명": row['name'], "현재가": curr_price, "전일비": day_pct, "평단가": row['buy_price'], "보유지분": row['qty'], "평가손익": pnl_amt, "수익률": pnl_pct,
                "비관": bear_target, "기준(최고치)": target_price, "낙관": bull_target, "안전목표가": safe_target_price, "목표평가손익": (safe_target_price - row['buy_price']) * row['qty'],
                "PEG": peg, "적용배수": target_multiple, "KRX섹터": krx_sector, "엔진모델": engine_model,
                "손절가": cut_loss_price, "손절시손익": expected_loss_amt,
                "외인20일": cache.get('foreign_20d_flow', 0.0), "기관20일": cache.get('institution_20d_flow', 0.0), "에프앤목표가": broker_target, "raw_data": row
            })

        total_pnl = total_value - total_invest
        total_pnl_pct = (total_pnl / total_invest) * 100 if total_invest > 0 else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("총 투입 자본", f"{total_invest:,} 원")
        c2.metric("현재 평가 자산", f"{total_value:,} 원")
        c3.metric("포트폴리오 수익", f"{total_pnl:,} 원", f"{total_pnl_pct:+.2f}%")
        
        df_base = pd.DataFrame(display_rows)
        df_disp = pd.DataFrame()
        df_disp["상태"] = df_base["밸류에이션 상태"]
        df_disp["종목명"] = df_base["종목명"]
        df_disp["KRX 업종"] = df_base["KRX섹터"]
        df_disp["현재가"] = df_base["현재가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["🛡️ 안전탈출(-5%)"] = df_base["안전목표가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["탈출 시 예상수익"] = df_base["목표평가손익"].apply(lambda x: f"₩ {int(x):+,}" if x != 0 else "₩ 0")
        df_disp["📉 비관(Bear)"] = df_base["비관"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["🟢 기준(Base)"] = df_base["기준(최고치)"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["📈 낙관(Bull)"] = df_base["낙관"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["외인 20일(주)"] = df_base["외인20일"].apply(lambda x: f"{int(x):+,}")
        df_disp["기관 20일(주)"] = df_base["기관20일"].apply(lambda x: f"{int(x):+,}")
        df_disp["진성 PEG"] = df_base["PEG"].apply(lambda x: f"📊 {x:.2f}")

        def style_mts_color(row):
            styles = [''] * len(row)
            pnl = df_base.loc[row.name, '수익률']
            day = df_base.loc[row.name, '전일비']
            peg_val = df_base.loc[row.name, 'PEG']
            f_buy = df_base.loc[row.name, '외인20일']
            
            pnl_style = 'background-color: rgba(240, 68, 82, 0.12); color: #F04452; font-weight: bold;' if pnl > 0 else ('background-color: rgba(49, 130, 246, 0.12); color: #3182F6; font-weight: bold;' if pnl < 0 else 'color: #4E5968;')
            safe_style = 'background-color: rgba(240, 150, 40, 0.08); color: #E67E22; font-weight: bold;'
            
            if "🛡️ 안전탈출(-5%)" in df_disp.columns: styles[df_disp.columns.get_loc('🛡️ 안전탈출(-5%)')] = safe_style
            if "탈출 시 예상수익" in df_disp.columns: styles[df_disp.columns.get_loc('탈출 시 예상수익')] = safe_style
            if peg_val < 1.0 and peg_val > 0 and "진성 PEG" in df_disp.columns: styles[df_disp.columns.get_loc('진성 PEG')] = 'background-color: rgba(0, 180, 100, 0.08); color: #00B464; font-weight: bold;'
            if f_buy > 0 and "외인 20일(주)" in df_disp.columns: styles[df_disp.columns.get_loc('외인 20일(주)')] = 'color: #F04452; font-weight: bold;'
            return styles

        styled_df = df_disp.style.apply(style_mts_color, axis=1)
        selection_event = st.dataframe(styled_df, width="stretch", on_select="rerun", selection_mode="single-row")
        
        selected_indices = selection_event.get("selection", {}).get("rows", [])
        
        if selected_indices:
            selected_idx = selected_indices[0]
            selected_stock = display_rows[selected_idx]
            s_name = selected_stock["종목명"]
            raw_row = selected_stock["raw_data"]
            s_ticker = raw_row['ticker']
            s_cache = raw_row.get("analysis_cache", {})
            
            # 👍 [원복 2] 그리드 터치 시 '하단 퀀트 통제실 내부'에 평단가/수매/매도 popover 3대장 완벽 결합
            st.markdown(f"### 🛠️ [{s_name}] 퀀트 익절/손절 실전 통제실")
            
            col_btn1, col_btn2, col_btn3 = st.columns(3)
            with col_btn1:
                with st.popover("✏️ 장부 평단/수량 수정", use_container_width=True):
                    new_p = st.number_input("수정할 평단가", value=int(raw_row['buy_price']), key=f"p_ed_{s_ticker}")
                    new_q = st.number_input("수정할 보유수량", value=int(raw_row['qty']), key=f"q_ed_{s_ticker}")
                    if st.button("수정 장부 인가", key=f"b_ed_{s_ticker}", use_container_width=True):
                        supabase.table("user_portfolio").update({"buy_price": new_p, "qty": new_q}).eq("id", raw_row['id']).execute()
                        insert_log(supabase, username, "장부 수정", f"[{s_name}] 수정", f"평단가 {new_p} / 수량 {new_q}")
                        st.success("장부 정보 정정 고시 완료!")
                        time.sleep(0.3)
                        st.rerun()
            with col_btn2:
                with st.popover("🛒 분할 추가매수", use_container_width=True):
                    add_p = st.number_input("추가 매수가격", value=int(s_cache.get('current_price', raw_row['buy_price'])), key=f"p_add_{s_ticker}")
                    add_q = st.number_input("추가 매수수량", value=10, key=f"q_add_{s_ticker}")
                    if st.button("추가매수 체결", key=f"b_add_{s_ticker}", use_container_width=True):
                        current_total_cost = raw_row['buy_price'] * raw_row['qty']
                        new_total_cost = current_total_cost + (add_p * add_q)
                        new_qty = raw_row['qty'] + add_q
                        new_avg_price = int(new_total_cost / new_qty)
                        supabase.table("user_portfolio").update({"buy_price": new_avg_price, "qty": new_qty}).eq("id", raw_row['id']).execute()
                        insert_log(supabase, username, "추가 매수", f"[{s_name}] {add_q}주 추매", f"단가 {add_p}원 합성")
                        st.success("가중평균 평단가 합성 완료!")
                        time.sleep(0.3)
                        st.rerun()
            with col_btn3:
                with st.popover("❌ 자산 매도(청산)", use_container_width=True):
                    st.write(f"현재 보유 수량: **{raw_row['qty']}주** (평단가: {raw_row['buy_price']:,}원)")
                    sell_p = st.number_input("매도 단가", value=int(s_cache.get('current_price', raw_row['buy_price'])), key=f"p_sl_{s_ticker}")
                    sell_q = st.number_input("매도 수량", min_value=1, max_value=int(raw_row['qty']), value=int(raw_row['qty']), key=f"q_sl_{s_ticker}")
                    if st.button("🚨 매도 집행", key=f"b_sl_{s_ticker}", use_container_width=True, type="primary"):
                        profit_amt = (sell_p - raw_row['buy_price']) * sell_q
                        profit_pct = round(((sell_p - raw_row['buy_price']) / raw_row['buy_price']) * 100, 2)
                        try:
                            supabase.table("user_history").insert({
                                "username": username, "ticker": raw_row['ticker'], "name": s_name,
                                "buy_price": raw_row['buy_price'], "sell_price": sell_p, "qty": sell_q,
                                "profit_amt": profit_amt, "profit_pct": profit_pct
                            }).execute()
                        except Exception as e: print(f"히스토리 전송 누수: {e}")
                        
                        if sell_q == raw_row['qty']:
                            supabase.table("user_portfolio").delete().eq("id", raw_row['id']).execute()
                        else:
                            supabase.table("user_portfolio").update({"qty": raw_row['qty'] - sell_q}).eq("id", raw_row['id']).execute()
                            
                        insert_log(supabase, username, "자산 매도", f"[{s_name}] {sell_q}주 청산", f"손익 {profit_amt:,}원 ({profit_pct:+.2f}%) 실현")
                        st.error("포지션 청산 오더 집행 완결!")
                        time.sleep(0.3)
                        st.rerun()

            st.divider()
            
            t1, t2, t3 = st.tabs(["📉 3단계 시나리오 및 수급 판세", "📰 전방 사업 명세", "📊 실적 턴어라운드 감지"])
            with t1:
                eps_val = s_cache.get('eps', 0.0)
                bps_val = s_cache.get('bps', 0.0)
                current_status = selected_stock['밸류에이션 상태']  
                status_tooltip = f"KRX 섹터: {selected_stock['KRX섹터']} | 연산 엔진: {selected_stock['엔진모델']} 모형"

                st.markdown(f"**• 종합 투자 의견:** <span title='{status_tooltip}' style='cursor: help; border-bottom: 1px dashed #4E5968; font-weight: bold;'>{current_status} ⓘ</span>", unsafe_allow_html=True)
                st.markdown(f"**• TTM 기초 지표:** EPS `{eps_val:,.0f}원` | BPS `{bps_val:,.0f}원` | **진성 기하학적 PEG:** `{selected_stock['PEG']}x`")
                st.markdown(f"**📉 비관적 저점 방어선 (Bear Case Target):** `₩ {selected_stock['비관']:,}`원")
                st.markdown(f"**🟢 기준 내재가치 최고점 (Base Case Target):** `₩ {selected_stock['기준(최고치)']:,}`원")
                st.markdown(f"**📈 유동성 오버슈팅 상방선 (Bull Case Target):** `₩ {selected_stock['낙관']:,}`원")
                st.markdown(f"**🛡️ 실전 대기 분할 안전탈출가 (-5%):** `₩ {selected_stock['안전목표가']:,}원` (청산 시 최종 누적 실현이익: `{selected_stock['목표평가손익']:,}원`)")
                st.markdown(f"**🚨 마지노선 손절가 격벽:** `₩ {selected_stock['손절가']:,}원` (손실 규모: `{selected_stock['손절시손익']:,}원`)")
                st.markdown(f"**🏛️ 에프앤가이드 여의도 컨센서스 목표주가 평균:** `₩ {int(selected_stock['에프앤목표가']):,}`원")
                
                st.write("**실시간 추적 뉴스**")
                for idx, news in enumerate(s_cache.get('news_list', []), 1):
                    st.markdown(f"[{idx}] [{news['title']}]({news['link']})")
                
            with t2:
                summary_text = s_cache.get('summary', '')
                if not summary_text: summary_text = "기업 분석 데이터를 가져올 수 없습니다 (네트워크 지연)."
                st.write(f"**📢 기업 개요 및 펀더멘탈 요약:** {summary_text}")
                st.write(f"**• 실적 턴어라운드율 모멘텀 총평:** {s_cache.get('bm_summary', '-')}")
                bm_list = s_cache.get('bm_list', [])
                if bm_list: st.table(pd.DataFrame(bm_list, columns=["사업부문", "주요품목", "구분", "비중(%)"]))
                else: st.info("사업 부문 명세를 로드할 수 없습니다.")
                    
            with t3:
                if s_cache.get('q_headers') and len(s_cache['q_headers']) >= 2:
                    fig, ax1 = plt.subplots(figsize=(10, 4.5))
                    ax1.set_facecolor('#FFFFFF')
                    ax1.bar(s_cache['q_headers'], s_cache['q_revenues'], color='#3182F6', alpha=0.8, width=0.3, label="매출액(억)")
                    ax1.set_ylabel('매출액', color='#8B95A1')
                    ax2 = ax1.twinx()
                    ax2.plot(s_cache['q_headers'], s_cache['q_op_profits'], color='#F04452', marker='o', linewidth=3, markersize=8, label="영업이익(억)")
                    ax2.set_ylabel('영업이익', color='#8B95A1')
                    ax2.axhline(0, color='#8B95A1', linewidth=1, linestyle='--')
                    st.pyplot(fig)
                else:
                    st.info("실적 차트 데이터가 부족합니다.")

    with tab_hist:
        st.subheader("📝 자산 매도(청산) 히스토리")
        hist_res = supabase.table("user_history").select("*").eq("username", username).order("created_at", desc=True).execute()
        if not hist_res.data: st.info("아직 자산 매도 내역이 없습니다.")
        else:
            total_realized = sum([r['profit_amt'] for r in hist_res.data])
            win_count = sum([1 for r in hist_res.data if r['profit_amt'] > 0])
            win_rate = (win_count / len(hist_res.data)) * 100
            
            h1, h2 = st.columns(2)
            h1.metric("누적 실현 손익", f"{total_realized:,} 원")
            h2.metric("매매 승률", f"{win_rate:.1f} %")
            
            df_hist = pd.DataFrame(hist_res.data)
            df_hist['created_at'] = pd.to_datetime(df_hist['created_at']) + pd.Timedelta(hours=9)
            df_hist['created_at'] = df_hist['created_at'].dt.strftime('%Y-%m-%d %H:%M')
            df_hist = df_hist[['created_at', 'name', 'buy_price', 'sell_price', 'qty', 'profit_amt', 'profit_pct']]
            df_hist.columns = ['매도일시', '종목명', '진입가', '청산가', '수량', '실현손익', '수익률(%)']
            
            df_hist['진입가'] = df_hist['진입가'].apply(lambda x: f"₩ {int(parse_num(str(x))):,}")
            df_hist['청산가'] = df_hist['청산가'].apply(lambda x: f"₩ {int(parse_num(str(x))):,}")
            df_hist['수량'] = df_hist['수량'].apply(lambda x: f"{int(parse_num(str(x))):,} 주")
            df_hist['실현손익'] = df_hist['실현손익'].apply(lambda x: f"₩ {int(parse_num(str(x))):,}")
            st.dataframe(df_hist, width="stretch")

    with tab_log:
        st.subheader("⚙️ 시스템 엔진 처리 기록")
        log_res = supabase.table("user_logs").select("*").eq("username", username).order("created_at", desc=True).execute()
        if not log_res.data: st.info("시스템 처리 기록이 없습니다.")
        else:
            df_log = pd.DataFrame(log_res.data)
            df_log['created_at'] = pd.to_datetime(df_log['created_at']) + pd.Timedelta(hours=9)
            df_log['created_at'] = df_log['created_at'].dt.strftime('%Y-%m-%d %H:%M:%S')
            df_log = df_log[['created_at', 'module', 'summary', 'details']]
            df_log.columns = ['시간', '모듈', '요약', '상세내역']
            st.dataframe(df_log, width="stretch")
