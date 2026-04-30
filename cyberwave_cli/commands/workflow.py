"""Workflow management commands for the Cyberwave CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import click
from rich.console import Console
from rich.table import Table

from ..credentials import load_credentials
from ..utils import get_sdk_client, resolve_api_url

console = Console()


def _friendly_error(action: str, exc: Exception, base_url: str | None = None) -> NoReturn:
    """Print a user-friendly error message and abort."""
    url = resolve_api_url(base_url, load_credentials())
    host = urlparse(url).hostname or url

    cause = exc.__cause__ or exc
    cls_name = type(cause).__name__

    if "ConnectionError" in cls_name or "NewConnectionError" in cls_name:
        console.print(f"[red]✗[/red] Failed to {action}. Unable to connect to [bold]{url}[/bold]")
        parsed = urlparse(url)
        if parsed.port is None:
            default_port = 443 if parsed.scheme == "https" else 80
            console.print(
                f"[dim]  No port specified (defaulting to {default_port}). "
                f"Did you mean {parsed.scheme}://{host}:8000 ?[/dim]"
            )
        else:
            console.print(f"[dim]  Check that the server is reachable at {host}:{parsed.port}.[/dim]")
    elif "Timeout" in cls_name:
        console.print(f"[red]✗[/red] Failed to {action}. Request to [bold]{url}[/bold] timed out.")
    elif "401" in str(cause) or "Unauthorized" in str(cause):
        console.print(f"[red]✗[/red] Failed to {action}. Authentication failed.")
        console.print("[dim]  Run 'cyberwave login' to refresh your credentials.[/dim]")
    elif "403" in str(cause) or "Forbidden" in str(cause):
        console.print(f"[red]✗[/red] Failed to {action}. Permission denied.")
    elif "404" in str(cause) or "Not Found" in str(cause):
        console.print(f"[red]✗[/red] Failed to {action}. Resource not found.")
    else:
        console.print(f"[red]✗[/red] Failed to {action}: {cause}")

    raise click.Abort()


# Workflow templates for common use cases
WORKFLOW_TEMPLATES = {
    "motion-detection": {
        "name": "Motion Detection Workflow",
        "description": "Triggers on motion events from camera",
    },
    "object-detection": {
        "name": "Object Detection Workflow",
        "description": "Detects objects using YOLO on camera frames",
    },
    "person-detection": {
        "name": "Person Detection Workflow",
        "description": "Detects people and emits alerts",
    },
}


def _base_url_option(func):
    """Shared --base-url option for workflow subcommands."""
    return click.option(
        "--base-url",
        "-u",
        default=None,
        help="Backend API URL (e.g. http://192.168.10.101:8000). "
        "Defaults to CYBERWAVE_BASE_URL or https://api.cyberwave.com.",
    )(func)


def _format_workflow_uuid_for_table(uuid) -> str:
    """Return the full workflow UUID for copy/paste into follow-up commands."""
    return str(uuid)


def _extract_twin_uuids(nodes) -> list[str]:
    """Extract unique twin UUIDs referenced by enabled workflow nodes."""
    seen: set[str] = set()
    result: list[str] = []
    for n in nodes:
        if getattr(n, "is_disabled", False):
            continue
        tid = (n.parameters or {}).get("twin_uuid")
        if tid and tid not in seen:
            seen.add(tid)
            result.append(tid)
    return result


def _load_local_edge_twin_uuids() -> list[str]:
    """Return twin UUIDs bound to this local edge install."""
    from ..core import _load_selected_twin_uuids

    selected = _load_selected_twin_uuids()
    return sorted(selected or [])


class _ApiError(RuntimeError):
    """Raised by ``_api_get_*`` helpers on HTTP / network failures.

    Carries the HTTP status code (when available) so call sites can
    branch on it explicitly — e.g. ``except _ApiError as exc: if
    exc.status == 404: ...`` — without substring-matching the error
    string. Network / decode failures set ``status=None``.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _api_get_json(path: str, base_url: str | None = None) -> dict[str, Any] | list[Any]:
    """GET ``path`` against the backend and return parsed JSON.

    Used for endpoints that the SDK doesn't expose as generated methods
    (``/workflows/edge-sync/{twin}``, ``/workflows/{uuid}/compile``).
    Raises :class:`_ApiError` on HTTP / network errors so callers can
    branch on ``exc.status`` and surface a friendly message to the user
    without leaking ``urllib`` types.
    """
    creds = load_credentials()
    token = creds.token if creds else None
    if not token:
        raise _ApiError("Not authenticated — run `cyberwave login` first.")

    api_url = resolve_api_url(base_url, creds).rstrip("/")
    url = f"{api_url}{path}"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
    except HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:
            err_body = ""
        raise _ApiError(
            f"HTTP {exc.code} from {url}: {err_body or exc.reason}",
            status=exc.code,
        ) from exc
    except URLError as exc:
        raise _ApiError(f"Network error talking to {url}: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise _ApiError(f"Invalid JSON from {url}: {exc}") from exc


def _api_get_text(path: str, base_url: str | None = None) -> str:
    """GET ``path`` and return the response body decoded as UTF-8.

    Used by ``workflow compile-source`` for the ``text/x-python`` payload
    served by ``/compile/source`` (``_api_get_json`` would fail trying to
    JSON-decode it).
    """
    creds = load_credentials()
    token = creds.token if creds else None
    if not token:
        raise _ApiError("Not authenticated — run `cyberwave login` first.")

    api_url = resolve_api_url(base_url, creds).rstrip("/")
    url = f"{api_url}{path}"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:
            err_body = ""
        raise _ApiError(
            f"HTTP {exc.code} from {url}: {err_body or exc.reason}",
            status=exc.code,
        ) from exc
    except URLError as exc:
        raise _ApiError(f"Network error talking to {url}: {exc.reason}") from exc


# Human-readable label for each ``compiled_kind`` the backend emits.
# After the edge compiler unification there's a single compiler that
# emits one of these artifact shapes per workflow; this dictionary just
# trades the wire enum for a friendlier word in CLI output. Keys come
# from ``EdgeWorkflowCompilationKind`` in
# ``cyberwave-backend/src/app/services/edge_workflow_compilers.py``;
# new kinds added there will fall through to the raw enum value in the
# CLI display until they're mapped here.
_LABEL_FOR_KIND: dict[str, str] = {
    "worker_module": "perception",
    "navigation_mission": "navigation",
}


def _print_workflow_metadata(w) -> None:
    """Print the workflow header block used by ``sync`` preflight.

    ``run_on_edge`` and ``environment`` are the fields that actually
    drive what edge sync will ship; they get prominent rows. The edge
    compiler dispatches per-subgraph based on graph shape — there is
    no user-visible workflow "kind" anymore.
    """
    console.print(f"\n[bold cyan]{w.name}[/bold cyan]")
    console.print(f"  uuid:             {w.uuid}")
    console.print(f"  is_active:        {getattr(w, 'is_active', None)}")
    console.print(f"  run_on_edge:      {getattr(w, 'run_on_edge', None)}")
    env_uuid = getattr(w, "environment_uuid", None)
    env_name = getattr(w, "environment_name", None)
    if env_uuid:
        console.print(
            f"  environment:      {env_uuid}"
            + (f"  [dim]({env_name})[/dim]" if env_name else "")
        )
    else:
        console.print("  environment:      [dim]none[/dim]")


def _diagnose_missing_workflow(w, twin_uuid: str) -> str:
    """Return a precise reason this workflow won't be shipped to *twin_uuid*.

    Mirrors the gates in ``edge_sync_workflows`` and the unified
    ``EdgeWorkflowCompiler`` so the user gets the same answer they'd
    get from the backend, without waiting for an MQTT round-trip that
    succeeds silently.

    The unified compiler renders any workflow with a ``twin_control``
    node through the navigation path and any workflow with a
    ``camera_frame`` trigger through the perception path, and combines
    both when the same workflow has both shapes. The only graph-shape
    failure left is "no subgraph the compiler can render at all" —
    which the backend's :func:`diagnose_compilation_dispatch` reports
    through the ``/compile`` endpoint, and which we mirror here
    through ``warnings`` returned by the compile call.
    """
    if not getattr(w, "is_active", False):
        return (
            "Workflow is inactive. Activate it with "
            f"`cyberwave workflow activate {w.uuid}` (or via the editor)."
        )

    return (
        "Cloud edge-sync did not return this workflow for the twin and "
        "the CLI defers graph diagnostics to the compiler. Run "
        f"`cyberwave workflow compile {w.uuid}` to see what artifact the "
        "edge compiler emitted (or the warning explaining why nothing "
        "compiled) — the backend's diagnose_compilation_dispatch helper "
        "surfaces the canonical reason there."
    )


def _preflight_sync(
    w,
    twin_uuids: list[str],
    base_url: str | None,
) -> tuple[list[str], list[str]]:
    """Check what edge-sync will actually ship before publishing the MQTT command.

    Returns ``(syncable_twins, blocking_messages)``. ``syncable_twins`` is the
    subset of ``twin_uuids`` for which the cloud's ``/workflows/edge-sync``
    response includes this workflow's UUID; ``blocking_messages`` is a
    human-readable list of reasons for any twin that won't get the workflow.
    """
    syncable: list[str] = []
    blocking: list[str] = []
    workflow_uuid = str(w.uuid)

    for twin_uuid in twin_uuids:
        try:
            payload = _api_get_json(
                f"/api/v1/workflows/edge-sync/{twin_uuid}", base_url=base_url
            )
        except _ApiError as exc:
            blocking.append(
                f"twin {twin_uuid[:8]}…: could not query cloud edge-sync ({exc})"
            )
            continue

        wfs = payload.get("workflows", []) if isinstance(payload, dict) else []
        ship_uuids = {str(entry.get("workflow_uuid")) for entry in wfs}
        if workflow_uuid in ship_uuids:
            syncable.append(twin_uuid)
            entry = next(
                (e for e in wfs if str(e.get("workflow_uuid")) == workflow_uuid),
                {},
            )
            console.print(
                f"  [green]✓[/green] twin {twin_uuid} → "
                f"will receive [bold]{entry.get('worker_filename') or '<no worker>'}[/bold] "
                f"({entry.get('compiled_kind') or 'unknown kind'})"
            )
        else:
            reason = _diagnose_missing_workflow(w, twin_uuid)
            other_uuids = sorted(ship_uuids)
            other_blurb = (
                f" Cloud is shipping {len(other_uuids)} other workflow(s) "
                f"to this twin: {', '.join(u[:8] + '…' for u in other_uuids)}."
                if other_uuids
                else " Cloud is shipping no workflows to this twin."
            )
            blocking.append(f"twin {twin_uuid}: {reason}{other_blurb}")

    return syncable, blocking


def _pick_workflow(client, title: str = "Select a workflow", base_url: str | None = None) -> str:
    """Fetch workflows and let the user pick one interactively.

    Returns the selected workflow UUID string.
    """
    from ..core import _select_with_arrows

    try:
        with console.status("[dim]Loading workflows...[/dim]"):
            workflows = client.api.src_app_api_workflows_list_workflows()
    except Exception as e:
        _friendly_error("list workflows", e, base_url)

    if not workflows:
        console.print("[dim]No workflows found.[/dim]")
        raise click.Abort()

    options = []
    for w in workflows:
        if w.is_active:
            status = "\033[32mActive\033[0m"
        else:
            status = "\033[2mInactive\033[0m"
        name = w.name or "Unnamed"
        execution_target = "edge" if bool(getattr(w, "run_on_edge", False)) else "cloud"
        options.append(f"{name} [{status}] [{execution_target}] ({str(w.uuid)[:8]}...)")

    idx = _select_with_arrows(title, options)
    return str(workflows[idx].uuid)


@click.group()
def workflow():
    """Manage workflows for automation."""
    pass


@workflow.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@_base_url_option
def list_workflows(as_json: bool, base_url: str | None):
    """List workflows."""
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    try:
        workflows = client.api.src_app_api_workflows_list_workflows()

        if not workflows:
            console.print("[dim]No workflows found.[/dim]")
            console.print("[dim]Create one with: cyberwave workflow create[/dim]")
            return

        wf_twins: dict[str, list[str]] = {}
        with console.status("[dim]Loading workflow details...[/dim]"):
            for w in workflows:
                try:
                    nodes = client.api.src_app_api_workflows_list_workflow_nodes(
                        str(w.uuid)
                    )
                    wf_twins[str(w.uuid)] = _extract_twin_uuids(nodes)
                except Exception:
                    wf_twins[str(w.uuid)] = []

        if as_json:
            data = [
                {
                    "uuid": str(w.uuid),
                    "name": w.name,
                    "is_active": w.is_active,
                    "run_on_edge": bool(getattr(w, "run_on_edge", False)),
                    "environment_uuid": (
                        str(getattr(w, "environment_uuid", None))
                        if getattr(w, "environment_uuid", None)
                        else None
                    ),
                    "execution_target": (
                        str(getattr(w, "execution_target", None))
                        if getattr(w, "execution_target", None)
                        else None
                    ),
                    "description": w.description,
                    "twin_uuids": wf_twins.get(str(w.uuid), []),
                }
                for w in workflows
            ]
            console.print(json.dumps(data, indent=2))
            return

        table = Table(title="Workflows")
        table.add_column("Name", style="cyan")
        table.add_column("UUID", style="dim", no_wrap=True)
        table.add_column("Status")
        table.add_column("Target")
        table.add_column("Affect")
        table.add_column("Environment UUID", style="dim")
        table.add_column("Twin(s)", style="magenta")
        table.add_column("Description")

        for w in workflows:
            status = "[green]Active[/green]" if w.is_active else "[dim]Inactive[/dim]"
            desc = (
                (w.description[:30] + "...")
                if w.description and len(w.description) > 30
                else (w.description or "-")
            )
            target = "edge" if bool(getattr(w, "run_on_edge", False)) else "cloud"
            affect = getattr(w, "execution_target", None) or "-"
            environment_uuid = getattr(w, "environment_uuid", None)
            env_fmt = str(environment_uuid)[:8] + "..." if environment_uuid else "-"
            twins = wf_twins.get(str(w.uuid), [])
            twins_fmt = ", ".join(t[:8] + "..." for t in twins) if twins else "[dim]-[/dim]"
            table.add_row(
                w.name or "Unnamed",
                _format_workflow_uuid_for_table(w.uuid),
                status,
                target,
                affect,
                env_fmt,
                twins_fmt,
                desc,
            )

        console.print(table)
        console.print(f"\n[dim]Total: {len(workflows)} workflow(s)[/dim]")

    except Exception as e:
        _friendly_error("list workflows", e, base_url)


@workflow.command("templates")
def list_templates():
    """List available workflow templates."""
    table = Table(title="Workflow Templates")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Description")

    for template_id, template in WORKFLOW_TEMPLATES.items():
        table.add_row(
            template_id,
            template["name"],
            template["description"],
        )

    console.print(table)
    console.print("\n[dim]Use: cyberwave workflow create --template <id>[/dim]")


@workflow.command("create")
@click.option("--name", "-n", help="Workflow name")
@click.option("--template", "-t", type=click.Choice(list(WORKFLOW_TEMPLATES.keys())), help="Use a template")
@_base_url_option
def create_workflow(name: str | None, template: str | None, base_url: str | None):
    """Create a new workflow.
    
    Examples:
    
        # Create from template
        cyberwave workflow create --template motion-detection
        
        # Create with custom name
        cyberwave workflow create -n "My Workflow"
    """
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not template and not name:
        console.print("[red]✗[/red] Either --name or --template is required.")
        console.print("[dim]Use: cyberwave workflow templates[/dim]")
        raise click.Abort()

    try:
        # Build workflow from template or custom
        if template:
            tmpl = WORKFLOW_TEMPLATES[template]
            workflow_name = name or tmpl["name"]
            workflow_desc = tmpl["description"]
        else:
            workflow_name = name
            workflow_desc = ""

        # Get workspace for the workflow
        workspaces = client.workspaces.list()
        if not workspaces:
            console.print("[red]✗[/red] No workspace found. Create one first.")
            raise click.Abort()
        
        workspace_uuid = str(workspaces[0].uuid)

        # Create workflow via SDK
        from cyberwave.rest.models import WorkflowCreateSchema
        
        result = client.api.src_app_api_workflows_create_workflow(
            workflow_create_schema=WorkflowCreateSchema(
                name=workflow_name,
                description=workflow_desc,
                workspace_uuid=workspace_uuid,
                is_active=True,
                metadata={"created_from": "cli", "template": template},
            )
        )

        console.print(f"[green]✓[/green] Created workflow: [bold]{workflow_name}[/bold]")
        console.print(f"  UUID: {result.uuid}")
        
        if template:
            console.print(f"  Template: {template}")
        
        # Build UI URL
        api_url = resolve_api_url(base_url, load_credentials())
        ui_url = api_url.replace(":8000", ":3000").replace("api.", "")
        console.print(f"\n[dim]View in UI: {ui_url}/workflows/{result.uuid}[/dim]")
        console.print("\n[yellow]Next:[/yellow] Add nodes in the UI workflow editor.")

    except Exception as e:
        _friendly_error("create workflow", e, base_url)


@workflow.command("show")
@click.argument("uuid", required=False, default=None)
@_base_url_option
def show_workflow(uuid: str | None, base_url: str | None):
    """Show workflow details.

    If UUID is omitted, an interactive selector is shown.
    """
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not uuid:
        uuid = _pick_workflow(client, "Select a workflow to show", base_url)

    try:
        w = client.api.src_app_api_workflows_get_workflow(uuid)
        nodes = client.api.src_app_api_workflows_list_workflow_nodes(uuid)

        console.print(f"\n[bold cyan]{w.name}[/bold cyan]")
        console.print(f"  UUID:        {w.uuid}")
        status = "[green]Active[/green]" if w.is_active else "[dim]Inactive[/dim]"
        console.print(f"  Status:      {status}")
        console.print(f"  Run on edge: {bool(getattr(w, 'run_on_edge', False))}")
        console.print(
            f"  Execution target: {getattr(w, 'execution_target', None) or '[dim]None[/dim]'}"
        )
        console.print(
            f"  Environment UUID: {getattr(w, 'environment_uuid', None) or '[dim]None[/dim]'}"
        )
        console.print(f"  Description: {w.description or '[dim]None[/dim]'}")

        twin_uuids = _extract_twin_uuids(nodes)
        if twin_uuids:
            console.print("\n  [bold]Referenced Twin(s):[/bold]")
            for tid in twin_uuids:
                console.print(f"    • {tid}")
        else:
            console.print("\n  [bold]Referenced Twin(s):[/bold] [dim]None assigned[/dim]")

        if nodes:
            console.print(f"\n  [bold]Nodes ({len(nodes)}):[/bold]")
            for n in nodes:
                console.print(f"    • {n.node_type}: {n.name or n.uuid}")

    except Exception as e:
        _friendly_error("get workflow", e, base_url)


@workflow.command("sync")
@click.argument("uuid", required=False, default=None)
@click.option(
    "--force",
    is_flag=True,
    help="Skip the cloud-side preflight check and publish the MQTT sync "
    "command anyway. Use only when you've verified the workflow is "
    "compilable (e.g. the diagnostic was a false negative).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Run the preflight diagnostic and print what would be synced, "
    "but don't publish the MQTT command.",
)
@_base_url_option
def sync_workflow(
    uuid: str | None,
    force: bool,
    dry_run: bool,
    base_url: str | None,
):
    """Sync a workflow to its edge node(s).

    Reads the local edge configuration to find which twin(s) this edge serves,
    queries the cloud's ``/workflows/edge-sync/{twin}`` endpoint to verify the
    cloud will actually compile and ship this workflow to each twin, then
    publishes the ``sync_workflows`` MQTT command.

    The preflight catches the case where edge-core silently fetches a
    different workflow (or no workflow at all) — for example because
    the workflow has no ``camera_frame`` trigger or ``twin_control``
    node for the unified edge compiler to render, so the cloud's
    ``/workflows/edge-sync`` response omits it.

    If UUID is omitted, an interactive selector is shown.

    \b
    Examples:
        cyberwave workflow sync
        cyberwave workflow sync e7f1856c
        cyberwave workflow sync e7f1856c --dry-run
        cyberwave workflow sync e7f1856c --force --base-url http://192.168.10.101:8000
    """
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not uuid:
        uuid = _pick_workflow(client, "Select a workflow to sync", base_url)

    try:
        w = client.api.src_app_api_workflows_get_workflow(uuid)

        _print_workflow_metadata(w)

        if not bool(getattr(w, "run_on_edge", False)):
            console.print(
                f"\n[red]✗[/red] [bold]{w.name}[/bold] targets cloud execution, "
                "so `cyberwave workflow sync` cannot sync it to edge."
            )
            console.print(
                "[dim]  Fix: choose an [bold]edge[/bold] workflow, or change this "
                "workflow's target to edge before syncing.[/dim]"
            )
            raise click.Abort()

        nodes = client.api.src_app_api_workflows_list_workflow_nodes(uuid)
        twin_uuids = _extract_twin_uuids(nodes)

        if not twin_uuids:
            console.print(
                f"\n[red]✗[/red] [bold]{w.name}[/bold] does not reference any "
                "enabled twin UUIDs, so there is no edge target to sync to."
            )
            console.print(
                "[dim]  Fix: add a workflow node with parameters.twin_uuid "
                "or enable an existing twin-referencing node.[/dim]"
            )
            raise click.Abort()

        console.print(
            f"\n[cyan]Preflight: querying cloud edge-sync for "
            f"{len(twin_uuids)} twin(s)...[/cyan]"
        )
        if force:
            syncable, blocking = list(twin_uuids), []
            console.print(
                "[yellow]⚠[/yellow] --force given; skipping cloud-side "
                "preflight checks."
            )
        else:
            syncable, blocking = _preflight_sync(w, twin_uuids, base_url)

        if blocking:
            console.print()
            for msg in blocking:
                console.print(f"  [red]✗[/red] {msg}")

        if not syncable:
            console.print(
                f"\n[red]✗[/red] No twin will receive [bold]{w.name}[/bold] "
                "from the cloud's edge-sync endpoint, so publishing the "
                "MQTT sync command would be a no-op."
            )
            console.print(
                "[dim]  Re-run with --force to publish anyway, or fix the "
                "blocking issue(s) above and retry.[/dim]"
            )
            raise click.Abort()

        if dry_run:
            console.print(
                f"\n[yellow]●[/yellow] --dry-run: would publish "
                f"sync_workflows to {len(syncable)} twin(s); not sending."
            )
            return

        console.print(
            f"\n[cyan]Publishing sync_workflows to "
            f"{len(syncable)} twin(s)...[/cyan]"
        )
        client.mqtt.connect()
        try:
            for twin_id in sorted(syncable):
                client.mqtt.publish_command_message(
                    twin_id, {"command": "sync_workflows"}
                )
                console.print(
                    f"  [green]✓[/green] Sent sync to twin {twin_id[:8]}..."
                )
        finally:
            client.mqtt.disconnect()

        console.print(
            f"\n[green]✓[/green] Sync command sent for workflow "
            f"[bold]{w.name}[/bold]"
        )
        console.print(
            "[dim]Check edge logs for results: cyberwave edge logs[/dim]"
        )

    except click.exceptions.Abort:
        raise
    except Exception as e:
        _friendly_error("sync workflow", e, base_url)


@workflow.command("compile")
@click.argument("uuid", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, help="Output the raw API response as JSON")
@_base_url_option
def compile_workflow(uuid: str | None, as_json: bool, base_url: str | None):
    """Show which edge compiler ran for a workflow, and why.

    Wraps ``GET /api/v1/workflows/{uuid}/compile`` — the same compile
    pipeline edge-sync runs internally — and prints the verdict the
    backend would otherwise only surface in ``warnings`` or in
    ``edge_sync`` logs. Reach for it when ``cyberwave workflow sync``
    reports that the cloud isn't shipping a workflow.

    Use ``--json`` for the raw response (``compiled_payload``,
    ``worker_source``, etc.). Use ``cyberwave workflow compile-source``
    to download the generated ``wf_*.py`` directly.

    If UUID is omitted, an interactive selector is shown.

    \b
    Examples:
        cyberwave workflow compile
        cyberwave workflow compile e7f1856c
        cyberwave workflow compile e7f1856c --json
    """
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not uuid:
        uuid = _pick_workflow(client, "Select a workflow to compile", base_url)

    try:
        data = _api_get_json(f"/api/v1/workflows/{uuid}/compile", base_url=base_url)
    except _ApiError as exc:
        _friendly_error("compile workflow", exc, base_url)

    if not isinstance(data, dict):
        console.print(
            f"[red]✗[/red] Unexpected /compile response shape: {type(data).__name__}"
        )
        raise click.Abort()

    if as_json:
        console.print(json.dumps(data, indent=2))
        return

    compiled_kind = data.get("compiled_kind")
    kind_label = (
        _LABEL_FOR_KIND.get(compiled_kind) if isinstance(compiled_kind, str) else None
    )
    twin_uuid = data.get("twin_uuid")
    worker_filename = data.get("worker_filename")
    worker_source = data.get("worker_source")
    model_requirements = data.get("model_requirements") or []
    warnings = data.get("warnings") or []

    console.print(f"\n[bold cyan]Workflow {uuid}[/bold cyan]")
    # The backend's unified edge compiler always returns *some*
    # ``compiled_kind``. We only fall back to "[dim]unknown[/dim]" if
    # the response shape ever changes (e.g. an older server build), so
    # the CLI keeps rendering instead of throwing.
    if kind_label:
        artifact_line = (
            f"[bold green]{kind_label}[/bold green] "
            f"[dim](compiled_kind={compiled_kind})[/dim]"
        )
    elif compiled_kind:
        artifact_line = f"[yellow]{compiled_kind}[/yellow]"
    else:
        artifact_line = "[dim]unknown[/dim]"
    console.print(f"  artifact:         {artifact_line}")
    console.print(f"  twin:             {twin_uuid or '[dim]none[/dim]'}")
    console.print(f"  worker filename:  {worker_filename or '[dim]none[/dim]'}")
    src_status = f"{len(worker_source)} bytes" if worker_source else "[dim]none[/dim]"
    console.print(f"  worker source:    {src_status}")

    if model_requirements:
        console.print("\n  [bold]Model requirements:[/bold]")
        for req in model_requirements:
            mid = req.get("model_id", "?")
            runtime = req.get("edge_runtime") or "?"
            package = req.get("edge_package") or "?"
            path = req.get("edge_model_path") or ""
            extra = f" → {path}" if path else ""
            console.print(f"    • {mid} [dim]({runtime}/{package})[/dim]{extra}")

    if warnings:
        console.print("\n  [bold yellow]Warnings:[/bold yellow]")
        for w in warnings:
            console.print(f"    [yellow]![/yellow] {w}")
    else:
        console.print("\n  [dim]No warnings.[/dim]")


@workflow.command("compile-source")
@click.argument("uuid", required=False, default=None)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    type=click.Path(file_okay=True, dir_okay=True, writable=True, resolve_path=False),
    help="Write the worker source to this path. If a directory, "
    "wf_<uuid>.py is appended. Defaults to printing to stdout.",
)
@_base_url_option
def compile_workflow_source(uuid: str | None, output: str | None, base_url: str | None):
    """Download the generated ``wf_*.py`` worker for a workflow.

    Wraps ``GET /api/v1/workflows/{uuid}/compile/source`` — the same
    bytes the edge fetches via ``edge-sync``. Compilations that don't
    produce Python source (e.g. a navigation workflow without
    ``run_on_edge=true``) return a clear hint to run ``cyberwave
    workflow compile`` instead.

    If UUID is omitted, an interactive selector is shown.

    \b
    Examples:
        cyberwave workflow compile-source e7f1856c
        cyberwave workflow compile-source e7f1856c -o ./wf.py
        cyberwave workflow compile-source e7f1856c -o /tmp/
    """
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not uuid:
        uuid = _pick_workflow(client, "Select a workflow", base_url)

    try:
        text = _api_get_text(
            f"/api/v1/workflows/{uuid}/compile/source", base_url=base_url
        )
    except _ApiError as exc:
        if exc.status == 404:
            console.print(
                "[yellow]●[/yellow] This workflow has no downloadable worker source "
                "(likely a navigation workflow without run_on_edge=true). "
                f"Run [bold]cyberwave workflow compile {uuid}[/bold] to see the "
                "compiler verdict instead."
            )
            raise click.Abort() from exc
        _friendly_error("download worker source", exc, base_url)

    if output is None:
        click.echo(text, nl=False)
        return

    # The backend names worker files ``wf_<uuid_hex[:12]>.py`` (see
    # ``download_workflow_source`` in cyberwave-backend); reconstruct
    # the same filename from the UUID rather than parsing
    # Content-Disposition.
    output_path = Path(output)
    if output_path.is_dir() or output.endswith(("/", "\\")):
        output_path = output_path / f"wf_{uuid.replace('-', '')[:12]}.py"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    console.print(
        f"[green]✓[/green] Wrote {len(text)} bytes to [bold]{output_path}[/bold]"
    )


