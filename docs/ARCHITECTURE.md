# Cyberwave CLI Architecture

## Overview

The Cyberwave CLI is a command-line tool for bootstrapping robotics and computer vision projects with the Cyberwave platform. It handles authentication, device setup, and edge node management.

```
┌─────────────────────────────────────────────────────────────────┐
│                         cyberwave-cli                           │
├─────────────────────────────────────────────────────────────────┤
│  main.py          Entry point, command registration, banner     │
├─────────────────────────────────────────────────────────────────┤
│  commands/        Click command groups                          │
│  ├── configure    Token/API configuration                       │
│  ├── camera       Camera twin setup wizard                      │
│  ├── edge         Edge node service management                  │
│  ├── scan         Network device discovery                      │
│  ├── model        ML model listing and binding                  │
│  └── plugin       Plugin management                             │
├─────────────────────────────────────────────────────────────────┤
│  credentials.py   Secure token storage (~/.cyberwave/)          │
│  config.py        API URLs, endpoints, defaults                 │
│  constants/       Shared plugin/model definitions               │
└─────────────────────────────────────────────────────────────────┘
          │
          │ Uses
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      cyberwave SDK                              │
│  - REST API client (environments, twins, assets, workspaces)    │
│  - MQTT client for real-time communication                      │
│  - Auto-discovery of resources                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

### 1. SDK-First Architecture

The CLI uses the `cyberwave` Python SDK for all API interactions:

```python
from cyberwave import Cyberwave

client = Cyberwave(base_url=get_api_url(), token=token)
envs = client.environments.list()
twin = client.twins.create(name="Camera", environment_id=env_id, asset_id=asset_id)
```

**Benefits:**
- Consistent authentication handling
- Automatic retry and error handling
- Type-safe API calls
- Single source of truth for API logic

### 2. Command Structure

Commands use Click with Rich for beautiful terminal output:

```
cyberwave-cli
├── configure     # One-time setup
├── login/logout  # Authentication
├── camera        # Interactive wizard for camera setup
├── edge          # Service lifecycle (start/stop/status)
├── scan          # Network discovery
├── model         # ML model catalog
└── plugin        # Plugin management
```

### 3. Credential Storage

Tokens stored securely in `~/.cyberwave/credentials.json`:

```python
@dataclass
class Credentials:
    token: str
    email: str | None
    workspace_uuid: str | None
```

- Unix: 600 permissions (user-only read/write)
- Loaded via `load_credentials()`, saved via `save_credentials()`

### 4. Graceful Fallbacks

When the API or SDK is unavailable, commands fall back to local data:

```python
def fetch_edge_models():
    try:
        client = Cyberwave(...)
        return client.api.mlmodels_list()
    except:
        return get_fallback_models()  # Local definitions
```

## File Structure

```
cyberwave_cli/
├── __init__.py           # Version
├── main.py               # Entry point, banner, command registration
├── config.py             # URLs, endpoints, paths
├── credentials.py        # Token storage/retrieval
├── commands/
│   ├── __init__.py       # Command exports
│   ├── configure.py      # `cyberwave configure --token`
│   ├── camera.py         # Camera setup wizard
│   ├── edge.py           # Edge service management
│   ├── scan.py           # Network scanner
│   ├── model.py          # ML model commands
│   └── plugin.py         # Plugin commands
├── constants/
│   ├── __init__.py       # Public exports
│   └── plugins.py        # Builtin plugin definitions
└── discovery/
    ├── __init__.py
    └── scanner.py        # ONVIF/UPnP/port scanning
```

## Command Workflows

### Camera Setup (`cyberwave camera`)

```
1. Check authentication (load_credentials)
2. Initialize SDK client
3. List/create environment
4. Find camera asset from catalog
5. Create twin with camera capabilities
6. Clone edge software (or use --local-edge)
7. Generate .env configuration
8. Print next steps
```

### Edge Management (`cyberwave edge`)

```
start   → Find .env, start edge service process
stop    → Signal running process to stop
status  → Check process state, show config
logs    → Tail edge service logs
```

### Model Binding (`cyberwave model bind`)

```
1. Fetch available models (SDK or fallback)
2. Select model and camera
3. Write binding to local config
4. Edge service picks up on next restart
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `click` | Command-line interface framework |
| `rich` | Beautiful terminal output (tables, colors, progress) |
| `cyberwave` | SDK for API and MQTT communication |
| `httpx` | HTTP client (used by SDK internally) |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CYBERWAVE_API_URL` | Backend API URL | `https://api.cyberwave.com` |
| `CYBERWAVE_TOKEN` | Auth token (alternative to credentials file) | - |

## Extension Points

### Adding a New Command

```python
# commands/mycommand.py
import click
from rich.console import Console

console = Console()

@click.command()
@click.option("--name", help="Name parameter")
def mycommand(name: str):
    """My new command description."""
    console.print(f"Hello, {name}!")
```

```python
# main.py
from .commands import mycommand
cli.add_command(mycommand)
```

### Adding Fallback Data

```python
# constants/plugins.py
BUILTIN_PLUGINS_FALLBACK["my-plugin"] = {
    "name": "My Plugin",
    "version": "1.0.0",
    ...
}
```

## Testing

```bash
# Install in dev mode
pip install -e .

# Run CLI
cyberwave-cli --help
cyberwave-cli model list
cyberwave-cli plugin list

# With debug output
CYBERWAVE_DEBUG=1 cyberwave-cli camera
```

## Examples

See the [examples](./examples/) directory for practical guides:

- [Quick Start](./examples/quick_start.md) - Get running in 5 minutes
- [Video Security System](./examples/video_security_system.md) - Complete security setup
- [Motion Detection](./examples/motion_detection.md) - ML-based detection config
- [Multi-Camera Setup](./examples/multi_camera.md) - Multiple camera deployment
