"""The ``edge install`` binding step; discovery itself is faked (see test_network_discovery.py)."""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

import pytest

from cyberwave_cli.discovery import hardware, interfaces, linkconfig, mdns
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


def _cam(ip, mac, *, reachable=True, announced=True):
    services = {}
    if announced:
        services[SERVICE] = mdns.MdnsService(
            f"Cam {mac[-5:]}.{SERVICE}.local", SERVICE, "cam.local", 9000, [ip]
        )
    return hardware.NetworkDevice(
        ip=ip,
        interface="eth0",
        mac=mac if reachable else "",
        vendor="ACME Corp",
        reachable=reachable,
        services=services,
    )


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
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda: [_port(LINK_LOCAL)])
    monkeypatch.setattr(hardware, "verify_binding", lambda spec, device, values: [])
    monkeypatch.setattr(linkconfig, "networkmanager_running", lambda: True)
    monkeypatch.setattr(linkconfig, "adopt_dhcp", lambda name: None)
    monkeypatch.setattr(
        linkconfig,
        "configure",
        lambda name, link_local=False, addresses=(): (
            state["configured"].append((name, link_local, [str(a) for a in addresses]))
            or f"cyberwave-{name}"
        ),
    )
    monkeypatch.setattr(
        linkconfig, "wait_for_ipv4", lambda name, **kw: [kw.get("want") or LINK_LOCAL]
    )

    def set_devices(*batches):
        queue = list(batches)

        def discover(ports, **kwargs):
            state["discovered"].append(([p.name for p in ports], kwargs))
            return queue.pop(0) if len(queue) > 1 else queue[0]

        monkeypatch.setattr(hardware, "discover", discover)

    state["core"], state["dir"], state["devices"] = core, tmp_path, set_devices
    return state


def test_the_only_matching_device_is_bound_with_its_ip_and_mac(install):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC)
    install["devices"]([_cam("169.254.79.239", "00:11:22:33:44:55")])

    core._detect_and_bind_network_devices()

    assert install["pins"] == {
        CAM_A: {"video_device": "rtsp://169.254.79.239/stream", "mac_address": "00:11:22:33:44:55"}
    }
    [(ports, kwargs)] = install["discovered"]
    assert ports == ["eth0"]
    assert kwargs["mdns_services"] == [SERVICE] and kwargs["arp_sweep"] is False
    assert kwargs["targets"] == [] and kwargs["timeout"] == 2.5
    assert install["prompts"] == [] and install["configured"] == []
    cached = json.loads((install["dir"] / f"{CAM_A}.json").read_text())["metadata"]
    assert cached["video_device"] == "rtsp://169.254.79.239/stream"


def test_the_arp_sweep_and_timeout_come_from_the_specs(install):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC)
    _write_twin(
        install["dir"],
        CAM_B,
        "Quiet device",
        spec={
            "methods": ["arp"],
            "mac_prefixes": ["00:aa:bb"],
            "timeout": 6,
            "bind": {"host": "{ip}"},
        },
    )
    install["devices"]([])

    core._detect_and_bind_network_devices()

    kwargs = install["discovered"][0][1]
    assert kwargs["arp_sweep"] is True and kwargs["timeout"] == 6
    assert kwargs["mdns_services"] == [SERVICE]


def test_a_saved_mac_keeps_identical_units_on_their_own_twins(install):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam left", spec=CAM_SPEC)
    _write_twin(
        install["dir"],
        CAM_B,
        "Cam right",
        spec=CAM_SPEC,
        metadata={"mac_address": "00-11-22-00-00-01"},
    )
    install["devices"](
        [_cam("169.254.1.1", "00:11:22:00:00:01"), _cam("169.254.2.2", "00:11:22:00:00:02")]
    )

    core._detect_and_bind_network_devices()

    assert install["pins"][CAM_B]["video_device"] == "rtsp://169.254.1.1/stream"
    assert install["pins"][CAM_A]["video_device"] == "rtsp://169.254.2.2/stream"
    assert install["prompts"] == []


