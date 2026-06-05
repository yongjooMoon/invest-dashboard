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

# 👍 [신규 핵심] 글로벌 매크로 멀티플 레이더 (환율 실시간 MA 분석 + 대형 IPO 유동성 검증)
def fetch_global_macro_factor(client_id, client_secret):
    macro_multiplier = 1.0
    macro_report = ""
    
    # 1. 원/달러 환율 실시간 추적 및 20일 이평선 격차 분석
    try:
        df_usd = fdr.DataReader('USD/KRW', start=(datetime.utcnow() - timedelta(days=45)).strftime('%Y-%m-%d'))
        if not df_usd.empty:
            current_usd = float(df_usd['Close'].iloc[-1])
            usd_ma20 = float(df_usd['Close'].rolling(20).mean().iloc[-1]) if len(df_usd) >= 20 else current_usd
            
            if current_usd >= 1500:
                macro_multiplier -= 0.08  # 고환율 임계점 돌파 시 자본 방어용 고강도 디스카운트
                macro_report += f"🚨 [고환율 임계점 돌파: {current_usd:.1f}원] 외국인 기계적 환차손 매도 공세 구역 (-8%). "
            elif current_usd > usd_ma20:
                macro_multiplier -= 0.03  
                macro_report += f"⚠️ [환율 단기 우상향: {current_usd:.1f}원] 시장 변동성 축적 및 대형주 수급 경계 (-3%). "
            else:
                macro_multiplier += 0.02  
                macro_report += f"🍏 [환율 하방 안정세: {current_usd:.1f}원] 외국인 자본 유입에 우호적 환경 (+2%). "
    except:
        macro_report += "⚠️ [환율 센서 통신 지연] "

    # 2. 대형 IPO 유동성 블랙홀 현상 진단
    if client_id and client_secret:
        exact_query = '"대형 IPO" "우려" "유동성"'
        url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(exact_query)}&display=5&sort=date"
        headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
        try:
            res = requests.get(url, headers=headers, timeout=3)
            if res.status_code == 200:
                items = res.json().get('items', [])
                if len(items) >= 2:
                    macro_multiplier -= 0.05  # 자금 흡수 리스크 반영
                    macro_report += f"💥 [대형 IPO 유동성 흡수 감지] 예탁금 고갈 및 기존 주식 매도 리스크 유발 (-5%). "
                else:
                    macro_report += "✨ [시장 유동성 건전] 대형 상장 계획에 따른 자금 교란 징후 없음. "
        except:
            macro_report += "⚠️ [IPO 리스크 센서 통신 지연] "
            
    return round(macro_multiplier, 2), macro_report

# 👍 [수정] 매크로 팩터가 수리적으로 결합된 내재가치 평가 함수
def calculate_intrinsic_target(row, cache, macro_multiplier=1.0):
    name = row['name']
    if name in CORE_CONVICTION_ASSETS:
        return CORE_CONVICTION_ASSETS[name]
        
    base_price = cache.get('current_price', row['buy_price'])
    net_sent = cache.get('net_sentiment', 0)
    bm_score = cache.get('bm_score', 0)
    
    # 1. 기업 실적 기반 팩터
    fundamental_factor = 1.0
    if cache.get('q_op_profits') and len(cache['q_op_profits']) >= 2:
        last_op = cache['q_op_profits'][-1]
        prev_op = cache['q_op_profits'][-2]
        if last_op > prev_op and prev_op > 0:
            fundamental_factor += min(0.20, (last_op - prev_op) / prev_op * 0.1)
        elif last_op < prev_op:
            fundamental_factor -= min(0.15, abs(last_op - prev_op) / prev_op * 0.1)
            
    sentiment_factor = (net_sent * 0.005)
    cycle_factor = (bm_score * 0.015)
    
    # 2. 거시경제 유동성 중력(환율+IPO) 계수 최종 융합
    total_multiplier = (fundamental_factor + sentiment_factor + cycle_factor) * macro_multiplier
    total_multiplier = max(0.75, min(total_multiplier, 1.35))
    
    return int(base_price * total_multiplier)

