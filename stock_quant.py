import streamlit as st
import requests
import re
import html
import FinanceDataReader as fdr
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import threading
import time

# --- 팩터 마스터 프리미엄 사전 설정 ---
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
            
    return score, report

def fetch_global_macro_factor(client_id, client_secret):
    macro_multiplier = 1.0
    current_usd = 1541.6  
    환율상태 = "정상"
    IPO상태 = "건전"
    
    try:
        df_usd = fdr.DataReader('USD/KRW', start=(datetime.utcnow() - timedelta(days=45)).strftime('%Y-%m-%d'))
        if not df_usd.empty:
            current_usd = round(float(df_usd['Close'].iloc[-1]), 1)
            usd_ma20 = round(float(df_usd['Close'].rolling(20).mean().iloc[-1]), 1) if len(df_usd) >= 20 else current_usd
            
            if current_usd >= 1500:
                macro_multiplier -= 0.08
                환율상태 = f"🚨 위기 장세 ({current_usd}원)"
            elif current_usd > usd_ma20:
                macro_multiplier -= 0.03  
                환율상태 = f"⚠️ 변동성 과열 ({current_usd}원)"
            else:
                macro_multiplier += 0.02  
                환율상태 = f"🍏 하방 진정 ({current_usd}원)"
    except:
        환율상태 = "⚠️ 센서 지연"

    if client_id and client_secret:
        exact_query = '"대형 IPO" "우려" "유동성"'
        url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(exact_query)}&display=5&sort=date"
        headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
        try:
            res = requests.get(url, headers=headers, timeout=3)
            if res.status_code == 200 and len(res.json().get('items', [])) >= 2:
                macro_multiplier -= 0.05
                IPO상태 = "🚨 블랙홀 감지"
            else:
                IPO상태 = "✨ 유동성 건전"
        except:
            IPO상태 = "⚠️ 센서 지연"
            
    return round(macro_multiplier, 2), current_usd, 환율상태, IPO상태

# 👍 [패치 완료] 삼화/광전자 강제 고정 코드 파괴 ➔ 전 종목 수리적 내재가치 모델 연동
def calculate_intrinsic_target(row, cache, macro_multiplier=1.0):
    base_price = max(row['buy_price'], cache.get('year_high', row['buy_price']))
    net_sent = cache.get('net_sentiment', 0)
    bm_score = cache.get('bm_score', 0)
    
    fundamental_factor = 1.25
    if cache.get('q_op_profits') and len(cache['q_op_profits']) >= 2:
        last_op = cache['q_op_profits'][-1]
        prev_op = cache['q_op_profits'][-2]
        if last_op > prev_op and prev_op > 0:
            fundamental_factor += min(0.15, (last_op - prev_op) / prev_op * 0.1)
            
    sentiment_factor = (net_sent * 0.01)
    cycle_factor = (bm_score * 0.02)
    macro_buffer = 1.0 + (macro_multiplier - 1.0) * 0.2
    
    total_multiplier = (fundamental_factor + sentiment_factor + cycle_factor) * macro_buffer
    total_multiplier = max(1.15, min(total_multiplier, 1.60)) 
    
    return int(base_price * total_multiplier)

def insert_log(supabase, username, module, summary, details):
    try:
        supabase.table("user_logs").insert({
            "username": username, "module": module, "summary": summary, "details": details
        }).execute()
    except Exception as e:
        print(f"로그 기록 실패: {e}")

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
                    m_mult, _, _, _ = fetch_global_macro_factor(naver_id, naver_secret)
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
                            
                            cache['target_2026'] = calculate_intrinsic_target(row, cache, m_mult)
                            supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                except Exception as e:
                    pass
        time.sleep(30)


