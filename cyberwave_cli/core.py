"""Install and manage the cyberwave-edge-core systemd service.

This module provides the logic for:
  1. Installing the cyberwave-edge-core .deb package via apt-get
  2. Creating a systemd service unit so it starts on boot
  3. Enabling and starting the service
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, Prompt

from .auth import AuthClient, AuthenticationError, Workspace
from .config import CONFIG_DIR, get_api_url
from .credentials import load_credentials

console = Console()

# ---- constants ---------------------------------------------------------------

PACKAGE_NAME = "cyberwave-edge-core"
BINARY_PATH = Path("/usr/bin/cyberwave-edge-core")
SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
SYSTEMD_UNIT_PATH = Path(f"/etc/systemd/system/{SYSTEMD_UNIT_NAME}")
ENVIRONMENT_FILE = CONFIG_DIR / "environment.json"
FINGERPRINT_FILE = CONFIG_DIR / "fingerprint.json"

# Buildkite Debian registry URL for the cyberwave-edge-core package
BUILDKITE_DEB_REPO_URL = (
    "https://packages.buildkite.com/cyberwave/cyberwave-edge-core/any/any"
)

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


# ---- helpers -----------------------------------------------------------------


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _has_systemd() -> bool:
    return Path("/run/systemd/system").is_dir()


def _run(cmd: list[str], *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess and stream output to the console."""
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
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
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    def _render() -> None:
        # Clear screen and draw menu every keypress for a simple TUI.
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.write(f"{title}\n")
        sys.stdout.write("Use \u2191/\u2193 and press Enter\n\n")
        for idx, option in enumerate(options):
            prefix = "❯" if idx == selected else " "
            sys.stdout.write(f"{prefix} {option}\n")
        sys.stdout.flush()

    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")
        _render()
        while True:
            char = sys.stdin.read(1)
            if char in ("\r", "\n"):
                return selected
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
        sys.stdout.write("\n")
        sys.stdout.flush()


def _get_sdk_client(token: str):
    """Create a Cyberwave SDK client from a token."""
    from cyberwave import Cyberwave

    return Cyberwave(base_url=get_api_url(), token=token)


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

    ENVIRONMENT_FILE.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    # Keep same permission model as credentials.
    if os.name != "nt":
        os.chmod(ENVIRONMENT_FILE, 0o600)


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

    from .fingerprint import generate_fingerprint

    return generate_fingerprint()


def _select_workspace(token: str, *, skip_confirm: bool) -> Workspace:
    """Get workspaces and let user select one."""
    with AuthClient() as auth_client:
        workspaces = auth_client.get_workspaces(token)

    if not workspaces:
        raise RuntimeError("No workspaces available for this account.")

    if len(workspaces) == 1:
        ws = workspaces[0]
        console.print(f"[green]Workspace:[/green] {ws.name}")
        _save_environment_file(workspace_uuid=ws.uuid, workspace_name=ws.name)
        return ws

    if skip_confirm:
        ws = workspaces[0]
        console.print(f"[yellow]Auto-selecting workspace:[/yellow] {ws.name}")
        _save_environment_file(workspace_uuid=ws.uuid, workspace_name=ws.name)
        return ws

    labels = [f"{ws.name} ({ws.uuid[:8]}...)" for ws in workspaces]
    idx = _select_with_arrows("Select a workspace", labels)
    ws = workspaces[idx]
    _save_environment_file(workspace_uuid=ws.uuid, workspace_name=ws.name)
    return ws


def _workspace_projects(client: Any, workspace_uuid: str) -> list[Any]:
    """Return projects that belong to the selected workspace."""
    projects = client.projects.list()
    result = []
    for project in projects:
        project_workspace_uuid = str(
            getattr(project, "workspace_uuid", "")
            or getattr(project, "workspace_id", "")
            or ""
        )
        if project_workspace_uuid == workspace_uuid:
            result.append(project)
    return result


def _workspace_environments(client: Any, workspace_uuid: str) -> list[Any]:
    """Return environments scoped to selected workspace projects."""
    environments: list[Any] = []
    seen_uuids: set[str] = set()
    projects = _workspace_projects(client, workspace_uuid)
    for project in projects:
        envs = client.environments.list(project_id=str(project.uuid))
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
        return _create_environment_in_workspace(
            client, workspace_uuid, skip_confirm=skip_confirm
        )

    if skip_confirm:
        return environments[0]

    labels = [f"{getattr(env, 'name', 'Unnamed')} ({str(env.uuid)[:8]}...)" for env in environments]
    labels.append("Create new environment")
    idx = _select_with_arrows("Select an environment", labels)

    if idx == len(environments):
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
    selected: set[int] = set()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    def _render() -> None:
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.write(f"{title}\n")
        sys.stdout.write("Use ↑/↓ to move, Space to toggle, Enter to confirm\n\n")
        for idx, option in enumerate(options):
            cursor_mark = "❯" if idx == cursor else " "
            selected_mark = "[x]" if idx in selected else "[ ]"
            sys.stdout.write(f"{cursor_mark} {selected_mark} {option}\n")
        sys.stdout.flush()

    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")
        _render()
        while True:
            char = sys.stdin.read(1)
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
        sys.stdout.write("\n")
        sys.stdout.flush()


