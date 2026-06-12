"""Install and manage the cyberwave-edge-core systemd service.

This module provides the logic for:
  1. Installing the cyberwave-edge-core .deb package via apt-get
  2. Creating a systemd service unit so it starts on boot
  3. Enabling and starting the service
"""

from __future__ import annotations

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

from rich.console import Console
from rich.prompt import Confirm, Prompt

from .config import (
    CONFIG_DIR,
    LEGACY_SYSTEM_CONFIG_DIR,
    chown_to_sudo_user,
    clean_subprocess_env,
    ensure_edge_core_importable,
    get_api_url,
)
from .credentials import (
    Credentials,
    collect_runtime_env_overrides,
    load_credentials,
    save_credentials,
)

from .macos import (
    bootstrap_launchd_service,
    is_macos,
    legacy_labels_for_package,
    setup_audio_playback_server,
    setup_audio_stream_server,
    setup_camera_stream_server,
    setup_usbip_server,
    wait_for_launchd_unload,
)
from .macos import init_console as _init_macos_console

console = Console()
_init_macos_console(console)

# ---- constants ---------------------------------------------------------------

PACKAGE_NAME = "cyberwave-edge-core"
BUILDKITE_ORG_SLUG = "cyberwave"
INTERNAL_DEB_REGISTRY_SLUG = "cyberwave-internal-deb"
INTERNAL_DEB_READ_TOKEN_ENV = "CYBERWAVE_INTERNAL_DEB_READ_TOKEN"
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
    After=network-online.target docker.service
    Wants=network-online.target docker.service

    [Service]
    Type=notify
    NotifyAccess=all
    ExecStart={binary_path}
    Restart=always
    RestartSec=5
    WatchdogSec=60
    TimeoutStartSec=900
    Environment=CYBERWAVE_EDGE_CONFIG_DIR={config_dir}
    OOMScoreAdjust=-800
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
    public_deb_registry_slug: str
    deb_repo_url: str
    gpg_key_url: str
    keyring_path: Path
    sources_list_path: Path
    process_match: str
    install_command_hint: str
    sudo_command_hint: str
    unit_template: str
    requires_docker: bool
    supports_macos_launchagent: bool
    launch_command: tuple[str, ...]


EDGE_CORE_SPEC = ServiceSpec(
    package_name=PACKAGE_NAME,
    binary_path=BINARY_PATH,
    unit_name=SYSTEMD_UNIT_NAME,
    unit_path=SYSTEMD_UNIT_PATH,
    package_channels=EDGE_CORE_PACKAGE_CHANNELS,
    public_deb_registry_slug="cyberwave-edge-core",
    deb_repo_url=BUILDKITE_DEB_REPO_URL,
    gpg_key_url=BUILDKITE_GPG_KEY_URL,
    keyring_path=BUILDKITE_KEYRING_PATH,
    sources_list_path=Path("/etc/apt/sources.list.d/buildkite-cyberwave-cyberwave-edge-core.list"),
    process_match="cyberwave-edge-core",
    install_command_hint="cyberwave edge install",
    sudo_command_hint="sudo cyberwave edge install",
    unit_template=SYSTEMD_UNIT_TEMPLATE,
    requires_docker=True,
    supports_macos_launchagent=True,
    launch_command=(),
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
    public_deb_registry_slug="cyberwave-cloud-node",
    deb_repo_url="https://packages.buildkite.com/cyberwave/cyberwave-cloud-node/any/",
    gpg_key_url="https://packages.buildkite.com/cyberwave/cyberwave-cloud-node/gpgkey",
    keyring_path=Path("/etc/apt/keyrings/cyberwave_cyberwave-cloud-node-archive-keyring.gpg"),
    sources_list_path=Path("/etc/apt/sources.list.d/buildkite-cyberwave-cyberwave-cloud-node.list"),
    process_match="cyberwave-cloud-node start",
    install_command_hint="cyberwave compute install",
    sudo_command_hint="sudo cyberwave compute install",
    unit_template=_CLOUD_NODE_UNIT_TEMPLATE,
    requires_docker=False,
    supports_macos_launchagent=True,
    launch_command=("start",),
)


# ---- helpers -----------------------------------------------------------------


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _has_systemd() -> bool:
    return Path("/run/systemd/system").is_dir()


def _copy_and_harden(src: Path, dst: Path) -> bool:
    """Copy *src* to *dst*, lock permissions to owner-only, and fix ownership.

    Returns True on success, False on OSError.
    """
    try:
        shutil.copy2(src, dst)
        if os.name != "nt":
            os.chmod(dst, 0o600)
        chown_to_sudo_user(dst)
        return True
    except OSError:
        return False


def _migrate_legacy_config_dir() -> None:
    """Copy config files from ``/etc/cyberwave`` to the user's ``~/.cyberwave``.

    Old CLI versions stored config under ``/etc/cyberwave`` on Linux.  When
    upgrading, we copy files across so the user doesn't have to re-login or
    reconfigure.  Existing files in the target directory are never overwritten.
    """
    legacy = LEGACY_SYSTEM_CONFIG_DIR
    target = CONFIG_DIR

    if legacy == target:
        return

    try:
        if not legacy.is_dir():
            return
    except OSError:
        return

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    migrated = 0
    try:
        for name in ("credentials.json", "environment.json", "fingerprint.json"):
            src = legacy / name
            dst = target / name
            if src.is_file() and not dst.exists() and _copy_and_harden(src, dst):
                migrated += 1

        for src in legacy.glob("*.json"):
            if src.name in ("credentials.json", "environment.json", "fingerprint.json"):
                continue
            dst = target / src.name
            if not dst.exists() and _copy_and_harden(src, dst):
                migrated += 1
    except OSError:
        pass

    chown_to_sudo_user(target)

    if migrated:
        console.print(f"[cyan]Migrated {migrated} config file(s) from {legacy} to {target}[/cyan]")


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


def require_root(hint: str) -> None:
    """Exit with a clear message if the current process is not running as root.

    Call this at the top of any command that needs root privileges so
    the user gets a single, upfront prompt to re-run with ``sudo``.
    """
    if os.geteuid() != 0:
        console.print(
            f"[red]This command requires root privileges.[/red]\n"
            f"[dim]Re-run with sudo: {hint}[/dim]"
        )
        raise SystemExit(1)


