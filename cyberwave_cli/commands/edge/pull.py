"""``cyberwave edge pull`` command and helpers.

Handles fetching twin/environment configs from the backend.
"""

from __future__ import annotations

from typing import Any

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

console = Console()


def register(edge_group: click.Group) -> None:
    """Register the ``pull`` command on the given click group."""
    edge_group.add_command(pull_config)


# ---------------------------------------------------------------------------
# Helpers (use lazy imports from edge.py for shared utilities)
# ---------------------------------------------------------------------------


@click.command("pull")
@click.option("--twin-uuid", "-t", help="Twin UUID to pull config from (legacy)")
@click.option("--environment-uuid", "-e", help="Environment UUID to pull all twins from (legacy)")
@click.option("--target-dir", "-d", default=".", help="Directory to write .env file")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def pull_config(
    twin_uuid: str | None, environment_uuid: str | None, target_dir: str, yes: bool
):
    """
    Pull edge configuration from backend.

    Uses the discovery API to fetch all twins bound to this edge device
    along with their camera configurations.

    \b
    Examples:
        # Pull all configs for this edge device
        cyberwave edge pull

        # Pull config for a specific twin (legacy)
        cyberwave edge pull --twin-uuid abc-123

        # Specify output directory
        cyberwave edge pull -d ./my-edge
    """
    from cyberwave.fingerprint import generate_fingerprint
    from ...utils import get_sdk_client, print_error

    client = get_sdk_client()
    if not client:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return

    fingerprint = generate_fingerprint()
    console.print(f"\nFingerprint: {fingerprint}\n")

    try:
        # Try new discovery API first
        if not twin_uuid and not environment_uuid:
            success = _pull_via_discovery_api(fingerprint, target_dir, yes)
            if success:
                return
            # Fall through to legacy if discovery API fails
            console.print("[dim]Discovery API not available, use --twin-uuid for legacy mode[/dim]")
            return

        # Legacy mode
        if environment_uuid:
            _pull_environment_configs(client, environment_uuid, fingerprint, target_dir, yes)
        else:
            _pull_single_twin_config(client, twin_uuid, fingerprint, target_dir, yes)
    except Exception as e:
        print_error(str(e))


