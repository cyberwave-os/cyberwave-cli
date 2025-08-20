## CyberWave CLI

Command-line interface for interacting with the CyberWave platform, including managing assets, viewing telemetry, controlling simulations, and managing robot drivers.

This CLI is designed with a plugin architecture, allowing for modular extension of its capabilities.

## Installation

It is recommended to install the CyberWave CLI using `pipx` to ensure it runs in an isolated environment.

**Core CLI:**

```bash
pipx install cyberwave-cli
```

**Installing Plugins:**

Plugins are separate Python packages that add subcommands to the core CLI. Use `pipx inject` to add them to the core CLI's environment.

```bash
# Example: Install the 'assets' and 'telemetry' plugins
pipx inject cyberwave-cli cyberwave-cli-assets cyberwave-cli-telemetry

# Example: Install robotics integrations for driver management
pipx inject cyberwave-cli cyberwave-robotics-integrations

# Example: Install the core CLI along with the SDK dependency (for development/advanced use)
pipx install "cyberwave-cli[sdk]"
pipx inject cyberwave-cli cyberwave-cli-assets # ... add other plugins
```

### Authentication

### Web-Based Authentication (Recommended)

The CyberWave CLI supports secure web-based authentication using device flow, similar to GitHub CLI or AWS CLI. This is the recommended authentication method as it:

- ✅ Never requires entering passwords in the terminal
- ✅ Supports all authentication methods (SSO, 2FA, etc.)
- ✅ Uses secure token storage (system keychain)
- ✅ Automatically handles token refresh

#### Quick Start

```bash
# Authenticate with CyberWave
cyberwave auth login

# Check authentication status
cyberwave auth status

# Log out
cyberwave auth logout
```

#### How Device Flow Authentication Works

1. **Initiate:** Run `cyberwave auth login`
2. **Browser Opens:** Your web browser opens to the CyberWave authentication page
3. **Enter Code:** Enter the displayed code (e.g., `A4B7-C9D2`) on the web page
4. **Authenticate:** Complete authentication in your browser (login, 2FA, etc.)
5. **Success:** CLI automatically receives secure tokens and stores them

#### Authentication Commands

```bash
# Login with automatic browser opening
cyberwave auth login

# Login without opening browser (manual)
cyberwave auth login --no-browser

# Login with custom URLs (for development)
cyberwave auth login --backend-url http://localhost:8000 --frontend-url http://localhost:3000

# Check current authentication status
cyberwave auth status

# Log out and clear stored tokens
cyberwave auth logout
```

### Alternative Authentication Methods

#### Environment Variables (For CI/CD)

For automated environments, you can use environment variables:

```bash
export CYBERWAVE_USER="your-email@example.com"
export CYBERWAVE_PASSWORD="your-password"
export CYBERWAVE_BACKEND_URL="https://api.cyberwave.com"

# CLI will automatically use these credentials
cyberwave projects list
```

#### Manual Token Setting (Advanced)

For advanced use cases, you can manually set tokens using the SDK:

```python
from cyberwave import Client

client = Client()
client._access_token = "your-access-token"
client._refresh_token = "your-refresh-token"
client._save_token_to_cache()
```

### Configuration

The CLI stores settings in `~/.cyberwave/config.toml`. Use the config commands to manage settings:

```bash
# List all configuration
cyberwave auth config --list

# Set configuration values
cyberwave auth config backend_url https://api.cyberwave.com
cyberwave auth config frontend_url https://app.cyberwave.com
cyberwave auth config default_workspace 1
cyberwave auth config default_project 42

# Get a configuration value
cyberwave auth config backend_url

# Remove a configuration value
cyberwave auth config --unset default_workspace
```

### Configuration Options

| Key | Description | Default |
|-----|-------------|---------|
| `backend_url` | CyberWave backend API URL | `http://localhost:8000` |
| `frontend_url` | CyberWave frontend URL (for auth) | `http://localhost:3000` |
| `default_workspace` | Default workspace ID | None |
| `default_project` | Default project ID | None |

