"""
주도섹터 / 주도주 10분 매트릭스 — 데이터 수집 + 페이지 생성

동작 요약
  1) sector_universe.SECTORS 에 정의된 전 종목의 현재 시세를 KIS API로 한 번에 훑는다
  2) 10분 단위 슬롯(0900, 0910, ... 1530)에 스냅샷으로 기록한다
  3) 하루치 스냅샷을 docs/data/YYYYMMDD.json 에 누적 저장한다
  4) 그 데이터를 그대로 박아넣은 정적 페이지 docs/index.html 을 다시 만든다

실행 예
  python sector_matrix.py            # 한 번 수집하고 페이지 생성
  python sector_matrix.py --loop     # 장중 10분마다 자동 반복
  python sector_matrix.py --push     # 수집 후 깃허브에 자동 커밋/푸시
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# 시세 조회는 실전 도메인이 호출 한도가 넉넉하므로, config 를 불러오기 전에 실전으로 고정한다
# (load_dotenv 는 이미 설정된 환경변수를 덮어쓰지 않으므로 이 한 줄이 우선 적용됨)
os.environ.setdefault("_SECTOR_MATRIX", "1")
os.environ["KIS_IS_PAPER"] = "false"

import requests  # noqa: E402

from auth import BASE_URL, get_access_token  # noqa: E402
from config import KIS_APP_KEY, KIS_APP_SECRET  # noqa: E402
from sector_universe import SECTORS, all_tickers  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
DATA = DOCS / "data"

PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"
PRICE_TR_ID = "FHKST01010100"

# 거래대금(=매수금액) 상위 30종목 순위 — 시장 전체 대상
RANK_ENDPOINT = "/uapi/domestic-stock/v1/quotations/volume-rank"
RANK_TR_ID = "FHPST01710000"

# 종목별 프로그램매매 추이 (전 종목 개별 조회 가능, 장중 잠정치)
PROGRAM_ENDPOINT = "/uapi/domestic-stock/v1/quotations/program-trade-by-stock"
PROGRAM_TR_ID = "FHPPG04650100"

# 외국인/기관 매매종목가집계 — "장중 잠정 상위 랭킹" 화면이라 특정 종목을 찍어 조회할 수는 없고,
# 순매수/순매도 상위 몇십 종목만 알려준다. 우리 상위 30종목 중 이 랭킹에 걸린 것만 값이 채워짐.
FLOW_ENDPOINT = "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
FLOW_TR_ID = "FHPTJ04400000"

# 종목별 투자자 매매동향 — 전일까지 확정치는 매수/매도/순매수를 전부 주지만,
# 당일(장중) 행은 항상 빈 문자열이라 사실상 "전일 확정" 용도로만 쓸 수 있다.
INVESTOR_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-investor"
INVESTOR_TR_ID = "FHKST01010900"

# 코스피/코스닥 지수 현재가 (장중 실시간)
INDEX_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
INDEX_TR_ID = "FHPUP02100000"
INDEX_CODES = (("0001", "KOSPI"), ("1001", "KOSDAQ"))

# 미국 주가지수 선물 (CME) — 코스피 옆에 같이 보여주기 위한 참고 지표
FUTURES_ENDPOINT = "/uapi/overseas-futureoption/v1/quotations/inquire-price"
FUTURES_TR_ID = "HHDFC55010000"
FUT_PRODUCTS = (("NQ", "나스닥100 선물"), ("ES", "S&P500 선물"), ("YM", "다우 선물"))
_QUARTER_MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}   # 지수선물은 3/6/9/12월 분기물만 있음

# 정규장 09:00 ~ 15:30 을 10분 단위로 자른 슬롯 라벨
SLOT_LABELS = [
    f"{h:02d}{m:02d}"
    for h in range(9, 16)
    for m in (0, 10, 20, 30, 40, 50)
    if (h, m) <= (15, 30)
]

REQ_INTERVAL = 0.12  # 초당 약 8건 — KIS 실전 유량제한(초당 20건) 안쪽으로 여유 있게


# ---------------------------------------------------------------- 시세 수집

def fetch_quote(code: str, retries: int = 3) -> dict | None:
    """
    종목 하나의 현재가/등락률/거래대금을 조회. 실패하면 None.
    시장구분을 UN(통합)으로 조회 — KRX 단독(J)이 아니라 넥스트트레이드(NXT) 체결까지 합친 값.
    (검증: KRX단독 거래량 + NXT단독 거래량 = UN 거래량, 정확히 일치하는 것 확인함)
    """
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": PRICE_TR_ID,
        "custtype": "P",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "UN", "FID_INPUT_ISCD": code}

    for attempt in range(retries):
        try:
            headers["authorization"] = f"Bearer {get_access_token()}"
            resp = requests.get(BASE_URL + PRICE_ENDPOINT, headers=headers,
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                time.sleep(0.6 * (attempt + 1))
                continue
            return data.get("output") or None
        except Exception:
            time.sleep(0.6 * (attempt + 1))
    return None


def _f(value, default=0.0) -> float:
    """API가 문자열로 주는 숫자를 안전하게 float 으로."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


