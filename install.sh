#!/bin/bash
# AI Investment Orchestrator - Installer v1.0
# Usage: curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install.sh | bash
#
# Sets up the orchestrator + Streamlit dashboard connecting to external Ghostfolio & llama-swap
VERSION="1.0"

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

REPO_RAW="https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main"
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Function to read input - works even when script is piped
ask() {
    local prompt="$1"
    local default="$2"
    local reply

    printf "%s" "$prompt" > /dev/tty
    read -r reply < /dev/tty

    if [ -z "$reply" ]; then
        echo "$default"
    else
        echo "$reply"
    fi
}

echo -e "${BLUE}"
echo "=============================================="
echo "  AI Investment Orchestrator v${VERSION}          "
echo "  LLM-powered portfolio management            "
echo "=============================================="
echo -e "${NC}"

# ============================================
# Step 1: Check Prerequisites
# ============================================
echo -e "${YELLOW}[1/5] Checking prerequisites...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "  ${RED}ERROR: Docker not installed${NC}"
    echo "  Install: https://docs.docker.com/engine/install/"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Docker found"

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "  ${RED}ERROR: Docker Compose not found${NC}"
    echo "  Install: https://docs.docker.com/compose/install/"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Docker Compose found"

# ============================================
# Step 2: Configure Services
# ============================================
echo ""
echo -e "${YELLOW}[2/5] Configure external services${NC}"
echo ""
echo "  The orchestrator connects to Ghostfolio and llama-swap."
echo "  Both must already be running on your network."
echo ""

DEFAULT_GHOSTFOLIO_URL="http://192.168.0.12:3333"
echo -e "  Ghostfolio URL (default: ${BLUE}$DEFAULT_GHOSTFOLIO_URL${NC})"
GHOSTFOLIO_URL=$(ask "  Ghostfolio URL [$DEFAULT_GHOSTFOLIO_URL]: " "$DEFAULT_GHOSTFOLIO_URL")

GHOSTFOLIO_TOKEN=$(ask "  Ghostfolio Access Token: " "")
if [ -z "$GHOSTFOLIO_TOKEN" ]; then
    echo -e "  ${RED}ERROR: Access token is required${NC}"
    echo "  Get it from Ghostfolio > Settings > Access Token"
    exit 1
fi

DEFAULT_LLM_URL="http://192.168.0.169:8080/v1"
echo ""
echo -e "  llama-swap URL (default: ${BLUE}$DEFAULT_LLM_URL${NC})"
LLM_URL=$(ask "  llama-swap URL [$DEFAULT_LLM_URL]: " "$DEFAULT_LLM_URL")

# Verify connectivity
echo ""
echo -e "  ${BLUE}Checking connectivity...${NC}"

if curl -sf --max-time 5 "$GHOSTFOLIO_URL/api/v1/info" > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} Ghostfolio reachable at $GHOSTFOLIO_URL"
else
    echo -e "  ${YELLOW}WARNING: Cannot reach Ghostfolio at $GHOSTFOLIO_URL${NC}"
    echo "  Make sure it's running before starting the orchestrator."
fi

LLM_BASE="${LLM_URL%/v1}"
if curl -sf --max-time 5 "$LLM_BASE/v1/models" > /dev/null 2>&1; then
    MODELS=$(curl -sf "$LLM_BASE/v1/models" 2>/dev/null | python3 -c "import sys,json; [print(f'    - {m[\"id\"]}') for m in json.load(sys.stdin).get('data',[])]" 2>/dev/null || echo "    (could not parse)")
    echo -e "  ${GREEN}✓${NC} llama-swap reachable. Available models:"
    echo "$MODELS"
else
    echo -e "  ${YELLOW}WARNING: Cannot reach llama-swap at $LLM_URL${NC}"
    echo "  Make sure it's running before starting the orchestrator."
fi

# ============================================
# Step 3: Choose Install Directory
# ============================================
echo ""
echo -e "${YELLOW}[3/5] Configure installation${NC}"
echo ""

DEFAULT_INSTALL_DIR="$HOME/invest-orchestrator"
echo -e "  Install directory (default: ${BLUE}$DEFAULT_INSTALL_DIR${NC})"
INSTALL_DIR=$(ask "  Directory [$DEFAULT_INSTALL_DIR]: " "$DEFAULT_INSTALL_DIR")
INSTALL_DIR="${INSTALL_DIR/#\~/$HOME}"

mkdir -p "$INSTALL_DIR"
echo -e "  ${GREEN}✓${NC} Using: $INSTALL_DIR"

DEFAULT_BUDGET=$(ask "  Initial budget per account [$10000]: " "10000")

