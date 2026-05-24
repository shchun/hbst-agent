#!/usr/bin/env bash
# ============================================================
# Hermes Matzip — Ubuntu 22.04 EC2 Setup Script
# Run once on a fresh instance after cloning the hbst-agent repo.
#
#   git clone git@github.com:shchun/hbst-agent.git ~/hbst-agent
#   bash ~/hbst-agent/deploy/setup-vm.sh
# ============================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}✓${NC} $*"; }
step() { echo -e "\n${GREEN}==>${NC} $*"; }

APP_DIR="$HOME/hbst-agent"
HERMES_AGENT_DIR="$HOME/hermes-agent"
VAULT_DIR="$HOME/hbst-obsidian"
# 'github-vault' 는 ~/.ssh/config 의 Host alias (볼트 전용 deploy key 와 연결).
# 이 스크립트 실행 전 아래를 미리 세팅해 둘 것:
#   ssh-keygen -t ed25519 -f ~/.ssh/hbst_obsidian -N "" -C "hermes-bot"
#   cat ~/.ssh/hbst_obsidian.pub  → GitHub hbst-obsidian > Settings > Deploy keys
#                                    (Allow write access 체크) 에 등록
#   cat >> ~/.ssh/config <<'EOF'
#   Host github-vault
#       HostName github.com
#       User git
#       IdentityFile ~/.ssh/hbst_obsidian
#   EOF
VAULT_REPO="git@github-vault:shchun/hbst-obsidian.git"

# ── 1. System packages ────────────────────────────────────────────────────────
step "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git curl \
    docker.io docker-compose-v2 \
    python3.11 python3.11-venv python3-pip \
    ripgrep
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
info "System packages ready"

# ── 2. Clone Hermes Agent (public repo) ──────────────────────────────────────
step "Cloning NousResearch/hermes-agent"
if [ ! -d "$HERMES_AGENT_DIR" ]; then
    git clone https://github.com/NousResearch/hermes-agent.git "$HERMES_AGENT_DIR"
else
    git -C "$HERMES_AGENT_DIR" pull --rebase --autostash
fi
info "hermes-agent ready"

# ── 2.5 Clone Obsidian vault (private repo, bot write access) ────────────────
# 봇이 hermes_inbox/ 에 push 하려면 hbst-obsidian 레포에 write 권한이 필요.
# deploy key(쓰기 허용) 또는 fine-grained PAT를 미리 ~/.ssh 에 설정해 둘 것.
step "Cloning Obsidian vault"
if [ ! -d "$VAULT_DIR/.git" ]; then
    git clone "$VAULT_REPO" "$VAULT_DIR" \
        || echo "  (볼트 clone 실패 — 봇 deploy key/PAT 설정 후 수동 clone 필요)"
else
    git -C "$VAULT_DIR" pull --rebase --autostash || true
fi
# 봇 커밋 identity (커밋 메시지 hermes(source): … 와 짝)
if [ -d "$VAULT_DIR/.git" ]; then
    git -C "$VAULT_DIR" config user.name  "hermes-bot"
    git -C "$VAULT_DIR" config user.email "hermes-bot@users.noreply.github.com"
    info "Vault ready ($VAULT_DIR)"
fi

# ── 3. Install Hermes CLI ────────────────────────────────────────────────────
step "Installing Hermes CLI"
cd "$HERMES_AGENT_DIR"
# printf input: '\n' = install ripgrep (already done), 'n' = skip setup wizard
printf '\nn' | ./setup-hermes.sh
export PATH="$HOME/.local/bin:$PATH"
grep -qF 'local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
info "Hermes CLI installed → $(hermes --version 2>/dev/null || echo 'check PATH')"

# ── 4. MCP Python venv ────────────────────────────────────────────────────────
step "Setting up MCP Python venv"
cd "$APP_DIR/mcp"
python3.11 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
info "MCP venv ready ($APP_DIR/mcp/.venv)"

# ── 5. Hermes config (rendered from deploy/config.template.yaml) ─────────────
# Path placeholder is filled in; secret placeholders are left for manual edit.
step "Writing ~/.hermes/config.yaml"
mkdir -p ~/.hermes

sed -e "s|__APP_DIR__|${APP_DIR}|g" \
    -e "s|__VAULT_DIR__|${VAULT_DIR}|g" \
    "$APP_DIR/deploy/config.template.yaml" > ~/.hermes/config.yaml
info "~/.hermes/config.yaml written (replace __*_API_KEY__ / __SLACK_*__ values)"

# ── 6. SOUL.md ────────────────────────────────────────────────────────────────
cp "$APP_DIR/mcp/SOUL.md" ~/.hermes/SOUL.md
info "SOUL.md copied"

# ── 7. .env template ─────────────────────────────────────────────────────────
if [ ! -f ~/.hermes/.env ]; then
    cat > ~/.hermes/.env << 'ENV'
OPENAI_API_KEY=__REPLACE_OPENAI_API_KEY__
SLACK_BOT_TOKEN=__REPLACE_SLACK_BOT_TOKEN__
SLACK_APP_TOKEN=__REPLACE_SLACK_APP_TOKEN__
SLACK_ALLOWED_USERS=__REPLACE_SLACK_USER_ID__
ENV
    info "~/.hermes/.env template created"
else
    info "~/.hermes/.env already exists (skipped)"
fi

# ── 8. PostgreSQL (Docker) ────────────────────────────────────────────────────
step "Starting PostgreSQL"
cd "$APP_DIR"
sudo docker compose up -d db
echo -n "  Waiting for DB"
for i in $(seq 1 40); do
    if sudo docker exec hermes_db psql -U hermes -d hermes -c "SELECT 1" &>/dev/null; then
        echo " ready"
        break
    fi
    echo -n "."
    sleep 3
    [ "$i" -eq 40 ] && { echo " TIMEOUT"; exit 1; }
done
info "PostgreSQL ready"

# ── 9. Import CSV data ────────────────────────────────────────────────────────
step "Importing matzip CSV data"
cd "$APP_DIR"
"$APP_DIR/mcp/.venv/bin/python" "$APP_DIR/scripts/import_csv.py"
info "CSV data imported"

# ── 10. Enable systemd lingering (needed for --user services on EC2) ──────────
step "Enabling systemd user session"
loginctl enable-linger "$USER" 2>/dev/null || true
info "Lingering enabled"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo " Setup complete! Fill in secrets, then install the gateway."
echo "============================================================"
echo ""
echo "  # Step 1 — fill in your API keys"
echo "  nano ~/.hermes/.env"
echo "  nano ~/.hermes/config.yaml   # replace __*_API_KEY__ / __SLACK_*__ values"
echo ""
echo "  # Step 2 — install Slack gateway as a systemd service"
echo "  source ~/.bashrc"
echo "  hermes gateway setup         # one-time Slack pairing"
echo "  hermes gateway install       # register systemd service"
echo ""
echo "  # Step 3 — verify"
echo "  systemctl --user status hermes-gateway"
echo "  hermes doctor"
echo ""
