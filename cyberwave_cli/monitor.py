"""Collectors and rendering logic for ``cyberwave worker monitor``.

Provides:
- :func:`get_docker_stats` — parse ``docker stats --no-stream`` output.
- :func:`get_gpu_stats` — query ``nvidia-smi`` on Linux, N/A on macOS.
- :func:`check_container_gpu` — inspect whether the container has GPU access.
- :func:`build_dashboard` — assemble a Rich renderable from collected data.
"""

from __future__ import annotations

import json
import platform
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from rich.console import Group as RenderGroup
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    from cyberwave.workers.constants import MONITOR_STATS_KEY
except ImportError:
    MONITOR_STATS_KEY = "cw/_monitor/worker_stats"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DockerStats:
    cpu_percent: str = "0%"
    mem_usage: str = ""
    mem_limit: str = ""
    mem_percent: str = "0%"
    net_io: str = ""
    pids: str = "0"
    running: bool = True


@dataclass
class GpuStats:
    available: bool = False
    utilization: str = ""
    mem_used: str = ""
    mem_total: str = ""
    temperature: str = ""
    message: str = ""


@dataclass
class ZenohChannelStats:
    channel: str = ""
    msgs_per_sec: float = 0.0
    total: int = 0
    bytes_per_sec: float = 0.0
    total_bytes: int = 0


@dataclass
class HookStats:
    name: str = ""
    frames: int = 0
    drops: int = 0


@dataclass
class ModelStats:
    name: str = ""
    device: str = ""
    count: int = 0
    avg_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0


@dataclass
class ThermalPowerStats:
    """Host-level temperature and power readings."""

    available: bool = False
    cpu_temp: float = 0.0
    gpu_temp: float = 0.0
    cpu_power_w: float = 0.0
    gpu_power_w: float = 0.0
    ane_power_w: float = 0.0
    total_power_w: float = 0.0
    avg_power_w: float = 0.0
    message: str = ""


@dataclass
class WorkerSnapshot:
    """All data needed to render one dashboard frame."""

    container_name: str = ""
    uptime: str = ""
    cpu_cores: int = 1
    docker: DockerStats = field(default_factory=DockerStats)
    gpu: GpuStats = field(default_factory=GpuStats)
    thermal_power: ThermalPowerStats = field(default_factory=ThermalPowerStats)
    zenoh_channels: list[ZenohChannelStats] = field(default_factory=list)
    hooks: list[HookStats] = field(default_factory=list)
    models: list[ModelStats] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Docker stats collector
# ---------------------------------------------------------------------------

_DOCKER_STATS_FORMAT = (
    '{"cpu":"{{.CPUPerc}}","mem_usage":"{{.MemUsage}}",'
    '"mem_perc":"{{.MemPerc}}","net":"{{.NetIO}}","pids":"{{.PIDs}}"}'
)


