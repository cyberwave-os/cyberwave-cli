from __future__ import annotations

import hashlib
import importlib
import json
import ssl
import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from cyberwave_cli.discovery import hardware, interfaces, probes


class Socket:
    def __init__(self, *, fail=None):
        self.calls = []
        self.closed = False
        self.fail = fail
        self.timeouts = []
        self.options = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def setsockopt(self, *args):
        self.options.append(args)

    def connect(self, endpoint):
        self.calls.append(endpoint)
        if self.fail:
            raise self.fail

    def getsockname(self):
        return ("10.0.0.2", 50000)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_socket(monkeypatch):
    sock = Socket()
    monkeypatch.setattr(probes.socket, "socket", lambda *a, **k: sock)
    monkeypatch.setattr(probes.socket, "getaddrinfo", lambda *a, **k: pytest.fail("DNS lookup"))
    return sock


def test_single_exact_tcp_attempt_and_distinct_identity(fake_socket):
    result = probes.verify_endpoint("10.0.0.8", 554)
    assert fake_socket.calls == [("10.0.0.8", 554)]
    assert fake_socket.closed and 0 < fake_socket.timeouts[0] <= 5
    assert result.service_responded and result.identity == "unverified"
    assert not result.tls_server_authenticated and not result.device_credential_matched
    assert result.local_address == ("10.0.0.2", 50000)


@pytest.mark.parametrize(
    "failure", [ConnectionRefusedError("refused"), TimeoutError("timeout"), KeyboardInterrupt()]
)
def test_failure_closes_without_retry(fake_socket, failure):
    fake_socket.fail = failure
    result = probes.verify_endpoint("10.0.0.8", 554)
    assert len(fake_socket.calls) == 1 and fake_socket.closed and result.error
    assert not result.service_responded


@pytest.mark.parametrize(
    "target",
    ["camera.local", "10.0.0.0/24", "10.0.0.1-9", "224.0.0.251", "255.255.255.255", "0.0.0.0"],
)
def test_invalid_targets_rejected_before_socket(target, monkeypatch):
    monkeypatch.setattr(probes.socket, "socket", lambda *a: pytest.fail("socket created"))
    with pytest.raises(ValueError):
        probes.verify_endpoint(target, 554)


def test_selected_interface_is_enforced_before_connect(fake_socket, monkeypatch):
    monkeypatch.setattr(probes.sys, "platform", "linux")
    monkeypatch.setattr(probes.socket, "SO_BINDTODEVICE", 25, raising=False)
    monkeypatch.setattr(probes.socket, "if_nametoindex", lambda name: 2)
    original = fake_socket.connect

    def connect(endpoint):
        assert fake_socket.options == [(probes.socket.SOL_SOCKET, 25, b"eth1\0")]
        original(endpoint)

    fake_socket.connect = connect
    assert probes.verify_endpoint("10.0.0.8", 554, interface="eth1").service_responded


@pytest.mark.parametrize("platform,denied", [("win32", False), ("linux", True)])
def test_unenforceable_interface_skips_without_fallback(fake_socket, monkeypatch, platform, denied):
    monkeypatch.setattr(probes.sys, "platform", platform)
    monkeypatch.setattr(probes.socket, "if_nametoindex", lambda name: 2)
    monkeypatch.setattr(probes.socket, "SO_BINDTODEVICE", 25, raising=False)
    if denied:

        def deny(*args):
            raise PermissionError("not allowed")

        fake_socket.setsockopt = deny
    result = probes.verify_endpoint("10.0.0.8", 554, interface="eth1")
    assert "cannot enforce interface" in result.error
    assert not fake_socket.calls and fake_socket.closed


@pytest.mark.parametrize(
    "fingerprint,match",
    [(None, False), (hashlib.sha256(b"device-cert").hexdigest(), True), ("00" * 32, False)],
)
def test_tls_uses_same_socket_and_compares_trusted_device_credential(
    fake_socket, monkeypatch, fingerprint, match
):
    contexts = []
    fake_socket.do_handshake = lambda: None
    fake_socket.getpeercert = lambda **k: b"device-cert"

    def context(**kwargs):
        contexts.append(kwargs)

        def wrap(sock, **kwargs):
            assert sock is fake_socket
            assert kwargs == {"server_hostname": "device.test", "do_handshake_on_connect": False}
            return sock

        return SimpleNamespace(wrap_socket=wrap)

    monkeypatch.setattr(probes.ssl, "create_default_context", context)
    result = probes.verify_endpoint(
        "10.0.0.8", 443, tls_name="device.test", ca_file="ca.pem", certificate_sha256=fingerprint
    )
    assert contexts == [{"cafile": "ca.pem"}]
    assert result.tls_server_authenticated and result.device_credential_matched == match
    assert bool(result.error) == (fingerprint is not None and not match)
    assert fake_socket.calls == [("10.0.0.8", 443)] and fake_socket.closed


