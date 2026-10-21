"""Docker credential helper: resolve API keys at use time, never persist them."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from .credentials import get_token
from .io_utils import atomic_write_json

REGISTRIES = {
    "registry.cyberwave.com",
    "registry-dev.cyberwave.com",
    "registry-staging.cyberwave.com",
}


def registry_host(server: str) -> str:
    host = server.removeprefix("https://").removesuffix("/")
    if host not in REGISTRIES:
        raise ValueError("Unsupported Cyberwave registry")
    return host


def main():
    try:
        command = sys.argv[1] if len(sys.argv) == 2 else ""
        if command == "get":
            registry_host(sys.stdin.read().strip())
            token = os.environ.get("CYBERWAVE_API_KEY") or get_token()
            if not token:
                # Docker and docker-py fall back to an anonymous pull (so public
                # images keep working) only on this exact stdout message.
                print("credentials not found in native keychain")
                print("Set CYBERWAVE_API_KEY or run cyberwave login", file=sys.stderr)
                raise SystemExit(1)
            print(json.dumps({"Username": "cyberwave", "Secret": token}))
        elif command == "list":
            print("{}")
        elif command in {"store", "erase"}:
            # Docker login/logout must not persist or destroy the CLI/API key.
            sys.stdin.read()
        else:
            raise ValueError("Expected get, store, erase, or list")
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


@click.group()
def auth():
    """Configure authentication for external tools."""


@auth.command("configure-docker")
@click.option("--registry", type=click.Choice(sorted(REGISTRIES)), default="registry.cyberwave.com")
def configure_docker(registry):
    """Use the Cyberwave API key for this Docker registry."""
    path = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))) / "config.json"
    try:
        config = json.loads(path.read_text()) if path.exists() else {}
        helpers = config.setdefault("credHelpers", {})
        if not isinstance(helpers, dict):
            raise ValueError("credHelpers must be an object")
        helpers[registry] = "cyberwave"
        atomic_write_json(path, config)
    except (ValueError, OSError, AttributeError) as exc:
        raise click.ClickException(f"Cannot update Docker config: {exc}") from exc
    click.echo(f"Configured API-key authentication for {registry}.")