@workflow.command("activate")
@click.argument("uuid", required=False, default=None)
@_base_url_option
def activate_workflow(uuid: str | None, base_url: str | None):
    """Activate a workflow.

    If UUID is omitted, an interactive selector is shown.
    """
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not uuid:
        uuid = _pick_workflow(client, "Select a workflow to activate", base_url)

    try:
        client.api.src_app_api_workflows_activate_workflow(uuid)
        console.print(f"[green]✓[/green] Workflow activated: {uuid}")
    except Exception as e:
        _friendly_error("activate workflow", e, base_url)


@workflow.command("deactivate")
@click.argument("uuid", required=False, default=None)
@_base_url_option
def deactivate_workflow(uuid: str | None, base_url: str | None):
    """Deactivate a workflow.

    If UUID is omitted, an interactive selector is shown.
    """
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not uuid:
        uuid = _pick_workflow(client, "Select a workflow to deactivate", base_url)

    try:
        client.api.src_app_api_workflows_deactivate_workflow(uuid)
        console.print(f"[green]✓[/green] Workflow deactivated: {uuid}")
    except Exception as e:
        _friendly_error("deactivate workflow", e, base_url)


@workflow.command("delete")
@click.argument("uuid", required=False, default=None)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@_base_url_option
def delete_workflow(uuid: str | None, yes: bool, base_url: str | None):
    """Delete a workflow.

    If UUID is omitted, an interactive selector is shown.
    """
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not uuid:
        uuid = _pick_workflow(client, "Select a workflow to delete", base_url)

    if not yes:
        if not click.confirm(f"Delete workflow {uuid}?"):
            raise click.Abort()

    try:
        client.api.src_app_api_workflows_delete_workflow(uuid)
        console.print(f"[green]✓[/green] Deleted workflow: {uuid}")
    except Exception as e:
        _friendly_error("delete workflow", e, base_url)
