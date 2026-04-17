<p align="center">
  <a href="https://cyberwave.com">
    <img src="https://cyberwave.com/cyberwave-logo-black.svg" alt="Cyberwave logo" width="240" />
  </a>
</p>

# Cyberwave CLI

This module is part of **Cyberwave: Making the physical world programmable**.

The official command-line interface for Cyberwave. Authenticate and bootstrap robotics projects from your terminal.

[![License](https://img.shields.io/badge/License-MIT-orange.svg)](https://github.com/cyberwave-os/cyberwave-cli/blob/main/LICENSE)
[![Documentation](https://img.shields.io/badge/Documentation-docs.cyberwave.com-orange)](https://docs.cyberwave.com)
[![Discord](https://badgen.net/badge/icon/discord?icon=discord&label&color=orange)](https://discord.gg/dfGhNrawyF)
[![PyPI version](https://img.shields.io/pypi/v/cyberwave-cli.svg)](https://pypi.org/project/cyberwave-cli/)
[![PyPI Python versions](https://img.shields.io/pypi/pyversions/cyberwave-cli.svg)](https://pypi.org/project/cyberwave-cli/)
[![Release to PyPI](https://github.com/cyberwave-os/cyberwave-cli/actions/workflows/release-pypi.yml/badge.svg)](https://github.com/cyberwave-os/cyberwave-cli/actions/workflows/release-pypi.yml)

## Installation

### From PyPI (pip)

```bash
pip install cyberwave-cli
```

### From APT (Debian/Ubuntu)

```bash
curl -fsSL https://cyberwave.com/install.sh | bash
```

The same Buildkite apt registry also carries **`cyberwave-cli-dev`** and **`cyberwave-cli-staging`** for CI builds from `dev` / `staging`. Use those package names explicitly when you want those channels; default `cyberwave-cli` is tagged releases. The packages conflict because they ship the same `/usr/bin/cyberwave`.

### From Source

```bash
git clone https://github.com/cyberwave-os/cyberwave-cli
cd cyberwave-cli
pip install -e .
```

## Quick Start - Edge

### 1. SSH into your edge device

```bash
ssh yourhost@your-ip
```

### 2. Set up your Edge device

Once you are in your edge device, set it up by:

```bash
cyberwave edge install
```

This command will guide you to your first-time setup of your edge device.

## Commands

| Command       | Description                                                   |
| ------------- | ------------------------------------------------------------- |
| `login`       | Authenticate with Cyberwave                                   |
| `logout`      | Remove stored credentials                                     |
| `configure`   | Set API token and URL directly (without login flow)           |
| `config-dir`  | Print the active configuration directory                      |
| `completion`  | Generate/install shell autocomplete                           |
| `twin`        | Create, pair, list, show, and delete digital twins            |
| `edge`        | Manage edge node (install, start, stop, drivers, sync, etc.)  |
| `compute`     | Manage cloud node (install, start, stop, status, logs)        |
| `workflow`    | Manage workflows (list, create, sync, activate, etc.)         |
| `worker`      | Manage local worker files for edge inference                   |
| `model`       | Manage ML model bindings on edge nodes                        |
| `plugin`      | Manage edge node plugins                                      |
| `environment` | List and inspect environments                                 |
| `scan`        | Discover IP cameras and NVRs on the local network             |
| `camera`      | Bootstrap a camera edge project                               |
| `so101`       | Bootstrap an SO-101 robot arm project                         |
| `manifest`    | Validate `cyberwave.yml` manifests                            |
| `edge bench`  | Benchmark Zenoh SDK hot paths vs a per-device baseline        |

## `cyberwave twin`

Create and manage digital twins — the virtual representation of your physical devices.

| Subcommand | Description                                           |
| ---------- | ----------------------------------------------------- |
| `create`   | Create a new twin from an asset                       |
| `pair`     | Pair this device with an existing twin                |
| `list`     | List all digital twins                                |
| `show`     | Show details of a specific twin                       |
| `delete`   | Delete a digital twin                                 |

### `cyberwave twin create`

Create a twin from an asset identifier. The ASSET argument can be a registry ID (`unitree/go2`, `cyberwave/standard-cam`), an alias (`go2`, `camera`), a local JSON file, or a URL.

```bash
cyberwave twin create camera
cyberwave twin create go2 --name "My Robot"
cyberwave twin create camera --pair                          # create + pair in one step
cyberwave twin create camera --pair --source "rtsp://..." --fps 15
```

**Options:**

- `-n, --name`: Twin name
- `-e, --environment`: Environment UUID to create the twin in
- `--pair`: Also pair this device to the new twin
- `-d, --target-dir`: Directory to save the `.env` file (when `--pair` is used)
- `-y, --yes`: Skip confirmation prompts
- `--<field> <value>`: Any additional field from the asset's `edge_config_schema`

### `cyberwave twin pair`

Register this device and bind it to an existing twin. After pairing, run `cyberwave edge start` to begin streaming.

```bash
cyberwave twin pair <TWIN_UUID>
cyberwave twin pair abc-123 --camera-source "rtsp://..." --fps 15
```

### `cyberwave twin list`

```bash
cyberwave twin list                    # table view
cyberwave twin list --json             # JSON output
cyberwave twin list -e <ENV_UUID>      # filter by environment
```

### `cyberwave twin show / delete`

```bash
cyberwave twin show <UUID>
cyberwave twin delete <UUID>
cyberwave twin delete <UUID> --yes     # skip confirmation
```

## Worker Management

Edge workers are Python modules that run inside the edge worker container and
process sensor data using the Cyberwave SDK hooks API.

There are two kinds of workers:
- **Custom** workers: handwritten files you manage directly.
- **Generated** (`wf_*`) workers: auto-generated from backend workflow definitions,
  synced by edge-core. Do not edit these directly — [eject](#eject-a-workflow-worker) them first.

### `cyberwave worker list`

```bash
cyberwave worker list           # table view
cyberwave worker list --json    # JSON output
```

Lists all installed workers with their origin (`custom` or `workflow`).

### `cyberwave worker add`

```bash
cyberwave worker add ./detect_people.py
cyberwave worker add ~/workers/my_model.py --name renamed.py
```

Copies a Python worker file into `{CONFIG_DIR}/workers/`. After adding a worker,
restart the edge worker container:

```bash
cyberwave-edge-core worker restart
```

### `cyberwave worker remove`

```bash
cyberwave worker remove detect_people        # without .py extension
cyberwave worker remove detect_people.py    # with .py extension
cyberwave worker remove wf_abc123 --yes     # skip confirmation
```

Removes a worker file. Generated (`wf_*`) workers warn you that they will be
re-created on the next edge sync unless the originating workflow is deactivated.

### `cyberwave worker status`

```bash
cyberwave worker status
```

Shows the list of installed worker files (with origin labels) and the status of
the edge worker container (requires Docker).

### `cyberwave worker logs`

```bash
cyberwave worker logs             # follow logs (default)
cyberwave worker logs --tail 100  # show last 100 lines
cyberwave worker logs --no-follow # print and exit
```

Streams logs from the edge worker container (requires Docker).

### `cyberwave worker doctor`

```bash
cyberwave worker doctor                 # static + runtime checks (default)
cyberwave worker doctor --verbose       # show hints for passing checks too
cyberwave worker doctor --no-runtime    # skip the live-bus probe
cyberwave worker doctor --window 6      # longer runtime probe (seconds)
```

Diagnoses the common silent failure modes where a worker container looks
healthy but hooks report `frames: 0`. The doctor runs two groups of checks.

**Static ("paperwork") checks:**

- `cyberwave-edge-core` is installed;
- worker files in `{CONFIG_DIR}/workers/` are world-readable (UID 1001 needs to read them);
- at least one `cyberwave-driver-*` container is running on this host;
- `CYBERWAVE_ENVIRONMENT`, `ZENOH_CONNECT`, `CYBERWAVE_DATA_BACKEND` and
  `ZENOH_SHARED_MEMORY` agree between the running driver and worker containers;
- no known legacy env-var spellings slip through — currently
  `ZENOH_SHM_ENABLED` (superseded by `ZENOH_SHARED_MEMORY`). Other
  legacy names may be added as they're discovered; the check is
  deliberately conservative and only flags names we've explicitly
  mapped.

**Runtime checks** — open a short Zenoh subscription to `**` and compare
live traffic against every `@cw.on_*(<twin>)` hook declared in the worker
files. Three distinct diagnoses are emitted:

- **Sensor mismatch** — hook listens on `sensor="default"` but the driver
  publishes on another sensor name (or vice-versa).
- **Wrong twin** — the channel is flowing, but under a different twin
  UUID than the hook expects.
- **Unscoped keys** — a publisher is putting to e.g. `frames/color_camera`
  without the canonical `cw/<twin>/data/...` prefix; twin-scoped hooks
  silently drop these.

Requires `eclipse-zenoh` on the host (`pip install --user eclipse-zenoh`).
When missing, the runtime section degrades to an info message.

`cyberwave worker start` still invokes only the static checks as a
pre-flight (no bus probe, to keep startup fast); pass `--skip-preflight`
to bypass them.

### `cyberwave worker monitor`

```bash
cyberwave worker monitor               # live dashboard (default 2s refresh)
cyberwave worker monitor --update 1    # refresh every 1 second
cyberwave worker monitor -c <name>     # target a specific container
```

Opens a live-updating dashboard showing:

- **Resource usage** — CPU %, memory, network I/O (via `docker stats`)
- **GPU** — utilization, memory, temperature (Linux + NVIDIA only; shows N/A on macOS)
- **Zenoh throughput** — per-channel messages/second and totals
- **Worker hooks** — per-hook frame counts and drop rates
- **Model inference** — per-model latency (avg / P95 / P99) and inference count

The dashboard connects to the worker's Zenoh data bus for runtime metrics.

**Options:**

- `-u, --update`: Refresh interval in seconds (default: `2.0`)
- `-c, --container`: Explicit container name (auto-detected if omitted)

### Eject a Workflow Worker

Workflow-generated workers (`wf_*.py`) can be **ejected** into custom workers
when you need to customise their logic:

```bash
# 1. Copy the generated worker to a custom name
cp ~/.cyberwave/workers/wf_a1b2c3d4.py \
   ~/.cyberwave/workers/my_detector.py

# 2. Edit your copy
nano ~/.cyberwave/workers/my_detector.py

# 3. Deactivate the originating workflow in the UI
#    The wf_a1b2c3d4.py file will be removed on the next edge sync.
```

After ejection, `my_detector.py` is yours to edit freely. Edge sync never
touches files that do not start with `wf_`.

## `cyberwave workflow`

Manage workflows for automation — list, create, show, activate, sync to edge, and delete.

| Subcommand   | Description                                    |
| ------------ | ---------------------------------------------- |
| `list`       | List workflows (table or `--json`)             |
| `templates`  | List available workflow templates               |
| `create`     | Create a workflow (`--name` or `--template`)   |
| `show`       | Show workflow details, nodes, and target twins |
| `sync`       | Sync a workflow to its edge node(s) via MQTT   |
| `activate`   | Activate a workflow                            |
| `deactivate` | Deactivate a workflow                          |
| `delete`     | Delete a workflow (`--yes` to skip confirm)    |

All subcommands accept `--base-url` / `-u` to override the API URL (e.g. `http://192.168.10.101:8000`). When a UUID argument is omitted, an interactive arrow-key selector is shown.

```bash
# List workflows with target twin(s) column
cyberwave workflow list
cyberwave workflow list --json

# Create from template
cyberwave workflow create --template motion-detection

# Show details (interactive if UUID omitted)
cyberwave workflow show
cyberwave workflow show e7f1856c

# Sync workflow to edge device(s)
cyberwave workflow sync
cyberwave workflow sync e7f1856c --base-url http://192.168.10.101:8000

# Activate / deactivate
cyberwave workflow activate
cyberwave workflow deactivate

# Delete
cyberwave workflow delete --yes
```

The `sync` command reads the workflow's `camera_frame` trigger nodes to find target twin(s), then sends `sync_workflows` MQTT commands to each twin's edge node.

## Shell Autocompletion

Enable persistent autocompletion in one step:

```bash
cyberwave completion install
```

This auto-detects your shell (`bash` or `zsh`), writes an idempotent completion block
into your shell RC file, and tells you which file to source.

### Explicit shell setup

```bash
# Bash
cyberwave completion install --shell bash

# Zsh
cyberwave completion install --shell zsh
```

### Generate scripts manually

```bash
cyberwave completion generate --shell bash
cyberwave completion generate --shell zsh
```

### Troubleshooting

- **Shell not detected**: run `cyberwave completion install --shell bash` or `--shell zsh`.
- **Permission denied writing RC file**: re-run with a writable `--rc-file` path, then source it.
- **Already installed**: the installer is idempotent and will report when completion is already configured.

## `cyberwave login`

Authenticates with Cyberwave using your email and password.

```bash
cyberwave login                                              # interactive
cyberwave login --email you@example.com --password yourpass   # non-interactive
```

**Options:**

- `-e, --email`: Email address
- `-p, --password`: Password (will prompt if not provided)

## `cyberwave config-dir`

Prints the resolved configuration directory path. Useful in scripts to locate credentials and config files without hardcoding paths.

```bash
cyberwave config-dir
# ~/.cyberwave

CONFIG_DIR=$(cyberwave config-dir)
cat "$CONFIG_DIR/credentials.json"
```

See [Configuration](#configuration) for how the directory is resolved.

## `cyberwave edge`

Manage the edge node service lifecycle, configuration, and monitoring.

| Subcommand       | Description                                              |
| ---------------- | -------------------------------------------------------- |
| `install`        | Install cyberwave-edge-core and register systemd service |
| `uninstall`      | Stop and remove the systemd service                      |
| `start`          | Start the edge node                                      |
| `stop`           | Stop the edge node                                       |
| `restart`        | Restart the edge node (systemd or process)               |
| `status`         | Check if the edge node is running                        |
| `pull`           | Pull edge configuration from backend                     |
| `whoami`         | Show device fingerprint and info                         |
| `health`         | Check edge health status via MQTT                        |
| `remote-status`  | Check edge status from twin metadata (heartbeat)         |
| `logs`           | Show edge node logs                                      |
| `install-deps`   | Install edge ML dependencies                             |
| `sync-workflows` | Trigger workflow sync on the edge node                   |
| `list-models`    | List model bindings loaded on the edge node              |
| `driver`         | Manage edge driver containers (subgroup — see [below](#cyberwave-edge-driver)) |

### `cyberwave edge install`

Installs the `cyberwave-edge-core` package (via apt-get on Debian/Ubuntu) and creates a systemd service so it starts on boot. Guides you through workspace, environment, and twin selection.

On **macOS**, the installer also sets up an MJPEG camera stream bridge (using `ffmpeg` and AVFoundation) and prompts you to select which camera to use. The selected camera is stored by **device name** (not index) so it persists across USB reconnections and reboots.

On non-apt platforms, `--channel dev|staging` installs prerelease Python builds from the Buildkite Python registry; stable installs continue to use the public PyPI release.

```bash
sudo cyberwave edge install
sudo cyberwave edge install -y   # skip prompts
sudo cyberwave edge install --reconfigure-camera   # re-select camera without full reinstall
sudo cyberwave edge install --channel dev
sudo cyberwave edge install --channel staging --version 0.0.42.595
```

**Options:**

- `--reconfigure-camera`: Re-run the camera selection prompt and restart the camera stream and edge-core service. Useful when switching between cameras (e.g. laptop webcam to external USB camera).
- `--force-reinstall`: Force reinstall of the USB/IP server on macOS. Camera stream and edge-core setup are always forced during install.
- `-y`: Skip interactive confirmation prompts.

### `cyberwave edge uninstall`

Stops the systemd service, removes the unit file, and optionally uninstalls the package.

```bash
sudo cyberwave edge uninstall
```

### `cyberwave edge start / stop / restart`

```bash
cyberwave edge start                        # background
cyberwave edge start -f                     # foreground
cyberwave edge start --env-file ./my/.env   # custom config

cyberwave edge stop

sudo cyberwave edge restart                 # systemd
cyberwave edge restart --env-file .env      # process mode
```

### `cyberwave edge status`

Checks whether the edge node process is running.

```bash
cyberwave edge status
```

### `cyberwave edge pull`

Pulls edge configuration from the backend using the discovery API (or legacy twin/environment lookup).

```bash
cyberwave edge pull                              # auto-discover via fingerprint
cyberwave edge pull --twin-uuid <UUID>           # single twin (legacy)
cyberwave edge pull --environment-uuid <UUID>    # all twins in environment (legacy)
cyberwave edge pull -d ./my-edge                 # custom output directory
```

### `cyberwave edge whoami`

Displays the unique hardware fingerprint for this device, used to identify the edge when connecting to twins.

```bash
cyberwave edge whoami
```

### `cyberwave edge health`

Queries real-time health status via MQTT (stream states, FPS, WebRTC connections).

```bash
cyberwave edge health -t <TWIN_UUID>
cyberwave edge health -t <TWIN_UUID> --watch     # continuous
cyberwave edge health -t <TWIN_UUID> --timeout 10
```

### `cyberwave edge remote-status`

Checks the last heartbeat stored in twin metadata to determine online/offline status without MQTT.

```bash
cyberwave edge remote-status -t <TWIN_UUID>
```

### `cyberwave edge logs`

```bash
cyberwave edge logs              # last 50 lines
cyberwave edge logs -n 100       # last 100 lines
cyberwave edge logs -f           # follow (tail -f)
```

### `cyberwave edge install-deps`

Installs common ML runtimes needed by edge plugins.

```bash
cyberwave edge install-deps                       # ultralytics + opencv
cyberwave edge install-deps -r onnx -r tflite     # specific runtimes
```

### `cyberwave edge sync-workflows / list-models`

```bash
cyberwave edge sync-workflows --twin-uuid <UUID>  # re-sync model bindings
cyberwave edge list-models --twin-uuid <UUID>      # show loaded models
```

### `cyberwave edge driver`

Manage edge driver containers.

| Subcommand | Description                                              |
| ---------- | -------------------------------------------------------- |
| `list`     | List running driver containers (`--all` includes exited) |
| `start`    | Start a stopped driver container |
| `stop`     | Stop a running driver container  |

```bash
cyberwave edge driver list           # running containers only
cyberwave edge driver list --all     # include exited containers

cyberwave edge driver start                             # interactive picker (stopped containers)
cyberwave edge driver start cyberwave-driver-624d7fe2   # directly by name

cyberwave edge driver stop                              # interactive picker (running containers)
cyberwave edge driver stop cyberwave-driver-624d7fe2    # directly by name
```

`start` restarts an existing stopped container and sets a `--restart=on-failure` Docker policy, so the driver automatically retries if it exits due to a transient error (e.g. robot not yet reachable on the network). To launch a brand-new driver, use the edge-core service which manages image selection and environment configuration.

`stop` disables the Docker restart policy before stopping, so the container does not come back automatically. If the driver is managed by a systemd service, stop that instead:

```bash
sudo systemctl stop cyberwave-video-grabber.service
```

### `cyberwave edge bench`

Micro-benchmark the Zenoh SDK hot paths (header pack, sample decode, stats
accounting, sequence numbering) and compare every metric against a per-device
baseline shipped with the CLI. Prints a device fingerprint, a results table
with `Baseline ops/s` / `Delta` / `Status` columns, and a pass/fail report
card. Exits with code `2` when any metric regresses beyond the threshold, so
CI on a reference device can gate merges.

```bash
# Default run (~30s on a modern laptop): 100k rounds, 2k warmup, 3 repeats,
# 15% regression threshold, auto-selected baseline.
cyberwave edge bench

# Tighter threshold + capture the full run result for later diffing.
cyberwave edge bench --threshold 0.10 --output /tmp/bench.json

# Pin to CPU 0 on Linux for quieter numbers.
cyberwave edge bench --pin

# Skip baseline comparison (just print raw numbers).
cyberwave edge bench --no-compare
```

**How the "device" is resolved**

The command auto-detects a `device_class` slug (e.g. `jetson-orin-nano`,
`rpi-5`, `x86-laptop`, `apple-silicon-m4`) from `/proc/device-tree/model`,
`/etc/nv_tegra_release`, `sysctl -n machdep.cpu.brand_string` (macOS),
`platform.machine()`, and (on Linux x86_64) whether a battery is present.
On Apple Silicon the chip generation is parsed from the brand string
(`"Apple M1"`, `"Apple M1 Pro"`, `"Apple M4 Max"`, …) and emitted as
`apple-silicon-m1`, `apple-silicon-m2`, `apple-silicon-m3`,
`apple-silicon-m4`; Pro/Max/Ultra variants share a tier.

It then loads `bench_baselines/{device_class}.json` from the CLI package,
falling back through parent slugs and finally `generic-{arch}.json`. The
chain is built by stripping trailing `-segment` suffixes, so
`apple-silicon-m4` tries `apple-silicon-m4.json` → `apple-silicon.json` →
`generic-arm64.json`. Missing files are silently skipped, so a newer
generation without a dedicated file still gets a sensible comparison. Use
`--baseline <path>` to override.

**Blessing a baseline for a new device class**

Ship-level baselines live inside the CLI package. To replace a provisional
baseline with real numbers captured on reference hardware:

```bash
# On the reference device (stable load, active cooling / MAXN power mode):
cyberwave edge bench \
    --rounds 500000 --warmup 20000 --repeat 5 --pin \
    --save-baseline ./jetson-orin-nano.json

# Then move the file into the package and commit.
```

The saved file uses the same schema the bench consumes. Set
`"provisional": false` once you are happy with the numbers. The file name
must match the `device_class` slug the fingerprint reports (e.g.
`apple-silicon-m4.json` on an M4 / M4 Pro / M4 Max MacBook). On macOS the
`--pin` flag is a no-op; plug in power, disable Low Power Mode, and close
background apps before blessing to avoid thermal throttling.

**Options**

| Flag | Default | Purpose |
| ---- | ------- | ------- |
| `-n, --rounds` | `100000` | Iterations per timed pass. |
| `--warmup` | `2000` | Un-timed warmup iterations before timing. |
| `--repeat` | `3` | Timed passes per benchmark; the median is reported. |
| `--threshold` | `0.15` | Regression threshold (fraction). |
| `--baseline PATH` | - | Override the auto-selected baseline file. |
| `--save-baseline PATH` | - | Write this run's metrics as a baseline file. |
| `--output PATH` | - | Write the full run result (fingerprint + metrics + deltas) as JSON. |
| `--pin` | `false` | Pin the bench to CPU 0 (Linux). |
| `--no-compare` | `false` | Skip baseline lookup and comparison. |

**Exit codes**: `0` when every metric is within threshold (or no baseline is
available), `2` when one or more metrics regress.

<!-- End of edge bench -->

## `cyberwave compute`

Manage the cloud node service — a GPU-powered companion that runs ML workloads in the cloud.

| Subcommand  | Description                                              |
| ----------- | -------------------------------------------------------- |
| `install`   | Install cyberwave-cloud-node and register a boot service |
| `uninstall` | Stop and remove the boot service                         |
| `start`     | Start the cloud node                                     |
| `stop`      | Stop the cloud node                                      |
| `restart`   | Restart the cloud node                                   |
| `status`    | Check if the cloud node is running                       |
| `logs`      | Show cloud node logs                                     |

```bash
sudo cyberwave compute install
sudo cyberwave compute install --channel dev
cyberwave compute start --slug my-gpu-node --profile gpu-a100
cyberwave compute status
cyberwave compute logs -f
cyberwave compute stop
```

On non-apt platforms, `cyberwave compute install --channel dev|staging` installs prerelease Python builds from the Buildkite Python registry; stable installs continue to use the public PyPI release.

## `cyberwave model`

Manage local ML model bindings on edge nodes. For most use cases, configure models via UI Workflows instead; these commands are for local/offline configuration.

| Subcommand | Description                           |
| ---------- | ------------------------------------- |
| `list`     | List available edge-compatible models |
| `bind`     | Configure a model for local inference |
| `show`     | Show current model configuration      |
| `remove`   | Remove a model binding                |

```bash
cyberwave model list
cyberwave model bind --model yolov8n --twin-uuid <UUID>
cyberwave model show
cyberwave model remove yolov8n
```

## `cyberwave plugin`

Manage edge node plugins (advanced — most users should use `cyberwave model` or UI Workflows).

| Subcommand  | Description           |
| ----------- | --------------------- |
| `list`      | List available plugins |
| `info`      | Show plugin details    |
| `install`   | Install/enable a plugin |
| `uninstall` | Uninstall a plugin     |

```bash
cyberwave plugin list
cyberwave plugin info yolo
cyberwave plugin install yolo
cyberwave plugin uninstall yolo
```

## `cyberwave environment`

Browse and inspect environments.

| Subcommand | Description                         |
| ---------- | ----------------------------------- |
| `list`     | List environments (table or `--json`) |
| `show`     | Show environment details and twins  |

```bash
cyberwave environment list
cyberwave environment list --json
cyberwave environment show <UUID>
```

## `cyberwave scan`

Discover IP cameras and NVRs on the local network using TCP port scanning, ONVIF WS-Discovery, and UPnP/SSDP.

```bash
cyberwave scan                        # auto-detect subnet
cyberwave scan -s 10.0.0              # specific subnet
cyberwave scan --json                 # JSON output
cyberwave scan --no-ports             # discovery protocols only
cyberwave scan -t 2.0                 # increase timeout
```

## `cyberwave camera`

Bootstrap a camera edge project — creates an environment, twin, and edge config for an IP camera.

```bash
cyberwave camera
cyberwave camera -u "rtsp://192.168.1.100:554/stream"
```

## `cyberwave so101`

Bootstrap a new SO-101 robot arm project. Clones the starter template and runs setup scripts.

```bash
cyberwave so101                       # default directory (./so101-project)
cyberwave so101 ~/projects/my-robot   # custom path
```

## `cyberwave manifest`

Validate `cyberwave.yml` manifest files.

```bash
cyberwave manifest validate                          # default: ./cyberwave.yml
cyberwave manifest validate path/to/cyberwave.yml
cyberwave manifest validate --lenient                # unknown fields as warnings
```

## `cyberwave configure`

Set API credentials directly without the interactive login flow. Useful when you already have a token from the dashboard.

```bash
cyberwave configure --token YOUR_TOKEN
cyberwave configure --token YOUR_TOKEN --base-url http://localhost:8000
cyberwave configure --internal-deb-read-token YOUR_BUILDKITE_DEB_TOKEN
cyberwave configure --internal-python-read-token YOUR_BUILDKITE_PYTHON_TOKEN
cyberwave configure --show
```

Private Buildkite read tokens are stored alongside the API credentials in `credentials.json`.
When prerelease (`dev`/`staging`) installs need access to `cyberwave-internal-deb` or
`cyberwave-internal-python`, the CLI prefers explicit environment variables first and then
falls back to those saved tokens.

## Configuration

Configuration is stored in a single directory shared by the CLI and the edge-core service. The directory is resolved as follows:

1. **`CYBERWAVE_EDGE_CONFIG_DIR`** env var — explicit override
2. **`~/.cyberwave`** — per-user directory (owner-only permissions `0700`)

Legacy installs that used `/etc/cyberwave` are automatically migrated on first CLI invocation.

Run `cyberwave config-dir` to see which directory is active.

**Files inside the config directory:**

- `credentials.json` — API token, workspace info, runtime env overrides, and optional private Buildkite registry read tokens (permissions `600`)
- `environment.json` — selected workspace, environment, and twin bindings
- `fingerprint.json` — unique edge device identifier

Other environment variables:

- `CYBERWAVE_BASE_URL`: Override the API URL (default: `https://api.cyberwave.com`)
- `CYBERWAVE_ENVIRONMENT`: Environment name (for example `dev`, defaults to `production`)
- `CYBERWAVE_MQTT_HOST`: MQTT broker host (for example `dev.mqtt.cyberwave.com` for dev; defaults to `mqtt.cyberwave.com`)

When credentials are written, the CLI also persists these `CYBERWAVE_*` values into
`credentials.json` so `cyberwave-edge-core` can reuse them in service mode.

## Building for Distribution

### PyInstaller (standalone binary)

```bash
pip install -e ".[build]"
pyinstaller --onefile --name cyberwave cyberwave_cli/main.py
```

### Debian Package

See `debian/` directory for packaging scripts.

## Contributing

Contributions are welcome. Please open an issue for bugs or feature requests, and submit a pull request for improvements.

## Support

- **Documentation**: [docs.cyberwave.com](https://docs.cyberwave.com)
- **Issues**: [GitHub Issues](https://github.com/cyberwave-os/cyberwave-cli/issues)
- **Community**: [Discord](https://discord.gg/dfGhNrawyF)
