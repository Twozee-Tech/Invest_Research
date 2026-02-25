#!/usr/bin/env bash
# AI Investment Orchestrator — Proxmox LXC Installer
#
# Run on the Proxmox HOST shell:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install-proxmox.sh)"
#
# What this does:
#   1. Creates a Debian 12 LXC container (auto-detects arm64 / amd64)
#   2. Installs Docker inside it
#   3. Logs in to GHCR and pulls the pre-built image
#   4. Starts the orchestrator + Streamlit dashboard
#
# Prerequisites on Proxmox host:
#   - Proxmox VE 7+ (pct command available)
#   - Internet access from the host
#   - A GitHub PAT with read:packages scope (to pull the private GHCR image)

set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "  ${RED}✗${NC} $*"; }
info() { echo -e "  ${BLUE}→${NC} $*"; }

ask() {           # ask <prompt> <default>
    local reply
    printf "%b" "$1" > /dev/tty
    read -r reply < /dev/tty
    echo "${reply:-$2}"
}
ask_secret() {    # ask_secret <prompt>
    local reply
    printf "%b" "$1" > /dev/tty
    read -rs reply < /dev/tty
    echo "" > /dev/tty
    echo "$reply"
}

# ── constants ─────────────────────────────────────────────────────────────────
IMAGE="ghcr.io/twozee-tech/invest-orchestrator:latest"
COMPOSE_URL="https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/docker-compose.prod.yml"
APP_DIR="/opt/invest-orchestrator"

# ── banner ────────────────────────────────────────────────────────────────────
echo -e "${BLUE}${BOLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║   AI Investment Orchestrator                 ║"
echo "║   Proxmox LXC Installer                      ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── 1. verify Proxmox host ────────────────────────────────────────────────────
echo -e "${YELLOW}[1/6] Checking Proxmox host...${NC}"
if ! command -v pct &>/dev/null; then
    err "pct not found — run this script on the Proxmox VE host shell."
    exit 1
fi
ok "Running on Proxmox VE"

# Detect host architecture
HOST_ARCH=$(uname -m)
case "$HOST_ARCH" in
    aarch64) ARCH="arm64" ;;
    x86_64)  ARCH="amd64" ;;
    *)       err "Unsupported host architecture: $HOST_ARCH"; exit 1 ;;
esac
ok "Host architecture: $ARCH"

# ── 2. find / download LXC template ──────────────────────────────────────────
echo ""
echo -e "${YELLOW}[2/6] Finding Debian 12 LXC template...${NC}"

TEMPLATE_PATTERN="debian-12.*${ARCH}"

# Search local storage first
TEMPLATE_FILE=$(find /var/lib/vz/template/cache/ -name "debian-12-*${ARCH}*.tar.*" 2>/dev/null | sort -V | tail -1 || true)

if [[ -z "$TEMPLATE_FILE" ]]; then
    info "Template not cached locally — downloading from Proxmox mirrors..."
    pveam update 2>/dev/null || true

    TEMPLATE_NAME=$(pveam available --section system 2>/dev/null \
        | awk '{print $2}' \
        | grep -E "$TEMPLATE_PATTERN" \
        | sort -V | tail -1 || true)

    if [[ -z "$TEMPLATE_NAME" ]]; then
        err "No Debian 12 ${ARCH} template found."
        echo "  Try: pveam update && pveam available --section system | grep debian"
        exit 1
    fi

    info "Downloading: $TEMPLATE_NAME"
    pveam download local "$TEMPLATE_NAME"
    TEMPLATE_FILE="/var/lib/vz/template/cache/$TEMPLATE_NAME"
fi
ok "Template: $(basename "$TEMPLATE_FILE")"

# Proxmox storage path format
TEMPLATE_STOR="local:vztmpl/$(basename "$TEMPLATE_FILE")"

# ── 3. select storage for rootfs ─────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[3/6] Select storage and configure LXC...${NC}"

echo ""
echo "  Available storage pools:"
pvesm status --content rootdir 2>/dev/null \
    | awk 'NR>1 && $3>0 {printf "    %-20s %s GiB free\n", $1, int($5/1024/1024)}' \
    || pvesm status | awk 'NR>1 {print "    "$1}'
echo ""

DEFAULT_STORAGE="local-lvm"
STORAGE=$(ask "  Storage for rootfs [${DEFAULT_STORAGE}]: " "$DEFAULT_STORAGE")

CTID=$(pvesh get /cluster/nextid 2>/dev/null || echo "200")
CTID=$(ask "  Container ID [${CTID}]: " "$CTID")

DEFAULT_HOSTNAME="invest-orchestrator"
HOSTNAME=$(ask "  Hostname [${DEFAULT_HOSTNAME}]: " "$DEFAULT_HOSTNAME")

DEFAULT_RAM="1024"
RAM=$(ask "  RAM in MB [${DEFAULT_RAM}]: " "$DEFAULT_RAM")

