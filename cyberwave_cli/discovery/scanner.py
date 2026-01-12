"""
Network scanner for discovering IP cameras and NVRs.

Supports multiple discovery methods:
- TCP port scanning (RTSP 554, HTTP 80/8080)
- ONVIF WS-Discovery
- UPnP/SSDP
"""

import socket
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterator


class DeviceType(Enum):
    """Type of discovered device."""

    CAMERA = "camera"
    NVR = "nvr"
    UNKNOWN = "unknown"


@dataclass
class DiscoveredDevice:
    """A discovered network device."""

    ip: str
    port: int
    device_type: DeviceType = DeviceType.UNKNOWN
    protocol: str = ""  # rtsp, http, onvif
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    mac: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        if not self.url:
            self.url = self._build_url()

    def _build_url(self) -> str:
        if self.protocol == "rtsp":
            return f"rtsp://{self.ip}:{self.port}/stream"
        elif self.protocol in ("http", "onvif"):
            return f"http://{self.ip}:{self.port}/"
        return f"{self.ip}:{self.port}"

    @property
    def display_name(self) -> str:
        parts = []
        if self.manufacturer:
            parts.append(self.manufacturer)
        if self.model:
            parts.append(self.model)
        if self.name:
            parts.append(f"({self.name})")
        return " ".join(parts) if parts else f"{self.device_type.value}@{self.ip}"


class NetworkScanner:
    """
    Scans the local network for IP cameras and NVRs.

    Usage:
        scanner = NetworkScanner()
        for device in scanner.scan():
            print(device.ip, device.url)
    """

    # Common ports for IP cameras and NVRs
    CAMERA_PORTS = {
        554: ("rtsp", DeviceType.CAMERA),   # RTSP standard
        8554: ("rtsp", DeviceType.CAMERA),  # RTSP alt
        80: ("http", DeviceType.UNKNOWN),   # HTTP
        8080: ("http", DeviceType.UNKNOWN), # HTTP alt
        443: ("https", DeviceType.UNKNOWN), # HTTPS
        37777: ("http", DeviceType.NVR),    # Dahua NVR
        34567: ("http", DeviceType.NVR),    # XMEye NVR
        9000: ("http", DeviceType.NVR),     # Hikvision
    }

    # ONVIF WS-Discovery
    ONVIF_MULTICAST = ("239.255.255.250", 3702)
    ONVIF_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
    xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
    xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
    <soap:Header>
        <wsa:Action xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">
            http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe
        </wsa:Action>
        <wsa:MessageID xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">
            uuid:NetworkScanner
        </wsa:MessageID>
        <wsa:To xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">
            urn:schemas-xmlsoap-org:ws:2005:04:discovery
        </wsa:To>
    </soap:Header>
    <soap:Body>
        <d:Probe><d:Types>tds:Device</d:Types></d:Probe>
    </soap:Body>
