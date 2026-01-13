# Local Testing Best Practices

This guide documents the working configuration for testing video streaming locally on macOS with Docker Desktop.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              macOS Host                                      │
│                                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────────────────────┐ │
│  │   Browser    │     │  Mediasoup   │     │      Docker Desktop          │ │
│  │  (Frontend)  │────▶│    SFU       │◀────│  ┌──────────────────────┐   │ │
│  │              │     │  Port 8080   │     │  │   Edge Container     │   │ │
│  └──────────────┘     │              │     │  │   (172.20.0.x)       │   │ │
│         │             │ ANNOUNCED_IP │     │  │                      │   │ │
│         │             │ 192.168.65.254│     │  │  - Camera capture    │   │ │
│         │             └──────────────┘     │  │  - WebRTC streaming  │   │ │
│         │                    ▲             │  │  - ML inference      │   │ │
│         │                    │             │  └──────────────────────┘   │ │
│         │                    │             │            │                 │ │
│         │                    │             │            │ host.docker.    │ │
│         │                    │             │            │ internal        │ │
│         │                    │             │            ▼                 │ │
│         │             ┌──────────────┐     │  ┌──────────────────────┐   │ │
│         └────────────▶│    MQTT      │◀────│──│   192.168.65.254     │   │ │
│                       │   Mosquitto  │     │  │   (Docker gateway)   │   │ │
│                       │ 1883, 9001   │     │  └──────────────────────┘   │ │
│                       └──────────────┘     └──────────────────────────────┘ │
│                              ▲                                               │
│                              │                                               │
│                       ┌──────────────┐                                       │
│                       │  IP Camera   │                                       │
│                       │ (RTSP/LAN)   │                                       │
│                       │ 192.168.1.x  │                                       │
│                       └──────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Configuration

### The Problem

On macOS with managed firewalls, native processes cannot communicate via UDP even on the same machine. This blocks WebRTC ICE connections between:
- Edge (Python/aiortc) → Mediasoup (Rust)
- Browser → Mediasoup

### The Solution

Run the edge service in Docker. Docker Desktop creates a virtual network that bypasses the firewall.

| Component | Location | Key Setting |
|-----------|----------|-------------|
| Mediasoup | Native (host) | `ANNOUNCED_IP=192.168.65.254` |
| Edge | Docker container | Uses `host.docker.internal` |
| MQTT | Native or Docker | Ports 1883, 9001 |

### Critical: ANNOUNCED_IP

The `ANNOUNCED_IP` must be reachable from wherever the edge runs:

| Edge Location | Mediasoup ANNOUNCED_IP | Why |
|---------------|------------------------|-----|
| Docker | `192.168.65.254` | Docker Desktop's gateway to host |
| Native (host) | `192.168.1.x` (your LAN IP) | Direct connection |
| Remote server | Your public IP | External access |

## Quick Start

### Prerequisites

1. Docker Desktop running
2. MQTT broker (Mosquitto) on ports 1883 and 9001
3. Mediasoup video-streaming service built
4. IP camera accessible on LAN

### Step 1: Start MQTT Broker

```bash
# If using Docker Compose
cd cyberwave-backend
docker compose -f local.yml up -d mqtt
```

Verify:
```bash
nc -z localhost 1883 && echo "MQTT TCP OK"
nc -z localhost 9001 && echo "MQTT WebSocket OK"
```

### Step 2: Start Mediasoup

```bash
cd media-service/video-streaming

# CRITICAL: Use 192.168.65.254 for Docker edge
ANNOUNCED_IP=192.168.65.254 \
HOST=0.0.0.0 \
PORT=8080 \
ENVIRONMENT=local \
MQTT_HOST=localhost \
MQTT_PORT=1883 \
USE_GCS=false \
MEDIA_DIR=./media \
BACKEND_URL=http://localhost:8000 \
./target/release/video-streaming
```

Verify:
```bash
curl http://localhost:8080/health
# Should return: {"status":"ok","service":"video-streaming-sfu",...}
```

### Step 3: Configure Edge Docker

Edit `cyberwave-edges/cyberwave-edge-python/docker/local.yml`:

```yaml
services:
  edge:
    environment:
      - CYBERWAVE_API_URL=http://host.docker.internal:8000
      - CYBERWAVE_MQTT_HOST=host.docker.internal
      - CYBERWAVE_MQTT_PORT=1883
      - CYBERWAVE_TWIN_UUID=your-twin-uuid-here
      - CYBERWAVE_LOCAL_ICE=true
      - CAMERAS=[{"camera_id":"default","source":"rtsp://user:pass@192.168.1.x:554/stream","fps":10}]
```

### Step 4: Build and Start Edge

```bash
cd cyberwave-edges/cyberwave-edge-python/docker

# Build (first time only)
docker compose -f local.yml build

# Start
docker compose -f local.yml up -d

# Check logs
docker logs cyberwave_local_edge
```

