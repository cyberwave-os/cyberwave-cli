"""
Smart connect command - unified entry point for twin creation + edge setup.

The `connect` command handles everything intelligently:
- Resolves asset from registry ID, alias, or local file
- Finds existing twin for this device's fingerprint, or creates new one
- Configures edge with interactive prompts
- Saves config to cloud (twin metadata) and local (.env)

Examples:
    cyberwave connect camera
    cyberwave connect unitree/go2 --name "My Robot"
    cyberwave connect camera --twin-uuid abc-123
    cyberwave connect camera --cloud-only
"""

import logging
from datetime import datetime, timezone
from typing import Any

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

console = Console()
logger = logging.getLogger(__name__)


@click.command()
@click.argument("asset")
@click.option("--twin-uuid", "-t", help="Use specific twin (skip discovery)")
@click.option("--environment-uuid", "-e", help="Create twin in this environment")
@click.option("--cloud-only", is_flag=True, help="Create twin without local edge setup")
@click.option("--name", "-n", help="Twin name (for new twins)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def connect(
    asset: str,
    twin_uuid: str | None,
    environment_uuid: str | None,
    cloud_only: bool,
    name: str | None,
    yes: bool,
):
    """
    Connect to a digital twin.
    
    ASSET can be:
    
    \b
      - Registry ID: unitree/go2, cyberwave/standard-cam
      - Alias: go2, camera (short names)
      - Local file: ./my-robot.json
      - URL: https://example.com/asset.json
    
    \b
    Examples:
        cyberwave connect camera
        cyberwave connect go2 --name "My Robot"
        cyberwave connect camera --twin-uuid abc123
        cyberwave connect camera --cloud-only
        cyberwave connect ./my-camera.json -e env-123
    """
    from ..asset_resolver import AssetResolutionError, get_asset_display_name, resolve_asset
    from ..fingerprint import generate_fingerprint, get_device_info
    from ..utils import get_sdk_client, print_error, print_success
    
    # Get SDK client
    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return
    
    # Generate fingerprint
    fingerprint = generate_fingerprint()
    device_info = get_device_info()
    
    console.print(f"\n[dim]Fingerprint: {fingerprint}[/dim]")
    
    # 1. Resolve asset
    console.print(f"\nResolving asset '{asset}'...", end=" ")
    try:
        resolved_asset = resolve_asset(asset, client)
        asset_name = get_asset_display_name(resolved_asset)
        console.print(f"[green]✓[/green] {asset_name}")
    except AssetResolutionError as e:
        console.print(f"[red]✗[/red]")
        print_error(str(e))
        return
    
    # 2. Find or create twin
    if twin_uuid:
        # Use specified twin
        try:
            twin = client.twins.get(twin_uuid)
            twin_name = getattr(twin, 'name', 'Unknown')
            console.print(f"\n[cyan]Using twin:[/cyan] {twin_name}")
        except Exception as e:
            logger.debug("Failed to get twin %s: %s", twin_uuid, e)
            print_error(f"Twin not found: {twin_uuid}")
            return
    else:
        # Find existing twin for this fingerprint or create new one
        twin = _find_or_create_twin(
            client=client,
            asset=resolved_asset,
            fingerprint=fingerprint,
            environment_uuid=environment_uuid,
            twin_name=name,
            yes=yes,
        )
        
        if twin is None:
            return  # User cancelled
    
    twin_uuid = str(getattr(twin, 'uuid', ''))
    twin_name = getattr(twin, 'name', 'Unknown')
    
    if cloud_only:
        print_success(f"Twin created: {twin_name} ({twin_uuid})")
        console.print(f"\n[dim]To connect an edge device later:[/dim]")
        console.print(f"  cyberwave connect {asset} --twin-uuid {twin_uuid}")
        return
    
    # 3. Configure edge (interactive or from existing config)
    config = _configure_edge(
        client=client,
        twin=twin,
        asset=resolved_asset,
        fingerprint=fingerprint,
        device_info=device_info,
        yes=yes,
    )
    
    if config is None:
        return  # User cancelled
    
    # 4. Register edge and pair twin using new API
    edge_uuid = _register_and_pair_edge(twin_uuid, fingerprint, device_info, config)
    
    if not edge_uuid:
        # Fallback to legacy twin metadata storage
        _save_config_to_twin(client, twin_uuid, fingerprint, config)
    
    _write_local_env(twin_uuid, config, fingerprint)
    
    print_success("Connected!")
    console.print(f"\n[bold]Saved to:[/bold]")
    if edge_uuid:
        console.print(f"  • Cloud: edge/{edge_uuid[:8]}... → twin/{twin_uuid[:8]}...")
    else:
        console.print(f"  • Cloud: twin/{twin_uuid}/edge_configs/{fingerprint[:20]}... (legacy)")
    console.print(f"  • Local: ./.env")
    console.print(f"\n[dim]Run: python -m cyberwave_edge.service[/dim]")


