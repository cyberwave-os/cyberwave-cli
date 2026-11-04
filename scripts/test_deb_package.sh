#!/bin/sh

set -eu

DEVICE_NAME="${DEVICE_NAME:-unknown-device}"
DEVICE_DISTRO="${DEVICE_DISTRO:-unknown-distro}"
PACKAGE_FILE="${PACKAGE_FILE:-}"

if [ -z "${PACKAGE_FILE}" ]; then
    echo "ERROR: PACKAGE_FILE environment variable is required."
    exit 1
fi

if [ ! -f "${PACKAGE_FILE}" ]; then
    echo "ERROR: package file not found: ${PACKAGE_FILE}"
    exit 1
fi

echo "=== Emulated package install test: ${DEVICE_NAME} (${DEVICE_DISTRO}) ==="
echo "Using prebuilt package: ${PACKAGE_FILE}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates

dpkg -i "${PACKAGE_FILE}"

echo "=== Verifying installed binary ==="
/usr/bin/cyberwave --help
/usr/bin/cyberwave __selfcheck_sdk
/usr/bin/cyberwave __selfcheck_tls
/usr/bin/cyberwave login --help
/usr/bin/cyberwave camera --help
echo "Package install works on ${DEVICE_NAME} (${DEVICE_DISTRO})"

echo "=== Verifying the Docker credential helper ==="
# Docker resolves credHelpers by executable name; the .deb ships it as a symlink.
test -L /usr/bin/docker-credential-cyberwave
printf 'registry.cyberwave.com' | CYBERWAVE_API_KEY=cw_test /usr/bin/docker-credential-cyberwave get \
    | grep -F '"Secret": "cw_test"'
echo "Docker credential helper works on ${DEVICE_NAME} (${DEVICE_DISTRO})"
