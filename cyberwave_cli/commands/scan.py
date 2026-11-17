from __future__ import annotations

import json
from dataclasses import asdict

import click

from ..discovery import interfaces, probes


@click.command()
@click.option("--target", help="One numeric unicast IPv4 address; no DNS or ranges.")
@click.option("--port", type=click.IntRange(1, 65535), help="One documented TCP service port.")
@click.option(
    "--verify", is_flag=True, help="Explicitly request one TCP connection, at most five seconds."
)
@click.option("--interface", help="Enforce this interface or skip the requested check.")
@click.option("--tls", is_flag=True, help="Validate TLS on the same connection.")
@click.option("--server-name", help="Expected TLS server identity/SNI; does not resolve DNS.")
@click.option(
    "--ca-file", type=click.Path(exists=True, dir_okay=False), help="Trusted private CA bundle."
)
@click.option(
    "--certificate-sha256",
    help="Independently supplied device certificate fingerprint. Checked in addition to "
    "normal TLS validation, so a self-signed device also needs --ca-file.",
)
@click.option(
    "--wired", is_flag=True, help="Show local wired inventory only; never discover devices."
)
@click.option("--json", "output_json", is_flag=True, help="Output results as JSON.")
@click.option("--subnet", "-s", hidden=True)
@click.option("--timeout", "-t", type=float, hidden=True)
@click.option("--no-ports", is_flag=True, hidden=True)
@click.option("--no-onvif", is_flag=True, hidden=True)
@click.option("--no-upnp", is_flag=True, hidden=True)
def scan(
    target,
    port,
    verify,
    interface,
    tls,
    server_name,
    ca_file,
    certificate_sha256,
    wired,
    output_json,
    subnet,
    timeout,
    no_ports,
    no_onvif,
    no_upnp,
):
    """Inspect local network configuration, or verify one known endpoint.

    Broad network scanning is no longer supported. Request a check explicitly:
    cyberwave scan --target 192.168.50.10 --port 554 --verify
    """
    if subnet is not None or timeout is not None or no_ports or no_onvif or no_upnp:
        raise click.UsageError(
            "Broad scan options were removed. Use --target IP --port PORT "
            "--verify for one known service, or no options for local observations."
        )
    if verify and (not target or not port):
        raise click.UsageError("--verify requires --target and --port; no ports are guessed.")
    verify_only = [
        flag
        for flag, value in (
            ("--port", port),
            ("--interface", interface),
            ("--tls", tls),
            ("--server-name", server_name),
            ("--ca-file", ca_file),
            ("--certificate-sha256", certificate_sha256),
        )
        if value
    ]
    if verify_only and not verify:
        raise click.UsageError(
            f"{', '.join(verify_only)} only appl{'ies' if len(verify_only) == 1 else 'y'} "
            "with --verify; without it no connection is made."
        )
    if (tls and not server_name) or ((server_name or ca_file or certificate_sha256) and not tls):
        raise click.UsageError("TLS options require --tls and --server-name.")
    if target:
        try:
            target = probes.numeric_target(target)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--target") from exc
    try:
        probes.normalize_fingerprint(certificate_sha256)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--certificate-sha256") from exc
    snapshot, ports, devices, lines = interfaces.observe_host(
        wired_only=wired, addresses=not target and not wired
    )
    route = interfaces.route_to(target) if target else None
    if target:
        lines.extend(interfaces.describe_endpoint(target, snapshot, route, ports))
    result = None
    echoed = 0
    if verify:
        matches = snapshot.matches(target)
        candidates = {name for name, _ in matches} | {
            d.interface for d in devices if d.ip == target
        }
        broadcast = any(
            target == str(a.network.broadcast_address) and a.network.prefixlen < 31
            for values in snapshot.addresses.values()
            for a in values
        )
        if broadcast:
            raise click.UsageError("The selected address is a configured subnet broadcast address.")
        if len(candidates) > 1 and not interface:
            lines.append(
                "Verification skipped: ambiguous interfaces; select --interface explicitly."
            )
        else:
            if not output_json:
                click.echo("\n".join(lines))
                echoed = len(lines)
                click.echo(
                    f"Checking {target}:{port} via {interface or 'kernel route'}; "
                    f"{'TLS' if tls else 'TCP'}, five-second deadline, no retries."
                )
            try:
                result = probes.verify_endpoint(
                    target,
                    port,
                    interface=interface,
                    tls_name=server_name if tls else None,
                    ca_file=ca_file,
                    certificate_sha256=certificate_sha256,
                )
            except ValueError as exc:
                raise click.UsageError(str(exc)) from exc
            lines.extend(
                [
                    f"Selected TCP service responded: {result.service_responded}",
                    f"Expected TLS server authenticated: {result.tls_server_authenticated}",
                    f"Expected device credential matched: {result.device_credential_matched}",
                    f"Identity: {result.identity}",
                    f"Actual local socket address: {result.local_address}",
                ]
            )
            if result.error:
                lines.append(result.error)
    lines.append("Host network configuration was not changed.")
    if output_json:
        click.echo(
            json.dumps(
                {
                    "observations": lines,
                    "route": asdict(route) if route else None,
                    "verification": asdict(result) if result else None,
                },
                indent=2,
            )
        )
    else:
        click.echo("\n".join(lines[echoed:]))
