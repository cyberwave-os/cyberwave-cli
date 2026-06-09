"""
Shared utilities for Cyberwave CLI commands.

This module provides common functionality used across multiple CLI commands:
- SDK client initialization
- Output formatting helpers
- Common CLI patterns
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import urlparse

import click
from rich.console import Console
from rich.table import Table

from .config import get_api_url
from .credentials import Credentials, load_credentials

console = Console()

T = TypeVar("T")


def resolve_api_url(
    api_url: Optional[str] = None,
    creds: Optional[Credentials] = None,
) -> str:
    """Resolve the backend API URL for CLI calls.

    Precedence is explicit command option, process environment, stored
    credentials, then the SDK default returned by ``get_api_url``.
    """
    if api_url:
        return api_url
    env_url = os.getenv("CYBERWAVE_BASE_URL")
    if env_url:
        return env_url
    if creds and creds.cyberwave_base_url:
        return creds.cyberwave_base_url
    return get_api_url()


def _is_local_or_private(hostname: str) -> bool:
    """Return True when *hostname* refers to a local or RFC-1918 private address."""
    if hostname in ("localhost", "0.0.0.0"):
        return True
    import ipaddress

    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False


def _infer_env_from_mqtt_host(mqtt_host: Optional[str]) -> Optional[str]:
    """Reverse-derive the environment name from the MQTT broker host.

    Mirrors :func:`cyberwave_cli.credentials._infer_env_from_base_url` but
    works in the opposite direction: given an MQTT broker host, return the
    environment family it belongs to so we can validate the resolved
    ``topic_prefix`` against it.

    ====================================  ==============
    host                                  environment
    ====================================  ==============
    ``mqtt.cyberwave.com``                ``production``
    ``dev.mqtt.cyberwave.com``            ``dev``
    ``staging.mqtt.cyberwave.com``        ``staging``
    ``localhost`` / RFC-1918 private IPs  ``local``
    ====================================  ==============

    Returns ``None`` when the host doesn't match any known pattern (custom
    brokers, on-prem deployments, …) so the consistency guard stays silent
    instead of yelling about every unknown host.
    """
    if not mqtt_host:
        return None
    host = mqtt_host.strip().lower()
    if not host:
        return None
    if host == "mqtt.cyberwave.com":
        return "production"
    if host.endswith(".mqtt.cyberwave.com"):
        return host.removesuffix(".mqtt.cyberwave.com")
    if _is_local_or_private(host):
        return "local"
    return None


def _normalize_topic_prefix(env_name: Optional[str]) -> Optional[str]:
    """Map an environment name to the SDK's ``topic_prefix`` value.

    Matches the rule in :class:`cyberwave.config.CyberwaveConfig`:
    ``production`` is normalized to an empty prefix; everything else is
    forwarded as-is.  ``None`` / blank means "let the SDK figure it out".
    """
    if env_name is None:
        return None
    name = env_name.strip().lower()
    if not name:
        return None
    if name == "production":
        return ""
    return name


def _derive_topic_prefix(
    creds: Optional[Credentials],
    mqtt_host: Optional[str],
) -> Optional[str]:
    """Derive the MQTT topic prefix the SDK should use.

    Resolution order mirrors :class:`cyberwave.config.CyberwaveConfig`:

    1. Explicit ``CYBERWAVE_MQTT_TOPIC_PREFIX`` env var — caller has
       opted out of auto-derivation.
    2. ``creds.cyberwave_environment`` from ``credentials.json`` — the
       same field ``cyberwave-edge-core`` consumes via
       ``load_credentials_envs()``, so the CLI lands on the same topic
       family edge-core listens on.
    3. Fallback: infer from the MQTT host (``dev.mqtt.cyberwave.com``
       implies ``dev``).  Catches the case where credentials were saved
       before ``cyberwave_environment`` was persisted but the broker
       host still tells us which environment we're talking to.

    Returns ``None`` when no prefix could be derived; callers should not
    forward the kwarg in that case (the SDK falls back to its own logic).
    """
    explicit = os.getenv("CYBERWAVE_MQTT_TOPIC_PREFIX")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    env_name: Optional[str] = None
    if creds and creds.cyberwave_environment:
        env_name = creds.cyberwave_environment
    if not env_name:
        env_name = _infer_env_from_mqtt_host(mqtt_host)

    return _normalize_topic_prefix(env_name)


def _validate_mqtt_topic_prefix_consistency(
    topic_prefix: Optional[str],
    mqtt_host: Optional[str],
) -> None:
    """Fail fast when the resolved prefix can't possibly match the broker.

    ``mqtt.publish_command_message`` is fire-and-forget at the protocol
    level: the broker happily ACKs publishes to topics nobody subscribed
    to.  When the CLI publishes to ``cyberwave/twin/.../command`` while
    edge-core is subscribed to ``devcyberwave/twin/.../command`` (or
    vice-versa) the message is silently dropped and the user sees a
    misleading "✓ Sent sync" line in their terminal.

    This guard cross-checks the prefix we resolved against what we would
    infer from the MQTT host and raises :class:`click.ClickException` if
    they disagree.  Unknown hosts (custom brokers, IP literals outside
    RFC-1918, …) are skipped — we only complain about combinations that
    are unambiguously wrong.
    """
    inferred_env = _infer_env_from_mqtt_host(mqtt_host)
    if inferred_env is None:
        return

    expected_prefix = _normalize_topic_prefix(inferred_env) or ""
    actual_prefix = topic_prefix or ""

    if actual_prefix == expected_prefix:
        return

    expected_label = expected_prefix or "<empty> (production)"
    actual_label = actual_prefix or "<empty> (production)"
    raise click.ClickException(
        "MQTT topic prefix doesn't match the broker host.\n\n"
        f"  broker host:       {mqtt_host}\n"
        f"  inferred env:      {inferred_env}\n"
        f"  expected prefix:   {expected_label!r}\n"
        f"  resolved prefix:   {actual_label!r}\n\n"
        "Published commands would land on a topic family no edge-core "
        "subscribes to and be silently dropped.\n"
        "Re-run `cyberwave login` (or set CYBERWAVE_ENVIRONMENT in your "
        "shell) so credentials match the broker, or override with "
        "CYBERWAVE_MQTT_TOPIC_PREFIX=<value> if you really mean it."
    )


def _resolve_mqtt_kwargs(
    creds: Optional[Credentials],
    base_url: str,
) -> dict[str, Any]:
    """Derive MQTT connection kwargs from credentials and base URL.

    When the backend base URL points at a local or private-network host
    (``localhost``, ``127.x``, ``192.168.x``, ``10.x``, …) the MQTT broker
    is assumed to be co-located on the same host at port 1883 without TLS.
    This matches the standard ``local.yml`` Docker Compose layout.

    For remote base URLs, the value stored in credentials
    (``cyberwave_mqtt_host``) is forwarded so the SDK connects to the right
    broker without requiring the ``CYBERWAVE_MQTT_HOST`` env var.

    The MQTT ``topic_prefix`` is resolved from credentials too — without
    it the CLI would publish to an un-prefixed topic (``cyberwave/...``)
    while edge-core subscribed under e.g. ``devcyberwave/...``, causing
    every command to be silently dropped.  The resolved prefix is then
    cross-checked against the broker host so an obvious env/host mismatch
    surfaces as a hard error instead of a misleading success line.

    Returns a dict suitable for passing as ``**kwargs`` to ``Cyberwave()``.
    """
    mqtt_host: Optional[str] = None
    mqtt_port: Optional[int] = None

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()

    if _is_local_or_private(hostname):
        mqtt_host = parsed.hostname  # preserve original casing / IP
        mqtt_port = 1883
    elif creds and creds.cyberwave_mqtt_host:
        mqtt_host = creds.cyberwave_mqtt_host

    topic_prefix = _derive_topic_prefix(creds, mqtt_host)
    _validate_mqtt_topic_prefix_consistency(topic_prefix, mqtt_host)

    kwargs: dict[str, Any] = {}
    if mqtt_host:
        kwargs["mqtt_host"] = mqtt_host
    if mqtt_port is not None:
        kwargs["mqtt_port"] = mqtt_port
    if topic_prefix is not None:
        kwargs["topic_prefix"] = topic_prefix
    return kwargs


def get_sdk_client(api_url: Optional[str] = None):
    """Get an authenticated Cyberwave SDK client.

    Args:
        api_url: Optional API URL override.  Falls back to
            ``CYBERWAVE_BASE_URL`` / SDK default when *None*.

    Returns:
        Cyberwave client if authenticated, None otherwise.

    The logged-in workspace from ``~/.cyberwave/credentials.json`` is passed as
    ``workspace_id`` so SDK helpers (e.g. quickstart environment reuse) target
    the same workspace as ``cyberwave login`` / ``cyberwave edge install``.

    Example:
        client = get_sdk_client()
        if client:
            twins = client.twins.list()
    """
    creds = load_credentials()
    if not creds or not creds.token:
        return None
    try:
        from cyberwave import Cyberwave

        base_url = resolve_api_url(api_url, creds)
        client_kwargs = _resolve_mqtt_kwargs(creds, base_url)
        if creds.workspace_uuid:
            client_kwargs["workspace_id"] = creds.workspace_uuid
        return Cyberwave(base_url=base_url, token=creds.token, **client_kwargs)
    except ImportError:
        return None


def require_client(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator that ensures SDK client is available.
    
    Automatically handles the common pattern of checking for login
    and SDK availability before running a command.
    
    Usage:
        @require_client
        def my_command(client):
            # client is guaranteed to be valid
            twins = client.twins.list()
    """
    import functools
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        client = get_sdk_client()
        if not client:
            console.print("[red]✗[/red] Not logged in or SDK not installed.")
            console.print(
                "[dim]Run: cyberwave login --token YOUR_TOKEN "
                "or cyberwave configure --token YOUR_TOKEN[/dim]"
            )
            raise click.Abort()
        return func(client, *args, **kwargs)
    
    return wrapper