def _select_connected_twins(
    client: Any, environment_uuid: str, *, skip_confirm: bool
) -> list[str]:
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
    idxs = _select_multiple_with_arrows(
        "Which twins are physically connected to your edge?",
        labels,
    )
    selected_uuids: list[str] = []
    for idx in idxs:
        twin_uuid = str(getattr(twins[idx], "uuid", ""))
        if twin_uuid:
            selected_uuids.append(twin_uuid)
    return selected_uuids


def _attach_edge_fingerprint_to_twins(
    client: Any, twin_uuids: list[str], edge_fingerprint: str
) -> tuple[int, int]:
    """Update selected twins metadata with edge_fingerprint.

    Uses direct HTTP to avoid SDK serialization issues with the PUT
    endpoint (the auto-generated TwinCreateSchema sends default values
    for every optional field, which can trigger unexpected side-effects).

    Returns:
        (updated_count, failed_count)
    """
    import httpx

    creds = load_credentials()
    if not creds or not creds.token:
        console.print("[red]No credentials — cannot update twin metadata.[/red]")
        return 0, len(twin_uuids)

    base_url = get_api_url()
    headers = {
        "Authorization": f"Token {creds.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    updated = 0
    failed = 0

    for twin_uuid in twin_uuids:
        try:
            # 1. GET current twin to read existing metadata.
            get_resp = httpx.get(
                f"{base_url}/api/v1/twins/{twin_uuid}",
                headers=headers,
                timeout=15.0,
            )
            if get_resp.status_code != 200:
                console.print(
                    f"[yellow]GET twin {twin_uuid[:8]}… returned {get_resp.status_code}[/yellow]"
                )
                failed += 1
                continue

            twin_data = get_resp.json()
            metadata = twin_data.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["edge_fingerprint"] = edge_fingerprint

            # 2. PUT only the metadata field.
            put_payload = {"metadata": metadata}
            console.print(
                f"[dim]  PUT twin {twin_uuid[:8]}… payload={json.dumps(put_payload)[:200]}[/dim]"
            )
            put_resp = httpx.put(
                f"{base_url}/api/v1/twins/{twin_uuid}",
                headers=headers,
                json=put_payload,
                timeout=15.0,
            )
            if put_resp.status_code != 200:
                console.print(
                    f"[yellow]PUT twin {twin_uuid[:8]}… returned "
                    f"{put_resp.status_code}: {put_resp.text[:200]}[/yellow]"
                )
                failed += 1
                continue

            # Verify the response includes the updated metadata.
            put_data = put_resp.json()
            resp_metadata = put_data.get("metadata", {})
            console.print(
                f"[dim]  PUT response metadata={json.dumps(resp_metadata)[:200]}[/dim]"
            )

            updated += 1
        except Exception as exc:
            console.print(f"[yellow]Error updating twin {twin_uuid[:8]}…: {exc}[/yellow]")
            failed += 1

    return updated, failed


def configure_edge_environment(*, skip_confirm: bool = False) -> bool:
    """Select workspace + environment and save ~/.cyberwave/environment.json."""
    creds = load_credentials()
    if not creds or not creds.token:
        console.print("[red]No credentials found.[/red]")
        console.print("[dim]Run 'cyberwave login' first.[/dim]")
        return False

    token = creds.token

    try:
        workspace = _select_workspace(token, skip_confirm=skip_confirm)
        client = _get_sdk_client(token)
        environment = _select_or_create_environment(
            client,
            workspace.uuid,
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
        if selected_twin_uuids:
            updated_count, failed_count = _attach_edge_fingerprint_to_twins(
                client,
                selected_twin_uuids,
                edge_fingerprint,
            )
            console.print(
                f"[dim]Updated twins with edge fingerprint: {updated_count}[/dim]"
            )
            if failed_count:
                console.print(
                    f"[yellow]Failed to update {failed_count} twin(s).[/yellow]"
                )

        _save_environment_file(
            workspace_uuid=workspace.uuid,
            workspace_name=workspace.name,
            environment_uuid=env_uuid,
            environment_name=env_name or None,
            twin_uuids=selected_twin_uuids,
        )

        console.print("[green]Environment saved:[/green] ~/.cyberwave/environment.json")
        console.print(f"[dim]Environment: {env_name or env_uuid}[/dim]")
        console.print(
            f"[dim]Connected twins selected: {len(selected_twin_uuids)}[/dim]"
        )
        return True
    except AuthenticationError as exc:
        console.print(f"[red]Authentication error:[/red] {exc}")
        return False
    except Exception as exc:
        console.print(f"[red]Failed to configure environment:[/red] {exc}")
        return False


# ---- apt-get installation ----------------------------------------------------


def _apt_get_install() -> bool:
    """Install cyberwave-edge-core via apt-get.

    Adds the Buildkite package registry if not already configured,
    then installs (or upgrades) the package.

    Returns True on success.
    """
    sources_list = Path("/etc/apt/sources.list.d/cyberwave-edge-core.list")

    # Add the repository if missing
    if not sources_list.exists():
        console.print("[cyan]Adding Cyberwave package repository...[/cyan]")
        try:
            sources_list.write_text(f"deb {BUILDKITE_DEB_REPO_URL} /\n")
        except PermissionError:
            console.print(
                "[red]Permission denied writing apt sources.[/red]\n"
                "[dim]Re-run with sudo: sudo cyberwave edge install[/dim]"
            )
            return False

    # Update and install
    console.print(f"[cyan]Installing {PACKAGE_NAME} via apt-get...[/cyan]")
    try:
        _run(["apt-get", "update", "-qq"])
        _run(["apt-get", "install", "-y", "-qq", PACKAGE_NAME])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]apt-get failed (exit {exc.returncode}).[/red]")
        return False

    if BINARY_PATH.exists():
        console.print(f"[green]Installed:[/green] {BINARY_PATH}")
        return True

    console.print("[red]Binary not found after installation.[/red]")
    return False


def _pip_install() -> bool:
    """Fallback: install cyberwave-edge-core via pip.

    Used on non-Debian systems (macOS, other Linux flavors).
    Returns True on success.
    """
    console.print(f"[cyan]Installing {PACKAGE_NAME} via pip...[/cyan]")
    try:
        _run([sys.executable, "-m", "pip", "install", PACKAGE_NAME])
        return True
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]pip install failed (exit {exc.returncode}).[/red]")
        return False