DEFAULT_DISK="8"
DISK=$(ask "  Disk size in GB [${DEFAULT_DISK}]: " "$DEFAULT_DISK")

DEFAULT_CORES="2"
CORES=$(ask "  CPU cores [${DEFAULT_CORES}]: " "$DEFAULT_CORES")

echo ""
echo "  Network (leave blank for DHCP):"
DEFAULT_IP="dhcp"
IP_CONFIG=$(ask "  IP address (e.g. 192.168.0.50/24 or dhcp) [dhcp]: " "$DEFAULT_IP")
if [[ "$IP_CONFIG" == "dhcp" ]]; then
    NET_IP="ip=dhcp"
else
    echo -e "  Gateway:"
    GW=$(ask "  Gateway [192.168.0.1]: " "192.168.0.1")
    NET_IP="ip=${IP_CONFIG},gw=${GW}"
fi

# ── 4. credentials ────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[4/6] Configure credentials...${NC}"
echo ""
echo -e "  ${BOLD}GitHub PAT${NC} — needed to pull the private GHCR image."
echo "  Create at: GitHub → Settings → Developer settings → Personal access tokens"
echo "  Required scope: read:packages"
echo ""
GITHUB_USER=$(ask "  GitHub username [Twozee-Tech]: " "Twozee-Tech")
GITHUB_TOKEN=$(ask_secret "  GitHub PAT (read:packages): ")
if [[ -z "$GITHUB_TOKEN" ]]; then
    err "GitHub token is required to pull the private image."
    exit 1
fi

echo ""
echo -e "  ${BOLD}Ghostfolio${NC}"
GHOSTFOLIO_URL=$(ask "  Ghostfolio URL [http://192.168.0.12:3333]: " "http://192.168.0.12:3333")
GHOSTFOLIO_TOKEN=$(ask_secret "  Ghostfolio Access Token: ")
if [[ -z "$GHOSTFOLIO_TOKEN" ]]; then
    err "Ghostfolio token is required."
    exit 1
fi

echo ""
echo -e "  ${BOLD}llama-swap${NC}"
LLM_URL=$(ask "  llama-swap URL [http://192.168.0.169:8080/v1]: " "http://192.168.0.169:8080/v1")

echo ""
INITIAL_BUDGET=$(ask "  Initial budget per account USD [10000]: " "10000")

# ── 5. create LXC ────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[5/6] Creating LXC container ${CTID}...${NC}"

pct create "$CTID" "$TEMPLATE_STOR" \
    --hostname "$HOSTNAME" \
    --memory "$RAM" \
    --cores "$CORES" \
    --rootfs "${STORAGE}:${DISK}" \
    --net0 "name=eth0,bridge=vmbr0,${NET_IP}" \
    --features "nesting=1,keyctl=1" \
    --unprivileged 0 \
    --ostype debian \
    --start 1 \
    --onboot 1

ok "Container ${CTID} created and started"

# Give container time to boot
info "Waiting for container to boot..."
sleep 8

# ── 6. provision inside container ────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[6/6] Provisioning container...${NC}"

pct exec "$CTID" -- bash -euo pipefail << PROVISION
set -euo pipefail

# Basics
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release

# Docker
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/debian \$(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list

apt-get update -qq
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker --now

echo "Docker installed: \$(docker --version)"

# App directory
mkdir -p ${APP_DIR}
cd ${APP_DIR}

# GHCR login
echo "${GITHUB_TOKEN}" | docker login ghcr.io -u "${GITHUB_USER}" --password-stdin

# docker-compose.prod.yml
curl -fsSL "${COMPOSE_URL}" -o docker-compose.yml

# .env
cat > .env << 'EOF'
GHOSTFOLIO_URL=${GHOSTFOLIO_URL}
GHOSTFOLIO_ACCESS_TOKEN=${GHOSTFOLIO_TOKEN}
LLM_BASE_URL=${LLM_URL}
INITIAL_BUDGET=${INITIAL_BUDGET}
LOG_LEVEL=INFO
EOF

# Pull and start
docker compose pull
docker compose up -d

echo "Done"
PROVISION

# Print final status
CT_IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || echo "<container-ip>")

echo ""
echo -e "${GREEN}${BOLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║   Installation complete!                     ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Dashboard:  ${BOLD}http://${CT_IP}:8501${NC}"
echo ""
echo "  Useful commands (run on Proxmox host):"
echo "    pct exec $CTID -- docker compose -f ${APP_DIR}/docker-compose.yml logs -f"
echo "    pct exec $CTID -- docker compose -f ${APP_DIR}/docker-compose.yml restart"
echo "    pct exec $CTID -- docker compose -f ${APP_DIR}/docker-compose.yml pull && \\"
echo "      docker compose -f ${APP_DIR}/docker-compose.yml up -d   # update"
echo ""
