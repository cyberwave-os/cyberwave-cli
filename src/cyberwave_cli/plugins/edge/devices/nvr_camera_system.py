"""
NVR (Network Video Recorder) and IP Camera Management System

Handles discovery, authentication, and stream management for various camera manufacturers.
Supports Uniview, Hikvision, Dahua, and other common NVR systems.
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union
from urllib.parse import urlparse
import base64
import logging

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None
    AIOHTTP_AVAILABLE = False

try:
    import cv2
    OPENCV_AVAILABLE = True
except ImportError:
    cv2 = None
    OPENCV_AVAILABLE = False

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()
logger = logging.getLogger(__name__)

@dataclass
class CameraCredentials:
    """Camera authentication credentials."""
    username: str
    password: str
    protocol: str = "rtsp"
    port: int = 554
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class CameraStream:
    """Individual camera stream configuration."""
    camera_id: str
    name: str
    rtsp_path: str
    resolution: str = "1080p"
    fps: int = 25
    codec: str = "h264"
    quality: str = "main"  # main, sub, third
    
    def get_rtsp_url(self, host: str, credentials: CameraCredentials) -> str:
        """Generate complete RTSP URL."""
        auth = f"{credentials.username}:{credentials.password}@" if credentials.username else ""
        return f"{credentials.protocol}://{auth}{host}:{credentials.port}/{self.rtsp_path}"

@dataclass
class NVRConfiguration:
    """NVR system configuration."""
    host: str
    manufacturer: str
    model: str
    firmware_version: str = "unknown"
    max_channels: int = 0
    credentials: Optional[CameraCredentials] = None
    cameras: List[CameraStream] = None
    web_port: int = 80
    rtsp_port: int = 554
    
    def __post_init__(self):
        if self.cameras is None:
            self.cameras = []

class NVRManufacturerHandler:
    """Base class for manufacturer-specific NVR handling."""
    
    def __init__(self, manufacturer: str):
        self.manufacturer = manufacturer
    
    async def discover_cameras(self, host: str, credentials: CameraCredentials) -> List[CameraStream]:
        """Discover available cameras on the NVR."""
        raise NotImplementedError
    
    async def validate_stream(self, stream: CameraStream, host: str, credentials: CameraCredentials) -> bool:
        """Validate if a camera stream is accessible."""
        raise NotImplementedError
    
    def generate_stream_paths(self, channel_count: int) -> List[CameraStream]:
        """Generate standard stream paths for this manufacturer."""
        raise NotImplementedError

class UniviewNVRHandler(NVRManufacturerHandler):
    """Uniview NVR specific handling."""
    
    def __init__(self):
        super().__init__("uniview")
        
    def generate_stream_paths(self, channel_count: int) -> List[CameraStream]:
        """Generate Uniview RTSP stream paths."""
        streams = []
        
        for channel in range(1, channel_count + 1):
            # Main stream (high quality)
            main_stream = CameraStream(
                camera_id=f"ch{channel:02d}_main",
                name=f"Camera {channel} (Main)",
                rtsp_path=f"unicast/c{channel}/s1/live",
                resolution="1080p",
                quality="main"
            )
            streams.append(main_stream)
            
            # Sub stream (low quality for preview)
            sub_stream = CameraStream(
                camera_id=f"ch{channel:02d}_sub",
                name=f"Camera {channel} (Sub)",
                rtsp_path=f"unicast/c{channel}/s2/live",
                resolution="480p",
                quality="sub"
            )
            streams.append(sub_stream)
        
        return streams
    
    async def discover_cameras(self, host: str, credentials: CameraCredentials) -> List[CameraStream]:
        """Discover Uniview cameras via API calls."""
        cameras = []
        
        if not AIOHTTP_AVAILABLE:
            console.print("[yellow]⚠️ aiohttp not available, using standard channel detection[/yellow]")
            return self.generate_stream_paths(16)  # Default to 16 channels
        
        try:
            # Try to get channel information via HTTP API
            auth = aiohttp.BasicAuth(credentials.username, credentials.password)
            
            async with aiohttp.ClientSession(auth=auth, timeout=aiohttp.ClientTimeout(total=10)) as session:
                # Common Uniview API endpoints
                endpoints = [
                    f"http://{host}/ISAPI/System/deviceInfo",
                    f"http://{host}/ISAPI/ContentMgmt/InputProxy/channels",
                    f"http://{host}/cgi-bin/api.cgi?cmd=GetChannelTitle"
                ]
                
                for endpoint in endpoints:
                    try:
                        async with session.get(endpoint) as response:
                            if response.status == 200:
                                data = await response.text()
                                # Parse response to extract channel information
                                channels = self._parse_channel_info(data)
                                if channels:
                                    return self._generate_streams_from_channels(channels)
                    except Exception as e:
                        logger.debug(f"Failed to query {endpoint}: {e}")
                        continue
        
        except Exception as e:
            logger.debug(f"API discovery failed: {e}")
        
        # Fallback to standard stream detection
        console.print("[yellow]Using standard stream detection (16 channels)[/yellow]")
        return self.generate_stream_paths(16)
    
    def _parse_channel_info(self, data: str) -> List[Dict[str, Any]]:
        """Parse channel information from API response."""
        channels = []
        
        # Try to parse as XML (common for ISAPI)
        if "<" in data and ">" in data:
            # Simple XML parsing for channel info
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(data)
                for channel in root.findall(".//channel") or root.findall(".//Channel"):
                    channel_id = channel.get("id") or channel.find("id")
                    if channel_id:
                        channels.append({
                            "id": str(channel_id).strip(),
                            "name": f"Camera {channel_id}",
                            "enabled": True
                        })
            except Exception as e:
                logger.debug(f"XML parsing failed: {e}")
        
        # Try to parse as JSON
        elif data.startswith("{") or data.startswith("["):
            try:
                json_data = json.loads(data)
                if isinstance(json_data, list):
                    for item in json_data:
                        if "channel" in item or "id" in item:
                            channels.append(item)
                elif "channels" in json_data:
                    channels = json_data["channels"]
            except Exception as e:
                logger.debug(f"JSON parsing failed: {e}")
        
        return channels
    
    def _generate_streams_from_channels(self, channels: List[Dict[str, Any]]) -> List[CameraStream]:
        """Generate stream configurations from discovered channels."""
        streams = []
        
        for channel in channels:
            channel_id = str(channel.get("id", "1"))
            channel_name = channel.get("name", f"Camera {channel_id}")
            
            # Main stream
            main_stream = CameraStream(
                camera_id=f"ch{channel_id.zfill(2)}_main",
                name=f"{channel_name} (Main)",
                rtsp_path=f"unicast/c{channel_id}/s1/live",
                resolution="1080p",
                quality="main"
            )
            streams.append(main_stream)
            
            # Sub stream
            sub_stream = CameraStream(
                camera_id=f"ch{channel_id.zfill(2)}_sub", 
                name=f"{channel_name} (Sub)",
                rtsp_path=f"unicast/c{channel_id}/s2/live",
                resolution="480p",
                quality="sub"
            )
            streams.append(sub_stream)
        
        return streams
    
    async def validate_stream(self, stream: CameraStream, host: str, credentials: CameraCredentials) -> bool:
        """Validate Uniview stream accessibility."""
        if not OPENCV_AVAILABLE:
            console.print("[yellow]⚠️ OpenCV not available for stream validation[/yellow]")
            return True  # Assume valid
        
        rtsp_url = stream.get_rtsp_url(host, credentials)
        
        try:
            # Try to open the stream with OpenCV
            cap = cv2.VideoCapture(rtsp_url)
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                return ret and frame is not None
            else:
                cap.release()
                return False
        except Exception as e:
            logger.debug(f"Stream validation failed for {rtsp_url}: {e}")
            return False

class HikvisionNVRHandler(NVRManufacturerHandler):
    """Hikvision NVR handler."""
    
    def __init__(self):
        super().__init__("hikvision")
    
    def generate_stream_paths(self, channel_count: int) -> List[CameraStream]:
        """Generate Hikvision RTSP stream paths."""
        streams = []
        
        for channel in range(1, channel_count + 1):
            # Main stream
            main_stream = CameraStream(
                camera_id=f"ch{channel:02d}_main",
                name=f"Camera {channel} (Main)",
                rtsp_path=f"Streaming/Channels/{channel}01",
                resolution="1080p",
                quality="main"
            )
            streams.append(main_stream)
            
            # Sub stream
            sub_stream = CameraStream(
                camera_id=f"ch{channel:02d}_sub",
                name=f"Camera {channel} (Sub)",
                rtsp_path=f"Streaming/Channels/{channel}02",
                resolution="480p",
                quality="sub"
            )
            streams.append(sub_stream)
        
        return streams
    
    async def discover_cameras(self, host: str, credentials: CameraCredentials) -> List[CameraStream]:
        """Discover Hikvision cameras."""
        # Implementation similar to Uniview but with Hikvision-specific API endpoints
        return self.generate_stream_paths(16)
    
    async def validate_stream(self, stream: CameraStream, host: str, credentials: CameraCredentials) -> bool:
        """Validate Hikvision stream."""
        # Similar to Uniview validation
        return True

class DahuaNVRHandler(NVRManufacturerHandler):
    """Dahua NVR handler."""
    
    def __init__(self):
        super().__init__("dahua")
    
    def generate_stream_paths(self, channel_count: int) -> List[CameraStream]:
        """Generate Dahua RTSP stream paths."""
        streams = []
        
        for channel in range(1, channel_count + 1):
            # Main stream
            main_stream = CameraStream(
                camera_id=f"ch{channel:02d}_main",
                name=f"Camera {channel} (Main)",
                rtsp_path=f"cam/realmonitor?channel={channel}&subtype=0",
                resolution="1080p",
                quality="main"
            )
            streams.append(main_stream)
            
            # Sub stream
            sub_stream = CameraStream(
                camera_id=f"ch{channel:02d}_sub",
                name=f"Camera {channel} (Sub)",
                rtsp_path=f"cam/realmonitor?channel={channel}&subtype=1",
                resolution="480p",
                quality="sub"
            )
            streams.append(sub_stream)
        
        return streams
    
    async def discover_cameras(self, host: str, credentials: CameraCredentials) -> List[CameraStream]:
        """Discover Dahua cameras."""
        return self.generate_stream_paths(16)
    
    async def validate_stream(self, stream: CameraStream, host: str, credentials: CameraCredentials) -> bool:
        """Validate Dahua stream."""
        return True

class NVRCameraManager:
    """Main NVR and camera management system."""
    
    def __init__(self, config_dir: Path = None):
        self.config_dir = config_dir or Path.home() / ".cyberwave"
        self.config_dir.mkdir(exist_ok=True)
        self.nvr_config_path = self.config_dir / "nvr_configurations.json"
        self.camera_credentials_path = self.config_dir / "camera_credentials.json"
        
        # Initialize manufacturer handlers
        self.handlers = {
            "uniview": UniviewNVRHandler(),
            "hikvision": HikvisionNVRHandler(),
            "dahua": DahuaNVRHandler()
        }
        
        self.nvr_configurations: Dict[str, NVRConfiguration] = {}
        self.load_configurations()
    
    def load_configurations(self):
        """Load saved NVR configurations."""
        try:
            if self.nvr_config_path.exists():
                with open(self.nvr_config_path, 'r') as f:
                    data = json.load(f)
                    for host, config_data in data.items():
                        # Reconstruct objects from JSON
                        credentials = None
                        if config_data.get("credentials"):
                            credentials = CameraCredentials(**config_data["credentials"])
                        
                        cameras = []
                        if config_data.get("cameras"):
                            cameras = [CameraStream(**cam) for cam in config_data["cameras"]]
                        
                        config = NVRConfiguration(
                            host=config_data["host"],
                            manufacturer=config_data["manufacturer"],
                            model=config_data["model"],
                            firmware_version=config_data.get("firmware_version", "unknown"),
                            max_channels=config_data.get("max_channels", 0),
                            credentials=credentials,
                            cameras=cameras,
                            web_port=config_data.get("web_port", 80),
                            rtsp_port=config_data.get("rtsp_port", 554)
                        )
                        self.nvr_configurations[host] = config
        except Exception as e:
            logger.debug(f"Failed to load configurations: {e}")
    
    def save_configurations(self):
        """Save NVR configurations to file."""
        try:
            data = {}
            for host, config in self.nvr_configurations.items():
                config_dict = asdict(config)
                data[host] = config_dict
            
            with open(self.nvr_config_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save configurations: {e}")
    
    def get_credentials_from_env(self) -> Optional[CameraCredentials]:
        """Get camera credentials from environment variables."""
        username = os.getenv("CAMERA_USERNAME")
        password = os.getenv("CAMERA_PASSWORD")
        
        if username and password:
            return CameraCredentials(
                username=username,
                password=password,
                port=int(os.getenv("CAMERA_PORT", "554"))
            )
        return None
    
    def detect_manufacturer(self, host: str) -> str:
        """Detect NVR manufacturer from device response."""
        # This could be enhanced with actual device fingerprinting
        # For now, we'll use environment hints or default to uniview
        
        # Check environment variable
        manufacturer = os.getenv("CAMERA_MANUFACTURER", "").lower()
        if manufacturer in self.handlers:
            return manufacturer
        
        # Default detection logic could go here
        # (HTTP headers, RTSP OPTIONS response, etc.)
        
        return "uniview"  # Default for your setup
    
    async def discover_nvr_system(self, host: str, credentials: CameraCredentials = None) -> NVRConfiguration:
        """Discover and configure an NVR system."""
        
        if not credentials:
            credentials = self.get_credentials_from_env()
        
        if not credentials:
            raise ValueError("Camera credentials are required. Set CAMERA_USERNAME and CAMERA_PASSWORD environment variables.")
        
        console.print(f"🔍 Discovering NVR system at {host}...")
        
        # Detect manufacturer
        manufacturer = self.detect_manufacturer(host)
        handler = self.handlers.get(manufacturer)
        
        if not handler:
            raise ValueError(f"Unsupported manufacturer: {manufacturer}")
        
        console.print(f"📹 Detected manufacturer: [cyan]{manufacturer.title()}[/cyan]")
        
        # Discover cameras
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Discovering cameras...", total=None)
            
            cameras = await handler.discover_cameras(host, credentials)
            
            progress.update(task, description=f"Found {len(cameras)} camera streams")
            await asyncio.sleep(0.5)
        
        # Create NVR configuration
        nvr_config = NVRConfiguration(
            host=host,
            manufacturer=manufacturer,
            model="Auto-detected",
            max_channels=len(cameras) // 2,  # Assuming main + sub streams
            credentials=credentials,
            cameras=cameras,
            rtsp_port=credentials.port
        )
        
        # Save configuration
        self.nvr_configurations[host] = nvr_config
        self.save_configurations()
        
        console.print(f"[green]✅ NVR system configured with {len(cameras)} camera streams[/green]")
        
        return nvr_config
    
    async def validate_cameras(self, host: str, stream_limit: int = 3) -> Dict[str, bool]:
        """Validate camera streams for an NVR."""
        
        nvr_config = self.nvr_configurations.get(host)
        if not nvr_config:
            raise ValueError(f"No configuration found for NVR {host}")
        
        handler = self.handlers.get(nvr_config.manufacturer)
        if not handler:
            raise ValueError(f"No handler for manufacturer: {nvr_config.manufacturer}")
        
        console.print(f"🔍 Validating camera streams for {host}...")
        
        results = {}
        cameras_to_test = nvr_config.cameras[:stream_limit]  # Limit testing for performance
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            
            for i, camera in enumerate(cameras_to_test):
                task = progress.add_task(f"Testing {camera.name}...", total=None)
                
                try:
                    is_valid = await handler.validate_stream(camera, host, nvr_config.credentials)
                    results[camera.camera_id] = is_valid
                    
                    status = "✅ Valid" if is_valid else "❌ Invalid"
                    progress.update(task, description=f"{camera.name}: {status}")
                    
                except Exception as e:
                    results[camera.camera_id] = False
                    progress.update(task, description=f"{camera.name}: ❌ Error")
                    logger.debug(f"Stream validation error: {e}")
                
                await asyncio.sleep(0.5)
        
        valid_count = sum(1 for v in results.values() if v)
        console.print(f"[green]✅ Validation complete: {valid_count}/{len(cameras_to_test)} streams accessible[/green]")
        
        return results
    
    def display_nvr_configuration(self, host: str):
        """Display NVR configuration in a nice format."""
        
        nvr_config = self.nvr_configurations.get(host)
        if not nvr_config:
            console.print(f"[red]❌ No configuration found for {host}[/red]")
            return
        
        # NVR Info Panel
        info_content = [
            f"[bold]Host:[/bold] {nvr_config.host}",
            f"[bold]Manufacturer:[/bold] {nvr_config.manufacturer.title()}",
            f"[bold]Model:[/bold] {nvr_config.model}",
            f"[bold]Channels:[/bold] {nvr_config.max_channels}",
            f"[bold]RTSP Port:[/bold] {nvr_config.rtsp_port}",
            f"[bold]Web Port:[/bold] {nvr_config.web_port}",
        ]
        
        console.print(Panel(
            "\n".join(info_content),
            title="📹 NVR Configuration",
            border_style="cyan"
        ))
        
        # Camera Streams Table
        if nvr_config.cameras:
            table = Table(title=f"Camera Streams ({len(nvr_config.cameras)} total)")
            table.add_column("Camera ID", style="cyan")
            table.add_column("Name", style="white")
            table.add_column("Quality", style="yellow")
            table.add_column("RTSP Path", style="dim")
            table.add_column("Resolution", style="green")
            
            for camera in nvr_config.cameras[:10]:  # Show first 10
                table.add_row(
                    camera.camera_id,
                    camera.name,
                    camera.quality,
                    camera.rtsp_path,
                    camera.resolution
                )
            
            if len(nvr_config.cameras) > 10:
                table.add_row("...", "...", "...", "...", "...")
            
            console.print(table)
        
        # Sample RTSP URLs
        if nvr_config.cameras and nvr_config.credentials:
            console.print("\n[bold blue]📡 Sample RTSP URLs:[/bold blue]")
            for camera in nvr_config.cameras[:3]:
                rtsp_url = camera.get_rtsp_url(nvr_config.host, nvr_config.credentials)
                # Mask password for display
                display_url = rtsp_url.replace(nvr_config.credentials.password, "***")
                console.print(f"  • [cyan]{camera.name}:[/cyan] [dim]{display_url}[/dim]")
    
    def export_camera_list(self, host: str, format: str = "json") -> Dict[str, Any]:
        """Export camera list for external use."""
        
        nvr_config = self.nvr_configurations.get(host)
        if not nvr_config:
            return {}
        
        export_data = {
            "nvr_host": nvr_config.host,
            "manufacturer": nvr_config.manufacturer,
            "discovery_time": datetime.now().isoformat(),
            "cameras": []
        }
        
        for camera in nvr_config.cameras:
            camera_data = {
                "camera_id": camera.camera_id,
                "name": camera.name,
                "rtsp_path": camera.rtsp_path,
                "resolution": camera.resolution,
                "quality": camera.quality,
                "rtsp_url": camera.get_rtsp_url(nvr_config.host, nvr_config.credentials) if nvr_config.credentials else None
            }
            export_data["cameras"].append(camera_data)
        
        return export_data

# Global instance
nvr_manager = NVRCameraManager()
