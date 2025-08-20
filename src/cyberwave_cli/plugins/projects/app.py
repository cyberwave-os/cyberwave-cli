import asyncio
from typing import List, Dict

import typer
from rich import print

from cyberwave import Client

app = typer.Typer(help="Manage projects within a workspace")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Project name"),
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace UUID"),
) -> None:
    """Create a new project."""

    async def _run() -> None:
        client = Client()
        await client.login()
        # Ensure workspace_id is numeric if API expects int
        try:
            ws_id = int(workspace)
        except Exception:
            ws_id = workspace
        project = await client.create_project(workspace_id=ws_id, name=name)
        print(f":white_check_mark: Created project [bold]{name}[/bold] (UUID {project.get('uuid') or project.get('id')})")

    asyncio.run(_run())


@app.command("list")
def list_projects(
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace UUID"),
) -> None:
    """List projects within a workspace."""

    async def _run() -> None:
        client = Client()
        await client.login()
        try:
            ws_id = int(workspace)
        except Exception:
            ws_id = workspace
        projects: List[Dict] = await client.get_projects(workspace_id=ws_id)
        for proj in projects:
            print(f"{proj.get('uuid') or proj.get('id')}: {proj.get('name')}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()

