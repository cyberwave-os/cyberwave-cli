from __future__ import annotations

import ipaddress
import json
import socket
import struct
import subprocess
import threading
import time

import pytest

from cyberwave_cli.discovery import hardware, interfaces, linkconfig, mdns, neighbors, probes

LINK_LOCAL = ipaddress.IPv4Interface("169.254.10.1/16")
SERVICE = "_acme-cam._tcp"
OUI = "00:11:22"
CAM_SPEC = {
    "mdns_services": [SERVICE],
    "mac_prefixes": [OUI],
    "tcp_ports": [554],
    "bind": {"video_device": "rtsp://{ip}/stream"},
}


def _netdev(
    root, name, *, device=True, kind="1", carrier=None, wireless=False, mac="02:00:00:00:00:01"
):
    path = root / name
    path.mkdir()
    if device:
        (path / "device").mkdir()
    (path / "type").write_text(kind)
    (path / "address").write_text(mac.upper())
    if carrier is not None:
        (path / "carrier").write_text(carrier)
    if wireless:
        (path / "wireless").mkdir()


def test_wired_interfaces_keeps_ethernet_hardware_only(tmp_path):
    _netdev(tmp_path, "eth0", carrier="1", mac="02:aa:bb:cc:dd:01")
    _netdev(tmp_path, "enx00e04c680001", carrier="0")
    _netdev(tmp_path, "wlan0", carrier="1", wireless=True)
    _netdev(tmp_path, "docker0", device=False, carrier="1")
    _netdev(tmp_path, "can0", kind="280")

    found = interfaces.wired_interfaces(tmp_path, {"eth0": [LINK_LOCAL]})

    assert [i.name for i in found] == ["enx00e04c680001", "eth0"]
    eth0 = found[1]
    assert eth0.carrier and eth0.mac == "02:aa:bb:cc:dd:01" and eth0.addresses == (LINK_LOCAL,)
    assert not found[0].carrier and not found[0].has_ipv4
    assert eth0.address_for("169.254.200.7") == LINK_LOCAL
    assert eth0.address_for("10.20.30.40") is None


def test_ipv4_addresses_parses_iproute2_json():
    output = json.dumps(
        [
            {
                "ifname": "eth0",
                "addr_info": [{"family": "inet", "local": "169.254.10.1", "prefixlen": 16}],
            },
            {
                "ifname": "wlan0",
                "addr_info": [{"family": "inet", "local": "192.168.88.127", "prefixlen": 24}],
            },
            {"ifname": "lo", "addr_info": []},
        ]
    )

    def run(args, **_kwargs):
        assert args == ["ip", "-j", "-4", "addr", "show"]
        return subprocess.CompletedProcess(args, 0, output, "")

    assert interfaces.ipv4_addresses(run) == {
        "eth0": [ipaddress.IPv4Interface("169.254.10.1/16")],
        "wlan0": [ipaddress.IPv4Interface("192.168.88.127/24")],
    }


def test_ipv4_addresses_without_iproute2_is_empty():
    def run(args, **_kwargs):
        raise FileNotFoundError("ip")

    assert interfaces.ipv4_addresses(run) == {}


def _name(*labels: str) -> bytes:
    return mdns.encode_name(labels)


def _rr(name: bytes, rtype: int, rdata: bytes, *, flush: bool = True) -> bytes:
    rclass = mdns.CLASS_IN | (0x8000 if flush else 0)
    return name + struct.pack("!HHIH", rtype, rclass, 120, len(rdata)) + rdata


INSTANCE = "ACME Cam v1.2 - 0001"


def _cam_response(ip: str = "169.254.79.239", *, with_a: bool = True) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 4 if with_a else 3)
    service = _name("_acme-cam", "_tcp", "local")  # first name in the message: offset 12
    instance = bytes([len(INSTANCE)]) + INSTANCE.encode() + b"\xc0\x0c"
    answer = _rr(service, mdns.TYPE_PTR, instance, flush=False)
    instance_ptr = struct.pack("!H", 0xC000 | (12 + len(service) + 10))
    host = _name("acme-cam-0001", "local")
    srv = _rr(instance_ptr, mdns.TYPE_SRV, struct.pack("!HHH", 0, 0, 9000) + host)
    txt = _rr(instance_ptr, mdns.TYPE_TXT, b"\x08model=X1\x06fw=1.0\x05ready")
    records = answer + srv + txt
    if with_a:
        records += _rr(host, mdns.TYPE_A, socket.inet_aton(ip))
    return header + records


def test_parse_message_decodes_compressed_dns_sd_records():
    kinds = {r.rtype: r for r in mdns.parse_message(_cam_response())}
    assert kinds[mdns.TYPE_PTR].data == (INSTANCE, "_acme-cam", "_tcp", "local")
    assert kinds[mdns.TYPE_SRV].data == (0, 0, 9000, ("acme-cam-0001", "local"))
    assert kinds[mdns.TYPE_TXT].data == (b"model=X1", b"fw=1.0", b"ready")
    assert kinds[mdns.TYPE_A].data == "169.254.79.239"


