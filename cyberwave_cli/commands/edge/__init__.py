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

from __future__ import annotations

import importlib
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

from cyberwave_cli.utils import colorize_log_line

console = Console()
DRIVER_CONTAINER_PREFIX = "cyberwave-driver-"
CYBERWAVE_CONTAINER_PREFIX = "cyberwave"


def _delete_registered_edges_for_fingerprint(
    *,
    fingerprint: str | None,
    token: str | None,
    base_url: str | None,
    workspace_uuid: str | None = None,
) -> tuple[int, int]:
    """Delete backend Edge registrations that match a device fingerprint.

    Returns:
        (deleted_count, failed_count)
    """
    if not fingerprint or not token:
        return 0, 0

    try:
        from cyberwave import Cyberwave
    except ImportError:
        console.print(
            "[yellow]SDK not available — skipping backend edge registration cleanup.[/yellow]"
        )
        return 0, 1

    try:
        from ...config import get_api_url

        client = Cyberwave(base_url=base_url or get_api_url(), token=token)
        edges = client.edges.list()
    except Exception as exc:
        console.print(f"[yellow]Could not list edges from backend: {exc}[/yellow]")
        return 0, 1

    matching_edges: list[str] = []
    for edge in edges:
        edge_fingerprint = str(getattr(edge, "fingerprint", "") or "")
        if edge_fingerprint != fingerprint:
            continue

        if workspace_uuid:
            edge_workspace_uuid = str(
                getattr(edge, "workspace_uuid", "") or getattr(edge, "workspace_id", "") or ""
            )
            if edge_workspace_uuid and edge_workspace_uuid != workspace_uuid:
                continue

        edge_uuid = str(getattr(edge, "uuid", "") or "")
        if edge_uuid:
            matching_edges.append(edge_uuid)

    deleted_count = 0
    failed_count = 0
    for edge_uuid in matching_edges:
        try:
            client.edges.delete(edge_uuid)
            deleted_count += 1
        except Exception:
            failed_count += 1

    return deleted_count, failed_count


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


def _stop_edge_driver_containers(run_command) -> list[str]:
    """Stop running edge driver containers managed by edge-core."""
    if not shutil.which("docker"):
        return []

    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--format",
                "{{.Names}}",
                "--filter",
                f"name=^{DRIVER_CONTAINER_PREFIX}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        console.print(f"[yellow]Could not list edge driver containers: {exc}[/yellow]")
        return []

    containers = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not containers:
        return []

    try:
        run_command(["docker", "stop", *containers], check=False)
    except FileNotFoundError:
        console.print("[yellow]docker not found — skipping driver container cleanup.[/yellow]")
        return []

    return containers


