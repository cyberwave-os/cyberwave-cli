"""Configuration and constants for the Cyberwave CLI."""

import os
import sys
from pathlib import Path

# API endpoints
DEFAULT_API_URL = "https://api.cyberwave.com"
AUTH_LOGIN_ENDPOINT = "/dj-rest-auth/login/"
AUTH_USER_ENDPOINT = "/dj-rest-auth/user/"
API_TOKENS_ENDPOINT = "/api-tokens/"
WORKSPACES_ENDPOINT = "/api/v1/users/workspaces"

# Config directory – system-wide location shared by the CLI, edge-core service,
# and driver containers.  /etc/cyberwave is the FHS-standard path for
# system service configuration.  The env var allows override (e.g. in tests).
CONFIG_DIR = Path(os.getenv("CYBERWAVE_EDGE_CONFIG_DIR", "/etc/cyberwave"))
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"

# SO-101 starter template
SO101_REPO_URL = "https://github.com/cyberwave-os/so101-starter"
SO101_DEFAULT_DIR = "so101-project"

# Camera edge software
CAMERA_EDGE_REPO_URL = "https://github.com/cyberwave-os/cyberwave-edge-python.git"
CAMERA_EDGE_DEFAULT_DIR = "cyberwave-camera"

# API endpoints for resources
ENVIRONMENTS_ENDPOINT = "/api/v1/environments"
TWINS_ENDPOINT = "/api/v1/twins"
ASSETS_ENDPOINT = "/api/v1/assets"


def get_api_url() -> str:
    """Get the API URL from environment or default."""
    return os.getenv("CYBERWAVE_API_URL", DEFAULT_API_URL)


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
