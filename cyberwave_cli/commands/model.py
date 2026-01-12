"""
CLI commands for managing local ML models on edge nodes.

For most use cases, configure ML analysis via UI Workflows:
  1. Create workflow with 'Camera Frame' trigger
  2. Add 'Call Model' node (edge-compatible model)
  3. Edge auto-syncs and runs analysis

CLI commands are for local/offline configuration:
    cyberwave model list       # List available models
    cyberwave model bind ...   # Configure local model
    cyberwave model show       # Show current config
"""

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ..config import get_api_url
from ..constants import get_fallback_models
from ..credentials import load_credentials

console = Console()


def fetch_edge_models() -> list[dict]:
    """Fetch edge-compatible models from the backend API using the SDK."""
    creds = load_credentials()
    if not creds or not creds.token:
        return []
    
    try:
        from cyberwave import Cyberwave
        client = Cyberwave(base_url=get_api_url(), token=creds.token)
        # Use raw API call for mlmodels (not a standard resource manager)
        models = client.api.mlmodels_list()
        return [
            {
                "uuid": str(m.uuid),
                "name": m.name,
                "description": m.description,
                "model_external_id": m.model_external_id,
                "model_provider_name": m.model_provider_name,
                "deployment": m.deployment,
                "is_edge_compatible": getattr(m, "is_edge_compatible", False),
                "metadata": getattr(m, "metadata", {}),
            }
            for m in models
            if getattr(m, "is_edge_compatible", False)
        ]
    except ImportError:
        console.print("[yellow]Cyberwave SDK not installed, using fallback[/yellow]")
        return []
    except Exception:
        return []


@click.group()
def model():
    """Manage local ML models for edge inference."""
    pass


@model.command("list")
@click.option("--deployment", "-d", type=click.Choice(["all", "edge", "cloud", "hybrid"]), default="edge", help="Filter by deployment type")
def list_models(deployment):
    """List available ML models from the catalog."""
    
    api_models = fetch_edge_models()
    
    table = Table(title="Available ML Models")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Provider")
    table.add_column("Deploy", style="yellow")
    table.add_column("Description")

    if api_models:
        for m in api_models:
            model_deploy = m.get("deployment", "cloud")
            if deployment != "all" and model_deploy != deployment:
                continue
            desc = m.get("description", "")
            table.add_row(
                m.get("model_external_id", "?"),
                m.get("name", "?"),
                m.get("model_provider_name", "?"),
                model_deploy.upper(),
                (desc[:50] + "...") if len(desc) > 50 else desc,
            )
        console.print(table)
        console.print(f"\n[dim]{len(api_models)} edge-compatible models from catalog[/dim]")
    else:
        console.print("[yellow]API unavailable - showing local fallback models[/yellow]\n")
        fallback = get_fallback_models()
        for model_id, info in fallback.items():
            table.add_row(
                model_id,
                info["name"],
                info.get("runtime", "?"),
                "EDGE",
                info.get("description", ""),
            )
        console.print(table)

    console.print("\n[dim]Tip: Configure ML analysis via UI Workflows for best experience[/dim]")


def get_model_info(model_id: str) -> dict | None:
    """Get model info from API or fallback."""
    # Try API first
    api_models = fetch_edge_models()
    for m in api_models:
        if m.get("model_external_id") == model_id or m.get("uuid") == model_id:
            metadata = m.get("metadata", {})
            return {
                "name": m.get("name"),
                "description": m.get("description"),
                "runtime": metadata.get("edge_runtime", m.get("model_provider_name")),
                "model_path": metadata.get("edge_model_path", m.get("model_external_id")),
                "event_types": metadata.get("event_types", ["object_detected"]),
                "uuid": m.get("uuid"),
            }
    
    # Fallback to local definitions
    fallback = get_fallback_models()
    return fallback.get(model_id)


