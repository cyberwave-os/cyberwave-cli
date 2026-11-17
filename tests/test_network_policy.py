import importlib.util
import ipaddress
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cyberwave_cli.discovery import hardware, interfaces
from tests.test_network_binding import CAM_A, CAM_SPEC, _port, _write_twin
from tests.test_network_binding import install as _install

install = _install


@pytest.fixture
def installed_runtime(install, monkeypatch):
    package = Path(__file__).resolve().parents[1] / "cyberwave_cli"
    spec = importlib.util.spec_from_file_location("review_cli_config", package / "config.py")
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    source = Path(__file__).resolve().parents[3] / "cyberwave-edge-core/cyberwave_edge_core"
    deb = install["dir"] / "deb-python"
    runtime = deb / "cyberwave_edge_core"
    runtime.mkdir(parents=True)
    for name in ("__init__.py", "_version.py", "driver_selection.py"):
        shutil.copy2(source / name, runtime / name)
    monkeypatch.setattr(config, "_EDGE_CORE_DEB_PYTHON_PATH", str(deb))
    monkeypatch.setattr(
        sys.modules["cyberwave_cli.config"],
        "ensure_edge_core_importable",
        config.ensure_edge_core_importable,
    )
    monkeypatch.setattr(
        install["core"], "ensure_edge_core_importable", config.ensure_edge_core_importable
    )
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in (
        "cyberwave_edge_core",
        "cyberwave_edge_core._version",
        "cyberwave_edge_core.driver_selection",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    monkeypatch.setenv("CYBERWAVE_PLATFORM_VARIANT", "jetson")
    yield
    for name in list(sys.modules):
        if name == "cyberwave_edge_core" or name.startswith("cyberwave_edge_core."):
            sys.modules.pop(name, None)


def paired_snapshot(install, uuids):
    for twin_uuid in uuids:
        path = install["dir"] / f"{twin_uuid}.json"
        data = json.loads(path.read_text())
        data.setdefault("attach_to_twin_uuid", None)
        data["metadata"]["edge_fingerprint"] = "test-edge"
        path.write_text(json.dumps(data))
    (install["dir"] / "environment.json").write_text(
        json.dumps(
            {
                "twin_uuids": uuids,
                "driver_context_fingerprint": "test-edge",
            }
        )
    )


def test_go2_wifi_route_is_not_device_verification(install, installed_runtime, monkeypatch):
    core = install["core"]
    sys_net = install["dir"] / "sys/class/net"
    (sys_net / "wlan0/device").mkdir(parents=True)
    monkeypatch.setattr(interfaces, "SYS_CLASS_NET", sys_net)
    _write_twin(
        install["dir"],
        CAM_A,
        "Go2",
        metadata={
            "drivers": {
                "default": {"shared_env": {"ROBOT_IP": "192.168.99.161"}},
                "linux-aarch64-jetson": {"shared_env": {"ROBOT_IP": "192.168.123.161"}},
            }
        },
    )
    configure_paired_snapshot(install, monkeypatch)
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda **kw: [_port()])
    monkeypatch.setattr(
        interfaces,
        "read_ipv4",
        lambda: interfaces.IPv4Snapshot({"wlan0": [ipaddress.IPv4Interface("192.168.123.20/24")]}),
    )
    routes = []
    monkeypatch.setattr(
        interfaces,
        "route_to",
        lambda ip: routes.append(ip) or interfaces.RouteObservation("wlan0", "192.168.123.20"),
    )
    core._detect_and_bind_network_devices()
    output = "\n".join(install["printed"]).replace("\\[", "[")
    assert "Go2 [linux-aarch64-jetson] ROBOT_IP: 192.168.123.161" in output
    assert routes == ["192.168.123.161"]
    assert "192.168.99.161" not in output
    assert "Matching host prefix: wlan0 192.168.123.20/24" in output
    assert "Kernel-selected route: wlan0, source 192.168.123.20" in output
    assert "separate eth0 cable" in output
    assert "Intended device: not verified" in output
    assert install["pins"] == {}


