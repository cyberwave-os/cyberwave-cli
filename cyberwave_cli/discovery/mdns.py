"""Standard-library mDNS / DNS-SD browser.

An ephemeral-port socket works beside avahi; a 5353 one hears devices that cannot unicast back.
"""

from __future__ import annotations

import random
import select
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SERVICE_ENUMERATION = "_services._dns-sd._udp.local"

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
CLASS_IN = 1
_CLASS_MASK = 0x7FFF
_IP_PKTINFO = getattr(socket, "IP_PKTINFO", 8)
_MAX_POINTER_HOPS = 64

Name = tuple[str, ...]


@dataclass(frozen=True)
class Record:
    """``data``: Name (PTR), dotted quad (A), (prio, weight, port, target) (SRV), bytes (TXT)."""

    name: Name
    rtype: int
    data: object


@dataclass
class MdnsService:
    instance: str
    service_type: str
    host: str = ""
    port: int = 0
    addresses: list[str] = field(default_factory=list)
    txt: dict[str, str] = field(default_factory=dict)


@dataclass
class BrowseResult:
    services: list[MdnsService]
    service_types: set[str]


def normalize_service_type(value: str) -> str:
    name = value.strip().rstrip(".").lower()
    if name.endswith(".local"):
        name = name[: -len(".local")]
    return name


def _labels(name: str) -> Name:
    """Split a name with no dots inside its labels (service types, the meta-query)."""
    return tuple(name.strip().rstrip(".").split("."))


def _key(name: Name) -> Name:
    return tuple(label.lower() for label in name)


def _display(name: Name) -> str:
    return ".".join(name)


def encode_name(labels: Name) -> bytes:
    out = bytearray()
    for label in labels:
        raw = label.encode("utf-8")
        if not raw or len(raw) > 63:
            raise ValueError(f"invalid DNS label {label!r}")
        out.append(len(raw))
        out += raw
    out.append(0)
    return bytes(out)


def build_query(questions: Iterable[tuple[Name, int]], txid: int = 0) -> bytes:
    encoded: list[bytes] = []
    for name, rtype in questions:
        try:
            encoded.append(encode_name(name) + struct.pack("!HH", rtype, CLASS_IN))
        except ValueError:
            continue  # a name learned from a malformed packet must not stop the others
    header = struct.pack("!HHHHHH", txid, 0, len(encoded), 0, 0, 0)
    return header + b"".join(encoded)


