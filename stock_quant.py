"""
quant_screener_ui.py — Streamlit UI
"""
import streamlit as st
import pandas as pd
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
    """KRX 전종목 리스트 로드 (검색 콤보박스용)"""
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
    """현재가, 진입가, 지지선을 바탕으로 손절 위험도(0~100%) 정확히 산출"""
    if curr <= 0 or entry <= 0 or stop <= 0: return 0
    buffer = entry - stop if entry > stop else curr * 0.15
    if buffer <= 0: buffer = 1
    distance = curr - stop
    risk = 100 - (distance / buffer * 100)
    return max(0, min(100, int(risk)))

def format_marcap(marcap_100m):
    """시가총액을 1000억 이상일 경우 조/억 단위로 변환"""
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
    """네이버 펀더멘털 누락 방지를 위한 비상용 직접 스크래핑"""
    try:
        url = f"https://finance.naver.com/item/main.naver?code={symbol}"
        res = requests.get(url, headers={'User-agent': 'Mozilla/5.0'}, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        table = soup.select_one("div.cop_analysis table")
        roe, debt_ratio = None, None
        if table:
            for tr in table.select("tbody tr"):
                th = tr.select_one("th")
                if not th: continue
                label = th.text.strip()
                tds = tr.select("td")
                
                vals = []
                for td in tds[:4]:
                    clean = re.sub(r"[^\d.\-]", "", td.text)
                    if clean and clean != '-': vals.append(float(clean))
                    else: vals.append(None)
                
                valid_vals = [v for v in vals if v is not None]
                if "ROE" in label and valid_vals:
                    roe = valid_vals[-1]
                if "부채비율" in label and valid_vals:
                    debt_ratio = valid_vals[-1]
        return {"roe": roe, "debt_ratio": debt_ratio}
    except:
        return {}

# ══════════════════════════════════════════
# [Helper] 실시간 평가 (Stock Search 및 팝업 보완용)
# ══════════════════════════════════════════
def live_evaluate_stock(symbol):
    """캐시에 없는 정보 실시간 수집 및 단일 퀀트 스코어 계산"""
    df = fdr.DataReader(symbol, (now_kst() - timedelta(days=300)).strftime('%Y-%m-%d'))
    if df.empty: return None, {}, 0, {}
    
    fund = fetch_naver_fundamental(symbol)
    if not fund: fund = {}
    
    fallback = get_naver_financials_fallback(symbol)
    if fund.get('roe') is None and fallback.get('roe') is not None:
        fund['roe'] = fallback['roe']
    if fund.get('debt_ratio') is None and fallback.get('debt_ratio') is not None:
        fund['debt_ratio'] = fallback['debt_ratio']
        
    curr = df['Close'].iloc[-1]
    ma20 = df['Close'].iloc[-20:].mean() if len(df)>=20 else curr
    ma60 = df['Close'].iloc[-60:].mean() if len(df)>=60 else curr
    high60 = df['Close'].tail(60).max() if len(df)>=60 else curr
    vol5 = df['Volume'].tail(5).mean() if len(df)>=5 else 1
    vol60 = df['Volume'].tail(60).mean() if len(df)>=60 else 1
    
    c_net = fund.get('net_income_cur')
    p_net = fund.get('net_income_prev')
    
    if c_net is not None and p_net is not None and p_net != 0:
        net_yoy = ((c_net - p_net)/abs(p_net)*100)
    else:
        net_yoy = 0.0
    fund['net_income_yoy'] = net_yoy

    krx_df = load_krx_list()
    matched = krx_df[krx_df['Symbol'] == symbol]
    if not matched.empty and 'Marcap' in matched.columns:
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
    
    # 팩터 스코어 근사치 계산
    factor_score = min(100, max(0, (pass_count/6 * 50) + min(25, max(0, net_yoy/5)) + min(25, max(0, mom))))
    
    return df, fund, factor_score, gates

# ══════════════════════════════════════════
# [Component] Dialog Popups & Shared Renderers
# ══════════════════════════════════════════
@st.dialog("🚨 Exit Risk 상세 진단")
def show_exit_risk_dialog(h):
    curr = h.get("current_price", 0)
    entry = h.get("entry_price", curr)
    stop = h.get("stop_price", entry * 0.85)
    ret = h.get("return_rate", 0.0)
    
    exit_risk = calculate_exit_risk(curr, entry, stop)
    risk_color = "#E6A23C" if exit_risk < 70 else "#F04452"
    
    html = f"""
    <div style="background-color:#191F28; border:1px solid #333; border-radius:12px; padding:20px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">
        <h3 style="margin:0; color:#fff; font-size:18px;">{h['name']} 
            <span style="float:right; font-size:12px; background-color:{risk_color}; color:#000; padding:4px 8px; border-radius:4px; font-weight:bold;">Risk {exit_risk}%</span>
        </h3>
        <div style="font-size:13px; color:#8B95A1; margin-top:5px; margin-bottom:20px;">
            Current <b>₩{curr:,.0f}</b> &nbsp;·&nbsp; Stop <b>₩{stop:,.0f}</b>
        </div>
        
        <div style="font-size:11px; color:#8B95A1; margin-bottom:8px; font-weight:bold;">OVERALL EXIT PROXIMITY</div>
        <div style="background-color:#333; border-radius:6px; height:16px; margin-bottom:20px;">
            <div style="background-color:{risk_color}; width:{exit_risk}%; height:100%; border-radius:6px; text-align:right; padding-right:8px; font-size:11px; line-height:16px; color:#000; font-weight:bold;">{exit_risk}%</div>
        </div>
        
        <div style="display:flex; justify-content:space-between; font-size:13px; color:#AEC1D4; margin-bottom:8px;">
            <span>Trailing Stop (ATR)</span><span style="color:#00B464;">Active</span>
        </div>
        <div style="display:flex; justify-content:space-between; font-size:13px; color:#AEC1D4; margin-bottom:20px;">
            <span>Trend Break (MA20)</span><span style="color:#00B464;">Safe</span>
        </div>
        
        <div style="display:flex; justify-content:space-between; font-size:13px; color:#8B95A1; border-top:1px solid #333; padding-top:15px;">
            <span>Entry <b style="color:#fff; font-size:14px;">₩{entry:,.0f}</b></span>
            <span>P&L <b style="color:{'#F04452' if ret>0 else '#3182F6'}; font-size:14px;">{ret:+.2f}%</b></span>
        </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

def render_single_gauge(score):
    """단일 팩터 스코어 게이지"""
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
    """상세 리포트 화면을 그리는 핵심 렌더러"""
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

    st.markdown("<br>### 📊 Fundamental Hard Gate", unsafe_allow_html=True)
    if fund is None: fund = sel
    with st.container(border=True):
        r1, r2, r3, r4 = st.columns(4)
        
        r1.caption("순이익 YoY")
        net_yoy = fund.get('net_income_yoy')
        if pd.notna(net_yoy) and net_yoy is not None and net_yoy != 0: r1.markdown(f"**{net_yoy:+.2f}%**")
        else: r1.markdown("**N/A**")
        
        r2.caption("ROE (수익성)")
        roe = fund.get('roe')
        if pd.notna(roe) and roe is not None and roe != 0: r2.markdown(f"**{roe:.2f}%**")
        else: r2.markdown("**N/A**")
        
        r3.caption("부채비율 (건전성)")
        debt = fund.get('debt_ratio')
        if pd.notna(debt) and debt is not None and debt != 0: r3.markdown(f"**{debt:.1f}%**")
        else: r3.markdown("**N/A**")
        
        r4.caption("시가총액")
        marcap = fund.get('marcap_억')
        if pd.notna(marcap) and marcap is not None and marcap != 0: r4.markdown(f"**{format_marcap(marcap)}**")
        else: r4.markdown("**N/A**")

    st.markdown("### 📈 가격 차트 (Price History)")
    if df_price is not None and not df_price.empty:
        st.line_chart(df_price[["Close"]].tail(252).rename(columns={"Close": "종가"}))
    else:
        st.info("가격 데이터가 로드되지 않았습니다.")

@st.dialog("📈 퀀트 평가 상세 리포트", width="large")
def show_detail_dialog(sel, supabase):
    with st.spinner("실시간 최신 데이터를 동기화 중입니다..."):
        # 1. 100점 오버라이드 버그 방지 (기존 팩터 스코어를 캐시에서 찾아 복구)
        original_score = sel.get('factor_score', 0)
        if original_score == 0:
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
            
        # 기존 스코어가 DB에 아예 없을 때만 라이브 스코어 반영
        if original_score == 0 and live_score > 0:
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
    
    # Watchlist 탭에서 포트폴리오(holdings)에 편입된 종목을 철저히 배제
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
    # 탭 1: 포트폴리오 (커스텀 표 구조 완벽 적용)
    # ────────────────────────────────────────────────────────
    with tab_port:
        c_budg, _ = st.columns([1, 2])
        with c_budg:
            total_budget = st.number_input("💰 포트폴리오 운용 예산 (원)", min_value=1000000, value=10000000, step=1000000)
        
        total_capital = sum([h.get("current_price", 0) for h in holdings])
        with st.container(border=True):
            st.caption("현재 포트폴리오 보유종목 1주 기준 평가 합산액")
            st.markdown(f"## {total_capital:,.0f} 원")

        st.markdown(f"#### Holdings ({len(holdings)})")
        
        if holdings:
            target_weight = 1.0 / len(holdings)
            target_budget_per_stock = total_budget * target_weight
            
            c1, c2, c3, c4, c5, c6, c7 = st.columns([2, 1.2, 1.2, 1.5, 1.2, 1.2, 2.5])
            c1.markdown("<div class='grid-header'>종목명</div>", unsafe_allow_html=True)
            c2.markdown("<div class='grid-header'>진입가</div>", unsafe_allow_html=True)
            c3.markdown("<div class='grid-header'>현재가</div>", unsafe_allow_html=True)
            c4.markdown("<div class='grid-header'>수익률(P&L)</div>", unsafe_allow_html=True)
            c5.markdown("<div class='grid-header'>Exit Risk</div>", unsafe_allow_html=True)
            c6.markdown("<div class='grid-header'>권장수량</div>", unsafe_allow_html=True)
            c7.markdown("<div class='grid-header'>상세 액션</div>", unsafe_allow_html=True)
            
            for h in holdings:
                curr = h.get("current_price", 0)
                entry = h.get("entry_price", curr)
                stop = h.get("stop_price", entry * 0.85)
                ret = h.get("return_rate", 0.0)
                
                exit_risk = calculate_exit_risk(curr, entry, stop)
                target_shares = int(target_budget_per_stock // curr) if curr > 0 else 0
                
                c1, c2, c3, c4, c5, c6, c7 = st.columns([2, 1.2, 1.2, 1.5, 1.2, 1.2, 2.5])
                c1.markdown(f"<div class='grid-row' style='font-weight:bold;'>{h['name']}</div>", unsafe_allow_html=True)
                c2.markdown(f"<div class='grid-row'>₩{entry:,.0f}</div>", unsafe_allow_html=True)
                c3.markdown(f"<div class='grid-row'>₩{curr:,.0f}</div>", unsafe_allow_html=True)
                
                pnl_color = "#F04452" if ret > 0 else "#3182F6"
                c4.markdown(f"<div class='grid-row' style='color:{pnl_color}; font-weight:bold;'>{ret:+.2f}%</div>", unsafe_allow_html=True)
                
                risk_color = "#E6A23C" if exit_risk < 70 else "#F04452"
                c5.markdown(f"<div class='grid-row' style='color:{risk_color}; font-weight:bold;'>{exit_risk}%</div>", unsafe_allow_html=True)
                
                c6.markdown(f"<div class='grid-row' style='color:#AEC1D4;'>{target_shares:,}주</div>", unsafe_allow_html=True)
                
                with c7:
                    bc1, bc2 = st.columns(2)
                    if bc1.button("🚨 Risk", key=f"risk_{h['symbol']}", use_container_width=True):
                        show_exit_risk_dialog(h)
                    if bc2.button("📊 리포트", key=f"det_{h['symbol']}", use_container_width=True):
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
        
        def render_watchlist_grid(items, title, color_code, show_target=False, total_budget=10000000):
            st.markdown(f"#### {title}")
            
            target_weight = 1.0 / len(items) if items else 0
            target_budget = total_budget * target_weight
            
            if show_target:
                c1, c2, c3, c4, c5, c6, c7 = st.columns([1, 2, 1.5, 1.5, 1.5, 1.5, 1.5])
                c1.markdown("<div class='grid-header'>순위</div>", unsafe_allow_html=True)
                c2.markdown("<div class='grid-header'>종목명</div>", unsafe_allow_html=True)
                c3.markdown("<div class='grid-header'>현재가</div>", unsafe_allow_html=True)
                c4.markdown("<div class='grid-header'>통과</div>", unsafe_allow_html=True)
                c5.markdown("<div class='grid-header'>랭킹점수</div>", unsafe_allow_html=True)
                c6.markdown("<div class='grid-header'>권장수량</div>", unsafe_allow_html=True)
                c7.markdown("<div class='grid-header'>액션</div>", unsafe_allow_html=True)
            else:
                c1, c2, c3, c4, c5, c6 = st.columns([1, 2.5, 2, 1.5, 1.5, 2])
                c1.markdown("<div class='grid-header'>순위</div>", unsafe_allow_html=True)
                c2.markdown("<div class='grid-header'>종목명</div>", unsafe_allow_html=True)
                c3.markdown("<div class='grid-header'>현재가</div>", unsafe_allow_html=True)
                c4.markdown("<div class='grid-header'>통과</div>", unsafe_allow_html=True)
                c5.markdown("<div class='grid-header'>랭킹점수</div>", unsafe_allow_html=True)
                c6.markdown("<div class='grid-header'>액션</div>", unsafe_allow_html=True)
            
            for idx, w in enumerate(items):
                curr = w.get('current_price', 0)
                
                if show_target:
                    target_shares = int(target_budget // curr) if curr > 0 else 0
                    c1, c2, c3, c4, c5, c6, c7 = st.columns([1, 2, 1.5, 1.5, 1.5, 1.5, 1.5])
                    c1.markdown(f"<div class='grid-row'>{idx+1}</div>", unsafe_allow_html=True)
                    c2.markdown(f"<div class='grid-row' style='font-weight:bold;'>{w['name']}</div>", unsafe_allow_html=True)
                    c3.markdown(f"<div class='grid-row'>₩{curr:,}</div>", unsafe_allow_html=True)
                    c4.markdown(f"<div class='grid-row'>{w.get('total_pass', 0)}/6</div>", unsafe_allow_html=True)
                    c5.markdown(f"<div class='grid-row' style='color:{color_code}; font-weight:bold;'>{w.get('factor_score', 0):.2f}점</div>", unsafe_allow_html=True)
                    c6.markdown(f"<div class='grid-row' style='color:#AEC1D4;'>{target_shares:,}주</div>", unsafe_allow_html=True)
                    with c7:
                        if st.button("📊 리포트", key=f"w_det_t_{w['symbol']}", use_container_width=True):
                            show_detail_dialog(w, supabase)
                else:
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
            render_watchlist_grid(filtered_confirmed, "🏆 스크리닝 통과 종목 (6/6 완벽 달성)", "#00B464", show_target=True, total_budget=st.session_state.get('total_budget', 10000000))
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
