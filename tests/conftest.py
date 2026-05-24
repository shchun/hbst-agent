import os
import sys
import pytest
import psycopg2

# mcp/ 내부 함수를 직접 import하기 위해 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

# 모듈 import 전에 필수 환경변수 설정
os.environ.setdefault("DATABASE_URL", "postgresql://hermes:hermes1234@localhost:5432/hermes")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-token")
os.environ.setdefault("SLACK_CHANNEL", "#test")
# vault_mcp 는 import 시점에 VAULT_DIR 를 요구 — 테스트에선 monkeypatch로 tmp 볼트로 교체
os.environ.setdefault("VAULT_DIR", os.path.dirname(__file__))
os.environ.setdefault("VAULT_GIT_SYNC", "0")

DB_URL = os.environ["DATABASE_URL"]


def _db_reachable() -> bool:
    try:
        conn = psycopg2.connect(DB_URL, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="DB not running — docker compose up -d db 먼저 실행",
)
