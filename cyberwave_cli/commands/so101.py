"""SO-101 bootstrap command for the Cyberwave CLI."""

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

from ..config import SO101_DEFAULT_DIR, SO101_REPO_URL
from ..credentials import load_credentials

console = Console()


def run_command(cmd: list[str], cwd: Path | None = None) -> bool:
    """Run a shell command and return success status."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_git_installed() -> bool:
    """Check if git is installed."""
    return run_command(["git", "--version"])


@click.command()
@click.argument("path", default=SO101_DEFAULT_DIR, required=False)
def so101(path: str) -> None:
    """Bootstrap a new SO-101 robot arm project.

    Clones the SO-101 starter template and runs setup scripts to get you started.

    PATH is the target directory for the project (default: ./so101-project)

    \b
    Examples:
        cyberwave-cli so101
        cyberwave-cli so101 ~/projects/my-robot
    """
    target_path = Path(path).expanduser().resolve()

    # Check authentication
    creds = load_credentials()
    if not creds or not creds.token:
        console.print(
            "\n[yellow]⚠[/yellow] Not logged in. Run [bold]cyberwave-cli login[/bold] first "
            "to enable full functionality."
        )
        console.print()

    # Check if git is installed
    if not check_git_installed():
        console.print("[red]✗[/red] Git is not installed. Please install git first.")
        raise click.Abort()

    # Check if target directory already exists
    if target_path.exists():
        console.print(f"[red]✗[/red] Directory already exists: [bold]{target_path}[/bold]")
        if not click.confirm("Do you want to overwrite it?"):
            raise click.Abort()
        # Remove existing directory
        import shutil

        shutil.rmtree(target_path)

    console.print(f"\n[bold]Cloning SO-101 starter template...[/bold]")
    console.print(f"[dim]→ {SO101_REPO_URL}[/dim]")

    # Clone the repository
    result = subprocess.run(
        ["git", "clone", SO101_REPO_URL, str(target_path)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        console.print(f"[red]✗[/red] Failed to clone repository")
        console.print(f"[dim]{result.stderr}[/dim]")
        raise click.Abort()

    console.print(f"[green]✓[/green] Cloned to [bold]{target_path}[/bold]")

    # Check for setup script
    setup_script = target_path / "setup.sh"
    setup_py = target_path / "setup.py"

    if setup_script.exists():
        console.print("\n[bold]Running setup script...[/bold]")
        result = subprocess.run(
            ["bash", str(setup_script)],
            cwd=target_path,
        )
        if result.returncode == 0:
            console.print("[green]✓[/green] Setup completed")
        else:
            console.print("[yellow]⚠[/yellow] Setup script returned non-zero exit code")

    elif setup_py.exists():
        console.print("\n[bold]Installing Python dependencies...[/bold]")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            cwd=target_path,
        )
        if result.returncode == 0:
            console.print("[green]✓[/green] Dependencies installed")
        else:
            console.print("[yellow]⚠[/yellow] Failed to install dependencies")

    # Print next steps
    console.print("\n[bold green]✓ Project created successfully![/bold green]")
    console.print("\n[bold]Next steps:[/bold]")
    console.print(f"  1. [dim]cd {target_path}[/dim]")
    console.print("  2. [dim]Read the README.md for setup instructions[/dim]")
    console.print("  3. [dim]Connect your SO-101 robot arm[/dim]")
    console.print()
    console.print("[dim]Documentation: https://docs.cyberwave.com/so101[/dim]")
