"""
Device-specific CLI modules for Cyberwave Edge.

This package contains CLI implementations for specific device types:
- SO-101 robotic arms
- Spot quadruped robots  
- Tello drones
- Generic cameras and sensors

Each device module provides:
- Setup commands
- Calibration procedures
- Device-specific configuration
- Teleoperation interfaces
- Status monitoring

Devices are dynamically discovered and loaded into the edge CLI.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import typer
from pathlib import Path

class BaseDeviceCLI(ABC):
    """Base class for device-specific CLI implementations."""
    
    @property
    @abstractmethod
    def device_type(self) -> str:
        """Device type identifier (e.g., 'robot/so-101', 'drone/tello')."""
        pass
    
    @property
    @abstractmethod
    def device_name(self) -> str:
        """Human-readable device name."""
        pass
    
    @property
    @abstractmethod
    def supported_capabilities(self) -> List[str]:
        """List of device capabilities."""
        pass
    
    @abstractmethod
    def create_typer_app(self) -> typer.Typer:
        """Create and return the device-specific Typer app."""
        pass
    
    @abstractmethod
    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate device-specific configuration. Return list of errors."""
        pass
    
    @abstractmethod
    def get_default_config(self) -> Dict[str, Any]:
        """Get default configuration for this device type."""
        pass
    
    def get_processor_configs(self) -> List[Dict[str, Any]]:
        """Get default processor configurations for this device."""
        return []

def discover_device_clis() -> Dict[str, BaseDeviceCLI]:
    """Discover and instantiate all available device CLI implementations."""
    import importlib
    import pkgutil
    
    devices = {}
    
    # Discover all modules in this package
    for importer, modname, ispkg in pkgutil.iter_modules(__path__, __name__ + "."):
        if modname.endswith('_device'):  # Only load *_device.py files
            try:
                module = importlib.import_module(modname)
                
                # Look for device CLI class
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type) and 
                        issubclass(attr, BaseDeviceCLI) and 
                        attr != BaseDeviceCLI):
                        
                        device_cli = attr()
                        devices[device_cli.device_type] = device_cli
                        break
                        
            except ImportError as e:
                # Skip modules that can't be imported (missing dependencies)
                continue
    
    return devices

__all__ = ["BaseDeviceCLI", "discover_device_clis"]
