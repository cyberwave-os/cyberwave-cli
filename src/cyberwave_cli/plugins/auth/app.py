import asyncio
from pathlib import Path
from typing import Optional, Dict, Any

import typer
from rich import print
from rich.console import Console
from rich.table import Table
import httpx

console = Console()
app = typer.Typer(help="Authentication and configuration management")

# Configuration constants
CONFIG_DIR = Path.home() / ".cyberwave"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DEFAULT_FRONTEND_URL = "http://localhost:3000"
DEFAULT_BACKEND_URL = "http://localhost:8000"


class SimpleAuthFlow:
    """Simple email/password authentication for CLI."""
    
    def __init__(self, backend_url: str):
        self.backend_url = backend_url.rstrip('/')
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Login with email and password.
        Returns token and user information.
        """
        try:
            response = await self.client.post(
                f"{self.backend_url}/api/v1/users/auth/cli/login",
                json={"email": email, "password": password}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            if e.response.status_code == 401:
                raise typer.Exit("Invalid email or password")
            else:
                raise typer.Exit(f"Authentication failed: {e}")
    
    async def check_status(self, token: str) -> Dict[str, Any]:
        """Check authentication status with token."""
        try:
            response = await self.client.get(
                f"{self.backend_url}/api/v1/users/auth/cli/status",
                headers={"Authorization": f"Token {token}"}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise typer.Exit(f"Status check failed: {e}")
    
    async def logout(self, token: str) -> None:
        """Logout and revoke token."""
        try:
            response = await self.client.post(
                f"{self.backend_url}/api/v1/users/auth/cli/logout",
                headers={"Authorization": f"Token {token}"}
            )
            response.raise_for_status()
        except httpx.HTTPError:
            # Ignore errors on logout - token might already be invalid
            pass
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


def load_config() -> Dict[str, Any]:
    """Load CLI configuration from file."""
    if not CONFIG_FILE.exists():
        return {}
    
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            print("[red]Error: Neither tomllib nor tomli is available. Please install tomli: pip install tomli[/red]")
            raise typer.Exit(1)
    
    try:
        with open(CONFIG_FILE, 'rb') as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"[red]Error reading config file: {e}[/red]")
        return {}


def save_config(config: Dict[str, Any]) -> None:
    """Save CLI configuration to file."""
    CONFIG_DIR.mkdir(exist_ok=True)
    
    try:
        import tomli_w
    except ImportError:
        print("[red]Error: tomli_w is not available. Please install tomli-w: pip install tomli-w[/red]")
        raise typer.Exit(1)
    
    try:
        with open(CONFIG_FILE, 'wb') as f:
            tomli_w.dump(config, f)
        print(f"[green]Configuration saved to {CONFIG_FILE}[/green]")
    except Exception as e:
        print(f"[red]Error saving config: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def login(
    backend_url: Optional[str] = typer.Option(None, "--backend-url", help="Backend URL"),
    frontend_url: Optional[str] = typer.Option(None, "--frontend-url", help="Frontend URL"),
    email: Optional[str] = typer.Option(None, "--email", help="Email address"),
    password: Optional[str] = typer.Option(None, "--password", help="Password (will be prompted if not provided)")
) -> None:
    """
    Authenticate with CyberWave using email and password.
    
    This is a simple, secure authentication method for CLI users.
    """
    asyncio.run(_login_flow(backend_url, frontend_url, email, password))


async def _login_flow(backend_url: Optional[str], frontend_url: Optional[str], email: Optional[str], password: Optional[str]) -> None:
    """Async implementation of simple login flow."""
    config = load_config()
    
    # Use provided URLs or fall back to config or defaults
    backend_url = backend_url or config.get("backend_url", DEFAULT_BACKEND_URL)
    frontend_url = frontend_url or config.get("frontend_url", DEFAULT_FRONTEND_URL)
    
    print(f"[cyan]Authenticating with CyberWave...[/cyan]")
    print(f"Backend: {backend_url}")
    
    # Get credentials from user if not provided
    if not email:
        email = typer.prompt("Email")
    
    if not password:
        password = typer.prompt("Password", hide_input=True)
    
    auth_flow = SimpleAuthFlow(backend_url)
    
    try:
        # Login with credentials
        login_data = await auth_flow.login(email, password)
        
        # Save token using the SDK's token storage
        from cyberwave import Client
        client = Client(base_url=backend_url)
        client._access_token = login_data["token"]
        
        if client._use_token_cache:
            client._save_token_to_cache()
        
        # Update config with URLs and user info
        config.update({
            "backend_url": backend_url,
            "frontend_url": frontend_url,
        })
        save_config(config)
        
        # Display success message
        user_info = login_data.get("user", {})
        print(f"[green]✓ Authentication successful![/green]")
        print(f"Logged in as: [bold]{user_info.get('email', email)}[/bold]")
        
        await client.aclose()
        
    except Exception as e:
        print(f"[red]✗ Authentication failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        await auth_flow.close()


async def _logout_flow() -> None:
    """Async logout implementation."""
    try:
        from cyberwave import Client
        config = load_config()
        backend_url = config.get("backend_url", DEFAULT_BACKEND_URL)
        
        client = Client(base_url=backend_url)
        
        if client._access_token:
            # Try to revoke token on server
            auth_flow = SimpleAuthFlow(backend_url)
            try:
                await auth_flow.logout(client._access_token)
            except Exception:
                pass  # Ignore server errors during logout
            finally:
                await auth_flow.close()
        
        # Clear local token cache
        await client.logout()
        
        print("[green]✓ Successfully logged out[/green]")
    except Exception as e:
        print(f"[red]Error during logout: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def logout() -> None:
    """Log out and clear stored authentication."""
    asyncio.run(_logout_flow())


@app.command()
def status() -> None:
    """Show current authentication and configuration status."""
    asyncio.run(_status_flow())


async def _status_flow() -> None:
    """Async status check implementation."""
    config = load_config()
    backend_url = config.get("backend_url", DEFAULT_BACKEND_URL)
    
    # Check if user is authenticated
    from cyberwave import Client
    client = Client(base_url=backend_url)
    
    table = Table(title="CyberWave CLI Status", show_header=True, header_style="bold magenta")
    table.add_column("Setting", style="cyan", width=20)
    table.add_column("Value", style="white")
    
    # Authentication status
    if client._access_token:
        # Check if token is still valid
        auth_flow = SimpleAuthFlow(backend_url)
        try:
            status_data = await auth_flow.check_status(client._access_token)
            if status_data.get("authenticated"):
                table.add_row("Authentication", "[green]✓ Authenticated[/green]")
                user_info = status_data.get("user", {})
                table.add_row("User", user_info.get('email', 'Unknown'))
            else:
                table.add_row("Authentication", "[yellow]Token expired[/yellow]")
        except Exception:
            table.add_row("Authentication", "[yellow]Token may be invalid[/yellow]")
        finally:
            await auth_flow.close()
    else:
        table.add_row("Authentication", "[red]✗ Not authenticated[/red]")
    
    # Configuration
    table.add_row("Backend URL", config.get("backend_url", DEFAULT_BACKEND_URL))
    table.add_row("Frontend URL", config.get("frontend_url", DEFAULT_FRONTEND_URL))
    table.add_row("Config File", str(CONFIG_FILE))
    
    console.print(table)
    
    await client.aclose()


@app.command()
def config(
    key: Optional[str] = typer.Argument(None, help="Configuration key to set or get"),
    value: Optional[str] = typer.Argument(None, help="Value to set (omit to get current value)"),
    list_all: bool = typer.Option(False, "--list", "-l", help="List all configuration"),
    unset: bool = typer.Option(False, "--unset", "-u", help="Unset a configuration key")
) -> None:
    """
    Manage CLI configuration.
    
    Examples:
        cyberwave auth config backend_url http://localhost:8000
        cyberwave auth config backend_url
        cyberwave auth config --list
        cyberwave auth config --unset default_workspace
    """
    current_config = load_config()
    
    if list_all or (key is None and value is None):
        # List all configuration
        if not current_config:
            print("[yellow]No configuration found[/yellow]")
            return
        
        table = Table(title="Configuration", show_header=True, header_style="bold magenta")
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="white")
        
        for k, v in current_config.items():
            table.add_row(k, str(v))
        
        console.print(table)
        return
    
    if key is None:
        print("[red]Error: Please specify a configuration key[/red]")
        raise typer.Exit(1)
    
    if unset:
        # Unset a key
        if key in current_config:
            del current_config[key]
            save_config(current_config)
            print(f"[green]Unset {key}[/green]")
        else:
            print(f"[yellow]Key '{key}' not found[/yellow]")
        return
    
    if value is None:
        # Get current value
        if key in current_config:
            print(f"{key} = {current_config[key]}")
        else:
            print(f"[yellow]Key '{key}' not found[/yellow]")
        return
    
    # Set value
    current_config[key] = value
    save_config(current_config)
    print(f"[green]Set {key} = {value}[/green]")


if __name__ == "__main__":
    app() 