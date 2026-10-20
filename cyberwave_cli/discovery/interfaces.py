from __future__ import annotations

import ipaddress
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

SYS_CLASS_NET = Path("/sys/class/net")
_ARPHRD_ETHER = "1"


@dataclass(frozen=True)
class WiredInterface:
    name: str
    mac: str
    carrier: bool
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


def ipv4_addresses(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, list[ipaddress.IPv4Interface]]:
    try:
        result = run(
            ["ip", "-j", "-4", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        entries = json.loads(result.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    found: dict[str, list[ipaddress.IPv4Interface]] = {}
    for entry in entries if isinstance(entries, list) else []:
        name = entry.get("ifname") if isinstance(entry, dict) else None
        if not name:
            continue
        for info in entry.get("addr_info") or []:
            if info.get("family") != "inet" or not info.get("local"):
                continue
            try:
                address = ipaddress.IPv4Interface(f"{info['local']}/{info.get('prefixlen', 32)}")
            except ValueError:
                continue
            found.setdefault(name, []).append(address)
    return found


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
            carrier=_read(path / "carrier") == "1",
            addresses=tuple(addresses.get(path.name, ())),
        )
        for path in entries
        if is_physical_wired(path)
    ]