def test_parse_message_ignores_queries_and_survives_garbage():
    assert mdns.parse_message(mdns.build_query([(("_x", "_tcp", "local"), mdns.TYPE_PTR)])) == []
    looped = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c"  # points at itself
    assert mdns.parse_message(looped) == []
    assert mdns.parse_message(_cam_response()[:-3])
    overrun = (
        struct.pack("!HHHHHH", 0, 0x8400, 0, 2, 0, 0)
        + _rr(_name("x", "local"), mdns.TYPE_TXT, b"\x30a=1")  # claims 48 bytes, has 3
        + _rr(_name("y", "local"), mdns.TYPE_A, socket.inet_aton("10.0.0.1"))
    )
    txt, address = mdns.parse_message(overrun)
    assert txt.data == (b"a=1",) and address.data == "10.0.0.1"


class _Responder:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.queries: list[bytes] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        while not self.stop.is_set():
            try:
                data, source = self.sock.recvfrom(9000)
            except OSError:
                continue
            self.queries.append(data)
            self.sock.sendto(self.payload, source)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.stop.set()
        self.thread.join()
        self.sock.close()


def _browse(responder, types):
    return mdns.browse(
        types,
        interface_name="lo",
        interface_ip="127.0.0.1",
        timeout=0.6,
        destination=("127.0.0.1", responder.port),
        multicast=False,
    )


def test_browse_resolves_a_service_from_a_legacy_unicast_reply():
    with _Responder(_cam_response()) as responder:
        result = _browse(responder, [f"{SERVICE}.local."])
    [service] = result.services
    assert service.service_type == SERVICE
    assert service.instance == f"{INSTANCE}.{SERVICE}.local"
    assert (service.host, service.port, service.addresses) == (
        "acme-cam-0001.local",
        9000,
        ["169.254.79.239"],
    )
    assert service.txt == {"model": "X1", "fw": "1.0", "ready": ""}
    assert struct.unpack_from("!H", responder.queries[0], 4)[0] == 1


def test_browse_falls_back_to_the_responders_address_without_an_a_record():
    with _Responder(_cam_response(with_a=False)) as responder:
        result = _browse(responder, [SERVICE])
    assert result.services[0].addresses == ["127.0.0.1"]


def test_browse_with_nothing_to_look_for_sends_nothing():
    result = mdns.browse([], interface_name="lo", interface_ip="127.0.0.1", timeout=5)
    assert result.services == [] and result.service_types == set()


def test_read_arp_table_keeps_complete_entries(tmp_path):
    table = tmp_path / "arp"
    table.write_text(
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "169.254.79.239   0x1         0x2         00:11:22:33:44:55     *        eth0\n"
        "169.254.79.240   0x1         0x0         00:00:00:00:00:00     *        eth0\n"
        "192.168.88.1     0x1         0x2         AA:BB:CC:DD:EE:FF     *        wlan0\n"
    )
    assert neighbors.read_arp_table(table) == {
        "169.254.79.239": ("00:11:22:33:44:55", "eth0"),
        "192.168.88.1": ("aa:bb:cc:dd:ee:ff", "wlan0"),
    }


def test_resolve_macs_only_reports_the_requested_interface(tmp_path, monkeypatch):
    table = tmp_path / "arp"
    table.write_text(
        "IP address HW type Flags HW address Mask Device\n"
        "169.254.79.239 0x1 0x2 00:11:22:33:44:55 * eth0\n"
        "10.0.0.9 0x1 0x2 00:11:22:00:00:09 * eth1\n"
    )
    poked: list[str] = []
    monkeypatch.setattr(neighbors, "_poke", lambda ips: poked.extend(ips))
    found = neighbors.resolve_macs(
        ["169.254.79.239", "10.0.0.9"], interface="eth0", timeout=0, table_path=table
    )
    assert found == {"169.254.79.239": "00:11:22:33:44:55"}
    assert poked == ["169.254.79.239", "10.0.0.9"]


def test_sweep_covers_small_subnets_and_skips_wide_ones():
    assert len(neighbors.sweep_hosts(ipaddress.IPv4Interface("10.20.30.200/24"))) == 253
    assert neighbors.sweep_hosts(LINK_LOCAL) == []


@pytest.mark.parametrize(
    ("mac", "prefix", "expected"),
    [
        ("00:11:22:33:44:55", "00:11:22", True),
        ("00-11-22-33-44-55", "001122", True),
        ("00:aa:bb:00:00:01", "00:11:22", False),
        ("00:11:22:33:44:55", "00:11", False),
    ],
)
def test_mac_prefix_matching(mac, prefix, expected):
    assert neighbors.mac_has_prefix(mac, prefix) is expected


def test_mac_helpers_normalise_and_validate():
    assert neighbors.normalize_mac("001122334455") == "00:11:22:33:44:55"
    assert neighbors.normalize_mac("not a mac") == ""
    assert neighbors.is_mac_prefix("00:11:22") and neighbors.is_mac_prefix("00:11:22:33")
    assert not neighbors.is_mac_prefix("00:1") and not neighbors.is_mac_prefix("zz:11:22")


