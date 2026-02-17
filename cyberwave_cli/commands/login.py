"""Login command for the Cyberwave CLI."""

import sys

import click
from rich.console import Console
from rich.prompt import Prompt

from ..auth import AuthClient, AuthenticationError, Workspace
from ..credentials import Credentials, load_credentials, save_credentials

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
    # Check if already logged in
    existing_creds = load_credentials()
    if existing_creds and existing_creds.token:
        try:
            with AuthClient() as client:
                user = client.get_current_user(existing_creds.token)
                workspace_info = ""
                if existing_creds.workspace_name:
                    workspace_info = f" (workspace: [bold]{existing_creds.workspace_name}[/bold])"
                msg = f"\n[green]✓[/green] Already logged in as [bold]{user.email}[/bold]"
                console.print(f"{msg}{workspace_info}")
                # Non-interactive: proceed with re-login (no way to ask).
                if sys.stdin.isatty():
                    if not click.confirm("Do you want to log in with a different account?"):
                        return
        except AuthenticationError:
            # Token is invalid, proceed with new login
            pass

    # Prompt for credentials if not provided
    if not email:
        email = Prompt.ask("\n[bold]Email[/bold]")

    if not password:
        password = Prompt.ask("[bold]Password[/bold]", password=True)

    console.print("\n[dim]Authenticating...[/dim]")

    try:
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

            # Let user select a workspace if multiple are available
            workspace = select_workspace(workspaces)
            console.print(f"\n[dim]Creating API token for workspace '{workspace.name}'...[/dim]")

            # Create a permanent API token for the selected workspace
            api_token = client.create_api_token(session_token, workspace.uuid)

            # Save the permanent API token (not the session token which expires)
            save_credentials(
                Credentials(
                    token=api_token.token,
                    email=user.email,
                    workspace_uuid=workspace.uuid,
                    workspace_name=workspace.name,
                )
            )

            console.print(f"\n[green]✓[/green] Successfully logged in as [bold]{user.email}[/bold]")
            console.print(f"[dim]Workspace: {workspace.name}[/dim]")
            from ..config import CREDENTIALS_FILE

            console.print(f"[dim]API token saved to {CREDENTIALS_FILE}[/dim]")

    except AuthenticationError as e:
        console.print(f"\n[red]✗[/red] Login failed: {e}")
        raise click.Abort()
