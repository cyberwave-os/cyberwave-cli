"""
Shared utilities for Cyberwave CLI commands.

This module provides common functionality used across multiple CLI commands:
- SDK client initialization
- Output formatting helpers
- Common CLI patterns
"""

import click
from rich.console import Console
from rich.table import Table
from typing import Any, Callable, Optional, TypeVar

from .config import get_api_url
from .credentials import load_credentials

console = Console()

T = TypeVar("T")


def get_sdk_client():
    """
    Get an authenticated Cyberwave SDK client.
    
    Returns:
        Cyberwave client if authenticated, None otherwise.
        
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
        return Cyberwave(base_url=get_api_url(), token=creds.token)
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
            console.print("[dim]Run: cyberwave-cli configure --token YOUR_TOKEN[/dim]")
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
    
    This is a shared utility used by both 'cyberwave connect' and 'cyberwave edge pull'
    commands to generate consistent .env files.
    
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
CYBERWAVE_TOKEN={token}
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
