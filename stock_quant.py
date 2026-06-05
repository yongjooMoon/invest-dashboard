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

# 👍 [핵심 패치] 네이버 금융에서 PER, EPS, PBR, BPS 데이터 정밀 추출
def fetch_naver_fundamentals(raw_code):
    url = f"https://finance.naver.com/item/main.naver?code={raw_code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.content, 'html.parser')
        
        summary_div = soup.select_one('.summary_info')
        company_summary = summary_div.text.replace('\n', ' ').strip() if summary_div else ""
        
        def parse_num(txt):
            try: return float(txt.replace(',','').strip())
            except: return 0.0

        # 투자정보 스크래핑 (PER, EPS, PBR, BPS)
        val_data = {'per': 10.0, 'eps': 0.0, 'pbr': 1.0, 'bps': 0.0}
        try:
            val_data['per'] = parse_num(soup.select_one('#_per').text) if soup.select_one('#_per') else 10.0
            val_data['eps'] = parse_num(soup.select_one('#_eps').text) if soup.select_one('#_eps') else 0.0
            val_data['pbr'] = parse_num(soup.select_one('#_pbr').text) if soup.select_one('#_pbr') else 1.0
            val_data['bps'] = parse_num(soup.select_one('#_bps').text) if soup.select_one('#_bps') else 0.0
        except: pass

        table = soup.select_one('div.cop_analysis table')
        if not table: 
            return {"q_headers": [], "q_revenues": [], "q_op_profits": [], "summary": company_summary, **val_data}
            
        rows = table.select_one('tbody').select('tr')
        thead = table.select_one('thead')
        
        q_headers = [th.text.strip() for th in thead.select('tr')[1].select('th')[5:10]]
        q_revenues = [parse_num(td.text) for td in rows[0].select('td')[5:10]]
        q_op_profits = [parse_num(td.text) for td in rows[1].select('td')[5:10]]
        
        valid_indices = [i for i, rev in enumerate(q_revenues) if rev != 0.0]
        if valid_indices:
            q_headers = [q_headers[i] for i in valid_indices]
            q_revenues = [q_revenues[i] for i in valid_indices]
            q_op_profits = [q_op_profits[i] for i in valid_indices]
        
        return {"q_headers": q_headers, "q_revenues": q_revenues, "q_op_profits": q_op_profits, "summary": company_summary, **val_data}
    except Exception as e: 
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
                macro_multiplier -= 0.08
                환율상태 = f"🚨 고환율/외인 이탈 우려 ({current_usd}원)"
            elif current_usd > usd_ma20:
                macro_multiplier -= 0.03  
                환율상태 = f"⚠️ 변동성 과열 ({current_usd}원)"
            else:
                macro_multiplier += 0.02  
                환율상태 = f"🍏 하방 안정화 ({current_usd}원)"
    except:
        환율상태 = "⚠️ 센서 지연"
        
    return round(macro_multiplier, 2), current_usd, 환율상태