def insert_log(supabase, username, module, summary, details):
    try:
        supabase.table("user_logs").insert({
            "username": username, "module": module, "summary": summary, "details": details
        }).execute()
    except Exception as e:
        print(f"로그 기록 실패: {e}")

# 👍 [수정] 백그라운드 오토 스케줄러 내 매크로 변수 실시간 갱신 및 DB 주입
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
                    # 매 종목 연산 전에 매크로 멀티플 선제 동기화
                    m_mult, m_rep = fetch_global_macro_factor(naver_id, naver_secret)
                    
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
                            
                            # 매크로 스냅샷 저장 및 가치평가 반영
                            cache['macro_multiplier_snapshot'] = m_mult
                            cache['macro_report_snapshot'] = m_rep
                            cache['target_2026'] = calculate_intrinsic_target(row, cache, m_mult)
                            
                            supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                            
                        insert_log(supabase, username, "🤖 오토 스케줄러", "매크로+시세 전역 동기화", f"멀티플 연산 완료: {m_mult}")
                except Exception as e:
                    pass
        time.sleep(30)


# --- [메인 진입 페이지 함수] ---
def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    st.title("📈 스마트 프랍 퀀트 포트폴리오 엔진 (Premium v3)")
    
    if username not in _active_threads:
        t = threading.Thread(target=auto_sync_job, args=(supabase, username, naver_id, naver_secret), daemon=True)
        t.start()
        _active_threads[username] = t
        
    st.sidebar.info("🤖 **매크로 감형 오토봇 가동 중**\n\n(환율/IPO 리스크 실시간 감지 필터 탑재)")

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
                st.error("현재 한국거래소(KRX) 서버가 혼잡하여 종목 리스트를 불러올 수 없습니다.")
                return {"삼성전자": "005930", "SK하이닉스": "000660"} 
                
    krx_map = load_krx_mapping()

    # 👍 [색상 버그 완벽 패치] UI 로드 시점에 실시간 매크로 지표를 즉시 계산하여 상단 레이아웃 분기 처리
    macro_mult, macro_rep = fetch_global_macro_factor(naver_id, naver_secret)

    tab_port, tab_hist, tab_log = st.tabs(["💼 보유 우량 자산", "📝 가치 실현 내역", "⚙️ 가치투자 엔진 기록"])

    with tab_port:
        # 💡 [핵심 패치 완료] 매크로 가중치 강도에 따라 경고창 색상이 기계적으로 전면 전환됩니다.
        if macro_mult <= 0.90:
            st.error(f"⚠️ **글로벌 거시경제 위험 경보 경계령 가동 (시장 멀티플 디스카운트 강행)** \n`현 판세`: {macro_rep}")
        elif macro_mult < 1.00:
            st.warning(f"🔍 **글로벌 거시경제 변동성 우려 추적 중 (보수적 분할 매수 권장)** \n`현 판세`: {macro_rep}")
        else:
            st.success(f"🍏 **글로벌 거시경제 유동성 청신호 (공격적 가치투자 친화 구역)** \n`현 판세`: {macro_rep}")

        st.write("⚡ **수동 제어판** (매크로 충격을 즉시 자산 가치에 반영시키려면 버튼을 누르십시오)")
        col_sync1, col_sync2, col_sync3 = st.columns(3)
        
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        if col_sync1.button("🔄 전체 시세 갱신", width="stretch"):
            if not portfolio_data: st.warning("장부에 종목이 없습니다."); st.stop()
            with st.status("전체 우량주 시세 및 매크로 멀티플 리캡 중...", expanded=True) as status:
                for row in portfolio_data:
                    st.write(f"[{row['name']}] 내재가치 가중 연산...")
                    df_p = fdr.DataReader(row['ticker'], start=(datetime.utcnow() + timedelta(hours=9) - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
                    if not df_p.empty:
                        cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                        cache['current_price'] = int(df_p['Close'].iloc[-1])
                        cache['year_high'] = int(df_p['High'].max())
                        prev_close = float(df_p['Close'].iloc[-2])
                        cache['pct_change'] = round(((cache['current_price'] - prev_close) / prev_close) * 100, 2)
                        
                        cache['macro_multiplier_snapshot'] = macro_mult
                        cache['macro_report_snapshot'] = macro_rep
                        cache['target_2026'] = calculate_intrinsic_target(row, cache, macro_multiplier=macro_mult)
                        supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                status.update(label="전체 시세 및 매크로 팩터 융합 완료!", state="complete")
            insert_log(supabase, username, "수동 시세 갱신", f"{len(portfolio_data)}종목 매크로 반영", f"환율 {macro_mult} 계수 정상 주입 완료")
            st.rerun()

        if col_sync2.button("📰 전체 뉴스 스캔", width="stretch"):
            if not naver_id or not naver_secret: st.error("네이버 API 키가 없습니다."); st.stop()
            if not portfolio_data: st.warning("장부에 종목이 없습니다."); st.stop()
            with st.status("전체 종목 미디어 소음 분석 및 리밸런싱...", expanded=True) as status:
                for row in portfolio_data:
                    st.write(f"[{row['name']}] 소음 가중치 계산...")
                    cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                    score, net_sent, _, n_list = get_auto_momentum(row['name'], naver_id, naver_secret)
                    cache['score'] = score
                    cache['net_sentiment'] = net_sent
                    cache['news_list'] = n_list
                    
                    cache['macro_multiplier_snapshot'] = macro_mult
                    cache['macro_report_snapshot'] = macro_rep
                    cache['target_2026'] = calculate_intrinsic_target(row, cache, macro_multiplier=macro_mult)
                    supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                status.update(label="미디어 모멘텀 연산 완료!", state="complete")
            st.rerun()

        if col_sync3.button("📊 강제 실적 매핑 (IP 주의)", width="stretch"):
            if not portfolio_data: st.warning("장부에 종목이 없습니다."); st.stop()
            with st.status("펀더멘탈 핵심 계정 딥 트레이킹...", expanded=True) as status:
                for row in portfolio_data:
                    st.write(f"[{row['name']}] 재무 데이터 동기화...")
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
                        
                    cache['macro_multiplier_snapshot'] = macro_mult
                    cache['macro_report_snapshot'] = macro_rep
                    cache['target_2026'] = calculate_intrinsic_target(row, cache, macro_multiplier=macro_mult)
                    supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                status.update(label="전체 기업 실적 마스터 매핑 성공!", state="complete")
            st.rerun()

        st.divider()

        with st.expander("➕ 장기 가치투자 신규 우량주 편입", expanded=False):
            col1, col2, col3 = st.columns(3)
            with col1: s_name = st.selectbox("우량 종목 선택", list(krx_map.keys()))
            with col2: buy_p = st.number_input("매입 평단가(원)", min_value=1, value=10000)
            with col3: qty = st.number_input("보유 수량(주)", min_value=1, value=10)
            if st.button("기업 동업 자산 등록", type="primary"):
                ticker = krx_map[s_name]
                try:
                    supabase.table("user_portfolio").upsert({
                        "username": username, "ticker": ticker, "name": s_name, "buy_price": buy_p, "qty": qty, "analysis_cache": {}
                    }).execute()
                    insert_log(supabase, username, "신규 편입", f"[{s_name}] 동업 개시", f"단가 {buy_p}원 등록")
                    st.success(f"[{s_name}] 포트폴리오 편입 완료!")
                    st.rerun()
                except Exception as e:
                    st.error("자산 편입 실패! RLS 설정을 확인하세요.")

        if not portfolio_data:
            st.info("현재 포트폴리오에 등록된 자산이 없습니다. 철저하게 여유 현금으로 우량주를 분할 매수해 보세요.")
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
            
            bm_scr = cache.get('bm_score', 0)
            pnl_amt = (curr_price - b_price) * s_qty
            pnl_pct = ((curr_price - b_price) / b_price) * 100 if b_price > 0 else 0
            
            stop_line = int(b_price * 0.85) 
            status_emoji = ""
            if name in CORE_CONVICTION_ASSETS:
                status_emoji = "💎 VIP 코어 확신주"
            else:
                if bm_scr >= 2 and pnl_pct < -10: status_emoji = "🛒 매크로 과매도 분할추매"
                elif curr_price < stop_line: status_emoji = "⚠️ 가치 재평가 필요"
                else:
                    if target_price > 0 and curr_price >= target_price * 0.95: status_emoji = "🎯 분할 가치실현 구간"
                    else: status_emoji = "🟢 본질가치 작동중" if pnl_pct >= 0 else "🔵 내재가치 축적구간"
            
            total_invest += b_price * s_qty
            total_value += curr_price * s_qty
            
            display_rows.append({
                "인프라 상태": status_emoji, 
                "기업명": name, 
                "현재가": f"₩ {int(curr_price):,}", 
                "전일비(%)": f"{day_pct:+.2f}%",
                "나의 동업단가": f"₩ {int(b_price):,}", 
                "보유지분": f"{int(s_qty):,} 주", 
                "누적 가치평가액": f"₩ {int(pnl_amt):,}", 
                "수익률(%)": f"{pnl_pct:+.2f}%",
                "매크로반영 적정가치": f"₩ {int(target_price):,}", 
                "raw_data": row
            })

        total_pnl = total_value - total_invest
        total_pnl_pct = (total_pnl / total_invest) * 100 if total_invest > 0 else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("총 가치투자 자본금", f"{total_invest:,} 원")
        c2.metric("현재 자산 평가액", f"{total_value:,} 원")
        c3.metric("총 가치 증식액", f"{total_pnl:,} 원", f"{total_pnl_pct:+.2f}%")
        
        st.write("💡 **자산 정렬 가이드:** 열 제목을 클릭하시면 가치 증식률 순으로 자동 정렬됩니다.")
        df_disp = pd.DataFrame(display_rows).drop(columns=["raw_data"])
        
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
            s_name = selected_stock["기업명"]
            raw_row = selected_stock["raw_data"]
            s_ticker = raw_row['ticker']
            s_cache = raw_row.get("analysis_cache") if raw_row.get("analysis_cache") else {}
            
            st.markdown(f"### 🛠️ [{s_name}] 자산 지분 조율 및 밸류에이션 정밀 분석")
            
            col_indi1, col_indi2, col_indi3 = st.columns(3)
            with col_indi1:
                if st.button(f"[{s_name}] 시세 단독 수집", width="stretch"):
                    df_p = fdr.DataReader(s_ticker, start=(datetime.utcnow() + timedelta(hours=9) - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
                    if not df_p.empty:
                        s_cache['current_price'] = int(df_p['Close'].iloc[-1])
                        s_cache['year_high'] = int(df_p['High'].max())
                        prev_close = float(df_p['Close'].iloc[-2])
                        s_cache['pct_change'] = round(((s_cache['current_price'] - prev_close) / prev_close) * 100, 2)
                        
                        s_cache['target_2026'] = calculate_intrinsic_target(raw_row, s_cache, macro_multiplier=macro_mult)
                        supabase.table("user_portfolio").update({"analysis_cache": s_cache}).eq("id", raw_row['id']).execute()
                        st.rerun()
            with col_indi2:
                if st.button(f"[{s_name}] 실적 단독 동기화", width="stretch"):
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
                    
                    s_cache['target_2026'] = calculate_intrinsic_target(raw_row, s_cache, macro_multiplier=macro_mult)
                    supabase.table("user_portfolio").update({"analysis_cache": s_cache}).eq("id", raw_row['id']).execute()
                    st.rerun()

            st.divider()

            col_btn1, col_btn2, col_btn3 = st.columns(3)
            with col_btn1:
                with st.popover("✏️ 장부 수량/단가 정정", width="stretch"):
                    new_p = st.number_input("정정 평단가", value=int(raw_row['buy_price']))
                    new_q = st.number_input("정정 지분수량", value=int(raw_row['qty']))
                    if st.button("장부 정정 인가", key="btn_edit_confirm"):
                        supabase.table("user_portfolio").update({"buy_price": new_p, "qty": new_q}).eq("id", raw_row['id']).execute()
                        insert_log(supabase, username, "장부 수정", f"[{s_name}] 정정", f"평단가 {new_p} / 수량 {new_q}")
                        st.success("장부 정정 완료!")
                        st.rerun()
            with col_btn2:
                with st.popover("🛒 분할 적립형 가치 매수", width="stretch"):
                    add_p = st.number_input("추가 동업가격", value=int(s_cache.get('current_price', raw_row['buy_price'])))
                    add_q = st.number_input("지분 추가수량", value=10)
                    if st.button("적립식 체결 승인", key="btn_buy_confirm"):
                        current_total_cost = raw_row['buy_price'] * raw_row['qty']
                        new_total_cost = current_total_cost + (add_p * add_q)
                        new_qty = raw_row['qty'] + add_q
                        new_avg_price = int(new_total_cost / new_qty)
                        supabase.table("user_portfolio").update({"buy_price": new_avg_price, "qty": new_qty}).eq("id", raw_row['id']).execute()
                        insert_log(supabase, username, "추가 적립", f"[{s_name}] {add_q}주 지분 확대", f"가치 매수단가 {add_p}원")
                        st.success("지분 합성 완료! 자산이 단단해졌습니다.")
                        st.rerun()
            with col_btn3:
                with st.popover("🚨 가치 실현 및 자산 회수", width="stretch"):
                    st.write(f"현재 보유 수량: **{raw_row['qty']}주**")
                    sell_p = st.number_input("자산 회수단가", value=int(s_cache.get('current_price', raw_row['buy_price'])))
                    sell_q = st.number_input("회수 수량", min_value=1, max_value=raw_row['qty'], value=raw_row['qty'])
                    if st.button("🚨 청산(매도) 집행", key="btn_sell_confirm"):
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
                            
                        insert_log(supabase, username, "가치 실현", f"[{s_name}] {sell_q}주 자산 회수", f"실현 이익 {profit_amt:,}원 ({profit_pct:+.2f}%)")
                        st.success("성공적으로 자본이 회수되어 내역에 박제되었습니다.")
                        st.rerun()

            st.divider()
            
            t1, t2, t3 = st.tabs(["📉 펀더멘탈 가치분석", "📰 전방 사업 명세", "📊 실적 트렌드 (착시 방지)"])
            with t1:
                st.markdown(f"**• 현재 종합 인프라 상태:** {selected_stock['인프라 상태']}")
                st.markdown(f"**• 안전마진 베이스라인 (현재가 기준):** `{s_cache.get('current_price', raw_row['buy_price']):,}`원")
                st.markdown(f"**• 미디어 소음 필터 가중치:** `{s_cache.get('net_sentiment', 0):+}` 점")
                st.markdown(f"**• 산업 사이클 및 마진 점수:** `{s_cache.get('bm_score', 0):+}` 점")
                st.markdown(f"**🌐 현재 적용 매크로 중력 계수:** `{macro_mult:.2f}` (환율 및 IPO 리스크 결합치)")
                st.markdown(f"**🚀 최종 매크로가 가중된 내재 적정가치:** `{s_cache.get('target_2026', raw_row['buy_price']):,}`원")
                st.write("**실시간 추적 뉴스 리스트**")
                for idx, news in enumerate(s_cache.get('news_list', []), 1):
                    st.markdown(f"[{idx}] [{news['title']}]({news['link']})")
                    
            with t2:
                st.write(f"**📢 기업 개요 및 펀더멘탈 요약:** {s_cache.get('summary', '실적 매핑을 실행해 주세요.')}")
                st.write(f"**• 실적 사이클 총평:** {s_cache.get('bm_summary', '-')}")
                if s_cache.get('bm_list'):
                    st.table(pd.DataFrame(s_cache['bm_list'], columns=["사업부문", "주요품목", "구분", "비중(%)"]))
                    
            with t3:
                if s_cache.get('q_headers') and len(s_cache['q_headers']) >= 2:
                    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True)
                    
                    ax1.set_facecolor('#F8F9FA')
                    ax1.bar(s_cache['q_headers'], s_cache['q_revenues'], color='#3182F6', alpha=0.8, width=0.35, label="매출액(억)")
                    ax1.set_ylabel('매출액 (억원)', color='#4E5968', fontweight='bold')
                    ax1.grid(True, linestyle=':', alpha=0.6)
                    ax1.legend(loc='upper left')
                    ax1.set_title(f"📊 {s_name} 분기별 펀더멘탈 추이 (매출액 vs 영업이익 독립 분석)", fontsize=12, fontweight='bold', pad=10)
                    
                    ax2.set_facecolor('#F8F9FA')
                    ax2.plot(s_cache['q_headers'], s_cache['q_op_profits'], color='#F04452', marker='o', linewidth=3, markersize=8, label="영업이익(억)")
                    ax2.set_ylabel('영업이익 (억원)', color='#4E5968', fontweight='bold')
                    ax2.axhline(0, color='#8B95A1', linewidth=1.5, linestyle='--') 
                    ax2.grid(True, linestyle=':', alpha=0.6)
                    ax2.legend(loc='upper left')
                    
                    plt.tight_layout()
                    st.pyplot(fig)
                else:
                    st.info("펀더멘탈 명세표를 먼저 매핑해 주세요. 데이터 확인 후 차트가 렌더링됩니다.")

    with tab_hist:
        st.subheader("📝 자산 회수(가치투자 청산) 히스토리")
        hist_res = supabase.table("user_history").select("*").eq("username", username).order("created_at", desc=True).execute()
        if not hist_res.data:
            st.info("아직 자산 회수(매도) 내역이 없습니다. 단타가 아닌 진성 가치수익의 기록이 이곳에 쌓이게 됩니다.")
        else:
            total_realized = sum([r['profit_amt'] for r in hist_res.data])
            win_count = sum([1 for r in hist_res.data if r['profit_amt'] > 0])
            win_rate = (win_count / len(hist_res.data)) * 100
            
            h1, h2 = st.columns(2)
            h1.metric("누적 가치 실현액", f"{total_realized:,} 원")
            h2.metric("가치투자 성공 승률", f"{win_rate:.1f} %")
            
            df_hist = pd.DataFrame(hist_res.data)
            df_hist['created_at'] = pd.to_datetime(df_hist['created_at']) + pd.Timedelta(hours=9)
            df_hist['created_at'] = df_hist['created_at'].dt.strftime('%Y-%m-%d %H:%M')
            df_hist = df_hist[['created_at', 'name', 'buy_price', 'sell_price', 'qty', 'profit_amt', 'profit_pct']]
            df_hist.columns = ['회수일시', '기업명', '진입단가', '회수단가', '보유지분', '실현이익', '최종수익률']
            
            df_hist['진입단가'] = df_hist['진입단가'].apply(lambda x: f"₩ {int(x):,}")
            df_hist['회수단가'] = df_hist['회수단가'].apply(lambda x: f"₩ {int(x):,}")
            df_hist['보유지분'] = df_hist['보유지분'].apply(lambda x: f"{int(x):,} 주")
            df_hist['실현이익'] = df_hist['실현이익'].apply(lambda x: f"₩ {int(x):,}")
            df_hist['최종수익률'] = df_hist['최종수익률'].apply(lambda x: f"{x:+.2f} %")
            
            st.dataframe(df_hist, width="stretch")

    with tab_log:
        st.subheader("⚙️ 퀀트 시스템 핵심 가동 로그")
        log_res = supabase.table("user_logs").select("*").eq("username", username).order("created_at", desc=True).execute()
        if not log_res.data:
            st.info("시스템 처리 기록이 깨끗합니다.")
        else:
            df_log = pd.DataFrame(log_res.data)
            df_log['created_at'] = pd.to_datetime(df_log['created_at']) + pd.Timedelta(hours=9)
            df_log['created_at'] = df_log['created_at'].dt.strftime('%Y-%m-%d %H:%M:%S')
            df_log = df_log[['created_at', 'module', 'summary', 'details']]
            df_log.columns = ['시간', '보안모듈', '요약', '상세 가동 내역']
            st.dataframe(df_log, width="stretch")
