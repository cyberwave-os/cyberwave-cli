"""Install and manage the cyberwave-edge-core systemd service.

This module provides the logic for:
  1. Installing the cyberwave-edge-core .deb package via apt-get
  2. Creating a systemd service unit so it starts on boot
  3. Enabling and starting the service
"""

import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

console = Console()

# ---- constants ---------------------------------------------------------------

PACKAGE_NAME = "cyberwave-edge-core"
BINARY_PATH = Path("/usr/bin/cyberwave-edge-core")
SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
SYSTEMD_UNIT_PATH = Path(f"/etc/systemd/system/{SYSTEMD_UNIT_NAME}")

# Buildkite Debian registry URL for the cyberwave-edge-core package
BUILDKITE_DEB_REPO_URL = (
    "https://packages.buildkite.com/cyberwave/cyberwave-edge-core/any/any"
)

SYSTEMD_UNIT_TEMPLATE = textwrap.dedent("""\
    [Unit]
    Description=Cyberwave Edge Core Orchestrator
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    ExecStart={binary_path}
    Restart=on-failure
    RestartSec=5
    Environment=CYBERWAVE_EDGE_CONFIG_DIR=/etc/cyberwave
    StandardOutput=journal
    StandardError=journal
    SyslogIdentifier=cyberwave-edge-core

    [Install]
    WantedBy=multi-user.target
""")


# ---- helpers -----------------------------------------------------------------


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _has_systemd() -> bool:
    return Path("/run/systemd/system").is_dir()


def _run(cmd: list[str], *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess and stream output to the console."""
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    return subprocess.run(cmd, check=check, **kwargs)


# ---- apt-get installation ----------------------------------------------------


def _apt_get_install() -> bool:
    """Install cyberwave-edge-core via apt-get.

    Adds the Buildkite package registry if not already configured,
    then installs (or upgrades) the package.

    Returns True on success.
    """
    sources_list = Path("/etc/apt/sources.list.d/cyberwave-edge-core.list")

    # Add the repository if missing
    if not sources_list.exists():
        console.print("[cyan]Adding Cyberwave package repository...[/cyan]")
        try:
            sources_list.write_text(f"deb {BUILDKITE_DEB_REPO_URL} /\n")
        except PermissionError:
            console.print(
                "[red]Permission denied writing apt sources.[/red]\n"
                "[dim]Re-run with sudo: sudo cyberwave edge install[/dim]"
            )
            return False

    # Update and install
    console.print(f"[cyan]Installing {PACKAGE_NAME} via apt-get...[/cyan]")
    try:
        _run(["apt-get", "update", "-qq"])
        _run(["apt-get", "install", "-y", "-qq", PACKAGE_NAME])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]apt-get failed (exit {exc.returncode}).[/red]")
        return False

    if BINARY_PATH.exists():
        console.print(f"[green]Installed:[/green] {BINARY_PATH}")
        return True

    console.print("[red]Binary not found after installation.[/red]")
    return False


def _pip_install() -> bool:
    """Fallback: install cyberwave-edge-core via pip.

    Used on non-Debian systems (macOS, other Linux flavors).
    Returns True on success.
    """
    console.print(f"[cyan]Installing {PACKAGE_NAME} via pip...[/cyan]")
    try:
        _run([sys.executable, "-m", "pip", "install", PACKAGE_NAME])
        return True
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]pip install failed (exit {exc.returncode}).[/red]")
        return False


def install_edge_core() -> bool:
    """Install the cyberwave-edge-core package.

    Prefers apt-get on Debian/Ubuntu, falls back to pip otherwise.
    Returns True on success.
    """
    if _is_linux() and shutil.which("apt-get"):
        return _apt_get_install()
    return _pip_install()


# ---- systemd service ---------------------------------------------------------


def create_systemd_service() -> bool:
    """Write the systemd unit file for cyberwave-edge-core.

    Returns True on success.
    """
    if not _has_systemd():
        console.print("[yellow]systemd not detected — skipping service creation.[/yellow]")
        return False

    binary = (
        str(BINARY_PATH)
        if BINARY_PATH.exists()
        else shutil.which(PACKAGE_NAME) or str(BINARY_PATH)
    )
    unit_contents = SYSTEMD_UNIT_TEMPLATE.format(binary_path=binary)

    try:
        SYSTEMD_UNIT_PATH.write_text(unit_contents)
    except PermissionError:
        console.print(
            "[red]Permission denied writing systemd unit.[/red]\n"
            "[dim]Re-run with sudo: sudo cyberwave edge install[/dim]"
        )
        return False

    console.print(f"[green]Created:[/green] {SYSTEMD_UNIT_PATH}")
    return True


def enable_and_start_service() -> bool:
    """Enable the service to start on boot, then start it now.

    Returns True on success.
    """
    if not SYSTEMD_UNIT_PATH.exists():
        console.print("[red]Service unit not found — run install first.[/red]")
        return False

    try:
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", SYSTEMD_UNIT_NAME])
        _run(["systemctl", "start", SYSTEMD_UNIT_NAME])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]systemctl command failed (exit {exc.returncode}).[/red]")
        return False

    console.print(f"[green]Service enabled and started:[/green] {SYSTEMD_UNIT_NAME}")
    return True


# ---- orchestrator ------------------------------------------------------------


def setup_edge_core(*, skip_confirm: bool = False) -> bool:
    """Full setup: install the package, create the service, enable on boot.

    Returns True if everything succeeded.
    """
    if not _is_linux():
        console.print("[yellow]Edge core service setup is only supported on Linux.[/yellow]")
        console.print(
            "[dim]You can still install the package with:"
            " pip install cyberwave-edge-core[/dim]"
        )
        return False

    if os.geteuid() != 0:
        console.print(
            "[red]Root privileges required.[/red]\n"
            "[dim]Re-run with sudo: sudo cyberwave edge install[/dim]"
        )
        return False

    if not skip_confirm:
        console.print(
            f"\nThis will:\n"
            f"  1. Install [bold]{PACKAGE_NAME}[/bold] via apt-get\n"
            f"  2. Create a systemd service ([bold]{SYSTEMD_UNIT_NAME}[/bold])\n"
            f"  3. Enable it to start on boot\n"
        )
        if not Confirm.ask("Continue?", default=True):
            console.print("[dim]Aborted.[/dim]")
            return False

    # Step 1 — install
    if not install_edge_core():
        return False

    # Step 2 — systemd unit
    if not create_systemd_service():
        return False

    # Step 3 — enable & start
    if not enable_and_start_service():
        return False

    console.print("\n[green]Edge core is installed and running.[/green]")
    console.print("[dim]Check status: systemctl status cyberwave-edge-core[/dim]")
    console.print("[dim]View logs:    journalctl -u cyberwave-edge-core -f[/dim]")
    return True
