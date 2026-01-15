"""
CLI commands for managing the edge node service.

Example usage:
    # Start the edge node
    cyberwave edge start
    
    # Start with specific config
    cyberwave edge start --env-file /path/to/.env
    
    # Check status
    cyberwave edge status
    
    # Install edge dependencies (ultralytics, opencv, etc.)
    cyberwave edge install-deps
    
    # Show device fingerprint
    cyberwave edge whoami
    
    # Pull config from twin
    cyberwave edge pull --twin-uuid UUID
    cyberwave edge pull --environment-uuid UUID
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


@click.group()
def edge():
    """Manage the edge node service."""
    pass


@edge.command("start")
@click.option("--env-file", type=click.Path(exists=True), default=".env", help="Path to .env file")
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (don't daemonize)")
def start_edge(env_file, foreground):
    """Start the edge node service."""
    env_path = Path(env_file).resolve()
    
    if not env_path.exists():
        console.print(f"[red]Error: .env file not found at {env_path}[/red]")
        console.print("[dim]Run 'cyberwave camera' first to configure the edge node[/dim]")
        return

    console.print(f"[cyan]Starting edge node with config: {env_path}[/cyan]")
    
    # Change to the directory containing .env
    work_dir = env_path.parent
    
    # Set environment
    env = os.environ.copy()
    env["DOTENV_PATH"] = str(env_path)
    
    try:
        if foreground:
            # Run in foreground
            console.print("[green]Running edge node in foreground (Ctrl+C to stop)...[/green]")
            subprocess.run(
                [sys.executable, "-m", "cyberwave_edge.service"],
                cwd=work_dir,
                env=env,
            )
        else:
            # Run in background
            console.print("[green]Starting edge node in background...[/green]")
            process = subprocess.Popen(
                [sys.executable, "-m", "cyberwave_edge.service"],
                cwd=work_dir,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            console.print(f"[green]✓ Edge node started (PID: {process.pid})[/green]")
            console.print(f"[dim]Logs: Check terminal or use 'cyberwave edge logs'[/dim]")
            
    except FileNotFoundError:
        console.print("[red]Error: cyberwave_edge package not found[/red]")
        console.print("[dim]Install it with: pip install -e cyberwave-edges/cyberwave-edge-python[/dim]")


@edge.command("stop")
def stop_edge():
    """Stop the edge node service."""
    import signal
    
    # Find and kill edge processes
    try:
        result = subprocess.run(
            ["pgrep", "-f", "cyberwave_edge.service"],
            capture_output=True,
            text=True,
        )
        pids = result.stdout.strip().split("\n")
        pids = [p for p in pids if p]
        
        if not pids:
            console.print("[yellow]No running edge node found[/yellow]")
            return
            
        for pid in pids:
            os.kill(int(pid), signal.SIGTERM)
            console.print(f"[green]✓ Stopped edge node (PID: {pid})[/green]")
            
    except Exception as e:
        console.print(f"[red]Error stopping edge node: {e}[/red]")


@edge.command("status")
def status_edge():
    """Check edge node status."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "cyberwave_edge.service"],
            capture_output=True,
            text=True,
        )
        pids = result.stdout.strip().split("\n")
        pids = [p for p in pids if p]
        
        if pids:
            console.print(f"[green]✓ Edge node is running (PIDs: {', '.join(pids)})[/green]")
        else:
            console.print("[yellow]Edge node is not running[/yellow]")
            
    except Exception as e:
        console.print(f"[red]Error checking status: {e}[/red]")


@edge.command("install-deps")
@click.option("--runtime", "-r", multiple=True, help="Specific runtime to install (ultralytics, opencv)")
def install_deps(runtime):
    """Install edge ML dependencies."""
    
    packages = {
        "ultralytics": ["ultralytics>=8.0.0"],
        "opencv": ["opencv-python>=4.8.0"],
        "onnx": ["onnxruntime>=1.15.0"],
        "tflite": ["tflite-runtime"],
    }
    
    if runtime:
        to_install = []
        for r in runtime:
            if r in packages:
                to_install.extend(packages[r])
            else:
                console.print(f"[yellow]Unknown runtime: {r}[/yellow]")
    else:
        # Install common ones
        to_install = packages["ultralytics"] + packages["opencv"]
    
    if not to_install:
        console.print("[yellow]Nothing to install[/yellow]")
        return
    
    console.print(f"[cyan]Installing: {', '.join(to_install)}[/cyan]")
    
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install"] + to_install,
            check=True,
        )
        console.print("[green]✓ Dependencies installed successfully[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error installing dependencies: {e}[/red]")


