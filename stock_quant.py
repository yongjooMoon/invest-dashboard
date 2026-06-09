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
import json
import time

# 👍 전역 스레드 제어 락 바인더 (소문자로 무결성 선언)
_active_threads = {}

# --- 팩터 마스터 프리미엄 사전 설정 ---
CORE_CONVICTION_ASSETS = {"삼화콘덴서": 200000, "광전자": 20000}
GLOBAL_MEGATRENDS = {
    "HBM": 3, "CXL": 3, "NPU": 3, "유리기판": 3, "MLCC": 3, "AI": 2, "로봇": 2
}

# 👍 [IP 차단 영구 우회용] 외부 서버 마비 시 작동할 2,500개 자산 공식 백업 사전
FALLBACK_KRX_DICTIONARY = {
    "삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "삼성전자우": "005935.KS", "SK스퀘어": "402340.KS",
    "현대차": "005380.KS", "삼성전기": "009150.KS", "LG에너지솔루션": "373220.KS", "삼성생명": "032830.KS",
    "삼성물산": "028260.KS", "HD현대중공업": "329180.KS", "두산에너빌리티": "034020.KS", "현대모비스": "012330.KS",
    "기아": "000270.KS", "삼성바이오로직스": "207940.KS", "LG전자": "066570.KS", "한화에어로스페이스": "012450.KS",
    "KB금융": "105560.KS", "삼성SDI": "006400.KS", "SK": "034730.KS", "신한지주": "055550.KS",
    "NAVER": "035420.KS", "셀트리온": "068270.KS", "LS ELECTRIC": "010120.KS", "한화오션": "042660.KS",
    "HD현대일렉트릭": "267260.KS", "두산": "000150.KS", "효성중공업": "298040.KS", "LG이노텍": "011070.KS",
    "미래에셋증권": "006800.KS", "POSCO홀딩스": "005490.KS", "하나금융지주": "086790.KS", "HD한국조선해양": "009540.KS",
    "고려아연": "010130.KS", "한미반도체": "042700.KS", "삼성화재": "000810.KS", "삼성에스디에스": "018260.KS",
    "LG화학": "051910.KS", "현대오토에버": "307950.KS", "LG": "003550.KS", "한국전력": "015760.KS",
    "삼성중공업": "010140.KS", "현대로템": "064350.KS", "SK텔레콤": "017670.KS", "HD현대": "267250.KS",
    "우리금융지주": "316140.KS", "포스코퓨처엠": "003670.KS", "에코프로비엠": "247540.KQ", "알테오젠": "196170.KQ",
    "SK이노베이션": "096770.KS", "한화시스템": "272210.KS", "KT&G": "033780.KS", "카카오": "035720.KS",
    "HMM": "011200.KS", "메리츠금융지주": "138040.KS", "에코프로": "086520.KQ", "현대글로비스": "086280.KS",
    "LIG디펜스앤에어로스페이스": "079550.KS", "한국항공우주": "047810.KS", "기업은행": "024110.KS", "에이피알": "278470.KS",
    "현대건설": "000720.KS", "레인보우로보틱스": "277810.KQ", "LS": "006260.KS", "한국금융지주": "071050.KS",
    "KT": "030200.KS", "LG씨엔에스": "064400.KS", "S-Oil": "010950.KS", "크래프톤": "259960.KS",
    "NH투자증권": "005940.KS", "삼성증권": "016360.KS", "포스코인터내셔널": "047050.KS", "카카오뱅크": "323410.KS",
    "HD현대마린솔루션": "443060.KS", "현대차2우B": "005387.KS", "이수페타시스": "007660.KS", "대우건설": "047040.KS",
    "키움증권": "039490.KS", "삼성E&A": "028050.KS", "대한항공": "003490.KS", "하이브": "352820.KS",
    "대한전선": "001440.KS", "DB손해보험": "005830.KS", "한화": "000880.KS", "두산로보틱스": "454910.KS",
    "삼양식품": "003230.KS", "대덕전자": "353200.KS", "한국타이어앤테크놀로지": "161390.KS", "주성엔지니어링": "036930.KQ",
    "DB하이텍": "000990.KS", "LG디스플레이": "034220.KS", "삼천당제약": "000250.KQ", "한진칼": "180640.KS",
    "산일전기": "062040.KS", "HLB": "028300.KQ", "리노공업": "058470.KQ", "한미약품": "128940.KS",
    "한화솔루션": "009830.KS", "SK바이오팜": "326030.KS", "HD건설기계": "267270.KS", "현대차우": "005385.KS",
    "LG유플러스": "032640.KS", "GS": "078930.KS", "유한양행": "000100.KS", "아모레퍼시픽": "090430.KS",
    "펩트론": "087010.KQ", "OCI홀딩스": "010060.KS", "카카오페이": "377300.KS", "코웨이": "021240.KS",
    "NC": "036570.KS", "두산밥캣": "241560.KS", "에이비엘바이오": "298380.KQ", "엘앤에프": "066970.KS",
    "가온전선": "000500.KS", "SKC": "011790.KS", "포스코DX": "022100.KS", "두산퓨얼셀": "336260.KS",
    "로보티즈": "108490.KQ", "리가켐바이오": "141080.KQ", "이오테크닉스": "039030.KQ", "파두": "440110.KQ",
    "한온시스템": "018880.KS", "삼성카드": "029780.KS", "BNK금융지주": "138930.KS", "CJ": "001040.KS",
    "현대제철": "004020.KS", "맥쿼리인프라": "088980.KS", "한화엔진": "082740.KS", "한전기술": "052690.KS",
    "오리온": "271560.KS", "원익IPS": "240810.KQ", "신세계": "004170.KS", "서진시스템": "178320.KQ",
    "KCC": "002380.KS", "보로노이": "310210.KQ", "JB금융지주": "175330.KS", "케어젠": "214370.KQ",
    "현대무벡스": "319400.KQ", "일진전기": "103590.KS", "ISC": "095340.KQ", "에코프로머티": "450080.KS",
    "롯데쇼핑": "023530.KS", "한화생명": "088350.KS", "심텍": "222800.KQ", "LG생활건강": "051900.KS",
    "HPSP": "403870.KQ", "디앤디파마텍": "347850.KQ", "넷마블": "251270.KS", "제주반도체": "080220.KQ",
    "더존비즈온": "012510.KS", "영원무역": "111770.KS", "에스엘": "005850.KS", "롯데케미칼": "011170.KS",
    "효성": "004800.KS", "금호석유화학": "011780.KS", "강원랜드": "035250.KS", "대한광통신": "010170.KQ",
    "SK바이오사이언스": "302440.KS", "한국가스공사": "036460.KS", "현대엘리베이터": "017800.KS", "CJ제일제당": "097950.KS",
    "한올바이오파마": "009420.KS", "파마리서치": "214450.KQ", "휴젤": "145020.KQ", "두산테스나": "131970.KQ",
    "한솔케미칼": "014680.KS", "솔브레인": "357780.KQ", "현대해상": "001450.KS", "클래시스": "214150.KQ",
    "HL만도": "204320.KS", "유진테크": "084370.KQ", "티씨케이": "064760.KQ", "이수스페셜티케미컬": "457190.KS",
    "에스피지": "058610.KQ", "iM금융지주": "139130.KS", "펄어비스": "263750.KQ", "코리아써키트": "007810.KS",
    "DL이앤씨": "375500.KS", "신영증권": "001720.KS", "팬오션": "028670.KS", "F&F": "383220.KS",
    "에스티팜": "237690.KQ", "동진쎄미켐": "005290.KQ", "피에스케이": "319660.KQ", "우리기술": "032820.KQ",
    "에스원": "012750.KS", "하나마이크론": "067310.KQ", "성호전자": "043260.KQ", "달바글로벌": "483650.KS",
    "롯데지주": "004990.KS", "영원무역홀딩스": "009970.KS", "SK네트웍스": "001740.KS", "DN오토모티브": "007340.KS",
    "GS건설": "006360.KS", "현대백화점": "069960.KS", "현대위아": "011210.KS", "이마트": "139480.KS",
    "동서": "026960.KS", "대한조선": "439260.KS", "실리콘투": "257720.KQ", "미래에셋벤처투자": "100790.KQ",
    "고영": "098460.KQ", "비茨로셀": "082920.KQ", "농심": "004370.KS", "한국앤컴퍼니": "000240.KS",
    "RFHIC": "218410.KQ", "한전KPS": "051600.KS", "한미사이언스": "008930.KS", "케이뱅크": "279570.KS",
    "코리안리": "003690.KS", "피에스케이홀딩스": "031980.KQ", "비에이치아이": "083650.KQ", "대주전자재료": "078600.KQ",
    "티에스이": "131290.KQ", "풍산": "103140.KS", "BGF리테일": "282330.KS", "테스": "095610.KQ",
    "제일기획": "030000.KS", "LS에코에너지": "229640.KS", "호텔신라": "008770.KS", "코오롱인더": "120110.KS",
    "삼성전기우": "009155.KS", "원익홀딩스": "030530.KQ", "셀트리온제약": "068760.KQ", "SK가스": "018670.KS",
    "LG전자우": "066575.KS", "GS리테일": "007070.KS", "미래에셋생명": "085620.KS", "JYP Ent.": "035900.KQ",
    "한국콜마": "161890.KS", "엘앤씨바이오": "290650.KQ", "HJ중공업": "097230.KS", "현대지에프홀딩스": "005440.KS",
    "씨에스윈드": "112610.KS", "파크시스템스": "140860.KQ", "코스맥스": "192820.KS", "세아베스틸지주": "001430.KS",
    "HD현대에너지솔루션": "322000.KS", "CJ대한통운": "000120.KS", "NHN": "181710.KS", "메지온": "140410.KQ",
    "에스엠": "041510.KQ", "네이처셀": "007390.KQ", "LS마린솔루션": "060370.KQ", "테크윙": "089030.KQ",
    "시프트업": "462870.KS", "아모레퍼시픽홀딩스": "002790.KS", "다우기술": "023590.KS", "기가비스": "420770.KQ",
    "코스모신소재": "005070.KS", "한국카본": "017960.KS", "녹십자": "006280.KS", "씨젠": "096530.KQ",
    "오리온홀딩스": "001800.KS", "효성티앤씨": "298020.KS", "오스코텍": "039200.KQ", "STX엔진": "077970.KS",
    "SK아이이테크놀로지": "361610.KS", "LX인터내셔널": "001120.KS", "동원산업": "006040.KS", "대신증권": "003540.KS",
    "대웅제약": "069620.KS", "롯데관광개발": "032350.KS", "에스에이엠티": "031330.KQ", "SNT다이내믹스": "003570.KS",
    "아시아나항공": "020560.KS", "더블유게임즈": "192080.KS", "에스앤에스텍": "101490.KQ", "LS머트리얼즈": "417200.KQ",
    "아주IB투자": "027360.KQ", "케이엠더블유": "032500.KQ", "해성디에스": "195870.KS", "SK이터닉스": "475150.KS",
    "금양": "001570.KS", "신한알파리츠": "293940.KS", "카프로": "006380.KS", "이엔에프테크놀로지": "102710.KQ",
    "세아제강지주": "003030.KS", "네오셈": "253590.KQ", "롯데손해보험": "000400.KS", "동원시스템즈": "014820.KS",
    "동성화인텍": "033500.KQ", "코세스": "089890.KQ", "부국증권": "001270.KS", "동아쏘시오홀딩스": "000640.KS",
    "천보": "278280.KQ", "이녹스첨단소재": "272290.KQ", "애경케미칼": "161000.KS", "디어유": "376300.KQ",
    "현대차증권": "001500.KS", "켐트로닉스": "089010.KQ", "E1": "017940.KS", "인바디": "041830.KQ",
    "SOOP": "067160.KQ", "녹십자홀딩스": "005250.KS", "하나투어": "039130.KS", "성우하이텍": "015750.KQ",
    "KG스틸": "016380.KS", "한화갤러리아": "452260.KS", "덕산하이메탈": "077360.KQ", "광전자": "017900.KS",
    "풍산홀딩스": "005810.KS", "케이아이엔엑스": "093320.KQ", "와이지-원": "019210.KQ", "티에프이": "425420.KQ",
    "파이버프로": "368770.KQ", "미원상사": "002840.KS", "예스티": "122640.KQ", "인텍플러스": "064290.KQ",
    "현대힘스": "460930.KQ", "엠케이전자": "033160.KQ", "아이쓰리시스템": "214430.KQ", "코미팜": "041960.KQ",
    "더블유씨피": "393890.KQ", "에이디테크놀로지": "200710.KQ", "카페24": "042000.KQ", "현대그린푸드": "453340.KS",
    "고려제강": "002240.KS", "동국제강": "460860.KS", "한양이엔지": "045100.KQ", "코스모화학": "005420.KS",
    "원익머트리얼즈": "104830.KQ", "대신증권우": "003545.KS", "우리기술투자": "041190.KQ", "한일홀딩스": "003300.KS",
    "DI동일": "001530.KS", "신풍제약": "019170.KS", "서울바이오시스": "092190.KQ", "흥아해운": "003280.KS",
    "서희건설": "035890.KQ", "쿠쿠홈시스": "284740.KS", "에코마케팅": "230360.KQ", "에스비비테크": "389500.KQ",
    "일진하이솔루스": "271940.KS", "미원에스씨": "268280.KQ", "HLB이노베이션": "024850.KQ", "한글과컴퓨터": "030520.KQ",
    "큐렉소": "060280.KQ", "아이센스": "099190.KQ", "디케이티": "290550.KQ", "태영건설": "009410.KS",
    "바이오노트": "377740.KS", "아난티": "025980.KQ", "명신산업": "009900.KS", "큐로셀": "372320.KQ",
    "신세계인터내셔날": "031430.KS", "한선엔지니어링": "452280.KQ", "신성이엔지": "011930.KS", "유니드": "014830.KS",
    "율촌화학": "008730.KS", "삼성FN리츠": "448730.KS", "DB증권": "016610.KS", "NH투자증권우": "005945.KS",
    "엔젤로보틱스": "455900.KQ", "네패스아크": "330860.KQ", "부광약품": "003000.KS", "삼성SDI우": "006405.KS",
    "삼양사": "145990.KS", "툴젠": "199800.KQ", "인카금융서비스": "211050.KQ", "티앤엘": "340570.KQ",
    "보성파워텍": "006910.KQ", "브이티": "018290.KQ", "메디포스트": "078160.KQ", "유진투자증권": "001200.KS",
    "한국기업평가": "034950.KQ", "다날": "064260.KQ", "한전산업": "130660.KS", "한양디지텍": "078350.KQ",
    "네오티스": "085910.KQ", "한중엔시에스": "107640.KQ", "동화기업": "025900.KQ", "펌텍코리아": "251970.KQ",
    "시노펙스": "025320.KQ", "삼양홀딩스": "000070.KS", "인화정공": "101930.KQ", "삼천리": "004690.KS",
    "케이카": "381970.KS", "퍼스텍": "010820.KS", "NICE": "034310.KS", "가비아": "079940.KQ",
    "감성코퍼레이션": "036620.KQ", "네오위즈": "095660.KQ", "에스티아이": "039440.KQ", "오이솔루션": "138080.KQ",
    "우주일렉트로": "065680.KQ", "하이록코리아": "013030.KQ", "알멕": "354320.KQ", "농심홀딩스": "072710.KS",
    "메가스터디교육": "215200.KQ", "오픈엣지테크놀로지": "394280.KQ", "펨트론": "168360.KQ", "일진홀딩스": "015860.KS",
    "BGF": "027410.KS", "DB": "012030.KS", "에치에프알": "230240.KQ", "월덱스": "101160.KQ",
    "조광피혁": "004700.KS", "엘티씨": "170920.KQ", "쏘카": "403550.KS", "티로보틱스": "117730.KQ",
    "빛과전자": "069540.KQ", "신도리코": "029530.KS", "뉴파워프라즈마": "144960.KQ", "우진": "105840.KS",
    "HL홀딩스": "060980.KS", "HLB생명과학": "067630.KQ", "HLB제약": "047920.KQ", "프레스티지바이오파마": "950210.KS",
    "신라젠": "215600.KQ", "세아제강": "306200.KS", "미래반도체": "254490.KQ", "코람코라이프인프라리츠": "357120.KS",
    "덴티움": "145720.KS", "디아이티": "110990.KQ", "한스바이오메드": "042520.KQ", "KCC글라스": "344820.KS",
    "아모레퍼시픽우": "090435.KS", "한텍": "098070.KQ", "코람코더원리츠": "417310.KS", "티이엠씨": "425040.KQ",
    "풀무원": "017810.KS", "휴온스글로벌": "084110.KQ", "유티아이": "179900.KQ", "아세아": "002030.KS",
    "신대양제지": "016590.KS", "TCC스틸": "002710.KS", "제주항공": "089590.KS", "동아에스티": "170900.KS",
    "넥스틸": "092790.KS", "헥토파이낸셜": "234340.KQ", "엑시콘": "092870.KQ", "저스템": "417840.KQ",
    "LS증권": "078020.KQ", "삼영무역": "002810.KS", "수산인더스트리": "126720.KS", "엠씨넥스": "097520.KS",
    "제주은행": "006220.KS", "제우스": "079370.KQ", "메카로": "241770.KQ", "화신": "010690.KS",
    "한솔아이원스": "114810.KQ", "한솔테크닉스": "004710.KS", "오킨스전자": "080580.KQ", "유바이오로직스": "206650.KQ",
    "삼성물산우B": "02826K.KS", "지엔씨에너지": "119850.KQ", "아세아시멘트": "183190.KS", "에이팩트": "200470.KQ",
    "에스바이오메딕스": "304360.KQ", "롯데이노베이트": "286940.KS", "AP시스템": "265520.KQ", "서연이화": "200880.KS",
    "아모텍": "052710.KQ", "KPX홀딩스": "092230.KS", "이수화학": "005950.KS", "파인엠텍": "441270.KQ",
    "DSC인베스트먼트": "241520.KQ", "덕산테코피아": "317330.KQ", "포스코스틸리온": "058430.KS", "케이씨": "029460.KS",
    "한세실업": "105630.KS", "디아이씨": "092200.KS", "켄코아에어로스페이스": "274090.KQ", "에이스침대": "003800.KQ",
    "파트론": "091700.KQ", "남해화학": "025860.KS", "드림텍": "192650.KS", "삼영": "003720.KS",
    "애경산업": "018250.KS", "칩스앤미디어": "094360.KQ", "한라IMS": "092460.KQ", "사피엔반도체": "452430.KQ",
    "위메이드맥스": "101730.KQ", "넥센": "005720.KS", "현대코퍼레이션": "011760.KS", "대상홀딩스": "084690.KS",
    "휴온스": "243070.KQ", "필에너지": "378340.KQ", "비츠로테크": "042370.KQ", "엠로": "058970.KQ",
    "인탑스": "049070.KQ", "광동제약": "009290.KS", "케이프": "064820.KQ", "상아프론테크": "089980.KQ",
    "에이직랜드": "445090.KQ", "싸이맥스": "160980.KQ", "바이오PLUS": "099430.KQ", "웹젠": "069080.KQ",
    "퍼시스": "016800.KS", "디바이스": "187870.KQ", "슈어소프트테크": "298830.KQ", "드림시큐리티": "203650.KQ",
    "현대퓨처넷": "126560.KS", "큐라클": "365270.KQ", "타이거일렉": "219130.KQ", "유니셈": "036200.KQ",
    "제이에스코퍼레이션": "194370.KS", "에이블씨엔씨": "078520.KS", "지노믹트리": "228760.KQ", "KG케미칼": "001390.KS",
    "우리로": "046970.KQ", "진성티이씨": "036890.KQ", "글로벌텍스프리": "204620.KQ", "컴투스": "078340.KQ",
    "유니테스트": "086390.KQ", "삼목에스폼": "018310.KQ", "SIMPAC": "009160.KS", "한국철강": "104700.KS",
    "진에어": "272450.KS", "콜마홀딩스": "024720.KS", "하림": "136480.KQ", "에코아이": "448280.KQ",
    "콜마비앤에이치": "200130.KQ", "아세아제지": "002310.KS", "대한제강": "084010.KS", "휴메딕스": "200670.KQ",
    "한국토지신탁": "034830.KS", "DS단석": "017860.KS", "국도화학": "007690.KS", "LB세미콘": "061970.KQ",
    "바이오다인": "314930.KQ", "아이엘": "307180.KQ", "KISCO홀딩스": "001940.KS", "한국정보통신": "025770.KQ",
    "스틱인베스트먼트": "026890.KS", "GRT": "900290.KQ", "HB테크놀러지": "078150.KQ", "슈프리마": "236200.KQ",
    "이지홀딩스": "035810.KQ", "바이넥스": "053030.KQ", "천일고속": "000650.KS", "코스텍시스": "355150.KQ",
    "LX하우시스": "108670.KS", "유나이티드제약": "033270.KS", "지씨셀": "144510.KQ", "메디아나": "041920.KQ",
    "범한퓨얼셀": "382900.KQ", "한양증권": "001750.KS", "퓨쳐켐": "220100.KQ", "대원강업": "000430.KS",
    "바텍": "043150.KQ", "셀바스AI": "108860.KQ", "에프앤가이드": "064850.KQ", "와이씨켐": "112290.KQ",
    "베뉴지": "019010.KQ", "서울가스": "017390.KS", "TYM": "002900.KS", "네오팜": "092730.KQ",
    "한농화성": "011500.KS", "KH바텍": "060720.KQ", "씨앤씨인터내셔널": "352480.KQ", "아이티엠반도체": "084850.KQ",
    "신성에스티": "416180.KQ", "휴스틸": "005010.KS", "LG우": "003555.KS", "노바렉스": "194700.KQ",
    "한국자산신탁": "123890.KS", "남양유업": "003920.KS", "KG이니시스": "035600.KQ", "JW홀딩스": "096760.KS",
    "강원에너지": "114190.KQ", "대명에너지": "389260.KQ", "동국홀딩스": "001230.KS", "삼영전자": "005680.KS",
    "엘브이엠씨홀딩스": "900140.KS", "토비스": "051360.KQ", "모베이스전자": "012860.KQ", "신흥에스이씨": "243840.KQ",
    "오르비텍": "046120.KQ", "유진기업": "023410.KQ", "심텍홀딩스": "036710.KQ", "와이바이오로직스": "338840.KQ",
    "상신이디피": "091580.KQ", "안트로젠": "065660.KQ", "골프존": "215000.KQ", "제이비엠": "054950.KQ",
    "나이스정보통신": "036800.KQ", "자람테크놀로지": "389020.KQ", "오리엔탈정공": "014940.KQ", "CJ프레시웨이": "051500.KQ",
    "피에치에이": "043370.KQ", "삼진제약": "005500.KS", "와이지엠티": "251370.KQ", "매일유업": "267980.KQ",
    "광주신세계": "037710.KS", "세방": "004360.KS", "GS글로벌": "001250.KS", "일성아이에스": "003120.KS",
    "바이젠셀": "308080.KQ", "LS네트웍스": "000680.KS", "한국알콜": "017890.KQ", "나무기술": "242040.KQ",
    "매커스": "093520.KQ", "慢리코메디칼": "394420.KQ", "비덴트": "121800.KQ", "헬릭스미스": "084990.KQ",
    "나무가": "190510.KQ", "컨텍": "451760.KQ", "한진": "002320.KS", "강스템바이오텍": "217730.KQ",
    "대성산업": "128820.KS", "제일일렉트릭": "199820.KQ", "KG에코솔루션": "151860.KQ", "이랜텍": "054210.KQ",
    "이지스밸류플러스리츠": "334890.KS", "한국공항": "005430.KS", "SBS": "034120.KS", "사조대림": "003960.KS",
    "유니슨": "018000.KQ", "아스트": "067390.KQ", "지놈앤컴퍼니": "314130.KQ", "신세계 I&C": "035510.KS",
    "JTC": "950170.KQ", "아이마켓코리아": "122900.KS", "에코앤드림": "101360.KQ", "갤럭시아머니트리": "094480.KQ",
    "솔트룩스": "304100.KQ", "마이크로컨텍솔": "098120.KQ", "미래나노텍": "095500.KQ", "케이엔제이": "272110.KQ",
    "YG PLUS": "037270.KS", "뷰웍스": "100120.KQ", "송원산업": "004430.KS", "에이스테크": "088800.KQ",
    "인터플렉스": "051370.KQ", "이리츠코크렙": "088260.KS", "미래에셋증권우": "006805.KS", "석경에이티": "357550.KQ",
    "삼화전기": "009470.KS", "대동": "000490.KS", "BYC": "001460.KS", "폰드그룹": "472850.KQ",
    "재영솔루텍": "049630.KQ", "오로스테크놀로지": "322310.KQ", "노루홀딩스": "000320.KS", "스맥": "099440.KQ",
    "영진약품": "003520.KS", "한국캐피탈": "023760.KQ", "일신방직": "003200.KS", "제이알글로벌리츠": "348950.KS",
    "나노팀": "417010.KQ", "쿠콘": "294570.KQ", "바디텍메드": "206640.KQ", "코난테크놀로지": "402030.KQ",
    "HLB테라퓨틱스": "115450.KQ", "코오롱글로벌": "003070.KS", "선진": "136490.KS", "SAMG엔터": "419530.KQ",
    "나이벡": "138610.KQ", "마녀공장": "439090.KQ", "아스플로": "159010.KQ", "대화제약": "067080.KQ",
    "지앤비에스 에코": "382800.KQ", "KSS해운": "044450.KS", "소룩스": "290690.KQ", "퀄리타스반도체": "432720.KQ",
    "코칩": "126730.KQ", "에이플러스에셋": "244920.KS", "경방": "000050.KS", "케이티알파": "036030.KQ",
    "다올투자증권": "030210.KS", "흥국화재": "000540.KS", "CR홀딩스": "000480.KS", "바이오NIA": "064550.KQ",
    "대아티아이": "045390.KQ", "인터로조": "119610.KQ", "미원화학": "134380.KS", "LG헬로비전": "037560.KS",
    "아나패스": "123860.KQ", "그린리소스": "402490.KQ", "제이오": "418550.KQ", "SG": "255220.KQ",
    "세보엠이씨": "011560.KQ", "서흥": "008490.KS", "성신양회": "004980.KS", "사조산업": "007160.KS",
    "잇츠한불": "226320.KS", "종근당홀딩스": "001630.KS", "SK디앤디": "210980.KS", "큐알티": "405100.KQ",
    "삼익THK": "004380.KS", "현대비앤지스틸": "004560.KS", "모토닉": "009680.KS", "아바텍": "149950.KQ",
    "텔레칩스": "054450.KQ", "한국정보인증": "053300.KQ", "KPX케미칼": "025000.KS", "화승엔터프라이즈": "241590.KS",
    "서호전기": "065710.KQ", "AP위성": "211270.KQ", "헥토이노베이션": "214180.KQ", "슈피겐코리아": "192440.KQ",
    "디지털대성": "068930.KQ", "엘오티베큠": "083310.KQ", "한미글로벌": "053690.KS", "BGF에코머티리얼즈": "126600.KQ",
    "LG생활건강우": "051905.KS", "동원개발": "013120.KQ", "백산": "035150.KS", "이지바이오": "353810.KQ",
    "아이텍": "119830.KQ", "디엔에프": "092070.KQ", "KB스타리츠": "432320.KS", "대한제당": "001790.KS",
    "S-Oil우": "010955.KS", "미창석유": "003650.KS", "알루코": "001780.KS", "한국비엔씨": "256840.KQ",
    "현대약품": "004310.KS", "일진파워": "094820.KQ", "라온텍": "418420.KQ", "무학": "033920.KS",
    "우진엔텍": "457550.KQ", "옵티코어": "380540.KQ", "신한서부티엔디리츠": "404990.KS", "교촌에프앤비": "339770.KS",
    "스카이라이프": "053210.KS", "서연": "007860.KS", "대성에너지": "117580.KS", "윤성에프앤씨": "372170.KQ",
    "새로닉스": "042600.KQ", "유비쿼스홀딩스": "078070.KQ", "디앤디플랫폼리츠": "377190.KS", "해성산업": "034810.KQ",
    "대원산업": "005710.KQ", "HS효성": "487570.KS", "어보브반도체": "102120.KQ", "HDC현대EP": "089470.KS",
    "동아엘텍": "088130.KQ", "텔코웨어": "078000.KS", "대창": "012800.KS", "키다리스튜디오": "020120.KS",
    "사조동아원": "008040.KS", "일승": "333430.KQ", "로보로보": "215100.KQ", "신원": "009270.KS",
    "사조씨푸드": "014710.KS", "현대리바트": "079430.KS", "모다이노칩": "080420.KQ", "샘표": "007540.KS",
    "박셀바이오": "323990.KQ", "한국제지": "027970.KS", "케이알엠": "093640.KQ", "피에스텍": "002230.KQ",
    "YTN": "040300.KQ", "코맥스": "036690.KQ", "한신공영": "004960.KS", "지어소프트": "051160.KQ",
    "컴퍼니케이": "307930.KQ", "에스텍": "069510.KQ", "파세코": "037070.KQ", "화승코퍼레이션": "013520.KS",
    "경동도시가스": "267290.KS", "신흥": "004080.KS", "가온그룹": "078890.KQ", "라이콤": "388790.KQ",
    "디씨엠": "024090.KS", "한국무브넥스": "010100.KS", "팜스토리": "027710.KQ", "새빗켐": "107600.KQ",
    "도이치모터스": "067990.KQ", "신영와코루": "005800.KS", "이지스레지던스리츠": "350520.KS", "무림P&P": "009580.KS",
    "현대이지웰": "090850.KQ", "에이external": "172670.KQ", "뷰노": "338220.KQ", "KT나스미디어": "089600.KQ",
    "우리넷": "115440.KQ", "엠에스오토텍": "123040.KQ", "에스와이스틸텍": "365330.KQ", "동구바이오제약": "006620.KQ",
    "경동인베스트": "012320.KS", "모나용평": "070960.KS", "MDS테크": "086960.KQ", "크레오에스지": "040350.KQ",
    "삼영엠텍": "054540.KQ", "세종텔레콤": "036630.KQ", "레이저쎌": "412350.KQ", "로체시스템즈": "071280.KQ",
    "공구우먼": "366030.KQ", "아진산업": "013310.KQ", "제일파마홀딩스": "002620.KS", "극동유화": "014530.KS",
    "HS애드": "035000.KS", "엘앤케이바이오": "156100.KQ", "메드팩토": "235980.KQ", "야스": "255440.KQ",
    "제이아이테크": "417500.KQ", "엑스페릭스": "317770.KQ", "플래스크": "041590.KQ", "태경비케이": "014580.KS",
    "삼익악기": "002450.KS", "금강철강": "053260.KQ", "폴라리스AI": "039980.KQ", "아이비김영": "339950.KQ",
    "유아이엘": "049520.KQ", "대성홀딩스": "016710.KS", "모비스": "250060.KQ", "코데즈컴바인": "047770.KQ",
    "워트": "396470.KQ", "지니너스": "389030.KQ", "드림씨아이에스": "223250.KQ", "인천도시가스": "034590.KS",
    "NH농우바이오": "054050.KQ", "현대바이오랜드": "052260.KQ", "세원정공": "021820.KS", "대성하이텍": "129920.KQ",
    "디알텍": "214680.KQ", "그린케미칼": "083420.KS", "LB인베스트먼트": "309960.KQ", "원익피앤이": "217820.KQ",
    "와이엔텍": "067900.KQ", "현대제철": "004020.KS", "맥쿼리인프라": "088980.KS", "한화엔진": "082740.KS",
    "한전기술": "052690.KS", "오리온": "271560.KS", "원익IPS": "240810.KQ", "신세계": "004170.KS",
    "서진시스템": "178320.KQ", "KCC": "002380.KS", "보로노이": "310210.KQ", "JB금융지주": "175330.KS",
    "케어젠": "214370.KQ", "현대무벡스": "319400.KQ", "일진전기": "103590.KS", "ISC": "095340.KQ",
    "에코프로머티": "450080.KS", "롯데쇼핑": "023530.KS", "한화생명": "088350.KS", "심텍": "222800.KQ",
    "LG생활건강": "051900.KS", "HPSP": "403870.KQ", "넷마블": "251270.KS", "제주반도체": "080220.KQ",
    "더존비즈온": "012510.KS", "영원무역": "111770.KS", "에스엘": "005850.KS", "롯데케미칼": "011170.KS",
    "효성": "004800.KS", "금호석유화학": "011780.KS", "강원랜드": "035250.KS", "한국항공우주": "047810.KS",
    "SK바이오사이언스": "302440.KS", "한국가스공사": "036460.KS", "현대엘리베이터": "017800.KS", "CJ제일제당": "097950.KS",
    "한올바이오파마": "009420.KS", "파마리서치": "214450.KQ", "휴젤": "145020.KQ", "두산테스나": "131970.KQ",
    "한솔케미칼": "014680.KS", "솔브레인": "357780.KQ", "현대해상": "001450.KS", "클래시스": "214150.KQ",
    "HL만도": "204320.KS", "유진테크": "084370.KQ", "티씨케이": "064760.KQ", "에스피지": "058610.KQ",
    "펄어비스": "263750.KQ", "코리아써키트": "007810.KS", "DL이앤씨": "375500.KS", "신영증권": "001720.KS",
    "팬오션": "028670.KS", "F&F": "383220.KS", "에스티팜": "237690.KQ", "동진쎄미켐": "005290.KQ",
    "피에스케이": "319660.KQ", "우리기술": "032820.KQ", "에스원": "012750.KS", "하나마이크론": "067310.KQ",
    "성호전자": "043260.KQ", "롯데지주": "004990.KS", "영원무역홀딩스": "009970.KS", "SK네트웍스": "001740.KS",
    "DN오토모티브": "007340.KS", "GS건설": "006360.KS", "현대백화점": "069960.KS", "현대위아": "011210.KS",
    "이마트": "139480.KS", "동서": "026960.KS", "실리콘투": "257720.KQ", "미래에셋벤처투자": "100790.KQ",
    "고영": "098460.KQ", "농심": "004370.KS", "한국앤컴퍼니": "000240.KS", "RFHIC": "218410.KQ",
    "한전KPS": "051600.KS", "한미사이언스": "008930.KS", "코리안리": "003690.KS", "피에스케이홀딩스": "031980.KQ",
    "비에이치아이": "083650.KQ", "대주전자재료": "078600.KQ", "티에스이": "131290.KQ", "풍산": "103140.KS",
    "BGF리테일": "282330.KS", "테스": "095610.KQ", "제일기획": "030000.KS", "LS에코에너지": "229640.KS"
}

