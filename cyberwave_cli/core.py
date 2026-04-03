"""Install and manage the cyberwave-edge-core systemd service.

This module provides the logic for:
  1. Installing the cyberwave-edge-core .deb package via apt-get
  2. Creating a systemd service unit so it starts on boot
  3. Enabling and starting the service
"""

import json
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyberwave.fingerprint import generate_fingerprint
from rich.console import Console
from rich.prompt import Confirm, Prompt

from .auth import APIToken, AuthClient, AuthenticationError
from .config import CONFIG_DIR, clean_subprocess_env, get_api_url
from .credentials import (
    Credentials,
    collect_runtime_env_overrides,
    load_credentials,
    save_credentials,
)

console = Console()

# ---- constants ---------------------------------------------------------------

PACKAGE_NAME = "cyberwave-edge-core"
EDGE_CORE_PACKAGE_CHANNELS = {
    "stable": PACKAGE_NAME,
    "dev": "cyberwave-edge-core-dev",
    "staging": "cyberwave-edge-core-staging",
}
BINARY_PATH = Path("/usr/bin/cyberwave-edge-core")
SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
SYSTEMD_UNIT_PATH = Path(f"/etc/systemd/system/{SYSTEMD_UNIT_NAME}")
ENVIRONMENT_FILE = CONFIG_DIR / "environment.json"
FINGERPRINT_FILE = CONFIG_DIR / "fingerprint.json"

# Buildkite Debian registry for the cyberwave-edge-core package
BUILDKITE_DEB_REPO_URL = "https://packages.buildkite.com/cyberwave/cyberwave-edge-core/any/"
BUILDKITE_GPG_KEY_URL = "https://packages.buildkite.com/cyberwave/cyberwave-edge-core/gpgkey"
BUILDKITE_KEYRING_PATH = Path("/etc/apt/keyrings/cyberwave_cyberwave-edge-core-archive-keyring.gpg")

SYSTEMD_UNIT_TEMPLATE = textwrap.dedent("""\
    [Unit]
    Description=Cyberwave Edge Core Orchestrator
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    ExecStart={binary_path}
    Restart=on-failure
    RestartSec=5
    Environment=CYBERWAVE_EDGE_CONFIG_DIR=/etc/cyberwave
    StandardOutput=journal
    StandardError=journal
    SyslogIdentifier=cyberwave-edge-core

    [Install]
    WantedBy=multi-user.target
""")


@dataclass
class ServiceSpec:
    package_name: str
    binary_path: Path
    unit_name: str
    unit_path: Path
    package_channels: dict[str, str]
    deb_repo_url: str
    gpg_key_url: str
    keyring_path: Path
    sources_list_path: Path
    process_match: str
    sudo_command_hint: str
    unit_template: str
    requires_docker: bool


EDGE_CORE_SPEC = ServiceSpec(
    package_name=PACKAGE_NAME,
    binary_path=BINARY_PATH,
    unit_name=SYSTEMD_UNIT_NAME,
    unit_path=SYSTEMD_UNIT_PATH,
    package_channels=EDGE_CORE_PACKAGE_CHANNELS,
    deb_repo_url=BUILDKITE_DEB_REPO_URL,
    gpg_key_url=BUILDKITE_GPG_KEY_URL,
    keyring_path=BUILDKITE_KEYRING_PATH,
    sources_list_path=Path("/etc/apt/sources.list.d/buildkite-cyberwave-cyberwave-edge-core.list"),
    process_match="cyberwave_edge.service",
    sudo_command_hint="sudo cyberwave edge install",
    unit_template=SYSTEMD_UNIT_TEMPLATE,
    requires_docker=True,
)

# ---- cloud node service constants -------------------------------------------

_CLOUD_NODE_PACKAGE_NAME = "cyberwave-cloud-node"
_CLOUD_NODE_UNIT_NAME = "cyberwave-cloud-node.service"
_CLOUD_NODE_UNIT_PATH = Path(f"/etc/systemd/system/{_CLOUD_NODE_UNIT_NAME}")

_CLOUD_NODE_UNIT_TEMPLATE = textwrap.dedent("""\
    [Unit]
    Description=Cyberwave Cloud Node
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    ExecStart={binary_path} start
    Restart=on-failure
    RestartSec=5
    StandardOutput=journal
    StandardError=journal
    SyslogIdentifier=cyberwave-cloud-node

    [Install]
    WantedBy=multi-user.target
""")

CLOUD_NODE_SPEC = ServiceSpec(
    package_name=_CLOUD_NODE_PACKAGE_NAME,
    binary_path=Path("/usr/bin/cyberwave-cloud-node"),
    unit_name=_CLOUD_NODE_UNIT_NAME,
    unit_path=_CLOUD_NODE_UNIT_PATH,
    package_channels={
        "stable": "cyberwave-cloud-node",
        "dev": "cyberwave-cloud-node-dev",
        "staging": "cyberwave-cloud-node-staging",
    },
    deb_repo_url="https://packages.buildkite.com/cyberwave/cyberwave-cloud-node/any/",
    gpg_key_url="https://packages.buildkite.com/cyberwave/cyberwave-cloud-node/gpgkey",
    keyring_path=Path("/etc/apt/keyrings/cyberwave_cyberwave-cloud-node-archive-keyring.gpg"),
    sources_list_path=Path("/etc/apt/sources.list.d/buildkite-cyberwave-cyberwave-cloud-node.list"),
    process_match="cyberwave-cloud-node start",
    sudo_command_hint="sudo cyberwave compute install",
    unit_template=_CLOUD_NODE_UNIT_TEMPLATE,
    requires_docker=False,
)


# ---- helpers -----------------------------------------------------------------


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _has_systemd() -> bool:
    return Path("/run/systemd/system").is_dir()


