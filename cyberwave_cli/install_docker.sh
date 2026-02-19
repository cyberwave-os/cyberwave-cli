#!/usr/bin/env bash
set -euo pipefail

SUPPORTED_UBUNTU_DOC="https://docs.docker.com/engine/install/ubuntu/"
SUPPORTED_DEBIAN_DOC="https://docs.docker.com/engine/install/debian/"

log() {
  printf '[install-docker] %s\n' "$*"
}

error() {
  printf '[install-docker] ERROR: %s\n' "$*" >&2
}

fail_unsupported() {
  local os_hint="${1:-unknown}"
  error "Automatic Docker installation is only supported for Linux Ubuntu/Debian/Raspbian (64-bit) right now."
  error "Detected: ${os_hint}"
  error "Please install Docker manually for this machine/OS."
  error "Ubuntu docs: ${SUPPORTED_UBUNTU_DOC}"
  error "Debian/Raspbian docs: ${SUPPORTED_DEBIAN_DOC}"
  exit 1
}

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  error "This script must run as root (sudo)."
  exit 1
fi

if [ "$(uname -s)" != "Linux" ]; then
  fail_unsupported "$(uname -s)"
fi

if [ ! -f /etc/os-release ]; then
  fail_unsupported "linux-without-/etc/os-release"
fi

# shellcheck disable=SC1091
. /etc/os-release

DISTRO_ID="${ID:-}"
CODENAME="${VERSION_CODENAME:-}"
ARCH="$(dpkg --print-architecture)"
DOCKER_REPO_DISTRO=""
DOCKER_REPO_CODENAME=""

case "$DISTRO_ID" in
  ubuntu)
    DOCKER_REPO_DISTRO="ubuntu"
    DOCKER_REPO_CODENAME="${UBUNTU_CODENAME:-$CODENAME}"
    ;;
  debian)
    DOCKER_REPO_DISTRO="debian"
    DOCKER_REPO_CODENAME="$CODENAME"
    ;;
  raspbian)
    # Raspbian (64-bit) follows Debian instructions and package repo layout.
    DOCKER_REPO_DISTRO="debian"
    DOCKER_REPO_CODENAME="$CODENAME"
    ;;
  *)
    fail_unsupported "linux-${DISTRO_ID:-unknown}"
    ;;
esac

case "$ARCH" in
  amd64|arm64)
    ;;
  *)
    fail_unsupported "${DISTRO_ID:-unknown}-${ARCH}"
    ;;
esac

if [ -z "$DOCKER_REPO_CODENAME" ]; then
  fail_unsupported "${DISTRO_ID:-unknown}-missing-codename"
fi

log "Preparing apt for Docker (${DISTRO_ID}, ${DOCKER_REPO_CODENAME}, ${ARCH})..."
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y ca-certificates curl

install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/${DOCKER_REPO_DISTRO}/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/${DOCKER_REPO_DISTRO}
Suites: ${DOCKER_REPO_CODENAME}
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

log "Installing Docker packages..."
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if command -v systemctl >/dev/null 2>&1; then
  log "Starting Docker service with systemd..."
  systemctl enable docker
  systemctl start docker
elif command -v service >/dev/null 2>&1; then
  log "Starting Docker service with service command..."
  service docker start
fi

log "Waiting for Docker daemon to become ready..."
docker_ready=0
for _ in $(seq 1 30); do
  if docker info >/dev/null 2>&1; then
    docker_ready=1
    break
  fi
  sleep 1
done

if [ "$docker_ready" -ne 1 ]; then
  error "Docker was installed, but the daemon is not ready yet."
  error "Try: systemctl status docker (or service docker status), then retry."
  exit 1
fi

log "Docker installation complete and daemon is ready."
docker --version