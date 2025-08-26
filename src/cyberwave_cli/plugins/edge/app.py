"""
Unified Edge CLI Commands - Scalable Device Architecture

Provides comprehensive edge node management through the CLI with support for:
- Dynamic backend configuration from web services  
- Multiple device types via plugin architecture
- Unified installation and deployment workflow
- Device-specific commands auto-discovered and loaded
- v1 compatibility and automatic migration
- Enhanced v2 architecture with all v1 processors preserved

Uses a scalable device plugin system where each device type (SO-101, Spot, Tello, etc.)
has its own dedicated module with device-specific CLI commands and configurations.
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel

from cyberwave import Client

from .core.node import EdgeNode
from .config.manager import ConfigManager, ConfigSource
from .config.schema import EdgeConfig, Environment
from .utils.migration import V1ToV2Migrator
from .devices import discover_device_clis, BaseDeviceCLI
from .utils.dependencies import dependency_manager, check_device_readiness
from .utils.node_identity import display_node_info, get_node_identity, export_node_info
from .utils.connectivity import get_connectivity_manager, refresh_connectivity_manager, CyberWaveEnvironment, ENVIRONMENT_URLS
from .utils.backend_integration import backend_registration_manager

console = Console()

# Main edge app
edge_app = typer.Typer(
    name="edge", 
    help="🤖 Unified edge node management with scalable device support",
    rich_markup_mode="rich"
)

# Generic device-category subcommands (will be enhanced by device-specific plugins)
camera_app = typer.Typer(name="camera", help="📷 Camera edge node commands")
robot_app = typer.Typer(name="robot", help="🦾 Robotics edge node commands") 
drone_app = typer.Typer(name="drone", help="🛸 Drone edge node commands")
sensor_app = typer.Typer(name="sensor", help="📡 Sensor edge node commands")

# Add generic subcommands to main app
edge_app.add_typer(camera_app, name="camera")
edge_app.add_typer(robot_app, name="robot")
edge_app.add_typer(drone_app, name="drone")
edge_app.add_typer(sensor_app, name="sensor")

# Discover and register device-specific CLI modules
def _register_device_clis():
    """Discover and register device-specific CLI modules."""
    try:
        device_clis = discover_device_clis()
        
        console.print(f"[dim]🔍 Discovered {len(device_clis)} device CLI modules[/dim]")
        
        for device_type, device_cli in device_clis.items():
            try:
                device_app = device_cli.create_typer_app()
                edge_app.add_typer(device_app, name=device_cli.device_name.lower().replace(' ', '-'))
                
                console.print(f"[dim]  ✅ Registered: {device_cli.device_name} ({device_type})[/dim]")
                
            except Exception as e:
                console.print(f"[dim]  ⚠️ Failed to register {device_type}: {e}[/dim]")
                
    except Exception as e:
        console.print(f"[dim]⚠️ Device CLI discovery failed: {e}[/dim]")

# Register device CLIs on module load
_register_device_clis()

DEFAULT_CONFIG_PATH = Path.home() / ".cyberwave" / "edge.json"

# Core unified commands (v2 architecture)

@edge_app.command("init")
def init_edge_node(
    device_type: str = typer.Option(..., "--device-type", "-d", help="Device type (camera, robot/so-101, drone/tello, sensor/lidar, etc.)"),
    backend_url: Optional[str] = typer.Option(None, "--backend", "-b", help="Backend service URL"),
    node_id: Optional[str] = typer.Option(None, "--node-id", "-n", help="Unique node identifier"),
    project_id: Optional[int] = typer.Option(None, "--project", "-p", help="Project ID for registration"),
    environment: str = typer.Option("development", "--environment", "-e", help="Deployment environment"),
    auto_register: bool = typer.Option(True, "--auto-register/--no-auto-register", help="Auto-register device"),
    config_path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", help="Configuration file path"),
    from_backend: bool = typer.Option(False, "--from-backend", help="Fetch configuration from backend service")
) -> None:
    """🚀 Initialize a new edge node with unified architecture."""
    
    console.print(Panel.fit(
        "[bold blue]Cyberwave Unified Edge Node Initialization[/bold blue]\n"
        "Setting up scalable edge computing with device-specific optimization",
        title="🤖 Edge Setup"
    ))
    
    try:
        asyncio.run(_init_edge_node_async(
            device_type=device_type,
            backend_url=backend_url,
            node_id=node_id,
            project_id=project_id,
            environment=environment,
            auto_register=auto_register,
            config_path=config_path,
            from_backend=from_backend
        ))
    except Exception as e:
        console.print(f"[red]❌ Initialization failed: {e}[/red]")
        raise typer.Exit(1)

async def _init_edge_node_async(
    device_type: str,
    backend_url: Optional[str],
    node_id: Optional[str], 
    project_id: Optional[int],
    environment: str,
    auto_register: bool,
    config_path: Path,
    from_backend: bool
) -> None:
    """Async implementation of edge node initialization with device-specific support."""
    
    # Generate node ID if not provided
    if not node_id:
        import uuid
        node_id = f"edge-{device_type.replace('/', '-')}-{str(uuid.uuid4())[:8]}"
    
    # Get backend URL from CLI config if not provided
    if not backend_url:
        try:
            from cyberwave_cli.plugins.auth.app import load_config as load_cli_config, DEFAULT_BACKEND_URL
            cli_config = load_cli_config()
            backend_url = cli_config.get("backend_url", DEFAULT_BACKEND_URL)
            if not backend_url.endswith("/api/v1"):
                backend_url = f"{backend_url}/api/v1"
        except Exception:
            backend_url = "http://localhost:8000/api/v1"
    
    console.print(f"📍 Initializing edge node: [cyan]{node_id}[/cyan]")
    console.print(f"🔧 Device type: [green]{device_type}[/green]")
    console.print(f"🌐 Backend: [blue]{backend_url}[/blue]")
    
    # Check if we have a device-specific CLI for this device type
    device_clis = discover_device_clis()
    device_cli = device_clis.get(device_type)
    
    if device_cli:
        console.print(f"✅ Using device-specific configuration for: [cyan]{device_cli.device_name}[/cyan]")
        edge_config = _create_device_specific_config(device_cli, node_id, backend_url, environment)
    else:
        console.print(f"⚠️ No device-specific configuration found for: [yellow]{device_type}[/yellow]")
        console.print("Using generic configuration...")
        edge_config = _create_generic_config(device_type, node_id, backend_url, environment)
    
    # Create configuration manager
    config_manager = ConfigManager(
        node_id=node_id,
        backend_url=backend_url,
        config_file=config_path
    )
    
    if from_backend:
        # Try to fetch configuration from backend
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task("Fetching configuration from backend...", total=None)
            
            try:
                await config_manager.initialize()
                backend_config = config_manager.to_edge_config()
                
                # Merge with device-specific config
                edge_config = _merge_configs(edge_config, backend_config)
                console.print("✅ Configuration merged with backend settings")
                
            except Exception as e:
                console.print(f"[yellow]⚠️ Backend configuration failed: {e}[/yellow]")
                console.print("Using device-specific configuration...")
    
    # Auto-register device if requested
    if auto_register and project_id:
        await _auto_register_device(edge_config, project_id, backend_url)
    
    # Validate configuration if device-specific CLI is available
    if device_cli:
        validation_errors = device_cli.validate_config(edge_config.to_dict())
        if validation_errors:
            console.print(f"[yellow]⚠️ Configuration validation warnings:[/yellow]")
            for error in validation_errors:
                console.print(f"  • {error}")
    
    # Save configuration
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        f.write(edge_config.to_json())
    
    console.print(f"💾 Configuration saved: [cyan]{config_path}[/cyan]")
    
    # Show configuration summary
    _show_config_summary(edge_config, device_cli)
    
    # Show device-specific next steps
    console.print("\n[bold green]🎉 Edge node initialized successfully![/bold green]")
    _show_next_steps(device_type, device_cli)

def _create_device_specific_config(device_cli: BaseDeviceCLI, node_id: str, backend_url: str, environment: str) -> EdgeConfig:
    """Create configuration using device-specific CLI."""
    
    # Get device-specific defaults
    device_config = device_cli.get_default_config()
    device_config["device_name"] = f"{node_id}-device"
    
    # Get device-specific processor configs
    processors = device_cli.get_processor_configs()
    
    # Create base configuration
    config_data = {
        "node_id": node_id,
        "environment": environment,
        "network": {
            "backend_url": backend_url,
            "timeout": 30.0,
            "retry_attempts": 3
        },
        "auth": {
            "use_device_token": True
        },
        "device": device_config,
        "telemetry": {
            "enabled": True,
            "interval": 0.1 if device_cli.device_type.startswith("robot/") else 1.0,  # Faster for robotics
            "compression": True
        },
        "health": {
            "enabled": True,
            "check_interval": 5.0 if device_cli.device_type.startswith("robot/") else 30.0  # Faster for robotics
        },
        "processors": processors
    }
    
    return EdgeConfig.from_dict(config_data)

def _create_generic_config(device_type: str, node_id: str, backend_url: str, environment: str) -> EdgeConfig:
    """Create generic configuration for unknown device types."""
    
    # Generic device configuration
    device_config = {
        "device_name": f"{node_id}-device",
        "device_type": device_type,
        "auto_register": True,
        "capabilities": ["telemetry", "health_monitoring"]
    }
    
    # Generic processor configuration
    processors = [
        {
            "name": "health_monitor",
            "enabled": True,
            "config": {
                "check_interval": 30.0
            }
        }
    ]
    
    # Add device-category specific defaults
    if device_type.startswith("camera"):
        device_config.update({
            "port": "/dev/video0",
            "capabilities": ["video_streaming", "motion_detection"]
        })
        processors.append({
            "name": "computer_vision",
            "enabled": True,
            "config": {
                "enable_motion_detection": True,
                "enable_object_detection": False
            }
        })
    elif device_type.startswith("robot/"):
        device_config.update({
            "port": "/dev/ttyACM0",
            "capabilities": ["movement", "teleoperation"]
        })
        processors.append({
            "name": "robotics_data",
            "enabled": True,
            "config": {
                "enable_anomaly_detection": True
            }
        })
    elif device_type.startswith("drone/"):
        device_config.update({
            "capabilities": ["flight", "video_streaming"]
        })
        processors.append({
            "name": "flight_controller",
            "enabled": True,
            "config": {
                "safety_enabled": True
            }
        })
    
    # Create base configuration
    config_data = {
        "node_id": node_id,
        "environment": environment,
        "network": {
            "backend_url": backend_url,
            "timeout": 30.0,
            "retry_attempts": 3
        },
        "auth": {
            "use_device_token": True
        },
        "device": device_config,
        "telemetry": {
            "enabled": True,
            "interval": 1.0,
            "compression": True
        },
        "health": {
            "enabled": True,
            "check_interval": 30.0
        },
        "processors": processors
    }
    
    return EdgeConfig.from_dict(config_data)

def _merge_configs(device_config: EdgeConfig, backend_config: EdgeConfig) -> EdgeConfig:
    """Merge device-specific config with backend config."""
    # For now, prioritize device-specific config but merge network settings from backend
    merged_dict = device_config.to_dict()
    
    # Merge network settings from backend
    if backend_config.network:
        merged_dict["network"].update(backend_config.network.to_dict())
    
    # Merge auth settings from backend
    if backend_config.auth:
        merged_dict["auth"].update(backend_config.auth.to_dict())
    
    return EdgeConfig.from_dict(merged_dict)

async def _auto_register_device(config: EdgeConfig, project_id: int, backend_url: str) -> None:
    """Auto-register device with backend."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task = progress.add_task("Registering device with backend...", total=None)
        
        try:
            client = Client(base_url=backend_url)
            await client.login()
            
            device = await client.register_device(
                project_id=project_id,
                name=config.device.device_name,
                device_type=config.device.device_type
            )
            
            config.device.device_id = str(device.get("id"))
            
            # Issue device token
            token = await client.issue_device_token(int(config.device.device_id))
            config.auth.device_token = token
            
            await client.aclose()
            
            console.print(f"✅ Device registered with ID: [cyan]{config.device.device_id}[/cyan]")
            
        except Exception as e:
            console.print(f"[yellow]⚠️ Device registration failed: {e}[/yellow]")

