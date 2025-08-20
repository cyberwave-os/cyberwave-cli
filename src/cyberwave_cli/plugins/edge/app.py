import json
import os
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich import print

from cyberwave import Client

app = typer.Typer(help="Configure and run Cyberwave Edge nodes")

DEFAULT_EDGE_CONFIG = Path.home() / ".cyberwave" / "edge.json"


def _write_config_file(cfg_path: Path, data: dict) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(cfg_path, 0o600)


@app.command("init")
def init(
    robot: str = typer.Option(..., "--robot", help="Robot driver type, e.g. so_arm100"),
    port: Optional[str] = typer.Option(None, "--port", help="Serial/connection port for the robot"),
    backend: Optional[str] = typer.Option(None, "--backend", help="Backend base URL (e.g. http://localhost:8000/api/v1)"),
    device_id: Optional[int] = typer.Option(None, "--device-id", help="Existing device ID"),
    project_id: Optional[int] = typer.Option(None, "--project", help="Project ID for auto-registration"),
    device_name: Optional[str] = typer.Option(None, "--device-name", help="Device name for auto-registration"),
    device_type: Optional[str] = typer.Option("robot/so-arm100", "--device-type", help="Device type for auto-registration"),
    auto_register: bool = typer.Option(False, "--auto-register", help="Register device if device_id not provided"),
    use_device_token: bool = typer.Option(False, "--use-device-token", help="Issue and use a device token for offline operation"),
    loop_hz: float = typer.Option(20.0, "--loop-hz", help="Polling frequency (Hz)"),
    config: Path = typer.Option(DEFAULT_EDGE_CONFIG, "--config", help="Where to write the Edge config JSON"),
) -> None:
    """Create or update an Edge node configuration file."""

    data = {
        "robot_type": robot,
        "robot_port": port,
        "backend_url": backend,
        "device_id": device_id,
        "project_id": project_id,
        "device_name": device_name,
        "device_type": device_type,
        "auto_register_device": auto_register,
        "use_device_token": use_device_token,
        "loop_hz": loop_hz,
    }

    # Attempt to reuse existing CLI auth to fill backend if not provided
    if backend is None:
        from cyberwave_cli.plugins.auth.app import load_config as _load_cli_cfg, DEFAULT_BACKEND_URL
        cli_cfg = _load_cli_cfg()
        data["backend_url"] = cli_cfg.get("backend_url", DEFAULT_BACKEND_URL) + "/api/v1"

    # If auto-register requested and no device_id, perform registration and optionally issue a token
    if auto_register and not device_id:
        if not project_id or not device_name or not device_type:
            raise typer.Exit("--project, --device-name, and --device-type are required for --auto-register")
        async def _run():
            client = Client(base_url=data["backend_url"])
            await client.login()
            dev = await client.register_device(project_id=project_id, name=device_name, device_type=device_type)
            data["device_id"] = int(dev.get("id"))
            if use_device_token:
                token = await client.issue_device_token(device_id=data["device_id"])
                data["access_token"] = token
            await client.aclose()
        import asyncio; asyncio.run(_run())

    _write_config_file(config, data)
    print(f":white_check_mark: Edge config written to [bold]{config}[/bold]")


@app.command("run")
def run(config: Path = typer.Option(DEFAULT_EDGE_CONFIG, "--config", help="Edge config JSON path")) -> None:
    """Run the Edge node with the provided configuration file."""
    if not config.exists():
        raise typer.Exit(f"Config file not found: {config}")
    # Prefer running the module directly to use current env
    cmd = ["python", "-m", "cyberwave_edge.main", "--config", str(config)]
    print(f"Starting Edge: {' '.join(cmd)}")
    subprocess.run(cmd, check=False)


@app.command("simulate")
def simulate(
    sensor: str = typer.Option(..., "--sensor", "-s", help="Sensor UUID"),
    video: Path = typer.Option(..., "--video", "-v", help="Path to local mp4"),
    fps: float = typer.Option(2.0, "--fps"),
) -> None:
    """Stream a local video file as a virtual camera to a sensor."""
    cmd = [
        "python", "-m", "cyberwave_edge.camera_worker",
        "--sensor", sensor,
        "--source", str(video),
        "--fps", str(fps),
    ]
    print(f"Simulating edge camera: {' '.join(cmd)}")
    subprocess.run(cmd, check=False)

@app.command("status")
def status(config: Path = typer.Option(DEFAULT_EDGE_CONFIG, "--config", help="Edge config JSON path")) -> None:
    """Quick diagnostics for Edge configuration and cloud connectivity."""
    if not config.exists():
        raise typer.Exit(f"Config file not found: {config}")
    with open(config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    missing = [k for k in ("robot_type", "backend_url") if not cfg.get(k)]
    if missing:
        print(f"[red]Missing required config keys: {missing}[/red]")
        raise typer.Exit(1)

    # Try SDK token and device reachability
    async def _check():
        client = Client(base_url=cfg["backend_url"])
        try:
            await client.login()  # uses cached token via SDK
        except Exception:
            if tok := cfg.get("access_token"):
                client._access_token = tok  # fallback to provided device token
            else:
                print("[yellow]No active session; telemetry may fail without token[/yellow]")
        did = cfg.get("device_id")
        ok = True
        if did:
            try:
                # simple check: send empty telemetry as dry run
                await client.send_telemetry(did, {"ping": True})
                print(f"[green]✓ Cloud reachable. Device {did} accepted telemetry[/green]")
            except Exception as e:
                ok = False
                print(f"[red]✗ Telemetry check failed for device {did}: {e}[/red]")
        await client.aclose()
        return ok

    import asyncio; ok = asyncio.run(_check())
    if not ok:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()


