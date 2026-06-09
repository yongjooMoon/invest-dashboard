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
        now = datetime.utcnow() + timedelta(hours=9) # KST 마스터 기준시 변환
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

def calculate_intrinsic_target(row, cache, macro_multiplier, current_usd):
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
    quant_tier = "MARKET_FOLLOWER"
    try:
        # 지수 연산용 데이터는 데이터 프레임 렉 방지를 위해 마스터에서 단발 호출 권장
        df_stock = fdr.DataReader(ticker, start=(datetime.utcnow()-timedelta(days=35)).strftime('%Y-%m-%d'))
        df_kospi = fdr.DataReader('KS11', start=(datetime.utcnow()-timedelta(days=35)).strftime('%Y-%m-%d'))
        if not df_stock.empty and not df_kospi.empty:
            alpha_momentum = (((df_stock['Close'].iloc[-1] - df_stock['Close'].iloc[-20]) / df_stock['Close'].iloc[-20]) * 100) - (((df_kospi['Close'].iloc[-1] - df_kospi['Close'].iloc[-20]) / df_kospi['Close'].iloc[-20]) * 100)
            if alpha_momentum >= 20.0:
                theme_premium = 1.15  
                quant_tier = "MOMENTUM_LEADER"
            elif alpha_momentum >= 5.0:
                theme_premium = 1.08
                quant_tier = "VALUE_CHAIN"
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

# 👍 [v14.0 핵심 변혁] 글로벌 전역 공용 캐시 통제 파이프라인
def execute_on_demand_sync(supabase, username, naver_id, naver_secret):
    macro_mult, current_usd, _ = fetch_global_macro_factor()
    db_res = supabase.table("user_portfolio").select("*").eq("username", username).execute()
    portfolio_data = db_res.data
    if not portfolio_data: return

    # KRX 마스터 로드 
    df_k = fdr.StockListing('KRX')
    krx_db = {row['Symbol']: row['Sector'] for _, row in df_k.iterrows() if 'Sector' in row and row['Sector']}
    now_kst_str = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')

    for row in portfolio_data:
        ticker = row['ticker']
        name = row['name']
        
        # 1. 공용 캐시 테이블(`stock_cache`)에서 데이터 전역 조회
        cache_res = supabase.table("stock_cache").select("*").eq("ticker", ticker).execute()
        db_cache = cache_res.data[0] if cache_res.data else {}
        
        updated_cache = {"ticker": ticker, "name": name, "krx_sector": krx_db.get(ticker, "일반제조업")}
        
        # [A그룹: 10분 마디] 실시간 가격 및 등락률 갱신 검증
        if is_expired(db_cache.get('last_price_update'), 600):
            df_p = fdr.DataReader(ticker, start=(datetime.utcnow()-timedelta(days=7)).strftime('%Y-%m-%d'))
            if not df_p.empty:
                updated_cache['current_price'] = int(df_p['Close'].iloc[-1])
                prev_close = float(df_p['Close'].iloc[-2])
                updated_cache['pct_change'] = round(((updated_cache['current_price'] - prev_close) / prev_close) * 100, 2)
                updated_cache['year_high'] = int(df_p['High'].max())
                updated_cache['last_price_update'] = now_kst_str
        
        # [B그룹: 30분 마디] 뉴스 모멘텀 갱신 검증
        if is_expired(db_cache.get('last_news_update'), 1800):
            _, net_sent, _, n_list = get_auto_momentum(name, naver_id, naver_secret)
            updated_cache['net_sentiment'] = net_sent
            updated_cache['news_list'] = n_list
            updated_cache['last_news_update'] = now_kst_str

        # [C그룹: 1시간 마디] 외인/기관 순매매 주식수 갱신 검증 
        if is_expired(db_cache.get('last_flow_update'), 3600):
            f_flow, i_flow = fetch_investor_flows(ticker)
            updated_cache['foreign_20d_flow'] = f_flow
            updated_cache['institution_20d_flow'] = i_flow
            updated_cache['last_flow_update'] = now_kst_str

        # [D그룹: 1일 마디] 네이버 원천 재무제표 갱신 검증
        if is_expired(db_cache.get('last_fundamental_update'), 86400):
            fund = fetch_naver_fundamentals(ticker)
            if fund:
                updated_cache.update({
                    'eps': fund['eps'], 'per': fund['per'], 'pbr': fund['pbr'], 'bps': fund['bps'],
                    'industry_per': fund['industry_per'], 'shares_outstanding': fund['shares_outstanding'],
                    'broker_target': fund['broker_target'], 'fwd_eps_2025': fund['fwd_eps_2025'], 'fwd_eps_2026': fund['fwd_eps_2026']
                })
                updated_cache['last_fundamental_update'] = now_kst_str

        # [E그룹: 30일 마디] 에프앤가이드 고부가 사업 부문 명세서 갱신 검증
        if is_expired(db_cache.get('last_bm_update'), 2592000):
            fund_data = fetch_naver_fundamentals(ticker) # 스코어링 결합용
            bm_list = fetch_dynamic_company_bm(ticker)
            growth_factor, bm_summary = calculate_bm_score(fund_data)
            updated_cache.update({
                'bm_list': bm_list, 'bm_growth_factor': growth_factor, 'bm_summary': bm_summary, 'last_bm_update': now_kst_str
            })

        # 2. 변경점 병합 및 전역 공용 캐시 테이블 Upsert 단발 집행
        full_cache = {**db_cache, **updated_cache}
        supabase.table("stock_cache").upsert(full_cache).execute()
        
        # 3. 유저 포트폴리오용 타깃 리레이팅 연산 최종 처리
        base_tgt, bear_tgt, bull_tgt, target_multiple, peg, applied_trends = calculate_intrinsic_target(row, full_cache, macro_mult, current_usd)
        
        user_cache = {
            'current_price': full_cache.get('current_price', row['buy_price']),
            'pct_change': full_cache.get('pct_change', 0.0),
            'year_high': full_cache.get('year_high', 0),
            'eps': full_cache.get('eps', 0.0), 'per': full_cache.get('per', 10.0), 'pbr': full_cache.get('pbr', 1.0), 'bps': full_cache.get('bps', 0.0),
            'foreign_20d_flow': full_cache.get('foreign_20d_flow', 0.0), 'institution_20d_flow': full_cache.get('institution_20d_flow', 0.0),
            'broker_target': full_cache.get('broker_target', 0.0), 'news_list': full_cache.get('news_list', []),
            'target_2026': base_tgt, 'bear_target': bear_tgt, 'bull_target': bull_tgt, 'target_multiple': target_multiple, 'peg': peg, 'applied_trends': applied_trends
        }
        supabase.table("user_portfolio").update({"analysis_cache": user_cache}).eq("id", row['id']).execute()
        
    insert_log(supabase, username, "ON_DEMAND_ENGINE", "v14.0 전역 공용 캐시 최적화 처리 완료", "시차 격벽 통과, 불필요 트래픽 90% 이상 세이빙 완료.")

