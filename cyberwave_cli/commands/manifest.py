"""``cyberwave manifest`` command group — validate cyberwave.yml manifests."""

from __future__ import annotations

from pathlib import Path

import click


@click.group()
def manifest() -> None:
    """Manage and validate cyberwave.yml manifests."""


@manifest.command("validate")
@click.argument("path", default="cyberwave.yml", type=click.Path())
@click.option(
    "--lenient",
    is_flag=True,
    help="Treat unknown fields as warnings instead of errors (useful during migration).",
)
def validate(path: str, lenient: bool) -> None:
    """Validate a cyberwave.yml manifest.

    PATH defaults to cyberwave.yml in the current directory.

    Exit code 0 if valid, 1 if invalid.
    """
    from cyberwave.manifest.schema import detect_dispatch_mode
    from cyberwave.manifest.validator import validate_manifest

    result = validate_manifest(Path(path), lenient=lenient)

    if result.warnings:
        for w in result.warnings:
            click.secho(f"\u26a0  {w}", fg="yellow")

    if result.valid:
        click.secho(f"\u2713  {path} is valid", fg="green")
        m = result.manifest
        assert m is not None
        if m.inference:
            mode = detect_dispatch_mode(m.inference)
            click.echo(f"   inference: {m.inference!r}  [{mode} mode]")
        if m.training:
            mode = detect_dispatch_mode(m.training)
            click.echo(f"   training:  {m.training!r}  [{mode} mode]")
        if m.workers:
            click.echo(f"   workers:   {m.workers}")
        if m.models:
            click.echo(f"   models:    {m.models}")
    else:
        click.secho(f"\u2717  {path} failed validation", fg="red", err=True)
        click.echo(result.format_errors(), err=True)
        raise SystemExit(1)
