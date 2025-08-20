import asyncio
import json
from typing import Optional

import typer
from rich import print

from cyberwave import Client

app = typer.Typer(help="Twin commands and control (teleop)")


@app.command("command")
def send_command(
    twin: str = typer.Option(..., "--twin", "-t", help="Twin UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Command name, e.g., arm.move_joints"),
    joints: Optional[str] = typer.Option(None, "--joints", help="JSON list of joint positions for arm.move_joints"),
    pose: Optional[str] = typer.Option(None, "--pose", help='JSON dict pose for arm.move_pose/move_to (e.g., {"x":0.1, "y":0.2, "z":0.0})'),
    mode: str = typer.Option("both", "--mode", help="sim|real|both"),
    source: Optional[str] = typer.Option("cli", "--source", help="Source tag, e.g., cli/web-ui/edge-leader"),
) -> None:
    """Send a unified command to a twin via the backend TeleopControllerService."""

    async def _run():
        client = Client()
        await client.login()

        payload: dict = {"name": name, "payload": {}, "mode": mode, "source": source}
        if name.endswith("move_joints") and joints:
            payload["payload"] = {"joints": json.loads(joints)}
        elif name.endswith("move_pose") or name.endswith("move_to") or name.endswith("fly_to"):
            if pose:
                payload["payload"] = {"pose": json.loads(pose)}
        # Fallback: if user gave raw payload via pose or joints json that doesn't match name, just pass through

        # Use SDK internals to POST
        headers = client._get_headers()  # type: ignore[attr-defined]
        resp = await client._client.post(f"/twins/{twin}/commands", json=payload, headers=headers)  # type: ignore[attr-defined]
        if resp.status_code >= 400:
            print(f"[red]Command failed: {resp.status_code} {resp.text}[/red]")
        else:
            print(f"[green]✓ Command sent: {name}[/green]")
        await client.aclose()

    asyncio.run(_run())


@app.command("apply-defaults")
def apply_defaults(twin: str = typer.Option(..., "--twin", "-t", help="Twin UUID")) -> None:
    """Apply asset catalog state defaults (pose, joints, logical) to the twin."""
    async def _run():
        client = Client()
        await client.login()
        headers = client._get_headers()
        resp = await client._client.post(f"/twins/{twin}/apply-defaults", headers=headers)
        if resp.status_code >= 400:
            print(f"[red]Failed to apply defaults: {resp.status_code} {resp.text}[/red]")
        else:
            print(f"[green]✓ Defaults applied[/green] {resp.json()}")
        await client.aclose()
    asyncio.run(_run())

if __name__ == "__main__":
    app()


