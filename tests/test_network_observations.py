from __future__ import annotations

import importlib.util
import ipaddress
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cyberwave_cli.discovery import hardware, interfaces, neighbors


def snapshot(values):
    return interfaces.IPv4Snapshot(
        {
            name: [ipaddress.IPv4Interface(a) for a in addresses]
            for name, addresses in values.items()
        }
    )


@pytest.mark.parametrize(
    "values,target,names,overlap",
    [
        ({"eth0": ["10.0.0.1/24"]}, "10.0.0.8", ["eth0"], False),
        ({"eth0": ["10.0.0.1/24", "192.168.50.1/24"]}, "192.168.50.10", ["eth0"], False),
        (
            {"eth0": ["192.168.123.2/24"], "eth1": ["192.168.50.2/24"]},
            "192.168.50.10",
            ["eth1"],
            False,
        ),
        ({"eth0": ["10.0.0.1/24"], "wlan0": ["10.0.0.2/24"]}, "10.0.0.8", ["eth0", "wlan0"], True),
        ({"eth0": ["10.0.0.1/16"], "wlan0": ["10.0.1.2/25"]}, "10.0.1.8", ["eth0", "wlan0"], True),
        ({"tun0": ["10.0.0.1/8"], "eth0": []}, "10.1.1.1", ["tun0"], False),
        ({"eth0": ["10.0.0.1/24"]}, "192.168.50.10", [], False),
    ],
)
def test_prefix_matrix(values, target, names, overlap):
    observation = snapshot(values)
    assert [n for n, _ in observation.matches(target)] == names
    assert bool(observation.overlaps()) == overlap
    route = interfaces.RouteObservation("vpn0", "172.16.1.2", "172.16.1.1")
    text = "\n".join(interfaces.describe_endpoint(target, observation, route))
    assert "Kernel-selected route: vpn0, source 172.16.1.2, gateway 172.16.1.1" in text
    assert "Intended device: not verified" in text
    if overlap:
        assert "intentional same-LAN" in "\n".join(interfaces.describe_host(observation, []))


@pytest.mark.parametrize("physical", [[], ["wlan0"], ["wlan0", "eth1"]])
def test_unaddressed_cable_advice_names_only_other_physical_links(tmp_path, monkeypatch, physical):
    for name in ("lo", "docker0", "eth0", *physical):
        (tmp_path / name).mkdir(exist_ok=True)
    for name in physical:
        (tmp_path / name / "device").mkdir()
    monkeypatch.setattr(interfaces, "SYS_CLASS_NET", tmp_path)
    observation = snapshot(
        {
            "eth0": [],
            "lo": ["127.0.0.1/8"],
            "docker0": ["172.17.0.1/16"],
            **{name: ["192.168.123.20/24"] for name in physical},
        }
    )
    port = interfaces.WiredInterface("eth0", "02:00:00:00:00:01", True)
    lines = interfaces.describe_host(observation, [port])
    advice = [line for line in lines if "the networks may overlap" in line]
    assert "eth0: cable connected, no IPv4 address; device addressing unknown" in lines
    if physical:
        assert len(advice) == 1
        assert f"on {', '.join(sorted(physical))}, the networks may overlap" in advice[0]
        assert "docker0" not in advice[0] and "lo," not in advice[0]
    else:
        assert not advice


def test_software_interface_prefix_conflicts_are_still_reported():
    observation = snapshot({"docker0": ["192.168.123.1/24"], "wlan0": ["192.168.123.20/24"]})
    assert any(
        "Possible overlap: docker0" in line for line in interfaces.describe_host(observation, [])
    )
    lines = interfaces.describe_endpoint(
        "192.168.123.161", observation, interfaces.RouteObservation("docker0")
    )
    assert any("Matching host prefix: docker0" in line for line in lines)
    assert "Kernel-selected route: docker0 (CLI context)" in lines