# Re-exported from interactive_select for backward compat.
from .interactive_select import _select_with_arrows, _select_multiple_with_arrows  # noqa: E402


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

    if os.name != "nt":
        os.chmod(ENVIRONMENT_FILE, 0o600)
        dir_fd = os.open(CONFIG_DIR, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    chown_to_sudo_user(CONFIG_DIR, ENVIRONMENT_FILE)


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

    from cyberwave.fingerprint import generate_fingerprint

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
    from .auth import APIToken, AuthClient, AuthenticationError

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
                        cyberwave_worker_log_level=runtime_overrides.get(
                            "CYBERWAVE_WORKER_LOG_LEVEL"
                        ),
                        cyberwave_base_url=runtime_overrides.get("CYBERWAVE_BASE_URL"),
                        cyberwave_mqtt_host=runtime_overrides.get("CYBERWAVE_MQTT_HOST"),
                        cyberwave_mqtt_port=runtime_overrides.get("CYBERWAVE_MQTT_PORT"),
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
                    cyberwave_mqtt_port=runtime_overrides.get("CYBERWAVE_MQTT_PORT"),
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

            with console.status(
                f"[dim]Creating API token for workspace '{workspace.name}'...[/dim]"
            ):
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
                    cyberwave_mqtt_port=runtime_overrides.get("CYBERWAVE_MQTT_PORT"),
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
        getattr(environment, "workspace_uuid", "") or getattr(environment, "workspace_id", "") or ""
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


def _twin_has_asset(twin: Any) -> bool:
    """Return ``True`` when the twin has an asset attached on the backend."""
    return bool(getattr(twin, "asset_uuid", None) or getattr(twin, "asset_id", None))


def _twin_has_docker_driver(twin: Any) -> bool:
    """Return ``True`` when the twin's metadata declares at least one Docker-based driver.

    A driver entry is considered Docker-compatible when it contains either
    ``docker_image`` (single container) or ``services`` (multi-container).
    Twins whose ``metadata.drivers`` dict is missing or contains only
    non-Docker entries (e.g. ``android``) are not usable on a Docker-based
    edge device.
    """
    metadata = getattr(twin, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    drivers = metadata.get("drivers")
    if not isinstance(drivers, dict):
        return False
    return any(
        isinstance(drv, dict) and ("docker_image" in drv or "services" in drv)
        for drv in drivers.values()
    )


def _select_connected_twins(client: Any, environment_uuid: str, *, skip_confirm: bool) -> list[str]:
    """List twins in environment and ask user which ones are connected."""
    all_twins = client.twins.list(environment_id=environment_uuid)
    if not all_twins:
        console.print("[yellow]No twins found in selected environment.[/yellow]")
        return []

    twins = [t for t in all_twins if _twin_has_docker_driver(t)]
    if not twins:
        console.print(
            "[yellow]No edge-compatible twins found in selected environment. "
            "Twins must have Docker-based drivers in their metadata.[/yellow]"
        )
        return []

    if skip_confirm:
        # Keep non-interactive flow deterministic by selecting the first twin.
        return [str(getattr(twins[0], "uuid", ""))] if getattr(twins[0], "uuid", None) else []

    labels: list[str] = []
    for twin in twins:
        name = getattr(twin, "name", "Unnamed")
        uuid_short = str(getattr(twin, "uuid", ""))[:8]
        if _twin_has_asset(twin):
            labels.append(f"{name} ({uuid_short}...)")
        else:
            # Broken twins are visibly flagged so the user doesn't silently pick
            # a twin that the edge-core will later fail to spawn a driver for.
            labels.append(f"{name} ({uuid_short}...)  \u26a0  NO ASSET ATTACHED — fix on dashboard")

    base_title = "Which twins are physically connected to your edge?"
    prompt_title = base_title
    while True:
        idxs = _select_multiple_with_arrows(prompt_title, labels)
        selected_uuids: list[str] = []
        selected_without_asset: list[tuple[str, str]] = []
        for idx in idxs:
            twin = twins[idx]
            twin_uuid = str(getattr(twin, "uuid", ""))
            if not twin_uuid:
                continue
            selected_uuids.append(twin_uuid)
            if not _twin_has_asset(twin):
                selected_without_asset.append((getattr(twin, "name", "Unnamed"), twin_uuid))

        if selected_uuids:
            if selected_without_asset:
                console.print()
                console.print(
                    "[bold red]\u2717 The following selected twin(s) have no "
                    "asset attached on the backend:[/bold red]"
                )
                for name, twin_uuid in selected_without_asset:
                    console.print(f"  [red]\u2022 {name} ({twin_uuid[:8]}...)[/red]")
                console.print(
                    "[red]The edge-core cannot spawn a driver for these twins. "
                    "Open each twin on the dashboard, attach an asset, then re-run "
                    "`cyberwave edge install`.[/red]"
                )
                console.print()
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
                except Exception as asset_exc:
                    console.print(
                        f"[yellow]Failed to fetch asset {str(asset_uuid)[:8]}… "
                        f"for twin {twin_uuid[:8]}…: {asset_exc}[/yellow]"
                    )
            else:
                # No asset on the twin means edge-core will silently skip
                # spawning a driver for it. Surface the failure loudly now so
                # the user can fix it before hitting the cryptic "No twins
                # with driver images matched this edge" message later.
                twin_name = getattr(twin, "name", "?")
                console.print(
                    f"[bold red]\u2717 Twin '{twin_name}' ({twin_uuid[:8]}...) "
                    f"has no asset attached on the backend.[/bold red]"
                )
                console.print(
                    "  [red]The edge-core cannot spawn a driver for this twin. "
                    "Open it on the dashboard, attach an asset, then re-run "
                    "`cyberwave edge install`.[/red]"
                )

            twin_data["asset"] = asset_data
            twin_json_file = CONFIG_DIR / f"{twin_uuid}.json"
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(twin_json_file, "w") as f:
                json.dump(twin_data, f, indent=2, default=_json_default)
            chown_to_sudo_user(CONFIG_DIR, twin_json_file)
            written += 1
        except Exception as exc:
            console.print(
                f"[yellow]Failed to download twin JSON for {twin_uuid[:8]}…: {exc}[/yellow]"
            )

    return written


def _attach_edge_fingerprint_to_twins(
    client: Any, twin_uuids: list[str], edge_fingerprint: str
) -> tuple[int, int]:
    """Update selected twins metadata with edge_fingerprint.

    The backend's ``twins.update`` merges ``metadata`` on top of the stored
    copy, so we only need to send the single key we want to write.

    Returns:
        (updated_count, failed_count)
    """
    updated = 0
    failed = 0
    for twin_uuid in twin_uuids:
        try:
            client.twins.update(twin_uuid, metadata={"edge_fingerprint": edge_fingerprint})
            updated += 1
        except Exception as exc:
            console.print(
                f"[yellow]Failed to attach fingerprint to twin {twin_uuid[:8]}…: {exc}[/yellow]"
            )
            failed += 1
    return updated, failed


def _detach_edge_fingerprint_from_other_twins(
    client: Any,
    environment_uuid: str,
    keep_twin_uuids: list[str],
    edge_fingerprint: str,
) -> tuple[int, int]:
    """Remove stale edge_fingerprint from twins not selected for this edge.

    The backend treats explicit ``None`` values in ``metadata`` as deletions
    while merging everything else, so we must send ``{"edge_fingerprint":
    None}`` to actually clear the key — simply omitting it would keep the
    stored value intact (which is exactly the bug that caused stale
    fingerprints from previous installs to pin unwanted drivers to this
    edge).

    Returns:
        (detached_count, failed_count)
    """
    keep_set = {str(twin_uuid) for twin_uuid in keep_twin_uuids if twin_uuid}
    detached = 0
    failed = 0

    try:
        twins = client.twins.list(environment_id=environment_uuid)
    except Exception as exc:
        console.print(f"[yellow]Could not list twins to clear stale fingerprints: {exc}[/yellow]")
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

        try:
            client.twins.update(twin_uuid, metadata={"edge_fingerprint": None})
            detached += 1
        except Exception as exc:
            console.print(
                f"[yellow]Failed to detach fingerprint from twin {twin_uuid[:8]}…: {exc}[/yellow]"
            )
            failed += 1

    return detached, failed


_NON_TWIN_JSON_FILES = frozenset(
    {
        "credentials.json",
        "environment.json",
        "cameras.json",
        "camera_streams.json",
        "fingerprint.json",
        "edge.json",
    }
)


def _detect_existing_edge_configuration() -> tuple[str | None, list[Path]]:
    """Check CONFIG_DIR for an existing edge environment configuration.

    Returns:
        (environment_name_or_uuid, list_of_twin_json_paths)
        *environment_name_or_uuid* is ``None`` when no prior configuration
        is detected.
    """
    twin_json_files = [
        p for p in sorted(CONFIG_DIR.glob("*.json")) if p.name not in _NON_TWIN_JSON_FILES
    ]
    if not twin_json_files:
        return None, []

    env_label: str | None = None
    if ENVIRONMENT_FILE.exists():
        try:
            data = json.loads(ENVIRONMENT_FILE.read_text(encoding="utf-8"))
            env_label = data.get("name") or data.get("uuid")
        except Exception:
            pass

    return env_label, twin_json_files


def _cleanup_existing_edge_configuration(
    *,
    twin_json_files: list[Path],
    creds: Credentials | None = None,
) -> None:
    """Remove local twin/environment files and backend edge registrations.

    This mirrors the cleanup performed by ``cyberwave edge uninstall`` but
    intentionally preserves credentials, fingerprint, the installed
    package, and the system service so that the subsequent install flow can
    continue seamlessly.
    """
    for path in twin_json_files:
        try:
            path.unlink()
        except OSError:
            console.print(f"[yellow]Could not remove {path}[/yellow]")

    if ENVIRONMENT_FILE.exists():
        try:
            ENVIRONMENT_FILE.unlink()
        except OSError:
            console.print(f"[yellow]Could not remove {ENVIRONMENT_FILE}[/yellow]")

    edge_fingerprint = _load_or_generate_edge_fingerprint()
    token = creds.token if creds else None
    base_url = str(getattr(creds, "cyberwave_base_url", "") or "") if creds else None
    workspace_uuid = str(getattr(creds, "workspace_uuid", "") or "") if creds else None

    if token:
        from .commands.edge import _delete_registered_edges_for_fingerprint

        deleted, failed = _delete_registered_edges_for_fingerprint(
            fingerprint=edge_fingerprint,
            token=token,
            base_url=base_url,
            workspace_uuid=workspace_uuid,
        )
        if deleted:
            console.print(
                f"[green]Removed backend edge registration(s): "
                f"{deleted} (fingerprint: {edge_fingerprint}).[/green]"
            )
        if failed:
            console.print(
                f"[yellow]Failed to remove {failed} backend edge registration(s).[/yellow]"
            )

    console.print("[green]Previous edge configuration cleared.[/green]")


def configure_edge_environment(*, skip_confirm: bool = False) -> bool:
    """Select workspace + environment and save /etc/cyberwave/environment.json."""
    from .auth import AuthenticationError

    creds = load_credentials()
    if not creds or not creds.token:
        console.print("[red]No credentials found.[/red]")
        console.print("[dim]Run 'cyberwave login' first.[/dim]")
        return False

    if not skip_confirm:
        env_label, twin_json_files = _detect_existing_edge_configuration()
        if env_label is not None:
            display_name = env_label or "an unknown environment"
            disconnect = Confirm.ask(
                f"This edge is already connected to [bold]{display_name}[/bold]. "
                "Do you want to disconnect it first before installing again?",
                default=True,
            )
            if disconnect:
                _cleanup_existing_edge_configuration(
                    twin_json_files=twin_json_files,
                    creds=creds,
                )

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
            console.print(f"[dim]Removed stale edge fingerprint from twins: {detached_count}[/dim]")
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


def _buildkite_deb_registry_urls(registry_slug: str) -> tuple[str, str]:
    """Return the apt repo and GPG key URLs for a Buildkite Debian registry."""
    return (
        f"https://packages.buildkite.com/{BUILDKITE_ORG_SLUG}/{registry_slug}/any/",
        f"https://packages.buildkite.com/{BUILDKITE_ORG_SLUG}/{registry_slug}/gpgkey",
    )


def _buildkite_deb_registry_paths(registry_slug: str) -> tuple[Path, Path]:
    """Return the keyring and sources-list paths for a Buildkite Debian registry."""
    return (
        Path(f"/etc/apt/keyrings/cyberwave_{registry_slug}-archive-keyring.gpg"),
        Path(f"/etc/apt/sources.list.d/buildkite-cyberwave-{registry_slug}.list"),
    )


def _buildkite_deb_registry_auth_conf_path(registry_slug: str) -> Path:
    """Return the apt auth.conf.d path for a Buildkite Debian registry."""
    return Path(f"/etc/apt/auth.conf.d/cyberwave_{registry_slug}.conf")


def _resolve_deb_registry_slug(spec: ServiceSpec, channel: str = "stable") -> str:
    """Return the Buildkite Debian registry slug for the selected channel."""
    normalized_channel = _normalize_service_channel(channel)
    if normalized_channel == "stable":
        return spec.public_deb_registry_slug
    return INTERNAL_DEB_REGISTRY_SLUG


def _resolve_deb_registry_urls(spec: ServiceSpec, channel: str = "stable") -> tuple[str, str]:
    """Return the apt repo and GPG key URLs for the selected service channel."""
    return _buildkite_deb_registry_urls(_resolve_deb_registry_slug(spec, channel))


def _resolve_deb_registry_paths(spec: ServiceSpec, channel: str = "stable") -> tuple[Path, Path]:
    """Return the keyring and sources-list paths for the selected service channel."""
    return _buildkite_deb_registry_paths(_resolve_deb_registry_slug(spec, channel))


def _resolve_deb_registry_auth_conf_path(spec: ServiceSpec, channel: str = "stable") -> Path:
    """Return the apt auth.conf.d path for the selected service channel."""
    return _buildkite_deb_registry_auth_conf_path(_resolve_deb_registry_slug(spec, channel))


def _resolve_deb_registry_read_token(channel: str = "stable") -> str | None:
    """Return the private registry read token required for prerelease channels."""
    normalized_channel = _normalize_service_channel(channel)
    if normalized_channel == "stable":
        return None
    env_token = os.environ.get(INTERNAL_DEB_READ_TOKEN_ENV)
    if env_token:
        return env_token
    creds = load_credentials()
    saved_token = getattr(creds, "internal_deb_read_token", None) if creds else None
    if isinstance(saved_token, str) and saved_token.strip():
        return saved_token.strip()
    return None


def _resolve_deb_registry_gpg_key_fetch_url(spec: ServiceSpec, channel: str = "stable") -> str:
    """Return the GPG key URL, embedding auth for private prerelease registries."""
    registry_slug = _resolve_deb_registry_slug(spec, channel)
    _, public_gpg_key_url = _buildkite_deb_registry_urls(registry_slug)
    token = _resolve_deb_registry_read_token(channel)
    if not token:
        return public_gpg_key_url
    return f"https://buildkite:{token}@packages.buildkite.com/{BUILDKITE_ORG_SLUG}/{registry_slug}/gpgkey"


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
    channel: str = "stable",
) -> bool:
    """Install a service package via apt-get.

    Adds the Buildkite package registry GPG key and source if not already
    configured, then installs (or upgrades) the requested version of the package.

    Returns True on success.
    """
    resolved_name = package_name or spec.package_name
    deb_repo_url, gpg_key_url = _resolve_deb_registry_urls(spec, channel)
    keyring_path, sources_list = _resolve_deb_registry_paths(spec, channel)
    auth_conf_path = _resolve_deb_registry_auth_conf_path(spec, channel)
    registry_slug = _resolve_deb_registry_slug(spec, channel)
    registry_read_token = _resolve_deb_registry_read_token(channel)

    if channel != "stable" and not registry_read_token:
        console.print(
            f"[red]{INTERNAL_DEB_READ_TOKEN_ENV} is required for {channel} package installs.[/red]"
        )
        return False

    if registry_read_token:
        try:
            auth_conf_path.parent.mkdir(parents=True, exist_ok=True)
            auth_conf_path.write_text(
                "machine "
                f"https://packages.buildkite.com/{BUILDKITE_ORG_SLUG}/{registry_slug}/ "
                f"login buildkite password {registry_read_token}\n",
                encoding="utf-8",
            )
            _run(["chmod", "600", str(auth_conf_path)])
        except PermissionError:
            console.print(
                "[red]Permission denied writing apt auth config.[/red]\n"
                f"[dim]Re-run with sudo: {spec.sudo_command_hint}[/dim]"
            )
            return False

    # Install the GPG signing key if missing
    if not keyring_path.exists():
        console.print("[cyan]Installing Cyberwave package signing key...[/cyan]")
        try:
            keyring_path.parent.mkdir(parents=True, exist_ok=True)

            child_env = clean_subprocess_env()
            ld_library_path = child_env.get("LD_LIBRARY_PATH", "(unset)")
            console.print(f"[dim]LD_LIBRARY_PATH for child: {ld_library_path}[/dim]")

            # Download the armored GPG key
            curl = subprocess.run(
                ["curl", "-fsSL", _resolve_deb_registry_gpg_key_fetch_url(spec, channel)],
                capture_output=True,
                check=True,
                env=child_env,
            )
            if not curl.stdout:
                console.print("[red]Downloaded GPG key is empty.[/red]")
                console.print(
                    f"[dim]URL: {_resolve_deb_registry_gpg_key_fetch_url(spec, channel)}[/dim]"
                )
                return False

            # Dearmor into the keyring file
            gpg = subprocess.run(
                ["gpg", "--batch", "--yes", "--dearmor", "-o", str(keyring_path)],
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
            console.print(
                f"[dim]URL: {_resolve_deb_registry_gpg_key_fetch_url(spec, channel)}[/dim]"
            )
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
        signed_by = f"signed-by={keyring_path}"
        source_lines = (
            f"deb [{signed_by}] {deb_repo_url} any main\n"
            f"deb-src [{signed_by}] {deb_repo_url} any main\n"
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

    # Prevent debconf interactive prompts that can block headless installs,
    # and reduce dpkg's fsync overhead which is brutal on SD-card devices
    # (Raspberry Pi etc.) and can cause the system to appear frozen.
    apt_env = {
        **clean_subprocess_env(),
        "DEBIAN_FRONTEND": "noninteractive",
    }
    dpkg_force_unsafe_io = "-o=Dpkg::Options::=--force-unsafe-io"

    try:
        # Retry apt-get update to handle transient CDN mirror sync failures.
        # After all attempts, warn and continue — apt will use its cached index
        # for any failing source and the install may still succeed.
        apt_update_retries = 3
        apt_update_retry_delay = 8  # seconds
        for attempt in range(1, apt_update_retries + 1):
            try:
                _run(["apt-get", "update", "-qq"], env=apt_env)
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
        _run(
            ["apt-get", "install", "-y", "-qq", dpkg_force_unsafe_io, install_target],
            env=apt_env,
        )
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]apt-get failed (exit {exc.returncode}).[/red]")
        return False

    if spec.binary_path.exists():
        console.print(f"[green]Installed:[/green] {spec.binary_path}")
        return True

    console.print("[red]Binary not found after installation.[/red]")
    return False


# Re-exported from pip_registry for backward compat.
from .pip_registry import (  # noqa: E402
    INTERNAL_PYTHON_REGISTRY_SLUG,
    Version,
    _buildkite_python_registry_index_url,
    _buildkite_python_registry_slug,
    _extract_version_from_distribution_filename,
    _fetch_available_simple_index_versions,
    _normalize_service_channel,
    _pip_version_matches_channel,
    _resolve_buildkite_python_registry_slug,
    _select_pip_version_for_channel,
    _validate_pip_channel_version,
)

INTERNAL_PYTHON_READ_TOKEN_ENV = "CYBERWAVE_INTERNAL_PYTHON_READ_TOKEN"


def _describe_pip_install_target(
    spec: ServiceSpec = EDGE_CORE_SPEC,
    *,
    channel: str = "stable",
    package_version: str | None = None,
) -> str:
    """Return the user-facing pip target shown during confirmation prompts."""
    if package_version:
        return f"{spec.package_name}=={package_version}"
    normalized_channel = _normalize_service_channel(channel)
    if normalized_channel == "stable":
        return f"{spec.package_name} (latest stable release)"
    return f"{spec.package_name} (latest {normalized_channel} release)"


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
    normalized_channel = _normalize_service_channel(channel)
    pip_command = [sys.executable, "-m", "pip", "install"]
    buildkite_index_url: str | None = None
    registry_read_token = os.environ.get(INTERNAL_PYTHON_READ_TOKEN_ENV)
    if not registry_read_token:
        creds = load_credentials()
        saved_token = getattr(creds, "internal_python_read_token", None) if creds else None
        if isinstance(saved_token, str) and saved_token.strip():
            registry_read_token = saved_token.strip()

    if normalized_channel == "stable" and not package_version:
        pip_target = spec.package_name
    else:
        try:
            if package_version:
                validated_version = _validate_pip_channel_version(
                    spec.package_name,
                    package_version,
                    normalized_channel,
                )
                pip_target = f"{spec.package_name}=={validated_version}"
            else:
                if normalized_channel != "stable" and not registry_read_token:
                    console.print(
                        f"[red]{INTERNAL_PYTHON_READ_TOKEN_ENV} is required for {normalized_channel} "
                        "Python package installs.[/red]"
                    )
                    return False
                if normalized_channel != "stable":
                    version_lookup_index_url = _buildkite_python_registry_index_url(
                        _resolve_buildkite_python_registry_slug(
                            spec.package_name, normalized_channel
                        )
                    )
                else:
                    version_lookup_index_url = buildkite_index_url
                available_versions = _fetch_available_simple_index_versions(
                    version_lookup_index_url,
                    spec.package_name,
                    buildkite_read_token=registry_read_token
                    if normalized_channel != "stable"
                    else None,
                )
                resolved_version = _select_pip_version_for_channel(
                    available_versions,
                    package_name=spec.package_name,
                    channel=normalized_channel,
                )
                pip_target = f"{spec.package_name}=={resolved_version}"
                console.print(
                    f"[cyan]Resolved {spec.package_name} {normalized_channel} channel to "
                    f"{resolved_version}.[/cyan]"
                )
        except (RuntimeError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            return False

        if normalized_channel != "stable":
            if buildkite_index_url is None:
                if not registry_read_token:
                    console.print(
                        f"[red]{INTERNAL_PYTHON_READ_TOKEN_ENV} is required for {normalized_channel} "
                        "Python package installs.[/red]"
                    )
                    return False
                buildkite_index_url = _buildkite_python_registry_index_url(
                    _resolve_buildkite_python_registry_slug(spec.package_name, normalized_channel),
                    registry_read_token,
                )
            assert buildkite_index_url is not None
            pip_command.extend(
                [
                    "--pre",
                    "--extra-index-url",
                    buildkite_index_url,
                ]
            )
            console.print(
                f"[cyan]Using Buildkite Python registry for the "
                f"{normalized_channel} channel.[/cyan]"
            )

    console.print(f"[cyan]Installing {pip_target} via pip...[/cyan]")
    try:
        _run([*pip_command, pip_target])
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
        return _apt_get_install(
            spec,
            package_name=package_name,
            package_version=version,
            channel=channel,
        )
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


def _check_docker_macos() -> bool:
    """Verify that Docker Desktop is installed and running on macOS.

    On macOS we do not auto-install Docker (Docker Desktop is a GUI app
    that requires interactive setup), so the pre-flight check just
    surfaces a clear, copy-pasteable hint and aborts the install if
    Docker is missing or the daemon is not yet running.

    Returns True when ``docker info`` succeeds.
    """
    if shutil.which("docker"):
        try:
            proc = subprocess.run(
                ["docker", "info"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=clean_subprocess_env(),
            )
        except FileNotFoundError:
            proc = None

        if proc is not None and proc.returncode == 0:
            return True

        console.print(
            "[red]Docker is installed, but the daemon is not running.[/red]\n"
            "[dim]Open Docker Desktop (or Colima) and wait for the daemon to be "
            "ready, then re-run 'cyberwave edge install'.[/dim]"
        )
        return False

    console.print(
        "[red]Docker Desktop is required for the edge drivers and ML workers, "
        "but it was not found on this Mac.[/red]"
    )
    if shutil.which("brew"):
        console.print(
            "[dim]Install with Homebrew:\n"
            "    brew install --cask docker\n"
            "Then open Docker Desktop once so the daemon starts, "
            "and re-run 'cyberwave edge install'.[/dim]"
        )
    else:
        console.print(
            "[dim]Download Docker Desktop from "
            "https://docs.docker.com/desktop/install/mac-install/, "
            "open it once so the daemon starts, then re-run "
            "'cyberwave edge install'.[/dim]"
        )
    return False


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
            "[red]Docker installation requires root privileges.[/red]\n"
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
    3. Current Python environment bin used by pip installs
    4. PATH lookup via shutil.which
    5. spec.binary_path as a fallback (may not exist)
    """
    if spec.binary_path.exists():
        return str(spec.binary_path)
    venv_bin = Path.home() / ".cyberwave-cli" / "venv-local" / "bin" / spec.package_name
    if venv_bin.exists():
        return str(venv_bin)
    current_env_bin = Path(sys.executable).parent / spec.package_name
    if current_env_bin.exists():
        return str(current_env_bin)
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


def _launchagent_target(spec: ServiceSpec) -> tuple[str, str]:
    """Return the launchctl domain/label target for a macOS LaunchAgent."""
    domain = f"gui/{os.getuid()}"
    return domain, f"{domain}/{_launchagent_label(spec)}"


def _launchagent_log_path(spec: ServiceSpec) -> Path:
    """Return the LaunchAgent log file path for the current user."""
    return Path.home() / "Library" / "Logs" / "Cyberwave" / f"{_launchagent_label(spec)}.log"


def create_launchagent_service(
    spec: ServiceSpec = CLOUD_NODE_SPEC,
    *,
    config_path: str | None = None,
) -> bool:
    """Write a LaunchAgent plist for the service on macOS."""
    program_arguments = [_resolve_service_binary(spec), *spec.launch_command]
    if config_path:
        # launchd launches from /, so config paths must be absolute.
        abs_config = str(Path(config_path).resolve())
        program_arguments.extend(["--config", abs_config])

    log_dir = _launchagent_log_path(spec).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    label = _launchagent_label(spec)
    plist_data = {
        "Label": label,
        "ProgramArguments": program_arguments,
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(_launchagent_log_path(spec)),
        "StandardErrorPath": str(_launchagent_log_path(spec)),
    }

    plist_path = _launchagent_plist_path(spec)
    try:
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_bytes(plistlib.dumps(plist_data))
    except PermissionError:
        console.print(
            "[red]Permission denied writing LaunchAgent plist.[/red]\n"
            f"[dim]Run '{spec.install_command_hint}' without sudo on macOS.[/dim]"
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
    domain, _bootout_target = _launchagent_target(spec)

    try:
        wait_for_launchd_unload(label, legacy_labels=legacy_labels_for_package(spec.package_name))
        bootstrap_launchd_service(domain, plist_path)
    except FileNotFoundError:
        console.print("[red]launchctl not found on this system.[/red]")
        return False
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]launchctl bootstrap failed (exit {exc.returncode}).[/red]")
        if exc.returncode == 5:
            console.print(
                "[dim]Hint: launchd reports a transient I/O error even after "
                f"retries. Inspect state with 'launchctl print {domain}/{label}', "
                "or re-run under sudo for richer launchctl error output.[/dim]"
            )
        elif os.getuid() == 0 and not os.getenv("SUDO_USER"):
            console.print(
                "[dim]LaunchAgent loading requires an active macOS GUI login "
                "session and the invoking user's UID (run without sudo, or "
                "ensure SUDO_USER is set).[/dim]"
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
    unit_contents = spec.unit_template.format(binary_path=binary, config_dir=CONFIG_DIR)

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

    Uses ``restart`` instead of ``start`` so that a re-install picks up
    the updated ``environment.json`` / twin selection without requiring
    a manual ``systemctl restart``.  On a fresh install the service is
    not yet active, so ``restart`` behaves identically to ``start``.

    The restart is issued with ``--no-block`` so the CLI returns
    immediately instead of waiting for the ``Type=notify`` service to
    signal ``READY=1`` (which includes Docker image pulls and can take
    several minutes on slow links).

    Returns True on success.
    """
    if not spec.unit_path.exists():
        console.print("[red]Service unit not found — run install first.[/red]")
        return False

    try:
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", spec.unit_name])
        _run(["systemctl", "restart", "--no-block", spec.unit_name])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]systemctl command failed (exit {exc.returncode}).[/red]")
        return False

    console.print(f"[green]Service enabled and (re)starting:[/green] {spec.unit_name}")
    console.print(
        f"[dim]The service is booting in the background (driver image pulls, "
        f"twin sync, etc.).\n"
        f"Run 'cyberwave edge logs' to follow progress.[/dim]"
    )
    return True


def restart_service(spec: ServiceSpec = EDGE_CORE_SPEC) -> bool:
    """Restart the systemd service described by ``spec``.

    Requires root privileges.  Returns True on success.
    """
    if not _has_systemd():
        console.print("[yellow]systemd not detected — cannot restart via systemd.[/yellow]")
        return False

    if not spec.unit_path.exists():
        console.print(f"[red]Service unit not found — run '{spec.sudo_command_hint}' first.[/red]")
        return False

    require_root(f"sudo {spec.install_command_hint.rsplit(' ', 1)[0]} restart")

    try:
        _run(["systemctl", "restart", spec.unit_name])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]systemctl restart failed (exit {exc.returncode}).[/red]")
        return False

    console.print(f"[green]Service restarted:[/green] {spec.unit_name}")
    return True


def stop_service(spec: ServiceSpec = EDGE_CORE_SPEC) -> bool:
    """Stop the systemd service described by ``spec``.

    Requires root privileges.  Returns True on success.
    """
    if not _has_systemd():
        console.print("[yellow]systemd not detected — cannot stop via systemd.[/yellow]")
        return False

    if not spec.unit_path.exists():
        console.print(f"[red]Service unit not found — run '{spec.sudo_command_hint}' first.[/red]")
        return False

    require_root(f"sudo {spec.install_command_hint.rsplit(' ', 1)[0]} stop")

    try:
        _run(["systemctl", "stop", spec.unit_name])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]systemctl stop failed (exit {exc.returncode}).[/red]")
        return False

    console.print(f"[green]Service stopped:[/green] {spec.unit_name}")
    return True


def start_service(spec: ServiceSpec = EDGE_CORE_SPEC) -> bool:
    """Start the service without re-enabling it (user-initiated start).

    When the service is already active, this is a no-op from systemd's
    perspective: ``systemctl start`` does not re-execute the unit's
    ``ExecStart`` (so edge-core's boot-time driver startup does *not*
    run again).  Surfacing this explicitly avoids the misleading
    ``✓ Started`` UX when an operator is actually trying to apply
    config changes — they should use ``cyberwave edge restart`` for
    that.

    Requires root privileges.  Returns True on success or when the
    service was already active.
    """
    if not _has_systemd():
        return False
    if not spec.unit_path.exists():
        console.print(f"[red]Service unit not found — run '{spec.sudo_command_hint}' first.[/red]")
        return False
    if is_service_active(spec):
        restart_hint = spec.install_command_hint.rsplit(" ", 1)[0] + " restart"
        console.print(
            f"[yellow]{spec.unit_name} is already running — `systemctl start` is a no-op.[/yellow]"
        )
        console.print(
            "[dim]To apply config changes (re-run boot-time driver startup, "
            f"reload twins, etc.) use: sudo {restart_hint}[/dim]"
        )
        return True

    require_root(f"sudo {spec.install_command_hint.rsplit(' ', 1)[0]} start")

    result = _run(["systemctl", "start", spec.unit_name], check=False)
    if result.returncode == 0:
        console.print(f"[green]✓ Started {spec.unit_name}[/green]")
        return True
    console.print(
        f"[yellow]systemctl start failed (exit {result.returncode}). "
        f"Check status with: systemctl status {spec.unit_name}[/yellow]"
    )
    return False


_CAMERA_SENSOR_TYPES = {"camera", "rgb", "depth_camera"}
_MICROPHONE_SENSOR_TYPES = {"audio", "audio_mono", "audio_stereo", "microphone"}
_SPEAKER_SENSOR_TYPES = {"speaker", "loudspeaker", "speakerphone", "audio_out"}


def _collect_twin_sensors(data: dict) -> list[dict]:
    """Gather sensor dicts from every catalog/twin JSON location we sync."""
    asset = data.get("asset") or {}
    schema = asset.get("universal_schema") or {}
    sensors: list[dict] = []
    for bucket in (
        schema.get("sensors"),
        (asset.get("metadata") or {}).get("sensors"),
        (data.get("metadata") or {}).get("sensors"),
    ):
        if isinstance(bucket, list):
            sensors.extend(sensor for sensor in bucket if isinstance(sensor, dict))
    return sensors


def _load_selected_twin_uuids() -> set[str] | None:
    """Return the twin UUIDs the user selected in ``environment.json``.

    Returns ``None`` when the file is missing or does not list any twins,
    signalling callers to fall back to "every cached twin".
    """
    if not ENVIRONMENT_FILE.exists():
        return None
    try:
        data = json.loads(ENVIRONMENT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    twin_uuids = data.get("twin_uuids")
    if not isinstance(twin_uuids, list):
        return None
    selected = {str(u) for u in twin_uuids if u}
    return selected or None


def _list_camera_twins() -> list[tuple[str, str]]:
    """Return ``(twin_uuid, twin_name)`` for each *selected* twin with a camera sensor.

    Reads the ``{twin_uuid}.json`` files written by
    :func:`_download_twin_json_files`, filters to assets that declare at
    least one camera-type sensor, and then restricts the result to the
    twins listed in ``environment.json`` when that file is present.  This
    ensures the per-twin camera mapping only walks through the twins the
    user actually chose in the connected-twin picker, ignoring stale
    caches from previous installs.
    """
    selected_uuids = _load_selected_twin_uuids()
    results: list[tuple[str, str]] = []
    for path in sorted(CONFIG_DIR.glob("*.json")):
        if path.name in (
            "credentials.json",
            "environment.json",
            "cameras.json",
            "fingerprint.json",
            "edge.json",
        ):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        asset = data.get("asset") or {}
        schema = asset.get("universal_schema") or {}
        sensors = schema.get("sensors") or []
        if not any(sensor.get("type") in _CAMERA_SENSOR_TYPES for sensor in sensors):
            continue
        twin_uuid = str(data.get("uuid") or path.stem)
        twin_name = str(data.get("name") or twin_uuid)
        if not twin_uuid:
            continue
        if selected_uuids is not None and twin_uuid not in selected_uuids:
            continue
        results.append((twin_uuid, twin_name))
    return results


def _any_twin_has_camera_sensor() -> bool:
    """Check downloaded twin JSON files for camera-type sensors."""
    return bool(_list_camera_twins())


def _list_microphone_twins() -> list[tuple[str, str]]:
    """Return ``(twin_uuid, twin_name)`` for each selected twin with a microphone sensor."""
    selected_uuids = _load_selected_twin_uuids()
    results: list[tuple[str, str]] = []
    for path in sorted(CONFIG_DIR.glob("*.json")):
        if path.name in (
            "credentials.json",
            "environment.json",
            "cameras.json",
            "camera_streams.json",
            "audio_streams.json",
            "fingerprint.json",
            "edge.json",
        ):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sensors = _collect_twin_sensors(data)
        if not any(
            str(sensor.get("type", "")).strip().lower() in _MICROPHONE_SENSOR_TYPES
            for sensor in sensors
        ):
            continue
        twin_uuid = str(data.get("uuid") or path.stem)
        twin_name = str(data.get("name") or twin_uuid)
        if not twin_uuid:
            continue
        if selected_uuids is not None and twin_uuid not in selected_uuids:
            continue
        results.append((twin_uuid, twin_name))
    return results


def _any_twin_has_microphone_sensor() -> bool:
    """Check downloaded twin JSON files for microphone-type sensors."""
    return bool(_list_microphone_twins())


def _list_speaker_twins() -> list[tuple[str, str]]:
    """Return ``(twin_uuid, twin_name)`` for each selected twin with a speaker sensor."""
    selected_uuids = _load_selected_twin_uuids()
    results: list[tuple[str, str]] = []
    for path in sorted(CONFIG_DIR.glob("*.json")):
        if path.name in (
            "credentials.json",
            "environment.json",
            "cameras.json",
            "camera_streams.json",
            "audio_streams.json",
            "fingerprint.json",
            "edge.json",
        ):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sensors = _collect_twin_sensors(data)
        if not any(
            str(sensor.get("type", "")).strip().lower() in _SPEAKER_SENSOR_TYPES
            for sensor in sensors
        ):
            continue
        twin_uuid = str(data.get("uuid") or path.stem)
        twin_name = str(data.get("name") or twin_uuid)
        if not twin_uuid:
            continue
        if selected_uuids is not None and twin_uuid not in selected_uuids:
            continue
        results.append((twin_uuid, twin_name))
    return results


def _any_twin_has_speaker_sensor() -> bool:
    """Check downloaded twin JSON files for speaker-type sensors."""
    return bool(_list_speaker_twins())


def _render_camera_menu(
    cameras: list[Any],
    *,
    assignments: dict[int, str] | None = None,
) -> dict[int, Any]:
    """Print a numbered camera menu and return the map of valid indices.

    When ``assignments`` is provided, each already-mapped camera is annotated
    with the twin name it is currently assigned to.
    """
    from .device_utils import camera_likelihood_score

    assignments = assignments or {}
    valid_indices: dict[int, Any] = {}
    for i, cam in enumerate(cameras):
        idx = cam.index if cam.index is not None else i
        valid_indices[idx] = cam
        score = camera_likelihood_score(cam)
        assigned_to = assignments.get(idx)
        suffix = f"  [yellow](assigned to {assigned_to})[/yellow]" if assigned_to else ""
        if score >= 40:
            label = (
                f"  [bold cyan]{idx}[/bold cyan])  {cam.card}  "
                f"[dim]{cam.primary_path}[/dim]{suffix}"
            )
        else:
            label = (
                f"  [dim]{idx})  {cam.card}  {cam.primary_path}  "
                f"(probably not a camera)[/dim]{suffix}"
            )
        console.print(label)
    return valid_indices


def _prompt_camera_index(
    cameras: list[Any],
    valid_indices: dict[int, Any],
    *,
    prompt: str,
    default_index: int,
) -> int | None:
    """Prompt for a single camera index. Returns ``None`` on invalid input."""
    raw = Prompt.ask(prompt, default=str(default_index))
    try:
        chosen = int(raw)
    except ValueError:
        console.print("[yellow]Invalid selection — skipping camera config.[/yellow]")
        return None
    if chosen not in valid_indices:
        console.print(f"[yellow]Camera {chosen} not available — skipping.[/yellow]")
        return None
    return chosen


def _upload_cameras_to_edge_metadata(cameras_data: dict) -> bool:
    """Upload camera config to Edge.metadata.cameras and persist edge.json.

    Best-effort: logs a warning and returns False on any failure.
    """
    from .credentials import load_credentials
    from .io_utils import atomic_write_json
    from .utils import resolve_api_url

    creds = load_credentials()
    if not creds or not creds.token:
        return False
    token = creds.token

    fingerprint_file = CONFIG_DIR / "fingerprint.json"
    if not fingerprint_file.exists():
        return False
    try:
        fingerprint = json.loads(fingerprint_file.read_text()).get("fingerprint", "")
    except Exception:
        return False
    if not fingerprint:
        return False

    try:
        from cyberwave import Cyberwave

        base_url = resolve_api_url(creds=creds)
        client = Cyberwave(base_url=base_url, api_key=token)

        edge = None
        for e in client.edges.list():
            if getattr(e, "fingerprint", None) == fingerprint:
                edge = e
                break
        if edge is None:
            edge = client.edges.create(fingerprint=fingerprint)

        edge_uuid = str(getattr(edge, "uuid", "") or "")
        if not edge_uuid:
            console.print("[dim]Could not resolve edge UUID for camera metadata upload.[/dim]")
            return False

        updated_edge = client.edges.update(
            edge_uuid,
            {"fingerprint": fingerprint, "metadata": {"cameras": cameras_data}},
        )
        # Use to_json() → loads() to get a plain serializable dict (handles datetimes).
        if hasattr(updated_edge, "to_json"):
            edge_dict = json.loads(updated_edge.to_json())
        else:
            edge_dict = dict(updated_edge)
        atomic_write_json(CONFIG_DIR / "edge.json", edge_dict, mode=0o644)
        console.print(f"[dim]Saved to {CONFIG_DIR / 'edge.json'}[/dim]")
        return True
    except Exception as exc:
        console.print(f"[dim]Camera metadata upload skipped: {exc}[/dim]")
        return False


def _detect_and_select_cameras() -> None:
    """Discover cameras and prompt the user to map them to camera-bearing twins.

    When a single camera twin is connected, the user is prompted once (matching
    the historical single-select behaviour).  When multiple camera twins are
    connected alongside multiple physical cameras, the user is walked through a
    per-twin mapping so each twin ends up bound to a specific ``/dev/video*``
    device.

    Best-effort: failures are logged but never block installation.
    """
    from .device_utils import discover_usb_cameras, write_cameras_json

    cameras = discover_usb_cameras()
    if not cameras:
        console.print(
            "[dim]No cameras detected. You can add one later: cyberwave edge cameras[/dim]"
        )
        return

    camera_twins = _list_camera_twins()
    default_idx = cameras[0].index if cameras[0].index is not None else 0

    def _save_cameras(
        selected_index: int | None,
        twin_to_device: dict[str, int] | None,
    ) -> None:
        write_cameras_json(
            cameras,
            CONFIG_DIR,
            selected_index=selected_index,
            twin_to_device=twin_to_device or None,
        )
        console.print(f"[dim]Saved to {CONFIG_DIR / 'cameras.json'}[/dim]\n")
        cameras_data: dict = {"devices": [c.to_dict() for c in cameras]}
        if selected_index is not None:
            cameras_data["selected_device"] = selected_index
        if twin_to_device:
            cameras_data["twin_to_device"] = {str(k): v for k, v in twin_to_device.items()}
        _upload_cameras_to_edge_metadata(cameras_data)

    # Single physical camera: auto-assign to every camera twin.
    if len(cameras) == 1:
        cam = cameras[0]
        selected = cam.index if cam.index is not None else 0
        twin_to_device = {twin_uuid: selected for twin_uuid, _ in camera_twins}
        console.print(f"\n[cyan]Detected camera:[/cyan] {cam.card} ({cam.primary_path})")
        if len(camera_twins) > 1:
            console.print(
                f"[dim]All {len(camera_twins)} camera twin(s) will share this device.[/dim]"
            )
        _save_cameras(selected, twin_to_device or None)
        return

    # Single camera twin (or none known) with multiple cameras: keep the legacy
    # single-select flow so existing scripted installs remain unchanged.
    if len(camera_twins) <= 1:
        console.print(f"\n[bold]Detected {len(cameras)} camera(s):[/bold]\n")
        valid_indices = _render_camera_menu(cameras)
        console.print()
        selected = _prompt_camera_index(
            cameras,
            valid_indices,
            prompt="Select camera",
            default_index=default_idx,
        )
        if selected is None:
            return
        console.print(f"[green]Selected:[/green] {valid_indices[selected].card}")
        twin_to_device = {camera_twins[0][0]: selected} if camera_twins else {}
        _save_cameras(selected, twin_to_device or None)
        return

    # Multiple camera twins AND multiple cameras: map each twin to a camera.
    console.print(
        f"\n[bold]Detected {len(cameras)} camera(s) and {len(camera_twins)} camera twin(s).[/bold]"
    )
    console.print(
        "[dim]Map each twin to the physical camera it is wired to. "
        "A device can be assigned to more than one twin if it is shared.[/dim]\n"
    )

    twin_to_device: dict[str, int] = {}
    assignments_label: dict[int, str] = {}
    first_selected: int | None = None
    remaining_default = default_idx

    for twin_uuid, twin_name in camera_twins:
        console.print(f"[bold]Twin:[/bold] {twin_name} [dim]({twin_uuid[:8]}...)[/dim]")
        valid_indices = _render_camera_menu(cameras, assignments=assignments_label)
        console.print()
        selected = _prompt_camera_index(
            cameras,
            valid_indices,
            prompt=f"Camera for {twin_name}",
            default_index=remaining_default,
        )
        if selected is None:
            console.print(
                "[yellow]Skipping remaining twin mappings; edge will fall back "
                "to /dev/video0 for unmapped twins.[/yellow]"
            )
            break
        twin_to_device[twin_uuid] = selected
        assignments_label[selected] = twin_name
        if first_selected is None:
            first_selected = selected
        # Prefer an unassigned device as the next default, but keep the current
        # choice if every device has been touched.
        remaining = [idx for idx in valid_indices if idx not in assignments_label]
        if remaining:
            remaining_default = remaining[0]
        console.print(f"[green]Selected:[/green] {valid_indices[selected].card}\n")

    if not twin_to_device:
        console.print("[yellow]No cameras mapped — skipping camera config.[/yellow]")
        return

    _save_cameras(first_selected, twin_to_device)


# ---- orchestrator ------------------------------------------------------------


def setup_service(
    spec: ServiceSpec,
    *,
    skip_confirm: bool = False,
    channel: str = "stable",
    version: str | None = None,
    config_path: str | None = None,
    post_install_hook: Any = None,
    force_reinstall: bool = False,
) -> bool:
    """Generic install orchestrator: install package, optionally set up Docker + systemd.

    When *force_reinstall* is True, platform helpers (USB/IP server, camera
    stream) are torn down and rebuilt from scratch instead of being skipped
    when already running.

    Returns True if everything succeeded.
    """
    _migrate_legacy_config_dir()

    linux_service_setup = _is_linux()
    macos_launchagent_supported = _is_macos() and spec.supports_macos_launchagent

    if linux_service_setup and os.geteuid() != 0:
        console.print(
            f"[red]This command requires root privileges.[/red]\n"
            f"[dim]Re-run with sudo: {spec.sudo_command_hint}[/dim]"
        )
        return False

    if macos_launchagent_supported and os.geteuid() == 0:
        console.print(
            "[red]macOS LaunchAgent installs must be run without sudo.[/red]\n"
            f"[dim]Re-run as your regular user: {spec.install_command_hint}[/dim]"
        )
        return False

    if not linux_service_setup and not macos_launchagent_supported:
        console.print(
            f"[yellow]{spec.package_name} systemd service setup is only supported on Linux. "
            "You will need to start it manually upon restart.[/yellow]"
        )
        if is_macos() and spec.requires_docker:
            console.print(
                "[dim]On macOS, USB devices are shared to Docker containers via USB/IP.[/dim]"
            )

    if not _ensure_credentials(skip_confirm=skip_confirm):
        return False

    if not skip_confirm:
        if linux_service_setup:
            has_apt = bool(shutil.which("apt-get"))
            if has_apt:
                selected_pkg = _resolve_service_package_name(channel, spec)
                selected_target = f"{selected_pkg}={version}" if version else selected_pkg
                install_method = f"Install [bold]{selected_target}[/bold] via apt-get"
            else:
                pip_target = _describe_pip_install_target(
                    spec,
                    channel=channel,
                    package_version=version,
                )
                install_method = f"Install [bold]{pip_target}[/bold] via pip"
            console.print(
                f"\nThis will:\n"
                f"  1. {install_method}\n"
                f"  2. Create a systemd service ([bold]{spec.unit_name}[/bold])\n"
                f"  3. Enable it to start on boot\n"
            )
        elif macos_launchagent_supported:
            pip_target = _describe_pip_install_target(
                spec,
                channel=channel,
                package_version=version,
            )
            console.print(
                f"\nThis will:\n"
                f"  1. Install [bold]{pip_target}[/bold] via pip\n"
                "  2. Configure service credentials\n"
                "  3. Create and load a LaunchAgent for your macOS user session\n"
            )
        else:
            pip_target = _describe_pip_install_target(
                spec,
                channel=channel,
                package_version=version,
            )
            console.print(
                f"\nThis will:\n"
                f"  1. Install [bold]{pip_target}[/bold] via pip\n"
                f"  2. Configure service credentials\n"
                f"  3. Skip service setup (manual startup required)\n"
            )
        if not Confirm.ask("Continue?", default=True):
            console.print("[dim]Aborted.[/dim]")
            return False

    # Pre-flight: on macOS we cannot auto-install Docker Desktop, so check
    # before pip-installing edge-core. Catches the common "I forgot to open
    # Docker Desktop" failure mode early instead of leaving the user with a
    # registered LaunchAgent that crash-loops trying to spawn drivers.
    if is_macos() and spec.requires_docker:
        if not _check_docker_macos():
            return False

    if not install_service_package(spec, channel=channel, version=version):
        return False

    if linux_service_setup and spec.requires_docker:
        if not _install_docker():
            return False

    if force_reinstall and not (is_macos() and spec.requires_docker):
        console.print(
            "[dim]--force-reinstall has no effect on this platform "
            "(only applies to macOS platform helpers).[/dim]"
        )

    if is_macos() and spec.requires_docker:
        if not setup_usbip_server(force=force_reinstall, skip_confirm=skip_confirm):
            console.print(
                "[yellow]USB/IP setup failed. USB devices will not be "
                "available inside Docker containers.[/yellow]\n"
                "[dim]You can retry later: cyberwave edge install[/dim]"
            )

    if post_install_hook is not None:
        if not post_install_hook():
            return False

    # Camera selection runs after twin selection so the interactive prompt
    # isn't wiped by the twin picker's screen clear.
    # Only prompt when at least one selected twin has a camera sensor.
    if spec.requires_docker and _any_twin_has_camera_sensor():
        if is_macos():
            if not setup_camera_stream_server(
                force=force_reinstall,
                camera_twins=_list_camera_twins(),
            ):
                console.print(
                    "[yellow]Camera stream setup failed. MJPEG fallback will "
                    "not be available.[/yellow]\n"
                    "[dim]You can retry later: cyberwave edge install[/dim]"
                )
        elif linux_service_setup:
            _detect_and_select_cameras()

    if spec.requires_docker and _any_twin_has_microphone_sensor():
        if is_macos():
            if not setup_audio_stream_server(
                force=force_reinstall,
                microphone_twins=_list_microphone_twins(),
            ):
                console.print(
                    "[yellow]Microphone stream setup failed. The generic-microphone "
                    "driver will not receive a host audio bridge URL.[/yellow]\n"
                    "[dim]Retry: cyberwave edge install --reconfigure-microphone[/dim]"
                )
        else:
            console.print(
                "[dim]Linux microphone twins bind-mount /dev/snd into Docker with "
                "--group-add audio and ALSA cgroup rule c 116:* rmw (edge-core "
                "adds this automatically).[/dim]"
            )

    if spec.requires_docker and _any_twin_has_speaker_sensor():
        if is_macos():
            if not setup_audio_playback_server(
                force=force_reinstall,
                speaker_twins=_list_speaker_twins(),
            ):
                console.print(
                    "[yellow]Speaker playback setup failed. The generic-speaker "
                    "driver will not receive a host playback sink URL.[/yellow]\n"
                    "[dim]Retry: cyberwave edge install --reconfigure-speaker[/dim]"
                )
        else:
            console.print(
                "[dim]Linux speaker twins bind-mount /dev/snd into Docker with "
                "--group-add audio and ALSA cgroup rule c 116:* rmw (edge-core "
                "adds this automatically).[/dim]"
            )

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


def _resolve_worker_image() -> str:
    """Return the worker Docker image reference, respecting CYBERWAVE_ENVIRONMENT.

    Delegates to the canonical ``resolve_worker_image()`` in
    ``cyberwave_edge_core.worker_manager`` when available.  Falls back to
    credentials-based inference when edge-core is not installed (e.g.
    during initial ``cyberwave edge install``).
    """
    try:
        ensure_edge_core_importable()
        from cyberwave_edge_core.worker_manager import resolve_worker_image

        return resolve_worker_image()
    except Exception:
        pass

    base = "cyberwaveos/edge-ml-worker"
    creds = load_credentials()
    env_name = creds.cyberwave_environment if creds and creds.cyberwave_environment else None
    if env_name and env_name not in ("production",):
        return f"{base}:{env_name}"
    return f"{base}:latest"


def _docker_image_present(docker_bin: str, image: str) -> bool:
    """Return True if *image* is already present in the local Docker daemon."""
    try:
        proc = subprocess.run(
            [docker_bin, "image", "inspect", image],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _pull_worker_image() -> bool:
    """Pull the ML worker Docker image (best-effort).

    Behavior mirrors ``WorkerManager._ensure_image_pulled`` in
    ``cyberwave-edge-core``:

    * If the resolved image is already present locally, skip the pull
      entirely.  This is the contract for the ``:local`` tag and for
      ``CYBERWAVE_WORKER_IMAGE`` overrides (e.g. ``:local-gpu``) — those
      tags only exist locally and a registry pull is guaranteed to fail.
    * Otherwise issue ``docker pull`` to refresh mutable tags
      (``:dev``, ``:staging``, …).  If that fails and the tag is not
      ``:latest``, retry with ``:latest`` as a last-ditch fallback for
      first-time installs.

    Returns True always — a failed pull is non-fatal because
    ``WorkerManager._run_container()`` will pull implicitly on first
    start and itself falls back to a locally-present image.
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        console.print("[yellow]Docker not found — skipping worker image pull.[/yellow]")
        return True

    image = _resolve_worker_image()

    if _docker_image_present(docker_bin, image):
        console.print(
            f"[green]Worker image {image} already present locally — skipping pull.[/green]"
        )
        return True

    console.print(f"[cyan]Pulling worker image {image}...[/cyan]")
    try:
        _run([docker_bin, "pull", image])
        console.print(f"[green]Worker image {image} pulled successfully.[/green]")
        return True
    except subprocess.CalledProcessError:
        pass

    fallback = image.rsplit(":", 1)[0] + ":latest"
    if fallback != image:
        console.print(f"[yellow]Tag not found, trying {fallback}...[/yellow]")
        try:
            _run([docker_bin, "pull", fallback])
            console.print(f"[green]Worker image {fallback} pulled successfully.[/green]")
            return True
        except subprocess.CalledProcessError:
            pass

    console.print("[yellow]Worker image pull failed.[/yellow]")
    console.print("[dim]Workers will still work — edge-core pulls on first start.[/dim]")
    return True


def setup_edge_core(
    *,
    skip_confirm: bool = False,
    channel: str = "stable",
    version: str | None = None,
    force_reinstall: bool = False,
    pull_worker_image: bool = True,
) -> bool:
    """Full setup for edge core: install the package, create the service, enable on boot.

    When *force_reinstall* is True, platform helpers (USB/IP, camera stream)
    are torn down and rebuilt from scratch.

    The *pull_worker_image* parameter is **deprecated** and ignored.
    The ML worker Docker image is now pulled asynchronously by the
    edge-core service on first startup via ``WorkerManager``, matching the
    same pattern used for driver images (CYB-2029).

    Returns True if everything succeeded.
    """

    def _post_install() -> bool:
        return configure_edge_environment(skip_confirm=skip_confirm)

    return setup_service(
        EDGE_CORE_SPEC,
        skip_confirm=skip_confirm,
        channel=channel,
        version=version,
        post_install_hook=_post_install,
        force_reinstall=force_reinstall,
    )