def _show_config_summary(config: EdgeConfig, device_cli: Optional[BaseDeviceCLI]) -> None:
    """Show configuration summary table."""
    table = Table(title="Configuration Summary")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")
    
    table.add_row("Node ID", config.node_id or "Not set")
    table.add_row("Environment", config.environment.value)
    table.add_row("Device Type", config.device.device_type)
    
    if device_cli:
        table.add_row("Device CLI", f"✅ {device_cli.device_name}")
        table.add_row("Capabilities", ", ".join(device_cli.supported_capabilities[:3]) + "...")
    else:
        table.add_row("Device CLI", "⚠️ Generic (no specific CLI)")
    
    table.add_row("Backend URL", config.network.backend_url)
    table.add_row("Auto Register", "Yes" if config.device.auto_register else "No")
    table.add_row("Telemetry", "Enabled" if config.telemetry.enabled else "Disabled")
    table.add_row("Processors", f"{len(config.processors)} configured")
    
    console.print(table)

def _show_next_steps(device_type: str, device_cli: Optional[BaseDeviceCLI]) -> None:
    """Show device-specific next steps."""
    console.print("\n[bold]Next steps:[/bold]")
    console.print("1. Review configuration:", "[cyan]cyberwave edge config show[/cyan]")
    console.print("2. Start edge node:", "[cyan]cyberwave edge run[/cyan]")
    console.print("3. Monitor status:", "[cyan]cyberwave edge status[/cyan]")
    
    if device_cli:
        # Device-specific next steps
        device_name = device_cli.device_name.lower().replace(' ', '-')
        
        if device_type.startswith("robot/"):
            console.print(f"4. Run calibration:", f"[cyan]cyberwave edge {device_name} calibrate[/cyan]")
            console.print(f"5. Start teleoperation:", f"[cyan]cyberwave edge {device_name} teleop[/cyan]")
        elif device_type.startswith("drone/"):
            console.print(f"4. Setup flight area:", f"[cyan]cyberwave edge {device_name} setup[/cyan]")
            console.print(f"5. Start flight session:", f"[cyan]cyberwave edge {device_name} takeoff[/cyan]")
        elif device_type.startswith("camera"):
            console.print(f"4. Test video stream:", f"[cyan]cyberwave edge {device_name} stream[/cyan]")
        
        console.print(f"\n💡 Use [cyan]cyberwave edge {device_name} --help[/cyan] for device-specific commands")
    else:
        console.print(f"\n💡 No device-specific commands available for: [yellow]{device_type}[/yellow]")
        console.print("Consider contributing a device CLI module!")

