"""Find hardware on wired links and bind twins to it from ``network_discovery`` metadata.

The metadata keys are documented in docs/wired-network-devices.md.
"""

from __future__ import annotations

import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from ..url_utils import redact_userinfo
from . import mdns, neighbors, probes
from .interfaces import WiredInterface

METADATA_KEY = "network_discovery"
MAC_METADATA_KEY = "mac_address"
METADATA_ENV_PREFIX = "CYBERWAVE_METADATA_"
METHOD_MDNS = "mdns"
METHOD_ARP = "arp"
METHOD_PORTS = "ports"
KNOWN_METHODS = frozenset({METHOD_MDNS, METHOD_ARP, METHOD_PORTS})
TEMPLATE_FIELDS = ("ip", "mac", "hostname", "port", "instance", "interface")
TXT_FIELD_PREFIX = "txt_"
DEFAULT_SUBNET_PREFIX = 24
DEFAULT_TIMEOUT = 2.5

SCORE_MDNS = 100
SCORE_MAC_PREFIX = 50
SCORE_PORTS = 25


class _StrictFields(dict):
    """Template fields: an unknown name is an error, an absent TXT record is empty."""

    def __missing__(self, key: str) -> str:
        if key.startswith(TXT_FIELD_PREFIX) and len(key) > len(TXT_FIELD_PREFIX):
            return ""
        raise KeyError(key)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _string_list(value: Any) -> tuple[str, ...]:
    return tuple(
        item.strip() for item in _as_list(value) if isinstance(item, str) and item.strip()
    )


def merge_blocks(*blocks: Any) -> dict[str, Any]:
    """Merge ``network_discovery`` blocks, later ones winning; ``bind`` merges per field."""
    merged: dict[str, Any] = {}
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        previous_bind = merged.get("bind")
        merged.update(block)
        if isinstance(previous_bind, Mapping) and isinstance(block.get("bind"), Mapping):
            merged["bind"] = {**previous_bind, **block["bind"]}
    return merged


def _bind_templates(raw: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, Mapping):
        return ()
    dummy = _StrictFields({name: "x" for name in TEMPLATE_FIELDS})
    templates: list[tuple[str, str]] = []
    for key, template in raw.items():
        if not isinstance(key, str) or not key or not isinstance(template, str):
            continue
        try:
            template.format_map(dummy)
        except (KeyError, ValueError, IndexError):
            continue
        templates.append((key, template))
    return tuple(templates)


def explicit_metadata_values(drivers: Any) -> dict[str, str]:
    """Driver params ``-e CYBERWAVE_METADATA_<FIELD>=value``, parsed the way edge-core does."""
    values: dict[str, str] = {}
    entries = drivers.values() if isinstance(drivers, Mapping) else ()
    for entry in entries:
        params = entry.get("params") if isinstance(entry, Mapping) else None
        if not isinstance(params, list):
            continue
        for index, param in enumerate(params):
            raw = None
            if param in ("-e", "--env") and index + 1 < len(params):
                raw = params[index + 1]
            elif isinstance(param, str) and param.startswith("--env="):
                raw = param.split("=", 1)[1]
            if not isinstance(raw, str):
                continue
            key, sep, value = raw.partition("=")
            if sep and key.startswith(METADATA_ENV_PREFIX) and len(key) > len(METADATA_ENV_PREFIX):
                values.setdefault(key[len(METADATA_ENV_PREFIX) :].lower(), value.strip())
    return values


