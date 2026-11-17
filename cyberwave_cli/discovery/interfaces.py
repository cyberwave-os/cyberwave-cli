from __future__ import annotations

import ipaddress
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

SYS_CLASS_NET = Path("/sys/class/net")
_ARPHRD_ETHER = "1"


@dataclass(frozen=True)
class WiredInterface:
    name: str
    mac: str
    carrier: Optional[bool]
    addresses: tuple[ipaddress.IPv4Interface, ...] = ()

    @property
    def has_ipv4(self) -> bool:
        return bool(self.addresses)

    def address_for(self, ip: str) -> Optional[ipaddress.IPv4Interface]:
        try:
            target = ipaddress.IPv4Address(ip)
        except ValueError:
            return None
        for address in self.addresses:
            if target in address.network:
                return address
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def is_physical_wired(path: Path) -> bool:
    """No ``device`` link: virtual (docker0, veth, bridge); ``wireless``/``phy80211``: Wi-Fi."""
    return (
        (path / "device").exists()
        and _read(path / "type") == _ARPHRD_ETHER
        and not (path / "wireless").exists()
        and not (path / "phy80211").exists()
    )


@dataclass(frozen=True)
class IPv4Snapshot:
    addresses: Mapping[str, Sequence[ipaddress.IPv4Interface]]
    error: Optional[str] = None

    def matches(self, ip: str) -> list[tuple[str, ipaddress.IPv4Interface]]:
        target = ipaddress.IPv4Address(ip)
        return [
            (name, address)
            for name, entries in self.addresses.items()
            for address in entries
            if target in address.network
        ]

    def overlaps(self) -> list[tuple[str, str, str, str]]:
        entries = [(name, a.network) for name, values in self.addresses.items() for a in values]
        return [
            (n, str(a), m, str(b))
            for i, (n, a) in enumerate(entries)
            for m, b in entries[i + 1 :]
            if n != m and a.overlaps(b)
        ]


@dataclass(frozen=True)
class RouteObservation:
    interface: Optional[str] = None
    source: Optional[str] = None
    gateway: Optional[str] = None
    error: Optional[str] = None


