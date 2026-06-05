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

# --- 팩터 마스터 프리미엄 사전 설정 ---
CORE_CONVICTION_ASSETS = {"삼화콘덴서": 200000, "광전자": 20000}
GLOBAL_MEGATRENDS = {
    "HBM": 3, "CXL": 3, "NPU": 3, "유리기판": 3, "MLCC": 3, "AI": 2, "로봇": 2
}

_active_threads = {}

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

def fetch_naver_fundamentals(raw_code):
    url = f"https://finance.naver.com/item/main.naver?code={raw_code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.content, 'html.parser')
        
        summary_div = soup.select_one('.summary_info')
        company_summary = summary_div.text.replace('\n', ' ').strip() if summary_div else ""
        
        table = soup.select_one('div.cop_analysis table')
        if not table: return None
        rows = table.select_one('tbody').select('tr')
        thead = table.select_one('thead')
        
        def parse_num(txt):
            try: return float(txt.replace(',','').strip())
            except: return 0.0
            
        q_headers = [th.text.strip() for th in thead.select('tr')[1].select('th')[5:10]]
        q_revenues = [parse_num(td.text) for td in rows[0].select('td')[5:10]]
        q_op_profits = [parse_num(td.text) for td in rows[1].select('td')[5:10]]
        
        valid_indices = [i for i, rev in enumerate(q_revenues) if rev != 0.0]
        if valid_indices:
            q_headers = [q_headers[i] for i in valid_indices]
            q_revenues = [q_revenues[i] for i in valid_indices]
            q_op_profits = [q_op_profits[i] for i in valid_indices]
        
        return {"q_headers": q_headers, "q_revenues": q_revenues, "q_op_profits": q_op_profits, "summary": company_summary}
    except Exception as e: 
        print(f"네이버 펀더멘탈 파싱 에러: {e}")
        return None

def get_auto_momentum(stock_name, client_id, client_secret):
    if not client_id or not client_secret:
        return 3, 0, "인증키 누락", []
    exact_query = f'"{stock_name}"'
    url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(exact_query)}&display=10&sort=date"
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code != 200: return 3, 0, "인증 대기", []
        items = res.json().get('items', [])
        if not items: return 3, 0, "뉴스 없음", []
        
        news_list, pos_count, neg_count = [], 0, 0
        for item in items:
            headline = html.unescape(re.compile('<.*?>').sub('', item['title'])).strip()
            news_list.append({"title": headline, "link": item.get('originallink', item['link'])})
            combined_text = headline.upper()
            for pw in ['수주', '흑자', '돌파', 'AI', '최대', '공급', '계약', '성장']:
                if pw in combined_text: pos_count += 1
            for nw in ['하락', '적자', '취소', '우려', '부진', '위기', '손실']:
                if nw in combined_text: neg_count += 1
                
        net_sentiment = pos_count - neg_count
        score = 5 if net_sentiment >= 3 else (4 if net_sentiment > 0 else (3 if net_sentiment == 0 else 2))
        return score, net_sentiment, news_list[0]['title'][:25] + "...", news_list
    except: return 3, 0, "네트워크 오류", []

def calculate_bm_score(fund_data, core_product, stock_name):
    score = 0
    report = ""
    if fund_data:
        q_revs = fund_data.get('q_revenues', [])
        q_ops = fund_data.get('q_op_profits', [])
        if len(q_revs) >= 2:
            last_rev, prev_rev = q_revs[-1], q_revs[-2]
            last_op, prev_op = q_ops[-1], q_ops[-2]
            qoq = ((last_rev - prev_rev) / abs(prev_rev)) * 100 if prev_rev != 0 else 0
            margin = (last_op / last_rev) * 100 if last_rev != 0 else 0
            if qoq >= 10: score += 2
            if margin >= 10: score += 2
            if prev_op < 0 and last_op > 0: score += 3; report += "🔥 [분기 흑자전환 포착] "
            report += f"최근 매출 {int(last_rev):,}억 (QoQ {qoq:+.1f}%) / 영업이익 {int(last_op):,}억 (OPM {margin:.1f}%)"
            
    if stock_name in CORE_CONVICTION_ASSETS:
        return 5, f"💎 [VIP 코어 승부주] 목표가 {CORE_CONVICTION_ASSETS[stock_name]:,}원 고정\n" + report
    return score, report

def calculate_intrinsic_target(row, cache):
    name = row['name']
    if name in CORE_CONVICTION_ASSETS: return CORE_CONVICTION_ASSETS[name]
    base_price = cache.get('current_price', row['buy_price'])
    net_sent = cache.get('net_sentiment', 0)
    bm_score = cache.get('bm_score', 0)
    fundamental_factor = 1.0
    if cache.get('q_op_profits') and len(cache['q_op_profits']) >= 2:
        last_op = cache['q_op_profits'][-1]
        prev_op = cache['q_op_profits'][-2]
        if last_op > prev_op and prev_op > 0: fundamental_factor += min(0.20, (last_op - prev_op) / prev_op * 0.1)
        elif last_op < prev_op: fundamental_factor -= min(0.15, abs(last_op - prev_op) / prev_op * 0.1)
    total_multiplier = max(0.85, min(fundamental_factor + (net_sent * 0.005) + (bm_score * 0.015), 1.40))
    return int(base_price * total_multiplier)

