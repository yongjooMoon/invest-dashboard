# ... existing code ...
from quant_core import (
    now_kst, now_kst_str, load_filtered_universe,
    upsert_daily_rows, trim_old_rows,
    load_fundamental_from_db, save_fundamental_to_db,
    run_screening_from_db, save_screening_result, get_fundamental,
    PREFILTER_MARCAP_억, PREFILTER_TVOL_억, load_price_from_db, NumpyEncoder
)

# 하드코딩된 위험한 fallback 키를 비우고 오직 안전한 환경변수만 바라보게 수정합니다.
SUPABASE_URL     = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY", "")
KIS_APP_KEY      = os.environ.get("KIS_APP_KEY", "")
KIS_APP_SECRET   = os.environ.get("KIS_APP_SECRET", "")
KIS_BASE_URL     = os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
DART_API_KEY     = os.environ.get("DART_API_KEY", "")

# 이메일 발송용 계정 정보도 안전하게 환경변수에서 주입받습니다.
SMTP_EMAIL       = os.environ.get("SMTP_EMAIL", "")
SMTP_PASSWORD    = os.environ.get("SMTP_PASSWORD", "")

SLEEP_SEC = 0.12

# ──────────────────────────────────────────
# ... existing code ...
def send_daily_email_report(holdings, trades, history):
    print(f"\n{'─'*50}\nSTEP 5. 리포트 메일 발송 중...\n{'─'*50}")
    today_str = now_kst().strftime("%Y-%m-%d")

    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("  [x] 이메일 발송 실패: SMTP_EMAIL 또는 SMTP_PASSWORD가 설정되지 않았습니다.")
        return

    # 1. 오늘 발생한 매매 분리
    today_sells = [t for t in trades if t.get('type') == 'SELL' and t.get('trade_date') == today_str]
# ... existing code ...
    # 5. SMTP 메일 발송 실행
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_EMAIL
        msg['To'] = 'ansdyd23@kakao.com'

        # 메일 제목
        msg['Subject'] = f'[QUANT DESK] ⚡ Avg {avg_total_ret:+.1f}% · 매수{len(today_buys)}·매도{len(today_sells)} · {today_str}'

        msg.attach(MIMEText(email_body, 'html'))

        # 네이버 SMTP 전송
        server = smtplib.SMTP("smtp.naver.com", 587)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)

        to_addrs = ["ansdyd23@kakao.com"]
        server.sendmail(SMTP_EMAIL, to_addrs, msg.as_string())
        server.quit()

        print("  [✓] HTML 리포트 이메일을 성공적으로 전송했습니다.")
    except Exception as e:
        print(f"  [x] 이메일 발송 실패: {e}")


# API Helper Functions
def load_dart_corp_map(dart_api_key: str) -> dict:
# ... existing code ...