@pytest.mark.parametrize("line", ["001122 ACME Corp", "00-11-22   (hex)\t\tACME Corp"])
def test_vendor_lookup_reads_nmap_and_ieee_databases(tmp_path, monkeypatch, line):
    database = tmp_path / "oui"
    database.write_text(f"# header\n{line}\n")
    monkeypatch.setattr(neighbors, "_OUI_DATABASES", (tmp_path / "missing", database))
    neighbors._vendor_table.cache_clear()
    try:
        assert neighbors.vendor_for("00:11:22:33:44:55") == "ACME Corp"
        assert neighbors.vendor_for("02:00:00:00:00:01") is None
    finally:
        neighbors._vendor_table.cache_clear()


def _tcp_server(reply: bytes):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    received: list[bytes] = []

    def serve():
        conn, _ = server.accept()
        with conn:
            received.append(conn.recv(4096))
            conn.sendall(reply)
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1], received


def test_rtsp_status_sends_describe_for_the_path():
    port, received = _tcp_server(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n")
    assert probes.rtsp_status(f"rtsp://127.0.0.1:{port}/stream", timeout=2) == 200
    assert received[0].startswith(f"DESCRIBE rtsp://127.0.0.1:{port}/stream RTSP/1.0\r\n".encode())


def test_rtsp_status_never_puts_credentials_on_the_wire():
    port, received = _tcp_server(b"RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\n\r\n")
    url = f"rtsp://admin:s3cret@127.0.0.1:{port}/stream"
    assert probes.rtsp_status(url, timeout=2) == 401
    assert received[0].startswith(f"DESCRIBE rtsp://127.0.0.1:{port}/stream RTSP/1.0\r\n".encode())
    assert b"s3cret" not in received[0]


def test_rtsps_is_not_probed_over_plain_tcp():
    port, received = _tcp_server(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n")
    assert probes.rtsp_status(f"rtsps://127.0.0.1:{port}/stream", timeout=1) is None
    assert received == []


def test_a_scalar_tcp_port_is_read_as_one_port():
    spec = hardware.DiscoverySpec.from_metadata(
        {
            "mdns_services": ["_acme._tcp"],
            "bind": {"video_device": "rtsp://{ip}/s"},
            "tcp_ports": 554,
        }
    )
    assert spec is not None and spec.tcp_ports == (554,)


def test_a_name_from_a_malformed_packet_does_not_stop_the_query():
    query = mdns.build_query([(("bad", "", "local"), mdns.TYPE_PTR), (("_x", "_tcp", "local"), 12)])
    assert struct.unpack("!HHHHHH", query[:12])[2] == 1


def test_rtsp_status_is_none_when_nothing_listens():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert probes.rtsp_status(f"rtsp://127.0.0.1:{port}/stream", timeout=1) is None
    assert probes.rtsp_status("http://127.0.0.1/stream") is None


def test_spec_from_metadata_parses_the_documented_block():
    spec = hardware.DiscoverySpec.from_metadata(
        {
            **CAM_SPEC,
            "subnet_prefix": 16,
            "host_address": "10.20.0.250/16",
            "link_local": True,
            "timeout": 4,
        }
    )
    assert spec == hardware.DiscoverySpec(
        bind=(("video_device", "rtsp://{ip}/stream"),),
        methods=frozenset({"mdns"}),
        mdns_services=(SERVICE,),
        mac_prefixes=(OUI,),
        subnet_prefix=16,
        host_address=ipaddress.IPv4Interface("10.20.0.250/16"),
        link_local=True,
        tcp_ports=(554,),
        timeout=4.0,
    )
    assert spec.uses_mdns and not spec.uses_arp


def test_the_arp_sweep_runs_only_for_twins_that_list_it():
    without = hardware.DiscoverySpec.from_metadata({"mac_prefixes": [OUI], "bind": {"h": "{ip}"}})
    assert without is None
    spec = hardware.DiscoverySpec.from_metadata(
        {"methods": ["arp"], "mac_prefixes": [OUI], "bind": {"h": "{ip}"}}
    )
    assert spec.uses_arp and not spec.uses_mdns
    both = hardware.DiscoverySpec.from_metadata({**CAM_SPEC, "methods": ["mdns", "ARP", "bogus"]})
    assert both.methods == frozenset({"mdns", "arp"})


def test_explicit_metadata_values_reads_driver_env_params_like_edge_core():
    drivers = {
        "default": {
            "params": [
                "-e",
                "CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://10.20.30.3/stream",
                "--env=CYBERWAVE_METADATA_RESOLUTION=640x480",
                "-e",
                "OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp",
                "-e",
                "CYBERWAVE_METADATA_=ignored",
                "-v",
                "/dev:/dev",
            ],
            "docker_image": "example/driver",
        },
        "gpu": "not a driver entry",
    }
    assert hardware.explicit_metadata_values(drivers) == {
        "video_device": "rtsp://10.20.30.3/stream",
        "resolution": "640x480",
    }
    assert hardware.explicit_metadata_values(None) == {}


@pytest.mark.parametrize(
    ("value", "ip"),
    [
        ("rtsp://10.20.30.3/stream", "10.20.30.3"),
        ("rtsp://user:secret@10.20.30.3:8554/stream?x=1", "10.20.30.3"),
        ("10.20.30.3", "10.20.30.3"),
        ("rtsp://192.168.X.X/stream", None),
        ("rtsp://camera.local/stream", None),
        ("/dev/video0", None),
        ("", None),
    ],
)
def test_ip_from_value(value, ip):
    assert hardware.ip_from_value(value) == ip


def test_a_driver_param_for_a_bind_field_fixes_the_source():
    explicit = {"video_device": "rtsp://10.20.30.3/stream"}
    spec = hardware.DiscoverySpec.from_metadata(
        {"bind": {"video_device": "rtsp://{ip}/stream"}}, explicit=explicit
    )
    assert (spec.fixed_field, spec.fixed_value, spec.ip_address) == (
        "video_device",
        "rtsp://10.20.30.3/stream",
        "10.20.30.3",
    )
    assert spec.methods == frozenset()
    assert spec.subnet_of("10.20.30.3") == ipaddress.IPv4Network("10.20.30.0/24")
    with_host = hardware.DiscoverySpec.from_metadata(
        {"host_address": "10.20.0.9/16", "bind": {"video_device": "{ip}"}}, explicit=explicit
    )
    assert with_host.subnet_of("10.20.30.3") == ipaddress.IPv4Network("10.20.0.0/16")


def test_a_driver_param_for_another_field_fixes_nothing():
    spec = hardware.DiscoverySpec.from_metadata(CAM_SPEC, explicit={"resolution": "640x480"})
    assert spec.fixed_field is None and spec.ip_address is None


def test_a_placeholder_source_is_fixed_but_names_no_ip():
    spec = hardware.DiscoverySpec.from_metadata(
        CAM_SPEC, explicit={"video_device": "rtsp://192.168.X.X/stream"}
    )
    assert spec.fixed_field == "video_device" and spec.ip_address is None


def test_twin_blocks_merge_over_the_asset_block():
    asset = {**CAM_SPEC, "bind": {"video_device": "rtsp://{ip}/stream", "host": "{ip}"}}
    twin = {"subnet_prefix": 16, "bind": {"video_device": "rtsp://{ip}/other"}}
    spec = hardware.DiscoverySpec.from_metadata(asset, twin)
    assert spec.subnet_prefix == 16 and spec.mdns_services == (SERVICE,)
    assert dict(spec.bind) == {"video_device": "rtsp://{ip}/other", "host": "{ip}"}


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not a dict",
        {"bind": {"video_device": "rtsp://{ip}/stream"}},
        {"mdns_services": [SERVICE]},
        {"mdns_services": [SERVICE], "bind": {"host": "{address}"}},
        {"ip_address": "10.20.30.3", "bind": {"host": "{ip}"}},  # not a key: use a driver param
        {"methods": [], "mdns_services": [SERVICE], "bind": {"host": "{ip}"}},
    ],
)
def test_spec_from_metadata_rejects_unusable_blocks(raw):
    assert hardware.DiscoverySpec.from_metadata(raw) is None