@edge.command("logs")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.option("--lines", "-n", default=50, help="Number of lines to show")
def show_logs(follow, lines):
    """Show edge node logs."""
    log_file = Path("/tmp/edge_service.log")
    
    if not log_file.exists():
        console.print("[yellow]No log file found at /tmp/edge_service.log[/yellow]")
        return
    
    if follow:
        subprocess.run(["tail", "-f", str(log_file)])
    else:
        subprocess.run(["tail", f"-{lines}", str(log_file)])


@edge.command("sync-workflows")
@click.option("--twin-uuid", required=True, help="Twin UUID to sync workflows for")
def sync_workflows(twin_uuid):
    """
    Trigger workflow sync on the edge node.
    
    This command sends an MQTT message to the edge node to re-sync
    model bindings from active workflows in the backend.
    """
    from ..utils import get_sdk_client, print_error
    
    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return
    
    console.print(f"[cyan]Sending sync_workflows command to twin {twin_uuid}...[/cyan]")
    
    try:
        # Publish command via MQTT
        client.mqtt.publish_command_message(twin_uuid, {"command": "sync_workflows"})
        from ..utils import print_success
        print_success("Command sent. Check edge logs for results.")
        console.print("[dim]Use 'cyberwave edge list-models --twin-uuid ...' to see loaded models[/dim]")
    except Exception as e:
        print_error(f"Error sending command: {e}")


@edge.command("list-models")
@click.option("--twin-uuid", required=True, help="Twin UUID to query")
def list_models(twin_uuid):
    """
    List model bindings loaded on the edge node.
    
    Shows which ML models are configured to run on the edge
    for the specified twin.
    """
    import json
    import time
    
    from ..utils import get_sdk_client, print_error
    
    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return
    
    console.print(f"[cyan]Querying model bindings for twin {twin_uuid}...[/cyan]")
    
    response_received = {"data": None}
    
    def on_response(data):
        if isinstance(data, dict) and data.get("status") == "ok":
            response_received["data"] = data
    
    try:
        # Subscribe to command responses
        client.mqtt.subscribe_command_message(twin_uuid, on_response)
        
        # Send list_models command
        client.mqtt.publish_command_message(twin_uuid, {"command": "list_models"})
        
        # Wait for response (with timeout)
        for _ in range(30):  # 3 second timeout
            time.sleep(0.1)
            if response_received["data"]:
                break
        
        if response_received["data"]:
            bindings = response_received["data"].get("model_bindings", [])
            
            if not bindings:
                from ..utils import print_warning
                print_warning("No model bindings loaded on edge")
                console.print("[dim]Use 'cyberwave edge sync-workflows' to load from workflows[/dim]")
                return
            
            table = Table(title="Edge Model Bindings")
            table.add_column("Plugin", style="cyan")
            table.add_column("Model", style="green")
            table.add_column("Camera", style="yellow")
            table.add_column("Events", style="magenta")
            table.add_column("Confidence", style="blue")
            table.add_column("FPS", style="blue")
            
            for binding in bindings:
                table.add_row(
                    binding.get("plugin_id", "?"),
                    binding.get("model_id", "?"),
                    binding.get("camera_id", "default"),
                    ", ".join(binding.get("event_types", [])),
                    f"{binding.get('confidence_threshold', 0.5):.2f}",
                    f"{binding.get('inference_fps', 2.0):.1f}",
                )
            
            console.print(table)
        else:
            from ..utils import print_warning
            print_warning("No response from edge node (is it running?)")
            
    except Exception as e:
        print_error(f"Error: {e}")


# =============================================================================
# Device Fingerprint Commands
# =============================================================================


@edge.command("whoami")
def whoami():
    """
    Show device fingerprint and info.
    
    Displays the unique fingerprint for this device, which is used to identify
    this edge device when connecting to twins. The fingerprint is stable across
    sessions and derived from hardware characteristics.
    
    \b
    Example:
        cyberwave edge whoami
        
        Fingerprint: macbook-pro-a1b2c3d4e5f6
        Hostname:    macbook-pro.local
        Platform:    Darwin-arm64
        Python:      3.11.0
        MAC:         a4:83:e7:xx:xx:xx
    """
    from ..fingerprint import format_device_info_table, generate_fingerprint
    
    console.print("\n[bold]Device Information[/bold]\n")
    console.print(format_device_info_table())
    console.print()


