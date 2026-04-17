"""``cyberwave edge bench`` — micro-benchmark the Zenoh SDK hot paths.

Run on an edge device to measure per-call throughput of the critical data
plane functions: header packing, sample decoding, stats accounting, and
sequence numbering.  Each run captures a device fingerprint and is compared
against a per-device-class baseline shipped with the CLI, so you can see at a
glance whether your machine meets (or beats) the reference for its class.

Usage::

    cyberwave edge bench
    cyberwave edge bench --rounds 500000 --warmup 5000
    cyberwave edge bench --baseline custom.json
    cyberwave edge bench --save-baseline my-device.json
    cyberwave edge bench --output run.json --threshold 0.1
    cyberwave edge bench --no-compare
"""

from __future__ import annotations

import collections
import gc
import itertools
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Callable

import click
import numpy as np
from rich.console import Console
from rich.table import Table

console = Console()

BASELINES_PACKAGE = "cyberwave_cli.commands.edge.bench_baselines"
REGRESSION_THRESHOLD_DEFAULT = 0.15

# Ordered list of (metric_key, display_label) pairs.  ``metric_key`` is the
# stable JSON key used in baseline files; ``display_label`` is what we show in
# the Rich table.
METRIC_SPEC: list[tuple[str, str]] = [
    ("header_pack",      "HeaderTemplate.pack()"),
    ("decode_zero_copy", "decode (zero-copy)"),
    ("decode_with_copy", "decode (with .copy())"),
    ("stats_lockfree",   "stats (lock-free)"),
    ("stats_locked",     "stats (with lock)"),
    ("seq_itertools",    "seq (itertools.count)"),
    ("seq_locked",       "seq (threading.Lock)"),
]

# Shape used for the frame-sized header/decode tests.
FRAME_SHAPE: tuple[int, int, int] = (480, 640, 3)


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def register(edge_group: click.Group) -> None:
    """Register the ``bench`` command on the edge group."""
    edge_group.add_command(bench)


# ---------------------------------------------------------------------------
# Device fingerprint
# ---------------------------------------------------------------------------

def _detect_device_class() -> str:
    """Best-effort slug identifying the device family for baseline lookup."""
    try:
        model_path = Path("/proc/device-tree/model")
        if model_path.exists():
            text = model_path.read_text(errors="ignore").strip("\x00\n\r\t ").lower()
            if "raspberry pi 5" in text:
                return "rpi-5"
            if "raspberry pi 4" in text:
                return "rpi-4"
            if "raspberry pi 3" in text:
                return "rpi-3"
            if "orin nano" in text:
                return "jetson-orin-nano"
            if "orin nx" in text:
                return "jetson-orin-nx"
            if "agx orin" in text:
                return "jetson-agx-orin"
            if "xavier nx" in text:
                return "jetson-xavier-nx"
            if "jetson nano" in text:
                return "jetson-nano"
    except Exception:
        pass

    try:
        if Path("/etc/nv_tegra_release").exists():
            return "jetson-generic"
    except Exception:
        pass

    arch = platform.machine().lower()
    system = platform.system().lower()
    if system == "darwin":
        if arch in ("arm64", "aarch64"):
            return "apple-silicon"
        return "apple-intel"
    if system == "linux":
        if arch in ("x86_64", "amd64"):
            return "x86-laptop" if _has_battery() else "x86-server"
        if arch in ("arm64", "aarch64"):
            return "generic-arm64"
        return f"generic-{arch}"
    if system == "windows":
        return f"windows-{arch}"
    return f"generic-{arch}"


def _has_battery() -> bool:
    try:
        for p in Path("/sys/class/power_supply").glob("BAT*"):
            if p.exists():
                return True
    except Exception:
        pass
    return False


def _cpu_model() -> str:
    try:
        text = Path("/proc/cpuinfo").read_text(errors="ignore")
        match = re.search(r"model name\s*:\s*(.+)", text)
        if match:
            return match.group(1).strip()
        match = re.search(r"Model\s*:\s*(.+)", text)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


def _cpu_freq_mhz_max() -> float | None:
    try:
        text = Path(
            "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq"
        ).read_text(errors="ignore").strip()
        return round(int(text) / 1000.0, 1)
    except Exception:
        pass
    try:
        import psutil  # type: ignore
        f = psutil.cpu_freq()
        if f and f.max:
            return float(f.max)
    except Exception:
        pass
    return None