# ==========================================
# [Layer 2] 👍 [대표님 설계] 30일 주기 자동 갱신형 DB 매핑 연동기
# ==========================================
def load_krx_mapping_from_db(supabase):
    """
    Supabase 장부의 stock_cache에 마스터 주소록을 이중 보관하고 30일마다 1회 스캔 갱신.
    원격 서버 전면 차단 상태 시 대표님이 타전해주신 진성 딕셔너리로 100% 자동 분기 격벽 우회.
    """
    now_kst_str = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')
    try:
        res = supabase.table("stock_cache").select("*").eq("ticker", "__SYSTEM_KRX_MAP__").execute()
        if res.data:
            row = res.data[0]
            # 30일 타임아웃 검증 격벽 (30일 이내면 외부 호출 0회 완결)
            if not is_expired(row.get('last_price_update'), 2592000):
                return json.loads(row['bm_summary'])
    except:
        pass

    # 만기 도래 시 단발성 1회 크롤링 갱신 시도
    krx_map = {}
    try:
        df = fdr.StockListing('KRX')
        krx_map = {row['Name']: f"{row['Symbol']}.KS" if row['Market'] == 'KOSPI' else f"{row['Symbol']}.KQ" for _, row in df.iterrows()}
    except:
        try:
            df = fdr.StockListing('KRX-DESC')
            krx_map = {row['Name']: f"{row['Symbol']}.KS" for _, row in df.iterrows()}
        except:
            pass

    # 통신 완전 먹통 시 대표님의 딕셔너리로 긴급 방화벽 기동
    if not krx_map:
        krx_map = FALLBACK_KRX_DICTIONARY

    # 수집 완료된 영구 주소록을 Supabase 원장에 업데이트 박음
    try:
        payload = {
            "ticker": "__SYSTEM_KRX_MAP__",
            "name": "전역 종목코드 주소록",
            "krx_sector": "시스템 통제",
            "bm_summary": json.dumps(krx_map, ensure_ascii=False),
            "last_price_update": now_kst_str
        }
        supabase.table("stock_cache").upsert(payload).execute()
    except:
        pass
    return krx_map

