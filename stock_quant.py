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
        res.encoding = 'utf-8' 
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
        res.encoding = 'utf-8'
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
            report += f"최근 매출 {int(last_rev):,}억 (QoQ {qoq:+.1f}%) / 영업이익 {int(last_op):,}억"
            
    if stock_name in CORE_CONVICTION_ASSETS:
        return 5, f"💎 [VIP 코어 승부주] 목표가 {CORE_CONVICTION_ASSETS[stock_name]:,}원 고정\n" + report
        
    return score, report

def insert_log(supabase, username, module, summary, details):
    try:
        supabase.table("user_logs").insert({
            "username": username, "module": module, "summary": summary, "details": details
        }).execute()
    except Exception as e:
        print(f"로그 기록 실패: {e}")

# 💡 [백그라운드 자동 동기화 봇 엔진]
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
                                
                            total_multiplier = 1 + (net_sent * 0.01) + (bm_score * 0.02)
                            cache['target_2026'] = int(cache.get('year_high', row['buy_price']) * total_multiplier)
                            if row['name'] in CORE_CONVICTION_ASSETS: cache['target_2026'] = CORE_CONVICTION_ASSETS[row['name']]
                            
                            supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                            
                        insert_log(supabase, username, "🤖 오토 스케줄러", f"{len(portfolio_data)}종목 자동 스캔", f"한국시간 {now_kst.strftime('%H:%M')} 정각 스케줄러 작동 완료 (시세/뉴스/실적)")
                except Exception as e:
                    pass
        time.sleep(30)