def print_error(message: str, hint: str = None):
    """Print an error message with optional hint."""
    console.print(f"[red]✗[/red] {message}")
    if hint:
        console.print(f"[dim]{hint}[/dim]")


def print_success(message: str):
    """Print a success message."""
    console.print(f"[green]✓[/green] {message}")


def print_warning(message: str):
    """Print a warning message."""
    console.print(f"[yellow]![/yellow] {message}")


def print_info(message: str):
    """Print an info message."""
    console.print(f"[blue]ℹ[/blue] {message}")


def truncate_uuid(uuid: str, length: int = 8) -> str:
    """Truncate a UUID for display."""
    if not uuid:
        return "-"
    s = str(uuid)
    return f"{s[:length]}..." if len(s) > length else s


def create_table(title: str, columns: list[tuple[str, str]]) -> Table:
    """
    Create a Rich table with common styling.
    
    Args:
        title: Table title
        columns: List of (name, style) tuples
        
    Returns:
        Configured Rich Table
    """
    table = Table(title=title)
    for name, style in columns:
        table.add_column(name, style=style)
    return table


_LEVEL_RICH_STYLE = {
    "CRITICAL": "bold red",
    "ERROR": "red",
    "WARNING": "yellow",
    "INFO": "green",
    "DEBUG": "cyan",
}