def _pull_via_discovery_api(fingerprint: str, target_dir: str, yes: bool) -> bool:
    """
    DEPRECATED: The core now handles discovery
    Pull config via the new discovery API.

    Returns True on success, False to fall back to legacy.
    """
    import platform
    import httpx

    from ...config import get_api_url
    from ...credentials import load_credentials
    from cyberwave.fingerprint import get_device_info
    from ...utils import print_error, print_success, print_warning, write_edge_env

    creds = load_credentials()
    if not creds or not creds.token:
        print_error("Not authenticated.", "Run 'cyberwave login' first.")
        return False

    base_url = get_api_url()
    headers = {"Authorization": f"Bearer {creds.token}"}
    device_info = get_device_info()

    try:
        # Call discovery API
        discover_url = f"{base_url}/api/v1/edges/discover"
        discover_payload = {
            "fingerprint": fingerprint,
            "hostname": device_info.get("hostname", ""),
            "platform": f"{platform.system()}-{platform.machine()}",
            "name": device_info.get("hostname", fingerprint[:20]),
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
        twins = data.get("twins", [])

        console.print(f"[cyan]Edge UUID:[/cyan] {edge_uuid}")
        console.print(f"[cyan]Bound twins:[/cyan] {len(twins)}\n")

        if not twins:
            print_warning("No twins bound to this edge device.")
            console.print(
                "[dim]Use 'cyberwave twin pair <twin_uuid>' to bind twins to this edge.[/dim]"
            )
            return True

        # Display twins and collect configs
        all_configs = []
        primary_twin = None

        for twin_info in twins:
            twin_uuid = twin_info.get("twin_uuid")
            twin_name = twin_info.get("twin_name", "Unknown")
            edge_config = twin_info.get("camera_config", {})  # Backend still uses camera_config key

            if not primary_twin:
                primary_twin = twin_uuid

            has_config = bool(edge_config)
            status = "[green]✓[/green]" if has_config else "[yellow]○[/yellow]"

            console.print(f"{status} {twin_name} ({twin_uuid[:8]}...)")

            if edge_config:
                config_entry = {
                    "twin_uuid": twin_uuid,
                    **edge_config,
                }
                all_configs.append(config_entry)

        if not all_configs:
            print_warning("No edge configurations found.")
            console.print(
                "[dim]Use 'cyberwave twin pair <twin_uuid>' with config options to set up twins.[/dim]"
            )
            return True

        # Write .env file
        write_edge_env(
            target_dir=target_dir,
            twin_uuid=primary_twin,
            fingerprint=fingerprint,
            edge_configs=all_configs,
            generator="cyberwave edge pull",
        )

        print_success(f"Config pulled to {target_dir}/.env")
        console.print(f"[dim]{len(all_configs)} config(s) from {len(twins)} twin(s)[/dim]")

        console.print("\n[dim]Run: cyberwave edge start[/dim]")

        return True

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # API not available, fall back to legacy
            return False
        print_error(f"Discovery API error: {e}")
        return False
    except Exception as e:
        console.print(f"[dim]Discovery API failed: {e}[/dim]")
        return False


def _pull_single_twin_config(
    client: Any, twin_uuid: str, fingerprint: str, target_dir: str, yes: bool
):
    """
    DEPRECATED: The core now handles this sync
    Pull config from a single twin (legacy).
    """
    from . import _binding_for_fingerprint, _iter_edge_bindings

    twin = client.twins.get(twin_uuid)
    twin_name = getattr(twin, "name", "Unknown")
    metadata = getattr(twin, "metadata", {}) or {}
    edge_configs = metadata.get("edge_configs", {})

    console.print(f"[cyan]Twin:[/cyan] {twin_name}")

    my_config = _binding_for_fingerprint(edge_configs, fingerprint)

    if my_config:
        from ...utils import print_success

        print_success("Found config for this device")
        console.print(f"[dim]  Registered: {my_config.get('registered_at', 'unknown')}[/dim]")

        cameras = my_config.get("cameras", [])
        if cameras:
            console.print(f"[dim]  Cameras: {len(cameras)} configured[/dim]")
    else:
        # No config for this fingerprint - check for other configs or default
        available_bindings = _iter_edge_bindings(edge_configs)
        if available_bindings:
            from ...utils import print_warning

            print_warning("No config for this device fingerprint")
            console.print(f"\n[dim]Available configs from other devices:[/dim]")

            for i, (fp, cfg) in enumerate(available_bindings, 1):
                device_info = cfg.get("device_info", {})
                hostname = device_info.get("hostname", "unknown")
                registered = (
                    cfg.get("registered_at", "unknown")[:10]
                    if cfg.get("registered_at")
                    else "unknown"
                )
                console.print(f"  {i}. {fp[:30]}... ({hostname}, {registered})")

            if not yes:
                choice = Prompt.ask(
                    "\n[bold]Copy config from which device? (number or 'n' to skip)[/bold]",
                    default="1",
                )

                if choice.lower() != "n":
                    try:
                        idx = int(choice) - 1
                        source_fp, source_cfg = available_bindings[idx]
                        my_config = source_cfg.copy()
                        from ...utils import print_success

                        print_success(f"Copying config from {source_fp[:20]}...")
                    except (ValueError, IndexError):
                        from ...utils import print_warning

                        print_warning("Invalid choice, skipping")
                        return
        else:
            # Check for default config
            default_config = metadata.get("default_edge_config")
            if default_config:
                from ...utils import print_warning

                print_warning("No config for this device, using default template")
                my_config = default_config.copy()
            else:
                from ...utils import print_error

                print_error(
                    "No configuration found for this twin",
                    "Use 'cyberwave twin create <asset> --pair' to set up this twin",
                )
                return

    if not my_config:
        return

    # Extract edge config (remove internal fields)
    edge_config = {
        k: v
        for k, v in my_config.items()
        if k not in ("device_info", "registered_at", "last_sync", "cameras")
    }

    # For backward compat: if config has 'cameras' array, use first entry as edge_config
    if not edge_config and my_config.get("cameras"):
        cameras = my_config["cameras"]
        if cameras:
            edge_config = {k: v for k, v in cameras[0].items() if k != "camera_id"}

    # Write .env file directly using shared utility
    from ...utils import write_edge_env, print_success

    write_edge_env(
        target_dir=target_dir,
        twin_uuid=twin_uuid,
        fingerprint=fingerprint,
        edge_config=edge_config,
        generator="cyberwave edge pull",
    )

    print_success(f"Config pulled to {target_dir}/.env")
    console.print("[dim]Run: cyberwave edge start[/dim]")


def _pull_environment_configs(
    client: Any, env_uuid: str, fingerprint: str, target_dir: str, yes: bool
):
    """
    DEPRECATED: The core now handles this sync
    Pull configs for all twins in an environment."""
    from . import _binding_for_fingerprint

    # Get environment info
    env = client.environments.get(env_uuid)
    env_name = getattr(env, "name", "Unknown")

    # Get twins in environment using SDK
    twins = client.twins.list(environment_id=env_uuid)

    if not twins:
        from ...utils import print_warning

        print_warning(f"No twins found in environment '{env_name}'")
        return

    console.print(f"[cyan]Environment:[/cyan] {env_name}")
    console.print(f"[cyan]Found {len(twins)} twin(s):[/cyan]\n")

    all_configs = []
    twins_with_config = []
    twins_without_config = []

    for twin in twins:
        twin_name = getattr(twin, "name", "Unknown")
        twin_uuid = str(getattr(twin, "uuid", ""))
        metadata = getattr(twin, "metadata", {}) or {}
        edge_configs = metadata.get("edge_configs", {})

        my_config = _binding_for_fingerprint(edge_configs, fingerprint)

        if my_config:
            twins_with_config.append((twin, my_config))
            # Extract edge config (remove internal fields)
            edge_config = {
                k: v
                for k, v in my_config.items()
                if k not in ("device_info", "registered_at", "last_sync", "cameras")
            }

            # For backward compat: if config has 'cameras' array, use first entry
            if not edge_config and my_config.get("cameras"):
                cameras = my_config["cameras"]
                if cameras:
                    edge_config = {k: v for k, v in cameras[0].items() if k != "camera_id"}

            config_entry = {
                "twin_uuid": twin_uuid,
                **edge_config,
            }
            all_configs.append(config_entry)
            console.print(f"  [green]✓[/green] {twin_name} - configured")
        else:
            twins_without_config.append(twin)
            console.print(f"  [yellow]○[/yellow] {twin_name} - no config for this device")

    if not all_configs and not twins_without_config:
        from ...utils import print_warning

        print_warning("No configurations to pull")
        return

    if twins_without_config:
        from ...utils import print_warning

        print_warning(f"{len(twins_without_config)} twin(s) need configuration")

    if not yes and not Confirm.ask("\nPull available configs?", default=True):
        return

    # Write .env file with all configs using shared utility
    if all_configs:
        from ...utils import write_edge_env, print_success

        # Extract primary twin
        primary_twin = all_configs[0].get("twin_uuid", "") if all_configs else ""

        write_edge_env(
            target_dir=target_dir,
            twin_uuid=primary_twin,
            fingerprint=fingerprint,
            edge_configs=all_configs,
            generator="cyberwave edge pull",
        )

        print_success(f"Config pulled to {target_dir}/.env")
        console.print(
            f"[dim]  {len(all_configs)} config(s) from {len(twins_with_config)} twin(s)[/dim]"
        )
        console.print("[dim]Run: cyberwave edge start[/dim]")
    else:
        from ...utils import print_warning

        print_warning(
            "No configs found. Use 'cyberwave twin create <asset> --pair' to set up twins."
        )
