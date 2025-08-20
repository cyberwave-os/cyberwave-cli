import typer
from rich import print
from .plugins import loader

app = typer.Typer(rich_markup_mode="markdown")

def main():
    # Discover and register plugins
    loader.register_all(app)
    # Run the app
    app()

@app.callback()
def callback():
    """
    CyberWave Command-Line Interface
    """
    pass

@app.command()
def version() -> None:
    """Show the installed CLI version."""
    import importlib.metadata

    cli_version = importlib.metadata.version("cyberwave-cli")
    print(f"CyberWave CLI version: [bold green]{cli_version}[/bold green]")


@app.command()
def plugins_cmd() -> None:
    """List available CLI plugins."""
    plugin_names = loader.discover_plugins()
    if not plugin_names:
        print("No plugins found")
    else:
        print("Loaded plugins:")
        for name in plugin_names:
            print(f"- {name}")


if __name__ == "__main__":
    main() 