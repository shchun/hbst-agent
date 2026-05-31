# 웹 프런트(커스텀 PWA) 설계 — 음성 비서 공개 웹앱

데스크톱 CLI 음성 MVP(`docs/Plan_voice_mode.md`)에 이은 다음 단계.
"개념 확인 수준"의 CLI를 넘어, **폰·데스크톱에서 쓰는 커스텀 음성 PWA**를 VM에 올린다.

## 목표

- 마이크로 말하고 음성/텍스트로 답받는 **커스텀 웹앱(PWA)**. 폰 홈화면에 추가해 "앱처럼" 사용.
- 두뇌는 기존 Hermes 그대로(에이전트 + matzip/vault MCP + 메모리). 웹은 "얼굴"만 새로.
- 이미 있는 **VM(EC2)** 에 호스팅. Slack 게이트웨이와 같은 두뇌·세션 공유.

## 비목표

- ❌ Open WebUI 등 기성 프런트 도입(통제권 위해 직접 제작 선택).
- ❌ 메신저 추가.
- ❌ 멀티유저/팀(단일 사용자 전제 — 인증·보안 단순화).

## 확정 결정 (논의 결과)

| 항목 | 결정 |
|------|------|
| 프런트 | **커스텀 PWA** (직접 제작) |
| 노출 | **인터넷 공개 + 인증** |
| 음성 처리 | **VM 글루 백엔드가 대리** — OpenAI Whisper(STT) + TTS, 키는 서버에만 |
| HTTPS | **chat.precipi.com** (A) → EC2 Elastic IP. **Caddy가 Let's Encrypt로 TLS**(ACM/ALB 안 씀, $0) |

---

## 아키텍처

```
[폰/데스크톱 PWA]
  마이크 캡처(getUserMedia, 푸시투토크)
  전사·응답 표시 / TTS 재생
        │ HTTPS  chat.precipi.com
        ▼
  Route53  chat.precipi.com (A) ──► EC2 Elastic IP
        ▼
  ┌─ VM (EC2, Ubuntu) ────────────────────────────────┐
  │ ┌─ Caddy (엣지) ────────────────────────────────┐ │
  │ │  - Let's Encrypt 자동 TLS (chat.precipi.com)   │ │
  │ │  - 인증(basic_auth)                            │ │
  │ │  - 정적 PWA 호스팅 + /api 리버스 프록시        │ │
  │ └──────────────┬─────────────────────────────────┘ │
  │                ▼                                    │
  │ ┌─ 글루 백엔드 (FastAPI) ── 새로 짜는 본체 ──────┐ │
  │ │  POST /api/stt   오디오 → 텍스트 (OpenAI STT)  │ │
  │ │  POST /api/chat  텍스트 → Hermes api_server 중계│ │
  │ │                  (SSE 스트리밍 응답)           │ │
  │ │  POST /api/tts   텍스트 → 오디오 (OpenAI TTS)  │ │
  │ │  세션 관리(X-Hermes-Session-* 헤더 유지)       │ │
  │ └──────────────┬─────────────────────────────────┘ │
  │                ▼ (localhost only, 비공개)           │
  │ [Hermes api_server]  :8642  OpenAI 호환             │
  │                ▼                                    │
  │ 에이전트 + matzip/vault MCP + 메모리               │
  │                │                                    │
  │ [Postgres/PostGIS (docker)]  ← 기존                │
  └────────────────────────────────────────────────────┘
```

**설계 원칙**
- TLS는 **Caddy가 Let's Encrypt로** 자동 발급·갱신. 브라우저 마이크용 HTTPS 충족. ALB/ACM 불필요($0).
- 외부 공개는 EC2의 **80/443만**(80은 LE 검증·HTTPS 리다이렉트).
- Hermes api_server·Postgres는 **localhost/내부에만 바인딩**(공개 안 함) → 공격면 최소화.
- OpenAI 키·`API_SERVER_KEY`는 **글루 백엔드/서버에만**. 브라우저엔 절대 노출 안 함.
- api_server는 **텍스트 채팅 전용**(오디오 엔드포인트 없음) → 음성은 글루가 책임.

---

## 컴포넌트 상세

### 1) Hermes api_server 활성화 (코드 0, 설정만)

- `api_server` 플랫폼을 게이트웨이에 켜기: `API_SERVER_KEY` 발급 + config에 플랫폼 enable.
- 바인딩 `127.0.0.1:8642` (외부 비노출). 제공: `/v1/chat/completions`, `/v1/responses`, `/api/sessions/*`, SSE 런 이벤트.
- Slack 게이트웨이와 **동일 프로세스/두뇌**에서 함께 동작.

### 2) 글루 백엔드 (FastAPI) — 직접 제작 핵심

