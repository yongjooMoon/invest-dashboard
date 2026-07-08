"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
import numpy as np
import json
import requests
import re
import html  # 🛡️ XSS 방어용 파이썬 내장 라이브러리 추가
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import plotly.graph_objects as go
import FinanceDataReader as fdr
import time # 로딩 애니메이션 지연용

from quant_core import (
    load_price_from_db, load_screening_result,
    now_kst, fetch_naver_fundamental,
    load_fundamental_from_db, save_fundamental_to_db,
    calc_quant_metrics
)

# ══════════════════════════════════════════
# [Helper] 공통 데이터 & 종목 캐싱
# ══════════════════════════════════════════
def load_portfolio_data(supabase):
    holdings, trades, history = [], [], []
    try:
        r1 = supabase.table("quant_screening_cache").select("results").eq("id", 11).execute()
        if r1.data: holdings = json.loads(r1.data[0]["results"])
        r2 = supabase.table("quant_screening_cache").select("results").eq("id", 12).execute()
        if r2.data: trades = json.loads(r2.data[0]["results"])
        r3 = supabase.table("quant_screening_cache").select("results").eq("id", 13).execute()
        if r3.data: history = json.loads(r3.data[0]["results"])
    except:
        pass
    return holdings, trades, history

@st.cache_data(ttl=3600)
def load_krx_list_from_db(_supabase):
    """UI에서 외부 API 호출을 배제하고 DB(크론이 수집한 캐시)에서 종목 마스터를 안전하게 로드"""
    try:
        res = _supabase.table("quant_screening_cache").select("results").eq("id", 99).execute()
        if res.data:
            krx_data = json.loads(res.data[0]["results"])
            return pd.DataFrame(krx_data)
    except Exception as e:
        pass
    return pd.DataFrame(columns=["Symbol", "Name", "SearchStr"])

@st.cache_data(ttl=1800)
def load_kospi_cached(start_date_str):
    """KOSPI 지수 데이터는 30분 캐시. 매 rerun마다 재조회되지 않도록 함."""
    return fdr.DataReader('KS11', start_date_str)

def calculate_exit_risk(curr, entry, stop):
    if curr <= 0 or entry <= 0 or stop <= 0: return 0
    buffer = entry - stop if entry > stop else curr * 0.15
    if buffer <= 0: buffer = 1
    distance = curr - stop
    risk = 100 - (distance / buffer * 100)
    return max(0, min(100, int(risk)))