def _ram_gb() -> float | None:
    try:
        text = Path("/proc/meminfo").read_text(errors="ignore")
        match = re.search(r"MemTotal:\s+(\d+)\s+kB", text)
        if match:
            return round(int(match.group(1)) / 1024 / 1024, 1)
    except Exception:
        pass
    try:
        import psutil  # type: ignore
        return round(psutil.virtual_memory().total / 1024**3, 1)
    except Exception:
        pass
    return None


def _accelerator() -> dict[str, Any]:
    if Path("/etc/nv_tegra_release").exists():
        return {"kind": "tegra", "name": "NVIDIA Tegra"}
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return {
                "kind": "cuda",
                "name": torch.cuda.get_device_name(0),
                "torch": torch.__version__,
            }
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            return {"kind": "mps", "name": "Apple MPS", "torch": torch.__version__}
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return {"kind": "cuda", "name": out.stdout.strip().splitlines()[0]}
    except Exception:
        pass
    return {"kind": "none", "name": None}


def _zenoh_version() -> str | None:
    try:
        import zenoh  # type: ignore
        return getattr(zenoh, "__version__", None) or "unknown"
    except Exception:
        return None


def _cyberwave_version() -> str | None:
    try:
        from importlib.metadata import version
        return version("cyberwave")
    except Exception:
        return None


def _cli_version() -> str | None:
    try:
        from importlib.metadata import version
        return version("cyberwave-cli")
    except Exception:
        return None


def _collect_fingerprint() -> dict[str, Any]:
    logical_cores: int | None
    try:
        logical_cores = os.cpu_count()
    except Exception:
        logical_cores = None

    physical_cores: int | None = None
    try:
        import psutil  # type: ignore
        physical_cores = psutil.cpu_count(logical=False)
    except Exception:
        pass

    return {
        "device_class": _detect_device_class(),
        "hostname": platform.node() or "unknown",
        "cpu": {
            "model": _cpu_model(),
            "cores_logical": logical_cores,
            "cores_physical": physical_cores,
            "freq_mhz_max": _cpu_freq_mhz_max(),
            "arch": platform.machine(),
        },
        "ram_gb": _ram_gb(),
        "accelerator": _accelerator(),
        "os": {
            "name": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
        },
        "versions": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "zenoh": _zenoh_version(),
            "cyberwave": _cyberwave_version(),
            "cli": _cli_version(),
        },
    }


def _render_fingerprint_header(fp: dict[str, Any]) -> None:
    cpu = fp["cpu"]
    cores = cpu.get("cores_logical")
    cores_str = f"{cores} cores" if cores else "? cores"
    freq = cpu.get("freq_mhz_max")
    freq_str = f"@ {freq:.0f} MHz" if freq else ""

    accel = fp["accelerator"]
    accel_str = accel.get("name") or "no accelerator"

    ram = fp["ram_gb"]
    ram_str = f"{ram} GB" if ram else "? GB"

    versions = fp["versions"]

    table = Table(
        title=f"Device: [bold]{fp['device_class']}[/bold]",
        show_header=False,
        expand=False,
    )
    table.add_column("key", style="cyan")
    table.add_column("value")

    table.add_row("hostname", str(fp.get("hostname", "?")))
    table.add_row("CPU", f"{cpu.get('model', '?')}  {cores_str} {freq_str}".strip())
    table.add_row("RAM", ram_str)
    table.add_row("accelerator", accel_str)
    table.add_row("OS", f"{fp['os']['name']} {fp['os']['release']}")
    table.add_row(
        "versions",
        f"python {versions['python']}"
        f"  •  numpy {versions['numpy']}"
        f"  •  zenoh {versions.get('zenoh') or '-'}"
        f"  •  cli {versions.get('cli') or '-'}",
    )

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Rigorous timing
# ---------------------------------------------------------------------------

def _maybe_pin_cpu(pin: bool) -> None:
    if not pin:
        return
    sched_setaffinity = getattr(os, "sched_setaffinity", None)
    if sched_setaffinity is None:
        console.print(
            "[dim]--pin requested but os.sched_setaffinity is not available on "
            "this platform[/dim]"
        )
        return
    try:
        sched_setaffinity(0, {0})
        console.print("[dim]pinned to CPU 0[/dim]")
    except Exception as exc:
        console.print(f"[dim]could not pin CPU: {exc}[/dim]")