# 👍 [진성 밸류에이션 모델] 과거 주가 종속 탈피, 오직 기업 이익(EPS)과 자본(BPS) 기준 연산
def calculate_intrinsic_target(row, cache, macro_multiplier=1.0):
    name = row['name']
    
    # 캐시에서 재무 지표 추출
    eps = cache.get('eps', 0.0)
    per = cache.get('per', 10.0)
    bps = cache.get('bps', 0.0)
    pbr = cache.get('pbr', 1.0)
    current_price = cache.get('current_price', row['buy_price'])
    
    # 1. 내재 가치 연산 (Intrinsic Value)
    intrinsic_value = current_price
    
    if eps > 0:
        # 흑자 기업: 2026년 예상 EPS (매년 보수적 5% 성장 가정 = 2년 약 10% 할증) * 타깃 PER
        # 터무니없는 PER 방지를 위해 상하단 캡 적용 (제조업 평균 수준 8배 ~ 성장주 20배 내외)
        target_per = min(max(per, 8.0), 25.0) 
        intrinsic_value = (eps * 1.10) * target_per
    elif bps > 0:
        # 적자 기업: 자본(BPS) 기반 PBR 밸류에이션 적용
        target_pbr = min(max(pbr, 0.8), 2.5)
        intrinsic_value = bps * target_pbr
    
    # 2. 업종별 매크로 차별화 (수출주 vs 내수/금융주 환율 영향력 분리)
    is_exporter = any(keyword in name for keyword in ["전자", "하이닉스", "전선", "로보", "테스", "광전자", "삼화"])
    
    macro_mod = 1.0
    if macro_multiplier < 1.0: # 환율 급등 시
        if is_exporter: 
            macro_mod = 1.05 # 수출주는 환차익 수혜로 프리미엄
        else: 
            macro_mod = 0.90 # 내수/금융/증권주는 자본 유출 리스크 디스카운트
    else:
        macro_mod = 1.0
        
    # 3. 비재무적 모멘텀은 미세 조정(최대 ±2%)으로만 제한
    sentiment_factor = 1.0 + (cache.get('net_sentiment', 0) * 0.005)
    cycle_factor = 1.0 + (cache.get('bm_score', 0) * 0.01)
    
    # 최종 타깃가 = 내재가치 * 수출주 보정 매크로 * 모멘텀 미세조정
    final_target = intrinsic_value * macro_mod * sentiment_factor * cycle_factor
    
    # 극한의 과소/과대평가 방지를 위해 현재가 대비 -20% ~ +150% 밴드 락(Lock)
    final_target = max(current_price * 0.80, min(final_target, current_price * 2.50))
    
    return int(final_target)

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
                    m_mult, _, _ = fetch_global_macro_factor()
                    db_res = supabase.table("user_portfolio").select("*").eq("username", username).execute()
                    portfolio_data = db_res.data
                    if portfolio_data:
                        for row in portfolio_data:
                            cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                            
                            df_p = fdr.DataReader(row['ticker'], start=(now_kst - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
                            if not df_p.empty:
                                cache['current_price'] = int(df_p['Close'].iloc[-1])
                                prev_close = float(df_p['Close'].iloc[-2])
                                cache['pct_change'] = round(((cache['current_price'] - prev_close) / prev_close) * 100, 2)
                            
                            score, net_sent, _, n_list = get_auto_momentum(row['name'], naver_id, naver_secret)
                            cache['score'] = score
                            cache['net_sentiment'] = net_sent
                            cache['news_list'] = n_list
                            
                            # 정밀 밸류에이션을 위해 펀더멘탈 주기적 갱신 보장
                            fund = fetch_naver_fundamentals(row['ticker'])
                            if fund:
                                cache['eps'] = fund.get('eps', 0)
                                cache['per'] = fund.get('per', 10)
                                cache['bps'] = fund.get('bps', 0)
                                cache['pbr'] = fund.get('pbr', 1)
                                cache['q_headers'] = fund.get('q_headers', [])
                                cache['q_revenues'] = fund.get('q_revenues', [])
                                cache['q_op_profits'] = fund.get('q_op_profits', [])
                                cache['summary'] = fund.get('summary', '')
                                
                                bm_list = fetch_dynamic_company_bm(row['ticker'])
                                core_prod = bm_list[0][1] if bm_list else "기반 사업"
                                bm_score, bm_summary = calculate_bm_score(fund, core_prod, row['name'])
                                cache['bm_list'] = bm_list
                                cache['bm_score'] = bm_score
                                cache['bm_summary'] = bm_summary
                            
                            cache['target_2026'] = calculate_intrinsic_target(row, cache, m_mult)
                            supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                except Exception as e:
                    pass
        time.sleep(30)


def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    st.title("📈 스마트 프랍 퀀트 포트폴리오 엔진 (Premium v6)")
    
    if username not in _active_threads:
        t = threading.Thread(target=auto_sync_job, args=(supabase, username, naver_id, naver_secret), daemon=True)
        t.start()
        _active_threads[username] = t

    krx_map = {"삼성전자": "005930", "SK하이닉스": "000660", "삼화콘덴서": "001820", "광전자": "017900", "삼성생명": "032830", "LG전자": "066570", "SK증권": "001510", "유안타증권": "003470", "미래에셋벤처투자": "100790", "로보스타": "090360", "테스": "095610", "대원전선우": "006345"}
    try:
        df_k = fdr.StockListing('KRX')
        krx_map = {row['Name']: row['Code'] for _, row in df_k.iterrows()}
    except: pass

    macro_mult, current_usd, 환율상태 = fetch_global_macro_factor()
    
    with st.container(border=True):
        st.markdown("##### 🌐 GLOBAL MACRO FLOW (외환 유동성 레이더)")
        m_col1, m_col2 = st.columns(2)
        with m_col1:
            st.metric("원/달러 환율 추이", 환율상태, delta="외인 이탈 리스크 상승" if current_usd >= 1400 else "안정 구역", delta_color="inverse")
        with m_col2:
            st.metric("시장 환율 보정 계수", f"{macro_mult}x", delta="수출/내수 분리 타겟팅 작동 중")

    tab_port, tab_hist, tab_log = st.tabs(["💼 기업 지분 자산", "📝 가치 실현 내역", "⚙️ 시스템 가동 로그"])

    with tab_port:
        st.write("⚡ **수동 제어판**")
        col_sync1, col_sync2, col_sync3 = st.columns(3)
        
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        if col_sync1.button("🔄 가치 밸류에이션 전면 재연산", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("EPS/PER 기반 정통 밸류에이션 리캡 중...", expanded=True) as status:
                for row in portfolio_data:
                    df_p = fdr.DataReader(row['ticker'], start=(datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d'))
                    if not df_p.empty:
                        cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                        cache['current_price'] = int(df_p['Close'].iloc[-1])
                        prev_close = float(df_p['Close'].iloc[-2])
                        cache['pct_change'] = round(((cache['current_price'] - prev_close) / prev_close) * 100, 2)
                        
                        fund = fetch_naver_fundamentals(row['ticker'])
                        if fund:
                            cache['eps'] = fund.get('eps', 0)
                            cache['per'] = fund.get('per', 10)
                            cache['bps'] = fund.get('bps', 0)
                            cache['pbr'] = fund.get('pbr', 1)
                            
                        cache['target_2026'] = calculate_intrinsic_target(row, cache, macro_multiplier=macro_mult)
                        supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
                status.update(label="적정가치 하방 오류 교정 및 EPS 맵핑 완료!", state="complete")
            st.rerun()

        if col_sync2.button("📰 뉴스 소음 필터링", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("가짜 모멘텀 비중 축소 중..."):
                for row in portfolio_data:
                    cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                    score, net_sent, _, n_list = get_auto_momentum(row['name'], naver_id, naver_secret)
                    cache['score'] = score
                    cache['net_sentiment'] = net_sent
                    cache['news_list'] = n_list
                    cache['target_2026'] = calculate_intrinsic_target(row, cache, macro_multiplier=macro_mult)
                    supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
            st.rerun()

        if col_sync3.button("📊 실적 딥 크롤링", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("재무제표 전수조사 중..."):
                for row in portfolio_data:
                    cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
                    fund = fetch_naver_fundamentals(row['ticker'])
                    bm_list = fetch_dynamic_company_bm(row['ticker'])
                    core_prod = bm_list[0][1] if bm_list else "기반 사업"
                    bm_score, bm_summary = calculate_bm_score(fund, core_prod, row['name'])
                    
                    if fund:
                        cache['eps'] = fund.get('eps', 0)
                        cache['per'] = fund.get('per', 10)
                        cache['bps'] = fund.get('bps', 0)
                        cache['pbr'] = fund.get('pbr', 1)
                        cache['q_headers'] = fund.get('q_headers', [])
                        cache['q_revenues'] = fund.get('q_revenues', [])
                        cache['q_op_profits'] = fund.get('q_op_profits', [])
                        cache['summary'] = fund.get('summary', '')
                        
                    cache['bm_list'] = bm_list
                    cache['bm_score'] = bm_score
                    cache['bm_summary'] = bm_summary
                    cache['target_2026'] = calculate_intrinsic_target(row, cache, macro_multiplier=macro_mult)
                    supabase.table("user_portfolio").update({"analysis_cache": cache}).eq("id", row['id']).execute()
            st.rerun()

        st.divider()

        if not portfolio_data:
            st.info("장부에 주식이 없습니다.")
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
            
            pnl_amt = (curr_price - b_price) * s_qty
            pnl_pct = ((curr_price - b_price) / b_price) * 100 if b_price > 0 else 0
            
            # 밸류에이션 기반 저평가 여부 상태 표시
            val_ratio = curr_price / target_price if target_price > 0 else 1.0
            status_emoji = ""
            if val_ratio < 0.6: status_emoji = "🛒 극심한 저평가 구간"
            elif val_ratio < 0.8: status_emoji = "🔵 안전마진 확보"
            elif val_ratio < 0.95: status_emoji = "🟢 본질가치 수렴 중"
            else: status_emoji = "🎯 가치 달성 / 청산 고려"
            
            total_invest += b_price * s_qty
            total_value += curr_price * s_qty
            
            display_rows.append({
                "밸류에이션 상태": status_emoji, 
                "기업명": name, 
                "현재가": curr_price, 
                "전일비": day_pct,
                "평단가": b_price, 
                "보유지분": s_qty, 
                "평가손익": pnl_amt, 
                "수익률": pnl_pct,
                "적정 내재가치": target_price, 
                "raw_data": row
            })

        total_pnl = total_value - total_invest
        total_pnl_pct = (total_pnl / total_invest) * 100 if total_invest > 0 else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("총 투입 자본", f"{total_invest:,} 원")
        c2.metric("현재 평가 자산", f"{total_value:,} 원")
        c3.metric("가치 증식", f"{total_pnl:,} 원", f"{total_pnl_pct:+.2f}%")
        
        df_base = pd.DataFrame(display_rows)
        df_disp = pd.DataFrame()
        df_disp["상태"] = df_base["밸류에이션 상태"]
        df_disp["기업명"] = df_base["기업명"]
        df_disp["현재가"] = df_base["현재가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["전일비(%)"] = df_base["전일비"].apply(lambda x: f"{x:+.2f}%")
        df_disp["평단가"] = df_base["평단가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["보유지분"] = df_base["보유지분"].apply(lambda x: f"{int(x):,} 주")
        df_disp["평가손익"] = df_base["평가손익"].apply(lambda x: f"₩ {int(x):+,}" if x != 0 else "₩ 0")
        df_disp["수익률(%)"] = df_base["수익률"].apply(lambda x: f"{x:+.2f}%")
        df_disp["내재 적정가치"] = df_base["적정 내재가치"].apply(lambda x: f"₩ {int(x):,}")

        def style_mts_color(row):
            styles = [''] * len(row)
            pnl = df_base.loc[row.name, '수익률']
            day = df_base.loc[row.name, '전일비']
            
            pnl_style = 'background-color: rgba(240, 68, 82, 0.12); color: #F04452; font-weight: bold;' if pnl > 0 else ('background-color: rgba(49, 130, 246, 0.12); color: #3182F6; font-weight: bold;' if pnl < 0 else 'color: #4E5968;')
            styles[df_disp.columns.get_loc('수익률(%)')] = pnl_style
            styles[df_disp.columns.get_loc('평가손익')] = pnl_style
            
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
            
            st.markdown(f"### 🛠️ [{s_name}] 정밀 밸류에이션 분석")
            
            col_indi1, col_indi2 = st.columns(2)
            with col_indi1:
                if st.button(f"[{s_name}] 시세 즉시 반영", width="stretch"):
                    df_p = fdr.DataReader(s_ticker, start=(datetime.utcnow() - timedelta(hours=9) - pd.DateOffset(days=7)).strftime('%Y-%m-%d'))
                    if not df_p.empty:
                        s_cache['current_price'] = int(df_p['Close'].iloc[-1])
                        prev_close = float(df_p['Close'].iloc[-2])
                        s_cache['pct_change'] = round(((s_cache['current_price'] - prev_close) / prev_close) * 100, 2)
                        s_cache['target_2026'] = calculate_intrinsic_target(raw_row, s_cache, macro_multiplier=macro_mult)
                        supabase.table("user_portfolio").update({"analysis_cache": s_cache}).eq("id", raw_row['id']).execute()
                        st.rerun()
            with col_indi2:
                if st.button(f"[{s_name}] 재무 데이터 최신화", width="stretch"):
                    fund = fetch_naver_fundamentals(s_ticker)
                    if fund:
                        s_cache['eps'] = fund.get('eps', 0)
                        s_cache['per'] = fund.get('per', 10)
                        s_cache['bps'] = fund.get('bps', 0)
                        s_cache['pbr'] = fund.get('pbr', 1)
                        s_cache['q_headers'] = fund.get('q_headers', [])
                        s_cache['q_revenues'] = fund.get('q_revenues', [])
                        s_cache['q_op_profits'] = fund.get('q_op_profits', [])
                    s_cache['target_2026'] = calculate_intrinsic_target(raw_row, s_cache, macro_multiplier=macro_mult)
                    supabase.table("user_portfolio").update({"analysis_cache": s_cache}).eq("id", raw_row['id']).execute()
                    st.rerun()

            st.divider()

            col_btn1, col_btn2, col_btn3 = st.columns(3)
            with col_btn1:
                with st.popover("✏️ 장부 정정", width="stretch"):
                    new_p = st.number_input("정정 평단가", value=int(raw_row['buy_price']))
                    new_q = st.number_input("정정 지분수량", value=int(raw_row['qty']))
                    if st.button("장부 정정", key="btn_edit_confirm"):
                        supabase.table("user_portfolio").update({"buy_price": new_p, "qty": new_q}).eq("id", raw_row['id']).execute()
                        st.rerun()
            with col_btn2:
                with st.popover("🛒 분할 적립", width="stretch"):
                    add_p = st.number_input("추가 단가", value=int(s_cache.get('current_price', raw_row['buy_price'])))
                    add_q = st.number_input("추가 수량", value=10)
                    if st.button("적립식 체결", key="btn_buy_confirm"):
                        current_total_cost = raw_row['buy_price'] * raw_row['qty']
                        new_total_cost = current_total_cost + (add_p * add_q)
                        new_qty = raw_row['qty'] + add_q
                        new_avg_price = int(new_total_cost / new_qty)
                        supabase.table("user_portfolio").update({"buy_price": new_avg_price, "qty": new_qty}).eq("id", raw_row['id']).execute()
                        st.rerun()
            with col_btn3:
                with st.popover("🚨 청산(매도)", width="stretch"):
                    sell_p = st.number_input("회수 단가", value=int(s_cache.get('current_price', raw_row['buy_price'])))
                    sell_q = st.number_input("회수 수량", min_value=1, max_value=raw_row['qty'], value=raw_row['qty'])
                    if st.button("🚨 매도 집행", key="btn_sell_confirm"):
                        profit_amt = (sell_p - raw_row['buy_price']) * sell_q
                        profit_pct = round(((sell_p - raw_row['buy_price']) / raw_row['buy_price']) * 100, 2)
                        try:
                            supabase.table("user_history").insert({
                                "username": username, "ticker": raw_row['ticker'], "name": s_name,
                                "buy_price": raw_row['buy_price'], "sell_price": sell_p, "qty": sell_q,
                                "profit_amt": profit_amt, "profit_pct": profit_pct
                            }).execute()
                        except: pass
                        if sell_q == raw_row['qty']:
                            supabase.table("user_portfolio").delete().eq("id", raw_row['id']).execute()
                        else:
                            supabase.table("user_portfolio").update({"qty": raw_row['qty'] - sell_q}).eq("id", raw_row['id']).execute()
                        st.rerun()

            st.divider()
            
            t1, t2, t3 = st.tabs(["📉 펀더멘탈 가치분석", "📰 전방 사업 명세", "📊 실적 트렌드 (착시 방지)"])
            with t1:
                # 밸류에이션 지표 화면 출력
                eps_val = s_cache.get('eps', 0.0)
                per_val = s_cache.get('per', 0.0)
                bps_val = s_cache.get('bps', 0.0)
                pbr_val = s_cache.get('pbr', 0.0)
                
                st.markdown(f"**• 현재 종합 상태:** {selected_stock['밸류에이션 상태']}")
                st.markdown(f"**• TTM 재무 지표:** EPS `{eps_val:,.0f}원` | PER `{per_val:.2f}배` | BPS `{bps_val:,.0f}원` | PBR `{pbr_val:.2f}배`")
                st.markdown(f"**• 매크로 보정 로직:** 수출주 수혜 / 내수주 할인 로직 적용 중")
                st.markdown(f"**🚀 EPS/BPS 기반 적정 내재가치:** `₩ {int(s_cache.get('target_2026', raw_row['buy_price'])):,}`원")
                st.write("**최신 뉴스 스니펫**")
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

    with tab_hist:
        st.subheader("📝 자산 회수 히스토리")
        hist_res = supabase.table("user_history").select("*").eq("username", username).order("created_at", desc=True).execute()
        if not hist_res.data:
            st.info("아직 자산 회수(매도) 내역이 없습니다.")
        else:
            total_realized = sum([r['profit_amt'] for r in hist_res.data])
            win_count = sum([1 for r in hist_res.data if r['profit_amt'] > 0])
            win_rate = (win_count / len(hist_res.data)) * 100 if len(hist_res.data) > 0 else 0
            
            h1, h2 = st.columns(2)
            h1.metric("누적 가치 실현액", f"{total_realized:,} 원")
            h2.metric("매매 승률", f"{win_rate:.1f} %")
            
            df_hist = pd.DataFrame(hist_res.data)
            df_hist['created_at'] = pd.to_datetime(df_hist['created_at']) + pd.Timedelta(hours=9)
            df_hist['created_at'] = df_hist['created_at'].dt.strftime('%Y-%m-%d %H:%M')
            df_hist = df_hist[['created_at', 'name', 'buy_price', 'sell_price', 'qty', 'profit_amt', 'profit_pct']]
            df_hist.columns = ['회수일시', '기업명', '진입단가', '회수단가', '보유지분', '실현이익', '수익률']
            
            df_hist['진입단가'] = df_hist['진입단가'].apply(lambda x: f"₩ {int(x):,}")
            df_hist['회수단가'] = df_hist['회수단가'].apply(lambda x: f"₩ {int(x):,}")
            df_hist['실현이익'] = df_hist['실현이익'].apply(lambda x: f"₩ {int(x):,}")
            df_hist['수익률'] = df_hist['수익률'].apply(lambda x: f"{x:+.2f} %")
            
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
            st.dataframe(df_log, width="stretch")
