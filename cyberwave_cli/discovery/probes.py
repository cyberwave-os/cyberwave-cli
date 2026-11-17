from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone


def numeric_target(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    if (
        address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or int(address) == 0xFFFFFFFF
    ):
        raise ValueError("Select a numeric unicast IPv4 address")
    return str(address)


def normalize_fingerprint(value: str | None) -> str | None:
    if not value:
        return None
    fingerprint = value.replace(":", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Expected a SHA-256 certificate fingerprint")
    return fingerprint


@dataclass
class Verification:
    target: str
    port: int
    observed_at: str
    service_responded: bool = False
    tls_server_authenticated: bool = False
    device_credential_matched: bool = False
    local_address: tuple | None = None
    interface: str | None = None
    error: str | None = None
    identity: str = "unverified"


def verify_endpoint(
    target: str,
    port: int,
    *,
    interface: str | None = None,
    tls_name: str | None = None,
    ca_file: str | None = None,
    certificate_sha256: str | None = None,
) -> Verification:
    target = numeric_target(target)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Select one service port between 1 and 65535")
    fingerprint = normalize_fingerprint(certificate_sha256)
    if (fingerprint or ca_file) and not tls_name:
        raise ValueError("TLS trust and fingerprint checks require an expected TLS server name")
    result = Verification(target, port, datetime.now(timezone.utc).isoformat(), interface=interface)
    deadline = time.monotonic() + 5.0
    sock = None

    def remaining() -> float:
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("five-second verification deadline exceeded")
        return seconds

    try:
        context = ssl.create_default_context(cafile=ca_file) if tls_name else None
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if interface:
            try:
                index = socket.if_nametoindex(interface)
                if sys.platform.startswith("linux") and hasattr(socket, "SO_BINDTODEVICE"):
                    sock.setsockopt(
                        socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b"\0"
                    )
                elif sys.platform == "darwin" and hasattr(socket, "IP_BOUND_IF"):
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_BOUND_IF, index)
                else:
                    raise OSError("interface enforcement is unsupported on this platform")
            except (OSError, ValueError) as exc:
                result.error = f"Verification skipped: cannot enforce interface: {exc}"
                return result
        sock.settimeout(remaining())
        sock.connect((target, port))
        remaining()
        result.service_responded = True
        result.local_address = sock.getsockname()
        if context:
            sock.settimeout(remaining())
            sock = context.wrap_socket(
                sock, server_hostname=tls_name, do_handshake_on_connect=False
            )
            sock.settimeout(remaining())
            sock.do_handshake()
            remaining()
            result.tls_server_authenticated = True
            result.identity = "expected TLS server authenticated; unique physical device unverified"
            if fingerprint:
                actual = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()
                if not hmac.compare_digest(actual, fingerprint):
                    result.identity = "expected device credential mismatch"
                    result.error = "Certificate SHA-256 fingerprint mismatch"
                else:
                    result.device_credential_matched = True
                    result.identity = (
                        "expected device certificate SHA-256 matched; "
                        "a shared certificate does not identify a unique device"
                    )
    except (OSError, ValueError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    except KeyboardInterrupt:
        result.error = "Verification cancelled"
    finally:
        if sock is not None:
            sock.close()
    return result


def endpoint_session_config(zenoh, endpoints: list[str]):
    if not endpoints:
        raise ValueError("No configured Zenoh endpoint; live session skipped")
    # Endpoint overrides can bypass these transport limits.
    for endpoint in endpoints:
        if not re.fullmatch(r"tcp/(?:[A-Za-z0-9.-]+|\[[0-9a-fA-F:]+\]):[0-9]+", endpoint):
            raise ValueError("Only explicit TCP Zenoh endpoints without overrides are supported")
    cfg = zenoh.Config()
    values = {
        "mode": "client",
        "connect/endpoints": endpoints,
        "connect/timeout_ms": 5000,
        "connect/exit_on_failure": True,
        "transport/unicast/open_timeout": 5000,
        "listen/endpoints": [],
        "transport/shared_memory/enabled": False,
        "scouting/multicast/enabled": False,
        "scouting/multicast/listen": False,
        "scouting/multicast/autoconnect": [],
        "scouting/gossip/enabled": False,
        "scouting/gossip/autoconnect": [],
        "open/return_conditions/connect_scouted": False,
        "open/return_conditions/declares": False,
    }
    for key, value in values.items():
        cfg.insert_json5(key, json.dumps(value))
    return cfg
