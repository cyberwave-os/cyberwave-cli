"""
Driver Manager for Edge Devices

Manages device drivers and their lifecycle.
"""

import logging
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

class BaseDriver(ABC):
    """Base class for device drivers."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.is_connected = False
    
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the device."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the device."""
        pass
    
    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Get device status."""
        pass

class DriverManager:
    """Manages device drivers."""
    
    def __init__(self):
        self.drivers: Dict[str, BaseDriver] = {}
    
    def register_driver(self, name: str, driver: BaseDriver) -> None:
        """Register a device driver."""
        self.drivers[name] = driver
        logger.info(f"Registered driver: {name}")
    
    def get_driver(self, name: str) -> Optional[BaseDriver]:
        """Get a registered driver."""
        return self.drivers.get(name)
    
    async def start_all(self) -> None:
        """Start all registered drivers."""
        for name, driver in self.drivers.items():
            try:
                await driver.connect()
                logger.info(f"Started driver: {name}")
            except Exception as e:
                logger.error(f"Failed to start driver {name}: {e}")
    
    async def stop_all(self) -> None:
        """Stop all registered drivers."""
        for name, driver in self.drivers.items():
            try:
                await driver.disconnect()
                logger.info(f"Stopped driver: {name}")
            except Exception as e:
                logger.error(f"Failed to stop driver {name}: {e}")
