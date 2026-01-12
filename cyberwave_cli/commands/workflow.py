"""Workflow management commands for the Cyberwave CLI."""

import json
import click
from rich.console import Console
from rich.table import Table

from ..config import get_api_url
from ..credentials import load_credentials

console = Console()


def get_sdk_client():
    """Get Cyberwave SDK client."""
    creds = load_credentials()
    if not creds or not creds.token:
        return None
    try:
        from cyberwave import Cyberwave
        return Cyberwave(base_url=get_api_url(), token=creds.token)
    except ImportError:
        return None


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


@click.group()
def workflow():
    """Manage workflows for automation."""
    pass


@workflow.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_workflows(as_json: bool):
    """List workflows."""
    client = get_sdk_client()
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    try:
        workflows = client.api.src_app_api_workflows_list_workflows()

        if as_json:
            data = [
                {
                    "uuid": str(w.uuid),
                    "name": w.name,
                    "is_active": w.is_active,
                    "description": w.description,
                }
                for w in workflows
            ]
            console.print(json.dumps(data, indent=2))
            return

        if not workflows:
            console.print("[dim]No workflows found.[/dim]")
            console.print("[dim]Create one with: cyberwave-cli workflow create[/dim]")
            return

        table = Table(title="Workflows")
        table.add_column("Name", style="cyan")
        table.add_column("UUID", style="dim")
        table.add_column("Status")
        table.add_column("Description")

        for w in workflows:
            status = "[green]Active[/green]" if w.is_active else "[dim]Inactive[/dim]"
            desc = (w.description[:30] + "...") if w.description and len(w.description) > 30 else (w.description or "-")
            table.add_row(
                w.name or "Unnamed",
                str(w.uuid)[:8] + "...",
                status,
                desc,
            )

        console.print(table)
        console.print(f"\n[dim]Total: {len(workflows)} workflow(s)[/dim]")

    except Exception as e:
        console.print(f"[red]✗[/red] Failed to list workflows: {e}")
        raise click.Abort()


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
    console.print("\n[dim]Use: cyberwave-cli workflow create --template <id>[/dim]")


@workflow.command("create")
@click.option("--name", "-n", help="Workflow name")
@click.option("--template", "-t", type=click.Choice(list(WORKFLOW_TEMPLATES.keys())), help="Use a template")
def create_workflow(name: str | None, template: str | None):
    """Create a new workflow.
    
    Examples:
    
        # Create from template
        cyberwave-cli workflow create --template motion-detection
        
        # Create with custom name
        cyberwave-cli workflow create -n "My Workflow"
    """
    client = get_sdk_client()
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not template and not name:
        console.print("[red]✗[/red] Either --name or --template is required.")
        console.print("[dim]Use: cyberwave-cli workflow templates[/dim]")
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
        api_url = get_api_url()
        ui_url = api_url.replace(":8000", ":3000").replace("api.", "")
        console.print(f"\n[dim]View in UI: {ui_url}/workflows/{result.uuid}[/dim]")
        console.print("\n[yellow]Next:[/yellow] Add nodes in the UI workflow editor.")

    except Exception as e:
        console.print(f"[red]✗[/red] Failed to create workflow: {e}")
        raise click.Abort()


@workflow.command("show")
@click.argument("uuid")
def show_workflow(uuid: str):
    """Show workflow details."""
    client = get_sdk_client()
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    try:
        w = client.api.src_app_api_workflows_get_workflow(uuid)
        
        console.print(f"\n[bold cyan]{w.name}[/bold cyan]")
        console.print(f"  UUID: {w.uuid}")
        console.print(f"  Status: {'Active' if w.is_active else 'Inactive'}")
        console.print(f"  Description: {w.description or 'None'}")
        
        # Get nodes
        nodes = client.api.src_app_api_workflows_list_workflow_nodes(uuid)
        if nodes:
            console.print(f"\n  [bold]Nodes ({len(nodes)}):[/bold]")
            for n in nodes:
                console.print(f"    • {n.node_type}: {n.name or n.uuid}")

    except Exception as e:
        console.print(f"[red]✗[/red] Failed to get workflow: {e}")
        raise click.Abort()


@workflow.command("activate")
@click.argument("uuid")
def activate_workflow(uuid: str):
    """Activate a workflow."""
    client = get_sdk_client()
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    try:
        client.api.src_app_api_workflows_activate_workflow(uuid)
        console.print(f"[green]✓[/green] Workflow activated: {uuid}")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to activate workflow: {e}")
        raise click.Abort()


@workflow.command("deactivate")
@click.argument("uuid")
def deactivate_workflow(uuid: str):
    """Deactivate a workflow."""
    client = get_sdk_client()
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    try:
        client.api.src_app_api_workflows_deactivate_workflow(uuid)
        console.print(f"[green]✓[/green] Workflow deactivated: {uuid}")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to deactivate workflow: {e}")
        raise click.Abort()


@workflow.command("delete")
@click.argument("uuid")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def delete_workflow(uuid: str, yes: bool):
    """Delete a workflow."""
    client = get_sdk_client()
    if not client:
        console.print("[red]✗[/red] Not logged in or SDK not installed.")
        raise click.Abort()

    if not yes:
        if not click.confirm(f"Delete workflow {uuid}?"):
            raise click.Abort()

    try:
        client.api.src_app_api_workflows_delete_workflow(uuid)
        console.print(f"[green]✓[/green] Deleted workflow: {uuid}")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to delete workflow: {e}")
        raise click.Abort()
