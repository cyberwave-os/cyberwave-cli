import typer
from rich import print

app = typer.Typer(help="Control local simulations (Isaac Sim, Unity).")

@app.command()
def launch(simulator: str = typer.Option("isaac", help="Simulator type ('isaac' or 'unity')")):
    """Launch a local simulation environment."""
    print(f":rocket: Launching local [yellow]{simulator}[/yellow] simulation... (not implemented)")

@app.command()
def status():
    """Check the status of running local simulations."""
    print(":magnifying_glass_right: Checking simulation status... (not implemented)")

if __name__ == "__main__":
    app() 