def test_setup_never_upgrades_an_existing_profile(install, monkeypatch):
    _write_twin(install["dir"], CAM_A, "Camera", spec=CAM_SPEC)
    install["devices"]([])
    install["core"]._detect_and_bind_network_devices()


def test_metadata_cannot_authorize_discovery(install, monkeypatch):
    _write_twin(
        install["dir"], CAM_A, "Camera", spec={**CAM_SPEC, "methods": ["arp", "mdns", "ports"]}
    )
    monkeypatch.setattr(
        hardware, "discover", lambda *a, **k: pytest.fail("active discovery"), raising=False
    )
    install["core"]._detect_and_bind_network_devices()


def test_yes_to_all_cannot_authorize_address_changes(install, monkeypatch):
    _write_twin(install["dir"], CAM_A, "Camera", spec={**CAM_SPEC, "link_local": True})
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda **kw: [_port()])
    install["devices"]([])
    install["core"]._detect_and_bind_network_devices()


def test_unresolved_profile_preserves_every_binding_and_mapping(install, monkeypatch):
    import json

    from tests.test_network_binding import WEBCAM, _one_usb_camera, _write_edge_json

    core = install["core"]
    metadata = {
        "video_device": "rtsp://10.0.0.8/old",
        "mac_address": "00:11:22:33:44:55",
        "drivers": {
            "default": {"params": ["-e", "CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://10.0.0.9/wrong"]},
            "linux-aarch64-jetson": {"shared_env": {"ROBOT_IP": "192.168.123.161"}},
        },
    }
    _write_twin(install["dir"], CAM_A, "Unresolved", spec=CAM_SPEC, metadata=metadata)
    _write_twin(install["dir"], WEBCAM, "Webcam")
    local = {"devices": [], "twin_to_device": {CAM_A: "rtsp://10.0.0.7/local"}}
    (install["dir"] / "cameras.json").write_text(json.dumps(local))
    _write_edge_json(
        install["dir"], {"devices": [], "twin_to_device": {CAM_A: "rtsp://10.0.0.6/cloud"}}
    )
    monkeypatch.setattr(core, "_upload_cameras_to_edge_metadata", lambda data: False)
    _one_usb_camera(monkeypatch)
    before = (install["dir"] / f"{CAM_A}.json").read_text()
    bindings = core._detect_and_bind_network_devices()
    core._detect_and_select_cameras()
    core._record_network_twin_sources(bindings)
    assert (install["dir"] / f"{CAM_A}.json").read_text() == before
    assert (
        json.loads((install["dir"] / "cameras.json").read_text())["twin_to_device"][CAM_A]
        == "rtsp://10.0.0.7/local"
    )
    assert (
        json.loads((install["dir"] / "edge.json").read_text())["metadata"]["cameras"][
            "twin_to_device"
        ][CAM_A]
        == "rtsp://10.0.0.6/cloud"
    )
    output = "\n".join(install["printed"])
    assert "complete child-registry context unavailable" in output
    assert "192.168.123.161" not in output and "10.0.0.9" not in output
    assert CAM_A not in bindings


def test_resolved_profile_shared_by_reference_and_declared_extraction(install, monkeypatch):
    core = install["core"]
    _write_twin(
        install["dir"], CAM_A, "Jetson", spec=CAM_SPEC, twin_params=["-e", "ROBOT_IP=10.9.9.9"]
    )
    calls = []

    def select(data, **kw):
        calls.append(data)
        return hardware.ProfileSelection(
            "resolved",
            "linux-aarch64-jetson",
            {
                "params": ["-e", "CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://10.0.0.4/stream"],
                "shared_env": {"ROBOT_IP": "10.0.0.4", "MQTT_HOST": "10.0.0.4"},
            },
        )

    monkeypatch.setattr(core, "_twin_driver_selection", select)
    routes = []
    monkeypatch.setattr(
        interfaces, "route_to", lambda ip: routes.append(ip) or interfaces.RouteObservation("wlan0")
    )
    bound = core._detect_and_bind_network_devices()
    core._record_network_twin_sources(bound)
    assert len(calls) == 1
    assert routes == ["10.0.0.4"]
    assert bound[CAM_A]["video_device"] == "rtsp://10.0.0.4/stream"
    output = "\n".join(install["printed"])
    assert "ROBOT_IP" in output and "MQTT_HOST" in output
    assert "linux-aarch64-jetson" in output and "10.9.9.9" not in output
    assert install["pins"] == {}