@edge_app.command("run")
def run_edge_node(
    config_path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", help="Configuration file path"),
    background: bool = typer.Option(False, "--background", "-b", help="Run in background"),
    log_level: str = typer.Option("INFO", "--log-level", "-l", help="Logging level")
) -> None:
    """🚀 Run an edge node with unified architecture."""
    
    if not config_path.exists():
        console.print(f"[red]❌ Configuration file not found: {config_path}[/red]")
        console.print("Initialize first with: [cyan]cyberwave edge init[/cyan]")
        raise typer.Exit(1)
    
    console.print(f"🚀 Starting edge node with config: [cyan]{config_path}[/cyan]")
    
    try:
        if background:
            console.print("[yellow]⚠️ Background mode not yet implemented[/yellow]")
            console.print("Running in foreground mode...")
        
        # Run edge node
        asyncio.run(_run_edge_node_async(config_path, log_level))
        
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️ Edge node stopped by user[/yellow]")
    except Exception as e:
        console.print(f"[red]❌ Edge node failed: {e}[/red]")
        raise typer.Exit(1)

async def _run_edge_node_async(config_path: Path, log_level: str) -> None:
    """Async implementation of edge node execution."""
    
    # Setup logging
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create and run edge node
    async with EdgeNode(config_path=str(config_path)).managed_lifecycle() as node:
        console.print("✅ Edge node started successfully")
        console.print("Press Ctrl+C to stop")
        await node.run()

@edge_app.command("status")
def show_status(
    config_path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", help="Configuration file path"),
    detailed: bool = typer.Option(False, "--detailed", "-d", help="Show detailed status")
) -> None:
    """📊 Show edge node status."""
    
    console.print("📊 [bold blue]Edge Node Status[/bold blue]")
    
    # Check configuration
    if config_path.exists():
        try:
            with open(config_path) as f:
                config_data = json.load(f)
            
            console.print(f"✅ Configuration: [green]{config_path}[/green]")
            
            # Show device-specific status if available
            device_type = config_data.get("device", {}).get("device_type", "")
            device_clis = discover_device_clis()
            device_cli = device_clis.get(device_type)
            
            if device_cli:
                console.print(f"🔧 Device CLI: [cyan]{device_cli.device_name}[/cyan]")
                
                # Run device-specific validation
                validation_errors = device_cli.validate_config(config_data)
                if validation_errors:
                    console.print(f"[yellow]⚠️ Configuration issues:[/yellow]")
                    for error in validation_errors:
                        console.print(f"  • {error}")
                else:
                    console.print("✅ Device configuration valid")
            
            if detailed:
                _show_detailed_status(config_data)
            else:
                _show_basic_status(config_data)
                
        except Exception as e:
            console.print(f"[red]❌ Configuration error: {e}[/red]")
    else:
        console.print(f"[red]❌ No configuration found: {config_path}[/red]")
        console.print("Initialize with: [cyan]cyberwave edge init[/cyan]")