def ip_from_value(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if "://" in candidate:
        try:
            candidate = urlsplit(candidate).hostname or ""
        except ValueError:
            return None
    try:
        return str(ipaddress.IPv4Address(candidate))
    except ValueError:
        return None


_HOST_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
_PLACEHOLDER_OCTET = re.compile(r"\d{1,3}|[xX*?#]+")


def source_problem(value: Any) -> Optional[str]:
    """Why a fixed source cannot reach a device; None when it names an IP or a host name."""
    if not isinstance(value, str) or not value.strip():
        return "it is empty"
    text = value.strip()
    host = text
    if "://" in text:
        try:
            parts = urlsplit(text)
            parts.port  # noqa: B018 - raises ValueError on a malformed port
        except ValueError:
            return "it is not a valid URL"
        host = parts.netloc.rpartition("@")[2]
        if host.startswith("["):
            return None if "]" in host else "it is not a valid URL"
        host = host.partition(":")[0]
    if not host:
        return "it names no host"
    if ip_from_value(host):
        return None
    labels = host.split(".")
    if any(ch in host for ch in "<>{}") or (
        len(labels) == 4 and all(_PLACEHOLDER_OCTET.fullmatch(label) for label in labels)
    ):
        return f"{host} is a placeholder, not an address"
    if all(label.isdigit() for label in labels):
        return f"{host} is not a valid IP address"
    if not all(_HOST_LABEL.fullmatch(label) for label in labels):
        return f"{host} is not a valid host name"
    return None


def _optional_interface(value: Any) -> Optional[ipaddress.IPv4Interface]:
    if not isinstance(value, str) or "/" not in value:
        return None
    try:
        return ipaddress.IPv4Interface(value.strip())
    except ValueError:
        return None


@dataclass(frozen=True)
class DiscoverySpec:
    bind: tuple[tuple[str, str], ...]
    methods: frozenset[str] = frozenset()
    mdns_services: tuple[str, ...] = ()
    mac_prefixes: tuple[str, ...] = ()
    name_patterns: tuple[str, ...] = ()
    subnet_prefix: int = DEFAULT_SUBNET_PREFIX
    host_address: Optional[ipaddress.IPv4Interface] = None
    link_local: bool = False
    tcp_ports: tuple[int, ...] = ()
    timeout: float = DEFAULT_TIMEOUT
    fixed_field: Optional[str] = None
    fixed_value: Optional[str] = None

    @property
    def uses_mdns(self) -> bool:
        return METHOD_MDNS in self.methods and bool(self.mdns_services)

    @property
    def uses_arp(self) -> bool:
        return METHOD_ARP in self.methods and bool(self.mac_prefixes)

    @property
    def uses_ports(self) -> bool:
        return METHOD_PORTS in self.methods and bool(self.tcp_ports)

    @property
    def sweeps(self) -> bool:
        return self.uses_arp or self.uses_ports

    @property
    def searchable(self) -> bool:
        return self.uses_mdns or self.sweeps

    @property
    def ip_address(self) -> Optional[str]:
        return ip_from_value(self.fixed_value) if self.fixed_field else None

    @property
    def fixed_source(self) -> Optional[str]:
        if self.fixed_field is None:
            return None
        return f"{METADATA_ENV_PREFIX}{self.fixed_field.upper()}={self.fixed_value}"

    @property
    def fixed_problem(self) -> Optional[str]:
        return source_problem(self.fixed_value) if self.fixed_field else None

    def without_fixed_source(self) -> "DiscoverySpec":
        return replace(self, fixed_field=None, fixed_value=None)

    def subnet_of(self, ip: str) -> ipaddress.IPv4Network:
        if self.host_address is not None and ipaddress.IPv4Address(ip) in self.host_address.network:
            return self.host_address.network
        return ipaddress.IPv4Network(f"{ip}/{self.subnet_prefix}", strict=False)

    @classmethod
    def from_metadata(
        cls, *blocks: Any, explicit: Optional[Mapping[str, str]] = None
    ) -> Optional["DiscoverySpec"]:
        """Parse merged ``network_discovery`` blocks; None if they cannot locate or bind."""
        raw = merge_blocks(*blocks)
        services = tuple(
            mdns.normalize_service_type(s) for s in _string_list(raw.get("mdns_services"))
        )
        prefixes = tuple(
            p for p in _string_list(raw.get("mac_prefixes")) if neighbors.is_mac_prefix(p)
        )
        if "methods" in raw:
            methods = frozenset(m.lower() for m in _string_list(raw.get("methods"))) & KNOWN_METHODS
        else:
            methods = frozenset({METHOD_MDNS}) if services else frozenset()
        prefix = raw.get("subnet_prefix", DEFAULT_SUBNET_PREFIX)
        if isinstance(prefix, bool) or not isinstance(prefix, int) or not 1 <= prefix <= 32:
            prefix = DEFAULT_SUBNET_PREFIX
        timeout = raw.get("timeout", DEFAULT_TIMEOUT)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < timeout <= 60
        ):
            timeout = DEFAULT_TIMEOUT
        bind = _bind_templates(raw.get("bind"))
        fixed = next(((key, explicit[key]) for key, _t in bind if (explicit or {}).get(key)), None)
        spec = cls(
            bind=bind,
            methods=methods,
            mdns_services=services,
            mac_prefixes=prefixes,
            name_patterns=tuple(p.lower() for p in _string_list(raw.get("name_patterns"))),
            fixed_field=fixed[0] if fixed else None,
            fixed_value=fixed[1] if fixed else None,
            subnet_prefix=prefix,
            host_address=_optional_interface(raw.get("host_address")),
            link_local=raw.get("link_local") is True,
            tcp_ports=tuple(
                p
                for p in _as_list(raw.get("tcp_ports"))
                if isinstance(p, int) and not isinstance(p, bool) and 0 < p < 65536
            ),
            timeout=float(timeout),
        )
        if not spec.bind or not (spec.fixed_field or spec.searchable):
            return None
        return spec


