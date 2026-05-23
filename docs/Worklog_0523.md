# Worklog — 2026-05-23

## 목표

로컬에서 개발·테스트하던 Hermes 맛집 에이전트를 AWS EC2(Seoul 리전)에 배포하고,
Slack 게이트웨이를 상시 운영 서비스로 구성한다.

---

## 1. EC2 인스턴스 생성

- **리전**: ap-northeast-2 (Seoul)
- **인스턴스 타입**: t3.small (비용 절감)
- **AMI**: Ubuntu 22.04 LTS (`ami-0596f7562954deb8e`)
- **가용 영역**: ap-northeast-2c
- **퍼블릭 IP**: 13.124.189.200
- **Security Group**: `hermes-matzip-sg` (sg-09de3761a0321b1c7) — SSH 22번 오픈
- **키 파일**: `C:\Users\seung\.ssh\hyo-personal.pem`
- **AWS CLI 프로파일**: `precipi` (기본 `web-helper` 프로파일에는 EC2 권한 없음)

---

## 2. 배포 스크립트 작성

### `deploy/push-to-vm.ps1`
로컬 Windows Hermes 설정에서 시크릿을 읽어 VM으로 SCP 전송하는 스크립트.

- 로컬 `~/.hermes/.env`에서 API 키·Slack 토큰 읽기
- 로컬 `~/.hermes/config.yaml`에서 Google Maps 키·Slack 채널 읽기
- VM용 `config.yaml` (Linux 경로 `/home/ubuntu/matzip/...`) 생성 후 SCP
- VM `~/.hermes/.env`, `SOUL.md`도 SCP

### `deploy/setup-vm.sh`
VM 최초 설정 자동화 스크립트 (Ubuntu 22.04 기준).

1. 시스템 패키지 설치 (docker, python3.11, ripgrep 등)
2. NousResearch/hermes-agent 클론
3. Hermes CLI 설치
4. MCP Python venv 구성 (`mcp/.venv`)
5. `~/.hermes/config.yaml` 작성 (placeholder 시크릿 포함)
6. `SOUL.md` 복사
7. PostgreSQL (Docker) 시작 및 대기
8. CSV 데이터 임포트
9. systemd lingering 활성화

---

## 3. 배포 중 발생한 문제들

### 3-1. `.pem` 파일 권한 오류
```
UNPROTECTED PRIVATE KEY FILE: Permissions 0644 for key.pem are too open.
```
**원인**: Windows에서 CodexSandboxUsers 그룹이 `.pem` 파일에 접근 가능.  
**해결**:
```powershell
icacls hyo-personal.pem /inheritance:r
icacls hyo-personal.pem /grant:r "seung:R"
```

### 3-2. SSH Agent Forwarding 실패
**원인**: `git@github.com` private repo 클론 시 에이전트 포워딩 불가.  
**해결**: `robocopy` + `scp`로 로컬 matzip 프로젝트 폴더를 VM에 직접 전송.

### 3-3. CSV Import 실패 — DB 준비 미완료
**오류**: `psycopg2.OperationalError: server closed the connection unexpectedly`  
**원인**: `pg_isready`는 PostgreSQL 프로세스 기동 직후 성공을 반환하지만,
PostGIS `init.sql` 스키마 적용이 완료되기 전에 import가 시작됨.  
**해결**: `setup-vm.sh`의 DB 대기 조건을 변경.
```bash
# Before (잘못됨)
docker exec hermes_db pg_isready -U hermes

# After (올바름)
docker exec hermes_db psql -U hermes -d hermes -c "SELECT 1"
```

### 3-4. CSV Import 실패 — 경로 문제
**오류**: `[WARN] data/ 에서 CSV 파일을 찾을 수 없습니다.`  
**원인**: `import_csv.py`가 상대 경로 `data/`를 사용하는데, 작업 디렉토리가 다름.  
**해결**: `setup-vm.sh`에서 절대 경로로 실행.
```bash
"$MATZIP_DIR/mcp/.venv/bin/python" "$MATZIP_DIR/scripts/import_csv.py"
```

---

## 4. "No home channel" Slack 알림 반복 버그

### 증상
Slack에서 봇을 멘션할 때마다 다음 메시지가 반복 전송됨:
```
📬 No home channel is set for Slack. A home channel is where Hermes delivers
cron job results and cross-platform messages.
Type /hermes sethome to make this chat your home channel, or ignore to skip.
```

### 디버깅 과정

**1단계**: `run.py:8441` 분석  
조건: `if not history and ... and not os.getenv('SLACK_HOME_CHANNEL'):`  
→ 환경변수 `SLACK_HOME_CHANNEL`이 없으면 매 새 대화마다 발송.

**2단계**: `SLACK_HOME_CHANNEL` 설정  
`save_env_value('SLACK_HOME_CHANNEL', 'C0B5SB0U6KB')`로 VM `~/.hermes/.env`에 추가.

**3단계**: 문제 지속 — 간헐적 발생  
VM `.env`에 값이 있는데도 알림이 간헐적으로 나타남.

**4단계**: systemd 서비스 파일 분석  
게이트웨이 서비스 파일(`~/.config/systemd/user/hermes-gateway.service`)에
`EnvironmentFile=` 지시자가 없음 → systemd가 `.env`를 읽지 않음.  
Python의 `load_dotenv(override=True)`에만 의존하는 구조.

