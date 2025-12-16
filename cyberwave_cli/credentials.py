"""Credentials management for the Cyberwave CLI."""

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
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

    def to_dict(self) -> dict:
        """Convert credentials to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Credentials":
        """Create credentials from dictionary."""
        return cls(
            token=data.get("token", ""),
            email=data.get("email"),
            created_at=data.get("created_at"),
            workspace_uuid=data.get("workspace_uuid"),
            workspace_name=data.get("workspace_name"),
        )


def ensure_config_dir() -> None:
    """Ensure the config directory exists with proper permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Set directory permissions to user-only on Unix systems
    if os.name != "nt":
        os.chmod(CONFIG_DIR, 0o700)


def save_credentials(credentials: Credentials) -> None:
    """Save credentials to the config file."""
    ensure_config_dir()

    # Add timestamp if not present
    if not credentials.created_at:
        credentials.created_at = datetime.utcnow().isoformat()

    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(credentials.to_dict(), f, indent=2)

    # Set file permissions to user-only on Unix systems
    if os.name != "nt":
        os.chmod(CREDENTIALS_FILE, 0o600)


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