### Step 5: Start Video Stream

```bash
# Replace with your twin UUID
python3 -c "
import paho.mqtt.publish as publish
import json
topic = 'localcyberwave/twin/YOUR-TWIN-UUID/command'
payload = json.dumps({'command': 'start_video', 'camera': 'default'})
publish.single(topic, payload, hostname='localhost', port=1883)
print('✓ Sent start_video command')
"
```

### Step 6: Verify in Browser

Open: `http://localhost:3000/twins/YOUR-TWIN-UUID`

## Troubleshooting

### Issue: Black video / ICE failed

**Symptoms:**
```
ICE connection state: checking
WebRTC connection failed
```

**Cause:** Wrong `ANNOUNCED_IP` for mediasoup

**Fix:**
```bash
# Check what IP Docker uses to reach host
docker exec cyberwave_local_edge getent hosts host.docker.internal
# Output: 192.168.65.254  host.docker.internal

# Restart mediasoup with that IP
ANNOUNCED_IP=192.168.65.254 ./target/release/video-streaming
```

### Issue: STUN transaction failed (403 - Forbidden IP)

**Symptoms:**
```
aioice.stun.TransactionFailed: STUN transaction failed (403 - Forbidden IP)
```

**Cause:** TURN server rejecting local IP addresses

**Fix:** Enable local ICE mode in edge:
```bash
CYBERWAVE_LOCAL_ICE=true
```

### Issue: Edge can't connect to MQTT

**Symptoms:**
```
Connection refused to host.docker.internal:1883
```

**Cause:** MQTT broker not running or not accessible

**Fix:**
```bash
# Verify MQTT is running
docker ps | grep mosquitto

# Check MQTT config allows external connections
# In mosquitto.conf:
listener 1883 0.0.0.0
listener 9001 0.0.0.0
protocol websockets
```

### Issue: Camera connection refused

**Symptoms:**
```
Could not open video capture
```

**Cause:** Wrong RTSP URL or credentials

**Fix:**
```bash
# Test RTSP from Docker container
docker exec cyberwave_local_edge \
  ffprobe -v error "rtsp://user:pass@192.168.1.x:554/stream"

# Common RTSP paths:
# - /stream
# - /live
# - /unicast/c1/s0/live  (UNV cameras)
# - /Streaming/Channels/101  (Hikvision)
```

### Issue: No frames sent

**Symptoms:**
```
No frames in last 10s
Timeout waiting for first frame
```

**Cause:** ICE connection not completing

**Fix:**
1. Check mediasoup logs for ICE errors
2. Verify `ANNOUNCED_IP` matches Docker gateway
3. Restart both services in order: mediasoup first, then edge

## Quick Reference Commands

```bash
# Check all services
ps aux | grep -E "video-streaming|mosquitto" | grep -v grep
docker ps | grep edge

# Restart edge
docker restart cyberwave_local_edge

# View edge logs
docker logs -f cyberwave_local_edge

# Test MQTT connection
mosquitto_pub -h localhost -p 1883 -t test -m "hello"
mosquitto_sub -h localhost -p 1883 -t test

# Send start_video command
python3 -c "import paho.mqtt.publish as p; import json; \
p.single('localcyberwave/twin/YOUR-TWIN-UUID/command', \
json.dumps({'command':'start_video','camera':'default'}), hostname='localhost')"

# Stop video
python3 -c "import paho.mqtt.publish as p; import json; \
p.single('localcyberwave/twin/YOUR-TWIN-UUID/command', \
json.dumps({'command':'stop_video','camera':'default'}), hostname='localhost')"
```

## Environment Variables Reference

### Mediasoup (video-streaming)

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `ANNOUNCED_IP` | Yes | IP for WebRTC SDP | `192.168.65.254` |
| `HOST` | Yes | Bind address | `0.0.0.0` |
| `PORT` | Yes | HTTP port | `8080` |
| `MQTT_HOST` | Yes | MQTT broker host | `localhost` |
| `MQTT_PORT` | Yes | MQTT broker port | `1883` |
| `ENVIRONMENT` | Yes | Topic prefix | `local` |

### Edge (Docker)

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `CYBERWAVE_TWIN_UUID` | Yes | Digital twin ID | `uuid-here` |
| `CYBERWAVE_MQTT_HOST` | Yes | MQTT host | `host.docker.internal` |
| `CYBERWAVE_LOCAL_ICE` | Yes | Disable TURN | `true` |
| `CAMERAS` | Yes | Camera config JSON | `[{"camera_id":"default",...}]` |
| `CYBERWAVE_API_URL` | No | Backend URL | `http://host.docker.internal:8000` |

## See Also

- [Video Security System Setup](examples/video_security_system.md)
- [Multi-Camera Configuration](examples/multi_camera.md)
- [Motion Detection Workflow](examples/motion_detection.md)
