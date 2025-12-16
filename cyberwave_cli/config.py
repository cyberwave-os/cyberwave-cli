"""Configuration and constants for the Cyberwave CLI."""

import os
from pathlib import Path

# API endpoints
DEFAULT_API_URL = "https://api.cyberwave.com"
AUTH_LOGIN_ENDPOINT = "/dj-rest-auth/login/"
AUTH_USER_ENDPOINT = "/dj-rest-auth/user/"
API_TOKENS_ENDPOINT = "/api-tokens/"
WORKSPACES_ENDPOINT = "/api/v1/users/workspaces"

# Config directory
CONFIG_DIR = Path.home() / ".cyberwave"
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
