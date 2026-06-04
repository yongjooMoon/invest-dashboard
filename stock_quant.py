import streamlit as st
import requests
import re
import html
import json
import FinanceDataReader as fdr
import pandas as pd
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from datetime import datetime  # 💡 [버그 픽스] 누락되었던 datetime 라이브러리 추가

# --- 팩터 마스터 사전 설정 ---
CORE_CONVICTION_ASSETS = {"삼화콘덴서": 200000, "광전자": 20000}
GLOBAL_MEGATRENDS = {
    "HBM": 3, "CXL": 3, "NPU": 3, "유리기판": 3, "MLCC": 3, "AI": 2, "로봇": 2
}

def fetch_dynamic_company_bm(raw_code):
    url = f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?gicode=A{raw_code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
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
        res.encoding = 'euc-kr'
        soup = BeautifulSoup(res.text, 'html.parser')
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
        
        return {"q_headers": q_headers, "q_revenues": q_revenues, "q_op_profits": q_op_profits, "summary": company_summary}
    except: return None

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
        clean_html = re.compile('<.*?>')
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
        summary = fund_data.get('summary', '')
        if len(q_revs) >= 2:
            last_rev, prev_rev = q_revs[-1], q_revs[-2]
            last_op, prev_op = q_ops[-1], q_ops[-2]
            qoq = ((last_rev - prev_rev) / abs(prev_rev)) * 100 if prev_rev != 0 else 0
            margin = (last_op / last_rev) * 100 if last_rev != 0 else 0
            if qoq >= 10: score += 2
            if margin >= 10: score += 2
            if prev_op < 0 and last_op > 0: score += 3; report += "🔥 [분기 흑자 턴어라운드 성공] "
            report += f"최근 매출 {int(last_rev):,}억 (QoQ {qoq:+.1f}%) / 영업이익 {int(last_op):,}억"
            
    if stock_name in CORE_CONVICTION_ASSETS:
        return 5, f"💎 [VIP 코어 승부주] 목표가 {CORE_CONVICTION_ASSETS[stock_name]:,}원 고정\n" + report
        
    return score, report

# --- [메인 주식 대시보드 스크립트] ---
def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    st.title("📈 스마트 프랍 퀀트 포트폴리오 엔진")
    if not naver_id or not naver_secret:
        st.info("⚠️ 상단 '내 API 키 자산 설정' 메뉴에서 네이버 뉴스 API 키쌍을 먼저 등록해 주세요.")
        return
        
    @st.cache_data
    def load_krx_mapping():
        df = fdr.StockListing('KRX')
        return {row['Name']: row['Code'] for _, row in df.iterrows()}
    
    krx_map = load_krx_mapping()
    
    # 1. 자산 편입 시스템
    with st.expander("➕ 포트폴리오 신규 자산 편입", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1: s_name = st.selectbox("종목 선택", list(krx_map.keys()))
        with col2: buy_p = st.number_input("매입 평단가(원)", min_value=1, value=10000)
        with col3: qty = st.number_input("보유 수량(주)", min_value=1, value=10)
        if st.button("장부 조율 및 매수 결제", type="primary"):
            ticker = krx_map[s_name]
            supabase.table("user_portfolio").upsert({
                "username": username, "ticker": ticker, "name": s_name, "buy_price": buy_p, "qty": qty
            }).execute()
            st.success(f"[{s_name}] 장부에 성공적으로 자산 편입 완료되었습니다.")
            st.rerun()

    # 2. 유저 계정 포트폴리오 로드
    db_res = supabase.table("user_portfolio").select("*").eq("username", username).execute()
    portfolio_data = db_res.data
    
    if not portfolio_data:
        st.info("현재 장부에 등록된 보유 주식이 없습니다. 위의 자산 편입 기능을 이용해 주세요.")
        return
        
    st.subheader("📊 실시간 변동성 및 자산 밸류에이션 현황")
    
    total_invest = 0
    total_value = 0
    display_rows = []
    
    for row in portfolio_data:
        ticker = row['ticker']
        name = row['name']
        b_price = row['buy_price']
        s_qty = row['qty']
        
        df_price = fdr.DataReader(ticker, start=(datetime.now() - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
        if not df_price.empty:
            curr_price = int(df_price['Close'].iloc[-1])
            y_high = int(df_price['High'].max())
            prev_c = float(df_price['Close'].iloc[-2])
            day_pct = ((curr_price - prev_c) / prev_c) * 100
        else:
            curr_price, y_high, day_pct = b_price, b_price, 0.0
            
        n_score, net_sent, n_title, n_list = get_auto_momentum(name, naver_id, naver_secret)
        fund = fetch_naver_fundamentals(ticker)
        bm_list = fetch_dynamic_company_bm(ticker)
        core_prod = bm_list[0][1] if bm_list else "기반 사업"
        bm_score, bm_summary = calculate_bm_score(fund, core_prod, name)
        
        total_premium = 1 + (net_sent * 0.01) + (bm_score * 0.02)
        target_price = int(y_high * total_premium)
        if name in CORE_CONVICTION_ASSETS:
            target_price = max(int(b_price * 1.5), CORE_CONVICTION_ASSETS[name])
            
        pnl_amt = (curr_price - b_price) * s_qty
        pnl_pct = ((curr_price - b_price) / b_price) * 100
        
        total_invest += b_price * s_qty
        total_value += curr_price * s_qty
        
        display_rows.append({
            "종목명": name, "현재가": f"{curr_price:,}원", "전일비": f"{day_pct:+.2f}%",
            "평단가": f"{b_price:,}원", "수량": f"{s_qty:,}주", "평가손익": f"{pnl_amt:,}원",
            "수익률": f"{pnl_pct:+.2f}%", "적정타깃가": f"{target_price:,}원", "데이터": {
                "fund": fund, "bm_list": bm_list, "summary": bm_summary, "news": n_list
            }
        })

    total_pnl = total_value - total_invest
    total_pnl_pct = (total_pnl / total_invest) * 100 if total_invest > 0 else 0
    
    c1, c2, c3 = st.columns(3)
    c1.metric("투자 원금", f"{total_invest:,} 원")
    c2.metric("평가 금액", f"{total_value:,} 원")
    c3.metric("총 평가 손익", f"{total_pnl:,} 원", f"{total_pnl_pct:+.2f}%")
    
    df_disp = pd.DataFrame(display_rows).drop(columns=["데이터"])
    st.dataframe(df_disp, use_container_width=True)
    
    st.subheader("🔍 프랍 데스크 종목별 심층 분석 명세서")
    selected_stock = st.selectbox("리포트를 로드할 종목을 고르세요", [r["종목명"] for r in display_rows])
    target_data = [r for r in display_rows if r["종목명"] == selected_stock][0]["데이터"]
    
    t1, t2 = st.tabs(["📊 실적 트렌드 차트", "📰 전방 사업 및 뉴스 명세"])
    with t1:
        if target_data["fund"] and len(target_data["fund"]["q_headers"]) >= 2:
            fig, ax1 = plt.subplots(figsize=(10, 4))
            ax1.bar(target_data["fund"]["q_headers"], target_data["fund"]["q_revenues"], color='#3182F6', alpha=0.8, width=0.3, label="매출액")
            ax2 = ax1.twinx()
            ax2.plot(target_data["fund"]["q_headers"], target_data["fund"]["q_op_profits"], color='#F04452', marker='o', linewidth=2, label="영업이익")
            st.pyplot(fig)
        else:
            st.info("해당 종목은 시각화 데이터 분기 헤더 정보가 부족합니다.")
            
    with t2:
        st.write(f"**📢 실적 요약 및 코멘트:** {target_data['summary']}")
        st.write("**🏢 매출 비중 구성 요소**")
        st.table(pd.DataFrame(target_data["bm_list"], columns=["사업부문", "주요품목", "구분", "비중(%)"]))