def install_edge_core() -> bool:
    """Install the cyberwave-edge-core package.

    Prefers apt-get on Debian/Ubuntu, falls back to pip otherwise.
    Returns True on success.
    """
    if _is_linux() and shutil.which("apt-get"):
        return _apt_get_install()
    return _pip_install()


# ---- systemd service ---------------------------------------------------------


def create_systemd_service() -> bool:
    """Write the systemd unit file for cyberwave-edge-core.

    Returns True on success.
    """
    if not _has_systemd():
        console.print("[yellow]systemd not detected — skipping service creation.[/yellow]")
        return False

    binary = (
        str(BINARY_PATH)
        if BINARY_PATH.exists()
        else shutil.which(PACKAGE_NAME) or str(BINARY_PATH)
    )
    unit_contents = SYSTEMD_UNIT_TEMPLATE.format(binary_path=binary)

    try:
        SYSTEMD_UNIT_PATH.write_text(unit_contents)
    except PermissionError:
        console.print(
            "[red]Permission denied writing systemd unit.[/red]\n"
            "[dim]Re-run with sudo: sudo cyberwave edge install[/dim]"
        )
        return False

    console.print(f"[green]Created:[/green] {SYSTEMD_UNIT_PATH}")
    return True


def enable_and_start_service() -> bool:
    """Enable the service to start on boot, then start it now.

    Returns True on success.
    """
    if not SYSTEMD_UNIT_PATH.exists():
        console.print("[red]Service unit not found — run install first.[/red]")
        return False

    try:
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", SYSTEMD_UNIT_NAME])
        _run(["systemctl", "start", SYSTEMD_UNIT_NAME])
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]systemctl command failed (exit {exc.returncode}).[/red]")
        return False

    console.print(f"[green]Service enabled and started:[/green] {SYSTEMD_UNIT_NAME}")
    return True


# ---- orchestrator ------------------------------------------------------------


def setup_edge_core(*, skip_confirm: bool = False) -> bool:
    """Full setup: install the package, create the service, enable on boot.

    Returns True if everything succeeded.
    """
    if not _is_linux():
        console.print("[yellow]Edge core service setup is only supported on Linux.[/yellow]")
        console.print(
            "[dim]You can still install the package with:"
            " pip install cyberwave-edge-core[/dim]"
        )
        return False

    if os.geteuid() != 0:
        console.print(
            "[red]Root privileges required.[/red]\n"
            "[dim]Re-run with sudo: sudo cyberwave edge install[/dim]"
        )
        return False

    if not skip_confirm:
        console.print(
            f"\nThis will:\n"
            f"  1. Install [bold]{PACKAGE_NAME}[/bold] via apt-get\n"
            f"  2. Create a systemd service ([bold]{SYSTEMD_UNIT_NAME}[/bold])\n"
            f"  3. Enable it to start on boot\n"
        )
        if not Confirm.ask("Continue?", default=True):
            console.print("[dim]Aborted.[/dim]")
            return False

    # Step 1 — install
    if not install_edge_core():
        return False

    # Step 2 — systemd unit
    if not create_systemd_service():
        return False

    # Step 3 — enable & start
    if not enable_and_start_service():
        return False

    # Step 4 — pick workspace/environment and persist config
    if not configure_edge_environment(skip_confirm=skip_confirm):
        return False

    console.print("\n[green]Edge core is installed and running.[/green]")
    console.print("[dim]Check status: systemctl status cyberwave-edge-core[/dim]")
    console.print("[dim]View logs:    journalctl -u cyberwave-edge-core -f[/dim]")
    return True
