#!/usr/bin/env bash
# ============================================================
# Hermes Matzip — Ubuntu 22.04 EC2 Setup Script
# Run once on a fresh instance after cloning the matzip repo.
#
#   git clone git@github.com:shchun/matzip.git ~/matzip
#   bash ~/matzip/deploy/setup-vm.sh
# ============================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}✓${NC} $*"; }
step() { echo -e "\n${GREEN}==>${NC} $*"; }

MATZIP_DIR="$HOME/matzip"
HERMES_AGENT_DIR="$HOME/hermes-agent"

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
cd "$MATZIP_DIR/mcp"
python3.11 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
info "MCP venv ready ($MATZIP_DIR/mcp/.venv)"

# ── 5. Hermes config (Linux paths, placeholder secrets) ──────────────────────
step "Writing ~/.hermes/config.yaml"
mkdir -p ~/.hermes

cat > ~/.hermes/config.yaml << YAML
model:
  default: gpt-4o-mini
  provider: custom
  base_url: https://api.openai.com/v1
agent:
  max_turns: 60
  verbose: false
  reasoning_effort: none
streaming:
  enabled: false
compression:
  enabled: true
  threshold: 0.5
  target_ratio: 0.2
  protect_last_n: 20
mcp_servers:
  matzip:
    command: ${MATZIP_DIR}/mcp/.venv/bin/python
    args:
      - ${MATZIP_DIR}/mcp/matzip_mcp.py
    env:
      DATABASE_URL: "postgresql://hermes:hermes1234@localhost:5432/hermes"
      GOOGLE_MAPS_API_KEY: "__REPLACE_GOOGLE_MAPS_API_KEY__"
      SLACK_BOT_TOKEN: "__REPLACE_SLACK_BOT_TOKEN__"
      SLACK_CHANNEL: "__REPLACE_SLACK_CHANNEL_ID__"
      PROXIMITY_RADIUS_METERS: "500"
      HOME_LAT: "37.4878"
      HOME_LNG: "126.9803"
YAML
info "~/.hermes/config.yaml written"

# ── 6. SOUL.md ────────────────────────────────────────────────────────────────
cp "$MATZIP_DIR/mcp/SOUL.md" ~/.hermes/SOUL.md
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
cd "$MATZIP_DIR"
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
cd "$MATZIP_DIR"
"$MATZIP_DIR/mcp/.venv/bin/python" "$MATZIP_DIR/scripts/import_csv.py"
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
echo "  nano ~/.hermes/config.yaml   # replace __REPLACE_* values"
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