def test_identical_units_without_saved_macs_are_asked_about_once(install):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam left", spec=CAM_SPEC)
    _write_twin(install["dir"], CAM_B, "Cam right", spec=CAM_SPEC)
    install["devices"](
        [_cam("169.254.1.1", "00:11:22:00:00:01"), _cam("169.254.2.2", "00:11:22:00:00:02")]
    )
    install["answers"] = ["2"]

    core._detect_and_bind_network_devices()

    assert len(install["prompts"]) == 1 and install["prompts"][0][1] == ["1", "2", "s"]
    assert install["pins"][CAM_A]["mac_address"] == "00:11:22:00:00:02"
    assert install["pins"][CAM_B]["mac_address"] == "00:11:22:00:00:01"
    assert any("00:11:22:00:00:01" in line for line in install["printed"])


def test_a_device_that_does_not_fit_the_spec_is_never_bound(install):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC, metadata={"video_device": USB_PIN})
    other = hardware.NetworkDevice(ip="169.254.5.5", interface="eth0", mac="02:00:00:00:00:05")
    install["devices"]([other])

    core._detect_and_bind_network_devices()

    assert install["pins"] == {CAM_A: {"video_device": ""}}
    assert any("was not found on the wired network" in line for line in install["printed"])


def test_no_wired_link_clears_a_usb_pin_left_on_a_network_twin(install, monkeypatch):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC, metadata={"video_device": USB_PIN})
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda: [_port(carrier=False)])
    install["devices"]([])

    core._detect_and_bind_network_devices()

    assert install["pins"] == {CAM_A: {"video_device": ""}}
    assert install["discovered"] == []
    assert any("No wired Ethernet port has a link" in line for line in install["printed"])


def test_link_local_is_offered_only_for_twins_whose_spec_asks_for_it(install, monkeypatch):
    core = install["core"]
    ports = [[_port()], [_port(LINK_LOCAL)]]
    monkeypatch.setattr(
        interfaces, "wired_interfaces", lambda: ports.pop(0) if len(ports) > 1 else ports[0]
    )
    _write_twin(install["dir"], CAM_A, "Cam", spec={**CAM_SPEC, "link_local": True})
    install["devices"]([_cam("169.254.79.239", "00:11:22:33:44:55")])

    core._detect_and_bind_network_devices()

    assert install["configured"] == [("eth0", True, [])]
    assert install["pins"][CAM_A]["video_device"] == "rtsp://169.254.79.239/stream"


def test_a_bare_port_is_left_alone_when_no_spec_asks_for_link_local(install, monkeypatch):
    core = install["core"]
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda: [_port()])
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC)
    install["devices"]([])

    core._detect_and_bind_network_devices()

    assert install["configured"] == []
    assert any("no IPv4 address" in line for line in install["printed"])


def test_a_device_announcing_another_subnet_gets_this_host_an_address_there(install):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam", spec={**CAM_SPEC, "subnet_prefix": 16})
    install["devices"](
        [_cam("10.20.30.3", "", reachable=False)],
        [_cam("10.20.30.3", "00:11:22:33:44:55")],
    )

    core._detect_and_bind_network_devices()

    assert install["configured"] == [("eth0", False, ["10.20.255.250/16"])]
    assert len(install["discovered"]) == 2
    assert install["pins"][CAM_A] == {
        "video_device": "rtsp://10.20.30.3/stream",
        "mac_address": "00:11:22:33:44:55",
    }


def test_declining_the_subnet_address_leaves_the_twin_unbound(install, monkeypatch):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC)
    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: False)
    install["devices"]([_cam("10.20.30.3", "", reachable=False)])

    core._detect_and_bind_network_devices()

    assert install["pins"] == {} and install["configured"] == []
    assert any("cannot be reached from this host" in line for line in install["printed"])


def _on_the_camera_subnet(monkeypatch):
    monkeypatch.setattr(
        interfaces, "wired_interfaces", lambda: [_port(ipaddress.IPv4Interface("10.20.30.200/24"))]
    )


