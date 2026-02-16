"""Configure command for the Cyberwave CLI."""

import click
from rich.console import Console
from rich.prompt import Prompt

from ..config import get_api_url
from ..credentials import Credentials, load_credentials, save_credentials

console = Console()


@click.command()
@click.option(
    "--token",
    "-t",
    help="API token to save",
)
@click.option(
    "--api-url",
    "-u",
    help="API URL (sets CYBERWAVE_API_URL env var hint)",
)
@click.option(
    "--show",
    is_flag=True,
    help="Show current configuration",
)
def configure(token: str | None, api_url: str | None, show: bool) -> None:
    """Configure CLI settings and credentials.

    Save an API token directly without going through the login flow.
    Useful when you already have a token from the dashboard.

    \b
    Examples:
        cyberwave-cli configure --token YOUR_TOKEN
        cyberwave-cli configure --show
        cyberwave-cli configure -t YOUR_TOKEN -u http://localhost:8000
    """
    if show:
        creds = load_credentials()
        console.print("\n[bold]Current Configuration:[/bold]")
        console.print(f"  API URL: [cyan]{get_api_url()}[/cyan]")
        
        if creds:
            console.print(f"  Token: [dim]{creds.token[:20]}...{creds.token[-8:]}[/dim]")
            if creds.email:
                console.print(f"  Email: [cyan]{creds.email}[/cyan]")
            if creds.workspace_name:
                console.print(f"  Workspace: [cyan]{creds.workspace_name}[/cyan]")
        else:
            console.print("  Token: [yellow]Not configured[/yellow]")
        
        console.print("\n[dim]Tip: Set CYBERWAVE_API_URL environment variable to change API URL[/dim]")
        return

    if not token:
        token = Prompt.ask("[bold]Enter API token[/bold]")
    
    if not token:
        console.print("[red]✗[/red] Token is required")
        raise click.Abort()

    # Test the token
    import httpx
    test_url = api_url or get_api_url()
    
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
            console.print(f"[yellow]⚠[/yellow] Unexpected response: {response.status_code}")
    except httpx.RequestError as e:
        console.print(f"[yellow]⚠[/yellow] Could not connect to API: {e}")
        if not click.confirm("Save token anyway?"):
            raise click.Abort()

    # Save credentials
    save_credentials(Credentials(token=token))
    console.print("[green]✓[/green] Token saved to /etc/cyberwave/credentials.json")
    
    if api_url:
        console.print(f"\n[dim]Note: To use {api_url} permanently, set:[/dim]")
        console.print(f"  [cyan]export CYBERWAVE_API_URL={api_url}[/cyan]")