# ============================================
# Step 4: Download & Build
# ============================================
echo ""
echo -e "${YELLOW}[4/5] Downloading and building...${NC}"

# Download project files
echo -e "  ${BLUE}Downloading project files...${NC}"

curl -fsSL "$REPO_RAW/docker-compose.yml"   -o "$INSTALL_DIR/docker-compose.yml"
curl -fsSL "$REPO_RAW/config.yaml"          -o "$INSTALL_DIR/config.yaml"

mkdir -p "$INSTALL_DIR/orchestrator/src" \
         "$INSTALL_DIR/orchestrator/dashboard/pages" \
         "$INSTALL_DIR/orchestrator/dashboard/components"
# data/ and logs/ are Docker named volumes — no host dirs needed

curl -fsSL "$REPO_RAW/orchestrator/Dockerfile"        -o "$INSTALL_DIR/orchestrator/Dockerfile"
curl -fsSL "$REPO_RAW/orchestrator/pyproject.toml"    -o "$INSTALL_DIR/orchestrator/pyproject.toml"
curl -fsSL "$REPO_RAW/orchestrator/supervisord.conf"  -o "$INSTALL_DIR/orchestrator/supervisord.conf"
curl -fsSL "$REPO_RAW/orchestrator/entrypoint.sh"     -o "$INSTALL_DIR/orchestrator/entrypoint.sh"
chmod +x "$INSTALL_DIR/orchestrator/entrypoint.sh"
curl -fsSL "$REPO_RAW/.dockerignore"                  -o "$INSTALL_DIR/.dockerignore"

# Source modules
for f in __init__ main ghostfolio_client llm_client market_data technical_indicators \
         portfolio_state news_fetcher account_manager prompt_builder decision_parser \
         risk_manager trade_executor audit_logger; do
    curl -fsSL "$REPO_RAW/orchestrator/src/${f}.py" -o "$INSTALL_DIR/orchestrator/src/${f}.py"
done

# Dashboard
curl -fsSL "$REPO_RAW/orchestrator/dashboard/__init__.py"    -o "$INSTALL_DIR/orchestrator/dashboard/__init__.py"
curl -fsSL "$REPO_RAW/orchestrator/dashboard/app.py"        -o "$INSTALL_DIR/orchestrator/dashboard/app.py"
curl -fsSL "$REPO_RAW/orchestrator/dashboard/config_utils.py" -o "$INSTALL_DIR/orchestrator/dashboard/config_utils.py"

for f in __init__ overview account_detail run_control model_compare audit_logs account_management settings; do
    curl -fsSL "$REPO_RAW/orchestrator/dashboard/pages/${f}.py" -o "$INSTALL_DIR/orchestrator/dashboard/pages/${f}.py"
done

curl -fsSL "$REPO_RAW/orchestrator/dashboard/components/__init__.py" -o "$INSTALL_DIR/orchestrator/dashboard/components/__init__.py"
curl -fsSL "$REPO_RAW/orchestrator/dashboard/components/charts.py"   -o "$INSTALL_DIR/orchestrator/dashboard/components/charts.py"

echo -e "  ${GREEN}✓${NC} Files downloaded"

# Create .env
cat > "$INSTALL_DIR/.env" << ENVEOF
GHOSTFOLIO_URL=$GHOSTFOLIO_URL
GHOSTFOLIO_ACCESS_TOKEN=$GHOSTFOLIO_TOKEN
LLM_BASE_URL=$LLM_URL
INITIAL_BUDGET=$DEFAULT_BUDGET
LOG_LEVEL=INFO
ENVEOF
echo -e "  ${GREEN}✓${NC} Configuration written"

# Build Docker image
echo -e "  ${BLUE}Building Docker image (this may take a few minutes)...${NC}"
cd "$INSTALL_DIR"
if docker compose build 2>&1 | tail -5; then
    echo -e "  ${GREEN}✓${NC} Docker image built"
else
    echo -e "  ${RED}ERROR: Docker build failed${NC}"
    exit 1
fi

# ============================================
# Step 5: Install CLI Command
# ============================================
echo ""
echo -e "${YELLOW}[5/5] Installing 'invest' command...${NC}"

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/invest" << 'WRAPPER'
#!/bin/bash
# AI Investment Orchestrator v__VERSION__
# Install dir: __INSTALL_DIR__

INSTALL_DIR="__INSTALL_DIR__"

