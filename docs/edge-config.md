# Edge Configuration System

This document describes how edge device configurations are stored and synced between the cloud and edge devices.

## Overview

Edge configurations are stored in **twin metadata** (`twin.metadata.edge_configs`) as a single binding object. This allows:

- A twin to keep one active edge binding at a time
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
    ],

    # Hardware found on the Ethernet port (see docs/wired-network-devices.md).
    # The asset describes the model; a twin's own block is merged over it.
    "network_discovery": {
        "methods": ["mdns"],                        # also "arp", "ports"
        "mdns_services": ["_flir-ircam._tcp"],
        "mac_prefixes": ["00:40:7F"],
        "link_local": True,                         # falls back to 169.254/16 without DHCP
        "tcp_ports": [554],
        "bind": {"video_device": "rtsp://{ip}/avc"} # written onto the twin once found
    }
}
```

### Twin Metadata Schema

Twins store per-edge configurations:

```python
# twin.metadata
{
    "edge_configs": {
        "edge_fingerprint": "macbook-pro-a1b2c3d4e5f6",
        "camera_config": {
            "camera_id": "default",
            "source": "rtsp://192.168.1.100:554/stream",
            "fps": 10
        },
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
    
    # Optional: template for new edges
    "default_edge_config": {
        "cameras": [],
        "models": []
    },

    # Device identity, written by `cyberwave edge install` on Linux (top level, not per edge)
    "video_device": "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920-video-index0",  # camera twins
    "audio_device": "/dev/snd/by-path/platform-xhci-hcd.1-usb-0:2:1.0",         # microphone and speaker twins
    "serial_number": "",                                                        # a hardware serial, or blank
    # A network camera instead: its stream URL, and the MAC that identifies the unit
    # "video_device": "rtsp://169.254.150.216/avc",
    "mac_address": "00:40:7f:14:8b:73"
}
```

The three identity keys pin a twin to one physical unit. The installer writes a stable udev
link into `video_device` or `audio_device` (`by-id` for a unique device, `by-path` for one of
several identical units) and leaves `serial_number` blank, because both the camera and the
microphone driver let a serial outrank the device field. `audio_device` is written only for
microphone and speaker twins: a twin that also has a camera (a webcam, a go2) gets its
`video_device` pinned and its microphone is left to its driver, which selects audio by name. A serial an operator types into the twin
is honoured as identity, and a stream URL in `video_device` marks a network camera. The rules are
described in the CLI README under `cyberwave edge install`. For a network device the identity is
`mac_address`, written next to whatever the asset's `network_discovery.bind` produces, so the same
unit is found again after its address changes.

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
       ├── Cloud: twin.metadata.edge_configs
       └── Local: ./.env (secrets only)

2. Edge service startup
   ├── Load .env (token, twin_uuid, secrets)
   ├── Connect to MQTT
   ├── Fetch twin metadata
   ├── Load edge_configs (match by edge_fingerprint)
   └── Merge with local config (local wins for secrets)
```

## Security Notes

- **Secrets** (passwords, tokens) are stored in local `.env` only, never synced to cloud
- **Configuration** (camera URLs, FPS, etc.) is synced to cloud for portability
- Device fingerprints are derived from hardware characteristics for stability

## Device discovery and saved camera sources

`cameras.json` describes discovered devices and can contain `selected_device` and
`twin_to_device` assignments. Numeric strings supplied to the writer are normalized
to integer indices for compatibility with older consumers. Stable device paths and
stream URLs remain strings; converting those into numeric indices would lose the
identity or source that the assignment is intended to preserve.

`cyberwave edge cameras --save` writes the detected inventory. Use installation-time
selection to configure twin mappings. `cyberwave edge microphones --json` exposes
current audio capture devices without writing configuration or changing audio pins.
Linux ALSA indices can change across boots, so they should not replace the existing
stable USB audio pinning workflow.

A network camera, such as a FLIR AX8 on the Ethernet port, appears in `twin_to_device`
as its stream URL. The installer finds it on the wire from the asset's
`network_discovery` metadata, writes the URL and the device's `mac_address` onto the
twin, and records the URL here so that Edge Core never hands that twin a USB camera.
[Wired network devices](wired-network-devices.md) describes that metadata, the
discovery, and the rules for several twins and several sources.

The [media troubleshooting guide](media-device-troubleshooting.md) explains discovery
failures and how to repair paths ending in `.json` that have become directories.
That filesystem error is distinct from camera mapping serialization.
