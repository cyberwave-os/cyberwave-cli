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
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

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
    AUDIO_STREAMS_FILENAME,
    CAMERA_STREAMS_FILENAME,
    SERIAL_BRIDGES_FILENAME,
    bootstrap_launchd_service,
    is_macos,
    legacy_labels_for_package,
    setup_audio_playback_server,
    setup_audio_stream_server,
    setup_camera_stream_server,
    setup_serial_bridges,
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
# PR builds get their own apt registry for the same reason as the Python ones:
# an unreviewed artifact should never sit in an index a normal install reads.
PR_DEB_REGISTRY_SLUG = "cyberwave-pr-deb"
INTERNAL_DEB_READ_TOKEN_ENV = "CYBERWAVE_INTERNAL_DEB_READ_TOKEN"
EDGE_CORE_PACKAGE_CHANNELS = {
    "stable": PACKAGE_NAME,
    "dev": "cyberwave-edge-core-dev",
    "staging": "cyberwave-edge-core-staging",
    "pr": "cyberwave-edge-core-pr",
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
        # No "pr": cloud-node-test-and-publish.yml builds no PR artifact, so the
        # channel would resolve to a package that is never published. Add it back
        # together with the workflow that fills it.
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
    """Create a Cyberwave SDK client from a token.

    The logged-in workspace is passed as ``workspace_id`` so the SDK names it
    on every request. Without it, a token scoped to more than one workspace
    has no way to say which one a call means and the backend answers
    ``400 workspace_required``.
    """
    from cyberwave import Cyberwave

    creds = load_credentials()
    workspace_id = getattr(creds, "workspace_uuid", None) if creds else None
    return Cyberwave(
        base_url=base_url or get_api_url(),
        token=token,
        workspace_id=workspace_id or None,
    )


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


def _resolve_base_url(
    runtime_overrides: Mapping[str, str | None], fallback: str | None
) -> str | None:
    """Pick which backend to talk to, given an optional device-local *fallback*.

    ``get_api_url()`` only reads ``CYBERWAVE_BASE_URL`` and otherwise returns
    the SDK default, so a device paired against a dev or staging stack keeps
    that URL solely in its stored credentials. ``--token`` swaps the credential,
    not the backend: without the fallback, re-pairing such a device would probe
    production and report a perfectly good token as rejected.
    """
    return (runtime_overrides.get("CYBERWAVE_BASE_URL") or fallback or "").strip() or None


def _authenticate_with_api_token(
    token: str, *, skip_confirm: bool, source: str, base_url: str | None = None
) -> None:
    """Validate an API token, resolve its workspace, and persist credentials.

    Shared by ``--token`` and ``CYBERWAVE_API_KEY`` so both resolve the
    workspace the same way — in particular preferring the workspace the token
    is bound to over ``workspaces[0]``, and honouring the
    ``CYBERWAVE_WORKSPACE_SLUG`` conflict check.

    *base_url* is the device-local fallback backend; ``CYBERWAVE_BASE_URL``
    still wins over it.

    Raises rather than returning a bool because the two callers need different
    policies on failure: an explicit ``--token`` aborts, while the env var
    falls back to an interactive login.
    """
    resolved_base_url = _resolve_base_url(collect_runtime_env_overrides(), base_url)
    # Re-collect with the resolved URL so the broker settings are inferred from
    # it, the way `cyberwave configure` does. Explicit env vars still win.
    runtime_overrides = collect_runtime_env_overrides(api_url_override=resolved_base_url)
    sdk_client = _get_sdk_client(token, base_url=resolved_base_url)
    with console.status(f"[dim]Checking the {source} API token...[/dim]"):
        workspace = _select_workspace_from_env_or_default(
            sdk_client,
            skip_confirm=skip_confirm,
            token=token,
            base_url=resolved_base_url,
        )

    save_credentials(
        Credentials(
            token=token,
            # Blank, not omitted: save_credentials merges, so omitting it would
            # keep reporting the previous login's address as this device.
            email="",
            workspace_uuid=str(getattr(workspace, "uuid", "") or ""),
            workspace_name=str(getattr(workspace, "name", "") or ""),
            cyberwave_environment=runtime_overrides.get("CYBERWAVE_ENVIRONMENT"),
            cyberwave_edge_log_level=_resolved_edge_log_level(runtime_overrides),
            cyberwave_worker_log_level=runtime_overrides.get("CYBERWAVE_WORKER_LOG_LEVEL"),
            # The resolved value, not the raw override: it is the backend this
            # token was just validated against.
            cyberwave_base_url=resolved_base_url,
            cyberwave_mqtt_host=runtime_overrides.get("CYBERWAVE_MQTT_HOST"),
            cyberwave_mqtt_port=runtime_overrides.get("CYBERWAVE_MQTT_PORT"),
        )
    )
    console.print(f"[green]✓[/green] Using the API token from {source}")
    console.print(f"[dim]Workspace: {workspace.name}[/dim]")
    console.print(f"[dim]Credentials saved to {CONFIG_DIR}/[/dim]\n")


def _ensure_credentials(*, skip_confirm: bool, token: str | None = None) -> bool:
    """Ensure valid credentials exist in ``CONFIG_DIR`` before installing.

    If saved credentials are found and valid, returns True immediately.
    Otherwise prompts for email/password and runs the full login flow.

    An explicit *token* (``--token``) overrides all of that: stored
    credentials, ``CYBERWAVE_API_KEY``, and the prompt.
    """
    from .auth import APIToken, AuthClient, AuthenticationError

    # `--token` is an instruction, not a fallback. Checking stored credentials
    # first would keep a device on its previous workspace whenever the old
    # token still validates — silently ignoring the flag, which is the failure
    # mode it exists to prevent.
    if token is not None:
        # The stored credentials are read for the backend URL only, never for
        # the token: --token swaps the credential, it does not move the device
        # to a different backend.
        stored = load_credentials()
        try:
            _authenticate_with_api_token(
                token,
                skip_confirm=skip_confirm,
                source="--token",
                base_url=getattr(stored, "cyberwave_base_url", None),
            )
            return True
        except WorkspaceConflictError as exc:
            console.print(f"[red]{exc}[/red]")
            return False
        except Exception as exc:
            # Deliberately no fallback: prompting here would pair the device
            # against whatever account is typed, which is not what --token asked
            # for, and on an unattended install there is nobody to type it.
            console.print(f"[red]The API token passed with --token was rejected:[/red] {exc}")
            console.print(
                "[dim]Nothing was installed. Check the token, or drop --token to log in "
                "interactively.[/dim]"
            )
            return False

    creds = load_credentials()
    if creds and creds.token:
        try:
            creds_base_url = creds.cyberwave_base_url
            sdk_client = _get_sdk_client(creds.token, base_url=creds_base_url)
            with console.status("[dim]Checking existing credentials...[/dim]"):
                sdk_client.workspaces.list()
            # API tokens carry no email, so a device paired with --token or
            # CYBERWAVE_API_KEY would otherwise report "Logged in as None".
            identity = creds.email or creds.workspace_name or "this device's API token"
            console.print(f"[green]✓[/green] Logged in as [bold]{identity}[/bold]")
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
            _authenticate_with_api_token(
                env_token, skip_confirm=skip_confirm, source="CYBERWAVE_API_KEY"
            )
            return True
        except WorkspaceConflictError as e:
            # Not a credentials problem, so prompting for a password below would
            # only pair the device against a workspace the operator ruled out.
            console.print(f"[red]{e}[/red]")
            return False
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


class WorkspaceConflictError(RuntimeError):
    """The requested workspace contradicts the one the API token is bound to.

    Distinct from a bad token: no other credential can resolve it, so callers
    must abort rather than fall through to an interactive login.
    """


@dataclass
class _TokenWorkspace:
    """Minimal workspace stand-in built from an API token's own context."""

    uuid: str
    name: str
    slug: str = ""


def _api_token_workspace(token: str, *, base_url: str | None = None) -> _TokenWorkspace | None:
    """Return the workspace an API token is bound to, or ``None``.

    API tokens are minted for exactly one workspace and the backend scopes
    every list endpoint to it, but ``workspaces.list()`` still returns every
    workspace the user can reach. Picking from that list (``workspaces[0]``,
    or a slug that names a different workspace) yields a workspace the token
    cannot see any environments in, so the install reports "no environments"
    even though the token works fine. Ask the backend which workspace the
    token is actually bound to instead of guessing.

    Returns ``None`` for tokens without workspace context, which the backend
    signals with 401/403 (Knox session tokens land here), so callers can fall
    back to selecting from the list. Every other failure — a 5xx, a backend too
    old to expose the endpoint, a dropped connection — is reported first,
    because falling back silently means guessing again.
    """
    from .auth import AuthClient, AuthenticationError

    def _report(exc: Exception) -> None:
        console.print(
            f"[yellow]Could not resolve the workspace bound to this token:[/yellow] {exc}"
        )
        console.print("[dim]Falling back to workspace selection.[/dim]")

    try:
        with AuthClient(base_url=base_url) as auth_client:
            context = auth_client.get_api_token_context(token)
    except AuthenticationError as exc:
        # Only 401/403 mean "this token has no workspace context". AuthClient
        # funnels transport errors and every non-2xx into this same type, so
        # without the status check a backend hiccup silently reverts to the
        # guessing this function exists to prevent.
        if getattr(exc, "status_code", None) not in (401, 403):
            _report(exc)
        return None
    except Exception as exc:
        _report(exc)
        return None

    workspace_uuid = str(getattr(context, "workspace_uuid", "") or "").strip()
    if not workspace_uuid:
        return None
    return _TokenWorkspace(
        uuid=workspace_uuid,
        name=str(getattr(context, "workspace_name", "") or "") or workspace_uuid,
    )


def _persist_corrected_workspace(creds: Any, workspace: Any) -> Any:
    """Rewrite the stored workspace so later runs stop using the stale one.

    ``creds.workspace_uuid`` is not only used to pick a workspace: it also
    filters which edge registrations the cleanup path deletes, so leaving a
    mismatched value on disk keeps that path broken.
    """
    from dataclasses import replace

    corrected = replace(
        creds,
        workspace_uuid=str(getattr(workspace, "uuid", "") or ""),
        workspace_name=str(getattr(workspace, "name", "") or ""),
    )
    try:
        save_credentials(corrected)
    except Exception as exc:
        console.print(f"[yellow]Could not update stored workspace:[/yellow] {exc}")
        return creds
    return corrected


def _repair_workspace_from_token(
    creds: Any, *, base_url: str | None
) -> tuple[Any, _TokenWorkspace | None, bool]:
    """Fill or correct the stored workspace from the one the token is bound to.

    Both halves of the backend edge cleanup are scoped by
    ``creds.workspace_uuid``, and they disagree about an empty one:
    ``_release_twins_for_fingerprint`` skips entirely, while
    ``_delete_registered_edges_for_fingerprint`` falls back to matching the
    fingerprint alone and deletes the row anyway. ``cyberwave configure
    --token`` persists exactly that on a fresh host, so an unset value has to be
    filled here -- not only a mismatched one -- or the cleanup deletes the edge
    row and releases nothing.

    Returns:
        (creds, token_workspace, overrode_stored_value). The flag is True only
        when a stored value was replaced, which the caller has been warned
        about; filling an unset one is not worth announcing.
    """
    token = str(getattr(creds, "token", "") or "")
    if not token:
        return creds, None, False

    token_workspace = _api_token_workspace(token, base_url=base_url)
    if token_workspace is None:
        return creds, None, False

    stored_uuid = str(getattr(creds, "workspace_uuid", "") or "").strip().lower()
    if not stored_uuid:
        return _persist_corrected_workspace(creds, token_workspace), token_workspace, False

    if stored_uuid != token_workspace.uuid.lower():
        console.print(
            f"[yellow]Stored workspace does not match the API token; using "
            f"'{token_workspace.name}' from the token instead.[/yellow]"
        )
        return _persist_corrected_workspace(creds, token_workspace), token_workspace, True

    return creds, token_workspace, False


def _select_workspace_from_env_or_default(
    client: Any,
    *,
    skip_confirm: bool,
    token: str | None = None,
    base_url: str | None = None,
) -> Any:
    """Pick a workspace, honoring ``CYBERWAVE_WORKSPACE_SLUG`` when provided.

    When ``token`` is an API token the workspace it is bound to always wins:
    it is the only workspace whose environments and twins the token can list.
    """
    workspaces = client.workspaces.list()
    if not workspaces:
        raise RuntimeError("No workspaces available for this account.")

    token_workspace = _api_token_workspace(token, base_url=base_url) if token else None
    workspace_slug = os.getenv("CYBERWAVE_WORKSPACE_SLUG", "").strip().lower()
    requested = None
    if workspace_slug:
        requested = next(
            (
                workspace
                for workspace in workspaces
                if str(getattr(workspace, "slug", "") or "").strip().lower() == workspace_slug
            ),
            None,
        )
        if requested is None:
            raise RuntimeError(
                f"Workspace slug '{workspace_slug}' was not found for the provided token."
            )

    if token_workspace is not None:
        # Prefer the full workspace object from the listing so callers keep
        # access to slug/metadata, falling back to the token context itself
        # when the listing does not include it.
        bound = next(
            (
                workspace
                for workspace in workspaces
                if str(getattr(workspace, "uuid", "") or "").strip().lower()
                == token_workspace.uuid.lower()
            ),
            token_workspace,
        )
        # Compare UUIDs rather than slugs: the token context carries no slug, so
        # a slug-to-slug check would silently pass whenever the bound workspace
        # is missing from the listing.
        if requested is not None and str(requested.uuid).strip().lower() != (
            token_workspace.uuid.lower()
        ):
            raise WorkspaceConflictError(
                f"CYBERWAVE_WORKSPACE_SLUG is '{workspace_slug}' but the provided API "
                f"token is bound to workspace '{bound.name}'. Create a token for "
                f"'{workspace_slug}' or drop CYBERWAVE_WORKSPACE_SLUG."
            )
        console.print(f"[green]Workspace:[/green] {bound.name} [dim](from API token)[/dim]")
        return bound

    if requested is not None:
        console.print(f"[green]Workspace:[/green] {requested.name}")
        return requested

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


def _select_workspace(
    client: Any,
    *,
    skip_confirm: bool,
    token: str | None = None,
    base_url: str | None = None,
) -> Any:
    """Get workspaces via SDK and let user select one."""
    return _select_workspace_from_env_or_default(
        client, skip_confirm=skip_confirm, token=token, base_url=base_url
    )


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
    listing_error: Exception | None = None
    try:
        all_environments = client.environments.list()
    except Exception as exc:
        # Never fail silently here: an empty list is indistinguishable from
        # "this workspace has no environments" and sends the caller straight
        # into the create-a-new-one branch.
        console.print(f"[yellow]Could not list environments:[/yellow] {exc}")
        listing_error = exc
        all_environments = []

    dropped_workspaces: set[str] = set()
    for env in all_environments:
        env_ws = _environment_workspace_uuid(env)
        # Include the environment when it belongs to the selected workspace OR
        # when its workspace could not be resolved (standalone environments
        # without a _workspace_uuid setting). The backend already restricts
        # the listing to environments the user has access to, so unresolved
        # environments are safe to show.
        if env_ws and env_ws != workspace_uuid:
            dropped_workspaces.add(env_ws)
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

    if not environments and dropped_workspaces:
        # The backend returned environments the caller can see, but all of them
        # belong to other workspaces. Say so instead of implying the account
        # has no environments at all.
        console.print(
            f"[yellow]The API returned {len(all_environments)} environment(s), but none "
            f"belong to workspace {workspace_uuid}.[/yellow]"
        )
        seen = ", ".join(sorted(dropped_workspaces))
        console.print(f"[dim]Workspaces seen instead: {seen}[/dim]")

    if not environments and listing_error is not None:
        # The project-scoped pass above found nothing either, so we genuinely do
        # not know whether this workspace is empty. Returning [] here is what
        # makes the caller create a stray environment on a failed API call.
        raise RuntimeError(
            f"Could not determine the environments in this workspace: {listing_error}. "
            "Re-run the install once the API is reachable."
        )

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


def _looks_like_uuid(value: str) -> bool:
    """Return ``True`` when *value* parses as a UUID."""
    from uuid import UUID

    try:
        UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


def normalize_environment_reference(reference: str) -> str:
    """Validate an ``-e/--environment`` reference and return it stripped.

    Format-only, so the CLI can reject a typo before installing anything. An
    empty value raises rather than reading as "no ``-e`` given", which would
    silently fall back to guessing an environment.
    """
    ref = reference.strip()
    if not ref:
        raise ValueError(
            "--environment was given an empty value. Pass an environment UUID or full "
            "slug, or omit the flag to select one interactively."
        )

    # The middle segment must be `envs`: the three-part shape is shared with
    # twins and workflows, so a URL pasted from the wrong page looks valid.
    slug_parts = ref.split("/")
    if not _looks_like_uuid(ref) and not (
        len(slug_parts) == 3 and all(slug_parts) and slug_parts[1] == "envs"
    ):
        raise ValueError(
            f"'{ref}' is not a valid environment reference. Pass the environment UUID, or "
            "its full slug as '<workspace>/envs/<environment>' "
            "(for example 'acme/envs/production-floor') — the environment name on its own "
            "is not enough."
        )
    return ref


def _resolve_requested_environment(client: Any, workspace_uuid: str, reference: str) -> Any:
    """Resolve an explicit ``-e/--environment`` reference.

    Accepts a UUID or a full unified slug (``acme/envs/production-floor``).
    Never creates: an unresolvable reference is a typo, and silently creating an
    environment named after one is far worse than refusing to continue.
    """
    ref = normalize_environment_reference(reference)

    try:
        environment = client.environments.get(ref)
    except Exception as exc:
        raise RuntimeError(
            f"Environment '{ref}' could not be loaded: {exc}. Check that it exists and that "
            "this device's API token belongs to the same workspace."
        ) from exc
    if environment is None:
        raise RuntimeError(
            f"Environment '{ref}' was not found. Check that it exists and that this device's "
            "API token belongs to the same workspace."
        )

    # A share link can make an environment readable from outside the token's
    # workspace; pairing into it would split the edge from its twins.
    environment_workspace = _environment_workspace_uuid(environment)
    if environment_workspace and environment_workspace.lower() != workspace_uuid.strip().lower():
        raise RuntimeError(
            f"Environment '{getattr(environment, 'name', ref)}' belongs to workspace "
            f"{environment_workspace}, not {workspace_uuid}. Use an environment from this "
            "device's workspace, or run 'cyberwave login' again to switch workspace."
        )

    console.print(
        f"[green]Environment:[/green] {getattr(environment, 'name', ref)} "
        "[dim](from --environment)[/dim]"
    )
    return environment


_PREFLIGHT_ABORT_HINT = (
    "[dim]Nothing was installed. Check that it exists and that this device's "
    "API token belongs to the same workspace.[/dim]"
)


def _preflight_environment_reference(reference: str, *, token: str | None = None) -> bool:
    """Confirm ``-e`` resolves before anything is installed.

    ``configure_edge_environment`` is the real check, but it runs post-install —
    too late to save the apt-get. The workspace is compared explicitly here:
    public, share-linked and directly-granted environments are readable across
    workspaces, so a successful lookup is not proof of the right one.

    An explicit ``--token`` wins over stored credentials: it is the credential
    the install will actually use, and on a never-paired device it is the only
    one there is — which is what lets the check run on a first-ever pair.
    Without one, the comparison falls back to the stored workspace, which is the
    one ``configure_edge_environment`` will pick.

    Only a definite answer aborts. A stale token, an unreachable backend, or a
    stored workspace this account can no longer see say nothing about the
    environment, and ``setup_service`` repairs them.
    """
    ref = normalize_environment_reference(reference)

    creds = load_credentials()
    if token:
        probe_token = token
        # Same backend the install itself will use: the env override, else the
        # one this device is already paired against.
        probe_base_url = _resolve_base_url(
            collect_runtime_env_overrides(), getattr(creds, "cyberwave_base_url", None)
        )
    elif creds and creds.token:
        probe_token = creds.token
        probe_base_url = creds.cyberwave_base_url
    else:
        # No credentials yet: login happens inside setup_service, so let it proceed.
        return True

    try:
        client = _get_sdk_client(probe_token, base_url=probe_base_url)
        # Probe the token first, the way _ensure_credentials does. Without this a
        # stale token reads as "bad environment" and blocks an install that would
        # have succeeded after the login inside setup_service.
        client.workspaces.list()
    except Exception as exc:
        console.print(f"[dim]Skipping the environment pre-check: {exc}[/dim]")
        return True

    try:
        environment = client.environments.get(ref)
    except Exception as exc:
        console.print(f"[red]Environment '{ref}' could not be loaded:[/red] {exc}")
        console.print(_PREFLIGHT_ABORT_HINT)
        return False

    if environment is None:
        console.print(f"[red]Environment '{ref}' was not found.[/red]")
        console.print(_PREFLIGHT_ABORT_HINT)
        return False

    token_workspace = _api_token_workspace(probe_token, base_url=probe_base_url)
    if token_workspace is None and not token:
        # A session token has no bound workspace, so configure_edge_environment
        # uses the stored one. Only compare while it is still listed: when it is
        # stale that check falls through to selection and may land elsewhere.
        stored_uuid = str(getattr(creds, "workspace_uuid", "") or "").strip()
        if stored_uuid and _resolve_workspace_from_credentials(client, stored_uuid) is not None:
            token_workspace = _TokenWorkspace(uuid=stored_uuid, name=stored_uuid)

    environment_workspace = _environment_workspace_uuid(environment)
    if (
        token_workspace is not None
        and environment_workspace
        and environment_workspace.lower() != token_workspace.uuid.strip().lower()
    ):
        console.print(
            f"[red]Environment '{getattr(environment, 'name', ref)}' belongs to workspace "
            f"{environment_workspace}, not {token_workspace.uuid}.[/red]"
        )
        switch_hint = "pass a token for that workspace" if token else "run 'cyberwave login' again"
        console.print(
            "[dim]Nothing was installed. Use an environment from this device's workspace, "
            f"or {switch_hint} to switch workspace.[/dim]"
        )
        return False

    return True


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
            # ``get_raw`` returns the generated ``TwinSchema``, whose
            # ``to_dict()`` carries every API field -- including ``metadata``,
            # which is where ``serial_number`` lives.  ``twins.get()`` returns
            # the rich ``Twin`` handle instead, and that class defines no
            # ``to_dict``: the old ``hasattr`` ternary therefore always took
            # the ``else`` branch and wrote a ``{uuid, name}`` stub.  The
            # failure was invisible because ``twin_data["asset"]`` below
            # refilled the file to ~10KB, but every twin-level field was gone,
            # so ``_load_twin_camera_serials`` never saw a pinned serial and
            # the camera auto-bind prompted for every twin.  No fallback here:
            # a missing ``to_dict`` must raise rather than silently truncate.
            twin = client.twins.get_raw(twin_uuid)
            twin_data: dict[str, Any] = twin.to_dict()

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


def _edge_uuid_for_fingerprint(client: Any, fingerprint: str, workspace_uuid: str) -> str | None:
    """The edge row for this host in this workspace, or ``None``.

    Both halves of the match matter: a host re-paired elsewhere must not get its
    previous workspace's bindings cleared, and ``get_twins`` on a workspace-less
    edge spans every workspace.
    """
    if not fingerprint or not workspace_uuid:
        return None

    for edge in client.edges.list():
        if str(getattr(edge, "fingerprint", "") or "") != fingerprint:
            continue
        if str(getattr(edge, "workspace_uuid", "") or "") != workspace_uuid:
            continue
        return str(getattr(edge, "uuid", "") or "") or None
    return None


def _release_twins_bound_to_edge(
    client: Any, edge_uuid: str, keep_twin_uuids: list[str] | None = None
) -> tuple[int, int]:
    """Unpair every twin bound to *edge_uuid* except those in *keep_twin_uuids*.

    ``DELETE /edges/{uuid}/twins/{twin_uuid}`` clears the canonical key and the
    legacy ``edge_configs`` map, and reports whether a binding was actually
    there — so the count cannot overstate what it removed. Failures are
    counted, never raised.

    Returns:
        (released_count, failed_count)
    """
    keep_set = {str(twin_uuid) for twin_uuid in keep_twin_uuids or [] if twin_uuid}
    released = 0
    failed = 0

    try:
        bound = client.edges.get_twins(edge_uuid)
    except Exception as exc:
        console.print(f"[yellow]Could not list twins bound to this edge: {exc}[/yellow]")
        return released, 1

    for binding in bound or []:
        if not isinstance(binding, dict):
            continue
        twin_uuid = str(binding.get("twin_uuid") or "")
        if not twin_uuid or twin_uuid in keep_set:
            continue

        try:
            result = client.edges.unpair_twin(edge_uuid, twin_uuid)
            if isinstance(result, dict) and result.get("success"):
                released += 1
        except Exception as exc:
            console.print(
                f"[yellow]Failed to release twin {twin_uuid[:8]}… from this edge: {exc}[/yellow]"
            )
            failed += 1

    return released, failed


def _detach_edge_fingerprint_from_other_twins(
    client: Any,
    workspace_uuid: str,
    keep_twin_uuids: list[str],
    edge_fingerprint: str,
) -> tuple[int, int]:
    """Release twins bound to this edge that were not selected for it.

    ``GET /edges/{uuid}/twins`` runs the same workspace-wide fingerprint match
    as discovery; sweeping a single environment missed twins bound by earlier
    pairings. Failures are counted, never raised.

    Returns:
        (detached_count, failed_count)
    """
    try:
        edge_uuid = _edge_uuid_for_fingerprint(client, edge_fingerprint, workspace_uuid)
    except Exception as exc:
        console.print(f"[yellow]Could not resolve this edge to clear stale twins: {exc}[/yellow]")
        return 0, 1

    # No edge row -> nothing this sweep can resolve. Both paths that delete the
    # row (the disconnect above, `edge uninstall`) release the bindings first, so
    # an absent row should mean an empty binding set. Say so rather than
    # returning a silent zero: a row deleted some other way strands twins this
    # sweep can no longer reach.
    if edge_uuid is None:
        console.print(
            "[dim]No edge registration for this host; skipping the stale-binding sweep.[/dim]"
        )
        return 0, 0

    return _release_twins_bound_to_edge(client, edge_uuid, keep_twin_uuids)


# Belt and braces only. The real test is `_is_twin_object_file`; keeping these
# names listed means a config file that somehow *is* UUID-shaped still survives.
_NON_TWIN_JSON_FILES = frozenset(
    {
        "credentials.json",
        "environment.json",
        "cameras.json",
        "fingerprint.json",
        "edge.json",
        # Spelled out rather than imported from `commands.compute`, which would
        # pull `click` into this module's import graph.
        "instance_identity.json",
        AUDIO_STREAMS_FILENAME,
        CAMERA_STREAMS_FILENAME,
        SERIAL_BRIDGES_FILENAME,
    }
)


def _is_twin_object_file(path: Path) -> bool:
    """True when *path* is a cached twin object file (``<twin_uuid>.json``).

    Identified by **shape**, not by excluding the config names we happen to know.
    The exclusion list alone failed open: any file not named in it was a deletion
    candidate, so every config file added later inherited the bug.

    ``serial_bridges.json`` is how that bit. ``setup_serial_bridges`` writes it
    early in ``cyberwave edge install``; ``configure_edge_environment`` runs at
    the *end* of the same install and swept it up as a twin file, so the
    installer deleted its own output. Edge-core then saw no serial bridge, put
    the macOS SO101 arms on USB/IP — which cannot carry their serial data,
    because AppleUSBACM holds the CDC interface and Darwin has no
    ``libusb_detach_kernel_driver`` — and the arms never appeared.

    ``cyberwave_edge_core.startup`` already identifies these files this way.
    """
    if path.name in _NON_TWIN_JSON_FILES:
        return False
    try:
        uuid.UUID(path.stem)
    except ValueError:
        return False
    return True


def _detect_existing_edge_configuration() -> tuple[str | None, list[Path]]:
    """Check CONFIG_DIR for an existing edge environment configuration.

    Returns:
        (environment_name_or_uuid, list_of_twin_json_paths)
        *environment_name_or_uuid* is ``None`` when no prior configuration
        is detected.
    """
    twin_json_files = [p for p in sorted(CONFIG_DIR.glob("*.json")) if _is_twin_object_file(p)]
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


def _release_twins_for_fingerprint(
    *,
    fingerprint: str,
    token: str,
    base_url: str | None,
    workspace_uuid: str | None,
) -> tuple[int, int]:
    """Unpair the twins bound to this host's edge row. Never raises.

    Returns:
        (released_count, failed_count)
    """
    # No workspace means we cannot tell this host's edge row from a same-named
    # one elsewhere; skipping is the safe half of that trade.
    if not workspace_uuid:
        return 0, 0

    try:
        client = _get_sdk_client(token, base_url=base_url or None)
        edge_uuid = _edge_uuid_for_fingerprint(client, fingerprint, workspace_uuid)
    except Exception as exc:
        console.print(f"[yellow]Could not resolve this edge to release its twins: {exc}[/yellow]")
        return 0, 1

    if edge_uuid is None:
        return 0, 0

    return _release_twins_bound_to_edge(client, edge_uuid)


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

        # Deleting the edge row leaves twin metadata untouched, so release the
        # bindings while the row still exists — otherwise nothing ever clears
        # them and the next discovery re-serves the same stale twins.
        released, release_failed = _release_twins_for_fingerprint(
            fingerprint=edge_fingerprint,
            token=token,
            base_url=base_url,
            workspace_uuid=workspace_uuid,
        )
        if released:
            console.print(f"[dim]Released twins bound to this edge: {released}[/dim]")
        if release_failed:
            console.print(f"[yellow]Failed to release {release_failed} twin binding(s).[/yellow]")

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


def configure_edge_environment(
    *, skip_confirm: bool = False, environment: str | None = None
) -> bool:
    """Select workspace + environment and save ``environment.json`` in ``CONFIG_DIR``.

    When *environment* is set (``-e/--environment``) that environment is used
    verbatim instead of prompting or defaulting to the most recent one.
    """
    from .auth import AuthenticationError

    creds = load_credentials()
    if not creds or not creds.token:
        console.print("[red]No credentials found.[/red]")
        console.print("[dim]Run 'cyberwave login' first.[/dim]")
        return False

    creds_base_url = creds.cyberwave_base_url
    # The token's own workspace wins over the stored one: it is the only
    # workspace whose environments the token can list, and an install that
    # persisted a different workspace would otherwise keep reporting "no
    # environments" forever.
    #
    # Resolve and repair it *before* the cleanup below. `creds.workspace_uuid`
    # filters which edge registrations the cleanup deletes, so running it
    # against a stale or unset value strands this device's registrations in the
    # backend. `edge uninstall` shares the helper for the same reason.
    creds, token_workspace, workspace_corrected = _repair_workspace_from_token(
        creds, base_url=creds_base_url
    )

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
        client = _get_sdk_client(creds.token, base_url=creds_base_url)

        workspace = None
        if token_workspace is not None:
            workspace = (
                _resolve_workspace_from_credentials(client, token_workspace.uuid) or token_workspace
            )
            if not workspace_corrected:
                console.print(f"[green]Workspace:[/green] {workspace.name}")
            # An explicit slug that the token cannot honour is worth saying out
            # loud. Warn rather than abort: devices already paired against a
            # stale slug must keep working.
            requested_slug = os.getenv("CYBERWAVE_WORKSPACE_SLUG", "").strip().lower()
            workspace_slug = str(getattr(workspace, "slug", "") or "").strip().lower()
            if requested_slug and workspace_slug and requested_slug != workspace_slug:
                console.print(
                    f"[yellow]Ignoring CYBERWAVE_WORKSPACE_SLUG='{requested_slug}': the API "
                    f"token is bound to '{workspace.name}'.[/yellow]"
                )
        elif creds.workspace_uuid:
            workspace = _resolve_workspace_from_credentials(client, creds.workspace_uuid)
            if workspace:
                console.print(f"[green]Using workspace from credentials:[/green] {workspace.name}")

        if workspace is None:
            if creds.workspace_uuid:
                console.print(
                    "[yellow]Stored workspace is not available for this account. "
                    "Select a workspace.[/yellow]"
                )
            # No token/base_url here: reaching this branch means the token has no
            # workspace context, so re-resolving it would only repeat a failed call.
            workspace = _select_workspace(client, skip_confirm=skip_confirm)

        # `is not None`, not truthiness: an empty --environment is a mistake
        # (unset shell variable), not a request to guess one.
        if environment is not None:
            selected_environment = _resolve_requested_environment(
                client, str(workspace.uuid), environment
            )
        else:
            selected_environment = _select_or_create_environment(
                client,
                str(workspace.uuid),
                skip_confirm=skip_confirm,
            )

        env_uuid = str(getattr(selected_environment, "uuid", ""))
        env_name = str(getattr(selected_environment, "name", ""))
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
            str(workspace.uuid),
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
    if normalized_channel == "pr":
        return PR_DEB_REGISTRY_SLUG
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
# Depth sensors appear under two vocabularies: ``asset.universal_schema``
# types them ``depth_camera``, while ``capabilities`` types them ``depth``
# with ``depth_camera`` as the sensor *id*. Both are matched so the check
# holds whichever shape a twin carries.
_DEPTH_SENSOR_TYPES = {"depth", "depth_camera"}
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
        if not _is_twin_object_file(path):
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


def _load_twin_camera_serials() -> dict[str, str]:
    """Return ``{twin_uuid: serial_number}`` for cached twins that set one.

    A camera ``serial_number`` is either a raw hardware serial (RealSense, UVC),
    which the driver resolves itself, or a by-id name left by an older
    installer. Either way the twin has already answered "which physical camera".
    Twins without the field are absent, so callers can tell unpinned from pinned.
    """
    serials: dict[str, str] = {}
    for path in sorted(CONFIG_DIR.glob("*.json")):
        if not _is_twin_object_file(path):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            continue
        serial = metadata.get("serial_number")
        if not isinstance(serial, str) or not serial.strip():
            continue
        twin_uuid = str(data.get("uuid") or path.stem)
        if twin_uuid:
            serials[twin_uuid] = serial.strip()
    return serials


# udev's stable-name trees. Named as in the camera driver's ``main.py`` so the
# two stay recognisably the same contract; overridden in tests to point at a
# fixture directory.
_V4L_BY_ID_DIR = "/dev/v4l/by-id"
_V4L_BY_PATH_DIR = "/dev/v4l/by-path"
_CAMERA_STREAM_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")
# Marks a ``twin_to_device`` value as hardware identity rather than a video
# source. Kept in sync with edge-core's ``_SERIAL_SOURCE_PREFIX``.
_SERIAL_PIN_PREFIX = "serial:"
_ALSA_BY_ID_DIR = "/dev/snd/by-id"
# A V4L by-id link ends with the node index within one physical device.
_V4L_NODE_RE = re.compile(r"-video-index(\d+)$")
# ID_BUS prefixes udev puts on by-id names (same set as edge-core); tells a
# stable name from a raw hardware serial.
_UDEV_NAME_PREFIXES = ("usb-", "pci-", "platform-")


# ---- sensor-agnostic twin <-> device matching ---------------------------------


class _Inference(NamedTuple):
    assigned: dict[str, tuple[Any, str]]  # twin -> (device, why it is certain)
    unresolved: list[str]


def _ambiguous_groups(units: Iterable[tuple[Hashable, bool]]) -> set[Hashable]:
    """Group keys whose identical units cannot be told apart by their stable id.

    *units* are ``(group key, has its own stable link)``. Identical units without
    distinct ids collapse onto fewer links, which then identify none of them:
    some-but-not-all linked means the whole group is ambiguous. No links at all
    says nothing (no udev tree), and all linked is fine.
    """
    groups: dict[Hashable, list[bool]] = {}
    for key, linked in units:
        groups.setdefault(key, []).append(linked)
    return {k for k, v in groups.items() if len(v) > 1 and any(v) and not all(v)}


def _infer_associations(
    twins: list[str],
    devices: list[Any],
    *,
    claimed: set[Any],
    compatible: Callable[[str, Any], bool],
    limit: int = 20000,
) -> _Inference:
    """Pair twins with devices wherever the evidence leaves exactly one answer.

    *claimed* are devices bound by another twin's verified pin (one sensor serves
    one twin); *compatible* rules out impossible pairings (e.g. a microphone twin
    with a playback-only card). Every full assignment is enumerated and a twin is
    decided only when all agree on its device. With more twins than devices
    nothing is decided: sharing is the operator's call. Gives up (decides
    nothing) past *limit* assignments or as soon as no twin can be decided.
    """
    free = [d for d in devices if d not in claimed]
    options = {t: [d for d in free if compatible(t, d)] for t in twins}
    images: dict[str, set[Any]] = {t: set() for t in twins}
    order = sorted(twins, key=lambda t: len(options[t]))
    found = 0
    stop = False

    def walk(i: int, used: set[Any], current: dict[str, Any]) -> None:
        nonlocal found, stop
        if stop:
            return
        if i == len(order):
            found += 1
            for twin, device in current.items():
                images[twin].add(device)
            stop = found > limit or all(len(v) > 1 for v in images.values())
            return
        twin = order[i]
        for device in options[twin]:
            if device not in used:
                used.add(device)
                current[twin] = device
                walk(i + 1, used, current)
                used.discard(device)
                current.pop(twin, None)

    if twins:
        walk(0, set(), {})
    if not twins or found == 0 or stop:
        return _Inference({}, list(twins))

    assigned: dict[str, tuple[Any, str]] = {}
    for twin in twins:
        if len(images[twin]) != 1:
            continue
        (device,) = images[twin]
        only = sum(1 for d in devices if compatible(twin, d)) == 1
        assigned[twin] = (
            device,
            "the only attached device it can use"
            if only
            else "the only device left once the other twins' verified bindings "
            "and sensor types are accounted for",
        )
    return _Inference(assigned, [t for t in twins if t not in assigned])


# ---- udev by-id helpers -------------------------------------------------------


class _UdevDevice(NamedTuple):
    name: str
    target: str  # resolved node, e.g. /dev/video0


def _looks_like_udev_name(value: str) -> bool:
    return value.strip().startswith(_UDEV_NAME_PREFIXES)


def _ask_yes_no(prompt: str, *, default: bool = True, on_eof: bool = True) -> bool:
    """Confirm with a fallback for non-interactive stdin (EOF)."""
    try:
        return bool(Confirm.ask(prompt, default=default))
    except EOFError:
        return on_eof


def _list_udev_by_id(directory: str) -> list[_UdevDevice]:
    """Live (non-dangling) entries of a udev by-id tree; udev removes the link on unplug."""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    found = [_UdevDevice(n, os.path.realpath(os.path.join(directory, n))) for n in names]
    return [d for d in found if os.path.exists(d.target)]


def _list_v4l_by_id() -> list[_UdevDevice]:
    return _list_udev_by_id(_V4L_BY_ID_DIR)


def _list_alsa_by_id() -> list[_UdevDevice]:
    return _list_udev_by_id(_ALSA_BY_ID_DIR)


def _udev_pin_name(value: str, directory: str) -> str | None:
    """Bare link name of a udev pin (name or full path under *directory*), else None.

    A raw hardware serial is not a udev pin, so it yields None here.
    """
    candidate = value.strip()
    if candidate.startswith(directory.rstrip("/") + "/"):
        candidate = candidate[len(directory.rstrip("/")) + 1 :]
    if not candidate or "/" in candidate or not _looks_like_udev_name(candidate):
        return None
    return candidate


def _udev_pin_is_connected(value: str, directory: str) -> bool:
    name = _udev_pin_name(value, directory)
    return name is not None and os.path.exists(os.path.realpath(os.path.join(directory, name)))


def _stable_camera_device_prefixes() -> tuple[str, ...]:
    """Path prefixes that name one physical camera rather than a position.

    Mirrors the camera driver's ``_is_stable_device_identifier``: the by-id and
    by-path trees embed serial or USB topology, so they survive replug, while
    ``/dev/videoN`` and a bare index mean "whatever enumerated there this boot".
    """
    return (f"{_V4L_BY_ID_DIR}/", f"{_V4L_BY_PATH_DIR}/")


def _is_camera_stream_url(value: str) -> bool:
    """True when *value* is a network stream rather than a local device."""
    return value.strip().lower().startswith(_CAMERA_STREAM_SCHEMES)


def _is_local_stable_camera_device(value: str) -> bool:
    """True when *value* is a udev stable name for a camera on *this* host.

    Only these resolve to a ``/dev/video*`` node, so only these can be matched
    against the discovery pass. Anything else stable (an RTSP/UDP URL) names a
    source that needs no local device at all.
    """
    candidate = value.strip()
    return candidate.startswith(_stable_camera_device_prefixes()) and (
        ".." not in candidate.split("/")
    )


def _is_stable_camera_device(value: str) -> bool:
    """True when *value* names one specific camera rather than a position.

    Mirrors the camera driver's ``_is_stable_device_identifier`` exactly, so a
    value this function accepts is one the driver will also refuse to
    substitute away from.  The driver's rule, restated: a bare index or a
    ``/dev/...`` node is positional; a ``by-id`` / ``by-path`` entry or any
    other explicit source (URL) names one device.

    Matching the driver matters in both directions.  Too narrow -- the previous
    by-id/by-path/RTSP allow-list -- and a ``udp://`` pin reads as unpinned
    here, so the installer offers it up for a learned serial that then
    overrides it downstream.  Too wide and we would skip a prompt the driver
    still needs answered.

    Cameras without a serial in their USB descriptor (the Logitech C920 reports
    ``iSerial 0``) can never be pinned by ``metadata.serial_number``, but udev
    still publishes a stable name for them under ``/dev/v4l/by-id``. Treating
    those as identity is what lets such a twin skip the mapping prompt.
    """
    candidate = value.strip()
    if not candidate:
        return False
    if _is_local_stable_camera_device(candidate):
        return True
    try:
        int(candidate)
    except ValueError:
        pass
    else:
        # A bare index is what ``_parse_camera_id`` turns into an OpenCV
        # device number -- "whatever enumerated there", not a device.
        return False
    return not candidate.startswith("/dev/")


def _load_twin_camera_devices() -> dict[str, str]:
    """Return ``{twin_uuid: video_device}`` for cached twins pinning a stable one.

    The companion to :func:`_load_twin_camera_serials` for hardware that has no
    serial to pin. Only stable values are returned: a twin whose
    ``metadata.video_device`` is an index or a ``/dev/videoN`` path has not
    answered "which physical camera", so it must still be asked.
    """
    devices: dict[str, str] = {}
    for path in sorted(CONFIG_DIR.glob("*.json")):
        if not _is_twin_object_file(path):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            continue
        device = metadata.get("video_device")
        if not isinstance(device, str) or not _is_stable_camera_device(device):
            continue
        twin_uuid = str(data.get("uuid") or path.stem)
        if twin_uuid:
            devices[twin_uuid] = device.strip()
    return devices


class _CameraPin(NamedTuple):
    """A twin's already-answered "which camera", ready to persist.

    ``device`` is the value written into ``cameras.json``'s ``twin_to_device``,
    and it is not interchangeable with ``index``.  Edge-core renders a stored
    index back as ``/dev/video{N}`` and injects it as
    ``CYBERWAVE_METADATA_VIDEO_DEVICE``; the camera driver's entrypoint then
    skips exporting the twin's own ``metadata.video_device`` because the
    variable is already set.  So persisting the index for a device-pinned twin
    silently downgrades a stable ``by-id`` pin into the positional path the
    driver is allowed to substitute away from -- defeating the pin entirely.

    Storing the stable string instead keeps the identity intact all the way
    into the container.  ``index`` is still carried for the installer's own
    bookkeeping (menu labels, default advancement), which is host-local and
    never persisted.
    """

    label: str
    index: int | None
    device: int | str
    # Set when the saved pin should move to video_device once the operator keeps it.
    migrate: str | None = None


def _camera_device_nodes(cam: Any) -> list[str]:
    """Every ``/dev/video*`` node a discovered camera owns.

    A multi-node device (RealSense exposes depth, colour and IR siblings) is
    matched on any of them, because a ``by-id`` symlink points at the capture
    node, which is not necessarily the camera's ``primary_path``.
    """
    paths = getattr(cam, "paths", None)
    if isinstance(paths, list) and paths:
        return [str(p) for p in paths if p]
    primary = getattr(cam, "primary_path", None)
    return [str(primary)] if primary else []


def _udev_name_for_camera(cam: Any) -> str | None:
    """The by-id name of *cam*: among the links to any of its nodes, the lowest node index."""
    nodes = set(_camera_device_nodes(cam))
    best: tuple[int, str] | None = None
    for device in _list_v4l_by_id():
        if device.target not in nodes:
            continue
        match = _V4L_NODE_RE.search(device.name)
        rank = (int(match.group(1)), device.name) if match else (sys.maxsize, device.name)
        best = min(best, rank) if best else rank
    return best[1] if best else None


def _ambiguous_camera_cards(cameras: list[Any]) -> set[Any]:
    """Models whose identical units udev collapsed onto fewer by-id links."""
    return _ambiguous_groups(
        (str(cam.card or ""), _udev_name_for_camera(cam) is not None) for cam in cameras
    )


def _camera_index_for_device(cameras: list[Any], device: str) -> int | None:
    """Index of the discovered camera *device* resolves to, or ``None``.

    ``by-id`` / ``by-path`` entries are symlinks into ``/dev/videoN``, so the
    link is resolved on this host and the target matched against each camera's
    nodes. Returns ``None`` when the symlink is dangling — the camera it names
    is not attached — so the caller can warn instead of binding a substitute.
    """
    candidate = device.strip()
    if not candidate or _is_camera_stream_url(candidate):
        return None
    try:
        target = os.path.realpath(candidate)
    except OSError:
        return None
    for i, cam in enumerate(cameras):
        if target in _camera_device_nodes(cam):
            index = getattr(cam, "index", None)
            return index if index is not None else i
    return None


def _twin_has_depth_sensor(twin_uuid: str) -> bool:
    """True when the cached twin declares a depth sensor.

    Depth twins are addressed by librealsense, not V4L2, which changes what an
    unmatched serial means -- see :func:`_detect_and_select_cameras`. Reads the
    same cached JSON as :func:`_list_camera_twins`, and checks both sensor
    vocabularies because assets differ in which one they populate.
    """
    path = CONFIG_DIR / f"{twin_uuid}.json"
    if not _is_twin_object_file(path):
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    asset = data.get("asset") or {}
    buckets = (
        (asset.get("universal_schema") or {}).get("sensors"),
        (data.get("capabilities") or {}).get("sensors"),
    )
    for bucket in buckets:
        if not isinstance(bucket, list):
            continue
        for sensor in bucket:
            if not isinstance(sensor, dict):
                continue
            if str(sensor.get("type", "")).strip().lower() in _DEPTH_SENSOR_TYPES:
                return True
    return False


def _any_twin_has_camera_sensor() -> bool:
    """Check downloaded twin JSON files for camera-type sensors."""
    return bool(_list_camera_twins())


def _list_microphone_twins() -> list[tuple[str, str]]:
    """Return ``(twin_uuid, twin_name)`` for each selected twin with a microphone sensor."""
    selected_uuids = _load_selected_twin_uuids()
    results: list[tuple[str, str]] = []
    for path in sorted(CONFIG_DIR.glob("*.json")):
        if not _is_twin_object_file(path):
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
        if not _is_twin_object_file(path):
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


def _camera_identity_hint(cam: Any) -> str:
    """The field that tells this camera apart from an identical sibling.

    Two cameras of the same model render identical ``card`` strings, so the
    menu's only other column -- ``/dev/videoN`` -- is what an operator ends up
    choosing on. That is the one attribute that is *not* identity: it is
    positional and moves on replug and reboot.

    The serial is also the value that goes into ``metadata.serial_number``, so
    showing it here is what lets an operator read a pin off the screen instead
    of going back to the host with ``v4l2-ctl``. A camera that reports no
    serial (the Logitech C920 ships an empty descriptor) falls back to its USB
    port, the only thing distinguishing two identical such cameras -- and a
    hint that this twin needs a ``video_device`` pin rather than a serial.
    """
    udev_name = _udev_name_for_camera(cam)
    if udev_name:
        return f"id {udev_name}"
    serial = (getattr(cam, "serial", None) or "").strip()
    if serial:
        return f"serial {serial}"
    bus_info = (getattr(cam, "bus_info", None) or "").strip()
    if bus_info:
        return f"port {bus_info}"
    return ""


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
    ambiguous = _ambiguous_camera_cards(cameras)
    valid_indices: dict[int, Any] = {}
    for i, cam in enumerate(cameras):
        idx = cam.index if cam.index is not None else i
        valid_indices[idx] = cam
        score = camera_likelihood_score(cam)
        assigned_to = assignments.get(idx)
        suffix = f"  [yellow](assigned to {assigned_to})[/yellow]" if assigned_to else ""
        if str(cam.card or "") in ambiguous:
            suffix += "  [red](identical unit, cannot be pinned)[/red]"
        hint = _camera_identity_hint(cam)
        hint_text = f"  {hint}" if hint else ""
        if score >= 40:
            label = (
                f"  [bold cyan]{idx}[/bold cyan])  {cam.card}  "
                f"[dim]{cam.primary_path}{hint_text}[/dim]{suffix}"
            )
        else:
            label = (
                f"  [dim]{idx})  {cam.card}  {cam.primary_path}{hint_text}  "
                f"(probably not a camera)[/dim]{suffix}"
            )
        console.print(label)
    return valid_indices


def _camera_index_for_serial(cameras: list[Any], serial: str) -> int | None:
    """Index of the discovered camera reporting *serial*, or ``None``.

    A serial reported by V4L2 is compared case-insensitively after stripping. One
    that only appears in a by-id name is matched exactly, as the driver does.
    """
    target = serial.strip().casefold()
    if not target:
        return None
    for i, cam in enumerate(cameras):
        cam_serial = (getattr(cam, "serial", None) or "").strip().casefold()
        if cam_serial and cam_serial == target:
            return cam.index if cam.index is not None else i
    name = _udev_pin_name(serial, _V4L_BY_ID_DIR)
    if name is not None:
        return _camera_index_for_device(cameras, os.path.join(_V4L_BY_ID_DIR, name))
    # Like the driver: a raw serial is the delimited field of a by-id name, index0 first.
    field = re.compile(rf"_{re.escape(serial.strip())}-video-index(\d+)$")
    matches = sorted(
        (int(m.group(1)), d.name)
        for d in _list_v4l_by_id()
        if (m := field.search(d.name))
    )
    for _node, link in matches:
        found = _camera_index_for_device(cameras, os.path.join(_V4L_BY_ID_DIR, link))
        if found is not None:
            return found
    return None


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


def _persist_twin_pins(pins: dict[str, dict[str, str]]) -> int:
    """Write device-identity metadata onto twins, best-effort (never blocks install).

    Cameras and audio alike get their by-id path (``video_device`` / ``audio_device``)
    and a blanked ``serial_number``: a serial outranks the device field in both
    drivers. A serial the operator set is never written here.
    """
    pins = {u: m for u, m in pins.items() if u and m}
    if not pins:
        return 0

    from .credentials import load_credentials
    from .utils import resolve_api_url

    creds = load_credentials()
    if not creds or not creds.token:
        return 0

    try:
        from cyberwave import Cyberwave

        client = Cyberwave(base_url=resolve_api_url(creds=creds), api_key=creds.token)
    except Exception as exc:
        console.print(f"[dim]Could not record device ids: {exc}[/dim]")
        return 0

    updated = 0
    for twin_uuid, metadata in pins.items():
        try:
            client.twins.update(twin_uuid, metadata=metadata)
            updated += 1
        except Exception as exc:
            console.print(
                f"[yellow]Could not pin device on twin {twin_uuid[:8]}…: {exc}. "
                f"The twin keeps its previous pin.[/yellow]"
            )
    if updated:
        console.print(
            f"[dim]Pinned device id on {updated} twin(s) — "
            f"future installs resolve them without prompting.[/dim]"
        )
    return updated


def _camera_pin_metadata(identity: str) -> dict[str, str]:
    """Twin metadata that pins a camera to the identity the installer learned."""
    name = _udev_pin_name(identity, _V4L_BY_ID_DIR)
    if name is not None:
        return {"video_device": f"{_V4L_BY_ID_DIR}/{name}", "serial_number": ""}
    return {"serial_number": identity}


def _detect_and_select_cameras() -> None:
    """Discover cameras and prompt the user to map them to camera-bearing twins.

    When a single camera twin is connected, the user is prompted once (matching
    the historical single-select behaviour).  When multiple camera twins are
    connected alongside multiple physical cameras, the user is walked through a
    per-twin mapping so each twin ends up bound to a specific ``/dev/video*``
    device.

    A twin that already pins ``metadata.serial_number`` is never prompted for:
    the serial names the physical unit, and the discovery pass reports the same
    serial per device, so the binding is computed rather than asked.  Twins
    without one are prompted as before, and their answer is written back as a
    serial so the question is asked at most once per camera.

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
    twin_serials = _load_twin_camera_serials()
    twin_devices = _load_twin_camera_devices()
    had_pin = set(twin_serials) | set(twin_devices)
    default_idx = cameras[0].index if cameras[0].index is not None else 0
    cameras_by_index: dict[int, Any] = {
        (cam.index if cam.index is not None else i): cam for i, cam in enumerate(cameras)
    }
    pins: dict[str, dict[str, str]] = {}
    ambiguous_cards = _ambiguous_camera_cards(cameras)

    def _is_ambiguous(index: int) -> bool:
        cam = cameras_by_index.get(index)
        return cam is not None and str(cam.card or "") in ambiguous_cards

    def _identity_of(index: int) -> str | None:
        """The identity to pin for a menu index, or None.

        Prefers the by-id name (it exists even without a USB serial). A camera
        udev cannot tell from an identical sibling is refused.
        """
        cam = cameras_by_index.get(index)
        if cam is None or _is_ambiguous(index):
            return None
        return _udev_name_for_camera(cam) or (getattr(cam, "serial", None) or "").strip() or None

    def _device_for(index: int) -> int | str:
        """What cameras.json stores for a camera: its by-id path when it has one.

        Edge-core injects this ahead of the twin's own video_device, so an index
        would undo a by-id pin.
        """
        cam = cameras_by_index.get(index)
        name = None if cam is None or _is_ambiguous(index) else _udev_name_for_camera(cam)
        return f"{_V4L_BY_ID_DIR}/{name}" if name else index

    def _explain_unpinnable(index: int) -> None:
        reason = (
            "is identical to another attached unit and udev gives them no distinct id"
            if _is_ambiguous(index)
            else "exposes no stable udev name or serial"
        )
        console.print(
            f"[yellow]This camera {reason}, so it cannot be pinned; "
            f"it will be asked again on the next install.[/yellow]"
        )

    def _record(twin_uuid: str, index: int) -> bool:
        """Queue the pin for *twin_uuid* -> camera *index*; False if it cannot be pinned."""
        identity = _identity_of(index)
        if identity:
            pins[twin_uuid] = _camera_pin_metadata(identity)
        return bool(identity)

    def _record_choice(twin_uuid: str, index: int) -> None:
        """Pin a manual choice. If the camera cannot be pinned, the twin's old pin is
        cleared: the driver honours the backend pin regardless of cameras.json.
        """
        if _record(twin_uuid, index):
            return
        _explain_unpinnable(index)
        if twin_uuid in had_pin:
            console.print(
                "[dim]Clearing the twin's previous camera pin so the new choice "
                "is the one that applies.[/dim]"
            )
            pins[twin_uuid] = {"serial_number": "", "video_device": ""}

    def _warn_unmatched_serial(twin_name: str, serial: str) -> None:
        console.print(
            f"[yellow]{twin_name} pins serial {serial}, which no attached camera "
            f"reports.[/yellow]\n"
            f"[dim]Its driver will refuse to start rather than open a different "
            f"camera. Check the twin's metadata.serial_number.[/dim]"
        )

    def _warn_unverifiable_serial(twin_name: str, serial: str) -> None:
        console.print(
            f"[dim]{twin_name} pins serial {serial}, which this host cannot "
            f"verify — a depth camera's librealsense serial is not visible to "
            f"v4l2. Leaving it to the driver, which will fail rather than open "
            f"a different camera if the value is wrong.[/dim]"
        )

    def _warn_unmatched_device(twin_name: str, device: str) -> None:
        console.print(
            f"[yellow]{twin_name} pins device {device}, which does not resolve on "
            f"this host.[/yellow]\n"
            f"[dim]Its driver will refuse to start rather than open a different "
            f"camera. Check the twin's metadata.video_device.[/dim]"
        )

    pin_cache: dict[str, _CameraPin | None] = {}

    def _pinned_camera(twin_uuid: str, twin_name: str) -> _CameraPin | None:
        """Memoised so a mismatch is warned about once."""
        if twin_uuid not in pin_cache:
            pin_cache[twin_uuid] = _resolve_pin(twin_uuid, twin_name)
        return pin_cache[twin_uuid]

    def _resolve_pin(twin_uuid: str, twin_name: str) -> _CameraPin | None:
        """How a twin that already names its camera should be bound.

        Returns ``None`` when the twin is unpinned, or pinned to hardware this
        host cannot see -- both fall back to asking rather than binding
        whatever else is attached, and the mismatch is warned about here so
        every caller reports it alike.

        Otherwise returns a :class:`_CameraPin`.  Its ``index`` is ``None`` for
        a source with no local node (an RTSP/UDP stream); its ``device`` is
        what gets persisted, and is deliberately *not* always the index --
        see :class:`_CameraPin`.

        ``serial_number`` is checked first: it is the stronger identity, and a
        twin carrying both should not be re-bound by the weaker one.
        """
        serial = twin_serials.get(twin_uuid)
        if serial:
            matched = _camera_index_for_serial(cameras, serial)
            if matched is not None:
                name = _udev_pin_name(serial, _V4L_BY_ID_DIR)
                if name is not None and _looks_like_udev_name(name):
                    # The camera driver cannot resolve a by-id name given as a serial.
                    return _CameraPin(
                        f"serial {serial}", matched, f"{_V4L_BY_ID_DIR}/{name}", migrate=name
                    )
                # The driver resolves the twin's raw serial first, so the index
                # here is never the one it opens.
                return _CameraPin(f"serial {serial}", matched, matched)
            if _twin_has_depth_sensor(twin_uuid) and not _looks_like_udev_name(serial):
                # A depth camera reports two unrelated serials: the USB
                # descriptor one that ``v4l2-ctl`` gives us, and librealsense's
                # own, which nothing on this host can read. So a serial we
                # cannot match here may still be a perfectly good pin, and
                # treating it as wrong would be a false alarm.
                #
                # Prompting is also pointless for these twins: the answer is a
                # ``/dev/videoN`` index, and ``_resolve_camera_source`` ignores
                # it on the depth path because librealsense addresses the
                # device by serial. Defer to the driver, which is the only
                # thing that can actually resolve the value.
                _warn_unverifiable_serial(twin_name, serial)
                # Tagged rather than stored bare: a librealsense serial is an
                # all-digit string, so edge-core's index coercion accepted it
                # and rendered ``/dev/video213722070420`` -- a device that
                # cannot exist, which made the driver log a "does not exist
                # inside the container" warning on every healthy depth camera.
                # The tag tells edge-core this is hardware identity, not a
                # video source, so it injects no device at all.
                return _CameraPin(
                    f"serial {serial} (verified by driver)",
                    None,
                    f"{_SERIAL_PIN_PREFIX}{serial}",
                )
            _warn_unmatched_serial(twin_name, serial)
            return None

        device = twin_devices.get(twin_uuid)
        if device:
            if not _is_local_stable_camera_device(device):
                return _CameraPin(f"source {device}", None, device)
            matched = _camera_index_for_device(cameras, device)
            if matched is not None:
                return _CameraPin(f"device {device}", matched, device)
            _warn_unmatched_device(twin_name, device)
        return None

    def _save_cameras(
        selected_index: int | None,
        twin_to_device: dict[str, int | str] | None,
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

    def _accept_pin(twin_uuid: str, pin: _CameraPin | None) -> None:
        """Queue the move of a kept by-id serial pin to video_device, once it is used."""
        if pin is not None and pin.migrate:
            pins.setdefault(twin_uuid, _camera_pin_metadata(pin.migrate))

    def _infer_cameras(
        done: frozenset[str] = frozenset(), taken: frozenset[int] = frozenset()
    ) -> dict[str, tuple[int, str]]:
        """Twins the evidence pairs with exactly one camera, {twin: (index, why)}.

        *done*/*taken* carry answers given earlier in this run. A camera that cannot
        be pinned is never paired automatically.
        """
        claimed: set[int] = set(taken)
        pending: list[str] = []
        for uuid_, name in camera_twins:
            if uuid_ in done:
                continue
            pin = _pinned_camera(uuid_, name)
            if pin is None:
                pending.append(uuid_)
            elif pin.index is not None:
                claimed.add(pin.index)
        result = _infer_associations(
            pending, list(cameras_by_index), claimed=claimed, compatible=lambda _t, _i: True
        )
        return {t: (d, why) for t, (d, why) in result.assigned.items() if _identity_of(d)}

    def _bind_inferred(twin_uuid: str, twin_name: str, index: int, why: str) -> bool:
        """Announce an automatic pairing and queue its pin; False if refused.

        A twin whose saved pin no longer resolves is never moved on inference
        alone: the operator approves the substitute.
        """
        if twin_uuid in twin_serials or twin_uuid in twin_devices:
            cam = cameras_by_index[index]
            if not _ask_yes_no(
                f"{twin_name}'s saved camera is not attached. "
                f"Bind it to {cam.card} ({_identity_of(index)}) instead?",
                on_eof=False,
            ):
                console.print(
                    f"[yellow]{twin_name} stays pinned to its saved camera; edge-core will "
                    f"not start its driver until that camera is reconnected or you "
                    f"re-run install.[/yellow]"
                )
                return False
        _record(twin_uuid, index)
        console.print(
            f"[green]{twin_name}[/green]: associated automatically with "
            f"[cyan]{_identity_of(index)}[/cyan] — {why}."
        )
        return True

    if len(cameras) == 1:
        cam = cameras[0]
        selected = cam.index if cam.index is not None else 0
        console.print(f"\n[cyan]Detected camera:[/cyan] {cam.card} ({cam.primary_path})")
        twin_to_device: dict[str, int | str] = {}
        shared = 0
        inferred = _infer_cameras()
        for twin_uuid, twin_name in camera_twins:
            pin = _pinned_camera(twin_uuid, twin_name)
            _accept_pin(twin_uuid, pin)
            # A stream-pinned twin needs no local device, but it cannot simply
            # be left out of the mapping: edge-core falls back to the global
            # ``selected_device`` for any twin it cannot find there.
            if pin is not None and pin.index is None:
                console.print(f"[dim]{twin_name} is served by {pin.label}.[/dim]")
                twin_to_device[twin_uuid] = pin.device
                continue
            twin_to_device[twin_uuid] = pin.device if pin is not None else _device_for(selected)
            shared += 1
            if pin is None and twin_uuid in inferred:
                _bind_inferred(twin_uuid, twin_name, *inferred[twin_uuid])
        if shared > 1:
            console.print(f"[dim]All {shared} camera twin(s) will share this device.[/dim]")
        elif shared == 1 and not pins and not _identity_of(selected):
            _explain_unpinnable(selected)
        _persist_twin_pins(pins)
        _save_cameras(selected, twin_to_device or None)
        return

    if not camera_twins:
        console.print(f"\n[bold]Detected {len(cameras)} camera(s):[/bold]\n")
        valid_indices = _render_camera_menu(cameras)
        console.print()
        selected = _prompt_camera_index(
            cameras, valid_indices, prompt="Select camera", default_index=default_idx
        )
        if selected is not None:
            console.print(f"[green]Selected:[/green] {valid_indices[selected].card}")
            _save_cameras(selected, None)
        return

    # Saved pins are trusted silently otherwise, which is wrong after cameras are
    # swapped or replugged: ask once whether to keep them.
    pinned_twins = [(u, n) for u, n in camera_twins if u in had_pin]
    if pinned_twins:
        console.print("\n[bold]Existing camera bindings found:[/bold]")
        for uuid_, name in pinned_twins:
            pin = _pinned_camera(uuid_, name)
            if pin is None:
                console.print(f"  {name}: [yellow]pinned camera not attached[/yellow]")
            elif pin.index is None:
                console.print(f"  {name}: served by {pin.label}")
            else:
                console.print(
                    f"  {name}: {pin.label} -> {cameras_by_index[pin.index].primary_path}"
                )
        if not _ask_yes_no("Keep these existing camera bindings?"):
            twin_serials.clear()
            twin_devices.clear()
            pin_cache.clear()
            console.print(
                "[dim]Re-mapping cameras manually; new choices replace the old pins.[/dim]"
            )

    console.print(
        f"\n[bold]Detected {len(cameras)} camera(s) and {len(camera_twins)} "
        f"camera twin(s).[/bold]"
    )
    if len(camera_twins) > 1:
        console.print(
            "[dim]Map each twin to the physical camera it is wired to. "
            "A device can be assigned to more than one twin if it is shared.[/dim]\n"
        )

    twin_to_device = {}
    assignments_label: dict[int, str] = {}
    first_selected: int | None = None
    remaining_default = default_idx

    def _claim(twin_uuid: str, twin_name: str, index: int) -> None:
        """Record that *twin_uuid* uses camera *index* and advance the menu default."""
        nonlocal first_selected, remaining_default
        assignments_label[index] = twin_name
        if first_selected is None:
            first_selected = index
        free = [idx for idx in cameras_by_index if idx not in assignments_label]
        if free:
            remaining_default = free[0]

    for twin_uuid, twin_name in camera_twins:
        console.print(f"[bold]Twin:[/bold] {twin_name} [dim]({twin_uuid[:8]}...)[/dim]")

        pin = _pinned_camera(twin_uuid, twin_name)
        _accept_pin(twin_uuid, pin)
        if pin is not None:
            # Recorded even without a local node: an absent mapping inherits the
            # global ``selected_device``.
            twin_to_device[twin_uuid] = pin.device
            if pin.index is None:
                console.print(f"[green]Served by[/green] [cyan]{pin.label}[/cyan]\n")
                continue
            _claim(twin_uuid, twin_name, pin.index)
            console.print(
                f"[green]Using saved device[/green] [cyan]{pin.label}[/cyan] (connected) → "
                f"{cameras_by_index[pin.index].primary_path}\n"
            )
            continue

        inferred = _infer_cameras(
            done=frozenset(twin_to_device), taken=frozenset(assignments_label)
        )
        if twin_uuid in inferred and _bind_inferred(twin_uuid, twin_name, *inferred[twin_uuid]):
            index = inferred[twin_uuid][0]
            twin_to_device[twin_uuid] = _device_for(index)
            _claim(twin_uuid, twin_name, index)
            console.print()
            continue

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
        twin_to_device[twin_uuid] = _device_for(selected)
        _claim(twin_uuid, twin_name, selected)
        _record_choice(twin_uuid, selected)
        console.print(f"[green]Selected:[/green] {valid_indices[selected].card}\n")

    if not twin_to_device:
        console.print("[yellow]No cameras mapped — skipping camera config.[/yellow]")
        return

    _persist_twin_pins(pins)
    _save_cameras(first_selected, twin_to_device)


# cardN/usbid exists only for USB audio; pcm*c / pcm*p mean capture / playback.
_PROC_ASOUND_DIR = "/proc/asound"


class _AudioCard(NamedTuple):
    """One USB audio card and the stable name (if any) udev gave it."""

    index: int
    usbid: str  # vid:pid, shared by identical units
    label: str
    link: str | None  # by-id name, None when udev gave it none
    capture: bool
    playback: bool


def _discover_usb_audio_cards() -> list[_AudioCard]:
    """USB audio cards on this host, each matched to its by-id link."""
    links: dict[str, str] = {}
    for device in _list_alsa_by_id():
        links.setdefault(os.path.basename(device.target), device.name)
    try:
        entries = sorted(os.listdir(_PROC_ASOUND_DIR))
    except OSError:
        return []
    cards: list[_AudioCard] = []
    for entry in entries:
        match = re.fullmatch(r"card(\d+)", entry)
        if not match:
            continue
        card_dir = os.path.join(_PROC_ASOUND_DIR, entry)
        try:
            with open(os.path.join(card_dir, "usbid"), encoding="utf-8") as fh:
                usbid = fh.read().strip()
        except OSError:
            continue  # not USB audio: udev publishes no by-id link for it
        try:
            with open(os.path.join(card_dir, "id"), encoding="utf-8") as fh:
                label = fh.read().strip() or entry
        except OSError:
            label = entry
        try:
            pcms = os.listdir(card_dir)
        except OSError:
            pcms = []
        index = int(match.group(1))
        cards.append(
            _AudioCard(
                index=index,
                usbid=usbid,
                label=label,
                link=links.get(f"controlC{index}"),
                capture=any(re.fullmatch(r"pcm\d+c", n) for n in pcms),
                playback=any(re.fullmatch(r"pcm\d+p", n) for n in pcms),
            )
        )
    return cards


def _cached_twin_metadata() -> dict[str, dict[str, Any]]:
    """{twin_uuid: metadata} from the twin JSON files downloaded at install."""
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(CONFIG_DIR.glob("*.json")):
        if not _is_twin_object_file(path):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            found[str(data.get("uuid") or path.stem)] = metadata
    return found


def _load_twin_audio_pins() -> dict[str, str]:
    """{twin_uuid: by-id name} for cached twins whose serial_number or audio_device
    names a /dev/snd/by-id link; raw hardware serials are not audio pins.
    """
    pins: dict[str, str] = {}
    for uuid_, metadata in _cached_twin_metadata().items():
        for key in ("serial_number", "audio_device"):
            value = metadata.get(key)
            name = _udev_pin_name(value, _ALSA_BY_ID_DIR) if isinstance(value, str) else None
            if name is not None:
                pins[uuid_] = name
                break
    return pins


def _load_twin_audio_serials() -> dict[str, str]:
    """{twin_uuid: serial} for twins whose serial_number is a hardware serial an
    operator set; a by-id link name there is a legacy pin, not a serial.
    """
    serials: dict[str, str] = {}
    for uuid_, metadata in _cached_twin_metadata().items():
        value = metadata.get("serial_number")
        if isinstance(value, str) and value.strip():
            if _udev_pin_name(value, _ALSA_BY_ID_DIR) is None:
                serials[uuid_] = value.strip()
    return serials


def _audio_card_for_serial(cards: list[_AudioCard], serial: str) -> _AudioCard | None:
    """The one card whose by-id link carries *serial* as its delimited serial field.

    Same rule as the microphone driver (``_<serial>-<N>`` at the end of the link
    name). ALSA reports no serial, so the link name is the only place to match.
    """
    serial = serial.strip()
    if not serial:
        return None
    field = re.compile(rf"_{re.escape(serial)}-\d+$")
    matches = [c for c in cards if c.link and field.search(c.link)]
    return matches[0] if len(matches) == 1 else None


def _audio_pin_metadata(name: str) -> dict[str, str]:
    """Pin an audio device to its by-id link, as cameras pin theirs."""
    return {"audio_device": f"{_ALSA_BY_ID_DIR}/{name}", "serial_number": ""}


def _detect_and_select_audio_devices() -> None:
    """Associate each microphone/speaker twin with a /dev/snd/by-id device (Linux).

    Same contract as cameras. A serial an operator set on the twin is checked
    first and kept; a saved by-id link is reused when connected. A new choice is
    written as audio_device plus a blank serial. Anything whose device is missing
    is replaced only with approval; unambiguous pairings are automatic, the rest
    is a menu. Identical units udev collapsed onto one link are never pinned.
    Best-effort.
    """
    if platform.system() != "Linux":
        return

    # uuid -> (name, needs capture, needs playback)
    twins: dict[str, tuple[str, bool, bool]] = {}
    for uuid_, name in _list_microphone_twins():
        twins[uuid_] = (name, True, False)
    for uuid_, name in _list_speaker_twins():
        prev = twins.get(uuid_)
        twins[uuid_] = (name, prev[1] if prev else False, True)
    if not twins:
        return

    cards = _discover_usb_audio_cards()
    ambiguous = _ambiguous_groups((c.usbid, bool(c.link)) for c in cards)
    serials = {u: s for u, s in _load_twin_audio_serials().items() if u in twins}
    saved = {u: n for u, n in _load_twin_audio_pins().items() if u in twins and u not in serials}
    connected = {u: n for u, n in saved.items() if _udev_pin_is_connected(n, _ALSA_BY_ID_DIR)}
    by_serial: dict[str, str] = {}
    for uuid_, serial in serials.items():
        card = _audio_card_for_serial(cards, serial)
        if card is not None and card.link:
            by_serial[uuid_] = card.link

    console.print("\n[bold]Audio devices[/bold]")
    for uuid_, serial in serials.items():
        if uuid_ in by_serial:
            console.print(
                f"  {twins[uuid_][0]}: pinned by serial {serial} -> "
                f"[cyan]{by_serial[uuid_]}[/cyan] (connected)"
            )
        else:
            console.print(
                f"[yellow]{twins[uuid_][0]} pins serial {serial}, which no attached audio "
                f"device reports.[/yellow]\n"
                f"[dim]Its driver will refuse to start rather than open a different "
                f"device. Check the twin's metadata.serial_number.[/dim]"
            )
    for card in cards:
        if card.usbid in ambiguous:
            console.print(
                f"  [yellow]{card.label}: identical to another attached unit and udev "
                f"gives them no distinct id — cannot be pinned[/yellow]"
            )
    for uuid_, name in saved.items():
        if uuid_ in connected:
            console.print(
                f"  {twins[uuid_][0]}: using saved device [cyan]{name}[/cyan] (connected)"
            )
        else:
            console.print(
                f"  {twins[uuid_][0]}: [yellow]saved device {name} is not connected "
                f"— it must be selected again[/yellow]"
            )
    # With a single device there is nothing to re-map, so nothing to ask.
    if (
        (connected or by_serial)
        and len(cards) > 1
        and not _ask_yes_no("Keep these existing audio bindings?")
    ):
        connected.clear()
        by_serial.clear()
        serials.clear()
        saved.clear()

    pending = [u for u in twins if u not in connected and u not in by_serial]
    taken: dict[str, str] = {
        link: twins[u][0] for u, link in {**connected, **by_serial}.items()
    }
    pins: dict[str, dict[str, str]] = {}
    by_index = {c.index: c for c in cards}
    cached = _cached_twin_metadata()

    # Bring twins written by an older installer (link name in serial_number) to the new shape.
    for uuid_, name in connected.items():
        metadata = cached.get(uuid_, {})
        current = metadata.get("audio_device") == f"{_ALSA_BY_ID_DIR}/{name}"
        if not current or str(metadata.get("serial_number") or "").strip():
            pins[uuid_] = _audio_pin_metadata(name)

    def _can_serve(uuid_: str, card: _AudioCard) -> bool:
        _name, needs_capture, needs_playback = twins[uuid_]
        return (card.capture or not needs_capture) and (card.playback or not needs_playback)

    def _pinnable(card: _AudioCard) -> bool:
        return bool(card.link) and card.usbid not in ambiguous

    def _assign(uuid_: str, card: _AudioCard) -> None:
        pins[uuid_] = _audio_pin_metadata(card.link or "")
        taken[card.link or ""] = twins[uuid_][0]

    for uuid_ in pending:
        name = twins[uuid_][0]
        # Unpinnable cards stay in the pool as rivals but are never paired automatically.
        result = _infer_associations(
            [u for u in pending if u not in pins],
            list(by_index),
            claimed={c.index for c in cards if c.link in taken},
            compatible=lambda t, i: _can_serve(t, by_index[i]),
        )
        decided = result.assigned.get(uuid_)
        if decided and _pinnable(by_index[decided[0]]):
            card = by_index[decided[0]]
            missing = (
                f"saved device {saved[uuid_]} is not connected"
                if uuid_ in saved
                else f"serial {serials[uuid_]} matches no attached device"
                if uuid_ in serials
                else None
            )
            if missing is None or _ask_yes_no(
                f"{name}'s {missing}. Use {card.link} instead?", on_eof=False
            ):
                console.print(
                    f"[green]{name}[/green]: associated automatically with "
                    f"[cyan]{card.link}[/cyan] — {decided[1]}."
                )
                _assign(uuid_, card)
                continue

        pool = [c for c in cards if _pinnable(c) and _can_serve(uuid_, c)]
        if not pool:
            console.print(
                f"[yellow]No USB audio device that can be pinned reliably was found for "
                f"{name} (it needs a distinct udev id). "
                f"Plug one in and run: cyberwave edge install[/yellow]"
            )
            continue
        pool = [c for c in pool if c.link not in taken] or pool
        if len(pool) == 1:
            if _ask_yes_no(f"Only one device ({pool[0].link}) is available for {name}. Use it?"):
                _assign(uuid_, pool[0])
            continue

        console.print(f"\n[bold]Audio device for {name}[/bold] [dim]({uuid_[:8]}...)[/dim]")
        for i, card in enumerate(pool):
            owner = taken.get(card.link or "")
            suffix = f"  [yellow](assigned to {owner})[/yellow]" if owner else ""
            console.print(
                f"  [bold cyan]{i}[/bold cyan])  {card.link}  [dim]card {card.index}[/dim]{suffix}"
            )
        try:
            raw = Prompt.ask(f"Audio device for {name}", default="0")
        except EOFError:
            console.print("[yellow]No input available — skipping audio pinning.[/yellow]")
            break
        try:
            choice = int(raw)
            if choice < 0:
                raise IndexError(choice)
            _assign(uuid_, pool[choice])
        except (ValueError, IndexError):
            console.print(f"[yellow]'{raw}' is not a listed device — skipping {name}.[/yellow]")
            continue
        console.print(f"[green]Selected:[/green] {pins[uuid_]['audio_device']}")

    _persist_twin_pins(pins)


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
    token: str | None = None,
) -> bool:
    """Generic install orchestrator: install package, optionally set up Docker + systemd.

    When *force_reinstall* is True, platform helpers (USB/IP server, camera
    stream) are torn down and rebuilt from scratch instead of being skipped
    when already running.

    *token* is an explicit API token to authenticate with, overriding stored
    credentials and the interactive login.

    Returns True if everything succeeded.
    """
    _migrate_legacy_config_dir()

    # Fail before any package index is touched: the PR registry holds builds
    # from unrelated branches whose ordering is meaningless, so there is no
    # sensible "latest" to fall back to. Asking up front also keeps the error
    # identical on Debian (apt) and elsewhere (pip).
    if _normalize_service_channel(channel) == "pr" and not version:
        console.print(
            "[red]--channel pr requires an explicit --version.[/red]\n"
            "[dim]PR builds are not ordered against each other, so there is no latest "
            "one to pick. Copy the version from the pull request's build comment, e.g.\n"
            f"  {spec.sudo_command_hint} --channel pr --version 1.2.3~pr4337.gabc123def456[/dim]"
        )
        return False

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

    # After the confirm, not before: `--token` overwrites a credential that may
    # still be valid, so declining above must leave the device untouched.
    if not _ensure_credentials(skip_confirm=skip_confirm, token=token):
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

        # USB/IP enumerates serial devices inside the VM but macOS keeps its own
        # CDC driver attached, so their bulk endpoints never carry data. Publish
        # each one over TCP as well; drivers prefer the bridge when present.
        if not setup_serial_bridges(force=force_reinstall):
            console.print(
                "[yellow]Serial bridge setup failed. USB serial devices will "
                "not be reachable from Docker containers.[/yellow]\n"
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

    if (
        spec.requires_docker
        and not is_macos()
        and (_any_twin_has_microphone_sensor() or _any_twin_has_speaker_sensor())
    ):
        try:
            _detect_and_select_audio_devices()
        except Exception as exc:
            console.print(f"[dim]Audio device pinning skipped: {exc}[/dim]")

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
    environment: str | None = None,
    token: str | None = None,
) -> bool:
    """Full setup for edge core: install the package, create the service, enable on boot.

    When *force_reinstall* is True, platform helpers (USB/IP, camera stream)
    are torn down and rebuilt from scratch.

    *environment* pins the environment to attach to (``-e/--environment``)
    instead of prompting for it. *token* pins the API token to pair with
    (``--token``) instead of stored credentials or an interactive login.

    The *pull_worker_image* parameter is **deprecated** and ignored.
    The ML worker Docker image is now pulled asynchronously by the
    edge-core service on first startup via ``WorkerManager``, matching the
    same pattern used for driver images (CYB-2029).

    Returns True if everything succeeded.
    """
    # The preflight reads credentials, and setup_service migrates too late —
    # `cyberwave pair` also skips the `edge` group callback's migration.
    _migrate_legacy_config_dir()

    if environment is not None and not _preflight_environment_reference(environment, token=token):
        return False

    def _post_install() -> bool:
        return configure_edge_environment(skip_confirm=skip_confirm, environment=environment)

    return setup_service(
        EDGE_CORE_SPEC,
        skip_confirm=skip_confirm,
        channel=channel,
        version=version,
        post_install_hook=_post_install,
        force_reinstall=force_reinstall,
        token=token,
    )
