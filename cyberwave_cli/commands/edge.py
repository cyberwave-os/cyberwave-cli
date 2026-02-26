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
import shutil
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


def _is_legacy_edge_configs_map(edge_configs: dict) -> bool:
    if not isinstance(edge_configs, dict) or not edge_configs:
        return False
    if "edge_fingerprint" in edge_configs or "camera_config" in edge_configs:
        return False
    return all(isinstance(value, dict) for value in edge_configs.values())


def _iter_edge_bindings(edge_configs: dict) -> list[tuple[str, dict]]:
    if not isinstance(edge_configs, dict) or not edge_configs:
        return []

    if _is_legacy_edge_configs_map(edge_configs):
        bindings: list[tuple[str, dict]] = []
        for fingerprint, binding in edge_configs.items():
            if isinstance(fingerprint, str) and isinstance(binding, dict):
                bindings.append((fingerprint, binding))
        return bindings

    fingerprint = edge_configs.get("edge_fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        return [(fingerprint, edge_configs)]
    return []


def _binding_for_fingerprint(edge_configs: dict, fingerprint: str) -> dict | None:
    for candidate_fingerprint, binding in _iter_edge_bindings(edge_configs):
        if candidate_fingerprint == fingerprint:
            return binding
    return None


@click.group()
def edge():
    """Manage the edge node service."""
    pass


@edge.command("install")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def install_edge(yes):
    """Install cyberwave-edge-core and register it as a boot service.

    Downloads the cyberwave-edge-core package (via apt-get on Debian/Ubuntu)
    and creates a systemd service so it starts automatically on boot.

    \b
    Examples:
        sudo cyberwave edge install
        sudo cyberwave edge install -y
    """
    from ..core import setup_edge_core

    if not setup_edge_core(skip_confirm=yes):
        raise SystemExit(1)


@edge.command("uninstall")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def uninstall_edge(yes):
    """Stop and remove the cyberwave-edge-core service.

    Disables the systemd service, removes the unit file, removes the edge
    config directory, and optionally uninstalls the package.

    \b
    Examples:
        sudo cyberwave edge uninstall
        sudo cyberwave edge uninstall -y
    """
    from ..config import CONFIG_DIR
    from ..core import PACKAGE_NAME, SYSTEMD_UNIT_NAME, SYSTEMD_UNIT_PATH, _run

    if not yes:
        from rich.prompt import Confirm as RichConfirm

        if not RichConfirm.ask(
            f"Remove {SYSTEMD_UNIT_NAME} and disable boot service?", default=False
        ):
            console.print("[dim]Aborted.[/dim]")
            return

    # Stop and disable the service
    try:
        _run(["systemctl", "stop", SYSTEMD_UNIT_NAME], check=False)
        _run(["systemctl", "disable", SYSTEMD_UNIT_NAME], check=False)
    except FileNotFoundError:
        console.print("[yellow]systemctl not found — skipping service cleanup.[/yellow]")

    # Remove the unit file
    if SYSTEMD_UNIT_PATH.exists():
        SYSTEMD_UNIT_PATH.unlink()
        console.print(f"[green]Removed:[/green] {SYSTEMD_UNIT_PATH}")
        try:
            _run(["systemctl", "daemon-reload"], check=False)
        except FileNotFoundError:
            pass

    # Remove the edge config directory (credentials.json, environment.json, etc.)
    if CONFIG_DIR.exists():
        try:
            shutil.rmtree(CONFIG_DIR)
            console.print(f"[green]Removed:[/green] {CONFIG_DIR}")
        except PermissionError:
            console.print(
                "[red]Permission denied removing edge config directory.[/red]\n"
                "[dim]Re-run with sudo: sudo cyberwave edge uninstall[/dim]"
            )
        except OSError as exc:
            console.print(f"[yellow]Could not fully remove {CONFIG_DIR}: {exc}[/yellow]")

    # Offer to uninstall the package
    if not yes:
        from rich.prompt import Confirm as RichConfirm

        if RichConfirm.ask(f"Also uninstall {PACKAGE_NAME} package?", default=False):
            try:
                _run(["apt-get", "remove", "-y", PACKAGE_NAME], check=False)
            except FileNotFoundError:
                console.print("[yellow]apt-get not found — remove manually with pip.[/yellow]")

    console.print("[green]Edge core service removed.[/green]")


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

    # Set environment (use clean env to avoid PyInstaller LD_LIBRARY_PATH leaking)
    from ..config import clean_subprocess_env

    env = clean_subprocess_env()
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
        console.print(
            "[dim]Install it with: pip install -e cyberwave-edges/cyberwave-edge-python[/dim]"
        )


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


@edge.command("restart")
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    default=None,
    help="Path to .env file (for process mode)",
)
def restart_edge(env_file):
    """Restart the edge node service.

    If the edge was installed as a systemd service, restarts it via systemctl.
    Otherwise falls back to stopping and re-starting the background process.

    \b
    Examples:
        sudo cyberwave edge restart
        cyberwave edge restart --env-file /path/to/.env
    """
    from ..core import SYSTEMD_UNIT_PATH, _has_systemd, restart_service

    # Prefer systemd when available
    if _has_systemd() and SYSTEMD_UNIT_PATH.exists():
        restart_service()
        return

    # Fallback: stop running process, then start a new one
    import signal

    console.print("[cyan]Restarting edge node process...[/cyan]")

    try:
        result = subprocess.run(
            ["pgrep", "-f", "cyberwave_edge.service"],
            capture_output=True,
            text=True,
        )
        pids = [p for p in result.stdout.strip().split("\n") if p]

        for pid in pids:
            os.kill(int(pid), signal.SIGTERM)
            console.print(f"[dim]Stopped PID {pid}[/dim]")

        if pids:
            # Give the old process a moment to release resources
            time.sleep(1)
    except Exception as exc:
        console.print(f"[yellow]Could not stop existing process: {exc}[/yellow]")

    # Re-start
    env_path = Path(env_file).resolve() if env_file else Path(".env").resolve()
    if not env_path.exists():
        console.print(f"[red]Error: .env file not found at {env_path}[/red]")
        console.print("[dim]Pass --env-file or run from the directory containing .env[/dim]")
        return

    from ..config import clean_subprocess_env

    env = clean_subprocess_env()
    env["DOTENV_PATH"] = str(env_path)

    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "cyberwave_edge.service"],
            cwd=env_path.parent,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        console.print(f"[green]✓ Edge node restarted (PID: {process.pid})[/green]")
    except FileNotFoundError:
        console.print("[red]Error: cyberwave_edge package not found[/red]")
        console.print(
            "[dim]Install it with: pip install -e cyberwave-edges/cyberwave-edge-python[/dim]"
        )


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
@click.option(
    "--runtime", "-r", multiple=True, help="Specific runtime to install (ultralytics, opencv)"
)
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
        console.print(
            "[dim]Use 'cyberwave edge list-models --twin-uuid ...' to see loaded models[/dim]"
        )
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
                console.print(
                    "[dim]Use 'cyberwave edge sync-workflows' to load from workflows[/dim]"
                )
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


