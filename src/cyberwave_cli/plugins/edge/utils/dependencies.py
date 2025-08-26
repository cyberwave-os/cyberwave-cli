"""
Scalable Dependency Management System for Cyberwave Edge

Provides graceful handling of optional dependencies with:
- Informative error messages
- Auto-installation capabilities
- Device-specific dependency mapping
- Fallback alternatives
- Installation guidance
"""

import subprocess
import sys
import importlib
from typing import Dict, List, Optional, Tuple, Any, Callable
from dataclasses import dataclass
from enum import Enum
import logging

from rich.console import Console
from rich.prompt import Confirm
from rich.panel import Panel

console = Console()
logger = logging.getLogger(__name__)

class DependencyType(Enum):
    """Types of dependencies and their installation methods."""
    PIP = "pip"
    CONDA = "conda"
    SYSTEM = "system"
    CUSTOM = "custom"

@dataclass
class DependencySpec:
    """Specification for a dependency."""
    name: str
    package: str  # pip package name
    import_name: str  # import name in Python
    version: Optional[str] = None
    dep_type: DependencyType = DependencyType.PIP
    description: str = ""
    docs_url: Optional[str] = None
    install_guide: Optional[str] = None
    fallback_message: Optional[str] = None
    required_for: List[str] = None
    alternatives: List[str] = None

    def __post_init__(self):
        if self.required_for is None:
            self.required_for = []
        if self.alternatives is None:
            self.alternatives = []

