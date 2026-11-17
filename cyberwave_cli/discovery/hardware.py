from __future__ import annotations

import inspect
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional
from urllib.parse import urlsplit

from . import neighbors

METADATA_KEY = "network_discovery"
METADATA_ENV_PREFIX = "CYBERWAVE_METADATA_"
TEMPLATE_FIELDS = ("ip", "mac", "hostname", "port", "instance", "interface")
TXT_FIELD_PREFIX = "txt_"


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
    return tuple(item.strip() for item in _as_list(value) if isinstance(item, str) and item.strip())


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


@dataclass(frozen=True)
class ProfileSelection:
    status: str
    name: Optional[str] = None
    entry: Optional[Mapping] = None
    reason: str = ""

    @property
    def unresolved(self) -> bool:
        return self.status not in {"resolved", "absent"}


def select_profile(
    drivers: Any, *, child_registry_ids: Optional[set[str]] = None, resolver: Any = None
) -> ProfileSelection:
    if drivers is None or drivers == {}:
        return ProfileSelection("absent")
    if not isinstance(drivers, Mapping) or any(
        not isinstance(k, str) or not isinstance(v, Mapping) for k, v in drivers.items()
    ):
        return ProfileSelection("unavailable", reason="malformed driver profiles")
    if child_registry_ids is None and len(drivers) > 1:
        return ProfileSelection("unavailable", reason="complete child-registry context unavailable")
    if resolver is None:
        from ..config import ensure_edge_core_importable

        ensure_edge_core_importable()
        try:
            from cyberwave_edge_core.driver_selection import _matched_driver_profile
        except ImportError:
            return ProfileSelection(
                "unavailable", reason="compatible runtime profile resolver unavailable"
            )
        resolver = _matched_driver_profile
    try:
        inspect.signature(resolver).bind(drivers, child_registry_ids=child_registry_ids or set())
    except (TypeError, ValueError):
        return ProfileSelection("unavailable", reason="incompatible runtime profile resolver")
    result = resolver(drivers, child_registry_ids=child_registry_ids or set())
    if not isinstance(result, tuple) or len(result) != 2:
        return ProfileSelection("unavailable", reason="incompatible runtime profile result")
    name, entry = result
    if entry is None:
        return ProfileSelection("no-match", reason="no matching runtime profile")
    if not isinstance(name, str) or name not in drivers or entry != drivers[name]:
        return ProfileSelection("unavailable", reason="incompatible runtime profile result")
    params = entry.get("params")
    if params is None:
        params = []
    if (
        not isinstance(params, list)
        or not all(isinstance(p, str) for p in params)
        or (entry.get("shared_env") is not None and not isinstance(entry["shared_env"], Mapping))
    ):
        return ProfileSelection("unavailable", name, reason="malformed selected environment")
    if any(k in entry for k in ("services", "compose", "docker_compose", "env")) or any(
        p == "--env-file" or p.startswith("--env-file=") for p in params
    ):
        return ProfileSelection(
            "unavailable", name, reason="service/environment overrides unresolved"
        )
    if any("=" not in raw for raw in _param_env_raw(params)):
        return ProfileSelection("unavailable", name, reason="host environment override unresolved")
    return ProfileSelection("resolved", name, entry)


def explicit_metadata_values(selection: ProfileSelection) -> dict[str, str]:
    if selection.status != "resolved":
        return {}
    return {
        key[len(METADATA_ENV_PREFIX) :].lower(): value.strip()
        for key, value in _env_pairs(selection.entry)
        if isinstance(key, str)
        and isinstance(value, str)
        and key.startswith(METADATA_ENV_PREFIX)
        and len(key) > len(METADATA_ENV_PREFIX)
    }


def _param_env_raw(params: Any) -> Iterator[str]:
    """Raw ``-e`` / ``--env`` / ``--env=`` values, bare ``KEY`` included."""
    if not isinstance(params, list):
        return
    for index, param in enumerate(params):
        if param in ("-e", "--env"):
            yield params[index + 1] if index + 1 < len(params) else ""
        elif isinstance(param, str) and param.startswith("--env="):
            yield param[len("--env=") :]


def _param_env_pairs(params: Any) -> Iterator[tuple[str, str]]:
    for raw in _param_env_raw(params):
        if isinstance(raw, str):
            key, sep, value = raw.partition("=")
            if sep:
                yield key, value


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


def _env_pairs(entry: Mapping) -> Iterator[tuple[Any, Any]]:
    # Same precedence as the launcher: ``shared_env`` is merged over ``-e`` params.
    yield from _param_env_pairs(entry.get("params"))
    shared = entry.get("shared_env")
    if isinstance(shared, Mapping):
        yield from shared.items()


def driver_env_addresses(selection: ProfileSelection) -> dict[tuple[str, str], str]:
    if selection.status != "resolved":
        return {}
    values = {}
    for key, value in dict(_env_pairs(selection.entry)).items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        ip = ip_from_value(value)
        if ip is None:
            continue
        address = ipaddress.IPv4Address(ip)
        if address.is_private and not (
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_reserved
            or address.is_multicast
        ):
            values[(selection.name, key.strip())] = ip
    return values


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


@dataclass(frozen=True)
class DiscoverySpec:
    bind: tuple[tuple[str, str], ...]
    methods: frozenset[str] = frozenset()
    selection: ProfileSelection = field(default_factory=lambda: ProfileSelection("absent"))
    explicit: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_metadata(
        cls,
        *blocks: Any,
        explicit: Optional[Mapping[str, str]] = None,
        selection: Optional[ProfileSelection] = None,
    ) -> Optional["DiscoverySpec"]:
        raw = merge_blocks(*blocks)
        bind = _bind_templates(raw.get("bind"))
        if not bind:
            return None
        methods = _string_list(raw.get("methods"))
        if raw.get("mdns_services"):
            methods = (*methods, "mdns")
        return cls(
            bind, frozenset(methods), selection or ProfileSelection("absent"), dict(explicit or {})
        )


@dataclass(frozen=True)
class AddressReference:
    profile: str
    source: str
    ip_address: str

    @classmethod
    def from_selection(cls, selection: ProfileSelection) -> list["AddressReference"]:
        return [
            cls(profile, source, ip)
            for (profile, source), ip in driver_env_addresses(selection).items()
        ]


@dataclass(frozen=True)
class NetworkDevice:
    ip: str
    interface: str
    mac: str = ""
    vendor: str = ""


def cached_devices() -> list[NetworkDevice]:
    return [
        NetworkDevice(ip, interface, mac, neighbors.vendor_for(mac) or "")
        for (interface, ip), mac in neighbors.read_arp_table().items()
    ]