# ETF/ETN 브랜드 접두어 — 코드 마스터가 없는 환경에서도 최소한의 안전장치가 되도록
ETF_NAME_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "PLUS ", "RISE ", "SOL ",
    "ACE ", "KOSEF", "TIMEFOLIO", "WOORI ", "1Q ", "BNK ", "히어로즈", "마이다스",
    "파워", "TREX", "FOCUS", "KIWOOM",
)


def looks_like_etf(name: str) -> bool:
    """종목명만 보고 ETF/ETN 인지 추정 (코드 마스터 조회가 실패했을 때의 보조 판정)."""
    n = name.upper().strip()
    return any(n.startswith(pre.upper()) for pre in ETF_NAME_PREFIXES)


_etf_cache: set[str] | None = None


def etf_codes() -> set[str]:
    """코드 마스터에서 ETF/ETN 코드를 뽑아둔다 (순위표에서 걸러낼 수 있도록 표시용)."""
    global _etf_cache
    if _etf_cache is not None:
        return _etf_cache
    try:
        import code_master as cm
        df = cm._parse_mst(cm.DATA_DIR / "kospi_code.mst", cm.KOSPI_PART1_TAIL_LEN,
                           cm.KOSPI_FIELD_WIDTHS, cm.KOSPI_FIELD_NAMES)
        # 그룹코드 EF=ETF, EN=ETN, EW=ELW — 개별 종목이 아니므로 별도 표시
        codes = df.loc[df["그룹코드"].astype(str).str.strip().isin(["EF", "EN", "EW"]), "단축코드"]
        _etf_cache = {str(c).strip() for c in codes}
    except Exception:
        _etf_cache = set()
    return _etf_cache