@edge.command("sync-devices")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def sync_devices(yes: bool):
    """
    Discover USB cameras and sync them to twin metadata via REST API.

    Runs v4l2-ctl to discover cameras, writes cameras.json to the config directory,
    and updates each twin linked to this edge with metadata.discovered_devices.
    The frontend will handle device selection via dialogs.

    \b
    Example:
        cyberwave edge sync-devices
    """
    from ..config import CONFIG_DIR
    from ..core import (
        _load_or_generate_edge_fingerprint,
        sync_discovered_devices_to_twins,
    )
    from ..utils import get_sdk_client, print_error, print_success, print_warning

    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return

    # Load environment to get twin UUIDs
    env_file = CONFIG_DIR / "environment.json"
    if not env_file.exists():
        print_error(
            "No environment configured.",
            "Run 'cyberwave edge install' first to configure the edge.",
        )
        return

    try:
        with open(env_file) as f:
            env_data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print_error(f"Failed to read environment file: {exc}")
        return

    twin_uuids = env_data.get("twin_uuids", [])
    if not twin_uuids:
        print_warning("No twins linked to this edge.")
        console.print(
            "[dim]Run 'cyberwave edge install' and select twins to link.[/dim]"
        )
        return

    fingerprint = _load_or_generate_edge_fingerprint()
    console.print(f"\n[dim]Fingerprint: {fingerprint}[/dim]")
    console.print(f"[cyan]Syncing discovered devices to {len(twin_uuids)} twin(s)...[/cyan]\n")

    updated, failed = sync_discovered_devices_to_twins(
        client,
        twin_uuids,
        fingerprint,
    )

    if updated:
        print_success(f"Synced discovered devices to {updated} twin(s)")
        console.print(f"[dim]cameras.json written to {CONFIG_DIR}[/dim]")
    if failed:
        print_warning(f"Failed to sync to {failed} twin(s)")
    if not updated and not failed:
        console.print("[dim]No cameras discovered (v4l2-ctl may not be available on this system)[/dim]")


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
@click.option("--twin-uuid", "-t", help="Twin UUID to pull config from (legacy)")
@click.option("--environment-uuid", "-e", help="Environment UUID to pull all twins from (legacy)")
@click.option("--target-dir", "-d", default=".", help="Directory to write .env file")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def pull_config(twin_uuid: str | None, environment_uuid: str | None, target_dir: str, yes: bool):
    """
    Pull edge configuration from backend.

    Uses the discovery API to fetch all twins bound to this edge device
    along with their camera configurations.

    \b
    Examples:
        # Pull all configs for this edge device
        cyberwave edge pull

        # Pull config for a specific twin (legacy)
        cyberwave edge pull --twin-uuid abc-123

        # Specify output directory
        cyberwave edge pull -d ./my-edge
    """
    from ..fingerprint import generate_fingerprint, get_device_info
    from ..utils import get_sdk_client, print_error

    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return

    fingerprint = generate_fingerprint()
    console.print(f"\n[dim]Fingerprint: {fingerprint}[/dim]\n")

    try:
        # Try new discovery API first
        if not twin_uuid and not environment_uuid:
            success = _pull_via_discovery_api(fingerprint, target_dir, yes)
            if success:
                return
            # Fall through to legacy if discovery API fails
            console.print("[dim]Discovery API not available, use --twin-uuid for legacy mode[/dim]")
            return

        # Legacy mode
        if environment_uuid:
            _pull_environment_configs(client, environment_uuid, fingerprint, target_dir, yes)
        else:
            _pull_single_twin_config(client, twin_uuid, fingerprint, target_dir, yes)
    except Exception as e:
        print_error(str(e))