def test_a_fixed_source_driver_param_binds_without_searching_or_rewriting(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(
        install["dir"],
        CAM_A,
        "Cam",
        spec=CAM_SPEC,
        twin_params=FIXED_PARAMS,
        metadata={"video_device": USB_PIN},
    )
    install["devices"]([_cam("10.20.30.3", "00:11:22:33:44:55", announced=False)])

    bindings = core._detect_and_bind_network_devices()

    assert install["discovered"][0][1]["targets"] == ["10.20.30.3"]
    assert install["pins"][CAM_A] == {"video_device": "", "mac_address": "00:11:22:33:44:55"}
    assert bindings[CAM_A] == {"video_device": FIXED_URL, "mac_address": "00:11:22:33:44:55"}
    assert install["prompts"] == [] and install["configured"] == []
    assert any("CYBERWAVE_METADATA_VIDEO_DEVICE" in line for line in install["printed"])


def test_the_asset_driver_param_applies_when_the_twin_has_no_drivers(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC, asset_params=FIXED_PARAMS)
    install["devices"]([_cam("10.20.30.3", "00:11:22:33:44:55", announced=False)])

    core._detect_and_bind_network_devices()

    assert install["discovered"][0][1]["targets"] == ["10.20.30.3"]
    assert install["pins"][CAM_A] == {"mac_address": "00:11:22:33:44:55"}


def test_twin_drivers_without_the_param_mean_the_device_is_searched_for(install):
    core = install["core"]
    _write_twin(
        install["dir"], CAM_A, "Cam", spec=CAM_SPEC, asset_params=FIXED_PARAMS, twin_params=[]
    )
    install["devices"]([_cam("169.254.79.239", "00:11:22:33:44:55")])

    core._detect_and_bind_network_devices()

    assert install["discovered"][0][1]["targets"] == []
    assert install["pins"][CAM_A]["video_device"] == "rtsp://169.254.79.239/stream"


def test_a_fixed_ip_outside_every_port_subnet_gets_the_spec_host_address(install, monkeypatch):
    core = install["core"]
    ports = [[_port(LINK_LOCAL)], [_port(LINK_LOCAL, ipaddress.IPv4Interface("10.20.30.200/24"))]]
    monkeypatch.setattr(
        interfaces, "wired_interfaces", lambda: ports.pop(0) if len(ports) > 1 else ports[0]
    )
    _write_twin(
        install["dir"],
        CAM_A,
        "Cam",
        spec=CAM_SPEC,
        twin_block={"host_address": "10.20.30.200/24"},
        twin_params=FIXED_PARAMS,
    )
    install["devices"]([_cam("10.20.30.3", "00:11:22:33:44:55", announced=False)])

    core._detect_and_bind_network_devices()

    assert install["configured"] == [("eth0", False, ["10.20.30.200/24"])]
    assert install["pins"][CAM_A] == {"mac_address": "00:11:22:33:44:55"}


def test_a_host_address_outside_the_device_subnet_is_ignored(install, monkeypatch):
    core = install["core"]
    ports = [[_port(LINK_LOCAL)], [_port(LINK_LOCAL, ipaddress.IPv4Interface("10.20.30.250/24"))]]
    monkeypatch.setattr(
        interfaces, "wired_interfaces", lambda: ports.pop(0) if len(ports) > 1 else ports[0]
    )
    _write_twin(
        install["dir"],
        CAM_A,
        "Cam",
        spec=CAM_SPEC,
        twin_block={"host_address": "192.168.5.10/24"},
        twin_params=FIXED_PARAMS,
    )
    install["devices"]([_cam("10.20.30.3", "00:11:22:33:44:55", announced=False)])

    core._detect_and_bind_network_devices()

    assert install["configured"] == [("eth0", False, ["10.20.30.250/24"])]
    assert any("192.168.5.10/24 is outside" in line for line in install["printed"])


def test_a_fixed_ip_answered_by_another_mac_is_flagged(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(
        install["dir"],
        CAM_A,
        "Cam",
        spec=CAM_SPEC,
        twin_params=FIXED_PARAMS,
        metadata={"mac_address": "00:11:22:00:00:01"},
    )
    install["devices"]([_cam("10.20.30.3", "00:11:22:00:00:99", announced=False)])

    core._detect_and_bind_network_devices()

    assert any("not the 00:11:22:00:00:01 saved" in line for line in install["printed"])
    assert install["pins"][CAM_A]["mac_address"] == "00:11:22:00:00:99"


def test_a_silent_fixed_ip_keeps_its_saved_mac(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(
        install["dir"],
        CAM_A,
        "Cam",
        spec=CAM_SPEC,
        twin_params=FIXED_PARAMS,
        metadata={"mac_address": "00:11:22:00:00:01"},
    )
    install["devices"]([])

    bindings = core._detect_and_bind_network_devices()

    assert install["pins"] == {}
    assert bindings == {CAM_A: {"video_device": FIXED_URL}}
    assert any("nothing answers at 10.20.30.3" in line for line in install["printed"])


PLACEHOLDER = "CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://192.168.X.X/stream"
OTHER_PARAM = "OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp"


def _plain(lines):
    return [re.sub(r"\[/?(?:bold|dim|green|yellow|red|cyan)\]", "", line) for line in lines]


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


def test_a_placeholder_is_ignored_and_the_device_found_on_the_wire_is_pinned(install):
    core = install["core"]
    _write_twin(
        install["dir"],
        CAM_A,
        "Cam",
        spec=CAM_SPEC,
        twin_params=["-e", PLACEHOLDER, "-e", OTHER_PARAM],
    )
    install["devices"]([_cam("169.254.79.239", "00:11:22:33:44:55")])

    bindings = core._detect_and_bind_network_devices()

    url = "rtsp://169.254.79.239/stream"
    assert bindings == {CAM_A: {"video_device": url, "mac_address": "00:11:22:33:44:55"}}
    assert install["discovered"][0][1]["targets"] == []
    assert install["pins"][CAM_A] == {
        "video_device": url,
        "mac_address": "00:11:22:33:44:55",
        "drivers": _drivers(["-e", OTHER_PARAM]),
    }
    cached = json.loads((install["dir"] / f"{CAM_A}.json").read_text())["metadata"]
    assert cached["drivers"]["default"]["params"] == ["-e", OTHER_PARAM]
    printed = "\n".join(install["printed"])
    assert f"ignoring {PLACEHOLDER}" in printed and "is a placeholder" in printed
    assert f"Cam → video_device={url}" in printed
    assert [u for u, _n, s in core._list_network_twins() if s.fixed_field] == []


def test_a_placeholder_from_the_asset_becomes_a_twin_level_override(install):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC, asset_params=["-e", PLACEHOLDER])
    install["devices"]([_cam("169.254.79.239", "00:11:22:33:44:55")])

    core._detect_and_bind_network_devices()

    assert install["pins"][CAM_A]["drivers"] == _drivers([])


def test_a_placeholder_whose_device_is_not_found_keeps_its_param(install):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC, twin_params=["-e", PLACEHOLDER])
    install["devices"]([])

    assert core._detect_and_bind_network_devices() == {}

    assert install["pins"] == {}
    assert any(
        "Cam → not pinned" in line and "was not found" in line
        for line in _plain(install["printed"])
    )


def test_a_placeholder_without_a_wired_link_cannot_be_pinned(install, monkeypatch):
    core = install["core"]
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda: [_port(carrier=False)])
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC, twin_params=["-e", PLACEHOLDER])
    install["devices"]([])

    assert core._detect_and_bind_network_devices() == {}

    assert install["discovered"] == [] and install["pins"] == {}
    [line] = [line for line in _plain(install["printed"]) if "Cam → not pinned" in line]
    assert "is unusable" in line and "no wired port has a link" in line