# =============================================================================
# Config Sync Commands
# =============================================================================


@edge.command("pull")
@click.option("--twin-uuid", "-t", help="Twin UUID to pull config from")
@click.option("--environment-uuid", "-e", help="Environment UUID to pull all twins from")
@click.option("--target-dir", "-d", default=".", help="Directory to write .env file")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def pull_config(twin_uuid: str | None, environment_uuid: str | None, target_dir: str, yes: bool):
    """
    Pull edge configuration from twin or environment.
    
    Downloads the edge configuration stored in twin metadata and writes it to
    a local .env file. Supports pulling from a single twin or all twins in
    an environment.
    
    \b
    Examples:
        # Pull config for a specific twin
        cyberwave edge pull --twin-uuid abc-123
        
        # Pull configs for all twins in an environment
        cyberwave edge pull --environment-uuid env-456
        
        # Specify output directory
        cyberwave edge pull -t abc-123 -d ./my-edge
    """
    from ..fingerprint import generate_fingerprint, get_device_info
    from ..utils import get_sdk_client, print_error
    
    if not twin_uuid and not environment_uuid:
        print_error("Provide --twin-uuid or --environment-uuid", "Example: cyberwave edge pull --twin-uuid abc-123")
        return
    
    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return
    
    fingerprint = generate_fingerprint()
    console.print(f"\n[dim]Fingerprint: {fingerprint}[/dim]\n")
    
    try:
        if environment_uuid:
            _pull_environment_configs(client, environment_uuid, fingerprint, target_dir, yes)
        else:
            _pull_single_twin_config(client, twin_uuid, fingerprint, target_dir, yes)
    except Exception as e:
        print_error(str(e))


def _pull_single_twin_config(client: Any, twin_uuid: str, fingerprint: str, target_dir: str, yes: bool):
    """Pull config from a single twin."""
    twin = client.twins.get(twin_uuid)
    twin_name = getattr(twin, 'name', 'Unknown')
    metadata = getattr(twin, 'metadata', {}) or {}
    edge_configs = metadata.get('edge_configs', {})
    
    console.print(f"[cyan]Twin:[/cyan] {twin_name}")
    
    my_config = edge_configs.get(fingerprint)
    
    if my_config:
        from ..utils import print_success
        print_success("Found config for this device")
        console.print(f"[dim]  Registered: {my_config.get('registered_at', 'unknown')}[/dim]")
        
        cameras = my_config.get('cameras', [])
        if cameras:
            console.print(f"[dim]  Cameras: {len(cameras)} configured[/dim]")
    else:
        # No config for this fingerprint - check for other configs or default
        if edge_configs:
            from ..utils import print_warning
            print_warning("No config for this device fingerprint")
            console.print(f"\n[dim]Available configs from other devices:[/dim]")
            
            for i, (fp, cfg) in enumerate(edge_configs.items(), 1):
                device_info = cfg.get('device_info', {})
                hostname = device_info.get('hostname', 'unknown')
                registered = cfg.get('registered_at', 'unknown')[:10] if cfg.get('registered_at') else 'unknown'
                console.print(f"  {i}. {fp[:30]}... ({hostname}, {registered})")
            
            if not yes:
                choice = Prompt.ask(
                    "\n[bold]Copy config from which device? (number or 'n' to skip)[/bold]",
                    default="1"
                )
                
                if choice.lower() != 'n':
                    try:
                        idx = int(choice) - 1
                        source_fp = list(edge_configs.keys())[idx]
                        my_config = edge_configs[source_fp].copy()
                        from ..utils import print_success
                        print_success(f"Copying config from {source_fp[:20]}...")
                    except (ValueError, IndexError):
                        from ..utils import print_warning
                        print_warning("Invalid choice, skipping")
                        return
        else:
            # Check for default config
            default_config = metadata.get('default_edge_config')
            if default_config:
                from ..utils import print_warning
                print_warning("No config for this device, using default template")
                my_config = default_config.copy()
            else:
                from ..utils import print_error
                print_error("No configuration found for this twin", "Use 'cyberwave connect' to set up this twin")
                return
    
    if not my_config:
        return
    
    # Prompt for credentials (not stored in cloud)
    cameras = my_config.get('cameras', [])
    has_rtsp = any('rtsp://' in str(c.get('source', '')) for c in cameras)
    
    username = None
    password = None
    
    if has_rtsp and not yes:
        console.print("\n[bold]Enter credentials (stored locally only):[/bold]")
        username = Prompt.ask("  RTSP Username", default="admin")
        password = Prompt.ask("  RTSP Password", password=True)
    
    # Write .env file directly using shared utility
    from ..utils import write_edge_env, print_success
    write_edge_env(
        target_dir=target_dir,
        twin_uuid=twin_uuid,
        cameras=cameras,
        fingerprint=fingerprint,
        username=username,
        password=password,
        generator="cyberwave edge pull",
    )
    
    print_success(f"Config pulled to {target_dir}/.env")
    console.print("[dim]Run: python -m cyberwave_edge.service[/dim]")