def test_metadata_only_manual_binding_is_explicit_and_has_no_probe(install, monkeypatch):
    import json

    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Camera", spec=CAM_SPEC)
    monkeypatch.setattr(core.sys.stdin, "isatty", lambda: True)
    install["answers"] = ["bind", CAM_A, "video_device", "rtsp://10.0.0.5/stream"]
    bound = core._detect_and_bind_network_devices()
    core._record_network_twin_sources(bound)
    assert install["pins"] == {CAM_A: {"video_device": "rtsp://10.0.0.5/stream"}}
    assert (
        json.loads((install["dir"] / "cameras.json").read_text())["twin_to_device"][CAM_A]
        == "rtsp://10.0.0.5/stream"
    )


def test_no_relevant_twins_does_not_inspect_host(install, monkeypatch):
    monkeypatch.setattr(interfaces, "read_ipv4", lambda: pytest.fail("unnecessary inventory"))
    assert install["core"]._detect_and_bind_network_devices() == {}


def test_refresh_is_bounded_and_refreshes_profile_context(install, monkeypatch):
    core = install["core"]
    _write_twin(
        install["dir"], CAM_A, "Camera", spec=CAM_SPEC, twin_params=["-e", "ROBOT_IP=10.0.0.1"]
    )
    selections = iter(
        [
            hardware.ProfileSelection("unavailable", reason="test unavailable"),
            hardware.ProfileSelection(
                "resolved", "default", {"shared_env": {"ROBOT_IP": "10.0.0.2"}}
            ),
        ]
    )
    monkeypatch.setattr(core, "_twin_driver_selection", lambda data, **kw: next(selections))
    monkeypatch.setattr(core.sys.stdin, "isatty", lambda: True)
    install["answers"] = ["refresh"]
    core._detect_and_bind_network_devices()
    assert "10.0.0.2" in "\n".join(install["printed"])
    assert len(install["prompts"]) == 1


def test_unresolved_absent_pin_does_not_create_or_classify_usb(install, monkeypatch):
    import cyberwave_cli.device_utils as devices

    core = install["core"]
    _write_twin(
        install["dir"],
        CAM_A,
        "Camera",
        spec=CAM_SPEC,
        metadata={"drivers": {"default": {}, "other": {}}},
    )
    monkeypatch.setattr(devices, "discover_usb_cameras", lambda: pytest.fail("USB reassignment"))
    bound = core._detect_and_bind_network_devices()
    core._record_network_twin_sources(bound)
    core._detect_and_select_cameras()
    assert install["pins"] == {} and not (install["dir"] / "cameras.json").exists()


def test_duplicate_cached_ip_cannot_replace_a_saved_binding(install, monkeypatch):
    core = install["core"]
    _write_twin(
        install["dir"],
        CAM_A,
        "Camera",
        spec=CAM_SPEC,
        metadata={"video_device": "rtsp://10.0.0.5/original", "mac_address": "00:11:22:33:44:55"},
    )
    monkeypatch.setattr(
        hardware,
        "cached_devices",
        lambda: [
            hardware.NetworkDevice("10.0.0.5", "eth0", "00:11:22:33:44:55"),
            hardware.NetworkDevice("10.0.0.5", "eth1", "00:11:22:33:44:66"),
        ],
    )
    bound = core._detect_and_bind_network_devices()
    assert bound[CAM_A]["video_device"] == "rtsp://10.0.0.5/original"
    assert install["pins"] == {}