Once configured, you can omit workspace and project parameters from commands:

```bash
# After setting defaults
cyberwave auth config default_workspace 1
cyberwave auth config default_project 42

# These commands will use the defaults
cyberwave devices register --name "My Bot" --type rover
cyberwave projects list
```

### Usage

The main command is `cyberwave`:

```bash
# Show help and available commands (including loaded plugins)
cyberwave --help

# Show CLI version
cyberwave version

# Authentication commands
cyberwave auth login
cyberwave auth status
cyberwave auth logout

# Example: Use the 'assets' plugin (if installed)
cyberwave assets upload /path/to/mesh.glb --name "My Robot" --workspace 1 --project 42
cyberwave assets list

# Example: Use the 'devices' plugin (if installed)
cyberwave devices register --name "My Bot" --type rover --asset <asset-uuid>
cyberwave devices issue-offline-token --device 123

# Example: Use the 'drivers' plugin (if cyberwave_robotics_integrations installed)
cyberwave drivers list-drivers  # Show available drivers and asset mappings
cyberwave drivers discover      # Find robots in catalog with drivers
cyberwave drivers create-twin <asset-uuid> --name "My Robot" --project 123
cyberwave drivers start spot    # Start driver with automatic asset linking
cyberwave drivers status spot   # Check driver status

# Example: Use the 'projects' plugin (if installed)
cyberwave projects create --workspace 1 --name "Demo Project"
cyberwave projects list --workspace 1
```

### Robotics Driver Management

When the `cyberwave-robotics-integrations` plugin is installed, you gain access to powerful driver management commands that integrate with the asset catalog:

### Driver Commands

```bash
# List all available drivers with their asset mappings
cyberwave drivers list-drivers

# Discover robots from asset catalog that have drivers
cyberwave drivers discover [--project <id>] [--org <id>]

# Create a digital twin from an asset blueprint
cyberwave drivers create-twin <asset-uuid> --name "My Robot" --project 123

# Start a driver (automatically links to asset catalog)
cyberwave drivers start <driver-name> [options]
  --alias        Custom name for this robot instance
  --device-id    Use existing device ID
  --token        Offline token for telemetry
  --video        Enable video streaming
  --asset        Override asset registry ID

# Check status of running drivers
cyberwave drivers status [alias]

# Stop a running driver
cyberwave drivers stop <alias>

# Execute commands on running drivers
cyberwave drivers cmd <alias> <method>

# Serve web dashboard for local control
cyberwave drivers serve [--driver <name>]
```

### Asset-Driver Integration

The driver system automatically connects with the asset catalog:

1. **Asset Discovery**: When starting a driver, it finds the corresponding asset in the catalog
2. **Metadata Inheritance**: Drivers inherit specifications, capabilities, and 3D models from assets
3. **Digital Twin Creation**: Physical robots are registered as twins linked to asset blueprints
4. **Enhanced Telemetry**: Device telemetry includes asset context for better analytics

Example workflow:
```bash
# 1. Discover available robots
cyberwave drivers discover
# Output: Spot | mujoco/spot | spot | Boston Dynamics

# 2. Create a digital twin
cyberwave drivers create-twin <spot-asset-uuid> --name "Warehouse Spot" --project 123

# 3. Start the driver (links to asset automatically)
cyberwave drivers start spot --alias "warehouse-spot"
```

### Architecture

#### Components

