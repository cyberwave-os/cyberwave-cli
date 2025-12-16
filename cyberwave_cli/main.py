"""Main entry point for the Cyberwave CLI."""

import click
from rich.console import Console

from . import __version__
from .commands import camera, login, logout, so101

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="cyberwave-cli")
def cli() -> None:
    """Cyberwave CLI - Authenticate and bootstrap robotics projects.

    \b
    Commands:
      login    Authenticate with Cyberwave via email/password
      logout   Remove stored credentials
      so101    Clone and set up the SO-101 robot arm project
      camera   Set up camera edge software for streaming

    \b
    Examples:
      cyberwave-cli login
      cyberwave-cli so101
      cyberwave-cli so101 ~/projects/my-robot
      cyberwave-cli camera
      cyberwave-cli camera -n "My Camera"

    \b
    Documentation: https://docs.cyberwave.com
    Support: https://discord.gg/dfGhNrawyF
    """
    pass


# Register commands
cli.add_command(camera)
cli.add_command(login)
cli.add_command(logout)
cli.add_command(so101)


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
