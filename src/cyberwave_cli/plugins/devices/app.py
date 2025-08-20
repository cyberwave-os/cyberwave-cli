import asyncio
from typing import Optional

import typer
from rich import print

from cyberwave import Client

app = typer.Typer(help="Manage devices (registration and tokens)")


@app.command("register")
def register_device(
    project: int = typer.Option(..., "--project", "-p", help="Project ID"),
    name: str = typer.Option(..., "--name", "-n", help="Device name"),
    type: str = typer.Option(..., "--type", "-t", help="Device type"),
    asset: Optional[str] = typer.Option(None, "--asset", "-a", help="Asset catalog UUID"),
) -> None:
    """Register a new device."""

    async def _run() -> None:
        client = Client()
        await client.login()
        device = await client.register_device(
            project_id=project,
            name=name,
            device_type=type,
            asset_catalog_uuid=asset,
        )
        print(f":white_check_mark: Registered device [bold]{name}[/bold] (ID {device['id']})")

    asyncio.run(_run())


@app.command("issue-offline-token")
def issue_offline_token(device: int = typer.Option(..., "--device", "-d", help="Device ID")) -> None:
    """Issue an offline token for a device."""

    async def _run() -> None:
        client = Client()
        await client.login()
        token = await client.issue_device_token(device_id=device)
        print(f":key: Offline token for device {device}: [bold]{token}[/bold]")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
