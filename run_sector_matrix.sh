#!/bin/bash
# 장중 10분마다 수집 + 페이지 생성 + 깃허브 푸시를 백그라운드로 실행
# 사용법:  ./run_sector_matrix.sh
#          tail -f sector_matrix.log   (진행 상황 확인)
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
nohup "$PY" sector_matrix.py --loop --push >> sector_matrix.log 2>&1 &
echo "수집기 시작 (PID $!) — 로그: sector_matrix.log"
