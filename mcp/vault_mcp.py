#!/usr/bin/env python3
"""
Vault MCP Server
Hermes Agent가 옵시디언 볼트를 검색(read)하고 기록(write)하는 Tool 모음.

설계 원칙 (docs/Plan_add_obsidian.md):
- 읽기: .hermesignore 경로는 검색에서 제외 (프라이버시 1차 필터)
- 쓰기: hermes_inbox/ 에 새 파일만 생성 (append-only, 기존 노트 절대 수정 안 함)
- git: 도구 안에서 동기 commit + pull --rebase + push (단일 사용자 전제)
"""

import os
import re
import sys
import json
import fnmatch
import logging
import asyncio
import subprocess
import urllib.parse
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# .env 로드 (로컬 실행 시)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

VAULT_DIR = os.path.abspath(os.environ["VAULT_DIR"])
# obsidian:// 링크의 vault= 값. 옵시디언에 등록된 실제 볼트 이름과 일치해야 함.
# VM은 폴더명(hbst-obsidian)이 사용자 볼트 이름과 다르므로 반드시 명시 설정할 것.
VAULT_NAME = os.environ.get("VAULT_NAME") or os.path.basename(VAULT_DIR)
INBOX_DIRNAME = "hermes_inbox"
# git 동기화 on/off (테스트·오프라인 시 "0"으로 끄기)
GIT_SYNC = os.environ.get("VAULT_GIT_SYNC", "1") != "0"
KST = timezone(timedelta(hours=9))

server = Server("vault")


# ── 내부 유틸 ──────────────────────────────────────────────────────────────────

def _json_response(obj) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(obj, ensure_ascii=False))]


def _load_hermesignore() -> list[str]:
    """볼트 루트의 .hermesignore 를 읽어 패턴 목록 반환 (없으면 빈 목록)."""
    path = os.path.join(VAULT_DIR, ".hermesignore")
    patterns: list[str] = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line.rstrip("/"))
    return patterns


def _is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """rel_path(슬래시 구분, 볼트 루트 기준)가 무시 패턴에 걸리는지."""
    rel = rel_path.replace("\\", "/")
    # 항상 제외: .git, .obsidian 등 점으로 시작하는 최상위 경로
    top = rel.split("/", 1)[0]
    if top.startswith("."):
        return True
    for pat in patterns:
        # 폴더 prefix 매칭 (예: "건강" → "건강/foo.md")
        if rel == pat or rel.startswith(pat + "/"):
            return True
        # glob 매칭 (예: "*.private.md")
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(os.path.basename(rel), pat):
            return True
    return False


def _iter_notes(folder: str | None, patterns: list[str]):
    """검색 대상 .md 파일을 (절대경로, 볼트상대경로)로 순회. ignore 경로는 건너뜀."""
    base = VAULT_DIR if not folder else os.path.join(VAULT_DIR, folder)
    base = os.path.abspath(base)
    # 디렉터리 탈출 방지
    if os.path.commonpath([base, VAULT_DIR]) != VAULT_DIR:
        return
    for root, dirs, files in os.walk(base):
        # 점으로 시작하는 디렉터리는 통째로 스킵 (.git/.obsidian 등)
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if not fname.lower().endswith(".md"):
                continue
            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, VAULT_DIR).replace("\\", "/")
            if _is_ignored(rel_path, patterns):
                continue
            yield abs_path, rel_path


def _note_title(text: str, rel_path: str) -> str:
    """본문 첫 번째 '# 제목' 또는 파일명(확장자 제외)을 제목으로."""
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return os.path.splitext(os.path.basename(rel_path))[0]


def _obsidian_uri(rel_path: str) -> str:
    """볼트 상대경로 → 클릭 시 옵시디언에서 노트가 열리는 obsidian:// 링크."""
    vault = urllib.parse.quote(VAULT_NAME, safe="")
    file = urllib.parse.quote(rel_path.replace("\\", "/"), safe="")
    return f"obsidian://open?vault={vault}&file={file}"


