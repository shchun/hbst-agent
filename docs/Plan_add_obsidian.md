# 옵시디언 볼트 연동 설계 (Hermes 범용 개인비서化)

## 목표

`C:\My Gdrive Vault\` 옵시디언 볼트를 matzip 서비스 VM에 올리고 Hermes가 참조·기록하게 하여,
"맛집 알림 봇"을 **개인 지식 베이스를 검색·종합하고 기록하는 범용 개인비서**로 확장한다.

- **주 용도**: 범용 개인비서 (맛집 노트 보강은 그 일부)
- **방향성**: 읽기(검색)뿐 아니라 쓰기(기록)까지 → 볼트가 입력이자 출력이 되는 양방향 비서

---

## 전체 아키텍처

```
옵시디언(로컬) ──git──> GitHub(private) <──git── VM(Hermes, hermes_inbox만 write)
       │
       └──백업──> Google Drive (단방향 백업, git과 무관)
```

- Google Drive는 **단방향 백업**(업로드 전용)이라 git과 충돌하지 않음.
  → 볼트를 드라이브 폴더 밖으로 뺄 필요 없음. 현재 위치에 그대로 `git init`.
- 진짜 양방향 동기화는 **git(로컬 ↔ GitHub ↔ VM) 하나뿐** = 옵시디언 Git 플러그인 표준 경로(검증된 길).
- 드라이브가 `.git`까지 백업 → 무해(이력 통째 백업 보너스). 신경 쓰이면 드라이브 백업에서 `.git` 제외 가능(필수 아님).

---

## 충돌 방지 원칙

**핵심: Hermes는 사용자가 편집하는 파일을 절대 건드리지 않는다.** 충돌은 같은 파일을 둘이 수정할 때만 발생.

- Hermes는 기존 노트 수정·삭제(in-place write) 금지 → 충돌 지옥이므로 회피.
- Hermes는 `hermes_inbox/`에 **새 파일만 생성**(append-only). 파일명에 타임스탬프(초 단위) → 충돌 거의 불가능.
- 사용자(옵시디언)와 Hermes가 만지는 파일 집합이 안 겹침 → git 충돌 실무적으로 0.

---

## 레포 구성

코드와 데이터는 **별도 레포**로 분리한다 (데이터 커밋이 코드 이력을 오염시키지 않도록).

| 레포 | 내용 | 비고 |
|------|------|------|
| 코드 레포 (현 matzip) | 에이전트/MCP 코드 | `hermes`(또는 `hermes-agent`)로 **개명** 제안. matzip은 그 안의 한 모듈/MCP 서버로 강등. ※ 개명 시 remote URL·CI 시크릿·VM clone 경로·배포 워크플로 수정 필요(공짜 아님) |
| 볼트 레포 (신규) | 옵시디언 노트 | **반드시 private** (일기·금융·건강 메모 포함 가능) |

---

## 1) 볼트 레포 폴더 구조

원칙: Hermes 쓰기는 한 폴더에 격리, 프라이버시 제외는 폴더 경계로.

```
vault/                      ← private GitHub 레포
├── .obsidian/              # 옵시디언 설정 (커밋 권장, workspace.json만 ignore)
├── .gitignore              # workspace.json, .trash/, 캐시 제외
├── .hermesignore           # Hermes가 "읽지도" 않을 경로 목록 (검색 제외)
├── hermes_inbox/           # ★ Hermes write 전용 (append-only, 새 파일만)
│   └── 2026-05-24-143205-맛집-oo.md
├── matzip/                 # 맛집 노트 (사용자)
├── notes/                  # 일반 노트
├── projects/
└── journal/                # 일기 → .hermesignore 등록(읽기 제외)
```

- `.hermesignore`: 읽기(검색) 제외를 폴더 단위로 관리. 일기·금융 노트 등록 → gpt-4o-mini로 안 흘러가게 하는 1차 장치.
- `hermes_inbox/`: Hermes만 새 파일 생성. 사용자가 읽고 본 폴더로 옮기거나 링크해서 "흡수" → 같은 파일 동시 수정 없음.

---

## 2) 캡처 노트 포맷

frontmatter로 출처·상태·태그를 기록, 본문 끝에 원문을 인용으로 보존.

```markdown
---
created: 2026-05-24T14:32:05+09:00
source: slack
type: capture
status: unprocessed          # 사용자가 흡수하면 processed로
tags: [hermes/inbox, 맛집]
---

# OO 맛집

오늘 간 OO 맛집 괜찮았어. 파스타가 좋았음.