| Artifact                | PyPI / Wheel Name         | Importable Python Package   | Console Command           | Description                                      |
| :---------------------- | :------------------------ | :-------------------------- | :------------------------ | :----------------------------------------------- |
| SDK                     | `cyberwave`               | `import cyberwave`          | (none)                    | Core Python library for programmatic interaction |
| **CLI (Core)**          | `cyberwave-cli`           | `import cyberwave_cli`      | `cyberwave`               | This package; provides the main entry point      |
| Built-in Plugin (Auth)  | (included)                | `cyberwave_cli.plugins.auth`| `cyberwave auth ...`      | Authentication and configuration management      |
| Built-in Plugin (Projects)| (included)              | `cyberwave_cli.plugins.projects`| `cyberwave projects ...` | Basic project management                      |
| Built-in Plugin (Devices) | (included)               | `cyberwave_cli.plugins.devices`| `cyberwave devices ...`   | Device registration and token management       |
| Optional Plugin (Drivers)| `cyberwave_robotics_integrations` | `cyberwave_robotics_integrations.cli` | `cyberwave drivers ...` | Robot driver management with asset integration |
| Optional Plugin (Assets)| `cyberwave-cli-assets`    | `cyberwave_cli_assets`    | `cyberwave assets ...`    | Manage catalog assets and upload meshes |
| Optional Plugin (Telemetry)|`cyberwave-cli-telemetry` | `cyberwave_cli_telemetry` | `cyberwave telemetry ...` | Adds telemetry viewing commands                |
| Optional Plugin (Sim)   | `cyberwave-cli-sim`       | `cyberwave_cli_sim`       | `cyberwave sim ...`       | Adds simulation control commands               |

**Note on Naming:** The console command is intentionally `cyberwave` for brevity and user experience, even though the core CLI package is `cyberwave-cli`. The Python packages (`cyberwave` vs `cyberwave_cli`) are distinct and do not clash.

#### Plugin System

The CLI uses Python's entry points (`cyberwave.cli.plugins` group) to discover and load plugins. Each plugin should be a separate installable package that provides a `typer.Typer` application object via its entry point.

- **Core (`cyberwave-cli`):** Ships the base `typer.Typer` application (`cyberwave_cli.core:app`) and the plugin loader (`cyberwave_cli.plugins.loader`).
  The console command now points to `cyberwave_cli.core:main` so that plugins are
  registered before the CLI executes. It defines the `cyberwave.cli.plugins`
  entry point group and provides built-in plugins.
- **Plugins (e.g., `cyberwave-cli-assets`):** Depend on `cyberwave-cli` and declare their entry points in their `pyproject.toml`:
  ```toml
  # Example from cyberwave-cli-assets/pyproject.toml
  [project.entry-points."cyberwave.cli.plugins"]
  assets = "cyberwave_cli_assets.app:app"
  ```

The loader (`loader.register_all`) iterates through these entry points, loads the specified `Typer` app, and adds it as a subcommand to the main `cyberwave` app using `root_app.add_typer(sub, name=ep.name)`.

## Edge Node (teleop) — SO-ARM100 example

The CLI includes an `edge` plugin to configure and run a Cyberwave Edge node which bridges a local robot driver to the cloud using the SDK.

### Install (dev, monorepo)

```bash
# From repo root
pip install -e cyberwave/cyberwave-sdk-python
pip install -e cyberwave/cyberwave_robotics_integrations
pip install -e cyberwave/cyberwave-cli[sdk]
```

### Authenticate (once)

```bash
cyberwave auth login --backend-url http://localhost:8000 --frontend-url http://localhost:3000
```

### Initialize an Edge config for SO-ARM100

Option A: auto-register device and issue a device token

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

Option B: use an existing device and token

```bash
cyberwave devices register -p <PROJECT_ID> -n edge-soarm100 -t robot/so-arm100
cyberwave devices issue-offline-token -d <DEVICE_ID>

cyberwave edge init \
  --robot so_arm100 \
  --port /dev/ttyUSB0 \
  --backend http://localhost:8000/api/v1 \
  --device-id <DEVICE_ID> \
  --use-device-token \
  --config ~/.cyberwave/edge.json
```

### Run the Edge node

```bash
cyberwave edge run --config ~/.cyberwave/edge.json
```

### Check status