def test_spec_accepts_a_single_string_and_drops_bad_entries():
    spec = hardware.DiscoverySpec.from_metadata(
        {
            "mdns_services": "_ACME-cam._tcp.local.",
            "mac_prefixes": [OUI, "junk"],
            "tcp_ports": [554, True, 0],
            "subnet_prefix": 99,
            "timeout": "slow",
            "bind": {"host": "{ip}", "bad": "{ip"},
        }
    )
    assert spec.mdns_services == (SERVICE,)
    assert spec.mac_prefixes == (OUI,)
    assert spec.tcp_ports == (554,)
    assert spec.subnet_prefix == 24 and spec.timeout == 2.5
    assert spec.bind == (("host", "{ip}"),)


def _cam_device(ip="169.254.79.239", mac="00:11:22:33:44:55", *, mdns_seen=True, reachable=True):
    services = {}
    if mdns_seen:
        services[SERVICE] = mdns.MdnsService(
            instance=f"{INSTANCE}.{SERVICE}.local",
            service_type=SERVICE,
            host="acme-cam-0001.local",
            port=9000,
            addresses=[ip],
        )
    return hardware.NetworkDevice(
        ip=ip, interface="eth0", mac=mac, vendor="ACME Corp", reachable=reachable, services=services
    )


def test_match_score_weights_mdns_over_the_mac_prefix():
    spec = hardware.DiscoverySpec.from_metadata(CAM_SPEC)
    assert (
        hardware.match_score(spec, _cam_device()) == hardware.SCORE_MDNS + hardware.SCORE_MAC_PREFIX
    )
    assert hardware.match_score(spec, _cam_device(mdns_seen=False)) == hardware.SCORE_MAC_PREFIX
    assert hardware.match_score(spec, _cam_device(mac="02:00:00:00:00:01", mdns_seen=False)) == 0


