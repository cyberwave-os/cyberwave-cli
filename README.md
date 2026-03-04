# Cyberwave CLI

The official command-line interface for Cyberwave. Authenticate and bootstrap robotics projects from your terminal.

## Installation

### From PyPI (pip)

```bash
pip install cyberwave-cli
```

### From APT (Debian/Ubuntu)

```bash
curl -fsSL https://cyberwave.com/install.sh | bash
```

### From Source

```bash
git clone https://github.com/cyberwave-os/cyberwave-cli
cd cyberwave-cli
pip install -e .
```

## Quick Start

SSH into your edge device, then run:

```bash
cyberwave login
cyberwave edge install
```

`cyberwave edge install` guides you through workspace and environment selection and registers the edge node as a systemd service that starts on boot.

## `cyberwave edge`

Manage the edge node service lifecycle, configuration, and monitoring.

| Subcommand       | Description                                              |
| ---------------- | -------------------------------------------------------- |
| `install`        | Install cyberwave-edge-core and register systemd service |
| `install-deps`   | Install edge ML dependencies                             |
| `list-drivers`   | List running driver containers                           |
| `list-models`    | List model bindings loaded on the edge node              |
| `logs`           | Show edge node logs                                      |
| `pull`           | Pull edge configuration from backend                     |
| `restart`        | Restart the edge node (systemd or process)               |
| `start`          | Start the edge node                                      |
| `status`         | Check if the edge node is running                        |
| `stop`           | Stop the edge node                                       |
| `stop-driver`    | Stop a running driver container                          |
| `sync-workflows` | Trigger workflow sync on the edge node                   |
| `uninstall`      | Stop and remove the systemd service                      |
| `whoami`         | Show device fingerprint and info                         |

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

### `cyberwave edge logs`

Streams logs from the systemd journal for the edge service.
cyberwave edge logs              # last 50 lines
cyberwave edge logs -n 100       # last 100 lines
cyberwave edge logs -f           # follow (tail -f)
```

### `cyberwave edge list-drivers`

Lists all running Docker containers whose name contains `cyberwave-driver`.

```bash
cyberwave edge list-drivers
```

### `cyberwave edge stop-driver`

Stops a named driver container. Disables any Docker restart policy first so the container does not restart automatically.

> **Note:** If the container is backed by a systemd service (e.g. on a Go2), Docker stop alone is not enough — systemd will restart it. Stop the backing service instead:
> ```bash
> sudo systemctl stop cyberwave-video-grabber.service
> ```

```bash
cyberwave edge stop-driver cyberwave-driver-624d7fe2
cyberwave edge stop-driver cyberwave-go2-driver
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

## Commands CLI tool

| Command      | Description                              |
| ------------ | ---------------------------------------- |
| `login`      | Authenticate with Cyberwave              |
| `logout`     | Remove stored credentials                |
| `edge`       | Manage the edge node service             |
| `config-dir` | Print the active configuration directory |

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

See the [Configuration](#configuration) section for the full resolution order.

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
