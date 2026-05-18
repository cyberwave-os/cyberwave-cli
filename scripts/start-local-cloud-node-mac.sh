#!/usr/bin/env bash
# Thin wrapper: run the canonical script under cyberwave-cloud-nodes/scripts/
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
exec "${REPO_ROOT}/cyberwave-cloud-nodes/scripts/start-local-cloud-node-mac.sh" "$@"
