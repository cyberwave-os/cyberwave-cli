"""
Configuration Manager for Edge Nodes

Provides centralized configuration management with support for:
- Backend service configuration
- Local file configuration
- Environment variable overrides
- Hot reloading and validation
- Multiple configuration sources with priority
"""
from __future__ import annotations

import json
import os
import logging
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
from dataclasses import dataclass, asdict
import asyncio
from datetime import datetime, timedelta

from .schema import EdgeConfig
from .client import BackendConfigClient

logger = logging.getLogger(__name__)

class ConfigSource(Enum):
    """Configuration source types in order of priority (highest to lowest)."""
    ENVIRONMENT = "environment"        # Highest priority
    COMMAND_LINE = "command_line"      
    BACKEND_SERVICE = "backend_service"
    LOCAL_FILE = "local_file"          # Lowest priority
    DEFAULTS = "defaults"

@dataclass
class ConfigEntry:
    """Represents a configuration entry with metadata."""
    key: str
    value: Any
    source: ConfigSource
    timestamp: datetime
    metadata: Dict[str, Any] = None

class ConfigManager:
    """
    Centralized configuration manager for edge nodes.
    
    Supports multiple configuration sources with priority ordering,
    hot reloading, and backend service integration.
    """
    
    def __init__(self, 
                 node_id: Optional[str] = None,
                 backend_url: Optional[str] = None,
                 access_token: Optional[str] = None,
                 config_file: Optional[Path] = None,
                 auto_reload: bool = True,
                 reload_interval: int = 60):
        self.node_id = node_id
        self.backend_url = backend_url
        self.access_token = access_token
        self.config_file = config_file or Path.home() / ".cyberwave" / "edge.json"
        self.auto_reload = auto_reload
        self.reload_interval = reload_interval
        
        # Configuration storage
        self._config_entries: Dict[str, ConfigEntry] = {}
        self._merged_config: Dict[str, Any] = {}
        self._last_reload: Optional[datetime] = None
        
        # Backend client for remote configuration
        self._backend_client: Optional[BackendConfigClient] = None
        
        # Reload task
        self._reload_task: Optional[asyncio.Task] = None
        
        # Configuration change callbacks
        self._change_callbacks: List[callable] = []
        
        logger.info(f"ConfigManager initialized for node {node_id}")

    async def initialize(self) -> None:
        """Initialize the configuration manager."""
        # Initialize backend client if configured
        if self.backend_url and self.access_token:
            self._backend_client = BackendConfigClient(
                backend_url=self.backend_url,
                access_token=self.access_token,
                node_id=self.node_id
            )
            await self._backend_client.initialize()
        
        # Load initial configuration
        await self.reload_config()
        
        # Start auto-reload if enabled
        if self.auto_reload:
            self._start_auto_reload()

    async def reload_config(self) -> None:
        """Reload configuration from all sources."""
        logger.info("Reloading configuration from all sources")
        
        # Clear existing entries
        self._config_entries.clear()
        
        # Load from each source in reverse priority order
        await self._load_defaults()
        await self._load_from_file()
        await self._load_from_backend()
        self._load_from_environment()
        self._load_from_command_line()
        
        # Merge configurations by priority
        self._merge_configurations()
        
        # Update timestamp
        self._last_reload = datetime.now()
        
        # Notify callbacks
        await self._notify_change_callbacks()
        
        logger.info(f"Configuration reloaded successfully from {len(self._config_entries)} entries")

    async def _load_defaults(self) -> None:
        """Load default configuration values."""
        defaults = {
            "loop_hz": 20,
            "control_mode": "telemetry",
            "auto_register_device": False,
            "use_device_token": True,
            "heartbeat_interval": 30,
            "reconnect_attempts": 5,
            "reconnect_delay": 5,
            "log_level": "INFO"
        }
        
        for key, value in defaults.items():
            self._config_entries[key] = ConfigEntry(
                key=key,
                value=value,
                source=ConfigSource.DEFAULTS,
                timestamp=datetime.now()
            )

    async def _load_from_file(self) -> None:
        """Load configuration from local file."""
        if not self.config_file.exists():
            logger.info(f"Config file {self.config_file} not found, skipping")
            return
        
        try:
            with open(self.config_file, 'r') as f:
                file_config = json.load(f)
            
            for key, value in file_config.items():
                self._config_entries[key] = ConfigEntry(
                    key=key,
                    value=value,
                    source=ConfigSource.LOCAL_FILE,
                    timestamp=datetime.fromtimestamp(self.config_file.stat().st_mtime),
                    metadata={"file": str(self.config_file)}
                )
            
            logger.info(f"Loaded {len(file_config)} entries from {self.config_file}")
            
        except Exception as e:
            logger.error(f"Failed to load config file {self.config_file}: {e}")

    async def _load_from_backend(self) -> None:
        """Load configuration from backend service."""
        if not self._backend_client:
            return
        
        try:
            backend_config = await self._backend_client.get_node_config()
            
            if backend_config:
                for key, value in backend_config.items():
                    self._config_entries[key] = ConfigEntry(
                        key=key,
                        value=value,
                        source=ConfigSource.BACKEND_SERVICE,
                        timestamp=datetime.now(),
                        metadata={"backend_url": self.backend_url}
                    )
                
                logger.info(f"Loaded {len(backend_config)} entries from backend service")
            
        except Exception as e:
            logger.warning(f"Failed to load config from backend: {e}")

    def _load_from_environment(self) -> None:
        """Load configuration from environment variables."""
        env_prefix = "EDGE_"
        env_configs = {}
        
        for key, value in os.environ.items():
            if key.startswith(env_prefix):
                config_key = key[len(env_prefix):].lower()
                
                # Try to parse as JSON, fall back to string
                try:
                    parsed_value = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    parsed_value = value
                
                env_configs[config_key] = parsed_value
                
                self._config_entries[config_key] = ConfigEntry(
                    key=config_key,
                    value=parsed_value,
                    source=ConfigSource.ENVIRONMENT,
                    timestamp=datetime.now(),
                    metadata={"env_var": key}
                )
        
        if env_configs:
            logger.info(f"Loaded {len(env_configs)} entries from environment variables")

    def _load_from_command_line(self) -> None:
        """Load configuration from command line arguments."""
        # This would be populated by the CLI when starting the edge node
        # For now, we'll check for a special environment variable
        cli_config = os.environ.get("EDGE_CLI_CONFIG")
        if cli_config:
            try:
                cli_data = json.loads(cli_config)
                for key, value in cli_data.items():
                    self._config_entries[key] = ConfigEntry(
                        key=key,
                        value=value,
                        source=ConfigSource.COMMAND_LINE,
                        timestamp=datetime.now(),
                        metadata={"source": "cli"}
                    )
                
                logger.info(f"Loaded {len(cli_data)} entries from command line")
                
            except Exception as e:
                logger.error(f"Failed to parse CLI config: {e}")

    def _merge_configurations(self) -> None:
        """Merge configurations by priority (highest priority wins)."""
        self._merged_config = {}
        
        # Start with lowest priority and work up
        priority_order = [
            ConfigSource.DEFAULTS,
            ConfigSource.LOCAL_FILE,
            ConfigSource.BACKEND_SERVICE,
            ConfigSource.COMMAND_LINE,
            ConfigSource.ENVIRONMENT
        ]
        
        for source in priority_order:
            for entry in self._config_entries.values():
                if entry.source == source:
                    self._merged_config[entry.key] = entry.value

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key."""
        return self._merged_config.get(key, default)

    def get_all(self) -> Dict[str, Any]:
        """Get all merged configuration."""
        return self._merged_config.copy()

    def get_entry(self, key: str) -> Optional[ConfigEntry]:
        """Get configuration entry with metadata."""
        # Find the highest priority entry for this key
        for source in [ConfigSource.ENVIRONMENT, ConfigSource.COMMAND_LINE, 
                      ConfigSource.BACKEND_SERVICE, ConfigSource.LOCAL_FILE, 
                      ConfigSource.DEFAULTS]:
            for entry in self._config_entries.values():
                if entry.key == key and entry.source == source:
                    return entry
        return None

    def set(self, key: str, value: Any, source: ConfigSource = ConfigSource.COMMAND_LINE) -> None:
        """Set configuration value."""
        self._config_entries[key] = ConfigEntry(
            key=key,
            value=value,
            source=source,
            timestamp=datetime.now(),
            metadata={"set_programmatically": True}
        )
        self._merge_configurations()

    def to_edge_config(self) -> EdgeConfig:
        """Convert merged configuration to typed EdgeConfig."""
        return EdgeConfig.from_dict(self._merged_config)

    async def update_backend_config(self, config: Dict[str, Any]) -> bool:
        """Update configuration on backend service."""
        if not self._backend_client:
            logger.warning("No backend client available for config update")
            return False
        
        try:
            success = await self._backend_client.update_node_config(config)
            if success:
                # Reload to get the updated config
                await self.reload_config()
            return success
        except Exception as e:
            logger.error(f"Failed to update backend config: {e}")
            return False

    def add_change_callback(self, callback: callable) -> None:
        """Add callback for configuration changes."""
        self._change_callbacks.append(callback)

    def remove_change_callback(self, callback: callable) -> None:
        """Remove callback for configuration changes."""
        if callback in self._change_callbacks:
            self._change_callbacks.remove(callback)

    async def _notify_change_callbacks(self) -> None:
        """Notify all change callbacks."""
        for callback in self._change_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(self._merged_config)
                else:
                    callback(self._merged_config)
            except Exception as e:
                logger.error(f"Error in config change callback: {e}")

    def _start_auto_reload(self) -> None:
        """Start automatic configuration reloading."""
        async def reload_loop():
            while True:
                try:
                    await asyncio.sleep(self.reload_interval)
                    await self.reload_config()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in auto-reload: {e}")
        
        self._reload_task = asyncio.create_task(reload_loop())

    async def shutdown(self) -> None:
        """Shutdown configuration manager."""
        if self._reload_task:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
        
        if self._backend_client:
            await self._backend_client.shutdown()
        
        logger.info("ConfigManager shutdown complete")

    def get_status(self) -> Dict[str, Any]:
        """Get configuration manager status."""
        return {
            "node_id": self.node_id,
            "last_reload": self._last_reload.isoformat() if self._last_reload else None,
            "config_entries": len(self._config_entries),
            "sources": list(set(entry.source.value for entry in self._config_entries.values())),
            "backend_available": self._backend_client is not None,
            "auto_reload": self.auto_reload,
            "reload_interval": self.reload_interval
        }
