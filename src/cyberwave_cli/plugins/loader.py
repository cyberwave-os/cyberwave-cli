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
    """Discovers and registers all plugins declared via entry points."""
    print("[dim]Discovering CLI plugins...[/dim]")
    discovered_plugins = list(_get_entry_points("cyberwave.cli.plugins"))

    if not discovered_plugins:
        print("[dim]No external plugins found.[/dim]")
        return

    for ep in discovered_plugins:
        print(f"  - Loading plugin: [bold cyan]{ep.name}[/bold cyan]")
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