def auto_sync_job(supabase, username, naver_id, naver_secret):
    last_sync_minute = None
    while True:
        now_kst = datetime.utcnow() + timedelta(hours=9)
        if 8 <= now_kst.hour <= 18 and now_kst.minute % 10 == 0:
            if now_kst.hour == 18 and now_kst.minute > 0:
                time.sleep(30)
                continue
            current_min_stamp = f"{now_kst.hour}:{now_kst.minute}"
            if last_sync_minute != current_min_stamp:
                last_sync_minute = current_min_stamp
                try:
                    db_res = supabase.table("user_portfolio").select("*").eq("username", username).execute()
                    portfolio_data = db_res.data
                    if portfolio_data:
                        for row in portfolio_data:
                            cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                            df_p = fdr.DataReader(row['ticker'], start=(now_kst - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
                            if not df_p.empty:
                                cache['current_price'] = int(df_p['Close'].iloc[-1])
                                cache['year_high'] = int(df_p['High'].max())
                                prev_close = float(df_p['Close'].iloc[-2])
                                cache['pct_change'] = round(((cache['current_price'] - prev_close) / prev_close) * 100, 2)
                            score, net_sent, _, n_list = get_auto_momentum(row['name'], naver_id, naver_secret)
                            cache['score'] = score
                            cache['net_sentiment'] = net_sent
                            cache['news_list'] = n_list
                            if 'q_revenues' not in cache or 'bm_list' not in cache:
                                fund = fetch_naver_fundamentals(row['ticker'])
                                bm_list = fetch_dynamic_company_bm(row['ticker'])
                                core_prod = bm_list[0][1] if bm_list else "기반 사업"
                                bm_score, bm_summary = calculate_bm_score(fund, core_prod, row['name'])
                                cache['bm_list'] = bm_list
                                cache['bm_score'] = bm_score
                                cache['bm_summary'] = bm_summary
                                if fund:
                                    cache['q_headers'] = fund['q_headers']
                                    cache['q_revenues'] = fund['q_revenues']
                                    cache['q_op_profits'] = fund['q_op_profits']
                                    cache['summary'] = fund['summary']
                            cache['target_2026'] = calculate_intrinsic_target(row, cache)
                            supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                except Exception as e: pass
        time.sleep(30)


# --- [메인 진입 페이지 함수] ---
def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    # 👍 [개선안 1] 금융 전용 폰트 'Pretendard' 주입 및 모바일 MTS 가시성 CSS 패치
    st.markdown("""
    <style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
    
    /* 전체 폰트 현대금융 스타일 교체 */
    html, body, [data-testid="stAppViewContainer"], .stWidgetFormValue, input, select, button {
        font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif !important;
    }
    
    /* 타이틀 및 가시성 극대화 */
    h1 { font-size: 30px !important; font-weight: 800 !important; color: #191F28 !important; letter-spacing: -0.5px; }
    h2 { font-size: 22px !important; font-weight: 700 !important; color: #333D4B !important; }
    
    /* 데이터프레임 내부 금융 지표 가독성 뻥튀기 */
    div[data-testid="stDataFrame"] table {
        font-size: 16px !important;
    }
    div[data-testid="stDataFrame"] td {
        padding: 12px 10px !important;
    }
    
    /* 탭 대시보드 텍스트 강조 */
    button[data-testid="stMarkdownContainer"] p {
        font-size: 16px !important;
        font-weight: 700 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("📈 스마트 프랍 퀀트 포트폴리오 엔진 (MTS Edition)")
    
    if username not in _active_threads:
        t = threading.Thread(target=auto_sync_job, args=(supabase, username, naver_id, naver_secret), daemon=True)
        t.start()
        _active_threads[username] = t
        
    st.sidebar.info("🤖 **MTS 고가시성 오토 가동 중**")

    @st.cache_data(ttl=86400)  
    def load_krx_mapping():
        try:
            df = fdr.StockListing('KRX')
            return {row['Name']: row['Code'] for _, row in df.iterrows()}
        except Exception:
            try:
                df = fdr.StockListing('KRX-DESC')
                return {row['Name']: row['Symbol'] for _, row in df.iterrows()}
            except Exception:
                return {"삼성전자": "005930", "SK하이닉스": "000660"} 
                
    krx_map = load_krx_mapping()

    tab_port, tab_hist, tab_log = st.tabs(["💼 나의 가치 자산", "📝 자산 회수 명세", "⚙️ 가동 로그"])

    with tab_port:
        st.write("⚡ **동기화 기어**")
        col_sync1, col_sync2, col_sync3 = st.columns(3)
        
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        if col_sync1.button("🔄 시세 동기화", width="stretch"):
            if not portfolio_data: st.stop()
            for row in portfolio_data:
                df_p = fdr.DataReader(row['ticker'], start=(datetime.utcnow() + timedelta(hours=9) - pd.DateOffset(
