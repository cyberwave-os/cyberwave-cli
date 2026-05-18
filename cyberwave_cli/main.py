"""Main entry point for the Cyberwave CLI."""

from __future__ import annotations

import importlib
from typing import Any

import click
from rich.console import Console

from . import __version__

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

# Maps CLI command names to their (module_path, attribute_name) for lazy loading.
# Each command module is only imported when the user actually invokes that command.
_LAZY_COMMANDS: dict[str, tuple[str, str]] = {
    "camera": (".commands.camera", "camera"),
    "compute": (".commands.compute", "compute"),
    "completion": (".commands.completion", "completion"),
    "config-dir": (".commands.config_dir", "config_dir"),
    "configure": (".commands.configure", "configure"),
    "edge": (".commands.edge", "edge"),
    "environment": (".commands.environment", "environment"),
    "login": (".commands.login", "login"),
    "logout": (".commands.logout", "logout"),
    "manifest": (".commands.manifest", "manifest"),
    "model": (".commands.model", "model"),
    "pair": (".commands.pair", "pair"),
    "plugin": (".commands.plugin", "plugin"),
    "scan": (".commands.scan", "scan"),
    "so101": (".commands.so101", "so101"),
    "twin": (".commands.twin", "twin"),
    "workflow": (".commands.workflow", "workflow"),
    "worker": (".commands.worker", "worker"),
}


class _LazyGroup(click.Group):
    """Click group that defers command module imports until invocation.

    Only the command the user is actually running gets imported,
    avoiding the cost of loading all 17+ command modules on every
    CLI invocation.  ``--help`` still lists all commands by name
    because the names are known statically from ``_LAZY_COMMANDS``.
    """

    def __init__(
        self,
        *args: Any,
        lazy_commands: dict[str, tuple[str, str]] | None = None,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._lazy_commands = lazy_commands or {}

    def list_commands(self, ctx: click.Context) -> list[str]:
        base = super().list_commands(ctx)
        lazy = sorted(self._lazy_commands.keys())
        return sorted(set(base + lazy))

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.BaseCommand | None:
        rv = super().get_command(ctx, cmd_name)
        if rv is not None:
            return rv
        if cmd_name not in self._lazy_commands:
            return None
        module_path, attr_name = self._lazy_commands[cmd_name]
        mod = importlib.import_module(module_path, package=__package__)
        cmd = getattr(mod, attr_name)
        self.add_command(cmd, cmd_name)
        return cmd


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


@click.group(cls=_LazyGroup, lazy_commands=_LAZY_COMMANDS, invoke_without_command=True)
@click.version_option(version=__version__, prog_name="cyberwave")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Cyberwave CLI - Manage digital twins and edge ML.

    \b
    Quick Start:
      1. cyberwave login                             # Login to your account
      2. cyberwave twin create <asset> --pair        # Create twin and pair device
      3. cyberwave pair                              # Pair this device as an edge node
                                                     # (alias for `cyberwave edge install`)
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


@cli.command(name="__selfcheck_sdk", hidden=True)
def selfcheck_sdk() -> None:
    """Hidden packaged-runtime check for generated SDK imports."""
    raise click.exceptions.Exit(run_sdk_selfcheck())


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
