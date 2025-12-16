# Cyberwave CLI

The official command-line interface for Cyberwave. Authenticate and bootstrap robotics projects from your terminal.

## Installation

```bash
sudo apt install cyberwave-cli
```

## Quick Start

### 1. Authenticate

```bash
cyberwave-cli login
```

Enter your email and password when prompted. If you have multiple workspaces, you'll be asked to select one. Your credentials (including a permanent API token) are stored locally at `~/.cyberwave/credentials.json`.

### 2. Bootstrap a Project

```bash
# Set up camera streaming
cyberwave-cli camera
```

## Commands

| Command  | Description                                   |
| -------- | --------------------------------------------- |
| `login`  | Authenticate with Cyberwave                   |
| `logout` | Remove stored credentials                     |
| `so101`  | Clone and set up the SO-101 robot arm project |
| `camera` | Set up camera edge software for streaming     |

### `cyberwave-cli login`

Authenticates with Cyberwave using your email and password.

```bash
# Interactive login (prompts for credentials)
cyberwave-cli login

# Non-interactive login
cyberwave-cli login --email you@example.com --password yourpassword
```

**Options:**

- `-e, --email`: Email address
- `-p, --password`: Password (will prompt if not provided)

### `cyberwave-cli logout`

Removes stored credentials from your local machine.

```bash
cyberwave-cli logout
```

### `cyberwave-cli so101 [path]`

Bootstraps a new SO-101 robot arm project.

```bash
# Clone to default directory (./so101-project)
cyberwave-cli so101

# Clone to custom directory
cyberwave-cli so101 ~/projects/my-robot
```

**Arguments:**

- `path` (optional): Target directory for the project. Defaults to `./so101-project`

### `cyberwave-cli camera [path]`

Sets up camera edge software for streaming to Cyberwave. Creates an environment and twin, then clones the camera edge repository with pre-configured credentials.

```bash
# Clone to default directory (./cyberwave-camera)
cyberwave-cli camera

# Clone to custom directory
cyberwave-cli camera ~/projects/my-camera

# Use an existing environment
cyberwave-cli camera -e <environment-uuid>

# Create a new environment with a specific name
cyberwave-cli camera -n "My Camera Setup"
```

**Arguments:**

- `path` (optional): Target directory for the project. Defaults to `./cyberwave-camera`

**Options:**

- `-e, --environment-uuid`: UUID of an existing environment to use
- `-n, --environment-name`: Name for a new environment (creates one if `--environment-uuid` not provided)

## Configuration

Credentials are stored in `~/.cyberwave/credentials.json` and include:

- API token
- Email
- Workspace UUID and name

## Building for Distribution

### PyInstaller (standalone binary)

```bash
pip install -e ".[build]"
pyinstaller --onefile --name cyberwave-cli cyberwave_cli/main.py
```

## Support

- **Documentation**: [docs.cyberwave.com](https://docs.cyberwave.com)
- **Issues**: [GitHub Issues](https://github.com/cyberwave-os/cyberwave-cli/issues)
- **Community**: [Discord](https://discord.gg/dfGhNrawyF)
