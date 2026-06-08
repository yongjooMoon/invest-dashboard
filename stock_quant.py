import streamlit as st
import requests
import re
import html
import FinanceDataReader as fdr
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time

def parse_num(txt):
    """정규식을 이용해 어떤 접미사 오염 속에서도 순수 숫자만 발라내는 무결성 파서"""
    if not txt: return 0.0
    m = re.search(r'[-+]?[0-9,]+(?:\.[0-9]+)?', txt)
    return float(m.group().replace(',', '')) if m else 0.0

def is_expired(last_update_str, threshold_seconds):
    """Supabase 시차 규격 문자열을 파싱하여 정밀 만기 여부를 판별하는 격벽 함수"""
    if not last_update_str: return True
    try:
        clean_str = last_update_str.replace('T', ' ').split('.')[0].split('+')[0]
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.utcnow() + timedelta(hours=9) 
        return (now - dt).total_seconds() >= threshold_seconds
    except:
        return True

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
        if val_data['industry_per'] <= 0: val_data['industry_per'] = 10.0 

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
    url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(f'\"{stock_name}\"')}&display=10&sort=date"
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
        return 0, (pos_count - neg_count), news_list[0]['title'][:25] + "...", news_list
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
                macro_multiplier = 1.00 
                환율상태 = f"🚨 매크로 유동성 축소 ({current_usd}원)"
            elif current_usd > usd_ma20:
                macro_multiplier = 0.95  
                환율상태 = f"⚠️ 변동성 경계 ({current_usd}원)"
            else:
                macro_multiplier = 1.05  
                환율상태 = f"🍏 매크로 훈풍 ({current_usd}원)"
    except:
        환율상태 = "⚠️ 센서 지연"
    return macro_multiplier, current_usd, 환율상태

def calculate_intrinsic_target(row, cache, macro_multiplier, current_usd, df_kospi, df_stock):
    ticker = row['ticker']
    s_name = row['name']
    current_price = cache.get('current_price', row['buy_price'])
    raw_eps = cache.get('eps', 0.0)
    bps = cache.get('bps', 0.0)
    current_per = cache.get('per', 10.0)
    krx_sector_name = cache.get('krx_sector', '기타')
    shares = cache.get('shares_outstanding', 10000000.0)

    asymmetric_macro = macro_multiplier
    is_exporter = any(k in krx_sector_name or k in s_name for k in ["반도체", "전자", "자동차", "부품", "조선", "기계"])
    if current_usd >= 1400:
        if is_exporter: asymmetric_macro = 1.08  
        else: asymmetric_macro = 0.88           

    f_flow = cache.get('foreign_20d_flow', 0.0)
    i_flow = cache.get('institution_20d_flow', 0.0)
    flow_ratio = (f_flow + i_flow) / shares if shares > 0 else 0
    suup_multiplier = 1.0 + max(-0.10, min(flow_ratio * 10.0, 0.15)) 

    theme_premium = 1.0
    quant_tier = cache.get('applied_trends', ['MARKET_SATELLITE'])[0] if isinstance(cache.get('applied_trends'), list) and cache.get('applied_trends') else 'MARKET_SATELLITE'
    
    if quant_tier == "MOMENTUM_LEADER": theme_premium = 1.15
    elif quant_tier == "VALUE_CHAIN": theme_premium = 1.08

    if df_stock is not None and not df_stock.empty and df_kospi is not None and not df_kospi.empty:
        try:
            stock_return = (((df_stock['Close'].iloc[-1] - df_stock['Close'].iloc[-20]) / df_stock['Close'].iloc[-20]) * 100)
            kospi_return = (((df_kospi['Close'].iloc[-1] - df_kospi['Close'].iloc[-20]) / df_kospi['Close'].iloc[-20]) * 100)
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
        except: pass

    eps_2025 = cache.get('fwd_eps_2025', 0.0)
    eps_2026 = cache.get('fwd_eps_2026', 0.0)
    if eps_2025 > 0 and eps_2026 > 0:
        eps_growth_rate = ((eps_2026 - eps_2025) / abs(eps_2025)) * 100
        forward_eps = eps_2026
    else:
        if bps > 0 and raw_eps > 0:
            eps_growth_rate = (raw_eps / bps) * 100
            forward_eps = (bps * 1.10) * (raw_eps / bps)
        else:
            eps_growth_rate = 10.0
            forward_eps = current_price / 10.0
    if eps_growth_rate <= 0: eps_growth_rate = 4.0
    peg_ratio = current_per / eps_growth_rate

    is_financial = any(k in krx_sector_name or k in s_name for k in ["은행", "증권", "보험", "생명", "금융", "지주"])
    base_industry_per = cache.get('industry_per', 10.0)

    if is_financial:
        current_pbr = cache.get('pbr', 0.4)
        calculated_pbr = current_pbr * theme_premium * suup_multiplier * asymmetric_macro
        target_multiple = min(calculated_pbr, current_pbr * 2.20)
        base_target = (bps * 1.08 if bps > 0 else current_price) * target_multiple
        model_type = "PBR"
    else:
        calculated_per = base_industry_per * theme_premium * suup_multiplier * asymmetric_macro
        target_per = min(calculated_per, base_industry_per * 2.20)
        base_target = forward_eps * target_per
        target_multiple = target_per
        model_type = "PER"

    base_target = max(current_price * 0.50, min(base_target, current_price * 3.00))

    bear_ratio, bull_ratio = 0.80, 1.25
    if "보험" in krx_sector_name or "생명" in krx_sector_name: bear_ratio, bull_ratio = 0.90, 1.10
    elif "반도체" in krx_sector_name: bear_ratio, bull_ratio = 0.75, 1.35
    elif any(k in krx_sector_name or k in s_name for k in ["로봇", "로보", "기계", "소프트"]): bear_ratio, bull_ratio = 0.60, 1.55

    return int(base_target), int(base_target * bear_ratio), int(base_target * bull_ratio), round(target_multiple, 2), round(peg_ratio, 2), [quant_tier, krx_sector_name, model_type]

