"""
V1 to V2 Migration Utilities

Provides automated migration from v1 edge configurations to v2 architecture.
Preserves all existing functionality while enabling new v2 features.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..config.schema import EdgeConfig

logger = logging.getLogger(__name__)

class V1ToV2Migrator:
    """Migrates v1 edge configurations to v2 format."""
    
    def __init__(self):
        self.v1_config: Dict[str, Any] = {}
        self.v2_config: Optional[EdgeConfig] = None
    
    def load_v1_config(self, config_path: Path) -> Dict[str, Any]:
        """Load v1 configuration file."""
        with open(config_path, 'r') as f:
            self.v1_config = json.load(f)
        
        logger.info(f"Loaded v1 config from {config_path}")
        return self.v1_config
    
    def migrate_to_v2(self) -> EdgeConfig:
        """Migrate v1 config to v2 format."""
        logger.info("Migrating v1 configuration to v2 format")
        
        # Extract v1 fields and map to v2 structure
        migrated_config = {
            # Node identity
            "node_id": self.v1_config.get("device_name", "migrated-edge-node"),
            "environment": "production",  # Default for existing deployments
            
            # Network configuration
            "network": {
                "backend_url": self.v1_config.get("backend_url"),
                "timeout": 30.0,
                "retry_attempts": 3,
                "use_ssl": True
            },
            
            # Authentication
            "auth": {
                "access_token": self.v1_config.get("access_token"),
                "username": self.v1_config.get("username"),
                "password": self.v1_config.get("password"),
                "device_token": None,
                "use_device_token": self.v1_config.get("use_device_token", True)
            },
            
            # Device configuration
            "device": {
                "device_id": str(self.v1_config.get("device_id")) if self.v1_config.get("device_id") else None,
                "device_name": self.v1_config.get("device_name", "migrated-device"),
                "device_type": self.v1_config.get("device_type", f"robot/{self.v1_config.get('robot_type', 'unknown')}"),
                "auto_register": self.v1_config.get("auto_register_device", False),
                "port": self.v1_config.get("robot_port"),
                "baudrate": self.v1_config.get("robot_args", {}).get("baudrate", 115200),
                "connection_args": self.v1_config.get("robot_args", {}),
                "capabilities": self._infer_capabilities(),
                "metadata": {
                    "migrated_from_v1": True,
                    "original_robot_type": self.v1_config.get("robot_type"),
                    "migration_timestamp": datetime.now().isoformat()
                }
            },
            
            # Core operation
            "control_mode": self.v1_config.get("control_mode", "telemetry"),
            "loop_hz": float(self.v1_config.get("loop_hz", 20)),
            "log_level": "INFO",
            
            # Project and twin
            "project_id": self.v1_config.get("project_id"),
            "twin_uuid": self.v1_config.get("twin_uuid"),
            
            # Telemetry configuration
            "telemetry": {
                "enabled": True,
                "interval": 1.0 / float(self.v1_config.get("loop_hz", 20)),
                "batch_size": 10,
                "compression": True
            },
            
            # Health monitoring
            "health": {
                "enabled": True,
                "check_interval": 30.0,
                "heartbeat_interval": 60.0
            },
            
            # Security
            "security": {
                "enable_encryption": True,
                "validate_certificates": True
            },
            
            # Processors (based on robot type and existing config)
            "processors": self._create_migrated_processors(),
            
            # Legacy compatibility
            "robot_type": self.v1_config.get("robot_type"),
            "robot_port": self.v1_config.get("robot_port"),
            "robot_args": self.v1_config.get("robot_args", {}),
            
            # Custom configuration
            "custom_config": {
                "v1_original": self.v1_config,
                "migration_notes": self._generate_migration_notes()
            }
        }
        
        self.v2_config = EdgeConfig.from_dict(migrated_config)
        logger.info("Migration to v2 format completed")
        return self.v2_config
    
    def _infer_capabilities(self) -> List[str]:
        """Infer device capabilities from v1 configuration."""
        capabilities = []
        
        robot_type = self.v1_config.get("robot_type", "").lower()
        device_type = self.v1_config.get("device_type", "").lower()
        
        # Robot capabilities
        if "so101" in robot_type or "so_arm" in robot_type:
            capabilities.extend([
                "arm_control",
                "joint_position_control", 
                "gripper_control",
                "teleoperation",
                "leader_follower",
                "calibration"
            ])
        elif "tello" in robot_type:
            capabilities.extend([
                "flight",
                "camera",
                "takeoff_landing",
                "waypoint_navigation"
            ])
        elif "spot" in robot_type:
            capabilities.extend([
                "locomotion",
                "navigation",
                "camera",
                "mapping"
            ])
        
        # Camera capabilities
        if "camera" in robot_type or "camera" in device_type:
            capabilities.extend([
                "video_streaming",
                "image_capture",
                "motion_detection",
                "object_detection"
            ])
        
        # Sensor capabilities
        if "sensor" in device_type:
            capabilities.extend([
                "sensor_data",
                "data_fusion",
                "environmental_monitoring"
            ])
        
        # Check for specific features in v1 config
        v1_features = []
        if self.v1_config.get("enable_motion_detection"):
            v1_features.append("motion_detection")
        if self.v1_config.get("enable_object_detection"):
            v1_features.append("object_detection")
        if self.v1_config.get("enable_hand_pose"):
            v1_features.append("hand_pose_tracking")
        
        capabilities.extend(v1_features)
        
        # Add generic capabilities
        capabilities.extend([
            "telemetry",
            "remote_control",
            "status_monitoring",
            "health_monitoring"
        ])
        
        return list(set(capabilities))  # Remove duplicates
    
    def _create_migrated_processors(self) -> List[Dict[str, Any]]:
        """Create migrated processors based on v1 configuration and device type."""
        processors = []
        
        robot_type = self.v1_config.get("robot_type", "").lower()
        device_type = self.v1_config.get("device_type", "").lower()
        
        # Migrate computer vision processors
        if ("camera" in robot_type or "camera" in device_type or 
            self.v1_config.get("enable_motion_detection") or 
            self.v1_config.get("enable_object_detection")):
            
            cv_config = {
                "enabled": True,
                "enable_motion_detection": self.v1_config.get("enable_motion_detection", True),
                "enable_object_detection": self.v1_config.get("enable_object_detection", False),
                "motion_detection": {
                    "motion_threshold": self.v1_config.get("motion_threshold", 0.02),
                    "min_contour_area": self.v1_config.get("min_contour_area", 500)
                },
                "target_fps": self.v1_config.get("stream_fps", 30),
                "priority": 100
            }
            
            processors.append({
                "name": "computer_vision",
                "enabled": True,
                "priority": 100,
                "config": cv_config
            })
        
        # Migrate robotics processors
        if any(term in robot_type for term in ["so101", "so_arm", "spot", "tello"]):
            # Teleoperation processor
            teleop_config = {
                "enabled": True,
                "control_mode": self.v1_config.get("control_mode", "telemetry"),
                "safety_enabled": True,
                "calibration_enabled": True,
                "priority": 50
            }
            
            processors.append({
                "name": "teleoperation",
                "enabled": True,
                "priority": 50,
                "config": teleop_config
            })
            
            # Robotics data processor
            robotics_config = {
                "enabled": True,
                "enable_anomaly_detection": True,
                "enable_performance_analysis": True,
                "anomaly_threshold": 0.1,
                "priority": 100
            }
            
            processors.append({
                "name": "robotics_data",
                "enabled": True,
                "priority": 100,
                "config": robotics_config
            })
            
            # Safety monitor
            safety_config = {
                "enabled": True,
                "emergency_stop_enabled": True,
                "joint_limit_checking": True,
                "priority": 10  # High priority for safety
            }
            
            processors.append({
                "name": "safety_monitor",
                "enabled": True,
                "priority": 10,
                "config": safety_config
            })
        
        # Migrate hand pose processor if enabled
        if self.v1_config.get("enable_hand_pose"):
            hand_pose_config = {
                "enabled": True,
                "max_num_hands": self.v1_config.get("max_num_hands", 2),
                "min_detection_confidence": self.v1_config.get("min_detection_confidence", 0.7),
                "enable_teleoperation": self.v1_config.get("enable_teleoperation", False),
                "twin_uuid": self.v1_config.get("twin_uuid"),
                "priority": 100
            }
            
            processors.append({
                "name": "hand_pose",
                "enabled": True,
                "priority": 100,
                "config": hand_pose_config
            })
        
        # Sensor fusion (always enabled)
        fusion_config = {
            "enabled": True,
            "fusion_window_ms": self.v1_config.get("fusion_window_ms", 100),
            "priority": 150
        }
        
        processors.append({
            "name": "sensor_fusion",
            "enabled": True,
            "priority": 150,
            "config": fusion_config
        })
        
        # Health monitoring (always enabled in v2)
        health_config = {
            "enabled": True,
            "check_system_resources": True,
            "check_device_connectivity": True,
            "priority": 200
        }
        
        processors.append({
            "name": "health_monitor",
            "enabled": True,
            "priority": 200,
            "config": health_config
        })
        
        return processors
    
    def _generate_migration_notes(self) -> List[str]:
        """Generate migration notes and recommendations."""
        notes = []
        
        # Check for deprecated features
        if "robot_port" in self.v1_config:
            notes.append("robot_port migrated to device.port")
        
        if "robot_args" in self.v1_config:
            notes.append("robot_args migrated to device.connection_args")
        
        if self.v1_config.get("robot_type"):
            notes.append(f"robot_type '{self.v1_config['robot_type']}' migrated to device_type")
        
        # Check for new features available
        notes.append("New v2 features available: dynamic configuration, enhanced processors, health monitoring")
        
        if not self.v1_config.get("enable_object_detection"):
            notes.append("Consider enabling object detection in computer_vision processor")
        
        if not self.v1_config.get("enable_hand_pose") and "camera" in str(self.v1_config.get("robot_type", "")):
            notes.append("Consider enabling hand pose tracking for teleoperation")
        
        return notes
    
    def save_v2_config(self, output_path: Path) -> None:
        """Save migrated v2 configuration."""
        if not self.v2_config:
            raise ValueError("No v2 config to save. Run migrate_to_v2() first.")
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            f.write(self.v2_config.to_json())
        
        logger.info(f"Saved v2 config to {output_path}")
    
    def validate_migration(self) -> List[str]:
        """Validate the migrated configuration."""
        if not self.v2_config:
            return ["No v2 config available for validation"]
        
        errors = self.v2_config.validate()
        
        if not errors:
            logger.info("✅ Migration validation passed")
        else:
            logger.warning(f"⚠️ Migration validation found {len(errors)} issues")
            for error in errors:
                logger.warning(f"  - {error}")
        
        return errors
    
    def create_migration_report(self) -> Dict[str, Any]:
        """Create a detailed migration report."""
        return {
            "migration_timestamp": datetime.now().isoformat(),
            "migration_version": "v1_to_v2",
            "source_config": {
                "robot_type": self.v1_config.get("robot_type"),
                "device_type": self.v1_config.get("device_type"),
                "backend_url": self.v1_config.get("backend_url"),
                "features": {
                    "motion_detection": self.v1_config.get("enable_motion_detection", False),
                    "object_detection": self.v1_config.get("enable_object_detection", False),
                    "hand_pose": self.v1_config.get("enable_hand_pose", False),
                    "teleoperation": self.v1_config.get("enable_teleoperation", False)
                }
            },
            "migrated_config": {
                "node_id": self.v2_config.node_id if self.v2_config else None,
                "device_type": self.v2_config.device.device_type if self.v2_config else None,
                "processor_count": len(self.v2_config.processors) if self.v2_config else 0,
                "capabilities": self.v2_config.device.capabilities if self.v2_config else []
            },
            "validation_errors": self.validate_migration(),
            "changes": {
                "new_features_added": [
                    "Dynamic configuration management",
                    "Enhanced processor framework", 
                    "Comprehensive health monitoring",
                    "Improved authentication and security",
                    "Telemetry batching and compression",
                    "Resource-aware processing",
                    "Multi-device support"
                ],
                "deprecated_features": [
                    "Static configuration only",
                    "Monolithic edge architecture",
                    "Single device limitation"
                ],
                "breaking_changes": [
                    "New configuration schema structure",
                    "Enhanced CLI interface",
                    "Updated Python API",
                    "Processor configuration format"
                ]
            },
            "recommendations": [
                "Test migrated configuration in development environment",
                "Update deployment scripts to use unified CLI",
                "Review and configure new processors",
                "Set up backend configuration service",
                "Enable health monitoring dashboards",
                "Consider enabling new CV and ML processors",
                "Update authentication to use device tokens"
            ],
            "migration_notes": self._generate_migration_notes()
        }

def migrate_v1_config_file(
    input_path: Path, 
    output_path: Optional[Path] = None,
    backup_original: bool = True
) -> Dict[str, Any]:
    """
    Convenience function to migrate a v1 config file to v2.
    
    Args:
        input_path: Path to v1 configuration file
        output_path: Path for v2 configuration (defaults to same as input)
        backup_original: Whether to backup the original file
        
    Returns:
        Migration report dictionary
    """
    if output_path is None:
        output_path = input_path
    
    migrator = V1ToV2Migrator()
    
    # Load and migrate
    migrator.load_v1_config(input_path)
    migrator.migrate_to_v2()
    
    # Create backup if requested
    if backup_original and output_path == input_path:
        backup_path = input_path.with_suffix(".v1.backup")
        input_path.rename(backup_path)
        logger.info(f"Original config backed up to {backup_path}")
    
    # Save migrated config
    migrator.save_v2_config(output_path)
    
    # Generate and return report
    report = migrator.create_migration_report()
    
    logger.info("Migration completed successfully")
    return report

def check_migration_needed(config_path: Path) -> bool:
    """
    Check if a configuration file needs migration to v2.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        True if migration is needed, False otherwise
    """
    if not config_path.exists():
        return False
    
    try:
        with open(config_path) as f:
            config_data = json.load(f)
        
        # Check for v2 indicators
        v2_indicators = [
            "network", "auth", "device", "processors", 
            "telemetry", "health", "security"
        ]
        
        has_v2_structure = any(key in config_data for key in v2_indicators)
        
        # Check for v1 indicators
        v1_indicators = [
            "robot_type", "robot_port", "robot_args"
        ]
        
        has_v1_structure = any(key in config_data for key in v1_indicators)
        
        # Needs migration if it has v1 structure but not v2 structure
        return has_v1_structure and not has_v2_structure
        
    except Exception as e:
        logger.error(f"Error checking migration status: {e}")
        return False
