import asyncio
import typer
from rich import print

from cyberwave import Client

app = typer.Typer(help="Manage environments")


@app.command("create")
def create(
    project: str = typer.Option(..., "--project", "-p", help="Project UUID"),
    name: str = typer.Option(..., "--name", "-n"),
    description: str = typer.Option("", "--description", "-d"),
):
    async def _run():
        client = Client()
        await client.login()
        env = await client.create_environment(project_uuid=project, name=name, description=description)
        print(f"[green]✓[/green] Created environment [bold]{env.get('name')}[/bold] (UUID {env.get('uuid')})")
        await client.aclose()
    asyncio.run(_run())


@app.command("list")
def list_envs(project: str = typer.Option(..., "--project", "-p", help="Project UUID")):
    async def _run():
        client = Client()
        await client.login()
        envs = await client.get_environments(project_uuid=project)
        for e in envs:
            print(f"- {e.get('name')} ({e.get('uuid')})")
        await client.aclose()
    asyncio.run(_run())


@app.command("events")
def list_events(
    environment: str = typer.Option(..., "--environment", "-e", help="Environment UUID"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max entries to show"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON output"),
):
    """List recent environment events (latest session per twin), including segment thumbnails when available."""
    async def _run():
        client = Client()
        await client.login()
        resp = await client._client.get(f"/environments/{environment}/events", headers=client._get_headers())
        resp.raise_for_status()
        data = resp.json()
        from datetime import datetime
        if json_out:
            import json as _json
            print(_json.dumps(data[:limit], indent=2))
        else:
            shown = 0
            for item in data:
                if shown >= limit:
                    break
                twin = item.get("twin_uuid")
                sess = item.get("session", {})
                seg = item.get("segment") or {}
                ts = sess.get("started_at_unix")
                ts_str = datetime.fromtimestamp(ts).isoformat() if ts else "?"
                seg_url = seg.get("url")
                seg_key = seg.get("storage_key")
                print(f"- twin={twin} started_at={ts_str} segment_key={seg_key} segment_url={seg_url}")
                shown += 1
        await client.aclose()
    asyncio.run(_run())


if __name__ == "__main__":
    app()