def _run_bench(
    label: str,
    fn: Callable[[], Any],
    rounds: int,
    *,
    warmup: int,
    repeat: int,
) -> dict[str, Any]:
    """Run *fn* repeatedly with warmup and GC disabled, returning timing stats.

    Performs *warmup* un-timed calls, then runs *repeat* independent timed
    passes of *rounds* iterations each with the garbage collector disabled.
    Returns the median ops/s across the passes together with the best pass
    and stdev so flaky devices are visible.
    """
    for _ in range(warmup):
        fn()

    samples_ops: list[float] = []
    samples_ns: list[float] = []
    samples_elapsed: list[float] = []

    gc_was_enabled = gc.isenabled()
    try:
        for _ in range(max(1, repeat)):
            gc.collect()
            gc.disable()
            try:
                t0 = time.perf_counter()
                for _ in range(rounds):
                    fn()
                elapsed = time.perf_counter() - t0
            finally:
                if gc_was_enabled:
                    gc.enable()
            if elapsed <= 0:
                continue
            samples_ops.append(rounds / elapsed)
            samples_ns.append(elapsed / rounds * 1e9)
            samples_elapsed.append(elapsed)
    finally:
        if gc_was_enabled and not gc.isenabled():
            gc.enable()

    if not samples_ops:
        return {
            "label": label,
            "ops": 0.0,
            "ns": 0.0,
            "best_ops": 0.0,
            "stdev_ops": 0.0,
            "elapsed": 0.0,
            "samples_ops": [],
        }

    return {
        "label": label,
        "ops": statistics.median(samples_ops),
        "ns": statistics.median(samples_ns),
        "best_ops": max(samples_ops),
        "stdev_ops": statistics.stdev(samples_ops) if len(samples_ops) > 1 else 0.0,
        "elapsed": statistics.median(samples_elapsed),
        "samples_ops": samples_ops,
    }


# ---------------------------------------------------------------------------
# Baseline loading / comparison
# ---------------------------------------------------------------------------

