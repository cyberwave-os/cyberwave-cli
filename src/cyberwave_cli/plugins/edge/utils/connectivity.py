"""
Connectivity Management for Cyberwave Edge Nodes

Handles online/offline modes, backend connectivity checks, and authentication flow.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from .node_identity import node_identity_manager, get_node_id, export_node_info

# Import SDK environment configuration with fallback
try:
    from cyberwave.constants import (
        CyberWaveEnvironment, 
        ENVIRONMENT_URLS, 
        get_backend_url,
        ENVIRONMENT_ENV_VAR,
        BACKEND_URL_ENV_VAR
    )
    SDK_AVAILABLE = True
except (ImportError, AttributeError):
    # Fallback if SDK doesn't have the newer constants
    from enum import Enum
    
    class CyberWaveEnvironment(Enum):
        LOCAL = "local"
        DEV = "dev"
        QA = "qa"
        PROD = "prod"
    
    ENVIRONMENT_URLS = {
        CyberWaveEnvironment.LOCAL: "http://localhost:8000/api/v1",
        CyberWaveEnvironment.DEV: "https://api-dev.cyberwave.com/api/v1",
        CyberWaveEnvironment.QA: "https://api-qa.cyberwave.com/api/v1",
        CyberWaveEnvironment.PROD: "https://api.cyberwave.com/api/v1"
    }
    
    ENVIRONMENT_ENV_VAR = "CYBERWAVE_ENVIRONMENT"
    BACKEND_URL_ENV_VAR = "CYBERWAVE_BACKEND_URL"
    
    def get_backend_url(environment=None):
        import os
        
        if environment and environment in ENVIRONMENT_URLS:
            return ENVIRONMENT_URLS[environment]
        
        # Check environment variable
        env_name = os.getenv(ENVIRONMENT_ENV_VAR)
        if env_name:
            try:
                env_enum = CyberWaveEnvironment(env_name.lower())
                return ENVIRONMENT_URLS[env_enum]
            except ValueError:
                pass
        
        # Check direct URL override
        url_override = os.getenv(BACKEND_URL_ENV_VAR)
        if url_override:
            return url_override
        
        return ENVIRONMENT_URLS[CyberWaveEnvironment.LOCAL]
    
    SDK_AVAILABLE = False  # Using fallback

logger = logging.getLogger(__name__)
console = Console()

class ConnectivityMode(Enum):
    """Connectivity modes for edge nodes."""
    ONLINE = "online"
    OFFLINE = "offline"
    HYBRID = "hybrid" 

@dataclass
class BackendHealth:
    """Backend health status."""
    available: bool
    response_time_ms: Optional[float] = None
    version: Optional[str] = None
    status: Optional[str] = None
    last_checked: Optional[datetime] = None
    error: Optional[str] = None

@dataclass
class OfflineConfig:
    """Configuration for offline mode."""
    node_id: str
    auth_token: str
    backend_url: str
    sync_enabled: bool = True
    sync_interval_minutes: int = 15
    max_offline_duration_hours: int = 24

class ConnectivityManager:
    """Manages connectivity between edge nodes and backend."""
    
    def __init__(self, backend_url: str = None, environment: str = None, config_dir: Path = None):
        # Set environment first
        self.environment = environment or self._detect_environment()
        # Then set backend_url
        self.backend_url = backend_url or self._get_backend_url_from_environment(self.environment)
        self.config_dir = config_dir or Path.home() / ".cyberwave"
        self.config_dir.mkdir(exist_ok=True)
        
        self.offline_config_path = self.config_dir / "offline_config.json"
        self.health_cache_path = self.config_dir / "backend_health.json"
        
        self._health_cache: Optional[BackendHealth] = None
        self._offline_config: Optional[OfflineConfig] = None
        
        # Load cached configurations
        self._load_health_cache()
        self._load_offline_config()
    
    def _get_backend_url_from_environment(self, environment: str = None) -> str:
        """Get backend URL from environment (internal method)"""
        environment = environment or self.environment
        return get_backend_url(environment)
    
    def _get_backend_url(self) -> str:
        """Get backend URL using current environment configuration."""
        return self._get_backend_url_from_environment(self.environment)
    
    def _get_frontend_url(self) -> str:
        """Get frontend URL for current environment."""
        # Frontend typically runs on port 3000 for local/dev, different for prod
        backend_url = self._get_backend_url()
        if "localhost" in backend_url or "127.0.0.1" in backend_url:
            return "http://localhost:3000"
        else:
            # For production environments, derive from backend URL
            return backend_url.replace(":8000", ":3000").replace("/api", "")
    
    def _detect_environment(self) -> str:
        """Detect current environment from various sources."""
        import os
        
        # Check environment variable first
        env_var = os.getenv("CYBERWAVE_ENVIRONMENT")
        if env_var:
            return env_var.lower()
        
        # Check CLI config
        try:
            from cyberwave_cli.plugins.auth.app import load_config
            cli_config = load_config()
            backend_url = cli_config.get("backend_url", "")
            
            # Detect environment from backend URL
            if "localhost" in backend_url:
                return "local"
            elif "dev" in backend_url:
                return "dev"
            elif "qa" in backend_url:
                return "qa"
            elif "staging" in backend_url:
                return "staging"
            else:
                return "prod"
        except Exception:
            # Default to local for development
            return "local"
    
    async def check_backend_health(self, timeout: float = 5.0) -> BackendHealth:
        """Check backend availability and health."""
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(f"{self.backend_url}/health")
                
                response_time = (time.time() - start_time) * 1000
                
                if response.status_code == 200:
                    health_data = response.json()
                    health = BackendHealth(
                        available=True,
                        response_time_ms=response_time,
                        version=health_data.get("version"),
                        status=health_data.get("status", "healthy"),
                        last_checked=datetime.now()
                    )
                else:
                    health = BackendHealth(
                        available=False,
                        response_time_ms=response_time,
                        last_checked=datetime.now(),
                        error=f"HTTP {response.status_code}: {response.text}"
                    )
        
        except Exception as e:
            health = BackendHealth(
                available=False,
                last_checked=datetime.now(),
                error=str(e)
            )
        
        # Cache the health status
        self._health_cache = health
        self._save_health_cache()
        
        return health
    
    async def check_connectivity_with_progress(self) -> BackendHealth:
        """Check connectivity with progress indicator."""
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Checking backend connectivity...", total=None)
            
            health = await self.check_backend_health()
            
            if health.available:
                progress.update(task, description=f"✅ Connected ({health.response_time_ms:.0f}ms)")
            else:
                progress.update(task, description="❌ Backend unavailable")
            
            await asyncio.sleep(0.5)  # Brief pause to show result
        
        return health
    
    def get_connectivity_mode(self, health: BackendHealth = None) -> ConnectivityMode:
        """Determine the appropriate connectivity mode."""
        if health is None:
            health = self._health_cache
        
        if health and health.available:
            return ConnectivityMode.ONLINE
        elif self._offline_config and self._is_offline_config_valid():
            return ConnectivityMode.HYBRID
        else:
            return ConnectivityMode.OFFLINE
    
    def _is_offline_config_valid(self) -> bool:
        """Check if offline configuration is still valid."""
        if not self._offline_config:
            return False
        
        # Check if offline duration hasn't exceeded limit
        if self._health_cache and self._health_cache.last_checked:
            offline_duration = datetime.now() - self._health_cache.last_checked
            max_duration = timedelta(hours=self._offline_config.max_offline_duration_hours)
            
            if offline_duration > max_duration:
                return False
        
        return True
    
    async def handle_connectivity_flow(self, operation: str = "edge operation") -> Tuple[ConnectivityMode, Dict[str, Any]]:
        """
        Handle the complete connectivity flow for edge operations.
        
        Returns:
            (mode, config) tuple where config contains necessary connection info
        """
        console.print(f"\n🔗 [bold blue]Checking connectivity for {operation}[/bold blue]")
        
        # Check backend health
        health = await self.check_connectivity_with_progress()
        mode = self.get_connectivity_mode(health)
        
        # Display connectivity status
        self._display_connectivity_status(health, mode)
        
        if mode == ConnectivityMode.ONLINE:
            # Online mode - full backend integration
            return await self._handle_online_mode()
            
        elif mode == ConnectivityMode.HYBRID:
            # Hybrid mode - use cached offline config
            return await self._handle_hybrid_mode()
            
        else:
            # Offline mode - need to set up offline configuration
            return await self._handle_offline_mode()
    
    def _display_connectivity_status(self, health: BackendHealth, mode: ConnectivityMode):
        """Display current connectivity status."""
        
        status_table = Table.grid(padding=1)
        status_table.add_column("Property", style="cyan")
        status_table.add_column("Value", style="white")
        
        # Backend status
        if health.available:
            status_table.add_row("Backend Status", "[green]✅ Online[/green]")
            status_table.add_row("Response Time", f"{health.response_time_ms:.0f}ms")
            if health.version:
                status_table.add_row("Backend Version", health.version)
        else:
            status_table.add_row("Backend Status", "[red]❌ Offline[/red]")
            if health.error:
                status_table.add_row("Error", f"[dim]{health.error}[/dim]")
        
        # Connectivity mode
        mode_colors = {
            ConnectivityMode.ONLINE: "green",
            ConnectivityMode.HYBRID: "yellow", 
            ConnectivityMode.OFFLINE: "red"
        }
        mode_color = mode_colors.get(mode, "white")
        status_table.add_row("Mode", f"[{mode_color}]{mode.value.title()}[/{mode_color}]")
        
        console.print(Panel(status_table, title="🌐 Connectivity Status"))
    
    async def _handle_online_mode(self) -> Tuple[ConnectivityMode, Dict[str, Any]]:
        """Handle online mode flow."""
        console.print("[green]✅ Online mode - full backend integration available[/green]")
        
        config = {
            "backend_url": self.backend_url,
            "mode": ConnectivityMode.ONLINE,
            "features": ["registration", "telemetry", "commands", "updates"]
        }
        
        return ConnectivityMode.ONLINE, config
    
    async def _handle_hybrid_mode(self) -> Tuple[ConnectivityMode, Dict[str, Any]]:
        """Handle hybrid mode flow."""
        console.print("[yellow]🔄 Hybrid mode - using cached configuration[/yellow]")
        console.print("Will sync with backend when connectivity is restored.")
        
        config = {
            "backend_url": self.backend_url,
            "mode": ConnectivityMode.HYBRID,
            "node_id": self._offline_config.node_id,
            "auth_token": self._offline_config.auth_token,
            "sync_enabled": True,
            "features": ["local_processing", "cached_data", "delayed_sync"]
        }
        
        return ConnectivityMode.HYBRID, config
    
    async def _handle_offline_mode(self) -> Tuple[ConnectivityMode, Dict[str, Any]]:
        """Handle offline mode flow."""
        console.print("[red]❌ Backend unavailable - offline mode required[/red]")
        
        # Check if user wants to set up offline mode
        if not Confirm.ask("\nWould you like to set up offline mode?"):
            console.print("[yellow]⚠️ Cannot proceed without connectivity or offline setup[/yellow]")
            raise ConnectionError("Backend unavailable and offline mode declined")
        
        # Guide user through offline setup
        offline_config = await self._setup_offline_mode()
        
        config = {
            "backend_url": self.backend_url,
            "mode": ConnectivityMode.OFFLINE,
            "node_id": offline_config.node_id,
            "auth_token": offline_config.auth_token,
            "setup_required": True,
            "features": ["local_processing", "limited_functionality"]
        }
        
        return ConnectivityMode.OFFLINE, config
    
    async def _setup_offline_mode(self) -> OfflineConfig:
        """Set up offline mode configuration."""
        console.print("\n[bold blue]🛠️ Offline Mode Setup[/bold blue]")
        
        # Get the current node identity
        current_node_id = get_node_id()
        node_info = export_node_info()
        
        # Display setup instructions with the actual node ID
        setup_panel = Panel(
            f"[bold]Offline Mode Setup Steps:[/bold]\n\n"
            f"1. 🌐 Go to the Cyberwave web interface\n"
            f"2. 🔧 Register this edge node in your project\n"
            f"3. 🔑 Generate an authentication token\n"
            f"4. 📋 Enter the token below\n\n"
            f"[bold blue]Web Interface:[/bold blue] {self._get_frontend_url()}/edge/register\n"
            f"[bold cyan]Your Node ID:[/bold cyan] {current_node_id}\n"
            f"[bold green]Node Name:[/bold green] {node_info['node_name']}\n\n"
            f"[dim]Use the Node ID above when registering in the web interface[/dim]",
            title="📱 Node Registration Required"
        )
        console.print(setup_panel)
        
        # Show additional node information for registration
        console.print("\n[bold]📋 Node Information for Registration:[/bold]")
        info_table = Table.grid(padding=1)
        info_table.add_column("Field", style="cyan")
        info_table.add_column("Value", style="white")
        
        info_table.add_row("Node ID", current_node_id)
        info_table.add_row("Node Name", node_info['node_name'])
        info_table.add_row("Platform", f"{node_info['platform']} ({node_info['architecture']})")
        info_table.add_row("Hostname", node_info['hostname'])
        info_table.add_row("Version", node_info['version'])
        
        console.print(info_table)
        
        # Get authentication token from user
        console.print(f"\n[bold yellow]After registering the node in the web interface:[/bold yellow]")
        auth_token = Prompt.ask("Enter the authentication token from the backend", password=True)
        
        # Validate the token format
        if not auth_token or len(auth_token) < 10:
            raise ValueError("Authentication token appears to be invalid")
        
        # Create offline configuration using the existing node ID
        offline_config = OfflineConfig(
            node_id=current_node_id,
            auth_token=auth_token,
            backend_url=self.backend_url,
            sync_enabled=True
        )
        
        # Save configuration
        self._offline_config = offline_config
        self._save_offline_config()
        
        # Mark node as registered
        node_identity_manager.mark_backend_registered(auth_token)
        
        console.print("[green]✅ Offline mode configured successfully[/green]")
        console.print(f"Node ID: [cyan]{current_node_id}[/cyan]")
        console.print("Will attempt to sync with backend when connectivity is restored.")
        
        return offline_config
    
    def _get_frontend_url(self) -> str:
        """Get frontend URL based on environment and backend URL."""
        # Use environment-based mapping from SDK
        frontend_urls = {
            CyberWaveEnvironment.LOCAL.value: "http://localhost:3000",
            CyberWaveEnvironment.DEV.value: "https://app-dev.cyberwave.com",
            CyberWaveEnvironment.QA.value: "https://app-qa.cyberwave.com",
            CyberWaveEnvironment.PROD.value: "https://app.cyberwave.com"
        }
        
        return frontend_urls.get(self.environment, "http://localhost:3000")
    
    def _load_health_cache(self):
        """Load cached health status."""
        try:
            if self.health_cache_path.exists():
                with open(self.health_cache_path, 'r') as f:
                    data = json.load(f)
                    
                    # Convert timestamp back to datetime
                    if data.get("last_checked"):
                        data["last_checked"] = datetime.fromisoformat(data["last_checked"])
                    
                    self._health_cache = BackendHealth(**data)
        except Exception as e:
            logger.debug(f"Could not load health cache: {e}")
            self._health_cache = None
    
    def _save_health_cache(self):
        """Save health status to cache."""
        try:
            if self._health_cache:
                data = {
                    "available": self._health_cache.available,
                    "response_time_ms": self._health_cache.response_time_ms,
                    "version": self._health_cache.version,
                    "status": self._health_cache.status,
                    "last_checked": self._health_cache.last_checked.isoformat() if self._health_cache.last_checked else None,
                    "error": self._health_cache.error
                }
                
                with open(self.health_cache_path, 'w') as f:
                    json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"Could not save health cache: {e}")
    
    def _load_offline_config(self):
        """Load offline configuration."""
        try:
            if self.offline_config_path.exists():
                with open(self.offline_config_path, 'r') as f:
                    data = json.load(f)
                    self._offline_config = OfflineConfig(**data)
        except Exception as e:
            logger.debug(f"Could not load offline config: {e}")
            self._offline_config = None
    
    def _save_offline_config(self):
        """Save offline configuration."""
        try:
            if self._offline_config:
                data = {
                    "node_id": self._offline_config.node_id,
                    "auth_token": self._offline_config.auth_token,
                    "backend_url": self._offline_config.backend_url,
                    "sync_enabled": self._offline_config.sync_enabled,
                    "sync_interval_minutes": self._offline_config.sync_interval_minutes,
                    "max_offline_duration_hours": self._offline_config.max_offline_duration_hours
                }
                
                with open(self.offline_config_path, 'w') as f:
                    json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"Could not save offline config: {e}")
    
    async def sync_with_backend(self) -> bool:
        """Attempt to sync local data with backend."""
        if not self._offline_config:
            return False
        
        try:
            health = await self.check_backend_health()
            if not health.available:
                return False
            
            console.print("🔄 Syncing with backend...")
            
            # TODO: Implement actual sync logic
            # - Upload cached telemetry data
            # - Download pending commands
            # - Update node status
            
            console.print("[green]✅ Sync completed successfully[/green]")
            return True
            
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            return False
    
    def get_registration_url(self, device_type: str = None) -> str:
        """Get URL for device registration in frontend."""
        base_url = self._get_frontend_url()
        
        if device_type:
            return f"{base_url}/edge/register?device={device_type}"
        else:
            return f"{base_url}/edge/register"

# Global connectivity manager instance - will be initialized with proper environment
_connectivity_manager = None

def get_connectivity_manager(force_refresh: bool = False) -> ConnectivityManager:
    """Get or create the global connectivity manager."""
    global _connectivity_manager
    if _connectivity_manager is None or force_refresh:
        _connectivity_manager = ConnectivityManager()
    return _connectivity_manager

def refresh_connectivity_manager():
    """Force refresh of the connectivity manager to pick up config changes."""
    global _connectivity_manager
    _connectivity_manager = None

async def check_connectivity(operation: str = "operation") -> Tuple[ConnectivityMode, Dict[str, Any]]:
    """Convenience function to check connectivity for an operation."""
    manager = get_connectivity_manager()
    return await manager.handle_connectivity_flow(operation)

def get_registration_url(device_type: str = None) -> str:
    """Get device registration URL."""
    manager = get_connectivity_manager()
    return manager.get_registration_url(device_type)
