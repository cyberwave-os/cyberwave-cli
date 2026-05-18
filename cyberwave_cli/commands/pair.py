"""Top-level ``cyberwave pair`` command.

Thin alias over ``cyberwave edge install``. It reuses the same callback and
options as the edge installer so the behavior — and any future changes to it —
stays in lock-step between the two commands. The alias exists because
``cyberwave pair`` reads as the natural verb for first-time device setup
(see ``docs-mintlify/get-started/index.mdx``).
"""

from __future__ import annotations

import click

from .edge import install_edge

_HELP = """Pair this device with Cyberwave as an edge node.

Alias for `cyberwave edge install`. Runs the full edge-core bootstrap flow:
workspace, environment, and twin selection, package install, and boot
service registration.

\b
Examples:
    sudo cyberwave pair
    sudo cyberwave pair -y
    sudo cyberwave pair --channel dev
    sudo cyberwave pair --reconfigure-camera
"""


pair = click.Command(
    name="pair",
    callback=install_edge.callback,
    params=list(install_edge.params),
    help=_HELP,
    short_help="Pair this device as a Cyberwave edge node (alias for `edge install`).",
)