case "${1:-help}" in
    start)
        echo "Starting orchestrator + dashboard..."
        cd "$INSTALL_DIR" && docker compose up -d
        echo ""
        echo "Dashboard: http://localhost:8501"
        echo "Scheduler running in background."
        ;;
    stop)
        echo "Stopping..."
        cd "$INSTALL_DIR" && docker compose down
        ;;
    restart)
        cd "$INSTALL_DIR" && docker compose restart
        echo "Restarted. Dashboard: http://localhost:8501"
        ;;
    status)
        cd "$INSTALL_DIR" && docker compose ps
        ;;
    logs)
        cd "$INSTALL_DIR" && docker compose logs -f --tail=100
        ;;
    run)
        shift
        ACCOUNT="${1:-}"
        if [ -z "$ACCOUNT" ]; then
            echo "Usage: invest run <account_key> [--dry-run]"
            echo ""
            echo "Accounts:"
            cd "$INSTALL_DIR" && docker compose exec orchestrator python3 -c 'import yaml; d=yaml.safe_load(open("/app/data/config.yaml")); [print("  "+k+" - "+v.get("name",k)) for k,v in d.get("accounts",{}).items()]' 2>/dev/null || echo "  (start container first: invest start)"
            exit 1
        fi
        shift
        cd "$INSTALL_DIR" && docker compose exec orchestrator python -m src.main --once "$ACCOUNT" "$@"
        ;;
    run-all)
        shift
        cd "$INSTALL_DIR" && docker compose exec orchestrator python -m src.main --all "$@"
        ;;
    config)
        cd "$INSTALL_DIR" && docker compose exec orchestrator sh -c 'cat /app/data/config.yaml' > /tmp/_invest_cfg.yaml 2>/dev/null || { echo "Container not running. Use: invest start"; exit 1; }
        ${EDITOR:-nano} /tmp/_invest_cfg.yaml
        cd "$INSTALL_DIR" && docker compose exec -T orchestrator sh -c 'cat > /app/data/config.yaml' < /tmp/_invest_cfg.yaml && echo "Config saved."
        rm -f /tmp/_invest_cfg.yaml
        ;;
    rebuild)
        echo "Rebuilding Docker image (no cache)..."
        cd "$INSTALL_DIR" && docker compose build --no-cache && docker compose up -d
        echo "Rebuilt. Dashboard: http://localhost:8501"
        ;;
    dashboard)
        echo "Dashboard: http://localhost:8501"
        command -v xdg-open &>/dev/null && xdg-open http://localhost:8501 2>/dev/null || true
        ;;
    update)
        echo "Updating..."
        curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install.sh | bash
        ;;
    *)
        echo "AI Investment Orchestrator v__VERSION__"
        echo ""
        echo "Usage: invest <command>"
        echo ""
        echo "Commands:"
        echo "  start          Start orchestrator + dashboard"
        echo "  stop           Stop all services"
        echo "  restart        Restart services"
        echo "  status         Show container status"
        echo "  logs           Follow container logs"
        echo "  run <account>  Run single cycle (add --dry-run for simulation)"
        echo "  run-all        Run all accounts once (add --dry-run)"
        echo "  config         Edit config (requires running container)"
        echo "  rebuild        Rebuild Docker image without cache"
        echo "  dashboard      Open dashboard in browser"
        echo "  update         Re-run installer to update"
        echo ""
        echo "Dashboard: http://localhost:8501"
        echo "Install:   $INSTALL_DIR"
        ;;
esac
WRAPPER
sed -i "s|__INSTALL_DIR__|$INSTALL_DIR|g; s|__VERSION__|$VERSION|g" "$BIN_DIR/invest"

chmod +x "$BIN_DIR/invest"
echo -e "  ${GREEN}✓${NC} Installed to $BIN_DIR/invest"

# Add to PATH if needed
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    SHELL_RC=""
    [ -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.zshrc"
    [ -f "$HOME/.bashrc" ] && SHELL_RC="${SHELL_RC:-$HOME/.bashrc}"

    if [ -n "$SHELL_RC" ] && ! grep -q ".local/bin" "$SHELL_RC" 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
        echo -e "  ${GREEN}✓${NC} Added ~/.local/bin to PATH in $SHELL_RC"
        echo -e "  ${YELLOW}Run: source $SHELL_RC${NC}"
    fi
fi

# ============================================
# Done
# ============================================
echo ""
echo -e "${GREEN}=============================================="
echo "         Installation Complete!               "
echo "==============================================${NC}"
echo ""
echo "Quick start:"
echo "  invest start         # Start services"
echo "  invest run-all --dry-run   # Test all accounts (no real trades)"
echo "  invest dashboard     # Open web dashboard"
echo ""
echo "Dashboard: http://localhost:8501"
echo "Config:    $INSTALL_DIR/data/config.yaml"
echo ""
