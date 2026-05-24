# Worklog — 2026-05-24

## 목표

맛집 알림 봇을 **옵시디언 볼트를 검색·기록하는 범용 개인비서**로 확장한다.
코드 레포를 `matzip` → `hbst-agent`로 개명하고, 볼트 연동(읽기/쓰기) MCP 서버를 추가한 뒤 VM에 배포한다.

설계 문서: `docs/Plan_add_obsidian.md`

---

## 0. 사전 결정사항

| 항목 | 결정 | 이유 |
|------|------|------|
| MCP 서버 구성 | **별도 `vault` 서버 신규** | matzip과 도메인 분리 |
| git 쓰기 방식 | **동기** (도구 안에서 commit+push) | 단일 사용자, 단순·즉시 반영 |
| 볼트 레포 | 이미 `hbst-obsidian` private 레포로 push 완료 | — |
| 프라이버시 제외 | `.hermesignore`에 `Private/` 폴더 규칙 신설 | 민감 노트는 Private/에 모음 |
| 로컬 git 동기화 | `VAULT_GIT_SYNC=0` (push는 옵시디언 Git 플러그인) | 로컬은 실시간 편집 중이라 |
| VM git 동기화 | `VAULT_GIT_SYNC=1` (Hermes가 직접 push) | 옵시디언 앱 없음 |

---

## 1. vault MCP 서버 구현 (`mcp/vault_mcp.py`)

기존 `matzip_mcp.py` 스타일에 맞춘 신규 stdio MCP 서버. 도구 2개.

### `search_notes(query, folder?, limit?)`
- 볼트 노트를 키워드로 검색. **순수 Python grep** (ripgrep 의존 없이 Windows/VM 공통 동작).
- `.hermesignore` 폴더 + 점(.)으로 시작하는 최상위 경로(`.git`/`.obsidian`) 검색 제외.
- 결과에 출처 `path` 항상 포함, 제목(첫 `# 헤딩` 또는 파일명)·스니펫 반환.

### `capture_note(title, content, tags?, source?, source_text?)`
- `hermes_inbox/`에 **새 파일만** 생성 (경로 하드코딩, append-only). 기존 노트는 절대 수정 안 함.
- frontmatter(`status: unprocessed`, `type: capture`, tags) + 원문 blockquote 보존.
- 파일명 `YYYY-MM-DD-HHMMSS-슬러그.md` (한글 슬러그 유지).
- 동기 git 사이클: `add <그 파일만> → commit → pull --rebase --autostash → push` (reject 시 1회 재시도).
  - `--autostash`: 사용자가 옵시디언에서 편집 중인 미저장 변경이 있어도 안전하게 rebase.
  - `VAULT_GIT_SYNC=0`이면 파일만 쓰고 git 생략.

### 테스트 (`tests/test_vault.py`)
- 26개: slugify / `.hermesignore` 매칭 / search(폴더 한정·limit·대소문자·md only) / capture(frontmatter·태그·원문·기존노트 불변).
- `conftest.py`에 `VAULT_DIR` 기본값 추가(import 시점 요구), 테스트는 `tmp_path`로 볼트 교체.
- `pytest` 전체 **40 passed, 13 skipped**(DB 미실행).
- git 사이클은 임시 bare 레포로 스모크 테스트 → `committed/pushed: true` 확인.

---

## 2. 설정 / 배포 스크립트

- `deploy/config.template.yaml`·`mcp/hermes_config_template.yaml`에 `vault` 서버 등록.
- `deploy/push-to-vm.ps1`·`setup-vm.sh`: `__APP_DIR__`/`__VAULT_DIR__` 렌더링 추가.
- `setup-vm.sh`에 **볼트 clone + `hermes-bot` git identity** 단계 추가 (deploy key host alias `github-vault` 사용).
- `.env.example`에 `VAULT_DIR`/`VAULT_GIT_SYNC` 추가.
- `MATZIP_DIR` → `APP_DIR`, 모든 경로 `matzip` → `hbst-agent` 치환.

---

## 3. 네이밍 재정의

- `SOUL.md`: "맛집 알리미" → **개인비서**(볼트 search/capture + 맛집 도구). 행동 원칙에 "기록은 capture_note, 출처(path) 함께 밝히기" 추가.
- `CLAUDE.md`: 제목·아키텍처 다이어그램·주요 파일·환경변수 표 갱신.

---

## 4. 볼트 스캐폴딩 (`hbst-obsidian` 레포)

별도 레포라 로컬 볼트(`C:\My Gdrive Vault`)에서 직접 생성·push.

- `.hermesignore` — `Private/` 제외 규칙 + 안내 주석.
- `hermes_inbox/.gitkeep`, `Private/.gitkeep`.
- → `hbst-obsidian` main에 push (`45b0953`).

---

## 5. PR & 머지

