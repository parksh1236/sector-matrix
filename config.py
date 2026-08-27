"""
KIS Open API 인증 정보 로드
- 이 프로젝트는 시세 '조회'만 하므로 계좌번호는 필요 없다 (주문 기능 없음)
- .env 파일에 앱키/시크릿을 넣어두면 자동으로 읽어온다
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# launchd 등 작업 디렉터리가 다를 수 있는 환경에서도 항상 이 파일 옆의 .env 를 읽도록 고정
load_dotenv(Path(__file__).resolve().parent / ".env")

# 기본은 실전 도메인 — 모의투자 도메인은 시세 조회 호출 한도가 훨씬 빡빡함
KIS_IS_PAPER = os.environ.get("KIS_IS_PAPER", "false").lower() == "true"

_KEY, _SECRET = ("KIS_APP_KEY", "KIS_APP_SECRET") if KIS_IS_PAPER \
    else ("KIS_APP_KEY_REAL", "KIS_APP_SECRET_REAL")  # 실전 계정 (조회 전용으로만 사용)

_missing = [k for k in (_KEY, _SECRET) if not os.environ.get(k)]
if _missing:
    # 자동 실행(launchd) 로그에서 원인이 바로 보이도록 친절한 메시지로 알린다
    raise SystemExit(
        f"[설정 오류] 환경변수 {', '.join(_missing)} 가 비어 있습니다.\n"
        f"  이 폴더에 .env 파일을 만들고 KIS 앱키/시크릿을 넣어주세요.\n"
        f"  예시는 .env.example 파일을 참고하세요."
    )

KIS_APP_KEY = os.environ[_KEY]
KIS_APP_SECRET = os.environ[_SECRET]
