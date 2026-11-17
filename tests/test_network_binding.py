from __future__ import annotations

import ipaddress
import json
from typing import Any

import pytest

from cyberwave_cli.discovery import hardware, interfaces
from tests._core_module_loader import load_core_module

CAM_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CAM_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
WEBCAM = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
LINK_LOCAL = ipaddress.IPv4Interface("169.254.10.1/16")
SERVICE = "_acme-cam._tcp"
CAM_SPEC = {
    "mdns_services": [SERVICE],
    "mac_prefixes": ["00:11:22"],
    "tcp_ports": [554],
    "bind": {"video_device": "rtsp://{ip}/stream"},
}
USB_PIN = "/dev/v4l/by-id/usb-Some_Webcam-video-index0"
FIXED_URL = "rtsp://10.20.30.3/stream"
FIXED_PARAMS = ["-e", f"CYBERWAVE_METADATA_VIDEO_DEVICE={FIXED_URL}"]


def _drivers(params):
    return {"default": {"docker_image": "example/camera-driver", "params": list(params)}}


def _write_twin(
    config_dir,
    twin_uuid,
    name,
    *,
    spec=None,
    twin_block=None,
    metadata=None,
    twin_params=None,
    asset_params=None,
):
    asset: dict[str, Any] = {"universal_schema": {"sensors": [{"type": "camera", "id": "cam0"}]}}
    asset_metadata: dict[str, Any] = {}
    if spec is not None:
        asset_metadata["network_discovery"] = spec
    if asset_params is not None:
        asset_metadata["drivers"] = _drivers(asset_params)
    if asset_metadata:
        asset["metadata"] = asset_metadata
    twin_metadata = dict(metadata or {})
    if twin_block is not None:
        twin_metadata["network_discovery"] = twin_block
    if twin_params is not None:
        twin_metadata["drivers"] = _drivers(twin_params)
    payload = {"uuid": twin_uuid, "name": name, "asset": asset, "metadata": twin_metadata}
    (config_dir / f"{twin_uuid}.json").write_text(json.dumps(payload))


def _port(*addresses, carrier=True):
    return interfaces.WiredInterface("eth0", "02:aa:bb:cc:dd:01", carrier, tuple(addresses))


