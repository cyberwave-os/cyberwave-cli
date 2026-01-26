"""Twin management commands for the Cyberwave CLI.

Commands:
    twin create <asset>     Create a new digital twin from an asset
    twin pair <uuid>        Pair this device with an existing twin
    twin list               List all digital twins
    twin show <uuid>        Show details of a specific twin
    twin delete <uuid>      Delete a digital twin

Examples:
    cyberwave twin create camera
    cyberwave twin create unitree/go2 --name "My Robot"
    cyberwave twin create camera --pair
    cyberwave twin pair abc-123-def-456
"""

import logging
import platform
from datetime import datetime, timezone
from typing import Any

import click
import httpx
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..utils import (
    console,
    get_sdk_client,
    print_error,
    print_success,
    print_warning,
    truncate_uuid,
    write_edge_env,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def _coerce_value(field_type: str, value: str | None) -> object | None:
    """Coerce string value to the type specified in schema."""
    if value is None or value == "":
        return None
    field_type = (field_type or "").lower()
    if field_type == "integer":
        try:
            return int(value)
        except ValueError:
            return value
    if field_type == "number":
        try:
            return float(value)
        except ValueError:
            return value
    if field_type == "boolean":
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "y"}
        return bool(value)
    return value


def _prompt_for_schema_fields(
    schema: list[dict],
    cli_overrides: dict[str, str],
    yes: bool,
) -> dict:
    """
    Prompt user for each field in the schema, using CLI overrides when provided.
    Returns a config dict with all non-empty values.
    """
    config = {}

    if not schema:
        return config

    if not yes:
        console.print("\n[bold]Edge Configuration[/bold]")
        console.print("[dim]Configure based on asset schema. Leave blank to skip optional fields.[/dim]")

    for field in schema:
        name = field.get("name", "")
        flag = field.get("flag", "")
        field_type = field.get("type", "string")
        default = field.get("default")
        description = field.get("description", "")
        required = field.get("required", False)

        if not name:
            continue

        # Check if CLI override was provided (match by flag without --)
        cli_key = flag.lstrip("-").replace("-", "_") if flag else name
        cli_value = cli_overrides.get(cli_key)

        if cli_value is not None:
            # Use CLI-provided value
            config[name] = _coerce_value(field_type, cli_value)
        elif yes:
            # Non-interactive: use default if available
            if default not in (None, ""):
                config[name] = _coerce_value(field_type, str(default))
            elif required:
                print_warning(f"Required field '{name}' has no default and --yes was used")
        else:
            # Interactive prompt
            prompt_text = f"  {name}"
            if description:
                console.print(f"[dim]  {description}[/dim]")

            default_str = str(default) if default not in (None, "") else ""
            value = Prompt.ask(prompt_text, default=default_str)

            if value.strip():
                config[name] = _coerce_value(field_type, value)
            elif required:
                print_warning(f"Required field '{name}' was left empty")

    return config


def _select_environment(client: Any, yes: bool) -> str | None:
    """Prompt user to select an environment."""
    try:
        environments = client.environments.list()

        if not environments:
            console.print("[yellow]No environments found. Creating one...[/yellow]")

            env_name = "Default Environment" if yes else Prompt.ask("Environment name", default="Default Environment")

            # Need to create workspace/project first
            projects = client.projects.list()
            if not projects:
                workspaces = client.workspaces.list()
                if not workspaces:
                    workspace = client.workspaces.create(name="Default Workspace")
                    workspace_id = workspace.uuid
                else:
                    workspace_id = workspaces[0].uuid

                project = client.projects.create(name="Default Project", workspace_id=str(workspace_id))
                project_id = project.uuid
            else:
                project_id = projects[0].uuid

            env = client.environments.create(name=env_name, project_id=str(project_id))
            return str(env.uuid)

        if yes:
            # Use first environment
            return str(environments[0].uuid)

        console.print("\n[bold]Select environment:[/bold]")
        for i, env in enumerate(environments[:10], 1):
            env_name = getattr(env, 'name', 'Unknown')
            env_uuid = str(getattr(env, 'uuid', ''))
            console.print(f"  {i}. {env_name} [dim]({env_uuid[:8]}...)[/dim]")
        console.print(f"  {len(environments[:10]) + 1}. [Create new environment]")

        choice = Prompt.ask("Select", default="1")

        try:
            idx = int(choice) - 1
            if idx < len(environments[:10]):
                return str(environments[idx].uuid)
            else:
                # Create new
                env_name = Prompt.ask("Environment name", default="Default Environment")
                projects = client.projects.list()
                project_id = projects[0].uuid if projects else None

                if not project_id:
                    print_error("No project found to create environment")
                    return None

                env = client.environments.create(name=env_name, project_id=str(project_id))
                return str(env.uuid)
        except (ValueError, IndexError):
            print_error("Invalid choice")
            return None

    except Exception as e:
        print_error(f"Error listing environments: {e}")
        return None


