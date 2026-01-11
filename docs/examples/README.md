# Cyberwave CLI Examples

Practical guides for common use cases.

## Getting Started

- **[Quick Start](./quick_start.md)** - Get running in 5 minutes

## Use Cases

- **[Video Security System](./video_security_system.md)** - Complete security camera setup with debugging tips
- **[Motion Detection](./motion_detection.md)** - Configure ML-based detection with smart event filtering
- **[Multi-Camera Setup](./multi_camera.md)** - Deploy multiple cameras with a single edge

## Quick Reference

### Common Commands

```bash
# Configure CLI
cyberwave-cli configure --token YOUR_TOKEN

# Discover cameras
cyberwave-cli scan

# Setup camera
cyberwave-cli camera setup --source "rtsp://..." --name "My Camera"

# List resources
cyberwave-cli twin list
cyberwave-cli workflow list
cyberwave-cli model list --edge-only

# Manage workflows
cyberwave-cli workflow create --template motion-detection
cyberwave-cli workflow activate UUID

# Edge management
cyberwave-cli edge sync-workflows --twin-uuid UUID
cyberwave-cli edge list-models --twin-uuid UUID
```

### Event Emission Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `on_enter` | New objects appear | Security alerts |
| `on_change` | Count/classes change | Occupancy monitoring |
| `always` | Every inference | Debugging, tracking |

### Model Configuration

```json
{
  "classes": ["person", "car"],
  "confidence_threshold": 0.5,
  "inference_fps": 2.0,
  "emit_event": {
    "enabled": true,
    "event_type": "person_detected",
    "emit_mode": "on_enter",
    "cooldown_seconds": 10.0
  }
}
```

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `CYBERWAVE_API_URL` | Backend API URL | `http://localhost:8000` |
| `CYBERWAVE_TOKEN` | API token | `abc123...` |
| `CYBERWAVE_TWIN_UUID` | Default twin | `uuid-here` |
| `CAMERAS` | Camera config JSON | `{"cam1": {...}}` |
| `MODELS` | Model config JSON | `{"yolo": {...}}` |
| `CYBERWAVE_LOCAL_ICE` | Disable TURN | `true` |
