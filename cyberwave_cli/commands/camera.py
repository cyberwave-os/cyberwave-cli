"""Camera edge setup command for the Cyberwave CLI."""

import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.prompt import Prompt

from ..config import (
    CAMERA_EDGE_DEFAULT_DIR,
    CAMERA_EDGE_REPO_URL,
    clean_subprocess_env,
    get_api_url,
)
from ..credentials import load_credentials

console = Console()


def get_sdk_client(token: str):
    """Get Cyberwave SDK client instance."""
    try:
        from cyberwave import Cyberwave
        return Cyberwave(base_url=get_api_url(), token=token)
    except ImportError:
        console.print("[red]✗[/red] Cyberwave SDK not installed.")
        console.print("[dim]Install with: pip install cyberwave[/dim]")
        return None


def run_command(cmd: list[str], cwd: Path | None = None) -> bool:
    """Run a shell command and return success status."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=clean_subprocess_env(),
        )
        return result.returncode == 0
    except Exception:
        return False


def check_git_installed() -> bool:
    """Check if git is installed."""
    return run_command(["git", "--version"])


def list_environments_sdk(client) -> list[dict]:
    """List existing environments via the SDK."""
    try:
        envs = client.environments.list()
        # Convert SDK objects to dicts for compatibility
        return [{"uuid": str(e.uuid), "name": e.name} for e in envs]
    except Exception:
        return []


def create_environment_sdk(client, name: str, description: str = "") -> Optional[dict]:
    """Create a new environment via the SDK."""
    try:
        # Need a project first - get or create one
        projects = client.projects.list()
        if not projects:
            # Create a workspace and project
            workspaces = client.workspaces.list()
            if not workspaces:
                workspace = client.workspaces.create(name="Camera Workspace")
                workspace_id = workspace.uuid
            else:
                workspace_id = workspaces[0].uuid
            
            project = client.projects.create(
                name="Camera Project",
                workspace_id=str(workspace_id),
            )
            project_id = project.uuid
        else:
            project_id = projects[0].uuid
        
        env = client.environments.create(
            name=name,
            project_id=str(project_id),
        )
        return {"uuid": str(env.uuid), "name": env.name}
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to create environment: {e}")
        return None


def get_camera_asset_sdk(client) -> Optional[dict]:
    """Get or find a camera asset from the catalog via the SDK."""
    try:
        assets = client.assets.list()
        
        # Look for an asset with camera-like capabilities or name
        for asset in assets:
            name = getattr(asset, "name", "").lower()
            capabilities = getattr(asset, "capabilities", {}) or {}
            sensors = capabilities.get("sensors", []) if isinstance(capabilities, dict) else []

            # Check if it has camera sensors or camera in the name
            has_camera_sensor = any(
                s.get("type") in ("rgb", "depth", "camera") for s in sensors
            )
            if has_camera_sensor or "camera" in name:
                return {"uuid": str(asset.uuid), "name": asset.name}

        # If no camera asset found, return the first asset as fallback
        if assets:
            return {"uuid": str(assets[0].uuid), "name": assets[0].name}
        return None
    except Exception:
        return None


def create_twin_sdk(
    client,
    name: str,
    environment_uuid: str,
    asset_uuid: str,
) -> Optional[dict]:
    """Create a new twin via the SDK."""
    try:
        twin = client.twins.create(
            name=name,
            environment_id=environment_uuid,
            asset_id=asset_uuid,
        )
        return {"uuid": str(twin.uuid), "name": twin.name}
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to create twin: {e}")
        return None


def write_env_file(
    target_path: Path,
    token: str,
    twin_uuid: str,
    base_url: str | None = None,
    camera_id: int = 0,
    camera_fps: int = 10,
    camera_url: str | None = None,
    camera_username: str | None = None,
    camera_password: str | None = None,
) -> bool:
    """Write the .env file with credentials and camera configuration."""
    # Build cameras JSON if IP camera URL is provided
    cameras_config = ""
    if camera_url:
        import json
        camera_entry = {
            "camera_id": "default",
            "source": camera_url,
            "fps": camera_fps,
        }
        if camera_username:
            camera_entry["username"] = camera_username
        if camera_password:
            camera_entry["password"] = camera_password
        cameras_config = f"\n# Multi-camera config (JSON array)\nCAMERAS='{json.dumps([camera_entry])}'"

    env_content = f"""# Cyberwave Edge Configuration