def _show_basic_status(config_data: Dict[str, Any]) -> None:
    """Show basic status information."""
    table = Table()
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="white")
    
    table.add_row("Node ID", config_data.get("node_id", "Not set"))
    table.add_row("Device Type", config_data.get("device", {}).get("device_type", "Unknown"))
    table.add_row("Environment", config_data.get("environment", "Unknown"))
    table.add_row("Backend", config_data.get("network", {}).get("backend_url", "Not set"))
    
    # Runtime status (would need actual process checking)
    table.add_row("Runtime", "[yellow]Unknown (not running)[/yellow]")
    
    console.print(table)

def _show_detailed_status(config_data: Dict[str, Any]) -> None:
    """Show detailed status information."""
    _show_basic_status(config_data)
    
    # Processors
    processors = config_data.get("processors", [])
    if processors:
        console.print("\n[bold]🧠 Processors:[/bold]")
        proc_table = Table()
        proc_table.add_column("Name", style="cyan")
        proc_table.add_column("Enabled", style="white")
        proc_table.add_column("Config", style="dim")
        
        for proc in processors:
            enabled = "✅ Yes" if proc.get("enabled", False) else "❌ No"
            config_keys = list(proc.get("config", {}).keys())
            config_str = ", ".join(config_keys[:3])
            if len(config_keys) > 3:
                config_str += "..."
            
            proc_table.add_row(proc.get("name", "Unknown"), enabled, config_str)
        
        console.print(proc_table)

@edge_app.command("config")
def config_command(
    action: str = typer.Argument(..., help="Action: show, edit, validate, migrate"),
    config_path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", "-c", help="Configuration file path"),
    from_backend: bool = typer.Option(False, "--from-backend", help="Fetch from backend service")
) -> None:
    """⚙️ Manage edge node configuration."""
    
    if action == "show":
        _show_config(config_path, from_backend)
    elif action == "edit":
        _edit_config(config_path)
    elif action == "validate":
        _validate_config(config_path)
    elif action == "migrate":
        _migrate_config(config_path)
    else:
        console.print(f"[red]❌ Unknown action: {action}[/red]")
        console.print("Available actions: show, edit, validate, migrate")
        raise typer.Exit(1)

def _show_config(config_path: Path, from_backend: bool) -> None:
    """Show current configuration."""
    if from_backend:
        console.print("[yellow]⚠️ Backend configuration fetch not yet implemented in show[/yellow]")
    
    if not config_path.exists():
        console.print(f"[red]❌ Configuration file not found: {config_path}[/red]")
        return
    
    try:
        with open(config_path) as f:
            config_data = json.load(f)
        
        console.print(f"📄 Configuration: [cyan]{config_path}[/cyan]")
        console.print("\n" + json.dumps(config_data, indent=2))
        
    except Exception as e:
        console.print(f"[red]❌ Error reading configuration: {e}[/red]")

def _edit_config(config_path: Path) -> None:
    """Edit configuration interactively."""
    console.print(f"📝 Editing configuration: [cyan]{config_path}[/cyan]")
    console.print("[yellow]⚠️ Interactive config editing not yet implemented[/yellow]")
    console.print(f"Edit manually: [cyan]{config_path}[/cyan]")

def _validate_config(config_path: Path) -> None:
    """Validate configuration with device-specific checks."""
    if not config_path.exists():
        console.print(f"[red]❌ Configuration file not found: {config_path}[/red]")
        return
    
    try:
        with open(config_path) as f:
            config_data = json.load(f)
        
        # Generic validation
        edge_config = EdgeConfig.from_dict(config_data)
        generic_errors = edge_config.validate()
        
        # Device-specific validation
        device_type = config_data.get("device", {}).get("device_type", "")
        device_clis = discover_device_clis()
        device_cli = device_clis.get(device_type)
        
        device_errors = []
        if device_cli:
            device_errors = device_cli.validate_config(config_data)
            console.print(f"✅ Using device-specific validation for: [cyan]{device_cli.device_name}[/cyan]")
        
        all_errors = generic_errors + device_errors
        
        if all_errors:
            console.print(f"[red]❌ Configuration validation failed ({len(all_errors)} errors):[/red]")
            for error in all_errors:
                console.print(f"  • {error}")
        else:
            console.print("✅ Configuration is valid")
            
    except Exception as e:
        console.print(f"[red]❌ Validation error: {e}[/red]")

def _migrate_config(config_path: Path) -> None:
    """Migrate v1 configuration to v2."""
    console.print("🔄 [bold]Configuration Migration: v1 → v2[/bold]")
    
    if not config_path.exists():
        console.print(f"[red]❌ Configuration file not found: {config_path}[/red]")
        return
    
    try:
        migrator = V1ToV2Migrator()
        migrator.load_v1_config(config_path)
        
        v2_config = migrator.migrate_to_v2()
        errors = migrator.validate_migration()
        
        if errors:
            console.print(f"[yellow]⚠️ Migration completed with {len(errors)} warnings:[/yellow]")
            for error in errors:
                console.print(f"  • {error}")
        else:
            console.print("✅ Migration completed successfully")
        
        # Save migrated config
        backup_path = config_path.with_suffix(".v1.backup")
        config_path.rename(backup_path)
        
        migrator.save_v2_config(config_path)
        
        console.print(f"💾 Original config backed up: [cyan]{backup_path}[/cyan]")
        console.print(f"💾 New v2 config saved: [cyan]{config_path}[/cyan]")
        
    except Exception as e:
        console.print(f"[red]❌ Migration failed: {e}[/red]")

