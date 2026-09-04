# 주도섹터 · 주도주 10분 매트릭스

한국 증시의 **주도섹터**와 각 섹터의 **주도주**를 10분 단위 스냅샷으로 쌓아
하나의 히트맵으로 보여주는 정적 대시보드입니다.
한국투자증권 Open API에서 시세를 읽어 `docs/index.html` 을 다시 만들고, GitHub Pages로 그대로 서비스합니다.

> 시세 **조회 전용** 도구입니다. 주문·매매 기능은 들어 있지 않고, 계좌번호도 요구하지 않습니다.

## 화면

### 코스피·코스닥·미국 선물 (두 탭 공통, 헤더 바로 아래)

| 항목 | 내용 |
|------|------|
| 코스피 / 코스닥 | 현재 지수·등락·등락률, 거래대금, 상승/보합/하락 종목 수, 하루치 10분 슬롯을 이은 라인차트(hover 시 크로스헤어+툴팁) |
| 나스닥100 / S&P500 / 다우 선물 | 코스피 옆에 나란히 — CME 지수선물 현재가·등락률, 같은 방식의 라인차트. 분기월물(3/6/9/12월)이라 거래량이 더 많은 근월물을 자동으로 골라 씀(만기 롤오버 수동 관리 불필요) |

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

### 넥스트트레이드(NXT) 반영 범위

2025년 시작된 대체거래소 넥스트트레이드(NXT) 체결분까지 포함할지는 KIS API 엔드포인트마다 다릅니다.

| 데이터 | NXT 반영 | 비고 |
|--------|:---:|------|
| 섹터 매트릭스의 종목 시세·거래대금 | ✅ | `FID_COND_MRKT_DIV_CODE=UN`(통합) 사용. KRX단독+NXT단독 거래량 합 = UN 거래량으로 검증함 |
| 매수금액 TOP 30 순위표 | ❌ | 순위분석 API가 UN을 받지 않음(파라미터 오류) — KRX 체결분만 집계 |
| 코스피/코스닥 지수 | ❌ | 지수 자체가 KRX 산출 지수라 NXT 별도 지수는 없음 |
| 외국인/기관/프로그램매매 | ❌ | UN을 줘도 KRX단독과 값이 완전히 같음(API가 사실상 무시) — 실측으로 확인 |

가능한 곳만 통합하고, 안 되는 곳은 화면에 "KRX 기준"이라고 명시해뒀습니다.

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

## 라이선스

[MIT](LICENSE) — 출처만 표시하면 자유롭게 가져다 쓰셔도 됩니다.