def is_service_active(spec: ServiceSpec = EDGE_CORE_SPEC) -> bool:
    """Return True if the systemd service described by ``spec`` is currently active."""
    if not _has_systemd():
        return False
    try:
        result = subprocess.run(
            ["systemctl", "is-active", spec.unit_name],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "active"
    except (FileNotFoundError, OSError):
        return False


def _run(cmd: list[str], *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess and stream output to the console."""
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    kwargs.setdefault("env", clean_subprocess_env())
    return subprocess.run(cmd, check=check, **kwargs)


def _select_with_arrows(title: str, options: list[str]) -> int:
    """Interactive arrow-key selector. Falls back to numeric prompt."""
    if not options:
        raise ValueError("options cannot be empty")

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print(f"\n[bold]{title}[/bold]")
        for idx, option in enumerate(options, 1):
            console.print(f"  {idx}. {option}")
        while True:
            raw = Prompt.ask("Select option number", default="1")
            try:
                chosen = int(raw) - 1
                if 0 <= chosen < len(options):
                    return chosen
            except ValueError:
                pass
            console.print(f"[red]Please enter a number between 1 and {len(options)}[/red]")

    try:
        import termios
        import tty
    except ImportError:
        # Non-POSIX fallback
        console.print(f"\n[bold]{title}[/bold]")
        for idx, option in enumerate(options, 1):
            console.print(f"  {idx}. {option}")
        while True:
            raw = Prompt.ask("Select option number", default="1")
            try:
                chosen = int(raw) - 1
                if 0 <= chosen < len(options):
                    return chosen
            except ValueError:
                pass
            console.print(f"[red]Please enter a number between 1 and {len(options)}[/red]")

    selected = 0
    scroll_offset = 0
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        term_height = shutil.get_terminal_size().lines
    except Exception:
        term_height = 24
    # Reserve lines for: title(1) + instructions(1) + blank(1) + scroll indicators(2)
    max_visible = max(5, term_height - 5)

    def _tty_write(text: str) -> None:
        """Write text in raw TTY mode using CRLF line endings."""
        sys.stdout.write(text.replace("\n", "\r\n"))

    def _render() -> None:
        nonlocal scroll_offset
        # Keep selected item within the visible viewport
        if selected < scroll_offset:
            scroll_offset = selected
        elif selected >= scroll_offset + max_visible:
            scroll_offset = selected - max_visible + 1

        _tty_write("\x1b[2J\x1b[H")
        _tty_write(f"{title}\n")
        _tty_write("Use \u2191/\u2193 and press Enter, q/Ctrl-C to abort\n\n")

        visible_end = min(scroll_offset + max_visible, len(options))

        if scroll_offset > 0:
            _tty_write(f"  \u2191 {scroll_offset} more above\n")

        for idx in range(scroll_offset, visible_end):
            prefix = "❯" if idx == selected else " "
            _tty_write(f"{prefix} {options[idx]}\n")

        remaining = len(options) - visible_end
        if remaining > 0:
            _tty_write(f"  \u2193 {remaining} more below\n")

        sys.stdout.flush()

    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")
        _render()
        while True:
            char = sys.stdin.read(1)
            if char in ("\r", "\n"):
                return selected
            if char in ("\x03", "q", "Q"):
                raise KeyboardInterrupt
            if char == "\x1b":
                nxt = sys.stdin.read(1)
                if nxt == "[":
                    arrow = sys.stdin.read(1)
                    if arrow == "A":
                        selected = (selected - 1) % len(options)
                        _render()
                    elif arrow == "B":
                        selected = (selected + 1) % len(options)
                        _render()
            elif char.lower() == "k":
                selected = (selected - 1) % len(options)
                _render()
            elif char.lower() == "j":
                selected = (selected + 1) % len(options)
                _render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def _get_sdk_client(token: str, *, base_url: str | None = None):
    """Create a Cyberwave SDK client from a token."""
    from cyberwave import Cyberwave

    return Cyberwave(base_url=base_url or get_api_url(), token=token)


def _save_environment_file(
    *,
    workspace_uuid: str,
    workspace_name: str,
    environment_uuid: str | None = None,
    environment_name: str | None = None,
    twin_uuids: list[str] | None = None,
) -> None:
    """Persist selected workspace/environment for edge startup."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "workspace_uuid": workspace_uuid,
        "workspace_name": workspace_name,
    }
    if environment_uuid:
        payload["uuid"] = environment_uuid
    if environment_name:
        payload["name"] = environment_name
    if twin_uuids is not None:
        payload["twin_uuids"] = twin_uuids

    serialized_payload = json.dumps(payload, indent=2) + "\n"

    # Write atomically so edge-core never observes a partially written file.
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=CONFIG_DIR,
        prefix=f".{ENVIRONMENT_FILE.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)
        tmp_file.write(serialized_payload)
        tmp_file.flush()
        os.fsync(tmp_file.fileno())

    os.replace(tmp_path, ENVIRONMENT_FILE)

    # Keep same permission model as credentials.
    if os.name != "nt":
        os.chmod(ENVIRONMENT_FILE, 0o600)
        # Also fsync the directory to persist the rename event.
        dir_fd = os.open(CONFIG_DIR, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _load_or_generate_edge_fingerprint() -> str:
    """Load edge fingerprint saved by edge-core, fallback to CLI generator."""
    if FINGERPRINT_FILE.exists():
        try:
            data = json.loads(FINGERPRINT_FILE.read_text(encoding="utf-8"))
            value = data.get("fingerprint")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except Exception:
            pass

    return generate_fingerprint()


def _resolved_edge_log_level(runtime_overrides: dict[str, str | None]) -> str | None:
    """Return persisted edge log level based on runtime overrides."""
    environment = (runtime_overrides.get("CYBERWAVE_ENVIRONMENT") or "").strip().lower()
    if environment and environment != "production":
        return "debug"
    return runtime_overrides.get("CYBERWAVE_EDGE_LOG_LEVEL")


def _ensure_credentials(*, skip_confirm: bool) -> bool:
    """Ensure valid credentials exist in /etc/cyberwave/ before installing.

    If saved credentials are found and valid, returns True immediately.
    Otherwise prompts for email/password and runs the full login flow.
    """
    creds = load_credentials()
    if creds and creds.token:
        try:
            creds_base_url = creds.cyberwave_base_url
            sdk_client = _get_sdk_client(creds.token, base_url=creds_base_url)
            with console.status("[dim]Checking existing credentials...[/dim]"):
                sdk_client.workspaces.list()
            console.print(f"[green]✓[/green] Logged in as [bold]{creds.email}[/bold]")
            # Backfill persisted environment overrides when running with explicit
            # env vars so systemd startups can reuse them later.
            runtime_overrides = collect_runtime_env_overrides()
            if runtime_overrides:
                save_credentials(
                    Credentials(
                        token=creds.token,
                        email=creds.email,
                        created_at=creds.created_at,
                        workspace_uuid=creds.workspace_uuid,
                        workspace_name=creds.workspace_name,
                        cyberwave_environment=runtime_overrides.get("CYBERWAVE_ENVIRONMENT"),
                        cyberwave_edge_log_level=_resolved_edge_log_level(runtime_overrides),
                        cyberwave_base_url=runtime_overrides.get("CYBERWAVE_BASE_URL"),
                        cyberwave_mqtt_host=runtime_overrides.get("CYBERWAVE_MQTT_HOST"),
                    )
                )
            return True
        except Exception as e:
            console.print("[yellow]Stored credentials are invalid or expired.[/yellow]")
            console.print(e)  # print the error for debugging purposes

    console.print("[yellow]No valid credentials found.[/yellow]")
    env_token = os.getenv("CYBERWAVE_API_KEY", "").strip()
    if env_token:
        try:
            runtime_overrides = collect_runtime_env_overrides()
            sdk_client = _get_sdk_client(
                env_token,
                base_url=runtime_overrides.get("CYBERWAVE_BASE_URL") or None,
            )
            with console.status("[dim]Checking CYBERWAVE_API_KEY...[/dim]"):
                workspace = _select_workspace_from_env_or_default(
                    sdk_client,
                    skip_confirm=skip_confirm,
                )

            save_credentials(
                Credentials(
                    token=env_token,
                    workspace_uuid=str(getattr(workspace, "uuid", "") or ""),
                    workspace_name=str(getattr(workspace, "name", "") or ""),
                    cyberwave_environment=runtime_overrides.get("CYBERWAVE_ENVIRONMENT"),
                    cyberwave_edge_log_level=_resolved_edge_log_level(runtime_overrides),
                    cyberwave_base_url=runtime_overrides.get("CYBERWAVE_BASE_URL"),
                    cyberwave_mqtt_host=runtime_overrides.get("CYBERWAVE_MQTT_HOST"),
                )
            )
            console.print("[green]✓[/green] Using CYBERWAVE_API_KEY from environment")
            console.print(f"[dim]Workspace: {workspace.name}[/dim]")
            console.print(f"[dim]Credentials saved to {CONFIG_DIR}/[/dim]\n")
            return True
        except Exception as e:
            console.print("[yellow]CYBERWAVE_API_KEY is invalid or incomplete.[/yellow]")
            console.print(e)

    console.print("[cyan]Please log in to continue.[/cyan]\n")

    email = Prompt.ask("[bold]Email[/bold]")
    password = Prompt.ask("[bold]Password[/bold]", password=True)

    try:
        runtime_overrides = collect_runtime_env_overrides()
        with AuthClient() as client:
            with console.status("[dim]Authenticating...[/dim]"):
                session_token = client.login(email, password)
                user = client.get_current_user(session_token)
                workspaces = client.get_workspaces(session_token)

            if not workspaces:
                console.print(
                    f"[yellow]Logged in as [bold]{user.email}[/bold] "
                    "but no workspaces found.[/yellow]"
                )
                console.print("[dim]Create a workspace at https://cyberwave.com first.[/dim]")
                return False

            if len(workspaces) == 1:
                workspace = workspaces[0]
            elif skip_confirm:
                workspace = workspaces[0]
                console.print(f"[yellow]Auto-selecting workspace:[/yellow] {workspace.name}")
            else:
                labels = [f"{ws.name} ({ws.uuid[:8]}...)" for ws in workspaces]
                idx = _select_with_arrows("Select a workspace", labels)
                workspace = workspaces[idx]

            with console.status(f"[dim]Creating API token for workspace '{workspace.name}'...[/dim]"):
                api_token: APIToken = client.create_api_token(session_token, workspace.uuid)

            save_credentials(
                Credentials(
                    token=api_token.token,
                    email=user.email,
                    workspace_uuid=workspace.uuid,
                    workspace_name=workspace.name,
                    cyberwave_environment=runtime_overrides.get("CYBERWAVE_ENVIRONMENT"),
                    cyberwave_edge_log_level=_resolved_edge_log_level(runtime_overrides),
                    cyberwave_base_url=runtime_overrides.get("CYBERWAVE_BASE_URL"),
                    cyberwave_mqtt_host=runtime_overrides.get("CYBERWAVE_MQTT_HOST"),
                )
            )

            console.print(f"[green]✓[/green] Logged in as [bold]{user.email}[/bold]")
            console.print(f"[dim]Workspace: {workspace.name}[/dim]")
            console.print(f"[dim]Credentials saved to {CONFIG_DIR}/[/dim]\n")
            return True

    except AuthenticationError as exc:
        console.print(f"[red]Login failed:[/red] {exc}")
        return False


def _select_workspace_from_env_or_default(client: Any, *, skip_confirm: bool) -> Any:
    """Pick a workspace, honoring ``CYBERWAVE_WORKSPACE_SLUG`` when provided."""
    workspaces = client.workspaces.list()
    if not workspaces:
        raise RuntimeError("No workspaces available for this account.")

    workspace_slug = os.getenv("CYBERWAVE_WORKSPACE_SLUG", "").strip().lower()
    if workspace_slug:
        for workspace in workspaces:
            candidate_slug = str(getattr(workspace, "slug", "") or "").strip().lower()
            if candidate_slug == workspace_slug:
                console.print(f"[green]Workspace:[/green] {workspace.name}")
                return workspace
        raise RuntimeError(
            f"Workspace slug '{workspace_slug}' was not found for the provided token."
        )

    if len(workspaces) == 1:
        workspace = workspaces[0]
        console.print(f"[green]Workspace:[/green] {workspace.name}")
        return workspace

    if skip_confirm:
        workspace = workspaces[0]
        console.print(f"[yellow]Auto-selecting workspace:[/yellow] {workspace.name}")
        return workspace

    labels = [f"{workspace.name} ({str(workspace.uuid)[:8]}...)" for workspace in workspaces]
    idx = _select_with_arrows("Select a workspace", labels)
    return workspaces[idx]


def _select_workspace(client: Any, *, skip_confirm: bool) -> Any:
    """Get workspaces via SDK and let user select one."""
    return _select_workspace_from_env_or_default(client, skip_confirm=skip_confirm)


def _resolve_workspace_from_credentials(client: Any, workspace_uuid: str) -> Any | None:
    """Return the SDK workspace matching ``workspace_uuid``, or None if not listed."""
    raw = str(workspace_uuid).strip()
    if not raw:
        return None
    target = raw.lower()
    try:
        workspaces = client.workspaces.list()
    except Exception:
        return None
    for ws in workspaces:
        ws_id = str(getattr(ws, "uuid", "") or "").strip().lower()
        if ws_id == target:
            return ws
    return None


def _workspace_projects(client: Any, workspace_uuid: str) -> list[Any]:
    """Return projects that belong to the selected workspace."""
    projects = client.projects.list()
    result = []
    for project in projects:
        project_workspace_uuid = str(
            getattr(project, "workspace_uuid", "") or getattr(project, "workspace_id", "") or ""
        )
        if project_workspace_uuid == workspace_uuid:
            result.append(project)
    return result


def _environment_workspace_uuid(environment: Any) -> str:
    """Best-effort workspace UUID extraction for environment objects."""
    workspace_uuid = str(
        getattr(environment, "workspace_uuid", "")
        or getattr(environment, "workspace_id", "")
        or ""
    )
    if workspace_uuid:
        return workspace_uuid

    settings = getattr(environment, "settings", None)
    if isinstance(settings, dict):
        standalone_workspace_uuid = settings.get("_workspace_uuid")
        if isinstance(standalone_workspace_uuid, str) and standalone_workspace_uuid:
            return standalone_workspace_uuid

    project = getattr(environment, "project", None)
    if project is not None:
        project_workspace_uuid = str(
            getattr(project, "workspace_uuid", "") or getattr(project, "workspace_id", "") or ""
        )
        if project_workspace_uuid:
            return project_workspace_uuid

        workspace = getattr(project, "workspace", None)
        if workspace is not None:
            nested_workspace_uuid = str(getattr(workspace, "uuid", "") or "")
            if nested_workspace_uuid:
                return nested_workspace_uuid

    return ""


def _workspace_environments(client: Any, workspace_uuid: str) -> list[Any]:
    """Return environments for the selected workspace.

    Includes both project-scoped environments and standalone environments
    attached directly to a workspace.
    """
    environments: list[Any] = []
    seen_uuids: set[str] = set()

    # Primary: fetch all environments visible to the user. The backend already
    # scopes results to the caller's workspaces/ownership, so this includes
    # both project-scoped and standalone environments.
    try:
        all_environments = client.environments.list()
    except Exception:
        all_environments = []

    for env in all_environments:
        env_ws = _environment_workspace_uuid(env)
        # Include the environment when it belongs to the selected workspace OR
        # when its workspace could not be resolved (standalone environments
        # without a _workspace_uuid setting). The backend already restricts
        # the listing to environments the user has access to, so unresolved
        # environments are safe to show.
        if env_ws and env_ws != workspace_uuid:
            continue
        env_uuid = str(getattr(env, "uuid", ""))
        if env_uuid and env_uuid not in seen_uuids:
            environments.append(env)
            seen_uuids.add(env_uuid)

    # Supplement with project-scoped discovery so we never miss environments
    # that the global listing might have filtered differently.
    projects = _workspace_projects(client, workspace_uuid)
    for project in projects:
        try:
            envs = client.environments.list(project_id=str(project.uuid))
        except Exception:
            continue
        for env in envs:
            env_uuid = str(getattr(env, "uuid", ""))
            if env_uuid and env_uuid not in seen_uuids:
                environments.append(env)
                seen_uuids.add(env_uuid)

    return environments


def _create_environment_in_workspace(
    client: Any, workspace_uuid: str, *, skip_confirm: bool
) -> Any:
    """Create a new environment inside the selected workspace."""
    projects = _workspace_projects(client, workspace_uuid)
    if projects:
        project_id = str(projects[0].uuid)
    else:
        console.print("[cyan]No project found in selected workspace. Creating one...[/cyan]")
        project = client.projects.create(
            name="Edge Project",
            workspace_id=workspace_uuid,
            description="Project created by cyberwave edge install",
        )
        project_id = str(project.uuid)

    env_name = "Edge Environment"
    if not skip_confirm:
        env_name = Prompt.ask("New environment name", default=env_name)
    environment = client.environments.create(
        name=env_name,
        project_id=project_id,
        description="Environment created by cyberwave edge install",
    )
    console.print(f"[green]Created environment:[/green] {environment.name}")
    return environment


def _select_or_create_environment(client: Any, workspace_uuid: str, *, skip_confirm: bool) -> Any:
    """Pick existing environment or create a new one."""
    environments = _workspace_environments(client, workspace_uuid)

    if not environments:
        console.print("[yellow]No environments found for the selected workspace.[/yellow]")
        return _create_environment_in_workspace(client, workspace_uuid, skip_confirm=skip_confirm)

    if skip_confirm:
        return environments[0]

    labels = [f"{getattr(env, 'name', 'Unnamed')} ({str(env.uuid)[:8]}...)" for env in environments]
    page_size = 10
    view_more_threshold = 10
    visible_count = page_size if len(environments) > view_more_threshold else len(environments)

    while True:
        has_more = visible_count < len(environments)
        visible_labels = labels[:visible_count]
        options = [*visible_labels, "Create new environment"]
        if has_more:
            options.append("View more")

        idx = _select_with_arrows("Select an environment", options)
        if has_more and idx == len(options) - 1:
            visible_count = min(visible_count + page_size, len(environments))
            continue
        if idx == len(visible_labels):
            return _create_environment_in_workspace(
                client, workspace_uuid, skip_confirm=skip_confirm
            )
        return environments[idx]


def _select_multiple_with_arrows(title: str, options: list[str]) -> list[int]:
    """Interactive multi-select. Toggle with Space, confirm with Enter."""
    if not options:
        return []

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print(f"\n[bold]{title}[/bold]")
        for idx, option in enumerate(options, 1):
            console.print(f"  {idx}. {option}")
        raw = Prompt.ask(
            "Select one or more (comma-separated numbers, empty for none)",
            default="",
        ).strip()
        if not raw:
            return []
        selected: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                idx = int(part) - 1
            except ValueError:
                continue
            if 0 <= idx < len(options) and idx not in selected:
                selected.append(idx)
        return selected

    try:
        import termios
        import tty
    except ImportError:
        console.print(f"\n[bold]{title}[/bold]")
        for idx, option in enumerate(options, 1):
            console.print(f"  {idx}. {option}")
        raw = Prompt.ask(
            "Select one or more (comma-separated numbers, empty for none)",
            default="",
        ).strip()
        if not raw:
            return []
        selected_fallback: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                idx = int(part) - 1
            except ValueError:
                continue
            if 0 <= idx < len(options) and idx not in selected_fallback:
                selected_fallback.append(idx)
        return selected_fallback

    cursor = 0
    scroll_offset = 0
    selected: set[int] = set()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        term_height = shutil.get_terminal_size().lines
    except Exception:
        term_height = 24
    max_visible = max(5, term_height - 5)

    def _tty_write(text: str) -> None:
        """Write text in raw TTY mode using CRLF line endings."""
        sys.stdout.write(text.replace("\n", "\r\n"))

    def _render() -> None:
        nonlocal scroll_offset
        if cursor < scroll_offset:
            scroll_offset = cursor
        elif cursor >= scroll_offset + max_visible:
            scroll_offset = cursor - max_visible + 1

        _tty_write("\x1b[2J\x1b[H")
        _tty_write(f"{title}\n")
        _tty_write("Use \u2191/\u2193 to move, Space to toggle, Enter to confirm, q/Ctrl-C to abort\n\n")

        visible_end = min(scroll_offset + max_visible, len(options))

        if scroll_offset > 0:
            _tty_write(f"  \u2191 {scroll_offset} more above\n")

        for idx in range(scroll_offset, visible_end):
            cursor_mark = "❯" if idx == cursor else " "
            selected_mark = "[x]" if idx in selected else "[ ]"
            _tty_write(f"{cursor_mark} {selected_mark} {options[idx]}\n")

        remaining = len(options) - visible_end
        if remaining > 0:
            _tty_write(f"  \u2193 {remaining} more below\n")

        sys.stdout.flush()

    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")
        _render()
        while True:
            char = sys.stdin.read(1)
            if char in ("\x03", "q", "Q"):
                raise KeyboardInterrupt
            if char in ("\r", "\n"):
                return sorted(selected)
            if char == " ":
                if cursor in selected:
                    selected.remove(cursor)
                else:
                    selected.add(cursor)
                _render()
                continue
            if char == "\x1b":
                nxt = sys.stdin.read(1)
                if nxt == "[":
                    arrow = sys.stdin.read(1)
                    if arrow == "A":
                        cursor = (cursor - 1) % len(options)
                        _render()
                    elif arrow == "B":
                        cursor = (cursor + 1) % len(options)
                        _render()
            elif char.lower() == "k":
                cursor = (cursor - 1) % len(options)
                _render()
            elif char.lower() == "j":
                cursor = (cursor + 1) % len(options)
                _render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\x1b[?25h")
        _tty_write("\n")
        sys.stdout.flush()


def _select_connected_twins(client: Any, environment_uuid: str, *, skip_confirm: bool) -> list[str]:
    """List twins in environment and ask user which ones are connected."""
    twins = client.twins.list(environment_id=environment_uuid)
    if not twins:
        console.print("[yellow]No twins found in selected environment.[/yellow]")
        return []

    if skip_confirm:
        # Keep non-interactive flow deterministic by selecting the first twin.
        return [str(getattr(twins[0], "uuid", ""))] if getattr(twins[0], "uuid", None) else []

    labels = [
        f"{getattr(twin, 'name', 'Unnamed')} ({str(getattr(twin, 'uuid', ''))[:8]}...)"
        for twin in twins
    ]
    base_title = "Which twins are physically connected to your edge?"
    prompt_title = base_title
    while True:
        idxs = _select_multiple_with_arrows(prompt_title, labels)
        selected_uuids: list[str] = []
        for idx in idxs:
            twin_uuid = str(getattr(twins[idx], "uuid", ""))
            if twin_uuid:
                selected_uuids.append(twin_uuid)
        if selected_uuids:
            return selected_uuids

        prompt_title = (
            "You did not select any twin! Please select at least 1 twin among "
            "this list pressing spacebar.\n\n"
            f"{base_title}"
        )


def _download_twin_json_files(client: Any, twin_uuids: list[str]) -> int:
    """Download twin+asset data and write JSON files consumed by edge drivers.

    Each selected twin gets a ``{twin_uuid}.json`` file in CONFIG_DIR so that
    edge-core and driver containers can read them immediately after install
    without needing an extra API round-trip on first boot.

    Returns the number of files successfully written.
    """
    from datetime import date, datetime

    def _json_default(obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    written = 0
    for twin_uuid in twin_uuids:
        try:
            twin = client.twins.get(twin_uuid)
            twin_data: dict[str, Any] = (
                twin.to_dict()
                if hasattr(twin, "to_dict")
                else {"uuid": twin_uuid, "name": getattr(twin, "name", None)}
            )

            asset_uuid = getattr(twin, "asset_uuid", None) or getattr(twin, "asset_id", "")
            asset_data: dict[str, Any] = {}
            if asset_uuid:
                try:
                    asset = client.assets.get(str(asset_uuid))
                    asset_data = asset.to_dict() if hasattr(asset, "to_dict") else {}
                except Exception:
                    pass

            twin_data["asset"] = asset_data
            twin_json_file = CONFIG_DIR / f"{twin_uuid}.json"
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(twin_json_file, "w") as f:
                json.dump(twin_data, f, indent=2, default=_json_default)
            written += 1
        except Exception as exc:
            console.print(f"[yellow]Failed to download twin JSON for {twin_uuid[:8]}…: {exc}[/yellow]")

    return written


def _attach_edge_fingerprint_to_twins(
    client: Any, twin_uuids: list[str], edge_fingerprint: str
) -> tuple[int, int]:
    """Update selected twins metadata with edge_fingerprint.

    Returns:
        (updated_count, failed_count)
    """
    updated = 0
    failed = 0

    for twin_uuid in twin_uuids:
        try:
            twin = client.twins.get(twin_uuid)
            metadata = getattr(twin, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["edge_fingerprint"] = edge_fingerprint
            client.twins.update(twin_uuid, metadata=metadata)
            updated += 1
        except Exception:
            failed += 1

    return updated, failed


def _detach_edge_fingerprint_from_other_twins(
    client: Any,
    environment_uuid: str,
    keep_twin_uuids: list[str],
    edge_fingerprint: str,
) -> tuple[int, int]:
    """Remove stale edge_fingerprint from twins not selected for this edge.

    Returns:
        (detached_count, failed_count)
    """
    keep_set = {str(twin_uuid) for twin_uuid in keep_twin_uuids if twin_uuid}
    detached = 0
    failed = 0

    try:
        twins = client.twins.list(environment_id=environment_uuid)
    except Exception:
        return detached, 1

    for twin in twins:
        twin_uuid = str(getattr(twin, "uuid", ""))
        if not twin_uuid or twin_uuid in keep_set:
            continue

        metadata = getattr(twin, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            continue

        if metadata.get("edge_fingerprint") != edge_fingerprint:
            continue

        updated_metadata = dict(metadata)
        updated_metadata.pop("edge_fingerprint", None)
        try:
            client.twins.update(twin_uuid, metadata=updated_metadata)
            detached += 1
        except Exception:
            failed += 1

    return detached, failed


def configure_edge_environment(*, skip_confirm: bool = False) -> bool:
    """Select workspace + environment and save /etc/cyberwave/environment.json."""
    creds = load_credentials()
    if not creds or not creds.token:
        console.print("[red]No credentials found.[/red]")
        console.print("[dim]Run 'cyberwave login' first.[/dim]")
        return False

    try:
        creds_base_url = creds.cyberwave_base_url
        client = _get_sdk_client(creds.token, base_url=creds_base_url)

        workspace = None
        if creds.workspace_uuid:
            workspace = _resolve_workspace_from_credentials(client, creds.workspace_uuid)
            if workspace:
                console.print(f"[green]Using workspace from credentials:[/green] {workspace.name}")

        if workspace is None:
            if creds.workspace_uuid:
                console.print(
                    "[yellow]Stored workspace is not available for this account. "
                    "Select a workspace.[/yellow]"
                )
            workspace = _select_workspace(client, skip_confirm=skip_confirm)
        environment = _select_or_create_environment(
            client,
            str(workspace.uuid),
            skip_confirm=skip_confirm,
        )

        env_uuid = str(getattr(environment, "uuid", ""))
        env_name = str(getattr(environment, "name", ""))
        if not env_uuid:
            console.print("[red]Could not determine selected environment UUID.[/red]")
            return False

        selected_twin_uuids = _select_connected_twins(
            client,
            env_uuid,
            skip_confirm=skip_confirm,
        )

        edge_fingerprint = _load_or_generate_edge_fingerprint()
        detached_count, detach_failed_count = _detach_edge_fingerprint_from_other_twins(
            client,
            env_uuid,
            selected_twin_uuids,
            edge_fingerprint,
        )
        if detached_count:
            console.print(
                f"[dim]Removed stale edge fingerprint from twins: {detached_count}[/dim]"
            )
        if detach_failed_count:
            console.print(
                f"[yellow]Failed to clear stale edge fingerprint from "
                f"{detach_failed_count} twin(s).[/yellow]"
            )
        if selected_twin_uuids:
            updated_count, failed_count = _attach_edge_fingerprint_to_twins(
                client,
                selected_twin_uuids,
                edge_fingerprint,
            )
            console.print(f"[dim]Updated twins with edge fingerprint: {updated_count}[/dim]")
            if failed_count:
                console.print(f"[yellow]Failed to update {failed_count} twin(s).[/yellow]")

            written = _download_twin_json_files(client, selected_twin_uuids)
            if written:
                console.print(f"[dim]Pre-cached twin JSON files: {written}[/dim]")

        _save_environment_file(
            workspace_uuid=str(workspace.uuid),
            workspace_name=workspace.name,
            environment_uuid=env_uuid,
            environment_name=env_name or None,
            twin_uuids=selected_twin_uuids,
        )

        console.print(f"[green]Environment saved:[/green] {ENVIRONMENT_FILE}")
        console.print(f"[dim]Environment: {env_name or env_uuid}[/dim]")
        console.print(f"[dim]Connected twins selected: {len(selected_twin_uuids)}[/dim]")
        return True
    except AuthenticationError as exc:
        console.print(f"[red]Authentication error:[/red] {exc}")
        return False
    except Exception as exc:
        console.print(f"[red]Failed to configure environment:[/red] {exc}")
        return False


# ---- apt-get installation ----------------------------------------------------


def _resolve_service_package_name(
    channel: str = "stable", spec: ServiceSpec = EDGE_CORE_SPEC
) -> str:
    """Resolve the Debian package name for the requested channel and service spec."""
    return spec.package_channels.get(channel.lower(), spec.package_name)


def _resolve_edge_core_package_name(channel: str | None) -> str:
    """Resolve the Debian package name for the requested edge-core channel."""
    normalized_channel = (channel or "stable").lower()
    try:
        return EDGE_CORE_PACKAGE_CHANNELS[normalized_channel]
    except KeyError as exc:
        raise ValueError(f"Unsupported edge-core channel: {normalized_channel}") from exc


def _resolve_installed_service_package_name(spec: ServiceSpec = EDGE_CORE_SPEC) -> str:
    """Best-effort detect which Debian package for the given service is currently installed."""
    for package_name in spec.package_channels.values():
        try:
            result = subprocess.run(
                ["dpkg-query", "-W", "-f=${db:Status-Status}", package_name],
                capture_output=True,
                text=True,
                check=False,
                env=clean_subprocess_env(),
            )
        except FileNotFoundError:
            break

        if result.returncode == 0 and result.stdout.strip() == "installed":
            return package_name

    return spec.package_name


def _resolve_installed_edge_core_package_name() -> str:
    """Best-effort detect which edge-core Debian package is currently installed."""
    return _resolve_installed_service_package_name(EDGE_CORE_SPEC)


def _apt_get_install(
    spec: ServiceSpec = EDGE_CORE_SPEC,
    *,
    package_name: str | None = None,
    package_version: str | None = None,
) -> bool:
    """Install a service package via apt-get.

    Adds the Buildkite package registry GPG key and source if not already
    configured, then installs (or upgrades) the requested version of the package.

    Returns True on success.
    """
    resolved_name = package_name or spec.package_name
    sources_list = spec.sources_list_path

    # Install the GPG signing key if missing
    if not spec.keyring_path.exists():
        console.print("[cyan]Installing Cyberwave package signing key...[/cyan]")
        try:
            spec.keyring_path.parent.mkdir(parents=True, exist_ok=True)

            child_env = clean_subprocess_env()
            ld_library_path = child_env.get("LD_LIBRARY_PATH", "(unset)")
            console.print(f"[dim]LD_LIBRARY_PATH for child: {ld_library_path}[/dim]")

            # Download the armored GPG key
            curl = subprocess.run(
                ["curl", "-fsSL", spec.gpg_key_url],
                capture_output=True,
                check=True,
                env=child_env,
            )
            if not curl.stdout:
                console.print("[red]Downloaded GPG key is empty.[/red]")
                console.print(f"[dim]URL: {spec.gpg_key_url}[/dim]")
                return False

            # Dearmor into the keyring file
            gpg = subprocess.run(
                ["gpg", "--batch", "--yes", "--dearmor", "-o", str(spec.keyring_path)],
                input=curl.stdout,
                capture_output=True,
                env=child_env,
            )
            if gpg.returncode != 0:
                stderr_msg = gpg.stderr.decode(errors="replace").strip()
                console.print(f"[red]gpg --dearmor failed (exit {gpg.returncode}).[/red]")
                if stderr_msg:
                    console.print(f"[dim]{stderr_msg}[/dim]")
                return False

        except subprocess.CalledProcessError as exc:
            stderr_msg = ""
            if exc.stderr:
                stderr_msg = exc.stderr.decode(errors="replace").strip()
            console.print(f"[red]Failed to download GPG key (exit {exc.returncode}).[/red]")
            if stderr_msg:
                console.print(f"[dim]{stderr_msg}[/dim]")
            console.print(f"[dim]URL: {spec.gpg_key_url}[/dim]")
            return False
        except FileNotFoundError as exc:
            console.print(f"[red]Required command not found: {exc.filename}[/red]")
            console.print(
                "[dim]Ensure curl and gpg are installed: sudo apt-get install curl gnupg[/dim]"
            )
            return False
        except PermissionError:
            console.print(
                "[red]Permission denied installing GPG key.[/red]\n"
                f"[dim]Re-run with sudo: {spec.sudo_command_hint}[/dim]"
            )
            return False

    # Add the repository if missing
    if not sources_list.exists():
        console.print("[cyan]Adding Cyberwave package repository...[/cyan]")
        signed_by = f"signed-by={spec.keyring_path}"
        source_lines = (
            f"deb [{signed_by}] {spec.deb_repo_url} any main\n"
            f"deb-src [{signed_by}] {spec.deb_repo_url} any main\n"
        )
        try:
            sources_list.write_text(source_lines)
        except PermissionError:
            console.print(
                "[red]Permission denied writing apt sources.[/red]\n"
                f"[dim]Re-run with sudo: {spec.sudo_command_hint}[/dim]"
            )
            return False

    # Update and install the latest version
    install_target = f"{resolved_name}={package_version}" if package_version else resolved_name
    console.print(f"[cyan]Installing {install_target} via apt-get...[/cyan]")
    try:
        # Retry apt-get update to handle transient CDN mirror sync failures.
        # After all attempts, warn and continue — apt will use its cached index
        # for any failing source and the install may still succeed.
        apt_update_retries = 3
        apt_update_retry_delay = 8  # seconds
        for attempt in range(1, apt_update_retries + 1):
            try:
                _run(["apt-get", "update", "-qq"])
                break
            except subprocess.CalledProcessError:
                if attempt < apt_update_retries:
                    console.print(
                        f"[yellow]apt-get update failed (attempt {attempt}/{apt_update_retries}),"
                        f" retrying in {apt_update_retry_delay}s"
                        " (likely a transient mirror sync — will resolve shortly)...[/yellow]"
                    )
                    time.sleep(apt_update_retry_delay)
                else:
                    console.print(
                        "[yellow]apt-get update failed after all retries — "
                        "one or more sources may be temporarily unavailable. "
                        "Proceeding with cached package index...[/yellow]"
                    )
        _run(["apt-get", "install", "-y", "-qq", install_target])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]apt-get failed (exit {exc.returncode}).[/red]")
        return False

    if spec.binary_path.exists():
        console.print(f"[green]Installed:[/green] {spec.binary_path}")
        return True

    console.print("[red]Binary not found after installation.[/red]")
    return False


def _pip_install(
    spec: ServiceSpec = EDGE_CORE_SPEC,
    *,
    package_version: str | None = None,
    channel: str = "stable",
) -> bool:
    """Fallback: install a service package via pip.

    Used on non-Debian systems (macOS, other Linux flavors).
    Returns True on success.
    """
    if channel != "stable":
        console.print(
            f"[red]Non-stable {spec.package_name} channels are only supported"
            " via apt-get on Debian/Ubuntu.[/red]"
        )
        return False

    pip_target = f"{spec.package_name}=={package_version}" if package_version else spec.package_name
    console.print(f"[cyan]Installing {pip_target} via pip...[/cyan]")
    try:
        _run([sys.executable, "-m", "pip", "install", pip_target])
        return True
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]pip install failed (exit {exc.returncode}).[/red]")
        return False


def install_service_package(
    spec: ServiceSpec = EDGE_CORE_SPEC,
    *,
    channel: str = "stable",
    version: str | None = None,
) -> bool:
    """Install the package described by *spec*.

    Prefers apt-get on Debian/Ubuntu, falls back to pip otherwise.
    Returns True on success.
    """
    if _is_linux() and shutil.which("apt-get"):
        package_name = _resolve_service_package_name(channel, spec)
        return _apt_get_install(spec, package_name=package_name, package_version=version)
    return _pip_install(spec, package_version=version, channel=channel)


def install_edge_core(*, channel: str = "stable", version: str | None = None) -> bool:
    """Install the cyberwave-edge-core package.

    Prefers apt-get on Debian/Ubuntu, falls back to pip otherwise.
    Returns True on success.
    """
    return install_service_package(EDGE_CORE_SPEC, channel=channel, version=version)


# ---- docker installation -----------------------------------------------------


def _ensure_docker_installed() -> bool:
    """Ensure Docker is installed and running."""
    if not shutil.which("docker"):
        console.print("[red]Docker not found.[/red]")
        return False

    try:
        proc = subprocess.run(
            ["docker", "info"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=clean_subprocess_env(),
        )
    except FileNotFoundError:
        console.print("[red]Docker not found in PATH.[/red]")
        return False

    if proc.returncode != 0:
        stderr_msg = proc.stderr.decode(errors="replace").strip() if proc.stderr else ""
        console.print("[red]Docker is installed, but the daemon is not ready/running.[/red]")
        if stderr_msg:
            console.print(f"[dim]{stderr_msg}[/dim]")
        return False

    return True


def _install_docker() -> bool:
    """Install Docker if not present in the edge device."""
    if shutil.which("docker"):
        console.print("[green]Docker is already installed.[/green]")
        return _ensure_docker_installed()

    script_path = _get_docker_installer_script_path()
    if not script_path.exists():
        console.print(f"[red]Docker installer script not found: {script_path}[/red]")
        return False

    if os.geteuid() != 0:
        console.print(
            "[red]Docker installation requires root permissions.[/red]\n"
            "[dim]Re-run with sudo: sudo cyberwave edge install[/dim]"
        )
        return False

    console.print("[cyan]Installing Docker...[/cyan]")
    try:
        _run(["bash", str(script_path)])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Docker installation failed (exit {exc.returncode}).[/red]")
        return False
    except FileNotFoundError as exc:
        console.print(f"[red]Required command not found: {exc.filename}[/red]")
        return False

    if not _ensure_docker_installed():
        console.print("[red]Docker installation did not complete successfully.[/red]")
        return False

    console.print("[green]Docker is installed and ready.[/green]")
    return True


def _get_docker_installer_script_path() -> Path:
    """Resolve install_docker.sh in source and bundled runtimes."""
    candidates = [Path(__file__).with_name("install_docker.sh")]

    mei_dir = getattr(sys, "_MEIPASS", None)
    if mei_dir:
        candidates.append(Path(mei_dir) / "cyberwave_cli" / "install_docker.sh")

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


# ---- systemd service ---------------------------------------------------------


def _service_override_path(spec: ServiceSpec) -> Path:
    """Return the path to the drop-in override file for ``spec``."""
    return spec.unit_path.parent / f"{spec.unit_name}.d" / "override.conf"


def _resolve_service_binary(spec: ServiceSpec) -> str:
    """Resolve the absolute path used to launch the service.

    Resolution order:
    1. spec.binary_path if it exists (Linux apt install)
    2. Venv-local bin used by install-local-cli-mac.sh
    3. PATH lookup via shutil.which
    4. spec.binary_path as a fallback (may not exist)
    """
    if spec.binary_path.exists():
        return str(spec.binary_path)
    venv_bin = Path.home() / ".cyberwave-cli" / "venv-local" / "bin" / spec.package_name
    if venv_bin.exists():
        return str(venv_bin)
    return shutil.which(spec.package_name) or str(spec.binary_path)


def _launchagent_label(spec: ServiceSpec) -> str:
    """Return the launchd label for a macOS service."""
    if spec.package_name == CLOUD_NODE_SPEC.package_name:
        return "com.cyberwave.cloud-node"
    package_suffix = spec.package_name.removeprefix("cyberwave-").replace("-", ".")
    return f"com.cyberwave.{package_suffix}"


def _launchagent_plist_path(spec: ServiceSpec) -> Path:
    """Return the LaunchAgent plist path for the current user."""
    return Path.home() / "Library" / "LaunchAgents" / f"{_launchagent_label(spec)}.plist"


def create_launchagent_service(
    spec: ServiceSpec = CLOUD_NODE_SPEC,
    *,
    config_path: str | None = None,
) -> bool:
    """Write a LaunchAgent plist for the service on macOS."""
    program_arguments = [_resolve_service_binary(spec), "start"]
    if config_path:
        # launchd launches from /, so config paths must be absolute.
        abs_config = str(Path(config_path).resolve())
        program_arguments.extend(["--config", abs_config])

    log_dir = Path.home() / "Library" / "Logs" / "Cyberwave"
    log_dir.mkdir(parents=True, exist_ok=True)
    label = _launchagent_label(spec)
    plist_data = {
        "Label": label,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_dir / f"{label}.log"),
        "StandardErrorPath": str(log_dir / f"{label}.log"),
    }

    plist_path = _launchagent_plist_path(spec)
    try:
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_bytes(plistlib.dumps(plist_data))
    except PermissionError:
        console.print(
            "[red]Permission denied writing LaunchAgent plist.[/red]\n"
            "[dim]Run 'cyberwave compute install' without sudo on macOS.[/dim]"
        )
        return False

    console.print(f"[green]Created:[/green] {plist_path}")
    return True


def load_launchagent_service(spec: ServiceSpec = CLOUD_NODE_SPEC) -> bool:
    """Load or reload the LaunchAgent for the current macOS user session."""
    plist_path = _launchagent_plist_path(spec)
    if not plist_path.exists():
        console.print("[red]LaunchAgent plist not found — run install first.[/red]")
        return False

    label = _launchagent_label(spec)
    domain = f"gui/{os.getuid()}"
    bootout_target = f"{domain}/{label}"

    try:
        result = subprocess.run(
            ["launchctl", "bootout", bootout_target],
            env=clean_subprocess_env(),
            capture_output=True,
        )
        if result.returncode == 0:
            # Give launchd a moment to fully unload the previous instance before
            # bootstrapping the new plist to avoid transient I/O errors (exit 5).
            time.sleep(1)
        _run(["launchctl", "bootstrap", domain, str(plist_path)])
    except FileNotFoundError:
        console.print("[red]launchctl not found on this system.[/red]")
        return False
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]launchctl failed (exit {exc.returncode}).[/red]")
        console.print(
            "[dim]LaunchAgent loading requires an active macOS GUI login session.[/dim]"
        )
        return False

    console.print(f"[green]LaunchAgent loaded:[/green] {label}")
    return True


def write_service_override(
    spec: ServiceSpec,
    *,
    config_path: str | None = None,
) -> bool:
    """Write a systemd drop-in override that sets --config on ExecStart.

    Creates ``<unit>.d/override.conf`` with a blank ``ExecStart=`` followed by
    the full command so systemd replaces (not appends) the base ExecStart.
    Calls ``daemon-reload`` automatically so the change takes effect.

    Returns True on success.  If no config_path is provided, returns True
    immediately without touching anything.
    """
    if not config_path:
        return True

    extra: list[str] = ["--config", config_path]
    binary = _resolve_service_binary(spec)
    exec_start = shlex.join([binary, "start", *extra])
    contents = textwrap.dedent(f"""\
        [Service]
        ExecStart=
        ExecStart={exec_start}
    """)

    override_file = _service_override_path(spec)
    try:
        override_file.parent.mkdir(parents=True, exist_ok=True)
        override_file.write_text(contents)
    except PermissionError:
        console.print(
            f"[red]Permission denied writing service override.[/red]\n"
            f"[dim]Re-run with sudo: {spec.sudo_command_hint}[/dim]"
        )
        return False

    console.print(f"[green]Created:[/green] {override_file}")

    if _has_systemd():
        try:
            _run(["systemctl", "daemon-reload"])
        except subprocess.CalledProcessError:
            pass

    return True


def clear_service_override(spec: ServiceSpec) -> None:
    """Remove the drop-in override file if it exists."""
    override_file = _service_override_path(spec)
    if not override_file.exists():
        return
    try:
        override_file.unlink()
        override_dir = override_file.parent
        if override_dir.exists() and not any(override_dir.iterdir()):
            override_dir.rmdir()
        console.print(f"[dim]Removed override: {override_file}[/dim]")
        if _has_systemd():
            try:
                _run(["systemctl", "daemon-reload"])
            except subprocess.CalledProcessError:
                pass
    except PermissionError:
        console.print(f"[yellow]Could not remove override file: {override_file}[/yellow]")


def create_systemd_service(spec: ServiceSpec = EDGE_CORE_SPEC) -> bool:
    """Write the systemd unit file described by ``spec``.

    Returns True on success.
    """
    if not _has_systemd():
        console.print("[yellow]systemd not detected — skipping service creation.[/yellow]")
        return False

    binary = _resolve_service_binary(spec)
    unit_contents = spec.unit_template.format(binary_path=binary)

    try:
        spec.unit_path.write_text(unit_contents)
    except PermissionError:
        console.print(
            f"[red]Permission denied writing systemd unit.[/red]\n"
            f"[dim]Re-run with sudo: {spec.sudo_command_hint}[/dim]"
        )
        return False

    console.print(f"[green]Created:[/green] {spec.unit_path}")
    return True


def enable_and_start_service(spec: ServiceSpec = EDGE_CORE_SPEC) -> bool:
    """Enable the service described by ``spec`` to start on boot, then start it now.

    Returns True on success.
    """
    if not spec.unit_path.exists():
        console.print("[red]Service unit not found — run install first.[/red]")
        return False

    try:
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", spec.unit_name])
        _run(["systemctl", "start", spec.unit_name])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]systemctl command failed (exit {exc.returncode}).[/red]")
        return False

    console.print(f"[green]Service enabled and started:[/green] {spec.unit_name}")
    return True


def restart_service(spec: ServiceSpec = EDGE_CORE_SPEC) -> bool:
    """Restart the systemd service described by ``spec``.

    Returns True on success.
    """
    if not _has_systemd():
        console.print("[yellow]systemd not detected — cannot restart via systemd.[/yellow]")
        return False

    if not spec.unit_path.exists():
        console.print(f"[red]Service unit not found — run '{spec.sudo_command_hint}' first.[/red]")
        return False

    try:
        _run(["systemctl", "restart", spec.unit_name])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]systemctl restart failed (exit {exc.returncode}).[/red]")
        return False

    console.print(f"[green]Service restarted:[/green] {spec.unit_name}")
    return True


def stop_service(spec: ServiceSpec = EDGE_CORE_SPEC) -> bool:
    """Stop the systemd service described by ``spec``.

    Returns True on success.
    """
    if not _has_systemd():
        console.print("[yellow]systemd not detected — cannot stop via systemd.[/yellow]")
        return False

    if not spec.unit_path.exists():
        console.print(f"[red]Service unit not found — run '{spec.sudo_command_hint}' first.[/red]")
        return False

    try:
        _run(["systemctl", "stop", spec.unit_name])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]systemctl stop failed (exit {exc.returncode}).[/red]")
        return False

    console.print(f"[green]Service stopped:[/green] {spec.unit_name}")
    return True


