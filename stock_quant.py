"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
import numpy as np
import json
import requests
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import plotly.graph_objects as go
import FinanceDataReader as fdr

from quant_core import (
    load_price_from_db, load_screening_result,
    HARD_GATES, SOFT_GATES, now_kst, fetch_naver_fundamental
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

@st.cache_data(ttl=86400)
def load_krx_list():
    try:
        krx = fdr.StockListing("KRX")
        if "Code" in krx.columns: krx = krx.rename(columns={"Code": "Symbol"})
        if "종목코드" in krx.columns: krx = krx.rename(columns={"종목코드": "Symbol"})
        if "Name" not in krx.columns and "종목명" in krx.columns: krx = krx.rename(columns={"종목명": "Name"})
        if "Marcap" not in krx.columns and "시가총액" in krx.columns: krx = krx.rename(columns={"시가총액": "Marcap"})
        
        krx = krx.dropna(subset=["Symbol", "Name"])
        krx["SearchStr"] = krx["Name"].astype(str) + " (" + krx["Symbol"].astype(str) + ")"
        
        if "Marcap" in krx.columns:
            return krx[["Symbol", "Name", "SearchStr", "Marcap"]]
        return krx[["Symbol", "Name", "SearchStr"]]
    except Exception as e:
        return pd.DataFrame(columns=["Symbol", "Name", "SearchStr", "Marcap"])

def calculate_exit_risk(curr, entry, stop):
    if curr <= 0 or entry <= 0 or stop <= 0: return 0
    buffer = entry - stop if entry > stop else curr * 0.15
    if buffer <= 0: buffer = 1
    distance = curr - stop
    risk = 100 - (distance / buffer * 100)
    return max(0, min(100, int(risk)))

def format_marcap(marcap_100m):
    if marcap_100m is None or pd.isna(marcap_100m) or marcap_100m == 0: 
        return "N/A"
    try:
        marcap_100m = float(marcap_100m)
        if marcap_100m >= 10000:
            jo = int(marcap_100m // 10000)
            eok = int(marcap_100m % 10000)
            if eok > 0:
                return f"{jo}조 {eok:,}억"
            return f"{jo}조"
        else:
            return f"{int(marcap_100m):,}억"
    except:
        return "N/A"

def get_naver_financials_fallback(symbol):
    fund = {}
    try:
        url = f"https://finance.naver.com/item/main.naver?code={symbol}"
        res = requests.get(url, headers={'User-agent': 'Mozilla/5.0'}, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        
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
                recent_val = valid_vals[-1] if valid_vals else None
                prev_val = valid_vals[-2] if len(valid_vals) > 1 else None

                if "매출액" in label:
                    fund['revenue_cur'] = recent_val
                    fund['revenue_prev'] = prev_val
                elif "영업이익" in label and "영업이익률" not in label:
                    fund['op_profit_cur'] = recent_val
                    fund['op_profit_prev'] = prev_val
                elif "당기순이익" in label or ("순이익" in label and "순이익률" not in label):
                    fund['net_income_cur'] = recent_val
                    fund['net_income_prev'] = prev_val
                elif "영업이익률" in label: fund['op_margin'] = recent_val
                elif "순이익률" in label: fund['net_margin'] = recent_val
                elif "ROE" in label: fund['roe'] = recent_val
                elif "ROA" in label: fund['roa'] = recent_val
                elif "부채비율" in label: fund['debt_ratio'] = recent_val
                elif "PER" in label: fund['per'] = recent_val
                elif "PBR" in label: fund['pbr'] = recent_val
                elif "배당수익률" in label or "시가배당률" in label: fund['dividend_yield'] = recent_val
                
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
                
        return fund
    except:
        return fund

def live_evaluate_stock(symbol):
    df = fdr.DataReader(symbol, (now_kst() - timedelta(days=300)).strftime('%Y-%m-%d'))
    if df.empty: return None, {}, 0, {}
    
    fund = fetch_naver_fundamental(symbol)
    if not fund: fund = {}
    
    fallback = get_naver_financials_fallback(symbol)
    for k, v in fallback.items():
        if v is not None:
            fund[k] = v
        
    curr = df['Close'].iloc[-1]
    ma20 = df['Close'].iloc[-20:].mean() if len(df)>=20 else curr
    ma60 = df['Close'].iloc[-60:].mean() if len(df)>=60 else curr
    high60 = df['Close'].tail(60).max() if len(df)>=60 else curr
    vol5 = df['Volume'].tail(5).mean() if len(df)>=5 else 1
    vol60 = df['Volume'].tail(60).mean() if len(df)>=60 else 1
    
    c_net = fund.get('net_income_cur')
    p_net = fund.get('net_income_prev')
    c_rev = fund.get('revenue_cur')
    p_rev = fund.get('revenue_prev')
    
    net_yoy = ((c_net - p_net)/abs(p_net)*100) if c_net is not None and p_net is not None and p_net != 0 else 0.0
    rev_yoy = ((c_rev - p_rev)/abs(p_rev)*100) if c_rev is not None and p_rev is not None and p_rev != 0 else 0.0
    
    fund['net_income_yoy'] = net_yoy
    fund['revenue_yoy'] = rev_yoy

    krx_df = load_krx_list()
    matched = krx_df[krx_df['Symbol'] == symbol]
    if not matched.empty and 'Marcap' in matched.columns and fund.get('marcap_억', 0) == 0:
        fund['marcap_억'] = matched.iloc[0]['Marcap'] / 1e8 if pd.notnull(matched.iloc[0]['Marcap']) else 0

    gates = {
        'A': {'name': 'Growth (YoY)', 'pass': net_yoy > 0, 'reason': f"{net_yoy:+.1f}%"},
        'B': {'name': 'Trend (MA)', 'pass': curr > ma20 > ma60, 'reason': "정배열" if curr>ma20>ma60 else "역배열"},
        'C': {'name': 'Breakout', 'pass': curr >= high60*0.9, 'reason': f"{curr/high60*100:.1f}%"},
        'D': {'name': 'Volume Surge', 'pass': vol5 > vol60*1.5, 'reason': f"{vol5/vol60:.1f}x"},
        'E': {'name': 'Liquidity', 'pass': (curr*vol5)/1e8 > 50, 'reason': f"{(curr*vol5)/1e8:,.0f}억"},
        'F': {'name': 'Profitability', 'pass': c_net is not None and c_net > 0, 'reason': f"{c_net or 0:,.0f}억"}
    }
    
    pass_count = sum([1 for g in gates.values() if g['pass']])
    mom = ((curr-ma60)/ma60*100) if ma60 > 0 else 0
    
    factor_score = min(99.9, max(0, (pass_count/6 * 50) + min(25, max(0, net_yoy/5)) + min(25, max(0, mom))))
    
    return df, fund, factor_score, gates

# ══════════════════════════════════════════
# [Component] Popover & Shared Renderers
# ══════════════════════════════════════════
def render_exit_risk_content(h, supabase):
    """(다이얼로그 아님) st.popover 내부에 그려지는 세련된 좌표 기준 팝업"""
    curr = h.get("current_price", 0)
    entry = h.get("entry_price", curr)
    stop = h.get("stop_price", entry * 0.85)
    ret = h.get("return_rate", 0.0)

    df = load_price_from_db(supabase, h['symbol'])
    ts_risk = 0.0
    ma_risk = 0.0

    if not df.empty and len(df) >= 20:
        high = df.get('High', df['Close'])
        low = df.get('Low', df['Close'])
        prev_close = df['Close'].shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean().iloc[-1]
        ma20 = df['Close'].iloc[-20:].mean()

        entry_date = pd.to_datetime(h.get('entry_date', now_kst().strftime("%Y-%m-%d")))
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
    risk_color = "#E6A23C" if exit_risk < 70 else "#F04452"
    badge_text = f"High {exit_risk}%" if exit_risk >= 70 else f"Mid {exit_risk}%" if exit_risk >= 40 else f"Low {exit_risk}%"
    pnl_color = "#F04452" if ret > 0 else ("#3182F6" if ret < 0 else "#AEC1D4")

    html = f"""
    <div style="background-color:#191F28; padding:5px; border-radius:8px; min-width: 250px;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px;">
            <div>
                <div style="color:#fff; font-size:16px; font-weight:bold;">{h['name']}</div>
                <div style="font-size:11px; color:#8B95A1;">Current <b>₩{curr:,.0f}</b> &nbsp;·&nbsp; Stop <b>₩{stop:,.0f}</b></div>
            </div>
            <div style="background-color:#333; color:{risk_color}; padding:4px 8px; border-radius:4px; font-weight:bold; font-size:11px;">{badge_text}</div>
        </div>

        <div style="font-size:10px; color:#8B95A1; margin-bottom:5px; font-weight:bold;">OVERALL EXIT PROXIMITY</div>
        <div style="background-color:#333; border-radius:4px; height:12px; margin-bottom:15px; position:relative;">
            <div style="background-color:{risk_color}; width:{exit_risk}%; height:100%; border-radius:4px;"></div>
            <div style="position:absolute; top:0; left:0; width:100%; text-align:center; font-size:10px; line-height:12px; color:#fff; font-weight:bold;">{exit_risk}%</div>
        </div>

        <div style="display:flex; justify-content:space-between; font-size:11px; color:#AEC1D4; margin-bottom:5px;">
            <span>Trailing Stop</span><span style="font-weight:bold;">{ts_risk:.1f}%</span>
        </div>
        <div style="background-color:#333; border-radius:4px; height:6px; margin-bottom:12px;">
            <div style="background-color:#AEC1D4; width:{ts_risk}%; height:100%; border-radius:4px;"></div>
        </div>

        <div style="display:flex; justify-content:space-between; font-size:11px; color:#AEC1D4; margin-bottom:5px;">
            <span>Trend Break</span><span style="font-weight:bold;">{ma_risk:.1f}%</span>
        </div>
        <div style="background-color:#333; border-radius:4px; height:6px; margin-bottom:20px;">
            <div style="background-color:#AEC1D4; width:{ma_risk}%; height:100%; border-radius:4px;"></div>
        </div>

        <div style="display:flex; justify-content:space-between; font-size:12px; color:#8B95A1; border-top:1px solid #333; padding-top:12px;">
            <span>Entry &nbsp;&nbsp;<b style="color:#fff; font-size:13px;">₩{entry:,.0f}</b></span>
            <span>P&L &nbsp;&nbsp;<b style="color:{pnl_color}; font-size:13px;">{ret:+.2f}%</b></span>
        </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

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
    
    st.markdown(f"## {sel['name']} <span style='font-size:18px; color:#AEC1D4;'>{sel['symbol']} &nbsp;|&nbsp; {sel.get('market', 'KOSPI')}</span>", unsafe_allow_html=True)
    st.markdown(f"<h1>{curr:,.0f} 원 <span style='font-size:20px; color:{'#F04452' if ret_1m>0 else '#3182F6'};'>{ret_1m:+.2f}% (1M)</span></h1>", unsafe_allow_html=True)
    st.divider()

    if factor_score is None: factor_score = sel.get('factor_score', 0)
    total_pass = sel.get('total_pass', sum([1 for g in gates.values() if g['pass']]) if gates else 0)
    
    c_header, c_gauge = st.columns([3, 2])
    with c_header:
        st.markdown("### ⚡ Quant Scores")
        c1, c2 = st.columns(2)
        c1.metric("종합 랭킹 스코어", f"{factor_score:.2f}점")
        c2.metric("생존 필터 통과", f"{total_pass} / 6")
        st.info("💡 실시간 퀀트 데이터에 기반하여 생성된 리포트입니다.")
            
    with c_gauge:
        render_single_gauge(factor_score)

    st.markdown("##### Entry Gates (6 conditions)")
    if gates is None:
        gates_data = sel.get("filter_details", {})
        if not gates_data or "Growth Composite" not in gates_data:
            gates = {
                'A': {'name': 'Growth (YoY)', 'pass': False, 'reason': '-'}, 'B': {'name': 'Trend (MA)', 'pass': False, 'reason': '-'},
                'C': {'name': 'Breakout', 'pass': False, 'reason': '-'}, 'D': {'name': 'Volume Surge', 'pass': False, 'reason': '-'},
                'E': {'name': 'Liquidity', 'pass': False, 'reason': '-'}, 'F': {'name': 'Dynamic MDD', 'pass': False, 'reason': '-'}
            }
        else:
            gates = {
                'A': {'name': 'Growth (YoY)', 'pass': gates_data.get("Growth Composite", {}).get("pass", False), 'reason': gates_data.get("Growth Composite", {}).get("reason", "-")},
                'B': {'name': 'Trend (MA)', 'pass': gates_data.get("Trend Alignment", {}).get("pass", False), 'reason': gates_data.get("Trend Alignment", {}).get("reason", "-")},
                'C': {'name': 'Breakout', 'pass': gates_data.get("Price Breakout", {}).get("pass", False), 'reason': gates_data.get("Price Breakout", {}).get("reason", "-")},
                'D': {'name': 'Volume Surge', 'pass': gates_data.get("Volume Surge", {}).get("pass", False), 'reason': gates_data.get("Volume Surge", {}).get("reason", "-")},
                'E': {'name': 'Liquidity', 'pass': gates_data.get("Liquidity", {}).get("pass", False), 'reason': gates_data.get("Liquidity", {}).get("reason", "-")},
                'F': {'name': 'Dynamic MDD', 'pass': gates_data.get("Dynamic MDD", {}).get("pass", False), 'reason': gates_data.get("Dynamic MDD", {}).get("reason", "-")}
            }
            
    cols = st.columns(6)
    labels = ['A', 'B', 'C', 'D', 'E', 'F']
    for idx, (col, key) in enumerate(zip(cols, gates.keys())):
        g = gates[key]
        passed = g['pass']
        color = "#00B464" if passed else "#333333"
        txt_color = "white" if passed else "#888888"
        with col:
            st.markdown(f"""
            <div style="background-color:#1E2329; border:1px solid {color}; border-radius:6px; padding:10px; height:85px;">
                <div style="display:flex; justify-content:space-between; font-weight:bold; color:{txt_color}; font-size:13px; margin-bottom:8px;">
                    <span>{labels[idx]}</span> <span style="font-size:11px;">{'✔️' if passed else '❌'}</span>
                </div>
                <div style="height:3px; background-color:{color}; border-radius:2px; margin-bottom:8px;"></div>
                <div style="font-size:10px; color:#AEC1D4; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{g['name']}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<br>### 📊 Financials & Valuation", unsafe_allow_html=True)
    if fund is None: fund = sel

    def safe_fmt(val, is_pct=False, is_eok=False):
        if val is None or pd.isna(val): return "N/A"
        try:
            val = float(val)
            if is_pct: return f"{val:+.2f}%" if val < 0 else f"{val:.2f}%"
            if is_eok: return f"{val:,.0f}억"
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
    with st.spinner("실시간 최신 데이터를 동기화 중입니다..."):
        original_score = sel.get('factor_score', 0)
        
        if original_score == 0 or original_score == 100:
            c_list, w_list, _ = load_screening_result(supabase)
            for item in c_list + w_list:
                if item['symbol'] == sel['symbol']:
                    original_score = item.get('factor_score', 0)
                    sel['factor_score'] = original_score
                    if 'filter_details' in item:
                        sel['filter_details'] = item['filter_details']
                    break

        df_price, live_fund, live_score, gates = live_evaluate_stock(sel['symbol'])
        
        if live_fund:
            for k, v in live_fund.items():
                if pd.notna(v) and v is not None and v != 0:
                    sel[k] = v
                    
        if 'filter_details' not in sel and gates:
            sel['total_pass'] = sum([1 for g in gates.values() if g['pass']])
            
        if (original_score == 0 or original_score == 100) and live_score > 0:
            sel['factor_score'] = live_score
            
        if 'ret_1m' not in sel or sel['ret_1m'] == 0:
            if df_price is not None and len(df_price) >= 21:
                sel['ret_1m'] = (df_price['Close'].iloc[-1] - df_price['Close'].iloc[-21]) / df_price['Close'].iloc[-21] * 100

    render_detailed_report_content(sel, df_price=df_price, fund=sel, factor_score=sel['factor_score'], gates=gates)


# ══════════════════════════════════════════
# [Main Entry Point]
# ══════════════════════════════════════════
def run_stock_quant_page(supabase, username: str = "admin", **kwargs):
    st.title("📡 정통 퀀트 스크리너 & 오토 트레이딩")

    confirmed, watchlist, last_updated = load_screening_result(supabase)
    holdings, trades, history = load_portfolio_data(supabase)
    
    holding_syms = set([h['symbol'] for h in holdings])
    filtered_confirmed = [c for c in confirmed if c['symbol'] not in holding_syms]
    filtered_watchlist = [w for w in watchlist if w['symbol'] not in holding_syms]

    tab_port, tab_watch, tab_hist, tab_search = st.tabs([
        f"Portfolio ({len(holdings)})", 
        f"Watchlist ({len(filtered_confirmed) + len(filtered_watchlist)})", 
        "매도 히스토리 (History)",
        "🔍 Stock Search"
    ])

    st.markdown("""
    <style>
    .grid-header { font-size: 13px; font-weight: bold; color: #8B95A1; border-bottom: 1px solid #333; padding-bottom: 8px; margin-bottom: 8px; }
    .grid-row { padding-top: 10px; padding-bottom: 10px; font-size: 14px; border-bottom: 1px solid #1E2329; display: flex; align-items: center;}
    </style>
    """, unsafe_allow_html=True)

    # ────────────────────────────────────────────────────────
    # 탭 1: 포트폴리오
    # ────────────────────────────────────────────────────────
    with tab_port:
        total_capital = sum([h.get("current_price", 0) for h in holdings])
        with st.container(border=True):
            st.caption("현재 포트폴리오 평가 총액 (보유종목 1주 기준 합산액)")
            st.markdown(f"## {total_capital:,.0f} 원")

        st.markdown(f"#### Holdings ({len(holdings)})")
        
        if holdings:
            c1, c2, c3, c4, c5, c6 = st.columns([2, 1.5, 1.5, 1.5, 1.5, 2.5])
            c1.markdown("<div class='grid-header'>종목명</div>", unsafe_allow_html=True)
            c2.markdown("<div class='grid-header'>진입가</div>", unsafe_allow_html=True)
            c3.markdown("<div class='grid-header'>현재가</div>", unsafe_allow_html=True)
            c4.markdown("<div class='grid-header'>수익률(P&L)</div>", unsafe_allow_html=True)
            c5.markdown("<div class='grid-header'>Exit Risk</div>", unsafe_allow_html=True)
            c6.markdown("<div class='grid-header'>상세 액션</div>", unsafe_allow_html=True)
            
            for h in holdings:
                curr = h.get("current_price", 0)
                entry = h.get("entry_price", curr)
                stop = h.get("stop_price", entry * 0.85)
                ret = h.get("return_rate", 0.0)
                exit_risk = calculate_exit_risk(curr, entry, stop)
                
                c1, c2, c3, c4, c5, c6 = st.columns([2, 1.5, 1.5, 1.5, 1.5, 2.5])
                c1.markdown(f"<div class='grid-row' style='font-weight:bold;'>{h['name']}</div>", unsafe_allow_html=True)
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
                            show_detail_dialog(h, supabase)
        else:
            st.info("현재 보유 중인 종목이 없습니다.")

        st.divider()
        st.markdown("#### KOSPI 대비 포트폴리오 성과 (Alpha)")
        end_date = now_kst()
        start_date = end_date - timedelta(days=30)
        
        df_kospi = fdr.DataReader('KS11', start_date.strftime('%Y-%m-%d'))
        if not df_kospi.empty:
            df_kospi['kospi_cum'] = df_kospi['Close'].pct_change().fillna(0).cumsum() * 100
        else:
            df_kospi = pd.DataFrame(columns=['Close', 'kospi_cum'])

        chart_df = pd.DataFrame(index=df_kospi.index)
        chart_df['KOSPI'] = df_kospi['kospi_cum']

        df_hist = pd.DataFrame(history)
        if not df_hist.empty:
            df_hist['date'] = pd.to_datetime(df_hist['date'])
            df_hist['date'] = df_hist['date'].dt.tz_localize(None) 
            df_hist = df_hist.set_index('date')
            df_hist['port_cum'] = df_hist['portfolio_return'].cumsum()
            chart_df = chart_df.join(df_hist['port_cum'], how='left')
            chart_df['Portfolio'] = chart_df['port_cum'].ffill().fillna(0)
            cum_ret = chart_df['Portfolio'].iloc[-1]
            day_ret = df_hist['portfolio_return'].iloc[-1]
        else:
            chart_df['Portfolio'] = 0.0
            cum_ret = 0.0
            day_ret = 0.0

        k_cum_ret = chart_df['KOSPI'].iloc[-1] if not chart_df['KOSPI'].empty else 0.0
        k_day_ret = df_kospi['Close'].pct_change().iloc[-1] * 100 if not df_kospi.empty else 0.0
        alpha = cum_ret - k_cum_ret
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Portfolio 누적", f"{cum_ret:+.2f}%", f"Day {day_ret:+.2f}%")
        col2.metric("KOSPI 누적", f"{k_cum_ret:+.2f}%", f"Day {k_day_ret:+.2f}%")
        col3.metric("Alpha (초과수익)", f"{alpha:+.2f}%")

        if not chart_df.empty:
            chart_df['Alpha'] = chart_df['Portfolio'] - chart_df['KOSPI']
            chart_df['Alpha_Color'] = chart_df['Alpha'].apply(lambda x: '#00B464' if x >= 0 else '#F04452')
            fig = go.Figure()
            custom_data = np.column_stack((chart_df['KOSPI'], chart_df['Alpha'], chart_df['Alpha_Color']))
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['Portfolio'], mode='lines+markers', name='Portfolio', line=dict(color='#F04452', width=2.5), fill='tozeroy', fillcolor='rgba(240, 68, 82, 0.05)', customdata=custom_data, hovertemplate="<span style='color:#AEC1D4; font-size:12px;'>%{x|%Y.%m.%d}</span><br><br><span style='color:#3182F6;'>●</span> Portfolio &nbsp;&nbsp;&nbsp;<b>%{y:.2f}%</b><br><span style='color:#8B95A1;'>●</span> KOSPI &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>%{customdata[0]:.2f}%</b><br>─────────────────<br><span style='color:#8B95A1;'>α</span> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b style='color:%{customdata[2]};'>%{customdata[1]:+.2f}%</b><extra></extra>"))
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['KOSPI'], mode='lines+markers', name='KOSPI', line=dict(color='#8B95A1', width=1.5, dash='dot'), hoverinfo='skip'))
            fig.update_layout(hovermode='x', xaxis=dict(showgrid=False, zeroline=False, tickformat="%Y-%m-%d"), yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)', ticksuffix="%"), hoverlabel=dict(bgcolor="#191F28", font_color="white"), margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False)
            if len(chart_df) == 1: fig.update_layout(xaxis=dict(tickformat="%Y-%m-%d", tickmode='array', tickvals=[chart_df.index[0]]))
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # ────────────────────────────────────────────────────────
    # 탭 2: Watchlist & Confirmed
    # ────────────────────────────────────────────────────────
    with tab_watch:
        st.markdown(f"**마지막 스크리닝:** {last_updated or '미실행'}")
        
        def render_watchlist_grid(items, title, color_code):
            st.markdown(f"#### {title}")
            
            c1, c2, c3, c4, c5, c6 = st.columns([1, 2.5, 2, 1.5, 1.5, 2])
            c1.markdown("<div class='grid-header'>순위</div>", unsafe_allow_html=True)
            c2.markdown("<div class='grid-header'>종목명</div>", unsafe_allow_html=True)
            c3.markdown("<div class='grid-header'>현재가</div>", unsafe_allow_html=True)
            c4.markdown("<div class='grid-header'>통과</div>", unsafe_allow_html=True)
            c5.markdown("<div class='grid-header'>랭킹점수</div>", unsafe_allow_html=True)
            c6.markdown("<div class='grid-header'>액션</div>", unsafe_allow_html=True)
            
            for idx, w in enumerate(items):
                curr = w.get('current_price', 0)
                
                c1, c2, c3, c4, c5, c6 = st.columns([1, 2.5, 2, 1.5, 1.5, 2])
                c1.markdown(f"<div class='grid-row'>{idx+1}</div>", unsafe_allow_html=True)
                c2.markdown(f"<div class='grid-row' style='font-weight:bold;'>{w['name']}</div>", unsafe_allow_html=True)
                c3.markdown(f"<div class='grid-row'>₩{curr:,}</div>", unsafe_allow_html=True)
                c4.markdown(f"<div class='grid-row'>{w.get('total_pass', 0)}/6</div>", unsafe_allow_html=True)
                c5.markdown(f"<div class='grid-row' style='color:{color_code}; font-weight:bold;'>{w.get('factor_score', 0):.2f}점</div>", unsafe_allow_html=True)
                with c6:
                    if st.button("📊 리포트", key=f"w_det_{w['symbol']}", use_container_width=True):
                        show_detail_dialog(w, supabase)
        
        if filtered_confirmed:
            render_watchlist_grid(filtered_confirmed, "🏆 스크리닝 통과 종목 (6/6 완벽 달성)", "#00B464")
            st.divider()

        if filtered_watchlist:
            render_watchlist_grid(filtered_watchlist[:20], "👀 예비 관심 종목 (4/6 조건 이상)", "#AEC1D4")
            if len(filtered_watchlist) > 20: st.caption("...그 외 다수 종목 생략됨")
        else:
            if not filtered_confirmed: st.info("WatchList 대기 종목이 없습니다.")

    # ────────────────────────────────────────────────────────
    # 탭 3: 매도 히스토리
    # ────────────────────────────────────────────────────────
    with tab_hist:
        st.markdown("#### 📉 자동 매도 (Exit) 완료 히스토리")
        
        sell_trades = [t for t in trades[::-1] if t.get('type') == 'SELL']
        if sell_trades:
            for t in sell_trades:
                trade_p = t.get('trade_price', 0)
                ret_pct = t.get('return_rate', 0.0)
                entry = trade_p / (1 + (ret_pct / 100)) if ret_pct != -100 else 0
                t['entry_price'] = entry
                t['profit_amount'] = trade_p - entry 

            t_df = pd.DataFrame(sell_trades)[["trade_date", "name", "entry_price", "trade_price", "return_rate", "profit_amount", "reason"]]
            t_df.columns = ["매도 일자", "종목명", "진입가", "매도가", "실현손익(%)", "손익금(원)", "매도 사유"]
            
            styled_t = t_df.style.map(
                lambda x: 'color: #F04452' if x > 0 else 'color: #3182F6', subset=['실현손익(%)', '손익금(원)']
            ).format({"진입가": "{:,.0f}", "매도가": "{:,.0f}", "실현손익(%)": "{:+.2f}%", "손익금(원)": "{:,.0f}"})
            st.dataframe(styled_t, hide_index=True, use_container_width=True)
        else:
            st.info("최근 매도(이탈) 이력이 없습니다.")

    # ────────────────────────────────────────────────────────
    # 탭 4: Stock Search (주식 조회)
    # ────────────────────────────────────────────────────────
    with tab_search:
        st.markdown("### 🔍 Stock Search & Report")
        st.caption("종목명 또는 코드를 콤보박스에서 검색하여 실시간 퀀트 분석을 진행합니다.")
        
        krx_df = load_krx_list()
        
        if krx_df.empty:
            st.error("⚠️ 종목 리스트를 불러오는데 실패했습니다. (API 일시적 장애)")
            options = [""]
        else:
            options = [""] + krx_df["SearchStr"].tolist()
        
        col_search, _ = st.columns([2, 1])
        with col_search:
            selected_stock_str = st.selectbox("🔎 종목 검색 (종목명 또는 코드 자동완성)", options=options)

        if selected_stock_str:
            search_query = selected_stock_str.split("(")[-1].replace(")", "").strip()
            
            all_cached_stocks = {item['symbol']: item for item in holdings + confirmed + watchlist}
            
            if search_query in all_cached_stocks:
                sel = all_cached_stocks[search_query]
                df_price = load_price_from_db(supabase, search_query)
                st.divider()
                st.success("✅ 캐시된 분석 데이터를 로드했습니다.")
                
                render_detailed_report_content(sel, df_price=df_price, factor_score=sel.get('factor_score', 0))
            else:
                with st.spinner(f"'{selected_stock_str}' 실시간 퀀트 데이터 스크래핑 중..."):
                    df_price, fund, factor_score, gates = live_evaluate_stock(search_query)
                    
                if df_price is None or df_price.empty:
                    st.error("해당 종목의 차트 데이터를 찾을 수 없습니다.")
                else:
                    sel = {
                        'symbol': search_query, 'name': selected_stock_str.split(" (")[0], 
                        'current_price': df_price['Close'].iloc[-1] if not df_price.empty else 0,
                        'ret_1m': (df_price['Close'].iloc[-1] - df_price['Close'].iloc[-21]) / df_price['Close'].iloc[-21] * 100 if len(df_price)>=21 else 0
                    }
                    if fund: sel.update(fund)
                    st.divider()
                    render_detailed_report_content(sel, df_price=df_price, fund=fund, factor_score=factor_score, gates=gates)