@model.command("bind")
@click.option("--model", "-m", "model_id", required=True, help="Model ID from 'model list'")
@click.option("--camera", "-c", default="default", help="Camera ID to process")
@click.option("--twin-uuid", "-t", required=True, help="Twin UUID to emit events for")
@click.option("--confidence", default=0.5, type=float, help="Confidence threshold (0-1)")
@click.option("--fps", default=2.0, type=float, help="Inference FPS (frames per second)")
@click.option("--classes", help="Comma-separated list of classes to detect")
@click.option("--env-file", type=click.Path(), default=".env", help="Path to .env file")
def bind_model(model_id, camera, twin_uuid, confidence, fps, classes, env_file):
    """Bind a model to run on a camera and emit events."""
    
    model_info = get_model_info(model_id)
    
    if not model_info:
        console.print(f"[red]Unknown model: {model_id}[/red]")
        console.print("[dim]Run 'cyberwave model list' to see available models[/dim]")
        return
    
    # Build model config
    model_config = {
        "model_id": model_id,
        "runtime": model_info["runtime"],
        "model_path": model_info["model_path"],
        "camera_id": camera,
        "twin_uuid": twin_uuid,
        "event_types": model_info.get("event_types", ["object_detected"]),
        "confidence_threshold": confidence,
        "inference_fps": fps,
        "enabled": True,
    }
    
    if classes:
        model_config["classes"] = [c.strip() for c in classes.split(",")]
    elif "classes" in model_info:
        model_config["classes"] = model_info["classes"]

    # Read existing .env or create new
    env_path = Path(env_file)
    env_content = {}
    
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_content[key.strip()] = value.strip()

    # Parse existing MODELS or create new
    existing_models = []
    if "MODELS" in env_content:
        try:
            raw = env_content["MODELS"].strip("'\"")
            existing_models = json.loads(raw)
        except json.JSONDecodeError:
            pass

    # Remove existing config for same model_id, add new one
    existing_models = [m for m in existing_models if m.get("model_id") != model_id]
    existing_models.append(model_config)
    
    env_content["MODELS"] = f"'{json.dumps(existing_models)}'"

    # Write back
    with open(env_path, "w") as f:
        for key, value in env_content.items():
            f.write(f"{key}={value}\n")

    console.print(f"[green]✓ Model '{model_id}' bound to camera '{camera}'[/green]")
    console.print(f"  Twin: {twin_uuid}")
    console.print(f"  Confidence: {confidence}")
    console.print(f"  Inference FPS: {fps}")
    
    if model_config.get("classes"):
        console.print(f"  Classes: {', '.join(model_config['classes'])}")

    console.print(f"\n[dim]Restart the edge service to apply changes[/dim]")


@model.command("show")
@click.option("--env-file", type=click.Path(), default=".env", help="Path to .env file")
def show_config(env_file):
    """Show current model configuration."""
    env_path = Path(env_file)
    
    if not env_path.exists():
        console.print("[yellow]No .env file found[/yellow]")
        return

    with open(env_path) as f:
        content = f.read()

    # Find MODELS line
    models = []
    for line in content.split("\n"):
        if line.startswith("MODELS="):
            try:
                raw = line.split("=", 1)[1].strip("'\"")
                models = json.loads(raw)
            except:
                pass
            break

    if not models:
        console.print("[yellow]No models configured[/yellow]")
        console.print("[dim]Use 'cyberwave model bind' to add a model[/dim]")
        return

    table = Table(title="Configured Models")
    table.add_column("Model ID", style="cyan")
    table.add_column("Runtime")
    table.add_column("Camera")
    table.add_column("Twin UUID")
    table.add_column("Events")
    table.add_column("Enabled")

    for m in models:
        table.add_row(
            m.get("model_id", "?"),
            m.get("runtime", "?"),
            m.get("camera_id", "default"),
            m.get("twin_uuid", "-")[:8] + "..." if m.get("twin_uuid") else "-",
            ", ".join(m.get("event_types", [])),
            "✓" if m.get("enabled", True) else "✗",
        )

    console.print(table)


@model.command("remove")
@click.option("--model", "-m", "model_id", required=True, help="Model ID to remove")
@click.option("--env-file", type=click.Path(), default=".env", help="Path to .env file")
def remove_model(model_id, env_file):
    """Remove a model binding."""
    env_path = Path(env_file)
    
    if not env_path.exists():
        console.print("[red]No .env file found[/red]")
        return

    # Read and parse
    env_content = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env_content[key.strip()] = value.strip()

    if "MODELS" not in env_content:
        console.print("[yellow]No models configured[/yellow]")
        return

    try:
        raw = env_content["MODELS"].strip("'\"")
        models = json.loads(raw)
    except:
        console.print("[red]Failed to parse MODELS[/red]")
        return

    # Filter out the model
    original_count = len(models)
    models = [m for m in models if m.get("model_id") != model_id]
    
    if len(models) == original_count:
        console.print(f"[yellow]Model '{model_id}' not found[/yellow]")
        return

    env_content["MODELS"] = f"'{json.dumps(models)}'"

    # Write back
    with open(env_path, "w") as f:
        for key, value in env_content.items():
            f.write(f"{key}={value}\n")

    console.print(f"[green]✓ Model '{model_id}' removed[/green]")
