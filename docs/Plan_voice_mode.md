# 음성 입출력 모드 설계 (데스크톱 MVP → 안드로이드 후속)

## 목표

Slack은 **그대로 둔 채**, 데스크톱 CLI(`hermes chat`)에 **음성 입출력**을 추가한다.
마이크로 말 걸면 STT로 받아 Hermes가 처리(matzip·vault MCP 그대로 사용)하고, 응답을 TTS로 읽어준다.

- **이번 범위(MVP)**: 데스크톱 터미널 음성. `마이크 → STT → Hermes → TTS → 스피커` 루프 검증.
- **후속(별도 단계)**: 안드로이드에서 쓰는 음성 프런트(PWA). 코어가 같으므로 MVP 검증 후 얹는다.
- **핵심 전제**: Hermes 0.14.0에 음성 스택이 **이미 내장**돼 있다. 새 게이트웨이를 만들지 않는다.

---

## 비목표 (Non-goals)

명시적으로 이번에 **하지 않는 것**:

- ❌ **메신저 추가 안 함** — Telegram/WhatsApp/Signal 등. (안 쓰는 메신저를 늘리지 않는다는 사용자 방침)
- ❌ **안드로이드 네이티브 앱 / Termux** — 이번 범위 아님. Termux는 프로토타입 느낌이라 제외.
- ❌ **웹 음성 클라이언트** — Hermes의 `web/`은 **설정·세션 모니터링 대시보드**일 뿐, 마이크 캡처 없음(소스 확인됨). 음성 프런트가 아니다.
- ❌ **Slack 제거** — CLI 챗과 Slack 게이트웨이는 독립 실행 모드. 음성은 Slack을 건드리지 않고 추가됨.

---

## 아키텍처 (현행 재사용)

```
마이크 (sounddevice)
   ↓ STT  (faster-whisper, local, 무료)
Hermes Agent  ──MCP──  matzip / vault  (그대로)
   ↓ TTS  (edge ko-KR, 무료  또는  openai)
스피커
```

- **진입점**: `hermes chat` (대화형 CLI). `/voice [on|off|tts|status]` 슬래시 커맨드로 토글.
  - Slack 게이트웨이(`hermes gateway start`)와 **독립** — 같은 config·MCP·볼트를 공유하되 별도 프로세스.
- **음성 코드**: `tools/voice_mode.py`(녹음/재생), `tools/transcription_tools.py`(STT), `tools/tts_tool.py`(TTS).
- **한국어 고려**:
  - STT 모델 `base` → **`small` 권장**(한국어 정확도 향상). 더 필요하면 `medium`.
  - TTS edge 한국어 보이스: `ko-KR-SunHiNeural`(여) / `ko-KR-InJoonNeural`(남).

---

## 현재 상태 (검증됨)

| 항목 | 상태 |
|------|------|
| 음성 의존성 `sounddevice` / `numpy` / `faster-whisper` | **미설치** — `[voice]` extra로 설치 필요 |
| `[voice]` extra 내용 | `faster-whisper==1.2.1`, `sounddevice==0.5.5`, `numpy==2.4.3` (pyproject.toml) |
| config 위치 | `C:\Users\seung\AppData\Local\hermes\config.yaml` (`HERMES_HOME`). `~/.hermes` 아님 — Windows |
| config `stt:` 블록 | 이미 존재 (provider: local 기본) |
| config `tts:` / `voice:` 블록 | 추가/확인 필요 |
| Hermes 버전 | 0.14.0 (venv: `…\AppData\Local\hermes\hermes-agent\venv`, editable install) |
| 에이전트 모델 | gpt-4o-mini (현행 유지) |

---

## 구현 단계

### 1) 음성 의존성 설치

Hermes venv에 `[voice]` extra 설치(셋 다 wheel-only 전이 의존성 포함).

```powershell
$py = "C:\Users\seung\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
& $py -m pip install faster-whisper==1.2.1 sounddevice==0.5.5 numpy==2.4.3
```

- Windows는 `sounddevice`가 PortAudio를 번들하므로 별도 시스템 설치 불필요(설치 후 import 확인).
- 마이크/스피커 장치 인식 확인: `python -c "import sounddevice as sd; print(sd.query_devices())"`.

### 2) config.yaml 음성 블록 설정 (한국어)

`C:\Users\seung\AppData\Local\hermes\config.yaml`에 아래를 확인/추가.