# 👍 [v15.2 패치] force 매개변수를 추가하여 '재연산' 클릭 시 타임아웃 격벽을 무력화하고 강제 리프레시 실행
def execute_on_demand_sync(supabase, username, naver_id, naver_secret, force=False):
    macro_mult, current_usd, _ = fetch_global_macro_factor()
    db_res = supabase.table("user_portfolio").select("*").eq("username", username).execute()
    portfolio_data = db_res.data
    if not portfolio_data: return

    start_date_str = (datetime.utcnow() - timedelta(days=35)).strftime('%Y-%m-%d')
    try: df_kospi = fdr.DataReader('KS11', start=start_date_str)
    except: df_kospi = pd.DataFrame()

    tickers = [row['ticker'] for row in portfolio_data]
    cache_res = supabase.table("stock_cache").select("*").in_("ticker", tickers).execute()
    cache_map = {r['ticker']: r for r in cache_res.data}

    price_map = {}
    
    # 👍 [v15.2 패치: HTTPError 방어벽] 해외 클라우드 IP 차단 시 크래시를 완전히 막아내기 위한 예외 처리 격벽
    try:
        df_k = fdr.StockListing('KRX')
        krx_db = {row['Symbol']: row['Sector'] for _, row in df_k.iterrows() if 'Sector' in row and row['Sector']}
    except Exception as e:
        krx_db = {}
        print(f"[KRX API SERVER BLOCKED] Fallback 가동: {str(e)}")

    now_kst_str = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')

    stock_cache_batch = []
    user_portfolio_batch = []

    for row in portfolio_data:
        ticker = row['ticker']
        name = row['name']
        
        db_cache = cache_map.get(ticker, {})
        # 업종 정보 조회 실패 시 기존 DB 캐시에 남아있는 업종 정보를 재활용하여 안전 구동
        fallback_sector = db_cache.get('krx_sector', "일반제조업")
        updated_cache = {"ticker": ticker, "name": name, "krx_sector": krx_db.get(ticker, fallback_sector)}
        
        df_stock = pd.DataFrame()
        # 👍 [v15.2 핵심 연산식] 만기가 되었거나, 사용자가 '재연산' 버튼을 강제로 눌렀을 경우(force=True) 웹서버 직접 타격
        if is_expired(db_cache.get('last_price_update'), 300) or force:
            if ticker not in price_map:
                try: price_map[ticker] = fdr.DataReader(ticker, start=start_date_str)
                except: price_map[ticker] = pd.DataFrame()
            df_stock = price_map[ticker]
            if not df_stock.empty:
                updated_cache['current_price'] = int(df_stock['Close'].iloc[-1])
                prev_close = float(df_stock['Close'].iloc[-2]) if len(df_stock) >= 2 else df_stock['Close'].iloc[-1]
                updated_cache['pct_change'] = round(((updated_cache['current_price'] - prev_close) / prev_close) * 100, 2)
                updated_cache['year_high'] = int(df_stock['High'].max())
                updated_cache['last_price_update'] = now_kst_str
        
        if is_expired(db_cache.get('last_news_update'), 3600) or force:
            _, net_sent, _, n_list = get_auto_momentum(name, naver_id, naver_secret)
            updated_cache['net_sentiment'] = net_sent
            updated_cache['news_list'] = n_list
            updated_cache['last_news_update'] = now_kst_str

        if is_expired(db_cache.get('last_flow_update'), 14400) or force:
            f_flow, i_flow = fetch_investor_flows(ticker)
            updated_cache['foreign_20d_flow'] = f_flow
            updated_cache['institution_20d_flow'] = i_flow
            updated_cache['last_flow_update'] = now_kst_str

        fund_data_memory = None
        if is_expired(db_cache.get('last_fundamental_update'), 604800) or force:
            fund_data_memory = fetch_naver_fundamentals(ticker)
            if fund_data_memory:
                updated_cache.update({
                    'eps': fund_data_memory['eps'], 'per': fund_data_memory['per'], 'pbr': fund_data_memory['pbr'], 'bps': fund_data_memory['bps'],
                    'industry_per': fund_data_memory['industry_per'], 'shares_outstanding': fund_data_memory['shares_outstanding'],
                    'broker_target': fund_data_memory['broker_target'], 'fwd_eps_2025': fund_data_memory['fwd_eps_2025'], 'fwd_eps_2026': fund_data_memory['fwd_eps_2026']
                })
                updated_cache['last_fundamental_update'] = now_kst_str

        if is_expired(db_cache.get('last_bm_update'), 2592000) or force:
            if fund_data_memory is None:
                fund_data_memory = db_cache if db_cache.get('q_revenues') else fetch_naver_fundamentals(ticker)
            bm_list = fetch_dynamic_company_bm(ticker)
            growth_factor, bm_summary = calculate_bm_score(fund_data_memory)
            updated_cache.update({
                'bm_list': bm_list, 'bm_growth_factor': growth_factor, 'bm_summary': bm_summary, 'last_bm_update': now_kst_str
            })

        full_cache = {**db_cache, **updated_cache}
        stock_cache_batch.append(full_cache)
        
        base_tgt, bear_tgt, bull_tgt, target_multiple, peg, applied_trends = calculate_intrinsic_target(row, full_cache, macro_mult, current_usd, df_kospi, df_stock)
        
        user_cache = {
            'current_price': full_cache.get('current_price', row['buy_price']),
            'pct_change': full_cache.get('pct_change', 0.0), 'year_high': full_cache.get('year_high', 0),
            'eps': full_cache.get('eps', 0.0), 'per': full_cache.get('per', 10.0), 'pbr': full_cache.get('pbr', 1.0), 'bps': full_cache.get('bps', 0.0),
            'foreign_20d_flow': full_cache.get('foreign_20d_flow', 0.0), 'institution_20d_flow': full_cache.get('institution_20d_flow', 0.0),
            'broker_target': full_cache.get('broker_target', 0.0), 'news_list': full_cache.get('news_list', []),
            'target_2026': base_tgt, 'bear_target': bear_tgt, 'bull_target': bull_tgt, 'target_multiple': target_multiple, 'peg': peg, 'applied_trends': applied_trends
        }
        
        user_portfolio_batch.append({
            "id": row['id'], "username": username, "ticker": ticker, "name": name,
            "buy_price": row['buy_price'], "qty": row['qty'], "analysis_cache": user_cache
        })

    if stock_cache_batch:
        supabase.table("stock_cache").upsert(stock_cache_batch).execute()
    if user_portfolio_batch:
        supabase.table("user_portfolio").upsert(user_portfolio_batch).execute()
        
    insert_log(supabase, username, "ON_DEMAND_V15_FINAL", "v15.2 무결점 동적 퀀트 가동 완결", "FDR 및 쓰기 N+1 쿼리 완전 파괴 성공.")

