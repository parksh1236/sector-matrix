# 주도섹터 · 주도주 10분 매트릭스

한국 증시의 **주도섹터**와 각 섹터의 **주도주**를 10분 단위 스냅샷으로 쌓아
하나의 히트맵으로 보여주는 정적 대시보드입니다.
한국투자증권 Open API에서 시세를 읽어 `docs/index.html` 을 다시 만들고, GitHub Pages로 그대로 서비스합니다.

> 시세 **조회 전용** 도구입니다. 주문·매매 기능은 들어 있지 않고, 계좌번호도 요구하지 않습니다.

## 화면

### ① 섹터 매트릭스 탭

| 영역 | 내용 |
|------|------|
| 상단 카드 | 현재 가장 강한 주도섹터 4개 — 평균 등락률, 상승/하락 종목 수, 거래대금, 최근 10분 변화폭, 그리고 그 섹터의 **주도주** |
| 매트릭스 | 세로축 = 섹터, 가로축 = 10분 단위 시간대. 칸 색은 그 시점 섹터 평균 등락률 (한국식: 상승 빨강 / 하락 파랑, ±3%에서 최대 농도) |
| 섹터 행 클릭 | 구성 종목이 펼쳐지며 **종목 × 시간** 미니 히트맵 표시. 맨 위 ★ 종목이 현재 주도주 |
| 칸 클릭 | 하단 상세 패널에 해당 시각·해당 섹터의 종목 전체 순위(등락률·현재가·거래대금) |
| 정렬 | 현재 강도 / 최근 10분 모멘텀 / 거래대금 |

### ② 매수금액 TOP 30 탭

| 영역 | 내용 |
|------|------|
| 요약 | 상위 30 합산 거래대금, 코스피/코스닥 분포, 직전 슬롯 대비 신규 진입 수, 1위 종목 |
| 자금 쏠림 | 상위 30의 거래대금을 섹터별로 묶은 막대 — 어느 섹터로 돈이 몰렸는지 한눈에 |
| 순위표 | 1~30위. 직전 10분 대비 순위 변동(▲▼/NEW), 섹터 뱃지, 등락률, 현재가, 거래대금, 거래량, 거래량 증가율 |
| 기준 시각 | 10분 슬롯을 골라 그 시점의 순위표를 그대로 다시 볼 수 있음 |

ETF·ETN은 순위에서 제외합니다. 시장 전체로 조회하면 KODEX·TIGER류가 상위권 절반 이상을 차지해
개별 주도주가 묻히기 때문에, 코스피·코스닥을 따로 조회해 합친 뒤 다시 정렬합니다.

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env    # 그리고 KIS 앱키/시크릿을 채워 넣기
```

## 실행

```bash
python sector_matrix.py               # 지금 한 번 수집하고 페이지 생성
python sector_matrix.py --loop        # 장중 10분마다 자동 수집
python sector_matrix.py --loop --push # 자동 수집 + 깃허브 자동 커밋/푸시
python sector_matrix.py --daily --push # 오늘 장만 수집하고 마감 후 종료 (자동실행용)
python sector_matrix.py --rebuild     # 수집 없이 저장된 데이터로 페이지만 재생성
./run_sector_matrix.sh                # --loop --push 를 백그라운드로 실행
```

### 매일 자동 실행 (macOS launchd)

`launchd/com.parksh.sector-matrix.plist` 를 `~/Library/LaunchAgents/` 에 두면
**매일 오전 8시에 자동으로 시작**해서 장 마감 후 스스로 종료합니다. 주말이면 즉시 종료합니다.

```bash
cp launchd/com.parksh.sector-matrix.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.parksh.sector-matrix.plist
launchctl print gui/$(id -u)/com.parksh.sector-matrix | grep -E "state|last exit code"
```

로그는 `~/Library/Logs/sector-matrix.log` 에 쌓입니다.

> ⚠️ 로그 경로를 `~/Documents` 안으로 두면 macOS 개인정보 보호(TCC)가 백그라운드 에이전트의
> 쓰기를 막아 **exit 78(EX_CONFIG)** 로 조용히 죽습니다. 로그는 반드시 `~/Library/Logs` 처럼
> 보호되지 않는 경로에 두세요.

중지하려면:

```bash
launchctl bootout gui/$(id -u)/com.parksh.sector-matrix
```

## 구조

| 파일 | 역할 |
|------|------|
| `sector_universe.py` | 섹터별 구성 종목 정의. **섹터 구성을 바꾸려면 이 파일만 수정하면 됩니다.** |
| `sector_matrix.py` | 시세 수집 → `docs/data/YYYYMMDD.json` 누적 → 페이지 생성 → 깃 푸시 |
| `sector_matrix_template.html` | 대시보드 UI 템플릿 (데이터가 주입되어 `docs/index.html` 이 됨) |
| `auth.py` / `config.py` | KIS Open API 토큰 발급 및 캐싱 |
| `launchd/` | macOS 자동 실행용 LaunchAgent 설정 |
| `docs/` | GitHub Pages 로 서비스되는 결과물 |

## 데이터

- 시세: `FHKST01010100` (주식현재가 시세) — 섹터 구성 종목 전체
- 순위: `FHPST01710000` (거래량순위, 거래금액순) — 코스피·코스닥 각각 조회 후 병합
- 섹터 평균 등락률 = 구성 종목 등락률의 단순 평균
- 주도주 = 해당 시점 섹터 내 등락률 1위 종목
- 스냅샷 원본은 `docs/data/YYYYMMDD.json` 에 하루 단위로 누적

## 주의

종목코드는 상장폐지·사명변경이 잦습니다. 섹터 구성을 손볼 때는 KRX 코드 마스터로 한 번 대조하는 편이 안전합니다.
