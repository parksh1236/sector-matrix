"""
한국투자증권 Open API 인증 모듈
- 접근토큰(access token) 발급 및 캐싱
- 모의투자(KIS_IS_PAPER=true) / 실전투자 도메인을 자동 분기
"""
import json
import time
from pathlib import Path

import requests

from config import KIS_APP_KEY, KIS_APP_SECRET, KIS_IS_PAPER

# 모의투자와 실전투자는 서버 주소(도메인)가 다름
REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"
PAPER_DOMAIN = "https://openapivts.koreainvestment.com:29443"

BASE_URL = PAPER_DOMAIN if KIS_IS_PAPER else REAL_DOMAIN

# 토큰은 발급 후 24시간 유효 -> 매번 새로 받지 않고 파일에 캐싱해서 재사용
# 모의/실전 도메인이 섞여 돌아갈 수 있으니 도메인별로 캐시 파일을 분리
_cache_suffix = "paper" if KIS_IS_PAPER else "real"
TOKEN_CACHE_PATH = Path(__file__).parent / f".token_cache_{_cache_suffix}.json"


def issue_access_token() -> str:
    """KIS 서버에 접근토큰을 새로 요청해서 발급받는다."""
    url = f"{BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
    }

    resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
    resp.raise_for_status()
    data = resp.json()

    token = data["access_token"]
    expires_in = data.get("expires_in", 86400)  # 초 단위, 기본 24시간

    # 다음 실행 때 재사용할 수 있도록 만료시각과 함께 저장
    cache = {"access_token": token, "expires_at": time.time() + expires_in - 60}
    TOKEN_CACHE_PATH.write_text(json.dumps(cache))

    return token


def get_access_token() -> str:
    """캐시된 토큰이 유효하면 재사용하고, 아니면 새로 발급받는다."""
    if TOKEN_CACHE_PATH.exists():
        cache = json.loads(TOKEN_CACHE_PATH.read_text())
        if time.time() < cache.get("expires_at", 0):
            return cache["access_token"]

    return issue_access_token()


if __name__ == "__main__":
    token = get_access_token()
    # 토큰 전체를 출력하면 노출 위험이 있으니 앞 8자리만 확인용으로 표시
    print(f"인증 성공. BASE_URL={BASE_URL}")
    print(f"access_token(앞 8자리): {token[:8]}...")