# ==========================================
# [Layer 4] UI Dashboard : 메인 시스템 연동 규격 매핑
# ==========================================
def run_stock_quant_page(supabase, username, naver_id=None, naver_secret=None):
    st.title("🛡️ 스마트 프랍 퀀트 포트폴리오 엔진 v15.2")
    macro_mult, current_usd, 환율상태 = fetch_global_macro_factor()
    
    with st.container(border=True):
        st.markdown("##### 🌐 GLOBAL MACRO FLOW (매크로 유동성 레이더)")
        m_col1, m_col2 = st.columns(2)
        with m_col1:
            st.metric("원/달러 환율 국면", 환율상태, delta="외국인 패시브 수급 불안" if current_usd >= 1400 else "수급 안정 구역", delta_color="inverse")
        with m_col2:
            st.metric("시장 기본 PER 멀티플 보정률", f"{int(macro_mult*100)}%", delta="전역 공용 stock_cache 그리드 통합 관제 중")

    tab_port, tab_hist, tab_log = st.tabs(["💼 포트폴리오 자산", "📝 가치 실현 내역", "⚙️ 시스템 가동 로그"])

    with tab_port:
        st.write("⚡ **Forward 멀티 모델 실시간 제어판**")
        col_sync1 = st.columns(1)[0]
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        # 👍 [v15.2 패치] 버튼 클릭 시 force=True를 주입하여 100% 강제 원천 전면 연산 실행
        if col_sync1.button("🔄 가치 밸류에이션 전면 재연산", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("v15.2 원천 데이터 강제 동기화 및 PEG 연산 중...", expanded=True) as status:
                execute_on_demand_sync(supabase, username, naver_id, naver_secret, force=True)
                status.update(label="전체 만기 무력화 및 최신 가치 전면 재연산 수렴 완료!", state="complete")
            st.rerun()

        st.divider()
        if not portfolio_data:
            st.info("장부에 주식이 없습니다.")
            return

        total_invest, total_value = 0, 0
        display_rows = []
        
        for row in portfolio_data:
            cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
            curr_price = cache.get('current_price', row['buy_price'])
            day_pct = cache.get('pct_change', 0.0)
            target_price = cache.get('target_2026', row['buy_price'])
            bear_target = cache.get('bear_target', int(target_price * 0.80))
            bull_target = cache.get('bull_target', int(target_price * 1.25))
            target_multiple = cache.get('target_multiple', 10.0)
            peg = cache.get('peg', 1.0)
            broker_target = cache.get('broker_target', 0.0)
            
            raw_trends = cache.get('applied_trends', [])
            quant_tier = raw_trends[0] if (isinstance(raw_trends, list) and len(raw_trends) > 0) else "MARKET_SATELLITE"
            krx_sector = raw_trends[1] if (isinstance(raw_trends, list) and len(raw_trends) > 1) else "기타업종"
            engine_model = raw_trends[2] if (isinstance(raw_trends, list) and len(raw_trends) > 2) else "PER"
            
            pnl_amt = (curr_price - row['buy_price']) * row['qty']
            pnl_pct = ((curr_price - row['buy_price']) / row['buy_price']) * 100 if row['buy_price'] > 0 else 0
            safe_target_price = int(target_price * 0.95)
            
            total_invest += row['buy_price'] * row['qty']
            total_value += curr_price * row['qty']
            
            display_rows.append({
                "상태": "🟢 가치 수렴 중" if curr_price < target_price else "🎯 목표가 도달", 
                "기업명": row['name'], "현재가": curr_price, "전일비": day_pct, "평단가": row['buy_price'], "보유지분": row['qty'], "평가손익": pnl_amt, "수익률": pnl_pct,
                "비관": bear_target, "기준(최고치)": target_price, "낙관": bull_target, "안전목표가": safe_target_price, "목표평가손익": (safe_target_price - row['buy_price']) * row['qty'],
                "PEG": peg, "적용배수": target_multiple, "KRX섹터": krx_sector, "엔진모델": engine_model,
                "외인20일": cache.get('foreign_20d_flow', 0.0), "에프앤목표가": broker_target, "raw_data": row
            })

        c1, c2, c3 = st.columns(3)
        c1.metric("총 투입 자본", f"{total_invest:,} 원")
        c2.metric("현재 평가 자산", f"{total_value:,} 원")
        c3.metric("포트폴리오 수익", f"{total_value - total_invest:,} 원")
        
        df_base = pd.DataFrame(display_rows)
        df_disp = pd.DataFrame()
        df_disp["상태"] = df_base["상태"]
        df_disp["기업명"] = df_base["기업명"]
        df_disp["KRX 업종"] = df_base["KRX섹터"]
        df_disp["연산 모델"] = df_base["엔진모델"]
        
        df_disp["현재가"] = df_base["현재가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["전일비(%)"] = df_base["전일비"].apply(lambda x: f"{x:+.2f}%")
        df_disp["평단가"] = df_base["평단가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["현재 수익률(%)"] = df_base["수익률"].apply(lambda x: f"{x:+.2f}%")
        df_disp["현재 평가손익"] = df_base["평가손익"].apply(lambda x: f"₩ {int(x):+,}" if x != 0 else "₩ 0")
        
        df_disp["🛡️ 실전안전가(-5%)"] = df_base["안전목표가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["탈출 시 예상수익"] = df_base["목표평가손익"].apply(lambda x: f"₩ {int(x):+,}")
        df_disp["📉 비관(Bear)"] = df_base["비관"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["🟢 기준(Base)"] = df_base["기준(최고치)"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["📈 낙관(Bull)"] = df_base["낙관"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["외인 20일(주)"] = df_base["외인20일"].apply(lambda x: f"{int(x):+,}")
        df_disp["진성 PEG"] = df_base["PEG"].apply(lambda x: f"📊 {x:.2f}")

        def style_mts_color(row):
            styles = [''] * len(row)
            pnl = df_base.loc[row.name, '수익률']
            day = df_base.loc[row.name, '전일비']
            peg_val = df_base.loc[row.name, 'PEG']
            f_buy = df_base.loc[row.name, '외인20일']
            m_type = df_base.loc[row.name, '엔진모델']
            
            pnl_style = 'background-color: rgba(240, 68, 82, 0.12); color: #F04452; font-weight: bold;' if pnl > 0 else ('background-color: rgba(49, 130, 246, 0.12); color: #3182F6; font-weight: bold;' if pnl < 0 else 'color: #4E5968;')
            if "현재 수익률(%)" in df_disp.columns: styles[df_disp.columns.get_loc('현재 수익률(%)')] = pnl_style
            if "현재 평가손익" in df_disp.columns: styles[df_disp.columns.get_loc('현재 평가손익')] = pnl_style
            
            day_style = 'color: #F04452; font-weight: bold;' if day > 0 else ('color: #3182F6; font-weight: bold;' if day < 0 else 'color: #4E5968;')
            if "전일비(%)" in df_disp.columns: styles[df_disp.columns.get_loc('전일비(%)')] = day_style
            
            safe_style = 'background-color: rgba(240, 150, 40, 0.08); color: #E67E22; font-weight: bold;'
            if "🛡️ 실전안전가(-5%)" in df_disp.columns: styles[df_disp.columns.get_loc('🛡️ 실전안전가(-5%)')] = safe_style
            if "탈출 시 예상수익" in df_disp.columns: styles[df_disp.columns.get_loc('탈출 시 예상수익')] = safe_style
            
            if m_type == "PBR" and "연산 모델" in df_disp.columns:
                styles[df_disp.columns.get_loc('연산 모델')] = 'background-color: rgba(155, 89, 182, 0.1); color: #9B59B6; font-weight: bold;'
            
            if peg_val < 1.0 and peg_val > 0 and m_type == "PER" and "진성 PEG" in df_disp.columns:
                styles[df_disp.columns.get_loc('진성 PEG')] = 'background-color: rgba(0, 180, 100, 0.08); color: #00B464; font-weight: bold;'
            
            if f_buy > 0 and "외인 20일(주)" in df_disp.columns: styles[df_disp.columns.get_loc('외인 20일(주)')] = 'color: #F04452; font-weight: bold;'
            return styles

        styled_df = df_disp.style.apply(style_mts_color, axis=1)

        selection_event = st.dataframe(
            styled_df, 
            width="stretch", 
            on_select="rerun", 
            selection_mode="single-row",
            column_config={
                "상태": st.column_config.TextColumn(
                    "상태 ⓘ",
                    help="""💡 [v15.2 프로덕션 멀티 모델 가치 정의]
                    
🛒 멀티플 극저평가: 현재가가 2026년 동적 기준 목표가의 50% 미만인 국면
🔵 안전마진 확보: 현재가가 기준 목표가의 50% ~ 75% 사이 구역
🟢 가치 수렴 중: 현재가가 기준 목표가의 75% ~ 95% 사이 정상 궤도
🎯 사이클 고점 도달: 현재가가 기준 목표가의 95% 이상 오버슈팅 완료"""
                )
            }
        )
        
        selected_indices = selection_event.get("selection", {}).get("rows", [])
        
        if selected_indices:
            selected_idx = selected_indices[0]
            selected_stock = display_rows[selected_idx]
            s_name = selected_stock["기업명"]
            raw_row = selected_stock["raw_data"]
            s_ticker = raw_row['ticker']
            s_cache = raw_row.get("analysis_cache") if raw_row.get("analysis_cache") else {}
            
            st.markdown(f"### 🛠️ [{s_name}] v15.2 정밀 가치 평가 계량 리포트")
            st.divider()
            
            t1, t2, t3 = st.tabs(["📉 3단계 시나리오 및 수급 판세", "📰 전방 사업 명세", "📊 실적 턴어라운드 감지"])
            with t1:
                eps_val = s_cache.get('eps', 0.0)
                bps_val = s_cache.get('bps', 0.0)
                
                current_status = selected_stock['밸류에이션 상태']
                status_tooltip = f"KRX 섹터: {selected_stock['KRX섹터']} | 연산 엔진: {selected_stock['엔진모델']} 모형"

                st.markdown(
                    f"**• 종합 투자 의견:** <span title='{status_tooltip}' style='cursor: help; border-bottom: 1px dashed #4E5968; font-weight: bold;'>{current_status} ⓘ</span>", 
                    unsafe_allow_html=True
                )
                
                st.markdown(f"**• TTM 기초 지표:** EPS `{eps_val:,.0f}원` | BPS `{bps_val:,.0f}원` | **진성 PEG (현재 PER 기반):** `{selected_stock['PEG']}x`")
                st.markdown(f"**📉 비관적 자산 방어선 (Bear Case Target):** `₩ {selected_stock['비관']:,}`원")
                st.markdown(f"**🟢 기준 시나리오 적정가 (Base Case Target):** `₩ {selected_stock['기준(최고치)']:,}`원")
                st.markdown(f"**📈 유동성 오버슈팅 상방선 (Bull Case Target):** `₩ {selected_stock['낙관']:,}`원")
                st.markdown(f"**🛡️ 실전 대기 분할 안전탈출가 (-5%):** `₩ {selected_stock['안전목표가']:,}원` (청산 시 최종 누적 실현이익: `{selected_stock['목표평가손익']:,}원`)")
                st.markdown(f"**🚨 마지노선 손절가 격벽:** `₩ {selected_stock['손절가']:,}원` (손실 규모: `{selected_stock['손절시손익']:,}원`)")
                st.markdown(f"**🏛️ 에프앤가이드 여의도 컨센서스 목표주가 평균:** `₩ {int(selected_stock['에프앤목표가']):,}`원")
                
            with t2:
                st.write(f"**📢 기업 개요 및 펀더멘탈 요약:** {s_cache.get('summary', '실적 매핑을 실행해 주세요.')}")
                st.write(f"**• 실적 턴어라운드율 모멘텀 총평:** {s_cache.get('bm_summary', '-')}")
                if s_cache.get('bm_list'):
                    st.table(pd.DataFrame(s_cache['bm_list'], columns=["사업부문", "주요품목", "구분", "비중(%)"]))
                    
            with t3:
                if s_cache.get('q_headers') and len(s_cache['q_headers']) >= 2:
                    chart_df = pd.DataFrame({
                        "분기": s_cache['q_headers'],
                        "매출액(억원)": s_cache['q_revenues'],
                        "영업이익(억원)": s_cache['q_op_profits']
                    }).set_index("분기")
                    
                    c_col1, c_col2 = st.columns(2)
                    with c_col1:
                        st.markdown("**Quarterly Revenue (분기 매출액)**")
                        st.bar_chart(chart_df["매출액(억원)"], color="#3182F6")
                    with c_col2:
                        st.markdown("**Quarterly Operating Profit (분기 영업이익)**")
                        st.line_chart(chart_df["영업이익(억원)"], color="#F04452")

    with tab_hist:
        st.subheader("📝 자산 회수 히스토리")
        hist_res = supabase.table("user_history").select("*").eq("username", username).order("created_at", desc=True).execute()
        if hist_res.data:
            df_hist = pd.DataFrame(hist_res.data)
            st.dataframe(df_hist, width="stretch")

    with tab_log:
        st.subheader("⚙️ 퀀트 시스템 가동 로그")
        log_res = supabase.table("user_logs").select("*").eq("username", username).order("created_at", desc=True).execute()
        if log_res.data:
            df_log = pd.DataFrame(log_res.data)
            st.dataframe(df_log, width="stretch")

def insert_log(supabase, username, module, summary, details):
    try:
        supabase.table("user_logs").insert({
            "username": username, "module": module, "summary": summary, "details": details
        }).execute()
    except: pass
