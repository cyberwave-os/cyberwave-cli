#!/usr/bin/env bash
# install-local-cli-mac.sh
#
# Install the local cyberwave-cli source into an isolated venv on macOS and
# symlink the `cyberwave` binary into $HOME/.local/bin so it is available on PATH.
#
# Usage:
#   ./scripts/install-local-cli-mac.sh [--sdk-source pypi|local]
#
# Environment variable overrides (optional):
#   CYBERWAVE_LOCAL_ENV_DIR   venv location  (default: $HOME/.cyberwave-cli/venv-local)
#   CYBERWAVE_LOCAL_BIN_DIR   symlink target (default: $HOME/.local/bin)
#
# The script must be run from any directory; it resolves the repo root relative
# to its own location so it always finds the correct source tree.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts/ lives inside cyberwave-python-cli/ which lives inside cyberwave-clis/
# The repo root is two levels above.
CLI_SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SDK_SRC_DIR="${REPO_ROOT}/cyberwave-sdks/cyberwave-python"
CLOUD_NODE_SRC_DIR="${REPO_ROOT}/cyberwave-cloud-nodes/cyberwave-cloud-node"

VENV_DIR="${CYBERWAVE_LOCAL_ENV_DIR:-${HOME}/.cyberwave-cli/venv-local}"
BIN_DIR="${CYBERWAVE_LOCAL_BIN_DIR:-${HOME}/.local/bin}"
CLI_BINARY="${VENV_DIR}/bin/cyberwave"
LAUNCHER_PATH="${BIN_DIR}/cyberwave"
SDK_SOURCE="pypi"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

print_header() {
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║     Cyberwave CLI — local source installer (macOS)   ║"
  echo "╚══════════════════════════════════════════════════════╝"
  echo ""
}

ensure_path_persisted() {
  local dir="$1"

  case ":${PATH}:" in
    *:"${dir}":*) return 0 ;;
  esac

  local shell_profile=""
  case "${SHELL:-}" in
    */zsh)  shell_profile="${HOME}/.zshrc" ;;
    */bash) shell_profile="${HOME}/.bashrc" ;;
    *)      shell_profile="${HOME}/.profile" ;;
  esac

  if [ -n "$shell_profile" ] && ! grep -qF "$dir" "$shell_profile" 2>/dev/null; then
    printf '\n# Added by Cyberwave local CLI installer\nexport PATH="%s:$PATH"\n' "$dir" >> "$shell_profile"
    echo "  → Updated ${shell_profile} to include ${dir}"
    echo "    Restart your terminal or run:  source ${shell_profile}"
  fi

  export PATH="${dir}:${PATH}"
}

print_usage() {
  cat <<EOF
Usage: ./scripts/install-local-cli-mac.sh [--sdk-source pypi|local]

Options:
  --sdk-source pypi|local   Install the SDK from PyPI (default) or from the local repo.
  -h, --help                Show this help message.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --sdk-source)
      if [ "$#" -lt 2 ]; then
        echo "ERROR: --sdk-source requires a value of 'pypi' or 'local'." >&2
        print_usage >&2
        exit 1
      fi
      SDK_SOURCE="$2"
      shift 2
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      print_usage >&2
      exit 1
      ;;
  esac
done

if [ "${SDK_SOURCE}" != "pypi" ] && [ "${SDK_SOURCE}" != "local" ]; then
  echo "ERROR: --sdk-source must be 'pypi' or 'local'." >&2
  print_usage >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

print_header

if [ "$(uname -s)" != "Darwin" ]; then
  echo "ERROR: This script is for macOS only." >&2
  exit 1
fi

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  echo "ERROR: Do not run this script with sudo — it installs a user-scoped venv." >&2
  exit 1
fi

if [ ! -f "${CLI_SRC_DIR}/pyproject.toml" ]; then
  echo "ERROR: Could not find pyproject.toml at ${CLI_SRC_DIR}." >&2
  echo "       Run this script from inside the cyberwave-python-cli directory tree." >&2
  exit 1
fi

if [ "${SDK_SOURCE}" = "local" ] && [ ! -f "${SDK_SRC_DIR}/pyproject.toml" ]; then
  echo "ERROR: Local SDK source not found at ${SDK_SRC_DIR}." >&2
  exit 1
fi

# Find a Python interpreter that satisfies >=3.10.
# Probe candidates in descending version order so we always pick the newest available.
PYTHON3=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    py_major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)
    py_minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)
    if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 10 ]; then
      PYTHON3="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON3" ]; then
  echo "ERROR: Python 3.10 or newer is required but was not found." >&2
  echo "" >&2
  echo "Install via Homebrew:" >&2
  echo "  brew install python" >&2
  echo "" >&2
  echo "Or with pyenv:" >&2
  echo "  pyenv install 3.12 && pyenv global 3.12" >&2
  exit 1
