import os

# agent.py가 import될 때 env var를 미리 설정
os.environ.setdefault("DATABASE_URL", "postgresql://hermes:hermes1234@localhost:5432/hermes")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-token")
os.environ.setdefault("SLACK_APP_TOKEN", "xapp-test-token")
os.environ.setdefault("SLACK_CHANNEL", "#hermes")
