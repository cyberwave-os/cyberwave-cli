"""Deprecated ``health`` and ``remote-status`` edge commands.

Both are marked deprecated (the core now handles these checks) but remain
registered for backward compatibility.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import click
from rich.console import Console
from rich.table import Table

console = Console()


def register(edge_group: click.Group) -> None:
    """Register ``health`` and ``remote-status`` on the given click group."""
    edge_group.add_command(health)
    edge_group.add_command(remote_status)


# ---------------------------------------------------------------------------
# health command
# ---------------------------------------------------------------------------


@click.command("health")
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

    from ...utils import get_sdk_client, print_error, print_warning

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


# ---------------------------------------------------------------------------
# remote-status command
# ---------------------------------------------------------------------------


@click.command("remote-status")
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
    from . import _binding_for_fingerprint
    from cyberwave.fingerprint import generate_fingerprint
    from ...utils import get_sdk_client, print_error

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
