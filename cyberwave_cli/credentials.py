"""Credentials management for the Cyberwave CLI."""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import click
from rich.console import Console

from .config import CONFIG_DIR, CREDENTIALS_FILE, chown_to_sudo_user
from .io_utils import atomic_write_json

_console = Console()


def _raise_permission_error() -> None:
    """Print a colored permission-denied message and exit."""
    try:
        ctx: click.Context | None = click.get_current_context()
        parts: list[str] = []
        while ctx is not None:
            name = "cyberwave" if ctx.parent is None else ctx.info_name
            if name:
                parts.append(name)
            ctx = ctx.parent
        parts.reverse()
        cmd = " ".join(parts)
    except RuntimeError:
        cmd = "cyberwave edge install"
    _console.print(f"[red]Root privileges required.[/red]\n[dim]Re-run with sudo: sudo {cmd}[/dim]")
    raise SystemExit(1)


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
    cyberwave_mqtt_host: Optional[str] = None
    cyberwave_mqtt_port: Optional[str] = None
    internal_deb_read_token: Optional[str] = None
    internal_python_read_token: Optional[str] = None

    def runtime_envs(self) -> dict[str, str]:
        """Return persisted runtime env vars for edge/core processes."""
        envs: dict[str, str] = {}
        if self.cyberwave_environment:
            envs["CYBERWAVE_ENVIRONMENT"] = self.cyberwave_environment
        if self.cyberwave_edge_log_level:
            envs["CYBERWAVE_EDGE_LOG_LEVEL"] = self.cyberwave_edge_log_level
        if self.cyberwave_base_url:
            envs["CYBERWAVE_BASE_URL"] = self.cyberwave_base_url
        if self.cyberwave_mqtt_host:
            envs["CYBERWAVE_MQTT_HOST"] = self.cyberwave_mqtt_host
        if self.cyberwave_mqtt_port:
            envs["CYBERWAVE_MQTT_PORT"] = self.cyberwave_mqtt_port
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
        package_registry_tokens: dict[str, str] = {}
        if self.internal_deb_read_token:
            package_registry_tokens["internal_deb_read_token"] = self.internal_deb_read_token
        if self.internal_python_read_token:
            package_registry_tokens["internal_python_read_token"] = self.internal_python_read_token
        if package_registry_tokens:
            payload["package_registry_tokens"] = package_registry_tokens
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "Credentials":
        """Create credentials from dictionary."""
        raw_envs = data.get("envs")
        envs: dict[str, Any] = raw_envs if isinstance(raw_envs, dict) else {}
        raw_package_registry_tokens = data.get("package_registry_tokens")
        package_registry_tokens: dict[str, Any] = (
            raw_package_registry_tokens if isinstance(raw_package_registry_tokens, dict) else {}
        )

        def _env_value(key: str) -> Optional[str]:
            value = envs.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            # Backward compatibility with old flat credentials schema.
            flat_value = data.get(key)
            if isinstance(flat_value, str) and flat_value.strip():
                return flat_value.strip()
            return None

        def _package_registry_token(key: str) -> Optional[str]:
            value = package_registry_tokens.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
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
            cyberwave_mqtt_host=_env_value("CYBERWAVE_MQTT_HOST"),
            cyberwave_mqtt_port=_env_value("CYBERWAVE_MQTT_PORT"),
            internal_deb_read_token=_package_registry_token("internal_deb_read_token"),
            internal_python_read_token=_package_registry_token("internal_python_read_token"),
        )


def _infer_env_from_base_url(base_url: str) -> dict[str, str]:
    """Derive CYBERWAVE_ENVIRONMENT, MQTT_HOST, MQTT_PORT and MQTT_USE_TLS from a base URL.

    Mapping:
        https://api-dev.cyberwave.com   → dev,  dev.mqtt.cyberwave.com:8883, TLS
        https://api-staging.cyberwave.com → staging, staging.mqtt.cyberwave.com:8883, TLS
        https://api.cyberwave.com       → production, mqtt.cyberwave.com:8883, TLS
        http://localhost:*              → local, localhost:1883, no TLS
    """
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()

    inferred: dict[str, str] = {}

    if host in ("localhost", "127.0.0.1"):
        inferred["CYBERWAVE_ENVIRONMENT"] = "local"
        inferred["CYBERWAVE_MQTT_HOST"] = "localhost"
        inferred["CYBERWAVE_MQTT_PORT"] = "1883"
    elif host.endswith(".cyberwave.com"):
        prefix = host.removesuffix(".cyberwave.com")
        if prefix.startswith("api-"):
            env_name = prefix[4:]  # e.g. "dev", "staging"
            inferred["CYBERWAVE_ENVIRONMENT"] = env_name
            inferred["CYBERWAVE_MQTT_HOST"] = f"{env_name}.mqtt.cyberwave.com"
            inferred["CYBERWAVE_MQTT_PORT"] = "8883"
            inferred["CYBERWAVE_MQTT_USE_TLS"] = "true"
        else:
            inferred["CYBERWAVE_ENVIRONMENT"] = "production"
            inferred["CYBERWAVE_MQTT_HOST"] = "mqtt.cyberwave.com"
            inferred["CYBERWAVE_MQTT_PORT"] = "8883"
            inferred["CYBERWAVE_MQTT_USE_TLS"] = "true"

    return inferred


