# Video Security System Setup Guide

This guide walks through setting up a video streaming pipeline from IP cameras to the Cyberwave platform.

## Architecture Overview

```
┌─────────────┐    RTSP     ┌─────────────┐   WebRTC    ┌─────────────┐   WebRTC    ┌─────────────┐
│  IP Camera  │ ──────────► │    Edge     │ ──────────► │     SFU     │ ──────────► │   Browser   │
│  (RTSP)     │             │   Service   │             │  (Docker)   │             │  (Frontend) │
└─────────────┘             └─────────────┘             └─────────────┘             └─────────────┘
                                   │                          │
                                   │         MQTT             │
                                   └──────────────────────────┘
```

## Prerequisites

- Cyberwave backend running (`docker compose -f local.yml up -d`)
- Video streaming service running (`media-service/video-streaming`)
- Mosquitto MQTT broker running
- Python 3.10+ with cyberwave-cli installed

## Quick Start (New Method)

### Step 1: Configure CLI

```bash
# Get token from the web UI or via login
curl -X POST http://localhost:8000/dj-rest-auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@cyberwave.com","password":"your-password"}'

# Configure CLI with local backend
cyberwave-cli configure --token YOUR_TOKEN --api-url http://localhost:8000

# Set for current session
export CYBERWAVE_API_URL=http://localhost:8000
```

### Step 2: Discover Cameras

```bash
# Quick port scan
cyberwave-cli scan -t 0.5 --no-onvif --no-upnp

# Example output:
Found 2 device(s)
┏━━━━┳━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃    ┃ IP Address   ┃ Port ┃ Protocol ┃ URL                     ┃
┡━━━━╇━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 📷 │ 192.168.1.3  │ 554  │ RTSP     │ rtsp://192.168.1.3:554  │
│ 📷 │ 192.168.1.5  │ 554  │ RTSP     │ rtsp://192.168.1.5:554  │
└────┴──────────────┴──────┴──────────┴─────────────────────────┘
```

### Step 3: Connect Camera

```bash
# Smart connect: creates twin + configures edge in one command
cyberwave-cli connect camera --name "Front Door Camera"

# Follow prompts:
# - Select/create environment
# - Enter RTSP URL, credentials, FPS
# - Config saved to cloud + local .env
```

### Step 4: Start Streaming

```bash
# Start the edge service
python -m cyberwave_edge.service

# Or via CLI
cyberwave-cli edge start -f
```

### Step 5: View in Browser

Navigate to: `http://localhost:3000/environments/<environment-uuid>`

Click on the camera twin to start the video stream.

---

## Detailed Setup

### Get Camera Stream URL

Common RTSP URL patterns:

| Manufacturer | RTSP URL Pattern |
|--------------|------------------|
| UNV | `rtsp://<user>:<pass>@<ip>:554/unicast/c1/s0/live` |
| Hikvision | `rtsp://<user>:<pass>@<ip>:554/Streaming/Channels/101` |
| Dahua | `rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0` |
| Generic ONVIF | Check camera's web interface |

### Verify Stream Access

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,codec_name \
  "rtsp://username:password@192.168.1.3:554/stream"

# Expected: codec_name, width, height (e.g., "hevc,1920,1080")
```

### Using the Connect Command

The `connect` command is the recommended way to set up cameras:

```bash
# Using asset alias
cyberwave-cli connect camera --name "Front Door"

# Using registry ID
cyberwave-cli connect cyberwave/ip-camera --name "Backyard"

# Skip prompts (use defaults)
cyberwave-cli connect camera -y --name "Garage" -e ENV_UUID

# Cloud only (no local .env)
cyberwave-cli connect camera --cloud-only --name "Remote Camera"
```

### Multiple Cameras on Same Edge

```bash
# Create first camera
cyberwave-cli connect camera --name "Front Door"
# Note the environment UUID from output

# Add more cameras to same environment
cyberwave-cli connect camera --name "Backyard" -e ENV_UUID
cyberwave-cli connect camera --name "Garage" -e ENV_UUID

# Pull all configs to single .env
cyberwave-cli edge pull -e ENV_UUID
```

### Check Device Fingerprint

```bash
cyberwave-cli edge whoami