def test_connect_and_tls_share_one_deadline(fake_socket, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(probes.time, "monotonic", lambda: now[0])

    def connect(endpoint):
        fake_socket.calls.append(endpoint)
        now[0] = 4.0

    fake_socket.connect = connect

    def handshake():
        assert fake_socket.timeouts[-1] == 1.0
        now[0] = 5.1

    fake_socket.do_handshake = handshake
    monkeypatch.setattr(
        probes.ssl,
        "create_default_context",
        lambda **k: SimpleNamespace(wrap_socket=lambda sock, **k: sock),
    )
    result = probes.verify_endpoint("10.0.0.8", 443, tls_name="device.test")
    assert result.service_responded and not result.tls_server_authenticated
    assert "deadline" in result.error and fake_socket.closed


def test_tls_trust_failure_never_retries_insecurely(fake_socket, monkeypatch):
    def fail():
        raise ssl.SSLCertVerificationError("untrusted or incorrect name")

    fake_socket.do_handshake = fail
    monkeypatch.setattr(
        probes.ssl,
        "create_default_context",
        lambda **k: SimpleNamespace(wrap_socket=lambda sock, **k: sock),
    )
    result = probes.verify_endpoint("10.0.0.8", 443, tls_name="device.test")
    assert result.service_responded and not result.tls_server_authenticated
    assert result.error and len(fake_socket.calls) == 1 and fake_socket.closed


@pytest.fixture
def scan_env(monkeypatch):
    module = importlib.import_module("cyberwave_cli.commands.scan")
    monkeypatch.setattr(interfaces, "read_ipv4", lambda: interfaces.IPv4Snapshot({}))
    monkeypatch.setattr(interfaces, "wired_interfaces", lambda **k: [])
    monkeypatch.setattr(
        interfaces, "route_to", lambda ip: interfaces.RouteObservation("wlan0", "10.0.0.2")
    )
    monkeypatch.setattr(hardware, "cached_devices", lambda: [])
    return module.scan


@pytest.mark.parametrize("args", [[], ["--wired"], ["--target", "10.0.0.8"]])
def test_scan_defaults_never_connect(scan_env, fake_socket, args):
    result = CliRunner().invoke(scan_env, args)
    assert result.exit_code == 0, result.output
    assert fake_socket.calls == []
    assert "not changed" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["-s", "10.0.0"],
        ["--no-ports"],
        ["--no-upnp"],
        ["--no-onvif"],
        ["-t", "2"],
        ["--verify"],
        ["--target", "10.0.0.8", "--verify"],
        ["--target", "10.0.0.8", "--interface", "eth1"],
        ["--target", "10.0.0.8", "--port", "554"],
        ["--target", "10.0.0.8", "--tls", "--server-name", "x.example"],
        ["--target", "10.0.0.8", "--certificate-sha256", "ab" * 32],
    ],
)
def test_scan_rejects_legacy_or_incomplete_verification_before_inventory(
    scan_env, monkeypatch, args
):
    monkeypatch.setattr(interfaces, "read_ipv4", lambda: pytest.fail("inventory before validation"))
    result = CliRunner().invoke(scan_env, args)
    assert result.exit_code == 2


def test_scan_says_a_verification_option_made_no_connection(scan_env, fake_socket):
    result = CliRunner().invoke(scan_env, ["--target", "10.0.0.8", "--port", "554"])

    assert result.exit_code == 2
    assert "only applies with --verify; without it no connection is made" in result.output
    assert fake_socket.calls == []


