import typer
import importlib.metadata as metadata
from rich import print


from importlib.metadata import EntryPoint
from typing import Iterable, List


def _get_entry_points(group: str) -> Iterable[EntryPoint]:
    """Return entry points for the given group across Python versions."""
    try:  # Python >=3.10
        return metadata.entry_points(group=group)
    except TypeError:  # Python <3.10
        return metadata.entry_points().get(group, [])


def discover_plugins() -> List[str]:
    """Return a list of available plugin names."""
    return [ep.name for ep in _get_entry_points("cyberwave.cli.plugins")]

def register_all(root_app: typer.Typer) -> None:
    """Discovers and registers all plugins declared via entry points and local plugins."""
    print("[dim]Discovering CLI plugins...[/dim]")
    
    # Register local plugins first
    _register_local_plugins(root_app)
    
    # Then register external plugins via entry points
    discovered_plugins = list(_get_entry_points("cyberwave.cli.plugins"))

    if not discovered_plugins:
        print("[dim]No external plugins found via entry points.[/dim]")
    else:
        for ep in discovered_plugins:
            print(f"  - Loading external plugin: [bold cyan]{ep.name}[/bold cyan]")
            try:
                sub_app = ep.load()
                if isinstance(sub_app, typer.Typer):
                    root_app.add_typer(sub_app, name=ep.name)
                    print(f"    [green]✓[/green] Registered typer app for '[bold]{ep.name}[/bold]'")
                else:
                    # Handle single commands if needed, though spec implies Typer apps
                     print(f"    [yellow]⚠[/yellow] Plugin '[bold]{ep.name}[/bold]' did not load a Typer app. Skipping.")
            except Exception as e:
                print(f"    [red]✗[/red] Failed to load plugin '[bold]{ep.name}[/bold]': {e}")

def _register_local_plugins(root_app: typer.Typer) -> None:
    """Register local plugins that are part of the CLI package."""
    local_plugins = [
        # Existing plugins
        ("auth", "cyberwave_cli.plugins.auth.app", "auth_app"),
        ("assets", "cyberwave_cli.plugins.assets.app", "assets_app"),
        ("devices", "cyberwave_cli.plugins.devices.app", "devices_app"),
        ("environments", "cyberwave_cli.plugins.environments.app", "environments_app"),
        ("projects", "cyberwave_cli.plugins.projects.app", "projects_app"),
        ("sensors", "cyberwave_cli.plugins.sensors.app", "sensors_app"),
        ("telemetry", "cyberwave_cli.plugins.telemetry.app", "telemetry_app"),
        ("twins", "cyberwave_cli.plugins.twins.app", "twins_app"),
        ("sim", "cyberwave_cli.plugins.sim.app", "sim_app"),
        
        # Unified edge plugin (consolidates v1 and v2 functionality)
        ("edge", "cyberwave_cli.plugins.edge.app", "app"),
    ]
    
    for plugin_name, module_path, app_attr in local_plugins:
        try:
            import importlib
            module = importlib.import_module(module_path)
            sub_app = getattr(module, app_attr)
            
            if isinstance(sub_app, typer.Typer):
                root_app.add_typer(sub_app, name=plugin_name)
                print(f"  - [green]✓[/green] Registered local plugin: [bold cyan]{plugin_name}[/bold cyan]")
            else:
                print(f"  - [yellow]⚠[/yellow] Local plugin '[bold]{plugin_name}[/bold]' is not a Typer app")
                
        except ImportError as e:
            print(f"  - [yellow]⚠[/yellow] Could not import local plugin '[bold]{plugin_name}[/bold]': {e}")
        except AttributeError as e:
            print(f"  - [yellow]⚠[/yellow] Local plugin '[bold]{plugin_name}[/bold]' missing app attribute: {e}")
        except Exception as e:
            print(f"  - [red]✗[/red] Failed to load local plugin '[bold]{plugin_name}[/bold]': {e}") 