fi

if ! "$PYTHON3" -m venv --help >/dev/null 2>&1; then
  echo "ERROR: $PYTHON3 'venv' module not available." >&2
  echo "Install via Homebrew:  brew install python" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Create / reuse venv
# ---------------------------------------------------------------------------

PYTHON3_VERSION="$("$PYTHON3" --version 2>&1)"
echo "CLI source:        ${CLI_SRC_DIR}"
echo "SDK source:        ${SDK_SOURCE}"
if [ "${SDK_SOURCE}" = "local" ]; then
  echo "SDK repo:          ${SDK_SRC_DIR}"
fi
echo "Cloud node source: ${CLOUD_NODE_SRC_DIR}"
echo "Python:            ${PYTHON3} (${PYTHON3_VERSION})"
echo "Venv:              ${VENV_DIR}"
echo "Binary:            ${LAUNCHER_PATH}"
echo ""

_need_new_venv=1
if [ -d "${VENV_DIR}" ] && [ -x "${VENV_DIR}/bin/python" ]; then
  venv_major=$("${VENV_DIR}/bin/python" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)
  venv_minor=$("${VENV_DIR}/bin/python" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)
  if [ "$venv_major" -ge 3 ] && [ "$venv_minor" -ge 10 ]; then
    echo "Reusing existing venv at ${VENV_DIR} (Python ${venv_major}.${venv_minor}) ..."
    _need_new_venv=0
  else
    echo "Existing venv uses Python ${venv_major}.${venv_minor} which is too old — recreating ..."
    rm -rf "${VENV_DIR}"
  fi
fi

if [ "${_need_new_venv}" -eq 1 ]; then
  echo "Creating Python virtual environment ..."
  mkdir -p "$(dirname "${VENV_DIR}")"
  "$PYTHON3" -m venv "${VENV_DIR}"
fi

# Ensure pip is up to date inside the venv
if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
  "${VENV_DIR}/bin/python" -m ensurepip --upgrade
fi
echo "Upgrading pip ..."
"${VENV_DIR}/bin/python" -m pip install --quiet --upgrade pip

# ---------------------------------------------------------------------------
# Editable installs of local sources
# ---------------------------------------------------------------------------

if [ "${SDK_SOURCE}" = "local" ]; then
  echo "Installing local cyberwave SDK in editable mode ..."
  "${VENV_DIR}/bin/python" -m pip install --quiet -e "${SDK_SRC_DIR}"
fi

echo "Installing local cyberwave-cli in editable mode ..."
"${VENV_DIR}/bin/python" -m pip install --quiet -e "${CLI_SRC_DIR}"

# Pre-install the local cloud node so that `cyberwave compute install` finds
# it already satisfied and skips the PyPI lookup (which would fail since the
# package is not publicly published).
if [ -f "${CLOUD_NODE_SRC_DIR}/pyproject.toml" ]; then
  echo "Installing local cyberwave-cloud-node in editable mode ..."
  "${VENV_DIR}/bin/python" -m pip install --quiet -e "${CLOUD_NODE_SRC_DIR}"
else
  echo "WARNING: cloud node source not found at ${CLOUD_NODE_SRC_DIR} — skipping." >&2
  echo "         'cyberwave compute install' may fail if the package is not on PyPI." >&2
fi

# ---------------------------------------------------------------------------
# Verify the binary was created
# ---------------------------------------------------------------------------

if [ ! -x "${CLI_BINARY}" ]; then
  echo "ERROR: Installation completed but '${CLI_BINARY}' was not found." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Symlink into $BIN_DIR
# ---------------------------------------------------------------------------

mkdir -p "${BIN_DIR}"
ln -sfn "${CLI_BINARY}" "${LAUNCHER_PATH}"
echo "Symlinked:  ${LAUNCHER_PATH} → ${CLI_BINARY}"

ensure_path_persisted "${BIN_DIR}"

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

echo ""
echo "Verifying installation ..."
INSTALLED_VERSION="$("${CLI_BINARY}" --version 2>&1 || true)"
echo "  cyberwave ${INSTALLED_VERSION}"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "✓ Local cyberwave CLI installed successfully."
echo ""
echo "Next steps:"
echo "  cyberwave --help"
echo "  cyberwave compute install      # install cloud node as a macOS LaunchAgent"
echo ""
echo "To update after source changes, simply rerun this script."
echo ""
