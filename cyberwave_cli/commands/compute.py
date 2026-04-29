"""
CLI commands for managing the cloud node service.

Example usage:
    # Install the cloud node
    sudo cyberwave compute install

    # Start with a specific hardware profile
    cyberwave compute start --slug my-gpu-node --profile gpu-a100

    # Check status
    cyberwave compute status

    # Follow logs
    cyberwave compute logs -f
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.prompt import Confirm

from cyberwave_cli.utils import colorize_log_line

console = Console()

from ..config import CONFIG_DIR as _CONFIG_DIR

CLOUD_NODE_IDENTITY_FILE = _CONFIG_DIR / "instance_identity.json"


def _runtime_envs_from_credentials() -> dict[str, str]:
    """Return stored runtime envs shared with cloud-node launches."""
    try:
        from ..credentials import load_credentials
    except Exception:
        return {}

    creds = load_credentials()
    if not creds or not hasattr(creds, "runtime_envs"):
        return {}

    runtime_envs = creds.runtime_envs()
    return runtime_envs if isinstance(runtime_envs, dict) else {}


def _find_cloud_node_binary() -> Optional[str]:
    """Locate the cyberwave-cloud-node binary."""
    from ..core import CLOUD_NODE_SPEC, _resolve_service_binary

    binary = _resolve_service_binary(CLOUD_NODE_SPEC)
    return binary if Path(binary).exists() else None


def _macos_launchagent_target() -> tuple[str, str]:
    """Return the launchctl domain/label target for the cloud node LaunchAgent."""
    from ..core import CLOUD_NODE_SPEC, _launchagent_target

    return _launchagent_target(CLOUD_NODE_SPEC)


def _ensure_macos_launchagent_installed() -> bool:
    """Ensure the macOS LaunchAgent plist exists before controlling it."""
    from ..core import CLOUD_NODE_SPEC, _launchagent_plist_path

    plist_path = _launchagent_plist_path(CLOUD_NODE_SPEC)
    if plist_path.exists():
        return True
    console.print(
        "[red]LaunchAgent plist not found.[/red]\n"
        "[dim]Run 'cyberwave compute install' first.[/dim]"
    )
    return False


def _macos_launchagent_plist_path() -> Path:
    """Return the cloud node LaunchAgent plist path for the current user."""
    from ..core import CLOUD_NODE_SPEC, _launchagent_plist_path

    return _launchagent_plist_path(CLOUD_NODE_SPEC)


def _macos_launchagent_log_path() -> Path:
    """Return the cloud node LaunchAgent log file path for the current user."""
    from ..core import CLOUD_NODE_SPEC, _launchagent_label

    label = _launchagent_label(CLOUD_NODE_SPEC)
    return Path.home() / "Library" / "Logs" / "Cyberwave" / f"{label}.log"


def _show_macos_launchagent_logs(*, follow: bool, lines: int) -> None:
    """Show logs from the macOS LaunchAgent log file."""
    log_path = _macos_launchagent_log_path()
    if not log_path.exists():
        console.print(
            "[yellow]Cloud node log file not found.[/yellow]\n"
            f"[dim]Expected: {log_path}[/dim]\n"
            "[dim]Run 'cyberwave compute install' and start the node to create it.[/dim]"
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
def compute():
    """Manage the cloud node service."""
    pass


@compute.command("install")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.option(
    "--channel",
    type=click.Choice(["stable", "dev", "staging"], case_sensitive=False),
    default="stable",
    show_default=True,
    help="Which cloud-node package channel to install",
)
@click.option(
    "--version",
    type=str,
    default=None,
    help="Exact version to install from the selected channel",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to cyberwave.yml to use when the service starts",
)
def install_cloud_node(
    yes: bool,
    channel: str,
    version: Optional[str],
    config_path: Optional[str],
) -> None:
    """Install cyberwave-cloud-node and register it as a boot service.

    Downloads the cyberwave-cloud-node package (via apt-get on Debian/Ubuntu,
    pip elsewhere) and creates the platform-appropriate boot service
    automatically.

    \b
    Examples:
        cyberwave compute install
        cyberwave compute install -y
        sudo cyberwave compute install --channel dev
        cyberwave compute install --config /path/to/cyberwave.yml
    """
    from ..core import CLOUD_NODE_SPEC, _has_systemd, setup_service, write_service_override

    # Write the override before setup so daemon-reload inside enable_and_start_service
    # picks it up together with the base unit on first start.
    if config_path and _has_systemd():
        if not write_service_override(CLOUD_NODE_SPEC, config_path=config_path):
            raise SystemExit(1)

    try:
        if not setup_service(
            CLOUD_NODE_SPEC,
            skip_confirm=yes,
            channel=channel.lower(),
            version=version,
            config_path=config_path,
        ):
            raise SystemExit(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Aborted.[/dim]")
        raise SystemExit(1)


@compute.command("uninstall")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def uninstall_cloud_node(yes: bool) -> None:
    """Stop and remove the cyberwave-cloud-node service.

    Removes the platform boot service and optionally uninstalls the package.
    Node credentials in ~/.cyberwave/ are preserved.

    \b
    Examples:
        sudo cyberwave compute uninstall
        sudo cyberwave compute uninstall -y
    """
    from ..core import CLOUD_NODE_SPEC, _is_macos, _resolve_installed_service_package_name, clear_service_override

    spec = CLOUD_NODE_SPEC

    if not yes:
        if not Confirm.ask(
            f"Remove {spec.unit_name} and disable boot service?", default=False
        ):
            console.print("[dim]Aborted.[/dim]")
            return

    if _is_macos():
        from ..config import clean_subprocess_env

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

        if plist_path.exists():
            try:
                plist_path.unlink()
                console.print(f"[green]Removed:[/green] {plist_path}")
            except PermissionError:
                console.print(
                    "[red]Permission denied removing LaunchAgent plist.[/red]\n"
                    "[dim]Run 'cyberwave compute uninstall' as your regular macOS user.[/dim]"
                )

        installed_pkg = _resolve_installed_service_package_name(spec)
        remove_pkg = yes or Confirm.ask(
            f"Also uninstall {installed_pkg} package?", default=False
        )
        if remove_pkg:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "uninstall", "-y", installed_pkg],
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

        console.print("[green]Cloud node service removed.[/green]")
        console.print("[dim]Node credentials in ~/.cyberwave/ were preserved.[/dim]")
        return

    # Stop and disable the service
    from ..core import _run

    try:
        _run(["systemctl", "stop", spec.unit_name], check=False)
        _run(["systemctl", "disable", spec.unit_name], check=False)
    except FileNotFoundError:
        console.print("[yellow]systemctl not found — skipping service cleanup.[/yellow]")

    # Remove any drop-in override
    clear_service_override(spec)

    # Remove the unit file
    if spec.unit_path.exists():
        try:
            spec.unit_path.unlink()
            console.print(f"[green]Removed:[/green] {spec.unit_path}")
            try:
                _run(["systemctl", "daemon-reload"], check=False)
            except FileNotFoundError:
                pass
        except PermissionError:
            console.print(
                "[red]Permission denied removing systemd unit file.[/red]\n"
                f"[dim]Re-run with sudo: {spec.sudo_command_hint}[/dim]"
            )

    # Remove the package: --yes answers this prompt automatically; otherwise ask.
    installed_pkg = _resolve_installed_service_package_name(spec)
    remove_pkg = yes or Confirm.ask(f"Also uninstall {installed_pkg} package?", default=False)
    if remove_pkg:
        if shutil.which("apt-get"):
            try:
                _run(["apt-get", "remove", "-y", installed_pkg], check=False)
            except FileNotFoundError:
                console.print("[yellow]apt-get not found — remove manually with pip.[/yellow]")
        else:
            try:
                _run([sys.executable, "-m", "pip", "uninstall", "-y", installed_pkg], check=False)
            except OSError:
                console.print("[yellow]pip uninstall failed — remove manually.[/yellow]")

    console.print("[green]Cloud node service removed.[/green]")
    console.print("[dim]Node credentials in ~/.cyberwave/ were preserved.[/dim]")


@compute.command("start")
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to cyberwave.yml (persisted in the service override)",
)
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (don't daemonize)")
def start_cloud_node(
    config_path: Optional[str],
    foreground: bool,
) -> None:
    """Start the cloud node service.

    Uses systemctl when the service unit is installed, launchctl on macOS when
    the LaunchAgent is installed, and otherwise spawns the binary directly in
    the background.

    When --config is provided under systemd, it is written to a drop-in
    override so it persists across reboots. On macOS it rewrites and reloads
    the LaunchAgent plist.

    \b
    Examples:
        cyberwave compute start
        cyberwave compute start --config /home/user/cyberwave.yml
        cyberwave compute start -f   # run in foreground
    """
    from ..core import (
        CLOUD_NODE_SPEC,
        _has_systemd,
        _is_macos,
        create_launchagent_service,
        load_launchagent_service,
        start_service,
        write_service_override,
    )

    spec = CLOUD_NODE_SPEC

    if _has_systemd() and spec.unit_path.exists():
        if config_path:
            if not write_service_override(spec, config_path=config_path):
                raise SystemExit(1)
        if not start_service(spec):
            raise SystemExit(1)
        return

    if foreground:
        binary = _find_cloud_node_binary()
        if not binary:
            console.print(
                "[red]cyberwave-cloud-node binary not found.[/red]\n"
                f"[dim]Run: {spec.sudo_command_hint}[/dim]"
            )
            return

        from ..config import clean_subprocess_env

        cmd: list[str] = [binary]
        if config_path:
            cmd += ["--config", str(config_path)]
        env = clean_subprocess_env()
        env.update(_runtime_envs_from_credentials())

        try:
            console.print("[green]Running cloud node in foreground (Ctrl+C to stop)...[/green]")
            subprocess.run(cmd, env=env, check=False)
        except FileNotFoundError:
            console.print(f"[red]Binary not found: {binary}[/red]")
        return

    if _is_macos():
        if not _ensure_macos_launchagent_installed():
            raise SystemExit(1)
        if config_path and not create_launchagent_service(spec, config_path=config_path):
            raise SystemExit(1)
        if config_path:
            if not load_launchagent_service(spec):
                raise SystemExit(1)
            return

        from ..config import clean_subprocess_env

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

    binary = _find_cloud_node_binary()
    if not binary:
        console.print(
            "[red]cyberwave-cloud-node binary not found.[/red]\n"
            f"[dim]Run: {spec.sudo_command_hint}[/dim]"
        )
        return

    from ..config import clean_subprocess_env

    cmd: list[str] = [binary]
    if config_path:
        cmd += ["--config", str(config_path)]

    env = clean_subprocess_env()
    env.update(_runtime_envs_from_credentials())

    try:
        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        console.print(f"[green]✓ Cloud node started (PID: {process.pid})[/green]")
    except FileNotFoundError:
        console.print(f"[red]Binary not found: {binary}[/red]")


@compute.command("stop")
def stop_cloud_node() -> None:
    """Stop the cloud node service."""
    from ..core import CLOUD_NODE_SPEC, _has_systemd, _is_macos, stop_service

    spec = CLOUD_NODE_SPEC

    if _has_systemd() and spec.unit_path.exists():
        stop_service(spec)
        return

    if _is_macos():
        if not _ensure_macos_launchagent_installed():
            raise SystemExit(1)

        from ..config import clean_subprocess_env

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
            console.print("[yellow]Cloud node LaunchAgent is not loaded.[/yellow]")
            return

        console.print(f"[red]launchctl bootout failed (exit {result.returncode}).[/red]")
        raise SystemExit(1)

    # Fallback: find and send SIGTERM to background process
    try:
        result = subprocess.run(
            ["pgrep", "-f", spec.process_match],
            capture_output=True,
            text=True,
        )
        own_pid = str(os.getpid())
        pids = [p for p in result.stdout.strip().split("\n") if p and p != own_pid]

        if not pids:
            console.print("[yellow]No running cloud node process found.[/yellow]")
            return

        for pid in pids:
            os.kill(int(pid), signal.SIGTERM)
            console.print(f"[green]✓ Stopped cloud node (PID: {pid})[/green]")

    except Exception as exc:
        console.print(f"[red]Error stopping cloud node: {exc}[/red]")


@compute.command("restart")
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to cyberwave.yml (persisted in the service override)",
)
def restart_cloud_node(config_path: Optional[str]) -> None:
    """Restart the cloud node service.

    If the node was installed as a systemd service, restarts it via systemctl.
    On macOS it reloads the installed LaunchAgent. Otherwise it falls back to
    stopping and re-starting the background process.

    When --config is provided under systemd, it is written to a drop-in
    override so it persists across reboots. On macOS it rewrites and reloads
    the LaunchAgent plist.

    \b
    Examples:
        sudo cyberwave compute restart
        sudo cyberwave compute restart --config /home/user/cyberwave.yml
    """
    from ..core import (
        CLOUD_NODE_SPEC,
        _has_systemd,
        _is_macos,
        create_launchagent_service,
        load_launchagent_service,
        restart_service,
        write_service_override,
    )

    spec = CLOUD_NODE_SPEC

    if _has_systemd() and spec.unit_path.exists():
        if config_path:
            if not write_service_override(spec, config_path=config_path):
                raise SystemExit(1)
        restart_service(spec)
        return

    if _is_macos():
        if not _ensure_macos_launchagent_installed():
            raise SystemExit(1)
        if config_path and not create_launchagent_service(spec, config_path=config_path):
            raise SystemExit(1)
        console.print("[cyan]Restarting cloud node LaunchAgent...[/cyan]")
        if not load_launchagent_service(spec):
            raise SystemExit(1)
        return

    console.print("[cyan]Restarting cloud node process...[/cyan]")

    try:
        result = subprocess.run(
            ["pgrep", "-f", spec.process_match],
            capture_output=True,
            text=True,
        )
        own_pid = str(os.getpid())
        pids = [p for p in result.stdout.strip().split("\n") if p and p != own_pid]
        for pid in pids:
            os.kill(int(pid), signal.SIGTERM)
            console.print(f"[dim]Stopped PID {pid}[/dim]")
    except Exception as exc:
        console.print(f"[yellow]Could not stop existing process: {exc}[/yellow]")

    binary = _find_cloud_node_binary()
    if not binary:
        console.print(
            "[red]cyberwave-cloud-node binary not found.[/red]\n"
            f"[dim]Run: {spec.sudo_command_hint}[/dim]"
        )
        return

    from ..config import clean_subprocess_env

    cmd: list[str] = [binary]
    if config_path:
        cmd += ["--config", str(config_path)]
    env = clean_subprocess_env()
    env.update(_runtime_envs_from_credentials())

    try:
        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        console.print(f"[green]✓ Cloud node restarted (PID: {process.pid})[/green]")
    except FileNotFoundError:
        console.print(f"[red]Binary not found: {binary}[/red]")


@compute.command("status")
def status_cloud_node() -> None:
    """Check cloud node status."""
    from ..core import CLOUD_NODE_SPEC, _is_macos

    spec = CLOUD_NODE_SPEC

    if _is_macos():
        from ..config import clean_subprocess_env

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
    # --- systemd service state ---
        try:
            result = subprocess.run(
                ["systemctl", "is-active", spec.unit_name],
                capture_output=True,
                text=True,
            )
            service_state = result.stdout.strip()
            if service_state == "active":
                console.print(f"[green]✓ Service {spec.unit_name}:[/green] active")
            elif service_state == "failed":
                console.print(f"[red]✗ Service {spec.unit_name}:[/red] failed")
            else:
                console.print(
                    f"[yellow]  Service {spec.unit_name}:[/yellow] "
                    f"{service_state or 'not installed'}"
                )
        except FileNotFoundError:
            console.print("[dim]  systemctl not found — skipping service check.[/dim]")

    # --- node identity ---
    if CLOUD_NODE_IDENTITY_FILE.exists():
        try:
            identity = json.loads(CLOUD_NODE_IDENTITY_FILE.read_text(encoding="utf-8"))
            node_uuid = identity.get("uuid", "unknown")
            node_slug = identity.get("slug", "unknown")
            console.print(f"  Node UUID: [cyan]{node_uuid}[/cyan]")
            console.print(f"  Node slug: [cyan]{node_slug}[/cyan]")
        except (json.JSONDecodeError, OSError) as exc:
            console.print(f"[yellow]  Could not read identity file: {exc}[/yellow]")
    else:
        console.print("[dim]  Node not yet registered (identity file missing).[/dim]")


@compute.command("logs")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.option("--lines", "-n", default=50, show_default=True, help="Number of lines to show")
def logs_cloud_node(follow: bool, lines: int) -> None:
    """Show cloud node logs."""
    from ..core import CLOUD_NODE_SPEC

    spec = CLOUD_NODE_SPEC
    if sys.platform == "darwin":
        try:
            _show_macos_launchagent_logs(follow=follow, lines=lines)
        except KeyboardInterrupt:
            pass
        return

    from ..config import clean_subprocess_env

    service_name = spec.unit_name.removesuffix(".service")
    cmd = [
        "journalctl",
        "-u", service_name,
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