def _load_baseline(
    device_class: str,
    override_path: str | None,
    *,
    no_compare: bool,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve a baseline, returning (baseline_dict_or_None, source_label)."""
    if no_compare:
        return None, "disabled"

    if override_path:
        path = Path(override_path)
        if not path.exists():
            console.print(f"[yellow]baseline file not found: {path}[/yellow]")
            return None, f"missing:{path}"
        try:
            return json.loads(path.read_text()), f"file:{path}"
        except Exception as exc:
            console.print(f"[yellow]could not load baseline {path}: {exc}[/yellow]")
            return None, f"error:{path}"

    arch = platform.machine().lower() or "unknown"
    candidates = [f"{device_class}.json", f"generic-{arch}.json"]

    for name in candidates:
        loaded = _load_packaged_baseline(name)
        if loaded is not None:
            return loaded, f"package:{name}"
    return None, "not_found"


def _load_packaged_baseline(filename: str) -> dict[str, Any] | None:
    try:
        root = resources.files(BASELINES_PACKAGE)
    except (FileNotFoundError, ModuleNotFoundError):
        return None
    except Exception as exc:
        console.print(f"[yellow]baseline package lookup failed: {exc}[/yellow]")
        return None
    try:
        target = root / filename
        if not target.is_file():
            return None
        return json.loads(target.read_text())
    except Exception as exc:
        console.print(f"[yellow]could not load packaged baseline {filename}: {exc}[/yellow]")
        return None


def _baseline_metric_ops(baseline: dict[str, Any] | None, key: str) -> float | None:
    if not baseline:
        return None
    metrics = baseline.get("metrics") or {}
    entry = metrics.get(key)
    if isinstance(entry, dict):
        val = entry.get("ops_per_s")
        return float(val) if isinstance(val, (int, float)) else None
    if isinstance(entry, (int, float)):
        return float(entry)
    return None


def _baseline_scalar(baseline: dict[str, Any] | None, key: str) -> float | None:
    if not baseline:
        return None
    metrics = baseline.get("metrics") or {}
    entry = metrics.get(key)
    if isinstance(entry, (int, float)):
        return float(entry)
    return None


def _compute_delta(value: float, baseline: float | None) -> float | None:
    if baseline is None or baseline <= 0:
        return None
    return (value - baseline) / baseline


def _status_for(delta: float | None, threshold: float) -> str:
    if delta is None:
        return "n/a"
    if delta < -threshold:
        return "regressed"
    if delta > threshold:
        return "improved"
    return "ok"


def _format_delta(delta: float | None) -> str:
    if delta is None:
        return "-"
    return f"{delta * 100:+.1f}%"


def _status_style(status: str) -> str:
    return {
        "regressed": "bold red",
        "improved":  "bold green",
        "ok":        "green",
        "n/a":       "dim",
    }.get(status, "")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _metrics_payload(
    results: dict[str, dict[str, Any]],
    decode_mb_s: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, _label in METRIC_SPEC:
        r = results[key]
        payload[key] = {
            "ops_per_s": round(r["ops"], 2),
            "ns_per_op": round(r["ns"], 2),
            "best_ops_per_s": round(r.get("best_ops", r["ops"]), 2),
            "stdev_ops_per_s": round(r.get("stdev_ops", 0.0), 2),
        }
    payload["decode_mb_s"] = round(decode_mb_s, 2)
    return payload


def _write_run_output(
    path: Path,
    fingerprint: dict[str, Any],
    metrics: dict[str, Any],
    baseline: dict[str, Any] | None,
    baseline_source: str,
    deltas: dict[str, float | None],
    statuses: dict[str, str],
    threshold: float,
    config: dict[str, Any],
) -> None:
    out = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fingerprint,
        "config": config,
        "metrics": metrics,
        "baseline": {
            "source": baseline_source,
            "data": baseline,
            "threshold": threshold,
            "deltas": {k: (round(v, 4) if v is not None else None) for k, v in deltas.items()},
            "statuses": statuses,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2))
    console.print(f"[green]wrote run result to[/green] {path}")


def _write_saved_baseline(
    path: Path,
    fingerprint: dict[str, Any],
    metrics: dict[str, Any],
) -> None:
    payload = {
        "device_class": fingerprint.get("device_class"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "cyberwave_cli_version": fingerprint.get("versions", {}).get("cli"),
        "cyberwave_version": fingerprint.get("versions", {}).get("cyberwave"),
        "provisional": False,
        "source_device": {
            "hostname": fingerprint.get("hostname"),
            "cpu": fingerprint.get("cpu", {}).get("model"),
            "accelerator": fingerprint.get("accelerator", {}).get("name"),
        },
        "metrics": metrics,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    console.print(f"[green]wrote baseline to[/green] {path}")


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

@click.command("bench")
@click.option(
    "--rounds", "-n", default=100_000, show_default=True,
    help="Number of iterations per timed pass.",
)
@click.option(
    "--warmup", default=2000, show_default=True,
    help="Warmup iterations executed before each benchmark (discarded).",
)
@click.option(
    "--repeat", default=3, show_default=True,
    help="Number of timed passes per benchmark; the median is reported.",
)
@click.option(
    "--threshold", default=REGRESSION_THRESHOLD_DEFAULT, show_default=True, type=float,
    help="Regression threshold as a fraction (0.15 = +/-15%).",
)
@click.option(
    "--baseline", "baseline_path", default=None,
    help="Override the baseline file used for comparison (JSON).",
)
@click.option(
    "--save-baseline", "save_baseline_path", default=None,
    help="Write this run's metrics as a baseline file at the given path.",
)
@click.option(
    "--output", "output_path", default=None,
    help="Write the full run result (fingerprint + metrics + baseline) to this JSON file.",
)
@click.option(
    "--pin", is_flag=True, default=False,
    help="Pin the benchmark to CPU 0 (Linux only).",
)
@click.option(
    "--no-compare", is_flag=True, default=False,
    help="Skip baseline lookup and comparison.",
)
@click.pass_context
def bench(
    ctx: click.Context,
    rounds: int,
    warmup: int,
    repeat: int,
    threshold: float,
    baseline_path: str | None,
    save_baseline_path: str | None,
    output_path: str | None,
    pin: bool,
    no_compare: bool,
) -> None:
    """Benchmark Zenoh SDK hot-path performance and compare to a device baseline.

    Measures header packing, sample decoding (zero-copy vs copy), stats
    accounting, and sequence numbering, then compares every metric to a
    baseline shipped for the detected device class.  Exits with code 2 when
    any metric regresses beyond ``--threshold``.
    """
    from cyberwave.data.header import CONTENT_TYPE_NUMPY, HeaderTemplate, decode

    console.print("[bold]Cyberwave Edge Bench[/bold]")
    console.print()

    fingerprint = _collect_fingerprint()
    _render_fingerprint_header(fingerprint)
    _maybe_pin_cpu(pin)

    shape = FRAME_SHAPE
    frame_bytes = np.zeros(shape, dtype=np.uint8).tobytes()
    frame_size_mb = len(frame_bytes) / 1e6

    # -- HeaderTemplate.pack() ------------------------------------------------
    tpl = HeaderTemplate(CONTENT_TYPE_NUMPY, shape=shape, dtype="uint8")

    results: dict[str, dict[str, Any]] = {}

    results["header_pack"] = _run_bench(
        "HeaderTemplate.pack()",
        lambda: tpl.pack(frame_bytes),
        rounds,
        warmup=warmup,
        repeat=repeat,
    )

    # -- decode + frombuffer (zero-copy vs copy) ------------------------------
    wire = tpl.pack(np.random.randint(0, 255, shape, dtype=np.uint8).tobytes())

    def _decode_zero_copy() -> np.ndarray:
        header, payload = decode(wire)
        return np.frombuffer(payload, dtype=header.dtype).reshape(header.shape)

    def _decode_with_copy() -> np.ndarray:
        header, payload = decode(wire)
        return np.frombuffer(payload, dtype=header.dtype).reshape(header.shape).copy()

    results["decode_zero_copy"] = _run_bench(
        "decode (zero-copy)", _decode_zero_copy, rounds, warmup=warmup, repeat=repeat
    )
    results["decode_with_copy"] = _run_bench(
        "decode (with .copy())", _decode_with_copy, rounds, warmup=warmup, repeat=repeat
    )

    # -- Stats counters -------------------------------------------------------
    dd: dict[str, int] = collections.defaultdict(int)
    channels = [f"cw/twin-{i}/data/camera" for i in range(4)]

    def _stats_lockfree() -> None:
        ch = channels[0]
        dd[ch] += 1

    lock = threading.Lock()
    ld: dict[str, int] = {}

    def _stats_locked() -> None:
        ch = channels[0]
        with lock:
            ld[ch] = ld.get(ch, 0) + 1

    results["stats_lockfree"] = _run_bench(
        "stats (lock-free)", _stats_lockfree, rounds * 5, warmup=warmup, repeat=repeat
    )
    results["stats_locked"] = _run_bench(
        "stats (with lock)", _stats_locked, rounds * 5, warmup=warmup, repeat=repeat
    )

    # -- Sequence counter -----------------------------------------------------
    counter = itertools.count()
    seq_val = 0

    def _seq_atomic() -> None:
        next(counter)

    seq_lock = threading.Lock()

    def _seq_locked() -> None:
        nonlocal seq_val
        with seq_lock:
            seq_val += 1

    results["seq_itertools"] = _run_bench(
        "seq (itertools.count)", _seq_atomic, rounds * 10, warmup=warmup, repeat=repeat
    )
    results["seq_locked"] = _run_bench(
        "seq (threading.Lock)", _seq_locked, rounds * 10, warmup=warmup, repeat=repeat
    )

    decode_mb_s = frame_size_mb * results["decode_zero_copy"]["ops"]

    # -- Baseline comparison --------------------------------------------------
    baseline, baseline_source = _load_baseline(
        fingerprint["device_class"], baseline_path, no_compare=no_compare
    )

    deltas: dict[str, float | None] = {}
    statuses: dict[str, str] = {}

    for key, _label in METRIC_SPEC:
        base_ops = _baseline_metric_ops(baseline, key)
        delta = _compute_delta(results[key]["ops"], base_ops)
        status = _status_for(delta, threshold)
        deltas[key] = delta
        statuses[key] = status

    decode_mb_baseline = _baseline_scalar(baseline, "decode_mb_s")
    decode_mb_delta = _compute_delta(decode_mb_s, decode_mb_baseline)
    decode_mb_status = _status_for(decode_mb_delta, threshold)
    deltas["decode_mb_s"] = decode_mb_delta
    statuses["decode_mb_s"] = decode_mb_status

    _render_results_table(
        results=results,
        decode_mb_s=decode_mb_s,
        baseline=baseline,
        baseline_source=baseline_source,
        deltas=deltas,
        statuses=statuses,
    )

    _render_report_card(
        fingerprint=fingerprint,
        baseline=baseline,
        baseline_source=baseline_source,
        statuses=statuses,
        threshold=threshold,
    )

    # -- Persistence ---------------------------------------------------------
    metrics_payload = _metrics_payload(results, decode_mb_s)

    if output_path:
        _write_run_output(
            Path(output_path),
            fingerprint=fingerprint,
            metrics=metrics_payload,
            baseline=baseline,
            baseline_source=baseline_source,
            deltas=deltas,
            statuses=statuses,
            threshold=threshold,
            config={
                "rounds": rounds,
                "warmup": warmup,
                "repeat": repeat,
                "pin": pin,
                "threshold": threshold,
                "no_compare": no_compare,
                "baseline_override": baseline_path,
            },
        )

    if save_baseline_path:
        _write_saved_baseline(
            Path(save_baseline_path),
            fingerprint=fingerprint,
            metrics=metrics_payload,
        )

    # -- Exit code ------------------------------------------------------------
    regressed = [k for k, s in statuses.items() if s == "regressed"]
    if regressed:
        ctx.exit(2)


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------

def _render_results_table(
    *,
    results: dict[str, dict[str, Any]],
    decode_mb_s: float,
    baseline: dict[str, Any] | None,
    baseline_source: str,
    deltas: dict[str, float | None],
    statuses: dict[str, str],
) -> None:
    comparing = baseline is not None
    title = "Results"
    if comparing:
        bc = baseline.get("device_class", "?") if baseline else "?"
        title = f"Results vs baseline [bold cyan]{bc}[/bold cyan] ({baseline_source})"
    elif baseline_source == "disabled":
        title = "Results (comparison disabled)"
    else:
        title = f"Results (no baseline available: {baseline_source})"

    table = Table(title=title, show_lines=False)
    table.add_column("Benchmark", style="cyan", min_width=24)
    table.add_column("ops/s", justify="right", style="green")
    table.add_column("ns/op", justify="right")
    table.add_column("Baseline ops/s", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Status", justify="center")

    for key, label in METRIC_SPEC:
        r = results[key]
        base_ops = _baseline_metric_ops(baseline, key)
        status = statuses[key]
        table.add_row(
            label,
            f"{r['ops']:,.0f}",
            f"{r['ns']:,.0f}",
            f"{base_ops:,.0f}" if base_ops is not None else "-",
            _format_delta(deltas.get(key)),
            f"[{_status_style(status)}]{status}[/{_status_style(status)}]"
            if _status_style(status) else status,
        )

    decode_mb_baseline = _baseline_scalar(baseline, "decode_mb_s")
    decode_mb_status = statuses["decode_mb_s"]
    table.add_row(
        "Decode throughput (MB/s)",
        f"{decode_mb_s:,.0f}",
        "-",
        f"{decode_mb_baseline:,.0f}" if decode_mb_baseline is not None else "-",
        _format_delta(deltas.get("decode_mb_s")),
        f"[{_status_style(decode_mb_status)}]{decode_mb_status}"
        f"[/{_status_style(decode_mb_status)}]"
        if _status_style(decode_mb_status) else decode_mb_status,
    )

    console.print(table)
    console.print()


def _render_report_card(
    *,
    fingerprint: dict[str, Any],
    baseline: dict[str, Any] | None,
    baseline_source: str,
    statuses: dict[str, str],
    threshold: float,
) -> None:
    total = len(statuses)
    ok = sum(1 for s in statuses.values() if s == "ok")
    improved = sum(1 for s in statuses.values() if s == "improved")
    regressed = sum(1 for s in statuses.values() if s == "regressed")
    na = sum(1 for s in statuses.values() if s == "n/a")

    if baseline is None:
        console.print(
            f"[bold]Device:[/bold] {fingerprint['device_class']}   "
            f"[dim]no baseline available ({baseline_source}); "
            f"run with --save-baseline to capture one[/dim]"
        )
        return

    provisional = baseline.get("provisional")
    provisional_note = "  [yellow](provisional baseline)[/yellow]" if provisional else ""

    if regressed:
        tag = f"[bold red]FAIL[/bold red] ({regressed} regressed)"
    elif improved and not regressed and improved + ok == total:
        tag = f"[bold green]PASS+[/bold green] ({improved} improved / {ok} ok)"
    else:
        tag = f"[bold green]PASS[/bold green] ({ok}/{total} within +/-{threshold * 100:.0f}%)"

    extra = []
    if improved:
        extra.append(f"improved={improved}")
    if na:
        extra.append(f"n/a={na}")
    extra_str = f"  [dim]{', '.join(extra)}[/dim]" if extra else ""

    console.print(
        f"[bold]Device:[/bold] {fingerprint['device_class']}   "
        f"Result: {tag}{extra_str}{provisional_note}"
    )