@edge_app.command("devices")
def list_devices() -> None:
    """📋 List available device types and their CLI modules."""
    
    console.print("📋 [bold blue]Available Device Types[/bold blue]\n")
    
    try:
        device_clis = discover_device_clis()
        
        if not device_clis:
            console.print("[yellow]⚠️ No device CLI modules found[/yellow]")
            return
        
        table = Table()
        table.add_column("Device Type", style="cyan")
        table.add_column("Device Name", style="white")
        table.add_column("Capabilities", style="dim")
        table.add_column("CLI Command", style="green")
        table.add_column("Ready", style="white")
        
        for device_type, device_cli in device_clis.items():
            capabilities = ", ".join(device_cli.supported_capabilities[:3])
            if len(device_cli.supported_capabilities) > 3:
                capabilities += "..."
            
            cli_command = device_cli.device_name.lower().replace(' ', '-')
            
            # Check if device dependencies are satisfied
            available, missing = dependency_manager.check_device_dependencies(device_type)
            ready_status = "[green]✅ Ready[/green]" if not missing else f"[red]❌ Missing {len(missing)}[/red]"
            
            table.add_row(
                device_type,
                device_cli.device_name,
                capabilities,
                f"cyberwave edge {cli_command}",
                ready_status
            )
        
        console.print(table)
        
        console.print(f"\n💡 [dim]Commands:[/dim]")
        console.print("  • Device help: [cyan]cyberwave edge <device-name> --help[/cyan]")
        console.print("  • Check dependencies: [cyan]cyberwave edge check-deps --device <device-type>[/cyan]")
        console.print("  • Install dependencies: [cyan]cyberwave edge install-deps --device <device-type>[/cyan]")
        
    except Exception as e:
        console.print(f"[red]❌ Error listing devices: {e}[/red]")

@edge_app.command("check-deps")
def check_dependencies(
    device: Optional[str] = typer.Option(None, "--device", help="Device type to check (e.g., camera/ip, robot/so-101)"),
    feature: Optional[str] = typer.Option(None, "--feature", help="Feature to check (e.g., computer_vision, hand_pose)"),
    all_devices: bool = typer.Option(False, "--all", help="Check all device types")
) -> None:
    """🔍 Check dependency status for devices or features."""
    
    console.print("🔍 [bold blue]Dependency Status Check[/bold blue]\n")
    
    if all_devices:
        device_clis = discover_device_clis()
        for device_type in device_clis.keys():
            dependency_manager.show_device_requirements(device_type)
            console.print()
    elif device:
        if check_device_readiness(device):
            console.print(f"[green]✅ {device} is ready - all dependencies satisfied[/green]")
        else:
            dependency_manager.show_device_requirements(device)
    elif feature:
        dependencies = dependency_manager.get_feature_dependencies(feature)
        if not dependencies:
            console.print(f"[green]✅ No dependencies required for {feature}[/green]")
            return
        
        console.print(f"[bold]📋 Dependencies for {feature}:[/bold]")
        for spec in dependencies:
            is_available, _ = dependency_manager.check_dependency(spec.package)
            status = "[green]✅ Available[/green]" if is_available else "[red]❌ Missing[/red]"
            console.print(f"  • {spec.name}: {status}")
    else:
        console.print("[red]❌ Please specify --device, --feature, or --all[/red]")
        raise typer.Exit(1)

@edge_app.command("install-deps")
def install_dependencies(
    device: Optional[str] = typer.Option(None, "--device", help="Device type to install dependencies for"),
    feature: Optional[str] = typer.Option(None, "--feature", help="Feature to install dependencies for"),
    package: Optional[str] = typer.Option(None, "--package", help="Specific package to install"),
    confirm: bool = typer.Option(True, "--confirm/--no-confirm", help="Confirm before installing")
) -> None:
    """📦 Install dependencies for devices or features."""
    
    console.print("📦 [bold blue]Dependency Installation[/bold blue]\n")
    
    if package:
        # Install specific package
        success, _ = dependency_manager.require_dependency(
            package, 
            context="manual installation",
            auto_install=True,
            silent=False
        )
        if success:
            console.print(f"[green]✅ Successfully installed {package}[/green]")
        else:
            console.print(f"[red]❌ Failed to install {package}[/red]")
            raise typer.Exit(1)
    
    elif device:
        # Install device dependencies
        dependencies = dependency_manager.get_device_dependencies(device)
        if not dependencies:
            console.print(f"[green]✅ No dependencies required for {device}[/green]")
            return
        
        console.print(f"Installing dependencies for: [cyan]{device}[/cyan]")
        
        for spec in dependencies:
            is_available, _ = dependency_manager.check_dependency(spec.package)
            if not is_available:
                if confirm and not Confirm.ask(f"Install {spec.name} ({spec.package})?"):
                    continue
                
                console.print(f"Installing {spec.name}...")
                success, _ = dependency_manager.require_dependency(
                    spec.package,
                    context=device,
                    auto_install=True
                )
                
                if success:
                    console.print(f"[green]✅ {spec.name} installed[/green]")
                else:
                    console.print(f"[red]❌ Failed to install {spec.name}[/red]")
    
    elif feature:
        # Install feature dependencies
        dependencies = dependency_manager.get_feature_dependencies(feature)
        if not dependencies:
            console.print(f"[green]✅ No dependencies required for {feature}[/green]")
            return
        
        console.print(f"Installing dependencies for: [cyan]{feature}[/cyan]")
        
        for spec in dependencies:
            is_available, _ = dependency_manager.check_dependency(spec.package)
            if not is_available:
                if confirm and not Confirm.ask(f"Install {spec.name} ({spec.package})?"):
                    continue
                
                console.print(f"Installing {spec.name}...")
                success, _ = dependency_manager.require_dependency(
                    spec.package,
                    context=feature,
                    auto_install=True
                )
                
                if success:
                    console.print(f"[green]✅ {spec.name} installed[/green]")
                else:
                    console.print(f"[red]❌ Failed to install {spec.name}[/red]")
    
    else:
        console.print("[red]❌ Please specify --device, --feature, or --package[/red]")
        raise typer.Exit(1)