def format_marcap(marcap_100m):
    if marcap_100m is None or pd.isna(marcap_100m):
        return "N/A"
    try:
        val = float(marcap_100m)
        if val == 0: return "0억"

        is_negative = val < 0
        abs_val = abs(val)

        if abs_val >= 10000:
            jo = int(abs_val // 10000)
            eok = int(abs_val % 10000)
            sign = "-" if is_negative else ""
            if eok > 0:
                return f"{sign}{jo}조 {eok:,}억"
            return f"{sign}{jo}조"
        else:
            return f"{int(val):,}억"
    except:
        return "N/A"

def get_ui_financial_extras(symbol, fund):
    """quant_core.py의 원래 스크래퍼가 가져오지 않는 UI 리포트 전용 지표들을 보완"""
    try:
        url = f"https://finance.naver.com/item/main.naver?code={symbol}"
        res = requests.get(url, headers={'User-agent': 'Mozilla/5.0'}, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')

        if fund.get('sector') is None:
            upjong_elem = soup.select_one("a[href*='type=upjong']")
            if upjong_elem:
                fund['sector'] = upjong_elem.text.strip()

        table = soup.select_one("div.cop_analysis table")
        if table:
            for tr in table.select("tbody tr"):
                th = tr.select_one("th")
                if not th: continue
                label = th.text.strip()
                tds = tr.select("td")
                vals = []
                for td in tds:
                    clean = re.sub(r"[^\d.\-]", "", td.text)
                    if clean and clean != '-': vals.append(float(clean))
                    else: vals.append(None)

                valid_vals = [v for v in vals if v is not None]
                if not valid_vals: continue
                recent_val = valid_vals[-1]

                if "영업이익률" in label and fund.get('op_margin') is None: fund['op_margin'] = recent_val
                elif "ROA" in label and fund.get('roa') is None: fund['roa'] = recent_val
                elif "PER" in label and fund.get('per') is None: fund['per'] = recent_val
                elif "PBR" in label and fund.get('pbr') is None: fund['pbr'] = recent_val

        if fund.get('marcap_억') is None:
            marcap_elem = soup.select_one("#_market_sum")
            if marcap_elem:
                txt = marcap_elem.text.strip().replace(',', '').replace('\t', '').replace('\n', '')
                if '조' in txt:
                    parts = txt.split('조')
                    jo = int(re.sub(r'\D', '', parts[0])) if parts[0] else 0
                    eok_str = re.sub(r'\D', '', parts[1]) if len(parts)>1 else ''
                    eok = int(eok_str) if eok_str else 0
                    fund['marcap_억'] = jo * 10000 + eok
                else:
                    eok_str = re.sub(r'\D', '', txt)
                    fund['marcap_억'] = int(eok_str) if eok_str else 0
    except:
        pass
    return fund

def live_evaluate_stock(supabase, symbol, name=""):
    df = fdr.DataReader(symbol, (now_kst() - timedelta(days=300)).strftime('%Y-%m-%d'))
    if df.empty: return None, {}, 0, {}

    fund = load_fundamental_from_db(supabase, symbol)
    if not fund: fund = {}

    required_keys = ['op_margin', 'roa', 'per', 'pbr', 'marcap_억', 'sector']
    needs_update = not fund or any(fund.get(k) is None for k in required_keys)

    if needs_update:
        scraped_fund = fetch_naver_fundamental(symbol) 
        for k, v in scraped_fund.items():
            if v is not None:
                fund[k] = v
        
        fund = get_ui_financial_extras(symbol, fund)
    
        if fund:
            try:
                save_fundamental_to_db(supabase, symbol, name, fund)
            except Exception as e:
                print(f"DB 저장 오류: {e}")

    c_net = fund.get('net_income_cur')
    p_net = fund.get('net_income_prev')
    if c_net is not None and p_net is not None and p_net != 0:
        fund['net_income_yoy'] = ((c_net - p_net) / abs(p_net)) * 100
        
    c_rev = fund.get('revenue_cur')
    p_rev = fund.get('revenue_prev')
    if c_rev is not None and p_rev is not None and p_rev != 0:
        fund['revenue_yoy'] = ((c_rev - p_rev) / abs(p_rev)) * 100

    c_op = fund.get('op_profit_cur')
    if fund.get('op_margin') is None and c_op is not None and c_rev:
        fund['op_margin'] = (c_op / c_rev) * 100

    metrics = calc_quant_metrics(df, fund)
    
    if "ma20" not in metrics or metrics.get("ma20", 0) == 0:
        return df, fund, 0, {}

    curr = df['Close'].iloc[-1]

    f_growth = metrics["growth_composite"] > 0
    f_mdd    = metrics["mdd"] >= metrics["dynamic_mdd_limit"]
    f_liq    = metrics["liquidity_20d"] >= 50
    f_trend  = (curr > metrics["ma20"]) and (metrics["ma20"] > metrics["ma60"])
    f_break  = curr >= (metrics["high_60d"] * 0.90)
    f_vol    = metrics["vol_5d"] > (metrics["vol_60d"] * 1.5)

    gates = {
        'A': {'name': 'Growth Composite', 'pass': f_growth, 'reason': f"Comp {metrics['growth_composite']:+.1f}%"},
        'B': {'name': 'Dynamic MDD', 'pass': f_mdd, 'reason': f"MDD {metrics['mdd']:.1f}% (Limit: {metrics['dynamic_mdd_limit']:.1f}%)"},
        'C': {'name': 'Liquidity', 'pass': f_liq, 'reason': f"{metrics['liquidity_20d']:,.0f}억"},
        'D': {'name': 'Trend Alignment', 'pass': f_trend, 'reason': "Price > 20MA > 60MA" if f_trend else "추세 미달"},
        'E': {'name': 'Price Breakout', 'pass': f_break, 'reason': f"고점대비 {(curr/metrics['high_60d'])*100:.1f}%" if metrics.get('high_60d') else "-"},
        'F': {'name': 'Volume Surge', 'pass': f_vol, 'reason': f"Vol {metrics['vol_5d']/metrics['vol_60d']:.1f}x 급증" if metrics.get('vol_60d') else "-"}
    }

    pass_count = sum([1 for g in gates.values() if g['pass']])
    
    mom = ((curr - metrics["ma60"]) / metrics["ma60"] * 100) if metrics["ma60"] > 0 else 0
    net_yoy = metrics.get("net_yoy", 0)
    factor_score = min(99.9, max(0, (pass_count/6 * 50) + min(25, max(0, net_yoy/5)) + min(25, max(0, mom))))

    return df, fund, factor_score, gates

# ══════════════════════════════════════════
# [Component] Popovers & Shared Renderers
# ══════════════════════════════════════════
def render_exit_risk_content(h, supabase):
    curr = h.get("current_price", 0)
    entry = h.get("entry_price", curr)
    stop = h.get("stop_price", entry * 0.85)
    ret = h.get("return_rate", 0.0)

    if "price_cache" not in st.session_state: 
        st.session_state.price_cache = {}
    
    if h['symbol'] not in st.session_state.price_cache:
        st.session_state.price_cache[h['symbol']] = load_price_from_db(supabase, h['symbol'])
        
    df = st.session_state.price_cache[h['symbol']]
    ts_risk, ma_risk = 0.0, 0.0

    if not df.empty and len(df) >= 20:
        high = df.get('High', df['Close'])
        low = df.get('Low', df['Close'])
        prev_close = df['Close'].shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean().iloc[-1]
        ma20 = df['Close'].iloc[-20:].mean()

        try:
            entry_date_str = h.get('entry_date', now_kst().strftime("%Y-%m-%d"))
            entry_date = pd.to_datetime(entry_date_str).tz_localize(None)
        except:
            entry_date = now_kst().replace(tzinfo=None)

        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        df_held = df[df.index >= entry_date]
        highest_close = df_held['Close'].max() if not df_held.empty else curr

        trailing_stop = highest_close - (2.5 * atr20)
        if curr > 0 and trailing_stop > 0:
            ts_dist = curr - trailing_stop
            ts_risk = max(0, min(100, 100 - (ts_dist / (curr * 0.15) * 100)))

        if curr > 0 and ma20 > 0:
            ma_dist = curr - ma20
            ma_risk = max(0, min(100, 100 - (ma_dist / (curr * 0.10) * 100)))

    exit_risk = calculate_exit_risk(curr, entry, stop)

    st.markdown(
        """<style>
        div[data-testid="stPopoverBody"] { min-width: 350px !important; }
        </style>""", unsafe_allow_html=True
    )

    safe_name = html.escape(str(h.get('name') or ''))
    st.subheader(f"🚨 {safe_name} Risk 분석")
    st.write(f"**현재가:** ₩{curr:,.0f} &nbsp;|&nbsp; **손절가:** ₩{stop:,.0f}")

    st.divider()

    st.markdown(f"**OVERALL EXIT PROXIMITY : {int(exit_risk)}%**")
    st.progress(int(exit_risk))
    st.write("")

    st.markdown(f"**Trailing Stop (ATR) : {int(ts_risk)}%**")
    st.progress(int(ts_risk))
    st.write("")

    st.markdown(f"**Trend Break (MA20) : {int(ma_risk)}%**")
    st.progress(int(ma_risk))

    st.divider()
    c1, c2 = st.columns(2)
    c1.metric("진입가 (Entry)", f"₩{entry:,.0f}")
    c2.metric("보유 수익률 (P&L)", f"{ret:+.2f}%")


def render_single_gauge(score):
    fig = go.Figure()
    fig.add_trace(go.Indicator(
        mode = "gauge+number", value = score,
        number = {'font': {'color': '#00B464', 'size': 45}, 'valueformat': '.1f'},
        title = {'text': "퀀트 랭킹 스코어", 'font': {'color': '#AEC1D4', 'size': 14}},
        gauge = {'axis': {'range': [None, 100], 'visible': False}, 'bar': {'color': "#00B464", 'thickness': 0.8}, 'bgcolor': "rgba(255,255,255,0.05)", 'shape': "angular"}
    ))
    fig.update_layout(height=220, margin=dict(l=20, r=20, t=30, b=10), paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

def render_detailed_report_content(sel, df_price=None, fund=None, factor_score=None, gates=None):
    curr = sel.get('current_price', 0)
    ret_1m = sel.get('ret_1m', 0)

    # 🛡️ XSS 방어: 안전하게 문자열 변환 및 이스케이프
    safe_name = html.escape(str(sel.get('name') or ''))
    safe_symbol = html.escape(str(sel.get('symbol') or ''))
    safe_market = html.escape(str(sel.get('market') or 'KOSPI'))
    safe_sector = html.escape(str(sel.get('sector') or ''))

    sector_str = f" &nbsp;|&nbsp; <span style='color:#F8B12A;'>{safe_sector}</span>" if safe_sector and safe_sector != "미분류" else ""

    st.markdown(f"## {safe_name} <span style='font-size:18px; color:#AEC1D4;'>{safe_symbol} &nbsp;|&nbsp; {safe_market}</span>{sector_str}", unsafe_allow_html=True)
    st.markdown(f"<h1>{curr:,.0f} 원 <span style='font-size:20px; color:{'#F04452' if ret_1m>0 else '#3182F6'};'>{ret_1m:+.2f}% (1M)</span></h1>", unsafe_allow_html=True)
    st.divider()

    if factor_score is None: factor_score = sel.get('factor_score', 0)
    
    if gates is None:
        gates_data = sel.get("filter_details", {})
        if not gates_data or "Growth Composite" not in gates_data:
            gates = {
                'A': {'name': 'Growth Composite', 'pass': False, 'reason': '-'}, 
                'B': {'name': 'Dynamic MDD', 'pass': False, 'reason': '-'},
                'C': {'name': 'Liquidity', 'pass': False, 'reason': '-'}, 
                'D': {'name': 'Trend Alignment', 'pass': False, 'reason': '-'},
                'E': {'name': 'Price Breakout', 'pass': False, 'reason': '-'}, 
                'F': {'name': 'Volume Surge', 'pass': False, 'reason': '-'}
            }
        else:
            gates = {
                'A': {'name': 'Growth Composite', 'pass': gates_data.get("Growth Composite", {}).get("pass", False), 'reason': gates_data.get("Growth Composite", {}).get("reason", "-")},
                'B': {'name': 'Dynamic MDD', 'pass': gates_data.get("Dynamic MDD", {}).get("pass", False), 'reason': gates_data.get("Dynamic MDD", {}).get("reason", "-")},
                'C': {'name': 'Liquidity', 'pass': gates_data.get("Liquidity", {}).get("pass", False), 'reason': gates_data.get("Liquidity", {}).get("reason", "-")},
                'D': {'name': 'Trend Alignment', 'pass': gates_data.get("Trend Alignment", {}).get("pass", False), 'reason': gates_data.get("Trend Alignment", {}).get("reason", "-")},
                'E': {'name': 'Price Breakout', 'pass': gates_data.get("Price Breakout", {}).get("pass", False), 'reason': gates_data.get("Price Breakout", {}).get("reason", "-")},
                'F': {'name': 'Volume Surge', 'pass': gates_data.get("Volume Surge", {}).get("pass", False), 'reason': gates_data.get("Volume Surge", {}).get("reason", "-")}
            }

    total_pass = sum([1 for g in gates.values() if g['pass']])

    c_header, c_gauge = st.columns([3, 2])
    with c_header:
        st.markdown("### ⚡ Quant Scores")
        c1, c2 = st.columns(2)
        c1.metric("실시간 랭킹 스코어", f"{factor_score:.2f}점")
        c2.metric("현재시점 생존 필터", f"{total_pass} / 6")
        st.info("💡 과거 배치(Cron) 시점엔 6/6 통과였어도, **현재 실시간 주가 변동**에 따라 지표가 하락(5/6 등)할 수 있습니다.")

    with c_gauge:
        render_single_gauge(factor_score)

    st.markdown("##### Entry Gates (6 conditions)")
    cols = st.columns(6)
    labels = ['A', 'B', 'C', 'D', 'E', 'F']
    for idx, (col, key) in enumerate(zip(cols, gates.keys())):
        g = gates[key]
        passed = g['pass']
        color = "#00B464" if passed else "#333333"
        txt_color = "white" if passed else "#888888"
        
        # 🛡️ XSS 방어 적용
        safe_g_name = html.escape(str(g.get('name') or ''))
        
        with col:
            st.markdown(f"""
            <div style="background-color:#1E2329; border:1px solid {color}; border-radius:6px; padding:10px; height:85px;">
                <div style="display:flex; justify-content:space-between; font-weight:bold; color:{txt_color}; font-size:13px; margin-bottom:8px;">
                    <span>{labels[idx]}</span> <span style="font-size:11px;">{'✔️' if passed else '❌'}</span>
                </div>
                <div style="height:3px; background-color:{color}; border-radius:2px; margin-bottom:8px;"></div>
                <div style="font-size:10px; color:#AEC1D4; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="{safe_g_name}">{safe_g_name}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
    st.markdown("### 📊 Financials & Valuation")
    if fund is None: fund = sel

    def safe_fmt(val, is_pct=False, is_eok=False):
        if val is None or pd.isna(val): return "N/A"
        try:
            val = float(val)
            if is_pct: return f"{val:+.2f}%" if val < 0 else f"{val:.2f}%"
            if is_eok: return format_marcap(val)
            return f"{val:,.2f}"
        except: return "N/A"

    with st.container(border=True):
        st.markdown("**재무 실적**")
        r1, r2, r3, r4 = st.columns(4)
        r1.caption("매출액"); r1.markdown(f"**{safe_fmt(fund.get('revenue_cur'), is_eok=True)}**")
        r2.caption("영업이익"); r2.markdown(f"**{safe_fmt(fund.get('op_profit_cur'), is_eok=True)}**")
        r3.caption("당기순이익"); r3.markdown(f"**{safe_fmt(fund.get('net_income_cur'), is_eok=True)}**")
        r4.caption("영업이익률"); r4.markdown(f"**{safe_fmt(fund.get('op_margin'), is_pct=True)}**")

        st.divider()
        st.markdown("**수익성 및 성장성 (YoY)**")
        r5, r6, r7, r8 = st.columns(4)
        r5.caption("순이익 성장률"); r5.markdown(f"**{safe_fmt(fund.get('net_income_yoy'), is_pct=True)}**")
        r6.caption("매출 성장률"); r6.markdown(f"**{safe_fmt(fund.get('revenue_yoy'), is_pct=True)}**")
        r7.caption("ROE"); r7.markdown(f"**<span style='color:#F04452'>{safe_fmt(fund.get('roe'), is_pct=True)}</span>**", unsafe_allow_html=True)
        r8.caption("ROA"); r8.markdown(f"**{safe_fmt(fund.get('roa'), is_pct=True)}**")

        st.divider()
        st.markdown("**밸류에이션 및 건전성**")
        r9, r10, r11, r12 = st.columns(4)
        r9.caption("시가총액"); r9.markdown(f"**{format_marcap(fund.get('marcap_억'))}**")
        r10.caption("PER"); r10.markdown(f"**{safe_fmt(fund.get('per'))} 배**")
        r11.caption("PBR"); r11.markdown(f"**{safe_fmt(fund.get('pbr'))} 배**")
        r12.caption("부채비율"); r12.markdown(f"**{safe_fmt(fund.get('debt_ratio'), is_pct=True)}**")

    st.markdown("### 📈 가격 차트 (Price History)")
    if df_price is not None and not df_price.empty:
        st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
    else:
        st.info("가격 데이터가 로드되지 않았습니다.")

@st.dialog("📈 퀀트 평가 상세 리포트", width="large")
def show_detail_dialog(sel, supabase):
    with st.spinner("캐시된 데이터를 불러오는 중..."):
        
        if 'filter_details' not in sel or sel.get('factor_score', 0) == 0:
            c_list, w_list, _ = load_screening_result(supabase)
            found = False
            for item in c_list + w_list:
                if item['symbol'] == sel['symbol']:
                    sel.update(item)
                    found = True
                    break
            
            if not found:
                fund_db = load_fundamental_from_db(supabase, sel['symbol'])
                if fund_db:
                    sel.update(fund_db)

        original_score = sel.get('factor_score', 0)

        if "price_cache" not in st.session_state: 
            st.session_state.price_cache = {}
        if sel['symbol'] not in st.session_state.price_cache:
            df_price = load_price_from_db(supabase, sel['symbol'])
            if df_price.empty:
                df_price = fdr.DataReader(sel['symbol'], (now_kst() - timedelta(days=300)).strftime('%Y-%m-%d'))
            st.session_state.price_cache[sel['symbol']] = df_price
            
        df_price = st.session_state.price_cache[sel['symbol']]

        if 'ret_1m' not in sel or sel['ret_1m'] == 0:
            if df_price is not None and len(df_price) >= 21:
                sel['ret_1m'] = (df_price['Close'].iloc[-1] - df_price['Close'].iloc[-21]) / df_price['Close'].iloc[-21] * 100

    render_detailed_report_content(sel, df_price=df_price, fund=sel, factor_score=original_score, gates=None)

# 🌟 모바일에서도 테이블 구조가 유지되도록 카드를 랜더링해주는 헬퍼 함수 🌟
def render_watchlist_grid(items, title, color_code, anchor_id):
    st.markdown(f"#### {title}")
    
    # 해당 테이블에만 독립적인 가로 스크롤 CSS를 입히기 위해 고유 앵커 ID 주입
    st.markdown(f'<div id="{anchor_id}"></div>', unsafe_allow_html=True)
    
    clicked_item = None
    
    with st.container():
        c1, c2, c3, c4, c5, c6 = st.columns([1, 2.5, 2, 1.5, 1.5, 2])
        c1.markdown("<div class='grid-header'>순위</div>", unsafe_allow_html=True)
        c2.markdown("<div class='grid-header'>종목명</div>", unsafe_allow_html=True)
        c3.markdown("<div class='grid-header'>현재가</div>", unsafe_allow_html=True)
        c4.markdown("<div class='grid-header'>통과</div>", unsafe_allow_html=True)
        c5.markdown("<div class='grid-header'>랭킹점수</div>", unsafe_allow_html=True)
        c6.markdown("<div class='grid-header'>액션</div>", unsafe_allow_html=True)

        for idx, w in enumerate(items):
            curr = w.get('current_price', 0)
            
            # 🛡️ XSS 방어
            safe_w_name = html.escape(str(w.get('name') or ''))

            c1, c2, c3, c4, c5, c6 = st.columns([1, 2.5, 2, 1.5, 1.5, 2])
            c1.markdown(f"<div class='grid-row'>{idx+1}</div>", unsafe_allow_html=True)
            c2.markdown(f"<div class='grid-row' style='font-weight:bold;'>{safe_w_name}</div>", unsafe_allow_html=True)
            c3.markdown(f"<div class='grid-row'>₩{curr:,}</div>", unsafe_allow_html=True)
            c4.markdown(f"<div class='grid-row'>{w.get('total_pass', 0)}/6</div>", unsafe_allow_html=True)
            c5.markdown(f"<div class='grid-row' style='color:{color_code}; font-weight:bold;'>{w.get('factor_score', 0):.2f}점</div>", unsafe_allow_html=True)
            with c6:
                if st.button("📊 리포트", key=f"w_det_{w['symbol']}", use_container_width=True):
                    clicked_item = w
                    
    return clicked_item

# ══════════════════════════════════════════
# [Main Entry Point]
# ══════════════════════════════════════════
def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    c1, c2, c3 = st.columns([8.2, 0.8, 1.0]) 
    with c1:
        st.title("📡 퀀트투자")
    with c3:
        st.markdown("<div style='margin-top: 26px;'></div>", unsafe_allow_html=True)
        if st.button("✨ Refresh", use_container_width=True):
            loading_overlay = st.empty()
            overlay_html = (
                "<style>"
                ".custom-overlay { position: fixed !important; top: 0px !important; left: 0px !important; right: 0px !important; bottom: 0px !important; width: 100vw !important; height: 100vh !important; background: rgba(3, 7, 18, 0.8) !important; backdrop-filter: blur(12px) !important; -webkit-backdrop-filter: blur(12px) !important; z-index: 9999999 !important; display: flex !important; flex-direction: column !important; align-items: center !important; justify-content: center !important; pointer-events: all !important; }"
                ".chart-box { position: relative; width: 160px; height: 130px; margin-bottom: 25px; }"
                ".chart-line { fill: none; stroke: #F04452; stroke-width: 6; stroke-linecap: round; stroke-linejoin: round; stroke-dasharray: 400; stroke-dashoffset: 400; animation: drawLine 0.9s cubic-bezier(0.4, 0, 0.2, 1) forwards; filter: drop-shadow(0px 0px 8px rgba(240, 68, 82, 0.7)); }"
                ".chart-point { fill: #F04452; opacity: 0; animation: fadeIn 0.2s ease-out 0.8s forwards; filter: drop-shadow(0px 0px 12px rgba(240, 68, 82, 1)); }"
                ".chart-grid { stroke: rgba(255,255,255,0.08); stroke-width: 1.5; stroke-dasharray: 4 6; }"
                "@keyframes drawLine { to { stroke-dashoffset: 0; } }"
                "@keyframes fadeIn { to { opacity: 1; } }"
                ".refresh-title { color: #FFFFFF !important; font-size: 24px !important; font-weight: 900 !important; letter-spacing: 5px !important; margin: 0 0 10px 0 !important; text-shadow: 0 0 15px rgba(255,255,255,0.3) !important; }"
                ".refresh-desc { color: #F04452 !important; font-size: 14px !important; font-weight: 600 !important; letter-spacing: 1.5px !important; margin: 0 !important; }"
                "</style>"
                "<div class='custom-overlay'>"
                "<div class='chart-box'>"
                "<svg viewBox='0 0 160 130' style='width:100%; height:100%; overflow:visible;'>"
                "<line x1='0' y1='35' x2='160' y2='35' class='chart-grid' />"
                "<line x1='0' y1='85' x2='160' y2='85' class='chart-grid' />"
                "<line x1='0' y1='130' x2='160' y2='130' class='chart-grid' />"
                "<path d='M 0,120 L 35,90 L 70,105 L 115,45 L 155,10' class='chart-line' />"
                "<circle cx='155' cy='10' r='7' class='chart-point' />"
                "</svg>"
                "</div>"
                "<div class='refresh-title'>SYNCHRONIZING</div>"
                "<div class='refresh-desc'>최신 시장 데이터를 퀀트 엔진에 반영 중입니다 🚀</div>"
                "</div>"
            )
            loading_overlay.markdown(overlay_html, unsafe_allow_html=True)
            
            start_time = time.time()
            
            for key in ["quant_portfolio", "quant_screening", "price_cache"]:
                if key in st.session_state:
                    del st.session_state[key]
            
            st.session_state.quant_portfolio = load_portfolio_data(supabase)
            st.session_state.quant_screening = load_screening_result(supabase)
            
            elapsed = time.time() - start_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            
            st.rerun()

    if "quant_portfolio" not in st.session_state:
        with st.spinner("💼 포트폴리오 데이터를 최초 1회 로드 중입니다..."):
            st.session_state.quant_portfolio = load_portfolio_data(supabase)
            
    if "quant_screening" not in st.session_state:
        with st.spinner("👀 스크리닝 데이터를 최초 1회 로드 중입니다..."):
            st.session_state.quant_screening = load_screening_result(supabase)

    holdings, trades, history = st.session_state.quant_portfolio
    confirmed, watchlist, last_updated = st.session_state.quant_screening

    holding_syms = set([h['symbol'] for h in holdings])
    filtered_confirmed = [c for c in confirmed if c['symbol'] not in holding_syms]
    filtered_watchlist = [w for w in watchlist if w['symbol'] not in holding_syms]

    st.markdown("""
    <style>
    .grid-header { font-size: 13px; font-weight: bold; color: #8B95A1; border-bottom: 1px solid #333; padding-bottom: 8px; margin-bottom: 8px; }
    .grid-row { padding-top: 10px; padding-bottom: 10px; font-size: 14px; border-bottom: 1px solid #1E2329; display: flex; align-items: center;}
    .popover-btn > button { padding: 0 !important; background: none !important; border: none !important; color: #AEC1D4 !important; }
    
    div[data-testid="stMarkdownContainer"]:has(#portfolio-holdings-anchor, #watchlist-confirmed-anchor, #watchlist-reserve-anchor) + div[data-testid="stVerticalBlock"] {
        overflow-x: auto !important;
        width: 100% !important;
        display: block !important;
        padding-bottom: 12px !important;
    }
    
    div[data-testid="stMarkdownContainer"]:has(#portfolio-holdings-anchor, #watchlist-confirmed-anchor, #watchlist-reserve-anchor) + div[data-testid="stVerticalBlock"] div[data-testid="stHorizontalBlock"] {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        min-width: 780px !important; 
        width: 100% !important;
    }

    div[data-testid="stMarkdownContainer"]:has(#portfolio-holdings-anchor, #watchlist-confirmed-anchor, #watchlist-reserve-anchor) + div[data-testid="stVerticalBlock"] div[data-testid="column"] {
        flex: 1 1 auto !important;
        min-width: unset !important;
        width: auto !important;
    }
    
    div[data-testid="stMarkdownContainer"]:has(#portfolio-holdings-anchor, #watchlist-confirmed-anchor, #watchlist-reserve-anchor) + div[data-testid="stVerticalBlock"]::-webkit-scrollbar {
        height: 6px;
    }
    div[data-testid="stMarkdownContainer"]:has(#portfolio-holdings-anchor, #watchlist-confirmed-anchor, #watchlist-reserve-anchor) + div[data-testid="stVerticalBlock"]::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.15) !important;
        border-radius: 3px !important;
    }
    </style>
    """, unsafe_allow_html=True)

    tab_port, tab_watch, tab_hist, tab_docs = st.tabs([
        f"Portfolio ({len(holdings)})",
        f"Watchlist ({len(filtered_confirmed) + len(filtered_watchlist)})",
        "매도 히스토리 (History)",
        "📖 Algo Whitepaper"
    ])

    # ────────────────────────────────────────────────────────
    # 탭 1: 포트폴리오
    # ────────────────────────────────────────────────────────
    with tab_port:
        with st.container(border=True):
            if holdings:
                max_price = max([h.get("current_price", 0) for h in holdings])
                total_stocks = len(holdings)
                total_seed = max_price * total_stocks
                
                st.caption("Equal-Weight Min Seed (동일비중 최소 시드)")
                st.markdown(f"## {total_seed:,.0f} 원")
                st.caption(f"종목당 {max_price:,.0f}원 기준 × {total_stocks}종목")
            else:
                st.caption("Equal-Weight Min Seed (동일비중 최소 시드)")
                st.markdown("## 0 원")
                st.caption("보유 중인 종목이 없습니다.")

        st.markdown(f"<h4 style='margin-bottom:10px; padding-top:4px;'>Holdings ({len(holdings)})</h4>", unsafe_allow_html=True)

        if holdings:
            st.markdown('<div id="portfolio-holdings-anchor"></div>', unsafe_allow_html=True)
            with st.container():
                c1, c2, c3, c4, c5, c6 = st.columns([2, 1.5, 1.5, 1.5, 1.5, 2.5])
                c1.markdown("<div class='grid-header'>종목명</div>", unsafe_allow_html=True)
                c2.markdown("<div class='grid-header'>진입가</div>", unsafe_allow_html=True)
                c3.markdown("<div class='grid-header'>현재가</div>", unsafe_allow_html=True)
                c4.markdown("<div class='grid-header'>수익률(P&L)</div>", unsafe_allow_html=True)
                c5.markdown("<div class='grid-header'>Exit Risk</div>", unsafe_allow_html=True)
                c6.markdown("<div class='grid-header'>상세 액션</div>", unsafe_allow_html=True)

                dialog_trigger = None
                dialog_payload = None

                for h in holdings:
                    curr = h.get("current_price", 0)
                    entry = h.get("entry_price", curr)
                    stop = h.get("stop_price", entry * 0.85)
                    ret = h.get("return_rate", 0.0)
                    exit_risk = calculate_exit_risk(curr, entry, stop)

                    safe_h_name = html.escape(str(h.get('name') or ''))

                    c1, c2, c3, c4, c5, c6 = st.columns([2, 1.5, 1.5, 1.5, 1.5, 2.5])
                    c1.markdown(f"<div class='grid-row' style='font-weight:bold;'>{safe_h_name}</div>", unsafe_allow_html=True)
                    c2.markdown(f"<div class='grid-row'>₩{entry:,.0f}</div>", unsafe_allow_html=True)
                    c3.markdown(f"<div class='grid-row'>₩{curr:,.0f}</div>", unsafe_allow_html=True)

                    pnl_color = "#F04452" if ret > 0 else ("#3182F6" if ret < 0 else "#AEC1D4")
                    c4.markdown(f"<div class='grid-row' style='color:{pnl_color}; font-weight:bold;'>{ret:+.2f}%</div>", unsafe_allow_html=True)

                    risk_color = "#E6A23C" if exit_risk < 70 else "#F04452"
                    c5.markdown(f"<div class='grid-row' style='color:{risk_color}; font-weight:bold;'>{exit_risk}%</div>", unsafe_allow_html=True)

                    with c6:
                        bc1, bc2 = st.columns(2)
                        with bc1:
                            with st.popover("🚨 Risk", use_container_width=True):
                                render_exit_risk_content(h, supabase)
                        with bc2:
                            if st.button("📊 리포트", key=f"det_{h['symbol']}", use_container_width=True):
                                dialog_trigger = "detail"
                                dialog_payload = h

            if dialog_trigger == "detail" and dialog_payload:
                show_detail_dialog(dialog_payload, supabase)

        else:
            st.info("현재 보유 중인 종목이 없습니다.")

        st.divider()
        st.markdown("#### KOSPI 대비 포트폴리오 성과 (Alpha - 실현수익 기준)")
        st.caption("※ 보유 중인 종목의 미실현 수익은 제외되며, 매도(Exit)가 완료된 종목의 실현 수익률만 차트에 반영됩니다.")

        end_date = now_kst()
        start_date = end_date - timedelta(days=30)

        df_kospi = load_kospi_cached(start_date.strftime('%Y-%m-%d'))
        if not df_kospi.empty:
            df_kospi['kospi_cum'] = df_kospi['Close'].pct_change().fillna(0).cumsum() * 100
        else:
            df_kospi = pd.DataFrame(columns=['Close', 'kospi_cum'])

        chart_df = pd.DataFrame(index=df_kospi.index)
        chart_df['KOSPI'] = df_kospi['kospi_cum']

        sell_trades = [t for t in trades if t.get('type') == 'SELL']
        if sell_trades:
            t_df = pd.DataFrame(sell_trades)
            t_df['date'] = pd.to_datetime(t_df['trade_date']).dt.tz_localize(None)

            daily_ret = t_df.groupby('date')['return_rate'].mean()
            df_hist = pd.DataFrame({'sold_return': daily_ret})

            chart_df = chart_df.join(df_hist['sold_return'], how='left')
            chart_df['sold_return'] = chart_df['sold_return'].fillna(0)
            chart_df['Portfolio'] = chart_df['sold_return'].cumsum()

            cum_ret = chart_df['Portfolio'].iloc[-1]
            day_ret = chart_df['sold_return'].iloc[-1]
        else:
            chart_df['Portfolio'] = 0.0
            cum_ret = 0.0
            day_ret = 0.0

        k_cum_ret = chart_df['KOSPI'].iloc[-1] if not chart_df['KOSPI'].empty else 0.0
        k_day_ret = df_kospi['Close'].pct_change().iloc[-1] * 100 if not df_kospi.empty else 0.0
        alpha = cum_ret - k_cum_ret

        col1, col2, col3 = st.columns(3)
        col1.metric("Portfolio 실현 누적", f"{cum_ret:+.2f}%", f"Day {day_ret:+.2f}%")
        col2.metric("KOSPI 누적", f"{k_cum_ret:+.2f}%", f"Day {k_day_ret:+.2f}%")
        col3.metric("Alpha (초과수익)", f"{alpha:+.2f}%")

        if not chart_df.empty:
            chart_df['Alpha'] = chart_df['Portfolio'] - chart_df['KOSPI']
            
            # 💡 [색상 고정] 마이너스라도 포트폴리오 메인 테마 색상인 '빨강'으로 통일
            port_color = '#F04452'
            bg_rgba = 'rgba(240, 68, 82, 0.1)'
            
            # 💡 [소수점 버그 픽스] 자바스크립트의 무한 소수점 오류 방지를 위해, 
            # Portfolio 값 자체를 Python 단계에서 문자열(String) 두 자리로 완벽 포맷팅해서 넘겨줍니다!
            port_str = chart_df['Portfolio'].map('{:+.2f}%'.format)
            kospi_str = chart_df['KOSPI'].map('{:+.2f}%'.format)
            alpha_str = chart_df['Alpha'].map('{:+.2f}%'.format)
            
            # Alpha(초과수익)는 직관성을 위해 +면 빨강, -면 파랑으로 유지
            alpha_color = chart_df['Alpha'].apply(lambda x: '#F04452' if x >= 0 else '#3182F6')
            
            # 툴팁에 표시될 모든 텍스트 요소를 커스텀 데이터에 담아서 넘깁니다.
            custom_data = np.column_stack((port_str, kospi_str, alpha_str, alpha_color))
            fig = go.Figure()
            
            fig.add_trace(go.Scatter(
                x=chart_df.index, y=chart_df['KOSPI'], 
                mode='lines', name='KOSPI', 
                line=dict(color='#475569', width=2, dash='dot', shape='spline', smoothing=1.0), 
                hoverinfo='skip'
            ))
            
            # 💡 [HTML 버그 픽스] div, hr 등 Plotly가 지원하지 않는 복잡한 태그를 제거하고 심플하고 텍스트 기반의 선(─)으로 교체
            fig.add_trace(go.Scatter(
                x=chart_df.index, y=chart_df['Portfolio'], 
                mode='lines', name='Portfolio', 
                line=dict(color=port_color, width=3.5, shape='spline', smoothing=1.0), 
                fill='tozeroy', fillcolor=bg_rgba, 
                customdata=custom_data, 
                hovertemplate=(
                    "<b>%{x|%Y.%m.%d}</b><br><br>"
                    "<span style='color:" + port_color + "'>●</span> <b>Portfolio</b> &nbsp; <b>%{customdata[0]}</b><br>"
                    "<span style='color:#94A3B8'>●</span> KOSPI &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; %{customdata[1]}<br>"
                    "────────────────<br>"
                    "Alpha (α) &nbsp;&nbsp;&nbsp;&nbsp; <b style='color:%{customdata[3]}'>%{customdata[2]}</b>"
                    "<extra></extra>"
                )
            ))
            
            fig.update_layout(
                hovermode='x unified',
                xaxis=dict(
                    showgrid=False, zeroline=False, tickformat="%m-%d",
                    showspikes=True, spikecolor="rgba(255,255,255,0.2)", spikethickness=1, spikedash="solid", spikemode="across",
                    tickfont=dict(color="#64748B", size=11)
                ), 
                yaxis=dict(
                    showgrid=True, gridcolor='rgba(255,255,255,0.04)', zeroline=True, zerolinecolor='rgba(255,255,255,0.1)', ticksuffix="%",
                    tickfont=dict(color="#64748B", size=11)
                ), 
                hoverlabel=dict(
                    bgcolor="rgba(15,23,42,0.9)", bordercolor="rgba(255,255,255,0.1)", font=dict(color="white")
                ), 
                margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False
            )
            if len(chart_df) == 1: fig.update_layout(xaxis=dict(tickformat="%Y-%m-%d", tickmode='array', tickvals=[chart_df.index[0]]))
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # ────────────────────────────────────────────────────────
    # 탭 2: Watchlist & Confirmed
    # ────────────────────────────────────────────────────────
    with tab_watch:
        st.markdown(f"**마지막 스크리닝:** {last_updated or '미실행'}")

        dialog_payload = None

        if filtered_confirmed:
            res = render_watchlist_grid(filtered_confirmed, "🏆 스크리닝 통과 종목 (6/6 완벽 달성)", "#00B464", "watchlist-confirmed-anchor")
            if res: dialog_payload = res
            st.divider()

        if filtered_watchlist:
            res = render_watchlist_grid(filtered_watchlist[:20], "👀 예비 관심 종목 (4/6 조건 이상)", "#AEC1D4", "watchlist-reserve-anchor")
            if res: dialog_payload = res
            if len(filtered_watchlist) > 20: st.caption("...그 외 다수 종목 생략됨")
        else:
            if not filtered_confirmed: st.info("WatchList 대기 종목이 없습니다.")

        if dialog_payload:
            show_detail_dialog(dialog_payload, supabase)

    # ────────────────────────────────────────────────────────
    # 탭 3: 매도 히스토리
    # ────────────────────────────────────────────────────────
    with tab_hist:
        st.markdown("#### 📉 자동 매도 (Exit) 완료 히스토리 & 성과 지표")

        sell_trades = [t for t in trades[::-1] if t.get('type') == 'SELL']
        if sell_trades:
            wins = [t for t in sell_trades if t.get('return_rate', 0) > 0]
            losses = [t for t in sell_trades if t.get('return_rate', 0) <= 0]
            
            win_rate = (len(wins) / len(sell_trades)) * 100
            
            avg_win_pct = sum([t.get('return_rate', 0) for t in wins]) / len(wins) if wins else 0.0
            avg_loss_pct = sum([t.get('return_rate', 0) for t in losses]) / len(losses) if losses else 0.0
            
            rr_ratio = abs(avg_win_pct / avg_loss_pct) if avg_loss_pct != 0 else (99.99 if avg_win_pct > 0 else 0)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("총 매도 횟수", f"{len(sell_trades)}회", f"승 {len(wins)} / 패 {len(losses)}")
            m2.metric("🎯 승률 (타율)", f"{win_rate:.1f}%")
            m3.metric("⚖️ 손익비 (RR Ratio)", f"{rr_ratio:.2f}", f"평균수익 {avg_win_pct:+.2f}% / 평균손실 {avg_loss_pct:+.2f}%")
            
            total_profit_amt = 0
            
            for t in sell_trades:
                trade_p = t.get('trade_price', 0)
                ret_pct = t.get('return_rate', 0.0)
                entry = trade_p / (1 + (ret_pct / 100)) if ret_pct != -100 else 0
                t['entry_price'] = entry
                
                profit = trade_p - entry 
                t['profit_amount'] = profit
                total_profit_amt += profit

            m4.metric("💰 주당 누적 실현손익금", f"{total_profit_amt:,.0f}원")
            
            st.divider()
            
            t_df = pd.DataFrame(sell_trades)[["trade_date", "name", "entry_price", "trade_price", "return_rate", "profit_amount", "reason"]]
            t_df.columns = ["매도 일자", "종목명", "진입가", "매도가", "실현손익(%)", "손익금(원)", "매도 사유"]

            styled_t = t_df.style.map(
                lambda x: 'color: #F04452' if x > 0 else 'color: #3182F6', subset=['실현손익(%)', '손익금(원)']
            ).format({"진입가": "{:,.0f}", "매도가": "{:,.0f}", "실현손익(%)": "{:+.2f}%", "손익금(원)": "{:,.0f}"})
            st.dataframe(styled_t, hide_index=True, use_container_width=True)
        else:
            st.info("최근 매도(이탈) 이력이 없습니다. 배치가 돌면서 매도가 발생하면 통계가 나타납니다.")

    # ────────────────────────────────────────────────────────
    # 탭 4: 알고리즘 백서 (Detailed Algorithm Strategy)
    # ────────────────────────────────────────────────────────
    with tab_docs:
        st.markdown("## 🧠 Chase Momentum Algorithm Whitepaper")
        st.caption("초보자부터 전문가까지 모두가 쉽게 이해할 수 있는 정통 퀀트 추격매수 & 방어 전략 안내서입니다.")
        st.divider()

        st.markdown("### 🚀 매수 진입 6대 관문 (Entry Gates)")
        st.markdown("""
        **A. 성장성 (Growth Composite)**
        > **"돈을 더 잘 벌고 있는가?"**
        * 회사의 기초 체력을 봅니다. 단순히 흑자가 아니라, 매출, 영업이익, 순이익이 작년 동기 대비 얼마나 성장했는지 종합적으로 점수를 매깁니다. 기초가 부실한 회사는 처음부터 걸러냅니다.

        **B. 방어력 (Dynamic MDD)**
        > **"최근에 심하게 다친 적 없이 튼튼하게 버티고 있는가?"**
        * 아무리 좋은 회사라도 롤러코스터처럼 고점 대비 심하게 폭락하는 종목은 피합니다. 주식마다 가지고 있는 변동성(ATR)을 계산해, 이 종목이 버틸 수 있는 최대 하락 폭을 넘어서 추락했다면 제외합니다.

        **C. 유동성 (Liquidity)**
        > **"시장에서 사람들이 많이 찾는 진짜 인기 주식인가?"**
        * 내가 사고 싶을 때 사고, 팔고 싶을 때 팔 수 있어야 합니다. 최근 20일 동안 하루 평균 거래 대금이 50억 원을 넘는, 시장의 돈이 몰리는 핫한 종목들 사이에서만 싸웁니다.

        **D. 추세 (Trend Alignment)**
        > **"오르막길을 안정적으로 걷고 있는가?"**
        * 주가가 미끄럼틀을 타고 내려가고 있는(역배열) 종목은 사지 않습니다. 현재 가격이 단기(20일) 평균 가격보다 위에 있고, 단기 평균 가격이 장기(60일) 평균 가격보다 위에 있는 '정배열' 상태의 상승 기류를 탄 종목만 고릅니다.

        **E. 가격 돌파 (Price Breakout)**
        > **"신기록을 세우며 천장을 뚫었는가?"**
        * 최근 3개월(60일) 동안 가장 비쌌던 '최고 기록'의 90% 이상까지 다시 치고 올라온 종목을 잡습니다. 위에 있는 매물대(벽)를 뚫고 언제든 날아갈 폭발적인 에너지를 모은 선수만 뽑는 과정입니다.

        **F. 수급 (Volume Surge)**
        > **"관중들이 우르르 몰려오며 환호하고 있는가?"**
        * 단순히 가격만 슬금슬금 오르는 게 아니라, 평소(60일 평균)보다 거래량이 1.5배 이상 '빵!' 터지는 순간이어야 합니다. 시장의 거대한 자금이 쏠리면서 모멘텀이 터졌다는 강력한 증거입니다.
        """)
        
        st.divider()
        
        st.markdown("### 🚨 생존 매도 3대 원칙 (Exit Signals)")
        st.markdown("""
        **1. Trailing Stop (ATR 기반 동적 손절)**
        > **"다치기 전에 치고 빠지는 최상위 안전장치"**
        * 단순히 "-5% 떨어지면 판다"처럼 바보같이 고정된 비율을 쓰지 않습니다. 주식의 성격(변동성)을 파악해, 위아래로 심하게 흔들리는 종목은 넉넉하게, 얌전한 종목은 타이트하게 손절선을 잡습니다. 특히 주가가 오르면 손절선도 같이 따라 올라가서(Trailing) 이익을 철통같이 방어합니다.

        **2. Trend Breakdown (추세 다중붕괴)**
        > **"상승 엔진이 꺼졌음을 감지"**
        * 오르막길을 가던 주가가 단기 이평선(10일, 20일) 아래로 뚫고 내려가고, 그 평균선들의 기울기마저 꺾여버리면(미끄럼틀 형성) 미련 없이 팔고 나옵니다. "아, 이제 상승하는 힘이 다 빠졌구나"라고 시스템이 냉정하게 판단합니다.

        **3. Target Take-Profit (목표 달성)**
        > **"승리를 챙기는 기분 좋은 축하 파티"**
        * 수익률이 +40%에 도달하면 욕심을 멈추고 안전하게 팔아 지갑에 챙겨 넣습니다. 주식 시장에서 가장 어려운 '익절'을 기계적으로 수행하여 계좌를 우상향 시킵니다.
        """)
