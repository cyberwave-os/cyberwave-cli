"""
Backend Integration for Edge Nodes

Handles automatic registration of edge nodes and devices with the Cyberwave backend.
Provides seamless integration without frontend coupling.
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import logging

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None
    HTTPX_AVAILABLE = False

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .node_identity import get_node_identity, node_identity_manager
from .connectivity import get_connectivity_manager, ConnectivityMode

logger = logging.getLogger(__name__)
console = Console()

class BackendRegistrationManager:
    """Manages registration of edge nodes and devices with the backend."""
    
    def __init__(self):
        self.config_dir = Path.home() / ".cyberwave"
        self.config_dir.mkdir(exist_ok=True)
        self.registration_cache_path = self.config_dir / "backend_registrations.json"
        self.device_cache_path = self.config_dir / "registered_devices.json"
        
        self.connectivity_manager = get_connectivity_manager()
        
    def _load_registration_cache(self) -> Dict[str, Any]:
        """Load cached registration data."""
        try:
            if self.registration_cache_path.exists():
                with open(self.registration_cache_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Failed to load registration cache: {e}")
        return {}
    
    def _save_registration_cache(self, data: Dict[str, Any]):
        """Save registration data to cache."""
        try:
            with open(self.registration_cache_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save registration cache: {e}")
    
    def _load_device_cache(self) -> Dict[str, Any]:
        """Load cached device registrations."""
        try:
            if self.device_cache_path.exists():
                with open(self.device_cache_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Failed to load device cache: {e}")
        return {"devices": [], "last_updated": None}
    
    def _save_device_cache(self, data: Dict[str, Any]):
        """Save device registrations to cache."""
        try:
            data["last_updated"] = datetime.now().isoformat()
            with open(self.device_cache_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save device cache: {e}")
    
    async def register_edge_node(self, 
                                project_id: Optional[str] = None,
                                environment_id: Optional[str] = None,
                                force: bool = False) -> Dict[str, Any]:
        """Register the edge node with the backend."""
        
        # Get node identity
        node_identity_obj = get_node_identity()
        # Convert to dict if it's an object
        if hasattr(node_identity_obj, '__dict__'):
            node_identity = {
                "node_id": node_identity_obj.node_id,
                "hostname": node_identity_obj.hostname,
                "platform": node_identity_obj.platform,
                "python_version": getattr(node_identity_obj, 'python_version', 'unknown'),
                "cyberwave_cli_version": getattr(node_identity_obj, 'version', 'unknown'),
                "network_info": {},
                "hardware_info": {}
            }
        else:
            node_identity = node_identity_obj
        
        node_id = node_identity["node_id"]
        
        # Check if already registered
        cache = self._load_registration_cache()
        if not force and cache.get("node_registered") and cache.get("node_id") == node_id:
            console.print(f"[green]✅ Node {node_id} already registered[/green]")
            return cache
        
        console.print(f"[blue]📡 Registering edge node {node_id} with backend...[/blue]")
        
        # Check connectivity
        try:
            mode, config = await self.connectivity_manager.check_connectivity("Node registration")
            
            if mode == ConnectivityMode.OFFLINE:
                return await self._register_node_offline(node_identity, project_id, environment_id)
            
        except Exception as e:
            logger.debug(f"Connectivity check failed: {e}")
            return await self._register_node_offline(node_identity, project_id, environment_id)
        
        # Try online registration
        try:
            return await self._register_node_online(node_identity, project_id, environment_id)
        except Exception as e:
            console.print(f"[yellow]⚠️ Online registration failed: {e}[/yellow]")
            return await self._register_node_offline(node_identity, project_id, environment_id)
    
    async def _register_node_online(self, 
                                   node_identity: Dict[str, Any],
                                   project_id: Optional[str],
                                   environment_id: Optional[str]) -> Dict[str, Any]:
        """Register node with backend API."""
        
        if not HTTPX_AVAILABLE:
            raise Exception("httpx not available for online registration")
        
        # Prepare registration data
        registration_data = {
            "node_id": node_identity["node_id"],
            "hostname": node_identity["hostname"],
            "platform": node_identity["platform"],
            "python_version": node_identity["python_version"],
            "cyberwave_cli_version": node_identity.get("cyberwave_cli_version", "unknown"),
            "capabilities": [
                "camera_discovery",
                "device_management", 
                "telemetry_collection",
                "command_execution"
            ],
            "status": "active",
            "last_seen": datetime.now().isoformat(),
            "metadata": {
                "registration_method": "cli_auto",
                "network_info": node_identity.get("network_info", {}),
                "hardware_info": node_identity.get("hardware_info", {})
            }
        }
        
        if project_id:
            registration_data["project_id"] = project_id
        if environment_id:
            registration_data["environment_id"] = environment_id
        
        # Make API call
        backend_url = self.connectivity_manager.backend_url
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # First, try to register or update the edge node
            try:
                response = await client.post(
                    f"{backend_url}/edge/nodes/register",
                    json=registration_data,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code in [200, 201]:
                    result = response.json()
                    console.print(f"[green]✅ Node registered successfully[/green]")
                    
                    # Cache the registration
                    cache_data = {
                        "node_registered": True,
                        "node_id": node_identity["node_id"],
                        "backend_node_id": result.get("id"),
                        "registration_time": datetime.now().isoformat(),
                        "project_id": project_id,
                        "environment_id": environment_id,
                        "backend_url": backend_url
                    }
                    
                    self._save_registration_cache(cache_data)
                    node_identity_manager.mark_backend_registered()
                    
                    return cache_data
                
                else:
                    raise Exception(f"Registration failed: {response.status_code} {response.text}")
                    
            except httpx.RequestError as e:
                raise Exception(f"Network error during registration: {e}")
    
    async def _register_node_offline(self, 
                                    node_identity: Dict[str, Any],
                                    project_id: Optional[str],
                                    environment_id: Optional[str]) -> Dict[str, Any]:
        """Handle offline node registration."""
        
        console.print("[yellow]📝 Registering node in offline mode[/yellow]")
        
        # Create offline registration record
        offline_data = {
            "node_registered": False,
            "offline_mode": True,
            "node_id": node_identity["node_id"],
            "registration_time": datetime.now().isoformat(),
            "project_id": project_id,
            "environment_id": environment_id,
            "pending_registration": {
                "node_data": node_identity,
                "registration_payload": {
                    "node_id": node_identity["node_id"],
                    "hostname": node_identity["hostname"],
                    "platform": node_identity["platform"],
                    "status": "active",
                    "capabilities": ["camera_discovery", "device_management"],
                    "project_id": project_id,
                    "environment_id": environment_id
                }
            }
        }
        
        self._save_registration_cache(offline_data)
        
        # Show offline registration info
        frontend_url = self.connectivity_manager._get_frontend_url()
        console.print(f"\n[cyan]🌐 Manual Registration Required:[/cyan]")
        console.print(f"1. Open: [link]{frontend_url}/edge/register[/link]")
        console.print(f"2. Enter Node ID: [bold]{node_identity['node_id']}[/bold]")
        console.print(f"3. Complete registration in the web interface")
        
        return offline_data
    
    async def register_device(self,
                            device_type: str,
                            device_name: str,
                            device_config: Dict[str, Any],
                            project_id: Optional[str] = None,
                            environment_id: Optional[str] = None) -> Dict[str, Any]:
        """Register a device with the backend."""
        
        # Ensure node is registered first
        node_registration = await self.register_edge_node(project_id, environment_id)
        
        console.print(f"[blue]📱 Registering device: {device_name} ({device_type})[/blue]")
        
        # Prepare device data
        device_data = {
            "device_id": f"{node_registration['node_id']}_{device_type}_{uuid.uuid4().hex[:8]}",
            "name": device_name,
            "type": device_type,
            "node_id": node_registration["node_id"],
            "status": "online",
            "configuration": device_config,
            "capabilities": device_config.get("capabilities", []),
            "metadata": {
                "registration_method": "cli_auto",
                "registration_time": datetime.now().isoformat(),
                "source": "edge_node_discovery"
            }
        }
        
        if project_id:
            device_data["project_id"] = project_id
        if environment_id:
            device_data["environment_id"] = environment_id
        
        # Try online registration
        try:
            mode, config = await self.connectivity_manager.check_connectivity("Device registration")
            
            if mode != ConnectivityMode.OFFLINE:
                return await self._register_device_online(device_data)
            
        except Exception as e:
            logger.debug(f"Connectivity check failed: {e}")
        
        # Fallback to offline registration
        return await self._register_device_offline(device_data)
    
    async def _register_device_online(self, device_data: Dict[str, Any]) -> Dict[str, Any]:
        """Register device with backend API."""
        
        if not HTTPX_AVAILABLE:
            raise Exception("httpx not available for online registration")
        
        backend_url = self.connectivity_manager.backend_url
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{backend_url}/devices/register",
                    json=device_data,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code in [200, 201]:
                    result = response.json()
                    console.print(f"[green]✅ Device registered: {device_data['name']}[/green]")
                    
                    # Update device cache
                    cache = self._load_device_cache()
                    cache["devices"].append({
                        "device_id": device_data["device_id"],
                        "backend_id": result.get("id"),
                        "name": device_data["name"],
                        "type": device_data["type"],
                        "registration_time": datetime.now().isoformat(),
                        "status": "registered"
                    })
                    self._save_device_cache(cache)
                    
                    return result
                
                else:
                    raise Exception(f"Device registration failed: {response.status_code} {response.text}")
                    
            except httpx.RequestError as e:
                raise Exception(f"Network error during device registration: {e}")
    
    async def _register_device_offline(self, device_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle offline device registration."""
        
        console.print(f"[yellow]📝 Caching device registration: {device_data['name']}[/yellow]")
        
        # Add to offline cache
        cache = self._load_device_cache()
        cache["devices"].append({
            "device_id": device_data["device_id"],
            "name": device_data["name"],
            "type": device_data["type"],
            "registration_time": datetime.now().isoformat(),
            "status": "pending_registration",
            "offline_data": device_data
        })
        self._save_device_cache(cache)
        
        return {"status": "cached", "device_id": device_data["device_id"]}
    
    async def register_discovered_cameras(self, 
                                        camera_list: List[Dict[str, Any]],
                                        project_id: Optional[str] = None,
                                        environment_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Register multiple discovered cameras."""
        
        console.print(f"[blue]📷 Registering {len(camera_list)} discovered cameras...[/blue]")
        
        results = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            
            for i, camera in enumerate(camera_list):
                task = progress.add_task(f"Registering camera {i+1}/{len(camera_list)}...", total=None)
                
                try:
                    # Prepare camera-specific configuration
                    camera_config = {
                        "ip_address": camera.get("ip_address"),
                        "port": camera.get("port", 80),
                        "protocol": camera.get("protocol", "http"),
                        "camera_type": camera.get("camera_type", "ip_camera"),
                        "capabilities": ["video_streaming", "motion_detection"],
                        "detection_method": camera.get("detection_method", "network_scan"),
                        "access_url": camera.get("access_url")
                    }
                    
                    device_name = f"Camera_{camera['ip_address'].replace('.', '_')}"
                    
                    result = await self.register_device(
                        device_type="camera/ip",
                        device_name=device_name,
                        device_config=camera_config,
                        project_id=project_id,
                        environment_id=environment_id
                    )
                    
                    results.append(result)
                    progress.update(task, description=f"✅ Registered {device_name}")
                    
                except Exception as e:
                    logger.error(f"Failed to register camera {camera.get('ip_address')}: {e}")
                    progress.update(task, description=f"❌ Failed {camera.get('ip_address')}")
                    results.append({"error": str(e), "camera": camera})
                
                await asyncio.sleep(0.1)  # Small delay between registrations
        
        successful = len([r for r in results if "error" not in r])
        console.print(f"[green]✅ Successfully registered {successful}/{len(camera_list)} cameras[/green]")
        
        return results
    
    def show_registration_status(self):
        """Display current registration status."""
        
        console.print("[bold blue]📊 Edge Node Registration Status[/bold blue]")
        
        # Node registration status
        cache = self._load_registration_cache()
        node_table = Table(title="Node Registration")
        node_table.add_column("Property", style="cyan")
        node_table.add_column("Value", style="white")
        
        if cache.get("node_registered"):
            node_table.add_row("Status", "[green]✅ Registered[/green]")
            node_table.add_row("Node ID", cache.get("node_id", "Unknown"))
            node_table.add_row("Backend ID", str(cache.get("backend_node_id", "Unknown")))
            node_table.add_row("Registration Time", cache.get("registration_time", "Unknown"))
        elif cache.get("offline_mode"):
            node_table.add_row("Status", "[yellow]⏳ Offline Mode[/yellow]")
            node_table.add_row("Node ID", cache.get("node_id", "Unknown"))
            node_table.add_row("Registration Time", cache.get("registration_time", "Unknown"))
        else:
            node_table.add_row("Status", "[red]❌ Not Registered[/red]")
        
        console.print(node_table)
        
        # Device registration status
        device_cache = self._load_device_cache()
        devices = device_cache.get("devices", [])
        
        if devices:
            console.print(f"\n[bold blue]📱 Registered Devices ({len(devices)})[/bold blue]")
            device_table = Table()
            device_table.add_column("Device Name", style="cyan")
            device_table.add_column("Type", style="white")
            device_table.add_column("Status", style="white")
            device_table.add_column("Registration Time", style="dim")
            
            for device in devices:
                status_color = "green" if device.get("status") == "registered" else "yellow"
                status_text = device.get("status", "unknown")
                
                device_table.add_row(
                    device.get("name", "Unknown"),
                    device.get("type", "Unknown"),
                    f"[{status_color}]{status_text}[/{status_color}]",
                    device.get("registration_time", "Unknown")
                )
            
            console.print(device_table)
        else:
            console.print("\n[dim]No devices registered yet[/dim]")
        
        # Next steps
        console.print(f"\n[bold blue]🔗 Frontend Access:[/bold blue]")
        frontend_url = self.connectivity_manager._get_frontend_url()
        console.print(f"View devices: [link]{frontend_url}/devices[/link]")
        if cache.get("project_id"):
            console.print(f"Project devices: [link]{frontend_url}/project/{cache['project_id']}/devices[/link]")

# Global instance
backend_registration_manager = BackendRegistrationManager()
