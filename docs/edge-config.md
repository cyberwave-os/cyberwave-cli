# Edge Configuration System

This document describes how edge device configurations are stored and synced between the cloud and edge devices.

## Overview

Edge configurations are stored in **twin metadata** (`twin.metadata.edge_configs`), keyed by device fingerprint. This allows:

- Multiple edge devices to connect to the same twin with different configurations
- Configuration to be pushed from cloud to edge (Steam-like)
- Configuration to persist across edge restarts

## Device Fingerprinting

Each edge device has a unique fingerprint generated from hardware characteristics:

```
fingerprint = "{hostname_prefix}-{sha256_hash[:12]}"
```

Example: `macbook-pro-a1b2c3d4e5f6`

The fingerprint can be overridden via `CYBERWAVE_EDGE_UUID` environment variable.

## Data Model

### Asset Metadata Schema

Assets define what edge runtimes are supported and the configuration schema for each:

```python
# asset.metadata
{
    # Short names for CLI
    "aliases": ["camera", "ip-cam"],
    
    # CLI command shown in catalog UI
    "cli_connect_cmd": "cyberwave twin create camera --pair",
    
    # Supported edge runtimes (can be multiple)
    "edge_runtimes": [
        {
            "name": "cyberwave-edge-python",
            "min_version": "0.2.0",
            "drivers": [
                {"package": "pyrealsense2", "optional": False},
                {"package": "ultralytics", "optional": True}
            ],
            "install_cmd": "pip install cyberwave-edge-python",
            
            # JSON Schema for configuration validation
            "config_schema": {
                "type": "object",
                "properties": {
                    "cameras": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "camera_id": {"type": "string"},
                                "source": {"type": "string"},
                                "fps": {"type": "integer", "default": 10, "maximum": 60},
                                "resolution": {"type": "string", "enum": ["VGA", "HD", "FULL_HD"]},
                                "enable_depth": {"type": "boolean", "default": False}
                            },
                            "required": ["camera_id", "source"]
                        }
                    },
                    "models": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "model_id": {"type": "string"},
                                "runtime": {"type": "string"},
                                "model_path": {"type": "string"}
                            }
                        }
                    }
                }
            }
        },
        {
            "name": "cyberwave-edge-ros2",
            "min_version": "0.1.0",
            "install_cmd": "apt install ros-humble-cyberwave-edge",
            "config_schema": {
                "type": "object",
                "properties": {
                    "ros_namespace": {"type": "string", "default": "cyberwave"},
                    "tf_prefix": {"type": "string", "default": ""}
                }
            }
        }
    ]
}
```

### Twin Metadata Schema

Twins store per-edge configurations:

```python
# twin.metadata
{
    "edge_configs": {
        # Keyed by device fingerprint
        "macbook-pro-a1b2c3d4e5f6": {
            "cameras": [
                {
                    "camera_id": "default",
                    "source": "rtsp://192.168.1.100:554/stream",
                    "fps": 10
                }
            ],
            "models": [],
            "device_info": {
                "hostname": "macbook-pro.local",
                "platform": "Darwin-arm64",
                "python_version": "3.11.0",
                "mac_address": "a4:83:e7:xx:xx:xx"
            },
            "registered_at": "2026-01-13T10:00:00Z",
            "last_sync": "2026-01-13T12:00:00Z"
        },
        "rpi4-x9y8z7w6v5u4": {
            # Another device's config
        }
    },
    
    # Optional: template for new edges
    "default_edge_config": {
        "cameras": [],
        "models": []
    }
}
```

## CLI Commands

### Twin Create (Create Twin and Optionally Pair)

```bash
# Create twin only (cloud)
cyberwave twin create camera

# Create twin + pair device in one command
cyberwave twin create camera --pair

# Create twin in specific environment
cyberwave twin create camera --environment abc-123
```

### Twin Pair (Pair to Existing Twin)

```bash
# Pair this device to an existing twin
cyberwave twin pair <twin-uuid>

# Pair with configuration options
cyberwave twin pair <twin-uuid> --camera-source "rtsp://..." --fps 15
```

### Pull Configuration

```bash
# Pull config for single twin
cyberwave edge pull --twin-uuid abc-123

# Pull configs for all twins in environment
cyberwave edge pull --environment-uuid env-456
```

### Device Info

```bash
# Show device fingerprint
cyberwave edge whoami

# Check connection status
cyberwave edge remote-status --twin-uuid abc-123
```

## Asset Resolution

The CLI resolves assets from multiple sources:

```bash
# Registry ID
cyberwave twin create unitree/go2

# Alias (defined in asset.metadata.aliases)
cyberwave twin create camera

# Local JSON file
cyberwave twin create ./my-camera.json

# URL
cyberwave twin create https://example.com/asset.json
```

## Configuration Flow

```
1. cyberwave twin create camera --pair
   ├── Resolve asset (ID/alias/file/URL)
   ├── Generate device fingerprint
   ├── Find existing twin OR create new
   ├── Configure edge (interactive)
   └── Save to:
       ├── Cloud: twin.metadata.edge_configs[fingerprint]
       └── Local: ./.env (secrets only)

2. Edge service startup
   ├── Load .env (token, twin_uuid, secrets)
   ├── Connect to MQTT
   ├── Fetch twin metadata
   ├── Load edge_configs[fingerprint]
   └── Merge with local config (local wins for secrets)
```

## Security Notes

- **Secrets** (passwords, tokens) are stored in local `.env` only, never synced to cloud
- **Configuration** (camera URLs, FPS, etc.) is synced to cloud for portability
- Device fingerprints are derived from hardware characteristics for stability