def _find_or_create_twin(
    client: Any,
    asset: dict,
    fingerprint: str,
    environment_uuid: str | None,
    twin_name: str | None,
    yes: bool,
) -> Any | None:
    """Find existing twin for this fingerprint or create a new one."""
    from ..fingerprint import get_device_info

    asset_uuid = asset.get('uuid')

    # Search for twins that have this fingerprint in their edge_configs
    if asset_uuid:
        try:
            twins = client.twins.list(asset_uuid=asset_uuid)

            for twin in twins:
                metadata = getattr(twin, 'metadata', {}) or {}
                edge_configs = metadata.get('edge_configs', {})

                if fingerprint in edge_configs:
                    # Found existing twin for this device
                    found_name = getattr(twin, 'name', 'Unknown')
                    config = edge_configs[fingerprint]
                    last_sync = config.get('last_sync', 'unknown')

                    console.print(f"\n[cyan]Found existing twin:[/cyan] {found_name}")
                    console.print(f"[dim]  Last connected: {last_sync}[/dim]")

                    if yes or Confirm.ask("\nUse this twin?", default=True):
                        return twin
        except Exception as e:
            # Log but continue - we'll create a new twin if lookup fails
            logger.debug("Failed to search for existing twins: %s", e)

    # No existing twin found - create new one
    console.print("\n[yellow]No existing twin found for this device.[/yellow]")

    if not yes and not Confirm.ask("Create new twin?", default=True):
        return None

    # Select environment
    if not environment_uuid:
        environment_uuid = _select_environment(client, yes)
        if not environment_uuid:
            return None

    # Get twin name
    if not twin_name:
        device_info = get_device_info()
        asset_name = asset.get('name', 'Edge')
        default_name = f"{asset_name[:20]}-{device_info.get('hostname', 'edge')[:10]}"

        if yes:
            twin_name = default_name
        else:
            twin_name = Prompt.ask("Twin name", default=default_name)

    # Create twin
    console.print(f"\n[dim]Creating twin '{twin_name}'...[/dim]")

    try:
        twin = client.twins.create(
            name=twin_name,
            environment_id=environment_uuid,
            asset_id=asset_uuid,
        )
        print_success(f"Created twin: {twin_name}")
        return twin
    except Exception as e:
        print_error(f"Failed to create twin: {e}")
        return None


def _configure_edge(
    client: Any,
    twin: Any,
    fingerprint: str,
    device_info: dict,
    edge_config_schema: list[dict],
    cli_overrides: dict[str, str],
    yes: bool = False,
) -> dict | None:
    """Configure edge settings from schema, interactively or from CLI/defaults."""
    metadata = getattr(twin, 'metadata', {}) or {}
    edge_configs = metadata.get('edge_configs', {})

    # Check for existing config (skip if CLI overrides provided)
    existing_config = edge_configs.get(fingerprint)

    if existing_config and not cli_overrides:
        console.print("\n[cyan]Found existing config for this device.[/cyan]")

        if yes or Confirm.ask("Use existing config?", default=True):
            existing_config['last_sync'] = datetime.now(timezone.utc).isoformat()
            return existing_config

    # Build config from schema
    edge_config = _prompt_for_schema_fields(edge_config_schema, cli_overrides, yes)

    config = {
        **edge_config,
        "device_info": device_info,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_sync": datetime.now(timezone.utc).isoformat(),
    }

    return config