# --- [메인 진입 페이지 함수] ---
def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    st.title("📈 스마트 프랍 퀀트 포트폴리오 엔진")
    
    if username not in _active_threads:
        t = threading.Thread(target=auto_sync_job, args=(supabase, username, naver_id, naver_secret), daemon=True)
        t.start()
        _active_threads[username] = t
        
    st.sidebar.info("🤖 **오토 스캔 봇 가동 중**\n\n(KST 08:00 ~ 18:00, 10분 주기)")

    @st.cache_data
    def load_krx_mapping():
        df = fdr.StockListing('KRX')
        return {row['Name']: row['Code'] for _, row in df.iterrows()}
    krx_map = load_krx_mapping()

    tab_port, tab_hist, tab_log = st.tabs(["💼 보유 자산", "📝 판매 내역", "⚙️ 엔진 기록"])

    with tab_port:
        st.write("⚡ **수동 동기화 제어판** (버튼을 누르면 즉시 최신 데이터를 수집합니다)")
        col_sync1, col_sync2, col_sync3 = st.columns(3)
        
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        if col_sync1.button("🔄 전체 시세 갱신", width="stretch"):
            if not portfolio_data: st.warning("장부에 종목이 없습니다."); st.stop()
            with st.status("전체 종목 시세 트래킹 중...", expanded=True) as status:
                for row in portfolio_data:
                    st.write(f"[{row['name']}] 시세 수집 중...")
                    df_p = fdr.DataReader(row['ticker'], start=(datetime.utcnow() + timedelta(hours=9) - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
                    if not df_p.empty:
                        cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                        cache['current_price'] = int(df_p['Close'].iloc[-1])
                        cache['year_high'] = int(df_p['High'].max())
                        prev_close = float(df_p['Close'].iloc[-2])
                        cache['pct_change'] = round(((cache['current_price'] - prev_close) / prev_close) * 100, 2)
                        
                        total_multiplier = 1 + (cache.get('net_sentiment', 0) * 0.01) + (cache.get('bm_score', 0) * 0.02)
                        cache['target_2026'] = int(cache['year_high'] * total_multiplier)
                        if row['name'] in CORE_CONVICTION_ASSETS: cache['target_2026'] = CORE_CONVICTION_ASSETS[row['name']]
                        
                        supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                status.update(label="전체 시세 동기화 완료!", state="complete")
            insert_log(supabase, username, "수동 전역 동기화", f"{len(portfolio_data)}종목 시세 갱신", "사용자 명령으로 주가 및 변동률 업데이트 성공")
            st.rerun()

        if col_sync2.button("📰 전체 뉴스 스캔", width="stretch"):
            if not naver_id or not naver_secret: st.error("네이버 API 키가 없습니다."); st.stop()
            if not portfolio_data: st.warning("장부에 종목이 없습니다."); st.stop()
            with st.status("전체 종목 네이버 AI 뉴스 스캔 중...", expanded=True) as status:
                for row in portfolio_data:
                    st.write(f"[{row['name']}] 감성 분석 중...")
                    cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                    score, net_sent, _, n_list = get_auto_momentum(row['name'], naver_id, naver_secret)
                    cache['score'] = score
                    cache['net_sentiment'] = net_sent
                    cache['news_list'] = n_list
                    
                    total_multiplier = 1 + (net_sent * 0.01) + (cache.get('bm_score', 0) * 0.02)
                    cache['target_2026'] = int(cache.get('year_high', row['buy_price']) * total_multiplier)
                    if row['name'] in CORE_CONVICTION_ASSETS: cache['target_2026'] = CORE_CONVICTION_ASSETS[row['name']]
                    
                    supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                status.update(label="전체 뉴스 스캔 완료!", state="complete")
            insert_log(supabase, username, "수동 뉴스 스캔", f"{len(portfolio_data)}종목 모멘텀 평가", "사용자 명령으로 감성 점수 및 뉴스 리스트 업데이트 성공")
            st.rerun()

        if col_sync3.button("📊 전체 실적 매핑", width="stretch"):
            if not portfolio_data: st.warning("장부에 종목이 없습니다."); st.stop()
            with st.status("전체 종목 펀더멘탈 긁어오는 중...", expanded=True) as status:
                for row in portfolio_data:
                    st.write(f"[{row['name']}] 재무 명세 매핑 중...")
                    cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
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
                        
                    total_multiplier = 1 + (cache.get('net_sentiment', 0) * 0.01) + (bm_score * 0.02)
                    cache['target_2026'] = int(cache.get('year_high', row['buy_price']) * total_multiplier)
                    if row['name'] in CORE_CONVICTION_ASSETS: cache['target_2026'] = CORE_CONVICTION_ASSETS[row['name']]
                    
                    supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                status.update(label="전체 재무 명세 매핑 완료!", state="complete")
            insert_log(supabase, username, "수동 실적 매핑", f"{len(portfolio_data)}종목 펀더멘탈 분석", "사용자 명령으로 분기 실적 및 BM 비중 업데이트 성공")
            st.rerun()

        st.divider()

        with st.expander("➕ 포트폴리오 신규 자산 편입", expanded=False):
            col1, col2, col3 = st.columns(3)
            with col1: s_name = st.selectbox("종목 선택", list(krx_map.keys()))
            with col2: buy_p = st.number_input("매입 평단가(원)", min_value=1, value=10000)
            with col3: qty = st.number_input("보유 수량(주)", min_value=1, value=10)
            if st.button("장부 조율 및 매수 결제", type="primary"):
                ticker = krx_map[s_name]
                try:
                    supabase.table("user_portfolio").upsert({
                        "username": username, "ticker": ticker, "name": s_name, "buy_price": buy_p, "qty": qty, "analysis_cache": {}
                    }).execute()
                    insert_log(supabase, username, "신규 편입", f"[{s_name}] 매수", f"단가 {buy_p}원, 수량 {qty}주")
                    st.success(f"[{s_name}] 편입 완료!")
                    st.rerun()
                except Exception as e:
                    st.error("자산 편입 실패! SQL에서 user_portfolio 테이블의 RLS를 해제했는지 확인해주세요.")

        if not portfolio_data:
            st.info("현재 장부에 등록된 보유 주식이 없습니다.")
            return

        total_invest, total_value = 0, 0
        display_rows = []
        
        for row in portfolio_data:
            ticker = row['ticker']
            name = row['name']
            b_price = row['buy_price']
            s_qty = row['qty']
            
            cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
            curr_price = cache.get('current_price', b_price)
            day_pct = cache.get('pct_change', 0.0)
            target_price = cache.get('target_2026', b_price)
            
            net_sent = cache.get('net_sentiment', 0)
            bm_scr = cache.get('bm_score', 0)
            
            pnl_amt = (curr_price - b_price) * s_qty
            pnl_pct = ((curr_price - b_price) / b_price) * 100 if b_price > 0 else 0
            
            # 💡 [핵심 패치] 웹 한계를 돌파하는 직관적 상태(음영/색상 대체) 로직
            stop_line = int(b_price * 0.92)
            status_emoji = ""
            if name in CORE_CONVICTION_ASSETS:
                if pnl_pct < -30: status_emoji = "☢️ 코어 침범 (독성)"
                else: status_emoji = "💎 VIP 강홀딩"
            else:
                if bm_scr >= 2 and pnl_pct < 0: status_emoji = "🛒 저점 추매"
                elif bm_scr <= -2 and pnl_pct < -5: status_emoji = "☢️ 맹독성 손절"
                elif curr_price < stop_line: status_emoji = "⚠️ 청산 시그널"
                else:
                    if target_price > 0 and curr_price >= target_price * 0.9: status_emoji = "🔥 청산 임박"
                    else: status_emoji = "🔴 흑자(상승)" if pnl_pct >= 0 else "🔵 적자(하락)"
            
            total_invest += b_price * s_qty
            total_value += curr_price * s_qty
            
            display_rows.append({
                "상태": status_emoji, "종목명": name, "티커": ticker, 
                "현재가": int(curr_price), "전일비(%)": round(day_pct, 2),
                "평단가": int(b_price), "수량": int(s_qty), "평가손익": int(pnl_amt), "수익률(%)": round(pnl_pct, 2),
                "적정타깃가": int(target_price), "raw_data": row
            })

        total_pnl = total_value - total_invest
        total_pnl_pct = (total_pnl / total_invest) * 100 if total_invest > 0 else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("투자 원금", f"{total_invest:,} 원")
        c2.metric("평가 금액", f"{total_value:,} 원")
        c3.metric("총 평가 손익", f"{total_pnl:,} 원", f"{total_pnl_pct:+.2f}%")
        
        st.write("💡 **열 제목(예: 수익률, 종목명 등)을 클릭하시면 오름차순/내림차순으로 자동 정렬됩니다.**")
        df_disp = pd.DataFrame(display_rows).drop(columns=["raw_data", "티커"])
        
        selection_event = st.dataframe(
            df_disp, 
            width="stretch", 
            on_select="rerun", 
            selection_mode="single-row"
        )
        
        selected_indices = selection_event.get("selection", {}).get("rows", [])
        
        if selected_indices:
            selected_idx = selected_indices[0]
            selected_stock = display_rows[selected_idx]
            s_name = selected_stock["종목명"]
            s_ticker = selected_stock["티커"]
            raw_row = selected_stock["raw_data"]
            s_cache = raw_row.get("analysis_cache") if raw_row.get("analysis_cache") else {}
            
            st.markdown(f"### 🛠️ [{s_name}] 장부 관리 및 심층 리포트")
            
            col_indi1, col_indi2, col_indi3 = st.columns(3)
            with col_indi1:
                if st.button(f"[{s_name}] 시세 단독 갱신", width="stretch"):
                    df_p = fdr.DataReader(s_ticker, start=(datetime.utcnow() + timedelta(hours=9) - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
                    if not df_p.empty:
                        s_cache['current_price'] = int(df_p['Close'].iloc[-1])
                        s_cache['year_high'] = int(df_p['High'].max())
                        prev_close = float(df_p['Close'].iloc[-2])
                        s_cache['pct_change'] = round(((s_cache['current_price'] - prev_close) / prev_close) * 100, 2)
                        total_multiplier = 1 + (s_cache.get('net_sentiment', 0) * 0.01) + (s_cache.get('bm_score', 0) * 0.02)
                        s_cache['target_2026'] = int(s_cache['year_high'] * total_multiplier)
                        if s_name in CORE_CONVICTION_ASSETS: s_cache['target_2026'] = CORE_CONVICTION_ASSETS[s_name]
                        supabase.table("user_portfolio").update({"analysis_cache": s_cache}).eq("id", raw_row['id']).execute()
                        st.rerun()
            with col_indi2:
                if st.button(f"[{s_name}] 실적 단독 매핑", width="stretch"):
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
                    st.rerun()

            st.divider()

            col_btn1, col_btn2, col_btn3 = st.columns(3)
            with col_btn1:
                with st.popover("✏️ 장부 평단/수량 수정", width="stretch"):
                    new_p = st.number_input("수정할 평단가", value=int(raw_row['buy_price']))
                    new_q = st.number_input("수정할 보유수량", value=int(raw_row['qty']))
                    if st.button("수정 장부 인가", key="btn_edit_confirm"):
                        supabase.table("user_portfolio").update({"buy_price": new_p, "qty": new_q}).eq("id", raw_row['id']).execute()
                        insert_log(supabase, username, "장부 수정", f"[{s_name}] 수정", f"평단가 {new_p} / 수량 {new_q}")
                        st.success("수정 완료!")
                        st.rerun()
            with col_btn2:
                with st.popover("🛒 분할 추가매수", width="stretch"):
                    add_p = st.number_input("추가 매수가격", value=int(selected_stock["현재가"]))
                    add_q = st.number_input("추가 매수수량", value=10)
                    if st.button("추가매수 체결", key="btn_buy_confirm"):
                        current_total_cost = raw_row['buy_price'] * raw_row['qty']
                        new_total_cost = current_total_cost + (add_p * add_q)
                        new_qty = raw_row['qty'] + add_q
                        new_avg_price = int(new_total_cost / new_qty)
                        supabase.table("user_portfolio").update({"buy_price": new_avg_price, "qty": new_qty}).eq("id", raw_row['id']).execute()
                        insert_log(supabase, username, "추가 매수", f"[{s_name}] {add_q}주 추매", f"단가 {add_p}원")
                        st.success("추가 매수 장부 합성 성공!")
                        st.rerun()
            with col_btn3:
                with st.popover("❌ 자산 매도(청산)", width="stretch"):
                    st.write(f"현재 보유 수량: **{raw_row['qty']}주** (평단가: {raw_row['buy_price']:,}원)")
                    sell_p = st.number_input("매도 단가", value=int(selected_stock["현재가"]))
                    sell_q = st.number_input("매도 수량", min_value=1, max_value=raw_row['qty'], value=raw_row['qty'])
                    if st.button("🚨 매도 집행", key="btn_sell_confirm"):
                        profit_amt = (sell_p - raw_row['buy_price']) * sell_q
                        profit_pct = round(((sell_p - raw_row['buy_price']) / raw_row['buy_price']) * 100, 2)
                        try:
                            supabase.table("user_history").insert({
                                "username": username, "ticker": raw_row['ticker'], "name": s_name,
                                "buy_price": raw_row['buy_price'], "sell_price": sell_p, "qty": sell_q,
                                "profit_amt": profit_amt, "profit_pct": profit_pct
                            }).execute()
                        except Exception as e:
                            print(f"히스토리 기록 실패: {e}")
                        
                        if sell_q == raw_row['qty']:
                            supabase.table("user_portfolio").delete().eq("id", raw_row['id']).execute()
                        else:
                            supabase.table("user_portfolio").update({"qty": raw_row['qty'] - sell_q}).eq("id", raw_row['id']).execute()
                            
                        insert_log(supabase, username, "자산 매도", f"[{s_name}] {sell_q}주 매도", f"수익 {profit_amt:,}원 ({profit_pct:+.2f}%)")
                        st.success("청산 완료 및 내역 기록되었습니다.")
                        st.rerun()

            st.divider()
            
            t1, t2, t3 = st.tabs(["📉 가치평가 및 뉴스", "📰 전방 사업 명세", "📊 실적 트렌드 차트"])
            with t1:
                st.markdown(f"**• 현재 종합 모멘텀:** {selected_stock['상태']}")
                st.markdown(f"**• 앵커 밸류에이션 (1년 최고가):** `{s_cache.get('year_high', raw_row['buy_price']):,}`원")
                st.markdown(f"**• 뉴스 감성 모멘텀 가중:** `{s_cache.get('net_sentiment', 0):+}` 점")
                st.markdown(f"**• 산업 사이클 점수 가중:** `{s_cache.get('bm_score', 0):+}` 점")
                st.markdown(f"**🚀 최종 적정 타깃 목표가:** `{selected_stock['적정타깃가']:,}`원")
                st.write("**실시간 추적 뉴스**")
                for idx, news in enumerate(s_cache.get('news_list', []), 1):
                    st.markdown(f"[{idx}] [{news['title']}]({news['link']})")
                    
            with t2:
                st.write(f"**📢 기업 개요:** {s_cache.get('summary', '실적 매핑을 먼저 실행해 주세요.')}")
                st.write(f"**• 실적 사이클:** {s_cache.get('bm_summary', '-')}")
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
                    st.info("실적 차트 데이터가 부족합니다.")

    with tab_hist:
        st.subheader("📝 자산 매도(청산) 히스토리")
        hist_res = supabase.table("user_history").select("*").eq("username", username).order("created_at", desc=True).execute()
        if not hist_res.data:
            st.info("아직 자산 매도 내역이 없습니다.")
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
            st.dataframe(df_hist, width="stretch")

    with tab_log:
        st.subheader("⚙️ 시스템 엔진 처리 기록")
        log_res = supabase.table("user_logs").select("*").eq("username", username).order("created_at", desc=True).execute()
        if not log_res.data:
            st.info("시스템 처리 기록이 없습니다.")
        else:
            df_log = pd.DataFrame(log_res.data)
            df_log['created_at'] = pd.to_datetime(df_log['created_at']) + pd.Timedelta(hours=9)
            df_log['created_at'] = df_log['created_at'].dt.strftime('%Y-%m-%d %H:%M:%S')
            df_log = df_log[['created_at', 'module', 'summary', 'details']]
            df_log.columns = ['시간', '모듈', '요약', '상세내역']
            st.dataframe(df_log, width="stretch")
