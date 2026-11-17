import importlib
import ipaddress
import json
import subprocess
from collections import Counter

import click
import pytest
from click.testing import CliRunner

from cyberwave_cli.discovery import hardware, interfaces, probes
from tests.test_network_binding import CAM_A as ISOLATED_CAMERA
from tests.test_network_binding import CAM_B as WIFI_CAMERA
from tests.test_network_binding import CAM_SPEC, _write_edge_json, _write_twin
from tests.test_network_binding import WEBCAM as ETHERNET_CAMERA
from tests.test_network_binding import install as _install

install = _install
edge_module = importlib.import_module("cyberwave_cli.commands.edge")
read_ipv4 = interfaces.read_ipv4
route_to = interfaces.route_to
wired_interfaces = interfaces.wired_interfaces


@pytest.mark.parametrize("router_interface", ["wlan0", "eth1"])
@pytest.mark.parametrize(
    "isolated_ip,isolated_host_address,ethernet_endpoint_known",
    [
        pytest.param(None, None, False, id="router-dhcp-endpoint-not-in-metadata"),
        pytest.param(None, None, True, id="bare-switch-no-device-address"),
        pytest.param("192.168.70.10", None, True, id="known-ip-missing-host-address"),
        pytest.param("192.168.50.161", None, True, id="unaddressed-link-overlaps-wifi"),
        pytest.param("192.168.50.101", "192.168.50.2/24", True, id="separate-lans-duplicate-ip"),
    ],
)
def test_mixed_camera_install_preserves_networks_and_reports_setup_needs(
    install,
    monkeypatch,
    router_interface,
    isolated_ip,
    isolated_host_address,
    ethernet_endpoint_known,
):
    core, directory = install["core"], install["dir"]
    endpoints = {WIFI_CAMERA: "rtsp://192.168.50.101/wifi"}
    if ethernet_endpoint_known:
        endpoints[ETHERNET_CAMERA] = "rtsp://192.168.50.102/ethernet"
    if isolated_ip:
        endpoints[ISOLATED_CAMERA] = f"rtsp://{isolated_ip}/isolated"
    for twin_uuid, name in (
        (ISOLATED_CAMERA, "Isolated camera"),
        (WIFI_CAMERA, "Wi-Fi camera"),
        (ETHERNET_CAMERA, "Router Ethernet camera"),
    ):
        metadata = {"video_device": endpoints[twin_uuid]} if twin_uuid in endpoints else {}
        _write_twin(directory, twin_uuid, name, spec=CAM_SPEC, metadata=metadata)
    (directory / "environment.json").write_text(
        json.dumps({"twin_uuids": [ISOLATED_CAMERA, WIFI_CAMERA, ETHERNET_CAMERA]})
    )
    twin_files = {p: p.read_bytes() for p in directory.glob("*.json")}

    unrelated_twin = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    cameras = {
        "devices": [{"index": 0, "card": "Existing USB camera"}],
        "selected_device": 0,
        "twin_to_device": {unrelated_twin: 0, WIFI_CAMERA: endpoints[WIFI_CAMERA]},
    }
    (directory / "cameras.json").write_text(json.dumps(cameras))
    _write_edge_json(directory, cameras)

    addresses = {"eth0": [], "wlan0": ["192.168.50.20/24"], "eth1": ["192.168.50.21/24"]}
    if isolated_host_address:
        addresses["eth0"] = [isolated_host_address]
    host = directory / "simulated-host"
    sys_net = host / "sys/class/net"
    for index, name in enumerate(addresses):
        port = sys_net / name
        (port / "device").mkdir(parents=True)
        for filename, value in {
            "type": "1",
            "carrier": "1",
            "address": f"02:00:00:00:00:0{index}",
        }.items():
            (port / filename).write_text(value)
        if name == "wlan0":
            (port / "wireless").mkdir()
    profiles = host / "NetworkManager/system-connections"
    profiles.mkdir(parents=True)
    for name in ("wlan0", "eth1"):
        (profiles / f"{name}.nmconnection").write_text(
            f"[connection]\ninterface-name={name}\n[ipv4]\nmethod=auto\n"
        )
    (host / "addresses.json").write_text(json.dumps(addresses))
    (host / "routes.json").write_text(json.dumps({"192.168.50.0/24": router_interface}))
    before_host = {p: p.read_bytes() for p in host.rglob("*") if p.is_file()}

    calls = []

    def run(args, **kwargs):
        calls.append(tuple(args))
        assert kwargs["timeout"] == 5 and kwargs["check"]
        if args == ["ip", "-j", "-4", "addr", "show"]:
            response = [
                {
                    "ifname": name,
                    "addr_info": [
                        {"family": "inet", "local": str(a.ip), "prefixlen": a.network.prefixlen}
                        for a in map(ipaddress.IPv4Interface, values)
                    ],
                }
                for name, values in addresses.items()
            ]
        else:
            assert args[:5] == ["ip", "-j", "-4", "route", "get"] and len(args) == 6
            assert args[5] in {hardware.ip_from_value(url) for url in endpoints.values()}
            if args[5] == "192.168.70.10":
                raise subprocess.CalledProcessError(
                    2, args, stderr="RTNETLINK answers: Network is unreachable\n"
                )
            response = [
                {"dev": router_interface, "prefsrc": addresses[router_interface][0].split("/")[0]}
            ]
        return subprocess.CompletedProcess(args, 0, json.dumps(response))

    def forbidden(*args, **kwargs):
        pytest.fail("passive device setup attempted discovery, verification or USB reassignment")

    monkeypatch.setattr(core.subprocess, "run", run)
    monkeypatch.setattr(core.subprocess, "Popen", forbidden)
    monkeypatch.setattr(interfaces, "read_ipv4", lambda: read_ipv4(run))
    monkeypatch.setattr(interfaces, "SYS_CLASS_NET", sys_net)
    monkeypatch.setattr(interfaces, "route_to", lambda ip: route_to(ip, run))
    monkeypatch.setattr(
        interfaces, "wired_interfaces", lambda **kw: wired_interfaces(sys_net, **kw)
    )
    cached = [
        hardware.NetworkDevice("192.168.50.101", "wlan0", "00:11:22:00:00:01"),
        hardware.NetworkDevice("192.168.50.102", "eth1", "00:11:22:00:00:02"),
    ]
    if isolated_host_address:
        cached.append(hardware.NetworkDevice(isolated_ip, "eth0", "00:11:22:00:00:03"))
    monkeypatch.setattr(hardware, "cached_devices", lambda: cached)
    monkeypatch.setattr(probes, "verify_endpoint", forbidden)
    monkeypatch.setattr(
        importlib.import_module("cyberwave_cli.device_utils"), "discover_usb_cameras", forbidden
    )
    monkeypatch.setattr(importlib.import_module("cyberwave_cli.macos"), "is_macos", lambda: False)
    for console in (core.console, edge_module.console):
        monkeypatch.setattr(
            console, "print", lambda *args, **kw: click.echo(" ".join(map(str, args)))
        )
    uploads = []
    monkeypatch.setattr(
        core, "_upload_cameras_to_edge_metadata", lambda data: uploads.append(data) or False
    )

    result = CliRunner().invoke(edge_module.edge, ["install", "--reconfigure-camera", "--yes"])

    assert result.exit_code == 0, (result.output, result.exception)
    output = result.output
    expected_cameras = {**cameras, "twin_to_device": {**cameras["twin_to_device"], **endpoints}}
    assert json.loads((directory / "cameras.json").read_text()) == expected_cameras
    assert (
        json.loads((directory / "edge.json").read_text())["metadata"]["cameras"] == expected_cameras
    )
    assert uploads == ([expected_cameras] if expected_cameras != cameras else [])
    assert {p: p.read_bytes() for p in twin_files} == twin_files
    assert {p: p.read_bytes() for p in host.rglob("*") if p.is_file()} == before_host
    assert install["pins"] == {} and install["prompts"] == []
    targets = {hardware.ip_from_value(url) for url in endpoints.values()}
    assert Counter(calls) == Counter(
        [("ip", "-j", "-4", "addr", "show")]
        + [("ip", "-j", "-4", "route", "get", ip) for ip in targets]
    )
    for url in endpoints.values():
        assert url in output
    assert "Matching host prefix: wlan0 192.168.50.20/24" in output
    assert "Matching host prefix: eth1 192.168.50.21/24" in output
    assert f"Kernel-selected route: {router_interface}, source" in output
    assert "this may be an intentional same-LAN or managed configuration" in output
    assert output.count("Intended device: not verified") == len(endpoints)
    assert "Host network configuration was not changed" in output
    if not ethernet_endpoint_known:
        assert "Router Ethernet camera: endpoint unknown. Broad discovery was not run." in output
        assert "Cached neighbor: eth1 192.168.50.102" in output
        assert ETHERNET_CAMERA not in expected_cameras["twin_to_device"]

    if isolated_ip is None:
        assert "Isolated camera: endpoint unknown. Broad discovery was not run." in output
        assert "explicitly provided DHCP service or a supported static address" in output
        assert "changing the host address alone cannot provide it" in output
        assert "eth0: cable connected, no IPv4 address; device addressing unknown" in output
        assert ISOLATED_CAMERA not in expected_cameras["twin_to_device"]
    elif isolated_ip == "192.168.70.10":
        assert "Matching host prefix: none" in output
        assert "Kernel-selected route: none returned" in output
        assert (
            "Confirm the device mask and physical link before configuring "
            "host addressing outside the CLI" in output
        )
    elif isolated_host_address is None:
        assert "If this device is on the separate eth0 cable" in output
        assert "overlaps the network on eth1, wlan0" in output
    else:
        assert "Possible overlap: eth0 192.168.50.0/24 and wlan0 192.168.50.0/24" in output
        assert (
            "multiple cached interfaces; device association is ambiguous. "
            "Existing binding preserved" in output
        )
