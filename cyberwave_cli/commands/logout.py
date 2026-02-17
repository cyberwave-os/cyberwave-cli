"""Logout command for the Cyberwave CLI."""

import click
from rich.console import Console

from ..config import CREDENTIALS_FILE
from ..credentials import clear_credentials, load_credentials

console = Console()


@click.command()
def logout() -> None:
    """Log out from Cyberwave.

    Removes stored credentials from your local machine.
    """
    creds = load_credentials()
    if not creds or not creds.token:
        console.print("\n[yellow]⚠[/yellow] Not logged in")
        return

    clear_credentials()
    console.print("\n[green]✓[/green] Successfully logged out")
    console.print(f"[dim]Credentials removed from {CREDENTIALS_FILE}[/dim]")