def _make_snippet(text: str, query: str, width: int = 80) -> str:
    """query가 처음 나오는 위치 주변을 잘라 한 줄 스니펫으로."""
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text.strip()[:width]
    start = max(0, idx - width // 2)
    end = min(len(text), idx + len(query) + width // 2)
    snippet = text[start:end].replace("\n", " ").strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def _search_notes(query: str, folder: str | None, limit: int) -> list[dict]:
    patterns = _load_hermesignore()
    results: list[dict] = []
    q = query.lower()
    for abs_path, rel_path in _iter_notes(folder, patterns):
        try:
            with open(abs_path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        if q in text.lower():
            results.append({
                "path": rel_path,
                "title": _note_title(text, rel_path),
                "snippet": _make_snippet(text, query),
                "link": _obsidian_uri(rel_path),
            })
            if len(results) >= limit:
                break
    return results


def _slugify(title: str) -> str:
    """파일명용 슬러그: 한글·영문·숫자 유지, 그 외는 '-'로. 길이 제한."""
    slug = re.sub(r"[^\w가-힣]+", "-", title, flags=re.UNICODE).strip("-")
    return (slug or "note")[:50]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", VAULT_DIR, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def _git_commit_push(rel_path: str, message: str) -> dict:
    """그 파일만 add → commit → pull --rebase → push. reject 시 1회 재시도."""
    status = {"committed": False, "pushed": False, "error": None}
    add = _git("add", "--", rel_path)
    if add.returncode != 0:
        status["error"] = f"git add 실패: {add.stderr.strip()}"
        return status
    commit = _git("commit", "-m", message)
    if commit.returncode != 0:
        status["error"] = f"git commit 실패: {commit.stderr.strip()}"
        return status
    status["committed"] = True

    for attempt in range(2):
        # --autostash: 사용자가 옵시디언에서 편집 중인 미저장 변경이 있어도 안전.
        _git("pull", "--rebase", "--autostash")
        push = _git("push")
        if push.returncode == 0:
            status["pushed"] = True
            return status
        log.warning("git push 실패(시도 %d): %s", attempt + 1, push.stderr.strip())
    status["error"] = "git push 실패 (커밋은 로컬에 보존됨)"
    return status


# ── Tool 정의 ──────────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_notes",
            description=(
                "옵시디언 볼트의 노트를 키워드로 검색합니다. "
                ".hermesignore에 등록된 비공개 폴더는 검색에서 제외됩니다. "
                "결과에는 출처 경로(path)가 항상 포함됩니다."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색 키워드"},
                    "folder": {"type": "string", "description": "검색을 한정할 하위 폴더 (선택)"},
                    "limit": {"type": "integer", "description": "최대 결과 수, 기본 20", "default": 20},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="capture_note",
            description=(
                "사용자 메모/맛집 기록을 볼트의 hermes_inbox에 새 노트로 저장합니다. "
                "기존 노트는 절대 수정하지 않습니다."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "노트 제목 (파일명 슬러그로도 사용)"},
                    "content": {"type": "string", "description": "본문 (마크다운)"},
                    "tags": {"type": "array", "items": {"type": "string"},
                             "description": "태그, 기본 [hermes/inbox]"},
                    "source": {"type": "string", "description": "출처: slack/agent 등", "default": "agent"},
                    "source_text": {"type": "string", "description": "원문 보존용 (선택)"},
                },
                "required": ["title", "content"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "search_notes":
        query = arguments["query"]
        folder = arguments.get("folder")
        limit = arguments.get("limit", 20)
        results = _search_notes(query, folder, limit)
        log.info("search_notes '%s' folder=%s → %d개", query, folder, len(results))
        return _json_response({"count": len(results), "results": results})

    elif name == "capture_note":
        result = _capture_note(
            title=arguments["title"],
            content=arguments["content"],
            tags=arguments.get("tags"),
            source=arguments.get("source", "agent"),
            source_text=arguments.get("source_text"),
        )
        log.info("capture_note '%s' → %s (committed=%s pushed=%s)",
                 arguments["title"], result["path"], result.get("committed"), result.get("pushed"))
        return _json_response(result)

    raise ValueError(f"Unknown tool: {name}")


def _capture_note(title: str, content: str, tags=None, source="agent", source_text=None) -> dict:
    now = datetime.now(KST)
    tags = tags or ["hermes/inbox"]
    fname = f"{now.strftime('%Y-%m-%d-%H%M%S')}-{_slugify(title)}.md"
    rel_path = f"{INBOX_DIRNAME}/{fname}"
    abs_path = os.path.join(VAULT_DIR, INBOX_DIRNAME, fname)

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    fm_tags = "[" + ", ".join(tags) + "]"
    parts = [
        "---",
        f"created: {now.isoformat(timespec='seconds')}",
        f"source: {source}",
        "type: capture",
        "status: unprocessed",
        f"tags: {fm_tags}",
        "---",
        "",
        f"# {title}",
        "",
        content.rstrip(),
    ]
    if source_text:
        parts += ["", f"> 원문({source}): \"{source_text}\""]
    parts.append("")

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    result = {"path": rel_path, "link": _obsidian_uri(rel_path)}
    if GIT_SYNC:
        result.update(_git_commit_push(rel_path, f"hermes({source}): {title}"))
    return result


# ── 진입점 ─────────────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