def test_a_usable_fixed_source_is_followed_without_a_wired_link(install, monkeypatch):
    core = install["core"]
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda: [_port(carrier=False)])
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC, twin_params=FIXED_PARAMS)
    install["devices"]([])

    bindings = core._detect_and_bind_network_devices()

    assert bindings == {CAM_A: {"video_device": FIXED_URL}}
    assert install["discovered"] == [] and install["pins"] == {}
    assert any(
        f"Cam → video_device={FIXED_URL}" in line
        and "set by CYBERWAVE_METADATA_VIDEO_DEVICE, not checked" in line
        for line in install["printed"]
    )


def test_no_wired_link_and_no_fixed_source_means_no_binding(install, monkeypatch):
    core = install["core"]
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda: [_port(carrier=False)])
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC)
    install["devices"]([])

    assert core._detect_and_bind_network_devices() == {}

    assert any(
        "Cam → not pinned" in line
        and "no wired port has a link and no CYBERWAVE_METADATA_VIDEO_DEVICE is set" in line
        for line in _plain(install["printed"])
    )


def test_a_placeholder_never_reaches_cameras_json(install, monkeypatch):
    core = install["core"]
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda: [_port(carrier=False)])
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC, twin_params=["-e", PLACEHOLDER])
    install["devices"]([])

    core._record_network_twin_sources(core._detect_and_bind_network_devices())

    path = install["dir"] / "cameras.json"
    assert not path.exists() or CAM_A not in json.loads(path.read_text()).get("twin_to_device", {})


