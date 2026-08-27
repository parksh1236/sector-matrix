"""
KIS Open API 인증 정보 로드
- 이 프로젝트는 시세 '조회'만 하므로 계좌번호는 필요 없다 (주문 기능 없음)
- .env 파일에 앱키/시크릿을 넣어두면 자동으로 읽어온다
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 기본은 실전 도메인 — 모의투자 도메인은 시세 조회 호출 한도가 훨씬 빡빡함
KIS_IS_PAPER = os.environ.get("KIS_IS_PAPER", "false").lower() == "true"

if KIS_IS_PAPER:
    KIS_APP_KEY = os.environ["KIS_APP_KEY"]
    KIS_APP_SECRET = os.environ["KIS_APP_SECRET"]
else:
    # 실전 계정의 앱키/시크릿 (조회 전용으로만 사용)
    KIS_APP_KEY = os.environ["KIS_APP_KEY_REAL"]
    KIS_APP_SECRET = os.environ["KIS_APP_SECRET_REAL"]
