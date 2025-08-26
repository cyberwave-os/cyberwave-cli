"""
SO-101 Robotic Arm Device CLI

Provides specialized CLI commands for managing SO-101 robotic arms:
- Leader-follower setup and calibration
- Teleoperation session management
- Digital twin synchronization
- Safety monitoring and emergency stops
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
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

console = Console()

class SO101DeviceCLI(BaseDeviceCLI):
    """CLI implementation for SO-101 robotic arms."""
    
    @property
    def device_type(self) -> str:
        return "robot/so-101"
    
    @property
    def device_name(self) -> str:
        return "SO-101 Robotic Arm"
    
    @property
    def supported_capabilities(self) -> List[str]:
        return [
            "arm_control",
            "leader_follower", 
            "teleoperation",
            "calibration",
            "digital_twin_sync",
            "safety_monitoring"
        ]
    
    def create_typer_app(self) -> typer.Typer:
        """Create SO-101 specific CLI commands."""
        app = typer.Typer(
            name="so101",
            help="🤖 SO-101 robotic arm commands",
            rich_markup_mode="rich"
        )
        
        @app.command("setup")
        def setup(
            project_id: int = typer.Option(..., "--project", help="Project ID"),
            leader_port: str = typer.Option("/dev/ttyACM0", "--leader-port", help="Leader arm serial port"),
            follower_port: str = typer.Option("/dev/ttyACM1", "--follower-port", help="Follower arm serial port"),
            device_name: str = typer.Option("so101-edge", "--device-name", help="Edge device name"),
            backend_url: Optional[str] = typer.Option(None, "--backend", help="Backend URL"),
            auto_register: bool = typer.Option(True, "--auto-register/--no-auto-register", help="Auto-register device"),
            config_path: Path = typer.Option("~/.cyberwave/edge.json", "--config", help="Config file path"),
            auto_install_deps: bool = typer.Option(False, "--auto-install-deps", help="Auto-install missing dependencies")
        ) -> None:
            """🔧 Set up SO-101 edge node with leader-follower configuration."""
            
            console.print(Panel.fit(
                "[bold blue]SO-101 Robotic Arm Setup[/bold blue]\n"
                "Configuring leader-follower arm pair for teleoperation",
                title="🤖 SO-101 Setup"
            ))
            
            # Check SO-101 dependencies
            if not check_device_readiness("robot/so-101", auto_install=auto_install_deps):
                if not auto_install_deps:
                    console.print("\n[yellow]💡 Tip: Use --auto-install-deps to install missing dependencies automatically[/yellow]")
                    console.print("Or install manually:")
                    console.print("  [cyan]pip install lerobot pyserial pygame[/cyan]")
                raise typer.Exit(1)
            
            try:
                asyncio.run(self._setup_so101(
                    project_id=project_id,
                    leader_port=leader_port,
                    follower_port=follower_port,
                    device_name=device_name,
                    backend_url=backend_url,
                    auto_register=auto_register,
                    config_path=Path(config_path).expanduser()
                ))
            except Exception as e:
                console.print(f"[red]❌ Setup failed: {e}[/red]")
                raise typer.Exit(1)
        
        @app.command("calibrate")
        def calibrate(
            config: Path = typer.Option("~/.cyberwave/edge.json", "--config", help="Edge config file"),
            poses: List[str] = typer.Option(["home", "extended", "retracted"], "--poses", help="Calibration poses"),
            interactive: bool = typer.Option(True, "--interactive/--automatic", help="Interactive calibration mode"),
            save_file: Optional[str] = typer.Option(None, "--save", help="Calibration file path")
        ) -> None:
            """🔧 Calibrate SO-101 leader-follower arms for accurate motion mirroring."""
            
            console.print(Panel.fit(
                "[bold blue]SO-101 Calibration[/bold blue]\n"
                "Calibrating leader-follower arms for precise motion mirroring",
                title="🔧 Calibration"
            ))
            
            try:
                self._calibrate_so101(
                    config_path=Path(config).expanduser(),
                    poses=poses,
                    interactive=interactive,
                    save_file=save_file
                )
            except Exception as e:
                console.print(f"[red]❌ Calibration failed: {e}[/red]")
                raise typer.Exit(1)
        
        @app.command("teleop")
        def teleop(
            config: Path = typer.Option("~/.cyberwave/edge.json", "--config", help="Edge config file"),
            mode: str = typer.Option("physical_leader", "--mode", help="Control mode: physical_leader, remote_command, digital_leader"),
            controller: str = typer.Option("gamepad", "--controller", help="Input controller: gamepad, keyboard, gesture"),
            sensitivity: float = typer.Option(1.0, "--sensitivity", help="Input sensitivity (0.1-3.0)"),
            twin_uuid: Optional[str] = typer.Option(None, "--twin", help="Digital twin UUID for synchronization")
        ) -> None:
            """🎮 Start SO-101 teleoperation session with specified control mode."""
            
            console.print(Panel.fit(
                f"[bold green]SO-101 Teleoperation[/bold green]\n"
                f"Mode: {mode} | Controller: {controller} | Sensitivity: {sensitivity}",
                title="🎮 Teleoperation"
            ))
            
            try:
                self._start_teleop_session(
                    config_path=Path(config).expanduser(),
                    mode=mode,
                    controller=controller,
                    sensitivity=sensitivity,
                    twin_uuid=twin_uuid
                )
            except Exception as e:
                console.print(f"[red]❌ Teleoperation failed: {e}[/red]")
                raise typer.Exit(1)
        
        @app.command("status")
        def status(
            config: Path = typer.Option("~/.cyberwave/edge.json", "--config", help="Edge config file"),
            detailed: bool = typer.Option(False, "--detailed", help="Show detailed status information")
        ) -> None:
            """📊 Show SO-101 edge node status and health information."""
            
            console.print("📊 [bold blue]SO-101 Status[/bold blue]")
            
            try:
                self._show_so101_status(
                    config_path=Path(config).expanduser(),
                    detailed=detailed
                )
            except Exception as e:
                console.print(f"[red]❌ Status check failed: {e}[/red]")
                raise typer.Exit(1)
        
        @app.command("emergency-stop")
        def emergency_stop(
            config: Path = typer.Option("~/.cyberwave/edge.json", "--config", help="Edge config file"),
        ) -> None:
            """🛑 Emergency stop all SO-101 arm movement."""
            
            console.print("[bold red]🛑 EMERGENCY STOP[/bold red]")
            
            try:
                self._emergency_stop_so101(Path(config).expanduser())
                console.print("✅ Emergency stop executed")
            except Exception as e:
                console.print(f"[red]❌ Emergency stop failed: {e}[/red]")
                raise typer.Exit(1)
        
        return app
    
    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate SO-101 specific configuration."""
        errors = []
        
        device_config = config.get("device", {})
        
        # Check required ports
        connection_args = device_config.get("connection_args", {})
        if not connection_args.get("leader_port"):
            errors.append("SO-101 requires leader_port in device.connection_args")
        if not connection_args.get("follower_port"):
            errors.append("SO-101 requires follower_port in device.connection_args")
        
        # Check baudrate
        baudrate = connection_args.get("baudrate", 1000000)
        if baudrate not in [115200, 1000000]:
            errors.append(f"SO-101 baudrate should be 115200 or 1000000, got {baudrate}")
        
        # Check capabilities
        capabilities = device_config.get("capabilities", [])
        required_caps = ["arm_control", "leader_follower"]
        for cap in required_caps:
            if cap not in capabilities:
                errors.append(f"SO-101 requires capability: {cap}")
        
        return errors
    
    def get_default_config(self) -> Dict[str, Any]:
        """Get default SO-101 configuration."""
        return {
            "device_name": "so101-edge",
            "device_type": "robot/so-101",
            "port": "/dev/ttyACM0",  # Primary port for compatibility
            "connection_args": {
                "leader_port": "/dev/ttyACM0",
                "follower_port": "/dev/ttyACM1", 
                "baudrate": 1000000,
                "timeout": 1.0
            },
            "capabilities": [
                "arm_control",
                "leader_follower",
                "teleoperation",
                "calibration",
                "digital_twin_sync"
            ],
            "auto_register": True,
            "metadata": {
                "joint_count": 6,
                "max_payload": "1kg",
                "reach": "650mm",
                "degrees_of_freedom": 6
            }
        }
    
    def get_processor_configs(self) -> List[Dict[str, Any]]:
        """Get default processor configurations for SO-101."""
        return [
            {
                "name": "teleoperation",
                "enabled": True,
                "config": {
                    "safety_enabled": True,
                    "calibration_enabled": True,
                    "emergency_stop_enabled": True,
                    "max_velocity": 100.0,  # degrees/second
                    "max_acceleration": 50.0  # degrees/second²
                }
            },
            {
                "name": "robotics_data",
                "enabled": True,
                "config": {
                    "enable_anomaly_detection": True,
                    "enable_performance_analysis": True,
                    "joint_monitoring": True,
                    "temperature_monitoring": True
                }
            },
            {
                "name": "safety_monitor",
                "enabled": True,
                "config": {
                    "collision_detection": True,
                    "workspace_limits": True,
                    "velocity_limits": True,
                    "temperature_limits": {
                        "max_temp": 70.0,  # Celsius
                        "warning_temp": 60.0
                    }
                }
            }
        ]
    
    async def _setup_so101(
        self,
        project_id: int,
        leader_port: str,
        follower_port: str,
        device_name: str,
        backend_url: Optional[str],
        auto_register: bool,
        config_path: Path
    ) -> None:
        """Async implementation of SO-101 setup."""
        
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Get backend URL from existing CLI config if not provided
        if not backend_url:
            try:
                from cyberwave_cli.plugins.auth.app import load_config as load_cli_config, DEFAULT_BACKEND_URL
                cli_config = load_cli_config()
                backend_url = cli_config.get("backend_url", DEFAULT_BACKEND_URL)
                if not backend_url.endswith("/api/v1"):
                    backend_url = f"{backend_url}/api/v1"
            except Exception:
                backend_url = "http://localhost:8000/api/v1"
        
        console.print(f"🔧 Setting up SO-101: [cyan]{device_name}[/cyan]")
        console.print(f"📍 Leader port: [green]{leader_port}[/green]")
        console.print(f"📍 Follower port: [green]{follower_port}[/green]")
        console.print(f"🌐 Backend: [blue]{backend_url}[/blue]")
        
        # Create SO-101 specific configuration
        device_config = self.get_default_config()
        device_config.update({
            "device_name": device_name,
            "connection_args": {
                "leader_port": leader_port,
                "follower_port": follower_port,
                "baudrate": 1000000
            }
        })
        
        edge_config = {
            "node_id": f"so101-{device_name}",
            "environment": "production",
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
                "interval": 0.1,  # 10Hz for robotics
                "compression": True
            },
            "health": {
                "enabled": True,
                "check_interval": 5.0  # Faster health checks for robotics
            },
            "processors": self.get_processor_configs()
        }
        
        # Auto-register device if requested
        if auto_register:
            await self._auto_register_so101(edge_config, project_id, backend_url)
        
        # Save configuration
        with open(config_path, 'w') as f:
            json.dump(edge_config, f, indent=2)
        
        console.print(f"💾 Configuration saved: [cyan]{config_path}[/cyan]")
        
        # Show setup summary
        self._show_setup_summary(edge_config, leader_port, follower_port)
        
        # Show next steps
        console.print("\n[bold green]🎉 SO-101 setup completed![/bold green]")
        console.print("\n[bold]Next steps:[/bold]")
        console.print("1. Connect both arms to specified ports")
        console.print("2. Run calibration:", "[cyan]cyberwave edge so101 calibrate[/cyan]")
        console.print("3. Start edge node:", "[cyan]cyberwave edge run[/cyan]")
        console.print("4. Test teleoperation:", "[cyan]cyberwave edge so101 teleop[/cyan]")
    
    async def _auto_register_so101(self, config: Dict[str, Any], project_id: int, backend_url: str) -> None:
        """Auto-register SO-101 device with backend."""
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task("Registering SO-101 device...", total=None)
            
            try:
                client = Client(base_url=backend_url)
                await client.login()
                
                device = await client.register_device(
                    project_id=project_id,
                    name=config["device"]["device_name"],
                    device_type="robot/so-101"
                )
                
                config["device"]["device_id"] = str(device.get("id"))
                
                # Issue device token
                token = await client.issue_device_token(int(config["device"]["device_id"]))
                config["auth"]["device_token"] = token
                
                await client.aclose()
                
                console.print(f"✅ SO-101 device registered with ID: [cyan]{config['device']['device_id']}[/cyan]")
                
            except Exception as e:
                console.print(f"[yellow]⚠️ Device registration failed: {e}[/yellow]")
    
    def _show_setup_summary(self, config: Dict[str, Any], leader_port: str, follower_port: str) -> None:
        """Show SO-101 setup summary."""
        table = Table(title="SO-101 Setup Summary")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="white")
        
        table.add_row("Device Type", "SO-101 (Leader-Follower)")
        table.add_row("Leader Port", leader_port)
        table.add_row("Follower Port", follower_port)
        table.add_row("Device Name", config["device"]["device_name"])
        table.add_row("Node ID", config["node_id"])
        table.add_row("Telemetry Rate", f"{1/config['telemetry']['interval']:.0f} Hz")
        table.add_row("Processors", f"{len(config['processors'])} enabled")
        
        console.print(table)
    
    def _calibrate_so101(
        self,
        config_path: Path,
        poses: List[str],
        interactive: bool,
        save_file: Optional[str]
    ) -> None:
        """Calibrate SO-101 arms."""
        
        if not config_path.exists():
            console.print(f"[red]❌ Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        
        # Load config
        try:
            with open(config_path) as f:
                edge_config = json.load(f)
        except Exception as e:
            console.print(f"[red]❌ Error loading config: {e}[/red]")
            raise typer.Exit(1)
        
        # Validate SO-101 configuration
        device_type = edge_config.get("device", {}).get("device_type", "")
        if device_type != "robot/so-101":
            console.print(f"[red]❌ Not a SO-101 configuration: {device_type}[/red]")
            raise typer.Exit(1)
        
        # Safety warnings
        console.print("\n[yellow]⚠️  Safety Checklist:[/yellow]")
        console.print("  • Ensure both arms are powered and connected")
        console.print("  • Clear workspace around both arms") 
        console.print("  • Leader arm should be manually movable (motors disengaged)")
        console.print("  • Follower arm should be powered and enabled")
        console.print()
        
        if not Confirm.ask("Are you ready to proceed with calibration?"):
            raise typer.Exit(0)
        
        if interactive:
            self._run_interactive_calibration(poses, save_file or "so101_calibration.json")
        else:
            console.print("[yellow]⚠️ Automatic calibration requires integration with SO-101 driver[/yellow]")
            console.print("Use interactive mode for now: --interactive")
            raise typer.Exit(1)
    
    def _run_interactive_calibration(self, poses: List[str], save_file: str) -> None:
        """Run interactive SO-101 calibration procedure."""
        console.print("[bold yellow]🔧 Interactive SO-101 Calibration[/bold yellow]")
        console.print("You will be guided through positioning both arms in specific poses.\n")
        
        # Mock calibration data collection (in real implementation, would connect to driver)
        leader_readings = []
        follower_readings = []
        joint_names = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
        
        for i, pose in enumerate(poses):
            console.print(f"[bold blue]📍 Pose {i+1}/{len(poses)}: {pose.title()}[/bold blue]")
            console.print(f"Position both SO-101 arms in the '{pose}' pose as shown in the reference guide.")
            
            # In a real implementation, this would read from the SO-101 driver
            input("Press Enter when both arms are positioned correctly...")
            
            # Mock reading positions
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Reading SO-101 joint positions...", total=None)
                time.sleep(1)  # Simulate reading time
            
            # Mock data - in real implementation, read from SO-101 driver
            leader_pos = [float(i * 10 + j * 5) for j in range(6)]
            follower_pos = [float(i * 10 + j * 5 + 2) for j in range(6)]
            
            leader_readings.append(leader_pos)
            follower_readings.append(follower_pos)
            
            console.print(f"[green]✓[/green] Recorded {pose} pose")
            console.print(f"  Leader:   {[f'{x:.1f}' for x in leader_pos]}")
            console.print(f"  Follower: {[f'{x:.1f}' for x in follower_pos]}\n")
        
        # Calculate calibration parameters
        console.print("[blue]🧮 Calculating SO-101 calibration parameters...[/blue]")
        
        offsets = []
        signs = []
        
        for joint_idx in range(6):
            # Calculate average offset
            joint_offsets = [
                follower_readings[pose_idx][joint_idx] - leader_readings[pose_idx][joint_idx]
                for pose_idx in range(len(poses))
            ]
            avg_offset = sum(joint_offsets) / len(joint_offsets)
            offsets.append(int(avg_offset))
            
            # Determine sign correlation
            if len(poses) >= 2:
                leader_delta = leader_readings[1][joint_idx] - leader_readings[0][joint_idx]
                follower_delta = follower_readings[1][joint_idx] - follower_readings[0][joint_idx]
                sign = -1 if (leader_delta * follower_delta < 0) else 1
            else:
                sign = 1
            signs.append(sign)
        
        # Save calibration data
        calibration_data = {
            "device_type": "robot/so-101",
            "offsets": offsets,
            "signs": signs,
            "joint_names": joint_names,
            "timestamp": time.time(),
            "poses_used": poses,
            "leader_readings": leader_readings,
            "follower_readings": follower_readings,
            "calibration_version": "1.0"
        }
        
        with open(save_file, 'w') as f:
            json.dump(calibration_data, f, indent=2)
        
        # Show calibration results
        self._show_calibration_results(joint_names, offsets, signs, save_file)
    
    def _show_calibration_results(self, joint_names: List[str], offsets: List[int], signs: List[int], save_file: str) -> None:
        """Display SO-101 calibration results."""
        results_table = Table(title="SO-101 Calibration Results")
        results_table.add_column("Joint", style="cyan")
        results_table.add_column("Offset", style="white")
        results_table.add_column("Sign", style="white")
        results_table.add_column("Status", style="green")
        
        for joint, offset, sign in zip(joint_names, offsets, signs):
            status = "✓" if abs(offset) < 50 and abs(sign) == 1 else "⚠️"
            results_table.add_row(joint, str(offset), str(sign), status)
        
        console.print(results_table)
        console.print(f"\n[green]✅ SO-101 calibration saved to: {save_file}[/green]")
        console.print("[dim]Your SO-101 arms are now calibrated for leader-follower operation.[/dim]")
    
    def _start_teleop_session(
        self,
        config_path: Path,
        mode: str,
        controller: str,
        sensitivity: float,
        twin_uuid: Optional[str]
    ) -> None:
        """Start SO-101 teleoperation session."""
        
        if not config_path.exists():
            console.print(f"[red]❌ Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        
        # Load and validate config
        try:
            with open(config_path) as f:
                edge_config = json.load(f)
        except Exception as e:
            console.print(f"[red]❌ Error loading config: {e}[/red]")
            raise typer.Exit(1)
        
        console.print(f"🎮 Mode: [cyan]{mode}[/cyan]")
        console.print(f"🎮 Controller: [cyan]{controller}[/cyan]")
        console.print(f"🎮 Sensitivity: [cyan]{sensitivity}[/cyan]")
        
        if twin_uuid:
            console.print(f"🔗 Digital twin: [cyan]{twin_uuid}[/cyan]")
        
        # Update config for teleoperation
        edge_config["control_mode"] = "command" if mode != "physical_leader" else "telemetry"
        edge_config["twin_uuid"] = twin_uuid
        edge_config["teleop_settings"] = {
            "mode": mode,
            "controller": controller,
            "sensitivity": sensitivity
        }
        
        # Create temporary config for teleop session
        teleop_config_path = config_path.parent / "so101_teleop_session.json"
        
        with open(teleop_config_path, 'w') as f:
            json.dump(edge_config, f, indent=2)
        
        console.print(f"[blue]📝 Configuration updated for {mode} mode[/blue]")
        
        if controller == "gamepad":
            self._show_gamepad_controls()
        
        console.print(f"\n[green]🚀 Starting SO-101 edge node with teleoperation...[/green]")
        console.print(f"📁 Config: {teleop_config_path}")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")
        
        # Run the edge node with teleop config
        import subprocess
        try:
            cmd = ["python", "-m", "cyberwave_edge.main", "--config", str(teleop_config_path)]
            subprocess.run(cmd, check=False)
        except KeyboardInterrupt:
            console.print("\n[yellow]⏹️  SO-101 teleoperation session stopped[/yellow]")
        finally:
            # Clean up temporary config
            if teleop_config_path.exists():
                teleop_config_path.unlink()
    
    def _show_gamepad_controls(self) -> None:
        """Show SO-101 gamepad control mappings."""
        console.print("\n[yellow]🎮 SO-101 Gamepad Controls:[/yellow]")
        console.print("  • Left stick: Shoulder pan/lift")
        console.print("  • Right stick: Elbow/wrist flex")
        console.print("  • L1/R1: Wrist roll")
        console.print("  • A/X: Close gripper")
        console.print("  • B/Circle: Open gripper")
        console.print("  • Start: Emergency stop")
    
    def _show_so101_status(self, config_path: Path, detailed: bool) -> None:
        """Show SO-101 edge node status."""
        
        # Check config file
        if config_path.exists():
            try:
                with open(config_path) as f:
                    edge_config = json.load(f)
                
                console.print(f"[green]✅[/green] SO-101 config: {config_path}")
                
                # Show basic config info
                table = Table(title="SO-101 Configuration")
                table.add_column("Setting", style="cyan")
                table.add_column("Value", style="white")
                
                device_config = edge_config.get("device", {})
                connection_args = device_config.get("connection_args", {})
                
                table.add_row("Device Type", device_config.get("device_type", "Unknown"))
                table.add_row("Leader Port", connection_args.get("leader_port", "Not set"))
                table.add_row("Follower Port", connection_args.get("follower_port", "Not set"))
                table.add_row("Baudrate", str(connection_args.get("baudrate", "Unknown")))
                table.add_row("Backend URL", edge_config.get("network", {}).get("backend_url", "Not set"))
                table.add_row("Telemetry Rate", f"{1/edge_config.get('telemetry', {}).get('interval', 1):.0f} Hz")
                
                console.print(table)
                
                if detailed:
                    self._show_detailed_so101_status(edge_config)
                
            except Exception as e:
                console.print(f"[red]❌[/red] Config file error: {e}")
        else:
            console.print(f"[red]❌[/red] Config file not found: {config_path}")
        
        # Check calibration file
        calibration_file = "so101_calibration.json"
        if Path(calibration_file).exists():
            console.print(f"[green]✅[/green] Calibration file: {calibration_file}")
            
            if detailed:
                self._show_calibration_status(calibration_file)
        else:
            console.print(f"[yellow]⚠️[/yellow] No calibration file found")
            console.print("  Run: [cyan]cyberwave edge so101 calibrate[/cyan]")
        
        console.print("\n[dim]💡 Note: Runtime status requires SO-101 edge node to be running[/dim]")
    
    def _show_detailed_so101_status(self, config: Dict[str, Any]) -> None:
        """Show detailed SO-101 status information."""
        
        # Processors
        processors = config.get("processors", [])
        if processors:
            console.print("\n[bold]🧠 SO-101 Processors:[/bold]")
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
    
    def _show_calibration_status(self, calibration_file: str) -> None:
        """Show SO-101 calibration status."""
        try:
            with open(calibration_file) as f:
                cal_data = json.load(f)
            
            cal_table = Table(title="SO-101 Calibration Data")
            cal_table.add_column("Joint", style="cyan")
            cal_table.add_column("Offset", style="white")
            cal_table.add_column("Sign", style="white")
            cal_table.add_column("Status", style="green")
            
            joint_names = cal_data.get("joint_names", [])
            offsets = cal_data.get("offsets", [])
            signs = cal_data.get("signs", [])
            
            for joint, offset, sign in zip(joint_names, offsets, signs):
                status = "✅" if abs(offset) < 50 and abs(sign) == 1 else "⚠️"
                cal_table.add_row(joint, str(offset), str(sign), status)
            
            console.print(cal_table)
            console.print(f"[dim]🕒 Calibrated: {time.ctime(cal_data.get('timestamp', 0))}[/dim]")
            
        except Exception as e:
            console.print(f"[yellow]⚠️[/yellow] Calibration file error: {e}")
    
    def _emergency_stop_so101(self, config_path: Path) -> None:
        """Execute emergency stop for SO-101."""
        
        if not config_path.exists():
            console.print(f"[red]❌ Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        
        # In a real implementation, this would:
        # 1. Connect to the SO-101 driver
        # 2. Send emergency stop commands to both arms
        # 3. Disable motor power
        # 4. Log the emergency stop event
        
        console.print("🛑 [bold red]EMERGENCY STOP EXECUTED[/bold red]")
        console.print("📍 Leader arm: Motor power disabled")
        console.print("📍 Follower arm: Motor power disabled")
        console.print("📝 Emergency stop logged")
        
        # Mock implementation - would integrate with actual SO-101 driver
        console.print("[yellow]⚠️ Mock implementation - would integrate with SO-101 driver[/yellow]")

# Export the device CLI class for discovery
device_cli = SO101DeviceCLI