def _find_or_create_twin(
    client: Any,
    asset: dict,
    fingerprint: str,
    environment_uuid: str | None,
    twin_name: str | None,
    yes: bool,
) -> Any | None:
    """Find existing twin for this fingerprint or create a new one."""
    asset_uuid = asset.get('uuid')
    
    # Search for twins that have this fingerprint in their edge_configs
    if asset_uuid:
        try:
            twins = client.twins.list(asset_uuid=asset_uuid)
            
            for twin in twins:
                metadata = getattr(twin, 'metadata', {}) or {}
                edge_configs = metadata.get('edge_configs', {})
                
                if fingerprint in edge_configs:
                    # Found existing twin for this device
                    twin_name = getattr(twin, 'name', 'Unknown')
                    config = edge_configs[fingerprint]
                    last_sync = config.get('last_sync', 'unknown')
                    
                    console.print(f"\n[cyan]Found existing twin:[/cyan] {twin_name}")
                    console.print(f"[dim]  Last connected: {last_sync}[/dim]")
                    
                    if yes or Confirm.ask("\nUse this twin?", default=True):
                        return twin
        except Exception as e:
            # Log but continue - we'll create a new twin if lookup fails
            logger.debug("Failed to search for existing twins: %s", e)
    
    # No existing twin found - create new one
    console.print("\n[yellow]No existing twin found for this device.[/yellow]")
    
    if not yes and not Confirm.ask("Create new twin?", default=True):
        return None
    
    # Select environment
    if not environment_uuid:
        environment_uuid = _select_environment(client, yes)
        if not environment_uuid:
            return None
    
    # Get twin name
    if not twin_name:
        device_info = get_device_info()
        default_name = f"Camera-{device_info.get('hostname', 'edge')[:15]}"
        
        if yes:
            twin_name = default_name
        else:
            twin_name = Prompt.ask("Twin name", default=default_name)
    
    # Create twin
    console.print(f"\n[dim]Creating twin '{twin_name}'...[/dim]")
    
    try:
        twin = client.twins.create(
            name=twin_name,
            environment_id=environment_uuid,
            asset_id=asset_uuid,
        )
        from ..utils import print_success, print_error
        print_success(f"Created twin: {twin_name}")
        return twin
    except Exception as e:
        from ..utils import print_error
        print_error(f"Failed to create twin: {e}")
        return None


