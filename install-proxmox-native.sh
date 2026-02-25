#!/usr/bin/env bash
# AI Investment Orchestrator — Proxmox LXC Native Installer (no Docker)
#
# Run on the Proxmox HOST shell:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install-proxmox-native.sh)"
#
# Creates a Debian 12 LXC, installs Python 3.12 + Supervisor directly.
# No Docker — lighter footprint (~250 MB RAM idle vs ~420 MB with Docker).
#
# Prerequisites:
#   - Proxmox VE 7+ (pct command)
#   - GitHub PAT with repo (read) scope — to clone the private repository

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "  ${RED}✗${NC} $*"; }
info() { echo -e "  ${BLUE}→${NC} $*"; }

ask() {
    local reply
    printf "%b" "$1" > /dev/tty
    read -r reply < /dev/tty
    echo "${reply:-$2}"
}
ask_secret() {
    local reply
    printf "%b" "$1" > /dev/tty
    read -r reply < /dev/tty
    echo "$reply"
}

APP_DIR="/opt/invest-orchestrator"
REPO="Twozee-Tech/Invest_Research"

# ── banner ────────────────────────────────────────────────────────────────────
echo -e "${BLUE}${BOLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║   AI Investment Orchestrator                 ║"
echo "║   Proxmox LXC — Native Python Installer     ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo "  No Docker. Python 3.12 + Supervisor directly in LXC."
echo "  Idle RAM: ~250 MB  |  Disk: ~1.2 GB"
echo ""

# ── 1. verify Proxmox host ────────────────────────────────────────────────────
echo -e "${YELLOW}[1/6] Checking Proxmox host...${NC}"
if ! command -v pct &>/dev/null; then
    err "pct not found — run this on the Proxmox VE host shell."
    exit 1
fi
ok "Proxmox VE detected"

HOST_ARCH=$(uname -m)
case "$HOST_ARCH" in
    aarch64) ARCH="arm64" ;;
    x86_64)  ARCH="amd64" ;;
    *) err "Unsupported architecture: $HOST_ARCH"; exit 1 ;;
esac
ok "Host architecture: $ARCH"

# ── 2. find / download Debian 12 template ────────────────────────────────────
echo ""
echo -e "${YELLOW}[2/6] Finding Debian 12 LXC template...${NC}"

TEMPLATE_FILE=$(find /var/lib/vz/template/cache/ -name "debian-12-*${ARCH}*.tar.*" 2>/dev/null | sort -V | tail -1 || true)

if [[ -z "$TEMPLATE_FILE" ]]; then
    info "Not found locally — checking Proxmox mirrors..."
    pveam update 2>/dev/null || true
    TEMPLATE_NAME=$(pveam available --section system 2>/dev/null \
        | awk '{print $2}' | grep -E "debian-12.*${ARCH}" | sort -V | tail -1 || true)

    if [[ -n "$TEMPLATE_NAME" ]]; then
        pveam download local "$TEMPLATE_NAME"
        TEMPLATE_FILE="/var/lib/vz/template/cache/$TEMPLATE_NAME"
    else
        # Proxmox mirrors don't carry arm64 templates — download from linuxcontainers.org
        warn "Proxmox mirrors have no ${ARCH} template. Downloading from linuxcontainers.org..."
        LC_BASE="https://images.linuxcontainers.org/images/debian/bookworm/${ARCH}/default"
        LC_VER=$(curl -s "${LC_BASE}/" | grep -oP '\d{8}_\d+:\d+' | tail -1)
        if [[ -z "$LC_VER" ]]; then
            err "Could not fetch template list from linuxcontainers.org"
            exit 1
        fi
        TEMPLATE_FILE="/var/lib/vz/template/cache/debian-12-standard_${ARCH}.tar.xz"
        info "Downloading ${LC_VER} rootfs (~100 MB)..."
        wget -q --show-progress \
            "${LC_BASE}/${LC_VER}/rootfs.tar.xz" \
            -O "$TEMPLATE_FILE"
    fi