@pytest.mark.parametrize(
    "raw", ["null", "{}", "[null]", "[{}]", '[{"ifname": "eth0", "addr_info": [{}]}', "garbage"]
)
def test_malformed_address_inventory_is_unknown(raw):
    result = interfaces.read_ipv4(lambda *a, **k: subprocess.CompletedProcess(a, 0, raw))
    assert result.error


def test_empty_inventory_and_failed_query_are_distinct():
    empty = interfaces.read_ipv4(lambda *a, **k: subprocess.CompletedProcess(a, 0, "[]"))
    assert not empty.error and empty.addresses == {}

    def missing(*a, **k):
        raise FileNotFoundError("ip")

    assert interfaces.read_ipv4(missing).error
    assert interfaces.route_to("10.0.0.1", missing).error


@pytest.mark.parametrize(
    "entries,expected,error",
    [
        ([{"dev": "wlan0", "gateway": "10.0.0.1", "prefsrc": "10.0.0.2"}], "wlan0", False),
        ([{"dev": "tun0", "src": "172.16.0.2"}], "tun0", False),
        ([], None, False),
        ({}, None, True),
        ([{"type": "unreachable"}], None, False),
        ([{}], None, True),
        ([None], None, True),
    ],
)
def test_route_reads_kernel_choice_including_default(entries, expected, error):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 5 and kwargs["check"]
        return subprocess.CompletedProcess(args, 0, json.dumps(entries))

    route = interfaces.route_to("192.168.50.10", run)
    assert route.interface == expected and bool(route.error) == error
    assert calls == [["ip", "-j", "-4", "route", "get", "192.168.50.10"]]


def test_neighbor_cache_preserves_duplicate_ips_and_never_stimulates(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("cache read sent traffic"))
    path = tmp_path / "arp"
    path.write_text(
        "IP address HW type Flags HW address Mask Device\n"
        "10.0.0.8 0x1 0x2 00:11:22:33:44:55 * eth0\n"
        "10.0.0.8 0x1 0x2 00:11:22:33:44:66 * wlan0\n"
    )
    assert neighbors.read_arp_table(path) == {
        ("eth0", "10.0.0.8"): "00:11:22:33:44:55",
        ("wlan0", "10.0.0.8"): "00:11:22:33:44:66",
    }


