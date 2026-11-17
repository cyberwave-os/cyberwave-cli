from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

ARP_TABLE = Path("/proc/net/arp")
_ATF_COM = 0x2
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


def read_arp_table(path: Path = ARP_TABLE) -> dict[tuple[str, str], str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return {}
    table: dict[tuple[str, str], str] = {}
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
            table[(device, ip)] = mac
    return table


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
