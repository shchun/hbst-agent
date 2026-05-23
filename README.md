# 🗺️ Hermes 맛집 프로젝트

내가 직접 저장한 맛집 근처에 오면 Slack으로 알려주는 개인 맛집 에이전트.

## 구조

```
[맛집2024.csv]
      ↓  import_csv.py
  PostgreSQL (PostGIS)
      ↓
  hermes agent
  (위치 체크 루프)
      ↓
  Slack Bot 알림
```

## 빠른 시작

### 1. 환경변수 설정

```bash
cp .env.example .env
# .env 파일에서 SLACK_BOT_TOKEN, SLACK_CHANNEL 입력
```

### 2. Docker 실행

```bash
docker-compose up -d
```

### 3. CSV 데이터 Import

```bash
docker-compose exec agent python /scripts/import_csv.py
```

또는 로컬에서:

```bash
pip install psycopg2-binary
DATABASE_URL=postgresql://hermes:hermes1234@localhost:5432/hermes \
CSV_PATH=data/맛집2024.csv \
python scripts/import_csv.py
```

## 파일 구조

```
matzip/
├── docker-compose.yml
├── .env.example
├── data/
│   └── 맛집2024.csv
├── scripts/
│   ├── init.sql          # DB 초기화 (PostGIS 포함)
│   └── import_csv.py     # CSV → DB import
└── app/
    ├── Dockerfile
    ├── requirements.txt
    └── agent.py          # 메인 에이전트
```

## 설정값

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `PROXIMITY_RADIUS_METERS` | 500 | 알림 반경 (미터) |
| `CHECK_INTERVAL_SECONDS` | 60 | 위치 체크 주기 (초) |
| `SLACK_CHANNEL` | #hermes | Slack 채널명 |

## Slack Bot 설정

1. https://api.slack.com/apps 에서 새 앱 생성
2. OAuth Scopes: `chat:write`, `chat:write.public`
3. Bot Token (`xoxb-...`) 을 `.env`에 입력
4. 채널에 봇 초대: `/invite @hermes`

## 위치 수집 방식

현재는 IP 기반 위치 사용 (정확도 낮음).
정확한 GPS 위치를 사용하려면 `agent.py`의 `get_current_location()` 함수를 교체:

```python
# 예: 휴대폰 GPS API, Tasker webhook, iOS Shortcuts 등
def get_current_location():
    r = requests.get("http://your-phone-gps-endpoint/location")
    data = r.json()
    return data["lat"], data["lng"]
```
