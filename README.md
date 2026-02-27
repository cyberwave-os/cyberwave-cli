# Cyberwave CLI

The official command-line interface for Cyberwave. Authenticate and bootstrap robotics projects from your terminal.

## Installation

### From PyPI (pip)

```bash
pip install cyberwave-cli
```

### From APT (Debian/Ubuntu)

```bash
# You may need to install curl and/or gpg first, if you're on a very minimal host:
sudo apt update && sudo apt install curl gpg -y

# Add Cyberwave repository (one-time setup)
curl -fsSL "https://packages.buildkite.com/cyberwave/cyberwave-cli/gpgkey" | sudo gpg --dearmor -o /etc/apt/keyrings/cyberwave_cyberwave-cli-archive-keyring.gpg

# Configure the source
echo -e "deb [signed-by=/etc/apt/keyrings/cyberwave_cyberwave-cli-archive-keyring.gpg] https://packages.buildkite.com/cyberwave/cyberwave-cli/any/ any main\ndeb-src [signed-by=/etc/apt/keyrings/cyberwave_cyberwave-cli-archive-keyring.gpg] https://packages.buildkite.com/cyberwave/cyberwave-cli/any/ any main" | sudo tee /etc/apt/sources.list.d/buildkite-cyberwave-cyberwave-cli.list > /dev/null

# Install
sudo apt update && sudo apt install cyberwave-cli
```

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

| Command      | Description                              |
| ------------ | ---------------------------------------- |
| `login`      | Authenticate with Cyberwave              |
| `logout`     | Remove stored credentials                |
| `config-dir` | Print the active configuration directory |
| `core`       | Visualize the core commands              |

### `cyberwave login`

Authenticates with Cyberwave using your email and password.

```bash
# Interactive login (prompts for credentials)
cyberwave login

# Non-interactive login
cyberwave login --email you@example.com --password yourpassword
```

**Options:**

- `-e, --email`: Email address
- `-p, --password`: Password (will prompt if not provided)

### `cyberwave config-dir`

Prints the resolved configuration directory path. Useful in scripts to locate credentials and config files without hardcoding paths.

```bash
cyberwave config-dir
# /etc/cyberwave

# Use in a script
CONFIG_DIR=$(cyberwave config-dir)
cat "$CONFIG_DIR/credentials.json"
```

The CLI resolves the directory with the following priority:

1. `CYBERWAVE_EDGE_CONFIG_DIR` environment variable (explicit override)
2. `/etc/cyberwave` if writable or creatable (system-wide, preferred)
3. `~/.cyberwave` as a fallback for non-root users

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

### `cyberwave edge install`

Installs the `cyberwave-edge-core` package (via apt-get on Debian/Ubuntu) and creates a systemd service so it starts on boot. Guides you through workspace and environment selection.

```bash
sudo cyberwave edge install
sudo cyberwave edge install -y   # skip prompts
```

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

## Configuration

Configuration is stored in a single directory shared by the CLI and the edge-core service. The directory is resolved as follows:

1. **`CYBERWAVE_EDGE_CONFIG_DIR`** env var — explicit override
2. **`/etc/cyberwave`** — system-wide (preferred, requires root or write access)
3. **`~/.cyberwave`** — per-user fallback for non-root environments

Run `cyberwave config-dir` to see which directory is active.

**Files inside the config directory:**

- `credentials.json` — API token and workspace info (permissions `600`)
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
pyinstaller --onefile --name cyberwave-cli cyberwave_cli/main.py
```

### Debian Package

See `debian/` directory for packaging scripts.

## Support

- **Documentation**: [docs.cyberwave.com](https://docs.cyberwave.com)
- **Issues**: [GitHub Issues](https://github.com/cyberwave-os/cyberwave-cli/issues)
- **Community**: [Discord](https://discord.gg/dfGhNrawyF)