게다가 Hermes는 **종료 시 서비스 파일을 자동으로 덮어씀**
("↻ Updated gateway user service definition" 메시지). 수동으로 `Environment=` 추가해도 다음 재시작 시 사라짐.

**5단계**: systemd drop-in 파일 적용 (1차 해결책)  
```
~/.config/systemd/user/hermes-gateway.service.d/env-override.conf
```
```ini
[Service]
EnvironmentFile=/home/ubuntu/.hermes/.env
```
서비스 파일과 별개의 drop-in 디렉토리는 Hermes가 덮어쓰지 않음.  
→ `/proc/<PID>/environ`에서 `SLACK_HOME_CHANNEL=C0B5SB0U6KB` 확인.

**6단계**: 여전히 2번째 메시지부터 알림 발생  
EC2 게이트웨이 로그에 디버그 추가:
```
DEBUG3 env_key='SLACK_HOME_CHANNEL' val='C0B5SB0U6KB' keys=['SLACK_HOME_CHANNEL', ...]
```
→ EC2 게이트웨이는 값이 정확히 설정되어 있음. 알림이 이 코드 경로에서 오는 게 아님.

**7단계**: 로컬 게이트웨이 발견 (진짜 원인)

`gateway_state.json` 및 `Get-Process`로 확인:
- PID 22284 (`pythonw`) — 로컬 Windows 게이트웨이가 **여전히 실행 중**이고 Slack에 연결되어 있음
- 로컬 `~/.hermes/.env`에는 `SLACK_HOME_CHANNEL` 미설정

**원인**: Slack Socket Mode는 동일한 App-Level Token으로 복수 WebSocket 연결을 허용하며,
이벤트를 라운드로빈으로 분산. 일부 이벤트는 EC2 게이트웨이(→ 정상)가,
일부는 로컬 게이트웨이(→ 알림 발송)가 처리.

### 최종 해결

1. **로컬 게이트웨이 종료**: `hermes gateway stop`
2. **로컬 `.env`에 추가** (재발 방지):
   ```
   SLACK_HOME_CHANNEL=C0B5SB0U6KB
   SLACK_HOME_CHANNEL_NAME=matzip-hermes
   ```
3. **`push-to-vm.ps1` 업데이트**: `SLACK_HOME_CHANNEL`, `SLACK_HOME_CHANNEL_NAME`을 VM `.env`에도 포함
4. **EC2 drop-in 유지**: 벨트-멜빵 이중 보호

**3회 연속 테스트 → 알림 없음 ✓**

---

## 5. Google Maps Geocoding 실패 → 해결

### 초기 가설 (틀림)
EC2 IP(`13.124.189.200`)가 API 키 IP 허용 목록에 없어서 `REQUEST_DENIED` 발생이라고 추정.
→ Google Cloud Console 확인 결과 API 키에 IP 제한 없음(None). 가설 기각.

### 실제 원인
VM `~/.hermes/config.yaml`의 MCP 서버 env 섹션이 플레이스홀더 그대로였음:
```yaml
mcp_servers:
  matzip:
    env:
      GOOGLE_MAPS_API_KEY: "__REPLACE_GOOGLE_MAPS_API_KEY__"  # ← 실제 키 아님
```

**발생 경위**: `push-to-vm.ps1`로 실제 키를 담은 config.yaml을 먼저 올렸으나,
이후 `setup-vm.sh` 실행 시 스크립트 내부의 heredoc이 config.yaml을 플레이스홀더로 덮어씀.
재배포 이후 `push-to-vm.ps1`을 다시 실행하지 않아 잘못된 상태 지속.

### 해결
```powershell
# 로컬에서 실행 — 실제 키 포함 config.yaml 재전송
.\deploy\push-to-vm.ps1 -IP 13.124.189.200 -KeyFile C:\Users\seung\.ssh\hyo-personal.pem
```
EC2에서 직접 API 호출 테스트로 정상 응답(lat/lng 반환) 확인.  
게이트웨이 재시작으로 MCP 서버가 새 config.yaml 적용.

**`geocode_area("Gangnam-gu Seoul")` → `lat: 37.53, lng: 127.12` ✓**

---

## 6. 최종 인프라 구성

```
Slack (Socket Mode)
    ↓ (단일 연결)
EC2 t3.small (13.124.189.200, ap-northeast-2c)
    └── hermes-gateway.service (systemd user service, lingering)
            ├── EnvironmentFile: ~/.hermes/.env (drop-in 적용)
            └── MCP 서버: ~/matzip/mcp/matzip_mcp.py
                    └── PostgreSQL (Docker) ← data/*.csv
```

**EC2 운영 명령어**:
```bash
ssh -i ~/.ssh/hyo-personal.pem ubuntu@13.124.189.200

# 서비스 상태 확인
systemctl --user status hermes-gateway

# 게이트웨이 로그
journalctl --user -u hermes-gateway -f

# MCP 서버 로그
journalctl --user -u hermes-gateway | grep mcp
```

---

## 주의사항

- EC2 게이트웨이 실행 중 **로컬에서 `hermes gateway start` 금지** — 동시 연결 시 이벤트 분산으로 split-brain 발생
- EC2 재배포 시 순서 중요: `setup-vm.sh` 실행 **후** 반드시 `push-to-vm.ps1` 재실행
  - `setup-vm.sh` 내부 heredoc이 config.yaml을 플레이스홀더로 덮어쓰기 때문
- `SLACK_HOME_CHANNEL` 변경 시 EC2 `~/.hermes/.env` 와 로컬 `~/.hermes/.env` 모두 업데이트
