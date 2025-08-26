"""
Processor Manager for Edge Nodes

Manages data processors and their lifecycle.
"""

import logging
from typing import Dict, List, Any, Optional
from .base import BaseProcessor

logger = logging.getLogger(__name__)

class ProcessorManager:
    """Manages data processors."""
    
    def __init__(self):
        self.processors: Dict[str, BaseProcessor] = {}
        self.running_processors: Dict[str, BaseProcessor] = {}
    
    def register_processor(self, name: str, processor: BaseProcessor) -> None:
        """Register a data processor."""
        self.processors[name] = processor
        logger.info(f"Registered processor: {name}")
    
    def get_processor(self, name: str) -> Optional[BaseProcessor]:
        """Get a registered processor."""
        return self.processors.get(name)
    
    async def start_processor(self, name: str) -> bool:
        """Start a specific processor."""
        processor = self.processors.get(name)
        if not processor:
            logger.error(f"Processor not found: {name}")
            return False
        
        try:
            await processor.start()
            self.running_processors[name] = processor
            logger.info(f"Started processor: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to start processor {name}: {e}")
            return False
    
    async def stop_processor(self, name: str) -> bool:
        """Stop a specific processor."""
        processor = self.running_processors.get(name)
        if not processor:
            logger.warning(f"Processor not running: {name}")
            return False
        
        try:
            await processor.stop()
            del self.running_processors[name]
            logger.info(f"Stopped processor: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to stop processor {name}: {e}")
            return False
    
    async def start_all(self) -> None:
        """Start all registered processors."""
        for name in self.processors:
            await self.start_processor(name)
    
    async def stop_all(self) -> None:
        """Stop all running processors."""
        for name in list(self.running_processors.keys()):
            await self.stop_processor(name)
    
    def get_status(self) -> Dict[str, Any]:
        """Get status of all processors."""
        return {
            "registered": list(self.processors.keys()),
            "running": list(self.running_processors.keys()),
            "total_registered": len(self.processors),
            "total_running": len(self.running_processors)
        }