@edge_app.command("node-info")
def show_node_info(
    detailed: bool = typer.Option(False, "--detailed", "-d", help="Show detailed node information"),
    export: bool = typer.Option(False, "--export", help="Export node information as JSON")
) -> None:
    """🤖 Show edge node identity and registration information."""
    
    if export:
        import json
        node_info = export_node_info()
        console.print(json.dumps(node_info, indent=2))
    else:
        # Display node info
        display_node_info(detailed=detailed)
        
        # Display environment info
        manager = get_connectivity_manager(force_refresh=True)
        console.print(f"\n[bold blue]🌐 Environment Configuration[/bold blue]")
        
        env_table = Table.grid(padding=1)
        env_table.add_column("Property", style="cyan")
        env_table.add_column("Value", style="white")
        
        env_table.add_row("Environment", manager.environment)
        env_table.add_row("Backend URL", manager.backend_url)
        env_table.add_row("Frontend URL", manager._get_frontend_url())
        
        console.print(env_table)
        
        if not detailed:
            console.print(f"\n💡 [dim]Use --detailed for more information[/dim]")
            console.print(f"💡 [dim]Use --export to get JSON output[/dim]")

@edge_app.command("register-node")
def register_node() -> None:
    """📝 Get registration information for backend setup."""
    
    identity = get_node_identity()
    node_info = export_node_info()
    
    console.print("\n[bold blue]🔗 Node Registration Information[/bold blue]")
    
    # Show registration panel
    registration_panel = Panel(
        f"[bold]Node Registration Details:[/bold]\n\n"
        f"[bold cyan]Node ID:[/bold cyan] {identity.node_id}\n"
        f"[bold green]Node Name:[/bold green] {identity.node_name}\n"
        f"[bold yellow]Platform:[/bold yellow] {identity.platform} ({identity.architecture})\n"
        f"[bold white]Hostname:[/bold white] {identity.hostname}\n"
        f"[bold magenta]Version:[/bold magenta] {identity.version}\n\n"
        f"[bold blue]Registration Status:[/bold blue] {'✅ Registered' if identity.registered_backend else '❌ Not Registered'}\n\n"
        f"[dim]Use this information to register the node in your backend[/dim]",
        title="📋 Registration Info"
    )
    console.print(registration_panel)
    
    # Show next steps
    if not identity.registered_backend:
        console.print("\n[bold yellow]📝 Next Steps:[/bold yellow]")
        console.print("1. Copy the Node ID above")
        console.print("2. Go to your Cyberwave backend/frontend")
        console.print("3. Register this node in your project")
        console.print("4. Generate an authentication token")
        console.print("5. Run device commands to trigger offline setup")
    else:
        console.print(f"\n[green]✅ Node is already registered with backend[/green]")
        if identity.registration_token:
            token_preview = f"{identity.registration_token[:8]}..."
            console.print(f"Token: [dim]{token_preview}[/dim]")

@edge_app.command("environment")
def manage_environment(
    environment: Optional[str] = typer.Argument(None, help="Environment to switch to (local, dev, qa, prod)"),
    show: bool = typer.Option(False, "--show", "-s", help="Show current environment configuration")
) -> None:
    """🌐 Manage environment configuration for backend/frontend URLs."""
    
    manager = get_connectivity_manager()
    
    if show or environment is None:
        # Show current environment
        console.print("[bold blue]🌐 Current Environment Configuration[/bold blue]\n")
        
        env_table = Table.grid(padding=1)
        env_table.add_column("Property", style="cyan")
        env_table.add_column("Value", style="white")
        
        env_table.add_row("Environment", f"[bold]{manager.environment}[/bold]")
        env_table.add_row("Backend URL", manager.backend_url)
        env_table.add_row("Frontend URL", manager._get_frontend_url())
        
        console.print(env_table)
        
        console.print("\n[bold]Available Environments:[/bold]")
        envs_table = Table(show_header=True, header_style="bold magenta")
        envs_table.add_column("Environment", style="cyan")
        envs_table.add_column("Backend URL", style="white")
        envs_table.add_column("Frontend URL", style="white")
        
        # Show available environments using SDK enums
        frontend_urls = {
            CyberWaveEnvironment.LOCAL.value: "http://localhost:3000",
            CyberWaveEnvironment.DEV.value: "https://app-dev.cyberwave.com",
            CyberWaveEnvironment.QA.value: "https://app-qa.cyberwave.com",
            CyberWaveEnvironment.PROD.value: "https://app.cyberwave.com"
        }
        
        for env_enum in CyberWaveEnvironment:
            env_name = env_enum.value
            backend_url = ENVIRONMENT_URLS.get(env_enum, "Unknown")
            frontend_url = frontend_urls.get(env_name, "Unknown")
            
            envs_table.add_row(env_name, backend_url, frontend_url)
        
        console.print(envs_table)
        
        console.print(f"\n💡 [dim]Use: cyberwave edge environment <env> to switch[/dim]")
        console.print(f"💡 [dim]Or set CYBERWAVE_ENVIRONMENT=<env> environment variable[/dim]")
        
    else:
        # Switch environment
        valid_envs = ["local", "dev", "qa", "staging", "prod"]
        if environment.lower() not in valid_envs:
            console.print(f"[red]❌ Invalid environment '{environment}'[/red]")
            console.print(f"Valid environments: {', '.join(valid_envs)}")
            raise typer.Exit(1)
        
        # Update CLI config with new environment
        try:
            from cyberwave_cli.plugins.auth.app import load_config, save_config
            config = load_config()
            
            # Get URLs for the new environment using SDK
            try:
                env_enum = CyberWaveEnvironment(environment.lower())
                new_backend_url = ENVIRONMENT_URLS[env_enum]
            except ValueError:
                console.print(f"[red]❌ Unknown environment '{environment}'[/red]")
                raise typer.Exit(1)
            
            # Map backend to frontend URL using SDK constants
            frontend_urls = {
                CyberWaveEnvironment.LOCAL.value: "http://localhost:3000",
                CyberWaveEnvironment.DEV.value: "https://app-dev.cyberwave.com",
                CyberWaveEnvironment.QA.value: "https://app-qa.cyberwave.com",
                CyberWaveEnvironment.PROD.value: "https://app.cyberwave.com"
            }
            
            new_frontend_url = frontend_urls.get(environment.lower(), "http://localhost:3000")
            
            # Update config
            config.update({
                "backend_url": new_backend_url,
                "frontend_url": new_frontend_url,
            })
            save_config(config)
            
            # Refresh connectivity manager to pick up new config
            refresh_connectivity_manager()
            
            console.print(f"[green]✅ Switched to '{environment}' environment[/green]")
            console.print(f"Backend: [cyan]{new_backend_url}[/cyan]")
            console.print(f"Frontend: [cyan]{new_frontend_url}[/cyan]")
            
        except Exception as e:
            console.print(f"[red]❌ Failed to update environment: {e}[/red]")
            console.print("You can also set the CYBERWAVE_ENVIRONMENT environment variable")
            raise typer.Exit(1)