</soap:Envelope>"""

    # UPnP/SSDP
    SSDP_MULTICAST = ("239.255.255.250", 1900)
    SSDP_SEARCH = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "MX: 2\r\n"
        "ST: ssdp:all\r\n"
        "\r\n"
    )

    def __init__(
        self,
        subnet: str | None = None,
        timeout: float = 1.0,
        max_workers: int = 50,
    ):
        """
        Initialize the scanner.

        Args:
            subnet: Subnet to scan (e.g., "192.168.1"). Auto-detected if None.
            timeout: Connection timeout in seconds.
            max_workers: Max concurrent scanning threads.
        """
        self.subnet = subnet or self._detect_subnet()
        self.timeout = timeout
        self.max_workers = max_workers
        self._discovered: dict[str, DiscoveredDevice] = {}
        self._lock = threading.Lock()

    def _detect_subnet(self) -> str:
        """Detect the local subnet."""
        try:
            # Connect to external address to get local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            # Return first 3 octets
            return ".".join(local_ip.split(".")[:3])
        except Exception:
            return "192.168.1"

    def _add_device(self, device: DiscoveredDevice) -> None:
        """Thread-safe device addition."""
        key = f"{device.ip}:{device.port}"
        with self._lock:
            if key not in self._discovered:
                self._discovered[key] = device
            else:
                # Merge info if we found more details
                existing = self._discovered[key]
                if device.manufacturer and not existing.manufacturer:
                    existing.manufacturer = device.manufacturer
                if device.model and not existing.model:
                    existing.model = device.model
                if device.name and not existing.name:
                    existing.name = device.name

    def _check_port(self, ip: str, port: int) -> DiscoveredDevice | None:
        """Check if a port is open on an IP."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((ip, port))
            sock.close()

            if result == 0:
                protocol, device_type = self.CAMERA_PORTS.get(
                    port, ("unknown", DeviceType.UNKNOWN)
                )
                return DiscoveredDevice(
                    ip=ip,
                    port=port,
                    protocol=protocol,
                    device_type=device_type,
                )
        except Exception:
            pass
        return None

    def _scan_ports(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Scan common camera ports on the subnet."""
        targets = [
            (f"{self.subnet}.{i}", port)
            for i in range(1, 255)
            for port in self.CAMERA_PORTS.keys()
        ]
        total = len(targets)
        completed = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._check_port, ip, port): (ip, port)
                for ip, port in targets
            }

            for future in as_completed(futures):
                completed += 1
                if on_progress:
                    on_progress(completed, total)

                device = future.result()
                if device:
                    self._add_device(device)

    def _discover_onvif(self) -> None:
        """Discover ONVIF-compliant devices via WS-Discovery."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(3.0)

            # Send multicast probe
            sock.sendto(
                self.ONVIF_PROBE.encode(),
                self.ONVIF_MULTICAST,
            )

            # Collect responses
            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                    ip = addr[0]

                    # Parse response for device info
                    response = data.decode(errors="ignore")
                    device = DiscoveredDevice(
                        ip=ip,
                        port=80,
                        protocol="onvif",
                        device_type=DeviceType.CAMERA,
                    )

                    # Try to extract manufacturer/model from response
                    if "hikvision" in response.lower():
                        device.manufacturer = "Hikvision"
                    elif "dahua" in response.lower():
                        device.manufacturer = "Dahua"
                    elif "axis" in response.lower():
                        device.manufacturer = "Axis"

                    self._add_device(device)
                except socket.timeout:
                    break
        except Exception:
            pass

    def _discover_upnp(self) -> None:
        """Discover devices via UPnP/SSDP."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(3.0)

            sock.sendto(self.SSDP_SEARCH.encode(), self.SSDP_MULTICAST)

            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                    ip = addr[0]
                    response = data.decode(errors="ignore").lower()

                    # Filter for camera/NVR related devices
                    if any(kw in response for kw in [
                        "camera", "nvr", "ipcam", "video", "rtsp",
                        "hikvision", "dahua", "axis", "onvif",
                    ]):
                        device = DiscoveredDevice(
                            ip=ip,
                            port=80,
                            protocol="http",
                            device_type=DeviceType.CAMERA,
                        )
                        self._add_device(device)
                except socket.timeout:
                    break
        except Exception:
            pass

    def scan(
        self,
        port_scan: bool = True,
        onvif: bool = True,
        upnp: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[DiscoveredDevice]:
        """
        Run network scan.

        Args:
            port_scan: Enable TCP port scanning.
            onvif: Enable ONVIF WS-Discovery.
            upnp: Enable UPnP/SSDP discovery.
            on_progress: Callback for progress (current, total).

        Returns:
            List of discovered devices.
        """
        self._discovered.clear()

        # Run discovery methods in parallel
        threads = []

        if onvif:
            t = threading.Thread(target=self._discover_onvif)
            t.start()
            threads.append(t)

        if upnp:
            t = threading.Thread(target=self._discover_upnp)
            t.start()
            threads.append(t)

        if port_scan:
            self._scan_ports(on_progress)

        # Wait for multicast discovery
        for t in threads:
            t.join(timeout=5.0)

        return list(self._discovered.values())

    def scan_iter(
        self,
        port_scan: bool = True,
        onvif: bool = True,
        upnp: bool = True,
    ) -> Iterator[DiscoveredDevice]:
        """
        Scan and yield devices as they're discovered.

        Useful for streaming results to UI.
        """
        devices = self.scan(port_scan=port_scan, onvif=onvif, upnp=upnp)
        yield from devices
