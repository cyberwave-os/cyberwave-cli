"""
Pair command - Register an edge device with a digital twin.

The `pair` command registers this device with the backend and binds it
to a digital twin. This is the first step to connect a physical
device to a digital twin.

Terminology:
- PAIR: Register device + bind to twin (one-time setup per twin)
- CONNECT: Establish streaming connection (cyberwave edge start)

Examples:
    cyberwave pair abc-123-def-456
    cyberwave pair abc-123-def-456 --camera-source "rtsp://..."
    cyberwave pair abc-123-def-456 --target-dir ./my-edge
"""

import platform

import click
import httpx
from rich.console import Console
from rich.prompt import Confirm, Prompt

from ..config import get_api_url
from ..credentials import load_credentials
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
@click.option(
    "--camera-source",
    "-c",
    default=None,
    help="Camera source (e.g., rtsp://..., 0 for webcam)",
)
@click.option(
    "--fps",
    default=30,
    help="Camera FPS (default: 30)",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def pair(twin_uuid: str, target_dir: str, camera_source: str | None, fps: int, yes: bool):
    """
    Pair this device with a digital twin.

    Registers this device with the backend and binds it to the specified twin.
    After pairing, run `cyberwave edge start` to begin streaming.

    \b
    What this does:
        1. Generates a unique fingerprint for this device
        2. Registers the edge device with the backend (auto-creates if new)
        3. Binds the twin to this edge with camera configuration
        4. Saves local .env file for edge service

    \b
    Examples:
        cyberwave pair abc-123-def-456
        cyberwave pair abc-123-def-456 --camera-source "rtsp://user:pass@192.168.1.100/stream"
        cyberwave pair abc-123-def-456 --camera-source 0 --fps 15

    \b
    Quick Start:
        1. cyberwave login           # Login to your account
        2. cyberwave pair <uuid>     # Pair device with twin
        3. cyberwave edge start      # Start streaming
    """
    # Get SDK client and auth
    client = get_sdk_client()
    creds = load_credentials()
    token = creds.token if creds else None
    base_url = get_api_url()
    
    if not client or not token:
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

    # Step 1: Register/discover edge device
    console.print("\n[dim]Registering edge device...[/dim]")
    
    edge_uuid = None
    try:
        headers = {"Authorization": f"Bearer {token}"}
        discover_url = f"{base_url}/api/v1/edges/discover"
        
        discover_payload = {
            "fingerprint": fingerprint,
            "hostname": device_info.get('hostname', ''),
            "platform": f"{platform.system()}-{platform.machine()}",
            "name": device_info.get('hostname', fingerprint[:20]),
        }
        
        with httpx.Client() as http_client:
            response = http_client.post(
                discover_url,
                json=discover_payload,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            edge_uuid = data.get("edge_uuid")
        
        print_success(f"Edge registered: {edge_uuid[:8]}...")
        
    except Exception as e:
        print_error(f"Failed to register edge device: {e}")
        return

    # Step 2: Get or prompt for camera config
    camera_config = {}
    
    if camera_source is not None:
        # Use provided camera source
        try:
            source = int(camera_source)  # Webcam index
        except ValueError:
            source = camera_source  # URL string
        camera_config = {"source": source, "fps": fps}
    else:
        # Prompt for camera source
        if not yes:
            console.print("\n[bold]Camera Configuration[/bold]")
            console.print("[dim]Enter camera source (RTSP URL, webcam index, or skip)[/dim]")
            source_input = Prompt.ask("Camera source", default="0")
            
            try:
                source = int(source_input)
            except ValueError:
                source = source_input
            
            if source:
                camera_config = {"source": source, "fps": fps}
        else:
            # Default to webcam 0
            camera_config = {"source": 0, "fps": fps}

    # Step 3: Pair twin to edge
    console.print("\n[dim]Binding twin to edge device...[/dim]")
    
    try:
        headers = {"Authorization": f"Bearer {token}"}
        pair_url = f"{base_url}/api/v1/edges/{edge_uuid}/pair"
        
        pair_payload = {
            "twin_uuid": twin_uuid,
            "camera_config": camera_config,
        }
        
        with httpx.Client() as http_client:
            response = http_client.post(
                pair_url,
                json=pair_payload,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
        
        print_success(f"Twin '{twin_name}' bound to this edge")
        
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            print_warning("Twin already paired to a different device")
        else:
            print_error(f"Failed to pair twin: {e}")
        return
    except Exception as e:
        print_error(f"Failed to pair twin: {e}")
        return

    # Step 4: Write local .env file
    cameras = [{"camera_id": "default", **camera_config}] if camera_config else []
    
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
    
    # Show current bindings
    console.print("\n[dim]This edge device is now paired to:[/dim]")
    console.print(f"  - {twin_name} ({twin_uuid[:8]}...)")