def get_docker_stats(container_name: str) -> DockerStats:
    """Run ``docker stats --no-stream`` and parse the JSON output."""
    try:
        result = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                _DOCKER_STATS_FORMAT,
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return DockerStats(running=False)
        data = json.loads(result.stdout.strip())
        parts = data.get("mem_usage", "").split("/")
        return DockerStats(
            cpu_percent=data.get("cpu", "0%").strip(),
            mem_usage=parts[0].strip() if parts else "",
            mem_limit=parts[1].strip() if len(parts) > 1 else "",
            mem_percent=data.get("mem_perc", "0%").strip(),
            net_io=data.get("net", ""),
            pids=data.get("pids", "0").strip(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return DockerStats(running=False)


def get_container_cpu_quota(container_name: str) -> int:
    """Return the number of CPU cores available to the container.

    Reads ``NanoCpus`` from ``docker inspect``.  Falls back to the host
    core count when the container has no CPU limit.
    """
    import os

    host_cores = os.cpu_count() or 1
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.HostConfig.NanoCpus}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            nano = int(result.stdout.strip())
            if nano > 0:
                return max(1, round(nano / 1_000_000_000))
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return host_cores


# ---------------------------------------------------------------------------
# GPU stats collector
# ---------------------------------------------------------------------------


def check_container_gpu(container_name: str) -> bool:
    """Return True if the container was started with GPU access."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .HostConfig.DeviceRequests}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip().lower()
            return "gpu" in text or "nvidia" in text
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return False


def get_gpu_stats(container_name: str) -> GpuStats:
    """Query GPU metrics.  Linux uses ``nvidia-smi``; macOS returns N/A."""
    if platform.system() == "Darwin":
        return GpuStats(message="N/A on macOS")

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            if len(parts) >= 4:
                return GpuStats(
                    available=True,
                    utilization=f"{parts[0]}%",
                    mem_used=f"{parts[1]} MiB",
                    mem_total=f"{parts[2]} MiB",
                    temperature=f"{parts[3]}°C",
                )
    except FileNotFoundError:
        has_gpu = check_container_gpu(container_name)
        if has_gpu:
            return GpuStats(message="N/A — install NVIDIA drivers for GPU metrics")
        return GpuStats(message="Container has no GPU access")
    except subprocess.TimeoutExpired:
        pass
    return GpuStats(message="N/A")


# ---------------------------------------------------------------------------
# Container uptime
# ---------------------------------------------------------------------------


def get_container_uptime(container_name: str) -> str:
    """Return a human-readable uptime string for the container."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.StartedAt}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            from datetime import datetime, timezone

            started_str = result.stdout.strip()
            # Docker timestamps: 2024-01-15T10:30:00.123456789Z
            # Truncate nanoseconds to microseconds for parsing.
            if "." in started_str:
                base, frac = started_str.split(".", 1)
                frac = frac.rstrip("Z")[:6]
                started_str = f"{base}.{frac}+00:00"
            started = datetime.fromisoformat(started_str)
            delta = datetime.now(timezone.utc) - started
            total_s = int(delta.total_seconds())
            if total_s < 0:
                return "just started"
            hours, remainder = divmod(total_s, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                return f"{hours}h {minutes}m"
            if minutes > 0:
                return f"{minutes}m {seconds}s"
            return f"{seconds}s"
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Parallel collection helper
# ---------------------------------------------------------------------------

_collector_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cw-collect")


def collect_host_metrics(
    container_name: str,
) -> tuple[DockerStats, GpuStats, str]:
    """Collect Docker stats, GPU stats, and uptime in parallel."""
    docker_fut = _collector_pool.submit(get_docker_stats, container_name)
    gpu_fut = _collector_pool.submit(get_gpu_stats, container_name)
    uptime_fut = _collector_pool.submit(get_container_uptime, container_name)
    return docker_fut.result(), gpu_fut.result(), uptime_fut.result()


# ---------------------------------------------------------------------------
# Container IP discovery (for Zenoh TCP connect)
# ---------------------------------------------------------------------------

ZENOH_LISTEN_PORT = 7447
"""Default Zenoh TCP listener port injected by the worker manager."""


def get_container_ip(container_name: str) -> str | None:
    """Return the bridge-network IP of a Docker container, or ``None``.

    Host-mode containers have no bridge IP; some Docker versions return
    the literal ``"invalid IP"`` instead of an empty string.
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            ip = result.stdout.strip()
            if ip and _is_valid_ip(ip):
                return ip
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _is_valid_ip(value: str) -> bool:
    """Return True if *value* looks like a valid IPv4 address."""
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


# ---------------------------------------------------------------------------
# Zenoh stats rate tracker
# ---------------------------------------------------------------------------


class RateTracker:
    """Stateful tracker that converts cumulative Zenoh counters into rates.

    Call :meth:`update` with each new transport snapshot.  The first call
    seeds internal state and returns zero rates (no spike).
    """

    def __init__(self) -> None:
        self._prev_publish: dict[str, int] = {}
        self._prev_recv: dict[str, int] = {}
        self._prev_publish_bytes: dict[str, int] = {}
        self._prev_recv_bytes: dict[str, int] = {}
        self._prev_ts: float = 0.0
        self._seeded = False

    def update(self, transport: dict[str, Any]) -> list[ZenohChannelStats]:
        """Return per-channel stats from the latest *transport* snapshot."""
        now = time.time()

        publish = transport.get("publish", {})
        recv = transport.get("recv", {})
        publish_bytes = transport.get("publish_bytes", {})
        recv_bytes = transport.get("recv_bytes", {})

        if not self._seeded:
            self._prev_publish = dict(publish)
            self._prev_recv = dict(recv)
            self._prev_publish_bytes = dict(publish_bytes)
            self._prev_recv_bytes = dict(recv_bytes)
            self._prev_ts = now
            self._seeded = True
            return self._zeros(publish, recv)

        elapsed = now - self._prev_ts
        if elapsed <= 0:
            elapsed = 1.0

        all_channels: set[str] = set(publish.keys()) | set(recv.keys())
        results: list[ZenohChannelStats] = []

        for ch in sorted(all_channels):
            total = publish.get(ch, 0) + recv.get(ch, 0)
            total_bytes = publish_bytes.get(ch, 0) + recv_bytes.get(ch, 0)

            prev_total = self._prev_publish.get(ch, 0) + self._prev_recv.get(ch, 0)
            prev_bytes = self._prev_publish_bytes.get(ch, 0) + self._prev_recv_bytes.get(ch, 0)

            msg_rate = (total - prev_total) / elapsed
            byte_rate = (total_bytes - prev_bytes) / elapsed

            results.append(
                ZenohChannelStats(
                    channel=_display_channel(ch),
                    msgs_per_sec=max(msg_rate, 0.0),
                    total=total,
                    bytes_per_sec=max(byte_rate, 0.0),
                    total_bytes=total_bytes,
                )
            )

        self._prev_publish = dict(publish)
        self._prev_recv = dict(recv)
        self._prev_publish_bytes = dict(publish_bytes)
        self._prev_recv_bytes = dict(recv_bytes)
        self._prev_ts = now
        return results

    @staticmethod
    def _zeros(publish: dict[str, int], recv: dict[str, int]) -> list[ZenohChannelStats]:
        """Return zero-rate entries for the seed call."""
        all_ch = set(publish.keys()) | set(recv.keys())
        return [
            ZenohChannelStats(
                channel=_display_channel(ch),
                total=publish.get(ch, 0) + recv.get(ch, 0),
            )
            for ch in sorted(all_ch)
        ]


def _display_channel(ch: str) -> str:
    """Strip the Zenoh key prefix for display."""
    parts = ch.split("/", 2)
    return parts[2] if len(parts) >= 3 else ch


def parse_hook_stats(hooks_data: dict[str, Any]) -> list[HookStats]:
    results: list[HookStats] = []
    for name, data in sorted(hooks_data.items()):
        results.append(
            HookStats(
                name=name,
                frames=data.get("frames", 0),
                drops=data.get("drops", 0),
            )
        )
    return results


def parse_model_stats(models_data: list[dict[str, Any]]) -> list[ModelStats]:
    results: list[ModelStats] = []
    for m in models_data:
        results.append(
            ModelStats(
                name=m.get("name", ""),
                device=m.get("device", ""),
                count=m.get("count", 0),
                avg_ms=m.get("avg_ms", 0.0),
                p95_ms=m.get("p95_ms", 0.0),
                p99_ms=m.get("p99_ms", 0.0),
            )
        )
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_percent(s: str) -> float:
    try:
        return float(s.strip().rstrip("%"))
    except (ValueError, AttributeError):
        return 0.0


def _colorize_temp(temp_c: float) -> str:
    """Return a Rich-markup string with green/yellow/red coloring."""
    label = f"{temp_c:.1f}°C"
    if temp_c >= 80:
        return f"[red]{label}[/red]"
    if temp_c >= 60:
        return f"[yellow]{label}[/yellow]"
    return f"[green]{label}[/green]"


def _format_bytes_rate(bps: float) -> str:
    """Format a bytes-per-second value into a human-readable string."""
    if bps <= 0:
        return "-"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} MB/s"
    if bps >= 1_000:
        return f"{bps / 1_000:.1f} kB/s"
    return f"{bps:.0f} B/s"


# ---------------------------------------------------------------------------
# Dashboard builder
# ---------------------------------------------------------------------------


def build_dashboard(snap: WorkerSnapshot) -> RenderGroup:
    """Build a Rich renderable from a :class:`WorkerSnapshot`."""
    parts: list[Any] = []

    # Header
    header = Text()
    header.append("Cyberwave Worker Monitor", style="bold cyan")
    header.append(f"  (container: {snap.container_name})", style="dim")
    parts.append(header)
    parts.append(Text("Press Ctrl+C to stop.\n", style="dim"))

    if not snap.docker.running:
        parts.append(
            Panel(
                Text("Container is not running.", style="bold red"),
                border_style="red",
            )
        )
        return RenderGroup(*parts)

    # --- Resource Usage table ---
    res_table = Table(title="Resource Usage", expand=True, show_edge=False)
    res_table.add_column("Metric", style="cyan", min_width=10)
    res_table.add_column("Value")
    res_table.add_column("Detail", style="dim")

    cpu_raw = _parse_percent(snap.docker.cpu_percent)
    cores_used = cpu_raw / 100.0
    total_cores = snap.cpu_cores
    overall_pct = cpu_raw / total_cores if total_cores > 0 else 0.0
    res_table.add_row(
        "CPU",
        f"{cores_used:.2f} / {total_cores} cores ({overall_pct:.1f}%)",
        f"PIDs: {snap.docker.pids}",
    )

    res_table.add_row(
        "Memory",
        snap.docker.mem_usage,
        f"/ {snap.docker.mem_limit}" if snap.docker.mem_limit else "",
    )

    if snap.gpu.available:
        res_table.add_row("GPU", snap.gpu.utilization, snap.gpu.temperature)
        res_table.add_row("GPU Mem", snap.gpu.mem_used, f"/ {snap.gpu.mem_total}")
    else:
        res_table.add_row("GPU", snap.gpu.message or "N/A", "")

    tp = snap.thermal_power
    if tp.available:
        temp_parts = []
        if tp.cpu_temp > 0:
            temp_parts.append(f"CPU: {_colorize_temp(tp.cpu_temp)}")
        if tp.gpu_temp > 0:
            temp_parts.append(f"GPU: {_colorize_temp(tp.gpu_temp)}")
        res_table.add_row(
            "Temp",
            " | ".join(temp_parts) if temp_parts else "N/A",
            "",
        )

        if tp.total_power_w > 0:
            power_detail_parts = []
            if tp.cpu_power_w > 0:
                power_detail_parts.append(f"CPU: {tp.cpu_power_w:.1f}W")
            if tp.gpu_power_w > 0:
                power_detail_parts.append(f"GPU: {tp.gpu_power_w:.1f}W")
            if tp.ane_power_w > 0:
                power_detail_parts.append(f"ANE: {tp.ane_power_w:.1f}W")
            res_table.add_row(
                "Power",
                f"{tp.total_power_w:.1f}W (avg: {tp.avg_power_w:.1f}W)",
                " | ".join(power_detail_parts),
            )
        elif tp.avg_power_w > 0:
            res_table.add_row("Power", f"avg: {tp.avg_power_w:.1f}W", "")
    else:
        res_table.add_row("Temp", "N/A", "")
        res_table.add_row("Power", "N/A", "")

    net_parts = [p.strip() for p in snap.docker.net_io.split("/")]
    net_display = (
        f"{net_parts[0]} in / {net_parts[1]} out" if len(net_parts) == 2 else snap.docker.net_io
    )
    res_table.add_row("Network", net_display, "")

    if snap.uptime:
        res_table.add_row("Uptime", snap.uptime, "")

    parts.append(Panel(res_table, border_style="dim"))

    # --- Zenoh Throughput table ---
    if snap.zenoh_channels:
        zen_table = Table(title="Zenoh Throughput", expand=True, show_edge=False)
        zen_table.add_column("Channel", style="cyan")
        zen_table.add_column("msgs/s", justify="right")
        zen_table.add_column("Throughput", justify="right")
        zen_table.add_column("Total", justify="right", style="dim")
        for ch in snap.zenoh_channels:
            if "_monitor/" in ch.channel:
                continue
            zen_table.add_row(
                ch.channel,
                f"{ch.msgs_per_sec:.1f}",
                _format_bytes_rate(ch.bytes_per_sec),
                f"{ch.total:,}",
            )
        parts.append(Panel(zen_table, border_style="dim"))

    # --- Worker Hooks table ---
    if snap.hooks:
        hook_table = Table(title="Worker Hooks", expand=True, show_edge=False)
        hook_table.add_column("Hook", style="cyan")
        hook_table.add_column("Frames", justify="right")
        hook_table.add_column("Drops", justify="right", style="yellow")
        for h in snap.hooks:
            hook_table.add_row(
                h.name,
                f"{h.frames:,}",
                f"{h.drops:,}" if h.drops > 0 else "[dim]0[/dim]",
            )
        parts.append(Panel(hook_table, border_style="dim"))

    # --- Model Inference table ---
    if snap.models:
        model_table = Table(title="Model Inference", expand=True, show_edge=False)
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Device", style="dim")
        model_table.add_column("Inferences", justify="right")
        model_table.add_column("Avg ms", justify="right")
        model_table.add_column("P95 ms", justify="right")
        model_table.add_column("P99 ms", justify="right")
        for m in snap.models:
            model_table.add_row(
                m.name,
                m.device,
                f"{m.count:,}",
                f"{m.avg_ms:.1f}" if m.avg_ms else "-",
                f"{m.p95_ms:.1f}" if m.p95_ms else "-",
                f"{m.p99_ms:.1f}" if m.p99_ms else "-",
            )
        parts.append(Panel(model_table, border_style="dim"))

    return RenderGroup(*parts)


# ---------------------------------------------------------------------------
# Thermal / power readers
# ---------------------------------------------------------------------------


class _RunningAverage:
    """Incrementally compute a running mean."""

    __slots__ = ("_sum", "_count")

    def __init__(self) -> None:
        self._sum = 0.0
        self._count = 0

    def add(self, value: float) -> float:
        self._sum += value
        self._count += 1
        return self._sum / self._count


class _ExponentialMovingAverage:
    """Exponential moving average for smoothing noisy sensor readings."""

    __slots__ = ("_alpha", "_value")

    def __init__(self, alpha: float = 0.3) -> None:
        self._alpha = alpha
        self._value: float | None = None

    def add(self, value: float) -> float:
        if self._value is None:
            self._value = value
        else:
            self._value = self._alpha * value + (1.0 - self._alpha) * self._value
        return self._value


class MacMonReader:
    """Read temperature and power from ``macmon pipe`` on Apple Silicon.

    Spawns the process in the background and parses its newline-delimited
    JSON output.  If ``macmon`` is not installed, :meth:`start` returns
    ``False`` and the monitor degrades gracefully.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest = ThermalPowerStats()
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._avg = _RunningAverage()

    def start(self) -> bool:
        try:
            self._proc = subprocess.Popen(
                ["macmon", "pipe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            return False

        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        return True

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = self._proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            temp = data.get("temp", {})
            total_power = float(data.get("all_power", 0.0))
            if total_power > 0:
                avg = self._avg.add(total_power)
            else:
                avg = self._avg._sum / max(self._avg._count, 1)

            stats = ThermalPowerStats(
                available=True,
                cpu_temp=float(temp.get("cpu_temp_avg", 0.0)),
                gpu_temp=float(temp.get("gpu_temp_avg", 0.0)),
                cpu_power_w=float(data.get("cpu_power", 0.0)),
                gpu_power_w=float(data.get("gpu_power", 0.0)),
                ane_power_w=float(data.get("ane_power", 0.0)),
                total_power_w=total_power,
                avg_power_w=avg,
            )
            with self._lock:
                self._latest = stats

    def latest(self) -> ThermalPowerStats:
        with self._lock:
            return self._latest

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


class LinuxThermalReader:
    """Read temperature from sysfs and power from RAPL / Jetson INA / battery.

    All data comes from the kernel's sysfs interface -- no external tools.
    """

    _CPU_ZONE_TYPES = {"x86_pkg_temp", "coretemp", "cpu-thermal", "cpu_thermal", "soc_thermal"}

    def __init__(self) -> None:
        self._thermal_zones: list[str] = []
        self._rapl_path: str | None = None
        self._jetson_power_path: str | None = None
        self._battery_power_path: str | None = None
        self._prev_energy_uj: int | None = None
        self._prev_energy_ts: float = 0.0
        self._avg = _RunningAverage()
        self._temp_ema = _ExponentialMovingAverage(alpha=0.3)
        self._available = False

    def start(self) -> bool:
        from pathlib import Path

        # -- Discover thermal zones (prefer CPU-specific ones) --
        thermal_base = Path("/sys/class/thermal")
        cpu_zones: list[str] = []
        all_zones: list[str] = []
        if thermal_base.exists():
            for zone in sorted(thermal_base.glob("thermal_zone*")):
                temp_file = zone / "temp"
                if not temp_file.exists():
                    continue
                all_zones.append(str(temp_file))
                type_file = zone / "type"
                if type_file.exists():
                    try:
                        zone_type = type_file.read_text().strip().lower()
                    except OSError:
                        continue
                    if zone_type in self._CPU_ZONE_TYPES or "cpu" in zone_type:
                        cpu_zones.append(str(temp_file))
        self._thermal_zones = cpu_zones if cpu_zones else all_zones

        # -- Discover power source (priority order) --
        # 1. RAPL (x86)
        rapl_base = Path("/sys/class/powercap")
        rapl_energy = rapl_base / "intel-rapl:0" / "energy_uj"
        if rapl_energy.exists():
            try:
                rapl_energy.read_text()
                self._rapl_path = str(rapl_energy)
            except PermissionError:
                pass

        # 2. Jetson INA3221 total board power (VDD_IN)
        if self._rapl_path is None:
            for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
                name_file = hwmon / "name"
                if name_file.exists():
                    try:
                        name = name_file.read_text().strip()
                    except OSError:
                        continue
                    if "ina3221" in name.lower():
                        for power_file in sorted(hwmon.glob("power*_input")):
                            self._jetson_power_path = str(power_file)
                            break
                        if self._jetson_power_path:
                            break

        # 3. Battery
        if self._rapl_path is None and self._jetson_power_path is None:
            for bat in Path("/sys/class/power_supply").glob("BAT*"):
                pnow = bat / "power_now"
                if pnow.exists():
                    self._battery_power_path = str(pnow)
                    break

        self._available = bool(self._thermal_zones) or self._has_power_source()
        return self._available

    def _has_power_source(self) -> bool:
        return any(
            [
                self._rapl_path,
                self._jetson_power_path,
                self._battery_power_path,
            ]
        )

    def latest(self) -> ThermalPowerStats:
        if not self._available:
            return ThermalPowerStats()

        cpu_temp = self._read_thermal_zones()
        power_w = self._read_power()
        avg = self._avg.add(power_w) if power_w > 0 else (self._avg._sum / max(self._avg._count, 1))

        return ThermalPowerStats(
            available=True,
            cpu_temp=cpu_temp,
            total_power_w=power_w,
            avg_power_w=avg,
        )

    def _read_thermal_zones(self) -> float:
        """Return the smoothed highest CPU thermal zone reading in Celsius."""
        max_temp = 0.0
        for path in self._thermal_zones:
            try:
                with open(path) as f:
                    raw = f.read().strip()
                temp_c = int(raw) / 1000.0
                if temp_c > max_temp:
                    max_temp = temp_c
            except (OSError, ValueError):
                continue
        if max_temp > 0:
            return self._temp_ema.add(max_temp)
        return max_temp

    def _read_power(self) -> float:
        """Return instantaneous power in watts from the best available source."""
        if self._rapl_path is not None:
            return self._read_rapl()
        if self._jetson_power_path is not None:
            return self._read_jetson()
        if self._battery_power_path is not None:
            return self._read_battery()
        return 0.0

    def _read_rapl(self) -> float:
        """Compute watts from the RAPL cumulative energy counter."""
        try:
            with open(self._rapl_path) as f:  # type: ignore[arg-type]
                energy_uj = int(f.read().strip())
        except (OSError, ValueError):
            return 0.0

        now = time.time()
        if self._prev_energy_uj is not None:
            elapsed = now - self._prev_energy_ts
            if elapsed > 0:
                delta_uj = energy_uj - self._prev_energy_uj
                if delta_uj < 0:
                    # Counter wrapped (32-bit or 64-bit overflow)
                    delta_uj = 0
                watts = (delta_uj / 1_000_000.0) / elapsed
                self._prev_energy_uj = energy_uj
                self._prev_energy_ts = now
                return watts

        self._prev_energy_uj = energy_uj
        self._prev_energy_ts = now
        return 0.0

    def _read_jetson(self) -> float:
        """Read Jetson INA3221 power in milliwatts, return watts."""
        try:
            with open(self._jetson_power_path) as f:  # type: ignore[arg-type]
                mw = int(f.read().strip())
            return mw / 1000.0
        except (OSError, ValueError):
            return 0.0

    def _read_battery(self) -> float:
        """Read battery power_now in microwatts, return watts."""
        try:
            with open(self._battery_power_path) as f:  # type: ignore[arg-type]
                uw = int(f.read().strip())
            return uw / 1_000_000.0
        except (OSError, ValueError):
            return 0.0

    def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Zenoh stats subscriber (reads from the worker's stats channel)
# ---------------------------------------------------------------------------


class ZenohStatsReader:
    """Subscribe to the worker's ``cw/_monitor/worker_stats`` Zenoh key.

    The worker runtime publishes a JSON stats snapshot periodically on a
    raw Zenoh key (bypassing DataBus validation).  This reader connects
    to the container's Zenoh TCP listener and picks up the latest snapshot.
    Falls back to empty data when Zenoh is unavailable.
    """

    def __init__(self) -> None:
        self._latest: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._session: Any = None
        self._subscription: Any = None

    def start(
        self,
        *,
        connect: list[str] | None = None,
        container_name: str | None = None,
    ) -> bool:
        """Attempt to open a Zenoh session and subscribe.  Returns False on failure.

        When *container_name* is given and *connect* is not, the container's
        bridge-network IP is discovered automatically and used as a TCP
        connect endpoint.  This is required on macOS where multicast
        discovery between the host and Docker Desktop's Linux VM doesn't work.
        """
        try:
            import zenoh
        except ImportError:
            return False
        try:
            if not connect and container_name:
                if platform.system() == "Darwin":
                    connect = [f"tcp/127.0.0.1:{ZENOH_LISTEN_PORT}"]
                else:
                    ip = get_container_ip(container_name)
                    connect = (
                        [f"tcp/{ip}:{ZENOH_LISTEN_PORT}"]
                        if ip
                        else [f"tcp/127.0.0.1:{ZENOH_LISTEN_PORT}"]
                    )
            cfg = zenoh.Config()
            if connect:
                cfg.insert_json5("connect/endpoints", json.dumps(connect))
            cfg.insert_json5("transport/shared_memory/enabled", "false")
            cfg.insert_json5("scouting/multicast/enabled", "false")

            self._session = zenoh.open(cfg)

            def _on_sample(sample: Any) -> None:
                try:
                    raw = bytes(sample.payload)
                except Exception:
                    try:
                        raw = sample.payload.to_bytes()
                    except Exception:
                        return
                try:
                    data = json.loads(raw)
                    with self._lock:
                        self._latest = data
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            self._subscription = self._session.declare_subscriber(
                MONITOR_STATS_KEY,
                _on_sample,
            )
            return True
        except Exception:
            return False

    def latest(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def stop(self) -> None:
        if self._subscription is not None:
            try:
                self._subscription.undeclare()
            except Exception:
                pass
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