def test_render_bind_fills_every_template_field():
    spec = hardware.DiscoverySpec.from_metadata(
        {
            "mdns_services": [SERVICE],
            "bind": {
                "video_device": "rtsp://{ip}/stream",
                "api": "{hostname}:{port}",
                "id": "{mac}@{interface}",
            },
        }
    )
    device = _cam_device()
    device.hostname = "acme-cam-0001"
    assert hardware.render_bind(spec, device) == {
        "video_device": "rtsp://169.254.79.239/stream",
        "api": "acme-cam-0001:9000",
        "id": "00:11:22:33:44:55@eth0",
    }


def test_name_patterns_narrow_a_service_type_many_devices_share():
    spec = hardware.DiscoverySpec.from_metadata(
        {**CAM_SPEC, "name_patterns": ["acme cam*", "other-*"]}
    )
    assert hardware.match_score(spec, _cam_device()) > 0
    stranger = _cam_device()
    stranger.services[SERVICE].instance = f"Lobby camera.{SERVICE}.local"
    assert hardware.match_score(spec, stranger) == 0
    by_host = _cam_device(mdns_seen=False)
    by_host.hostname = "other-unit"
    assert hardware.match_score(spec, by_host) > 0
    assert hardware.match_score(spec, _cam_device(mdns_seen=False)) == 0


def test_the_ports_method_matches_a_host_by_its_open_ports():
    spec = hardware.DiscoverySpec.from_metadata(
        {"methods": ["ports"], "tcp_ports": [502], "bind": {"host": "{ip}:{port}"}}
    )
    assert spec is not None and spec.uses_ports and spec.sweeps and spec.searchable
    plc = hardware.NetworkDevice(ip="192.168.0.20", interface="eth0", mac="02:00:00:00:00:20")
    assert hardware.match_score(spec, plc) == 0
    plc.open_ports = frozenset({80, 502})
    assert hardware.match_score(spec, plc) == hardware.SCORE_PORTS
    assert hardware.render_bind(spec, plc) == {"host": "192.168.0.20:502"}


def test_ports_alone_are_only_checked_after_binding():
    spec = hardware.DiscoverySpec.from_metadata(CAM_SPEC)  # tcp_ports, but not the method
    assert not spec.uses_ports and not spec.sweeps
    assert hardware.match_score(spec, _cam_device(mdns_seen=False, mac="02:00:00:00:00:01")) == 0
    no_ports = {"methods": ["ports"], "bind": {"a": "{ip}"}}
    assert hardware.DiscoverySpec.from_metadata(no_ports) is None


def test_txt_records_of_the_matched_service_fill_templates():
    spec = hardware.DiscoverySpec.from_metadata(
        {"mdns_services": [SERVICE], "bind": {"url": "http://{ip}:{port}{txt_path}{txt_none}"}}
    )
    device = _cam_device()
    device.services[SERVICE].txt = {"Path": "/api/v1"}
    assert hardware.render_bind(spec, device) == {"url": "http://169.254.79.239:9000/api/v1"}


def test_discover_probes_the_requested_ports_on_hosts_that_answered(fake_network, monkeypatch):
    tried = []

    def tcp_port_open(ip, port, timeout=1.5):
        tried.append((ip, port))
        return port == 502

    monkeypatch.setattr(hardware.probes, "tcp_port_open", tcp_port_open)
    found = hardware.discover([ETH0], arp_sweep=True, probe_ports=[502, 80])

    assert sorted(tried) == [("169.254.1.50", 80), ("169.254.1.50", 502)]
    [plc] = found
    assert plc.open_ports == frozenset({502})


def test_verify_binding_reports_a_wrong_stream_path(monkeypatch):
    spec = hardware.DiscoverySpec.from_metadata(CAM_SPEC)
    values = {"video_device": "rtsp://169.254.79.239/stream"}
    monkeypatch.setattr(hardware.probes, "tcp_port_open", lambda ip, port: True)
    monkeypatch.setattr(hardware.probes, "rtsp_status", lambda url: 404)
    assert hardware.verify_binding(spec, _cam_device(), values) == [
        "video_device rtsp://169.254.79.239/stream gave RTSP 404"
    ]
    monkeypatch.setattr(hardware.probes, "rtsp_status", lambda url: 401)
    assert hardware.verify_binding(spec, _cam_device(), values) == []


