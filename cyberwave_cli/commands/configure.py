"""Configure command for the Cyberwave CLI."""

from __future__ import annotations

import click
from rich.console import Console
from rich.prompt import Prompt

from ..config import CREDENTIALS_FILE, get_api_url
from ..credentials import (
    Credentials,
    collect_runtime_env_overrides,
    load_credentials,
    save_credentials,
)

console = Console()


def _redact_secret(secret: str | None) -> str:
    """Return a short redacted representation of a secret."""
    if not secret:
        return "[yellow]Not configured[/yellow]"
    if len(secret) <= 10:
        return f"[dim]{secret[:2]}...{secret[-2:]}[/dim]"
    return f"[dim]{secret[:6]}...{secret[-4:]}[/dim]"


@click.command()
@click.option(
    "--token",
    "-t",
    help="API token to save",
)
@click.option(
    "--base-url",
    "-u",
    help="API URL (sets CYBERWAVE_BASE_URL env var hint)",
)
@click.option(
    "--show",
    is_flag=True,
    help="Show current configuration",
)
@click.option(
    "--internal-deb-read-token",
    help="Buildkite read token for the private internal Debian registry",
)
@click.option(
    "--internal-python-read-token",
    help="Buildkite read token for the private internal Python registry",
)
def configure(
    token: str | None,
    base_url: str | None,
    show: bool,
    internal_deb_read_token: str | None,
    internal_python_read_token: str | None,
) -> None:
    """Configure CLI settings and credentials.

    Save an API token directly without going through the login flow.
    Useful when you already have a token from the dashboard.

    \b
    Examples:
        cyberwave configure --token YOUR_TOKEN
        cyberwave configure --show
        cyberwave configure -t YOUR_TOKEN -u http://localhost:8000
    """
    if show:
        creds = load_credentials()
        console.print("\n[bold]Current Configuration:[/bold]")
        console.print(f"  API URL: [cyan]{get_api_url()}[/cyan]")

        if creds:
            console.print(f"  Token: {_redact_secret(creds.token)}")
            if creds.email:
                console.print(f"  Email: [cyan]{creds.email}[/cyan]")
            if creds.workspace_name:
                console.print(f"  Workspace: [cyan]{creds.workspace_name}[/cyan]")
            console.print(
                f"  Internal deb token: {_redact_secret(creds.internal_deb_read_token)}"
            )
            console.print(
                f"  Internal python token: {_redact_secret(creds.internal_python_read_token)}"
            )
        else:
            console.print("  Token: [yellow]Not configured[/yellow]")
            console.print("  Internal deb token: [yellow]Not configured[/yellow]")
            console.print("  Internal python token: [yellow]Not configured[/yellow]")

        console.print(
            "\n[dim]Tip: Set CYBERWAVE_BASE_URL environment variable to change API URL[/dim]"
        )
        return

    existing_credentials = load_credentials()
    has_registry_updates = bool(internal_deb_read_token or internal_python_read_token)

    if not token and not has_registry_updates:
        token = Prompt.ask("[bold]Enter API token[/bold]")

    if not token and not (existing_credentials and existing_credentials.token):
        console.print("[red]✗[/red] Token is required")
        raise click.Abort()

    token_to_save = token or (existing_credentials.token if existing_credentials else "")

    if token:
        # Test the token
        import httpx

        test_url = base_url or get_api_url()

        console.print(f"\n[dim]Testing token against {test_url}...[/dim]")

        try:
            response = httpx.get(
                f"{test_url}/api/v1/environments",
                headers={"Authorization": f"Token {token}"},
                timeout=10.0,
            )

            if response.status_code == 200:
                console.print("[green]✓[/green] Token is valid")
            elif response.status_code == 401:
                console.print("[red]✗[/red] Token is invalid or expired")
                if not click.confirm("Save anyway?"):
                    raise click.Abort()
            else:
                console.print(
                    f"[yellow]⚠[/yellow] Unexpected response: {response.status_code}"
                )
        except httpx.RequestError as e:
            console.print(f"[yellow]⚠[/yellow] Could not connect to API: {e}")
            if not click.confirm("Save token anyway?"):
                raise click.Abort()

    runtime_overrides = collect_runtime_env_overrides(api_url_override=base_url)
    resolved_credentials = Credentials(
        token=token_to_save,
        email=existing_credentials.email if existing_credentials else None,
        created_at=existing_credentials.created_at if existing_credentials else None,
        workspace_uuid=existing_credentials.workspace_uuid if existing_credentials else None,
        workspace_name=existing_credentials.workspace_name if existing_credentials else None,
        cyberwave_environment=runtime_overrides.get("CYBERWAVE_ENVIRONMENT")
        or (existing_credentials.cyberwave_environment if existing_credentials else None),
        cyberwave_edge_log_level=runtime_overrides.get("CYBERWAVE_EDGE_LOG_LEVEL")
        or (existing_credentials.cyberwave_edge_log_level if existing_credentials else None),
        cyberwave_worker_log_level=runtime_overrides.get("CYBERWAVE_WORKER_LOG_LEVEL")
        or (existing_credentials.cyberwave_worker_log_level if existing_credentials else None),
        cyberwave_base_url=runtime_overrides.get("CYBERWAVE_BASE_URL")
        or (existing_credentials.cyberwave_base_url if existing_credentials else None),
        cyberwave_mqtt_host=runtime_overrides.get("CYBERWAVE_MQTT_HOST")
        or (existing_credentials.cyberwave_mqtt_host if existing_credentials else None),
        cyberwave_mqtt_port=runtime_overrides.get("CYBERWAVE_MQTT_PORT")
        or (existing_credentials.cyberwave_mqtt_port if existing_credentials else None),
        internal_deb_read_token=internal_deb_read_token
        or (existing_credentials.internal_deb_read_token if existing_credentials else None),
        internal_python_read_token=internal_python_read_token
        or (existing_credentials.internal_python_read_token if existing_credentials else None),
    )
    # Save credentials
    save_credentials(resolved_credentials)
    if has_registry_updates and not token:
        console.print(f"[green]✓[/green] Configuration saved to {CREDENTIALS_FILE}")
    else:
        console.print(f"[green]✓[/green] Token saved to {CREDENTIALS_FILE}")

    if base_url:
        console.print(f"\n[dim]Note: To use {base_url} permanently, set:[/dim]")
        console.print(f"  [cyan]export CYBERWAVE_BASE_URL={base_url}[/cyan]")