def _read_name(msg: bytes, offset: int) -> tuple[Name, int]:
    labels: list[str] = []
    resume: Optional[int] = None
    hops = 0
    while True:
        if offset >= len(msg):
            raise ValueError("truncated name")
        length = msg[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(msg):
                raise ValueError("truncated pointer")
            if resume is None:
                resume = offset + 2
            hops += 1
            if hops > _MAX_POINTER_HOPS:
                raise ValueError("compression loop")
            offset = ((length & 0x3F) << 8) | msg[offset + 1]
            continue
        if length & 0xC0:
            raise ValueError("unsupported label type")
        offset += 1
        if length == 0:
            return tuple(labels), (resume if resume is not None else offset)
        if offset + length > len(msg):
            raise ValueError("truncated label")
        labels.append(msg[offset : offset + length].decode("utf-8", "replace"))
        offset += length


def _parse_rdata(msg: bytes, rtype: int, start: int, length: int) -> object:
    if rtype == TYPE_A:
        return socket.inet_ntoa(msg[start : start + 4]) if length == 4 else None
    if rtype == TYPE_PTR:
        return _read_name(msg, start)[0]
    if rtype == TYPE_SRV:
        if length < 7:
            return None
        priority, weight, port = struct.unpack_from("!HHH", msg, start)
        return (priority, weight, port, _read_name(msg, start + 6)[0])
    if rtype == TYPE_TXT:
        entries: list[bytes] = []
        offset, end = start, start + length
        while offset < end:
            size = msg[offset]
            # A length overrunning the record must not read into the next one.
            entries.append(msg[offset + 1 : min(offset + 1 + size, end)])
            offset += 1 + size
        return tuple(entries)
    return None


def parse_message(msg: bytes) -> list[Record]:
    """A malformed tail is dropped, not raised: one bad responder must not hide the others."""
    if len(msg) < 12:
        return []
    _txid, flags, questions, answers, authorities, additionals = struct.unpack_from("!HHHHHH", msg)
    if not flags & 0x8000:
        return []
    records: list[Record] = []
    offset = 12
    try:
        for _ in range(questions):
            _name, offset = _read_name(msg, offset)
            offset += 4
        for _ in range(answers + authorities + additionals):
            name, offset = _read_name(msg, offset)
            rtype, rclass, _ttl, length = struct.unpack_from("!HHIH", msg, offset)
            offset += 10
            start, offset = offset, offset + length
            if offset > len(msg):
                break
            if rclass & _CLASS_MASK != CLASS_IN:
                continue
            data = _parse_rdata(msg, rtype, start, length)
            if data is not None:
                records.append(Record(name, rtype, data))
    except (ValueError, struct.error, IndexError):
        pass
    return records


def _txt_dict(entries: tuple[bytes, ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    for entry in entries:
        if not entry:
            continue
        key, _sep, value = entry.decode("utf-8", "replace").partition("=")
        found.setdefault(key, value)
    return found


class _Collector:
    def __init__(self) -> None:
        self.records: list[Record] = []
        self.source: dict[Name, str] = {}

    def add(self, payload: bytes, source_ip: str) -> None:
        for record in parse_message(payload):
            self.records.append(record)
            self.source.setdefault(_key(record.name), source_ip)

    def find(self, name: Name, rtype: int) -> list[Record]:
        wanted = _key(name)
        return [r for r in self.records if r.rtype == rtype and _key(r.name) == wanted]

    def instances(self, service_type: str) -> list[Name]:
        seen: dict[Name, Name] = {}
        for record in self.find(_labels(f"{service_type}.local"), TYPE_PTR):
            seen.setdefault(_key(record.data), record.data)  # type: ignore[arg-type]
        return list(seen.values())

    def enumerated_types(self) -> set[str]:
        return {
            normalize_service_type(_display(r.data))  # type: ignore[arg-type]
            for r in self.find(_labels(SERVICE_ENUMERATION), TYPE_PTR)
        }

    def missing(self, service_types: Iterable[str]) -> list[tuple[Name, int]]:
        asked: list[tuple[Name, int]] = []
        for service_type in service_types:
            for instance in self.instances(service_type):
                srv = self.find(instance, TYPE_SRV)
                if not srv:
                    asked += [(instance, TYPE_SRV), (instance, TYPE_TXT)]
                    continue
                host = srv[0].data[3]  # type: ignore[index]
                if not self.find(host, TYPE_A):
                    asked.append((host, TYPE_A))
        return asked

    def services(self, service_types: Iterable[str]) -> list[MdnsService]:
        resolved: list[MdnsService] = []
        for service_type in service_types:
            for instance in self.instances(service_type):
                service = MdnsService(instance=_display(instance), service_type=service_type)
                srv = self.find(instance, TYPE_SRV)
                if srv:
                    _priority, _weight, service.port, host = srv[0].data  # type: ignore[misc]
                    service.host = _display(host)
                    service.addresses = sorted({r.data for r in self.find(host, TYPE_A)})  # type: ignore[misc]
                txt = self.find(instance, TYPE_TXT)
                if txt:
                    service.txt = _txt_dict(txt[0].data)  # type: ignore[arg-type]
                if not service.addresses:
                    fallback = self.source.get(_key(instance))
                    if fallback:
                        service.addresses = [fallback]
                resolved.append(service)
        return resolved


def _unicast_socket(interface_ip: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.bind((interface_ip, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    except OSError:
        sock.close()
        raise
    return sock


def _multicast_socket(interface_ip: str) -> Optional[socket.socket]:
    """None if 5353 is held; avahi builds without SO_REUSEPORT share it with SO_REUSEADDR alone."""
    reuse_options = [(socket.SO_REUSEADDR,)]
    if hasattr(socket, "SO_REUSEPORT"):
        reuse_options.insert(0, (socket.SO_REUSEADDR, socket.SO_REUSEPORT))
    for options in reuse_options:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            for option in options:
                sock.setsockopt(socket.SOL_SOCKET, option, 1)
            sock.bind(("", MDNS_PORT))
            membership = socket.inet_aton(MDNS_GROUP) + socket.inet_aton(interface_ip)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip)
            )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setsockopt(socket.IPPROTO_IP, _IP_PKTINFO, 1)
            return sock
        except OSError:
            sock.close()
    return None


def _arrival_ifindex(ancillary: list[tuple[int, int, bytes]]) -> Optional[int]:
    for level, kind, data in ancillary:
        if level == socket.IPPROTO_IP and kind == _IP_PKTINFO and len(data) >= 4:
            return struct.unpack_from("I", data)[0]
    return None


def browse(
    service_types: Iterable[str],
    *,
    interface_name: str,
    interface_ip: str,
    timeout: float = 2.5,
    enumerate_types: bool = False,
    destination: tuple[str, int] = (MDNS_GROUP, MDNS_PORT),
    multicast: bool = True,
) -> BrowseResult:
    """``destination`` and ``multicast`` exist so tests can target a local responder."""
    wanted = {normalize_service_type(t) for t in service_types if t and t.strip()}
    if not wanted and not enumerate_types:
        return BrowseResult([], set())

    collector = _Collector()
    sockets: list[socket.socket] = [_unicast_socket(interface_ip)]
    group_socket = _multicast_socket(interface_ip) if multicast else None
    if group_socket is not None:
        sockets.append(group_socket)
    try:
        ifindex = socket.if_nametoindex(interface_name)
    except OSError:
        ifindex = None

    def send(questions: list[tuple[Name, int]]) -> None:
        for start in range(0, len(questions), 12):
            payload = build_query(questions[start : start + 12], txid=random.randint(1, 0xFFFF))
            for sock in sockets:
                try:
                    sock.sendto(payload, destination)
                except OSError:
                    continue

    def initial_questions() -> list[tuple[Name, int]]:
        questions = [(_labels(f"{t}.local"), TYPE_PTR) for t in sorted(wanted)]
        if enumerate_types:
            questions.append((_labels(SERVICE_ENUMERATION), TYPE_PTR))
        return questions

    def all_types() -> set[str]:
        return wanted | (collector.enumerated_types() if enumerate_types else set())

    deadline = time.monotonic() + timeout
    checkpoints = [time.monotonic() + timeout * 0.3, time.monotonic() + timeout * 0.6]
    send(initial_questions())
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if checkpoints and now >= checkpoints[0]:
                checkpoints.pop(0)
                discovered = all_types() - wanted
                follow_up = initial_questions()
                follow_up += [(_labels(f"{t}.local"), TYPE_PTR) for t in sorted(discovered)]
                follow_up += collector.missing(all_types())
                send(follow_up)
            wait = min(deadline, checkpoints[0] if checkpoints else deadline) - now
            readable, _w, _x = select.select(sockets, [], [], max(wait, 0.01))
            for sock in readable:
                try:
                    if sock is group_socket:
                        payload, ancillary, _flags, source = sock.recvmsg(9000, 256)
                        arrived = _arrival_ifindex(ancillary)
                        if ifindex is not None and arrived is not None and arrived != ifindex:
                            continue
                    else:
                        payload, source = sock.recvfrom(9000)
                except OSError:
                    continue
                collector.add(payload, source[0])
    finally:
        for sock in sockets:
            sock.close()

    types = all_types()
    return BrowseResult(collector.services(sorted(types)), collector.enumerated_types())
