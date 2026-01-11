# Motion Detection with ML Models

Configure intelligent motion detection using YOLO or other ML models on edge devices.

## Overview

The edge service processes video frames through ML models and emits events based on configurable rules. This guide covers:

- Setting up ML-based detection
- Configuring event emission modes
- Filtering by object classes
- Tuning for your use case

## Prerequisites

```bash
# Ensure you have a camera twin registered
cyberwave-cli twin list

# Check available edge models
cyberwave-cli model list --edge-only
```

## Workflow Configuration

### Create a Detection Workflow

1. **Via CLI:**
```bash
cyberwave-cli workflow create --template motion-detection
```

2. **Or via UI:** Navigate to Workflows → Create → Add nodes

### Workflow Nodes

A typical motion detection workflow:

```
[Data Source: Twin Video] → [Call Model: YOLOv8] → [Action: Send Alert]
```

## ML Model Configuration

### Model Selection

| Model | Speed | Accuracy | Use Case |
|-------|-------|----------|----------|
| `yolov8n` | Fast | Good | Real-time on CPU |
| `yolov8s` | Medium | Better | Balanced |
| `yolov8m` | Slower | Best | High accuracy |

### Input Parameters

Configure the **Call Model** node with these parameters:

```json
{
  "confidence_threshold": 0.5,
  "classes": ["person", "car", "dog"],
  "inference_fps": 2.0,
  "emit_event": {
    "enabled": true,
    "event_type": "motion_detected",
    "severity": "INFO",
    "emit_mode": "on_enter",
    "cooldown_seconds": 5.0
  }
}
```

## Event Emission Modes

### `on_enter` (Recommended)

Emits an event only when **new object classes** appear in the scene.

```json
{
  "emit_mode": "on_enter",
  "cooldown_seconds": 10.0
}
```

**Behavior:**
- Person walks into frame → Event emitted
- Person stays in frame → No event
- Person leaves, returns → Event emitted (after cooldown)

**Best for:** Security alerts, presence detection

### `on_change`

Emits when the detection count or classes change.

```json
{
  "emit_mode": "on_change",
  "cooldown_seconds": 5.0
}
```

**Behavior:**
- 2 people in frame → Event
- 3rd person enters → Event
- 1 person leaves → Event

**Best for:** Occupancy monitoring, crowd tracking

### `always`

Emits every inference cycle (respect cooldown).

```json
{
  "emit_mode": "always",
  "cooldown_seconds": 1.0
}
```

**Best for:** Continuous tracking, debugging

## Class Filtering

Filter detections to specific object classes:

### Person Detection Only

```json
{
  "classes": ["person"],
  "confidence_threshold": 0.6,
  "emit_event": {
    "enabled": true,
    "event_type": "person_detected",
    "emit_mode": "on_enter"
  }
}
```

### Vehicle Detection

```json
{
  "classes": ["car", "truck", "bus", "motorcycle"],
  "confidence_threshold": 0.5,
  "emit_event": {
    "enabled": true,
    "event_type": "vehicle_detected",
    "emit_mode": "on_enter"
  }
}
```

### Pet Detection

```json
{
  "classes": ["dog", "cat", "bird"],
  "confidence_threshold": 0.4,
  "emit_event": {
    "enabled": true,
    "event_type": "pet_detected",
    "emit_mode": "on_enter"
  }
}
```

## Running the Edge Service

### Start the Edge

```bash
cd cyberwave-edges/cyberwave-edge-python

# Configure environment
cat > .env << EOF
CYBERWAVE_TOKEN=your-token
CYBERWAVE_BASE_URL=http://localhost:8000
CYBERWAVE_TWIN_UUID=your-twin-uuid
CAMERAS={"default": {"source": "rtsp://..."}}
CYBERWAVE_LOCAL_ICE=true
EOF

# Run
python -m cyberwave_edge.service
```

### Sync Workflow to Edge

The edge automatically syncs workflows on startup. To force re-sync:

```bash
cyberwave-cli edge sync-workflows --twin-uuid YOUR_TWIN_UUID
```

### Check Active Models

```bash
cyberwave-cli edge list-models --twin-uuid YOUR_TWIN_UUID
```

## Viewing Events

### Via API

```bash
curl -H "Authorization: Token YOUR_TOKEN" \
  "http://localhost:8000/api/v1/events/live?environment_uuid=YOUR_ENV_UUID"
```

### Via UI

1. Open the environment in the Cyberwave UI
2. Navigate to the twin's detail page
3. View the Events panel

### Event Structure

```json
{
  "event_type": "person_detected",
  "source": "edge_node",
  "severity": "INFO",
  "data": {
    "model": "yolov8s",
    "plugin": "yolo",
    "detection_count": 2,
    "detections": [
      {
        "class": "person",
        "confidence": 0.92,
        "bbox": [120, 80, 280, 450]
      }
    ]
  }
}
```

## Troubleshooting

### No Events Emitting

1. Check edge logs for detection activity:
   ```bash
   tail -f /tmp/edge.log | grep -i "frame\|event\|detect"
   ```

2. Verify workflow is active:
   ```bash
   cyberwave-cli workflow list
   ```

3. Check model binding:
   ```bash
   cyberwave-cli edge list-models --twin-uuid YOUR_UUID
   ```

### Too Many Events

Increase cooldown or switch to `on_enter` mode:
```json
{
  "emit_mode": "on_enter",
  "cooldown_seconds": 30.0
}
```

### Missing Detections

Lower confidence threshold or check classes:
```json
{
  "confidence_threshold": 0.3,
  "classes": []  // Empty = all classes
}
```
