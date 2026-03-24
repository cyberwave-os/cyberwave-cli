"""Shell completion commands for the Cyberwave CLI."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import click
from click import shell_completion
from rich.console import Console

console = Console()

_SUPPORTED_SHELLS = ("bash", "zsh")
_BLOCK_START = "# >>> cyberwave completion >>>"
_BLOCK_END = "# <<< cyberwave completion <<<"
_DEFAULT_PROG_NAME = "cyberwave"


def _complete_var(prog_name: str) -> str:
    return f"_{prog_name.replace('-', '_').upper()}_COMPLETE"


def _detect_shell() -> str | None:
    shell_path = os.environ.get("SHELL", "").strip()
    if not shell_path:
        return None

    shell_name = Path(shell_path).name.lower()
    if shell_name in _SUPPORTED_SHELLS:
        return shell_name

    return None


def _resolve_shell(shell: str | None) -> str | None:
    if shell:
        return shell
    return _detect_shell()


def _default_rc_file(shell: Literal["bash", "zsh"]) -> Path:
    home = Path.home()
    if shell == "bash":
        return home / ".bashrc"
    return home / ".zshrc"


def _generate_completion_script(command: click.Command, shell: str, prog_name: str) -> str:
    shell_class = shell_completion.get_completion_class(shell)
    if shell_class is None:
        raise click.ClickException(
            f"Shell '{shell}' is not supported. Supported shells: {', '.join(_SUPPORTED_SHELLS)}."
        )

    completer = shell_class(
        command,
        ctx_args={},
        prog_name=prog_name,
        complete_var=_complete_var(prog_name),
    )
    return completer.source()


def _render_install_block(shell: str, prog_name: str) -> str:
    return "\n".join(
        [
            _BLOCK_START,
            f"if command -v {prog_name} >/dev/null 2>&1; then",
            f'  eval "$({_complete_var(prog_name)}={shell}_source {prog_name})"',
            "fi",
            _BLOCK_END,
            "",
        ]
    )


def _upsert_completion_block(existing_text: str, desired_block: str) -> tuple[str, str]:
    """Insert or update completion block in an rc file.

    Returns a tuple: (new_text, status) where status is one of:
    - "added": block was appended
    - "updated": existing block was replaced
    - "unchanged": existing block already matches
    """
    pattern = re.compile(
        rf"{re.escape(_BLOCK_START)}.*?{re.escape(_BLOCK_END)}\n?",
        re.DOTALL,
    )
    match = pattern.search(existing_text)
    normalized_desired = desired_block.rstrip("\n") + "\n"

    if match:
        normalized_existing = match.group(0).rstrip("\n") + "\n"
        if normalized_existing == normalized_desired:
            return existing_text, "unchanged"

        new_text = pattern.sub(normalized_desired, existing_text, count=1)
        return new_text, "updated"

    separator = "" if not existing_text or existing_text.endswith("\n") else "\n"
    return f"{existing_text}{separator}{normalized_desired}", "added"


def _write_completion_block(rc_file: Path, block: str) -> str:
    existing_text = rc_file.read_text(encoding="utf-8") if rc_file.exists() else ""
    new_text, status = _upsert_completion_block(existing_text, block)

    if status == "unchanged":
        return status

    rc_file.parent.mkdir(parents=True, exist_ok=True)
    rc_file.write_text(new_text, encoding="utf-8")
    return status


@click.group()
def completion() -> None:
    """Generate and install shell completions (bash/zsh)."""


@completion.command("generate")
@click.option(
    "--shell",
    type=click.Choice(_SUPPORTED_SHELLS),
    required=True,
    help="Shell to generate completion script for.",
)
@click.option(
    "--prog-name",
    default=_DEFAULT_PROG_NAME,
    show_default=True,
    help="CLI executable name used in the completion script.",
)
@click.pass_context
def generate_completion(ctx: click.Context, shell: str, prog_name: str) -> None:
    """Print shell completion script to stdout.

    Examples:
      cyberwave completion generate --shell bash
      cyberwave completion generate --shell zsh > ~/.cyberwave-completion.zsh
    """
    root_command = ctx.find_root().command
    script = _generate_completion_script(root_command, shell=shell, prog_name=prog_name)
    click.echo(script)


@completion.command("install")
@click.option(
    "--shell",
    "shell_name",
    type=click.Choice(_SUPPORTED_SHELLS),
    help="Shell to configure. Auto-detected from $SHELL when omitted.",
)
@click.option(
    "--rc-file",
    type=click.Path(path_type=Path, dir_okay=False, writable=True),
    help="Optional shell rc file path override (for example ~/.bashrc).",
)
@click.option(
    "--prog-name",
    default=_DEFAULT_PROG_NAME,
    show_default=True,
    help="CLI executable name to use in the completion snippet.",
)
def install_completion(shell_name: str | None, rc_file: Path | None, prog_name: str) -> None:
    """Install persistent shell completion into your shell rc file.

    One-step setup:
      cyberwave completion install
    """
    resolved_shell = _resolve_shell(shell_name)
    if resolved_shell is None:
        console.print("[red]✗[/red] Could not detect your shell from $SHELL.")
        console.print("[dim]Run one of:[/dim]")
        console.print("  [cyan]cyberwave completion install --shell bash[/cyan]")
        console.print("  [cyan]cyberwave completion install --shell zsh[/cyan]")
        raise click.Abort()

    target_rc_file = rc_file or _default_rc_file(resolved_shell)
    install_block = _render_install_block(resolved_shell, prog_name)

    try:
        status = _write_completion_block(target_rc_file, install_block)
    except OSError as exc:
        console.print(
            f"[red]✗[/red] Unable to write completion config to [bold]{target_rc_file}[/bold]: {exc}"
        )
        console.print("[dim]Manual setup:[/dim]")
        console.print(f"  [cyan]{install_block.rstrip()}[/cyan]")
        raise click.Abort()

    if status == "added":
        console.print(
            f"[green]✓[/green] Installed {resolved_shell} completion in [bold]{target_rc_file}[/bold]"
        )
    elif status == "updated":
        console.print(
            f"[green]✓[/green] Updated {resolved_shell} completion in [bold]{target_rc_file}[/bold]"
        )
    else:
        console.print(
            f"[green]✓[/green] Completion already configured in [bold]{target_rc_file}[/bold]"
        )

    console.print(
        f"[dim]Apply now:[/dim] [cyan]source {target_rc_file}[/cyan] "
        f"[dim]or restart your shell.[/dim]"
    )
