"""IP -> MAC without raw sockets: a UDP datagram to an on-link address makes the kernel ARP."""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

ARP_TABLE = Path("/proc/net/arp")
_ATF_COM = 0x2
_DISCARD_PORT = 9
MAX_SWEEP_HOSTS = 254
_OUI_DATABASES = (
    Path("/usr/share/nmap/nmap-mac-prefixes"),
    Path("/usr/share/ieee-data/oui.txt"),
    Path("/usr/share/hwdata/oui.txt"),
    Path("/usr/share/misc/oui.txt"),
)
_HEX = re.compile(r"[0-9a-f]")


def normalize_mac(mac: str) -> str:
    digits = "".join(_HEX.findall(mac.lower()))
    if len(digits) != 12:
        return ""
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def is_mac_prefix(prefix: str) -> bool:
    digits = "".join(_HEX.findall(prefix.lower()))
    return (
        6 <= len(digits) <= 12
        and len(digits) % 2 == 0
        and not re.search(r"[^0-9a-f:\-. ]", prefix.lower())
    )


def mac_has_prefix(mac: str, prefix: str) -> bool:
    mac_digits = "".join(_HEX.findall(mac.lower()))
    prefix_digits = "".join(_HEX.findall(prefix.lower()))
    return len(prefix_digits) >= 6 and mac_digits.startswith(prefix_digits)


def read_arp_table(path: Path = ARP_TABLE) -> dict[str, tuple[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return {}
    table: dict[str, tuple[str, str]] = {}
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            continue
        ip, _hw_type, flags, mac, _mask, device = fields[:6]
        try:
            complete = int(flags, 16) & _ATF_COM
        except ValueError:
            continue
        mac = normalize_mac(mac)
        if complete and mac and mac != "00:00:00:00:00:00":
            table[ip] = (mac, device)
    return table


def _poke(ips: Iterable[str]) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setblocking(False)
        for ip in ips:
            try:
                sock.sendto(b"", (ip, _DISCARD_PORT))
            except OSError:
                continue
    finally:
        sock.close()


def resolve_macs(
    ips: Iterable[str],
    *,
    interface: str,
    timeout: float = 2.0,
    table_path: Path = ARP_TABLE,
) -> dict[str, str]:
    targets = list(dict.fromkeys(ips))
    if not targets:
        return {}
    _poke(targets)
    deadline = time.monotonic() + timeout
    found: dict[str, str] = {}
    while True:
        table = read_arp_table(table_path)
        for ip in targets:
            entry = table.get(ip)
            if entry and entry[1] == interface:
                found[ip] = entry[0]
        if len(found) == len(targets) or time.monotonic() >= deadline:
            return found
        time.sleep(0.2)


def sweep_hosts(address: ipaddress.IPv4Interface) -> list[str]:
    network = address.network
    if network.num_addresses - 2 > MAX_SWEEP_HOSTS:
        return []
    own = str(address.ip)
    return [str(host) for host in network.hosts() if str(host) != own]


def arp_sweep(
    address: ipaddress.IPv4Interface,
    *,
    interface: str,
    timeout: float = 3.0,
    table_path: Path = ARP_TABLE,
) -> dict[str, str]:
    hosts = sweep_hosts(address)
    if not hosts:
        return {}
    _poke(hosts)
    time.sleep(timeout)
    table = read_arp_table(table_path)
    wanted = set(hosts)
    return {ip: mac for ip, (mac, device) in table.items() if device == interface and ip in wanted}


@lru_cache(maxsize=None)
def _vendor_table() -> dict[str, str]:
    for path in _OUI_DATABASES:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        table: dict[str, str] = {}
        for line in text.splitlines():
            match = re.match(
                r"^([0-9A-Fa-f]{2})[-:]?([0-9A-Fa-f]{2})[-:]?([0-9A-Fa-f]{2})\s+(?:\(hex\)\s+)?(.+)$",
                line,
            )
            if match:
                table.setdefault("".join(match.group(1, 2, 3)).lower(), match.group(4).strip())
        if table:
            return table
    return {}


def vendor_for(mac: str) -> Optional[str]:
    digits = "".join(_HEX.findall(mac.lower()))
    return _vendor_table().get(digits[:6]) if len(digits) >= 6 else None