class DependencyManager:
    """Manages dependencies for edge devices and features."""
    
    def __init__(self):
        self._dependencies: Dict[str, DependencySpec] = {}
        self._device_requirements: Dict[str, List[str]] = {}
        self._feature_requirements: Dict[str, List[str]] = {}
        self._import_cache: Dict[str, Any] = {}
        self._register_builtin_dependencies()
    
    def _register_builtin_dependencies(self):
        """Register built-in dependencies for common devices and features."""
        
        # Computer Vision Dependencies
        self.register_dependency(DependencySpec(
            name="OpenCV",
            package="opencv-python",
            import_name="cv2",
            description="Computer vision library for image and video processing",
            docs_url="https://opencv.org/",
            required_for=["computer_vision", "camera_analysis", "motion_detection"],
            fallback_message="Some camera features will be disabled without OpenCV"
        ))
        
        self.register_dependency(DependencySpec(
            name="Pillow",
            package="Pillow",
            import_name="PIL",
            description="Python Imaging Library for image processing",
            docs_url="https://pillow.readthedocs.io/",
            required_for=["image_processing", "computer_vision"],
            alternatives=["opencv-python"]
        ))
        
        # System Monitoring
        self.register_dependency(DependencySpec(
            name="psutil",
            package="psutil",
            import_name="psutil",
            description="System and process monitoring library",
            docs_url="https://psutil.readthedocs.io/",
            required_for=["health_monitoring", "system_metrics"],
            fallback_message="System health monitoring will be limited without psutil"
        ))
        
        # Robotics Dependencies
        self.register_dependency(DependencySpec(
            name="LeRobot",
            package="lerobot",
            import_name="lerobot",
            description="Robotics framework for robot control and learning",
            docs_url="https://github.com/huggingface/lerobot",
            required_for=["so101", "robot_control", "teleoperation"],
            install_guide="pip install lerobot OR clone from https://github.com/cyberwave-os/lerobot-private-temp",
            fallback_message="SO-101 and other robot features require LeRobot"
        ))
        
        self.register_dependency(DependencySpec(
            name="PySerial",
            package="pyserial",
            import_name="serial",
            description="Serial communication library for hardware interfaces",
            docs_url="https://pyserial.readthedocs.io/",
            required_for=["serial_communication", "so101", "hardware_interface"]
        ))
        
        # Input/Control Dependencies
        self.register_dependency(DependencySpec(
            name="pygame",
            package="pygame",
            import_name="pygame",
            description="Game development library used for gamepad input",
            docs_url="https://pygame.org/",
            required_for=["gamepad_control", "teleoperation", "input_devices"],
            fallback_message="Gamepad teleoperation will not be available without pygame"
        ))
        
        # Machine Learning Dependencies
        self.register_dependency(DependencySpec(
            name="MediaPipe",
            package="mediapipe",
            import_name="mediapipe",
            description="Google's ML framework for hand pose detection",
            docs_url="https://mediapipe.dev/",
            required_for=["hand_pose", "gesture_recognition", "pose_estimation"],
            fallback_message="Hand pose detection features will be disabled without MediaPipe"
        ))
        
        # Network Dependencies
        self.register_dependency(DependencySpec(
            name="aiohttp",
            package="aiohttp",
            import_name="aiohttp",
            description="Async HTTP client/server framework",
            docs_url="https://docs.aiohttp.org/",
            required_for=["http_client", "async_requests", "camera_discovery"],
            alternatives=["requests"]
        ))
        
        # Audio/Video Dependencies
        self.register_dependency(DependencySpec(
            name="FFmpeg-python",
            package="ffmpeg-python",
            import_name="ffmpeg",
            description="Python bindings for FFmpeg multimedia framework",
            docs_url="https://ffmpeg-python.readthedocs.io/",
            required_for=["video_processing", "streaming", "recording"],
            install_guide="pip install ffmpeg-python (requires FFmpeg to be installed separately)",
            fallback_message="Video recording and streaming features require FFmpeg"
        ))
        
        # Boston Dynamics Spot
        self.register_dependency(DependencySpec(
            name="Spot SDK",
            package="bosdyn-client",
            import_name="bosdyn.client",
            description="Boston Dynamics Spot Robot SDK",
            docs_url="https://dev.bostondynamics.com/",
            required_for=["spot", "boston_dynamics"],
            install_guide="pip install bosdyn-client bosdyn-mission bosdyn-choreography-client",
            fallback_message="Boston Dynamics Spot features require the official Spot SDK"
        ))
        
        # DJI Tello
        self.register_dependency(DependencySpec(
            name="DJITelloPy",
            package="djitellopy",
            import_name="djitellopy",
            description="DJI Tello drone control library",
            docs_url="https://djitellopy.readthedocs.io/",
            required_for=["tello", "dji_drone"],
            fallback_message="DJI Tello drone features require DJITelloPy"
        ))
        
        # Register device-specific requirements
        self._device_requirements.update({
            "camera/ip": ["opencv-python", "aiohttp", "pillow"],
            "camera/nvr": ["opencv-python", "aiohttp", "pillow"],
            "robot/so-101": ["lerobot", "pyserial", "pygame"],
            "robot/spot": ["bosdyn-client"],
            "drone/tello": ["djitellopy", "opencv-python"],
            "hand_pose": ["mediapipe", "opencv-python"],
            "computer_vision": ["opencv-python", "pillow"],
            "health_monitoring": ["psutil"]
        })
        
        # Register feature-specific requirements
        self._feature_requirements.update({
            "motion_detection": ["opencv-python"],
            "object_detection": ["opencv-python"],
            "face_recognition": ["opencv-python"],
            "hand_pose_estimation": ["mediapipe"],
            "gamepad_control": ["pygame"],
            "serial_communication": ["pyserial"],
            "video_streaming": ["opencv-python", "ffmpeg-python"],
            "system_monitoring": ["psutil"],
            "async_networking": ["aiohttp"]
        })
    
    def register_dependency(self, spec: DependencySpec):
        """Register a new dependency specification."""
        self._dependencies[spec.package] = spec
        logger.debug(f"Registered dependency: {spec.name} ({spec.package})")
    
    def get_device_dependencies(self, device_type: str) -> List[DependencySpec]:
        """Get all dependencies required for a device type."""
        package_names = self._device_requirements.get(device_type, [])
        return [self._dependencies[pkg] for pkg in package_names if pkg in self._dependencies]
    
    def get_feature_dependencies(self, feature: str) -> List[DependencySpec]:
        """Get all dependencies required for a feature."""
        package_names = self._feature_requirements.get(feature, [])
        return [self._dependencies[pkg] for pkg in package_names if pkg in self._dependencies]
    
    def check_dependency(self, package_name: str) -> Tuple[bool, Optional[Any]]:
        """Check if a dependency is available and return the module if found."""
        if package_name in self._import_cache:
            return True, self._import_cache[package_name]
        
        spec = self._dependencies.get(package_name)
        if not spec:
            logger.warning(f"Unknown dependency: {package_name}")
            return False, None
        
        try:
            module = importlib.import_module(spec.import_name)
            self._import_cache[package_name] = module
            return True, module
        except ImportError:
            return False, None
    
    def require_dependency(
        self, 
        package_name: str, 
        context: str = "this feature",
        auto_install: bool = False,
        silent: bool = False
    ) -> Tuple[bool, Optional[Any]]:
        """
        Require a dependency with graceful handling.
        
        Args:
            package_name: Name of the package to require
            context: Context description for error messages
            auto_install: Whether to attempt auto-installation
            silent: Whether to suppress error messages
            
        Returns:
            (success, module) tuple
        """
        available, module = self.check_dependency(package_name)
        
        if available:
            return True, module
        
        spec = self._dependencies.get(package_name)
        if not spec:
            if not silent:
                console.print(f"[red]❌ Unknown dependency: {package_name}[/red]")
            return False, None
        
        if not silent:
            self._show_dependency_error(spec, context)
        
        if auto_install and self._can_auto_install():
            if not silent:
                console.print(f"[blue]🔧 Attempting to install {spec.name}...[/blue]")
            
            if self._install_dependency(spec):
                # Try importing again after installation
                available, module = self.check_dependency(package_name)
                if available:
                    if not silent:
                        console.print(f"[green]✅ Successfully installed {spec.name}[/green]")
                    return True, module
        
        return False, None
    
    def _show_dependency_error(self, spec: DependencySpec, context: str):
        """Show a comprehensive dependency error message."""
        
        error_content = [
            f"[bold red]Missing Dependency: {spec.name}[/bold red]",
            f"[dim]Required for: {context}[/dim]",
            "",
            f"[bold]Description:[/bold] {spec.description}",
        ]
        
        # Installation instructions
        error_content.append("")
        error_content.append("[bold]📦 Installation:[/bold]")
        
        if spec.install_guide:
            error_content.append(f"  {spec.install_guide}")
        else:
            if spec.dep_type == DependencyType.PIP:
                error_content.append(f"  [cyan]pip install {spec.package}[/cyan]")
                if spec.version:
                    error_content.append(f"  [cyan]pip install {spec.package}=={spec.version}[/cyan]")
            elif spec.dep_type == DependencyType.CONDA:
                error_content.append(f"  [cyan]conda install {spec.package}[/cyan]")
        
        # Documentation link
        if spec.docs_url:
            error_content.append("")
            error_content.append(f"[bold]📚 Documentation:[/bold] {spec.docs_url}")
        
        # Alternatives
        if spec.alternatives:
            error_content.append("")
            error_content.append(f"[bold]🔄 Alternatives:[/bold] {', '.join(spec.alternatives)}")
        
        # Fallback message
        if spec.fallback_message:
            error_content.append("")
            error_content.append(f"[yellow]⚠️ {spec.fallback_message}[/yellow]")
        
        console.print(Panel(
            "\n".join(error_content),
            title="🚫 Dependency Missing",
            border_style="red"
        ))
    
    def _can_auto_install(self) -> bool:
        """Check if auto-installation is possible."""
        try:
            # Check if pip is available
            subprocess.run([sys.executable, "-m", "pip", "--version"], 
                         capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            try:
                # Check if pip3 is available
                subprocess.run(["pip3", "--version"], 
                             capture_output=True, check=True)
                return True
            except (subprocess.CalledProcessError, FileNotFoundError):
                return False
    
    def _install_dependency(self, spec: DependencySpec) -> bool:
        """Attempt to install a dependency."""
        if spec.dep_type != DependencyType.PIP:
            console.print(f"[yellow]⚠️ Auto-installation not supported for {spec.dep_type.value} packages[/yellow]")
            return False
        
        package_spec = spec.package
        if spec.version:
            package_spec = f"{spec.package}=={spec.version}"
        
        try:
            # Try with current Python's pip
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package_spec],
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode == 0:
                return True
            else:
                logger.error(f"pip install failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            console.print("[red]❌ Installation timed out[/red]")
        except Exception as e:
            logger.error(f"Installation error: {e}")
        
        # Try with pip3 as fallback
        try:
            result = subprocess.run(
                ["pip3", "install", package_spec],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                return True
            else:
                logger.error(f"pip3 install failed: {result.stderr}")
                
        except Exception as e:
            logger.error(f"pip3 installation error: {e}")
        
        return False
    
    def check_device_dependencies(
        self, 
        device_type: str, 
        auto_install: bool = False,
        interactive: bool = True
    ) -> Tuple[List[str], List[str]]:
        """
        Check all dependencies for a device type.
        
        Returns:
            (available_deps, missing_deps) tuple
        """
        dependencies = self.get_device_dependencies(device_type)
        available = []
        missing = []
        
        for spec in dependencies:
            is_available, _ = self.check_dependency(spec.package)
            if is_available:
                available.append(spec.package)
            else:
                missing.append(spec.package)
                
                if auto_install and interactive:
                    install_prompt = f"Install {spec.name} for {device_type}?"
                    if Confirm.ask(install_prompt):
                        success, _ = self.require_dependency(
                            spec.package, 
                            device_type,
                            auto_install=True
                        )
                        if success:
                            available.append(spec.package)
                            missing.remove(spec.package)
        
        return available, missing
    
    def show_device_requirements(self, device_type: str):
        """Show all requirements for a device type."""
        dependencies = self.get_device_dependencies(device_type)
        
        if not dependencies:
            console.print(f"[green]✅ No additional dependencies required for {device_type}[/green]")
            return
        
        console.print(f"\n[bold]📋 Dependencies for {device_type}:[/bold]")
        
        for spec in dependencies:
            is_available, _ = self.check_dependency(spec.package)
            status = "[green]✅ Installed[/green]" if is_available else "[red]❌ Missing[/red]"
            console.print(f"  • {spec.name}: {status}")
            if not is_available:
                console.print(f"    [dim]Install: pip install {spec.package}[/dim]")
        
        missing_count = sum(1 for spec in dependencies if not self.check_dependency(spec.package)[0])
        if missing_count > 0:
            console.print(f"\n[yellow]⚠️ {missing_count} dependencies missing[/yellow]")
            console.print("Run with [cyan]--auto-install[/cyan] to install automatically")

# Global dependency manager instance
dependency_manager = DependencyManager()

def require_for_device(device_type: str, package_name: str, auto_install: bool = False) -> Tuple[bool, Optional[Any]]:
    """Convenience function to require a dependency for a device."""
    return dependency_manager.require_dependency(
        package_name, 
        context=f"{device_type} device", 
        auto_install=auto_install
    )

def require_for_feature(feature: str, package_name: str, auto_install: bool = False) -> Tuple[bool, Optional[Any]]:
    """Convenience function to require a dependency for a feature."""
    return dependency_manager.require_dependency(
        package_name, 
        context=f"{feature} feature", 
        auto_install=auto_install
    )

def optional_import(package_name: str, fallback_message: str = None) -> Optional[Any]:
    """
    Optional import with graceful fallback.
    
    Returns the module if available, None otherwise.
    Shows an informative message if the import fails.
    """
    available, module = dependency_manager.check_dependency(package_name)
    if not available and fallback_message:
        console.print(f"[yellow]⚠️ {fallback_message}[/yellow]")
    return module

def check_device_readiness(device_type: str, auto_install: bool = False) -> bool:
    """Check if all dependencies for a device type are satisfied."""
    available, missing = dependency_manager.check_device_dependencies(
        device_type, 
        auto_install=auto_install,
        interactive=True
    )
    
    if missing:
        console.print(f"[red]❌ {device_type} is not ready - missing {len(missing)} dependencies[/red]")
        dependency_manager.show_device_requirements(device_type)
        return False
    
    console.print(f"[green]✅ {device_type} is ready - all dependencies satisfied[/green]")
    return True

# Decorator for graceful dependency handling
def requires_dependency(package_name: str, feature_name: str = None):
    """Decorator to require dependencies for functions."""
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            context = feature_name or func.__name__
            success, module = dependency_manager.require_dependency(
                package_name, 
                context=context,
                auto_install=kwargs.pop('auto_install_deps', False)
            )
            
            if not success:
                raise ImportError(f"Required dependency {package_name} not available for {context}")
            
            return func(*args, **kwargs)
        
        wrapper.__doc__ = func.__doc__
        wrapper.__name__ = func.__name__
        return wrapper
    
    return decorator