@dataclass
class NetworkDevice:
    ip: str
    interface: str
    mac: str = ""
    vendor: str = ""
    hostname: str = ""
    reachable: bool = True
    services: dict[str, mdns.MdnsService] = field(default_factory=dict)
    open_ports: frozenset[int] = frozenset()

    @property
    def key(self) -> str:
        return self.mac or f"{self.interface}/{self.ip}"

    def names(self) -> list[str]:
        found = [s.instance.split("._", 1)[0] for s in self.services.values()]
        return [n for n in [*found, self.hostname] if n]

    def label(self) -> str:
        parts = [
            self.vendor or "Unknown vendor",
            self.mac or "MAC unknown",
            self.ip,
            f"({self.interface})" if self.interface else "",
        ]
        instance = next((s.instance.split("._", 1)[0] for s in self.services.values()), "")
        if instance:
            parts.append(f"· {instance}")
        return " ".join(p for p in parts if p)


def match_score(spec: DiscoverySpec, device: NetworkDevice) -> int:
    """How strongly *device* fits *spec*'s signature; 0 means it does not fit."""
    if spec.name_patterns and not any(
        fnmatchcase(name.lower(), pattern)
        for name in device.names()
        for pattern in spec.name_patterns
    ):
        return 0
    score = 0
    if spec.uses_mdns and any(service in device.services for service in spec.mdns_services):
        score += SCORE_MDNS
    if device.mac and any(neighbors.mac_has_prefix(device.mac, p) for p in spec.mac_prefixes):
        score += SCORE_MAC_PREFIX
    if spec.uses_ports and set(spec.tcp_ports) <= device.open_ports:
        score += SCORE_PORTS
    return score


def render_bind(spec: DiscoverySpec, device: NetworkDevice) -> dict[str, str]:
    service = next((device.services[s] for s in spec.mdns_services if s in device.services), None)
    port = service.port if service and service.port else None
    if port is None:
        port = next((p for p in spec.tcp_ports if p in device.open_ports), None)
    txt = service.txt if service else {}
    fields = _StrictFields(
        ip=device.ip,
        mac=device.mac,
        hostname=device.hostname,
        port=str(port) if port else "",
        instance=service.instance if service else "",
        interface=device.interface,
        **{f"{TXT_FIELD_PREFIX}{key.lower()}": value for key, value in txt.items()},
    )
    return {key: template.format_map(fields) for key, template in spec.bind}


def verify_binding(spec: DiscoverySpec, device: NetworkDevice, values: dict[str, str]) -> list[str]:
    problems = [
        f"TCP port {port} does not accept connections"
        for port in spec.tcp_ports
        if not probes.tcp_port_open(device.ip, port)
    ]
    for key, value in values.items():
        if value.startswith("rtsp://"):  # rtsps is not probed: that would need TLS
            status = probes.rtsp_status(value)
            if status not in probes.RTSP_PATH_PRESENT:
                answer = "no answer" if status is None else f"RTSP {status}"
                problems.append(f"{key} {redact_userinfo(value)} gave {answer}")
    return problems


