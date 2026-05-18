"""Configuration and constants for the Cyberwave CLI."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Config directory shared by CLI, edge-core service, and driver containers.
# All platforms resolve to ``~/.cyberwave`` under the invoking user's home
# (even when running via ``sudo``).
# ``CYBERWAVE_EDGE_CONFIG_DIR`` always overrides the default.
#
# Legacy: Linux previously used ``/etc/cyberwave``.  A migration helper in
# ``core.py`` copies config from there on first install after the change.
LEGACY_SYSTEM_CONFIG_DIR = Path("/etc/cyberwave")


def _resolve_sudo_user_home() -> Path | None:
    """Return invoking user's home when running via sudo (best effort)."""
    sudo_user = os.getenv("SUDO_USER", "").strip()
    if not sudo_user:
        return None

    try:
        import pwd

        home = pwd.getpwnam(sudo_user).pw_dir
    except Exception:
        return None
    if not home:
        return None
    return Path(home)


def _resolve_config_dir() -> Path:
    """Pick the config directory.

    Priority:
      1. ``CYBERWAVE_EDGE_CONFIG_DIR`` env var (explicit override)
      2. ``~/.cyberwave`` under the invoking user's home (all platforms)

    When running via ``sudo``, the invoking user's home is resolved from
    ``SUDO_USER`` so that both regular and sudo invocations converge to the
    same directory.
    """
    env_override = os.getenv("CYBERWAVE_EDGE_CONFIG_DIR")
    if env_override:
        return Path(env_override)

    sudo_home = _resolve_sudo_user_home()
    base_home = sudo_home or Path.home()
    return base_home / ".cyberwave"


CONFIG_DIR = _resolve_config_dir()
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"


def chown_to_sudo_user(*paths: "os.PathLike[str]") -> None:
    """Best-effort chown to the invoking (non-root) user when running via sudo.

    The config dir lives under the user's home.  Files created by
    ``sudo cyberwave …`` end up owned by root, which locks out subsequent
    non-sudo invocations.  Restoring ownership avoids the "permission denied /
    re-run with sudo" loop.
    """
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if not sudo_uid:
        return
    try:
        uid = int(sudo_uid)
        gid = int(sudo_gid) if sudo_gid else -1
    except (ValueError, TypeError):
        return
    for p in paths:
        try:
            os.chown(p, uid, gid)
        except OSError:
            pass


# SO-101 starter template
SO101_REPO_URL = "https://github.com/cyberwave-os/so101-starter"
SO101_DEFAULT_DIR = "so101-project"

# Camera edge software
CAMERA_EDGE_REPO_URL = "https://github.com/cyberwave-os/cyberwave-edge-python.git"
CAMERA_EDGE_DEFAULT_DIR = "cyberwave-camera"


def get_api_url() -> str:
    """Get the API URL from environment or default.

    Checks ``CYBERWAVE_BASE_URL`` first, and finally to the SDK's
    ``DEFAULT_BASE_URL``.
    """
    # Imported here (not at module level) to avoid triggering the full
    # cyberwave SDK init, which transitively imports numpy and adds ~2 s of
    # startup latency even for commands like `--help` that never touch the API.
    from cyberwave.config import DEFAULT_BASE_URL  # noqa: PLC0415

    return os.getenv("CYBERWAVE_BASE_URL", DEFAULT_BASE_URL)


_EDGE_CORE_DEB_PYTHON_PATH = "/usr/lib/cyberwave-edge-core/python"


def ensure_edge_core_importable() -> None:
    """Make ``cyberwave_edge_core`` importable when installed via deb.

    The deb package ships the Python source tree under
    ``/usr/lib/cyberwave-edge-core/python/cyberwave_edge_core/``.
    This path is not on ``sys.path`` by default, so we add it once.
    """
    if _EDGE_CORE_DEB_PYTHON_PATH not in sys.path:
        if os.path.isdir(
            os.path.join(_EDGE_CORE_DEB_PYTHON_PATH, "cyberwave_edge_core")
        ):
            sys.path.insert(0, _EDGE_CORE_DEB_PYTHON_PATH)


def clean_subprocess_env() -> dict[str, str]:
    """Return a copy of os.environ safe for child processes.

    When the CLI is packaged with PyInstaller, ``LD_LIBRARY_PATH`` is set to the
    temp extraction directory so bundled Python can find its libraries.  Child
    processes (curl, gpg, git, apt-get, systemctl …) that inherit this will load
    the wrong shared libraries.

    Strategy (belt-and-suspenders):
      1. If PyInstaller saved the original value in ``LD_LIBRARY_PATH_ORIG``,
         restore it.
      2. Otherwise, if we detect a PyInstaller bundle (``sys._MEIPASS``),
         strip that path from ``LD_LIBRARY_PATH`` directly.
    """
    env = os.environ.copy()

    # Approach 1: restore the original LD_LIBRARY_PATH saved by PyInstaller.
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        if orig:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
        return env

    # Approach 2: manually strip the PyInstaller extraction dir.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        ld_path = env.get("LD_LIBRARY_PATH", "")
        if ld_path:
            cleaned = [p for p in ld_path.split(os.pathsep) if p != meipass]
            if cleaned:
                env["LD_LIBRARY_PATH"] = os.pathsep.join(cleaned)
            else:
                env.pop("LD_LIBRARY_PATH", None)

    return env