def _pull_via_discovery_api(fingerprint: str, target_dir: str, yes: bool) -> bool:
    """
    DEPRECATED: The core now handles discovery
    Pull config via the new discovery API.

    Returns True on success, False to fall back to legacy.
    """
    import platform
    import httpx

    from ..config import get_api_url
    from ..credentials import load_credentials
    from ..fingerprint import get_device_info
    from ..utils import print_error, print_success, print_warning, write_edge_env

    creds = load_credentials()
    if not creds or not creds.token:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return False

    base_url = get_api_url()
    headers = {"Authorization": f"Bearer {creds.token}"}
    device_info = get_device_info()

    try:
        # Call discovery API
        discover_url = f"{base_url}/api/v1/edges/discover"
        discover_payload = {
            "fingerprint": fingerprint,
            "hostname": device_info.get("hostname", ""),
            "platform": f"{platform.system()}-{platform.machine()}",
            "name": device_info.get("hostname", fingerprint[:20]),
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
        twins = data.get("twins", [])

        console.print(f"[cyan]Edge UUID:[/cyan] {edge_uuid}")
        console.print(f"[cyan]Bound twins:[/cyan] {len(twins)}\n")

        if not twins:
            print_warning("No twins bound to this edge device.")
            console.print(
                "[dim]Use 'cyberwave twin pair <twin_uuid>' to bind twins to this edge.[/dim]"
            )
            return True

        # Display twins and collect configs
        all_configs = []
        primary_twin = None

        for twin_info in twins:
            twin_uuid = twin_info.get("twin_uuid")
            twin_name = twin_info.get("twin_name", "Unknown")
            edge_config = twin_info.get("camera_config", {})  # Backend still uses camera_config key

            if not primary_twin:
                primary_twin = twin_uuid

            has_config = bool(edge_config)
            status = "[green]✓[/green]" if has_config else "[yellow]○[/yellow]"

            console.print(f"  {status} {twin_name} ({twin_uuid[:8]}...)")

            if edge_config:
                config_entry = {
                    "twin_uuid": twin_uuid,
                    **edge_config,
                }
                all_configs.append(config_entry)

        if not all_configs:
            print_warning("No edge configurations found.")
            console.print(
                "[dim]Use 'cyberwave twin pair <twin_uuid>' with config options to set up twins.[/dim]"
            )
            return True

        # Write .env file
        write_edge_env(
            target_dir=target_dir,
            twin_uuid=primary_twin,
            fingerprint=fingerprint,
            edge_configs=all_configs,
            generator="cyberwave edge pull",
        )

        print_success(f"Config pulled to {target_dir}/.env")
        console.print(f"[dim]  {len(all_configs)} config(s) from {len(twins)} twin(s)[/dim]")
        console.print("[dim]Run: cyberwave edge start[/dim]")

        return True

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # API not available, fall back to legacy
            return False
        print_error(f"Discovery API error: {e}")
        return False
    except Exception as e:
        console.print(f"[dim]Discovery API failed: {e}[/dim]")
        return False


def _pull_single_twin_config(
    client: Any, twin_uuid: str, fingerprint: str, target_dir: str, yes: bool
):
    """
    DEPRECATED: The core now handles this sync
    Pull config from a single twin (legacy).
    """
    twin = client.twins.get(twin_uuid)
    twin_name = getattr(twin, "name", "Unknown")
    metadata = getattr(twin, "metadata", {}) or {}
    edge_configs = metadata.get("edge_configs", {})

    console.print(f"[cyan]Twin:[/cyan] {twin_name}")

    my_config = _binding_for_fingerprint(edge_configs, fingerprint)

    if my_config:
        from ..utils import print_success

        print_success("Found config for this device")
        console.print(f"[dim]  Registered: {my_config.get('registered_at', 'unknown')}[/dim]")

        cameras = my_config.get("cameras", [])
        if cameras:
            console.print(f"[dim]  Cameras: {len(cameras)} configured[/dim]")
    else:
        # No config for this fingerprint - check for other configs or default
        available_bindings = _iter_edge_bindings(edge_configs)
        if available_bindings:
            from ..utils import print_warning

            print_warning("No config for this device fingerprint")
            console.print(f"\n[dim]Available configs from other devices:[/dim]")

            for i, (fp, cfg) in enumerate(available_bindings, 1):
                device_info = cfg.get("device_info", {})
                hostname = device_info.get("hostname", "unknown")
                registered = (
                    cfg.get("registered_at", "unknown")[:10]
                    if cfg.get("registered_at")
                    else "unknown"
                )
                console.print(f"  {i}. {fp[:30]}... ({hostname}, {registered})")

            if not yes:
                choice = Prompt.ask(
                    "\n[bold]Copy config from which device? (number or 'n' to skip)[/bold]",
                    default="1",
                )

                if choice.lower() != "n":
                    try:
                        idx = int(choice) - 1
                        source_fp, source_cfg = available_bindings[idx]
                        my_config = source_cfg.copy()
                        from ..utils import print_success

                        print_success(f"Copying config from {source_fp[:20]}...")
                    except (ValueError, IndexError):
                        from ..utils import print_warning

                        print_warning("Invalid choice, skipping")
                        return
        else:
            # Check for default config
            default_config = metadata.get("default_edge_config")
            if default_config:
                from ..utils import print_warning

                print_warning("No config for this device, using default template")
                my_config = default_config.copy()
            else:
                from ..utils import print_error

                print_error(
                    "No configuration found for this twin",
                    "Use 'cyberwave twin create <asset> --pair' to set up this twin",
                )
                return

    if not my_config:
        return

    # Extract edge config (remove internal fields)
    edge_config = {
        k: v
        for k, v in my_config.items()
        if k not in ("device_info", "registered_at", "last_sync", "cameras")
    }

    # For backward compat: if config has 'cameras' array, use first entry as edge_config
    if not edge_config and my_config.get("cameras"):
        cameras = my_config["cameras"]
        if cameras:
            edge_config = {k: v for k, v in cameras[0].items() if k != "camera_id"}

    # Write .env file directly using shared utility
    from ..utils import write_edge_env, print_success

    write_edge_env(
        target_dir=target_dir,
        twin_uuid=twin_uuid,
        fingerprint=fingerprint,
        edge_config=edge_config,
        generator="cyberwave edge pull",
    )

    print_success(f"Config pulled to {target_dir}/.env")
    console.print("[dim]Run: python -m cyberwave_edge.service[/dim]")


def _pull_environment_configs(
    client: Any, env_uuid: str, fingerprint: str, target_dir: str, yes: bool
):
    """
    DEPRECATED: The core now handles this sync
    Pull configs for all twins in an environment."""
    # Get environment info
    env = client.environments.get(env_uuid)
    env_name = getattr(env, "name", "Unknown")

    # Get twins in environment using SDK
    twins = client.twins.list(environment_id=env_uuid)

    if not twins:
        from ..utils import print_warning

        print_warning(f"No twins found in environment '{env_name}'")
        return

    console.print(f"[cyan]Environment:[/cyan] {env_name}")
    console.print(f"[cyan]Found {len(twins)} twin(s):[/cyan]\n")

    all_configs = []
    twins_with_config = []
    twins_without_config = []

    for twin in twins:
        twin_name = getattr(twin, "name", "Unknown")
        twin_uuid = str(getattr(twin, "uuid", ""))
        metadata = getattr(twin, "metadata", {}) or {}
        edge_configs = metadata.get("edge_configs", {})

        my_config = _binding_for_fingerprint(edge_configs, fingerprint)

        if my_config:
            twins_with_config.append((twin, my_config))
            # Extract edge config (remove internal fields)
            edge_config = {
                k: v
                for k, v in my_config.items()
                if k not in ("device_info", "registered_at", "last_sync", "cameras")
            }

            # For backward compat: if config has 'cameras' array, use first entry
            if not edge_config and my_config.get("cameras"):
                cameras = my_config["cameras"]
                if cameras:
                    edge_config = {k: v for k, v in cameras[0].items() if k != "camera_id"}

            config_entry = {
                "twin_uuid": twin_uuid,
                **edge_config,
            }
            all_configs.append(config_entry)
            console.print(f"  [green]✓[/green] {twin_name} - configured")
        else:
            twins_without_config.append(twin)
            console.print(f"  [yellow]○[/yellow] {twin_name} - no config for this device")

    if not all_configs and not twins_without_config:
        from ..utils import print_warning

        print_warning("No configurations to pull")
        return

    if twins_without_config:
        from ..utils import print_warning

        print_warning(f"{len(twins_without_config)} twin(s) need configuration")

    if not yes and not Confirm.ask("\nPull available configs?", default=True):
        return

    # Write .env file with all configs using shared utility
    if all_configs:
        from ..utils import write_edge_env, print_success

        # Extract primary twin
        primary_twin = all_configs[0].get("twin_uuid", "") if all_configs else ""

        write_edge_env(
            target_dir=target_dir,
            twin_uuid=primary_twin,
            fingerprint=fingerprint,
            edge_configs=all_configs,
            generator="cyberwave edge pull",
        )

        print_success(f"Config pulled to {target_dir}/.env")
        console.print(
            f"[dim]  {len(all_configs)} config(s) from {len(twins_with_config)} twin(s)[/dim]"
        )
        console.print("[dim]Run: python -m cyberwave_edge.service[/dim]")
    else:
        from ..utils import print_warning

        print_warning(
            "No configs found. Use 'cyberwave twin create <asset> --pair' to set up twins."
        )


@edge.command("health")
@click.option("--twin-uuid", "-t", required=True, help="Twin UUID to check health for")
@click.option("--timeout", default=5, help="Timeout in seconds to wait for response")
@click.option("--watch", "-w", is_flag=True, help="Continuously watch health status")
def health(twin_uuid: str, timeout: int, watch: bool):
    """
    DEPRECATED: The core now handles this health check

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
            console.print(
                f"[cyan]Watching health for twin {twin_uuid}... (Ctrl+C to stop)[/cyan]\n"
            )
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
                console.print(
                    "[dim]The edge service may be offline or not publishing health status.[/dim]"
                )
                console.print(
                    "[dim]Ensure the edge service is running with health publishing enabled.[/dim]"
                )

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
    DEPRECATED: The core now handles this remote status check
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
        twin_name = getattr(twin, "name", "Unknown")
        metadata = getattr(twin, "metadata", {}) or {}
        edge_configs = metadata.get("edge_configs", {})

        my_config = _binding_for_fingerprint(edge_configs, fingerprint)

        console.print(f'\n[bold]Edge Status for "{twin_name}"[/bold]')
        console.print("━" * 40)
        console.print(f"Fingerprint:    {fingerprint}")

        if not my_config:
            console.print(f"Status:         [yellow]Not registered[/yellow]")
            console.print("\n[dim]This device hasn't connected to this twin yet.[/dim]")
            console.print(
                "[dim]Use 'cyberwave twin pair <uuid>' or 'cyberwave edge pull' first.[/dim]"
            )
            return

        # Check last heartbeat
        last_heartbeat = my_config.get("last_heartbeat")
        last_status = my_config.get("last_status", {})

        if last_heartbeat:
            try:
                hb_time = datetime.fromisoformat(last_heartbeat.replace("Z", "+00:00"))
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
                    last_seen = (
                        f"{int(delta.total_seconds() / 3600)} hours ago"
                        if delta.total_seconds() > 3600
                        else f"{int(delta.total_seconds() / 60)} minutes ago"
                    )

                console.print(f"Status:         {status}")
                console.print(f"Last heartbeat: {last_seen}")

                # Show uptime if available
                uptime = last_status.get("uptime_seconds")
                if uptime:
                    days = uptime // 86400
                    hours = (uptime % 86400) // 3600
                    if days > 0:
                        console.print(f"Uptime:         {days} days, {hours} hours")
                    else:
                        console.print(f"Uptime:         {hours} hours")

                # Show streams if available
                streams = last_status.get("streams", {})
                if streams:
                    console.print(f"\nStreams:")
                    for stream_id, stream_info in streams.items():
                        stream_status = stream_info.get("status", "unknown")
                        fps = stream_info.get("fps", "?")
                        res = stream_info.get("resolution", "?")
                        if stream_status == "streaming":
                            console.print(
                                f"  • {stream_id}: [green]{stream_status}[/green] ({fps} fps, {res})"
                            )
                        else:
                            console.print(f"  • {stream_id}: [yellow]{stream_status}[/yellow]")

            except Exception:
                console.print(f"Last heartbeat: {last_heartbeat}")
        else:
            registered = my_config.get("registered_at", "unknown")
            console.print(f"Status:         [yellow]Never connected[/yellow]")
            console.print(f"Registered:     {registered}")

        console.print()

    except Exception as e:
        print_error(str(e))
