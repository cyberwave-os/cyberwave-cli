"""
Pair command - Register an edge device with a digital twin.

The `pair` command registers this device with the backend and downloads
the edge configuration. This is the first step to connect a physical
device to a digital twin.

Terminology:
- PAIR: Register device with backend (one-time setup)
- CONNECT: Establish streaming connection (cyberwave edge start)

Examples:
    cyberwave pair abc-123-def-456
    cyberwave pair abc-123-def-456 --target-dir ./my-edge
"""

import click
from rich.console import Console
from rich.prompt import Confirm

from ..fingerprint import generate_fingerprint, get_device_info
from ..utils import get_sdk_client, write_edge_env

console = Console()


@click.command()
@click.argument("twin_uuid")
@click.option(
    "--target-dir",
    "-d",
    default=".",
    help="Directory to save edge configuration (default: current directory)",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def pair(twin_uuid: str, target_dir: str, yes: bool):
    """
    Pair this device with a digital twin.

    Registers this device with the backend and downloads the edge configuration.
    After pairing, run `cyberwave edge start` to begin streaming.

    \b
    What this does:
        1. Generates a unique fingerprint for this device
        2. Registers the device with the twin in the backend
        3. Downloads edge configuration to local .env file

    \b
    Examples:
        cyberwave pair abc-123-def-456
        cyberwave pair abc-123-def-456 --target-dir ./my-edge

    \b
    Quick Start:
        1. cyberwave login           # Login to your account
        2. cyberwave pair <uuid>     # Pair device with twin
        3. cyberwave edge start      # Start streaming
    """
    # Get SDK client
    client = get_sdk_client()
    if not client:
        console.print("[red]Not authenticated. Run 'cyberwave login' first.[/red]")
        return

    # Generate fingerprint
    fingerprint = generate_fingerprint()
    device_info = get_device_info()

    console.print(f"\n[bold]Device Pairing[/bold]")
    console.print(f"Fingerprint: [cyan]{fingerprint}[/cyan]")
    console.print(f"Hostname:    {device_info.get('hostname', 'unknown')}")
    console.print(f"Platform:    {device_info.get('platform', 'unknown')}")

    # Get twin info
    try:
        twin = client.twins.get(twin_uuid)
        twin_name = getattr(twin, 'name', 'Unknown')
        console.print(f"\n[bold]Target Twin:[/bold] {twin_name}")
    except Exception as e:
        console.print(f"\n[red]Twin not found: {twin_uuid}[/red]")
        console.print(f"[dim]Error: {e}[/dim]")
        return

    # Confirm pairing
    if not yes and not Confirm.ask(f"\nPair this device to '{twin_name}'?", default=True):
        console.print("[yellow]Pairing cancelled.[/yellow]")
        return

    # Register device with backend via pairing API
    console.print("\n[dim]Registering device with backend...[/dim]")

    try:
        # Call the pairing API
        # The SDK should have a method like: client.twins.pair_device(twin_uuid, {...})
        # For now, we'll use a direct API call via the REST client
        pair_response = _call_pair_api(
            client,
            twin_uuid,
            fingerprint=fingerprint,
            hostname=device_info.get('hostname', ''),
            platform=device_info.get('platform', ''),
        )

        if pair_response:
            console.print("[green]✓[/green] Device registered with backend")
        else:
            console.print("[yellow]![/yellow] Backend registration skipped (API not available)")
    except Exception as e:
        # Log warning but continue - pairing API might not be deployed yet
        console.print(f"[yellow]![/yellow] Backend registration failed: {e}")
        console.print("[dim]Continuing with local configuration...[/dim]")

    # Get edge config from twin metadata (backward compatible)
    metadata = getattr(twin, 'metadata', {}) or {}
    edge_configs = metadata.get('edge_configs', {})
    my_config = edge_configs.get(fingerprint, {})

    cameras = my_config.get('cameras', [])
    if not cameras:
        # Create default camera config
        cameras = [{"camera_id": "default", "source": 0, "fps": 30}]

    # Write local .env file
    write_edge_env(
        target_dir=target_dir,
        twin_uuid=twin_uuid,
        cameras=cameras,
        fingerprint=fingerprint,
        generator="cyberwave pair",
    )

    console.print(f"[green]✓[/green] Configuration saved to {target_dir}/.env")

    console.print(f"\n[bold green]Pairing complete![/bold green]")
    console.print(f"\n[dim]To start streaming:[/dim]")
    console.print(f"  cd {target_dir}")
    console.print(f"  cyberwave edge start")


def _call_pair_api(
    client,
    twin_uuid: str,
    fingerprint: str,
    hostname: str,
    platform: str,
) -> dict | None:
    """
    Call the backend pairing API.

    Returns the response dict if successful, None if API not available.
    """
    try:
        # Try to use SDK method if available
        if hasattr(client.twins, 'pair_device'):
            return client.twins.pair_device(
                twin_uuid,
                fingerprint=fingerprint,
                hostname=hostname,
                platform=platform,
            )

        # Fallback: Direct REST call
        from cyberwave.rest import ApiException

        api = client._api
        response = api.api_client.call_api(
            f"/api/v1/twins/{twin_uuid}/pair-device",
            "POST",
            body={
                "fingerprint": fingerprint,
                "hostname": hostname,
                "platform": platform,
            },
            response_type=object,
            auth_settings=["TokenAuth"],
        )
        return response

    except ImportError:
        return None
    except Exception as e:
        # API might not be deployed yet
        if "404" in str(e) or "Not Found" in str(e):
            return None
        raise