def _select_environment(client: Any, yes: bool) -> str | None:
    """Prompt user to select an environment."""
    try:
        environments = client.environments.list()
        
        if not environments:
            console.print("[yellow]No environments found. Creating one...[/yellow]")
            
            env_name = "Camera Environment" if yes else Prompt.ask("Environment name", default="Camera Environment")
            
            # Need to create workspace/project first
            projects = client.projects.list()
            if not projects:
                workspaces = client.workspaces.list()
                if not workspaces:
                    workspace = client.workspaces.create(name="Default Workspace")
                    workspace_id = workspace.uuid
                else:
                    workspace_id = workspaces[0].uuid
                
                project = client.projects.create(name="Default Project", workspace_id=str(workspace_id))
                project_id = project.uuid
            else:
                project_id = projects[0].uuid
            
            env = client.environments.create(name=env_name, project_id=str(project_id))
            return str(env.uuid)
        
        if yes:
            # Use first environment
            return str(environments[0].uuid)
        
        console.print("\n[bold]Select environment:[/bold]")
        for i, env in enumerate(environments[:10], 1):
            env_name = getattr(env, 'name', 'Unknown')
            env_uuid = str(getattr(env, 'uuid', ''))
            console.print(f"  {i}. {env_name} [dim]({env_uuid[:8]}...)[/dim]")
        console.print(f"  {len(environments[:10]) + 1}. [Create new environment]")
        
        choice = Prompt.ask("Select", default="1")
        
        try:
            idx = int(choice) - 1
            if idx < len(environments[:10]):
                return str(environments[idx].uuid)
            else:
                # Create new
                env_name = Prompt.ask("Environment name", default="Camera Environment")
                projects = client.projects.list()
                project_id = projects[0].uuid if projects else None
                
                if not project_id:
                    from ..utils import print_error
                    print_error("No project found to create environment")
                    return None
                
                env = client.environments.create(name=env_name, project_id=str(project_id))
                return str(env.uuid)
        except (ValueError, IndexError):
            from ..utils import print_error
            print_error("Invalid choice")
            return None
            
    except Exception as e:
        from ..utils import print_error
        print_error(f"Error listing environments: {e}")
        return None


def _configure_edge(
    client: Any,
    twin: Any,
    asset: dict,
    fingerprint: str,
    device_info: dict,
    yes: bool,
) -> dict | None:
    """Configure edge settings interactively or from existing config."""
    twin_uuid = str(getattr(twin, 'uuid', ''))
    metadata = getattr(twin, 'metadata', {}) or {}
    edge_configs = metadata.get('edge_configs', {})
    
    # Check for existing config
    existing_config = edge_configs.get(fingerprint)
    
    if existing_config:
        console.print("\n[cyan]Found existing config for this device.[/cyan]")
        cameras = existing_config.get('cameras', [])
        if cameras:
            console.print(f"[dim]  Cameras: {len(cameras)}[/dim]")
        
        if yes or Confirm.ask("Use existing config?", default=True):
            # Just update last_sync and return
            existing_config['last_sync'] = datetime.now(timezone.utc).isoformat()
            return existing_config
    
    # Interactive configuration
    console.print("\n[bold]Configure camera:[/bold]")
    
    # Source type - use centralized constants
    from shared_constants import CAMERA_SOURCE_TYPES, CAMERA_SOURCE_TYPE_RTSP
    
    if not yes:
        console.print("\n  Source type:")
        for i, st in enumerate(CAMERA_SOURCE_TYPES, 1):
            console.print(f"    {i}. {st}")
        source_choice = Prompt.ask("  Select", default="1")
        source_type = CAMERA_SOURCE_TYPES[int(source_choice) - 1] if source_choice.isdigit() else CAMERA_SOURCE_TYPE_RTSP
    else:
        source_type = CAMERA_SOURCE_TYPE_RTSP
    
    cameras = []
    
    if source_type == "RTSP":
        source = Prompt.ask("  RTSP URL", default="rtsp://") if not yes else "rtsp://"
        username = Prompt.ask("  Username", default="admin") if not yes else "admin"
        password = Prompt.ask("  Password", password=True) if not yes else ""
        fps = int(Prompt.ask("  FPS", default="10")) if not yes else 10
        
        cameras.append({
            "camera_id": "default",
            "source": source,
            "fps": fps,
            # Note: username/password stored in local .env only
        })
        
        # Store credentials for local .env
        config_secrets = {
            "username": username,
            "password": password,
        }
    elif source_type == "USB":
        device_id = Prompt.ask("  Device ID", default="0") if not yes else "0"
        fps = int(Prompt.ask("  FPS", default="30")) if not yes else 30
        
        cameras.append({
            "camera_id": "default",
            "source": int(device_id),
            "fps": fps,
        })
        config_secrets = {}
    else:  # RealSense
        fps = int(Prompt.ask("  FPS", default="30")) if not yes else 30
        enable_depth = Confirm.ask("  Enable depth?", default=True) if not yes else True
        
        cameras.append({
            "camera_id": "default",
            "source": "realsense",
            "camera_type": "realsense",
            "fps": fps,
            "enable_depth": enable_depth,
        })
        config_secrets = {}
    
    config = {
        "cameras": cameras,
        "models": [],
        "device_info": device_info,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_sync": datetime.now(timezone.utc).isoformat(),
        "_secrets": config_secrets,  # Not saved to cloud
    }
    
    return config