# Generated by cyberwave-cli

# Required
CYBERWAVE_API_KEY={token}
CYBERWAVE_TWIN_UUID={twin_uuid}

# API Settings
CYBERWAVE_BASE_URL={base_url or get_api_url()}

# Device Identification
# CYBERWAVE_EDGE_UUID=edge-device-001

# Camera Settings (legacy single-camera mode)
CAMERA_ID={camera_id}
CAMERA_FPS={camera_fps}
{cameras_config}
# Multi-camera examples:
# CAMERAS='[
#   {{"camera_id": "local", "source": 0, "fps": 15}},
#   {{"camera_id": "ip_cam", "source": "rtsp://192.168.1.100:554/stream", "username": "admin", "password": "pass", "fps": 10}},
#   {{"camera_id": "nvr_ch1", "source": "rtsp://nvr:554/ch1/main", "channel": 1, "username": "admin", "password": "nvrpass"}}
# ]'

# Logging
LOG_LEVEL=INFO
"""
    try:
        env_file = target_path / ".env"
        env_file.write_text(env_content)
        return True
    except Exception as e:
        console.print(f"[red]✗[/red] Error writing .env file: {e}")
        return False


@click.command()
@click.argument("path", default=CAMERA_EDGE_DEFAULT_DIR, required=False)
@click.option(
    "--environment-uuid",
    "-e",
    help="UUID of an existing environment to use",
)
@click.option(
    "--environment-name",
    "-n",
    help="Name for a new environment (creates one if --environment-uuid not provided)",
)
@click.option(
    "--twin-uuid",
    "-t",
    help="UUID of an existing twin to use (skips twin creation)",
)
@click.option(
    "--twin-name",
    help="Name for the camera twin (default: prompted or auto-generated)",
)
@click.option(
    "--camera-id",
    "-c",
    type=int,
    default=0,
    help="Local camera device index (default: 0)",
)
@click.option(
    "--camera-fps",
    "-f",
    type=int,
    default=10,
    help="Frames per second (default: 10)",
)
@click.option(
    "--camera-url",
    "-u",
    help="IP camera URL (RTSP/HTTP). Example: rtsp://192.168.1.100:554/stream",
)
@click.option(
    "--camera-user",
    help="Username for IP camera authentication",
)
@click.option(
    "--camera-pass",
    help="Password for IP camera authentication",
)
@click.option(
    "--local-edge",
    type=click.Path(exists=True),
    help="Path to local edge code (skips git clone)",
)
@click.option(
    "--env-only",
    is_flag=True,
    help="Only generate .env file (use with --local-edge)",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompts (non-interactive mode)",
)
def camera(
    path: str,
    environment_uuid: str | None,
    environment_name: str | None,
    twin_uuid: str | None,
    twin_name: str | None,
    camera_id: int,
    camera_fps: int,
    camera_url: str | None,
    camera_user: str | None,
    camera_pass: str | None,
    local_edge: str | None,
    env_only: bool,
    yes: bool,
) -> None:
    """Set up camera edge software for streaming to Cyberwave.

    Clones the cyberwave-edge-python repository and configures it with your
    credentials. Supports local cameras and IP cameras (RTSP/HTTP).

    PATH is the target directory for the project (default: ./cyberwave-camera)

    \b
    Examples:
        # Local webcam
        cyberwave-cli camera
        cyberwave-cli camera -c 0 -f 15

        # IP camera with authentication
        cyberwave-cli camera -u rtsp://192.168.1.100:554/stream --camera-user admin --camera-pass secret

        # Use local edge code (development)
        cyberwave-cli camera --local-edge ./cyberwave-edge-python --env-only

        # Non-interactive with existing twin
        cyberwave-cli camera -t TWIN_UUID -e ENV_UUID -y

        # Named environment
        cyberwave-cli camera -n "Warehouse Cameras"
    """
    # Determine target path
    if local_edge:
        target_path = Path(local_edge).expanduser().resolve()
    else:
        target_path = Path(path).expanduser().resolve()

    # Check authentication
    creds = load_credentials()
    if not creds or not creds.token:
        console.print("\n[red]✗[/red] Not logged in.")
        console.print("[dim]Run [bold]cyberwave-cli login[/bold] or [bold]cyberwave-cli configure --token YOUR_TOKEN[/bold][/dim]")
        raise click.Abort()

    token = creds.token
    
    # Initialize SDK client
    sdk_client = get_sdk_client(token)
    if not sdk_client:
        raise click.Abort()

    # Skip git check if using local edge or env-only mode
    if not local_edge and not env_only:
        if not check_git_installed():
            console.print("[red]✗[/red] Git is not installed. Please install git first.")
            raise click.Abort()

        # Check if target directory already exists
        if target_path.exists():
            console.print(f"[red]✗[/red] Directory already exists: [bold]{target_path}[/bold]")
            if not yes and not click.confirm("Do you want to overwrite it?"):
                raise click.Abort()
            shutil.rmtree(target_path)

    # Handle environment selection/creation
    env_uuid = environment_uuid
    env_name = environment_name

    if not env_uuid:
        # No environment UUID provided
        console.print("\n[bold]Environment Setup[/bold]")

        # List existing environments
        existing_envs = list_environments_sdk(sdk_client)

        if existing_envs and not yes:
            console.print("\n[dim]Found existing environments:[/dim]")
            for i, env in enumerate(existing_envs[:10], 1):  # Show max 10
                env_display_name = env.get("name", "Unnamed")
                env_display_uuid = env.get("uuid", "")
                console.print(f"  {i}. {env_display_name} [dim]({env_display_uuid})[/dim]")

            choice = Prompt.ask(
                "\n[bold]Enter environment number to use, or press Enter to create new[/bold]",
                default="",
            )

            if choice.strip():
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(existing_envs):
                        env_uuid = existing_envs[idx].get("uuid")
                        env_name = existing_envs[idx].get("name")
                        console.print(
                            f"[green]✓[/green] Using environment: [bold]{env_name}[/bold]"
                        )
                except ValueError:
                    pass

        if not env_uuid:
            # Create new environment
            if not env_name:
                if yes:
                    # Auto-generate name in non-interactive mode
                    env_name = f"Camera Environment"
                    if camera_url:
                        # Extract IP from URL for naming
                        import re
                        ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', camera_url)
                        if ip_match:
                            env_name = f"Camera @ {ip_match.group(1)}"
                else:
                    env_name = Prompt.ask(
                        "[bold]Enter name for new environment[/bold]",
                        default="Camera Environment",
                    )

            console.print(f"\n[dim]Creating environment '{env_name}'...[/dim]")
            env_data = create_environment_sdk(sdk_client, env_name)

            if not env_data:
                console.print("[red]✗[/red] Failed to create environment")
                raise click.Abort()

            env_uuid = env_data.get("uuid")
            console.print(f"[green]✓[/green] Created environment: [bold]{env_name}[/bold]")
            console.print(f"[dim]  UUID: {env_uuid}[/dim]")

    # Handle twin - use provided UUID or create new
    final_twin_uuid = twin_uuid

    if not final_twin_uuid:
        # Find or get a camera asset
        console.print("\n[dim]Finding camera asset...[/dim]")
        camera_asset = get_camera_asset_sdk(sdk_client)

        if not camera_asset:
            console.print(
                "[yellow]⚠[/yellow] No camera asset found in catalog. "
                "The twin will be created without a specific asset."
            )
            asset_uuid = None
        else:
            asset_uuid = camera_asset.get("uuid")
            asset_name = camera_asset.get("name", "Unknown")
            console.print(f"[green]✓[/green] Using asset: [bold]{asset_name}[/bold]")

        # Determine twin name
        final_twin_name = twin_name
        if not final_twin_name:
            if yes:
                # Auto-generate name
                if camera_url:
                    import re
                    ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', camera_url)
                    final_twin_name = f"Camera {ip_match.group(1)}" if ip_match else "Camera Twin"
                else:
                    final_twin_name = f"Camera {camera_id}"
            else:
                final_twin_name = Prompt.ask(
                    "[bold]Enter name for the camera twin[/bold]",
                    default="Camera Twin",
                )

        console.print(f"\n[dim]Creating twin '{final_twin_name}'...[/dim]")

        if asset_uuid:
            twin_data = create_twin_sdk(sdk_client, final_twin_name, env_uuid, asset_uuid)
        else:
            console.print("[yellow]⚠[/yellow] Skipping twin creation - no asset available")
            console.print("[dim]You'll need to create a twin manually in the Cyberwave dashboard[/dim]")
            twin_data = None

        if twin_data:
            final_twin_uuid = twin_data.get("uuid")
            console.print(f"[green]✓[/green] Created twin: [bold]{final_twin_name}[/bold]")
            console.print(f"[dim]  UUID: {final_twin_uuid}[/dim]")
        elif not yes:
            final_twin_uuid = Prompt.ask(
                "[bold]Enter existing twin UUID (or create one in dashboard)[/bold]",
                default="",
            )

        if not final_twin_uuid:
            console.print("[red]✗[/red] Twin UUID is required for camera setup")
            raise click.Abort()
    else:
        console.print(f"[green]✓[/green] Using existing twin: [dim]{final_twin_uuid}[/dim]")

    # Handle edge software setup
    if local_edge or env_only:
        console.print(f"\n[green]✓[/green] Using local edge code: [bold]{target_path}[/bold]")
    else:
        # Clone the repository
        console.print("\n[bold]Cloning camera edge software...[/bold]")
        console.print(f"[dim]→ {CAMERA_EDGE_REPO_URL}[/dim]")

        result = subprocess.run(
            ["git", "clone", CAMERA_EDGE_REPO_URL, str(target_path)],
            capture_output=True,
            text=True,
            env=clean_subprocess_env(),
        )

        if result.returncode != 0:
            console.print("[red]✗[/red] Failed to clone repository")
            console.print(f"[dim]{result.stderr}[/dim]")
            raise click.Abort()

        console.print(f"[green]✓[/green] Cloned to [bold]{target_path}[/bold]")

    # Write the .env file
    console.print("\n[bold]Configuring credentials...[/bold]")
    if write_env_file(
        target_path,
        token,
        final_twin_uuid,
        base_url=get_api_url(),
        camera_id=camera_id,
        camera_fps=camera_fps,
        camera_url=camera_url,
        camera_username=camera_user,
        camera_password=camera_pass,
    ):
        console.print("[green]✓[/green] Created .env file with credentials")
        if camera_url:
            console.print(f"[green]✓[/green] Configured IP camera: [dim]{camera_url}[/dim]")
        else:
            console.print(f"[green]✓[/green] Configured local camera: [dim]device {camera_id}[/dim]")
    else:
        raise click.Abort()

    # Print success and next steps
    console.print("\n[bold green]✓ Camera setup completed![/bold green]")

    console.print("\n[bold]Environment Details:[/bold]")
    console.print(f"  • Environment: [bold]{env_name or 'Unknown'}[/bold]")
    console.print(f"  • Environment UUID: [dim]{env_uuid}[/dim]")
    console.print(f"  • Twin UUID: [dim]{final_twin_uuid}[/dim]")

    # Generate environment URL
    api_base = get_api_url().replace("api.", "").replace("/api", "")
    if "localhost" in api_base or "127.0.0.1" in api_base:
        env_url = f"http://localhost:3000/environments/{env_uuid}"
    else:
        env_url = f"https://cyberwave.com/environments/{env_uuid}"

    console.print("\n[bold]View your environment:[/bold]")
    console.print(f"  {env_url}")

    if local_edge or env_only:
        console.print("\n[bold]Start streaming:[/bold]")
        console.print(f"  [dim]cd {target_path} && python -m cyberwave_edge.service[/dim]")
    else:
        console.print("\n[bold]Next steps:[/bold]")
        console.print(f"  1. [dim]cd {target_path}[/dim]")
        console.print("  2. [dim]pip install -e .[/dim]")
        console.print("  3. [dim]python -m cyberwave_edge.service[/dim]")

    console.print()
    console.print("[dim]Documentation: https://docs.cyberwave.com/edge[/dim]")
