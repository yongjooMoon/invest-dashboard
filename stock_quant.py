import streamlit as st
import requests
import re
import html
import json
import FinanceDataReader as fdr
import pandas as pd
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from datetime import datetime

# --- 팩터 마스터 프리미엄 사전 설정 ---
CORE_CONVICTION_ASSETS = {"삼화콘덴서": 200000, "광전자": 20000}
GLOBAL_MEGATRENDS = {
    "HBM": 3, "CXL": 3, "NPU": 3, "유리기판": 3, "MLCC": 3, "AI": 2, "로봇": 2
}

# --- 데이터 스크래핑 핵심 엔진 부활 ---
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
            report += f"최근 매출 {int(last_rev):,}억 (QoQ {qoq:+.1f}%) / 영업이익 {int(last_op):,}억"
            
    if stock_name in CORE_CONVICTION_ASSETS:
        return 5, f"💎 [VIP 코어 승부주] 목표가 {CORE_CONVICTION_ASSETS[stock_name]:,}원 고정\n" + report
        
    return score, report

# --- [메인 진입 페이지 함수] ---
def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    st.title("📈 스마트 프랍 퀀트 포트폴리오 엔진")
    
    @st.cache_data
    def load_krx_mapping():
        df = fdr.StockListing('KRX')
        return {row['Name']: row['Code'] for _, row in df.iterrows()}
    krx_map = load_krx_mapping()
    
    # 1. 신규 자산 편입 시스템
    with st.expander("➕ 포트폴리오 신규 자산 편입", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1: s_name = st.selectbox("종목 선택", list(krx_map.keys()))
        with col2: buy_p = st.number_input("매입 평단가(원)", min_value=1, value=10000)
        with col3: qty = st.number_input("보유 수량(주)", min_value=1, value=10)
        if st.button("장부 조율 및 매수 결제", type="primary"):
            ticker = krx_map[s_name]
            supabase.table("user_portfolio").upsert({
                "username": username, "ticker": ticker, "name": s_name, "buy_price": buy_p, "qty": qty, "analysis_cache": {}
            }).execute()
            st.success(f"[{s_name}] 편입 완료!")
            st.rerun()

    # 2. DB 장부 데이터 불러오기 (캐시 데이터 기반 기동 -> 렉 원천 차단)
    db_res = supabase.table("user_portfolio").select("*").eq("username", username).execute()
    portfolio_data = db_res.data
    
    if not portfolio_data:
        st.info("현재 장부에 등록된 보유 주식이 없습니다.")
        return
        
    total_invest = 0
    total_value = 0
    display_rows = []
    
    for row in portfolio_data:
        ticker = row['ticker']
        name = row['name']
        b_price = row['buy_price']
        s_qty = row['qty']
        
        # 캐시 데이터 추출 및 디폴트 빌드
        cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
        curr_price = cache.get('current_price', b_price)
        day_pct = cache.get('pct_change', 0.0)
        target_price = cache.get('target_2026', b_price)
        
        pnl_amt = (curr_price - b_price) * s_qty
        pnl_pct = ((curr_price - b_price) / b_price) * 100 if b_price > 0 else 0
        
        total_invest += b_price * s_qty
        total_value += curr_price * s_qty
        
        display_rows.append({
            "종목명": name, "티커": ticker, "현재가": curr_price, "전일비(%)": day_pct,
            "평단가": b_price, "수량": s_qty, "평가손익": pnl_amt, "수익률(%)": round(pnl_pct, 2),
            "적정타깃가": target_price, "raw_data": row
        })

    # 전광판 출력
    total_pnl = total_value - total_invest
    total_pnl_pct = (total_pnl / total_invest) * 100 if total_invest > 0 else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("투자 원금", f"{total_invest:,} 원")
    c2.metric("평가 금액", f"{total_value:,} 원")
    c3.metric("총 평가 손익", f"{total_pnl:,} 원", f"{total_pnl_pct:+.2f}%")
    
    # 💡 [핵심 패치] 드롭다운 삭제 및 로우(Row) 클릭 선택 제어부 가동
    st.write("💡 아래의 표에서 **원하는 종목 줄(Row)을 클릭**하시면 수정/청산 및 심층 리포트가 열립니다.")
    df_disp = pd.DataFrame(display_rows).drop(columns=["raw_data"])
    
    # 스트림릿 내장 로우 클릭 이벤트 캐치 프로토콜 (Streamlit 1.35.0+ 지원)
    selection_event = st.dataframe(
        df_disp, 
        use_container_width=True, 
        on_select="rerun", 
        selection_mode="single_row"
    )
    
    selected_indices = selection_event.get("selection", {}).get("rows", [])
    
    if selected_indices:
        selected_idx = selected_indices[0]
        selected_stock = display_rows[selected_idx]
        s_name = selected_stock["종목명"]
        s_ticker = selected_stock["티커"]
        raw_row = selected_stock["raw_data"]
        s_cache = raw_row.get("analysis_cache") if raw_row.get("analysis_cache") else {}
        
        st.markdown(f"### 🛠️ [{s_name}] 장부 관리 및 실시간 동기화 데스크")
        
        # --- [A. 토스식 거래 트랜잭션 관리 단추 기동] ---
        col_btn1, col_btn2, col_btn3 = st.columns(3)
        with col_btn1:
            with st.popover("✏️ 장부 평단/수량 수정", use_container_width=True):
                new_p = st.number_input("수정할 평단가", value=int(raw_row['buy_price']))
                new_q = st.number_input("수정할 보유수량", value=int(raw_row['qty']))
                if st.button("수정 장부 인가", key="btn_edit_confirm"):
                    supabase.table("user_portfolio").update({"buy_price": new_p, "qty": new_q}).eq("id", raw_row['id']).execute()
                    st.success("수정 완료!")
                    st.rerun()
        with col_btn2:
            with st.popover("🛒 저점 분할 추가매수", use_container_width=True):
                add_p = st.number_input("추가 매수가격", value=selected_stock["현재가"])
                add_q = st.number_input("추가 매수수량", value=10)
                if st.button("추가매수 체결", key="btn_buy_confirm"):
                    current_total_cost = raw_row['buy_price'] * raw_row['qty']
                    new_total_cost = current_total_cost + (add_p * add_q)
                    new_qty = raw_row['qty'] + add_q
                    new_avg_price = int(new_total_cost / new_qty)
                    supabase.table("user_portfolio").update({"buy_price": new_avg_price, "qty": new_qty}).eq("id", raw_row['id']).execute()
                    st.success("추가 매수 장부 합성 성공!")
                    st.rerun()
        with col_btn3:
            with st.popover("❌ 자산 포지션 전체 청산(팔기)", use_container_width=True):
                st.warning(f"정말로 [{s_name}] 자산을 전량 청산 처리하시겠습니까?")
                if st.button("🚨 청산 최종 집행", key="btn_sell_confirm"):
                    supabase.table("user_portfolio").delete().eq("id", raw_row['id']).execute()
                    st.success("포지션 완전히 삭제됨.")
                    st.rerun()

        # --- [B. 동기화 스캔 스위치 단추 기동 (이전 Tkinter 기능 완벽 이식)] ---
        st.write("⚡ **실시간 데이터 동기화 단추** (누를 때만 신선한 데이터를 가져와 DB에 저장합니다)")
        s_col1, s_col2, s_col3 = st.columns(3)
        
        with s_col1:
            if st.button("🔄 실시간 시세 갱신", use_container_width=True, key="sync_p"):
                with st.spinner("시세 트래킹 중..."):
                    df_p = fdr.DataReader(s_ticker, start=(datetime.now() - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
                    if not df_p.empty:
                        s_cache['current_price'] = int(df_p['Close'].iloc[-1])
                        s_cache['year_high'] = int(df_p['High'].max())
                        prev_close = float(df_p['Close'].iloc[-2])
                        s_cache['pct_change'] = round(((s_cache['current_price'] - prev_close) / prev_close) * 100, 2)
                        # 목표가 수식 재연산 적용
                        total_multiplier = 1 + (s_cache.get('net_sentiment', 0) * 0.01) + (s_cache.get('bm_score', 0) * 0.02)
                        s_cache['target_2026'] = int(s_cache['year_high'] * total_multiplier)
                        if s_name in CORE_CONVICTION_ASSETS: s_cache['target_2026'] = CORE_CONVICTION_ASSETS[s_name]
                        supabase.table("user_portfolio").update({"analysis_cache": s_cache}).eq("id", raw_row['id']).execute()
                        st.success("시세 동기화 완료!")
                        st.rerun()
                        
        with s_col2:
            if st.button("📰 실시간 뉴스 및 감성 스캔", use_container_width=True, key="sync_n"):
                if not naver_id or not naver_secret: st.error("네이버 API 키가 없습니다."); st.stop()
                with st.spinner("네이버 AI 뉴스 센티먼트 분석 중..."):
                    score, net_sent, _, n_list = get_auto_momentum(s_name, naver_id, naver_secret)
                    s_cache['score'] = score
                    s_cache['net_sentiment'] = net_sent
                    s_cache['news_list'] = n_list
                    # 목표가 리밸런싱
                    total_multiplier = 1 + (net_sent * 0.01) + (s_cache.get('bm_score', 0) * 0.02)
                    s_cache['target_2026'] = int(s_cache.get('year_high', raw_row['buy_price']) * total_multiplier)
                    if s_name in CORE_CONVICTION_ASSETS: s_cache['target_2026'] = CORE_CONVICTION_ASSETS[s_name]
                    supabase.table("user_portfolio").update({"analysis_cache": s_cache}).eq("id", raw_row['id']).execute()
                    st.success("뉴스 스캔 가동 완료!")
                    st.rerun()
                    
        with s_col3:
            if st.button("📊 분기 실적 펀더멘탈 매핑", use_container_width=True, key="sync_b"):
                with st.spinner("FnGuide 및 네이버 기업 구조 명세서 긁어오는 중..."):
                    fund = fetch_naver_fundamentals(s_ticker)
                    bm_list = fetch_dynamic_company_bm(s_ticker)
                    core_prod = bm_list[0][1] if bm_list else "기반 사업"
                    bm_score, bm_summary = calculate_bm_score(fund, core_prod, s_name)
                    s_cache['bm_list'] = bm_list
                    s_cache['bm_score'] = bm_score
                    s_cache['bm_summary'] = bm_summary
                    if fund:
                        s_cache['q_headers'] = fund['q_headers']
                        s_cache['q_revenues'] = fund['q_revenues']
                        s_cache['q_op_profits'] = fund['q_op_profits']
                        s_cache['summary'] = fund['summary']
                    total_multiplier = 1 + (s_cache.get('net_sentiment', 0) * 0.01) + (bm_score * 0.02)
                    s_cache['target_2026'] = int(s_cache.get('year_high', raw_row['buy_price']) * total_multiplier)
                    if s_name in CORE_CONVICTION_ASSETS: s_cache['target_2026'] = CORE_CONVICTION_ASSETS[s_name]
                    supabase.table("user_portfolio").update({"analysis_cache": s_cache}).eq("id", raw_row['id']).execute()
                    st.success("재무 명세 매핑 성공!")
                    st.rerun()

        # --- [C. 개별 심층 리포트 탭 렌더링 구동] ---
        st.markdown(f"#### 🏢 [{s_name}] 프랍 데스크 심층 리포트")
        t1, t2, t3 = st.tabs(["📉 가치평가 및 뉴스 연산식", "전방 사업 명세서", "📊 분기별 실적 트렌드 차트"])
        
        with t1:
            st.markdown(f"**• 앵커 밸류에이션 (1년 최고가):** `{s_cache.get('year_high', raw_row['buy_price']):,}`원")
            st.markdown(f"**• 뉴스 감성 모멘텀 가중:** `{s_cache.get('net_sentiment', 0):+}` 점")
            st.markdown(f"**• 산업 사이클 점수 가중:** `{s_cache.get('bm_score', 0):+}` 점")
            st.markdown(f"**🚀 최종 적정 타깃 목표가:** `{selected_stock['적정타깃가']}`원")
            
            st.write("**📰 실시간 추적 뉴스 명세 링크**")
            for idx, news in enumerate(s_cache.get('news_list', []), 1):
                st.markdown(f"[{idx}] [{news['title']}]({news['link']})")
                
        with t2:
            st.write(f"**📢 기업 개요 및 코멘트:** {s_cache.get('summary', '재무 매핑을 먼저 실행해 주세요.')}")
            st.write(f"**• 실적 사이클 평가 요약:** {s_cache.get('bm_summary', '-')}")
            if s_cache.get('bm_list'):
                st.table(pd.DataFrame(s_cache['bm_list'], columns=["사업부문", "주요품목", "구분", "비중(%)"]))
                
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
                st.info("실적 차트 시각화 데이터가 부족합니다. [분기 실적 펀더멘탈 매핑] 단추를 눌러주세요.")