@pytest.fixture
def fake_network(monkeypatch):
    calls = {"browse": [], "sweep": [], "resolve": []}
    announced = [
        mdns.MdnsService(f"A.{SERVICE}.local", SERVICE, "cam-a.local", 9000, ["169.254.79.239"]),
        mdns.MdnsService(f"B.{SERVICE}.local", SERVICE, "cam-b.local", 9000, ["10.20.30.3"]),
        mdns.MdnsService(f"Self.{SERVICE}.local", SERVICE, "edge.local", 9, ["169.254.10.1"]),
    ]

    def browse(types, **kwargs):
        calls["browse"].append((tuple(types), kwargs["interface_name"], kwargs["interface_ip"]))
        return mdns.BrowseResult(announced, set())

    def sweep(address, interface):
        calls["sweep"].append(str(address))
        return {"169.254.1.50": "00:11:22:00:00:50"}

    def resolve(ips, *, interface):
        calls["resolve"].append(list(ips))
        return {ip: "00:11:22:33:44:55" for ip in ips}

    monkeypatch.setattr(hardware.mdns, "browse", browse)
    monkeypatch.setattr(hardware.neighbors, "arp_sweep", sweep)
    monkeypatch.setattr(hardware.neighbors, "resolve_macs", resolve)
    monkeypatch.setattr(hardware.neighbors, "vendor_for", lambda mac: "ACME Corp")
    return calls


ETH0 = interfaces.WiredInterface("eth0", "02:aa:bb:cc:dd:01", True, (LINK_LOCAL,))
ETH1_DOWN = interfaces.WiredInterface("eth1", "02:aa:bb:cc:dd:02", False, ())


def test_discover_merges_mdns_hits_and_flags_unreachable_devices(fake_network):
    found = hardware.discover([ETH0, ETH1_DOWN], mdns_services=[SERVICE], timeout=0.1)

    assert fake_network["browse"] == [((SERVICE,), "eth0", "169.254.10.1")]
    assert fake_network["sweep"] == []
    assert fake_network["resolve"] == [["169.254.79.239"]]
    by_ip = {d.ip: d for d in found}
    assert set(by_ip) == {"169.254.79.239", "10.20.30.3"}
    near, far = by_ip["169.254.79.239"], by_ip["10.20.30.3"]
    assert (near.ip, near.mac, near.reachable, near.hostname) == (
        "169.254.79.239",
        "00:11:22:33:44:55",
        True,
        "cam-a",
    )
    assert (far.ip, far.mac, far.reachable) == ("10.20.30.3", "", False)


def test_discover_sweeps_only_when_asked_and_includes_fixed_targets(fake_network):
    found = hardware.discover([ETH0], arp_sweep=True, targets=["169.254.7.7", "10.9.9.9"])

    assert fake_network["browse"] == []
    assert fake_network["sweep"] == ["169.254.10.1/16"]
    assert {d.ip: d.mac for d in found} == {
        "169.254.1.50": "00:11:22:00:00:50",
        "169.254.7.7": "00:11:22:33:44:55",
    }  # 10.9.9.9 is on no port's subnet, so it cannot be looked up


class _Nmcli:
    def __init__(self, profiles=None, reject=(), active=None):
        self.calls: list[list[str]] = []
        self.profiles = dict(profiles or {})
        self.reject = reject
        self.active = active

    def __call__(self, args, **_kwargs):
        argv = args[1:]
        self.calls.append(argv)
        if argv[:3] == ["-t", "-f", "NAME"]:
            return subprocess.CompletedProcess(args, 0, "\n".join(self.profiles) + "\n", "")
        if argv[:2] == ["-g", "ipv4.method"]:
            return subprocess.CompletedProcess(args, 0, self.profiles[argv[-1]] + "\n", "")
        if argv[:2] == ["-g", "GENERAL.CONNECTION"]:
            return subprocess.CompletedProcess(args, 0, (self.active or "") + "\n", "")
        if any(word in argv for word in self.reject):
            raise subprocess.CalledProcessError(2, args, "", "Error: invalid property")
        return subprocess.CompletedProcess(args, 0, "", "")

    def changes(self):
        return [c for c in self.calls if c[:2] != ["-t", "-f"] and c[:1] != ["-g"]]


def _settings(argv):
    start = next(i for i, word in enumerate(argv) if "." in word)
    pairs = argv[start:]
    return dict(zip(pairs[::2], pairs[1::2]))


def test_an_idle_port_gets_dhcp_and_link_local_side_by_side_and_no_default_route():
    nmcli = _Nmcli(profiles={"uplink": "auto"})
    assert linkconfig.configure("eth0", link_local=True, run=nmcli) == "cyberwave-eth0"
    add, up = nmcli.changes()
    assert add[:7] == ["connection", "add", "type", "ethernet", "ifname", "eth0", "con-name"]
    settings = _settings(add)
    assert settings["ipv4.method"] == "auto" and settings["ipv4.link-local"] == "enabled"
    assert settings["ipv4.dhcp-timeout"] == "2147483647"
    assert settings["ipv4.never-default"] == "yes"
    assert up == ["connection", "up", "cyberwave-eth0"]