```bash
cyberwave edge status --config ~/.cyberwave/edge.json
```

Notes:

- Ensure the serial port (`/dev/ttyUSB0`) matches your SO-ARM100 connection.
- The Edge runtime sends joint states periodically to the backend `devices/{device_id}/telemetry` endpoint via the SDK.
- To switch robot or server, update `~/.cyberwave/edge.json` and rerun.

### Security

#### Token Storage

The CLI stores authentication tokens securely using:

1. **Primary Storage:** System keychain/credential store
   - **macOS:** Keychain Access
   - **Windows:** Windows Credential Store
   - **Linux:** Secret Service (GNOME Keyring, KWallet, etc.)

2. **Fallback Storage:** Encrypted JSON file at `~/.cyberwave/token_cache.json`

#### Token Management

- **Access Tokens:** Short-lived tokens for API requests
- **Refresh Tokens:** Long-lived tokens for automatic renewal
- **Automatic Refresh:** Tokens are automatically refreshed when expired
- **Secure Cleanup:** Tokens are securely cleared on logout

## Troubleshooting

### Authentication Issues

**Problem:** `cyberwave auth login` fails to open browser
```bash
# Solution: Use manual mode
cyberwave auth login --no-browser
# Then manually visit the displayed URL
```

**Problem:** "Token may be expired" in status
```bash
# Solution: Login again
cyberwave auth logout
cyberwave auth login
```

**Problem:** CLI can't connect to backend
```bash
# Solution: Check/set backend URL
cyberwave auth config backend_url https://api.cyberwave.com
cyberwave auth status
```

### Configuration Issues

**Problem:** Commands require workspace/project every time
```bash
# Solution: Set defaults
cyberwave auth config default_workspace 1
cyberwave auth config default_project 42
```

**Problem:** Config file is corrupted
```bash
# Solution: Reset configuration
rm ~/.cyberwave/config.toml
cyberwave auth login
```

### Development Setup

For development against local servers:

```bash
# Configure for local development
cyberwave auth config backend_url http://localhost:8000
cyberwave auth config frontend_url http://localhost:3000

# Login against local servers
cyberwave auth login
```

## Plugin Development

1.  **Create a new Python package** (e.g., `cyberwave-cli-myplugin`).
2.  **Add `cyberwave-cli` as a dependency** in its `pyproject.toml`, likely with a version constraint (e.g., `cyberwave-cli >=0.11,<0.12`).
3.  **Create your Typer application** (e.g., in `src/cyberwave_cli_myplugin/app.py`).
4.  **Define the entry point** in your plugin's `pyproject.toml`:
    ```toml
    [project.entry-points."cyberwave.cli.plugins"]
    myplugin = "cyberwave_cli_myplugin.app:app"
    ```
5.  **Install** your plugin package into the same environment as `cyberwave-cli` (e.g., using `pipx inject cyberwave-cli cyberwave-cli-myplugin`).

### Plugin Authentication

Plugins can access authentication automatically through the SDK:

```python
import asyncio
from cyberwave import Client

async def my_command():
    client = Client()  # Automatically loads stored tokens
    await client.login()  # Only prompts if no valid tokens
    
    # Use authenticated client
    user_info = await client.get_current_user_info()
    print(f"Hello, {user_info['email']}!")
```

## Building Standalone Binaries

(Instructions for using PyInstaller or similar tools to create self-contained executables can be added here if needed, referencing the structure mentioned in the specification.)

### Guard Rails

- **Incorrect Package Import:** The `cyberwave` SDK package may emit a warning if executed directly (`python -m cyberwave`), directing users to install `cyberwave-cli`.
- **Missing Plugins:** The plugin loader attempts to provide helpful messages if a plugin fails to load.
- **Version Compatibility:** Plugin dependencies on `cyberwave-cli` help `pip` prevent installation of incompatible versions.
- **Authentication Failures:** Clear error messages guide users to re-authenticate when tokens are invalid or expired. 