def _pull_environment_configs(client: Any, env_uuid: str, fingerprint: str, target_dir: str, yes: bool):
    """Pull configs for all twins in an environment."""
    # Get environment info
    env = client.environments.get(env_uuid)
    env_name = getattr(env, 'name', 'Unknown')
    
    # Get twins in environment using SDK
    twins = client.twins.list(environment_id=env_uuid)
    
    if not twins:
        from ..utils import print_warning
        print_warning(f"No twins found in environment '{env_name}'")
        return
    
    console.print(f"[cyan]Environment:[/cyan] {env_name}")
    console.print(f"[cyan]Found {len(twins)} twin(s):[/cyan]\n")
    
    all_cameras = []
    twins_with_config = []
    twins_without_config = []
    
    for twin in twins:
        twin_name = getattr(twin, 'name', 'Unknown')
        twin_uuid = str(getattr(twin, 'uuid', ''))
        metadata = getattr(twin, 'metadata', {}) or {}
        edge_configs = metadata.get('edge_configs', {})
        
        my_config = edge_configs.get(fingerprint)
        
        if my_config:
            twins_with_config.append((twin, my_config))
            cameras = my_config.get('cameras', [])
            for cam in cameras:
                cam_copy = cam.copy()
                cam_copy['twin_uuid'] = twin_uuid
                cam_copy['twin_name'] = twin_name
                all_cameras.append(cam_copy)
            console.print(f"  [green]✓[/green] {twin_name} - {len(cameras)} camera(s)")
        else:
            twins_without_config.append(twin)
            console.print(f"  [yellow]○[/yellow] {twin_name} - no config for this device")
    
    if not all_cameras and not twins_without_config:
        from ..utils import print_warning
        print_warning("No configurations to pull")
        return
    
    if twins_without_config:
        from ..utils import print_warning
        print_warning(f"{len(twins_without_config)} twin(s) need configuration")
    
    if not yes and not Confirm.ask("\nPull available configs?", default=True):
        return
    
    # Prompt for shared credentials
    has_rtsp = any('rtsp://' in str(c.get('source', '')) for c in all_cameras)
    
    username = None
    password = None
    
    if has_rtsp and not yes:
        console.print("\n[bold]Enter shared credentials (stored locally only):[/bold]")
        username = Prompt.ask("  RTSP Username", default="admin")
        password = Prompt.ask("  RTSP Password", password=True)
    
    # Write .env file with all cameras directly using shared utility
    if all_cameras:
        from ..utils import write_edge_env, print_success
        # Extract primary twin from cameras
        twin_uuids = set(cam.get("twin_uuid", "") for cam in all_cameras if cam.get("twin_uuid"))
        primary_twin = list(twin_uuids)[0] if twin_uuids else ""
        
        write_edge_env(
            target_dir=target_dir,
            twin_uuid=primary_twin,
            cameras=all_cameras,
            fingerprint=fingerprint,
            username=username,
            password=password,
            generator="cyberwave edge pull",
        )
        
        print_success(f"Config pulled to {target_dir}/.env")
        console.print(f"[dim]  {len(all_cameras)} camera(s) from {len(twins_with_config)} twin(s)[/dim]")
        console.print("[dim]Run: python -m cyberwave_edge.service[/dim]")
    else:
        from ..utils import print_warning
        print_warning("No cameras configured. Use 'cyberwave connect' to set up twins.")


