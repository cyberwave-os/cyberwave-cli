"""
CLI commands for managing local worker files on edge nodes.

Workers are Python modules that run inside the edge worker container and
process sensor data using the Cyberwave SDK hooks API.

There are two kinds of workers:
  - **Custom** workers: handwritten files placed in the workers directory.
  - **Generated** (``wf_*``) workers: automatically generated from backend
    workflow definitions and synced via ``cyberwave-edge-core``.

Worker files live in ``{CONFIG_DIR}/workers/``.

Example usage:
    cyberwave worker list                    # List installed workers
    cyberwave worker add detect_people.py   # Copy a file into workers dir
    cyberwave worker remove detect_people   # Remove a worker (with or without .py)
    cyberwave worker logs                   # Stream worker container logs
    cyberwave worker status                 # Show worker container status
    cyberwave worker monitor                # Live resource/throughput dashboard
"""

from __future__ import annotations

import ast
import datetime
import json
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.live import Live
from rich.table import Table

from ..config import CONFIG_DIR
from ..utils import colorize_log_line

console = Console()

WORKERS_DIR = CONFIG_DIR / "workers"

WORKER_CONTAINER_PREFIX = "cyberwave-worker-"
DRIVER_CONTAINER_PREFIX = "cyberwave-driver-"

# Prefix used for workflow-generated worker files.
GENERATED_WORKER_PREFIX = "wf_"


def _get_workers_dir() -> Path:
    """Return the workers directory path, creating it if necessary."""
    WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    return WORKERS_DIR


def _worker_origin(filename: str) -> str:
    """Return origin label: 'workflow' for wf_* files, 'custom' otherwise."""
    if filename.startswith(GENERATED_WORKER_PREFIX):
        return "workflow"
    return "custom"


_CW_MODELS_LOAD_RE = re.compile(
    r"""cw\.models\.load\s*\(\s*['"]([^'"]+)['"]\s*""",
    re.MULTILINE,
)


def _scan_model_ids(filepath: Path) -> list[str]:
    """Return deduplicated model IDs referenced by ``cw.models.load(...)`` in *filepath*."""
    try:
        source = filepath.read_text(encoding="utf-8")
    except OSError:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for match in _CW_MODELS_LOAD_RE.finditer(source):
        model_id = match.group(1)
        if model_id not in seen:
            seen.add(model_id)
            result.append(model_id)
    return result


