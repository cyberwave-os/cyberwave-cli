import asyncio
import typer
from rich import print
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm
from typing import Optional, List

from cyberwave import Client

console = Console()
app = typer.Typer(help="🏠 Manage environments and spatial setups")


@app.command("create")
def create(
    project: str = typer.Option(..., "--project", "-p", help="Project UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Environment name"),
    description: str = typer.Option("", "--description", "-d", help="Environment description"),
    environment_type: str = typer.Option("general", "--type", help="Environment type: general, laboratory, factory, home"),
    dimensions: Optional[str] = typer.Option(None, "--dimensions", help="Dimensions as 'WxHxD' (e.g., '10x3x8')"),
    setup_cameras: bool = typer.Option(False, "--setup-cameras", help="Setup for camera-based monitoring"),
):
    """🏗️ Create a new environment for digital twins and sensors."""
    async def _run():
        client = Client()
        await client.login()
        
        # Parse dimensions if provided
        env_metadata = {"type": environment_type}
        if dimensions:
            try:
                w, h, d = map(float, dimensions.split('x'))
                env_metadata["dimensions"] = {"width": w, "height": h, "depth": d}
                console.print(f"📐 Dimensions: [cyan]{w}m x {h}m x {d}m[/cyan]")
            except ValueError:
                console.print("[yellow]⚠️ Invalid dimensions format, use 'WxHxD'[/yellow]")
        
        # Enhanced description for camera setups
        if setup_cameras:
            if not description:
                description = f"Camera monitoring environment for {name}"
            env_metadata["camera_ready"] = True
            env_metadata["suggested_sensors"] = ["camera", "motion_detector"]
        
        env = await client.create_environment(
            project_uuid=project, 
            name=name, 
            description=description,
            metadata=env_metadata
        )
        
        env_uuid = env.get('uuid')
        console.print(f"[green]✅ Created environment[/green] [bold]{env.get('name')}[/bold]")
        console.print(f"📋 UUID: [cyan]{env_uuid}[/cyan]")
        console.print(f"🏠 Type: [blue]{environment_type}[/blue]")
        
        if setup_cameras:
            console.print("\n[bold]🎯 Camera Setup Next Steps:[/bold]")
            console.print("1. Discover cameras:", "[cyan]cyberwave edge camera discover[/cyan]")
            console.print("2. Register cameras:", f"[cyan]cyberwave edge camera register --environment {env_uuid} --camera <IP>[/cyan]")
            console.print("3. Setup edge node:", f"[cyan]cyberwave edge init --device-type camera --environment {env_uuid}[/cyan]")
        
        await client.aclose()
    asyncio.run(_run())


@app.command("setup-camera-lab")
def setup_camera_lab(
    project: str = typer.Option(..., "--project", "-p", help="Project UUID"),
    name: str = typer.Option("Camera Lab", "--name", "-n", help="Environment name"),
    auto_discover: bool = typer.Option(True, "--auto-discover/--no-auto-discover", help="Auto-discover cameras"),
    dimensions: str = typer.Option("5x3x3", "--dimensions", help="Lab dimensions as 'WxHxD'"),
    enable_analytics: bool = typer.Option(True, "--analytics/--no-analytics", help="Enable computer vision analytics"),
):
    """🧪 Setup a complete camera laboratory environment with auto-discovery."""
    
    console.print("🧪 [bold blue]Setting up Camera Laboratory Environment[/bold blue]")
    
    async def _run():
        client = Client()
        await client.login()
        
        # Create environment
        console.print(f"🏗️ Creating environment: [cyan]{name}[/cyan]")
        
        try:
            w, h, d = map(float, dimensions.split('x'))
            env_metadata = {
                "type": "laboratory",
                "purpose": "camera_testing_and_analytics",
                "dimensions": {"width": w, "height": h, "depth": d},
                "camera_ready": True,
                "auto_discovery_enabled": auto_discover,
                "analytics_enabled": enable_analytics,
                "suggested_sensors": ["camera", "motion_detector", "light_sensor"]
            }
        except ValueError:
            console.print("[red]❌ Invalid dimensions format[/red]")
            raise typer.Exit(1)
        
        env = await client.create_environment(
            project_uuid=project,
            name=name,
            description=f"Camera laboratory environment with {w}x{h}x{d}m workspace",
            metadata=env_metadata
        )
        
        env_uuid = env.get('uuid')
        console.print(f"✅ Environment created: [green]{env_uuid}[/green]")
        
        await client.aclose()
        
        # Show complete setup workflow
        console.print("\n[bold]🎯 Complete Camera Lab Setup Workflow:[/bold]")
        console.print("\n[bold]Step 1: Discover Cameras[/bold]")
        console.print("  [cyan]cyberwave edge camera discover --network auto --save[/cyan]")
        
        console.print("\n[bold]Step 2: Register Discovered Cameras[/bold]")
        console.print(f"  [cyan]cyberwave edge camera register --environment {env_uuid} --camera <IP1>[/cyan]")
        console.print(f"  [cyan]cyberwave edge camera register --environment {env_uuid} --camera <IP2>[/cyan]")
        
        console.print("\n[bold]Step 3: Setup Edge Node for Analysis[/bold]")
        console.print(f"  [cyan]cyberwave edge init --device-type camera --environment {env_uuid}[/cyan]")
        
        console.print("\n[bold]Step 4: Start Computer Vision Analytics[/bold]")
        console.print("  [cyan]cyberwave edge run --enable-motion-detection --enable-object-detection[/cyan]")
        
        console.print("\n[bold]Step 5: Monitor and Analyze[/bold]")
        console.print("  [cyan]cyberwave edge camera stream --camera <IP> --preview[/cyan]")
        console.print("  [cyan]cyberwave edge camera analyze --camera <IP> --type motion[/cyan]")
        console.print("  [cyan]cyberwave edge status --detailed[/cyan]")
        
        console.print(f"\n💡 [dim]Environment UUID for future reference: {env_uuid}[/dim]")
    
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