@edge.command("health")
@click.option("--twin-uuid", "-t", required=True, help="Twin UUID to check health for")
@click.option("--timeout", default=5, help="Timeout in seconds to wait for response")
@click.option("--watch", "-w", is_flag=True, help="Continuously watch health status")
def health(twin_uuid: str, timeout: int, watch: bool):
    """
    Check edge health status via MQTT.
    
    Queries the edge service for real-time health status including:
    - Stream states (connected/failed/stale)
    - Frame rates and counts
    - WebRTC connection states
    - Automatic recovery status
    
    \b
    Examples:
        # One-time health check
        cyberwave edge health --twin-uuid abc-123
        
        # Watch health status continuously
        cyberwave edge health --twin-uuid abc-123 --watch
    """
    import json
    import time as time_module
    
    from ..utils import get_sdk_client, print_error, print_warning
    
    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return
    
    health_data: dict[str, Any] = {"received": False, "data": {}}
    
    def on_health_message(data):
        if isinstance(data, dict) and data.get("type") == "edge_health":
            health_data["received"] = True
            health_data["data"] = data
    
    try:
        # Subscribe to health topic
        prefix = client.mqtt.topic_prefix
        health_topic = f"{prefix}cyberwave/twin/{twin_uuid}/edge_health"
        
        client.mqtt._client.subscribe(health_topic)
        client.mqtt._client.on_message = lambda c, u, msg: on_health_message(
            json.loads(msg.payload.decode()) if msg.payload else {}
        )
        
        if watch:
            console.print(f"[cyan]Watching health for twin {twin_uuid}... (Ctrl+C to stop)[/cyan]\n")
            try:
                while True:
                    health_data["received"] = False
                    
                    # Wait for health message
                    start = time_module.time()
                    while not health_data["received"] and (time_module.time() - start) < timeout:
                        time_module.sleep(0.1)
                    
                    if health_data["received"]:
                        _display_health_status(health_data["data"])
                    else:
                        console.print("[yellow]No health data received (edge offline?)[/yellow]")
                    
                    time_module.sleep(max(1, timeout - 1))
                    
            except KeyboardInterrupt:
                console.print("\n[dim]Stopped watching.[/dim]")
        else:
            console.print(f"[cyan]Checking health for twin {twin_uuid}...[/cyan]\n")
            
            # Wait for health message with timeout
            start = time_module.time()
            while not health_data["received"] and (time_module.time() - start) < timeout:
                time_module.sleep(0.1)
            
            if health_data["received"]:
                _display_health_status(health_data["data"])
            else:
                print_warning("No health data received within timeout.")
                console.print("[dim]The edge service may be offline or not publishing health status.[/dim]")
                console.print("[dim]Ensure the edge service is running with health publishing enabled.[/dim]")
                
    except Exception as e:
        print_error(str(e))


def _display_health_status(data: dict):
    """Display health status in a formatted table."""
    from datetime import datetime
    
    edge_id = data.get("edge_id", "unknown")
    uptime = data.get("uptime_seconds", 0)
    streams = data.get("streams", {})
    stream_count = data.get("stream_count", 0)
    healthy_count = data.get("healthy_streams", 0)
    timestamp = data.get("timestamp", 0)
    
    # Format uptime
    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    uptime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    
    # Overall status
    if stream_count == 0:
        status = "[yellow]No streams[/yellow]"
    elif healthy_count == stream_count:
        status = "[green]Healthy[/green]"
    elif healthy_count > 0:
        status = "[yellow]Degraded[/yellow]"
    else:
        status = "[red]Unhealthy[/red]"
    
    console.print(f"Edge ID:     {edge_id}")
    console.print(f"Status:      {status}")
    console.print(f"Uptime:      {uptime_str}")
    console.print(f"Streams:     {healthy_count}/{stream_count} healthy")
    console.print(f"Last update: {datetime.fromtimestamp(timestamp).strftime('%H:%M:%S')}")
    
    if streams:
        console.print()
        table = Table(title="Stream Status")
        table.add_column("Camera", style="cyan")
        table.add_column("State", style="green")
        table.add_column("ICE", style="blue")
        table.add_column("FPS", style="yellow")
        table.add_column("Frames", style="magenta")
        table.add_column("Restarts", style="red")
        table.add_column("Stale", style="dim")
        
        for camera_id, stream_info in streams.items():
            conn_state = stream_info.get("connection_state", "unknown")
            ice_state = stream_info.get("ice_connection_state", "unknown")
            fps = stream_info.get("fps", 0)
            frames = stream_info.get("frames_sent", 0)
            restarts = stream_info.get("restart_count", 0)
            is_stale = stream_info.get("is_stale", False)
            
            # Color code connection state
            if conn_state == "connected":
                conn_display = f"[green]{conn_state}[/green]"
            elif conn_state == "failed":
                conn_display = f"[red]{conn_state}[/red]"
            else:
                conn_display = f"[yellow]{conn_state}[/yellow]"
            
            # Color code ICE state
            if ice_state in ("connected", "completed"):
                ice_display = f"[green]{ice_state}[/green]"
            elif ice_state == "failed":
                ice_display = f"[red]{ice_state}[/red]"
            else:
                ice_display = f"[yellow]{ice_state}[/yellow]"
            
            stale_display = "[red]Yes[/red]" if is_stale else "[green]No[/green]"
            
            table.add_row(
                camera_id,
                conn_display,
                ice_display,
                f"{fps:.1f}",
                str(frames),
                str(restarts),
                stale_display,
            )
        
        console.print(table)
    
    console.print()