def _find_worker_container(*, include_stopped: bool = False) -> str | None:
    """Return a worker container name, or None if none exists.

    When *include_stopped* is True, stopped and exited containers are also
    considered (useful for ``status`` where we want to show crash/exit info).
    """
    cmd = ["docker", "ps"]
    if include_stopped:
        cmd.append("-a")
    cmd += [
        "--filter",
        f"name=^{WORKER_CONTAINER_PREFIX}",
        "--format",
        "{{.Names}}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            names = [n.strip() for n in result.stdout.strip().splitlines() if n.strip()]
            return names[0] if names else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


_EDGE_CORE_BINARY = "cyberwave-edge-core"


def _find_edge_core_binary() -> str | None:
    """Locate the cyberwave-edge-core binary on this system.

    Search order:
      1. The same bin directory as the running Python interpreter (covers
         pipx venvs, isolated venvs, and pip --user installs on macOS).
      2. System PATH via ``shutil.which``.
      3. Well-known system paths (apt-installed on Linux).
    """
    venv_candidate = Path(sys.executable).parent / _EDGE_CORE_BINARY
    if venv_candidate.is_file():
        return str(venv_candidate)

    found = shutil.which(_EDGE_CORE_BINARY)
    if found:
        return found

    for candidate in ("/usr/bin/cyberwave-edge-core", "/usr/local/bin/cyberwave-edge-core"):
        if Path(candidate).is_file():
            return candidate
    return None


def _delegate_to_edge_core(*args: str) -> None:
    """Run a cyberwave-edge-core subcommand, forwarding output and exit code."""
    binary = _find_edge_core_binary()
    if not binary:
        console.print(
            "[red]✗[/red] cyberwave-edge-core is required for this command.\n"
            "  Install it with: [bold]cyberwave edge install[/bold]"
        )
        sys.exit(1)

    try:
        result = subprocess.run([binary, *args])
        if result.returncode != 0:
            sys.exit(result.returncode)
    except OSError as exc:
        console.print(f"[red]✗[/red] Failed to run cyberwave-edge-core: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        pass


@click.group()
def worker() -> None:
    """Manage local worker files for edge inference.

    \b
    Workers are Python modules that run inside the edge worker container and
    subscribe to sensor data using @cw.on_frame and related hooks.

    \b
    Two kinds of workers:
      custom    Handwritten worker files you manage manually.
      workflow  Auto-generated from backend workflows (wf_* prefix).
                Managed by edge-core sync — do not edit directly.

    \b
    Quick start:
      cyberwave worker add my_detector.py   # Install a worker
      cyberwave worker list                 # Verify it's registered
      cyberwave worker status               # Check container state
    """
    from ..core import _migrate_legacy_config_dir

    _migrate_legacy_config_dir()


@worker.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_workers(as_json: bool) -> None:
    """List installed worker files."""
    workers_dir = _get_workers_dir()
    files = sorted(workers_dir.glob("*.py"))

    if as_json:
        import json

        data = [
            {
                "name": f.stem,
                "filename": f.name,
                "origin": _worker_origin(f.name),
                "size_bytes": f.stat().st_size,
                "model_ids": _scan_model_ids(f),
                "installed_at": datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            }
            for f in files
        ]
        console.print(json.dumps(data, indent=2))
        return

    if not files:
        console.print("[dim]No workers installed.[/dim]")
        console.print(f"[dim]Workers directory: {workers_dir}[/dim]")
        console.print("\n[dim]Add a worker with: cyberwave worker add <file.py>[/dim]")
        return

    table = Table(title=f"Installed Workers ({workers_dir})")
    table.add_column("Name", style="cyan")
    table.add_column("Origin", style="yellow")
    table.add_column("Models", style="green")
    table.add_column("File")
    table.add_column("Size")
    table.add_column("Installed", style="dim")

    for f in files:
        stat = f.stat()
        size = f"{stat.st_size:,} B"
        origin = _worker_origin(f.name)
        origin_fmt = f"[dim]{origin}[/dim]" if origin == "workflow" else origin
        model_ids = _scan_model_ids(f)
        models_fmt = ", ".join(model_ids) if model_ids else "[dim]-[/dim]"
        installed_dt = datetime.datetime.fromtimestamp(stat.st_mtime)
        installed_fmt = installed_dt.strftime("%Y-%m-%d %H:%M")
        table.add_row(f.stem, origin_fmt, models_fmt, f.name, size, installed_fmt)

    console.print(table)
    console.print(f"\n[dim]{len(files)} worker(s) installed.[/dim]")
    console.print("[dim]Tip: Generated workers (wf_*) are managed by edge-core sync.[/dim]")


@worker.command("add")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option(
    "--name",
    "-n",
    help="Override the destination filename (must end with .py)",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Overwrite existing worker without confirmation",
)
def add_worker(source: str, name: str | None, force: bool) -> None:
    """Add a worker file to the workers directory.

    SOURCE is the path to the Python worker file to install.

    Examples:

    \b
        cyberwave worker add ./detect_people.py
        cyberwave worker add ~/workers/my_model.py --name my_detector.py
    """
    src = Path(source)

    if name:
        if not name.endswith(".py"):
            console.print("[red]✗[/red] Name must end with .py")
            raise click.Abort()
        dest_name = name
    else:
        dest_name = src.name

    if not dest_name.endswith(".py"):
        console.print("[red]✗[/red] Worker files must have a .py extension")
        raise click.Abort()

    if dest_name.startswith(GENERATED_WORKER_PREFIX):
        console.print(
            f"[yellow]⚠[/yellow] Files starting with '{GENERATED_WORKER_PREFIX}' are "
            "reserved for workflow-generated workers managed by edge-core sync."
        )
        if not force:
            if not click.confirm("Continue anyway?"):
                raise click.Abort()

    workers_dir = _get_workers_dir()
    dest = workers_dir / dest_name

    if dest.exists() and not force:
        console.print(f"[yellow]⚠[/yellow] Worker [bold]{dest_name}[/bold] already exists.")
        if not click.confirm("Overwrite?"):
            raise click.Abort()

    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        console.print(f"[red]✗[/red] Failed to copy worker: {exc}")
        raise click.Abort() from exc

    # shutil.copy2 preserves the source's mode. When `worker add` is invoked
    # as root (e.g. via sudo or an edge-core-managed hook) the destination
    # ends up root-owned; a private source mode like 0600 is also preserved.
    # The worker container runs as a non-root user (UID 1001 in
    # cyberwaveos/edge-ml-worker), so restrictive modes produce silent
    # PermissionError at container start and 0 hooks loaded. Force world-
    # readable to avoid the footgun. A filesystem that can't honor chmod
    # (some FUSE mounts, macOS bind-mounts) shouldn't nuke a successful
    # copy — warn and continue.
    try:
        dest.chmod(0o644)
    except OSError as exc:
        console.print(
            f"[yellow]⚠[/yellow] Copied, but could not chmod 0644: {exc}. "
            "Verify the worker file is readable by the worker container "
            f"(UID 1001): chmod 0644 {dest}"
        )

    console.print(f"[green]✓[/green] Worker installed: [bold]{dest_name}[/bold]")
    console.print(f"  Path: {dest}")
    console.print(
        "\n[dim]Edge-core will detect the change and restart "
        "the worker container automatically.[/dim]"
    )
    console.print("[dim]Run 'cyberwave worker status' to check the container state.[/dim]")


@worker.command("remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def remove_worker(name: str, yes: bool) -> None:
    """Remove an installed worker file.

    NAME is the worker name or filename (with or without the .py extension).

    Examples:

    \b
        cyberwave worker remove detect_people
        cyberwave worker remove detect_people.py
    """
    workers_dir = _get_workers_dir()

    # Normalise: ensure .py extension.
    filename = name if name.endswith(".py") else f"{name}.py"
    target = workers_dir / filename

    if not target.exists():
        console.print(f"[red]✗[/red] Worker not found: [bold]{filename}[/bold]")
        console.print(f"[dim]Workers directory: {workers_dir}[/dim]")
        console.print("[dim]Run 'cyberwave worker list' to see installed workers.[/dim]")
        raise click.Abort()

    if filename.startswith(GENERATED_WORKER_PREFIX):
        console.print(
            f"[yellow]⚠[/yellow] [bold]{filename}[/bold] is a workflow-generated worker. "
            "Removing it manually will cause it to be re-created on the next edge sync."
        )
        console.print("[dim]To stop a workflow worker, deactivate the workflow in the UI.[/dim]")

    if not yes:
        if not click.confirm(f"Remove worker [bold]{filename}[/bold]?"):
            raise click.Abort()

    try:
        target.unlink()
    except OSError as exc:
        console.print(f"[red]✗[/red] Failed to remove worker: {exc}")
        raise click.Abort() from exc

    console.print(f"[green]✓[/green] Removed: [bold]{filename}[/bold]")
    console.print(
        "\n[dim]Edge-core will detect the change and restart "
        "the worker container automatically.[/dim]"
    )
    console.print("[dim]Run 'cyberwave worker status' to check the container state.[/dim]")


@worker.command("logs")
@click.option(
    "--follow",
    "-f",
    is_flag=True,
    default=True,
    show_default=True,
    help="Follow log output",
)
@click.option(
    "--tail",
    "-n",
    default=50,
    show_default=True,
    help="Number of lines to show from the end of the logs",
)
@click.option(
    "--container",
    "-c",
    help="Explicit container name (auto-detected if omitted)",
)
def worker_logs(follow: bool, tail: int, container: str | None) -> None:
    """Stream worker container logs.

    Requires Docker to be available on this host and the worker container to
    be running (managed by cyberwave-edge-core).

    Examples:

    \b
        cyberwave worker logs
        cyberwave worker logs --tail 100
        cyberwave worker logs --no-follow
    """
    container_name = container or _find_worker_container(include_stopped=True)

    if not container_name:
        console.print("[red]✗[/red] Worker container not found.")
        console.print("[dim]Start the worker container with: cyberwave worker start[/dim]")
        raise click.Abort()

    console.print(f"[dim]Streaming logs for container: [bold]{container_name}[/bold][/dim]")

    cmd = ["docker", "logs", "--tail", str(tail)]
    if follow:
        cmd.append("--follow")
    cmd.append(container_name)

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if proc.stdout:
            for line in proc.stdout:
                console.print(colorize_log_line(line.rstrip()))
        proc.wait()
    except FileNotFoundError:
        console.print("[red]✗[/red] Docker not found. Is Docker installed?")
        raise click.Abort()
    except KeyboardInterrupt:
        pass


@worker.command("status")
@click.option(
    "--container",
    "-c",
    help="Explicit container name (auto-detected if omitted)",
)
def worker_status(container: str | None) -> None:
    """Show worker container status and loaded worker files.

    Examples:

    \b
        cyberwave worker status
    """
    workers_dir = _get_workers_dir()
    files = sorted(workers_dir.glob("*.py"))

    # --- Worker files section ---
    console.print("\n[bold]Worker Files[/bold]")
    console.print(f"[dim]Directory: {workers_dir}[/dim]\n")

    if not files:
        console.print("  [dim]No workers installed.[/dim]")
    else:
        for f in files:
            origin = _worker_origin(f.name)
            tag = "[dim](workflow)[/dim]" if origin == "workflow" else "[dim](custom)[/dim]"
            console.print(f"  [cyan]{f.name}[/cyan]  {tag}")

    console.print(f"\n  Total: {len(files)} worker(s)")

    # --- Container section ---
    console.print("\n[bold]Worker Container[/bold]\n")
    container_name = container or _find_worker_container(include_stopped=True)

    if not container_name:
        console.print("  [yellow]⚠[/yellow] No worker container found.")
        console.print("  [dim]Start with: cyberwave worker start[/dim]")
        console.print()
        return

    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                (
                    "{{.State.Status}} | {{.State.StartedAt}} | "
                    "{{.State.FinishedAt}} | {{.State.ExitCode}}"
                ),
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(" | ")
            status = parts[0] if parts else "unknown"
            started = parts[1] if len(parts) > 1 else ""
            finished = parts[2] if len(parts) > 2 else ""
            exit_code = parts[3] if len(parts) > 3 else ""

            if status == "running":
                status_fmt = f"[green]{status}[/green]"
            elif status in {"exited", "dead"}:
                status_fmt = f"[red]{status}[/red]"
            else:
                status_fmt = f"[yellow]{status}[/yellow]"

            console.print(f"  Container: [bold]{container_name}[/bold]")
            console.print(f"  Status:    {status_fmt}")
            if started and not started.startswith("0001"):
                console.print(f"  Started:   [dim]{started[:19]}[/dim]")
            if status in {"exited", "dead"}:
                if finished and not finished.startswith("0001"):
                    console.print(f"  Stopped:   [dim]{finished[:19]}[/dim]")
                if exit_code and exit_code != "0":
                    console.print(f"  Exit code: [red]{exit_code}[/red]")
                console.print("\n  [dim]View logs with: cyberwave worker logs --no-follow[/dim]")
        else:
            console.print(f"  [yellow]⚠[/yellow] Could not inspect container {container_name}.")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        console.print("[yellow]⚠[/yellow] Docker not available or timed out.")

    console.print()


@worker.command("monitor")
@click.option(
    "--update",
    "-u",
    default=2.0,
    type=float,
    show_default=True,
    help="Dashboard refresh interval in seconds",
)
@click.option(
    "--container",
    "-c",
    help="Explicit container name (auto-detected if omitted)",
)
@click.option(
    "--all-hosts",
    "-a",
    is_flag=True,
    default=False,
    help=(
        "Show Zenoh stats from every worker discovered on the network "
        "instead of filtering to the local container only."
    ),
)
def worker_monitor(update: float, container: str | None, all_hosts: bool) -> None:
    """Live dashboard showing worker resource usage and Zenoh throughput.

    Displays CPU, memory, temperature, power consumption, GPU
    (Linux/NVIDIA only), per-channel Zenoh message rates, hook frame
    counts, and ML inference latency.

    With --all-hosts, shows a compact Zenoh-only panel for every worker
    reachable on the local Zenoh mesh (Docker/thermal metrics are not
    available for remote workers).

    \b
    Examples:
        cyberwave worker monitor
        cyberwave worker monitor --update 1
        cyberwave worker monitor -c cyberwave-worker-abc12345
        cyberwave worker monitor --all-hosts
    """
    import platform as _platform

    from ..monitor import (
        LinuxThermalReader,
        MacMonReader,
        RateTracker,
        ThermalPowerStats,
        WorkerSnapshot,
        ZenohStatsReader,
        build_dashboard,
        build_network_dashboard,
        collect_host_metrics,
        get_container_cpu_quota,
        get_container_hostname,
        parse_hook_stats,
        parse_model_stats,
    )

    container_name = container or _find_worker_container()

    if not container_name and not all_hosts:
        stopped = _find_worker_container(include_stopped=True)
        if stopped:
            console.print(f"[red]✗[/red] Worker container '{stopped}' exists but is not running.")
            console.print(
                "[dim]Check exit reason with: cyberwave worker logs\n"
                "Restart with: cyberwave worker restart[/dim]"
            )
        else:
            console.print("[red]✗[/red] Worker container not found.")
            console.print("[dim]Start the worker container with: cyberwave worker start[/dim]")
        raise click.Abort()

    if all_hosts:
        # ------------------------------------------------------------------ #
        # Network-wide view: one panel per discovered Zenoh worker host.      #
        # Docker/thermal metrics are omitted — they are not remotely          #
        # accessible.  The local container (if any) is just another entry.   #
        # ------------------------------------------------------------------ #
        if container_name:
            console.print(
                f"[dim]Connecting via container [bold]{container_name}[/bold] "
                f"— showing all hosts (refresh every {update}s)[/dim]\n"
            )
        else:
            console.print(
                f"[dim]Connecting to local Zenoh bus "
                f"— showing all hosts (refresh every {update}s)[/dim]\n"
            )

        zenoh_reader = ZenohStatsReader()
        zenoh_ok = zenoh_reader.start(container_name=container_name, all_hosts=True)
        if not zenoh_ok:
            console.print(
                "[red]✗[/red] Zenoh connection failed. "
                "Is a worker container running and exposing port 7447?"
            )
            raise click.Abort()

        per_host_trackers: dict[str, RateTracker] = {}
        try:
            with Live(console=console, refresh_per_second=1, screen=False) as live:
                while True:
                    live.update(
                        build_network_dashboard(
                            zenoh_reader.all_latest(),
                            per_host_trackers,
                        )
                    )
                    time.sleep(update)
        except KeyboardInterrupt:
            pass
        finally:
            zenoh_reader.stop()
            console.print("\n[dim]Monitor stopped.[/dim]")
        return

    # ---------------------------------------------------------------------- #
    # Default: single-container view with Docker + thermal + Zenoh stats.    #
    # ---------------------------------------------------------------------- #
    console.print(
        f"[dim]Monitoring container: [bold]{container_name}[/bold]  "
        f"(refresh every {update}s)[/dim]\n"
    )

    cpu_cores = get_container_cpu_quota(container_name)
    rate_tracker = RateTracker()

    container_hostname = get_container_hostname(container_name)
    zenoh_reader = ZenohStatsReader()
    zenoh_ok = zenoh_reader.start(
        container_name=container_name,
        target_hostname=container_hostname,
    )
    if not zenoh_ok:
        console.print(
            "[dim]Zenoh connection failed — showing Docker metrics only. "
            "The worker container may not be exposing the Zenoh listener yet.[/dim]\n"
        )

    if _platform.system() == "Darwin":
        thermal_reader: MacMonReader | LinuxThermalReader = MacMonReader()
    else:
        thermal_reader = LinuxThermalReader()
    thermal_ok = thermal_reader.start()
    if not thermal_ok and _platform.system() == "Darwin":
        console.print("[dim]Tip: brew install macmon for temperature & power data.[/dim]\n")

    try:
        with Live(console=console, refresh_per_second=1, screen=False) as live:
            while True:
                docker, gpu, uptime = collect_host_metrics(container_name)

                zenoh_data = zenoh_reader.latest() if zenoh_ok else {}
                transport = zenoh_data.get("transport", {})
                hooks_data = zenoh_data.get("hooks", {})
                models_data = zenoh_data.get("models", [])
                # Anchor rates on the worker clock to avoid wall-clock aliasing.
                snapshot_ts = zenoh_data.get("ts")

                snap = WorkerSnapshot(
                    container_name=container_name,
                    uptime=uptime,
                    cpu_cores=cpu_cores,
                    docker=docker,
                    gpu=gpu,
                    thermal_power=thermal_reader.latest() if thermal_ok else ThermalPowerStats(),
                    zenoh_channels=rate_tracker.update(transport, snapshot_ts=snapshot_ts),
                    hooks=parse_hook_stats(hooks_data),
                    models=parse_model_stats(models_data),
                )

                live.update(build_dashboard(snap))
                time.sleep(update)
    except KeyboardInterrupt:
        pass
    finally:
        if zenoh_ok:
            zenoh_reader.stop()
        if thermal_ok:
            thermal_reader.stop()
        console.print("\n[dim]Monitor stopped.[/dim]")


# ---------------------------------------------------------------------------
# Worker container lifecycle commands (delegated to cyberwave-edge-core)
# ---------------------------------------------------------------------------


@worker.command("start")
@click.option(
    "--skip-preflight",
    is_flag=True,
    help="Skip the pre-flight sanity checks and start unconditionally",
)
def worker_start(skip_preflight: bool) -> None:
    """Start the worker container.

    Requires cyberwave-edge-core to be installed. Ensures model weights
    are cached before launching.

    \b
    Examples:
        cyberwave worker start
        cyberwave worker start --skip-preflight
    """
    workers_dir = _get_workers_dir()
    has_workers = any(workers_dir.glob("*.py"))
    if not has_workers:
        console.print("[yellow]⚠[/yellow] No worker files found.")
        console.print(
            f"[dim]Workers directory: {workers_dir}\n"
            "Sync workers from the cloud with: cyberwave workflow sync\n"
            "Or place .py worker files in the directory above.[/dim]"
        )
        return

    # Run a quick pre-flight so common misconfigurations (unreadable worker
    # files, no matching driver on the same host, missing Zenoh env vars) are
    # surfaced before the container crashes with a PermissionError traceback
    # or loads 0 hooks.
    if not skip_preflight:
        preflight = _collect_preflight_checks(workers_dir)
        _print_preflight_checks(preflight, verbose=False)
        if any(c.level == "error" for c in preflight):
            console.print(
                "\n[red]✗[/red] Pre-flight failed. Run "
                "[bold]cyberwave worker doctor[/bold] for details, or re-run "
                "with [bold]--skip-preflight[/bold] if you know what you're doing."
            )
            raise click.Abort()

    _delegate_to_edge_core("worker", "start")


@worker.command("stop")
def worker_stop() -> None:
    """Stop the worker container.

    \b
    Examples:
        cyberwave worker stop
    """
    _delegate_to_edge_core("worker", "stop")


@worker.command("restart")
def worker_restart() -> None:
    """Restart the worker container.

    Re-scans worker files and re-ensures model weights are cached.

    \b
    Examples:
        cyberwave worker restart
    """
    _delegate_to_edge_core("worker", "restart")


@worker.command("health")
def worker_health() -> None:
    """Show detailed worker health: restart history and circuit-breaker state.

    \b
    Examples:
        cyberwave worker health
    """
    _delegate_to_edge_core("worker", "health")


# ---------------------------------------------------------------------------
# Pre-flight / doctor checks
# ---------------------------------------------------------------------------


class _Check:
    """Lightweight result object for a single pre-flight check."""

    __slots__ = ("name", "level", "message", "hint")

    def __init__(
        self,
        name: str,
        level: str,
        message: str,
        hint: str | None = None,
    ) -> None:
        # level is one of: "ok", "warn", "error", "info"
        self.name = name
        self.level = level
        self.message = message
        self.hint = hint


def _running_containers_with_prefix(prefix: str) -> list[str]:
    """Return the names of running containers whose name starts with *prefix*."""
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"name=^{prefix}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [n.strip() for n in result.stdout.splitlines() if n.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _running_driver_containers() -> list[str]:
    """Return the names of running camera/robot driver containers, or []."""
    return _running_containers_with_prefix(DRIVER_CONTAINER_PREFIX)


def _running_worker_containers() -> list[str]:
    """Return the names of running ML worker containers, or []."""
    return _running_containers_with_prefix(WORKER_CONTAINER_PREFIX)


def _inspect_container_env(container_name: str) -> dict[str, str]:
    """Return the ``KEY=VAL`` env map for a running container, or ``{}``."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config.Env}}", container_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        raw = json.loads(result.stdout)
        if not isinstance(raw, list):
            return {}
        env: dict[str, str] = {}
        for entry in raw:
            if not isinstance(entry, str) or "=" not in entry:
                continue
            key, _, value = entry.partition("=")
            env[key] = value
        return env
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return {}


_ENV_KEYS_TO_COMPARE = (
    "CYBERWAVE_ENVIRONMENT",
    "ZENOH_CONNECT",
    # Mismatches here silently put driver and worker on different transports.
    "CYBERWAVE_DATA_BACKEND",
    "ZENOH_SHARED_MEMORY",
)

_LEGACY_ENV_VARS: dict[str, str] = {
    # Canonical name is ``ZENOH_SHARED_MEMORY`` (see
    # ``cyberwave-edge-core/cyberwave_edge_core/zenoh_config.py``). A few
    # older docs and snippets referred to ``ZENOH_SHM_ENABLED``; if it shows
    # up in a container env it is silently ignored, producing a TCP-loopback
    # fallback that the operator didn't ask for.
    "ZENOH_SHM_ENABLED": "ZENOH_SHARED_MEMORY",
}


def _collect_preflight_checks(workers_dir: Path) -> list[_Check]:
    """Collect pre-flight signals that a worker is likely to run successfully.

    Each check is cheap (no network) and safe to run repeatedly. Severity
    reflects whether the issue *definitely* breaks the worker:

    * ``error`` — the worker cannot start or cannot load its hooks:
      ``edge-core`` missing, ``docker`` missing, worker files not
      world-readable.
    * ``warn`` — the worker may come up but has a known footgun class:
      no co-located driver yet, env drift between driver containers or
      between driver and worker containers, drivers with no twin binding.
    """
    checks: list[_Check] = []

    # 1. edge-core presence — delegation will fail without it.
    binary = _find_edge_core_binary()
    if binary:
        checks.append(_Check("edge-core", "ok", f"cyberwave-edge-core found at {binary}"))
    else:
        checks.append(
            _Check(
                "edge-core",
                "error",
                "cyberwave-edge-core not installed",
                hint="Install it with: cyberwave edge install",
            )
        )

    # 2. workers dir and file permissions — the main silent-failure class.
    if not workers_dir.is_dir():
        checks.append(
            _Check(
                "workers-dir",
                "error",
                f"Workers directory not found: {workers_dir}",
                hint="Add a worker with: cyberwave worker add <file.py>",
            )
        )
    else:
        files = sorted(workers_dir.glob("*.py"))
        if not files:
            checks.append(
                _Check(
                    "worker-files",
                    "warn",
                    "No .py worker files installed",
                    hint="Install one with: cyberwave worker add <file.py>",
                )
            )
        else:
            unreadable: list[str] = []
            for f in files:
                mode = f.stat().st_mode
                # The worker container runs as UID 1001 and must be able
                # to read every file; require at least "other-readable".
                if not (mode & stat.S_IROTH):
                    unreadable.append(f.name)
            if unreadable:
                checks.append(
                    _Check(
                        "worker-perms",
                        "error",
                        f"Worker files not world-readable: {', '.join(unreadable)}",
                        hint=(
                            "The worker container runs as a non-root user "
                            "(UID 1001) and cannot read mode 0600 files. "
                            f"Fix with: chmod 0644 {workers_dir}/*.py  "
                            "(or re-run `cyberwave worker add`, which now "
                            "chmod's 0644)."
                        ),
                    )
                )
            else:
                checks.append(
                    _Check(
                        "worker-perms",
                        "ok",
                        f"{len(files)} worker file(s), all readable",
                    )
                )

    # 3. docker availability — every subsequent check needs it, and so does
    # the delegated `edge-core worker start`.
    if not shutil.which("docker"):
        checks.append(
            _Check(
                "docker",
                "error",
                "docker not on PATH",
                hint=(
                    "edge-core manages workers and drivers via Docker. "
                    "Install Docker Engine and add your user to the "
                    "`docker` group."
                ),
            )
        )
        return checks

    # 4. co-located drivers: this is a warning, not an error. A worker can
    # legitimately come up before drivers (edge-core reconciles), or bind
    # to a remote Zenoh router with no local driver at all.
    drivers = _running_driver_containers()
    if not drivers:
        checks.append(
            _Check(
                "driver-container",
                "warn",
                "No cyberwave-driver-* container running on this host",
                hint=(
                    "Workers only receive frames from drivers reachable on "
                    "the same Zenoh session. If you expect a driver here, "
                    "start it with `cyberwave drivers start`; otherwise "
                    "ensure ZENOH_CONNECT points at the remote router."
                ),
            )
        )
    else:
        checks.append(
            _Check(
                "driver-container",
                "ok",
                f"{len(drivers)} driver container(s) running: {', '.join(drivers)}",
            )
        )

    # 5. env consistency across drivers and (if running) the worker.
    # We compare what containers actually have, not the CLI host's shell,
    # because the worker container is launched by edge-core with its own
    # env and the shell that ran this command is mostly irrelevant.
    driver_envs: dict[str, dict[str, str]] = {
        name: _inspect_container_env(name) for name in drivers
    }
    driver_envs = {k: v for k, v in driver_envs.items() if v}

    workers = _running_worker_containers()
    worker_envs: dict[str, dict[str, str]] = {
        name: _inspect_container_env(name) for name in workers
    }
    worker_envs = {k: v for k, v in worker_envs.items() if v}

    mismatches: list[str] = []

    # driver ↔ driver: multiple drivers on the same host must agree, or
    # they're publishing on disjoint Zenoh sessions / environments.
    if len(driver_envs) > 1:
        names = list(driver_envs)
        base = driver_envs[names[0]]
        for other_name in names[1:]:
            other = driver_envs[other_name]
            for key in _ENV_KEYS_TO_COMPARE:
                a, b = base.get(key, ""), other.get(key, "")
                if a and b and a != b:
                    mismatches.append(
                        f"driver disagreement: {names[0]}:{key}={a!r} vs "
                        f"{other_name}:{key}={b!r}"
                    )

    # driver ↔ worker: at least one driver must share env with the worker.
    for w_name, w_env in worker_envs.items():
        for d_name, d_env in driver_envs.items():
            for key in _ENV_KEYS_TO_COMPARE:
                a, b = w_env.get(key, ""), d_env.get(key, "")
                if a and b and a != b:
                    mismatches.append(
                        f"worker/driver disagreement: {w_name}:{key}={a!r} "
                        f"vs {d_name}:{key}={b!r}"
                    )

    if mismatches:
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique = [m for m in mismatches if not (m in seen or seen.add(m))]
        checks.append(
            _Check(
                "env-consistency",
                "warn",
                "Container env drift detected",
                hint=(
                    "Mismatched env produces healthy-looking containers "
                    "that publish on disjoint Zenoh sessions. Review:\n  "
                    + "\n  ".join(unique)
                ),
            )
        )
    elif driver_envs or worker_envs:
        checks.append(
            _Check(
                "env-consistency",
                "ok",
                "Container env agrees on CYBERWAVE_ENVIRONMENT / ZENOH_CONNECT",
            )
        )

    # 6. legacy / typo'd env var names. The canonical name is
    # ``ZENOH_SHARED_MEMORY`` but older docs leaked ``ZENOH_SHM_ENABLED``;
    # if either side picked up the legacy spelling the transport silently
    # degrades to TCP loopback.
    legacy_sightings: list[str] = []
    for cname, env in {**driver_envs, **worker_envs}.items():
        for legacy, canonical in _LEGACY_ENV_VARS.items():
            if legacy in env:
                legacy_sightings.append(
                    f"{cname}: {legacy}={env[legacy]!r} (use {canonical})"
                )
    if legacy_sightings:
        checks.append(
            _Check(
                "env-legacy-names",
                "warn",
                "Non-canonical env var names detected — will be ignored",
                hint=(
                    "Cyberwave reads only the canonical name. "
                    "Rename these on the affected containers:\n  "
                    + "\n  ".join(legacy_sightings)
                ),
            )
        )

    # 7. twin binding: drivers with no twin env publish on undefined keys.
    missing_twin: list[str] = [
        name
        for name, env in driver_envs.items()
        if not env.get("CYBERWAVE_TWIN_JSON_FILE") and not env.get("CYBERWAVE_TWIN_UUID")
    ]
    if missing_twin:
        checks.append(
            _Check(
                "twin-binding",
                "warn",
                "Driver containers with no twin binding: " + ", ".join(missing_twin),
                hint=(
                    "Drivers started without CYBERWAVE_TWIN_JSON_FILE or "
                    "CYBERWAVE_TWIN_UUID publish to undefined keys."
                ),
            )
        )

    return checks


# ---------------------------------------------------------------------------
# Runtime probes — actually join the Zenoh bus and compare hook key-expressions
# against live publisher traffic. These complement the static checks above
# (which only verify "paperwork") with a ground-truth view of what is flowing.
# ---------------------------------------------------------------------------


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Canonical key shape: ``cw/<twin-uuid>/data/<channel>[/<sensor>]``. Anything
# else is considered "unscoped" and won't match twin-scoped hook subscriptions.
# See cyberwave-sdks/cyberwave-python/cyberwave/data/keys.py for the spec.
_CANONICAL_KEY_RE = re.compile(
    r"^cw/"
    r"(?P<twin>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/"
    r"data/"
    r"(?P<channel>[a-z][a-z0-9_]*)"
    r"(?:/(?P<sensor>[a-z][a-z0-9_]*))?$"
)


@dataclass(frozen=True)
class _ParsedKey:
    """Parsed canonical key, or ``None`` at call sites for non-canonical keys."""

    twin: str
    channel: str
    sensor: str | None


def _parse_canonical_key(key: str) -> _ParsedKey | None:
    """Return a :class:`_ParsedKey` or ``None`` if *key* isn't canonical."""
    m = _CANONICAL_KEY_RE.match(key)
    if not m:
        return None
    return _ParsedKey(
        twin=m.group("twin"), channel=m.group("channel"), sensor=m.group("sensor")
    )


# Maps @cw.on_<method> to (channel base name, whether the channel carries an
# optional "sensor" qualifier). Mirrors HookRegistry in
# cyberwave-sdks/cyberwave-python/cyberwave/workers/hooks.py.
_HOOK_METHOD_MAP: dict[str, tuple[str, bool]] = {
    "on_frame": ("frames", True),
    "on_depth": ("depth", True),
    "on_audio": ("audio", True),
    "on_pointcloud": ("pointcloud", True),
    "on_lidar": ("lidar", True),
    "on_imu": ("imu", False),
    "on_force_torque": ("force_torque", False),
    "on_joint_states": ("joint_states", False),
    "on_attitude": ("attitude", False),
    "on_gps": ("gps", False),
    "on_end_effector_pose": ("end_effector_pose", False),
    "on_gripper_state": ("gripper_state", False),
    "on_map": ("map", False),
    "on_battery": ("battery", False),
    "on_temperature": ("temperature", False),
}


@dataclass(frozen=True)
class _HookBinding:
    """One scanned @cw.on_* registration from a worker file."""

    file: str
    hook_name: str
    method: str
    twin_uuid: str
    channel: str
    sensor: str | None

    @property
    def expected_key(self) -> str:
        # Keys look like ``cw/<uuid>/data/<channel>[/<sensor>]`` — see
        # cyberwave/data/keys.py. Generic ``on_data`` is skipped by the
        # scanner (its second positional arg is the channel name and we
        # don't try to resolve it through the ast).
        if self.sensor:
            return f"cw/{self.twin_uuid}/data/{self.channel}/{self.sensor}"
        return f"cw/{self.twin_uuid}/data/{self.channel}"

    @property
    def label(self) -> str:
        return f"{self.file}:{self.hook_name}"


def _resolve_str_constant(
    node: ast.expr, symbols: dict[str, str]
) -> str | None:
    """Best-effort resolution of *node* to a str at parse time."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return symbols.get(node.id)
    return None


def _collect_module_symbols(tree: ast.Module) -> dict[str, str]:
    """Build a symbol table of module-level ``name = "<literal str>"``.

    Covers plain ``ast.Assign`` and annotated ``ast.AnnAssign``. Returns a
    best-effort map — anything that isn't a trivial literal is skipped.
    """
    symbols: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols[target.id] = node.value.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            symbols[node.target.id] = node.value.value
    return symbols


def _scan_hook_registrations(filepath: Path) -> list[_HookBinding]:
    """Statically extract ``@cw.on_*(twin, ...)`` decorators from *filepath*.

    The scanner is deliberately conservative: it resolves twin UUIDs from
    module-level string literals (plain or annotated assigns). Anything
    else — dynamically built UUIDs, attribute accesses, function calls,
    tuple unpacking — is skipped rather than guessed.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []

    symbols = _collect_module_symbols(tree)

    bindings: list[_HookBinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            method = (
                dec.func.attr
                if isinstance(dec.func, ast.Attribute)
                else (dec.func.id if isinstance(dec.func, ast.Name) else None)
            )
            if method not in _HOOK_METHOD_MAP or not dec.args:
                continue
            channel_base, has_sensor = _HOOK_METHOD_MAP[method]
            twin_uuid = _resolve_str_constant(dec.args[0], symbols)
            if not twin_uuid or not _UUID_RE.match(twin_uuid):
                continue
            sensor: str | None = "default" if has_sensor else None
            for kw in dec.keywords:
                if (
                    kw.arg == "sensor"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    sensor = kw.value.value
            bindings.append(
                _HookBinding(
                    file=filepath.name,
                    hook_name=node.name,
                    method=method,
                    twin_uuid=twin_uuid,
                    channel=channel_base,
                    sensor=sensor,
                )
            )
    return bindings


def _listen_to_loopback_connect(listen: str) -> str | None:
    """Rewrite a container's ``ZENOH_LISTEN`` into a connectable endpoint.

    The doctor runs on the host; container-side listeners that bind
    ``0.0.0.0`` / ``[::]`` must be rewritten to a loopback address so a
    host-side Zenoh session can reach them. Non-TCP endpoints are skipped
    — Zenoh supports ``udp/``, ``quic/``, ``ws/``, etc., but loopback
    fallback only makes sense for TCP. Comma-separated lists (Zenoh
    accepts JSON arrays too) are not handled here and skipped.
    """
    listen = listen.strip()
    if not listen or "," in listen or not listen.startswith("tcp/"):
        return None
    return listen.replace("tcp/0.0.0.0", "tcp/127.0.0.1").replace(
        "tcp/[::]", "tcp/[::1]"
    )


def _zenoh_probe_endpoint(
    driver_containers: list[str], worker_containers: list[str]
) -> list[str] | None:
    """Pick a reasonable ``connect/endpoints`` list for the doctor's session.

    We try, in order:
      1. Any ``ZENOH_LISTEN`` value declared on a running worker/driver
         container (most accurate on bridge-networked setups).
      2. The container's bridge IP at port 7447.
      3. Fall back to ``tcp/127.0.0.1:7447`` — correct when the
         driver/worker use host networking (the common Linux edge case).

    Returns ``None`` when no running container could be used. The caller
    is then expected to leave multicast discovery enabled so distant
    peers on the same network can still be picked up.
    """
    from ..monitor import get_container_ip

    candidates = worker_containers + driver_containers
    for name in candidates:
        env = _inspect_container_env(name)
        listen = env.get("ZENOH_LISTEN", "")
        conn = _listen_to_loopback_connect(listen)
        if conn:
            return [conn]
    for name in candidates:
        # get_container_ip is only correct for bridge networks — host-mode
        # containers return no IP, and loopback is the right answer there.
        ip = get_container_ip(name)
        if ip:
            return [f"tcp/{ip}:7447"]
    if candidates:
        return ["tcp/127.0.0.1:7447"]
    return None


def _probe_zenoh_bus(
    *, duration: float, connect: list[str] | None
) -> tuple[bool, dict[str, int], str | None]:
    """Subscribe to ``**`` for *duration* seconds. Returns (ok, counts, err).

    When *connect* is ``None`` we leave multicast scouting enabled so the
    probe has a chance of discovering peers we couldn't enumerate via
    Docker. When *connect* is set we disable multicast (the endpoint list
    is authoritative, multicast would just add latency).
    """
    try:
        import zenoh
    except ImportError:
        return False, {}, "missing-dep:eclipse-zenoh"

    seen: dict[str, int] = {}
    session: Any = None
    subscription: Any = None
    try:
        cfg = zenoh.Config()
        cfg.insert_json5("transport/shared_memory/enabled", "false")
        if connect:
            cfg.insert_json5("connect/endpoints", json.dumps(connect))
            cfg.insert_json5("scouting/multicast/enabled", "false")
        session = zenoh.open(cfg)

        def _on_sample(sample: Any) -> None:
            try:
                key = str(sample.key_expr)
            except Exception:
                return
            seen[key] = seen.get(key, 0) + 1

        subscription = session.declare_subscriber("**", _on_sample)
        # Give the session ~200ms to complete the TCP handshake before we
        # start the listening window, so --window 1 actually gives ~1s of
        # sampling rather than ~0.5s.
        time.sleep(0.2)
        time.sleep(max(0.5, duration))
        return True, seen, None
    except Exception as exc:  # noqa: BLE001 — zenoh raises many shapes
        return False, seen, str(exc)
    finally:
        if subscription is not None:
            try:
                subscription.undeclare()
            except Exception:
                pass
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _diagnose_binding(
    binding: _HookBinding,
    seen: dict[str, int],
    parsed_by_key: dict[str, _ParsedKey],
) -> tuple[str, str | None]:
    """Classify *binding* against the observed bus traffic.

    Returns ``(reason, example_key)`` where *reason* is one of:

    * ``"ok"``             — at least one publication matches the hook exactly.
    * ``"sensor_mismatch"`` — same twin + same base channel, different sensor.
    * ``"wrong_twin"``     — same base channel + sensor, different twin UUID.
    * ``"no_publisher"``   — nothing on the bus looks anything like this hook.
    """
    expected = binding.expected_key
    if expected in seen:
        return "ok", expected

    same_twin_channel = [
        k
        for k, p in parsed_by_key.items()
        if p.twin == binding.twin_uuid and p.channel == binding.channel
    ]
    if same_twin_channel:
        return "sensor_mismatch", same_twin_channel[0]

    wrong_twin = [
        k
        for k, p in parsed_by_key.items()
        if p.channel == binding.channel
        and p.sensor == binding.sensor
        and p.twin != binding.twin_uuid
    ]
    if wrong_twin:
        return "wrong_twin", wrong_twin[0]

    return "no_publisher", None


def _collect_runtime_checks(
    workers_dir: Path, *, duration: float
) -> list[_Check]:
    """Join the Zenoh bus for a few seconds and compare actual traffic
    against worker hook key-expressions."""
    checks: list[_Check] = []

    drivers = _running_driver_containers()
    workers = _running_worker_containers()
    connect = _zenoh_probe_endpoint(drivers, workers)

    ok, seen, err = _probe_zenoh_bus(duration=duration, connect=connect)

    if not ok and err == "missing-dep:eclipse-zenoh":
        checks.append(
            _Check(
                "zenoh-liveness",
                "info",
                "Runtime probe skipped — eclipse-zenoh not installed on the host",
                hint=(
                    "Install it so the doctor can join the bus and verify "
                    "traffic flow: pip install --user eclipse-zenoh"
                ),
            )
        )
        return checks
    if not ok:
        checks.append(
            _Check(
                "zenoh-liveness",
                "warn",
                f"Could not open a Zenoh session ({err or 'unknown error'})",
                hint=(
                    "The doctor couldn't join the bus, so runtime probes "
                    "are disabled. Verify a worker/driver container is "
                    "running and exposing ZENOH_LISTEN (default "
                    "tcp/0.0.0.0:7447).\n"
                    f"Attempted connect: {connect or 'default/multicast'}"
                ),
            )
        )
        return checks

    # Classify every observed key once, up-front. Canonical keys are the
    # only ones that can possibly feed a twin-scoped hook; everything else
    # is categorized for reporting.
    parsed_by_key: dict[str, _ParsedKey] = {}
    unscoped_keys: list[str] = []
    monitor_keys: list[str] = []
    admin_keys: list[str] = []
    for k in seen:
        parsed = _parse_canonical_key(k)
        if parsed is not None:
            parsed_by_key[k] = parsed
        elif k.startswith("cw/_monitor/"):
            monitor_keys.append(k)
        elif k.startswith("@/"):
            # Zenoh admin/liveliness keys. Not actionable for users, but
            # worth counting separately so they don't crowd data in the
            # top-keys display.
            admin_keys.append(k)
        else:
            unscoped_keys.append(k)

    def _msg_count(keys: list[str] | dict[str, Any]) -> int:
        return sum(seen[k] for k in keys)

    data_msgs = _msg_count(parsed_by_key)
    monitor_msgs = _msg_count(monitor_keys)
    unscoped_msgs = _msg_count(unscoped_keys)
    admin_msgs = _msg_count(admin_keys)
    app_msgs = data_msgs + monitor_msgs + unscoped_msgs

    # Silent bus: report once and early-return. Running alignment here
    # would just duplicate the warning with "no matching publisher
    # traffic", which the user already knows.
    #
    # "Silent" means: no application-level traffic. Zenoh admin/liveliness
    # keys (``@/...``) alone don't count — they're just peer discovery
    # heartbeats and prove nothing about whether drivers are publishing.
    if app_msgs == 0:
        hint_lines = [
            "Either no publisher is putting frames, or the doctor can't "
            "reach the bus from this host. Next steps:",
            "  • Confirm a driver container is running "
            "(docker ps --filter name=cyberwave-driver-)",
            "  • Check the driver isn't disabled via "
            "CYBERWAVE_PUBLISH_MODE=off",
            "  • Run `cyberwave worker monitor` for live rates",
        ]
        if admin_msgs:
            hint_lines.append(
                f"  • Saw {admin_msgs} Zenoh admin message(s) — the bus "
                "is reachable but no publisher is putting to it."
            )
        if not drivers and not workers:
            hint_lines.append(
                "  • No cyberwave-driver-* / cyberwave-worker-* containers "
                "are running locally; the probe used "
                f"{connect or 'default multicast discovery'}."
            )
        checks.append(
            _Check(
                "zenoh-liveness",
                "warn",
                f"No application traffic seen in {duration:.0f}s"
                + (f" ({admin_msgs} admin msg(s) only)" if admin_msgs else ""),
                hint="\n".join(hint_lines),
            )
        )
        return checks

    # Rank top keys with data first (the user cares about what hooks will
    # see), then monitor + unscoped, then admin as last resort. Within
    # each tier, sort by message count.
    def _ranked(keys: list[str] | dict[str, Any]) -> list[tuple[str, int]]:
        return sorted(((k, seen[k]) for k in keys), key=lambda kv: -kv[1])

    top = (
        _ranked(parsed_by_key)
        + _ranked(monitor_keys)
        + _ranked(unscoped_keys)
        + _ranked(admin_keys)
    )[:5]
    top_lines = [f"{n:>6} msg(s)  {k}" for k, n in top]
    app_key_count = len(parsed_by_key) + len(monitor_keys) + len(unscoped_keys)
    summary = (
        f"{app_msgs} app msg(s) across {app_key_count} key(s) in "
        f"{duration:.0f}s  ({len(parsed_by_key)} data, "
        f"{len(monitor_keys)} monitor, {len(unscoped_keys)} unscoped"
        + (f"; +{len(admin_keys)} admin" if admin_keys else "")
        + ")"
    )
    checks.append(
        _Check(
            "zenoh-liveness",
            "ok",
            summary,
            hint="Top keys:\n  " + "\n  ".join(top_lines),
        )
    )

    # Flag keys that don't follow the canonical ``cw/<twin>/data/...``
    # schema. These will never be delivered to a twin-scoped hook, no
    # matter how many messages they carry. Includes both bare keys
    # (``frames/color_camera``) and ``cw/``-prefixed but malformed ones
    # (``cw/camera/frames``).
    if unscoped_keys:
        uniq_unscoped = sorted(set(unscoped_keys))
        sample = uniq_unscoped[:5]
        checks.append(
            _Check(
                "keyexpr-scoping",
                "warn",
                f"{len(uniq_unscoped)} key(s) published outside the "
                "canonical 'cw/<twin>/data/<channel>[/<sensor>]' schema",
                hint=(
                    "Twin-scoped worker hooks won't match these keys, so "
                    "their messages are silently ignored. Fix the publisher "
                    "to use the canonical schema (build keys with "
                    "cyberwave.data.keys.build_key).\n"
                    f"Example keys: {', '.join(sample)}"
                ),
            )
        )

    # Hook alignment: diagnose each @cw.on_* binding individually.
    bindings: list[_HookBinding] = []
    if workers_dir.is_dir():
        for f in sorted(workers_dir.glob("*.py")):
            bindings.extend(_scan_hook_registrations(f))

    if not bindings:
        checks.append(
            _Check(
                "keyexpr-alignment",
                "info",
                "No resolvable @cw.on_* hooks found in worker files",
                hint=(
                    "Either no hooks are declared, or the twin UUIDs are "
                    "not string literals / module-level constants. The "
                    "scanner only resolves static UUIDs."
                ),
            )
        )
        return checks

    diagnoses = [
        (b, *_diagnose_binding(b, seen, parsed_by_key)) for b in bindings
    ]
    n_matched = sum(1 for d in diagnoses if d[1] == "ok")
    unmatched = [d for d in diagnoses if d[1] != "ok"]

    if not unmatched:
        checks.append(
            _Check(
                "keyexpr-alignment",
                "ok",
                f"All {n_matched} hook(s) see matching publisher traffic",
            )
        )
        return checks

    # Build a per-hook diagnostic block. Group by reason so users see the
    # structural bug (sensor mismatch vs wrong twin vs no publisher at all)
    # instead of a wall of expected-key lines.
    sections: list[str] = []
    by_reason: dict[str, list[tuple[_HookBinding, str | None]]] = {
        "sensor_mismatch": [],
        "wrong_twin": [],
        "no_publisher": [],
    }
    for b, reason, example in unmatched:
        by_reason[reason].append((b, example))

    if by_reason["sensor_mismatch"]:
        sections.append(
            "Sensor mismatch — hook and publisher agree on the twin and "
            "channel but not the sensor qualifier:"
        )
        for b, ex in by_reason["sensor_mismatch"]:
            parsed_ex = _parse_canonical_key(ex or "") if ex else None
            pub_sensor = parsed_ex.sensor if parsed_ex else None
            hook_sensor_repr = repr(b.sensor) if b.sensor else "(none)"
            pub_sensor_repr = repr(pub_sensor) if pub_sensor else "(none)"
            sections.append(
                f"  {b.label}  hook sensor={hook_sensor_repr}, "
                f"publisher sensor={pub_sensor_repr}  "
                f"(expected {b.expected_key}, bus has {ex})"
            )
    if by_reason["wrong_twin"]:
        sections.append(
            "Wrong twin — channel is flowing, but under a different twin UUID:"
        )
        for b, ex in by_reason["wrong_twin"]:
            sections.append(
                f"  {b.label}  expects {b.expected_key}  →  bus has {ex}"
            )
    if by_reason["no_publisher"]:
        sections.append(
            "No publisher — no key on the bus comes close to this hook:"
        )
        for b, _ in by_reason["no_publisher"]:
            sections.append(f"  {b.label}  expects {b.expected_key}")

    sections.append(
        "Compare to the 'zenoh-liveness' top-keys list above and verify "
        "the twin/sensor the driver publishes under match @cw.on_*()."
    )

    checks.append(
        _Check(
            "keyexpr-alignment",
            "warn",
            f"{len(unmatched)} of {len(bindings)} hook(s) have no "
            "matching publisher traffic",
            hint="\n".join(sections),
        )
    )

    return checks


_LEVEL_GLYPHS = {
    "ok": "[green]✓[/green]",
    "warn": "[yellow]⚠[/yellow]",
    "error": "[red]✗[/red]",
    "info": "[dim]·[/dim]",
}


def _print_preflight_checks(checks: list[_Check], *, verbose: bool) -> None:
    """Render pre-flight/doctor results to the console."""
    for c in checks:
        glyph = _LEVEL_GLYPHS.get(c.level, "·")
        console.print(f"  {glyph} [bold]{c.name}[/bold]  {c.message}")
        if c.hint and (verbose or c.level in {"warn", "error"}):
            for line in c.hint.splitlines():
                console.print(f"      [dim]{line}[/dim]")


@worker.command("doctor")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show hints for passing checks too",
)
@click.option(
    "--runtime/--no-runtime",
    default=True,
    show_default=True,
    help=(
        "Run the live-bus probe: subscribe to Zenoh for a few seconds and "
        "compare actual traffic against worker hook key-expressions. "
        "Disable with --no-runtime for a pure-paperwork check."
    ),
)
@click.option(
    "--window",
    default=3.0,
    type=float,
    show_default=True,
    help="Seconds to listen on the Zenoh bus during the runtime probe",
)
def worker_doctor(verbose: bool, runtime: bool, window: float) -> None:
    """Diagnose why a worker may not receive frames.

    Runs two groups of checks:

    \b
    1. Static (paperwork): edge-core/docker present, worker files readable,
       co-located drivers running, env-var agreement between driver and
       worker containers, twin bindings.
    2. Runtime (actual traffic): opens a short Zenoh subscription to '**',
       counts keys seen, and checks that every @cw.on_* hook declared in
       your worker files has a matching publisher on the bus. Catches the
       main 'paperwork OK, nothing flowing' failure mode.

    \b
    Examples:
        cyberwave worker doctor
        cyberwave worker doctor --verbose
        cyberwave worker doctor --no-runtime      # skip the 3s bus probe
        cyberwave worker doctor --window 6        # longer runtime probe
    """
    workers_dir = _get_workers_dir()
    console.print("\n[bold]Cyberwave worker doctor[/bold]\n")
    console.print(f"[dim]Workers directory: {workers_dir}[/dim]\n")

    console.print("[bold]Static checks[/bold]")
    static_checks = _collect_preflight_checks(workers_dir)
    _print_preflight_checks(static_checks, verbose=verbose)

    runtime_checks: list[_Check] = []
    if runtime:
        console.print(
            "\n[bold]Runtime checks[/bold]  "
            f"[dim](probing Zenoh for {window:.0f}s)[/dim]"
        )
        runtime_checks = _collect_runtime_checks(workers_dir, duration=window)
        _print_preflight_checks(runtime_checks, verbose=verbose)
    else:
        console.print("\n[dim]Runtime checks skipped (--no-runtime).[/dim]")

    all_checks = static_checks + runtime_checks
    errors = sum(1 for c in all_checks if c.level == "error")
    warnings = sum(1 for c in all_checks if c.level == "warn")
    console.print()
    if errors:
        console.print(f"[red]{errors} blocking issue(s)[/red], {warnings} warning(s)")
        sys.exit(1)
    if warnings:
        console.print(f"[yellow]{warnings} warning(s)[/yellow] — worker may still run")
        return
    console.print("[green]All checks passed.[/green]")