def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    st.title("📈 스마트 프랍 퀀트 포트폴리오 엔진 (Premium v5)")
    
    if username not in _active_threads:
        t = threading.Thread(target=auto_sync_job, args=(supabase, username, naver_id, naver_secret), daemon=True)
        t.start()
        _active_threads[username] = t

    krx_map = {"삼성전자": "005930", "SK하이닉스": "000660", "삼화콘덴서": "001820", "광전자": "017900", "삼성생명": "032830", "LG전자": "066570", "SK증권": "001510", "유안타증권": "003470", "미래에셋벤처투자": "100790", "로보스타": "090360", "테스": "095610", "대원전선우": "006345"}
    try:
        df_k = fdr.StockListing('KRX')
        krx_map = {row['Name']: row['Code'] for _, row in df_k.iterrows()}
    except: pass

    macro_mult, current_usd, 환율상태, IPO상태 = fetch_global_macro_factor(naver_id, naver_secret)
    
    with st.container(border=True):
        st.markdown("##### 🌐 GLOBAL MACRO FLOW (거시경제 유동성 레이더)")
        m_col1, m_col2, m_col3 = st.columns(3)
        with m_col1:
            st.metric("원/달러 FX 중력", 환율상태, delta="⚠️ 매도 과부하" if current_usd >= 1500 else "안정 구역", delta_color="inverse")
        with m_col2:
            st.metric("대형 IPO 유동성", IPO상태, delta="⚠️ 자금 흡수" if "블랙홀" in IPO상태 else "수급 양호", delta_color="inverse")
        with m_col3:
            st.metric("통합 매크로 계수", f"{macro_mult}x", delta=f"보수적 안전벨트 작동 중")

    tab_port, tab_hist, tab_log = st.tabs(["💼 보유 우량 자산", "📝 가치 실현 내역", "⚙️ 가치투자 엔진 기록"])

    with tab_port:
        st.write("⚡ **수동 제어판**")
        col_sync1, col_sync2, col_sync3 = st.columns(3)
        
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        if col_sync1.button("🔄 전체 시세 갱신", width="stretch"):
            if not portfolio_data: st.warning("장부에 종목이 없습니다."); st.stop()
            with st.status("전체 우량주 가치 재연산 및 복구 중...", expanded=True) as status:
                for row in portfolio_data:
                    df_p = fdr.DataReader(row['ticker'], start=(datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d'))
                    if not df_p.empty:
                        cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                        cache['current_price'] = int(df_p['Close'].iloc[-1])
                        cache['year_high'] = int(df_p['High'].max())
                        prev_close = float(df_p['Close'].iloc[-2])
                        cache['pct_change'] = round(((cache['current_price'] - prev_close) / prev_close) * 100, 2)
                        cache['target_2026'] = calculate_intrinsic_target(row, cache, macro_multiplier=macro_mult)
                        supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                status.update(label="동기화 및 상방 밸류에이션 모델 주입 완료!", state="complete")
            st.rerun()

        if col_sync2.button("📰 전체 뉴스 스캔", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("미디어 감성 필터링 중..."):
                for row in portfolio_data:
                    cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                    score, net_sent, _, n_list = get_auto_momentum(row['name'], naver_id, naver_secret)
                    cache['score'] = score
                    cache['net_sentiment'] = net_sent
                    cache['news_list'] = n_list
                    cache['target_2026'] = calculate_intrinsic_target(row, cache, macro_multiplier=macro_mult)
                    supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
            st.rerun()

        if col_sync3.button("📊 강제 실적 매핑", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("재무 전수조사 중..."):
                for row in portfolio_data:
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
                    cache['target_2026'] = calculate_intrinsic_target(row, cache, macro_multiplier=macro_mult)
                    supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
            st.rerun()

        st.divider()

        if not portfolio_data:
            st.info("현재 장부에 주식이 없습니다.")
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
            
            # 👍 [필터 교정] 삼화/광전자도 일반 종목과 동일하게 인프라 상태 및 매수추매 시그널 자동 파싱
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
                "현재가": curr_price, 
                "전일비": day_pct,
                "평단가": b_price, 
                "보유지분": s_qty, 
                "누적 가치평가액": pnl_amt, 
                "수익률": pnl_pct,
                "2026 적정가치": target_price, 
                "raw_data": row
            })

        total_pnl = total_value - total_invest
        total_pnl_pct = (total_pnl / total_invest) * 100 if total_invest > 0 else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("총 가치투자 자본금", f"{total_invest:,} 원")
        c2.metric("현재 자산 평가액", f"{total_value:,} 원")
        c3.metric("총 가치 증식액", f"{total_pnl:,} 원", f"{total_pnl_pct:+.2f}%")
        
        df_base = pd.DataFrame(display_rows)
        df_disp = pd.DataFrame()
        df_disp["인프라 상태"] = df_base["인프라 상태"]
        df_disp["기업명"] = df_base["기업명"]
        df_disp["현재가"] = df_base["현재가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["전일비(%)"] = df_base["전일비"].apply(lambda x: f"{x:+.2f}%")
        df_disp["평단가"] = df_base["평단가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["보유지분"] = df_base["보유지분"].apply(lambda x: f"{int(x):,} 주")
        df_disp["누적 가치평가액"] = df_base["누적 가치평가액"].apply(lambda x: f"₩ {int(x):+,}" if x != 0 else "₩ 0")
        df_disp["수익률(%)"] = df_base["수익률"].apply(lambda x: f"{x:+.2f}%")
        df_disp["2026 적정가치"] = df_base["2026 적정가치"].apply(lambda x: f"₩ {int(x):,}")

        def style_mts_color(row):
            styles = [''] * len(row)
            pnl = df_base.loc[row.name, '수익률']
            day = df_base.loc[row.name, '전일비']
            
            pnl_style = 'background-color: rgba(240, 68, 82, 0.12); color: #F04452; font-weight: bold;' if pnl > 0 else ('background-color: rgba(49, 130, 246, 0.12); color: #3182F6; font-weight: bold;' if pnl < 0 else 'color: #4E5968;')
            styles[df_disp.columns.get_loc('수익률(%)')] = pnl_style
            styles[df_disp.columns.get_loc('누적 가치평가액')] = pnl_style
            
            day_style = 'color: #F04452; font-weight: bold;' if day > 0 else ('color: #3182F6; font-weight: bold;' if day < 0 else 'color: #4E5968;')
            styles[df_disp.columns.get_loc('전일비(%)')] = day_style
            return styles

        styled_df = df_disp.style.apply(style_mts_color, axis=1)

        selection_event = st.dataframe(
            styled_df, 
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
            
            col_indi1, col_indi2 = st.columns(2)
            with col_indi1:
                if st.button(f"[{s_name}] 시세 단독 수집", width="stretch"):
                    df_p = fdr.DataReader(s_ticker, start=(datetime.utcnow() - timedelta(hours=9) - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
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
                        st.success("지분 합성 완료!")
                        st.rerun()
            with col_btn3:
                with st.popover("🚨 가치 실현 및 자산 회수", width="stretch"):
                    sell_p = st.number_input("자산 회수단가", value=int(s_cache.get('current_price', raw_row['buy_price'])))
                    sell_q = st.number_input("회수 수량", min_value=1, max_value=raw_row['qty'], value=raw_row['qty'])
                    if st.button("🚨 청산(매도) 집행", key="btn_sell_confirm"):
                        # 👍 [패치 완료] 누락되었던 가치실현 청산 히스토리 수치 역산 및 DB 백업 로직 전면 복구
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
                        st.success("자본 회수 완료!")
                        st.rerun()

            st.divider()
            
            t1, t2, t3 = st.tabs(["📉 펀더멘탈 가치분석", "📰 전방 사업 명세", "📊 실적 트렌드 (착시 방지)"])
            with t1:
                st.markdown(f"**• 현재 종합 인프라 상태:** {selected_stock['인프라 상태']}")
                st.markdown(f"**• 나의 평단가 마디:** `₩ {int(raw_row['buy_price']):,}`원")
                st.markdown(f"**• 미디어 소음 필터 가중치:** `{s_cache.get('net_sentiment', 0):+}` 점")
                st.markdown(f"**• 산업 사이클 및 마진 점수:** `{s_cache.get('bm_score', 0):+}` 점")
                st.markdown(f"**🌐 현재 적용 매크로 보정 계수:** `{macro_mult:.2f}x`")
                st.markdown(f"**🚀 2026년 기준 최종 내재 적정가치(목표치):** `₩ {int(s_cache.get('target_2026', raw_row['buy_price'])):,}`원")
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
                    chart_df = pd.DataFrame({
                        "분기": s_cache['q_headers'],
                        "매출액(억원)": s_cache['q_revenues'],
                        "영업이익(억원)": s_cache['q_op_profits']
                    }).set_index("분기")
                    
                    c_col1, c_col2 = st.columns(2)
                    with c_col1:
                        st.markdown("**Quarterly Revenue (분기 매출액 추이)**")
                        st.bar_chart(chart_df["매출액(억원)"], color="#3182F6")
                    with c_col2:
                        st.markdown("**Quarterly Operating Profit (분기 영업이익 추이)**")
                        st.line_chart(chart_df["영업이익(억원)"], color="#F04452")
                else:
                    st.info("펀더멘탈 명세표를 먼저 매핑해 주세요.")

    with tab_hist:
        # 👍 [MTS 피드백 수렴 완료] 청산 히스토리 및 매매 승률 연산 렌더링 화면 완벽 복구
        st.subheader("📝 자산 회수(가치투자 청산) 히스토리")
        hist_res = supabase.table("user_history").select("*").eq("username", username).order("created_at", desc=True).execute()
        if not hist_res.data:
            st.info("아직 자산 회수(매도) 내역이 없습니다. 가치 실현이 완료되면 승률 기록이 이곳에 실시간 빌드됩니다.")
        else:
            total_realized = sum([r['profit_amt'] for r in hist_res.data])
            win_count = sum([1 for r in hist_res.data if r['profit_amt'] > 0])
            win_rate = (win_count / len(hist_res.data)) * 100 if len(hist_res.data) > 0 else 0
            
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
        st.subheader("⚙️ 퀀트 시스템 가동 로그")
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
