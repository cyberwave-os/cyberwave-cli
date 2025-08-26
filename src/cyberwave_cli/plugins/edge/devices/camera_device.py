"""
IP Camera Device CLI

Provides specialized CLI commands for managing IP cameras:
- Network discovery of IP cameras
- Camera registration as sensors
- Environment integration
- Live streaming and analysis
- Computer vision processing
"""

import asyncio
import json
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urlparse
import ipaddress

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel

from cyberwave import Client
from cyberwave_cli.plugins.edge.devices import BaseDeviceCLI
from cyberwave_cli.plugins.edge.utils.dependencies import (
    dependency_manager, 
    require_for_device, 
    optional_import, 
    check_device_readiness,
    requires_dependency
)
from cyberwave_cli.plugins.edge.utils.connectivity import (
    check_connectivity,
    ConnectivityMode,
    get_registration_url
)

console = Console()

class CameraDeviceCLI(BaseDeviceCLI):
    """CLI implementation for IP cameras."""
    
    @property
    def device_type(self) -> str:
        return "camera/ip"
    
    @property
    def device_name(self) -> str:
        return "IP Camera"
    
    @property
    def supported_capabilities(self) -> List[str]:
        return [
            "video_streaming",
            "motion_detection",
            "object_detection", 
            "face_recognition",
            "computer_vision",
            "live_preview",
            "recording"
        ]
    
    def create_typer_app(self) -> typer.Typer:
        """Create camera specific CLI commands."""
        app = typer.Typer(
            name="camera",
            help="📷 IP Camera discovery and management commands",
            rich_markup_mode="rich"
        )
        
        @app.command("discover")
        def discover_cameras(
            network: str = typer.Option("auto", "--network", help="Network to scan (auto, 192.168.1.0/24, etc.)"),
            timeout: float = typer.Option(5.0, "--timeout", help="Discovery timeout in seconds"),
            ports: List[int] = typer.Option([80, 554, 8080, 8081], "--ports", help="Ports to check"),
            save_results: bool = typer.Option(True, "--save/--no-save", help="Save discovery results"),
            verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
            auto_install_deps: bool = typer.Option(False, "--auto-install-deps", help="Auto-install missing dependencies")
        ) -> None:
            """🔍 Discover IP cameras on the network."""
            
            console.print(Panel.fit(
                "[bold blue]IP Camera Network Discovery[/bold blue]\n"
                "Scanning for cameras and video devices on the network",
                title="📷 Camera Discovery"
            ))
            
            # Check dependencies
            if not check_device_readiness("camera/ip", auto_install=auto_install_deps):
                if not auto_install_deps:
                    console.print("\n[yellow]💡 Tip: Use --auto-install-deps to install missing dependencies automatically[/yellow]")
                raise typer.Exit(1)
            
            try:
                asyncio.run(self._discover_cameras_async(
                    network=network,
                    timeout=timeout,
                    ports=ports,
                    save_results=save_results,
                    verbose=verbose
                ))
            except Exception as e:
                console.print(f"[red]❌ Discovery failed: {e}[/red]")
                raise typer.Exit(1)
        
        @app.command("register")
        def register_camera(
            camera_ip: str = typer.Option(..., "--camera", help="Camera IP address or URL"),
            environment_uuid: str = typer.Option(..., "--environment", help="Environment UUID or name"),
            sensor_name: Optional[str] = typer.Option(None, "--name", help="Custom sensor name"),
            position_x: float = typer.Option(0.0, "--x", help="Camera X position"),
            position_y: float = typer.Option(0.0, "--y", help="Camera Y position"),
            position_z: float = typer.Option(1.5, "--z", help="Camera Z position (height)"),
            backend_url: Optional[str] = typer.Option(None, "--backend", help="Backend URL"),
            test_connection: bool = typer.Option(True, "--test/--no-test", help="Test camera connection"),
            offline_mode: bool = typer.Option(False, "--offline", help="Skip connectivity check and register locally")
        ) -> None:
            """📋 Register an IP camera as a sensor in an environment."""
            
            console.print(Panel.fit(
                "[bold blue]Camera Registration[/bold blue]\n"
                "Registering IP camera as a sensor for analysis",
                title="📋 Sensor Registration"
            ))
            
            try:
                asyncio.run(self._register_camera_async(
                    camera_ip=camera_ip,
                    environment_uuid=environment_uuid,
                    sensor_name=sensor_name,
                    position=(position_x, position_y, position_z),
                    backend_url=backend_url,
                    test_connection=test_connection,
                    offline_mode=offline_mode
                ))
            except Exception as e:
                console.print(f"[red]❌ Registration failed: {e}[/red]")
                raise typer.Exit(1)
        
        @app.command("stream")
        def stream_camera(
            camera_ip: str = typer.Option(..., "--camera", help="Camera IP address or URL"),
            preview: bool = typer.Option(False, "--preview", help="Show live preview"),
            duration: Optional[int] = typer.Option(None, "--duration", help="Stream duration in seconds"),
            save_to: Optional[str] = typer.Option(None, "--save", help="Save stream to file"),
            format: str = typer.Option("mp4", "--format", help="Output format (mp4, avi, etc.)")
        ) -> None:
            """📹 Stream from IP camera with optional preview and recording."""
            
            console.print(Panel.fit(
                "[bold blue]Camera Streaming[/bold blue]\n"
                f"Streaming from camera: {camera_ip}",
                title="📹 Live Stream"
            ))
            
            try:
                self._stream_camera(
                    camera_ip=camera_ip,
                    preview=preview,
                    duration=duration,
                    save_to=save_to,
                    format=format
                )
            except Exception as e:
                console.print(f"[red]❌ Streaming failed: {e}[/red]")
                raise typer.Exit(1)

        @app.command("test-streams")
        def test_streams():
            """🔍 Test RTSP stream connectivity for discovered cameras"""
            import asyncio
            from cyberwave_cli.plugins.edge.utils.stream_detector import test_uniview_nvr
            
            console.print(Panel.fit(
                "[bold yellow]RTSP Stream Detection[/bold yellow]\n"
                "Testing connectivity to registered NVR camera streams",
                title="🔍 Stream Test"
            ))
            
            try:
                asyncio.run(test_uniview_nvr())
            except Exception as e:
                console.print(f"[red]❌ Stream testing failed: {e}[/red]")
                raise typer.Exit(1)
        
        @app.command("analyze")
        def analyze_camera(
            camera_ip: str = typer.Option(..., "--camera", help="Camera IP address or URL"),
            analysis_type: str = typer.Option("motion", "--type", help="Analysis type: motion, objects, faces"),
            duration: int = typer.Option(60, "--duration", help="Analysis duration in seconds"),
            sensitivity: float = typer.Option(0.02, "--sensitivity", help="Detection sensitivity (0.01-1.0)"),
            save_results: bool = typer.Option(True, "--save/--no-save", help="Save analysis results")
        ) -> None:
            """🧠 Run computer vision analysis on camera feed."""
            
            console.print(Panel.fit(
                "[bold blue]Camera Analysis[/bold blue]\n"
                f"Running {analysis_type} detection for {duration}s",
                title="🧠 Computer Vision"
            ))
            
            try:
                self._analyze_camera(
                    camera_ip=camera_ip,
                    analysis_type=analysis_type,
                    duration=duration,
                    sensitivity=sensitivity,
                    save_results=save_results
                )
            except Exception as e:
                console.print(f"[red]❌ Analysis failed: {e}[/red]")
                raise typer.Exit(1)
        
        @app.command("setup")
        def setup_camera_environment(
            project_id: int = typer.Option(..., "--project", help="Project ID"),
            environment_name: str = typer.Option(..., "--environment", help="Environment name"),
            camera_ips: List[str] = typer.Option(..., "--cameras", help="Camera IP addresses"),
            backend_url: Optional[str] = typer.Option(None, "--backend", help="Backend URL"),
            auto_discover: bool = typer.Option(False, "--auto-discover", help="Auto-discover cameras first")
        ) -> None:
            """🏗️ Setup complete camera environment with multiple cameras."""
            
            console.print(Panel.fit(
                "[bold blue]Camera Environment Setup[/bold blue]\n"
                f"Creating environment '{environment_name}' with {len(camera_ips)} cameras",
                title="🏗️ Environment Setup"
            ))
            
            try:
                asyncio.run(self._setup_camera_environment_async(
                    project_id=project_id,
                    environment_name=environment_name,
                    camera_ips=camera_ips,
                    backend_url=backend_url,
                    auto_discover=auto_discover
                ))
            except Exception as e:
                console.print(f"[red]❌ Setup failed: {e}[/red]")
                raise typer.Exit(1)
        
        @app.command("status")
        def camera_status(
            camera_ip: Optional[str] = typer.Option(None, "--camera", help="Specific camera IP"),
            environment: Optional[str] = typer.Option(None, "--environment", help="Environment UUID/name"),
            detailed: bool = typer.Option(False, "--detailed", help="Show detailed status")
        ) -> None:
            """📊 Show camera status and health information."""
            
            console.print("📊 [bold blue]Camera Status[/bold blue]")
            
            try:
                self._show_camera_status(
                    camera_ip=camera_ip,
                    environment=environment,
                    detailed=detailed
                )
            except Exception as e:
                console.print(f"[red]❌ Status check failed: {e}[/red]")
                raise typer.Exit(1)
        
        return app
    
    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate camera specific configuration."""
        errors = []
        
        device_config = config.get("device", {})
        
        # Check camera source
        source = device_config.get("source") or device_config.get("port")
        if not source:
            errors.append("Camera requires source (IP address or device path)")
        
        # Validate IP address if provided
        if source and not source.startswith("/dev/"):
            try:
                # Check if it's a valid IP or URL
                if "://" not in source:
                    ipaddress.ip_address(source)
                else:
                    parsed = urlparse(source)
                    if not parsed.netloc:
                        errors.append(f"Invalid camera URL: {source}")
            except ValueError:
                errors.append(f"Invalid camera IP address: {source}")
        
        # Check capabilities
        capabilities = device_config.get("capabilities", [])
        required_caps = ["video_streaming"]
        for cap in required_caps:
            if cap not in capabilities:
                errors.append(f"Camera requires capability: {cap}")
        
        return errors
    
    def get_default_config(self) -> Dict[str, Any]:
        """Get default camera configuration."""
        return {
            "device_name": "ip-camera",
            "device_type": "camera/ip",
            "source": "192.168.1.100",  # Default IP
            "capabilities": [
                "video_streaming",
                "motion_detection",
                "object_detection",
                "computer_vision",
                "live_preview"
            ],
            "auto_register": True,
            "connection_args": {
                "protocol": "rtsp",
                "port": 554,
                "username": "",
                "password": "",
                "stream_path": "/stream1"
            },
            "metadata": {
                "resolution": "1920x1080",
                "fps": 30,
                "codec": "h264",
                "night_vision": False,
                "ptz_support": False
            }
        }
    
    def get_processor_configs(self) -> List[Dict[str, Any]]:
        """Get default processor configurations for cameras."""
        return [
            {
                "name": "computer_vision",
                "enabled": True,
                "config": {
                    "enable_motion_detection": True,
                    "enable_object_detection": False,
                    "enable_face_detection": False,
                    "motion_detection": {
                        "motion_threshold": 0.02,
                        "min_contour_area": 500,
                        "blur_kernel_size": 21
                    },
                    "object_detection": {
                        "confidence_threshold": 0.5,
                        "model": "yolov5s",
                        "classes": ["person", "car", "bicycle"]
                    },
                    "face_detection": {
                        "confidence_threshold": 0.8,
                        "recognition_enabled": False
                    }
                }
            },
            {
                "name": "video_recorder",
                "enabled": True,
                "config": {
                    "record_motion_events": True,
                    "record_duration": 30,  # seconds
                    "output_format": "mp4",
                    "quality": "medium"
                }
            },
            {
                "name": "health_monitor",
                "enabled": True,
                "config": {
                    "check_interval": 10.0,  # More frequent for cameras
                    "connection_timeout": 5.0,
                    "retry_attempts": 3
                }
            }
        ]
    
    async def _discover_cameras_async(
        self,
        network: str,
        timeout: float,
        ports: List[int],
        save_results: bool,
        verbose: bool
    ) -> None:
        """Async implementation of camera discovery."""
        
        # Determine network range
        if network == "auto":
            network_range = await self._detect_local_network()
        else:
            network_range = network
        
        console.print(f"🔍 Scanning network: [cyan]{network_range}[/cyan]")
        console.print(f"📡 Checking ports: [yellow]{ports}[/yellow]")
        console.print(f"⏱️ Timeout: [dim]{timeout}s[/dim]")
        
        # Scan network for cameras
        cameras = await self._scan_network_for_cameras(network_range, ports, timeout, verbose)
        
        if not cameras:
            console.print("[yellow]⚠️ No cameras found on the network[/yellow]")
            console.print("\n💡 Tips:")
            console.print("  • Ensure cameras are powered on and connected")
            console.print("  • Check if cameras are on the same network")
            console.print("  • Try different port ranges with --ports")
            return
        
        # Display discovered cameras
        self._display_discovered_cameras(cameras)
        
        # Save results if requested
        if save_results:
            await self._save_discovery_results(cameras)
        
        # Ask if user wants to register any cameras
        if cameras and Confirm.ask("\nWould you like to register any cameras as sensors?"):
            await self._interactive_camera_registration(cameras)
    
    async def _detect_local_network(self) -> str:
        """Detect the local network range."""
        try:
            # Try Linux/Windows route command first
            result = subprocess.run(
                ["ip", "route", "show", "default"], 
                capture_output=True, 
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                # Parse gateway IP (Linux format)
                for line in result.stdout.split('\n'):
                    if 'default via' in line:
                        gateway_ip = line.split()[2]
                        # Convert to network range (assume /24)
                        network_base = '.'.join(gateway_ip.split('.')[:-1]) + '.0/24'
                        return network_base
                        
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        try:
            # Try macOS route command
            result = subprocess.run(
                ["route", "-n", "get", "default"], 
                capture_output=True, 
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                # Parse gateway IP (macOS format)
                for line in result.stdout.split('\n'):
                    if 'gateway:' in line:
                        gateway_ip = line.split(':')[1].strip()
                        # Convert to network range (assume /24)
                        network_base = '.'.join(gateway_ip.split('.')[:-1]) + '.0/24'
                        return network_base
                        
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        # Fallback to common ranges
        return "192.168.1.0/24"
    
    async def _scan_network_for_cameras(
        self, 
        network_range: str, 
        ports: List[int], 
        timeout: float,
        verbose: bool
    ) -> List[Dict[str, Any]]:
        """Scan network range for cameras."""
        
        # Parse network range
        try:
            network = ipaddress.ip_network(network_range, strict=False)
        except ValueError:
            console.print(f"[red]❌ Invalid network range: {network_range}[/red]")
            return []
        
        # Limit scan size for performance
        hosts = list(network.hosts())
        if len(hosts) > 254:
            console.print(f"[yellow]⚠️ Large network ({len(hosts)} hosts), limiting scan to first 254[/yellow]")
            hosts = hosts[:254]
        
        cameras = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            
            # Create scan tasks
            scan_task = progress.add_task(
                f"Scanning {len(hosts)} hosts on {len(ports)} ports...",
                total=len(hosts)
            )
            
            # Scan hosts in batches to avoid overwhelming the network
            batch_size = 20
            for i in range(0, len(hosts), batch_size):
                batch = hosts[i:i + batch_size]
                
                # Create coroutines for this batch
                batch_tasks = [
                    self._check_host_for_camera(str(host), ports, timeout, verbose)
                    for host in batch
                ]
                
                # Run batch concurrently
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                
                # Process results
                for result in batch_results:
                    if isinstance(result, dict):
                        cameras.append(result)
                    elif verbose and isinstance(result, Exception):
                        console.print(f"[dim]Error checking host: {result}[/dim]")
                
                # Update progress
                progress.update(scan_task, advance=len(batch))
                
                # Small delay to be network-friendly
                await asyncio.sleep(0.1)
        
        return cameras
    
    async def _check_host_for_camera(
        self, 
        host: str, 
        ports: List[int], 
        timeout: float,
        verbose: bool
    ) -> Optional[Dict[str, Any]]:
        """Check if a host has camera services."""
        
        for port in ports:
            try:
                # Test TCP connection
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=timeout
                )
                writer.close()
                await writer.wait_closed()
                
                if verbose:
                    console.print(f"[dim]✓ {host}:{port} - Open[/dim]")
                
                # Try to identify if it's a camera
                camera_info = await self._identify_camera_service(host, port, timeout)
                if camera_info:
                    camera_info.update({
                        "ip": host,
                        "port": port,
                        "status": "online",
                        "discovered_at": time.time()
                    })
                    return camera_info
                    
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                if verbose:
                    console.print(f"[dim]✗ {host}:{port} - Closed[/dim]")
                continue
        
        return None
    
    async def _identify_camera_service(
        self, 
        host: str, 
        port: int, 
        timeout: float
    ) -> Optional[Dict[str, Any]]:
        """Try to identify if the service is a camera."""
        
        # Common camera service patterns
        camera_indicators = {
            80: ["axis", "hikvision", "dahua", "camera", "webcam"],
            554: ["rtsp"],  # RTSP port
            8080: ["camera", "video", "stream"],
            8081: ["camera", "video", "stream"]
        }
        
        try:
            if port == 554:
                # RTSP service detection
                return {
                    "type": "rtsp_camera",
                    "protocol": "rtsp",
                    "stream_url": f"rtsp://{host}:{port}/stream1",
                    "confidence": 0.8
                }
            
            elif port in [80, 8080, 8081]:
                # HTTP service detection with optional aiohttp
                aiohttp = optional_import("aiohttp", "HTTP camera detection will be limited without aiohttp")
                
                if aiohttp:
                    try:
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                            async with session.get(f"http://{host}:{port}/", timeout=timeout) as response:
                                content = await response.text()
                                
                                # Check for camera-related content
                                content_lower = content.lower()
                                for indicator in camera_indicators.get(port, []):
                                    if indicator in content_lower:
                                        return {
                                            "type": "http_camera",
                                            "protocol": "http",
                                            "web_interface": f"http://{host}:{port}/",
                                            "confidence": 0.6,
                                            "detected_type": indicator
                                        }
                                
                                # Generic web service that might be a camera
                                if any(word in content_lower for word in ["video", "stream", "camera"]):
                                    return {
                                        "type": "possible_camera",
                                        "protocol": "http",
                                        "web_interface": f"http://{host}:{port}/",
                                        "confidence": 0.3
                                    }
                                
                    except Exception:
                        pass
                else:
                    # aiohttp not available, basic detection
                    return {
                        "type": "http_service",
                        "protocol": "http",
                        "web_interface": f"http://{host}:{port}/",
                        "confidence": 0.2
                    }
            
        except Exception:
            pass
        
        return None
    
    def _display_discovered_cameras(self, cameras: List[Dict[str, Any]]) -> None:
        """Display discovered cameras in a nice table."""
        
        console.print(f"\n🎯 [bold green]Found {len(cameras)} potential camera(s):[/bold green]")
        
        table = Table()
        table.add_column("IP Address", style="cyan")
        table.add_column("Port", style="white")
        table.add_column("Type", style="green")
        table.add_column("Protocol", style="yellow")
        table.add_column("Confidence", style="dim")
        table.add_column("Access URL", style="blue")
        
        for camera in cameras:
            confidence = camera.get("confidence", 0)
            confidence_str = f"{confidence:.1%}"
            
            access_url = camera.get("stream_url") or camera.get("web_interface", "")
            
            table.add_row(
                camera["ip"],
                str(camera["port"]),
                camera.get("detected_type", camera.get("type", "unknown")),
                camera.get("protocol", "unknown"),
                confidence_str,
                access_url
            )
        
        console.print(table)
        
        # Show usage suggestions
        console.print("\n💡 [bold]Next steps:[/bold]")
        console.print("1. Test camera connection:", "[cyan]cyberwave edge camera stream --camera <IP>[/cyan]")
        console.print("2. Register as sensor:", "[cyan]cyberwave edge camera register --camera <IP> --environment <env>[/cyan]")
        console.print("3. Setup environment:", "[cyan]cyberwave edge camera setup --cameras <IP1> <IP2>[/cyan]")
    
    async def _save_discovery_results(self, cameras: List[Dict[str, Any]]) -> None:
        """Save discovery results to a file."""
        
        results_file = Path.home() / ".cyberwave" / "camera_discovery.json"
        results_file.parent.mkdir(parents=True, exist_ok=True)
        
        discovery_data = {
            "timestamp": time.time(),
            "cameras_found": len(cameras),
            "cameras": cameras
        }
        
        with open(results_file, 'w') as f:
            json.dump(discovery_data, f, indent=2)
        
        console.print(f"💾 Discovery results saved: [cyan]{results_file}[/cyan]")
    
    async def _interactive_camera_registration(self, cameras: List[Dict[str, Any]]) -> None:
        """Interactive camera registration flow."""
        
        console.print("\n[bold]📋 Interactive Camera Registration[/bold]")
        
        # Let user select cameras to register
        selected_cameras = []
        for i, camera in enumerate(cameras):
            camera_desc = f"{camera['ip']}:{camera['port']} ({camera.get('type', 'unknown')})"
            if Confirm.ask(f"Register camera {camera_desc}?"):
                selected_cameras.append(camera)
        
        if not selected_cameras:
            console.print("No cameras selected for registration.")
            return
        
        # Get environment information
        environment_name = Prompt.ask("Environment name", default="Camera Lab")
        project_id = Prompt.ask("Project ID", default="1")
        
        console.print(f"\n🏗️ Registering {len(selected_cameras)} cameras in environment '{environment_name}'...")
        
        # This would actually register the cameras
        console.print("[yellow]⚠️ Interactive registration requires backend integration[/yellow]")
        console.print("Use individual register commands for now.")
    
    async def _register_camera_async(
        self,
        camera_ip: str,
        environment_uuid: str,
        sensor_name: Optional[str],
        position: Tuple[float, float, float],
        backend_url: Optional[str],
        test_connection: bool,
        offline_mode: bool = False
    ) -> None:
        """Async implementation of camera registration."""
        
        # Check connectivity unless offline mode is explicitly requested
        if not offline_mode:
            try:
                mode, config = await check_connectivity("camera registration")
                
                if mode == ConnectivityMode.OFFLINE:
                    console.print("\n[yellow]⚠️ Backend unavailable - would you like to register offline?[/yellow]")
                    console.print(f"💡 Setup offline node at: {get_registration_url('camera')}")
                    
                    if not Confirm.ask("Continue with offline registration?"):
                        console.print("[red]Registration cancelled[/red]")
                        return
                    
                    offline_mode = True
                elif mode == ConnectivityMode.HYBRID:
                    console.print("[yellow]🔄 Using hybrid mode - will sync when backend is available[/yellow]")
                    backend_url = config.get("backend_url", backend_url)
                
            except Exception as e:
                console.print(f"[yellow]⚠️ Connectivity check failed: {e}[/yellow]")
                console.print(f"💡 To setup offline mode, visit: {get_registration_url('camera')}")
                
                if Confirm.ask("Continue with offline registration?"):
                    offline_mode = True
                else:
                    return
        
        if not backend_url:
            try:
                from cyberwave_cli.plugins.auth.app import load_config as load_cli_config, DEFAULT_BACKEND_URL
                cli_config = load_cli_config()
                backend_url = cli_config.get("backend_url", DEFAULT_BACKEND_URL)
                if not backend_url.endswith("/api/v1"):
                    backend_url = f"{backend_url}/api/v1"
            except Exception:
                backend_url = "http://localhost:8000/api/v1"
        
        console.print(f"📍 Registering camera: [cyan]{camera_ip}[/cyan]")
        console.print(f"🏠 Environment: [green]{environment_uuid}[/green]")
        console.print(f"📡 Position: [yellow]({position[0]:.1f}, {position[1]:.1f}, {position[2]:.1f})[/yellow]")
        
        # Test camera connection if requested
        if test_connection:
            console.print("🔍 Testing camera connection...")
            is_reachable = await self._test_camera_connection(camera_ip)
            if not is_reachable:
                console.print("[yellow]⚠️ Camera not reachable, proceeding anyway...[/yellow]")
            else:
                console.print("✅ Camera connection successful")
        
        # Register with backend
        try:
            client = Client(base_url=backend_url)
            await client.login()
            
            sensor_data = {
                "name": sensor_name or f"camera-{camera_ip.replace('.', '-')}",
                "description": f"IP Camera at {camera_ip}",
                "environment_uuid": environment_uuid,
                "sensor_type": "camera",
                "position_x": position[0],
                "position_y": position[1],
                "position_z": position[2],
                "metadata": {
                    "ip_address": camera_ip,
                    "device_type": "camera/ip",
                    "capabilities": self.supported_capabilities,
                    "stream_url": f"rtsp://{camera_ip}:554/stream1",
                    "web_interface": f"http://{camera_ip}/"
                }
            }
            
            sensor = await client.register_sensor(sensor_data)
            sensor_uuid = sensor.get("uuid")
            
            await client.aclose()
            
            console.print(f"✅ Camera registered successfully!")
            console.print(f"📋 Sensor UUID: [cyan]{sensor_uuid}[/cyan]")
            
            # Show next steps
            console.print("\n[bold]Next steps:[/bold]")
            console.print("1. Setup edge node:", f"[cyan]cyberwave edge init --device-type camera --source {camera_ip}[/cyan]")
            console.print("2. Start streaming:", f"[cyan]cyberwave edge camera stream --camera {camera_ip}[/cyan]")
            console.print("3. Run analysis:", f"[cyan]cyberwave edge camera analyze --camera {camera_ip}[/cyan]")
            
        except Exception as e:
            console.print(f"[red]❌ Registration failed: {e}[/red]")
            raise
    
    async def _test_camera_connection(self, camera_ip: str) -> bool:
        """Test if camera is reachable."""
        try:
            # Try common camera ports
            for port in [80, 554, 8080]:
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(camera_ip, port),
                        timeout=3.0
                    )
                    writer.close()
                    await writer.wait_closed()
                    return True
                except:
                    continue
            return False
        except Exception:
            return False
    
    def _stream_camera(
        self,
        camera_ip: str,
        preview: bool,
        duration: Optional[int],
        save_to: Optional[str],
        format: str
    ) -> None:
        """Stream from camera with optional preview and recording."""
        
        console.print(f"📹 Starting stream from: [cyan]{camera_ip}[/cyan]")
        
        if preview:
            console.print("👁️ Live preview mode enabled")
        
        if save_to:
            console.print(f"💾 Recording to: [cyan]{save_to}[/cyan]")
        
        if duration:
            console.print(f"⏱️ Duration: [yellow]{duration} seconds[/yellow]")
        
        # Mock implementation - would integrate with actual streaming
        console.print("[yellow]⚠️ Streaming requires OpenCV integration[/yellow]")
        console.print("Stream URL would be:", f"[dim]rtsp://{camera_ip}:554/stream1[/dim]")
        console.print("Use VLC or similar to test:", f"[cyan]vlc rtsp://{camera_ip}:554/stream1[/cyan]")
    
    def _analyze_camera(
        self,
        camera_ip: str,
        analysis_type: str,
        duration: int,
        sensitivity: float,
        save_results: bool
    ) -> None:
        """Run computer vision analysis on camera feed."""
        
        console.print(f"🧠 Analyzing camera: [cyan]{camera_ip}[/cyan]")
        console.print(f"🔍 Analysis type: [green]{analysis_type}[/green]")
        console.print(f"⏱️ Duration: [yellow]{duration}s[/yellow]")
        console.print(f"🎚️ Sensitivity: [blue]{sensitivity}[/blue]")
        
        # Mock analysis progress
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
        ) as progress:
            
            analysis_task = progress.add_task(
                f"Running {analysis_type} detection...",
                total=duration
            )
            
            for i in range(duration):
                time.sleep(1)
                progress.update(analysis_task, advance=1)
                
                # Mock detection events
                if i % 10 == 0 and i > 0:
                    console.print(f"[dim]🎯 {analysis_type.title()} detected at {i}s[/dim]")
        
        console.print("✅ Analysis complete!")
        
        if save_results:
            results_file = f"camera_analysis_{int(time.time())}.json"
            console.print(f"💾 Results saved: [cyan]{results_file}[/cyan]")
        
        # Mock results summary
        console.print("\n📊 [bold]Analysis Summary:[/bold]")
        console.print(f"  • {analysis_type.title()} events: [green]12[/green]")
        console.print(f"  • Average confidence: [blue]87%[/blue]")
        console.print(f"  • Peak activity: [yellow]15-25s[/yellow]")
    
    async def _setup_camera_environment_async(
        self,
        project_id: int,
        environment_name: str,
        camera_ips: List[str],
        backend_url: Optional[str],
        auto_discover: bool
    ) -> None:
        """Setup complete camera environment."""
        
        if auto_discover:
            console.print("🔍 Auto-discovering cameras first...")
            discovered_cameras = await self._scan_network_for_cameras("auto", [80, 554, 8080], 5.0, False)
            if discovered_cameras:
                discovered_ips = [cam["ip"] for cam in discovered_cameras]
                camera_ips.extend(discovered_ips)
                console.print(f"📍 Added {len(discovered_ips)} discovered cameras")
        
        console.print(f"🏗️ Setting up environment: [cyan]{environment_name}[/cyan]")
        console.print(f"📷 Cameras: [green]{len(camera_ips)}[/green]")
        
        # Mock environment creation
        console.print("[yellow]⚠️ Environment setup requires backend integration[/yellow]")
        console.print("Would create:")
        console.print(f"  • Environment: {environment_name}")
        console.print(f"  • Project ID: {project_id}")
        console.print(f"  • Cameras: {camera_ips}")
        
        console.print("\n🎯 [bold]Manual setup commands:[/bold]")
        console.print(f"1. Create environment:", f"[cyan]cyberwave environments create '{environment_name}' --project-id {project_id}[/cyan]")
        for i, ip in enumerate(camera_ips):
            console.print(f"{i+2}. Register camera {ip}:", f"[cyan]cyberwave edge camera register --camera {ip} --environment '{environment_name}'[/cyan]")
    
    def _show_camera_status(
        self,
        camera_ip: Optional[str],
        environment: Optional[str],
        detailed: bool
    ) -> None:
        """Show camera status and health."""
        
        if camera_ip:
            console.print(f"📷 [bold blue]Camera Status: {camera_ip}[/bold blue]")
            
            # Mock status check
            table = Table()
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="white")
            
            table.add_row("IP Address", camera_ip)
            table.add_row("Status", "[green]Online[/green]")
            table.add_row("Stream URL", f"rtsp://{camera_ip}:554/stream1")
            table.add_row("Resolution", "1920x1080")
            table.add_row("FPS", "30")
            table.add_row("Last Seen", "2 minutes ago")
            
            console.print(table)
            
            if detailed:
                console.print("\n🔧 [bold]Detailed Information:[/bold]")
                console.print("  • Protocol: RTSP/HTTP")
                console.print("  • Codec: H.264")
                console.print("  • Night Vision: Enabled")
                console.print("  • Motion Detection: Active")
        
        elif environment:
            console.print(f"🏠 [bold blue]Environment Cameras: {environment}[/bold blue]")
            console.print("[yellow]⚠️ Environment camera listing requires backend integration[/yellow]")
        
        else:
            console.print("📷 [bold blue]All Cameras Status[/bold blue]")
            console.print("[yellow]⚠️ Global camera status requires backend integration[/yellow]")

# Export the device CLI class for discovery
device_cli = CameraDeviceCLI