def _register_and_pair_edge(twin_uuid: str, fingerprint: str, device_info: dict, config: dict) -> str | None:
    """Register edge device and pair it to the twin using new API.

    Returns edge_uuid on success, None on failure (fallback to legacy).
    """
    from ..config import get_api_url
    from ..credentials import load_credentials

    creds = load_credentials()
    if not creds or not creds.token:
        return None

    base_url = get_api_url()
    headers = {"Authorization": f"Bearer {creds.token}"}

    try:
        # Step 1: Register/discover edge device
        discover_url = f"{base_url}/api/v1/edges/discover"
        discover_payload = {
            "fingerprint": fingerprint,
            "hostname": device_info.get('hostname', ''),
            "platform": f"{platform.system()}-{platform.machine()}",
            "name": device_info.get('hostname', fingerprint[:20]),
        }

        with httpx.Client() as http_client:
            response = http_client.post(
                discover_url,
                json=discover_payload,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            edge_uuid = data.get("edge_uuid")

        if not edge_uuid:
            return None

        # Step 2: Pair twin to edge with config
        pair_url = f"{base_url}/api/v1/edges/{edge_uuid}/pair"

        # Remove internal fields from config
        config_clean = {k: v for k, v in config.items() if not k.startswith('_') and k not in ('device_info', 'registered_at', 'last_sync')}

        pair_payload = {
            "twin_uuid": twin_uuid,
            "camera_config": config_clean,  # Backend expects camera_config for now
        }

        with httpx.Client() as http_client:
            response = http_client.post(
                pair_url,
                json=pair_payload,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()

        console.print(f"[dim]Edge registered: {edge_uuid[:8]}...[/dim]")
        return edge_uuid

    except Exception as e:
        print_warning(f"New API failed, using legacy: {e}")
        return None


def _save_config_to_twin(client: Any, twin_uuid: str, fingerprint: str, config: dict):
    """Save edge config to twin metadata (legacy fallback)."""
    # Remove secrets before saving to cloud
    config_for_cloud = {k: v for k, v in config.items() if not k.startswith('_')}

    try:
        twin = client.twins.get(twin_uuid)
        metadata = getattr(twin, 'metadata', {}) or {}
        edge_configs = metadata.get('edge_configs', {})
        edge_configs[fingerprint] = config_for_cloud
        metadata['edge_configs'] = edge_configs

        client.twins.update(twin_uuid, metadata=metadata)
    except Exception as e:
        print_warning(f"Could not save to cloud: {e}")


def _write_local_env(twin_uuid: str, config: dict, fingerprint: str, target_dir: str = ".", generator: str = "cyberwave twin"):
    """Write .env file locally using shared utility."""
    # Clean config of internal fields
    edge_config = {k: v for k, v in config.items() if k not in ('device_info', 'registered_at', 'last_sync')}

    write_edge_env(
        target_dir=target_dir,
        twin_uuid=twin_uuid,
        fingerprint=fingerprint,
        edge_config=edge_config,
        generator=generator,
    )


# =============================================================================
# CLI Command Group
# =============================================================================


@click.group()
def twin():
    """Manage digital twins.

    \b
    Commands:
        create      Create a new digital twin from an asset
        pair        Pair this device with an existing twin
        list        List all digital twins
        show        Show details of a specific twin
        delete      Delete a digital twin
    """
    pass


# =============================================================================
# twin create
# =============================================================================


@twin.command("create", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("asset")
@click.option("--name", "-n", help="Twin name")
@click.option("--environment", "-e", "environment_uuid", help="Environment UUID to create twin in")
@click.option("--pair", "do_pair", is_flag=True, help="Also pair this device to the twin")
@click.option("--target-dir", "-d", default=".", help="Directory to save .env file (when --pair is used)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.pass_context
def create_twin(
    ctx: click.Context,
    asset: str,
    name: str | None,
    environment_uuid: str | None,
    do_pair: bool,
    target_dir: str,
    yes: bool,
):
    """
    Create a new digital twin from an asset.

    ASSET can be:

    \b
      - Registry ID: unitree/go2, cyberwave/standard-cam
      - Alias: go2, camera (short names)
      - Local file: ./my-robot.json
      - URL: https://example.com/asset.json

    Use --pair to also pair this device to the twin in one step.
    Configuration options are determined by the asset's edge_config_schema.
    Pass any schema field as --field-name value.

    \b
    Examples:
        cyberwave twin create camera
        cyberwave twin create go2 --name "My Robot"
        cyberwave twin create camera --pair
        cyberwave twin create camera --pair --source "rtsp://..." --fps 15
    """
    # Parse extra args as --key value pairs for schema fields
    cli_overrides: dict[str, str] = {}
    args = ctx.args
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                cli_overrides[key] = args[i + 1]
                i += 2
            else:
                cli_overrides[key] = "true"  # Flag without value
                i += 1
        else:
            i += 1

    from ..asset_resolver import AssetResolutionError, get_asset_display_name, resolve_asset
    from ..fingerprint import generate_fingerprint, get_device_info

    # Get SDK client
    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return

    # Generate fingerprint (needed for pairing)
    fingerprint = generate_fingerprint()
    device_info = get_device_info()

    if do_pair:
        console.print(f"\n[dim]Fingerprint: {fingerprint}[/dim]")

    # 1. Resolve asset
    console.print(f"\nResolving asset '{asset}'...", end=" ")
    try:
        resolved_asset = resolve_asset(asset, client)
        asset_name = get_asset_display_name(resolved_asset)
        console.print(f"[green]✓[/green] {asset_name}")
    except AssetResolutionError as e:
        console.print("[red]✗[/red]")
        print_error(str(e))
        return

    # Get edge_config_schema from asset capabilities
    capabilities = resolved_asset.get('capabilities', {}) or {}
    edge_config_schema: list[dict] = capabilities.get("edge_config_schema", []) or []

    if edge_config_schema and do_pair:
        console.print(f"[dim]  Config schema: {len(edge_config_schema)} field(s)[/dim]")

    # 2. Find or create twin
    # Only pass fingerprint when --pair is used; otherwise always create a new twin
    # to ensure deterministic "create" semantics (no silent reuse of existing twins)
    twin_obj = _find_or_create_twin(
        client=client,
        asset=resolved_asset,
        fingerprint=fingerprint if do_pair else None,
        environment_uuid=environment_uuid,
        twin_name=name,
        yes=yes,
    )

    if twin_obj is None:
        return  # User cancelled

    twin_uuid = str(getattr(twin_obj, 'uuid', ''))
    twin_name_display = getattr(twin_obj, 'name', 'Unknown')

    if not do_pair:
        print_success(f"Twin created: {twin_name_display} ({twin_uuid})")
        console.print(f"\n[dim]To pair an edge device:[/dim]")
        console.print(f"  cyberwave twin pair {twin_uuid}")
        return

    # 3. Configure edge from schema (only if pairing)
    config = _configure_edge(
        client=client,
        twin=twin_obj,
        fingerprint=fingerprint,
        device_info=device_info,
        edge_config_schema=edge_config_schema,
        cli_overrides=cli_overrides,
        yes=yes,
    )

    if config is None:
        return  # User cancelled

    # 4. Register edge and pair twin using new API
    edge_uuid = _register_and_pair_edge(twin_uuid, fingerprint, device_info, config)

    if not edge_uuid:
        # Fallback to legacy twin metadata storage
        _save_config_to_twin(client, twin_uuid, fingerprint, config)

    _write_local_env(twin_uuid, config, fingerprint, target_dir, "cyberwave twin create --pair")

    print_success("Twin created and paired!")
    console.print(f"\n[bold]Saved to:[/bold]")
    if edge_uuid:
        console.print(f"  - Cloud: edge/{edge_uuid[:8]}... -> twin/{twin_uuid[:8]}...")
    else:
        console.print(f"  - Cloud: twin/{twin_uuid}/edge_configs/{fingerprint[:20]}... (legacy)")
    console.print(f"  - Local: {target_dir}/.env")
    console.print(f"\n[dim]Run: cyberwave edge start[/dim]")


# =============================================================================
# twin pair
# =============================================================================


@twin.command("pair", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("twin_uuid")
@click.option(
    "--target-dir",
    "-d",
    default=".",
    help="Directory to save edge configuration (default: current directory)",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.pass_context
def pair_twin(ctx: click.Context, twin_uuid: str, target_dir: str, yes: bool):
    """
    Pair this device with an existing digital twin.

    Registers this device with the backend and binds it to the specified twin.
    After pairing, run `cyberwave edge start` to begin streaming.

    Configuration options are determined by the asset's edge_config_schema.
    Pass any schema field as --field-name value.

    \b
    What this does:
        1. Generates a unique fingerprint for this device
        2. Registers the edge device with the backend (auto-creates if new)
        3. Binds the twin to this edge with configuration from schema
        4. Saves local .env file for edge service

    \b
    Examples:
        cyberwave twin pair abc-123-def-456
        cyberwave twin pair abc-123-def-456 --camera-source "rtsp://..." --fps 15

    \b
    Quick Start:
        1. cyberwave login                    # Login to your account
        2. cyberwave twin pair <uuid>         # Pair device with twin
        3. cyberwave edge start               # Start streaming
    """
    from ..config import get_api_url
    from ..credentials import load_credentials
    from ..fingerprint import generate_fingerprint, get_device_info

    # Parse extra args as --key value pairs for schema fields
    cli_overrides: dict[str, str] = {}
    args = ctx.args
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                cli_overrides[key] = args[i + 1]
                i += 2
            else:
                cli_overrides[key] = "true"  # Flag without value
                i += 1
        else:
            i += 1

    # Get SDK client and auth
    client = get_sdk_client()
    creds = load_credentials()
    token = creds.token if creds else None
    base_url = get_api_url()

    if not client or not token:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return

    # Generate fingerprint
    fingerprint = generate_fingerprint()
    device_info = get_device_info()

    console.print("\n[bold]Device Pairing[/bold]")
    console.print(f"Fingerprint: [cyan]{fingerprint}[/cyan]")
    console.print(f"Hostname:    {device_info.get('hostname', 'unknown')}")
    console.print(f"Platform:    {device_info.get('platform', 'unknown')}")

    # Get twin info and asset capabilities
    try:
        twin_obj = client.twins.get(twin_uuid)
        twin_name = getattr(twin_obj, 'name', 'Unknown')
        console.print(f"\n[bold]Target Twin:[/bold] {twin_name}")

        # Get asset capabilities including edge_config_schema
        # Twin may have asset_uuid or asset_id depending on SDK version
        asset_uuid = getattr(twin_obj, 'asset_uuid', None) or getattr(twin_obj, 'asset_id', None)
        edge_config_schema: list[dict] = []
        if asset_uuid:
            try:
                asset = client.assets.get(asset_uuid)
                capabilities = getattr(asset, 'capabilities', {}) or {}
                edge_config_schema = capabilities.get("edge_config_schema", []) or []

                # Show capabilities summary
                sensors = capabilities.get('sensors', [])
                caps_summary = []
                if sensors:
                    caps_summary.append(f"{len(sensors)} sensor(s)")
                if capabilities.get('has_joints'):
                    caps_summary.append("joints")
                if capabilities.get('can_locomote'):
                    caps_summary.append("locomotion")
                if caps_summary:
                    console.print(f"[dim]  Capabilities: {', '.join(caps_summary)}[/dim]")
                if edge_config_schema:
                    console.print(f"[dim]  Config schema: {len(edge_config_schema)} field(s)[/dim]")
            except Exception as e:
                console.print(f"[dim]  Could not fetch asset details: {e}[/dim]")
    except Exception as e:
        print_error(f"Twin not found: {twin_uuid}", f"Error: {e}")
        return

    # Confirm pairing
    if not yes and not Confirm.ask(f"\nPair this device to '{twin_name}'?", default=True):
        print_warning("Pairing cancelled.")
        return

    # Step 1: Register/discover edge device
    console.print("\n[dim]Registering edge device...[/dim]")

    edge_uuid = None
    try:
        headers = {"Authorization": f"Bearer {token}"}
        discover_url = f"{base_url}/api/v1/edges/discover"

        discover_payload = {
            "fingerprint": fingerprint,
            "hostname": device_info.get('hostname', ''),
            "platform": f"{platform.system()}-{platform.machine()}",
            "name": device_info.get('hostname', fingerprint[:20]),
        }

        with httpx.Client() as http_client:
            response = http_client.post(
                discover_url,
                json=discover_payload,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            edge_uuid = data.get("edge_uuid")

        print_success(f"Edge registered: {edge_uuid[:8]}...")

    except Exception as e:
        print_error(f"Failed to register edge device: {e}")
        return

    # Step 2: Build config from schema (fully dynamic)
    edge_config = _prompt_for_schema_fields(edge_config_schema, cli_overrides, yes)

    # Step 3: Pair twin to edge
    console.print("\n[dim]Binding twin to edge device...[/dim]")

    try:
        headers = {"Authorization": f"Bearer {token}"}
        pair_url = f"{base_url}/api/v1/edges/{edge_uuid}/pair"

        pair_payload = {
            "twin_uuid": twin_uuid,
            "camera_config": edge_config,  # Backend expects camera_config for now
        }

        with httpx.Client() as http_client:
            response = http_client.post(
                pair_url,
                json=pair_payload,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()

        print_success(f"Twin '{twin_name}' bound to this edge")

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            print_warning("Twin already paired to a different device")
        else:
            print_error(f"Failed to pair twin: {e}")
        return
    except Exception as e:
        print_error(f"Failed to pair twin: {e}")
        return

    # Step 4: Write local .env file
    write_edge_env(
        target_dir=target_dir,
        twin_uuid=twin_uuid,
        fingerprint=fingerprint,
        edge_config=edge_config,
        generator="cyberwave twin pair",
    )

    print_success(f"Configuration saved to {target_dir}/.env")

    print_success("Pairing complete!")
    console.print("\n[dim]To start streaming:[/dim]")
    console.print(f"  cd {target_dir}")
    console.print("  cyberwave edge start")

    # Show current bindings
    console.print("\n[dim]This edge device is now paired to:[/dim]")
    console.print(f"  - {twin_name} ({twin_uuid[:8]}...)")


# =============================================================================
# twin list
# =============================================================================


@twin.command("list")
@click.option("--environment", "-e", help="Filter by environment UUID")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_twins(environment: str | None, as_json: bool):
    """List digital twins."""
    import json as json_module

    client = get_sdk_client()
    if not client:
        print_error("Not logged in or SDK not installed.", "Run: cyberwave login")
        raise click.Abort()

    try:
        if environment:
            twins = client.twins.list(environment_id=environment)
        else:
            twins = client.twins.list()

        if as_json:
            data = [
                {
                    "uuid": str(t.uuid),
                    "name": t.name,
                    "asset_uuid": str(t.asset_uuid) if t.asset_uuid else None,
                    "environment_uuid": str(t.environment_uuid) if t.environment_uuid else None,
                }
                for t in twins
            ]
            console.print(json_module.dumps(data, indent=2))
            return

        if not twins:
            console.print("[dim]No twins found.[/dim]")
            console.print("[dim]Create one with: cyberwave twin create <asset>[/dim]")
            return

        table = Table(title="Digital Twins")
        table.add_column("Name", style="cyan")
        table.add_column("UUID", style="dim")
        table.add_column("Asset")
        table.add_column("Environment")

        for t in twins:
            table.add_row(
                t.name or "Unnamed",
                truncate_uuid(t.uuid),
                truncate_uuid(t.asset_uuid),
                truncate_uuid(t.environment_uuid),
            )

        console.print(table)
        console.print(f"\n[dim]Total: {len(twins)} twin(s)[/dim]")

    except Exception as e:
        print_error(f"Failed to list twins: {e}")
        raise click.Abort()


# =============================================================================
# twin show
# =============================================================================


@twin.command("show")
@click.argument("uuid")
def show_twin(uuid: str):
    """Show details of a specific twin."""
    client = get_sdk_client()
    if not client:
        print_error("Not logged in or SDK not installed.")
        raise click.Abort()

    try:
        twin_data = client.twins.get_raw(uuid)

        console.print(f"\n[bold cyan]{twin_data.name}[/bold cyan]")
        console.print(f"  UUID: {twin_data.uuid}")
        console.print(f"  Asset: {twin_data.asset_uuid or 'None'}")
        console.print(f"  Environment: {twin_data.environment_uuid or 'None'}")

        if hasattr(twin_data, 'capabilities') and twin_data.capabilities:
            console.print(f"  Capabilities: {twin_data.capabilities}")

    except Exception as e:
        print_error(f"Failed to get twin: {e}")
        raise click.Abort()


# =============================================================================
# twin delete
# =============================================================================


@twin.command("delete")
@click.argument("uuid")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def delete_twin(uuid: str, yes: bool):
    """Delete a digital twin."""
    client = get_sdk_client()
    if not client:
        print_error("Not logged in or SDK not installed.")
        raise click.Abort()

    if not yes:
        if not click.confirm(f"Delete twin {uuid}?"):
            raise click.Abort()

    try:
        client.twins.delete(uuid)
        print_success(f"Deleted twin: {uuid}")
    except Exception as e:
        print_error(f"Failed to delete twin: {e}")
        raise click.Abort()
