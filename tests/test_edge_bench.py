"""Tests for ``cyberwave edge bench`` device-class + baseline fallback logic.

These cover the code paths that used to silently break when a new hardware
generation (e.g. Apple M4) arrived: the tier parser for
``sysctl -n machdep.cpu.brand_string`` and the ordered baseline candidate
chain consumed by ``_load_baseline``.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from cyberwave_cli.commands.edge import bench

# ---------------------------------------------------------------------------
# _apple_silicon_chip_tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "brand,expected",
    [
        ("Apple M1", "m1"),
        ("Apple M1 Pro", "m1"),
        ("Apple M1 Max", "m1"),
        ("Apple M1 Ultra", "m1"),
        ("apple m2", "m2"),
        ("Apple M2 Pro", "m2"),
        ("Apple M3 Max", "m3"),
        ("Apple M4", "m4"),
        ("Apple M4 Pro", "m4"),
        ("Apple M4 Max", "m4"),
        ("  Apple M5  ", "m5"),  # future-proof: any digit is accepted
    ],
)
def test_apple_silicon_chip_tier_recognizes_known_brands(brand: str, expected: str) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=brand, stderr="")
    with patch.object(bench.subprocess, "run", return_value=completed):
        assert bench._apple_silicon_chip_tier() == expected


@pytest.mark.parametrize(
    "brand",
    [
        "",
        "Intel(R) Core(TM) i7-1185G7 @ 3.00GHz",
        "Apple Silicon",  # no digit
        "Virtual CPU @ 2.5GHz",
    ],
)
def test_apple_silicon_chip_tier_returns_none_for_unknown_brands(brand: str) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=brand, stderr="")
    with patch.object(bench.subprocess, "run", return_value=completed):
        assert bench._apple_silicon_chip_tier() is None


def test_apple_silicon_chip_tier_returns_none_on_sysctl_failure() -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="nope")
    with patch.object(bench.subprocess, "run", return_value=completed):
        assert bench._apple_silicon_chip_tier() is None


def test_apple_silicon_chip_tier_returns_none_when_sysctl_raises() -> None:
    with patch.object(bench.subprocess, "run", side_effect=FileNotFoundError("sysctl")):
        assert bench._apple_silicon_chip_tier() is None


# ---------------------------------------------------------------------------
# _ram_gb (macOS fallback via `sysctl -n hw.memsize`)
# ---------------------------------------------------------------------------


def _mock_meminfo_missing() -> object:
    """Make ``Path("/proc/meminfo").read_text()`` raise, as it does on macOS."""
    return patch.object(
        bench.Path, "read_text", side_effect=FileNotFoundError("/proc/meminfo")
    )


def test_ram_gb_uses_sysctl_on_macos() -> None:
    # 24 GiB = 25769803776 bytes -> 24.0 GB when divided by 1024**3.
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="25769803776\n", stderr=""
    )
    with _mock_meminfo_missing(), patch.object(
        bench.subprocess, "run", return_value=completed
    ) as run:
        assert bench._ram_gb() == 24.0
    # Verify we actually asked sysctl for hw.memsize, not something else.
    assert run.call_args_list[0].args[0] == ["sysctl", "-n", "hw.memsize"]


def test_ram_gb_returns_none_when_sysctl_output_is_garbage() -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="not-a-number\n", stderr=""
    )
    with _mock_meminfo_missing(), patch.object(
        bench.subprocess, "run", return_value=completed
    ):
        # psutil is not a dep and /proc/meminfo is gone, so we should get None
        # rather than crashing on int("not-a-number").
        assert bench._ram_gb() is None


def test_ram_gb_returns_none_when_sysctl_fails() -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="nope")
    with _mock_meminfo_missing(), patch.object(
        bench.subprocess, "run", return_value=completed
    ):
        assert bench._ram_gb() is None


# ---------------------------------------------------------------------------
# _storage / _storage_kind / _linux_root_block_device
# ---------------------------------------------------------------------------


class _FakeUsage:
    """Mimic the namedtuple returned by ``shutil.disk_usage``."""

    def __init__(self, total: int, free: int) -> None:
        self.total = total
        self.used = total - free
        self.free = free


def test_storage_returns_capacity_and_kind() -> None:
    # 500 GiB total, 100 GiB free.
    usage = _FakeUsage(total=500 * 1024**3, free=100 * 1024**3)
    with (
        patch("shutil.disk_usage", return_value=usage),
        patch.object(bench, "_storage_kind", return_value="nvme"),
    ):
        info = bench._storage()
    assert info["total_gb"] == 500.0
    assert info["free_gb"] == 100.0
    assert info["kind"] == "nvme"


def test_storage_is_resilient_to_disk_usage_failure() -> None:
    with (
        patch("shutil.disk_usage", side_effect=PermissionError("denied")),
        patch.object(bench, "_storage_kind", return_value=None),
    ):
        info = bench._storage()
    assert info == {"total_gb": None, "free_gb": None, "kind": None}


def _diskutil_output(protocol: str, solid_state: str) -> subprocess.CompletedProcess:
    stdout = (
        "   Device Identifier:        disk3s1s1\n"
        f"   Protocol:                 {protocol}\n"
        f"   Solid State:              {solid_state}\n"
    )
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


@pytest.mark.parametrize(
    "protocol,solid_state,expected",
    [
        ("Apple Fabric", "Yes", "nvme"),   # Apple Silicon internal SSD
        ("PCI-Express", "Yes", "nvme"),    # Intel Mac with NVMe
        ("USB", "Yes", "ssd"),             # External USB SSD
        ("SATA", "Yes", "ssd"),            # Legacy Intel Mac SATA SSD
        ("SATA", "No", "hdd"),             # Legacy spinning disk
    ],
)
def test_storage_kind_darwin_parses_diskutil(
    protocol: str, solid_state: str, expected: str
) -> None:
    with (
        patch("platform.system", return_value="Darwin"),
        patch.object(
            bench.subprocess, "run", return_value=_diskutil_output(protocol, solid_state)
        ),
    ):
        assert bench._storage_kind() == expected


def test_storage_kind_darwin_returns_none_when_diskutil_fails() -> None:
    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="nope")
    with (
        patch("platform.system", return_value="Darwin"),
        patch.object(bench.subprocess, "run", return_value=failed),
    ):
        assert bench._storage_kind() is None


@pytest.mark.parametrize(
    "mount_line,expected",
    [
        ("/dev/nvme0n1p2 / ext4 rw 0 0", "nvme0n1"),
        ("/dev/sda1 / ext4 rw 0 0", "sda"),
        ("/dev/mmcblk0p2 / ext4 rw 0 0", "mmcblk0"),
        ("/dev/vda / ext4 rw 0 0", "vda"),  # whole-disk virtio mount
    ],
)
def test_linux_root_block_device_strips_partition_suffix(
    mount_line: str, expected: str
) -> None:
    mounts = f"proc /proc proc rw 0 0\n{mount_line}\ntmpfs /run tmpfs rw 0 0\n"
    with patch.object(bench.Path, "read_text", return_value=mounts):
        assert bench._linux_root_block_device() == expected


def test_linux_root_block_device_returns_none_when_no_root_line() -> None:
    with patch.object(
        bench.Path, "read_text", return_value="proc /proc proc rw 0 0\n"
    ):
        assert bench._linux_root_block_device() is None


def test_storage_kind_linux_maps_nvme_prefix() -> None:
    with (
        patch("platform.system", return_value="Linux"),
        patch.object(bench, "_linux_root_block_device", return_value="nvme0n1"),
    ):
        assert bench._storage_kind() == "nvme"


def test_storage_kind_linux_uses_rotational_flag_for_sata() -> None:
    def _read(self: bench.Path, *args: object, **kwargs: object) -> str:
        if str(self).endswith("/sys/block/sda/queue/rotational"):
            return "0\n"
        raise FileNotFoundError(self)

    with (
        patch("platform.system", return_value="Linux"),
        patch.object(bench, "_linux_root_block_device", return_value="sda"),
        patch.object(bench.Path, "read_text", _read),
    ):
        assert bench._storage_kind() == "ssd"


@pytest.mark.parametrize(
    "delta,expected",
    [
        # Threshold 0.15 -> noise floor at ±5%.
        (None,   "dim"),          # no baseline
        (0.0,    "dim"),          # exact match
        (0.04,   "dim"),          # +4%  within noise
        (-0.04,  "dim"),          # -4%  within noise
        (0.05,   "dim"),          # +5%  still on the noise boundary
        (-0.05,  "dim"),          # -5%  still on the noise boundary
        (0.08,   "green"),        # +8%  modest speedup
        (-0.08,  "yellow"),       # -8%  trending under baseline
        (0.14,   "green"),        # +14% still a modest speedup
        (-0.14,  "yellow"),       # -14% still within tolerance
        (0.16,   "bold green"),   # +16% crosses threshold -> clear improvement
        (-0.16,  "bold red"),     # -16% crosses threshold -> regressed
        (1.50,   "bold green"),   # +150% way above threshold
        (-0.99,  "bold red"),     # catastrophic regression
    ],
)
def test_delta_style_gradient_for_default_threshold(delta: float | None, expected: str) -> None:
    assert bench._delta_style(delta, 0.15) == expected


def test_delta_style_scales_noise_floor_with_threshold() -> None:
    # Threshold 0.30 -> noise floor at 10%; -7% should now register as "dim".
    assert bench._delta_style(-0.07, 0.30) == "dim"
    # Threshold 0.09 -> noise floor at 3%; -7% now lights up as "yellow".
    assert bench._delta_style(-0.07, 0.09) == "yellow"


def test_delta_style_exact_threshold_counts_as_regression() -> None:
    # Matches _status_for(): regression band is inclusive of -threshold (<=).
    assert bench._delta_style(-0.15, 0.15) == "bold red"
    # But the improvement band is inclusive of +threshold (>=).
    assert bench._delta_style(0.15, 0.15) == "bold green"


def test_render_delta_cell_wraps_formatted_value_in_markup() -> None:
    cell = bench._render_delta_cell(-0.184, 0.15)
    assert cell == "[bold red]-18.4%[/bold red]"

    noise_cell = bench._render_delta_cell(0.02, 0.15)
    assert noise_cell == "[dim]+2.0%[/dim]"

    missing_cell = bench._render_delta_cell(None, 0.15)
    assert missing_cell == "[dim]-[/dim]"


def test_storage_kind_linux_distinguishes_emmc_from_sd() -> None:
    def _emmc(self: bench.Path, *args: object, **kwargs: object) -> str:
        if str(self).endswith("/sys/block/mmcblk0/device/type"):
            return "MMC\n"
        raise FileNotFoundError(self)

    with (
        patch("platform.system", return_value="Linux"),
        patch.object(bench, "_linux_root_block_device", return_value="mmcblk0"),
        patch.object(bench.Path, "read_text", _emmc),
    ):
        assert bench._storage_kind() == "emmc"


# ---------------------------------------------------------------------------
# _detect_device_class integration
# ---------------------------------------------------------------------------


def test_detect_device_class_emits_tier_specific_apple_silicon_slug() -> None:
    # On macOS arm64 with a readable brand string we should get the tier slug.
    with (
        patch("platform.system", return_value="Darwin"),
        patch("platform.machine", return_value="arm64"),
        patch.object(bench, "_apple_silicon_chip_tier", return_value="m4"),
        patch.object(bench.Path, "exists", return_value=False),
    ):
        assert bench._detect_device_class() == "apple-silicon-m4"


def test_detect_device_class_falls_back_to_apple_silicon_when_tier_unknown() -> None:
    with (
        patch("platform.system", return_value="Darwin"),
        patch("platform.machine", return_value="arm64"),
        patch.object(bench, "_apple_silicon_chip_tier", return_value=None),
        patch.object(bench.Path, "exists", return_value=False),
    ):
        assert bench._detect_device_class() == "apple-silicon"


# ---------------------------------------------------------------------------
# _baseline_candidate_files
# ---------------------------------------------------------------------------


def test_baseline_candidate_files_walks_tier_slug_for_apple_silicon() -> None:
    assert bench._baseline_candidate_files("apple-silicon-m4", "arm64") == [
        "apple-silicon-m4.json",
        "apple-silicon.json",
        "generic-arm64.json",
    ]


def test_baseline_candidate_files_walks_multiple_segments_for_jetson() -> None:
    # Single-segment roots (``jetson``) are intentionally skipped — they are
    # never shipped as their own baselines and would just add dead lookups.
    assert bench._baseline_candidate_files("jetson-orin-nano", "aarch64") == [
        "jetson-orin-nano.json",
        "jetson-orin.json",
        "generic-aarch64.json",
    ]


def test_baseline_candidate_files_handles_leaf_slug() -> None:
    # ``x86-laptop`` has a single-segment parent (``x86``) which is skipped.
    assert bench._baseline_candidate_files("x86-laptop", "x86_64") == [
        "x86-laptop.json",
        "generic-x86_64.json",
    ]


def test_baseline_candidate_files_does_not_duplicate_generic_fallback() -> None:
    # The slug itself already matches the generic fallback; we should not emit it twice.
    out = bench._baseline_candidate_files("generic-arm64", "arm64")
    assert out == ["generic-arm64.json"]


# ---------------------------------------------------------------------------
# _load_baseline
# ---------------------------------------------------------------------------


def test_load_baseline_prefers_tier_specific_file_over_fallback() -> None:
    def _fake_load(name: str) -> dict | None:
        return {"device_class": "apple-silicon-m4"} if name == "apple-silicon-m4.json" else None

    with (
        patch("platform.machine", return_value="arm64"),
        patch.object(bench, "_load_packaged_baseline", side_effect=_fake_load) as mocked,
    ):
        baseline, source = bench._load_baseline(
            "apple-silicon-m4", override_path=None, no_compare=False
        )

    assert baseline == {"device_class": "apple-silicon-m4"}
    assert source == "package:apple-silicon-m4.json"
    # We must stop looking as soon as we find a match — no parent lookups.
    assert [call.args[0] for call in mocked.call_args_list] == ["apple-silicon-m4.json"]


def test_load_baseline_falls_through_to_parent_slug_when_tier_missing() -> None:
    def _fake_load(name: str) -> dict | None:
        return {"device_class": "apple-silicon"} if name == "apple-silicon.json" else None

    with (
        patch("platform.machine", return_value="arm64"),
        patch.object(bench, "_load_packaged_baseline", side_effect=_fake_load),
    ):
        baseline, source = bench._load_baseline(
            "apple-silicon-m9", override_path=None, no_compare=False
        )

    assert baseline == {"device_class": "apple-silicon"}
    assert source == "package:apple-silicon.json"


def test_load_baseline_returns_none_when_no_compare_requested() -> None:
    baseline, source = bench._load_baseline("apple-silicon-m4", override_path=None, no_compare=True)
    assert baseline is None
    assert source == "disabled"


# ---------------------------------------------------------------------------
# Shipped tier baseline files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["m1", "m2", "m3", "m4"])
def test_packaged_tier_baseline_is_shipped_and_wellformed(tier: str) -> None:
    data = bench._load_packaged_baseline(f"apple-silicon-{tier}.json")
    assert data is not None, f"apple-silicon-{tier}.json is missing from the package"
    assert data["device_class"] == f"apple-silicon-{tier}"
    metrics = data["metrics"]
    for key, _label in bench.METRIC_SPEC:
        entry = metrics[key]
        assert entry["ops_per_s"] > 0
        assert entry["ns_per_op"] > 0
    assert metrics["decode_mb_s"] > 0


def test_packaged_tier_baselines_scale_monotonically() -> None:
    # Header pack ops/s should grow from M1 → M4 — locks in the 1.00/1.15/1.30/1.50 ladder.
    tiers = ["m1", "m2", "m3", "m4"]
    ops = []
    for tier in tiers:
        data = bench._load_packaged_baseline(f"apple-silicon-{tier}.json")
        assert data is not None
        ops.append(data["metrics"]["header_pack"]["ops_per_s"])
    assert ops == sorted(ops), f"header_pack ops/s not monotonic across tiers: {ops}"
    assert ops[-1] > ops[0], "M4 baseline should be strictly faster than M1"


def test_packaged_apple_silicon_fallback_matches_m1_ceiling() -> None:
    # The generic apple-silicon.json is the safety net; it must not claim numbers
    # higher than the conservative M1 tier, otherwise older hardware would regress.
    generic = bench._load_packaged_baseline("apple-silicon.json")
    m1 = bench._load_packaged_baseline("apple-silicon-m1.json")
    assert generic is not None and m1 is not None
    for key, _label in bench.METRIC_SPEC:
        g = generic["metrics"][key]["ops_per_s"]
        ref = m1["metrics"][key]["ops_per_s"]
        assert g <= ref, f"apple-silicon fallback must be <= M1 for {key}: {g} > {ref}"
