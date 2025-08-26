"""
Node Identity Management for Cyberwave Edge Nodes

Handles unique node ID generation, caching, and registration workflow.
"""

import json
import uuid
import platform
import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
import logging

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

@dataclass
class NodeIdentity:
    """Node identity information."""
    node_id: str
    node_name: str
    created_at: str
    platform: str
    architecture: str
    hostname: str
    mac_address: str
    installation_id: str
    version: str = "0.11.5"
    last_seen: Optional[str] = None
    registered_backend: bool = False
    registration_token: Optional[str] = None

class NodeIdentityManager:
    """Manages node identity creation, caching, and registration."""
    
    def __init__(self, config_dir: Path = None):
        self.config_dir = config_dir or Path.home() / ".cyberwave"
        self.config_dir.mkdir(exist_ok=True)
        
        self.node_identity_path = self.config_dir / "node_identity.json"
        self._node_identity: Optional[NodeIdentity] = None
        
        # Load existing identity or create new one
        self._load_or_create_identity()
    
    def _load_or_create_identity(self) -> NodeIdentity:
        """Load existing node identity or create a new one."""
        
        if self.node_identity_path.exists():
            try:
                self._node_identity = self._load_identity()
                # Update last_seen
                self._node_identity.last_seen = datetime.now().isoformat()
                self._save_identity()
                logger.info(f"Loaded existing node identity: {self._node_identity.node_id}")
            except Exception as e:
                logger.warning(f"Failed to load existing identity: {e}, creating new one")
                self._node_identity = self._create_new_identity()
        else:
            self._node_identity = self._create_new_identity()
            logger.info(f"Created new node identity: {self._node_identity.node_id}")
        
        return self._node_identity
    
    def _create_new_identity(self) -> NodeIdentity:
        """Create a new node identity."""
        
        # Generate unique node ID
        node_id = self._generate_node_id()
        
        # Get system information
        hostname = platform.node()
        platform_info = platform.system()
        architecture = platform.machine()
        mac_address = self._get_mac_address()
        
        # Generate installation ID (unique per installation)
        installation_id = str(uuid.uuid4())
        
        # Create node name
        node_name = f"{hostname}-edge-{node_id[:8]}"
        
        identity = NodeIdentity(
            node_id=node_id,
            node_name=node_name,
            created_at=datetime.now().isoformat(),
            platform=platform_info,
            architecture=architecture,
            hostname=hostname,
            mac_address=mac_address,
            installation_id=installation_id,
            last_seen=datetime.now().isoformat()
        )
        
        # Save the new identity
        self._node_identity = identity
        self._save_identity()
        
        # Display welcome message
        self._display_new_node_welcome()
        
        return identity
    
    def _generate_node_id(self) -> str:
        """Generate a unique node ID."""
        
        # Create a unique ID based on:
        # - Current timestamp
        # - Random UUID
        # - System information
        
        timestamp = str(int(time.time() * 1000))  # milliseconds
        random_uuid = str(uuid.uuid4())
        system_info = f"{platform.node()}{platform.system()}{platform.machine()}"
        
        # Create hash from combined information
        combined = f"{timestamp}{random_uuid}{system_info}"
        hash_obj = hashlib.sha256(combined.encode())
        
        # Use first 16 characters of hash with timestamp prefix
        node_id = f"edge_{timestamp[-6:]}{hash_obj.hexdigest()[:10]}"
        
        return node_id
    
    def _get_mac_address(self) -> str:
        """Get the MAC address of the primary network interface."""
        try:
            import uuid
            mac = uuid.getnode()
            mac_hex = ':'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))
            return mac_hex
        except Exception:
            return "unknown"
    
    def _display_new_node_welcome(self):
        """Display welcome message for new node."""
        
        welcome_content = [
            f"[bold green]🎉 New Edge Node Created![/bold green]",
            "",
            f"[bold]Node ID:[/bold] [cyan]{self._node_identity.node_id}[/cyan]",
            f"[bold]Node Name:[/bold] [white]{self._node_identity.node_name}[/white]",
            f"[bold]Platform:[/bold] [dim]{self._node_identity.platform} ({self._node_identity.architecture})[/dim]",
            "",
            "[bold blue]Next Steps:[/bold blue]",
            "1. 🌐 Register this node in your backend (for online mode)",
            "2. 🔧 Configure devices and sensors", 
            "3. 🚀 Start edge processing",
            "",
            f"[bold]Registration URL:[/bold] [link]{self._get_registration_url()}[/link]",
            "",
            "[dim]Node identity cached locally for future use[/dim]"
        ]
        
        console.print(Panel(
            "\n".join(welcome_content),
            title="🤖 Edge Node Initialized",
            border_style="green"
        ))
    
    def _get_registration_url(self) -> str:
        """Get the registration URL for this node."""
        # This would be configurable based on environment
        base_url = "https://app.cyberwave.dev"
        return f"{base_url}/edge/register?node_id={self._node_identity.node_id}"
    
    def _load_identity(self) -> NodeIdentity:
        """Load node identity from cache."""
        with open(self.node_identity_path, 'r') as f:
            data = json.load(f)
            return NodeIdentity(**data)
    
    def _save_identity(self):
        """Save node identity to cache."""
        try:
            with open(self.node_identity_path, 'w') as f:
                json.dump(asdict(self._node_identity), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save node identity: {e}")
    
    def get_node_identity(self) -> NodeIdentity:
        """Get the current node identity."""
        return self._node_identity
    
    def get_node_id(self) -> str:
        """Get the node ID."""
        return self._node_identity.node_id
    
    def get_node_name(self) -> str:
        """Get the node name."""
        return self._node_identity.node_name
    
    def mark_backend_registered(self, registration_token: str = None):
        """Mark the node as registered with backend."""
        self._node_identity.registered_backend = True
        self._node_identity.registration_token = registration_token
        self._node_identity.last_seen = datetime.now().isoformat()
        self._save_identity()
        
        console.print(f"[green]✅ Node {self._node_identity.node_id} marked as registered[/green]")
    
    def is_registered_with_backend(self) -> bool:
        """Check if node is registered with backend."""
        return self._node_identity.registered_backend
    
    def update_last_seen(self):
        """Update the last seen timestamp."""
        self._node_identity.last_seen = datetime.now().isoformat()
        self._save_identity()
    
    def display_node_info(self, detailed: bool = False):
        """Display current node information."""
        
        info_table = Table.grid(padding=1)
        info_table.add_column("Property", style="cyan")
        info_table.add_column("Value", style="white")
        
        info_table.add_row("Node ID", self._node_identity.node_id)
        info_table.add_row("Node Name", self._node_identity.node_name)
        info_table.add_row("Status", 
                          "[green]Registered[/green]" if self._node_identity.registered_backend 
                          else "[yellow]Local Only[/yellow]")
        info_table.add_row("Created", self._node_identity.created_at[:19])
        info_table.add_row("Last Seen", self._node_identity.last_seen[:19] if self._node_identity.last_seen else "Unknown")
        
        if detailed:
            info_table.add_row("Platform", f"{self._node_identity.platform} ({self._node_identity.architecture})")
            info_table.add_row("Hostname", self._node_identity.hostname)
            info_table.add_row("Installation ID", self._node_identity.installation_id)
            info_table.add_row("Version", self._node_identity.version)
            if self._node_identity.registration_token:
                token_preview = f"{self._node_identity.registration_token[:8]}..."
                info_table.add_row("Token", f"[dim]{token_preview}[/dim]")
        
        console.print(Panel(info_table, title="🤖 Node Identity"))
        
        if not self._node_identity.registered_backend:
            console.print(f"\n💡 [dim]Register at: {self._get_registration_url()}[/dim]")
    
    def export_node_info(self) -> Dict[str, Any]:
        """Export node information for registration."""
        return {
            "node_id": self._node_identity.node_id,
            "node_name": self._node_identity.node_name,
            "platform": self._node_identity.platform,
            "architecture": self._node_identity.architecture,
            "hostname": self._node_identity.hostname,
            "mac_address": self._node_identity.mac_address,
            "installation_id": self._node_identity.installation_id,
            "version": self._node_identity.version,
            "created_at": self._node_identity.created_at,
            "capabilities": [
                "camera_processing",
                "computer_vision", 
                "telemetry",
                "offline_operation"
            ]
        }
    
    def reset_identity(self) -> NodeIdentity:
        """Reset node identity (creates a new one)."""
        
        console.print("[yellow]⚠️ Resetting node identity...[/yellow]")
        
        # Remove existing identity file
        if self.node_identity_path.exists():
            self.node_identity_path.unlink()
        
        # Create new identity
        new_identity = self._create_new_identity()
        
        console.print(f"[green]✅ New node identity created: {new_identity.node_id}[/green]")
        
        return new_identity

# Global node identity manager
node_identity_manager = NodeIdentityManager()

def get_node_id() -> str:
    """Get the current node ID."""
    return node_identity_manager.get_node_id()

def get_node_identity() -> NodeIdentity:
    """Get the current node identity."""
    return node_identity_manager.get_node_identity()

def mark_node_registered(registration_token: str = None):
    """Mark node as registered with backend."""
    node_identity_manager.mark_backend_registered(registration_token)

def display_node_info(detailed: bool = False):
    """Display node information."""
    node_identity_manager.display_node_info(detailed)

def export_node_info() -> Dict[str, Any]:
    """Export node information for registration."""
    return node_identity_manager.export_node_info()
