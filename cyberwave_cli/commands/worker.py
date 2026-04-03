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
"""

import re
import shutil
import subprocess
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ..config import CONFIG_DIR

console = Console()

WORKERS_DIR = CONFIG_DIR / "workers"

WORKER_CONTAINER_PREFIX = "cyberwave-worker-"

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

    for f in files:
        stat = f.stat()
        size = f"{stat.st_size:,} B"
        origin = _worker_origin(f.name)
        origin_fmt = f"[dim]{origin}[/dim]" if origin == "workflow" else origin
        model_ids = _scan_model_ids(f)
        models_fmt = ", ".join(model_ids) if model_ids else "[dim]-[/dim]"
        table.add_row(f.stem, origin_fmt, models_fmt, f.name, size)

    console.print(table)
    console.print(f"\n[dim]{len(files)} worker(s) installed.[/dim]")
    console.print(
        "[dim]Tip: Generated workers (wf_*) are managed by edge-core sync.[/dim]"
    )


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

    console.print(f"[green]✓[/green] Worker installed: [bold]{dest_name}[/bold]")
    console.print(f"  Path: {dest}")
    console.print(
        "\n[dim]Edge-core will detect the change and restart the worker container automatically.[/dim]"
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
        console.print(
            "[dim]To stop a workflow worker, deactivate the workflow in the UI.[/dim]"
        )

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
        "\n[dim]Edge-core will detect the change and restart the worker container automatically.[/dim]"
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
    container_name = container or _find_worker_container()

    if not container_name:
        console.print("[red]✗[/red] Worker container not found.")
        console.print(
            "[dim]Start the worker container with: cyberwave-edge-core worker start[/dim]"
        )
        raise click.Abort()

    console.print(
        f"[dim]Streaming logs for container: [bold]{container_name}[/bold][/dim]"
    )

    cmd = ["docker", "logs", "--tail", str(tail)]
    if follow:
        cmd.append("--follow")
    cmd.append(container_name)

    try:
        subprocess.run(cmd, check=False)
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
        console.print(
            "  [dim]Start with: cyberwave-edge-core worker start[/dim]"
        )
        console.print()
        return

    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}} | {{.State.StartedAt}} | {{.State.FinishedAt}} | {{.State.ExitCode}}",
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
                console.print(
                    "\n  [dim]View logs with: cyberwave worker logs --no-follow[/dim]"
                )
        else:
            console.print(f"  [yellow]⚠[/yellow] Could not inspect container {container_name}.")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        console.print("[yellow]⚠[/yellow] Docker not available or timed out.")

    console.print()
