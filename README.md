## CyberWave CLI

Command-line interface for CyberWave. Provides authentication, device and project management, and plugins for Edge nodes and twin control.

### Install

- Stable (recommended):
  ```bash
  pipx install cyberwave-cli
  ```
- Monorepo/dev with SDK and integrations:
  ```bash
  pip install -e cyberwave/cyberwave-sdk-python
  pip install -e cyberwave/cyberwave_robotics_integrations
  pip install -e cyberwave/cyberwave-cli[sdk]
  ```

### Authenticate

```bash
cyberwave auth login --backend-url http://localhost:8000 --frontend-url http://localhost:3000
cyberwave auth status
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
