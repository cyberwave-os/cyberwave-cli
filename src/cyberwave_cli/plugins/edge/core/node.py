"""
Edge Node Core Implementation

The main EdgeNode class that orchestrates all edge node functionality.
Provides a clean interface for starting, stopping, and managing edge nodes.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Dict, Any, List

from ..config.manager import ConfigManager
from ..config.schema import EdgeConfig
from ..services.cloud_client import CloudClient
from ..services.health_monitor import HealthMonitor
from ..drivers.manager import DriverManager
from ..processors.manager import ProcessorManager
from ..utils.logger import setup_logging

logger = logging.getLogger(__name__)

class EdgeNode:
    """
    Main edge node orchestrator.
    
    Manages all edge node components including:
    - Configuration management
    - Device drivers
    - Processors
    - Cloud connectivity
    - Health monitoring
    """
    
    def __init__(self, 
                 config_path: Optional[str] = None,
                 node_id: Optional[str] = None,
                 backend_url: Optional[str] = None,
                 access_token: Optional[str] = None):
        self.node_id = node_id
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        # Core components
        self.config_manager: Optional[ConfigManager] = None
        self.cloud_client: Optional[CloudClient] = None
        self.health_monitor: Optional[HealthMonitor] = None
        self.driver_manager: Optional[DriverManager] = None
        self.processor_manager: Optional[ProcessorManager] = None
        
        # Configuration
        self._config_path = config_path
        self._backend_url = backend_url
        self._access_token = access_token
        self._edge_config: Optional[EdgeConfig] = None
        
        # Tasks
        self._main_task: Optional[asyncio.Task] = None
        self._background_tasks: List[asyncio.Task] = []

    async def initialize(self) -> None:
        """Initialize all edge node components."""
        logger.info(f"Initializing edge node {self.node_id}")
        
        try:
            # Initialize configuration manager
            await self._setup_configuration()
            
            # Setup logging based on configuration
            setup_logging(
                level=self._edge_config.log_level.value,
                node_id=self.node_id
            )
            
            # Initialize core services
            await self._setup_cloud_client()
            await self._setup_health_monitor()
            await self._setup_driver_manager()
            await self._setup_processor_manager()
            await self._setup_video_proxy()
            
            # Register signal handlers
            self._setup_signal_handlers()
            
            logger.info("Edge node initialization complete")
            
        except Exception as e:
            logger.error(f"Failed to initialize edge node: {e}")
            await self.shutdown()
            raise

    async def _setup_configuration(self) -> None:
        """Setup configuration management."""
        # Convert config_path to Path object if it's a string
        config_file = None
        if self._config_path:
            config_file = self._config_path if isinstance(self._config_path, Path) else Path(self._config_path)
        
        self.config_manager = ConfigManager(
            node_id=self.node_id,
            backend_url=self._backend_url,
            access_token=self._access_token,
            config_file=config_file
        )
        
        await self.config_manager.initialize()
        self._edge_config = self.config_manager.to_edge_config()
        
        # Update node_id if provided by config
        if not self.node_id and self._edge_config.node_id:
            self.node_id = self._edge_config.node_id

    async def _setup_cloud_client(self) -> None:
        """Setup cloud connectivity."""
        self.cloud_client = CloudClient(
            backend_url=self._edge_config.network.backend_url,
            auth_config=self._edge_config.auth,
            device_config=self._edge_config.device,
            node_id=self.node_id
        )
        
        await self.cloud_client.initialize()

    async def _setup_health_monitor(self) -> None:
        """Setup health monitoring."""
        # Extract values from health config
        health_config = self._edge_config.health
        self.health_monitor = HealthMonitor(
            check_interval=getattr(health_config, 'check_interval', 30.0),
            enable_detailed_metrics=getattr(health_config, 'enable_detailed_metrics', True)
        )
        
        await self.health_monitor.start()

    async def _setup_driver_manager(self) -> None:
        """Setup device driver management."""
        self.driver_manager = DriverManager()
        
        # Load existing devices from backend
        await self._load_backend_devices()
        
        # Start all registered drivers
        await self.driver_manager.start_all()
    
    async def _load_backend_devices(self) -> None:
        """Load existing devices from backend and initialize camera systems."""
        if not self.cloud_client:
            logger.warning("No cloud client available, skipping backend device loading")
            return
        
        try:
            logger.info("Loading existing devices from backend...")
            devices = await self.cloud_client.fetch_node_devices()
            
            if not devices:
                logger.info("No existing devices found in backend")
                return
            
            logger.info(f"Found {len(devices)} existing devices, initializing systems...")
            
            # Initialize camera devices list for video proxy
            self.camera_devices = []
            
            for device in devices:
                await self._process_backend_device(device)
                
        except Exception as e:
            logger.error(f"Failed to load backend devices: {e}")
    
    async def _process_backend_device(self, device: Dict[str, Any]) -> None:
        """Process a single device from the backend."""
        try:
            device_type = device.get("device_type", "unknown")
            device_name = device.get("name", "Unknown Device")
            device_id = device.get("id")
            
            logger.info(f"Processing device: {device_name} (type: {device_type})")
            
            # Handle camera/NVR devices using the existing NVR system
            if device_type in ["camera", "ip_camera", "nvr", "nvr_system", "sensor/camera", "camera/ip"]:
                await self._initialize_camera_device(device)
                
            elif device_type in ["robot", "so-101", "robotic_arm"]:
                logger.info(f"Robot device detected: {device_name} (not yet implemented)")
                
            elif device_type in ["drone", "tello"]:
                logger.info(f"Drone device detected: {device_name} (not yet implemented)")
                
            else:
                logger.warning(f"Unknown device type: {device_type}, skipping")
            
        except Exception as e:
            logger.error(f"Failed to process device {device.get('name', 'unknown')}: {e}")
    
    async def _initialize_camera_device(self, device: Dict[str, Any]) -> None:
        """Initialize a camera/NVR device using the existing NVR system."""
        try:
            device_name = device.get("name", "Unknown Camera")
            config = device.get("config", {})
            connection_string = device.get("connection_string", "")
            
            logger.info(f"Initializing camera device: {device_name}")
            logger.info(f"Device config: {config}")
            logger.info(f"Connection string: {connection_string}")
            
            # Extract connection details from config or connection_string
            ip_address = config.get("ip_address")
            username = config.get("username", "")
            password = ""
            
            # Extract credentials from connection_string if available
            if "://" in connection_string and "@" in connection_string:
                try:
                    auth_part = connection_string.split("://")[1].split("@")[0]
                    if ":" in auth_part:
                        username, password = auth_part.split(":", 1)
                        # Extract IP from connection string if not in config
                        if not ip_address:
                            ip_address = connection_string.split("@")[1].split(":")[0]
                except Exception as e:
                    logger.warning(f"Could not parse credentials from connection_string: {e}")
            
            if not ip_address:
                logger.warning(f"No IP address found for device {device_name}")
                return
            
            # Extract streams from config.cameras array or config.streams
            stream_paths = []
            
            # Check for cameras array (new format)
            cameras = config.get("cameras", [])
            if cameras:
                logger.info(f"Found {len(cameras)} cameras in config")
                for camera in cameras:
                    camera_path = camera.get("path", "")
                    if camera_path:
                        stream_paths.append(camera_path)
                        logger.info(f"  Camera: {camera.get('name', 'Unknown')} -> {camera_path}")
            
            # Fallback to streams array (old format)
            if not stream_paths:
                streams = config.get("streams", [])
                if streams:
                    stream_paths = streams
                    logger.info(f"Found {len(streams)} streams in legacy format")
            
            if not stream_paths:
                logger.warning(f"No camera streams found for device {device_name}")
                return
            
            # Create stream URLs
            port = config.get("port", 554)
            stream_urls = []
            
            for stream_path in stream_paths:
                if username and password:
                    rtsp_url = f"rtsp://{username}:{password}@{ip_address}:{port}/{stream_path}"
                else:
                    rtsp_url = f"rtsp://{ip_address}:{port}/{stream_path}"
                stream_urls.append(rtsp_url)
            
            logger.info(f"Generated {len(stream_urls)} stream URLs for {device_name}")
            for i, url in enumerate(stream_urls[:3], 1):  # Log first 3 URLs
                # Mask password for logging
                log_url = url.replace(password, "***") if password else url
                logger.info(f"  Stream {i}: {log_url}")
            
            # Store camera device for video proxy
            camera_device = {
                'id': device.get("id"),
                'name': device_name,
                'ip_address': ip_address,
                'stream_urls': stream_urls,
                'device_type': device.get("device_type"),
                'manufacturer': device.get("manufacturer", "unknown")
            }
            self.camera_devices.append(camera_device)
            
            logger.info(f"✅ Initialized camera device: {device_name} with {len(stream_urls)} streams")
            
        except Exception as e:
            logger.error(f"Failed to initialize camera device {device.get('name', 'unknown')}: {e}")

    async def _setup_processor_manager(self) -> None:
        """Setup processor management."""
        self.processor_manager = ProcessorManager()
        
        # Start all registered processors
        await self.processor_manager.start_all()
    
    async def _setup_video_proxy(self) -> None:
        """Setup video proxy service for camera streams."""
        try:
            # Check if we have any camera devices loaded from backend
            if not hasattr(self, 'camera_devices') or not self.camera_devices:
                logger.info("No camera devices found, skipping video proxy setup")
                self.video_proxy = None
                return
            
            # Prepare camera configurations for video proxy
            proxy_camera_configs = []
            
            for camera_device in self.camera_devices:
                device_name = camera_device['name']
                stream_urls = camera_device['stream_urls']
                device_id = camera_device['id']
                
                logger.info(f"Setting up proxy for camera: {device_name} with {len(stream_urls)} streams")
                
                # Create proxy configs for each stream
                # Use the actual camera ID from the device config for proper mapping
                device_config = camera_device.get('config', {})
                cameras_config = device_config.get('cameras', [])
                
                for i, stream_url in enumerate(stream_urls):
                    # Get the actual camera info from device config if available
                    camera_info = cameras_config[i] if i < len(cameras_config) else {}
                    camera_id = camera_info.get('id', i + 1)  # Use actual camera ID or fallback to index
                    camera_name = camera_info.get('name', f"Camera {i + 1}")
                    
                    # Create a unique but predictable ID for the video proxy
                    # Format: camera_id (ensures frontend compatibility)
                    proxy_camera_id = camera_id
                    
                    camera_config = {
                        'id': proxy_camera_id,
                        'name': f"{device_name} - {camera_name}" if len(stream_urls) > 1 else device_name,
                        'rtsp_url': stream_url,
                        'backend_device_id': device_id,  # Keep reference to backend device
                        'camera_index': i,  # Keep track of stream index
                        'original_camera_id': camera_id  # Original camera ID from NVR
                    }
                    proxy_camera_configs.append(camera_config)
                    
                    # Mask password for logging
                    log_url = stream_url
                    if "@" in log_url and ":" in log_url.split("@")[0]:
                        parts = log_url.split("://")
                        if len(parts) == 2:
                            protocol = parts[0]
                            rest = parts[1]
                            if "@" in rest:
                                auth_part = rest.split("@")[0]
                                host_part = rest.split("@")[1]
                                if ":" in auth_part:
                                    username = auth_part.split(":")[0]
                                    log_url = f"{protocol}://{username}:***@{host_part}"
                    
                    logger.info(f"Added camera config: {camera_config['name']} -> {log_url}")
            
            if not proxy_camera_configs:
                logger.info("No valid camera streams found, skipping video proxy setup")
                self.video_proxy = None
                return

            # Validate proxy camera configs before proceeding (resilient approach)
            valid_configs = []
            for config in proxy_camera_configs:
                try:
                    # Ensure required fields are present
                    if not all(key in config for key in ['id', 'name', 'rtsp_url']):
                        logger.warning(f"Invalid camera config missing required fields: {config}")
                        continue
                    
                    # Validate RTSP URL format
                    if not config['rtsp_url'].startswith('rtsp://'):
                        logger.warning(f"Invalid RTSP URL format: {config['rtsp_url']}")
                        continue
                    
                    # Ensure ID is valid (not None, empty, etc.)
                    if config['id'] is None or str(config['id']).strip() == '':
                        logger.warning(f"Invalid camera ID: {config['id']}")
                        continue
                    
                    valid_configs.append(config)
                    logger.info(f"✅ Validated camera config: {config['name']} (ID: {config['id']})")
                
                except Exception as e:
                    logger.error(f"Error validating camera config: {e}")
                    continue

            if not valid_configs:
                logger.error("No valid camera configurations found for video proxy")
                self.video_proxy = None
                return

            # Update proxy_camera_configs to only include valid ones
            proxy_camera_configs = valid_configs
            
            logger.info(f"Setting up video proxy for {len(self.camera_devices)} camera devices with {len(valid_configs)} valid streams")
            
            # Import video proxy service
            from ..services.video_proxy import VideoProxyService
            
            # Initialize video proxy service
            proxy_port = 8095  # Default video proxy port
            backend_url = self.cloud_client.backend_url if self.cloud_client else "http://localhost:8000"
            
            # Get authentication token from cloud client
            auth_token = None
            if self.cloud_client and hasattr(self.cloud_client, '_client') and self.cloud_client._client:
                auth_header = self.cloud_client._client.headers.get('Authorization')
                if auth_header:
                    auth_token = auth_header
            
            self.video_proxy = VideoProxyService(
                backend_url=backend_url,
                node_id=self.node_id,
                proxy_port=proxy_port,
                auth_token=auth_token
            )
            
            # Initialize streams
            await self.video_proxy.initialize_streams(proxy_camera_configs)
            
            # Start video captures
            self.video_proxy.start_all_streams()
            
            # Create and start web server
            from aiohttp import web
            app = self.video_proxy.create_web_app()
            
            runner = web.AppRunner(app)
            await runner.setup()
            
            site = web.TCPSite(runner, '0.0.0.0', proxy_port)
            await site.start()
            
            # Store runner for cleanup
            self.video_proxy_runner = runner
            
            # Register proxy service with backend
            try:
                registration_success = await self.video_proxy.register_with_backend()
                if registration_success:
                    logger.info("Video proxy service registered with backend")
                else:
                    logger.warning("Failed to register video proxy with backend (continuing anyway)")
            except Exception as e:
                logger.warning(f"Failed to register video proxy with backend: {e}")
            
            logger.info(f"Video proxy service started on http://localhost:{proxy_port}")
            
        except Exception as e:
            logger.error(f"Failed to setup video proxy: {e}")
            self.video_proxy = None

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        if sys.platform != "win32":
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._signal_handler)

    def _signal_handler(self) -> None:
        """Handle shutdown signals."""
        logger.info("Received shutdown signal")
        self._shutdown_event.set()

    async def run(self) -> None:
        """Run the edge node."""
        if self._running:
            logger.warning("Edge node is already running")
            return
        
        try:
            self._running = True
            logger.info("Starting edge node")
            
            # Start background services
            await self._start_background_services()
            
            # Start main loop
            self._main_task = asyncio.create_task(self._main_loop())
            
            # Wait for shutdown signal or main task completion
            await asyncio.gather(
                self._main_task,
                self._wait_for_shutdown(),
                return_exceptions=True
            )
            
        except Exception as e:
            logger.error(f"Error running edge node: {e}")
            raise
        finally:
            await self.shutdown()

    async def _start_background_services(self) -> None:
        """Start background services."""
        # Health monitor is already started during initialization
        # Driver manager and processor manager are already started during initialization
        # No additional background services need to be started
        logger.info("Background services are already running")

    async def _main_loop(self) -> None:
        """Main edge node control loop."""
        loop_interval = 1.0 / self._edge_config.loop_hz
        
        logger.info(f"Starting main loop at {self._edge_config.loop_hz} Hz")
        
        while self._running and not self._shutdown_event.is_set():
            try:
                # Main processing cycle
                await self._process_cycle()
                
                # Sleep until next cycle
                await asyncio.sleep(loop_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                # Continue running unless it's a critical error
                await asyncio.sleep(loop_interval)

    async def _process_cycle(self) -> None:
        """Process one cycle of the main loop."""
        # Collect telemetry data from camera devices
        telemetry_data = {}
        
        if hasattr(self, 'camera_devices') and self.camera_devices:
            # Collect camera device status
            for camera_device in self.camera_devices:
                device_name = camera_device['name']
                device_id = camera_device['id']
                
                try:
                    # Basic status data for cameras
                    camera_status = {
                        'device_id': device_id,
                        'device_name': device_name,
                        'device_type': camera_device.get('device_type', 'camera'),
                        'stream_count': len(camera_device.get('stream_urls', [])),
                        'status': 'active',
                        'timestamp': asyncio.get_event_loop().time()
                    }
                    telemetry_data[f"camera_{device_id}"] = camera_status
                    
                except Exception as e:
                    logger.error(f"Error collecting status from camera {device_name}: {e}")
        
        # Send telemetry if enabled and we have data
        if telemetry_data and self._edge_config.telemetry.enabled and self.cloud_client:
            await self.cloud_client.send_telemetry(telemetry_data)
        
        # Process any incoming commands
        if self.cloud_client and self._edge_config.control_mode.value in ["command", "hybrid"]:
            try:
                commands = await self.cloud_client.get_pending_commands()
                
                if commands:
                    logger.info(f"Received {len(commands)} commands (camera command handling not yet implemented)")
                    # TODO: Implement camera command handling (e.g., start/stop streaming, adjust settings)
                    
            except Exception as e:
                logger.debug(f"No pending commands available: {e}")

    async def _wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        await self._shutdown_event.wait()

    async def shutdown(self) -> None:
        """Shutdown the edge node gracefully."""
        if not self._running:
            return
        
        logger.info("Shutting down edge node")
        self._running = False
        self._shutdown_event.set()
        
        # Cancel main task
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        
        # Cancel background tasks
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        
        # Shutdown components
        shutdown_tasks = []
        
        if self.processor_manager:
            shutdown_tasks.append(self.processor_manager.stop_all())
        
        if self.driver_manager:
            shutdown_tasks.append(self.driver_manager.stop_all())
        
        if self.health_monitor:
            shutdown_tasks.append(self.health_monitor.stop())
        
        if self.cloud_client:
            shutdown_tasks.append(self.cloud_client.shutdown())
        
        if self.config_manager:
            shutdown_tasks.append(self.config_manager.shutdown())
        
        # Shutdown video proxy
        if hasattr(self, 'video_proxy') and self.video_proxy:
            self.video_proxy.stop_all_streams()
            
        if hasattr(self, 'video_proxy_runner') and self.video_proxy_runner:
            shutdown_tasks.append(self.video_proxy_runner.cleanup())
        
        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)
        
        logger.info("Edge node shutdown complete")

    @asynccontextmanager
    async def managed_lifecycle(self):
        """Context manager for edge node lifecycle."""
        try:
            await self.initialize()
            yield self
        finally:
            await self.shutdown()

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive edge node status."""
        status = {
            "node_id": self.node_id,
            "running": self._running,
            "config": self.config_manager.get_status() if self.config_manager else None,
            "cloud": self.cloud_client.get_status() if self.cloud_client else None,
            "health": self.health_monitor.get_status() if self.health_monitor else None,
            "drivers": self.driver_manager.get_status() if self.driver_manager else None,
            "processors": self.processor_manager.get_status() if self.processor_manager else None,
        }
        
        return status

# Convenience functions

async def create_edge_node(config_path: Optional[str] = None, **kwargs) -> EdgeNode:
    """Create and initialize an edge node."""
    node = EdgeNode(config_path=config_path, **kwargs)
    await node.initialize()
    return node

async def run_edge_node(config_path: Optional[str] = None, **kwargs) -> None:
    """Run an edge node with managed lifecycle."""
    async with EdgeNode(config_path=config_path, **kwargs).managed_lifecycle() as node:
        await node.run()