- 브랜치 `feature/add_obsidian` (← `chore/refactor`에서 개명), 12파일 +776/-50.
- **PR #5** (base `main`) 생성 → 머지.
- ⚠️ 커밋 메시지/PR 본문에 PowerShell here-string(`@'...'@`)을 Bash에서 써서 앞에 `@`가 붙는 실수 → `git commit --amend`, `gh pr edit --body-file -`로 수정.

---

## 6. 머지 후 배포 실패 → VM 재부트스트랩

### 증상
PR #5 머지 트리거된 deploy Action 실패:
```
bash: line 1: /home/ubuntu/hbst-agent/mcp/.venv/bin/pip: No such file or directory
##[error]Process completed with exit code 127.
```

### 원인
**VM이 개명 후 재부트스트랩되지 않았다.** VM 상태 점검 결과:
- `~/matzip` (구버전, venv 있음) — **gateway가 여전히 이걸로 active 동작 중**.
- `~/hbst-agent` (deploy 워크플로 rsync 타깃, 코드만, **venv 없음, git 레포 아님**).
- `~/.hermes/config.yaml`이 아직 옛 matzip 경로를 가리킴, vault 서버 없음.

→ deploy 워크플로는 경량(rsync→pip→restart)이라 venv를 안 만듦. `mcp/.venv`는 rsync 제외 대상이라 새 경로에 부재.

### 해결 — 타깃 수술 (무거운 setup-vm.sh 전체 재실행 회피, 서비스 무중단)

**Step 1 — CI 복구 + 봇 새 경로 이전**
```bash
cd ~/hbst-agent/mcp && python3.11 -m venv .venv
.venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt
sed -i "s#/home/ubuntu/matzip/#/home/ubuntu/hbst-agent/#g" ~/.hermes/config.yaml
systemctl --user restart hermes-gateway   # → active
```

**Step 2 — vault deploy key (write 권한)**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/hbst_obsidian -N "" -C "hermes-bot"
# ~/.ssh/config 에 Host github-vault alias 추가 (IdentityFile ~/.ssh/hbst_obsidian)
```
→ 공개키를 GitHub `hbst-obsidian` > Settings > Deploy keys 에 등록 (**Allow write access 체크**, 사용자 수동).

**Step 3 — 볼트 clone + vault 서버 연결**
```bash
git clone git@github-vault:shchun/hbst-obsidian.git ~/hbst-obsidian
git -C ~/hbst-obsidian config user.name hermes-bot
git -C ~/hbst-obsidian config user.email hermes-bot@users.noreply.github.com
# ~/.hermes/config.yaml 의 mcp_servers 에 vault 블록 삽입 (VAULT_DIR, VAULT_GIT_SYNC=1)
systemctl --user restart hermes-gateway
```

### 검증
- `capture_note` 엔드투엔드 → `committed/pushed: true`, 리모트 커밋 확인 → **deploy key write 동작 검증**.
- 실패했던 Action **Re-run → 전 단계 통과** ✓.

---

## 7. 정리

- 검증용 테스트 노트 삭제(commit+push, `cc84982`).
- 구버전 `~/matzip` 제거 (`rm -rf`). gateway 여전히 active.
- 로컬 브랜치 정리(`main` 체크아웃 + 머지 브랜치 삭제) — 사용자 직접 완료.
- 옵시디언 Git 플러그인 auto-pull 주기 설정 — 사용자 직접 완료.

---

## 8. 최종 구성

```
[로컬]  옵시디언(C:\My Gdrive Vault)
          ├─ Git 플러그인: commit+push + auto-pull
          └─ 로컬 Hermes vault 서버 (VAULT_GIT_SYNC=0, 파일만 쓰기)
                              │ git
[GitHub] hbst-obsidian (private)  ◀── hermes_inbox/ 만 write (append-only)
                              │ git (deploy key, alias github-vault)
[EC2]   ~/hbst-obsidian  ◀── vault 서버 capture_note push (VAULT_GIT_SYNC=1)
        ~/hbst-agent     ── matzip + vault MCP 서버 (rsync 타깃, .venv 직접 생성)
        hermes-gateway.service (systemd user)
```

---

## 주의사항 (이번에 배운 것)

- **개명/경로 변경 시**: deploy 워크플로는 venv를 안 만든다. VM에서 `python3.11 -m venv`로 새 경로에 venv 생성 + `config.yaml` 경로 갱신해야 CI "Update MCP Python dependencies"가 통과.
- `~/hbst-agent`는 git clone이 아니라 **rsync 타깃**(`.git` 없음) — `git pull` 안 됨.
- 볼트(`~/hbst-obsidian`)는 **별도 clone**, deploy key + ssh alias `github-vault`로 write.
- 충돌 0의 핵심: 로컬·VM 양쪽 다 `hermes_inbox/`(append-only)에만 쓴다.
- Bash 툴에서 커밋/PR 메시지 작성 시 PowerShell here-string(`@'...'@`) 쓰지 말 것 → heredoc(`<<'EOF'`) 사용.
- Node.js 20 deprecation 경고(`actions/checkout@v4`) — 2026-06-02 강제 전환 전 최신 버전으로 올릴 것(긴급 아님).