def test_static_addresses_join_dhcp_and_link_local_on_the_cyberwave_profile():
    nmcli = _Nmcli(profiles={"cyberwave-eth0": "link-local"})
    address = ipaddress.IPv4Interface("10.20.30.250/24")
    linkconfig.configure("eth0", addresses=[address], run=nmcli)
    modify, _up = nmcli.changes()
    assert modify[:3] == ["connection", "modify", "cyberwave-eth0"]
    settings = _settings(modify)
    assert settings["+ipv4.addresses"] == "10.20.30.250/24"
    assert settings["ipv4.method"] == "auto" and settings["ipv4.link-local"] == "enabled"


def test_configure_falls_back_when_networkmanager_cannot_keep_link_local():
    nmcli = _Nmcli(reject=("ipv4.link-local",))
    linkconfig.configure(
        "eth0", link_local=True, addresses=[ipaddress.IPv4Interface("10.20.30.250/24")], run=nmcli
    )
    first, retry, up = nmcli.changes()
    assert "ipv4.link-local" in first and "ipv4.link-local" not in retry
    assert "10.20.30.250/24" in retry and up[:2] == ["connection", "up"]


def test_link_local_alone_falls_back_to_a_link_local_profile_before_networkmanager_1_40():
    nmcli = _Nmcli(reject=("ipv4.link-local",))
    linkconfig.configure("eth0", link_local=True, run=nmcli)
    _first, retry, _up = nmcli.changes()
    assert _settings(retry)["ipv4.method"] == "link-local"


def test_an_earlier_static_cyberwave_profile_also_gets_dhcp_and_link_local():
    nmcli = _Nmcli(profiles={"cyberwave-eth0": "manual"})
    linkconfig.configure("eth0", link_local=True, run=nmcli)
    modify, _up = nmcli.changes()
    assert modify[:3] == ["connection", "modify", "cyberwave-eth0"]
    assert _settings(modify)["ipv4.method"] == "auto"
    assert _settings(modify)["ipv4.link-local"] == "enabled"


def test_link_local_joins_the_profile_already_running_the_port():
    nmcli = _Nmcli(profiles={"Wired connection 1": "auto"}, active="Wired connection 1")

    assert linkconfig.configure("eth0", link_local=True, run=nmcli) == "Wired connection 1"

    assert nmcli.changes() == [
        ["connection", "modify", "Wired connection 1", "ipv4.link-local", "enabled"],
        ["device", "reapply", "eth0"],
    ]


def test_a_running_profile_with_ipv4_disabled_is_reported_not_changed():
    nmcli = _Nmcli(profiles={"lab": "disabled"}, active="lab")
    with pytest.raises(linkconfig.LinkConfigError, match="IPv4 disabled"):
        linkconfig.configure("eth0", link_local=True, run=nmcli)
    assert nmcli.changes() == []


def test_the_cyberwave_profile_running_the_port_is_updated_and_reactivated():
    nmcli = _Nmcli(profiles={"cyberwave-eth0": "link-local"}, active="cyberwave-eth0")
    address = ipaddress.IPv4Interface("10.20.30.250/24")
    assert linkconfig.configure("eth0", addresses=[address], run=nmcli) == "cyberwave-eth0"
    modify, up = nmcli.changes()
    assert modify[:3] == ["connection", "modify", "cyberwave-eth0"]
    assert up == ["connection", "up", "cyberwave-eth0"]


def test_a_link_local_only_cyberwave_profile_is_upgraded_to_take_dhcp_too():
    nmcli = _Nmcli(profiles={"cyberwave-eth0": "link-local"}, active="cyberwave-eth0")
    assert linkconfig.adopt_dhcp("eth0", run=nmcli) == "cyberwave-eth0"
    modify, up = nmcli.changes()
    assert _settings(modify) == {
        "ipv4.method": "auto",
        "ipv4.dhcp-timeout": "2147483647",
        "ipv4.link-local": "enabled",
    }
    assert up == ["connection", "up", "cyberwave-eth0"]


@pytest.mark.parametrize(
    ("profiles", "active"),
    [
        ({"Wired connection 1": "link-local"}, "Wired connection 1"),  # not ours
        ({"cyberwave-eth0": "auto"}, "cyberwave-eth0"),  # already takes DHCP
        ({"cyberwave-eth0": "link-local"}, None),  # not running the port
    ],
)
def test_adopt_dhcp_leaves_other_profiles_alone(profiles, active):
    nmcli = _Nmcli(profiles=profiles, active=active)
    assert linkconfig.adopt_dhcp("eth0", run=nmcli) is None
    assert nmcli.changes() == []


def test_adopt_dhcp_keeps_the_profile_when_networkmanager_is_older_than_1_40():
    nmcli = _Nmcli(
        profiles={"cyberwave-eth0": "link-local"},
        active="cyberwave-eth0",
        reject=("ipv4.link-local",),
    )
    assert linkconfig.adopt_dhcp("eth0", run=nmcli) is None
    assert [c[:2] for c in nmcli.changes()] == [["connection", "modify"]]


