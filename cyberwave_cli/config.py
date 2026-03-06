"""Configuration and constants for the Cyberwave CLI."""

import os
import platform
import sys
from pathlib import Path

# Config directory shared by CLI, edge-core service, and driver containers.
# - macOS defaults to ~/.cyberwave for Docker Desktop bind-mount compatibility
# - other platforms prefer /etc/cyberwave and fall back to ~/.cyberwave when
#   /etc is not writable
# CYBERWAVE_EDGE_CONFIG_DIR always overrides the default.
_SYSTEM_CONFIG_DIR = Path("/etc/cyberwave")
_USER_CONFIG_DIR = Path.home() / ".cyberwave"


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


def _resolve_macos_config_dir() -> Path:
    """Resolve a Docker Desktop-friendly config dir on macOS.

    On macOS, storing edge config under /etc can fail when Docker Desktop
    tries to bind-mount it into driver containers. Prefer the invoking user's
    home directory so both regular and sudo executions converge to the same
    path (e.g. /Users/alice/.cyberwave).
    """
    sudo_home = _resolve_sudo_user_home()
    base_home = sudo_home or Path.home()
    return base_home / ".cyberwave"


def _resolve_config_dir() -> Path:
    """Pick the best writable config directory.

    Priority:
      1. ``CYBERWAVE_EDGE_CONFIG_DIR`` env var (explicit override)
      2. On macOS: ``~/.cyberwave`` for Docker bind-mount compatibility
      3. On other platforms: ``/etc/cyberwave`` if writable/creatable
      4. ``~/.cyberwave`` as a fallback for non-root users
    """
    env_override = os.getenv("CYBERWAVE_EDGE_CONFIG_DIR")
    if env_override:
        return Path(env_override)

    if platform.system() == "Darwin":
        return _resolve_macos_config_dir()

    # Prefer /etc/cyberwave if we can write to it. If it already exists and is
    # not writable, fall back to user config immediately.
    if _SYSTEM_CONFIG_DIR.exists():
        if os.access(_SYSTEM_CONFIG_DIR, os.W_OK):
            return _SYSTEM_CONFIG_DIR
        return _USER_CONFIG_DIR

    # Try to create it (works when running as root)
    try:
        _SYSTEM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        return _SYSTEM_CONFIG_DIR
    except PermissionError:
        pass

    return _USER_CONFIG_DIR


CONFIG_DIR = _resolve_config_dir()
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"

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