def collect_runtime_env_overrides(*, api_url_override: Optional[str] = None) -> dict[str, str]:
    """Collect Cyberwave environment overrides from the current process.

    Explicit env vars always win.  When ``CYBERWAVE_BASE_URL`` is known
    (either from the environment or *api_url_override*) but other vars are
    missing, they are inferred from the URL so that a single
    ``--base-url`` flag is enough to fully configure the CLI.
    """
    overrides: dict[str, str] = {}
    for key in (
        "CYBERWAVE_ENVIRONMENT",
        "CYBERWAVE_EDGE_LOG_LEVEL",
        "CYBERWAVE_BASE_URL",
        "CYBERWAVE_MQTT_HOST",
        "CYBERWAVE_MQTT_PORT",
        "CYBERWAVE_MQTT_USE_TLS",
    ):
        value = os.getenv(key)
        if isinstance(value, str) and value.strip():
            overrides[key] = value.strip()

    if api_url_override and api_url_override.strip():
        overrides["CYBERWAVE_BASE_URL"] = api_url_override.strip()

    base_url = overrides.get("CYBERWAVE_BASE_URL", "").strip()
    if base_url:
        for key, value in _infer_env_from_base_url(base_url).items():
            overrides.setdefault(key, value)

    # In non-production explicit environments, default edge-core to verbose logs.
    env_name = overrides.get("CYBERWAVE_ENVIRONMENT", "").strip().lower()
    if env_name and env_name != "production":
        overrides.setdefault("CYBERWAVE_EDGE_LOG_LEVEL", "debug")
    return overrides


def ensure_config_dir() -> None:
    """Ensure the config directory exists with proper permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(CONFIG_DIR, 0o700)
        except PermissionError:
            pass
        chown_to_sudo_user(CONFIG_DIR)


def save_credentials(credentials: Credentials) -> None:
    """Save credentials to the config file."""
    try:
        ensure_config_dir()
    except PermissionError:
        _raise_permission_error()

    # Add timestamp if not present
    if not credentials.created_at:
        credentials.created_at = datetime.utcnow().isoformat()

    payload = credentials.to_dict()
    existing_payload: dict = {}
    try:
        if CREDENTIALS_FILE.exists():
            try:
                with open(CREDENTIALS_FILE, "r") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing_payload = loaded
            except (json.JSONDecodeError, OSError):
                existing_payload = {}
    except PermissionError:
        _raise_permission_error()

    merged_payload = {**existing_payload, **payload}
    existing_envs = existing_payload.get("envs")
    payload_envs = payload.get("envs")
    if isinstance(existing_envs, dict) or isinstance(payload_envs, dict):
        merged_envs = {
            **(existing_envs if isinstance(existing_envs, dict) else {}),
            **(payload_envs if isinstance(payload_envs, dict) else {}),
        }
        merged_payload["envs"] = merged_envs
    try:
        atomic_write_json(CREDENTIALS_FILE, merged_payload)
    except PermissionError:
        _raise_permission_error()

    chown_to_sudo_user(CREDENTIALS_FILE)


def load_credentials() -> Optional[Credentials]:
    """Load credentials from the config file."""
    try:
        if not CREDENTIALS_FILE.exists():
            return None
    except PermissionError:
        _raise_permission_error()

    try:
        with open(CREDENTIALS_FILE, "r") as f:
            data = json.load(f)
            return Credentials.from_dict(data)
    except PermissionError:
        _raise_permission_error()
    except (json.JSONDecodeError, KeyError):
        return None


def clear_credentials() -> None:
    """Remove stored credentials."""
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()


def upsert_runtime_env(key: str, value: str) -> None:
    """Set a single runtime env var in credentials.json without a full save.

    Creates the ``envs`` dict if it doesn't exist yet.  Other fields
    (token, email, etc.) are preserved as-is.
    """
    ensure_config_dir()
    data: dict[str, Any] = {}
    try:
        if CREDENTIALS_FILE.exists():
            with open(CREDENTIALS_FILE) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
    except (json.JSONDecodeError, OSError):
        pass

    envs = data.get("envs")
    if not isinstance(envs, dict):
        envs = {}
    envs[key] = value
    data["envs"] = envs

    atomic_write_json(CREDENTIALS_FILE, data)


def get_token() -> Optional[str]:
    """Get the stored token, if any."""
    creds = load_credentials()
    return creds.token if creds else None