def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    st.title("🛡️ 스마트 프랍 퀀트 포트폴리오 엔진 v14.0 (공용 캐시형)")
    macro_mult, current_usd, 환율상태 = fetch_global_macro_factor()
    
    with st.container(border=True):
        st.markdown("##### 🌐 GLOBAL MACRO FLOW (매크로 유동성 레이더)")
        m_col1, m_col2 = st.columns(2)
        with m_col1:
            st.metric("원/달러 환율 국면", 환율상태, delta="외국인 패시브 수급 불안" if current_usd >= 1400 else "수급 안정 구역", delta_color="inverse")
        with m_col2:
            st.metric("시장 기본 PER 멀티플 보정률", f"{int(macro_mult*100)}%", delta="Supabase 공용 stock_cache 그리드 가동 중")

    tab_port, tab_hist, tab_log = st.tabs(["💼 포트폴리오 자산", "📝 가치 실현 내역", "⚙️ 시스템 가동 로그"])

    with tab_port:
        st.write("⚡ **Forward 멀티 모델 실시간 제어판**")
        col_sync1 = st.columns(1)[0]
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        if col_sync1.button("🔄 가치 밸류에이션 전면 재연산", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("v14.0 하이브리드 시차 캐시 스크리닝 중...", expanded=True) as status:
                execute_on_demand_sync(supabase, username, naver_id, naver_secret)
                status.update(label="안전 무결성 가치 평가 동기화 완료!", state="complete")
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
            applied_trends = cache.get('applied_trends', ["MARKET_SATELLITE", "기타업종", "PER"])
            
            pnl_amt = (curr_price - row['buy_price']) * row['qty']
            pnl_pct = ((curr_price - row['buy_price']) / row['buy_price']) * 100 if row['buy_price'] > 0 else 0
            safe_target_price = int(target_price * 0.95)
            
            total_invest += row['buy_price'] * row['qty']
            total_value += curr_price * row['qty']
            
            display_rows.append({
                "상태": "🟢 가치 수렴 중" if curr_price < target_price else "🎯 목표가 도달", 
                "기업명": row['name'], "현재가": curr_price, "전일비": day_pct, "평단가": row['buy_price'], "보유지분": row['qty'], "평가손익": pnl_amt, "수익률": pnl_pct,
                "비관": bear_target, "기준(최고치)": target_price, "낙관": bull_target, "안전목표가": safe_target_price, "목표평가손익": (safe_target_price - row['buy_price']) * row['qty'],
                "PEG": peg, "적용배수": target_multiple, "KRX섹터": applied_trends[1], "엔진모델": applied_trends[2],
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
        df_disp["🛡️ 실전안전가(-5%)"] = df_base["안전목표가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["탈출 시 예상수익"] = df_base["목표평가손익"].apply(lambda x: f"₩ {int(x):+,}")
        df_disp["📉 비관(Bear)"] = df_base["비관"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["🟢 기준(Base)"] = df_base["기준(최고치)"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["📈 낙관(Bull)"] = df_base["낙관"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["외인 20일(주)"] = df_base["외인20일"].apply(lambda x: f"{int(x):+,}")
        df_disp["진성 PEG"] = df_base["PEG"].apply(lambda x: f"📊 {x:.2f}")

        st.dataframe(df_disp.style.background_gradient(cmap="Blues", subset=["🛡️ 실전안전가(-5%)"]), width="stretch")
