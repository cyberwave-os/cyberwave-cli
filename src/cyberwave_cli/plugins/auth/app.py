import asyncio
import webbrowser
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any
import os

import typer
from rich import print
from rich.prompt import Confirm
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


class DeviceAuthFlow:
    """Handles device flow authentication with the CyberWave platform."""
    
    def __init__(self, backend_url: str, frontend_url: str):
        self.backend_url = backend_url.rstrip('/')
        self.frontend_url = frontend_url.rstrip('/')
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def initiate_device_flow(self) -> Dict[str, Any]:
        """
        Initiate device flow authentication.
        Returns device code, user code, and verification URLs.
        """
        try:
            response = await self.client.post(
                f"{self.backend_url}/api/v1/auth/device/initiate",
                json={"client_type": "cli"}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise typer.Exit(f"Failed to initiate device flow: {e}")
    
    async def poll_for_token(self, device_code: str, interval: int = 5) -> Optional[Dict[str, Any]]:
        """
        Poll the backend for token completion.
        Returns tokens when user completes authentication, None if still pending.
        """
        try:
            response = await self.client.post(
                f"{self.backend_url}/api/v1/auth/device/token",
                json={"device_code": device_code}
            )
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 202:
                # Still pending
                return None
            else:
                response.raise_for_status()
        except httpx.HTTPError:
            return None
    
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
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically")
) -> None:
    """
    Authenticate with CyberWave using web-based device flow.
    
    This will open your web browser to complete authentication on the CyberWave platform.
    """
    asyncio.run(_login_flow(backend_url, frontend_url, no_browser))


async def _login_flow(backend_url: Optional[str], frontend_url: Optional[str], no_browser: bool) -> None:
    """Async implementation of login flow."""
    config = load_config()
    
    # Use provided URLs or fall back to config or defaults
    backend_url = backend_url or config.get("backend_url", DEFAULT_BACKEND_URL)
    frontend_url = frontend_url or config.get("frontend_url", DEFAULT_FRONTEND_URL)
    
    print(f"[cyan]Initiating authentication with CyberWave...[/cyan]")
    print(f"Backend: {backend_url}")
    print(f"Frontend: {frontend_url}")
    
    auth_flow = DeviceAuthFlow(backend_url, frontend_url)
    
    try:
        # Step 1: Initiate device flow
        device_data = await auth_flow.initiate_device_flow()
        
        device_code = device_data["device_code"]
        user_code = device_data["user_code"]
        verification_url = device_data["verification_url"]
        expires_in = device_data.get("expires_in", 300)  # 5 minutes default
        interval = device_data.get("interval", 5)  # 5 seconds default
        
        # Step 2: Display instructions to user
        print(f"\n[bold yellow]Authentication Required[/bold yellow]")
        print(f"Please visit: [bold blue]{verification_url}[/bold blue]")
        print(f"And enter the code: [bold green]{user_code}[/bold green]")
        print(f"\nCode expires in {expires_in} seconds")
        
        # Step 3: Open browser (unless disabled)
        if not no_browser:
            try:
                # Construct the full URL with the user code
                full_url = f"{frontend_url}/auth/device?user_code={user_code}"
                webbrowser.open(full_url)
                print(f"[dim]Opened browser to: {full_url}[/dim]")
            except Exception as e:
                print(f"[yellow]Could not open browser automatically: {e}[/yellow]")
        
        # Step 4: Poll for completion
        print(f"\n[dim]Waiting for authentication completion...[/dim]")
        start_time = time.time()
        
        with console.status("[bold green]Waiting for authentication...") as status:
            while time.time() - start_time < expires_in:
                token_data = await auth_flow.poll_for_token(device_code, interval)
                
                if token_data:
                    # Success! Store tokens
                    print(f"\n[green]✓ Authentication successful![/green]")
                    
                    # Save tokens using the SDK's token storage
                    from cyberwave import Client
                    client = Client(base_url=backend_url)
                    client._access_token = token_data["access_token"]
                    client._refresh_token = token_data.get("refresh_token")
                    client._session_info = {k: v for k, v in token_data.items() 
                                          if k not in ["access_token", "refresh_token"]}
                    
                    if client._use_token_cache:
                        client._save_token_to_cache()
                    
                    # Update config with URLs
                    config.update({
                        "backend_url": backend_url,
                        "frontend_url": frontend_url,
                        "default_workspace": token_data.get("default_workspace"),
                        "default_project": token_data.get("default_project")
                    })
                    save_config(config)
                    
                    # Display user info
                    try:
                        user_info = await client.get_current_user_info()
                        print(f"Logged in as: [bold]{user_info.get('email', 'Unknown')}[/bold]")
                    except Exception:
                        print("Logged in successfully!")
                    
                    await client.aclose()
                    return
                
                # Wait before next poll
                await asyncio.sleep(interval)
                status.update(f"[bold green]Waiting for authentication... ({int(expires_in - (time.time() - start_time))}s remaining)")
        
        print(f"\n[red]✗ Authentication timed out. Please try again.[/red]")
        
    except Exception as e:
        print(f"[red]✗ Authentication failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        await auth_flow.close()


@app.command()
def logout() -> None:
    """Log out and clear stored authentication."""
    try:
        from cyberwave import Client
        
        # Clear tokens from cache
        client = Client()
        asyncio.run(client.logout())
        
        print("[green]✓ Successfully logged out[/green]")
    except Exception as e:
        print(f"[red]Error during logout: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show current authentication and configuration status."""
    config = load_config()
    
    # Check if user is authenticated
    from cyberwave import Client
    client = Client()
    
    table = Table(title="CyberWave CLI Status", show_header=True, header_style="bold magenta")
    table.add_column("Setting", style="cyan", width=20)
    table.add_column("Value", style="white")
    
    # Authentication status
    if client._access_token:
        table.add_row("Authentication", "[green]✓ Authenticated[/green]")
        
        # Try to get user info
        try:
            user_info = asyncio.run(client.get_current_user_info())
            table.add_row("User", user_info.get('email', 'Unknown'))
        except Exception:
            table.add_row("User", "[yellow]Token may be expired[/yellow]")
    else:
        table.add_row("Authentication", "[red]✗ Not authenticated[/red]")
    
    # Configuration
    table.add_row("Backend URL", config.get("backend_url", DEFAULT_BACKEND_URL))
    table.add_row("Frontend URL", config.get("frontend_url", DEFAULT_FRONTEND_URL))
    table.add_row("Default Workspace", str(config.get("default_workspace", "Not set")))
    table.add_row("Default Project", str(config.get("default_project", "Not set")))
    table.add_row("Config File", str(CONFIG_FILE))
    
    console.print(table)


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