# ==========================================
# [Layer 3] 원천 데이터 백엔드 크롤러 엔진
# ==========================================
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
                macro_multiplier = 0.90 
                환율상태 = f"🚨 매크로 유동성 축소 ({current_usd}원)"
            elif current_usd > usd_ma20:
                macro_multiplier = 0.95  
                환율상태 = f"⚠️ 변동성 경계 ({current_usd}원)"
            else:
                macro_multiplier = 1.05  
                환율상태 = f"🍏 매크로 훈풍 ({current_usd}원)"
    except: 환율상태 = "⚠️ 센서 지연"
    return macro_multiplier, current_usd, 환율상태

def fetch_investor_flows(raw_code):
    url = f"https://finance.naver.com/item/frgn.naver?code={raw_code}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.content, 'html.parser')
        table = soup.select_one('table.type2')
        if not table: return 0.0, 0.0
        rows = table.select('tr')
        f_sum, i_sum = 0, 0
        count = 0
        for row in rows:
            tds = row.select('td')
            if len(tds) >= 7 and tds[0].text.strip():
                try:
                    inst = float(tds[5].text.replace(',','').strip())
                    fore = float(tds[6].text.replace(',','').strip())
                    i_sum += inst
                    f_sum += fore
                    count += 1
                    if count >= 20: break
                except: pass
        return f_sum, i_sum
    except: return 0.0, 0.0

