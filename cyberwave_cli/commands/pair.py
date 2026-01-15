"""
Pair command - user-friendly alias for device pairing.

The `pair` command is a simplified interface for pairing an edge device
to an existing twin. It's equivalent to `cyberwave edge pull --twin-uuid <uuid>`.

Examples:
    cyberwave pair abc-123-def-456
    cyberwave pair abc-123-def-456 --target-dir ./my-edge
"""

import click
from rich.console import Console

from .edge import pull_config

console = Console()


@click.command()
@click.argument("twin_uuid")
@click.option(
    "--target-dir",
    "-d",
    default=".",
    help="Directory to save edge configuration (default: current directory)",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def pair(twin_uuid: str, target_dir: str, yes: bool):
    """
    Pair this device with a digital twin.
    
    Downloads the edge configuration for TWIN_UUID and saves it locally.
    After pairing, run `cyberwave edge start` to begin streaming.
    
    \b
    Examples:
        cyberwave pair abc-123-def-456
        cyberwave pair abc-123-def-456 --target-dir ./my-edge
    
    \b
    Quick Start:
        1. cyberwave login           # Login to your account
        2. cyberwave pair <uuid>     # Pair device with twin
        3. cyberwave edge start      # Start streaming
    """
    # Delegate to edge pull command
    pull_config.callback(
        twin_uuid=twin_uuid,
        environment_uuid=None,
        target_dir=target_dir,
        yes=yes,
    )
