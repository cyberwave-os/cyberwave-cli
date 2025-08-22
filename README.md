# Cyberwave CLI

[![PyPI version](https://badge.fury.io/py/cyberwave-cli.svg)](https://badge.fury.io/py/cyberwave-cli)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Command-line interface for the **Cyberwave Digital Twin Platform**. Manage projects, environments, digital twins, and robot integrations from the terminal.

**Cyberwave** is a comprehensive robotics platform that enables you to create digital twins of physical robots, run realistic simulations, and seamlessly bridge the gap between virtual development and real-world deployment.

## 🚀 Features

- **🤖 Robot Management**: Register, control, and monitor physical robots
- **🌍 Environment Control**: Create and manage 3D simulation environments  
- **🔗 Digital Twin Operations**: Seamless physical-digital synchronization
- **⚙️ Edge Runtime**: Bridge local robots to cloud platform
- **🔐 Secure Authentication**: Token-based authentication with device tokens
- **📊 Telemetry Streaming**: Real-time data collection and monitoring
- **🧩 Plugin Architecture**: Extensible command system

## 🤖 Supported Robots & Hardware

- **Robotic Arms**: SO-ARM100, Universal Robots, custom URDF-based arms
- **Drones**: DJI Tello, custom UAVs with MAVLink support
- **Mobile Robots**: Custom ground vehicles, AGVs, autonomous platforms
- **Sensors**: Cameras, LIDAR, IMU, custom sensor integrations
- **Custom Hardware**: Extensible driver system for any robot type

## 💡 Use Cases

- **Algorithm Development**: Test path planning and control algorithms safely
- **Fleet Management**: Monitor and control multiple robots remotely  
- **Training & Education**: Learn robotics with realistic simulations
- **Prototyping**: Validate designs before physical implementation
- **Industrial Automation**: Optimize factory and warehouse operations

## Installation

### One-Liner (Recommended)
```bash
curl -sSL https://raw.githubusercontent.com/cyberwave-os/cyberwave-cli/main/install.py | python3
```
*Automatically installs packages and configures PATH*

### Manual Installation
```bash
# CLI with SDK (automatically includes cyberwave SDK)
pip install cyberwave-cli

# CLI with robotics integrations (recommended for hardware)
pip install cyberwave-cli[robotics]

# Or install everything separately
pip install cyberwave-cli cyberwave-robotics-integrations

# Development installation (isolated)
pipx install cyberwave-cli
```

### PATH Configuration (if needed)
If you get "command not found: cyberwave" after manual installation:
```bash
# Auto-configure PATH
python3 -m cyberwave_cli.setup_utils

# Or use built-in setup command
cyberwave setup

# Verify installation
cyberwave doctor
```

## 🎯 Quick Start

Get up and running with Cyberwave in 3 simple steps:

```bash
# 1. Install (includes SDK automatically)
pip install cyberwave-cli

# 2. Authenticate 
cyberwave auth login --backend-url https://api.cyberwave.com --frontend-url https://app.cyberwave.com

# 3. Start using
cyberwave projects list
cyberwave devices register --project PROJECT_ID --name "My Robot" --type robot/so-arm100
```

## 📖 Detailed Usage

### 1. Authentication
```bash
# Login to your Cyberwave instance (will prompt for email/password)
cyberwave auth login --backend-url http://localhost:8000

# Or provide credentials directly
cyberwave auth login --email your@email.com --password yourpassword

# For production (replace with your actual domain)
# cyberwave auth login --backend-url https://api.cyberwave.com

# Check authentication status
cyberwave auth status

# Logout
cyberwave auth logout
```

### 2. Project Management
```bash
# List projects
cyberwave projects list

# Create new project
cyberwave projects create "My Robot Project" --description "Autonomous robot fleet"

# Get project details
cyberwave projects show <project-uuid>
```

### 3. Environment Management
```bash
# List environments
cyberwave environments list

# Create environment
cyberwave environments create "Test Environment" --project <project-uuid>

# Environment details
cyberwave environments show <environment-uuid>
```

### 4. Digital Twin Operations
```bash
# List twins in environment
cyberwave twins list --environment <environment-uuid>

# Add twin from catalog
cyberwave twins add cyberwave/so101 --environment <environment-uuid>

# Control twin position
cyberwave twins move <twin-uuid> --x 1.0 --y 0.0 --z 0.5

# Control robot joints
cyberwave twins joint <twin-uuid> --joint shoulder --angle 45
```

### 5. Device Management
```bash
# Register device
cyberwave devices register --name "My Robot" --type so100

# List devices
cyberwave devices list

# Device status
cyberwave devices status <device-id>
```

### Plugins

Plugins are discovered via the `cyberwave.cli.plugins` entry point and loaded automatically.

- Built-in: `auth`, `projects`, `devices`, `edge`, `twins`
- List loaded plugins:
  ```bash
  cyberwave plugins-cmd
  ```

### Devices

```bash
# Register a device and issue a device token
cyberwave devices register --project <PROJECT_ID> --name my-edge --type robot/so-arm100
cyberwave devices issue-offline-token --device <DEVICE_ID>
```

### Edge Node (SO-ARM100 example and simulation)

Configure and run a CyberWave Edge node that bridges a local driver to the cloud via the SDK.

- Initialize config (auto-register and create a device token):
  ```bash
  cyberwave edge init \
    --robot so_arm100 \
    --port /dev/ttyUSB0 \
    --backend http://localhost:8000/api/v1 \
    --project <PROJECT_ID> \
    --device-name edge-soarm100 \
    --device-type robot/so-arm100 \
    --auto-register \
    --use-device-token \
    --config ~/.cyberwave/edge.json
  ```
- Run:
  ```bash
  cyberwave edge run --config ~/.cyberwave/edge.json
  ```
- Status:
  ```bash
  cyberwave edge status --config ~/.cyberwave/edge.json
  ```
- Simulate a virtual camera from a local mp4 (no hardware needed):
  ```bash
  cyberwave edge simulate --sensor <SENSOR_UUID> --video ./sample.mp4 --fps 2
  ```
- Command mode (optional): set in `~/.cyberwave/edge.json` to route via backend controller
  ```json
  {
    "control_mode": "command",
    "twin_uuid": "<TWIN_UUID>"
  }
  ```

### Twin Control (Unified Command)

Send a command to a twin through the backend TeleopController.

```bash
# Move joints (degrees/radians based on driver semantics)
cyberwave twins command \
  --twin <TWIN_UUID> \
  --name arm.move_joints \
  --joints "[0,10,0,0,0,0]" \
  --mode both \
  --source cli

# Move to pose
cyberwave twins command \
  --twin <TWIN_UUID> \
  --name arm.move_pose \
  --pose '{"x":0.1, "y":0.2, "z":0.0}' \
  --mode sim
```

### Configuration

- CLI config: `~/.cyberwave/config.toml` (managed by `cyberwave auth config`)
- Edge config: `~/.cyberwave/edge.json` (managed by `cyberwave edge init`)

### Security

- Tokens are stored in system keychain when available, with JSON fallback.
- Device tokens are long-lived; prefer them for headless Edge deployments.

### Environments and Sensors (new)

List environments for a project and show recent events (latest session per twin):
```bash
cyberwave environments list --project <PROJECT_UUID>
cyberwave environments events --environment <ENVIRONMENT_UUID> -n 5
```

Create/list sensors in an environment:
```bash
cyberwave sensors create --environment <ENVIRONMENT_UUID> --name "Living Room Cam" --type camera
cyberwave sensors list --environment <ENVIRONMENT_UUID>
```

List analyzer events for a specific sensor from the latest session:
```bash
cyberwave sensors events --environment <ENVIRONMENT_UUID> --sensor <SENSOR_UUID> -n 20
```