def _browse_interface(
    interface: WiredInterface, service_types: Sequence[str], timeout: float, enumerate_types: bool
) -> mdns.BrowseResult:
    try:
        return mdns.browse(
            service_types,
            interface_name=interface.name,
            interface_ip=str(interface.addresses[0].ip),
            timeout=timeout,
            enumerate_types=enumerate_types,
        )
    except OSError:
        return mdns.BrowseResult([], set())


def _sweep_interface(interface: WiredInterface) -> dict[str, str]:
    found: dict[str, str] = {}
    for address in interface.addresses:
        found.update(neighbors.arp_sweep(address, interface=interface.name))
    return found


def discover(
    interfaces: Sequence[WiredInterface],
    *,
    mdns_services: Iterable[str] = (),
    arp_sweep: bool = False,
    targets: Iterable[str] = (),
    probe_ports: Iterable[int] = (),
    timeout: float = DEFAULT_TIMEOUT,
    enumerate_types: bool = False,
) -> list[NetworkDevice]:
    """Every device found on *interfaces* that have a link and an IPv4 address."""
    usable = [i for i in interfaces if i.carrier and i.has_ipv4]
    types = sorted({mdns.normalize_service_type(t) for t in mdns_services if t})
    browse = bool(types) or enumerate_types
    devices: dict[tuple[str, str], NetworkDevice] = {}

    def device(interface: WiredInterface, ip: str) -> NetworkDevice:
        entry = devices.get((interface.name, ip))
        if entry is None:
            entry = NetworkDevice(
                ip=ip, interface=interface.name, reachable=interface.address_for(ip) is not None
            )
            devices[(interface.name, ip)] = entry
        return entry

    for ip in targets:
        port = next((i for i in usable if i.address_for(ip) is not None), None)
        if port is not None:
            device(port, ip)

    with ThreadPoolExecutor(max_workers=max(2, 2 * len(usable))) as pool:
        browses = (
            {
                i.name: pool.submit(_browse_interface, i, types, timeout, enumerate_types)
                for i in usable
            }
            if browse
            else {}
        )
        sweeps = {i.name: pool.submit(_sweep_interface, i) for i in usable} if arp_sweep else {}
        for interface in usable:
            if interface.name in browses:
                for service in browses[interface.name].result().services:
                    for ip in service.addresses:
                        try:
                            ipaddress.IPv4Address(ip)
                        except ValueError:
                            continue
                        entry = device(interface, ip)
                        entry.services.setdefault(service.service_type, service)
                        entry.hostname = entry.hostname or service.host.removesuffix(".local")
            if interface.name in sweeps:
                for ip, mac in sweeps[interface.name].result().items():
                    device(interface, ip).mac = mac

    own = {str(address.ip) for interface in interfaces for address in interface.addresses}
    devices = {key: entry for key, entry in devices.items() if entry.ip not in own}

    for interface in usable:
        unresolved = [
            d.ip
            for (name, _ip), d in devices.items()
            if name == interface.name and d.reachable and not d.mac
        ]
        for ip, mac in neighbors.resolve_macs(unresolved, interface=interface.name).items():
            devices[(interface.name, ip)].mac = mac

    for entry in devices.values():
        entry.vendor = (neighbors.vendor_for(entry.mac) or "") if entry.mac else ""
    _probe_open_ports([d for d in devices.values() if d.mac], sorted(set(probe_ports)))
    return sorted(devices.values(), key=lambda d: (d.interface, ipaddress.IPv4Address(d.ip)))


def _probe_open_ports(devices: Sequence[NetworkDevice], ports: Sequence[int]) -> None:
    if not devices or not ports:
        return
    pairs = [(d, port) for d in devices for port in ports]
    with ThreadPoolExecutor(max_workers=min(64, len(pairs))) as pool:
        answers = pool.map(lambda pair: probes.tcp_port_open(pair[0].ip, pair[1], 1.0), pairs)
        for (device, port), is_open in zip(pairs, answers):
            if is_open:
                device.open_ports = device.open_ports | {port}