# Output:
Fingerprint: macbook-pro-a1b2c3d4e5f6
Hostname:    macbook-pro.local
Platform:    Darwin-arm64
```

---

## Configuration Reference

### Generated .env File

```bash
# Cyberwave Edge Configuration
# Generated by: cyberwave connect
# Fingerprint: macbook-pro-a1b2c3d4e5f6

# Required
CYBERWAVE_TOKEN=your-api-token
CYBERWAVE_TWIN_UUID=your-twin-uuid

# API Settings
CYBERWAVE_BASE_URL=http://localhost:8000

# Device Identification
CYBERWAVE_EDGE_UUID=macbook-pro-a1b2c3d4e5f6

# Camera Configuration
CAMERAS='[{"camera_id": "default", "source": "rtsp://admin:pass@192.168.1.3:554/stream", "fps": 10}]'

# Local development
CYBERWAVE_LOCAL_ICE=true

# Logging
LOG_LEVEL=INFO
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CYBERWAVE_TOKEN` | API authentication token | Required |
| `CYBERWAVE_BASE_URL` | Backend API URL | `http://localhost:8000` |
| `CYBERWAVE_TWIN_UUID` | Twin to stream for | Required |
| `CYBERWAVE_EDGE_UUID` | Device fingerprint | Auto-generated |
| `CAMERAS` | JSON camera config | Required |
| `CYBERWAVE_LOCAL_ICE` | Disable TURN for local dev | `false` |

---

## Debugging Guide

### Issue: Black Video Screen

1. **Check Edge Logs**
   ```bash
   tail -50 /tmp/edge.log
   ```
   Look for: `Initialized camera`, `WebRTC connection`, `No frames sent`

2. **Verify Camera Stream**
   ```bash
   ffprobe -v error "rtsp://user:pass@ip:554/stream"
   ```

3. **Check ICE Connectivity**
   If you see `STUN transaction failed (403)`:
   - Ensure `CYBERWAVE_LOCAL_ICE=true` is set
   - Restart edge service

### Issue: MQTT Connection Failed

1. **Check Mosquitto**
   ```bash
   docker ps | grep mosquitto
   nc -zv localhost 9001  # WebSocket port
   nc -zv localhost 1883  # TCP port
   ```

2. **Verify Frontend Config**
   ```bash
   # .env.local should have:
   NEXT_PUBLIC_MQTT_BROKER_URL=ws://localhost:9001
   ```

### Issue: Config Not Syncing

```bash
# Check your fingerprint
cyberwave-cli edge whoami

# Check edge status
cyberwave-cli edge remote-status -t TWIN_UUID

# Re-pull config
cyberwave-cli edge pull -t TWIN_UUID -y
```

### Issue: Twin Not Found

```bash
# List twins
cyberwave-cli twin list

# Show twin details
cyberwave-cli twin show TWIN_UUID
```

---

## Service Status Commands

```bash
# Check all services
docker compose -f cyberwave-backend/local.yml ps

# Video streaming service
docker logs cyberwave_local_video_streaming --tail 20

# MQTT broker
docker logs cyberwave-mosquitto --tail 20

# Edge service
tail -30 /tmp/edge.log

# Test MQTT
mosquitto_pub -h localhost -p 1883 -t "test" -m "hello"
mosquitto_sub -h localhost -p 1883 -t "test"
```

---

## Quick Checklist

- [ ] Camera accessible via RTSP (test with ffprobe)
- [ ] CLI configured with local backend URL
- [ ] Twin created via `cyberwave connect camera`
- [ ] `.env` file generated with correct twin UUID
- [ ] `CYBERWAVE_LOCAL_ICE=true` for local development
- [ ] Video streaming service RTC ports match Docker mapping
- [ ] All services running (Django, Mosquitto, Video Streaming, Edge)

---

## CLI Command Reference

| Command | Description |
|---------|-------------|
| `cyberwave-cli configure --token X --api-url Y` | Configure CLI |
| `cyberwave-cli scan` | Discover cameras |
| `cyberwave-cli connect camera --name "X"` | Create twin + configure |
| `cyberwave-cli edge whoami` | Show device fingerprint |
| `cyberwave-cli edge pull -e UUID` | Pull environment configs |
| `cyberwave-cli edge remote-status -t UUID` | Check edge status |
| `cyberwave-cli edge start -f` | Start edge in foreground |
| `cyberwave-cli twin list` | List all twins |