| 엔드포인트 | 동작 |
|-----------|------|
| `POST /api/stt` | 업로드 오디오 → OpenAI Whisper(`whisper-1`, ko) → 텍스트. (음성 MVP에서 검증한 2.5초 경로 재사용) |
| `POST /api/chat` | 텍스트 → Hermes api_server `/v1/chat/completions`(stream) → **SSE로 토큰 중계** |
| `POST /api/tts` | 텍스트(문장 단위) → OpenAI TTS → 오디오 스트림 |
| 세션 | 사용자별 `X-Hermes-Session-Id`/`Session-Key` 유지 → 대화 맥락·장기기억 지속 |

- 키 보관·인증 검증·레이트리밋·로깅 담당. 코드량은 작지만 **보안 경계의 핵심**.

### 3) PWA 프런트 (정적 SPA)

- 스택: Vite + (React 또는 경량). PWA `manifest.json` + service worker(설치·오프라인 셸).
- 음성 UX(MVP): **푸시투토크**(마이크 버튼 길게 눌러 말하기) — 폰에서 가장 안정적. 이후 VAD 자동감지 옵션.
- 흐름: 녹음 → `/api/stt` → 전사 표시 → `/api/chat`(SSE 토큰 점진 표시) → 문장 완성 시 `/api/tts` 재생.
- 저지연: 응답 텍스트 스트리밍 + **문장 단위 TTS**(전체 응답 기다리지 않음).

### 4) Caddy — EC2 엣지

- **Let's Encrypt 자동 TLS**(`chat.precipi.com`). 공인 인증서 자동 발급·갱신 → 브라우저 마이크용 HTTPS 충족.
- `basic_auth`(단일 사용자 MVP). 이후 로그인+세션 토큰 승급 가능.
- 정적 PWA 서빙 + `/api/*` → 글루 백엔드 리버스 프록시.
- HTTP-01 검증·리다이렉트 위해 80 개방, 실서비스는 443.

---

## 보안 (공개 노출 전제)

- [ ] Caddy 인증 필수(미인증 차단). 단일 사용자라 강한 비밀번호 1개로 충분(MVP).
- [ ] Hermes api_server·Postgres는 **공개 금지**(localhost/내부망만). EC2 SG 인바운드는 **80/443만** 개방(80은 LE 검증·리다이렉트).
- [ ] OpenAI 키·`API_SERVER_KEY`는 서버 .env에만. 브라우저 번들에 키 절대 금지.
- [ ] 레이트리밋(글루) — 공개 엔드포인트 남용·비용 폭주 방지(STT/LLM 과금).
- [ ] CORS는 `chat.precipi.com`만 허용.

---

## 배포 (VM, 기존 docker compose 확장)

- 기존 `docker-compose.yml`(db)에 **caddy + 글루 백엔드** 서비스 추가.
- Hermes 게이트웨이는 systemd 서비스 그대로(api_server 플랫폼만 추가 enable).
- 정적 PWA는 빌드 산출물을 Caddy가 서빙(또는 글루가 서빙).
- **DNS/TLS**: EC2에 Elastic IP 할당 → Route53 `chat.precipi.com` **A 레코드 → EIP**. Caddy가 Let's Encrypt로 인증서 자동 발급·갱신. **ALB/ACM 불필요**.

---

## 단계별 구현