> 원문(slack): "오늘 간 OO 맛집 괜찮았어"
```

- `status: unprocessed` → 옵시디언 쿼리로 미정리 캡처만 모아보기.
- `tags: [hermes/inbox]` → 봇 생성물 한눈에 필터.
- 원문 blockquote → 노트 생성 근거 추적(출처 보존).
- 파일명 `YYYY-MM-DD-HHMMSS-슬러그.md` → 초 단위, 단일 사용자 환경에서 충돌 거의 불가능.

---

## 3) Hermes 도구 시그니처

기존 `mcp/matzip_mcp.py` 도구 스타일에 맞춘 MCP 도구.

### write 도구 — `capture_note`

경로는 `hermes_inbox/`로 **하드코딩** (앱계층에서 폴더 제한 강제. GitHub는 폴더 단위 쓰기 권한이 없으므로).

```python
types.Tool(
    name="capture_note",
    description="사용자 메모/맛집 기록을 볼트의 hermes_inbox에 새 노트로 저장합니다. 기존 노트는 절대 수정하지 않습니다.",
    inputSchema={
        "type": "object",
        "properties": {
            "title":       {"type": "string", "description": "노트 제목 (파일명 슬러그로도 사용)"},
            "content":     {"type": "string", "description": "본문 (마크다운)"},
            "tags":        {"type": "array", "items": {"type": "string"}, "description": "태그, 기본 [hermes/inbox]"},
            "source":      {"type": "string", "description": "출처: slack/agent 등", "default": "agent"},
            "source_text": {"type": "string", "description": "원문 보존용 (선택)"},
        },
        "required": ["title", "content"],
    },
)
```

내부 동작 (충돌 안전한 git 사이클):

```
1. 파일 경로 = hermes_inbox/{date}-{slug}.md   ← 경로 하드코딩, 밖으론 절대 안 씀
2. frontmatter + 본문 write (새 파일만)
3. git add <그 파일만>
4. git commit -m "hermes({source}): {title}"
5. git pull --rebase  →  git push   (push reject 시 fetch·rebase 후 재시도)
6. 저장된 파일 경로 반환
```

### read 도구 — `search_notes` (범용 비서의 본체, grep 기반으로 시작)

```python
# search_notes(query, folder?, limit?) → [{path, title, snippet}]
#   .hermesignore 경로는 검색 대상에서 제외, 결과에 출처(path) 항상 포함
```

---

## 검색 방식 로드맵

1. **grep으로 시작** — 볼트가 수천 노트가 아니면 충분. 비용 0, 동기화만 되면 항상 최신. 출처(파일 경로) 반환 필수.
2. **pgvector 하이브리드로 확장** — grep이 한계 보일 때. DB가 이미 PostgreSQL이라 `pgvector` 확장만 얹으면 별도 벡터 DB 없이 맛집 데이터 + 노트 임베딩을 한 곳에서 관리(이 프로젝트의 큰 이점). 의미 검색(pgvector)으로 후보 → 제목/태그/최근수정일로 재정렬. 옵시디언의 `[[링크]]`·`#태그`·frontmatter를 메타데이터로 같이 인덱싱하면 품질 상승.
   - pgvector 도입 시 "git pull로 노트 바뀜 → 재임베딩" 훅 필요(지금 설계에 자리만 남겨둠).

---

## 추가로 필요한 것 (체크리스트)

- [ ] **볼트 레포 private 생성** — 절대 public 금지.
- [ ] **단일 동기화 메커니즘** — 드라이브는 단방향 백업이라 OK. git만 양방향.
- [ ] **hermes_inbox 쓰기 강제** — MCP 도구가 경로 하드코딩(1차). 선택적으로 pre-push 훅/CI로 inbox 밖 커밋 거부(2차 방어선).
- [ ] **VM → GitHub 인증** — 봇용 deploy key 또는 fine-grained PAT, **볼트 레포에만** write 스코프. 코드 레포 키와 분리.
- [ ] **충돌 처리** — `pull --rebase → commit → push`, reject 시 재시도. 쓰기 직렬화(단일 프로세스면 대체로 OK).
- [ ] **볼트 `.gitignore`** — `.obsidian/workspace.json`, `.trash/`, 캐시 제외. 노트 안 토큰/비번 주의.
- [ ] **프라이버시 필터** — `.hermesignore`로 일기·민감 폴더 읽기 제외(RAG 가기 전 필수).
- [ ] **지연 기대치** — Hermes 쓰기 → push → 로컬 pull까지 실시간 아님. 옵시디언 Git 플러그인 pull 주기 의존.
- [ ] **커밋 메시지 규약** — `hermes(slack): OO 맛집 캡처` 형태로 출처 남기기.

---

## 미정 결정사항

1. **git 쓰기 방식**: 동기(도구 안에서 commit+push) vs 비동기(파일만 쓰고 별도 루프가 주기 push).
   → 단일 사용자면 **동기 방식** 추천(단순·즉시 반영).
2. **`.obsidian/` 커밋 여부**: 커밋하면 기기 간 설정 통일, 단 플러그인 캐시 노이즈. 보통 `workspace.json`만 ignore.
3. **MCP 서버 분리**: `vault` MCP 서버 신규 생성(matzip과 분리) vs 기존 서버에 두 도구 추가.
   → 도메인 혼동 방지 위해 **분리** 권장.

---

## 다음 단계

위 미정사항(특히 MCP 서버 분리 여부)을 정하면 구현 착수 가능. 구현 순서 제안:

1. 볼트 private 레포 생성 + `git init` + `.gitignore`/`.hermesignore`
2. 옵시디언 Git 플러그인 세팅(로컬) + VM clone + 봇 인증
3. `vault` MCP 서버 + `search_notes`(grep) 도구
4. `capture_note` 도구 + git 쓰기 사이클
5. SOUL.md·네이밍 재정의 (맛집 알리미 → 개인비서)
6. (이후) pgvector 하이브리드 검색