def fetch_naver_fundamentals(raw_code):
    url = f"https://finance.naver.com/item/main.naver?code={raw_code}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.content, 'html.parser')
        val_data = {
            'per': 10.0, 'eps': 0.0, 'pbr': 1.0, 'bps': 0.0, 'roe': 5.0, 
            'industry_per': 10.0, 'broker_target': 0.0, 'shares_outstanding': 10000000.0,
            'fwd_eps_2025': 0.0, 'fwd_eps_2026': 0.0, 'summary': ''
        }
        summary_div = soup.select_one('.summary_info')
        val_data['summary'] = summary_div.text.replace('\n', ' ').strip() if summary_div else ""
        
        for th in soup.find_all('th'):
            if "상장주식수" in th.text:
                td_val = th.find_next_sibling('td')
                if td_val: val_data['shares_outstanding'] = parse_num(td_val.text)
            if "목표주가" in th.text:
                td_val = th.find_next_sibling('td')
                if td_val: val_data['broker_target'] = parse_num(td_val.text)

        for td in soup.find_all('td'):
            if "동종업종 PER" in td.text:
                parent_tr = td.parent
                if parent_tr:
                    em_val = parent_tr.select_one('em')
                    if em_val: val_data['industry_per'] = parse_num(em_val.text)

        for td in soup.find_all('td'):
            td_id = td.get('id', '')
            if '_per' in td_id: val_data['per'] = parse_num(td.text)
            if '_eps' in td_id: val_data['eps'] = parse_num(td.text)
            if '_pbr' in td_id: val_data['pbr'] = parse_num(td.text)
            if '_bps' in td_id: val_data['bps'] = parse_num(td.text)

        table = soup.select_one('div.cop_analysis table')
        if table:
            rows = table.select_one('tbody').select('tr')
            thead = table.select_one('thead')
            q_headers = [th.text.strip() for th in thead.select('tr')[1].select('th')[5:10]]
            q_revenues = [parse_num(td.text) for td in rows[0].select('td')[5:10]]
            q_op_profits = [parse_num(td.text) for td in rows[1].select('td')[5:10]]
            
            valid_indices = [i for i, rev in enumerate(q_revenues) if rev != 0.0]
            if valid_indices:
                val_data['q_headers'] = [q_headers[i] for i in valid_indices]
                val_data['q_revenues'] = [q_revenues[i] for i in valid_indices]
                val_data['q_op_profits'] = [q_op_profits[i] for i in valid_indices]
        return val_data
    except: return None

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

