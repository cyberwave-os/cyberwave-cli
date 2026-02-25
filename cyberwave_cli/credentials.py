"""Credentials management for the Cyberwave CLI."""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

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
    cyberwave_base_url: Optional[str] = None

    def runtime_envs(self) -> dict[str, str]:
        """Return persisted runtime env vars for edge/core processes."""
        envs: dict[str, str] = {}
        if self.cyberwave_environment:
            envs["CYBERWAVE_ENVIRONMENT"] = self.cyberwave_environment
        if self.cyberwave_edge_log_level:
            envs["CYBERWAVE_EDGE_LOG_LEVEL"] = self.cyberwave_edge_log_level
        if self.cyberwave_base_url:
            envs["CYBERWAVE_BASE_URL"] = self.cyberwave_base_url
        return envs

    def to_dict(self) -> dict[str, Any]:
        """Convert credentials to dictionary."""
        payload: dict[str, Any] = {
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
        envs = self.runtime_envs()
        if envs:
            payload["envs"] = envs
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "Credentials":
        """Create credentials from dictionary."""
        raw_envs = data.get("envs")
        envs: dict[str, Any] = raw_envs if isinstance(raw_envs, dict) else {}

        def _env_value(key: str) -> Optional[str]:
            value = envs.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            # Backward compatibility with old flat credentials schema.
            flat_value = data.get(key)
            if isinstance(flat_value, str) and flat_value.strip():
                return flat_value.strip()
            return None

        return cls(
            token=data.get("token", ""),
            email=data.get("email"),
            created_at=data.get("created_at"),
            workspace_uuid=data.get("workspace_uuid"),
            workspace_name=data.get("workspace_name"),
            cyberwave_environment=_env_value("CYBERWAVE_ENVIRONMENT"),
            cyberwave_edge_log_level=_env_value("CYBERWAVE_EDGE_LOG_LEVEL"),
            cyberwave_base_url=_env_value("CYBERWAVE_BASE_URL"),
        )


def collect_runtime_env_overrides(*, api_url_override: Optional[str] = None) -> dict[str, str]:
    """Collect Cyberwave environment overrides from the current process."""
    overrides: dict[str, str] = {}
    for key in (
        "CYBERWAVE_ENVIRONMENT",
        "CYBERWAVE_EDGE_LOG_LEVEL",
        "CYBERWAVE_BASE_URL",
    ):
        value = os.getenv(key)
        if isinstance(value, str) and value.strip():
            overrides[key] = value.strip()

    # In non-production explicit environments, default edge-core to verbose logs.
    env_name = overrides.get("CYBERWAVE_ENVIRONMENT", "").strip().lower()
    if env_name and env_name != "production":
        overrides.setdefault("CYBERWAVE_EDGE_LOG_LEVEL", "debug")
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
    existing_envs = existing_payload.get("envs")
    payload_envs = payload.get("envs")
    if isinstance(existing_envs, dict) or isinstance(payload_envs, dict):
        merged_payload["envs"] = {
            **(existing_envs if isinstance(existing_envs, dict) else {}),
            **(payload_envs if isinstance(payload_envs, dict) else {}),
        }
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
