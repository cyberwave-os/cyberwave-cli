"""Configuration and constants for the Cyberwave CLI."""

import os
import sys
from pathlib import Path

from cyberwave.config import DEFAULT_BASE_URL

# Config directory – system-wide location shared by the CLI, edge-core service,
# and driver containers.  /etc/cyberwave is the FHS-standard path for
# system service configuration.  Falls back to ~/.cyberwave when the user
# doesn't have write access to /etc (e.g. non-root, CI runners).
# The env var CYBERWAVE_EDGE_CONFIG_DIR allows explicit override.
_SYSTEM_CONFIG_DIR = Path("/etc/cyberwave")
_USER_CONFIG_DIR = Path.home() / ".cyberwave"


def _resolve_config_dir() -> Path:
    """Pick the best writable config directory.

    Priority:
      1. ``CYBERWAVE_EDGE_CONFIG_DIR`` env var (explicit override)
      2. ``/etc/cyberwave`` if it exists and is writable, or can be created
      3. ``~/.cyberwave`` as a fallback for non-root users
    """
    env_override = os.getenv("CYBERWAVE_EDGE_CONFIG_DIR")
    if env_override:
        return Path(env_override)

    # Prefer /etc/cyberwave if we can write to it
    if _SYSTEM_CONFIG_DIR.exists() and os.access(_SYSTEM_CONFIG_DIR, os.W_OK):
        return _SYSTEM_CONFIG_DIR

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

    Checks ``CYBERWAVE_API_URL`` first (CLI convention), then falls back to
    ``CYBERWAVE_BASE_URL`` (SDK convention), and finally to the SDK's
    ``DEFAULT_BASE_URL``.
    """
    return os.getenv("CYBERWAVE_API_URL", os.getenv("CYBERWAVE_BASE_URL", DEFAULT_BASE_URL))


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