_LOG_LINE_RE: re.Pattern[str] | None = None


def _log_line_re() -> re.Pattern[str]:
    global _LOG_LINE_RE  # noqa: PLW0603
    if _LOG_LINE_RE is None:
        _LOG_LINE_RE = re.compile(
            r"(\[(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\])"
            r"(\s+\[[^\]]+\])"
        )
    return _LOG_LINE_RE


def colorize_log_line(line: str) -> str:
    """Wrap log-level and module-name tags with Rich markup for colored output.

    The module name brackets must be escaped (``\\[``) so Rich doesn't
    interpret them as style tags.
    """
    from rich.markup import escape

    m = _log_line_re().search(line)
    if not m:
        return escape(line)
    level_tag = m.group(1)
    name_tag = m.group(2)
    level = level_tag[1:-1]
    style = _LEVEL_RICH_STYLE.get(level, "")
    colored_level = f"[{style}]{escape(level_tag)}[/{style}]" if style else escape(level_tag)
    colored_name = f"[dim]{escape(name_tag)}[/dim]"
    rest = escape(line[m.end() :])
    prefix = escape(line[: m.start()])
    return prefix + colored_level + colored_name + rest


def format_json(data: Any) -> str:
    """Format data as pretty JSON."""
    import json
    return json.dumps(data, indent=2, default=str)


