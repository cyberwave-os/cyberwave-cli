"""Main entry point for the Cyberwave CLI."""

from __future__ import annotations

import click
from rich.console import Console

from . import __version__
from .commands import (
    camera,
    compute,
    completion,
    config_dir,
    configure,
    edge,
    environment,
    login,
    logout,
    manifest,
    model,
    plugin,
    scan,
    so101,
    twin,
    workflow,
    worker,
)

console = Console()


BANNER = """
[#00b3db]
 ██████╗██╗   ██╗██████╗ ███████╗██████╗ ██╗    ██╗ █████╗ ██╗   ██╗███████╗
██╔════╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██║    ██║██╔══██╗██║   ██║██╔════╝
██║      ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║ █╗ ██║███████║██║   ██║█████╗
██║       ╚██╔╝  ██╔══██╗██╔══╝  ██╔══██╗██║███╗██║██╔══██║╚██╗ ██╔╝██╔══╝
╚██████╗   ██║   ██████╔╝███████╗██║  ██║╚███╔███╔╝██║  ██║ ╚████╔╝ ███████╗
 ╚═════╝   ╚═╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝ ╚═╝  ╚═╝  ╚═══╝  ╚══════╝
[/#00b3db]
"""


def _load_sdk_default_api():
    """Import and return the generated SDK DefaultApi type."""
    from cyberwave.rest import DefaultApi

    return DefaultApi


def run_sdk_selfcheck() -> int:
    """Verify the packaged runtime contains the generated REST SDK."""
    try:
        default_api = _load_sdk_default_api()
    except Exception as exc:
        click.echo(f"sdk-rest-missing: {exc}", err=True)
        return 1

    if default_api is None:
        click.echo("sdk-rest-missing: DefaultApi unavailable", err=True)
        return 1

    click.echo("sdk-rest-ok")
    return 0


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="cyberwave")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Cyberwave CLI - Manage digital twins and edge ML.

    \b
    Quick Start:
      1. cyberwave login                             # Login to your account
      2. cyberwave twin create <asset> --pair        # Create twin and pair device
      3. cyberwave edge install                      # Install edge node on device
      4. cyberwave edge driver list                  # List available drivers

    \b
    Twin Management:
      twin create     Create a new digital twin from an asset
      twin pair       Pair this device with an existing twin
      twin list       List all digital twins
      twin show       Show details of a specific twin
      twin delete     Delete a digital twin

    \b
    Edge & Cloud:
      edge        Manage edge node (start, stop, pull config)
      compute     Manage cloud node (install, start, stop, status)
      scan        Discover IP cameras on the network
      completion  Generate/install shell completion

    \b
    Resource Management:
      environment List environments
      workflow    Create and manage automation workflows

    \b
    Worker Management:
      worker      Manage local worker files for edge inference

    \b
    Documentation: https://docs.cyberwave.com
    """
    if ctx.invoked_subcommand is None:
        console.print(BANNER)
        console.print("[dim]Type [bold]cyberwave --help[/bold] for available commands.[/dim]\n")


# Register commands
cli.add_command(camera)
cli.add_command(compute)
cli.add_command(completion)
cli.add_command(config_dir)
cli.add_command(configure)
cli.add_command(edge)
cli.add_command(environment)
cli.add_command(login)
cli.add_command(logout)
cli.add_command(manifest)
cli.add_command(model)
cli.add_command(plugin)
cli.add_command(scan)
cli.add_command(so101)
cli.add_command(twin)
cli.add_command(workflow)
cli.add_command(worker)


@cli.command(name="__selfcheck_sdk", hidden=True)
def selfcheck_sdk() -> None:
    """Hidden packaged-runtime check for generated SDK imports."""
    raise click.exceptions.Exit(run_sdk_selfcheck())


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
