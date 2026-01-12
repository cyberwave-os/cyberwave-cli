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
"""

import os
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
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
    from cyberwave_cli.auth import get_authenticated_client
    
    client = get_authenticated_client()
    if not client:
        console.print("[red]Not authenticated. Run 'cyberwave login' first.[/red]")
        return
    
    console.print(f"[cyan]Sending sync_workflows command to twin {twin_uuid}...[/cyan]")
    
    try:
        # Publish command via MQTT
        client.mqtt.publish_command_message(twin_uuid, {"command": "sync_workflows"})
        console.print("[green]✓ Command sent. Check edge logs for results.[/green]")
        console.print("[dim]Use 'cyberwave edge list-models --twin-uuid ...' to see loaded models[/dim]")
    except Exception as e:
        console.print(f"[red]Error sending command: {e}[/red]")


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
    
    from cyberwave_cli.auth import get_authenticated_client
    
    client = get_authenticated_client()
    if not client:
        console.print("[red]Not authenticated. Run 'cyberwave login' first.[/red]")
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
                console.print("[yellow]No model bindings loaded on edge[/yellow]")
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
            console.print("[yellow]No response from edge node (is it running?)[/yellow]")
            
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
