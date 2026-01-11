"""Twin management commands for the Cyberwave CLI."""

import click
from rich.table import Table

from ..utils import (
    console,
    get_sdk_client,
    print_error,
    print_success,
    truncate_uuid,
)


@click.group()
def twin():
    """Manage digital twins."""
    pass


@twin.command("list")
@click.option("--environment", "-e", help="Filter by environment UUID")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_twins(environment: str | None, as_json: bool):
    """List digital twins."""
    client = get_sdk_client()
    if not client:
        print_error("Not logged in or SDK not installed.", "Run: cyberwave-cli configure --token YOUR_TOKEN")
        raise click.Abort()

    try:
        if environment:
            twins = client.twins.list(environment_id=environment)
        else:
            twins = client.twins.list()

        if as_json:
            import json
            data = [
                {
                    "uuid": str(t.uuid),
                    "name": t.name,
                    "asset_uuid": str(t.asset_uuid) if t.asset_uuid else None,
                    "environment_uuid": str(t.environment_uuid) if t.environment_uuid else None,
                }
                for t in twins
            ]
            console.print(json.dumps(data, indent=2))
            return

        if not twins:
            console.print("[dim]No twins found.[/dim]")
            console.print("[dim]Create one with: cyberwave-cli camera[/dim]")
            return

        table = Table(title="Digital Twins")
        table.add_column("Name", style="cyan")
        table.add_column("UUID", style="dim")
        table.add_column("Asset")
        table.add_column("Environment")

        for t in twins:
            table.add_row(
                t.name or "Unnamed",
                truncate_uuid(t.uuid),
                truncate_uuid(t.asset_uuid),
                truncate_uuid(t.environment_uuid),
            )

        console.print(table)
        console.print(f"\n[dim]Total: {len(twins)} twin(s)[/dim]")

    except Exception as e:
        print_error(f"Failed to list twins: {e}")
        raise click.Abort()


@twin.command("show")
@click.argument("uuid")
def show_twin(uuid: str):
    """Show details of a specific twin."""
    client = get_sdk_client()
    if not client:
        print_error("Not logged in or SDK not installed.")
        raise click.Abort()

    try:
        twin_data = client.twins.get_raw(uuid)
        
        console.print(f"\n[bold cyan]{twin_data.name}[/bold cyan]")
        console.print(f"  UUID: {twin_data.uuid}")
        console.print(f"  Asset: {twin_data.asset_uuid or 'None'}")
        console.print(f"  Environment: {twin_data.environment_uuid or 'None'}")
        
        if hasattr(twin_data, 'capabilities') and twin_data.capabilities:
            console.print(f"  Capabilities: {twin_data.capabilities}")

    except Exception as e:
        print_error(f"Failed to get twin: {e}")
        raise click.Abort()


@twin.command("delete")
@click.argument("uuid")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def delete_twin(uuid: str, yes: bool):
    """Delete a digital twin."""
    client = get_sdk_client()
    if not client:
        print_error("Not logged in or SDK not installed.")
        raise click.Abort()

    if not yes:
        if not click.confirm(f"Delete twin {uuid}?"):
            raise click.Abort()

    try:
        client.twins.delete(uuid)
        print_success(f"Deleted twin: {uuid}")
    except Exception as e:
        print_error(f"Failed to delete twin: {e}")
        raise click.Abort()
