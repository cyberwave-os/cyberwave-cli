"""Network scan command for discovering IP cameras and NVRs."""

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table

from ..discovery import NetworkScanner, DiscoveredDevice, DeviceType

console = Console()


def format_device_row(device: DiscoveredDevice) -> tuple:
    """Format a device for table display."""
    type_emoji = {
        DeviceType.CAMERA: "📷",
        DeviceType.NVR: "🎥",
        DeviceType.UNKNOWN: "❓",
    }

    return (
        type_emoji.get(device.device_type, "❓"),
        device.ip,
        str(device.port),
        device.protocol.upper(),
        device.manufacturer or "-",
        device.url,
    )


@click.command()
@click.option(
    "--subnet",
    "-s",
    help="Subnet to scan (e.g., 192.168.1). Auto-detected if not provided.",
)
@click.option(
    "--timeout",
    "-t",
    type=float,
    default=1.0,
    help="Connection timeout in seconds (default: 1.0)",
)
@click.option(
    "--no-ports",
    is_flag=True,
    help="Disable TCP port scanning",
)
@click.option(
    "--no-onvif",
    is_flag=True,
    help="Disable ONVIF WS-Discovery",
)
@click.option(
    "--no-upnp",
    is_flag=True,
    help="Disable UPnP/SSDP discovery",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output results as JSON",
)
def scan(
    subnet: str | None,
    timeout: float,
    no_ports: bool,
    no_onvif: bool,
    no_upnp: bool,
    output_json: bool,
) -> None:
    """Scan the network for IP cameras and NVRs.

    Discovers devices using multiple methods:
    - TCP port scanning (RTSP, HTTP)
    - ONVIF WS-Discovery
    - UPnP/SSDP

    \b
    Examples:
        cyberwave-cli scan
        cyberwave-cli scan -s 10.0.0
        cyberwave-cli scan --json
        cyberwave-cli scan --no-ports  # Only use discovery protocols
    """
    scanner = NetworkScanner(subnet=subnet, timeout=timeout)

    if not output_json:
        console.print(f"\n[bold]Scanning network: {scanner.subnet}.0/24[/bold]")
        console.print("[dim]This may take a minute...[/dim]\n")

        methods = []
        if not no_ports:
            methods.append("Port scan")
        if not no_onvif:
            methods.append("ONVIF")
        if not no_upnp:
            methods.append("UPnP")
        console.print(f"[dim]Methods: {', '.join(methods)}[/dim]\n")

    # Run scan with progress
    devices: list[DiscoveredDevice] = []

    if output_json:
        devices = scanner.scan(
            port_scan=not no_ports,
            onvif=not no_onvif,
            upnp=not no_upnp,
        )
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            task = progress.add_task("Scanning...", total=100)

            def on_progress(current: int, total: int) -> None:
                progress.update(task, completed=(current / total) * 100)

            devices = scanner.scan(
                port_scan=not no_ports,
                onvif=not no_onvif,
                upnp=not no_upnp,
                on_progress=on_progress if not no_ports else None,
            )

    # Output results
    if output_json:
        import json
        result = [
            {
                "ip": d.ip,
                "port": d.port,
                "protocol": d.protocol,
                "type": d.device_type.value,
                "manufacturer": d.manufacturer,
                "model": d.model,
                "url": d.url,
            }
            for d in devices
        ]
        console.print(json.dumps(result, indent=2))
        return

    if not devices:
        console.print("[yellow]No devices found.[/yellow]")
        console.print("\n[dim]Tips:[/dim]")
        console.print("  • Make sure cameras are powered on and connected")
        console.print("  • Try a different subnet with -s <subnet>")
        console.print("  • Increase timeout with -t 2.0")
        return

    # Display table
    table = Table(title=f"Found {len(devices)} device(s)")
    table.add_column("", justify="center", width=2)
    table.add_column("IP Address", style="cyan")
    table.add_column("Port", style="magenta")
    table.add_column("Protocol", style="green")
    table.add_column("Manufacturer", style="yellow")
    table.add_column("URL", style="dim")

    # Sort by IP
    devices.sort(key=lambda d: tuple(map(int, d.ip.split("."))))

    for device in devices:
        table.add_row(*format_device_row(device))

    console.print(table)

    # Show usage hints
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  Use discovered URLs with the camera command:")
    console.print()

    # Show example with first RTSP device
    rtsp_device = next((d for d in devices if d.protocol == "rtsp"), None)
    if rtsp_device:
        console.print(f"  [dim]cyberwave-cli camera -u \"{rtsp_device.url}\"[/dim]")
    else:
        http_device = next((d for d in devices if d.protocol == "http"), None)
        if http_device:
            console.print(
                f"  [dim]cyberwave-cli camera -u \"http://{http_device.ip}/snapshot.jpg\"[/dim]"
            )