1. **api_server 켜기 + 로컬 확인** — `curl localhost:8642/v1/chat/completions`로 에이전트(MCP 포함) 응답 확인.
2. **글루 백엔드 골격** — `/api/chat`(SSE 중계)부터. 텍스트 왕복 먼저 동작.
3. **PWA 텍스트 채팅 MVP** — 마이크 없이 텍스트로 SSE 스트리밍 표시 + PWA 설치 가능.
4. **음성 추가** — `/api/stt`(Whisper) + 푸시투토크 + `/api/tts` 문장단위 재생.
5. **Route53(`chat.precipi.com` A→EIP) + Caddy(Let's Encrypt TLS + basic_auth)** — 공개. 폰에서 마이크 동작 확인(HTTPS 보안 컨텍스트).
6. **폴리시** — 세션 사이드바, VAD 옵션, 오프라인 셸, 에러/로딩 UX, 레이트리밋.

> 순서 의도: **공개·음성을 마지막에**. 텍스트 왕복(api_server↔글루↔PWA)이 로컬에서 안정된 뒤 음성·노출을 얹어야 디버깅이 쉬움.

---

## 확정 세부 결정

| 항목 | 결정 | 비고 |
|------|------|------|
| PWA 스택 | **React + Vite** | `vite-plugin-pwa`로 manifest·서비스워커. Hermes web/과 동형 |
| 인증 | **Caddy `basic_auth`** (MVP) | HTTPS 위 단일 비밀번호. 나중에 로그인+토큰으로 승급 가능 |
| TTS | **OpenAI** (유료, 품질↑) | STT와 같은 OpenAI 키 재사용. (CLI 음성 MVP는 edge였음 — surface가 다름) |
| 글루 위치 | **별도 FastAPI 컨테이너** | 독립 배포·경계 명확·의존성 격리 |
| 레포 구성 | **이 레포 monorepo** | `web/`(PWA) + `server/`(글루) 하위 폴더. 문서·배포 한 곳 |

### 레포 레이아웃 (잠정)

```
hbst-agent/
├── mcp/                 # 기존 MCP 서버
├── scripts/ data/       # 기존 DB·데이터
├── server/              # ★ 글루 백엔드 (FastAPI): /api/stt, /api/chat, /api/tts
├── web/                 # ★ PWA (React+Vite, manifest+SW)
├── deploy/              # 기존 + caddy/compose 확장
└── docs/
```

## 후속 (구현 중 정함)

- VAD 자동감지(푸시투토크 다음), 세션 사이드바, 오프라인 셸, 레이트리밋 임계값.
- 로그인+세션 토큰 승급 시점(basic_auth로 충분한지 운영 후 판단).

---

## Step 1 검증 결과 (2026-05-31, 로컬)

`API_SERVER_KEY`를 `.env`에 넣어 게이트웨이에서 api_server 활성화 → 로컬 검증 완료.

- ✅ **두뇌 노출 동작**: `GET /v1/models` → `hermes-agent`. `POST /v1/chat/completions`(단순 질의) **7초, 200 OK**. OpenAI 호환 API로 에이전트 응답 확인.
- ⚠️ **툴 라우팅·지연 문제 발견** (웹 품질의 핵심):
  - 단순 "안녕"도 7초 + 시스템 프롬프트 `in≈14,458토큰` — 28개 플러그인 툴 설명이 다 실림(느림·고비용).
  - 맛집 질의에서 gpt-4o-mini가 `mcp_matzip_find_nearby` 대신 **`search_files`(홈 디렉터리 스캔, 65초×2)** 를 골라 타임아웃.
  - `search_files`는 `_HERMES_CORE_TOOLS`에 포함 → `hermes-slack`/`hermes-api-server` 등 **기본 툴셋 전부에 존재**. 토글만으론 못 뺌.

**→ 설계 반영 (웹 페르소나):**
1. **커스텀 경량 툴셋** — `matzip`/`vault` MCP + 최소만, `search_files`·`terminal`·`process`·파일·브라우저 제외. 프롬프트 축소 → 빠름·저렴·오라우팅↓.
2. **페르소나 강화** — SOUL이 "맛집/노트 질문 → 해당 MCP 우선" 명시.
3. **모델 승급 검토** — 라우팅 신뢰도가 곧 UX.

### 후속 조사 결과 (toolset 디테일, 미해결)

- MCP 도구는 코드상 **`mcp-<서버>` 전용 toolset**으로 등록됨(`tools/mcp_tool.py:3235` `f"mcp-{name}"`) → `mcp-matzip`, `mcp-vault`. `hermes-api-server` 등 일반 툴셋엔 **미포함**.
- `platform_toolsets`로 api_server 툴셋을 바꾸면 **프롬프트 크기는 확실히 통제됨**(전체 14,458토큰 → `[mcp-matzip, mcp-vault]` 지정 시 ~1,006토큰).
- **그러나** 시도한 config 어느 것도 "matzip+vault 도구가 실제로 실리고 호출되는" 상태를 못 만듦:
  - 미지정(기본): 전체 주입(matzip 있음) but search_files 오선택.
  - `[hermes-api-server]`: matzip 제외.
  - `[mcp-matzip, mcp-vault]`: 프롬프트 1k but 도구 거의 안 실림(`tool_turns=0`).
- **결론**: Hermes의 `agent_init.py` toolset↔MCP 주입 로직(`enabled_toolsets` 필터와 MCP 주입의 상호작용)을 정독해 정확한 config를 도출해야 함. 모델 승급(gpt-4o-mini→gpt-4.1-mini)만으론 해결 안 됨(동일 오라우팅). **추측-재시작이 아니라 코드 정독 또는 VM(Slack 동작 환경)에서 검증 권장.**

### 로컬 실험 상태 (정리 필요)

- `config.yaml`: `model.default = gpt-4.1-mini`, `platform_toolsets.api_server = [mcp-matzip, mcp-vault]`(도구 미적재 상태), `.env`에 `API_SERVER_KEY` 추가, 로컬 게이트웨이 실행 중.
- 깔끔히 가려면 api_server platform_toolsets 항목을 정정하거나 제거 필요.

## 음성 MVP와의 관계

- 두뇌·MCP·메모리·STT(Whisper)·TTS 결정은 **그대로 재사용**(`Plan_voice_mode.md` 결과).
- 데스크톱 CLI 음성은 로컬 개발·디버깅용으로 계속 유효(같은 코어).
- 이 문서는 그 위에 "공개 웹 얼굴"을 얹는 별도 워크스트림.