@pytest.fixture
def runtime(monkeypatch):
    path = (
        Path(__file__).resolve().parents[3]
        / "cyberwave-edge-core/cyberwave_edge_core/driver_selection.py"
    )
    spec = importlib.util.spec_from_file_location("runtime_selection_contract", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(module, "is_jetson", lambda: True)
    return module


@pytest.mark.parametrize(
    "child,expected", [(set(), "linux-aarch64-jetson"), ({"vendor/child"}, "vendor/child")]
)
def test_selection_uses_real_runtime_policy(runtime, child, expected):
    drivers = {
        name: {
            "params": ["-e", f"CYBERWAVE_METADATA_HOST=10.0.0.{i}"],
            "shared_env": {"ROBOT_IP": f"10.0.0.{i}"},
        }
        for i, name in enumerate(
            ["default", "linux-aarch64-jetson", "linux-x86_64", "vendor/child"], 1
        )
    }
    result = hardware.select_profile(
        drivers, child_registry_ids=child, resolver=runtime._matched_driver_profile
    )
    assert result.name == expected
    assert hardware.explicit_metadata_values(result) == {
        "host": drivers[expected]["shared_env"]["ROBOT_IP"]
    }
    references = hardware.AddressReference.from_selection(result)
    assert len(references) == 2 and all(r.profile == expected for r in references)
    assert len({r.ip_address for r in references}) == 1


def test_no_inheritance_from_inactive_default(runtime):
    drivers = {
        "default": {"params": ["-e", "CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://10.0.0.9/a"]},
        "linux-aarch64-jetson": {"shared_env": {"MQTT_HOST": "10.0.0.8"}},
    }
    result = hardware.select_profile(
        drivers, child_registry_ids=set(), resolver=runtime._matched_driver_profile
    )
    assert hardware.explicit_metadata_values(result) == {}
    assert hardware.driver_env_addresses(result) == {
        ("linux-aarch64-jetson", "MQTT_HOST"): "10.0.0.8"
    }


@pytest.mark.parametrize(
    "drivers,context",
    [
        ({"default": {}, "other": {}}, None),
        ({"default": "bad"}, set()),
        ({"default": {"services": {"camera": {}}}}, set()),
    ],
)
def test_unresolved_profiles_expose_no_candidates(drivers, context, runtime):
    result = hardware.select_profile(
        drivers, child_registry_ids=context, resolver=runtime._matched_driver_profile
    )
    assert result.unresolved
    assert (
        hardware.explicit_metadata_values(result) == {}
        and hardware.driver_env_addresses(result) == {}
    )


def test_runtime_fallback_and_no_match_are_distinct(runtime):
    fallback = hardware.select_profile({"default": {}}, resolver=runtime._matched_driver_profile)
    assert fallback.status == "resolved" and fallback.name == "default"
    missing = hardware.select_profile({"windows": {}}, resolver=runtime._matched_driver_profile)
    assert missing.status == "no-match"
    assert hardware.select_profile(None).status == "absent"


def test_standalone_cli_missing_runtime_is_explicit(monkeypatch):
    monkeypatch.setitem(sys.modules, "cyberwave_edge_core.driver_selection", None)
    result = hardware.select_profile({"default": {"shared_env": {"ROBOT_IP": "10.0.0.1"}}})
    assert result.unresolved and "resolver unavailable" in result.reason


@pytest.mark.parametrize(
    "params", [["--env-file", "/some/file"], ["-e", "ROBOT_IP"], ["--env=ROBOT_IP"], ["-e"], [42]]
)
def test_unsupported_environment_overrides_do_not_supply_endpoints(runtime, params):
    result = hardware.select_profile(
        {"default": {"params": params, "shared_env": {"ROBOT_IP": "10.0.0.8"}}},
        resolver=runtime._matched_driver_profile,
    )
    assert result.unresolved and not hardware.driver_env_addresses(result)


@pytest.mark.parametrize(
    "stderr,code,no_route",
    [
        ("RTNETLINK answers: Network is unreachable\n", 2, True),
        ("RTNETLINK answers: Operation not permitted\n", 2, False),
        ("RTNETLINK answers: Network is unreachable\n", 1, False),
        ("invalid argument", 1, False),
    ],
)
def test_route_command_distinguishes_unreachable_from_inspection_failure(stderr, code, no_route):
    def run(args, **kwargs):
        assert kwargs["env"]["LC_ALL"] == "C"
        raise subprocess.CalledProcessError(code, args, stderr=stderr)

    route = interfaces.route_to("192.168.50.10", run)
    assert route.interface is None
    assert bool(route.error) is not no_route


def test_resolver_bugs_are_not_reported_as_missing_runtime():
    def broken(drivers, *, child_registry_ids):
        raise RuntimeError("selector bug")

    with pytest.raises(RuntimeError, match="selector bug"):
        hardware.select_profile({"default": {}}, resolver=broken)


@pytest.mark.parametrize(
    "resolver", [lambda drivers: ("default", {}), lambda *a, **kw: {"default": {}}]
)
def test_incompatible_selector_is_unavailable(resolver):
    assert hardware.select_profile({"default": {}}, resolver=resolver).unresolved


@pytest.mark.parametrize("params", [{}, "", 0])
def test_malformed_empty_params_are_not_treated_as_no_overrides(params, runtime):
    selection = hardware.select_profile(
        {"default": {"params": params}}, resolver=runtime._matched_driver_profile
    )
    assert selection.unresolved and selection.reason == "malformed selected environment"
