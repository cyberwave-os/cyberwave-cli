"""Login command for the Cyberwave CLI."""

import sys
import time

import click
from rich.console import Console
from rich.prompt import Prompt

from ..auth import AuthClient, AuthenticationError, Workspace
from ..config import CREDENTIALS_FILE, get_api_url
from ..credentials import (
    Credentials,
    collect_runtime_env_overrides,
    load_credentials,
    save_credentials,
)

console = Console()

MAX_LOGIN_ATTEMPTS = 3
MAX_API_TOKEN_RETRIES = 3


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


def _is_retryable_workspace_token_error(error: AuthenticationError) -> bool:
    """Return True when API token creation should be retried."""
    return "HTTP error: 500" in str(error)


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
    return credentials.cyberwave_base_url


def _abort_for_missing_workspaces(user_email: str) -> None:
    """Show the shared no-workspaces guidance and abort."""
    console.print(
        f"\n[yellow][WARN][/yellow] Logged in as [bold]{user_email}[/bold] "
        "but no workspaces found."
    )
    console.print(
        "[dim]Please create a workspace at https://cyberwave.com "
        "to use the CLI.[/dim]"
    )
    raise click.Abort()


def _save_login_credentials(
    *,
    token: str,
    user_email: str,
    workspace: Workspace,
    runtime_overrides: dict[str, str],
) -> None:
    """Persist login credentials using the standard CLI schema."""
    save_credentials(
        Credentials(
            token=token,
            email=user_email,
            workspace_uuid=workspace.uuid,
            workspace_name=workspace.name,
            cyberwave_environment=runtime_overrides.get("CYBERWAVE_ENVIRONMENT"),
            cyberwave_edge_log_level=runtime_overrides.get("CYBERWAVE_EDGE_LOG_LEVEL"),
            cyberwave_base_url=runtime_overrides.get("CYBERWAVE_BASE_URL"),
            cyberwave_mqtt_host=runtime_overrides.get("CYBERWAVE_MQTT_HOST"),
        )
    )


def _print_login_success(user_email: str, workspace_name: str) -> None:
    """Show the standard post-login success output."""
    console.print(f"\n[green][OK][/green] Successfully logged in as [bold]{user_email}[/bold]")
    console.print(f"[dim]Workspace: {workspace_name}[/dim]")
    console.print(f"[dim]API token saved to {CREDENTIALS_FILE}[/dim]", highlight=False)


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
@click.option(
    "--token",
    help="API token for login",
)
def login(email: str | None, password: str | None, token: str | None) -> None:
    """Authenticate with Cyberwave.

    Logs you in to Cyberwave and stores your credentials locally for future CLI operations.

    If email and password are not provided as options, you will be prompted to enter them.
    You can also authenticate directly with an existing API token.
    """
    auth_token = token.strip() if token is not None else None
    if token is not None and not auth_token:
        raise click.UsageError("--token cannot be empty")
    if auth_token and (email or password):
        raise click.UsageError("--token is mutually exclusive with --email/--password")

    # Check if already logged in by validating the stored API token via the SDK
    existing_creds = load_credentials()
    if existing_creds and existing_creds.token and not auth_token:
        # Validate against stored API URL first when present (edge/dev setups),
        # then fall back to the current process URL resolution.
        validate_token = existing_creds.token
        with console.status("[dim]Checking existing credentials...[/dim]"):
            if _stored_api_url(existing_creds):
                try:
                    from cyberwave import Cyberwave

                    stored_url = _stored_api_url(existing_creds)
                    client = Cyberwave(base_url=stored_url, token=validate_token)
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
            msg = f"\n[green][OK][/green] Already logged in as [bold]{display_email}[/bold]"
            console.print(f"{msg}{workspace_info}")
            # Non-interactive: proceed with re-login (no way to ask).
            if sys.stdin.isatty():
                if not click.confirm("Do you want to log in with a different account?"):
                    return

    if auth_token:
        try:
            runtime_overrides = collect_runtime_env_overrides()
            with AuthClient() as client:
                with console.status("[dim]Authenticating with API token...[/dim]"):
                    token_context = client.get_api_token_context(auth_token)

            workspace = Workspace(
                uuid=token_context.workspace_uuid,
                name=token_context.workspace_name,
                slug="",
            )
            _save_login_credentials(
                token=auth_token,
                user_email=token_context.email,
                workspace=workspace,
                runtime_overrides=runtime_overrides,
            )
            _print_login_success(token_context.email, workspace.name)
            return
        except AuthenticationError as e:
            console.print(f"\n[red][ERROR][/red] Login failed: {e}")
            raise click.Abort() from None

    email_from_cli = email
    password_from_cli = password
    # Without a TTY we cannot re-prompt; a single attempt avoids hanging on Prompt.ask.
    max_attempts = MAX_LOGIN_ATTEMPTS if sys.stdin.isatty() else 1

    for attempt in range(1, max_attempts + 1):
        login_email = email_from_cli if email_from_cli else Prompt.ask("\n[bold]Email[/bold]")
        login_password = password_from_cli if password_from_cli else Prompt.ask(
            "[bold]Password[/bold]", password=True
        )

        try:
            runtime_overrides = collect_runtime_env_overrides()
            with AuthClient() as client:
                with console.status("[dim]Authenticating...[/dim]"):
                    session_token = client.login(login_email, login_password)
                    user = client.get_current_user(session_token)
                    workspaces = client.get_workspaces(session_token)

                if not workspaces:
                    _abort_for_missing_workspaces(user.email)

                workspace = select_workspace(workspaces)
                api_token = None

                for token_attempt in range(1, MAX_API_TOKEN_RETRIES + 1):
                    status_msg = (
                        f"[dim]Creating API token for workspace '{workspace.name}'...[/dim]"
                        if token_attempt == 1
                        else (
                            f"[dim]Retrying API token creation for workspace "
                            f"'{workspace.name}' ({token_attempt}/{MAX_API_TOKEN_RETRIES})...[/dim]"
                        )
                    )
                    with console.status(status_msg):
                        try:
                            api_token = client.create_api_token(session_token, workspace.uuid)
                            break
                        except AuthenticationError as exc:
                            retryable = _is_retryable_workspace_token_error(exc)
                            if token_attempt == MAX_API_TOKEN_RETRIES or not retryable:
                                raise
                    wait_seconds = 2 * token_attempt
                    console.print(
                        f"[yellow][WARN][/yellow] Temporary token creation failure. "
                        f"Retrying in {wait_seconds}s..."
                    )
                    time.sleep(wait_seconds)

                if api_token is None:
                    raise AuthenticationError("Failed to create API token")

                _save_login_credentials(
                    token=api_token.token,
                    user_email=user.email,
                    workspace=workspace,
                    runtime_overrides=runtime_overrides,
                )
                _print_login_success(user.email, workspace.name)
            return

        except AuthenticationError as e:
            console.print(f"\n[red][ERROR][/red] Login failed: {e}")
            password_from_cli = None
            if not email_from_cli:
                email_from_cli = None
            if attempt >= max_attempts:
                raise click.Abort() from None
            console.print("[yellow]Please try again.[/yellow]")
