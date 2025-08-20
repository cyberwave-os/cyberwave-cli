import asyncio
import typer
from rich import print

from cyberwave import Client
import httpx
import json

app = typer.Typer(help="Manage sensors")


@app.command("list")
def list_sensors(environment: str = typer.Option(..., "--environment", "-e", help="Environment UUID")):
    async def _run():
        client = Client()
        await client.login()
        sensors = await client._client.get(f"/environments/{environment}/sensors", headers=client._get_headers())
        sensors.raise_for_status()
        for s in sensors.json():
            print(f"- {s.get('name')} ({s.get('uuid')}) type={s.get('sensor_type')} twin={s.get('twin_uuid')}")
        await client.aclose()
    asyncio.run(_run())


@app.command("events")
def sensor_events(
    sensor_uuid: str = typer.Option(..., "--sensor", "-s"),
    environment: str = typer.Option(None, "--environment", "-e", help="Environment UUID (to locate latest session)"),
    limit: int = typer.Option(50, "--limit", "-n"),
):
    """Tail analyzer events for a sensor.

    - Backend mode (default): polls /api/v1/sensors/{sensor_uuid}/events and prints latest events.
    - Node+session mode (fallback): if --environment is provided and there is an active teleop session,
      read session NDJSON and filter events for the sensor.
    """
    async def _run():
        client = Client()
        await client.login()
        # Preferred: backend mode
        try:
            r = await client._client.get(f"/sensors/{sensor_uuid}/events", headers=client._get_headers())
            r.raise_for_status()
            events = r.json()
            for ev in events[-limit:]:
                print(ev)
            await client.aclose(); return
        except httpx.HTTPStatusError:
            pass

        # Fallback: node+session mode requires environment to locate latest session
        if not environment:
            print("[yellow]Backend events endpoint unavailable. Provide --environment to fallback to session log tailing.[/yellow]")
            await client.aclose(); return
        sensors = await client._client.get(f"/environments/{environment}/sensors", headers=client._get_headers())
        sensors.raise_for_status()
        twin_uuid = None
        for s in sensors.json():
            if s.get("uuid") == sensor_uuid:
                twin_uuid = s.get("twin_uuid")
                break
        if not twin_uuid:
            print(f"[red]Sensor {sensor_uuid} not found in environment {environment}[/red]")
            await client.aclose(); return
        evs = await client._client.get(f"/environments/{environment}/events", headers=client._get_headers())
        evs.raise_for_status()
        session_id = None
        for row in evs.json():
            if row.get("twin_uuid") == twin_uuid:
                session_id = row.get("session", {}).get("session_id")
                break
        if not session_id:
            print(f"[yellow]No sessions found for twin {twin_uuid}[/yellow]")
            await client.aclose(); return
        r = await client._client.get(f"/twins/{twin_uuid}/teleop/sessions/{session_id}/events", headers=client._get_headers())
        r.raise_for_status()
        count = 0
        for line in r.text.splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") == "event" and obj.get("payload", {}).get("sensor_uuid") == sensor_uuid:
                analyzer = obj.get("payload", {}).get("analyzer")
                ts = obj.get("ts_unix")
                print(f"{ts} analyzer={analyzer} event={obj.get('payload')}")
                count += 1
                if count >= limit:
                    break
        await client.aclose()
    asyncio.run(_run())

@app.command("create")
def create_sensor(
    environment: str = typer.Option(..., "--environment", "-e"),
    name: str = typer.Option(..., "--name", "-n"),
    sensor_type: str = typer.Option("camera", "--type", "-t"),
    twin: str = typer.Option(None, "--twin"),
):
    async def _run():
        client = Client()
        await client.login()
        payload = {"name": name, "sensor_type": sensor_type, "twin_uuid": twin}
        resp = await client._client.post(f"/environments/{environment}/sensors", json=payload, headers=client._get_headers())
        resp.raise_for_status()
        s = resp.json()
        print(f"[green]✓[/green] Created sensor [bold]{s.get('name')}[/bold] (UUID {s.get('uuid')})")
        await client.aclose()
    asyncio.run(_run())


@app.command("update")
def update_sensor(
    environment: str = typer.Option(..., "--environment", "-e"),
    sensor_uuid: str = typer.Option(..., "--sensor", "-s"),
    name: str = typer.Option(None, "--name"),
    sensor_type: str = typer.Option(None, "--type"),
    twin: str = typer.Option(None, "--twin"),
):
    async def _run():
        client = Client()
        await client.login()
        payload = {}
        if name is not None: payload["name"] = name
        if sensor_type is not None: payload["sensor_type"] = sensor_type
        if twin is not None: payload["twin_uuid"] = twin
        resp = await client._client.put(f"/environments/{environment}/sensors/{sensor_uuid}", json=payload, headers=client._get_headers())
        resp.raise_for_status()
        s = resp.json()
        print(f"[green]✓[/green] Updated sensor [bold]{s.get('name')}[/bold] (UUID {s.get('uuid')})")
        await client.aclose()
    asyncio.run(_run())


if __name__ == "__main__":
    app()


