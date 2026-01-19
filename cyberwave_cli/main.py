"""Main entry point for the Cyberwave CLI."""

import click
from rich.console import Console

from . import __version__
from .commands import camera, configure, connect, edge, environment, login, logout, model, pair, plugin, scan, so101, twin, workflow

console = Console()

BANNER = """
[#00b3db]░▒█▀▀▄░▒█░░▒█░▒█▀▀▄░▒█▀▀▀░▒█▀▀▄░▒█░░▒█░█▀▀▄░▒█░░▒█░▒█▀▀▀
░▒█░░░░▒▀▄▄▄▀░▒█▀▀▄░▒█▀▀▀░▒█▄▄▀░▒█▒█▒█▒█▄▄█░░▒█▒█░░▒█▀▀▀
░▒█▄▄▀░░░▒█░░░▒█▄▄█░▒█▄▄▄░▒█░▒█░▒▀▄▀▄▀▒█░▒█░░░▀▄▀░░▒█▄▄▄[/#00b3db]
"""


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="cyberwave-cli")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Cyberwave CLI - Manage digital twins and edge ML.

    \b
    Quick Start:
      1. cyberwave login                             # Login to your account
      2. cyberwave pair <twin-uuid>                  # Pair device with existing twin
      3. cyberwave edge start                        # Start streaming

    \b
    Connect & Pair:
      pair        Pair this device with an existing twin
      connect     Smart connect - create new twin + configure edge in one command
      scan        Discover IP cameras on the network

    \b
    Resource Management:
      twin        List, show, delete digital twins
      environment List environments
      workflow    Create and manage automation workflows
      edge        Manage edge node (start, stop, pull config)

    \b
    Documentation: https://docs.cyberwave.com
    """
    if ctx.invoked_subcommand is None:
        console.print(BANNER)
        console.print("[dim]Type [bold]cyberwave --help[/bold] for available commands.[/dim]\n")


# Register commands
cli.add_command(camera)
cli.add_command(configure)
cli.add_command(connect)
cli.add_command(edge)
cli.add_command(environment)
cli.add_command(login)
cli.add_command(logout)
cli.add_command(model)
cli.add_command(pair)
cli.add_command(plugin)
cli.add_command(scan)
cli.add_command(so101)
cli.add_command(twin)
cli.add_command(workflow)


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
