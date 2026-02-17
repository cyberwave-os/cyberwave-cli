"""Config-dir command for the Cyberwave CLI."""

import click

from ..config import CONFIG_DIR


@click.command("config-dir")
def config_dir() -> None:
    """Print the active configuration directory.

    Outputs the resolved path where the CLI stores credentials and
    configuration files (e.g. /etc/cyberwave or ~/.cyberwave).

    The output is plain text with no formatting, so it can be captured
    by shell scripts:

    \b
        CREDS=$(cyberwave config-dir)/credentials.json
    """
    click.echo(CONFIG_DIR)