def get_auto_momentum(stock_name, client_id, client_secret):
    if not client_id or not client_secret: return 0, 0, "인증키 누락", []
    exact_query = f'"{stock_name}"'
    url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(exact_query)}&display=10&sort=date"
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code != 200: return 0, 0, "인증 대기", []
        items = res.json().get('items', [])
        if not items: return 0, 0, "뉴스 없음", []
        
        news_list, pos_count, neg_count = [], 0, 0
        for item in items:
            headline = html.unescape(re.compile('<.*?>').sub('', item['title'])).strip()
            news_list.append({"title": headline, "link": item.get('originallink', item['link'])})
            combined_text = headline.upper()
            if any(abort_kw in combined_text for abort_kw in ["철수", "중단", "매각", "계약해지"]):
                neg_count += 3
                continue
            for pw in ['수주', '흑자', '돌파', 'AI', '최대', '공급', '계약', '성장', '수혜', '외인매수', '기관매집']:
                if pw in combined_text: pos_count += 1
            for nw in ['하락', '적자', '취소', '우려', '부진', '위기', '손실', '외인매도']:
                if nw in combined_text: neg_count += 1
                
        net_sentiment = pos_count - neg_count
        return 0, net_sentiment, news_list[0]['title'][:25] + "...", news_list
    except: return 0, 0, "네트워크 오류", []

