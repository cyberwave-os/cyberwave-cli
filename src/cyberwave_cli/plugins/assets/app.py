import asyncio
import re
from pathlib import Path
from typing import Optional

import typer
from rich import print

from cyberwave import Client

app = typer.Typer(help="Manage CyberWave assets (upload meshes to the catalog, list).")


def _simple_slugify(text: str) -> str:
    """Very small helper to create URL-friendly slugs."""
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", text.lower()).strip("-")
    return slug


async def _upload_asset(
    mesh_path: Path,
    name: str,
    workspace_uuid: str,
    project_uuid: str,
    description: Optional[str],
    registry_id: Optional[str],
) -> None:
    client = Client()
    await client.login()
    # Create a simple catalog asset and attach a GLB file
    # Prefer high-level helpers if present (test stub API)
    if hasattr(client, "create_asset_definition"):
        created = await client.create_asset_definition(name=name)
        await client.upload_mesh(created["id"], mesh_path)
        await client.add_geometry_to_asset_definition(created["id"])
        print(f":white_check_mark: Created asset '[bold]{name}[/bold]' (ID {created['id']}) and uploaded GLB")
        return
    # Fallback to raw request + upload the GLB
    created = await client._request(
        "POST",
        "/assets",
        json={
            "name": name,
            "description": description or "",
            "public": False,
            "registry_id": registry_id,
        },
    )
    asset = created.json() if hasattr(created, "json") else created
    await client.upload_asset_glb(asset.get("uuid") or asset.get("id"), mesh_path)
    # If no thumbnail, attempt to auto-generate a simple one from the GLB path (placeholder icon)
    # Backend may also generate thumbnails asynchronously; this is a best-effort client-side fallback.
    try:
        # For now, rely on backend pipeline; printing hint for the user.
        print(":bulb: If the asset lacks a thumbnail, the backend will attempt to generate one.")
    except Exception:
        pass
    print(f":white_check_mark: Created asset '[bold]{name}[/bold]' (UUID {asset['uuid']}) and uploaded GLB")

@app.command()
def upload(
    mesh: Path = typer.Argument(..., exists=True, help="Path to a mesh file"),
    name: str = typer.Option(..., "--name", help="Name for the catalog entry"),
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace UUID"),
    project: str = typer.Option(..., "--project", "-p", help="Project UUID (for future)"),
    registry_id: Optional[str] = typer.Option(None, "--registry-id", help="Catalog registry identifier (e.g., so/100)"),
    description: Optional[str] = typer.Option(None, "--description", help="Description"),
):
    """Upload a mesh and create a catalog asset definition."""

    asyncio.run(_upload_asset(mesh, name, workspace, project, description, registry_id))

@app.command("list") # Explicit command name
def list_assets():
    """List available assets in CyberWave storage."""
    async def _run() -> None:
        client = Client()
        await client.login()
        assets = await client.list_assets()
        for a in assets:
            rid = a.get("registry_id") or (a.get("metadata", {}) or {}).get("registry_id")
            print(f"{a.get('uuid')}  {a.get('name')}  registry_id={rid}")

    asyncio.run(_run())

if __name__ == "__main__":
    app()
