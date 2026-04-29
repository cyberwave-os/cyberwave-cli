"""Workflow management commands for the Cyberwave CLI."""

from __future__ import annotations

import json
from typing import NoReturn
from urllib.parse import urlparse

import click
from rich.console import Console
from rich.table import Table

from ..config import get_api_url
from ..utils import get_sdk_client

console = Console()


def _friendly_error(action: str, exc: Exception, base_url: str | None = None) -> NoReturn:
    """Print a user-friendly error message and abort."""
    url = base_url or get_api_url()
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


def _extract_twin_uuids(nodes) -> list[str]:
    """Extract unique twin UUIDs from a workflow's trigger nodes."""
    seen: set[str] = set()
    result: list[str] = []
    for n in nodes:
        if n.node_type == "trigger" and n.trigger_type == "camera_frame":
            tid = (n.parameters or {}).get("twin_uuid")
            if tid and tid not in seen:
                seen.add(tid)
                result.append(tid)
    return result


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
        options.append(f"{name} [{status}] ({str(w.uuid)[:8]}...)")

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
                    "description": w.description,
                    "twin_uuids": wf_twins.get(str(w.uuid), []),
                }
                for w in workflows
            ]
            console.print(json.dumps(data, indent=2))
            return

        table = Table(title="Workflows")
        table.add_column("Name", style="cyan")
        table.add_column("UUID", style="dim")
        table.add_column("Status")
        table.add_column("Twin(s)", style="magenta")
        table.add_column("Description")

        for w in workflows:
            status = "[green]Active[/green]" if w.is_active else "[dim]Inactive[/dim]"
            desc = (w.description[:30] + "...") if w.description and len(w.description) > 30 else (w.description or "-")
            twins = wf_twins.get(str(w.uuid), [])
            twins_fmt = ", ".join(t[:8] + "..." for t in twins) if twins else "[dim]-[/dim]"
            table.add_row(
                w.name or "Unnamed",
                str(w.uuid)[:8] + "...",
                status,
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
        api_url = base_url or get_api_url()
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
        console.print(f"  Status:      {'[green]Active[/green]' if w.is_active else '[dim]Inactive[/dim]'}")
        console.print(f"  Description: {w.description or '[dim]None[/dim]'}")

        twin_uuids = _extract_twin_uuids(nodes)
        if twin_uuids:
            console.print("\n  [bold]Target Twin(s):[/bold]")
            for tid in twin_uuids:
                console.print(f"    • {tid}")
        else:
            console.print("\n  [bold]Target Twin(s):[/bold] [dim]None assigned[/dim]")

        if nodes:
            console.print(f"\n  [bold]Nodes ({len(nodes)}):[/bold]")
            for n in nodes:
                console.print(f"    • {n.node_type}: {n.name or n.uuid}")

    except Exception as e:
        _friendly_error("get workflow", e, base_url)


@workflow.command("sync")
@click.argument("uuid", required=False, default=None)
@_base_url_option
def sync_workflow(uuid: str | None, base_url: str | None):
    """Sync a workflow to its edge node(s).

    Reads the workflow's trigger nodes to find which twin(s) it targets,
    then sends a sync command to each twin's edge node via MQTT.

    If UUID is omitted, an interactive selector is shown.

    \b
    Examples:
        cyberwave workflow sync
        cyberwave workflow sync e7f1856c
        cyberwave workflow sync e7f1856c --base-url http://192.168.10.101:8000
    """
    client = get_sdk_client(api_url=base_url)
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not uuid:
        uuid = _pick_workflow(client, "Select a workflow to sync", base_url)

    try:
        w = client.api.src_app_api_workflows_get_workflow(uuid)
        nodes = client.api.src_app_api_workflows_list_workflow_nodes(uuid)
        twin_uuids = _extract_twin_uuids(nodes)

        if not twin_uuids:
            console.print(
                f"[yellow]⚠[/yellow] Workflow [bold]{w.name}[/bold] has no trigger nodes "
                "with a twin assigned."
            )
            console.print(
                "[dim]Assign a twin to a camera_frame trigger node in the workflow editor.[/dim]"
            )
            raise click.Abort()

        console.print(
            f"[cyan]Syncing workflow [bold]{w.name}[/bold] "
            f"to {len(twin_uuids)} twin(s)...[/cyan]"
        )

        client.mqtt.connect()
        try:
            for twin_id in sorted(twin_uuids):
                client.mqtt.publish_command_message(
                    twin_id, {"command": "sync_workflows"}
                )
                console.print(f"  [green]✓[/green] Sent sync to twin {twin_id[:8]}...")
        finally:
            client.mqtt.disconnect()

        console.print(
            f"\n[green]✓[/green] Sync command sent for workflow [bold]{w.name}[/bold]"
        )
        console.print("[dim]Check edge logs for results: cyberwave edge logs[/dim]")

    except click.exceptions.Abort:
        raise
    except Exception as e:
        _friendly_error("sync workflow", e, base_url)


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