def test_scan_explicit_target_and_json_evidence(scan_env, fake_socket):
    result = CliRunner().invoke(
        scan_env, ["--target", "10.0.0.8", "--port", "554", "--verify", "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["route"]["interface"] == "wlan0"
    assert (
        data["verification"]["service_responded"]
        and data["verification"]["identity"] == "unverified"
    )
    assert fake_socket.calls == [("10.0.0.8", 554)]


def test_scan_verify_text_does_not_repeat_observations(scan_env, fake_socket):
    result = CliRunner().invoke(scan_env, ["--target", "10.0.0.8", "--port", "554", "--verify"])
    assert result.exit_code == 0, result.output
    assert result.output.count("Host network configuration was not changed.") == 1
    assert result.output.count("Matching host prefix") == 1
    assert result.output.count("Kernel-selected route") == 1
    assert result.output.count("Selected TCP service responded: True") == 1


def test_scan_rejects_malformed_fingerprint_before_any_output(scan_env, fake_socket):
    result = CliRunner().invoke(
        scan_env,
        [
            "--target",
            "10.0.0.8",
            "--port",
            "443",
            "--verify",
            "--tls",
            "--server-name",
            "device.example",
            "--certificate-sha256",
            "abc",
        ],
    )
    assert result.exit_code == 2
    assert "SHA-256 certificate fingerprint" in result.output
    assert "Matching host prefix" not in result.output
    assert fake_socket.calls == []


def test_overlap_without_interface_never_chooses_first(scan_env, fake_socket, monkeypatch):
    from tests.test_network_observations import snapshot

    monkeypatch.setattr(
        interfaces,
        "read_ipv4",
        lambda: snapshot({"eth0": ["10.0.0.1/24"], "wlan0": ["10.0.0.2/24"]}),
    )
    result = CliRunner().invoke(scan_env, ["--target", "10.0.0.8", "--port", "554", "--verify"])
    assert result.exit_code == 0 and "ambiguous interfaces" in result.output
    assert fake_socket.calls == []


def test_real_zenoh_binding_accepts_constraints_without_opening_session():
    import zenoh

    cfg = json.loads(str(probes.endpoint_session_config(zenoh, ["tcp/127.0.0.1:7447"])))
    assert cfg["mode"] == "client" and cfg["listen"]["endpoints"] == []
    assert cfg["scouting"]["multicast"]["enabled"] is False
    assert cfg["scouting"]["multicast"]["listen"] is False
    assert cfg["scouting"]["gossip"]["enabled"] is False
    assert cfg["scouting"]["gossip"]["autoconnect"] == []
    assert cfg["connect"]["timeout_ms"] == 5000


def test_worker_and_monitor_skip_unknown_endpoints(monkeypatch):
    from cyberwave_cli.monitor import ZenohStatsReader

    worker = importlib.import_module("cyberwave_cli.commands.worker")
    fake = SimpleNamespace(
        open=lambda *a: pytest.fail("SDK discovery fallback"),
        Config=lambda: pytest.fail("no endpoint"),
    )
    monkeypatch.setitem(sys.modules, "zenoh", fake)
    assert not worker._probe_zenoh_bus(duration=0.1, connect=None)[0]
    assert not ZenohStatsReader().start()


@pytest.fixture(scope="module")
def tls_certificate(tmp_path_factory):
    import shutil
    import subprocess

    if not shutil.which("openssl"):
        pytest.skip("OpenSSL is required for the isolated in-memory TLS trust fixture")
    directory = tmp_path_factory.mktemp("tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=device.test",
            "-addext",
            "subjectAltName=DNS:device.test",
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return cert, key


@pytest.mark.parametrize(
    "trusted,name,authenticated",
    [(True, "device.test", True), (False, "device.test", False), (True, "wrong.test", False)],
)
def test_real_tls_chain_and_name_validation_without_network(
    fake_socket, monkeypatch, tls_certificate, trusted, name, authenticated
):
    cert, key = tls_certificate
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(cert), str(key))
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    create = ssl.create_default_context

    class Context:
        def __init__(self, **kwargs):
            self.context = create(**kwargs)
            assert self.context.check_hostname
            assert self.context.verify_mode == ssl.CERT_REQUIRED

        def wrap_socket(self, sock, *, server_hostname, do_handshake_on_connect):
            assert sock is fake_socket and not do_handshake_on_connect
            incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
            client = self.context.wrap_bio(incoming, outgoing, server_hostname=server_hostname)

            def handshake():
                for _ in range(20):
                    try:
                        client.do_handshake()
                        return
                    except ssl.SSLWantReadError:
                        pass
                    server_in.write(outgoing.read())
                    try:
                        server.do_handshake()
                    except ssl.SSLWantReadError:
                        pass
                    incoming.write(server_out.read())
                raise AssertionError("TLS fixture handshake did not finish")

            sock.do_handshake = handshake
            sock.getpeercert = client.getpeercert
            return sock

    monkeypatch.setattr(probes.ssl, "create_default_context", Context)
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    result = probes.verify_endpoint(
        "10.0.0.8",
        443,
        tls_name=name,
        ca_file=str(cert) if trusted else None,
        certificate_sha256=fingerprint,
    )
    assert result.service_responded and result.tls_server_authenticated == authenticated
    assert result.device_credential_matched == authenticated
    assert bool(result.error) != authenticated
    assert fake_socket.closed and len(fake_socket.calls) == 1
