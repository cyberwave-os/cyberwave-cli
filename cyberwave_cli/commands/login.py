"""Login command for the Cyberwave CLI."""

import sys

import click
from rich.console import Console
from rich.prompt import Prompt

from ..auth import AuthClient, AuthenticationError, Workspace
from ..config import get_api_url
from ..credentials import (
    Credentials,
    collect_runtime_env_overrides,
    load_credentials,
    save_credentials,
)

console = Console()


def select_workspace(workspaces: list[Workspace]) -> Workspace:
    """Prompt user to select a workspace if multiple are available."""
    if len(workspaces) == 1:
        return workspaces[0]

    # Non-interactive: auto-select the first workspace when stdin is not a TTY.
    if not sys.stdin.isatty():
        console.print(f"[yellow]Auto-selecting workspace:[/yellow] {workspaces[0].name}")
        return workspaces[0]

    console.print("\n[bold]Select a workspace:[/bold]")
    for i, ws in enumerate(workspaces, 1):
        console.print(f"  {i}. {ws.name}")

    while True:
        choice = Prompt.ask(
            "\n[bold]Workspace number[/bold]",
            default="1",
        )
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(workspaces):
                return workspaces[idx]
            console.print(f"[red]Please enter a number between 1 and {len(workspaces)}[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number[/red]")


def _workspace_token_candidates(
    workspaces: list[Workspace], selected_workspace: Workspace
) -> list[Workspace]:
    """Return workspace token-creation candidates in preferred order.

    In non-interactive mode, token creation should be resilient: if the first
    workspace fails (e.g. transient 5xx or restricted workspace), try others.
    """
    if sys.stdin.isatty() or len(workspaces) <= 1:
        return [selected_workspace]

    return [
        selected_workspace,
        *[ws for ws in workspaces if ws.uuid != selected_workspace.uuid],
    ]


def _validate_stored_token(token: str) -> bool:
    """Validate a stored API token by making a lightweight SDK call."""
    try:
        from cyberwave import Cyberwave

        client = Cyberwave(base_url=get_api_url(), token=token)
        client.workspaces.list()
        return True
    except Exception:
        return False


def _stored_api_url(credentials: Credentials) -> str | None:
    """Read API URL override persisted in credentials (if any)."""
    return credentials.cyberwave_api_url or credentials.cyberwave_base_url


@click.command()
@click.option(
    "--email",
    "-e",
    help="Email address for login",
)
@click.option(
    "--password",
    "-p",
    help="Password (will prompt if not provided)",
    hide_input=True,
)
def login(email: str | None, password: str | None) -> None:
    """Authenticate with Cyberwave.

    Logs you in to Cyberwave and stores your credentials locally for future CLI operations.

    If email and password are not provided as options, you will be prompted to enter them.
    """
    # Check if already logged in by validating the stored API token via the SDK
    existing_creds = load_credentials()
    if existing_creds and existing_creds.token:
        # Validate against stored API URL first when present (edge/dev setups),
        # then fall back to the current process URL resolution.
        validate_token = existing_creds.token
        if _stored_api_url(existing_creds):
            try:
                from cyberwave import Cyberwave

                client = Cyberwave(base_url=_stored_api_url(existing_creds), token=validate_token)
                client.workspaces.list()
                is_valid = True
            except Exception:
                is_valid = _validate_stored_token(validate_token)
        else:
            is_valid = _validate_stored_token(validate_token)

        if is_valid:
            workspace_info = ""
            if existing_creds.workspace_name:
                workspace_info = f" (workspace: [bold]{existing_creds.workspace_name}[/bold])"
            display_email = existing_creds.email or "unknown"
            msg = f"\n[green]✓[/green] Already logged in as [bold]{display_email}[/bold]"
            console.print(f"{msg}{workspace_info}")
            # Non-interactive: proceed with re-login (no way to ask).
            if sys.stdin.isatty():
                if not click.confirm("Do you want to log in with a different account?"):
                    return

    # Prompt for credentials if not provided
    if not email:
        email = Prompt.ask("\n[bold]Email[/bold]")

    if not password:
        password = Prompt.ask("[bold]Password[/bold]", password=True)

    console.print("\n[dim]Authenticating...[/dim]")

    try:
        runtime_overrides = collect_runtime_env_overrides()
        with AuthClient() as client:
            # First, get a session token via OAuth
            session_token = client.login(email, password)

            # Get user info to confirm login
            user = client.get_current_user(session_token)

            # Get user's workspaces to create a permanent API token
            workspaces = client.get_workspaces(session_token)

            if not workspaces:
                console.print(
                    f"\n[yellow]⚠[/yellow] Logged in as [bold]{user.email}[/bold] "
                    "but no workspaces found."
                )
                console.print(
                    "[dim]Please create a workspace at https://cyberwave.com to use the CLI.[/dim]"
                )
                raise click.Abort()

            # Let user select a workspace if multiple are available.
            # In non-interactive mode, fall back across other workspaces if
            # token creation fails for the first choice.
            workspace = select_workspace(workspaces)
            candidate_workspaces = _workspace_token_candidates(workspaces, workspace)

            api_token = None
            token_workspace = None
            last_token_error: AuthenticationError | None = None

            for idx, candidate in enumerate(candidate_workspaces):
                if idx == 0:
                    console.print(
                        f"\n[dim]Creating API token for workspace '{candidate.name}'...[/dim]"
                    )
                else:
                    console.print(
                        f"\n[dim]Retrying API token creation with workspace "
                        f"'{candidate.name}'...[/dim]"
                    )

                try:
                    api_token = client.create_api_token(session_token, candidate.uuid)
                    token_workspace = candidate
                    break
                except AuthenticationError as exc:
                    last_token_error = exc
                    if len(candidate_workspaces) == 1:
                        raise
                    console.print(
                        f"[yellow]⚠[/yellow] Could not create API token for "
                        f"'{candidate.name}': {exc}"
                    )

            if api_token is None or token_workspace is None:
                if last_token_error:
                    raise last_token_error
                raise AuthenticationError(
                    "Failed to create API token for all available workspaces"
                )

            # Save the permanent API token (not the session token which expires)
            save_credentials(
                Credentials(
                    token=api_token.token,
                    email=user.email,
                    workspace_uuid=token_workspace.uuid,
                    workspace_name=token_workspace.name,
                    cyberwave_environment=runtime_overrides.get("CYBERWAVE_ENVIRONMENT"),
                    cyberwave_edge_log_level=runtime_overrides.get("CYBERWAVE_EDGE_LOG_LEVEL"),
                    cyberwave_api_url=runtime_overrides.get("CYBERWAVE_API_URL"),
                    cyberwave_base_url=runtime_overrides.get("CYBERWAVE_BASE_URL"),
                )
            )

            console.print(f"\n[green]✓[/green] Successfully logged in as [bold]{user.email}[/bold]")
            console.print(f"[dim]Workspace: {token_workspace.name}[/dim]")
            from ..config import CREDENTIALS_FILE

            console.print(f"[dim]API token saved to {CREDENTIALS_FILE}[/dim]")

    except AuthenticationError as e:
        console.print(f"\n[red]✗[/red] Login failed: {e}")
        raise click.Abort()