def _prune_stopped_cyberwave_containers() -> int:
    """Remove all stopped containers whose name starts with ``cyberwave``."""
    if not shutil.which("docker"):
        return 0

    try:
        all_result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--format",
                "{{.Names}}",
                "--filter",
                f"name=^{CYBERWAVE_CONTAINER_PREFIX}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return 0

    try:
        running_result = subprocess.run(
            [
                "docker",
                "ps",
                "--format",
                "{{.Names}}",
                "--filter",
                f"name=^{CYBERWAVE_CONTAINER_PREFIX}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return 0

    all_containers = {l.strip() for l in all_result.stdout.splitlines() if l.strip()}
    running = {l.strip() for l in running_result.stdout.splitlines() if l.strip()}
    stopped = all_containers - running

    removed = 0
    for name in stopped:
        try:
            subprocess.run(
                ["docker", "rm", "-f", name],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            removed += 1
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            pass
    return removed


def _prune_unused_docker_images() -> bool:
    """Run ``docker image prune --all --force`` to remove unused images."""
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(
            ["docker", "image", "prune", "--all", "--force"],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def _find_edge_core_binary() -> str | None:
    """Locate the cyberwave-edge-core binary used for process-mode startups."""
    from ...core import EDGE_CORE_SPEC, _resolve_service_binary

    return _resolve_service_binary(EDGE_CORE_SPEC)


def _edge_process_match() -> str:
    """Return the process match string for manual edge-core launches."""
    from ...core import EDGE_CORE_SPEC

    return EDGE_CORE_SPEC.process_match


def _edge_process_pids() -> list[str]:
    """Return running edge-core process IDs for non-systemd process mode."""
    result = subprocess.run(
        ["pgrep", "-f", _edge_process_match()],
        capture_output=True,
        text=True,
    )
    own_pid = str(os.getpid())
    return [pid for pid in result.stdout.strip().split("\n") if pid and pid != own_pid]


def _kill_lingering_edge_processes(timeout: float = 5.0) -> None:
    """Kill any remaining edge-core processes and wait for them to exit.

    Called during uninstall after ``systemctl stop`` / ``launchctl bootout``
    to guarantee the process is fully gone before we remove the config
    directory.  Without this, a still-running edge-core can recreate
    subdirectories (e.g. ``workers/``) between the ``rmtree`` call and
    any subsequent assertions.
    """
    import signal

    try:
        pids = _edge_process_pids()
    except FileNotFoundError:
        pids = []

    if not pids:
        return

    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, ValueError):
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            pids = _edge_process_pids()
        except FileNotFoundError:
            return
        if not pids:
            return
        time.sleep(0.2)


def _remove_config_dir_reliably(config_dir: Path, *, retries: int = 5, delay: float = 1.0) -> bool:
    """Remove *config_dir*, retrying if a dying process recreates subdirectories.

    Returns True if the directory no longer exists after all attempts.
    """
    for _attempt in range(retries):
        if not config_dir.exists():
            return True
        try:
            shutil.rmtree(config_dir)
        except PermissionError:
            console.print("[red]Permission denied removing edge config directory.[/red]")
            return False
        except OSError as exc:
            console.print(f"[yellow]Could not fully remove {config_dir}: {exc}[/yellow]")
            return False
        if not config_dir.exists():
            return True
        time.sleep(delay)

    if config_dir.exists():
        console.print(
            f"[yellow]{config_dir} was recreated by a lingering process; "
            f"attempting final cleanup.[/yellow]"
        )
        try:
            shutil.rmtree(config_dir)
        except OSError:
            pass
    return not config_dir.exists()


def _edge_process_logs_hint() -> str:
    """Return the manual log guidance for process mode."""
    return "[dim]Logs: run 'cyberwave edge start -f' to view live output.[/dim]"


def _macos_launchagent_target() -> tuple[str, str]:
    """Return the launchctl domain/label target for the edge LaunchAgent."""
    from ...core import EDGE_CORE_SPEC, _launchagent_target

    return _launchagent_target(EDGE_CORE_SPEC)


def _ensure_macos_launchagent_installed() -> bool:
    """Ensure the macOS LaunchAgent plist exists before controlling it."""
    if _macos_launchagent_plist_path().exists():
        return True
    console.print(
        "[red]LaunchAgent plist not found.[/red]\n[dim]Run 'cyberwave edge install' first.[/dim]"
    )
    return False


def _macos_launchagent_plist_path() -> Path:
    """Return the edge LaunchAgent plist path for the current user."""
    from ...core import EDGE_CORE_SPEC, _launchagent_plist_path

    return _launchagent_plist_path(EDGE_CORE_SPEC)


def _macos_launchagent_log_path() -> Path:
    """Return the edge LaunchAgent log file path for the current user."""
    from ...core import EDGE_CORE_SPEC, _launchagent_label

    label = _launchagent_label(EDGE_CORE_SPEC)
    return Path.home() / "Library" / "Logs" / "Cyberwave" / f"{label}.log"


def _show_macos_launchagent_logs(*, follow: bool, lines: int) -> None:
    """Show logs from the macOS LaunchAgent log file."""
    log_path = _macos_launchagent_log_path()
    if not log_path.exists():
        console.print(
            "[yellow]Edge core log file not found.[/yellow]\n"
            f"[dim]Expected: {log_path}[/dim]\n"
            "[dim]Run 'cyberwave edge install' to enable LaunchAgent logs, "
            "or use 'cyberwave edge start -f' for manual process output.[/dim]"
        )
        return

    lines_to_show = max(0, int(lines))
    existing_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in existing_lines[-lines_to_show:]:
        console.print(colorize_log_line(line))

    if not follow:
        return

    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                console.print(colorize_log_line(line.rstrip("\n")))
                continue
            time.sleep(0.5)


@click.group()
def edge():
    """Manage the edge node service."""
    from ...core import _migrate_legacy_config_dir

    _migrate_legacy_config_dir()


@edge.command("install")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.option(
    "--channel",
    type=click.Choice(["stable", "dev", "staging"], case_sensitive=False),
    default="stable",
    show_default=True,
    help="Which edge-core package channel to install",
)
@click.option(
    "--version",
    type=str,
    default=None,
    help="Exact edge-core version to install from the selected channel",
)
@click.option(
    "--force-reinstall",
    is_flag=True,
    default=False,
    help="Tear down and reinstall the USB/IP server from scratch (macOS only)",
)
@click.option(
    "--reconfigure-camera",
    is_flag=True,
    default=False,
    help="Re-run camera detection and save to cameras.json",
)
@click.option(
    "--without-workers",
    is_flag=True,
    default=False,
    help="Skip pulling the ML worker Docker image (cyberwaveos/edge-ml-worker)",
)
def install_edge(yes, channel, version, force_reinstall, reconfigure_camera, without_workers):
    """Install cyberwave-edge-core and register it as a boot service.

    Downloads the cyberwave-edge-core package (via apt-get on Debian/Ubuntu,
    pip elsewhere) and creates a systemd service so it starts automatically on boot.
    By default the ML worker Docker image is also pulled.

    On Linux, this command requires root privileges.

    \b
    Examples:
        sudo cyberwave edge install
        sudo cyberwave edge install -y
        sudo cyberwave edge install --without-workers
        sudo cyberwave edge install --force-reinstall
        sudo cyberwave edge install --reconfigure-camera
        sudo cyberwave edge install --channel dev
        sudo cyberwave edge install --channel staging --version 0.0.42.595
    """
    if reconfigure_camera:
        from ...macos import is_macos

        if is_macos():
            from ...core import _list_camera_twins
            from ...macos import (
                setup_camera_stream_server,
                start_edge_core_service,
                stop_edge_core_service,
            )

            try:
                if not setup_camera_stream_server(
                    force=True,
                    camera_twins=_list_camera_twins(),
                ):
                    raise SystemExit(1)
                console.print("[cyan]Restarting edge-core so the driver reconnects...[/cyan]")
                stop_edge_core_service()
                start_edge_core_service()
            except KeyboardInterrupt:
                console.print("\n[dim]Aborted.[/dim]")
                raise SystemExit(1)
        else:
            from ...core import _detect_and_select_cameras

            _detect_and_select_cameras()
            console.print(
                "[dim]Edge-core will automatically pick up the new camera "
                "within a few seconds.[/dim]"
            )
        return

    from ...core import setup_edge_core

    try:
        if not setup_edge_core(
            skip_confirm=yes,
            channel=channel.lower(),
            version=version,
            force_reinstall=force_reinstall,
            pull_worker_image=not without_workers,
        ):
            raise SystemExit(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Aborted.[/dim]")
        raise SystemExit(1)


@edge.command("uninstall")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def uninstall_edge(yes):
    """Stop and remove the cyberwave-edge-core service.

    On Linux: disables the systemd service, removes the unit file (requires root).
    On macOS: tears down the launchd LaunchAgent.
    On both: removes the edge config directory and optionally the package.

    \b
    Examples:
        sudo cyberwave edge uninstall
        sudo cyberwave edge uninstall -y
    """
    from ...config import CONFIG_DIR
    from ...core import (
        EDGE_CORE_SPEC,
        SYSTEMD_UNIT_NAME,
        _is_macos,
        _load_or_generate_edge_fingerprint,
        _resolve_installed_edge_core_package_name,
    )
    from ...credentials import load_credentials
    from ...macos import is_macos

    creds = load_credentials()
    edge_fingerprint = _load_or_generate_edge_fingerprint()
    token = creds.token if creds else None
    workspace_uuid = str(getattr(creds, "workspace_uuid", "") or "") if creds else None
    base_url = str(getattr(creds, "cyberwave_base_url", "") or "") if creds else None

    service_label = "edge-core LaunchAgent" if is_macos() else SYSTEMD_UNIT_NAME

    if not yes:
        from rich.prompt import Confirm as RichConfirm

        if not RichConfirm.ask(f"Remove {service_label} and disable boot service?", default=False):
            console.print("[dim]Aborted.[/dim]")
            return

    if _is_macos():
        from ...config import clean_subprocess_env
        from ...macos import _teardown_camera_stream_server

        _domain, target = _macos_launchagent_target()
        plist_path = _macos_launchagent_plist_path()

        try:
            result = subprocess.run(
                ["launchctl", "bootout", target],
                env=clean_subprocess_env(),
                capture_output=True,
            )
            if result.returncode == 0:
                console.print(f"[green]Stopped LaunchAgent:[/green] {target}")
            elif result.returncode not in {3, 36}:
                console.print(
                    f"[yellow]launchctl bootout failed (exit {result.returncode}). Continuing cleanup.[/yellow]"
                )
        except FileNotFoundError:
            console.print("[yellow]launchctl not found — skipping LaunchAgent unload.[/yellow]")

        _kill_lingering_edge_processes()

        # Tear down MJPEG camera stream LaunchAgents and kill ffmpeg
        # processes so the camera LED turns off and the port is released.
        try:
            _teardown_camera_stream_server()
        except Exception as exc:
            console.print(
                f"[yellow]Camera stream teardown encountered an error: {exc}[/yellow]"
            )

        if plist_path.exists():
            try:
                plist_path.unlink()
                console.print(f"[green]Removed:[/green] {plist_path}")
            except PermissionError:
                console.print(
                    "[red]Permission denied removing LaunchAgent plist.[/red]\n"
                    "[dim]Run 'cyberwave edge uninstall' as your regular macOS user.[/dim]"
                )

        stopped_driver_containers = _stop_edge_driver_containers(
            lambda command, check=False: subprocess.run(
                command,
                check=check,
                env=clean_subprocess_env(),
            )
        )
        if stopped_driver_containers:
            console.print(
                f"[green]Stopped {len(stopped_driver_containers)} edge driver container(s).[/green]"
            )

        pruned_containers = _prune_stopped_cyberwave_containers()
        if pruned_containers:
            console.print(
                f"[green]Pruned {pruned_containers} stopped cyberwave container(s).[/green]"
            )

        if _prune_unused_docker_images():
            console.print("[green]Pruned unused Docker images.[/green]")

        if _remove_config_dir_reliably(CONFIG_DIR):
            console.print(f"[green]Removed:[/green] {CONFIG_DIR}")

        installed_package_name = _resolve_installed_edge_core_package_name()
        remove_pkg = yes or Confirm.ask(
            f"Also uninstall {installed_package_name} package?", default=False
        )
        if remove_pkg:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "uninstall", "-y", installed_package_name],
                    env=clean_subprocess_env(),
                    check=False,
                )
                if result.returncode != 0:
                    console.print(
                        "[yellow]pip uninstall returned a non-zero exit code. "
                        "The package may need manual removal.[/yellow]"
                    )
            except FileNotFoundError:
                console.print("[yellow]pip not found — remove the package manually.[/yellow]")

        deleted_count, failed_count = _delete_registered_edges_for_fingerprint(
            fingerprint=edge_fingerprint,
            token=token,
            base_url=base_url,
            workspace_uuid=workspace_uuid,
        )
        if deleted_count:
            console.print(
                "[green]Removed backend edge registration(s): "
                f"{deleted_count} (fingerprint: {edge_fingerprint}).[/green]"
            )
        elif token and failed_count == 0:
            console.print(
                "[dim]No backend edge registration found for this fingerprint "
                f"({edge_fingerprint}).[/dim]"
            )

        if failed_count:
            console.print(
                f"[yellow]Failed to remove {failed_count} backend edge registration(s).[/yellow]"
            )

        console.print(f"[green]{EDGE_CORE_SPEC.package_name} service removed.[/green]")
        return

    from ...core import SYSTEMD_UNIT_PATH, _run, require_root

    require_root("sudo cyberwave edge uninstall")

    # Stop and disable the service
    try:
        _run(["systemctl", "stop", SYSTEMD_UNIT_NAME], check=False)
        _run(["systemctl", "disable", SYSTEMD_UNIT_NAME], check=False)
    except FileNotFoundError:
        console.print("[yellow]systemctl not found — skipping service cleanup.[/yellow]")

    _kill_lingering_edge_processes()

    # Remove the systemd unit file
    if SYSTEMD_UNIT_PATH.exists():
        try:
            _run(["rm", "-f", str(SYSTEMD_UNIT_PATH)], check=True)
            console.print(f"[green]Removed:[/green] {SYSTEMD_UNIT_PATH}")
            try:
                _run(["systemctl", "daemon-reload"], check=False)
            except FileNotFoundError:
                pass
        except subprocess.CalledProcessError:
            console.print(f"[red]Failed to remove {SYSTEMD_UNIT_PATH}.[/red]")

    stopped_driver_containers = _stop_edge_driver_containers(_run)
    if stopped_driver_containers:
        console.print(
            f"[green]Stopped {len(stopped_driver_containers)} edge driver container(s).[/green]"
        )

    pruned_containers = _prune_stopped_cyberwave_containers()
    if pruned_containers:
        console.print(f"[green]Pruned {pruned_containers} stopped cyberwave container(s).[/green]")

    if _prune_unused_docker_images():
        console.print("[green]Pruned unused Docker images.[/green]")

    if _remove_config_dir_reliably(CONFIG_DIR):
        console.print(f"[green]Removed:[/green] {CONFIG_DIR}")

    if not yes:
        from rich.prompt import Confirm as RichConfirm

        installed_package_name = _resolve_installed_edge_core_package_name()
        if RichConfirm.ask(f"Also uninstall {installed_package_name} package?", default=False):
            if shutil.which("apt-get"):
                try:
                    _run(["apt-get", "remove", "-y", installed_package_name], check=False)
                except FileNotFoundError:
                    console.print("[yellow]apt-get not found — remove manually with pip.[/yellow]")
            else:
                try:
                    _run(
                        [sys.executable, "-m", "pip", "uninstall", "-y", installed_package_name],
                        check=False,
                    )
                except OSError:
                    console.print("[yellow]pip uninstall failed — remove manually.[/yellow]")

    deleted_count, failed_count = _delete_registered_edges_for_fingerprint(
        fingerprint=edge_fingerprint,
        token=token,
        base_url=base_url,
        workspace_uuid=workspace_uuid,
    )
    if deleted_count:
        console.print(
            "[green]Removed backend edge registration(s): "
            f"{deleted_count} (fingerprint: {edge_fingerprint}).[/green]"
        )
    elif token and failed_count == 0:
        console.print(
            "[dim]No backend edge registration found for this fingerprint "
            f"({edge_fingerprint}).[/dim]"
        )

    if failed_count:
        console.print(
            f"[yellow]Failed to remove {failed_count} backend edge registration(s).[/yellow]"
        )

    console.print("[green]Edge core service removed.[/green]")


@edge.command("start")
@click.option("--env-file", type=click.Path(), default=None, help="Path to .env file")
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (don't daemonize)")
def start_edge(env_file, foreground):
    """Start the edge node service.

    When the service is already running, ``systemctl start`` is a no-op
    and edge-core's boot-time driver startup is *not* re-executed.  Use
    ``cyberwave edge restart`` to force a full reload of twin/asset
    metadata and re-spawn driver containers.
    """
    from ...core import (
        EDGE_CORE_SPEC,
        SYSTEMD_UNIT_PATH,
        _has_systemd,
        _is_macos,
        load_launchagent_service,
        start_service,
    )

    spec = EDGE_CORE_SPEC

    if not foreground and _has_systemd() and SYSTEMD_UNIT_PATH.exists():
        if not start_service(spec):
            raise SystemExit(1)
        return

    if not foreground and _is_macos() and _macos_launchagent_plist_path().exists():
        from ...config import clean_subprocess_env

        domain, target = _macos_launchagent_target()
        try:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", target],
                env=clean_subprocess_env(),
                capture_output=True,
            )
            if result.returncode == 0:
                console.print(f"[green]✓ LaunchAgent started:[/green] {target}")
                return
            result = subprocess.run(
                ["launchctl", "bootstrap", domain, str(_macos_launchagent_plist_path())],
                env=clean_subprocess_env(),
                capture_output=True,
            )
            if result.returncode == 0:
                console.print(f"[green]✓ LaunchAgent started:[/green] {target}")
                return
            if not load_launchagent_service(spec):
                raise SystemExit(1)
            return
        except FileNotFoundError:
            console.print("[red]launchctl not found on this system.[/red]")
            raise SystemExit(1)

    env_path = Path(env_file).resolve() if env_file else Path(".env").resolve()

    if not env_path.exists():
        console.print(f"[red]Error: .env file not found at {env_path}[/red]")
        console.print("[dim]Run 'cyberwave edge install' first to configure the edge node[/dim]")
        return

    console.print(f"[cyan]Starting edge node with config: {env_path}[/cyan]")

    work_dir = env_path.parent

    from ...config import clean_subprocess_env

    env = clean_subprocess_env()
    env["DOTENV_PATH"] = str(env_path)
    binary = _find_edge_core_binary()
    if not binary:
        console.print("[red]Error: cyberwave-edge-core binary not found[/red]")
        console.print("[dim]Run 'cyberwave edge install' to install it first.[/dim]")
        return

    try:
        if foreground:
            console.print("[green]Running edge node in foreground (Ctrl+C to stop)...[/green]")
            subprocess.run(
                [binary],
                cwd=work_dir,
                env=env,
            )
        else:
            console.print("[green]Starting edge node in background...[/green]")
            process = subprocess.Popen(
                [binary],
                cwd=work_dir,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            console.print(f"[green]✓ Edge node started (PID: {process.pid})[/green]")
            console.print(_edge_process_logs_hint())

    except FileNotFoundError:
        console.print(f"[red]Binary not found: {binary}[/red]")
        console.print("[dim]Run 'cyberwave edge install' to reinstall it.[/dim]")


@edge.command("stop")
def stop_edge():
    """Stop the edge node service."""
    from ...macos import is_macos

    if is_macos():
        from ...macos import stop_edge_core_service

        stop_edge_core_service()
        return

    import signal

    from ...core import EDGE_CORE_SPEC, SYSTEMD_UNIT_PATH, _has_systemd, _is_macos, stop_service

    if _has_systemd() and SYSTEMD_UNIT_PATH.exists():
        stop_service()
        return

    if _is_macos() and _macos_launchagent_plist_path().exists():
        if not _ensure_macos_launchagent_installed():
            raise SystemExit(1)

        from ...config import clean_subprocess_env

        _domain, target = _macos_launchagent_target()
        try:
            result = subprocess.run(
                ["launchctl", "bootout", target],
                env=clean_subprocess_env(),
                capture_output=True,
            )
        except FileNotFoundError:
            console.print("[red]launchctl not found on this system.[/red]")
            raise SystemExit(1)

        if result.returncode == 0:
            console.print(f"[green]✓ LaunchAgent stopped:[/green] {target}")
            return

        if result.returncode in {3, 36}:
            console.print(
                f"[yellow]{EDGE_CORE_SPEC.package_name} LaunchAgent is not loaded.[/yellow]"
            )
            return

        console.print(f"[red]launchctl bootout failed (exit {result.returncode}).[/red]")
        raise SystemExit(1)

    # Fallback: find and kill background process
    try:
        pids = _edge_process_pids()

        if not pids:
            console.print("[yellow]No running edge node found[/yellow]")
            return

        for pid in pids:
            os.kill(int(pid), signal.SIGTERM)
            console.print(f"[green]Stopped edge node (PID: {pid})[/green]")

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

    If the edge was installed as a systemd service, restarts it via systemctl
    (requires root).  On macOS with a LaunchAgent, restarts via launchctl.
    Otherwise falls back to stopping and re-starting the background process.

    \b
    Examples:
        sudo cyberwave edge restart
        cyberwave edge restart --env-file /path/to/.env
    """
    from ...core import (
        EDGE_CORE_SPEC,
        SYSTEMD_UNIT_PATH,
        _has_systemd,
        _is_macos,
        load_launchagent_service,
        restart_service,
    )

    if _has_systemd() and SYSTEMD_UNIT_PATH.exists():
        restart_service()
        return

    if _is_macos() and _macos_launchagent_plist_path().exists():
        if not _ensure_macos_launchagent_installed():
            raise SystemExit(1)
        console.print("[cyan]Restarting edge LaunchAgent...[/cyan]")
        if not load_launchagent_service(EDGE_CORE_SPEC):
            raise SystemExit(1)
        # The MJPEG camera bridge is a separate LaunchAgent
        # (com.cyberwave.camera-stream*) installed by ``edge install``,
        # so an edge-core reload alone won't recover a wedged ffmpeg
        # child or a slot that hit launchd's spawn-throttle.  Detect
        # silent ports and kickstart only the unhealthy ones — healthy
        # slots are left alone to avoid an unnecessary video gap.
        from ...macos import (
            kickstart_unhealthy_camera_streams,
            warn_on_camera_stream_config_drift,
        )

        kickstart_unhealthy_camera_streams()
        warn_on_camera_stream_config_drift()
        return

    # Fallback: stop running process, then start a new one
    import signal

    console.print("[cyan]Restarting edge node process...[/cyan]")

    try:
        pids = _edge_process_pids()

        for pid in pids:
            os.kill(int(pid), signal.SIGTERM)
            console.print(f"[dim]Stopped PID {pid}[/dim]")

        if pids:
            time.sleep(1)
    except Exception as exc:
        console.print(f"[yellow]Could not stop existing process: {exc}[/yellow]")

    env_path = Path(env_file).resolve() if env_file else Path(".env").resolve()
    if not env_path.exists():
        console.print(f"[red]Error: .env file not found at {env_path}[/red]")
        console.print("[dim]Pass --env-file or run from the directory containing .env[/dim]")
        return

    from ...config import clean_subprocess_env

    env = clean_subprocess_env()
    env["DOTENV_PATH"] = str(env_path)
    binary = _find_edge_core_binary()
    if not binary:
        console.print("[red]Error: cyberwave-edge-core binary not found[/red]")
        console.print("[dim]Run 'cyberwave edge install' to install it first.[/dim]")
        return

    try:
        process = subprocess.Popen(
            [binary],
            cwd=env_path.parent,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        console.print(f"[green]✓ Edge node restarted (PID: {process.pid})[/green]")
        console.print(_edge_process_logs_hint())
    except FileNotFoundError:
        console.print(f"[red]Binary not found: {binary}[/red]")
        console.print("[dim]Run 'cyberwave edge install' to reinstall it.[/dim]")


def _inspect_container_twin_uuid(container_name: str) -> str:
    """Extract CYBERWAVE_TWIN_UUID from a driver container's env vars."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{range .Config.Env}}{{println .}}{{end}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if line.startswith("CYBERWAVE_TWIN_UUID="):
                return line.split("=", 1)[1].strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


def _inspect_container_twin_uuids(container_name: str) -> list[str]:
    """Extract CYBERWAVE_TWIN_UUIDS from a worker container's env vars."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{range .Config.Env}}{{println .}}{{end}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if line.startswith("CYBERWAVE_TWIN_UUIDS="):
                csv = line.split("=", 1)[1].strip()
                return [t.strip() for t in csv.split(",") if t.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return []


@edge.command("status")
def status_edge():
    """Check edge node status."""
    from ...core import EDGE_CORE_SPEC, SYSTEMD_UNIT_NAME, _is_macos

    if _is_macos():
        from ...config import clean_subprocess_env

        _domain, target = _macos_launchagent_target()
        result = subprocess.run(
            ["launchctl", "print", target],
            capture_output=True,
            text=True,
            env=clean_subprocess_env(),
            check=False,
        )
        if result.returncode == 0:
            console.print(f"[green]✓ LaunchAgent {target}:[/green] loaded")
        elif _macos_launchagent_plist_path().exists():
            console.print(f"[yellow]  LaunchAgent {target}:[/yellow] installed but not loaded")
        else:
            console.print(f"[yellow]  LaunchAgent {target}:[/yellow] not installed")
    else:
        # --- systemd service ---
        try:
            result = subprocess.run(
                ["systemctl", "is-active", SYSTEMD_UNIT_NAME],
                capture_output=True,
                text=True,
            )
            service_state = result.stdout.strip()  # "active", "inactive", "failed", etc.
            if service_state == "active":
                console.print(f"[green]✓ Service {SYSTEMD_UNIT_NAME}:[/green] active")
            elif service_state == "failed":
                console.print(f"[red]✗ Service {SYSTEMD_UNIT_NAME}:[/red] failed")
            else:
                console.print(
                    f"[yellow]  Service {SYSTEMD_UNIT_NAME}:[/yellow] {service_state or 'not installed'}"
                )
        except FileNotFoundError:
            console.print("[dim]  systemctl not found — skipping service check.[/dim]")

    # --- driver containers (common to both platforms) ---
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "name=cyberwave-driver",
                "--format",
                "{{.Names}}\t{{.Image}}\t{{.Status}}",
            ],
            capture_output=True,
            text=True,
        )
        lines = [ln for ln in result.stdout.strip().splitlines() if ln]
        if lines:
            running_count = sum(1 for ln in lines if "Up " in ln.split("\t", 2)[-1])
            console.print(f"[green]Driver containers: {running_count}/{len(lines)} running[/green]")
            for line in lines:
                parts = line.split("\t")
                name = parts[0]
                image = parts[1] if len(parts) > 1 else ""
                status = parts[2] if len(parts) > 2 else ""
                is_running = "Up " in status
                twin_uuid = _inspect_container_twin_uuid(name)
                twin_label = f" twin={twin_uuid[:12]}…" if twin_uuid else ""
                color = "cyan" if is_running else "red"
                console.print(
                    f"   [{color}]{name}[/{color}]{twin_label}"
                    f"  [dim]{image}[/dim]  [dim]{status}[/dim]"
                )
        else:
            console.print("[yellow]No driver containers found[/yellow]")
    except FileNotFoundError:
        console.print("[dim]docker not found — skipping driver container check.[/dim]")
    except Exception as e:
        console.print(f"[red]Error checking driver containers: {e}[/red]")

    # --- worker container ---
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "name=cyberwave-worker",
                "--format",
                "{{.Names}}\t{{.Image}}\t{{.Status}}",
            ],
            capture_output=True,
            text=True,
        )
        lines = [ln for ln in result.stdout.strip().splitlines() if ln]
        if lines:
            for line in lines:
                parts = line.split("\t")
                name = parts[0]
                image = parts[1] if len(parts) > 1 else ""
                status = parts[2] if len(parts) > 2 else ""
                is_running = "Up " in status
                twin_uuids = _inspect_container_twin_uuids(name)
                twin_label = ""
                if twin_uuids:
                    twin_label = f" twins={','.join(t[:8] for t in twin_uuids)}"
                color = "green" if is_running else "red"
                icon = "✓" if is_running else "✗"
                console.print(
                    f"[{color}]{icon} Worker {name}[/{color}]{twin_label}"
                    f"  [dim]{image}[/dim]  [dim]{status}[/dim]"
                )
        else:
            console.print("[dim]No worker container found[/dim]")
    except FileNotFoundError:
        pass
    except Exception as e:
        console.print(f"[red]Error checking worker container: {e}[/red]")


@edge.command("cameras")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--save", is_flag=True, default=False, help="Save results to cameras.json")
def list_cameras(as_json: bool, save: bool):
    """List cameras detected on this edge device.

    Discovers connected cameras using platform-native tools:
    Linux uses v4l2-ctl, macOS uses AVFoundation (ffmpeg).

    \b
    Examples:
        cyberwave edge cameras
        cyberwave edge cameras --json
        cyberwave edge cameras --save
    """
    import platform as _platform

    from ...device_utils import discover_usb_cameras

    system = _platform.system()
    cameras = discover_usb_cameras()

    if as_json:
        click.echo(json.dumps([c.to_dict() for c in cameras], indent=2))
    elif not cameras:
        if system == "Linux":
            console.print("[yellow]No cameras detected.[/yellow]")
            if not shutil.which("v4l2-ctl"):
                if shutil.which("apt-get"):
                    console.print("[dim]Install v4l-utils: sudo apt-get install v4l-utils[/dim]")
                elif shutil.which("pacman"):
                    console.print("[dim]Install v4l-utils: sudo pacman -S v4l-utils[/dim]")
                else:
                    console.print("[dim]Install v4l-utils using your package manager.[/dim]")
        elif system == "Darwin":
            console.print("[yellow]No cameras detected.[/yellow]")
            if not shutil.which("ffmpeg"):
                console.print("[dim]Install ffmpeg: brew install ffmpeg[/dim]")
        else:
            console.print(f"[yellow]Camera discovery not supported on {system}.[/yellow]")
    else:
        from ...device_utils import camera_likelihood_score

        console.print(f"\n[bold]Detected {len(cameras)} camera(s):[/bold]\n")
        for i, cam in enumerate(cameras):
            idx_str = cam.index if cam.index is not None else i
            score = camera_likelihood_score(cam)
            dim = score < 40
            style_open = "[dim]" if dim else ""
            style_close = "[/dim]" if dim else ""
            tag = "  (probably not a camera)" if dim else ""
            console.print(
                f"  {style_open}[bold cyan]{idx_str}[/bold cyan])  {cam.card}{tag}{style_close}"
            )
            if cam.primary_path:
                console.print(f"  {style_open}     Device: {cam.primary_path}{style_close}")
            if cam.bus_info:
                console.print(f"  {style_open}     Bus:    {cam.bus_info}{style_close}")
            if cam.driver:
                console.print(f"  {style_open}     Driver: {cam.driver}{style_close}")
            if cam.serial:
                console.print(f"  {style_open}     Serial: {cam.serial}{style_close}")
            console.print()

    if save and cameras:
        from ...config import CONFIG_DIR
        from ...device_utils import write_cameras_json

        write_cameras_json(cameras, CONFIG_DIR)
        console.print(f"[green]✓[/green] Saved to {CONFIG_DIR / 'cameras.json'}")


# =============================================================================
# Driver Commands (containers.py)
# =============================================================================
from . import containers  # noqa: E402

containers.register(edge)


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
    if sys.platform == "darwin":
        try:
            _show_macos_launchagent_logs(follow=follow, lines=lines)
        except KeyboardInterrupt:
            pass
        return

    from ...config import clean_subprocess_env
    from ...core import SYSTEMD_UNIT_NAME

    service_name = SYSTEMD_UNIT_NAME.removesuffix(".service")
    cmd = [
        "journalctl",
        "-u",
        service_name,
        f"-n{lines}",
        "--no-pager",
        "--output=cat",
    ]
    if follow:
        cmd.append("-f")

    try:
        proc = subprocess.Popen(
            cmd,
            env=clean_subprocess_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                console.print(colorize_log_line(line.rstrip("\n")))
        except KeyboardInterrupt:
            pass
        finally:
            proc.terminate()
            proc.wait()
        if proc.returncode and proc.returncode not in (0, -15):
            console.print("[dim]Tip: run with sudo if you see no output.[/dim]")
    except FileNotFoundError:
        console.print("[red]journalctl not found. Is systemd available on this host?[/red]")
    except KeyboardInterrupt:
        pass


@edge.command("sync-workflows")
@click.option("--twin-uuid", help="Twin UUID to trigger workflow sync for via MQTT")
def sync_workflows(twin_uuid):
    """
    Sync workflow workers on the edge node.

    Without --twin-uuid, syncs locally for all twin UUIDs configured in
    environment.json. With --twin-uuid, keeps the remote trigger behavior and
    sends an MQTT command to the edge node for that twin.
    """
    from ...utils import get_sdk_client, print_error, print_success

    if not twin_uuid:
        try:
            from ...config import ensure_edge_core_importable

            ensure_edge_core_importable()
            startup = importlib.import_module("cyberwave_edge_core.startup")
        except Exception as e:
            print_error(
                f"Edge core is not available: {e}",
                "Install cyberwave-edge-core on this edge device and try again.",
            )
            return

        token = startup.load_token()
        if not token:
            print_error("Not authenticated.", "Run 'cyberwave login' first.")
            return

        twin_uuids = startup.load_selected_twin_uuids()
        if not twin_uuids:
            print_error(
                "No local twin UUIDs configured.",
                "Run 'cyberwave edge install' to populate environment.json.",
            )
            return

        base_url = (
            startup.get_runtime_env_var("CYBERWAVE_BASE_URL", startup.DEFAULT_API_URL)
            or startup.DEFAULT_API_URL
        )
        console.print(
            f"[cyan]Syncing workflow workers locally for {len(twin_uuids)} twin(s)...[/cyan]"
        )
        summary = startup._sync_workers_for_twins(
            token=token,
            twin_uuids=twin_uuids,
            base_url=base_url,
        )

        total_written = sum(r.get("written", 0) for r in summary.values())
        total_removed = sum(r.get("removed", 0) for r in summary.values())
        total_unchanged = sum(r.get("unchanged", 0) for r in summary.values())
        total_errors = sum(r.get("errors", 0) for r in summary.values())

        for twin in twin_uuids:
            stats = summary.get(twin, {})
            console.print(
                f"  [bold]{twin}[/bold] "
                f"written={stats.get('written', 0)} "
                f"removed={stats.get('removed', 0)} "
                f"unchanged={stats.get('unchanged', 0)} "
                f"errors={stats.get('errors', 0)}"
            )

        if total_errors:
            print_error(
                "Workflow sync completed with errors.",
                (
                    f"written={total_written}, removed={total_removed}, "
                    f"unchanged={total_unchanged}, errors={total_errors}"
                ),
            )
        else:
            print_success(
                (
                    "Workflow sync complete "
                    f"(written={total_written}, removed={total_removed}, "
                    f"unchanged={total_unchanged})."
                )
            )
        return

    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return

    console.print(f"[cyan]Sending sync_workflows command to twin {twin_uuid}...[/cyan]")

    try:
        # Publish command via MQTT
        client.mqtt.publish_command_message(twin_uuid, {"command": "sync_workflows"})

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

    from ...utils import get_sdk_client, print_error

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
                from ...utils import print_warning

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
            from ...utils import print_warning

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
    from cyberwave.fingerprint import format_device_info_table

    console.print("\n[bold]Device Information[/bold]\n")
    console.print(format_device_info_table())


# =============================================================================
# Config Sync Commands (pull.py / health.py)
# =============================================================================
from . import bench, pull, health  # noqa: E402

bench.register(edge)
pull.register(edge)
health.register(edge)