@pytest.fixture
def install(monkeypatch, tmp_path):
    """Core with a temp config dir, a linked eth0, and recorded pins, prompts and port changes."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")
    state: dict[str, Any] = {
        "pins": {},
        "printed": [],
        "prompts": [],
        "answers": [],
        "discovered": [],
        "configured": [],
    }

    def persist(pins):
        for twin_uuid, metadata in pins.items():
            state["pins"].setdefault(twin_uuid, {}).update(metadata)
            core._write_local_twin_pin(twin_uuid, metadata)
        return set()

    def prompt(question, *args, **kwargs):
        state["prompts"].append((question, kwargs.get("choices")))
        return state["answers"].pop(0)

    monkeypatch.setattr(core, "_persist_twin_pins", persist)
    monkeypatch.setattr(
        core.console, "print", lambda *a, **k: state["printed"].append(" ".join(map(str, a)))
    )
    monkeypatch.setattr(core.Prompt, "ask", prompt)
    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: True)
    monkeypatch.setattr(core.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        interfaces, "read_ipv4", lambda: interfaces.IPv4Snapshot({"eth0": [LINK_LOCAL]})
    )
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda **kw: [_port(LINK_LOCAL)])
    monkeypatch.setattr(interfaces, "route_to", lambda ip: interfaces.RouteObservation("eth0"))
    monkeypatch.setattr(hardware, "cached_devices", lambda: [])

    def forbidden(*a, **k):
        pytest.fail("host mutation or implicit device traffic")

    import socket

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    def set_devices(*batches):
        monkeypatch.setattr(hardware, "cached_devices", lambda: batches[0])

    state["core"], state["dir"], state["devices"] = core, tmp_path, set_devices
    return state


PLACEHOLDER = "CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://192.168.X.X/stream"
OTHER_PARAM = "OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp"


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("rtsp://10.20.30.3/stream", None),
        ("rtsp://cam-1.local:554/stream", None),
        ("rtsp://[fe80::1]/stream", None),
        ("10.20.30.3", None),
        ("rtsp://192.168.X.X/stream", "192.168.X.X is a placeholder, not an address"),
        ("rtsp://<ip>/stream", "<ip> is a placeholder, not an address"),
        ("rtsp://192.168.1/stream", "192.168.1 is not a valid IP address"),
        ("rtsp:///stream", "it names no host"),
        ("rtsp://cam:99999/stream", "it is not a valid URL"),
        ("", "it is empty"),
    ],
)
def test_source_problem_tells_a_usable_address_from_a_placeholder(value, problem):
    assert hardware.source_problem(value) == problem


LEFT_MAC, RIGHT_MAC = "00:11:22:00:00:03", "00:11:22:00:00:04"


class _FakeCam:
    def __init__(self, idx, card, path):
        self.index, self.card, self.primary_path, self.paths, self.serial = (
            idx,
            card,
            path,
            [path],
            None,
        )

    def to_dict(self):
        return {
            "index": self.index,
            "card": self.card,
            "primary_path": self.primary_path,
            "serial": None,
        }


def _one_usb_camera(monkeypatch):
    import cyberwave_cli.device_utils as device_utils

    monkeypatch.setattr(
        device_utils, "discover_usb_cameras", lambda: [_FakeCam(0, "USB Cam", "/dev/video0")]
    )
    monkeypatch.setattr(device_utils, "camera_likelihood_score", lambda _cam: 80)


def test_a_network_camera_twin_is_never_paired_with_a_usb_camera(install, monkeypatch):
    core = install["core"]
    _write_twin(install["dir"], WEBCAM, "Webcam")
    _write_twin(
        install["dir"], CAM_A, "Network cam", spec=CAM_SPEC, metadata={"video_device": USB_PIN}
    )
    _one_usb_camera(monkeypatch)
    install["devices"]([])

    core._detect_and_bind_network_devices()
    core._detect_and_select_cameras()
    core._record_network_twin_sources()

    mapping = json.loads((install["dir"] / "cameras.json").read_text())["twin_to_device"]
    assert mapping == {WEBCAM: 0}
    assert not any("Network cam" in str(question) for question, _choices in install["prompts"])


def _write_edge_json(directory, cameras):
    (directory / "edge.json").write_text(
        json.dumps({"uuid": "edge-1", "metadata": {"cameras": cameras}})
    )


def _edge_json_cameras(directory):
    return json.loads((directory / "edge.json").read_text())["metadata"].get("cameras")


def test_edge_json_already_carrying_the_url_is_not_uploaded_again(install, monkeypatch):
    core = install["core"]
    url = "rtsp://169.254.79.239/stream"
    _write_twin(install["dir"], CAM_A, "Network cam", spec=CAM_SPEC, metadata={"video_device": url})
    _write_edge_json(install["dir"], {"devices": [], "twin_to_device": {CAM_A: url}})

    def no_upload(_cameras):
        raise AssertionError("an edge.json that already matches must not be uploaded")

    monkeypatch.setattr(core, "_upload_cameras_to_edge_metadata", no_upload)

    core._record_network_twin_sources()

    assert _edge_json_cameras(install["dir"])["twin_to_device"] == {CAM_A: url}


def test_an_edge_json_without_cameras_is_left_to_cameras_json(install, monkeypatch):
    core = install["core"]
    url = "rtsp://169.254.79.239/stream"
    _write_twin(install["dir"], CAM_A, "Network cam", spec=CAM_SPEC, metadata={"video_device": url})
    (install["dir"] / "edge.json").write_text(json.dumps({"uuid": "edge-1", "metadata": {}}))

    def no_upload(_cameras):
        raise AssertionError("nothing to carry when edge.json holds no cameras")

    monkeypatch.setattr(core, "_upload_cameras_to_edge_metadata", no_upload)

    core._record_network_twin_sources()

    mapping = json.loads((install["dir"] / "cameras.json").read_text())["twin_to_device"]
    assert mapping == {CAM_A: url}
    assert _edge_json_cameras(install["dir"]) is None


def test_only_network_camera_twins_skip_the_usb_step_entirely(install, monkeypatch):
    import cyberwave_cli.device_utils as device_utils

    core = install["core"]
    _write_twin(
        install["dir"],
        CAM_A,
        "Network cam",
        spec=CAM_SPEC,
        metadata={"video_device": "rtsp://169.254.79.239/stream"},
    )

    def no_usb_scan():
        raise AssertionError("USB cameras must not be scanned for a network-only setup")

    monkeypatch.setattr(device_utils, "discover_usb_cameras", no_usb_scan)

    core._detect_and_select_cameras()
    core._record_network_twin_sources()

    data = json.loads((install["dir"] / "cameras.json").read_text())
    assert data == {"devices": [], "twin_to_device": {CAM_A: "rtsp://169.254.79.239/stream"}}


GO2_UUID = "88f03abd-0000-0000-0000-000000000001"
