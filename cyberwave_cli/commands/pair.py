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
from ..utils import get_sdk_client, write_edge_env, print_error, print_success, print_warning

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
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return

    # Generate fingerprint
    fingerprint = generate_fingerprint()
    device_info = get_device_info()

    console.print("\n[bold]Device Pairing[/bold]")
    console.print(f"Fingerprint: [cyan]{fingerprint}[/cyan]")
    console.print(f"Hostname:    {device_info.get('hostname', 'unknown')}")
    console.print(f"Platform:    {device_info.get('platform', 'unknown')}")

    # Get twin info
    try:
        twin = client.twins.get(twin_uuid)
        twin_name = getattr(twin, 'name', 'Unknown')
        console.print(f"\n[bold]Target Twin:[/bold] {twin_name}")
    except Exception as e:
        print_error(f"Twin not found: {twin_uuid}", f"Error: {e}")
        return

    # Confirm pairing
    if not yes and not Confirm.ask(f"\nPair this device to '{twin_name}'?", default=True):
        print_warning("Pairing cancelled.")
        return

    # Register device with backend via pairing API
    console.print("\n[dim]Registering device with backend...[/dim]")

    try:
        # Call the pairing API via SDK
        client.twins.pair_device(
            twin_uuid,
            fingerprint=fingerprint,
            hostname=device_info.get('hostname', ''),
            platform=device_info.get('platform', ''),
        )
        print_success("Device registered with backend")
    except Exception as e:
        # Log warning but continue - pairing API might not be deployed yet
        print_warning(f"Backend registration failed: {e}")
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

    print_success(f"Configuration saved to {target_dir}/.env")

    print_success("Pairing complete!")
    console.print("\n[dim]To start streaming:[/dim]")
    console.print(f"  cd {target_dir}")
    console.print("  cyberwave edge start")


