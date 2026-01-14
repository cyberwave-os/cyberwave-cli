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

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


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
    from ..utils import get_sdk_client
    
    # Get SDK client
    client = get_sdk_client()
    if not client:
        console.print("[red]Not authenticated. Run 'cyberwave login' first.[/red]")
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
        console.print(f"\n[red]{e}[/red]")
        return
    
    # 2. Find or create twin
    if twin_uuid:
        # Use specified twin
        try:
            twin = client.twins.get(twin_uuid)
            twin_name = getattr(twin, 'name', 'Unknown')
            console.print(f"\n[cyan]Using twin:[/cyan] {twin_name}")
        except Exception as e:
            console.print(f"\n[red]Twin not found: {twin_uuid}[/red]")
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
        console.print(f"\n[green]✓[/green] Twin created: {twin_name} ({twin_uuid})")
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
    
    # 4. Save config to cloud + local
    _save_config_to_twin(client, twin_uuid, fingerprint, config)
    _write_local_env(twin_uuid, config, fingerprint)
    
    console.print(f"\n[green]✓[/green] Connected!")
    console.print(f"\n[bold]Saved to:[/bold]")
    console.print(f"  • Cloud: twin/{twin_uuid}/edge_configs/{fingerprint[:20]}...")
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
        except Exception:
            pass  # Continue to create new twin
    
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
        console.print(f"[green]✓[/green] Created twin: {twin_name}")
        return twin
    except Exception as e:
        console.print(f"[red]Failed to create twin: {e}[/red]")
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
                    console.print("[red]No project found to create environment[/red]")
                    return None
                
                env = client.environments.create(name=env_name, project_id=str(project_id))
                return str(env.uuid)
        except (ValueError, IndexError):
            console.print("[red]Invalid choice[/red]")
            return None
            
    except Exception as e:
        console.print(f"[red]Error listing environments: {e}[/red]")
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
    
    # Source type
    source_types = ["RTSP", "USB", "RealSense"]
    if not yes:
        console.print("\n  Source type:")
        for i, st in enumerate(source_types, 1):
            console.print(f"    {i}. {st}")
        source_choice = Prompt.ask("  Select", default="1")
        source_type = source_types[int(source_choice) - 1] if source_choice.isdigit() else "RTSP"
    else:
        source_type = "RTSP"
    
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


def _save_config_to_twin(client: Any, twin_uuid: str, fingerprint: str, config: dict):
    """Save edge config to twin metadata."""
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
        console.print(f"[yellow]Warning: Could not save to cloud: {e}[/yellow]")


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