def start_service(spec: ServiceSpec = EDGE_CORE_SPEC) -> bool:
    """Start the service without re-enabling it (user-initiated start).

    Returns True on success.
    """
    if not _has_systemd():
        return False
    if not spec.unit_path.exists():
        console.print(f"[red]Service unit not found — run '{spec.sudo_command_hint}' first.[/red]")
        return False
    result = _run(["systemctl", "start", spec.unit_name], check=False)
    if result.returncode == 0:
        console.print(f"[green]✓ Started {spec.unit_name}[/green]")
        return True
    console.print(
        f"[yellow]systemctl start failed (exit {result.returncode}). "
        f"Check status with: systemctl status {spec.unit_name}[/yellow]"
    )
    return False


# ---- orchestrator ------------------------------------------------------------


def setup_service(
    spec: ServiceSpec,
    *,
    skip_confirm: bool = False,
    channel: str = "stable",
    version: str | None = None,
    config_path: str | None = None,
    post_install_hook: Any = None,
) -> bool:
    """Generic install orchestrator: install package, optionally set up Docker + systemd.

    Returns True if everything succeeded.
    """
    linux_service_setup = _is_linux()
    macos_launchagent_supported = _is_macos() and spec.package_name == CLOUD_NODE_SPEC.package_name

    if linux_service_setup and os.geteuid() != 0:
        console.print(
            "[red]Root privileges required.[/red]\n"
            f"[dim]Re-run with sudo: {spec.sudo_command_hint}[/dim]"
        )
        return False

    if macos_launchagent_supported and os.geteuid() == 0:
        console.print(
            "[red]macOS LaunchAgent installs must be run without sudo.[/red]\n"
            "[dim]Re-run as your regular user: cyberwave compute install[/dim]"
        )
        return False

    if not linux_service_setup and not macos_launchagent_supported:
        console.print(
            f"[yellow]{spec.package_name} service setup is only supported on Linux. "
            "You will need to start it manually upon restart.[/yellow]"
        )
        if channel != "stable":
            console.print(
                f"[red]Non-stable {spec.package_name} channels are only supported"
                " via apt-get on Debian/Ubuntu.[/red]"
            )
            return False

    if not _ensure_credentials(skip_confirm=skip_confirm):
        return False

    if not skip_confirm:
        if linux_service_setup:
            selected_pkg = _resolve_service_package_name(channel, spec)
            selected_target = f"{selected_pkg}={version}" if version else selected_pkg
            console.print(
                f"\nThis will:\n"
                f"  1. Install [bold]{selected_target}[/bold] via apt-get\n"
                f"  2. Create a systemd service ([bold]{spec.unit_name}[/bold])\n"
                f"  3. Enable it to start on boot\n"
            )
        elif macos_launchagent_supported:
            pip_target = f"{spec.package_name}=={version}" if version else spec.package_name
            console.print(
                f"\nThis will:\n"
                f"  1. Install [bold]{pip_target}[/bold] via pip\n"
                "  2. Configure service credentials\n"
                "  3. Create and load a LaunchAgent for your macOS user session\n"
            )
        else:
            pip_target = f"{spec.package_name}=={version}" if version else spec.package_name
            console.print(
                f"\nThis will:\n"
                f"  1. Install [bold]{pip_target}[/bold] via pip\n"
                f"  2. Configure service credentials\n"
                f"  3. Skip service setup (manual startup required)\n"
            )
        if not Confirm.ask("Continue?", default=True):
            console.print("[dim]Aborted.[/dim]")
            return False

    if not install_service_package(spec, channel=channel, version=version):
        return False

    if linux_service_setup and spec.requires_docker:
        if not _install_docker():
            return False

    if post_install_hook is not None:
        if not post_install_hook():
            return False

    if linux_service_setup:
        if not create_systemd_service(spec):
            return False
        if not enable_and_start_service(spec):
            return False
        console.print(f"\n[green]{spec.package_name} is installed and running.[/green]")
        console.print(f"[dim]Check status: systemctl status {spec.unit_name}[/dim]")
    elif macos_launchagent_supported:
        if not create_launchagent_service(spec, config_path=config_path):
            return False
        if not load_launchagent_service(spec):
            return False
        console.print(f"\n[green]{spec.package_name} is installed and running.[/green]")
        console.print(f"[dim]LaunchAgent: {_launchagent_label(spec)}[/dim]")
        console.print(f"[dim]Plist: {_launchagent_plist_path(spec)}[/dim]")
    else:
        console.print(f"\n[green]{spec.package_name} is installed.[/green]")
        console.print(f"[dim]Start manually: {spec.package_name}[/dim]")

    return True


def setup_edge_core(
    *,
    skip_confirm: bool = False,
    edge_core_channel: str = "stable",
    edge_core_version: str | None = None,
) -> bool:
    """Full setup for edge core: install the package, create the service, enable on boot.

    Returns True if everything succeeded.
    """
    return setup_service(
        EDGE_CORE_SPEC,
        skip_confirm=skip_confirm,
        channel=edge_core_channel,
        version=edge_core_version,
        post_install_hook=lambda: configure_edge_environment(skip_confirm=skip_confirm),
    )
