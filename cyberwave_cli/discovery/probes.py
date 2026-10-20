from __future__ import annotations

import socket
from typing import Optional
from urllib.parse import urlsplit

RTSP_DEFAULT_PORT = 554
RTSP_PATH_PRESENT = frozenset({200, 401, 403})


def tcp_port_open(ip: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def rtsp_status(url: str, timeout: float = 3.0) -> Optional[int]:
    """RTSP status of a DESCRIBE (OPTIONS ignores the path); credentials never go on the wire."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "rtsp" or not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    target = parts._replace(netloc=f"{host}:{port}" if port else host).geturl()
    request = (
        f"DESCRIBE {target} RTSP/1.0\r\n"
        "CSeq: 1\r\n"
        "Accept: application/sdp\r\n"
        "User-Agent: cyberwave-cli\r\n"
        "\r\n"
    ).encode()
    try:
        with socket.create_connection(
            (parts.hostname, port or RTSP_DEFAULT_PORT), timeout=timeout
        ) as sock:
            sock.sendall(request)
            status_line = sock.recv(256).split(b"\r\n", 1)[0].decode("latin-1")
    except OSError:
        return None
    fields = status_line.split()
    if len(fields) >= 2 and fields[0].startswith("RTSP/") and fields[1].isdigit():
        return int(fields[1])
    return None
