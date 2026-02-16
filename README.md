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

## Quick Start

### 1. Authenticate

```bash
cyberwave-cli login
```

Enter your email and password when prompted. Your credentials are stored locally at `~/.cyberwave/credentials.json`.

### 2. Bootstrap a Project

```bash
cyberwave-cli so101
```

This clones the SO-101 robot arm starter template and runs the setup scripts.

## Commands

| Command  | Description                                   |
| -------- | --------------------------------------------- |
| `login`  | Authenticate with Cyberwave                   |
| `logout` | Remove stored credentials                     |
| `so101`  | Clone and set up the SO-101 robot arm project |

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

## Configuration

Credentials are stored in `~/.cyberwave/credentials.json` with restricted permissions (600).

Environment variables:

- `CYBERWAVE_API_URL`: Override the API URL (default: `https://api.cyberwave.com`)

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