@edge.command("remote-status")
@click.option("--twin-uuid", "-t", required=True, help="Twin UUID to check status for")
def remote_status(twin_uuid: str):
    """
    Check edge status from twin metadata (heartbeat).
    
    Queries the twin's metadata for the last heartbeat from this device's
    fingerprint. Shows online/offline status, uptime, and stream info.
    
    \b
    Example:
        cyberwave edge remote-status --twin-uuid abc-123
    """
    from ..fingerprint import generate_fingerprint
    from ..utils import get_sdk_client, print_error
    
    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return
    
    fingerprint = generate_fingerprint()
    
    try:
        twin = client.twins.get(twin_uuid)
        twin_name = getattr(twin, 'name', 'Unknown')
        metadata = getattr(twin, 'metadata', {}) or {}
        edge_configs = metadata.get('edge_configs', {})
        
        my_config = edge_configs.get(fingerprint)
        
        console.print(f"\n[bold]Edge Status for \"{twin_name}\"[/bold]")
        console.print("━" * 40)
        console.print(f"Fingerprint:    {fingerprint}")
        
        if not my_config:
            console.print(f"Status:         [yellow]Not registered[/yellow]")
            console.print("\n[dim]This device hasn't connected to this twin yet.[/dim]")
            console.print("[dim]Use 'cyberwave connect' or 'cyberwave edge pull' first.[/dim]")
            return
        
        # Check last heartbeat
        last_heartbeat = my_config.get('last_heartbeat')
        last_status = my_config.get('last_status', {})
        
        if last_heartbeat:
            try:
                hb_time = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                delta = now - hb_time
                
                if delta.total_seconds() < 60:
                    status = "[green]Online[/green]"
                    last_seen = f"{int(delta.total_seconds())} seconds ago"
                elif delta.total_seconds() < 300:
                    status = "[yellow]Stale[/yellow]"
                    last_seen = f"{int(delta.total_seconds() / 60)} minutes ago"
                else:
                    status = "[red]Offline[/red]"
                    last_seen = f"{int(delta.total_seconds() / 3600)} hours ago" if delta.total_seconds() > 3600 else f"{int(delta.total_seconds() / 60)} minutes ago"
                
                console.print(f"Status:         {status}")
                console.print(f"Last heartbeat: {last_seen}")
                
                # Show uptime if available
                uptime = last_status.get('uptime_seconds')
                if uptime:
                    days = uptime // 86400
                    hours = (uptime % 86400) // 3600
                    if days > 0:
                        console.print(f"Uptime:         {days} days, {hours} hours")
                    else:
                        console.print(f"Uptime:         {hours} hours")
                
                # Show streams if available
                streams = last_status.get('streams', {})
                if streams:
                    console.print(f"\nStreams:")
                    for stream_id, stream_info in streams.items():
                        stream_status = stream_info.get('status', 'unknown')
                        fps = stream_info.get('fps', '?')
                        res = stream_info.get('resolution', '?')
                        if stream_status == 'streaming':
                            console.print(f"  • {stream_id}: [green]{stream_status}[/green] ({fps} fps, {res})")
                        else:
                            console.print(f"  • {stream_id}: [yellow]{stream_status}[/yellow]")
                
            except Exception:
                console.print(f"Last heartbeat: {last_heartbeat}")
        else:
            registered = my_config.get('registered_at', 'unknown')
            console.print(f"Status:         [yellow]Never connected[/yellow]")
            console.print(f"Registered:     {registered}")
        
        console.print()
        
    except Exception as e:
        print_error(str(e))