LEFT_MAC, RIGHT_MAC = "00:11:22:00:00:03", "00:11:22:00:00:04"


def _two_twins_sharing_the_asset_source(install, **right_metadata):
    twins = ((CAM_A, "Cam left", {}), (CAM_B, "Cam right", right_metadata))
    for twin_uuid, name, metadata in twins:
        _write_twin(
            install["dir"],
            twin_uuid,
            name,
            spec=CAM_SPEC,
            asset_params=FIXED_PARAMS,
            metadata=metadata,
        )


def test_a_source_both_twins_inherit_goes_to_the_one_the_operator_names(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _two_twins_sharing_the_asset_source(install)
    install["devices"](
        [_cam("10.20.30.3", LEFT_MAC, announced=False), _cam("10.20.30.4", RIGHT_MAC)]
    )
    install["answers"] = ["1"]

    bindings = core._detect_and_bind_network_devices()

    [(question, choices)] = install["prompts"]
    assert "Which twin is it for" in question and choices == ["1", "2", "s"]
    assert bindings[CAM_A] == {"video_device": FIXED_URL, "mac_address": LEFT_MAC}
    assert bindings[CAM_B] == {"video_device": "rtsp://10.20.30.4/stream", "mac_address": RIGHT_MAC}
    assert install["pins"][CAM_B]["drivers"] == _drivers([])
    assert "drivers" not in install["pins"].get(CAM_A, {})
    printed = "\n".join(_plain(install["printed"]))
    assert "it names Cam left's device" in printed


def test_the_saved_mac_answering_at_a_shared_source_names_its_twin(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _two_twins_sharing_the_asset_source(install, mac_address=LEFT_MAC)
    install["devices"](
        [_cam("10.20.30.3", LEFT_MAC, announced=False), _cam("10.20.30.4", RIGHT_MAC)]
    )

    bindings = core._detect_and_bind_network_devices()

    assert install["prompts"] == []
    assert bindings[CAM_B] == {"video_device": FIXED_URL, "mac_address": LEFT_MAC}
    assert bindings[CAM_A]["video_device"] == "rtsp://10.20.30.4/stream"
    assert install["pins"][CAM_A]["drivers"] == _drivers([])


def test_an_unanswered_shared_source_question_changes_nothing(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _two_twins_sharing_the_asset_source(install)
    install["devices"]([_cam("10.20.30.3", LEFT_MAC, announced=False)])

    def eof(*_args, **_kwargs):
        raise EOFError

    monkeypatch.setattr(core.Prompt, "ask", eof)

    bindings = core._detect_and_bind_network_devices()

    assert bindings[CAM_A]["video_device"] == bindings[CAM_B]["video_device"] == FIXED_URL
    assert all("drivers" not in pins for pins in install["pins"].values())
    assert any("so they show the same device" in line for line in install["printed"])


def test_a_shared_source_without_a_wired_link_still_goes_to_one_twin(install, monkeypatch):
    core = install["core"]
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda: [_port(carrier=False)])
    _two_twins_sharing_the_asset_source(install)
    install["devices"]([])
    install["answers"] = ["2"]

    bindings = core._detect_and_bind_network_devices()

    assert bindings == {CAM_B: {"video_device": FIXED_URL}}
    assert install["pins"][CAM_A] == {"drivers": _drivers([])}
    [line] = [line for line in _plain(install["printed"]) if "Cam left → not pinned" in line]
    assert "names Cam right's device" in line and "no wired port has a link" in line


def test_a_shared_source_needs_the_host_address_once(install, monkeypatch):
    core = install["core"]
    _two_twins_sharing_the_asset_source(install)
    install["devices"]([])
    install["answers"] = ["s"]

    core._detect_and_bind_network_devices()

    assert install["configured"] == [("eth0", False, ["10.20.30.250/24"])]


def test_the_operator_chooses_between_a_silent_source_and_the_device_found(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(
        install["dir"],
        CAM_A,
        "Cam",
        spec=CAM_SPEC,
        twin_params=["-e", f"CYBERWAVE_METADATA_VIDEO_DEVICE={FIXED_URL}", "-e", OTHER_PARAM],
        metadata={"mac_address": RIGHT_MAC},
    )
    install["devices"]([_cam("10.20.30.4", RIGHT_MAC)])
    install["answers"] = ["2"]

    bindings = core._detect_and_bind_network_devices()

    [(question, choices)] = install["prompts"]
    assert "Source for Cam" in question and choices == ["1", "2"]
    printed = "\n".join(_plain(install["printed"]))
    assert "nothing answers there" in printed and "(the MAC saved on this twin)" in printed
    assert bindings[CAM_A] == {"video_device": "rtsp://10.20.30.4/stream", "mac_address": RIGHT_MAC}
    assert install["pins"][CAM_A]["drivers"] == _drivers(["-e", OTHER_PARAM])
    assert "the device chosen on the wire replaces it" in printed


def test_keeping_the_source_is_the_default(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC, twin_params=FIXED_PARAMS)
    install["devices"](
        [_cam("10.20.30.3", LEFT_MAC, announced=False), _cam("10.20.30.4", RIGHT_MAC)]
    )
    install["answers"] = ["1"]

    bindings = core._detect_and_bind_network_devices()

    assert len(install["prompts"]) == 1
    assert bindings[CAM_A] == {"video_device": FIXED_URL, "mac_address": LEFT_MAC}
    assert "drivers" not in install["pins"][CAM_A]
    assert any("Not pinned to any twin" in ln and RIGHT_MAC in ln for ln in install["printed"])


def test_a_source_its_saved_mac_answers_at_is_not_questioned(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(
        install["dir"],
        CAM_A,
        "Cam",
        spec=CAM_SPEC,
        twin_params=FIXED_PARAMS,
        metadata={"mac_address": LEFT_MAC},
    )
    install["devices"](
        [_cam("10.20.30.3", LEFT_MAC, announced=False), _cam("10.20.30.4", RIGHT_MAC)]
    )

    core._detect_and_bind_network_devices()

    assert install["prompts"] == []
    assert any("Not pinned to any twin" in ln and RIGHT_MAC in ln for ln in install["printed"])


def test_devices_twins_without_a_source_take_are_not_offered_to_a_source(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(install["dir"], CAM_A, "Cam fixed", spec=CAM_SPEC, twin_params=FIXED_PARAMS)
    _write_twin(install["dir"], CAM_B, "Cam found", spec=CAM_SPEC)
    install["devices"]([_cam("10.20.30.4", RIGHT_MAC)])

    bindings = core._detect_and_bind_network_devices()

    assert install["prompts"] == []
    assert bindings[CAM_B]["mac_address"] == RIGHT_MAC
    assert bindings[CAM_A] == {"video_device": FIXED_URL}


def test_a_moved_unit_stays_with_the_twin_whose_source_it_left(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(
        install["dir"],
        CAM_A,
        "Cam fixed",
        spec=CAM_SPEC,
        twin_params=FIXED_PARAMS,
        metadata={"mac_address": LEFT_MAC},
    )
    _write_twin(install["dir"], CAM_B, "Cam found", spec=CAM_SPEC)
    install["devices"]([_cam("10.20.30.5", LEFT_MAC), _cam("10.20.30.6", RIGHT_MAC)])
    install["answers"] = ["2"]

    bindings = core._detect_and_bind_network_devices()

    [(question, _choices)] = install["prompts"]
    assert "Source for Cam fixed" in question
    assert bindings[CAM_B]["mac_address"] == RIGHT_MAC
    assert bindings[CAM_A] == {"video_device": "rtsp://10.20.30.5/stream", "mac_address": LEFT_MAC}


def test_more_twins_than_devices_asks_who_gets_it_and_says_why_the_other_waits(install):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Cam left", spec=CAM_SPEC)
    _write_twin(install["dir"], CAM_B, "Cam right", spec=CAM_SPEC)
    install["devices"]([_cam("169.254.1.1", LEFT_MAC)])
    install["answers"] = ["1"]

    bindings = core._detect_and_bind_network_devices()

    assert len(install["prompts"]) == 1 and install["prompts"][0][1] == ["1", "s"]
    printed = _plain(install["printed"])
    assert any("2 twins (Cam left, Cam right) fit it" in line for line in printed)
    assert list(bindings) == [CAM_A]
    [line] = [line for line in printed if "Cam right → not pinned" in line]
    assert "every matching device found is pinned to another twin (Cam left)" in line


def test_a_device_at_another_twins_source_is_named_as_taken(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(install["dir"], CAM_A, "Cam fixed", spec=CAM_SPEC, twin_params=FIXED_PARAMS)
    _write_twin(install["dir"], CAM_B, "Cam found", spec=CAM_SPEC)
    install["devices"]([_cam("10.20.30.3", LEFT_MAC)])

    core._detect_and_bind_network_devices()

    [line] = [line for line in _plain(install["printed"]) if "Cam found → not pinned" in line]
    assert "pinned to another twin (Cam fixed)" in line


def test_a_device_on_two_addresses_is_offered_once_at_the_routable_one(install, monkeypatch):
    core = install["core"]
    monkeypatch.setattr(
        interfaces,
        "wired_interfaces",
        lambda: [_port(LINK_LOCAL, ipaddress.IPv4Interface("192.168.1.10/24"))],
    )
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC)
    install["devices"]([_cam("169.254.1.1", LEFT_MAC), _cam("192.168.1.20", LEFT_MAC)])

    bindings = core._detect_and_bind_network_devices()

    assert install["prompts"] == []
    assert bindings[CAM_A]["video_device"] == "rtsp://192.168.1.20/stream"


def test_a_link_local_device_beside_a_dhcp_subnet_gets_the_port_link_local(install, monkeypatch):
    core = install["core"]
    monkeypatch.setattr(
        interfaces, "wired_interfaces", lambda: [_port(ipaddress.IPv4Interface("192.168.1.10/24"))]
    )
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC)
    install["devices"]([_cam("169.254.1.1", "", reachable=False)], [_cam("169.254.1.1", LEFT_MAC)])

    core._detect_and_bind_network_devices()

    assert install["configured"] == [("eth0", True, [])]


def test_an_earlier_link_local_only_profile_is_upgraded_before_the_search(install, monkeypatch):
    core = install["core"]
    adopted = []
    monkeypatch.setattr(
        linkconfig, "adopt_dhcp", lambda name: adopted.append(name) or "cyberwave-eth0"
    )
    _write_twin(install["dir"], CAM_A, "Cam", spec=CAM_SPEC)
    install["devices"]([_cam("169.254.1.1", LEFT_MAC)])

    core._detect_and_bind_network_devices()

    assert adopted == ["eth0"]
    assert any("now also takes a DHCP address" in line for line in install["printed"])


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


def test_a_bound_network_camera_is_recorded_with_its_url(install, monkeypatch):
    core = install["core"]
    _write_twin(install["dir"], WEBCAM, "Webcam")
    _write_twin(install["dir"], CAM_A, "Network cam", spec=CAM_SPEC)
    _one_usb_camera(monkeypatch)
    install["devices"]([_cam("169.254.79.239", "00:11:22:33:44:55")])

    core._detect_and_bind_network_devices()
    core._detect_and_select_cameras()
    core._record_network_twin_sources()

    mapping = json.loads((install["dir"] / "cameras.json").read_text())["twin_to_device"]
    assert mapping == {WEBCAM: 0, CAM_A: "rtsp://169.254.79.239/stream"}


def _write_edge_json(directory, cameras):
    (directory / "edge.json").write_text(
        json.dumps({"uuid": "edge-1", "metadata": {"cameras": cameras}})
    )


def _edge_json_cameras(directory):
    return json.loads((directory / "edge.json").read_text())["metadata"].get("cameras")


def test_a_bound_network_camera_reaches_the_edge_json_edge_core_reads_first(install, monkeypatch):
    core = install["core"]
    _write_twin(install["dir"], WEBCAM, "Webcam")
    _write_twin(install["dir"], CAM_A, "Network cam", spec=CAM_SPEC)
    _one_usb_camera(monkeypatch)
    install["devices"]([_cam("169.254.79.239", "00:11:22:33:44:55")])
    uploads = []

    def upload(cameras):
        uploads.append(cameras)
        _write_edge_json(install["dir"], cameras)
        return True

    monkeypatch.setattr(core, "_upload_cameras_to_edge_metadata", upload)

    core._detect_and_bind_network_devices()
    core._detect_and_select_cameras()
    core._record_network_twin_sources()

    cameras = _edge_json_cameras(install["dir"])
    assert cameras["twin_to_device"][CAM_A] == "rtsp://169.254.79.239/stream"
    assert WEBCAM in cameras["twin_to_device"]
    assert cameras["selected_device"] == 0
    assert CAM_A not in uploads[0].get("twin_to_device", {})
    assert len(uploads) == 2


def test_an_unreachable_backend_still_gets_the_url_into_edge_json(install, monkeypatch):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Network cam", spec=CAM_SPEC)
    usb_pin = "/dev/v4l/by-id/usb-cam-video-index0"
    _write_edge_json(
        install["dir"], {"devices": [], "selected_device": 0, "twin_to_device": {WEBCAM: usb_pin}}
    )
    monkeypatch.setattr(core, "_upload_cameras_to_edge_metadata", lambda _cameras: False)
    install["devices"]([_cam("169.254.79.239", "00:11:22:33:44:55")])

    core._record_network_twin_sources(core._detect_and_bind_network_devices())

    cameras = _edge_json_cameras(install["dir"])
    assert cameras["twin_to_device"] == {WEBCAM: usb_pin, CAM_A: "rtsp://169.254.79.239/stream"}
    assert cameras["selected_device"] == 0
    assert any("edge.json updated" in line for line in install["printed"])


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


def test_a_binding_the_backend_refused_still_reaches_cameras_json(install, monkeypatch):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Network cam", spec=CAM_SPEC)
    monkeypatch.setattr(core, "_persist_twin_pins", lambda pins: set(pins))  # every save fails
    install["devices"]([_cam("169.254.79.239", "00:11:22:33:44:55")])

    bindings = core._detect_and_bind_network_devices()
    core._record_network_twin_sources(bindings)

    assert bindings == {
        CAM_A: {"video_device": "rtsp://169.254.79.239/stream", "mac_address": "00:11:22:33:44:55"}
    }
    cached = json.loads((install["dir"] / f"{CAM_A}.json").read_text())["metadata"]
    assert "video_device" not in cached
    mapping = json.loads((install["dir"] / "cameras.json").read_text())["twin_to_device"]
    assert mapping == {CAM_A: "rtsp://169.254.79.239/stream"}
    assert any("still holds its old binding" in line for line in install["printed"])


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


def test_which_twins_are_network_twins_comes_from_their_metadata(install):
    core = install["core"]
    _write_twin(install["dir"], WEBCAM, "Webcam")
    _write_twin(
        install["dir"],
        CAM_A,
        "No way to locate it",
        spec={"bind": {"video_device": "rtsp://{ip}/s"}},
    )
    assert core._list_network_twins() == []
    _write_twin(install["dir"], CAM_B, "Twin-level block", twin_block=CAM_SPEC)
    assert [u for u, _n, _s in core._list_network_twins()] == [CAM_B]
    _write_twin(
        install["dir"],
        CAM_A,
        "A fixed source completes it",
        spec={"bind": {"video_device": "rtsp://{ip}/s"}},
        twin_params=FIXED_PARAMS,
    )
    assert {u for u, _n, _s in core._list_network_twins()} == {CAM_A, CAM_B}


def test_a_fixed_source_is_recorded_in_cameras_json(install, monkeypatch):
    core = install["core"]
    _on_the_camera_subnet(monkeypatch)
    _write_twin(install["dir"], CAM_A, "Network cam", spec=CAM_SPEC, twin_params=FIXED_PARAMS)
    install["devices"]([_cam("10.20.30.3", "00:11:22:33:44:55", announced=False)])

    core._record_network_twin_sources(core._detect_and_bind_network_devices())

    mapping = json.loads((install["dir"] / "cameras.json").read_text())["twin_to_device"]
    assert mapping == {CAM_A: FIXED_URL}
