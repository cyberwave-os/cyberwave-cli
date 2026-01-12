"""Environment management commands for the Cyberwave CLI."""

import click
from rich.console import Console
from rich.table import Table

from ..config import get_api_url
from ..credentials import load_credentials

console = Console()


def get_sdk_client():
    """Get Cyberwave SDK client."""
    creds = load_credentials()
    if not creds or not creds.token:
        return None
    try:
        from cyberwave import Cyberwave
        return Cyberwave(base_url=get_api_url(), token=creds.token)
    except ImportError:
        return None


@click.group()
def environment():
    """Manage environments."""
    pass


@environment.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_environments(as_json: bool):
    """List environments."""
    client = get_sdk_client()
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        console.print("[dim]Run: cyberwave-cli configure --token YOUR_TOKEN[/dim]")
        raise click.Abort()

    try:
        envs = client.environments.list()

        if as_json:
            import json
            data = [
                {
                    "uuid": str(e.uuid),
                    "name": e.name,
                    "project_uuid": str(e.project_uuid) if hasattr(e, 'project_uuid') and e.project_uuid else None,
                }
                for e in envs
            ]
            console.print(json.dumps(data, indent=2))
            return

        if not envs:
            console.print("[dim]No environments found.[/dim]")
            console.print("[dim]Create one with: cyberwave-cli camera[/dim]")
            return

        table = Table(title="Environments")
        table.add_column("Name", style="cyan")
        table.add_column("UUID", style="dim")
        table.add_column("Project")

        for e in envs:
            project = str(e.project_uuid)[:8] + "..." if hasattr(e, 'project_uuid') and e.project_uuid else "-"
            table.add_row(
                e.name or "Unnamed",
                str(e.uuid)[:8] + "...",
                project,
            )

        console.print(table)
        console.print(f"\n[dim]Total: {len(envs)} environment(s)[/dim]")

    except Exception as e:
        console.print(f"[red]✗[/red] Failed to list environments: {e}")
        raise click.Abort()


@environment.command("show")
@click.argument("uuid")
def show_environment(uuid: str):
    """Show details of an environment."""
    client = get_sdk_client()
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    try:
        env = client.environments.get(uuid)
        
        console.print(f"\n[bold cyan]{env.name}[/bold cyan]")
        console.print(f"  UUID: {env.uuid}")
        if hasattr(env, 'project_uuid'):
            console.print(f"  Project: {env.project_uuid or 'None'}")
        
        # List twins in this environment
        twins = client.twins.list(environment_id=uuid)
        if twins:
            console.print(f"\n  [bold]Twins ({len(twins)}):[/bold]")
            for t in twins:
                console.print(f"    • {t.name} [dim]({t.uuid})[/dim]")

    except Exception as e:
        console.print(f"[red]✗[/red] Failed to get environment: {e}")
        raise click.Abort()