def test_addresses_join_the_profile_already_running_the_port():
    nmcli = _Nmcli(profiles={"Wired connection 1": "auto"}, active="Wired connection 1")
    address = ipaddress.IPv4Interface("192.168.0.250/24")

    assert linkconfig.configure("eth0", addresses=[address], run=nmcli) == "Wired connection 1"

    assert nmcli.changes() == [
        ["connection", "modify", "Wired connection 1", "+ipv4.addresses", "192.168.0.250/24"],
        ["device", "reapply", "eth0"],
    ]


def test_a_profile_that_fails_to_come_up_is_deleted():
    nmcli = _Nmcli(reject=("up",))
    with pytest.raises(linkconfig.LinkConfigError):
        linkconfig.configure(
            "eth0", addresses=[ipaddress.IPv4Interface("10.20.30.250/24")], run=nmcli
        )
    assert nmcli.changes()[-1] == ["connection", "delete", "cyberwave-eth0"]


def test_an_idle_port_still_gets_its_own_profile():
    nmcli = _Nmcli(active=None)
    linkconfig.configure("eth0", addresses=[ipaddress.IPv4Interface("10.20.30.250/24")], run=nmcli)
    add, up = nmcli.changes()
    assert add[:2] == ["connection", "add"] and up == ["connection", "up", "cyberwave-eth0"]


def test_configure_needs_something_to_do_and_reports_nmcli_errors():
    with pytest.raises(ValueError):
        linkconfig.configure("eth0", run=_Nmcli())

    def refuse(args, **_kwargs):
        raise subprocess.CalledProcessError(
            4, args, "", "Error: Not authorized to control networking."
        )

    with pytest.raises(linkconfig.LinkConfigError, match="Not authorized"):
        linkconfig.configure("eth0", link_local=True, run=refuse)
    assert linkconfig.networkmanager_running(run=refuse) is False


def test_pick_free_address_avoids_taken_and_reserved_top_addresses():
    network = ipaddress.IPv4Network("10.20.30.0/24")
    assert linkconfig.pick_free_address(network, {"10.20.30.3"}) == ipaddress.IPv4Interface(
        "10.20.30.250/24"
    )
    assert linkconfig.pick_free_address(network, {"10.20.30.250"}) == ipaddress.IPv4Interface(
        "10.20.30.249/24"
    )
    assert linkconfig.pick_free_address(network, (), reserved_top=0) == ipaddress.IPv4Interface(
        "10.20.30.254/24"
    )


def _listed_pick(network, taken, reserved_top=4):
    """Reference that lists every host; pick_free_address must return the same address."""
    hosts = [h for h in network.hosts() if str(h) not in set(taken)]
    if not hosts:
        return None
    top = network.broadcast_address if network.prefixlen < 31 else network.network_address
    preferred = [h for h in reversed(hosts) if int(top) - int(h) > reserved_top]
    host = (preferred or list(reversed(hosts)))[0]
    return ipaddress.IPv4Interface(f"{host}/{network.prefixlen}")


@pytest.mark.parametrize("prefix", [24, 28, 29, 30, 31, 32])
@pytest.mark.parametrize("reserved_top", [0, 4, 10])
def test_pick_free_address_matches_the_listing_it_replaced(prefix, reserved_top):
    network = ipaddress.IPv4Network(f"10.20.30.0/{prefix}", strict=False)
    hosts = [str(h) for h in network.hosts()]
    for taken in ([], hosts[-6:], hosts[::2], hosts):
        assert linkconfig.pick_free_address(
            network, taken, reserved_top=reserved_top
        ) == _listed_pick(network, taken, reserved_top)


def test_pick_free_address_does_not_list_a_wide_subnet():
    start = time.monotonic()
    picked = linkconfig.pick_free_address(ipaddress.IPv4Network("10.0.0.0/8"), {"10.0.0.1"})
    assert picked == ipaddress.IPv4Interface("10.255.255.250/8")
    assert time.monotonic() - start < 1.0


def test_wait_for_ipv4_returns_once_the_address_appears():
    states = iter([{}, {"eth0": [LINK_LOCAL]}])
    assert linkconfig.wait_for_ipv4("eth0", timeout=5, addresses=lambda: next(states)) == [
        LINK_LOCAL
    ]
    assert linkconfig.wait_for_ipv4("eth0", timeout=0, addresses=dict) == []


def test_scan_json_is_plain_json_whatever_the_devices_announce(monkeypatch, capsys):
    import importlib

    scan = importlib.import_module("cyberwave_cli.commands.scan")
    service = mdns.MdnsService("[/b] cam._acme._tcp.local", "_acme._tcp", "cam.local", 554, [])
    device = hardware.NetworkDevice(
        ip="169.254.1.2",
        interface="eth0",
        mac="00:11:22:33:44:55",
        services={"_acme._tcp": service},
    )
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda: [])
    monkeypatch.setattr(hardware, "discover", lambda *a, **k: [device])

    scan._scan_wired(timeout=0.1, output_json=True)

    data = json.loads(capsys.readouterr().out)
    assert data["devices"][0]["services"]["_acme._tcp"]["instance"].startswith("[/b] cam")
