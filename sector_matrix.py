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
    """종목 하나의 현재가/등락률/거래대금을 조회. 실패하면 None."""
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": PRICE_TR_ID,
        "custtype": "P",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}

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


def fetch_top_amount(retries: int = 3) -> list:
    """
    거래대금(매수금액) 상위 30종목을 조회.
    코스피(0001)·코스닥(1001)을 따로 조회한 뒤 합쳐서 다시 정렬한다.
      - 시장 전체(0000)로 조회하면 KODEX·TIGER 같은 ETF가 절반 넘게 차지해버림
      - 시장별로 조회하면 ETF가 섞이지 않아 개별종목만으로 30위를 채울 수 있음
    반환: [코드, 종목명, 현재가, 등락률, 거래대금(억), 거래량, 거래량증가율, 시장] 리스트
    """
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": RANK_TR_ID,
        "custtype": "P",
    }
    base_params = {
        "FID_COND_MRKT_DIV_CODE": "J",     # J = 주식
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
    return merged[:30]


def collect_snapshot() -> dict:
    """전 종목을 한 바퀴 돌며 {종목코드: [등락률, 현재가, 거래대금(억)]} 스냅샷을 만든다."""
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
            snapshot[code] = [chg, price, amount]
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


def upsert_slot(day: dict, slot: str, snapshot: dict, ranks: list | None = None) -> dict:
    """같은 슬롯을 다시 수집하면 덮어쓰고, 새 슬롯이면 시간순으로 끼워 넣는다."""
    entry = {"t": slot, "d": snapshot, "r": ranks or []}
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

    day = load_day(date_str)
    day = upsert_slot(day, slot, snapshot, ranks)
    day["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    save_day(day)

    out = build_page(day)
    print(f"  ✔ {out} 생성 (슬롯 {len(day['slots'])}개 누적, 순위 {len(ranks)}종목)")

    if push:
        git_push(date_str, slot)


def is_market_time(now: datetime) -> bool:
    """평일 09:00~15:35 사이인지 (주말/야간에는 수집하지 않음)."""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 900 <= hm <= 1535


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
    parser.add_argument("--loop", action="store_true", help="장중 10분마다 자동 반복")
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

    if args.loop:
        run_loop(push=args.push)
    else:
        run_once(push=args.push)
