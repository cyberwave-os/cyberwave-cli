"""
Configuration Schema Definitions

Defines typed configuration structures for edge nodes, devices, and processors.
Provides validation, serialization, and environment-specific configurations.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Union
from enum import Enum
import json
import os

class LogLevel(Enum):
    """Logging levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class ControlMode(Enum):
    """Edge node control modes."""
    TELEMETRY = "telemetry"      # Send telemetry only
    COMMAND = "command"          # Accept commands from cloud
    HYBRID = "hybrid"            # Both telemetry and commands

class Environment(Enum):
    """Deployment environments."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

@dataclass
class NetworkConfig:
    """Network configuration."""
    backend_url: str
    timeout: float = 30.0
    retry_attempts: int = 3
    retry_delay: float = 1.0
    use_ssl: bool = True
    verify_ssl: bool = True

@dataclass
class AuthConfig:
    """Authentication configuration."""
    access_token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    device_token: Optional[str] = None
    use_device_token: bool = True
    token_refresh_threshold: int = 300  # seconds

@dataclass  
class DeviceConfig:
    """Device-specific configuration."""
    device_id: Optional[str] = None
    device_name: str = "edge-device"
    device_type: str = "generic"
    auto_register: bool = True
    
    # Hardware configuration
    port: Optional[str] = None
    baudrate: int = 115200
    connection_args: Dict[str, Any] = field(default_factory=dict)
    
    # Device capabilities
    capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ProcessorConfig:
    """Processor configuration."""
    name: str
    enabled: bool = True
    priority: int = 100
    config: Dict[str, Any] = field(default_factory=dict)
    
    # Resource limits
    max_memory_mb: Optional[int] = None
    max_cpu_percent: Optional[float] = None
    timeout_seconds: Optional[float] = None

@dataclass
class TelemetryConfig:
    """Telemetry configuration."""
    enabled: bool = True
    interval: float = 1.0  # seconds
    batch_size: int = 10
    compression: bool = True
    
    # Data filtering
    include_fields: Optional[List[str]] = None
    exclude_fields: Optional[List[str]] = None
    
    # Buffer settings
    buffer_size: int = 1000
    flush_interval: float = 5.0

@dataclass
class HealthConfig:
    """Health monitoring configuration."""
    enabled: bool = True
    check_interval: float = 30.0
    heartbeat_interval: float = 60.0
    
    # Health checks
    check_disk_space: bool = True
    check_memory: bool = True
    check_cpu: bool = True
    check_network: bool = True
    
    # Thresholds
    disk_threshold_percent: float = 90.0
    memory_threshold_percent: float = 85.0
    cpu_threshold_percent: float = 80.0

@dataclass
class SecurityConfig:
    """Security configuration."""
    enable_encryption: bool = True
    validate_certificates: bool = True
    allowed_commands: Optional[List[str]] = None
    command_rate_limit: Optional[float] = None
    
    # Local security
    file_permissions: str = "600"
    log_sensitive_data: bool = False

@dataclass
class EdgeConfig:
    """Complete edge node configuration."""
    
    # Node identity
    node_id: Optional[str] = None
    node_name: str = "edge-node"
    environment: Environment = Environment.DEVELOPMENT
    
    # Project and organization
    project_id: Optional[int] = None
    organization_id: Optional[str] = None
    twin_uuid: Optional[str] = None
    
    # Core operation
    control_mode: ControlMode = ControlMode.TELEMETRY
    loop_hz: float = 20.0
    log_level: LogLevel = LogLevel.INFO
    
    # Configuration sections
    network: NetworkConfig = field(default_factory=lambda: NetworkConfig(backend_url=""))
    auth: AuthConfig = field(default_factory=AuthConfig)
    device: DeviceConfig = field(default_factory=DeviceConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    
    # Processors
    processors: List[ProcessorConfig] = field(default_factory=list)
    
    # Robot-specific (legacy compatibility)
    robot_type: Optional[str] = None
    robot_port: Optional[str] = None
    robot_args: Dict[str, Any] = field(default_factory=dict)
    
    # Deprecated fields for backward compatibility
    auto_register_device: Optional[bool] = None
    use_device_token: Optional[bool] = None
    heartbeat_interval: Optional[float] = None
    reconnect_attempts: Optional[int] = None
    reconnect_delay: Optional[float] = None
    
    # Additional configuration
    custom_config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EdgeConfig':
        """Create EdgeConfig from dictionary."""
        # Extract nested configurations
        network_data = data.pop('network', {})
        if isinstance(network_data, dict):
            # Handle backend_url at root level for backward compatibility
            if 'backend_url' not in network_data and 'backend_url' in data:
                network_data['backend_url'] = data.pop('backend_url')
            network = NetworkConfig(**network_data)
        else:
            network = NetworkConfig(backend_url=data.get('backend_url', ''))
        
        auth_data = data.pop('auth', {})
        # Handle auth fields at root level for backward compatibility  
        for field_name in ['access_token', 'username', 'password', 'device_token', 'use_device_token']:
            if field_name not in auth_data and field_name in data:
                auth_data[field_name] = data.pop(field_name)
        
        # If no access_token and use_device_token is True, try to get CLI token
        if not auth_data.get('access_token') and not auth_data.get('device_token') and auth_data.get('use_device_token', True):
            try:
                from cyberwave import Client
                client = Client()
                if client._access_token:
                    auth_data['access_token'] = client._access_token
                    auth_data['use_device_token'] = False  # Use access token instead
            except Exception:
                pass  # Fallback to existing behavior
        
        auth = AuthConfig(**auth_data)
        
        device_data = data.pop('device', {})
        # Handle device fields at root level for backward compatibility
        for field_name in ['device_id', 'device_name', 'device_type']:
            if field_name not in device_data and field_name in data:
                device_data[field_name] = data.pop(field_name)
        
        # Handle auto_register_device -> auto_register mapping
        if 'auto_register_device' in data and 'auto_register' not in device_data:
            device_data['auto_register'] = data.get('auto_register_device')
        elif 'auto_register' in data and 'auto_register' not in device_data:
            device_data['auto_register'] = data.get('auto_register')
        
        # Handle robot fields for device config
        if 'robot_port' in data and 'port' not in device_data:
            device_data['port'] = data.get('robot_port')
        if 'robot_type' in data and 'device_type' not in device_data:
            device_data['device_type'] = f"robot/{data.get('robot_type')}"
        
        device = DeviceConfig(**device_data)
        
        telemetry_data = data.pop('telemetry', {})
        telemetry = TelemetryConfig(**telemetry_data)
        
        health_data = data.pop('health', {})
        health = HealthConfig(**health_data)
        
        security_data = data.pop('security', {})
        security = SecurityConfig(**security_data)
        
        processors_data = data.pop('processors', [])
        processors = [ProcessorConfig(**p) if isinstance(p, dict) else p for p in processors_data]
        
        # Handle enum conversions
        if 'environment' in data and isinstance(data['environment'], str):
            data['environment'] = Environment(data['environment'])
        
        if 'control_mode' in data and isinstance(data['control_mode'], str):
            data['control_mode'] = ControlMode(data['control_mode'])
        
        if 'log_level' in data and isinstance(data['log_level'], str):
            data['log_level'] = LogLevel(data['log_level'])
        
        # Create config with extracted sections
        return cls(
            network=network,
            auth=auth,
            device=device,
            telemetry=telemetry,
            health=health,
            security=security,
            processors=processors,
            **data
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert EdgeConfig to dictionary."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Convert EdgeConfig to JSON string."""
        def enum_serializer(obj):
            if isinstance(obj, Enum):
                return obj.value
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        return json.dumps(self.to_dict(), indent=indent, default=enum_serializer)

    @classmethod
    def from_json(cls, json_str: str) -> 'EdgeConfig':
        """Create EdgeConfig from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        # Required fields
        if not self.network.backend_url:
            errors.append("backend_url is required")
        
        if not self.auth.access_token and not (self.auth.username and self.auth.password):
            errors.append("Either access_token or username/password is required")
        
        # Validation ranges
        if self.loop_hz <= 0:
            errors.append("loop_hz must be positive")
        
        if self.telemetry.interval <= 0:
            errors.append("telemetry.interval must be positive")
        
        # Device validation
        if self.device.auto_register and not self.project_id:
            errors.append("project_id is required when auto_register is enabled")
        
        return errors

    def merge_environment_overrides(self) -> None:
        """Apply environment-specific overrides."""
        # Apply environment-based defaults
        if self.environment == Environment.PRODUCTION:
            self.log_level = LogLevel.WARNING
            self.security.log_sensitive_data = False
            self.security.validate_certificates = True
        elif self.environment == Environment.DEVELOPMENT:
            self.log_level = LogLevel.DEBUG
            self.security.validate_certificates = False
        
        # Apply environment variables
        env_overrides = {
            'EDGE_LOG_LEVEL': 'log_level',
            'EDGE_LOOP_HZ': 'loop_hz',
            'EDGE_BACKEND_URL': 'network.backend_url',
            'EDGE_ACCESS_TOKEN': 'auth.access_token',
            'EDGE_NODE_ID': 'node_id',
            'EDGE_PROJECT_ID': 'project_id'
        }
        
        for env_var, config_path in env_overrides.items():
            value = os.environ.get(env_var)
            if value:
                self._set_nested_value(config_path, value)

    def _set_nested_value(self, path: str, value: str) -> None:
        """Set nested configuration value from dot-separated path."""
        parts = path.split('.')
        obj = self
        
        for part in parts[:-1]:
            obj = getattr(obj, part)
        
        # Type conversion
        final_attr = parts[-1]
        current_value = getattr(obj, final_attr)
        
        if isinstance(current_value, bool):
            value = value.lower() in ('true', '1', 'yes', 'on')
        elif isinstance(current_value, int):
            value = int(value)
        elif isinstance(current_value, float):
            value = float(value)
        elif isinstance(current_value, Enum):
            # Try to convert to the same enum type
            enum_type = type(current_value)
            value = enum_type(value)
        
        setattr(obj, final_attr, value)

    def get_legacy_dict(self) -> Dict[str, Any]:
        """Get configuration in legacy format for backward compatibility."""
        return {
            "robot_type": self.robot_type or self.device.device_type.replace("robot/", ""),
            "robot_port": self.robot_port or self.device.port,
            "robot_args": self.robot_args,
            "backend_url": self.network.backend_url,
            "access_token": self.auth.access_token,
            "username": self.auth.username,
            "password": self.auth.password,
            "device_id": self.device.device_id,
            "project_id": self.project_id,
            "device_name": self.device.device_name,
            "device_type": self.device.device_type,
            "auto_register_device": self.device.auto_register,
            "use_device_token": self.auth.use_device_token,
            "control_mode": self.control_mode.value,
            "twin_uuid": self.twin_uuid,
            "loop_hz": self.loop_hz
        }
