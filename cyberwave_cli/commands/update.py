"""``cyberwave update``: update the CLI itself."""

from __future__ import annotations

import click


@click.command("update")
@click.option(
    "--version",
    type=str,
    default=None,
    help=(
        "Exact CLI version to install from the installed channel, older ones "
        "included (required for PR builds, e.g. 1.2.3~pr4337.gabc123def456)"
    ),
)
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Show the installed and available versions without changing anything",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Update even when the package is held with 'apt-mark hold'",
)
def update(version, check, force):
    """Update the Cyberwave CLI to the latest version of its channel.

    The channel is the one of the installed CLI (stable, dev, staging or pr);
    update never switches channel. The SDK is bundled in Debian packages, so it
    updates with the CLI; in a pip or pipx install it is upgraded together with
    the CLI when the CLI is updated (stable channel). Edge-core is not touched:
    use 'cyberwave edge update'.

    \b
    Examples:
        cyberwave update --check
        sudo cyberwave update        # Debian / Ubuntu package
        cyberwave update             # pip, pipx or the installer's venv
        cyberwave update --version 0.12.7
    """
    from ..core import update_cli

    if not update_cli(version=version, check=check, force=force):
        raise SystemExit(1)
