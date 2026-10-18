#!/usr/bin/env bash
# Run `cyberwave update` from the staged CLI .deb (the real PyInstaller binary,
# so the "frozen, installed by apt" path is the one exercised) against a local
# apt repository holding the same deb repacked as stable 1.0.0 and 1.0.1.
#
# Checks, each against the installed version and the binary still working:
# --check, an update, an update with nothing new, a rollback with --version,
# a held package without and with --force, and a non-root run.
#
# Meant to run as root inside a throwaway ubuntu:22.04 container:
#   docker run --rm -e PACKAGE_FILE=/workspace/path/to/cyberwave-cli*.deb \
#     -v "$PWD:/workspace:ro" -w /workspace ubuntu:22.04 \
#     /bin/bash ./scripts/test_cli_update_deb.sh

set -euo pipefail

PACKAGE_FILE="${PACKAGE_FILE:-}"
[ -n "${PACKAGE_FILE}" ] || { echo "ERROR: PACKAGE_FILE is required." >&2; exit 1; }
[ -f "${PACKAGE_FILE}" ] || { echo "ERROR: package file not found: ${PACKAGE_FILE}" >&2; exit 1; }

STABLE_PACKAGE=cyberwave-cli
REPO_DIR=/srv/cyberwave-ci-apt
CLI=/usr/bin/cyberwave

fail() {
    echo "❌ $*" >&2
    exit 1
}

assert_version() {
    local installed
    installed="$(dpkg-query -W -f='${Version}' "${STABLE_PACKAGE}")"
    [ "${installed}" = "$1" ] || fail "installed version is ${installed}, expected $1 ($2)"
}

assert_log() {
    grep -qF "$2" "$1" || fail "$(basename "$1") lacks: $2"
}

# Run the CLI with its output in /tmp/<name>.log; returns the CLI's exit code.
cli() {
    local name="$1" rc
    shift
    echo "\$ cyberwave $*"
    # Wide enough that rich never wraps a line the checks grep for.
    COLUMNS=200 "${CLI}" "$@" >"/tmp/${name}.log" 2>&1 && rc=0 || rc=$?
    cat "/tmp/${name}.log"
    return "${rc}"
}

# Repack the staged deb as the stable package at version $1. The staged deb's
# Conflicts/Replaces name the stable package, so they are dropped.
repack_as_stable() {
    local work="/tmp/repack-$1"
    rm -rf "${work}"
    dpkg-deb -R "${PACKAGE_FILE}" "${work}"
    sed -i \
        -e "s/^Package: .*/Package: ${STABLE_PACKAGE}/" \
        -e "s/^Version: .*/Version: $1/" \
        -e '/^Conflicts:/d' -e '/^Replaces:/d' \
        "${work}/DEBIAN/control"
    # Uncompressed: this deb only travels to the local repository.
    dpkg-deb -Znone --build "${work}" "${REPO_DIR}/${STABLE_PACKAGE}_$1.deb" >/dev/null
    rm -rf "${work}"
}

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends apt-utils

staged_package="$(dpkg-deb -f "${PACKAGE_FILE}" Package)"
echo "=== Staged package: ${staged_package} $(dpkg-deb -f "${PACKAGE_FILE}" Version) ==="

if [ "${staged_package}" = "${STABLE_PACKAGE}-pr" ]; then
    echo "=== A PR build needs --version ==="
    dpkg -i "${PACKAGE_FILE}"
    if cli update-pr update; then
        fail "update of a PR build without --version succeeded"
    fi
    assert_log /tmp/update-pr.log "PR builds are not ordered against each other"
    echo "✅ a PR build without --version is refused"
fi

echo "=== Serving ${STABLE_PACKAGE} 1.0.0 and 1.0.1 from ${REPO_DIR} ==="
dpkg --purge "${staged_package}" 2>/dev/null || true
mkdir -p "${REPO_DIR}"
repack_as_stable 1.0.0
repack_as_stable 1.0.1
(
    cd "${REPO_DIR}"
    apt-ftparchive packages . >Packages
    # Written outside the repository first, so it does not hash itself half-written.
    apt-ftparchive release . >/tmp/Release
    mv /tmp/Release Release
)
echo "deb [trusted=yes] file:${REPO_DIR} ./" >/etc/apt/sources.list.d/cyberwave-ci.list
# Keep apt off the Ubuntu mirrors: every package the test needs is installed.
mv /etc/apt/sources.list /etc/apt/sources.list.ci-disabled
apt-get update -qq
dpkg -i "${REPO_DIR}/${STABLE_PACKAGE}_1.0.0.deb"
assert_version 1.0.0 "start"

echo "=== --check changes nothing ==="
cli check update --check || fail "--check failed"
assert_log /tmp/check.log "Installed: 1.0.0"
assert_log /tmp/check.log "Available: 1.0.1"
assert_log /tmp/check.log "An update is available"
assert_version 1.0.0 "--check"
echo "✅ --check reports 1.0.1 and changes nothing"

echo "=== Updating to the latest version ==="
cli update update || fail "the update failed"
assert_log /tmp/update.log "Updated ${STABLE_PACKAGE}: 1.0.0 → 1.0.1"
assert_version 1.0.1 "update"
"${CLI}" __selfcheck_sdk || fail "the updated binary fails its SDK self-check"
echo "✅ update installs 1.0.1 and the new binary works"

echo "=== Updating again with nothing new ==="
cli update-noop update || fail "the second update failed"
assert_log /tmp/update-noop.log "already at 1.0.1"
assert_version 1.0.1 "second update"
echo "✅ update with nothing new changes nothing"

echo "=== Rolling back with --version ==="
cli rollback update --version 1.0.0 || fail "the rollback failed"
assert_log /tmp/rollback.log "Updated ${STABLE_PACKAGE}: 1.0.1 → 1.0.0"
assert_version 1.0.0 "rollback"
echo "✅ --version 1.0.0 downgrades"

echo "=== A held package ==="
apt-mark hold "${STABLE_PACKAGE}"
if cli update-held update; then
    fail "update of a held package without --force succeeded"
fi
assert_log /tmp/update-held.log "is held (apt-mark hold)"
assert_version 1.0.0 "held"
cli update-force update --force || fail "the forced update failed"
assert_version 1.0.1 "--force"
apt-mark unhold "${STABLE_PACKAGE}"
echo "✅ a held package is refused, and updated with --force"

echo "=== Not as root ==="
apt-get install -y -qq --no-install-recommends util-linux >/dev/null
if setpriv --reuid=65534 --regid=65534 --clear-groups env HOME=/tmp COLUMNS=200 \
    "${CLI}" update >/tmp/update-nonroot.log 2>&1; then
    cat /tmp/update-nonroot.log
    fail "update without root succeeded"
fi
cat /tmp/update-nonroot.log
assert_log /tmp/update-nonroot.log "requires root privileges"
assert_version 1.0.1 "non-root"
echo "✅ update without root is refused"

echo "✅ cyberwave update works from the staged Debian package"
