"""Credentials management for the Cyberwave CLI."""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .config import CONFIG_DIR, CREDENTIALS_FILE


@dataclass
class Credentials:
    """User credentials for the Cyberwave API."""

    token: str
    email: Optional[str] = None
    created_at: Optional[str] = None
    workspace_uuid: Optional[str] = None
    workspace_name: Optional[str] = None
    cyberwave_environment: Optional[str] = None
    cyberwave_edge_log_level: Optional[str] = None
    cyberwave_api_url: Optional[str] = None
    cyberwave_base_url: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert credentials to dictionary."""
        payload: dict[str, str] = {
            "token": self.token,
        }
        if self.email:
            payload["email"] = self.email
        if self.created_at:
            payload["created_at"] = self.created_at
        if self.workspace_uuid:
            payload["workspace_uuid"] = self.workspace_uuid
        if self.workspace_name:
            payload["workspace_name"] = self.workspace_name
        if self.cyberwave_environment:
            payload["CYBERWAVE_ENVIRONMENT"] = self.cyberwave_environment
        if self.cyberwave_edge_log_level:
            payload["CYBERWAVE_EDGE_LOG_LEVEL"] = self.cyberwave_edge_log_level
        if self.cyberwave_api_url:
            payload["CYBERWAVE_API_URL"] = self.cyberwave_api_url
        if self.cyberwave_base_url:
            payload["CYBERWAVE_BASE_URL"] = self.cyberwave_base_url
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "Credentials":
        """Create credentials from dictionary."""
        return cls(
            token=data.get("token", ""),
            email=data.get("email"),
            created_at=data.get("created_at"),
            workspace_uuid=data.get("workspace_uuid"),
            workspace_name=data.get("workspace_name"),
            cyberwave_environment=data.get("CYBERWAVE_ENVIRONMENT"),
            cyberwave_edge_log_level=data.get("CYBERWAVE_EDGE_LOG_LEVEL"),
            cyberwave_api_url=data.get("CYBERWAVE_API_URL"),
            cyberwave_base_url=data.get("CYBERWAVE_BASE_URL"),
        )


def collect_runtime_env_overrides(*, api_url_override: Optional[str] = None) -> dict[str, str]:
    """Collect Cyberwave environment overrides from the current process."""
    overrides: dict[str, str] = {}
    for key in (
        "CYBERWAVE_ENVIRONMENT",
        "CYBERWAVE_EDGE_LOG_LEVEL",
        "CYBERWAVE_API_URL",
        "CYBERWAVE_BASE_URL",
    ):
        value = os.getenv(key)
        if isinstance(value, str) and value.strip():
            overrides[key] = value.strip()

    # In non-production explicit environments, default edge-core to verbose logs.
    env_name = overrides.get("CYBERWAVE_ENVIRONMENT", "").strip().lower()
    if env_name and env_name != "production":
        overrides.setdefault("CYBERWAVE_EDGE_LOG_LEVEL", "debug")

    if api_url_override:
        api_url = api_url_override.strip()
        if api_url:
            overrides["CYBERWAVE_API_URL"] = api_url
            # Keep SDK-compatible alias in sync when caller explicitly sets API URL.
            overrides.setdefault("CYBERWAVE_BASE_URL", api_url)
    return overrides


def ensure_config_dir() -> None:
    """Ensure the config directory exists with proper permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Best-effort: restrict to owner only.  May fail if the directory is
    # owned by another user (e.g. root-created /etc/cyberwave on a CI runner).
    if os.name != "nt":
        try:
            os.chmod(CONFIG_DIR, 0o700)
        except PermissionError:
            pass


def save_credentials(credentials: Credentials) -> None:
    """Save credentials to the config file."""
    ensure_config_dir()

    # Add timestamp if not present
    if not credentials.created_at:
        credentials.created_at = datetime.utcnow().isoformat()

    payload = credentials.to_dict()
    existing_payload: dict = {}
    if CREDENTIALS_FILE.exists():
        try:
            with open(CREDENTIALS_FILE, "r") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing_payload = loaded
        except (json.JSONDecodeError, OSError):
            existing_payload = {}

    merged_payload = {**existing_payload, **payload}
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(merged_payload, f, indent=2)

    # Best-effort permission restriction.
    if os.name != "nt":
        try:
            os.chmod(CREDENTIALS_FILE, 0o600)
        except PermissionError:
            pass


def load_credentials() -> Optional[Credentials]:
    """Load credentials from the config file."""
    if not CREDENTIALS_FILE.exists():
        return None

    try:
        with open(CREDENTIALS_FILE, "r") as f:
            data = json.load(f)
            return Credentials.from_dict(data)
    except (json.JSONDecodeError, KeyError):
        return None


def clear_credentials() -> None:
    """Remove stored credentials."""
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()


def get_token() -> Optional[str]:
    """Get the stored token, if any."""
    creds = load_credentials()
    return creds.token if creds else None