# Legacy support commands (preserved for backward compatibility)

@edge_app.command("legacy-init", hidden=True)
def legacy_init(
    robot: str = typer.Option("so_arm100", "--robot", help="Robot driver type"),
    port: Optional[str] = typer.Option(None, "--port", help="Serial/connection port"),
    backend: Optional[str] = typer.Option(None, "--backend", help="Backend base URL"),
    device_id: Optional[int] = typer.Option(None, "--device-id", help="Existing device ID"),
    project_id: Optional[int] = typer.Option(None, "--project", help="Project ID"),
    device_name: Optional[str] = typer.Option(None, "--device-name", help="Device name"),
    device_type: Optional[str] = typer.Option("robot/so-arm100", "--device-type", help="Device type"),
    auto_register: bool = typer.Option(False, "--auto-register", help="Auto-register device"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Config file path"),
) -> None:
    """Legacy init command for backward compatibility."""
    console.print("[yellow]⚠️ Legacy init command - migrating to unified format[/yellow]")
    
    # Convert to new format and call unified init
    device_type_unified = device_type or f"robot/{robot}"
    
    try:
        asyncio.run(_init_edge_node_async(
            device_type=device_type_unified,
            backend_url=backend,
            node_id=device_name,
            project_id=project_id,
            environment="production",
            auto_register=auto_register,
            config_path=config,
            from_backend=False
        ))
    except Exception as e:
        console.print(f"[red]❌ Legacy init failed: {e}[/red]")
        raise typer.Exit(1)

@edge_app.command("auto-register")
def auto_register(
    project_id: Optional[str] = typer.Option(None, "--project", help="Project ID for registration"),
    environment_id: Optional[str] = typer.Option(None, "--environment", help="Environment ID for registration"),
    force: bool = typer.Option(False, "--force", help="Force re-registration"),
    discover_cameras: bool = typer.Option(True, "--discover-cameras/--no-cameras", help="Auto-discover and register cameras"),
    timeout: int = typer.Option(10, "--timeout", help="Discovery timeout in seconds")
):
    """🚀 Automatically register node and discovered devices with backend."""
    
    console.print("[bold blue]🚀 Auto-Registration Workflow[/bold blue]")
    
    try:
        asyncio.run(_auto_register_async(
            project_id=project_id,
            environment_id=environment_id,
            force=force,
            discover_cameras=discover_cameras,
            timeout=timeout
        ))
    except Exception as e:
        console.print(f"[red]❌ Auto-registration failed: {e}[/red]")
        raise typer.Exit(1)

@edge_app.command("registration-status")
def registration_status():
    """📊 Show current registration status for node and devices."""
    backend_registration_manager.show_registration_status()

@edge_app.command("start-video-proxy")
def start_video_proxy(
    port: int = typer.Option(8001, help="Port for video proxy service"),
    analysis_enabled: bool = typer.Option(True, "--analysis/--no-analysis", help="Enable video analysis"),
    cameras_config: Optional[str] = typer.Option(None, "--cameras", help="JSON file with camera configurations")
):
    """🎥 Start video proxy service for secure camera streaming and analysis"""
    asyncio.run(_start_video_proxy_async(port, analysis_enabled, cameras_config))

async def _auto_register_async(
    project_id: Optional[str],
    environment_id: Optional[str],
    force: bool,
    discover_cameras: bool,
    timeout: int
):
    """Async implementation for auto-registration."""
    
    # Step 1: Register the node
    console.print("[blue]1️⃣ Registering edge node...[/blue]")
    node_result = await backend_registration_manager.register_edge_node(
        project_id=project_id,
        environment_id=environment_id,
        force=force
    )
    
    if not discover_cameras:
        console.print("[green]✅ Node registration completed[/green]")
        return
    
    # Step 2: Discover cameras
    console.print(f"[blue]2️⃣ Discovering cameras (timeout: {timeout}s)...[/blue]")
    
    # Import camera discovery function
    from cyberwave_cli.plugins.edge.devices.camera_device import CameraDeviceCLI
    camera_cli = CameraDeviceCLI()
    
    # Create a mock camera discovery (this would be replaced with actual discovery)
    discovered_cameras = [
        {
            "ip_address": "192.168.1.6",
            "port": 80,
            "protocol": "http",
            "camera_type": "ip_camera",
            "detection_method": "network_scan",
            "access_url": "http://192.168.1.6:80"
        }
    ]
    
    # Add your NVR if provided in environment
    import os
    nvr_host = os.getenv("CAMERA_HOST")
    if nvr_host:
        discovered_cameras.append({
            "ip_address": nvr_host,
            "port": int(os.getenv("CAMERA_PORT", "554")),
            "protocol": "rtsp",
            "camera_type": "nvr_system",
            "detection_method": "environment_config",
            "access_url": f"rtsp://{nvr_host}:554",
            "manufacturer": os.getenv("CAMERA_MANUFACTURER", "uniview"),
            "credentials_available": bool(os.getenv("CAMERA_USERNAME"))
        })
    
    console.print(f"[green]✅ Found {len(discovered_cameras)} camera systems[/green]")
    
    # Step 3: Register discovered cameras
    if discovered_cameras:
        console.print("[blue]3️⃣ Registering discovered cameras...[/blue]")
        camera_results = await backend_registration_manager.register_discovered_cameras(
            camera_list=discovered_cameras,
            project_id=project_id,
            environment_id=environment_id
        )
        
        successful_registrations = len([r for r in camera_results if "error" not in r])
        console.print(f"[green]✅ Registered {successful_registrations}/{len(discovered_cameras)} cameras[/green]")
    
    # Step 4: Show final status
    console.print("[blue]4️⃣ Final registration status:[/blue]")
    backend_registration_manager.show_registration_status()
    
    console.print(f"\n[green]🎉 Auto-registration completed![/green]")
    console.print(f"[cyan]View devices in frontend: {backend_registration_manager.connectivity_manager._get_frontend_url()}/devices[/cyan]")

async def _start_video_proxy_async(port: int, analysis_enabled: bool, cameras_config: Optional[str]):
    """Start video proxy service asynchronously"""
    try:
        # Import video proxy service
        from .services.video_proxy import VideoProxyService
        
        console.print(Panel.fit(
            f"[bold blue]Video Proxy Service[/bold blue]\n"
            f"Port: {port}\n"
            f"Analysis: {'Enabled' if analysis_enabled else 'Disabled'}",
            title="🎥 Starting Service"
        ))
        
        # Get node identity for backend integration
        node_identity = get_node_identity()
        
        # Get backend URL from connectivity manager
        connectivity_manager = get_connectivity_manager()
        backend_url = connectivity_manager._get_backend_url()
        
        # Load camera configurations
        cameras = []
        
        if cameras_config and Path(cameras_config).exists():
            # Load from file
            with open(cameras_config, 'r') as f:
                camera_data = json.load(f)
                cameras = camera_data.get('cameras', [])
        else:
            # Use default Uniview NVR configuration
            cameras = [
                {
                    'id': 1,
                    'name': 'D1 (Camerette)',
                    'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c1/s1/live'
                },
                {
                    'id': 2,
                    'name': 'D2 (Salone)',
                    'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c2/s1/live'
                },
                {
                    'id': 3,
                    'name': 'D3 (Ingresso)',
                    'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c3/s1/live'
                },
                {
                    'id': 4,
                    'name': 'D4 (Salone > Ovest)',
                    'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c4/s1/live'
                },
                {
                    'id': 5,
                    'name': 'D5 (Salone > Sud)',
                    'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c5/s1/live'
                },
                {
                    'id': 6,
                    'name': 'D6 (Cameretta > Est)',
                    'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c6/s1/live'
                },
                {
                    'id': 7,
                    'name': 'D7 (Settimo piano)',
                    'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c7/s1/live'
                },
                {
                    'id': 8,
                    'name': 'D8 (Camera Letto)',
                    'rtsp_url': 'rtsp://admin:Stralis26$@192.168.1.6:554/unicast/c8/s1/live'
                }
            ]
        
        console.print(f"[green]✅ Loaded {len(cameras)} camera configurations[/green]")
        
        # Initialize video proxy service
        service = VideoProxyService(
            backend_url=backend_url,
            node_id=node_identity.node_id,
            proxy_port=port
        )
        
        # Initialize streams
        await service.initialize_streams(cameras)
        
        # Start video captures
        if analysis_enabled:
            service.start_all_streams()
            console.print("[green]✅ Video analysis enabled[/green]")
        else:
            console.print("[yellow]⚠️ Video analysis disabled[/yellow]")
        
        # Create and start web server
        from aiohttp import web
        app = service.create_web_app()
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        
        # Register proxy service with backend
        registration_success = await service.register_with_backend()
        if registration_success:
            console.print(f"[green]✅ Proxy service registered with backend[/green]")
        else:
            console.print(f"[yellow]⚠️ Failed to register with backend (continuing anyway)[/yellow]")
        
        console.print(f"[green]🎥 Video Proxy Service started on http://localhost:{port}[/green]")
        console.print("\n📡 Available endpoints:")
        
        endpoints_table = Table(show_header=True, header_style="bold blue")
        endpoints_table.add_column("Method", style="green")
        endpoints_table.add_column("Endpoint", style="cyan")
        endpoints_table.add_column("Description")
        
        endpoints_table.add_row("GET", f"http://localhost:{port}/streams", "List all camera streams")
        endpoints_table.add_row("GET", f"http://localhost:{port}/streams/{{id}}/status", "Get stream status")
        endpoints_table.add_row("GET", f"http://localhost:{port}/streams/{{id}}/snapshot", "Get current frame (JPEG)")
        endpoints_table.add_row("GET", f"http://localhost:{port}/streams/{{id}}/mjpeg", "MJPEG video stream")
        endpoints_table.add_row("WS", f"ws://localhost:{port}/ws", "WebSocket for real-time events")
        endpoints_table.add_row("GET", f"http://localhost:{port}/health", "Service health check")
        
        console.print(endpoints_table)
        
        console.print(f"\n💡 Integration notes:")
        console.print(f"   • Frontend can access streams via: http://localhost:{port}/streams/{{camera_id}}/mjpeg")
        console.print(f"   • No RTSP credentials exposed to frontend")
        console.print(f"   • Motion detection events sent to: {backend_url}")
        console.print(f"   • WebSocket events available at: ws://localhost:{port}/ws")
        
        console.print(f"\n[yellow]Press Ctrl+C to stop the service[/yellow]")
        
        try:
            # Keep running
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]🛑 Shutting down video proxy service...[/yellow]")
            
            # Unregister from backend
            await service.unregister_from_backend()
            
            # Stop all services
            service.stop_all_streams()
            await runner.cleanup()
            console.print("[green]✅ Video proxy service stopped[/green]")
            
    except ImportError as e:
        console.print(f"[red]❌ Missing dependencies for video proxy: {e}[/red]")
        console.print("Install with: pip install opencv-python aiohttp aiohttp-cors websockets")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]❌ Failed to start video proxy: {e}[/red]")
        raise typer.Exit(1)

# Export the main app for plugin loading
app = edge_app

if __name__ == "__main__":
    edge_app()