def _register_and_pair_edge(twin_uuid: str, fingerprint: str, device_info: dict, config: dict) -> str | None:
    """Register edge device and pair it to the twin using new API.
    
    Returns edge_uuid on success, None on failure (fallback to legacy).
    """
    import platform
    import httpx
    
    from ..config import get_api_url
    from ..credentials import load_credentials
    from ..utils import print_warning
    
    creds = load_credentials()
    if not creds or not creds.token:
        return None
    
    base_url = get_api_url()
    headers = {"Authorization": f"Bearer {creds.token}"}
    
    try:
        # Step 1: Register/discover edge device
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
        
        if not edge_uuid:
            return None
        
        # Step 2: Pair twin to edge with camera config
        pair_url = f"{base_url}/api/v1/edges/{edge_uuid}/pair"
        
        # Extract camera config from the config dict
        cameras = config.get('cameras', [])
        camera_config = cameras[0] if cameras else {}
        
        # Remove secrets from camera config (stored locally only)
        camera_config_clean = {k: v for k, v in camera_config.items() if k not in ('username', 'password')}
        
        pair_payload = {
            "twin_uuid": twin_uuid,
            "camera_config": camera_config_clean,
        }
        
        with httpx.Client() as http_client:
            response = http_client.post(
                pair_url,
                json=pair_payload,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
        
        console.print(f"[dim]Edge registered: {edge_uuid[:8]}...[/dim]")
        return edge_uuid
        
    except Exception as e:
        print_warning(f"New API failed, using legacy: {e}")
        return None


def _save_config_to_twin(client: Any, twin_uuid: str, fingerprint: str, config: dict):
    """Save edge config to twin metadata (legacy fallback)."""
    # Remove secrets before saving to cloud
    config_for_cloud = {k: v for k, v in config.items() if not k.startswith('_')}
    
    try:
        twin = client.twins.get(twin_uuid)
        metadata = getattr(twin, 'metadata', {}) or {}
        edge_configs = metadata.get('edge_configs', {})
        edge_configs[fingerprint] = config_for_cloud
        metadata['edge_configs'] = edge_configs
        
        client.twins.update(twin_uuid, metadata=metadata)
    except Exception as e:
        from ..utils import print_warning
        print_warning(f"Could not save to cloud: {e}")


def _write_local_env(twin_uuid: str, config: dict, fingerprint: str):
    """Write .env file locally using shared utility."""
    from ..utils import write_edge_env
    
    secrets = config.get('_secrets', {})
    cameras = config.get('cameras', [])
    
    write_edge_env(
        target_dir=".",
        twin_uuid=twin_uuid,
        cameras=cameras,
        fingerprint=fingerprint,
        username=secrets.get('username'),
        password=secrets.get('password'),
        generator="cyberwave connect",
    )


def get_device_info() -> dict:
    """Import and return device info."""
    from ..fingerprint import get_device_info as _get_device_info
    return _get_device_info()
