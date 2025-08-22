import typer
from rich import print
from .plugins import loader
from .setup_utils import setup_cyberwave_cli, verify_installation

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


@app.command()
def setup() -> None:
    """Setup Cyberwave CLI PATH configuration automatically."""
    setup_cyberwave_cli()


@app.command()
def doctor() -> None:
    """Verify Cyberwave CLI installation and configuration."""
    print("🔍 Running Cyberwave CLI diagnostics...")
    print("=" * 40)
    
    # Check if cyberwave is in PATH
    import shutil
    if shutil.which("cyberwave"):
        print("✅ Cyberwave CLI found in PATH")
    else:
        print("❌ Cyberwave CLI not found in PATH")
        print("💡 Run 'python -m cyberwave_cli.setup_utils' to fix this")
        return
    
    # Verify installation
    if verify_installation():
        print("✅ All checks passed!")
    else:
        print("❌ Some checks failed. Please check the output above.")


if __name__ == "__main__":
    main() 