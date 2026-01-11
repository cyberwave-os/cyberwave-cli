# Video Security System Setup Guide

This guide walks through setting up a video streaming pipeline from IP cameras to the Cyberwave platform, including common debugging steps.

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

## Step 1: Discover Cameras on Network

Use the CLI to scan your local network for ONVIF-compatible cameras:

```bash
# Scan default network range
cyberwave scan

# Scan specific subnet
cyberwave scan --network 192.168.1.0/24

# Scan with extended timeout for slow cameras
cyberwave scan --timeout 10
```

Example output:
```
Scanning network 192.168.1.0/24 for cameras...

Found 2 cameras:

┌─────────────────┬──────────────────┬─────────────────────────────────────────┐
│ IP              │ Manufacturer     │ Model                                   │
├─────────────────┼──────────────────┼─────────────────────────────────────────┤
│ 192.168.1.5     │ UNV              │ IPC-D124-PF28                           │
│ 192.168.1.10    │ Hikvision        │ DS-2CD2143G2-I                          │
└─────────────────┴──────────────────┴─────────────────────────────────────────┘
```

## Step 2: Get Camera Stream URL

Most IP cameras use RTSP for streaming. Common URL patterns:

| Manufacturer | RTSP URL Pattern |
|--------------|------------------|
| UNV | `rtsp://<user>:<pass>@<ip>:554/unicast/c1/s0/live` |
| Hikvision | `rtsp://<user>:<pass>@<ip>:554/Streaming/Channels/101` |
| Dahua | `rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0` |
| Generic ONVIF | Check camera's web interface or use `cyberwave scan --profiles` |

### Verify Stream Access

Test the RTSP stream before configuring:

```bash
# Using ffprobe to verify stream
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,codec_name \
  "rtsp://username:password@192.168.1.5:554/unicast/c1/s0/live"

# Expected output: codec_name, width, height (e.g., "hevc,1920,1080")
```

## Step 3: Create Environment and Twin

### Option A: Using CLI (Recommended)

```bash
# Create an environment for your cameras
cyberwave environment list  # Check existing environments

# Setup camera with edge software
cyberwave camera setup \
  --name "Front Door Camera" \
  --rtsp-url "rtsp://username:password@192.168.1.5:554/unicast/c1/s0/live" \
  --fps 10
```

### Option B: Manual Setup

1. Create environment via API or UI
2. Create a camera-type asset
3. Create a twin with RGB sensor capability

The twin must have an `rgb` sensor in its capabilities:

```json
{
  "sensors": [
    {
      "id": "default",
      "type": "rgb", 
      "name": "Main Camera"
    }
  ]
}
```

## Step 4: Configure Edge Service

Create/edit `.env` file in `cyberwave-edges/cyberwave-edge-python/`:

```bash
# Authentication
CYBERWAVE_TOKEN=your-api-token-here
CYBERWAVE_BASE_URL=http://localhost:8000

# MQTT Configuration
MQTT_HOST=localhost
MQTT_PORT=1883

# Edge Identity
EDGE_UUID=edge-device-001
CYBERWAVE_TWIN_UUID=your-twin-uuid-here

# Camera Configuration (JSON format)
CAMERAS='{"default": {"source": "rtsp://user:pass@192.168.1.5:554/stream", "fps": 10}}'

# Local Development - Disable TURN servers for local WebRTC
CYBERWAVE_LOCAL_ICE=true
```

### Important Configuration Notes

- `CYBERWAVE_LOCAL_ICE=true` is **required** for local development to bypass TURN servers
- `CYBERWAVE_TWIN_UUID` must match an existing twin with RGB sensor capability
- Camera credentials in `CAMERAS` must be URL-encoded if they contain special characters

## Step 5: Start Edge Service

```bash
cd cyberwave-edges/cyberwave-edge-python

# Run in foreground (for debugging)
python -m cyberwave_edge.service

# Run in background with logging
python -m cyberwave_edge.service > /tmp/edge.log 2>&1 &
```

## Step 6: View in Browser

Navigate to the environment page:
```
http://localhost:3000/environments/<environment-uuid>
```

Click on the camera twin to start the video stream.

---

## Debugging Guide

### Issue: Black Video Screen

**Symptoms**: Video component shows but displays black/no image.

**Debug Steps**:

