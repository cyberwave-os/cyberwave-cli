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