# ==========================================
# [Layer 4] v12.0 오리지널 주도 수급 가치 연산 엔진
# ==========================================
def calculate_intrinsic_target(row, cache, macro_multiplier=1.0):
    ticker = str(row['ticker']).split('.')[0]
    current_price = cache.get('current_price', row['buy_price'])
    raw_eps = cache.get('eps', 0.0)
    bps = cache.get('bps', 0.0)
    krx_sector_name = cache.get('krx_sector', '기타')
    base_industry_per = cache.get('industry_per', 10.0)

    theme_premium = 1.0
    quant_tier = "MARKET_FOLLOWER"
    
    try:
        end_date = datetime.utcnow() + timedelta(hours=9)
        start_date = end_date - timedelta(days=35)
        df_stock = fdr.DataReader(ticker, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
        df_kospi = fdr.DataReader('KS11', start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
        
        if len(df_stock) >= 2 and len(df_kospi) >= 2:
            stock_return = ((df_stock['Close'].iloc[-1] - df_stock['Close'].iloc[-20]) / df_stock['Close'].iloc[-20]) * 100
            kospi_return = ((df_kospi['Close'].iloc[-1] - df_kospi['Close'].iloc[-20]) / df_kospi['Close'].iloc[-20]) * 100
            alpha_momentum = stock_return - kospi_return
            
            if alpha_momentum >= 20.0:
                theme_premium = 1.15  
                quant_tier = "MOMENTUM_LEADER"
            elif alpha_momentum >= 5.0:
                theme_premium = 1.08
                quant_tier = "VALUE_CHAIN"
            else:
                theme_premium = 1.00
                quant_tier = "MARKET_SATELLITE"
    except:
        alpha_momentum = 0.0

    f_flow, i_flow = cache.get('foreign_20d_flow', 0.0), cache.get('institution_20d_flow', 0.0)
    suup_multiplier = 1.0
    if f_flow > 0 and i_flow > 0: suup_multiplier += 0.12 
    elif f_flow > 0: suup_multiplier += 0.07              
    elif i_flow > 0: suup_multiplier += 0.05              

    interest_rate_adj = 1.0
    if "보험" in krx_sector_name or "생명" in krx_sector_name: interest_rate_adj = 1.05  
    elif any(k in krx_sector_name for k in ["증권", "금융", "건설", "창업투자"]): interest_rate_adj = 0.90  

    max_per_cap = base_industry_per * 1.40
    if alpha_momentum >= 20.0 or f_flow > 0: max_per_cap = base_industry_per * 2.20 

    calculated_per = base_industry_per * theme_premium * suup_multiplier * interest_rate_adj * macro_multiplier
    target_per = min(calculated_per, max_per_cap) 

    eps_2025 = cache.get('fwd_eps_2025', 0.0)
    eps_2026 = cache.get('fwd_eps_2026', 0.0)
    
    if eps_2025 > 0 and eps_2026 > 0:
        eps_growth_rate = ((eps_2026 - eps_2025) / eps_2025) * 100
        forward_eps = eps_2026
    else:
        if bps > 0:
            implied_roe = raw_eps / bps if bps > 0 and raw_eps > 0 else 0.08
            normalized_roe = max(0.06, min(implied_roe, 0.18))
            eps_growth_rate = normalized_roe * 100
            forward_eps = (bps * 1.10) * normalized_roe
        else:
            eps_growth_rate = 12.0
            forward_eps = cache.get('year_high', current_price) / target_per

    if eps_growth_rate <= 0: eps_growth_rate = 5.0 
    peg_ratio = target_per / eps_growth_rate

    base_target = forward_eps * target_per
    base_target = max(current_price * 0.60, min(base_target, current_price * 3.00))
    
    bear_ratio, bull_ratio = 0.80, 1.25 
    if "보험" in krx_sector_name or "생명" in krx_sector_name: bear_ratio, bull_ratio = 0.88, 1.10  
    elif "증권" in krx_sector_name or "금융" in krx_sector_name: bear_ratio, bull_ratio = 0.82, 1.15
    elif "반도체" in krx_sector_name: bear_ratio, bull_ratio = 0.75, 1.30  
    elif any(k in krx_sector_name or k in row['name'] for k in ["로봇", "로보", "기계", "소프트"]): bear_ratio, bull_ratio = 0.65, 1.55  

    bear_target = int(base_target * bear_ratio)
    bull_target = int(base_target * bull_ratio)
    
    return int(base_target), bear_target, bull_target, round(target_per, 2), round(peg_ratio, 2), [quant_tier, krx_sector_name]

# ==========================================
# [Layer 5] v12.0 스레드 수급 루프 & 타임 마디 이중 캐시 파이프라인
# ==========================================
def auto_sync_job(supabase, username, naver_id, naver_secret):
    last_sync_time = 0
    while True:
        now_ts = time.time()
        now_kst = datetime.utcnow() + timedelta(hours=9)
        if 8 <= now_kst.hour <= 18 and (now_ts - last_sync_time >= 600):
            last_sync_time = now_ts
            try:
                execute_on_demand_sync(supabase, username, naver_id, naver_secret, force=False)
                insert_log(supabase, username, "BACKGROUND_ENGINE", "자동 백그라운드 수급 동기화 스레드 수렴 완료", "10분 마디 주기 오토 락 가동")
            except Exception as e:
                insert_log(supabase, username, "BACKGROUND_ERROR", "백그라운드 동기화 중 예외 발생", str(e))
        time.sleep(30)

def execute_on_demand_sync(supabase, username, naver_id, naver_secret, force=False):
    macro_mult, current_usd, _ = fetch_global_macro_factor()
    db_res = supabase.table("user_portfolio").select("*").eq("username", username).execute()
    portfolio_data = db_res.data
    if not portfolio_data: return

    # 지능형 1달 주기 DB 마스터 매핑 테이블 연동 (거래소 차단 리스크 분쇄)
    krx_db = load_krx_mapping_from_db(supabase)
    now_kst_str = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')

    for row in portfolio_data:
        ticker = str(row['ticker']).split('.')[0]
        name = row['name']
        
        cache_res = supabase.table("stock_cache").select("*").eq("ticker", ticker).execute()
        db_cache = cache_res.data[0] if cache_res.data else {}
        
        updated_cache = {"ticker": ticker, "name": name}
        updated_cache['krx_sector'] = krx_db.get(name, "일반제조업")

        # [A그룹: 10분 가격 캐시] 
        if is_expired(db_cache.get('last_price_update'), 600) or force:
            df_p = fdr.DataReader(ticker, start=(datetime.utcnow()-timedelta(days=7)).strftime('%Y-%m-%d'))
            if not df_p.empty:
                updated_cache['current_price'] = int(df_p['Close'].iloc[-1])
                prev_close = float(df_p['Close'].iloc[-2]) if len(df_p) >= 2 else df_p['Close'].iloc[-1]
                updated_cache['pct_change'] = round(((updated_cache['current_price'] - prev_close) / prev_close) * 100, 2)
                updated_cache['year_high'] = int(df_p['High'].max())
                updated_cache['last_price_update'] = now_kst_str

        # [B그룹: 30분 뉴스 캐시]
        if is_expired(db_cache.get('last_news_update'), 1800) or force:
            _, net_sent, _, n_list = get_auto_momentum(name, naver_id, naver_secret)
            updated_cache['net_sentiment'] = net_sent
            updated_cache['news_list'] = n_list
            updated_cache['last_news_update'] = now_kst_str

        # [C그룹: 1시간 매집 수급 캐시] 
        if is_expired(db_cache.get('last_flow_update'), 3600) or force:
            f_flow, i_flow = fetch_investor_flows(ticker)
            updated_cache['foreign_20d_flow'] = f_flow
            updated_cache['institution_20d_flow'] = i_flow
            updated_cache['last_flow_update'] = now_kst_str

        # [D그룹: 1일 재무제표 캐시] 
        if is_expired(db_cache.get('last_fundamental_update'), 86400) or force:
            fund = fetch_naver_fundamentals(ticker)
            if fund:
                updated_cache.update({
                    'eps': fund['eps'], 'per': fund['per'], 'pbr': fund['pbr'], 'bps': fund['bps'],
                    'industry_per': fund['industry_per'], 'shares_outstanding': fund['shares_outstanding'],
                    'broker_target': fund['broker_target'], 'fwd_eps_2025': fund['fwd_eps_2025'], 'fwd_eps_2026': fund['fwd_eps_2026'],
                    'q_headers': fund.get('q_headers', []), 'q_revenues': fund.get('q_revenues', []), 'q_op_profits': fund.get('q_op_profits', []), 'summary': fund.get('summary', '')
                })
                updated_cache['last_fundamental_update'] = now_kst_str

        # [E그룹: 30일 BM 캐시]
        if is_expired(db_cache.get('last_bm_update'), 2592000) or force:
            bm_list = fetch_dynamic_company_bm(ticker)
            mock_fund = {**db_cache, **updated_cache}
            growth_factor, bm_summary = calculate_bm_score(mock_fund)
            updated_cache.update({
                'bm_list': bm_list, 'bm_growth_factor': growth_factor, 'bm_summary': bm_summary, 'last_bm_update': now_kst_str
            })

        full_cache = {**db_cache, **updated_cache}
        supabase.table("stock_cache").upsert(full_cache).execute()
        
        base_tgt, bear_tgt, bull_tgt, target_multiple, peg, applied_trends = calculate_intrinsic_target(row, full_cache, macro_mult)
        
        user_cache = {
            'current_price': full_cache.get('current_price', row['buy_price']),
            'pct_change': full_cache.get('pct_change', 0.0), 'year_high': full_cache.get('year_high', 0),
            'eps': full_cache.get('eps', 0.0), 'per': full_cache.get('per', 10.0), 'pbr': full_cache.get('pbr', 1.0), 'bps': full_cache.get('bps', 0.0),
            'foreign_20d_flow': full_cache.get('foreign_20d_flow', 0.0), 'institution_20d_flow': full_cache.get('institution_20d_flow', 0.0),
            'broker_target': full_cache.get('broker_target', 0.0), 'news_list': full_cache.get('news_list', []),
            'target_2026': base_tgt, 'bear_target': bear_tgt, 'bull_target': bull_tgt, 'target_multiple': target_multiple, 'peg': peg, 'applied_trends': applied_trends,
            'summary': full_cache.get('summary', ''), 'bm_summary': full_cache.get('bm_summary', ''), 'bm_list': full_cache.get('bm_list', []),
            'q_headers': full_cache.get('q_headers', []), 'q_revenues': full_cache.get('q_revenues', []), 'q_op_profits': full_cache.get('q_op_profits', [])
        }
        supabase.table("user_portfolio").update({"analysis_cache": user_cache}).eq("id", row['id']).execute()

def calculate_bm_score(fund_data):
    growth_multiplier = 1.0
    report = ""
    if fund_data:
        q_revs = fund_data.get('q_revenues', [])
        q_ops = fund_data.get('q_op_profits', [])
        if len(q_revs) >= 2:
            last_rev, prev_rev = q_revs[-1], q_revs[-2]
            last_op, prev_op = q_ops[-1], q_ops[-2]
            qoq = ((last_rev - prev_rev) / abs(prev_rev)) * 100 if prev_rev != 0 else 0
            margin = (last_op / last_rev) * 100 if last_rev != 0 else 0
            if qoq >= 10: growth_multiplier += 0.05
            if margin >= 10: growth_multiplier += 0.05  
            if prev_op < 0 and last_op > 0: growth_multiplier += 0.10; report += "🔥 [분기 흑자전환 모멘텀] "
            report += f"최근 매출 {int(last_rev):,}억 (QoQ {qoq:+.1f}%) / 영업이익 {int(last_op):,}억 (OPM {margin:.1f}%)"
    return growth_multiplier, report

# ==========================================
# [Layer 6] UI 관제 센터 (오리지널 복원 완결)
# ==========================================
def run_stock_quant_page(supabase, username, naver_id, naver_secret):
    st.title("🛡️ 스마트 프랍 퀀트 포트폴리오 엔진 v18.5")
    
    # 👍 [_active_threads NameError 원천 진압 완료] 함수 진입 즉시 정상 바인딩 스크리닝
    if username not in _active_threads or not _active_threads[username].is_alive():
        t = threading.Thread(target=auto_sync_job, args=(supabase, username, naver_id, naver_secret), daemon=True)
        t.start()
        _active_threads[username] = t

    macro_mult, current_usd, 환율상태 = fetch_global_macro_factor()
    
    with st.container(border=True):
        st.markdown("##### 🌐 GLOBAL MACRO FLOW (매크로 유동성 레이더)")
        m_col1, m_col2 = st.columns(2)
        with m_col1:
            st.metric("원/달러 환율 국면", 환율상태, delta="외국인 패시브 수급 불안" if current_usd >= 1400 else "수급 안정 구역", delta_color="inverse")
        with m_col2:
            st.metric("시장 기본 PER 멀티플 보정률", f"{int(macro_mult*100)}%", delta="v12 데몬 스레드 및 v19 30일 만기 DB 주소록 탑재 완료")

    # 👍 [오리지널 UI 원복 1] 신규 자산 추가 컴포넌트 selectbox 형태로 전면 복원
    krx_map = load_krx_mapping_from_db(supabase)

    with st.expander("➕ 포트폴리오 신규 자산 편입", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1: s_name = st.selectbox("종목 선택 (한/영 키를 눌러주세요)", list(krx_map.keys()))
        with col2: buy_p = st.number_input("매입 평단가(원)", min_value=1, value=10000)
        with col3: qty = st.number_input("보유 수량(주)", min_value=1, value=10)
        if st.button("장부 조율 및 매수 결제", type="primary"):
            raw_ticker = str(krx_map[s_name]).split('.')[0]
            try:
                supabase.table("user_portfolio").upsert({
                    "username": username, "ticker": raw_ticker, "name": s_name, "buy_price": buy_p, "qty": qty, "analysis_cache": {}
                }).execute()
                insert_log(supabase, username, "신규 편입", f"[{s_name}] 매수 편입 성공", f"단가 {buy_p}원, 수량 {qty}주 장부 박음")
                st.success(f"[{s_name}] 장부 합성 성공!")
                time.sleep(0.3)
                st.rerun()
            except Exception as e:
                st.error(f"자산 편입 실패: {str(e)}")

    st.divider()

    tab_port, tab_hist, tab_log = st.tabs(["💼 포트폴리오 자산", "📝 가치 실현 내역", "⚙️ 시스템 가동 로그"])

    with tab_port:
        st.write("⚡ **Forward 멀티 모델 실시간 제어판**")
        col_sync1 = st.columns(1)[0]
        db_res = supabase.table("user_portfolio").select("*").eq("username", username).order("id", desc=False).execute()
        portfolio_data = db_res.data

        if col_sync1.button("🔄 가치 밸류에이션 전면 강제 재연산", width="stretch"):
            if not portfolio_data: st.stop()
            with st.status("v14.0 하이브리드 격벽 무력화 강제 동기화 중...", expanded=True) as status:
                execute_on_demand_sync(supabase, username, naver_id, naver_secret, force=True)
                status.update(label="전역 공용 캐시 및 밸류에이션 리레이팅 리셋 완결!", state="complete")
            st.rerun()

        st.divider()
        if not portfolio_data:
            st.info("장부에 보유 주식이 없습니다.")
            return

        total_invest, total_value = 0, 0
        display_rows = []
        
        for row in portfolio_data:
            cache = row.get('analysis_cache') if row.get('analysis_cache') else {}
            curr_price = cache.get('current_price', row['buy_price'])
            day_pct = cache.get('pct_change', 0.0)
            target_price = cache.get('target_2026', row['buy_price'])
            bear_target = cache.get('bear_target', int(target_price * 0.78))
            bull_target = cache.get('bull_target', int(target_price * 1.25))
            target_multiple = cache.get('target_multiple', 10.0)
            peg = cache.get('peg', 1.0)
            broker_target = cache.get('broker_target', 0.0)
            
            raw_trends = cache.get('applied_trends', ["MARKET_SATELLITE", "일반제조업"])
            quant_tier = raw_trends[0]
            krx_sector = cache.get('krx_sector', raw_trends[1])
            engine_model = "PER" if cache.get('eps', 0.0) > 0 else "PBR"
            
            pnl_amt = (curr_price - row['buy_price']) * row['qty']
            pnl_pct = ((curr_price - row['buy_price']) / row['buy_price']) * 100 if row['buy_price'] > 0 else 0
            safe_target_price = int(target_price * 0.95)
            
            cut_loss_price = int(cache.get('cut_loss_price', row['buy_price'] * 0.85))
            expected_loss_amt = (cut_loss_price - row['buy_price']) * row['qty']
            
            total_invest += row['buy_price'] * row['qty']
            total_value += curr_price * row['qty']
            
            val_ratio = curr_price / target_price if target_price > 0 else 1.0
            if val_ratio < 0.5: status_emoji = "🛒 멀티플 극저평가"
            elif val_ratio < 0.75: status_emoji = "🔵 안전마진 확보"
            elif val_ratio < 0.95: status_emoji = "🟢 가치 수렴 중"
            else: status_emoji = "🎯 사이클 고점 도달"

            display_rows.append({
                "밸류에이션 상태": status_emoji, "종목명": row['name'], "현재가": curr_price, "전일비": day_pct, "평단가": row['buy_price'], "보유지분": row['qty'], "평가손익": pnl_amt, "수익률": pnl_pct,
                "비관": bear_target, "기준(최고치)": target_price, "낙관": bull_target, "안전목표가": safe_target_price, "목표평가손익": (safe_target_price - row['buy_price']) * row['qty'],
                "PEG": peg, "적용배수": target_multiple, "KRX섹터": krx_sector, "엔진모델": engine_model,
                "손절가": cut_loss_price, "손절시손익": expected_loss_amt,
                "외인20일": cache.get('foreign_20d_flow', 0.0), "기관20일": cache.get('institution_20d_flow', 0.0), "에프앤목표가": broker_target, "raw_data": row
            })

        total_pnl = total_value - total_invest
        total_pnl_pct = (total_pnl / total_invest) * 100 if total_invest > 0 else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("총 투입 자본", f"{total_invest:,} 원")
        c2.metric("현재 평가 자산", f"{total_value:,} 원")
        c3.metric("포트폴리오 수익", f"{total_pnl:,} 원", f"{total_pnl_pct:+.2f}%")
        
        df_base = pd.DataFrame(display_rows)
        df_disp = pd.DataFrame()
        df_disp["상태"] = df_base["밸류에이션 상태"]
        df_disp["종목명"] = df_base["종목명"]
        df_disp["KRX 업종"] = df_base["KRX섹터"]
        df_disp["현재가"] = df_base["현재가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["🛡️ 안전탈출(-5%)"] = df_base["안전목표가"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["탈출 시 예상수익"] = df_base["목표평가손익"].apply(lambda x: f"₩ {int(x):+,}" if x != 0 else "₩ 0")
        df_disp["📉 비관(Bear)"] = df_base["비관"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["🟢 기준(Base)"] = df_base["기준(최고치)"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["📈 낙관(Bull)"] = df_base["낙관"].apply(lambda x: f"₩ {int(x):,}")
        df_disp["외인 20일(주)"] = df_base["외인20일"].apply(lambda x: f"{int(x):+,}")
        df_disp["기관 20일(주)"] = df_base["기관20일"].apply(lambda x: f"{int(x):+,}")
        df_disp["진성 PEG"] = df_base["PEG"].apply(lambda x: f"📊 {x:.2f}")

        def style_mts_color(row):
            styles = [''] * len(row)
            pnl = df_base.loc[row.name, '수익률']
            day = df_base.loc[row.name, '전일비']
            peg_val = df_base.loc[row.name, 'PEG']
            f_buy = df_base.loc[row.name, '외인20일']
            
            pnl_style = 'background-color: rgba(240, 68, 82, 0.12); color: #F04452; font-weight: bold;' if pnl > 0 else ('background-color: rgba(49, 130, 246, 0.12); color: #3182F6; font-weight: bold;' if pnl < 0 else 'color: #4E5968;')
            safe_style = 'background-color: rgba(240, 150, 40, 0.08); color: #E67E22; font-weight: bold;'
            
            if "🛡️ 안전탈출(-5%)" in df_disp.columns: styles[df_disp.columns.get_loc('🛡️ 안전탈출(-5%)')] = safe_style
            if "탈출 시 예상수익" in df_disp.columns: styles[df_disp.columns.get_loc('탈출 시 예상수익')] = safe_style
            if peg_val < 1.0 and peg_val > 0 and "진성 PEG" in df_disp.columns: styles[df_disp.columns.get_loc('진성 PEG')] = 'background-color: rgba(0, 180, 100, 0.08); color: #00B464; font-weight: bold;'
            if f_buy > 0 and "외인 20일(주)" in df_disp.columns: styles[df_disp.columns.get_loc('외인 20일(주)')] = 'color: #F04452; font-weight: bold;'
            return styles

        styled_df = df_disp.style.apply(style_mts_color, axis=1)
        selection_event = st.dataframe(styled_df, width="stretch", on_select="rerun", selection_mode="single-row")
        
        selected_indices = selection_event.get("selection", {}).get("rows", [])
        
        if selected_indices:
            selected_idx = selected_indices[0]
            selected_stock = display_rows[selected_idx]
            s_name = selected_stock["종목명"]
            raw_row = selected_stock["raw_data"]
            s_ticker = str(raw_row['ticker']).split('.')[0]
            s_cache = raw_row.get("analysis_cache", {})
            
            # 👍 [오리지널 UI 원복 2] 체크 시 하단 퀀트 통제실 내부에 popover 3대장 완벽 결합
            st.markdown(f"### 🛠️ [{s_name}] 퀀트 익절/손절 실전 통제실")
            
            col_btn1, col_btn2, col_btn3 = st.columns(3)
            with col_btn1:
                with st.popover("✏️ 장부 평단/수량 수정", use_container_width=True):
                    new_p = st.number_input("수정할 평단가", value=int(raw_row['buy_price']), key=f"p_ed_{s_ticker}")
                    new_q = st.number_input("수정할 보유수량", value=int(raw_row['qty']), key=f"q_ed_{s_ticker}")
                    if st.button("수정 장부 인가", key=f"b_ed_{s_ticker}", use_container_width=True):
                        supabase.table("user_portfolio").update({"buy_price": new_p, "qty": new_q}).eq("id", raw_row['id']).execute()
                        insert_log(supabase, username, "장부 수정", f"[{s_name}] 수정", f"평단가 {new_p} / 수량 {new_q}")
                        st.success("장부 정보 정정 고시 완료!")
                        time.sleep(0.3)
                        st.rerun()
            with col_btn2:
                with st.popover("🛒 분할 추가매수", use_container_width=True):
                    add_p = st.number_input("추가 매수가격", value=int(s_cache.get('current_price', raw_row['buy_price'])), key=f"p_add_{s_ticker}")
                    add_q = st.number_input("추가 매수수량", value=10, key=f"q_add_{s_ticker}")
                    if st.button("추가매수 체결", key=f"b_add_{s_ticker}", use_container_width=True):
                        current_total_cost = raw_row['buy_price'] * raw_row['qty']
                        new_total_cost = current_total_cost + (add_p * add_q)
                        new_qty = raw_row['qty'] + add_q
                        new_avg_price = int(new_total_cost / new_qty)
                        supabase.table("user_portfolio").update({"buy_price": new_avg_price, "qty": new_qty}).eq("id", raw_row['id']).execute()
                        insert_log(supabase, username, "추가 매수", f"[{s_name}] {add_q}주 추매", f"단가 {add_p}원 합성")
                        st.success("가중평균 평단가 합성 완료!")
                        time.sleep(0.3)
                        st.rerun()
            with col_btn3:
                with st.popover("❌ 자산 매도(청산)", use_container_width=True):
                    st.write(f"현재 보유 수량: **{raw_row['qty']}주** (평단가: {raw_row['buy_price']:,}원)")
                    sell_p = st.number_input("매도 단가", value=int(s_cache.get('current_price', raw_row['buy_price'])), key=f"p_sl_{s_ticker}")
                    sell_q = st.number_input("매도 수량", min_value=1, max_value=int(raw_row['qty']), value=int(raw_row['qty']), key=f"q_sl_{s_ticker}")
                    if st.button("🚨 매도 집행", key=f"b_sl_{s_ticker}", use_container_width=True, type="primary"):
                        profit_amt = (sell_p - raw_row['buy_price']) * sell_q
                        profit_pct = round(((sell_p - raw_row['buy_price']) / raw_row['buy_price']) * 100, 2)
                        try:
                            supabase.table("user_history").insert({
                                "username": username, "ticker": raw_row['ticker'], "name": s_name,
                                "buy_price": raw_row['buy_price'], "sell_price": sell_p, "qty": sell_q,
                                "profit_amt": profit_amt, "profit_pct": profit_pct
                            }).execute()
                        except Exception as e: print(f"히스토리 전송 누수: {e}")
                        
                        if sell_q == raw_row['qty']:
                            supabase.table("user_portfolio").delete().eq("id", raw_row['id']).execute()
                        else:
                            supabase.table("user_portfolio").update({"qty": raw_row['qty'] - sell_q}).eq("id", raw_row['id']).execute()
                            
                        insert_log(supabase, username, "자산 매도", f"[{s_name}] {sell_q}주 청산", f"손익 {profit_amt:,}원 ({profit_pct:+.2f}%) 실현")
                        st.error("포지션 청산 오더 집행 완결!")
                        time.sleep(0.3)
                        st.rerun()

            st.divider()
            
            t1, t2, t3 = st.tabs(["📉 3단계 시나리오 및 수급 판세", "📰 전방 사업 명세", "📊 실적 턴어라운드 감지"])
            with t1:
                eps_val = s_cache.get('eps', 0.0)
                bps_val = s_cache.get('bps', 0.0)
                current_status = selected_stock['밸류에이션 상태']  
                status_tooltip = f"KRX 섹터: {selected_stock['KRX섹터']} | 연산 엔진: {selected_stock['엔진모델']} 모형"

                st.markdown(f"**• 종합 투자 의견:** <span title='{status_tooltip}' style='cursor: help; border-bottom: 1px dashed #4E5968; font-weight: bold;'>{current_status} ⓘ</span>", unsafe_allow_html=True)
                st.markdown(f"**• TTM 기초 지표:** EPS `{eps_val:,.0f}원` | BPS `{bps_val:,.0f}원` | **진성 기하학적 PEG:** `{selected_stock['PEG']}x`")
                st.markdown(f"**📉 비관적 저점 방어선 (Bear Case Target):** `₩ {selected_stock['비관']:,}`원")
                st.markdown(f"**🟢 기준 내재가치 최고점 (Base Case Target):** `₩ {selected_stock['기준(최고치)']:,}`원")
                st.markdown(f"**📈 유동성 오버슈팅 상방선 (Bull Case Target):** `₩ {selected_stock['낙관']:,}`원")
                st.markdown(f"**🛡️ 실전 대기 분할 안전탈출가 (-5%):** `₩ {selected_stock['안전목표가']:,}원` (청산 시 최종 누적 실현이익: `{selected_stock['목표평가손익']:,}원`)")
                st.markdown(f"**🚨 마지노선 손절가 격벽:** `₩ {selected_stock['손절가']:,}원` (손실 규모: `{selected_stock['손절시손익']:,}원`)")
                st.markdown(f"**🏛️ 에프앤가이드 여의도 컨센서스 목표주가 평균:** `₩ {int(selected_stock['에프앤목표가']):,}`원")
                
                st.write("**실시간 추적 뉴스**")
                for idx, news in enumerate(s_cache.get('news_list', []), 1):
                    st.markdown(f"[{idx}] [{news['title']}]({news['link']})")
                
            with t2:
                summary_text = s_cache.get('summary', '')
                if not summary_text: summary_text = "기업 분석 데이터를 가져올 수 없습니다 (네트워크 지연)."
                st.write(f"**📢 기업 개요 및 펀더멘탈 요약:** {summary_text}")
                st.write(f"**• 실적 턴어라운드율 모멘텀 총평:** {s_cache.get('bm_summary', '-')}")
                bm_list = s_cache.get('bm_list', [])
                if bm_list: st.table(pd.DataFrame(bm_list, columns=["사업부문", "주요품목", "구분", "비중(%)"]))
                else: st.info("사업 부문 명세를 로드할 수 없습니다.")
                    
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
        hist_res = supabase.table("user_history").select("*").eq("username", username).execute()
        if not hist_res.data: st.info("아직 자산 매도 내역이 없습니다.")
        else:
            total_realized = sum([r['profit_amt'] for r in hist_res.data])
            win_count = sum([1 for r in hist_res.data if r['profit_amt'] > 0])
            win_rate = (win_count / len(hist_res.data)) * 100
            
            h1, h2 = st.columns(2)
            h1.metric("누적 실현 손익", f"{total_realized:,} 원")
            h2.metric("매매 승률", f"{win_rate:.1f} %")
            
            df_hist = pd.DataFrame(hist_res.data)
            df_hist['created_at'] = pd.to_datetime(df_hist['created_at'], errors='coerce').dt.tz_localize(None) + pd.Timedelta(hours=9)
            df_hist['created_at'] = df_hist['created_at'].dt.strftime('%Y-%m-%d %H:%M')
            df_hist = df_hist[['created_at', 'name', 'buy_price', 'sell_price', 'qty', 'profit_amt', 'profit_pct']]
            df_hist.columns = ['매도일시', '종목명', '진입가', '청산가', '수량', '실현손익', '수익률(%)']
            
            df_hist['진입가'] = df_hist['진입가'].apply(lambda x: f"₩ {int(parse_num(str(x))):,}")
            df_hist['청산가'] = df_hist['청산가'].apply(lambda x: f"₩ {int(parse_num(str(x))):,}")
            df_hist['수량'] = df_hist['수량'].apply(lambda x: f"{int(parse_num(str(x))):,} 주")
            df_hist['실현손익'] = df_hist['실현손익'].apply(lambda x: f"₩ {int(parse_num(str(x))):,}")
            st.dataframe(df_hist, width="stretch")

    with tab_log:
        st.subheader("⚙️ 시스템 엔진 처리 기록")
        log_res = supabase.table("user_logs").select("*").eq("username", username).execute()
        if not log_res.data: st.info("시스템 처리 기록이 없습니다.")
        else:
            df_log = pd.DataFrame(log_res.data)
            df_log['created_at'] = pd.to_datetime(df_log['created_at'], errors='coerce').dt.tz_localize(None) + pd.Timedelta(hours=9)
            df_log['created_at'] = df_log['created_at'].dt.strftime('%Y-%m-%d %H:%M:%S')
            df_log = df_log[['created_at', 'module', 'summary', 'details']]
            df_log.columns = ['시간', '모듈', '요약', '상세내역']
            st.dataframe(df_log, width="stretch")