def fetch_program_trade(code: str, retries: int = 2) -> list | None:
    """
    종목 하나의 프로그램매매 잠정치를 조회 (장중에도 실시간으로 채워짐).
    반환: [매도량, 매수량, 순매수량, 매도대금(억), 매수대금(억), 순매수대금(억)]
    참고: FID_COND_MRKT_DIV_CODE 를 UN(통합)으로 바꿔봐도 J(KRX단독)와 값이 완전히 같음
    (실측 확인) — 이 API는 넥스트트레이드(NXT)를 반영하지 않는 것으로 보여 J 유지.
    """
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": PROGRAM_TR_ID,
        "custtype": "P",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}

    for attempt in range(retries):
        try:
            headers["authorization"] = f"Bearer {get_access_token()}"
            resp = requests.get(BASE_URL + PROGRAM_ENDPOINT, headers=headers,
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                time.sleep(0.5 * (attempt + 1))
                continue
            o = data.get("output") or {}
            if isinstance(o, list):        # 이 API는 output 을 배열로 줌 (첫 번째가 최신 시각)
                o = o[0] if o else {}
            if not o:
                return None
            return [
                int(_f(o.get("whol_smtn_seln_vol"))),
                int(_f(o.get("whol_smtn_shnu_vol"))),
                int(_f(o.get("whol_smtn_ntby_qty"))),
                round(_f(o.get("whol_smtn_seln_tr_pbmn")) / 1e8, 1),
                round(_f(o.get("whol_smtn_shnu_tr_pbmn")) / 1e8, 1),
                round(_f(o.get("whol_smtn_ntby_tr_pbmn")) / 1e8, 1),
            ]
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return None


def fetch_investor_daily(code: str, retries: int = 2) -> list | None:
    """
    종목 하나의 '전일까지 확정된' 외국인/기관/개인 매수·매도·순매수를 조회.
    당일(장중) 값은 KIS 쪽에서 항상 빈 문자열로 내려오므로, 값이 채워진 가장 최근 날짜
    (보통 전일)의 행을 찾아서 쓴다 — 프로그램매매처럼 실시간 잠정치가 아니라 "확정치".
    반환: [기준일자, 외국인[매도량,매수량,순매수량,매도대금(억),매수대금(억),순매수대금(억)],
                     기관[같은 구성], 개인[같은 구성]]  (조회 실패/전부 공백이면 None)
    참고: 이 API도 UN(통합)을 줘봐도 J(KRX단독)와 값이 같음 — NXT 미반영, J 유지.
    """
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": INVESTOR_TR_ID,
        "custtype": "P",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}

    def trio(row: dict, prefix: str) -> list:
        # 이 API의 _tr_pbmn 필드는 백만원 단위 (프로그램매매 API의 원 단위와 다름) -> /100 해야 억원
        return [
            int(_f(row.get(f"{prefix}_seln_vol"))),
            int(_f(row.get(f"{prefix}_shnu_vol"))),
            int(_f(row.get(f"{prefix}_ntby_qty"))),
            round(_f(row.get(f"{prefix}_seln_tr_pbmn")) / 100, 1),
            round(_f(row.get(f"{prefix}_shnu_tr_pbmn")) / 100, 1),
            round(_f(row.get(f"{prefix}_ntby_tr_pbmn")) / 100, 1),
        ]

    for attempt in range(retries):
        try:
            headers["authorization"] = f"Bearer {get_access_token()}"
            resp = requests.get(BASE_URL + INVESTOR_ENDPOINT, headers=headers,
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                time.sleep(0.5 * (attempt + 1))
                continue
            for row in data.get("output", []):
                if str(row.get("frgn_ntby_qty", "")).strip() == "":
                    continue   # 당일(장중) 행 — 아직 확정 전이라 건너뜀
                return [row.get("stck_bsop_date"), trio(row, "frgn"), trio(row, "orgn"), trio(row, "prsn")]
            return None
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return None


def fetch_index(iscd: str, retries: int = 3) -> list | None:
    """
    코스피/코스닥 지수 현재가를 조회.
    주의: 이 API의 acml_tr_pbmn 은 개별종목 시세 API와 같은 필드명이지만 단위가 다르다
    (여기는 백만원 단위 -> /100 해야 억원, 개별종목 쪽은 원 단위 -> /1e8).
    반환: [지수, 등락, 등락률%, 시가, 고가, 저가, 거래대금(억), 상승종목수, 보합종목수, 하락종목수]
    """
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": INDEX_TR_ID,
        "custtype": "P",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": iscd}

    for attempt in range(retries):
        try:
            headers["authorization"] = f"Bearer {get_access_token()}"
            resp = requests.get(BASE_URL + INDEX_ENDPOINT, headers=headers,
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                time.sleep(0.5 * (attempt + 1))
                continue
            o = data.get("output") or {}
            if not o:
                return None
            sign = -1 if o.get("prdy_vrss_sign") in ("4", "5") else 1   # 4/5 = 하락
            return [
                round(_f(o.get("bstp_nmix_prpr")), 2),
                round(sign * abs(_f(o.get("bstp_nmix_prdy_vrss"))), 2),
                round(sign * abs(_f(o.get("bstp_nmix_prdy_ctrt"))), 2),
                round(_f(o.get("bstp_nmix_oprc")), 2),
                round(_f(o.get("bstp_nmix_hgpr")), 2),
                round(_f(o.get("bstp_nmix_lwpr")), 2),
                round(_f(o.get("acml_tr_pbmn")) / 100, 1),
                int(_f(o.get("ascn_issu_cnt"))),
                int(_f(o.get("stnr_issu_cnt"))),
                int(_f(o.get("down_issu_cnt"))),
            ]
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return None


def _quarterly_codes(n: int = 2) -> list:
    """오늘부터 가장 가까운 분기월(3/6/9/12) 선물 만기코드를 [\"U26\", \"Z26\", ...] 형태로 n개 반환."""
    now = datetime.now(KST)
    y, m = now.year, now.month
    codes = []
    while len(codes) < n:
        if m in _QUARTER_MONTH_CODE:
            codes.append(f"{_QUARTER_MONTH_CODE[m]}{y % 100:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return codes


def _fetch_futures_contract(srs_cd: str, retries: int = 2) -> dict | None:
    """선물 계약 코드 하나(예: NQU26)의 시세를 조회. 존재하지 않거나 미거래 계약이면 None."""
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": FUTURES_TR_ID,
        "custtype": "P",
    }
    params = {"srs_cd": srs_cd}
    for attempt in range(retries):
        try:
            headers["authorization"] = f"Bearer {get_access_token()}"
            resp = requests.get(BASE_URL + FUTURES_ENDPOINT, headers=headers,
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                time.sleep(0.5 * (attempt + 1))
                continue
            o = data.get("output1") or {}
            if not str(o.get("last_price", "")).strip():   # 존재하지 않는/휴장 계약은 전부 빈 문자열로 옴
                return None
            return o
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return None


def fetch_us_futures() -> dict:
    """
    나스닥100/S&P500/다우 선물의 현재가를 조회.
    분기월(3/6/9/12) 계약만 존재하므로, 가까운 두 분기월을 둘 다 조회해서
    거래량이 더 많은 쪽(=현재 거래되는 최근월물)을 고른다 — 만기 롤오버를 수동으로 관리할 필요 없음.
    가격 스케일은 상품마다 다름: tick_size 가 소수(0.25 등, NQ·ES)면 실제값의 1000배로 내려오고,
    정수(1, YM)면 그대로 내려온다 — tick_size 로 스케일을 판정 (하드코딩하지 않고 응답 기준 실측).
    반환: {"NQ": [현재가, 등락, 등락률%, 거래량, 거래소], "ES": [...], "YM": [...]}
    """
    months = _quarterly_codes(2)
    result: dict = {}
    for code, _name in FUT_PRODUCTS:
        candidates = []
        for mo in months:
            o = _fetch_futures_contract(f"{code}{mo}")
            if o:
                candidates.append(o)
            time.sleep(REQ_INTERVAL)
        if not candidates:
            continue
        best = max(candidates, key=lambda o: _f(o.get("vol")))
        scale = 1000 if _f(best.get("tick_size")) < 1 else 1
        sign = -1 if best.get("prev_diff_flag") in ("4", "5") else 1
        result[code] = [
            round(_f(best.get("last_price")) / scale, 2),
            round(sign * abs(_f(best.get("prev_diff_price"))) / scale, 2),
            round(sign * abs(_f(best.get("prev_diff_rate"))), 2),
            int(_f(best.get("vol"))),
            best.get("exch_cd", ""),
        ]
    return result


def fetch_investor_flow_rank(retries: int = 2) -> dict:
    """
    외국인/기관 매매종목가집계(장중 잠정 상위 랭킹)를 4가지 정렬로 모아 병합.
      - KIS API 특성상 특정 종목을 찍어 조회할 수 없고, 순매수/순매도 상위 30위 랭킹만 제공됨
      - '합산 순매수상위·순매도상위' + '외국인 순매수상위·순매도상위' 4개를 모으면
        움직임이 큰 종목은 대부분 걸리지만, 랭킹 밖으로 밀린 종목은 값이 아예 없음(구조적 한계)
    반환: {종목코드: [외국인순매수량, 기관순매수량, 외국인순매수대금(억), 기관순매수대금(억)]}
    """
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": FLOW_TR_ID,
        "custtype": "P",
    }
    base_params = {
        "FID_COND_MRKT_DIV_CODE": "V",     # V = 전체시장
        "FID_COND_SCR_DIV_CODE": "16449",  # 화면번호(고정값)
        "FID_INPUT_ISCD": "0000",
        "FID_ETC_CLS_CODE": "0",
    }

    flow: dict = {}
    # DIV=0(외국인+기관 합산 기준) / DIV=1(외국인 단독 기준) × SORT=0(순매수상위) / SORT=1(순매도상위)
    for div in ("0", "1"):
        for sort in ("0", "1"):
            params = {**base_params, "FID_DIV_CLS_CODE": div, "FID_RANK_SORT_CLS_CODE": sort}
            for attempt in range(retries):
                try:
                    headers["authorization"] = f"Bearer {get_access_token()}"
                    resp = requests.get(BASE_URL + FLOW_ENDPOINT, headers=headers,
                                        params=params, timeout=10)
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("rt_cd") != "0":
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    for o in data.get("output", []):
                        code = str(o.get("mksc_shrn_iscd", "")).strip()
                        flow[code] = [
                            int(_f(o.get("frgn_ntby_qty"))),
                            int(_f(o.get("orgn_ntby_qty"))),
                            round(_f(o.get("frgn_ntby_tr_pbmn")) / 100, 1),   # 백만원 -> 억원
                            round(_f(o.get("orgn_ntby_tr_pbmn")) / 100, 1),
                        ]
                    break
                except Exception:
                    time.sleep(0.5 * (attempt + 1))
            time.sleep(REQ_INTERVAL)
    return flow


def fetch_top_amount(retries: int = 3) -> list:
    """
    거래대금(매수금액) 상위 30종목을 조회.
    코스피(0001)·코스닥(1001)을 따로 조회한 뒤 합쳐서 다시 정렬한다.
      - 시장 전체(0000)로 조회하면 KODEX·TIGER 같은 ETF가 절반 넘게 차지해버림
      - 시장별로 조회하면 ETF가 섞이지 않아 개별종목만으로 30위를 채울 수 있음
    이어서 3가지 수급 데이터를 덧붙인다.
      - 외국인/기관: 장중 잠정 순매수 (상위 랭킹에 걸린 종목만 커버, 매수/매도 개별 조회는 API 미지원)
      - 프로그램매매: 장중 잠정 매수/매도/순매수 (전 종목 커버)
      - 전일 확정: 외국인/기관/개인 각각 매수/매도/순매수 (전일까지 확정치, 당일 데이터 없음)
    반환: [코드, 종목명, 현재가, 등락률, 거래대금(억), 거래량, 거래량증가율, 시장,
           외국인순매수량|null, 외국인순매수대금(억)|null, 기관순매수량|null, 기관순매수대금(억)|null,
           프로그램순매수량|null, 프로그램순매수대금(억)|null,
           프로그램매수량|null, 프로그램매도량|null, 프로그램매수대금(억)|null, 프로그램매도대금(억)|null,
           전일확정[기준일자,외국인[매도량,매수량,순매수량,매도대금,매수대금,순매수대금],
                    기관[..],개인[..]]|null,
           시가총액(억)|null] 리스트
    """
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": RANK_TR_ID,
        "custtype": "P",
    }
    base_params = {
        # J = 주식. 이 순위분석 화면은 UN(통합)을 넣으면 "잘못된 조건"으로 에러가 남 —
        # 즉 거래대금 상위 30 랭킹 자체는 KRX 체결분만 집계됨(넥스트트레이드 미반영, API 제약).
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",  # 순위분석 화면번호(고정값)
        "FID_DIV_CLS_CODE": "0",           # 0 = 전체
        "FID_BLNG_CLS_CODE": "3",          # 3 = 거래금액순 (= 매수금액 상위)
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }

    etfs = etf_codes()
    merged: list = []
    shares: dict = {}   # {종목코드: 상장주수} — 시가총액 계산용, volume-rank 응답에 이미 들어있음

    for iscd, market in (("0001", "코스피"), ("1001", "코스닥")):
        params = {**base_params, "FID_INPUT_ISCD": iscd}
        for attempt in range(retries):
            try:
                headers["authorization"] = f"Bearer {get_access_token()}"
                resp = requests.get(BASE_URL + RANK_ENDPOINT, headers=headers,
                                    params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                if data.get("rt_cd") != "0":
                    time.sleep(0.6 * (attempt + 1))
                    continue
                for o in data.get("output", []):
                    code = str(o.get("mksc_shrn_iscd", "")).strip()
                    name = str(o.get("hts_kor_isnm", "")).strip()
                    if code in etfs or looks_like_etf(name):   # 혹시 섞여 들어오면 한 번 더 걸러냄
                        continue
                    shares[code] = _f(o.get("lstn_stcn"))
                    merged.append([
                        code,
                        name,
                        int(_f(o.get("stck_prpr"))),                    # 현재가
                        round(_f(o.get("prdy_ctrt")), 2),               # 등락률 %
                        round(_f(o.get("acml_tr_pbmn")) / 1e8, 1),      # 거래대금 -> 억원
                        int(_f(o.get("acml_vol"))),                     # 거래량
                        round(_f(o.get("vol_inrt")), 1),                # 전일대비 거래량증가율 %
                        market,                                         # 코스피 / 코스닥
                    ])
                break
            except Exception:
                time.sleep(0.6 * (attempt + 1))
        time.sleep(REQ_INTERVAL)

    merged.sort(key=lambda x: -x[4])   # 거래대금 내림차순
    top30 = merged[:30]

    flow = fetch_investor_flow_rank()
    for row in top30:
        code = row[0]
        f = flow.get(code)
        row.append(f[0] if f else None)   # 외국인순매수량
        row.append(f[2] if f else None)   # 외국인순매수대금(억)
        row.append(f[1] if f else None)   # 기관순매수량
        row.append(f[3] if f else None)   # 기관순매수대금(억)

        prog = fetch_program_trade(code)
        row.append(prog[2] if prog else None)   # 프로그램순매수량
        row.append(prog[5] if prog else None)   # 프로그램순매수대금(억)
        row.append(prog[1] if prog else None)   # 프로그램매수량
        row.append(prog[0] if prog else None)   # 프로그램매도량
        row.append(prog[4] if prog else None)   # 프로그램매수대금(억)
        row.append(prog[3] if prog else None)   # 프로그램매도대금(억)
        time.sleep(REQ_INTERVAL)

        row.append(fetch_investor_daily(code))  # 전일 확정 외국인/기관/개인 매수·매도·순매수 (없으면 None)
        time.sleep(REQ_INTERVAL)

        sh = shares.get(code)
        row.append(round(row[2] * sh / 1e8, 0) if sh else None)   # 시가총액(억) = 현재가 × 상장주수

    return top30


def collect_snapshot() -> dict:
    """전 종목을 한 바퀴 돌며 {종목코드: [등락률, 현재가, 거래대금(억), 시가총액(억)]} 스냅샷을 만든다."""
    rows = all_tickers()
    snapshot: dict[str, list] = {}
    failed = []

    for i, (_sector, code, name) in enumerate(rows, 1):
        out = fetch_quote(code)
        if out is None:
            failed.append(f"{name}({code})")
        else:
            chg = round(_f(out.get("prdy_ctrt")), 2)          # 전일대비 등락률 %
            price = int(_f(out.get("stck_prpr")))              # 현재가
            amount = round(_f(out.get("acml_tr_pbmn")) / 1e8, 1)  # 누적거래대금 -> 억원
            cap = round(_f(out.get("hts_avls")), 0)             # 시가총액(이미 억원 단위로 내려옴)
            snapshot[code] = [chg, price, amount, cap]
        if i % 20 == 0:
            print(f"  … {i}/{len(rows)} 수집", flush=True)
        time.sleep(REQ_INTERVAL)

    if failed:
        print(f"  ⚠ 조회 실패 {len(failed)}종목: {', '.join(failed[:8])}"
              f"{' 외' if len(failed) > 8 else ''}")
    return snapshot


# ---------------------------------------------------------------- 슬롯/저장

def current_slot(now: datetime) -> str:
    """현재 시각이 속한 10분 슬롯 라벨. 장 시작 전이면 0900, 장 마감 후면 1530으로 묶는다."""
    hm = now.hour * 100 + now.minute
    if hm < 900:
        return "0900"
    if hm >= 1530:
        return "1530"
    return f"{now.hour:02d}{(now.minute // 10) * 10:02d}"


def load_day(date_str: str) -> dict:
    """그날 파일이 있으면 읽고, 없으면 빈 구조를 만든다."""
    path = DATA / f"{date_str}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"date": date_str, "updated_at": None, "slots": []}


def save_day(day: dict) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{day['date']}.json"
    path.write_text(json.dumps(day, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    return path


def upsert_slot(day: dict, slot: str, snapshot: dict, ranks: list | None = None,
                idx: dict | None = None, fut: dict | None = None) -> dict:
    """같은 슬롯을 다시 수집하면 덮어쓰고, 새 슬롯이면 시간순으로 끼워 넣는다."""
    entry = {"t": slot, "d": snapshot, "r": ranks or [], "idx": idx or {}, "fut": fut or {}}
    for i, s in enumerate(day["slots"]):
        if s["t"] == slot:
            day["slots"][i] = entry
            break
    else:
        day["slots"].append(entry)
    day["slots"].sort(key=lambda s: s["t"])
    return day


# ---------------------------------------------------------------- 페이지 생성

def build_page(day: dict) -> Path:
    """수집한 데이터를 그대로 박아 넣은 자립형(self-contained) HTML 을 생성."""
    universe = {sec: [[c, n] for c, n in members] for sec, members in SECTORS.items()}
    payload = {
        "universe": universe,
        "date": day["date"],
        "updated_at": day["updated_at"],
        "slot_labels": SLOT_LABELS,
        "slots": day["slots"],
        "available_dates": sorted(p.stem for p in DATA.glob("*.json")),
    }
    template = (ROOT / "sector_matrix_template.html").read_text(encoding="utf-8")
    html = template.replace(
        "/*__PAYLOAD__*/null",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    DOCS.mkdir(exist_ok=True)
    out = DOCS / "index.html"
    out.write_text(html, encoding="utf-8")
    return out


# ---------------------------------------------------------------- 깃허브 반영

def git_push(date_str: str, slot: str) -> None:
    """생성된 페이지와 데이터를 커밋하고 원격에 올린다."""
    try:
        subprocess.run(["git", "add", "docs", "sector_matrix.py",
                        "sector_universe.py", "sector_matrix_template.html"],
                       cwd=ROOT, check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
        if result.returncode == 0:
            print("  변경 사항 없음 — 푸시 생략")
            return
        subprocess.run(["git", "commit", "-m",
                        f"Sector matrix snapshot {date_str} {slot[:2]}:{slot[2:]}"],
                       cwd=ROOT, check=True)
        subprocess.run(["git", "push"], cwd=ROOT, check=True)
        print("  ✔ 깃허브 푸시 완료")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ 깃 작업 실패: {e}")


# ---------------------------------------------------------------- 실행 흐름

def run_once(push: bool = False) -> None:
    now = datetime.now(KST)
    date_str = now.strftime("%Y%m%d")
    slot = current_slot(now)

    print(f"[{now:%H:%M:%S}] {date_str} {slot} 슬롯 수집 시작 "
          f"({len(all_tickers())}종목)")
    snapshot = collect_snapshot()

    print("  매수금액 상위 30 조회 중…", flush=True)
    ranks = fetch_top_amount()
    if not ranks:
        print("  ⚠ 매수금액 순위 조회 실패 — 이번 슬롯은 순위표 없이 저장")

    idx = {}
    for iscd, name in INDEX_CODES:
        v = fetch_index(iscd)
        if v is not None:
            idx[name] = v
        time.sleep(REQ_INTERVAL)
    if len(idx) < 2:
        print(f"  ⚠ 지수 조회 일부 실패 ({', '.join(idx) or '전부 실패'})")

    fut = fetch_us_futures()
    if len(fut) < len(FUT_PRODUCTS):
        print(f"  ⚠ 미국 선물 일부 실패 ({', '.join(fut) or '전부 실패'})")

    day = load_day(date_str)
    day = upsert_slot(day, slot, snapshot, ranks, idx, fut)
    day["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    save_day(day)

    out = build_page(day)
    print(f"  ✔ {out} 생성 (슬롯 {len(day['slots'])}개 누적, 순위 {len(ranks)}종목, "
          f"지수 {len(idx)}개, 선물 {len(fut)}개)")

    if push:
        git_push(date_str, slot)


def is_market_time(now: datetime) -> bool:
    """평일 09:00~15:35 사이인지 (주말/야간에는 수집하지 않음)."""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 900 <= hm <= 1535


def run_daily(push: bool) -> None:
    """
    하루 한 번 실행되는 모드 (launchd 가 매일 오전 8시에 띄움).
      - 주말/공휴일이면 아무것도 하지 않고 즉시 종료
      - 장 시작 전에는 대기, 09:00~15:30 은 10분마다 수집
      - 장 마감(15:35) 이 지나면 스스로 종료 -> 다음 날 아침에 새 프로세스로 다시 시작
    """
    now = datetime.now(KST)
    if now.weekday() >= 5:
        print(f"[{now:%m-%d %H:%M}] 주말 — 오늘은 수집하지 않고 종료")
        return
    if now.hour * 100 + now.minute > 1535:
        print(f"[{now:%m-%d %H:%M}] 이미 장 마감 이후 — 종료")
        return

    print(f"[{now:%m-%d %H:%M}] 오늘 장 수집 시작 (마감까지 10분 간격)")
    while True:
        now = datetime.now(KST)
        hm = now.hour * 100 + now.minute
        if hm > 1535:
            print(f"[{now:%m-%d %H:%M}] 장 마감 — 오늘 수집 종료")
            return
        if hm >= 900:
            run_once(push=push)
        else:
            print(f"[{now:%m-%d %H:%M}] 장 시작 전 — 대기", flush=True)

        # 다음 10분 경계까지 대기 (수집에 걸린 시간만큼 자동으로 짧아짐)
        now = datetime.now(KST)
        nxt = (now + timedelta(minutes=10)).replace(second=5, microsecond=0)
        nxt = nxt.replace(minute=(nxt.minute // 10) * 10)
        time.sleep(max(20, (nxt - now).total_seconds()))


def run_loop(push: bool) -> None:
    """장중에는 10분 경계마다, 장외에는 대기하며 반복."""
    print("장중 10분 단위 자동 수집 시작 (Ctrl+C 로 종료)")
    while True:
        now = datetime.now(KST)
        if is_market_time(now):
            run_once(push=push)
        else:
            print(f"[{now:%m-%d %H:%M}] 장외 시간 — 대기")

        # 다음 10분 경계까지 대기
        now = datetime.now(KST)
        nxt = (now + timedelta(minutes=10)).replace(second=5, microsecond=0)
        nxt = nxt.replace(minute=(nxt.minute // 10) * 10)
        wait = max(30, (nxt - now).total_seconds())
        time.sleep(wait)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="주도섹터/주도주 10분 매트릭스")
    parser.add_argument("--loop", action="store_true", help="장중 10분마다 자동 반복 (계속 상주)")
    parser.add_argument("--daily", action="store_true",
                        help="오늘 장만 수집하고 마감 후 종료 (launchd 자동실행용)")
    parser.add_argument("--push", action="store_true", help="수집 후 깃허브 커밋/푸시")
    parser.add_argument("--rebuild", action="store_true",
                        help="시세 수집 없이 저장된 데이터로 페이지만 다시 생성")
    parser.add_argument("--date", default=None, help="rebuild 대상 날짜 YYYYMMDD")
    args = parser.parse_args()

    if args.rebuild:
        date_str = args.date or datetime.now(KST).strftime("%Y%m%d")
        day = load_day(date_str)
        print(f"✔ {build_page(day)} 재생성 (슬롯 {len(day['slots'])}개)")
        sys.exit(0)

    if args.daily:
        run_daily(push=args.push)
    elif args.loop:
        run_loop(push=args.push)
    else:
        run_once(push=args.push)
