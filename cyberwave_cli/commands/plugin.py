"""
CLI commands for managing edge node plugins.

Note: For most users, 'cyberwave model' commands are recommended.
      Plugin commands are for advanced local/offline configuration.

Example usage:
    # List available plugins
    cyberwave plugin list
    
    # Install/enable a plugin
    cyberwave plugin install yolo
    
    # Show plugin details
    cyberwave plugin info yolo
"""

import json
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ..constants import get_builtin_plugins

console = Console()


def get_plugins_dir() -> Path:
    """Get the plugins directory."""
    from ..config import CONFIG_DIR

    return CONFIG_DIR / "plugins"


def load_registry() -> dict:
    """Load the plugin registry."""
    registry_file = get_plugins_dir() / "registry.json"
    if registry_file.exists():
        with open(registry_file) as f:
            return json.load(f)
    return {"version": "1.0", "plugins": []}


def save_registry(registry: dict):
    """Save the plugin registry."""
    plugins_dir = get_plugins_dir()
    plugins_dir.mkdir(parents=True, exist_ok=True)
    with open(plugins_dir / "registry.json", "w") as f:
        json.dump(registry, f, indent=2)


@click.group()
def plugin():
    """Manage edge node plugins."""
    pass


@plugin.command("list")
@click.option("--installed", "-i", is_flag=True, help="Show only installed plugins")
def list_plugins(installed):
    """List available plugins."""
    table = Table(title="Available Plugins")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Version")
    table.add_column("Status", style="yellow")
    table.add_column("Capabilities")
    
    registry = load_registry()
    installed_ids = {p["plugin_id"] for p in registry.get("plugins", [])}
    builtin = get_builtin_plugins()
    
    for plugin_id, info in builtin.items():
        if installed and plugin_id not in installed_ids:
            continue
        
        status = "✓ Installed" if plugin_id in installed_ids else "Available"
        caps = ", ".join(info.get("capabilities", [])[:3])
        
        table.add_row(
            plugin_id,
            info["name"],
            info.get("version", "1.0.0"),
            status,
            caps,
        )
    
    console.print(table)
    console.print("\n[dim]Use 'cyberwave plugin install <id>' to install a plugin[/dim]")


@plugin.command("info")
@click.argument("plugin_id")
def plugin_info(plugin_id):
    """Show detailed information about a plugin."""
    builtin = get_builtin_plugins()
    if plugin_id not in builtin:
        console.print(f"[red]Unknown plugin: {plugin_id}[/red]")
        return
    
    info = builtin[plugin_id]
    
    console.print(f"\n[bold cyan]{info['name']}[/bold cyan] ({plugin_id})")
    console.print(f"[dim]{info.get('description', '')}[/dim]\n")
    
    console.print(f"Version: {info.get('version', '1.0.0')}")
    console.print(f"Runtime: {info.get('runtime', 'unknown')}")
    console.print(f"Capabilities: {', '.join(info.get('capabilities', []))}")
    
    if info.get("dependencies"):
        console.print(f"\n[yellow]Dependencies:[/yellow]")
        for dep in info["dependencies"]:
            console.print(f"  • {dep}")
    
    if info.get("models"):
        console.print(f"\n[green]Models:[/green]")
        for model in info["models"]:
            console.print(f"  • {model['id']}: {model.get('name', model['id'])}")


@plugin.command("install")
@click.argument("plugin_id")
@click.option("--no-deps", is_flag=True, help="Skip dependency installation")
def install_plugin(plugin_id, no_deps):
    """Install a plugin and its dependencies."""
    builtin = get_builtin_plugins()
    if plugin_id not in builtin:
        console.print(f"[red]Unknown plugin: {plugin_id}[/red]")
        return
    
    info = builtin[plugin_id]
    
    console.print(f"[cyan]Installing plugin: {info['name']}[/cyan]")
    
    # Install dependencies
    if not no_deps and info.get("dependencies"):
        console.print(f"[yellow]Installing dependencies...[/yellow]")
        try:
            from ..config import clean_subprocess_env

            subprocess.run(
                [sys.executable, "-m", "pip", "install"] + info["dependencies"],
                check=True,
                env=clean_subprocess_env(),
            )
            console.print("[green]✓ Dependencies installed[/green]")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to install dependencies: {e}[/red]")
            if not click.confirm("Continue anyway?"):
                return
    
    # Add to registry
    registry = load_registry()
    
    # Remove if already installed
    registry["plugins"] = [p for p in registry.get("plugins", []) if p["plugin_id"] != plugin_id]
    
    # Add new entry
    from datetime import datetime
    registry["plugins"].append({
        "plugin_id": plugin_id,
        "manifest": info,
        "installed_at": datetime.now().isoformat(),
        "enabled": True,
        "config": {},
    })
    
    save_registry(registry)
    console.print(f"[green]✓ Plugin '{plugin_id}' installed successfully[/green]")


@plugin.command("uninstall")
@click.argument("plugin_id")
def uninstall_plugin(plugin_id):
    """Uninstall a plugin."""
    registry = load_registry()
    original_count = len(registry.get("plugins", []))
    registry["plugins"] = [p for p in registry.get("plugins", []) if p["plugin_id"] != plugin_id]
    
    if len(registry["plugins"]) < original_count:
        save_registry(registry)
        console.print(f"[green]✓ Plugin '{plugin_id}' uninstalled[/green]")
    else:
        console.print(f"[yellow]Plugin '{plugin_id}' was not installed[/yellow]")


# Note: Model binding is handled by:
# - 'cyberwave model bind' for CLI-based local config
# - UI Workflows with camera_frame trigger for backend-driven config (recommended)
