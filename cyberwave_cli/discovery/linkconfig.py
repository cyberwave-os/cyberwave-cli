"""NetworkManager profiles for wired ports, documented in docs/wired-network-devices.md.

Extend a port's running profile in place: activating another drops its lease and default route.
"""

from __future__ import annotations

import ipaddress
import subprocess
import time
from typing import Callable, Iterable, Iterator, Optional, Sequence

from .interfaces import ipv4_addresses

DEFAULT_PROFILE_PREFIX = "cyberwave-"
DEFAULT_DAD_TIMEOUT_MS = 3000
_DHCP_FOREVER = "2147483647"
_DHCP_AND_LINK_LOCAL = [
    "ipv4.method",
    "auto",
    "ipv4.dhcp-timeout",
    _DHCP_FOREVER,
    "ipv4.link-local",
    "enabled",
]

Runner = Callable[..., subprocess.CompletedProcess]


class LinkConfigError(RuntimeError):
    """NetworkManager refused or failed a change."""


def profile_name(interface: str, prefix: str = DEFAULT_PROFILE_PREFIX) -> str:
    return f"{prefix}{interface}"


def _nmcli(args: list[str], run: Runner) -> subprocess.CompletedProcess:
    try:
        return run(["nmcli", *args], capture_output=True, text=True, timeout=45, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or f"exit {exc.returncode}"
        raise LinkConfigError(detail) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LinkConfigError(str(exc)) from exc


def networkmanager_running(run: Runner = subprocess.run) -> bool:
    try:
        result = _nmcli(["-t", "-f", "RUNNING", "general"], run)
    except LinkConfigError:
        return False
    return result.stdout.strip() == "running"


def _method(name: str, run: Runner) -> str:
    return _nmcli(["-g", "ipv4.method", "connection", "show", name], run).stdout.strip()


def _profile_method(name: str, run: Runner) -> Optional[str]:
    profiles = _nmcli(["-t", "-f", "NAME", "connection", "show"], run).stdout.splitlines()
    return _method(name, run) if name in profiles else None


def _active_profile(interface: str, run: Runner) -> Optional[str]:
    result = _nmcli(["-g", "GENERAL.CONNECTION", "device", "show", interface], run)
    name = result.stdout.strip()
    return name if name and name != "--" else None


def _extend_active(
    active: str,
    interface: str,
    link_local: bool,
    addresses: Sequence[ipaddress.IPv4Interface],
    run: Runner,
) -> None:
    changes: list[str] = []
    if addresses:
        changes += ["+ipv4.addresses", ",".join(str(a) for a in addresses)]
    if link_local:
        if _method(active, run) == "disabled":
            raise LinkConfigError(f"{active} has IPv4 disabled, so {interface} gets no address")
        changes += ["ipv4.link-local", "enabled"]
    try:
        _nmcli(["connection", "modify", active, *changes], run)
    except LinkConfigError as exc:
        if not link_local:
            raise
        raise LinkConfigError(
            f"{exc} (link-local beside {active}'s own addressing needs NetworkManager 1.40)"
        ) from exc
    _nmcli(["device", "reapply", interface], run)


def configure(
    interface: str,
    *,
    link_local: bool = False,
    addresses: Sequence[ipaddress.IPv4Interface] = (),
    profile: Optional[str] = None,
    dad_timeout_ms: int = DEFAULT_DAD_TIMEOUT_MS,
    run: Runner = subprocess.run,
) -> str:
    """Give *interface* link-local and/or static *addresses*; returns the profile used."""
    if not link_local and not addresses:
        raise ValueError("nothing to configure")
    name = profile or profile_name(interface)
    active = None if profile else _active_profile(interface, run)
    if active is not None and active != name:
        _extend_active(active, interface, link_local, addresses, run)
        return active

    method = _profile_method(name, run)
    joined = ",".join(str(a) for a in addresses)
    if method is None:
        command = [
            *["connection", "add", "type", "ethernet", "ifname", interface, "con-name", name],
            *["ipv4.never-default", "yes", "ipv6.method", "link-local"],
            *["connection.autoconnect", "yes"],
        ]
        static = ["ipv4.addresses", joined] if addresses else []
    else:
        command = ["connection", "modify", name]
        static = ["+ipv4.addresses", joined] if addresses else []
    if addresses:
        static += ["ipv4.dad-timeout", str(dad_timeout_ms)]
    try:
        _nmcli([*command, *_DHCP_AND_LINK_LOCAL, *static], run)
    except LinkConfigError:
        legacy = ["ipv4.method", "auto", *static] if addresses else ["ipv4.method", "link-local"]
        _nmcli([*command, *legacy], run)

    try:
        _nmcli(["connection", "up", name], run)
    except LinkConfigError:
        if method is None:
            # Created here: left behind, it would autoconnect at the next boot.
            try:
                _nmcli(["connection", "delete", name], run)
            except LinkConfigError:
                pass
        raise
    return name


def adopt_dhcp(
    interface: str, *, prefix: str = DEFAULT_PROFILE_PREFIX, run: Runner = subprocess.run
) -> Optional[str]:
    """Let a link-local-only profile this CLI made also take DHCP; the profile if it changed."""
    active = _active_profile(interface, run)
    if active is None or not active.startswith(prefix) or _method(active, run) != "link-local":
        return None
    try:
        _nmcli(["connection", "modify", active, *_DHCP_AND_LINK_LOCAL], run)
    except LinkConfigError:
        return None
    _nmcli(["connection", "up", active], run)
    return active


def wait_for_ipv4(
    interface: str,
    *,
    timeout: float = 20.0,
    want: Optional[ipaddress.IPv4Interface] = None,
    addresses: Callable[[], dict[str, list[ipaddress.IPv4Interface]]] = ipv4_addresses,
) -> list[ipaddress.IPv4Interface]:
    """[] on timeout. Link-local takes seconds: NetworkManager first checks it is unused."""
    deadline = time.monotonic() + timeout
    while True:
        current = addresses().get(interface, [])
        if current and (want is None or want in current):
            return current
        if time.monotonic() >= deadline:
            return []
        time.sleep(0.5)


def pick_free_address(
    network: ipaddress.IPv4Network,
    taken: Iterable[str],
    *,
    reserved_top: int = 4,
) -> Optional[ipaddress.IPv4Interface]:
    """Highest free host address, skipping the *reserved_top* ones gateways and switches hold."""
    avoid = set(taken)
    top = network.broadcast_address if network.prefixlen < 31 else network.network_address
    fallback = None
    # Walked lazily from the top: listing a /8 would take minutes and gigabytes.
    for host in _hosts_descending(network):
        if str(host) in avoid:
            continue
        if int(top) - int(host) > reserved_top:
            return ipaddress.IPv4Interface(f"{host}/{network.prefixlen}")
        if fallback is None:
            fallback = host
    if fallback is None:
        return None
    return ipaddress.IPv4Interface(f"{fallback}/{network.prefixlen}")


def _hosts_descending(network: ipaddress.IPv4Network) -> Iterator[ipaddress.IPv4Address]:
    if network.prefixlen >= 31:
        yield from reversed(list(network.hosts()))
        return
    for value in range(int(network.broadcast_address) - 1, int(network.network_address), -1):
        yield ipaddress.IPv4Address(value)