def read_ipv4(run: Callable = subprocess.run) -> IPv4Snapshot:
    try:
        result = run(
            ["ip", "-j", "-4", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        entries = json.loads(result.stdout)
        if not isinstance(entries, list):
            raise ValueError("expected an address list")
        found = {}
        for entry in entries:
            name = entry["ifname"]
            if not isinstance(name, str):
                raise ValueError("invalid interface name")
            found[name] = [
                ipaddress.IPv4Interface(f"{a['local']}/{a['prefixlen']}")
                for a in entry.get("addr_info", [])
                if a.get("family") == "inet"
            ]
        return IPv4Snapshot(found)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError):
        return IPv4Snapshot({}, "IPv4 inspection unavailable (ip command or invalid response)")


def ipv4_addresses(run: Callable = subprocess.run) -> dict:
    return dict(read_ipv4(run).addresses)


def wired_interfaces(
    sys_class_net: Path = SYS_CLASS_NET,
    addresses: Optional[Mapping[str, Sequence[ipaddress.IPv4Interface]]] = None,
) -> list[WiredInterface]:
    if addresses is None:
        addresses = ipv4_addresses()
    try:
        entries = sorted(sys_class_net.iterdir())
    except OSError:
        return []
    return [
        WiredInterface(
            name=path.name,
            mac=_read(path / "address").lower(),
            carrier={"1": True, "0": False}.get(_read(path / "carrier")),
            addresses=tuple(addresses.get(path.name, ())),
        )
        for path in entries
        if is_physical_wired(path)
    ]


def route_to(ip: str, run: Callable = subprocess.run) -> RouteObservation:
    try:
        target = str(ipaddress.IPv4Address(ip))
        result = run(
            ["ip", "-j", "-4", "route", "get", target],
            env={**os.environ, "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        entries = json.loads(result.stdout)
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ValueError("expected a route list")
        for entry in entries:
            if entry.get("type", "unicast") not in {"unicast", "local"}:
                continue
            if "linkdown" in entry.get("flags", []):
                continue
            if not isinstance(entry.get("dev"), str) or not entry["dev"]:
                raise ValueError("route lacks an interface")
            for key in ("prefsrc", "src", "gateway"):
                if key in entry:
                    ipaddress.IPv4Address(entry[key])
            return RouteObservation(
                entry["dev"], entry.get("prefsrc") or entry.get("src"), entry.get("gateway")
            )
        return RouteObservation()
    except subprocess.CalledProcessError as exc:
        if (
            exc.returncode == 2
            and (exc.stderr or "").strip() == "RTNETLINK answers: Network is unreachable"
        ):
            return RouteObservation()
        return RouteObservation(error="Route inspection unavailable (ip command failed)")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return RouteObservation(
            error="Route inspection unavailable (ip command or invalid response)"
        )


_OVERLAP_ADVICE = (
    "A route through another interface does not establish access to the device. "
    "Confirm the physical link and mask; use non-overlapping networks or "
    "operator-managed network isolation."
)


def cached_neighbor_line(device: Any) -> str:
    return (
        f"Cached neighbor: {device.interface} {device.ip} {device.mac}; "
        "freshness and identity unverified"
    )


def observe_host(
    *, wired_only: bool = False, addresses: bool = False
) -> tuple[IPv4Snapshot, list[WiredInterface], list[Any], list[str]]:
    """Read the host once: (snapshot, wired ports, cached neighbors, description lines)."""
    from . import hardware

    snapshot = read_ipv4()
    ports = wired_interfaces(addresses=snapshot.addresses)
    devices = hardware.cached_devices()
    lines = describe_host(snapshot, ports)
    if addresses:
        for name, assigned in snapshot.addresses.items():
            lines.append(f"{name}: {', '.join(map(str, assigned)) or 'no IPv4 address'}")
    names = {p.name for p in ports}
    lines.extend(
        cached_neighbor_line(d) for d in devices if not wired_only or d.interface in names
    )
    return snapshot, ports, devices, lines


def describe_endpoint(
    ip: str, snapshot: IPv4Snapshot, route: RouteObservation, ports: Sequence[WiredInterface] = ()
) -> list[str]:
    lines = [snapshot.error] if snapshot.error else []
    matches = snapshot.matches(ip)
    lines.extend(f"Matching host prefix: {name} {address}" for name, address in matches)
    if not matches and not snapshot.error:
        lines.append("Matching host prefix: none (a gateway route may still apply)")
    if route.error:
        lines.append(route.error)
    elif route.interface:
        details = [route.interface]
        if route.source:
            details.append(f"source {route.source}")
        if route.gateway:
            details.append(f"gateway {route.gateway}")
        lines.append("Kernel-selected route: " + ", ".join(details) + " (CLI context)")
    else:
        lines.append("Kernel-selected route: none returned")
    lines.append("Intended device: not verified")
    for port in ports:
        if port.carrier and not port.addresses and not snapshot.error:
            others = sorted({name for name, _ in matches if name != port.name})
            if others:
                lines.append(
                    f"If this device is on the separate {port.name} cable, its addressing "
                    f"overlaps the network on {', '.join(others)}. {_OVERLAP_ADVICE}"
                )
    return lines


def describe_host(snapshot: IPv4Snapshot, ports: Sequence[WiredInterface]) -> list[str]:
    lines = [snapshot.error] if snapshot.error else []
    for port in ports:
        state = (
            "carrier inspection unavailable"
            if port.carrier is None
            else "cable connected"
            if port.carrier
            else "cable/link not detected"
        )
        addressing = (
            "IPv4 inspection unavailable"
            if snapshot.error
            else ", ".join(map(str, port.addresses)) or "no IPv4 address; device addressing unknown"
        )
        lines.append(f"{port.name}: {state}, {addressing}")
        others = sorted(
            name
            for name, entries in snapshot.addresses.items()
            if entries and name != port.name and (SYS_CLASS_NET / name / "device").exists()
        )
        # This host-wide advice concerns another physical link. Keep software
        # interfaces in the actual prefix/route observations below and in
        # describe_endpoint: their routes can still overlap a known endpoint.
        if port.carrier and not port.addresses and not snapshot.error and others:
            lines.append(
                f"If a device on the separate {port.name} cable uses an address within a prefix "
                f"on {', '.join(others)}, the networks may overlap. {_OVERLAP_ADVICE} "
                "No address reference is assigned to this cable."
            )
    for name, a, other, b in snapshot.overlaps():
        lines.append(
            f"Possible overlap: {name} {a} and {other} {b}; "
            "this may be an intentional same-LAN or managed configuration."
        )
    return lines
