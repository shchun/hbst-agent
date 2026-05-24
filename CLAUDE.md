# Hermes 개인비서

개인 지식 베이스(옵시디언 볼트)와 맛집 DB를 검색·종합하고 기록하는 범용 개인비서.
Slack DM·멘션에 응답하고, 현재 위치 근처 맛집은 프로액티브 알림을 보낸다. 맛집은 그 일부 기능.

## 아키텍처

```
data/*.csv                        옵시디언 볼트 (git: 로컬 ↔ GitHub ↔ VM)
    ↓ scripts/import_csv.py            ↓ (hermes_inbox만 write)
PostgreSQL + PostGIS               mcp/vault_mcp.py  ← search_notes / capture_note
    ↓                                  ↓
mcp/matzip_mcp.py  ← MCP 서버 (stdio) ─┘
    ↓
Hermes Agent (gpt-4o-mini)
    ↓
Slack 게이트웨이 (DM/멘션 응답 + cron 프로액티브 알림)
```

- **DB**: PostgreSQL 15 + PostGIS 3.4 (`ST_DWithin`, `ST_MakePoint`)
- **볼트**: 옵시디언 볼트를 git으로 동기화. Hermes는 `hermes_inbox/`에 새 파일만 생성(append-only), 기존 노트는 수정 안 함. `.hermesignore` 폴더는 검색 제외.
- **위치**: `HOME_LAT`/`HOME_LNG` 환경변수 우선, 없으면 ip-api.com
- **지오코딩**: Google Maps Geocoding API
- **에이전트**: Hermes Agent + MCP 프로토콜 (stdio 서버)
- **Slack**: Hermes 게이트웨이 Socket Mode

상세 설계는 `docs/Plan_add_obsidian.md` 참고.

## 주요 파일

| 파일 | 역할 |
|------|------|
| `mcp/matzip_mcp.py` | matzip MCP 서버 — 6개 도구 (find_nearby, geocode_area 등) |
| `mcp/vault_mcp.py` | vault MCP 서버 — search_notes(grep), capture_note(동기 git commit+push) |
| `mcp/SOUL.md` | Hermes 에이전트 페르소나 |
| `mcp/hermes_config_template.yaml` | `~/.hermes/config.yaml` MCP 설정 참고용 |
| `scripts/init.sql` | DB 스키마 (PostGIS extension + matzip 테이블) |
| `scripts/import_csv.py` | `data/*.csv` → DB 전체 재임포트 |
| `data/*.csv` | 맛집 데이터 (이름, 주소, 메모, 위도, 경도) |

## 개발 환경 시작

```powershell
# DB 실행
docker compose up -d db

# CSV 데이터 재임포트
python scripts/import_csv.py

# Hermes 에이전트 실행
hermes chat

# Slack 게이트웨이 실행
hermes gateway start
```

## MCP 서버 로그 확인

```powershell
Get-Content "$env:USERPROFILE\AppData\Local\hermes\logs\mcp-stderr.log" -Wait
```

## 환경 변수 (`.env`)

`.env.example` 참고. 필수값:

| 변수 | 설명 |
|------|------|
| `SLACK_BOT_TOKEN` | `xoxb-` Bot Token |
| `SLACK_APP_TOKEN` | `xapp-` App-Level Token (Socket Mode, `connections:write` 스코프) |
| `SLACK_CHANNEL` | 프로액티브 알림 채널 ID |
| `GOOGLE_MAPS_API_KEY` | 지오코딩용 API 키 |
| `HOME_LAT` / `HOME_LNG` | 고정 위치 좌표 |
| `PROXIMITY_RADIUS_METERS` | 알림 반경 (기본 500m) |
| `VAULT_DIR` | 옵시디언 볼트 경로 (vault MCP 서버) |
| `VAULT_GIT_SYNC` | capture_note의 git commit+push (1=켬, 0=끔) |

## Hermes 설정 위치

Hermes 관련 설정은 프로젝트 외부에 있음:

| 경로 | 내용 |
|------|------|
| `~/.hermes/config.yaml` | 모델, MCP 서버, 플랫폼 설정 |
| `~/.hermes/.env` | API 키, Slack 토큰 |
| `~/.hermes/SOUL.md` | 에이전트 페르소나 (mcp/SOUL.md와 동기화) |

## Slack 앱 설정 요구사항

- **OAuth Scopes**: `chat:write`, `im:history`
- **Event Subscriptions**: `app_mention`, `message.im`
- **App Home**: Messages Tab 활성화
- **Socket Mode**: 활성화 + App-Level Token 발급

## 주의사항

- `.env`는 절대 커밋하지 말 것 (`.gitignore`에 포함)
- MCP 서버는 `mcp/.venv` 가상환경 사용
- `find_nearby`: 결과 < 3개면 1km → 2km → 5km 자동 확장
- CSV 파일명 필터: 한글·영문·숫자만 허용, 인코딩 깨진 파일 제외
