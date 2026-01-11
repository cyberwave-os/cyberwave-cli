# Multi-Camera Setup

Deploy multiple cameras with a single edge service.

## Overview

The Cyberwave edge service supports multiple concurrent video streams. This guide covers:

- Discovering multiple cameras
- Configuring multi-camera environments
- Running a single edge for multiple cameras
- Per-camera ML model configuration

## Discovery

### Scan Network for Cameras

```bash
# Scan local network
cyberwave-cli scan

# Scan specific subnet
cyberwave-cli scan --network 192.168.1.0/24

# Scan with credentials for RTSP
cyberwave-cli scan --rtsp-user admin --rtsp-password secret
```

### Example Output

```
Discovered devices:
  ├── 192.168.1.100 (Hikvision DS-2CD2143)
  │   └── rtsp://192.168.1.100:554/Streaming/Channels/101
  ├── 192.168.1.101 (Dahua IPC-HDW4433C-A)
  │   └── rtsp://192.168.1.101:554/cam/realmonitor?channel=1
  └── 192.168.1.102 (Generic ONVIF)
      └── rtsp://192.168.1.102:554/stream1
```

## Setup

### Option 1: CLI Setup (Recommended)

Register each camera:

```bash
# Front door camera
cyberwave-cli camera setup \
  --source "rtsp://admin:pass@192.168.1.100:554/stream" \
  --name "Front Door" \
  --environment "Home Security"

# Backyard camera  
cyberwave-cli camera setup \
  --source "rtsp://admin:pass@192.168.1.101:554/stream" \
  --name "Backyard" \
  --environment "Home Security"

# Garage camera
cyberwave-cli camera setup \
  --source "rtsp://admin:pass@192.168.1.102:554/stream" \
  --name "Garage" \
  --environment "Home Security"
```

### Option 2: Environment Variable

Configure all cameras in `.env`:

```bash
CAMERAS='{
  "front_door": {
    "source": "rtsp://admin:pass@192.168.1.100:554/stream",
    "fps": 10,
    "enabled": true
  },
  "backyard": {
    "source": "rtsp://admin:pass@192.168.1.101:554/stream",
    "fps": 10,
    "enabled": true
  },
  "garage": {
    "source": "rtsp://admin:pass@192.168.1.102:554/stream",
    "fps": 5,
    "enabled": true
  }
}'
```

## Edge Configuration

### Single Edge for Multiple Cameras

Create `.env` for the edge service:

```bash
# API Configuration
CYBERWAVE_TOKEN=your-api-token
CYBERWAVE_BASE_URL=http://localhost:8000

# Primary twin (for commands)
CYBERWAVE_TWIN_UUID=front-door-twin-uuid

# All cameras
CAMERAS='{
  "front_door": {"source": "rtsp://...", "fps": 10},
  "backyard": {"source": "rtsp://...", "fps": 10},
  "garage": {"source": "rtsp://...", "fps": 5}
}'

# Local development
CYBERWAVE_LOCAL_ICE=true
```

### Per-Camera ML Models

Configure different models per camera via workflow nodes or `MODELS` env:

```bash
MODELS='{
  "front_door_yolo": {
    "runtime": "ultralytics",
    "model_path": "yolov8s.pt",
    "camera_id": "front_door",
    "classes": ["person", "car"],
    "confidence_threshold": 0.6,
    "event_types": ["person_detected"]
  },
  "backyard_motion": {
    "runtime": "motion",
    "model_path": "mog2",
    "camera_id": "backyard",
    "event_types": ["motion_detected"]
  }
}'
```

## Workflow Setup

### Shared Workflow for All Cameras

Create a workflow with a Data Source node that targets the environment (not a specific twin):

```
[Trigger: Event] → [Condition: Check Camera] → [Action: Alert]
     ↓
Event filters:
  - source: "edge_node"
  - event_type: "person_detected"
```

### Per-Camera Workflows

Create separate workflows for different cameras with different rules:

**Front Door (High Security):**
```json
{
  "classes": ["person"],
  "emit_mode": "on_enter",
  "cooldown_seconds": 5
}
```

**Backyard (Activity Monitoring):**
```json
{
  "classes": ["person", "dog", "cat"],
  "emit_mode": "on_change",
  "cooldown_seconds": 30
}
```

**Garage (Low Frequency):**
```json
{
  "classes": ["person", "car"],
  "emit_mode": "on_enter",
  "cooldown_seconds": 60
}
```

## Running

### Start Edge Service

```bash
cd cyberwave-edges/cyberwave-edge-python
python -m cyberwave_edge.service
```

### Monitor All Cameras

```bash
# Watch edge logs
tail -f /tmp/edge.log | grep -E "camera|frame|event"

# List active models
cyberwave-cli edge list-models --twin-uuid FRONT_DOOR_UUID
```

### Start/Stop Individual Cameras

Via MQTT commands:

```bash
# Stop backyard camera
mosquitto_pub -t "localcyberwave/twin/BACKYARD_UUID/command" \
  -m '{"command": "stop_video", "camera": "backyard"}'

# Start garage camera
mosquitto_pub -t "localcyberwave/twin/GARAGE_UUID/command" \
  -m '{"command": "start_video", "camera": "garage"}'
```

## Resource Management

### CPU/GPU Considerations

| Cameras | Model | Recommended Setup |
|---------|-------|-------------------|
| 1-2 | YOLOv8s | Single edge, CPU |
| 3-5 | YOLOv8n | Single edge, GPU recommended |
| 5+ | YOLOv8n | Multiple edges or cloud offload |

### Reducing Load

1. **Lower FPS:**
   ```json
   {"fps": 5}  // Instead of 10
   ```

2. **Reduce inference frequency:**
   ```json
   {"inference_fps": 1.0}  // Once per second
   ```

3. **Use lighter models:**
   ```json
   {"model_path": "yolov8n.pt"}  // Nano instead of Small
   ```

4. **Selective detection:**
   ```json
   {"classes": ["person"]}  // Only detect people
   ```

## Viewing Streams

### In UI

Navigate to the environment and select any twin to view its stream.

### API

```bash
# List all twins in environment
curl -H "Authorization: Token $TOKEN" \
  "http://localhost:8000/api/v1/environments/ENV_UUID/twins"
```

## Troubleshooting

### Camera Not Streaming

```bash
# Check camera connectivity
ffprobe "rtsp://admin:pass@192.168.1.100:554/stream"

# Verify twin exists
cyberwave-cli twin show CAMERA_TWIN_UUID
```

### High CPU Usage

- Reduce inference FPS
- Use YOLOv8n instead of larger models
- Enable GPU if available

### Events Not Appearing

- Check each camera's workflow is active
- Verify model bindings per camera
- Check edge logs for errors