```yaml
stt:
  enabled: true
  provider: local          # 무료, 오프라인
  local:
    model: small           # base → small (한국어 정확도)
    language: ko           # 자동감지 대신 한국어 고정

tts:
  provider: edge           # 무료. 한국어 보이스 양호
  edge:
    voice: ko-KR-SunHiNeural   # 또는 ko-KR-InJoonNeural

voice:
  record_key: ctrl+b       # 푸시투토크 키
  max_recording_seconds: 120
  auto_tts: true           # 에이전트 응답 자동 음성 출력 (MVP에선 켜서 체감)
  beep_enabled: true
  silence_threshold: 200
  silence_duration: 3.0    # 무음 3초 → 자동 종료
```

- 백업: 변경 전 `config.yaml.bak.<날짜>` 자동 생성 패턴 이미 존재(이전 백업 확인됨). 수동 백업도 권장.

### 3) (선택) TTS 키 — edge면 불필요

- **edge TTS**: API 키 불필요(무료). MVP 기본값.
- OpenAI TTS로 바꿀 경우만 `HERMES_HOME\.env`에 `VOICE_TOOLS_OPENAI_KEY` 설정.

### 4) 음성 루프 1차 검증

```powershell
hermes chat
# 대화창에서:
/voice status      # 장치·STT·TTS 인식 상태
/voice on          # 음성 모드 on → ctrl+b로 말하고 떼기
```

- 한국어로 짧게 질의 → STT 텍스트 정확도 확인 → 응답 음성 출력 확인.

### 5) MCP 통합 검증 (음성으로 실제 기능)

음성으로 아래가 동작하는지 확인(텍스트와 동일 코어이므로 도구는 그대로):

- "근처 맛집 알려줘" → `matzip` MCP(`find_nearby`/`get_current_location`).
- "볼트에서 OO 찾아줘" → `vault` MCP(`search_notes`).
- "이거 메모해줘: …" → `vault` MCP(`capture_note`, git commit+push).

### 6) 튜닝

- STT 모델 크기(`base`/`small`/`medium`) ↔ 지연·정확도 트레이드오프.
- `silence_threshold`/`silence_duration` 환경 소음에 맞게.
- `auto_tts` 끄고 `/voice tts`로 필요할 때만 읽기 등 사용 패턴 조정.
- 응답 지연 큰 경우 모델/프롬프트 검토(음성은 텍스트보다 지연 체감 큼).

---

## 체크리스트 (MVP 완료 기준)

- [ ] `[voice]` 의존성 설치 + `sounddevice` 장치 인식 확인
- [ ] config `stt`/`tts`/`voice` 블록 한국어로 설정
- [ ] `/voice status` 정상, `/voice on`으로 한국어 STT 동작
- [ ] 응답 TTS(edge ko-KR) 출력 확인
- [ ] 음성으로 matzip / vault MCP 3종 동작 확인
- [ ] Slack 게이트웨이 영향 없음 확인(병행 실행 시)
- [ ] 지연·정확도 허용 범위 확인 후 STT 모델/무음 파라미터 확정

---

## 미정 결정사항

1. **STT 모델 크기**: `small`(권장) vs `medium`(정확도↑·지연↑). 실측 후 확정.
2. **TTS 프로바이더**: `edge`(무료, MVP 기본) vs `openai`(품질↑·유료·키 필요). edge로 시작 권장.
3. **녹음 트리거**: 푸시투토크(`record_key`) vs 무음 자동감지(`silence_*`). 둘 다 설정돼 있으니 사용해보고 선호 확정.
4. **`auto_tts`**: 항상 읽기 vs 필요할 때만. MVP는 on으로 체감 후 결정.

---

## 후속 단계 — 안드로이드 음성 (별도 프로젝트)

MVP 검증 후 진행. **메신저 추가 없이** 폰에서 쓰는 길은 커스텀 프런트뿐(턴키 없음).

- **방식**: 마이크 잡는 **PWA**(브라우저 `getUserMedia`/`MediaRecorder`) → Hermes 백엔드로 오디오 전송 → STT/TTS.
  - 백엔드 후보: `tui_gateway`의 websocket 서버(이미 `input_audio`/`audio` 처리) **또는** `gateway/platforms/api_server.py`.
  - 폰 홈화면에 PWA로 추가 → "앱처럼" 사용.
- **트레이드오프**: Telegram을 뺀 대가로 이 프런트를 직접 제작. 코어(에이전트+MCP+STT/TTS)는 MVP와 동일하게 재사용.
- **전제**: 데스크톱 MVP에서 음성 루프가 안정적이어야 함. MVP에서 안 풀리는 문제는 폰에서도 안 풀림.
- 별도 설계 문서로 분리 예정(백엔드 선택·인증·PWA 구조).
