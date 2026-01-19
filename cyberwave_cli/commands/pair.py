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


def _coerce_value(field_type: str, value: str | None) -> object | None:
    """Coerce string value to the type specified in schema."""
    if value is None or value == "":
        return None
    field_type = (field_type or "").lower()
    if field_type == "integer":
        try:
            return int(value)
        except ValueError:
            return value
    if field_type == "number":
        try:
            return float(value)
        except ValueError:
            return value
    if field_type == "boolean":
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "y"}
        return bool(value)
    return value


def _prompt_for_schema_fields(
    schema: list[dict],
    cli_overrides: dict[str, str],
    yes: bool,
) -> dict:
    """
    Prompt user for each field in the schema, using CLI overrides when provided.
    Returns a config dict with all non-empty values.
    """
    config = {}
    
    if not schema:
        return config
    
    if not yes:
        console.print("\n[bold]Edge Configuration[/bold]")
        console.print("[dim]Configure based on asset schema. Leave blank to skip optional fields.[/dim]")
    
    for field in schema:
        name = field.get("name", "")
        flag = field.get("flag", "")
        field_type = field.get("type", "string")
        default = field.get("default")
        description = field.get("description", "")
        required = field.get("required", False)
        
        if not name:
            continue
        
        # Check if CLI override was provided (match by flag without --)
        cli_key = flag.lstrip("-").replace("-", "_") if flag else name
        cli_value = cli_overrides.get(cli_key)
        
        if cli_value is not None:
            # Use CLI-provided value
            config[name] = _coerce_value(field_type, cli_value)
        elif yes:
            # Non-interactive: use default if available
            if default not in (None, ""):
                config[name] = _coerce_value(field_type, str(default))
            elif required:
                print_warning(f"Required field '{name}' has no default and --yes was used")
        else:
            # Interactive prompt
            prompt_text = f"  {name}"
            if description:
                console.print(f"[dim]  {description}[/dim]")
            
            default_str = str(default) if default not in (None, "") else ""
            value = Prompt.ask(prompt_text, default=default_str)
            
            if value.strip():
                config[name] = _coerce_value(field_type, value)
            elif required:
                print_warning(f"Required field '{name}' was left empty")
    
    return config


@click.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("twin_uuid")
@click.option(
    "--target-dir",
    "-d",
    default=".",
    help="Directory to save edge configuration (default: current directory)",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.pass_context
def pair(ctx: click.Context, twin_uuid: str, target_dir: str, yes: bool):
    """
    Pair this device with a digital twin.

    Registers this device with the backend and binds it to the specified twin.
    After pairing, run `cyberwave edge start` to begin streaming.

    Configuration options are determined by the asset's edge_config_schema.
    Pass any schema field as --field-name value.

    \b
    What this does:
        1. Generates a unique fingerprint for this device
        2. Registers the edge device with the backend (auto-creates if new)
        3. Binds the twin to this edge with configuration from schema
        4. Saves local .env file for edge service

    \b
    Examples:
        cyberwave pair abc-123-def-456
        cyberwave pair abc-123-def-456 --camera-source "rtsp://..." --fps 15

    \b
    Quick Start:
        1. cyberwave login           # Login to your account
        2. cyberwave pair <uuid>     # Pair device with twin
        3. cyberwave edge start      # Start streaming
    """
    # Parse extra args as --key value pairs for schema fields
    cli_overrides: dict[str, str] = {}
    args = ctx.args
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                cli_overrides[key] = args[i + 1]
                i += 2
            else:
                cli_overrides[key] = "true"  # Flag without value
                i += 1
        else:
            i += 1

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

    # Get twin info and asset capabilities
    try:
        twin = client.twins.get(twin_uuid)
        twin_name = getattr(twin, 'name', 'Unknown')
        console.print(f"\n[bold]Target Twin:[/bold] {twin_name}")
        
        # Get asset capabilities including edge_config_schema
        asset_uuid = getattr(twin, 'asset_uuid', None)
        edge_config_schema: list[dict] = []
        if asset_uuid:
            try:
                asset = client.assets.get(asset_uuid)
                capabilities = getattr(asset, 'capabilities', {}) or {}
                edge_config_schema = capabilities.get("edge_config_schema", []) or []
                
                # Show capabilities summary
                sensors = capabilities.get('sensors', [])
                caps_summary = []
                if sensors:
                    caps_summary.append(f"{len(sensors)} sensor(s)")
                if capabilities.get('has_joints'):
                    caps_summary.append("joints")
                if capabilities.get('can_locomote'):
                    caps_summary.append("locomotion")
                if caps_summary:
                    console.print(f"[dim]  Capabilities: {', '.join(caps_summary)}[/dim]")
                if edge_config_schema:
                    console.print(f"[dim]  Config schema: {len(edge_config_schema)} field(s)[/dim]")
            except Exception as e:
                console.print(f"[dim]  Could not fetch asset details: {e}[/dim]")
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

    # Step 2: Build config from schema (fully dynamic)
    edge_config = _prompt_for_schema_fields(edge_config_schema, cli_overrides, yes)

    # Step 3: Pair twin to edge
    console.print("\n[dim]Binding twin to edge device...[/dim]")
    
    try:
        headers = {"Authorization": f"Bearer {token}"}
        pair_url = f"{base_url}/api/v1/edges/{edge_uuid}/pair"
        
        pair_payload = {
            "twin_uuid": twin_uuid,
            "camera_config": edge_config,  # Backend expects camera_config for now
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
    cameras = [{"camera_id": "default", **edge_config}] if edge_config else []
    
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
