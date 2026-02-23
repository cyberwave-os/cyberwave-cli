# Quick Start Guide

Get up and running with Cyberwave CLI in 5 minutes.

## Prerequisites

- Python 3.10+
- A Cyberwave account with API token
- (Optional) A camera for video streaming

## Installation

```bash
pip install cyberwave-cli
```

## Configuration

### 1. Configure your API token

```bash
cyberwave-cli configure --token YOUR_API_TOKEN
```

Or set environment variables:
```bash
export CYBERWAVE_API_URL=http://localhost:8000
export CYBERWAVE_API_KEY=your-token-here
```

### 2. Verify connection

```bash
cyberwave-cli twin list
```

## Basic Commands

### List resources

```bash
# List all twins
cyberwave-cli twin list

# List workflows
cyberwave-cli workflow list

# List environments
cyberwave-cli environment list

# List available ML models
cyberwave-cli model list --edge-only
```

### Create a camera twin

```bash
# Discover cameras on the network
cyberwave-cli scan

# Setup a camera (creates environment, asset, and twin)
cyberwave-cli camera setup \
  --source "rtsp://user:pass@192.168.1.100:554/stream" \
  --name "Front Door Camera"
```

### Manage workflows

```bash
# List workflow templates
cyberwave-cli workflow templates

# Create a motion detection workflow
cyberwave-cli workflow create --template motion-detection

# Activate a workflow
cyberwave-cli workflow activate <workflow-uuid>

# View workflow details
cyberwave-cli workflow show <workflow-uuid>
```

## Next Steps

- [Video Security System](./video_security_system.md) - Complete security setup
- [Motion Detection](./motion_detection.md) - Configure ML-based detection
- [Multi-Camera Setup](./multi_camera.md) - Multiple camera deployment
