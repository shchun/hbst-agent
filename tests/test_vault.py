import json
import asyncio
import pytest

import vault_mcp as vm


# ── 픽스처: tmp_path 를 볼트로 사용 (git 동기화 끔) ─────────────────────────────

@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(vm, "VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(vm, "GIT_SYNC", False)
    return tmp_path


def _write(vault, rel, text):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── _slugify ─────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_keeps_korean_and_alnum(self):
        assert vm._slugify("OO 맛집 2024") == "OO-맛집-2024"

    def test_strips_special_chars(self):
        assert vm._slugify("a/b:c*d?") == "a-b-c-d"

    def test_empty_falls_back(self):
        assert vm._slugify("///") == "note"

    def test_length_capped(self):
        assert len(vm._slugify("가" * 100)) == 50


# ── _is_ignored ──────────────────────────────────────────────────────────────

class TestIsIgnored:
    def test_dotfolders_always_ignored(self):
        assert vm._is_ignored(".obsidian/app.json", [])
        assert vm._is_ignored(".git/config", [])

    def test_folder_prefix_match(self):
        pats = ["건강", "Daily"]
        assert vm._is_ignored("건강/혈압.md", pats)
        assert vm._is_ignored("Daily/2026-05-24.md", pats)

    def test_non_matching_path(self):
        assert not vm._is_ignored("레시피/김밥.md", ["건강"])

    def test_exact_folder_name_not_prefix_of_sibling(self):
        # "건강" 패턴이 "건강식단/" 까지 잡으면 안 됨
        assert not vm._is_ignored("건강식단/메뉴.md", ["건강"])

    def test_glob_pattern(self):
        assert vm._is_ignored("notes/secret.private.md", ["*.private.md"])


# ── _obsidian_uri ─────────────────────────────────────────────────────────────

class TestObsidianUri:
    def test_encodes_vault_name_with_space(self, monkeypatch):
        monkeypatch.setattr(vm, "VAULT_NAME", "My Gdrive Vault")
        uri = vm._obsidian_uri("notes/foo.md")
        assert uri == "obsidian://open?vault=My%20Gdrive%20Vault&file=notes%2Ffoo.md"

    def test_roundtrips_korean_path(self, monkeypatch):
        from urllib.parse import urlparse, parse_qs, unquote
        monkeypatch.setattr(vm, "VAULT_NAME", "볼트")
        uri = vm._obsidian_uri("레시피/가지튀김.md")
        q = parse_qs(urlparse(uri).query)
        assert unquote(q["vault"][0]) == "볼트"
        assert unquote(q["file"][0]) == "레시피/가지튀김.md"

    def test_backslashes_normalized(self, monkeypatch):
        monkeypatch.setattr(vm, "VAULT_NAME", "V")
        assert "%5C" not in vm._obsidian_uri("a\\b.md")  # \ → / 후 인코딩


# ── _note_title / _make_snippet ───────────────────────────────────────────────

class TestTitleAndSnippet:
    def test_title_from_heading(self):
        assert vm._note_title("# 진짜 제목\n본문", "foo/bar.md") == "진짜 제목"

    def test_title_falls_back_to_filename(self):
        assert vm._note_title("제목 없는 본문", "foo/김밥.md") == "김밥"

    def test_snippet_centers_on_query(self):
        text = "가" * 100 + "키워드" + "나" * 100
        snip = vm._make_snippet(text, "키워드")
        assert "키워드" in snip
        assert snip.startswith("…") and snip.endswith("…")


# ── _search_notes ─────────────────────────────────────────────────────────────

class TestSearchNotes:
    def test_finds_matching_note(self, vault, monkeypatch):
        monkeypatch.setattr(vm, "VAULT_NAME", "TestVault")
        _write(vault, "레시피/김밥.md", "# 김밥\n참치 김밥 만드는 법")
        results = vm._search_notes("참치", None, 20)
        assert len(results) == 1
        assert results[0]["path"] == "레시피/김밥.md"
        assert results[0]["title"] == "김밥"
        assert results[0]["link"].startswith("obsidian://open?vault=TestVault&file=")

    def test_case_insensitive(self, vault):
        _write(vault, "note.md", "Hello World")
        assert len(vm._search_notes("hello", None, 20)) == 1

    def test_ignores_hermesignore_folders(self, vault):
        _write(vault, ".hermesignore", "건강\n")
        _write(vault, "건강/혈압.md", "혈압 키워드 기록")
        _write(vault, "레시피/김밥.md", "혈압 키워드 비교")
        results = vm._search_notes("키워드", None, 20)
        paths = [r["path"] for r in results]
        assert "건강/혈압.md" not in paths
        assert "레시피/김밥.md" in paths

    def test_skips_dot_dirs(self, vault):
        _write(vault, ".obsidian/app.json", "키워드")
        assert vm._search_notes("키워드", None, 20) == []

    def test_folder_scope(self, vault):
        _write(vault, "a/x.md", "키워드 A")
        _write(vault, "b/y.md", "키워드 B")
        results = vm._search_notes("키워드", "a", 20)
        assert [r["path"] for r in results] == ["a/x.md"]

    def test_respects_limit(self, vault):
        for i in range(5):
            _write(vault, f"n{i}.md", "키워드")
        assert len(vm._search_notes("키워드", None, 3)) == 3

    def test_only_md_files(self, vault):
        _write(vault, "data.txt", "키워드")
        assert vm._search_notes("키워드", None, 20) == []


# ── capture_note (git 동기화 꺼진 상태) ───────────────────────────────────────

class TestCaptureNote:
    def _run(self, **kw):
        return asyncio.run(vm.call_tool("capture_note", kw))

    def test_creates_file_in_inbox(self, vault):
        result = self._run(title="OO 맛집", content="파스타 좋았음")
        data = json.loads(result[0].text)
        assert data["path"].startswith("hermes_inbox/")
        assert (vault / data["path"]).exists()

    def test_file_has_frontmatter(self, vault):
        result = self._run(title="테스트", content="본문")
        data = json.loads(result[0].text)
        text = (vault / data["path"]).read_text(encoding="utf-8")
        assert text.startswith("---")
        assert "status: unprocessed" in text
        assert "type: capture" in text
        assert "# 테스트" in text
        assert "본문" in text

    def test_default_tags(self, vault):
        result = self._run(title="t", content="c")
        text = (vault / json.loads(result[0].text)["path"]).read_text(encoding="utf-8")
        assert "tags: [hermes/inbox]" in text

    def test_custom_tags_and_source(self, vault):
        result = self._run(title="t", content="c", tags=["hermes/inbox", "맛집"], source="slack")
        text = (vault / json.loads(result[0].text)["path"]).read_text(encoding="utf-8")
        assert "tags: [hermes/inbox, 맛집]" in text
        assert "source: slack" in text

    def test_source_text_preserved_as_quote(self, vault):
        result = self._run(title="t", content="c", source="slack", source_text="원본 메시지")
        text = (vault / json.loads(result[0].text)["path"]).read_text(encoding="utf-8")
        assert '> 원문(slack): "원본 메시지"' in text

    def test_never_touches_existing_notes(self, vault):
        existing = _write(vault, "기존노트.md", "사용자 원본")
        self._run(title="새 캡처", content="봇 기록")
        assert existing.read_text(encoding="utf-8") == "사용자 원본"

    def test_filename_has_timestamp_and_slug(self, vault):
        result = self._run(title="맛집 메모", content="c")
        path = json.loads(result[0].text)["path"]
        # hermes_inbox/YYYY-MM-DD-HHMMSS-맛집-메모.md
        assert path.endswith("-맛집-메모.md")
