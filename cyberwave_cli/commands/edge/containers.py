"""``cyberwave edge driver`` subgroup (list, start, stop).

Manages cyberwave-driver Docker containers.
"""

from __future__ import annotations

import subprocess

import click
from rich.console import Console

console = Console()


def register(edge_group: click.Group) -> None:
    """Register the ``driver`` subgroup on the given click group."""
    edge_group.add_command(driver)


@click.group("driver")
def driver():
    """Manage edge driver containers."""
    pass


@driver.command("list")
@click.option("--all", "show_all", is_flag=True, default=False, help="Also show exited driver containers.")
def list_drivers(show_all: bool):
    """List running driver containers."""
    try:
        cmd = [
            "docker", "ps",
            "--filter", "name=cyberwave-driver",
            "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.ID}}",
        ]
        if show_all:
            cmd.insert(2, "--all")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]Error: {result.stderr.strip()}[/red]")
            return

        lines = result.stdout.strip().splitlines()
        if len(lines) <= 1:
            console.print("[yellow]No running driver containers found[/yellow]")
            return

        console.print("\n".join(lines))

    except FileNotFoundError:
        console.print("[red]Error: docker not found — is Docker installed and running?[/red]")
    except Exception as e:
        console.print(f"[red]Error listing drivers: {e}[/red]")


def _pick_driver_name(title: str = "Select a driver", *, stopped: bool = False) -> str | None:
    """Interactively pick a cyberwave-driver container name.

    When stopped=False (default) only running containers are listed.
    When stopped=True only exited/stopped containers are listed.
    """
    from ...core import _select_with_arrows

    if stopped:
        filter_args = ["--filter", "status=exited"]
    else:
        filter_args = ["--filter", "status=running", "--filter", "status=restarting"]
    try:
        result = subprocess.run(
            [
                "docker", "ps", "-a",
                "--filter", "name=cyberwave-driver",
                *filter_args,
                "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}",
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        console.print("[red]Error: docker not found — is Docker installed and running?[/red]")
        return None

    lines = [l for l in result.stdout.strip().splitlines() if l]
    if not lines:
        kind = "stopped" if stopped else "running"
        console.print(f"[yellow]No {kind} driver containers found[/yellow]")
        return None

    names = []
    options = []
    for line in lines:
        parts = line.split("\t")
        name  = parts[0]
        image = parts[1] if len(parts) > 1 else ""
        names.append(name)
        options.append(f"{name} [{image}]")

    idx = _select_with_arrows(title, options)
    return names[idx]


@driver.command("start")
@click.argument("name", required=False, default=None)
def start_driver(name: str | None):
    """Start a stopped driver container.

    If NAME is omitted, shows an interactive list of stopped driver containers
    to pick from.

    Note: this restarts an existing container that was previously stopped.
    To launch a brand-new driver, use the edge-core service which manages
    image selection and environment configuration.

    \b
    Examples:
        cyberwave edge driver start
        cyberwave edge driver start cyberwave-driver-624d7fe2
    """
    if name is None:
        try:
            name = _pick_driver_name("Select a driver to start", stopped=True)
        except KeyboardInterrupt:
            console.print("\n[dim]Aborted.[/dim]")
            return
        if name is None:
            return

    try:
        inspect = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", name],
            capture_output=True,
            text=True,
        )
        if inspect.returncode != 0:
            console.print(f"[red]Container '{name}' not found[/red]")
            return

        status = inspect.stdout.strip()
        if status == "running":
            console.print(f"[yellow]Container '{name}' is already running[/yellow]")
            return

        console.print(f"[cyan]Starting driver container '{name}'...[/cyan]")

        subprocess.run(
            ["docker", "update", "--restart=on-failure", name],
            capture_output=True,
            check=True,
        )

        result = subprocess.run(
            ["docker", "start", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]Failed to start container: {result.stderr.strip()}[/red]")
            return

        console.print(f"[green]✓ Started '{name}'[/green]")
        console.print(f"[dim]Restart policy set to on-failure — container will retry automatically[/dim]")

    except FileNotFoundError:
        console.print("[red]Error: docker not found — is Docker installed and running?[/red]")
    except Exception as e:
        console.print(f"[red]Error starting driver: {e}[/red]")


@driver.command("stop")
@click.argument("name", required=False, default=None)
def stop_driver(name: str | None):
    """Stop a running driver container.

    If NAME is omitted, shows an interactive list of running driver containers
    to pick from.

    Disables any Docker restart policy before stopping, so the container
    does not come back automatically.

    If the container is managed by a systemd service (e.g. on a Go2),
    Docker stop alone is not enough — systemd will restart it. In that
    case stop the backing service instead:

    \b
        sudo systemctl stop cyberwave-video-grabber.service

    \b
    Examples:
        cyberwave edge driver stop
        cyberwave edge driver stop cyberwave-driver-624d7fe2
        cyberwave edge driver stop cyberwave-go2-driver
    """
    if name is None:
        try:
            name = _pick_driver_name("Select a driver to stop")
        except KeyboardInterrupt:
            console.print("\n[dim]Aborted.[/dim]")
            return
        if name is None:
            return

    try:
        inspect = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}} {{.State.Restarting}} {{.HostConfig.RestartPolicy.Name}}", name],
            capture_output=True,
            text=True,
        )
        if inspect.returncode != 0:
            console.print(f"[red]Container '{name}' not found[/red]")
            return

        parts = inspect.stdout.strip().split()
        is_running = parts[0] == "true" if parts else False
        is_restarting = parts[1] == "true" if len(parts) > 1 else False
        restart_policy = parts[2] if len(parts) > 2 else "no"

        if not is_running and not is_restarting:
            console.print(f"[yellow]Container '{name}' is not running[/yellow]")
            return

        if restart_policy not in ("no", ""):
            console.print(f"[dim]Disabling Docker restart policy ({restart_policy})...[/dim]")
            subprocess.run(
                ["docker", "update", "--restart=no", name],
                capture_output=True,
                check=True,
            )

        console.print(f"[cyan]Stopping driver container '{name}'...[/cyan]")
        result = subprocess.run(
            ["docker", "stop", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]Failed to stop container: {result.stderr.strip()}[/red]")
            return

        console.print(f"[green]✓ Stopped '{name}'[/green]")
        console.print(
            "[dim]Note: if this driver is managed by a systemd service it may restart.\n"
            "      Check with: systemctl list-units 'cyberwave-*.service'[/dim]"
        )

    except FileNotFoundError:
        console.print("[red]Error: docker not found — is Docker installed and running?[/red]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Error stopping driver: {e}[/red]")