@pytest.mark.parametrize("spec", [None, CAM_SPEC])
def test_broker_reference_has_no_ethernet_requirement(install, monkeypatch, spec):
    core = install["core"]
    sys_net = install["dir"] / "sys/class/net"
    (sys_net / "wlan0/device").mkdir(parents=True)
    monkeypatch.setattr(interfaces, "SYS_CLASS_NET", sys_net)
    _write_twin(
        install["dir"],
        CAM_A,
        "Broker client",
        spec=spec,
        twin_params=["-e", "MQTT_HOST=192.168.1.5"],
    )
    monkeypatch.setattr(
        core,
        "_twin_driver_selection",
        lambda data, **kw: hardware.ProfileSelection(
            "resolved", "default", {"shared_env": {"MQTT_HOST": "192.168.1.5"}}
        ),
    )
    monkeypatch.setattr(
        interfaces,
        "read_ipv4",
        lambda: interfaces.IPv4Snapshot({"wlan0": [ipaddress.IPv4Interface("192.168.1.20/24")]}),
    )
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda **k: [_port()])
    core._detect_and_bind_network_devices()
    output = "\n".join(install["printed"])
    assert "MQTT_HOST: 192.168.1.5 (address reference)" in output
    assert "If this device" not in output
    assert output.index("No address reference is assigned to this cable") < output.index(
        "MQTT_HOST"
    )
    assert install["pins"] == {}


def test_device_flow_allows_only_read_only_ip_commands(install, monkeypatch):
    import json
    import subprocess

    core = install["core"]
    _write_twin(
        install["dir"],
        CAM_A,
        "Camera",
        spec=CAM_SPEC,
        metadata={"video_device": "rtsp://192.168.50.10/stream"},
    )
    import importlib.util
    import sys

    from cyberwave_cli.discovery import interfaces as module

    spec = importlib.util.spec_from_file_location("observation_runner_contract", module.__file__)
    readers = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, readers)
    spec.loader.exec_module(readers)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 5
        if args == ["ip", "-j", "-4", "addr", "show"]:
            data = [
                {
                    "ifname": "eth0",
                    "addr_info": [{"family": "inet", "local": "192.168.50.2", "prefixlen": 24}],
                }
            ]
        else:
            assert args == ["ip", "-j", "-4", "route", "get", "192.168.50.10"]
            data = [{"dev": "eth0", "prefsrc": "192.168.50.2"}]
        return subprocess.CompletedProcess(args, 0, json.dumps(data))

    monkeypatch.setattr(interfaces, "read_ipv4", lambda: readers.read_ipv4(run))
    monkeypatch.setattr(interfaces, "route_to", lambda ip: readers.route_to(ip, run))
    monkeypatch.setattr(core.subprocess, "run", run)
    core._detect_and_bind_network_devices()
    assert len(calls) == 2