1. **Check Edge Logs**
   ```bash
   tail -50 /tmp/edge.log
   ```
   
   Look for:
   - `Initialized camera <url> at X FPS` - Camera connected
   - `WebRTC connection state changed: connected` - WebRTC working
   - `No frames sent in last 5 seconds` - Problem indicator

2. **Verify Camera Stream**
   ```bash
   ffprobe -v error "rtsp://user:pass@ip:554/stream"
   ```

3. **Check WebRTC ICE Connectivity**
   
   If you see `STUN transaction failed (403)`:
   - Ensure `CYBERWAVE_LOCAL_ICE=true` is set in edge `.env`
   - Restart edge service after changing config

### Issue: MQTT Connection Failed

**Symptoms**: Frontend shows "WebSocket connection failed"

**Debug Steps**:

1. **Check Mosquitto is running**
   ```bash
   docker ps | grep mosquitto
   nc -zv localhost 9001  # WebSocket port
   nc -zv localhost 1883  # TCP port
   ```

2. **Verify Frontend Config**
   
   Check `cyberwave-frontend/.env.local`:
   ```bash
   # Must include ws:// protocol
   NEXT_PUBLIC_MQTT_BROKER_URL=ws://localhost:9001
   ```

3. **Check Mosquitto Config**
   ```bash
   docker exec cyberwave-mosquitto cat /mosquitto/config/mosquitto.conf
   ```
   
   Should include WebSocket listener:
   ```
   listener 9001
   protocol websockets
   ```

### Issue: WebRTC ICE Failed

**Symptoms**: Edge logs show `STUN transaction failed` or ICE candidates failing

**Debug Steps**:

1. **Check Port Mapping** (for Docker video streaming)
   
   In `media-service/video-streaming/docker/local.yml`:
   ```yaml
   ports:
     - "50000-50100:50000-50100/udp"  # RTC ports
   environment:
     - RTC_MIN_PORT=50000
     - RTC_MAX_PORT=50100  # Must match port mapping!
   ```

2. **Enable Local ICE Mode**
   
   Add to edge `.env`:
   ```bash
   CYBERWAVE_LOCAL_ICE=true
   ```

3. **Restart Services**
   ```bash
   # Restart video streaming
   cd media-service/video-streaming
   docker compose -f docker/local.yml up -d
   
   # Restart edge
   pkill -f cyberwave_edge.service
   python -m cyberwave_edge.service > /tmp/edge.log 2>&1 &
   ```

### Issue: Twin Not Found / Wrong Twin

**Symptoms**: Edge connects but browser shows wrong/no stream

**Debug Steps**:

1. **Verify Twin UUID**
   ```bash
   cyberwave twin list
   ```

2. **Check Twin Has RGB Sensor**
   ```bash
   cyberwave twin show <twin-uuid>
   ```
   
   Must have sensor type `rgb` in capabilities.

3. **Update Edge Config**
   
   Edit `.env` and set correct `CYBERWAVE_TWIN_UUID`

### Issue: Codec Mismatch

**Symptoms**: Video streaming service logs show codec errors

**Common Codecs**:
- H.264 (most compatible)
- H.265/HEVC (higher quality, less compatible)
- VP8/VP9 (WebRTC native)

**Solution**: Configure camera to use H.264 baseline profile if possible, or ensure the SFU supports the camera's codec.

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

# Test MQTT connectivity
mosquitto_pub -h localhost -p 1883 -t "test" -m "hello"
mosquitto_sub -h localhost -p 1883 -t "test"
```

## Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `CYBERWAVE_TOKEN` | API authentication token | Required |
| `CYBERWAVE_BASE_URL` | Backend API URL | `http://localhost:8000` |
| `CYBERWAVE_TWIN_UUID` | Twin to stream for | Required |
| `MQTT_HOST` | MQTT broker hostname | `localhost` |
| `MQTT_PORT` | MQTT broker port | `1883` |
| `CAMERAS` | JSON camera config | Required |
| `CYBERWAVE_LOCAL_ICE` | Disable TURN for local dev | `false` |

## Quick Checklist

- [ ] Camera accessible via RTSP (test with ffprobe)
- [ ] Twin exists with `rgb` sensor capability
- [ ] Edge `.env` configured with correct twin UUID
- [ ] `CYBERWAVE_LOCAL_ICE=true` set for local development
- [ ] Video streaming service RTC ports match Docker mapping
- [ ] Frontend MQTT URL includes `ws://` protocol
- [ ] All services running (Django, Mosquitto, Video Streaming, Edge)