def confirm_action(message: str, default: bool = False) -> bool:
    """Ask for confirmation before an action."""
    return click.confirm(message, default=default)


# Common column definitions for tables
COLUMNS = {
    "name": ("Name", "cyan"),
    "uuid": ("UUID", "dim"),
    "status": ("Status", "green"),
    "type": ("Type", "magenta"),
    "description": ("Description", ""),
    "created": ("Created", "dim"),
}


# =============================================================================
# Edge Environment File Writing
# =============================================================================


def write_edge_env(
    target_dir: str,
    twin_uuid: str,
    fingerprint: str,
    edge_config: dict | None = None,
    edge_configs: list[dict] | None = None,
    generator: str = "cyberwave",
) -> str:
    """
    Write a .env file for Cyberwave Edge configuration.
    
    This is a shared utility used by 'cyberwave twin create', 'cyberwave twin pair',
    and 'cyberwave edge pull' commands to generate consistent .env files.
    
    Args:
        target_dir: Directory to write .env file to
        twin_uuid: Primary twin UUID for MQTT commands
        fingerprint: Device fingerprint for identification
        edge_config: Single edge configuration dict (for single-twin mode)
        edge_configs: List of edge configs with twin_uuid (for multi-twin mode)
        generator: Command that generated this file (for comment)
    
    Returns:
        Path to the written .env file as string
    
    Example (single twin):
        write_edge_env(
            target_dir=".",
            twin_uuid="abc-123",
            fingerprint="my-device-fp",
            edge_config={"camera-source": "rtsp://...", "fps": 15},
        )
    
    Example (multi-twin):
        write_edge_env(
            target_dir=".",
            twin_uuid="abc-123",  # primary twin
            fingerprint="my-device-fp",
            edge_configs=[
                {"twin_uuid": "abc-123", "camera-source": "rtsp://cam1"},
                {"twin_uuid": "def-456", "camera-source": "rtsp://cam2"},
            ],
        )
    """
    import json
    from pathlib import Path
    
    creds = load_credentials()
    token = creds.token if creds else ""
    
    # Build header comment
    header_lines = [
        "# Cyberwave Edge Configuration",
        f"# Generated by: {generator}",
        f"# Fingerprint: {fingerprint}",
    ]
    
    # Handle multi-twin vs single-twin mode
    if edge_configs:
        # Multi-twin mode: list of configs with twin_uuid
        config_data = edge_configs
        header_lines.append(f"# Twins: {len(edge_configs)}")
        config_str = json.dumps(config_data, indent=2)
    else:
        # Single-twin mode: single config dict
        config_data = edge_config or {}
        if config_data and len(config_data) > 2:
            config_str = json.dumps(config_data, indent=2)
        else:
            config_str = json.dumps(config_data)
    
    env_content = f"""{chr(10).join(header_lines)}

# Required
CYBERWAVE_API_KEY={token}
CYBERWAVE_TWIN_UUID={twin_uuid}

# API Settings
CYBERWAVE_BASE_URL={get_api_url()}

# Device Identification
CYBERWAVE_EDGE_UUID={fingerprint}

# Edge Configuration (from asset's edge_config_schema)
# This is passed to the edge driver/plugin
# For multi-twin setups, this is a list with twin_uuid in each entry
EDGE_CONFIG='{config_str}'

# Logging
LOG_LEVEL=INFO
"""
    
    target_path = Path(target_dir).expanduser().resolve()
    target_path.mkdir(parents=True, exist_ok=True)
    env_file = target_path / ".env"
    env_file.write_text(env_content)
    
    return str(env_file)