def test_install_verification_requires_separate_explicit_acceptance(install, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path

    from cyberwave_cli.discovery import probes

    path = Path(hardware.__file__).parents[1] / "commands/scan.py"
    spec = importlib.util.spec_from_file_location("cyberwave_cli.commands.scan", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Camera", spec=CAM_SPEC)
    monkeypatch.setattr(core.sys.stdin, "isatty", lambda: True)
    install["answers"] = ["verify", "10.0.0.8", "554", "skip"]
    monkeypatch.setattr(
        probes, "verify_endpoint", lambda *a, **k: pytest.fail("generic yes authorized check")
    )
    core._detect_and_bind_network_devices()
    assert install["pins"] == {}


def test_secret_and_markup_are_not_rendered_as_configuration(install):
    core = install["core"]
    _write_twin(
        install["dir"],
        CAM_A,
        "[red]Camera[/red]",
        spec=CAM_SPEC,
        metadata={"video_device": "rtsp://user:secret@10.0.0.8/stream?password=querysecret#token"},
    )
    core._detect_and_bind_network_devices()
    output = "\n".join(install["printed"])
    assert "secret" not in output and "querysecret" not in output
    assert "\\[red]" in output


def configure_paired_snapshot(
    install, monkeypatch, *, detach_fail=0, attach_fail=0, download_fail=False
):
    core = install["core"]
    data = json.loads((install["dir"] / f"{CAM_A}.json").read_text())
    asset = data.pop("asset")
    data.update(attach_to_twin_uuid=None, asset_uuid="asset-test")
    data["metadata"]["edge_fingerprint"] = "test-edge"
    creds = SimpleNamespace(
        token="test-token", cyberwave_base_url="https://example.test", workspace_uuid="ws"
    )
    workspace = SimpleNamespace(uuid="ws", name="Test")

    def get_raw(twin_uuid):
        if download_fail:
            raise OSError("download unavailable")
        return SimpleNamespace(asset_uuid="asset-test", to_dict=lambda: data)

    client = SimpleNamespace(
        twins=SimpleNamespace(get_raw=get_raw),
        assets=SimpleNamespace(get=lambda uuid: SimpleNamespace(to_dict=lambda: asset)),
    )
    monkeypatch.setattr(core, "load_credentials", lambda: creds)
    monkeypatch.setattr(
        core, "_repair_workspace_from_token", lambda *a, **kw: (creds, workspace, False)
    )
    monkeypatch.setattr(core, "_resolve_workspace_from_credentials", lambda *a: workspace)
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: client)
    monkeypatch.setattr(
        core,
        "_select_or_create_environment",
        lambda *a, **kw: SimpleNamespace(uuid="env", name="Test"),
    )
    monkeypatch.setattr(core, "_select_connected_twins", lambda *a, **kw: [CAM_A])
    monkeypatch.setattr(core, "_load_or_generate_edge_fingerprint", lambda: "test-edge")
    monkeypatch.setattr(
        core, "_detach_edge_fingerprint_from_other_twins", lambda *a: (0, detach_fail)
    )
    monkeypatch.setattr(
        core, "_attach_edge_fingerprint_to_twins", lambda *a: (1 - attach_fail, attach_fail)
    )
    assert core.configure_edge_environment(skip_confirm=True)


@pytest.mark.parametrize(
    "failure", [{}, {"detach_fail": 1}, {"attach_fail": 1}, {"download_fail": True}]
)
def test_pairing_records_context_only_after_complete_sync(install, monkeypatch, failure):
    _write_twin(install["dir"], CAM_A, "Go2", metadata={"drivers": {"default": {}, "linux": {}}})
    paired_snapshot(install, [CAM_A])
    configure_paired_snapshot(install, monkeypatch, **failure)
    environment = json.loads((install["dir"] / "environment.json").read_text())
    children = install["core"]._cached_child_registry_ids(
        dict(install["core"]._iter_selected_twins())
    )
    if failure:
        assert "driver_context_fingerprint" not in environment
        assert children is None
    else:
        assert environment["driver_context_fingerprint"] == "test-edge"
        assert children == {CAM_A: set()}


@pytest.mark.parametrize(
    "damage", ["manifest", "missing_child", "malformed_child", "fingerprint", "attachment", "asset"]
)
def test_incomplete_cached_context_never_selects_a_profile(install, installed_runtime, damage):
    from tests.test_network_binding import CAM_B

    _write_twin(
        install["dir"],
        CAM_A,
        "Go2",
        metadata={
            "drivers": {
                "default": {"shared_env": {"ROBOT_IP": "10.0.0.1"}},
                "vendor/child": {"shared_env": {"ROBOT_IP": "10.0.0.2"}},
            }
        },
    )
    _write_twin(install["dir"], CAM_B, "Child")
    paired_snapshot(install, [CAM_A, CAM_B])
    path = install["dir"] / f"{CAM_B}.json"
    data = json.loads(path.read_text())
    data["attach_to_twin_uuid"] = CAM_A
    data["asset"]["registry_id"] = "vendor/child"
    if damage == "fingerprint":
        data["metadata"]["edge_fingerprint"] = "other-edge"
    elif damage == "attachment":
        data.pop("attach_to_twin_uuid")
    elif damage == "asset":
        data["asset"] = {}
    path.write_text(json.dumps(data))
    if damage == "malformed_child":
        path.write_text("null")
    elif damage == "missing_child":
        path.unlink()
    elif damage == "manifest":
        (install["dir"] / "environment.json").write_text(json.dumps({"twin_uuids": [CAM_A, CAM_B]}))
    install["core"]._detect_and_bind_network_devices()
    output = "\n".join(install["printed"])
    assert "complete child-registry context unavailable" in output
    assert "10.0.0.1" not in output and "10.0.0.2" not in output
    assert install["pins"] == {}


def test_real_child_override_controls_both_extractions(install, installed_runtime):
    from tests.test_network_binding import CAM_B

    _write_twin(
        install["dir"],
        CAM_A,
        "Camera",
        spec=CAM_SPEC,
        metadata={
            "drivers": {
                "linux-aarch64-jetson": {"shared_env": {"ROBOT_IP": "10.0.0.1"}},
                "vendor/child": {
                    "shared_env": {
                        "ROBOT_IP": "10.0.0.2",
                        "CYBERWAVE_METADATA_VIDEO_DEVICE": "rtsp://10.0.0.2/live",
                    }
                },
            }
        },
    )
    _write_twin(install["dir"], CAM_B, "Child", metadata={"attach_to_twin_uuid": CAM_A})
    paired_snapshot(install, [CAM_A, CAM_B])
    path = install["dir"] / f"{CAM_B}.json"
    data = json.loads(path.read_text())
    data["asset"]["metadata"] = {"registry_id": " vendor/child "}
    path.write_text(json.dumps(data))
    bindings = install["core"]._detect_and_bind_network_devices()
    install["core"]._record_network_twin_sources(bindings)
    assert bindings[CAM_A]["video_device"] == "rtsp://10.0.0.2/live"
    output = "\n".join(install["printed"])
    assert "vendor/child" in output and "ROBOT_IP: 10.0.0.2" in output
    assert "10.0.0.1" not in output


def test_noninteractive_install_on_tty_does_not_prompt_or_verify(install, monkeypatch):
    core = install["core"]
    _write_twin(install["dir"], CAM_A, "Camera", spec=CAM_SPEC)
    monkeypatch.setattr(core.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core, "_is_macos", lambda: False)
    monkeypatch.setattr(core, "is_macos", lambda: False)
    monkeypatch.setattr(core.os, "geteuid", lambda: 0)
    for name in (
        "_migrate_legacy_config_dir",
        "_any_twin_has_camera_sensor",
        "_any_twin_has_microphone_sensor",
        "_any_twin_has_speaker_sensor",
    ):
        monkeypatch.setattr(core, name, lambda: False)
    for name in ("_ensure_credentials", "install_service_package", "_install_docker"):
        monkeypatch.setattr(core, name, lambda *a, **kw: True)
    monkeypatch.setattr(core, "create_systemd_service", lambda *a, **kw: False)
    core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=True)
    assert "Network observations" in "\n".join(install["printed"])
    assert install["prompts"] == [] and install["pins"] == {}


def test_driver_env_address_follows_launcher_precedence_shared_env_wins():
    entry = {
        "shared_env": {"MQTT_HOST": "192.168.1.10"},
        "params": ["-e", "MQTT_HOST=192.168.1.20"],
    }
    selection = hardware.ProfileSelection("resolved", "default", entry)
    assert hardware.driver_env_addresses(selection) == {("default", "MQTT_HOST"): "192.168.1.10"}
