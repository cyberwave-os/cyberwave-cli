"""Network scan command for discovering IP cameras and NVRs."""

from __future__ import annotations

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table

from ..discovery import NetworkScanner, DiscoveredDevice, DeviceType

console = Console()


def _scan_wired(*, timeout: float, output_json: bool) -> None:
    import json

    from ..discovery import hardware
    from ..discovery.interfaces import wired_interfaces

    ports = wired_interfaces()
    devices = hardware.discover(ports, timeout=timeout, enumerate_types=True, arp_sweep=True)
    if output_json:
        # Plain stdout: Rich would wrap, highlight and parse markup in device-supplied strings.
        click.echo(
            json.dumps(
                {
                    "interfaces": [
                        {"name": p.name, "mac": p.mac, "carrier": p.carrier,
                         "addresses": [str(a) for a in p.addresses]}
                        for p in ports
                    ],
                    "devices": [
                        {
                            "interface": d.interface, "ip": d.ip, "mac": d.mac, "vendor": d.vendor,
                            "hostname": d.hostname, "reachable": d.reachable,
                            "services": {
                                t: {"instance": s.instance, "port": s.port, "txt": s.txt}
                                for t, s in d.services.items()
                            },
                        }
                        for d in devices
                    ],
                },
                indent=2,
            )
        )
        return

    if not ports:
        console.print("[yellow]This host has no wired Ethernet ports.[/yellow]")
        return
    for port in ports:
        if not port.carrier:
            state = "[dim]no link[/dim]"
        elif not port.has_ipv4:
            state = "[yellow]link up, no IPv4 address: devices on it cannot be found[/yellow]"
        else:
            state = ", ".join(str(a) for a in port.addresses)
        console.print(f"[bold]{port.name}[/bold] ({port.mac}): {state}")
    if not devices:
        console.print("\n[yellow]No devices found on the wired ports.[/yellow]")
        return
    table = Table(title=f"Found {len(devices)} wired device(s)")
    table.add_column("Port")
    table.add_column("IP Address", style="cyan")
    table.add_column("MAC", style="magenta")
    table.add_column("Vendor", style="yellow")
    table.add_column("mDNS services", style="green")
    for d in devices:
        services = ", ".join(
            f"{t}:{s.port}" if s.port else t for t, s in sorted(d.services.items())
        )
        ip = d.ip if d.reachable else f"{d.ip} [yellow](other subnet)[/yellow]"
        table.add_row(d.interface, ip, d.mac or "-", d.vendor or "-", services or "-")
    console.print(table)


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
@click.option(
    "--wired",
    is_flag=True,
    help="List the hardware on this host's Ethernet ports (mDNS + ARP), with MAC addresses",
)
def scan(
    subnet: str | None,
    timeout: float,
    no_ports: bool,
    no_onvif: bool,
    no_upnp: bool,
    output_json: bool,
    wired: bool,
) -> None:
    """Scan the network for IP cameras and NVRs.

    Discovers devices using multiple methods:
    - TCP port scanning (RTSP, HTTP)
    - ONVIF WS-Discovery
    - UPnP/SSDP

    With --wired, lists what is plugged into this host's Ethernet ports
    instead: every mDNS service announced there, and every host answering
    ARP, with its MAC address and vendor. This is what `cyberwave edge
    install` matches twins against.

    \b
    Examples:
        cyberwave scan
        cyberwave scan -s 10.0.0
        cyberwave scan --json
        cyberwave scan --no-ports  # Only use discovery protocols
        cyberwave scan --wired     # hardware on the Ethernet ports
    """
    if wired:
        _scan_wired(timeout=max(timeout, 2.5), output_json=output_json)
        return

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
        console.print(f"  [dim]cyberwave camera -u \"{rtsp_device.url}\"[/dim]")
    else:
        http_device = next((d for d in devices if d.protocol == "http"), None)
        if http_device:
            console.print(
                f"  [dim]cyberwave camera -u \"http://{http_device.ip}/snapshot.jpg\"[/dim]"
            )