fi
ok "Template: $(basename "$TEMPLATE_FILE")"
TEMPLATE_STOR="local:vztmpl/$(basename "$TEMPLATE_FILE")"

# ── 3. LXC configuration ──────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[3/6] Configure LXC...${NC}"
echo ""

echo "  Available storage:"
pvesm status --content rootdir 2>/dev/null \
    | awk 'NR>1 {printf "    %-20s %s GiB free\n", $1, int($5/1024/1024)}' || true
echo ""

DEFAULT_STORAGE=$(pvesm status --content rootdir 2>/dev/null \
    | awk 'NR>1 {print $1; exit}')
DEFAULT_STORAGE=${DEFAULT_STORAGE:-local}

CTID=$(pvesh get /cluster/nextid 2>/dev/null || echo "200")
CTID=$(ask "  Container ID [${CTID}]: " "$CTID")
STORAGE=$(ask "  Storage [${DEFAULT_STORAGE}]: " "$DEFAULT_STORAGE")
HOSTNAME=$(ask "  Hostname [invest-orchestrator]: " "invest-orchestrator")
RAM=$(ask   "  RAM MB [512]: " "512")
DISK=$(ask  "  Disk GB [4]: " "4")
CORES=$(ask "  CPU cores [1]: " "1")

echo ""
# Suggest next free IP by scanning existing CT configs
LAST_IP=$(grep -h '^net0:' /etc/pve/lxc/*.conf 2>/dev/null \
    | grep -oE 'ip=[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' \
    | grep -oE '[0-9]+$' | sort -n | tail -1)
SUGGEST_LAST=$(( ${LAST_IP:-12} + 1 ))
DEFAULT_IP="192.168.0.${SUGGEST_LAST}/24"

CT_IP=$(ask "  Container IP [${DEFAULT_IP}]: " "$DEFAULT_IP")
GW=$(ask   "  Gateway [192.168.0.1]: " "192.168.0.1")
NET_IP="ip=${CT_IP},gw=${GW}"

# ── 4. credentials ────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[4/6] Configure credentials...${NC}"
echo ""

echo -e "  ${BOLD}GitHub PAT${NC} — to clone the private repository."
echo "  Create at: GitHub → Settings → Developer settings → Personal access tokens"
echo "  Required scope: repo (or Contents: Read for fine-grained PAT)"
echo ""
GITHUB_TOKEN=$(ask_secret "  GitHub PAT: ")
[[ -z "$GITHUB_TOKEN" ]] && { err "GitHub token required."; exit 1; }

echo ""
echo -e "  ${BOLD}Ghostfolio${NC}"
GHOSTFOLIO_URL=$(ask  "  URL [http://192.168.0.12:3333]: " "http://192.168.0.12:3333")
GHOSTFOLIO_TOKEN=$(ask_secret "  Access Token: ")
[[ -z "$GHOSTFOLIO_TOKEN" ]] && { err "Ghostfolio token required."; exit 1; }

echo ""
echo -e "  ${BOLD}llama-swap${NC}"
LLM_URL=$(ask "  URL [http://192.168.0.169:8080/v1]: " "http://192.168.0.169:8080/v1")

echo ""
INITIAL_BUDGET=$(ask "  Initial budget per account USD [10000]: " "10000")

# ── 5. create LXC ────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[5/6] Creating LXC ${CTID}...${NC}"

# Auto-detect bridge used by existing containers (works for non-standard setups like NanoPi)
BRIDGE=$(grep -h 'bridge=' /etc/pve/lxc/*.conf 2>/dev/null \
    | grep -oE 'bridge=[^,]+' | cut -d= -f2 \
    | sort | uniq -c | sort -rn | awk 'NR==1{print $2}')
BRIDGE=${BRIDGE:-vmbr0}
info "Using network bridge: ${BRIDGE}"

pct create "$CTID" "$TEMPLATE_STOR" \
    --hostname "$HOSTNAME" \
    --memory   "$RAM" \
    --cores    "$CORES" \
    --rootfs   "${STORAGE}:${DISK}" \
    --net0     "name=eth0,bridge=${BRIDGE},${NET_IP}" \
    --unprivileged 1 \
    --ostype   debian \
    --start    1 \
    --onboot   1

ok "Container ${CTID} created and started"
info "Waiting for boot..."
sleep 6

# ── 6. provision ─────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[6/6] Provisioning (Python 3.11 + deps)...${NC}"
info "This takes 3–5 minutes on first run (compiling packages on ARM)."

WORK_DIR="${APP_DIR}/orchestrator"
ENV_INLINE="GHOSTFOLIO_URL=\"${GHOSTFOLIO_URL}\",GHOSTFOLIO_ACCESS_TOKEN=\"${GHOSTFOLIO_TOKEN}\",LLM_BASE_URL=\"${LLM_URL}\",INITIAL_BUDGET=\"${INITIAL_BUDGET}\",LOG_LEVEL=\"INFO\",TZ=\"Europe/Warsaw\""

# ---- 6a: packages + clone + venv ----
pct exec "$CTID" -- bash -euo pipefail << PROVISION
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev \
    gcc g++ git curl ca-certificates supervisor

git clone --depth=1 "https://${GITHUB_TOKEN}@github.com/${REPO}.git" "${APP_DIR}"
git -C "${APP_DIR}" remote set-url origin "https://github.com/${REPO}.git"

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip --quiet
echo "Installing Python packages (may take 15+ min on ARM)..."
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/orchestrator/requirements.txt"

mkdir -p "${APP_DIR}/orchestrator/data" "${APP_DIR}/orchestrator/logs"
cp "${APP_DIR}/config.yaml" "${APP_DIR}/orchestrator/data/config.yaml"
PROVISION

# ---- 6b: write supervisor config from outer shell (avoids nested heredoc issues) ----
SUPERVISOR_CONF=$(cat << EOF
[program:scheduler]
command=${APP_DIR}/.venv/bin/python -m src.main
directory=${WORK_DIR}
environment=${ENV_INLINE}
autostart=true
autorestart=true
stdout_logfile=${WORK_DIR}/logs/scheduler.log
stderr_logfile=${WORK_DIR}/logs/scheduler.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3

[program:dashboard]
command=${APP_DIR}/.venv/bin/streamlit run dashboard/app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true
directory=${WORK_DIR}
environment=${ENV_INLINE}
autostart=true
autorestart=true
stdout_logfile=${WORK_DIR}/logs/dashboard.log
stderr_logfile=${WORK_DIR}/logs/dashboard.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
EOF
)
echo "$SUPERVISOR_CONF" > /tmp/invest-supervisor.conf
pct push "$CTID" /tmp/invest-supervisor.conf /etc/supervisor/conf.d/invest.conf
rm -f /tmp/invest-supervisor.conf

# ---- 6c: enable and start supervisor ----
pct exec "$CTID" -- bash -euo pipefail << 'FINALIZE'
systemctl enable supervisor
systemctl restart supervisor
sleep 5
supervisorctl status
FINALIZE

CT_IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || echo "<container-ip>")

echo ""
echo -e "${GREEN}${BOLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║   Installation complete!                     ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Dashboard:  ${BOLD}http://${CT_IP}:8501${NC}"
echo ""
echo "  Management (run on Proxmox host):"
echo "    pct exec $CTID -- supervisorctl status"
echo "    pct exec $CTID -- supervisorctl restart all"
echo "    pct exec $CTID -- tail -f ${APP_DIR}/logs/scheduler.log"
echo ""
echo "  Update app:"
echo "    pct exec $CTID -- bash -c 'cd ${APP_DIR} && git pull && supervisorctl restart all'"
echo ""
