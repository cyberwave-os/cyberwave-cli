"""``cyberwave edge bench`` — micro-benchmark the Zenoh SDK hot paths.

Run on an edge device to measure per-call throughput of the critical data
plane functions: header packing, sample decoding, stats accounting, and
sequence numbering.  Prints ops/s and per-op latency for both the current
(optimised) and baseline (lock/copy) variants so improvements are visible
in a single run.

Usage::

    cyberwave edge bench
    cyberwave edge bench --rounds 500000
"""

from __future__ import annotations

import platform
import sys
import time

import click
import numpy as np
from rich.console import Console
from rich.table import Table

console = Console()


def register(edge_group: click.Group) -> None:
    """Register the ``bench`` command on the edge group."""
    edge_group.add_command(bench)


def _run_bench(label: str, fn, rounds: int) -> dict:
    """Run *fn* for *rounds* iterations and return timing stats."""
    fn()  # warm-up call
    t0 = time.perf_counter()
    for _ in range(rounds):
        fn()
    elapsed = time.perf_counter() - t0
    ops = rounds / elapsed
    ns = elapsed / rounds * 1e9
    return {"label": label, "ops": ops, "ns": ns, "elapsed": elapsed}


@click.command("bench")
@click.option(
    "--rounds", "-n", default=100_000, show_default=True,
    help="Number of iterations per benchmark.",
)
def bench(rounds: int) -> None:
    """Benchmark Zenoh SDK hot-path performance on this device.

    Measures header packing, sample decoding (zero-copy vs copy),
    stats accounting, and sequence numbering.  Use this to verify
    optimisation gains on your specific hardware.
    """
    from cyberwave.data.header import HeaderTemplate, CONTENT_TYPE_NUMPY, decode

    console.print(f"[bold]Cyberwave Edge Bench[/bold]")
    console.print(f"Python {sys.version.split()[0]}  •  {platform.machine()}  •  NumPy {np.__version__}")
    console.print()

    shape = (480, 640, 3)
    frame_bytes = np.zeros(shape, dtype=np.uint8).tobytes()
    frame_size_mb = len(frame_bytes) / 1e6

    # -- HeaderTemplate.pack() ------------------------------------------------
    tpl = HeaderTemplate(CONTENT_TYPE_NUMPY, shape=shape, dtype="uint8")

    r_pack = _run_bench(
        "HeaderTemplate.pack()",
        lambda: tpl.pack(frame_bytes),
        rounds,
    )

    # -- decode + frombuffer (zero-copy vs copy) ------------------------------
    wire = tpl.pack(np.random.randint(0, 255, shape, dtype=np.uint8).tobytes())

    def _decode_zero_copy():
        header, payload = decode(wire)
        return np.frombuffer(payload, dtype=header.dtype).reshape(header.shape)

    def _decode_with_copy():
        header, payload = decode(wire)
        return np.frombuffer(payload, dtype=header.dtype).reshape(header.shape).copy()

    r_decode_zc = _run_bench("decode (zero-copy)", _decode_zero_copy, rounds)
    r_decode_cp = _run_bench("decode (with .copy())", _decode_with_copy, rounds)

    # -- Stats counters -------------------------------------------------------
    import collections
    import threading

    dd: dict[str, int] = collections.defaultdict(int)
    channels = [f"cw/twin-{i}/data/camera" for i in range(4)]

    def _stats_lockfree():
        ch = channels[0]
        dd[ch] += 1

    lock = threading.Lock()
    ld: dict[str, int] = {}

    def _stats_locked():
        ch = channels[0]
        with lock:
            ld[ch] = ld.get(ch, 0) + 1

    r_stats_lf = _run_bench("stats (lock-free)", _stats_lockfree, rounds * 5)
    r_stats_lk = _run_bench("stats (with lock)", _stats_locked, rounds * 5)

    # -- Sequence counter -----------------------------------------------------
    import itertools

    counter = itertools.count()
    seq_val = 0

    def _seq_atomic():
        next(counter)

    seq_lock = threading.Lock()

    def _seq_locked():
        nonlocal seq_val
        with seq_lock:
            seq_val += 1

    r_seq_a = _run_bench("seq (itertools.count)", _seq_atomic, rounds * 10)
    r_seq_l = _run_bench("seq (threading.Lock)", _seq_locked, rounds * 10)

    # -- Results table --------------------------------------------------------
    table = Table(title="Results", show_lines=True)
    table.add_column("Benchmark", style="cyan", min_width=24)
    table.add_column("ops/s", justify="right", style="green")
    table.add_column("ns/op", justify="right")
    table.add_column("Speedup", justify="right", style="bold yellow")

    pairs = [
        (r_decode_zc, r_decode_cp, True),
        (r_stats_lf, r_stats_lk, True),
        (r_seq_a, r_seq_l, True),
    ]

    table.add_row(
        r_pack["label"],
        f"{r_pack['ops']:,.0f}",
        f"{r_pack['ns']:,.0f}",
        "",
    )

    for fast, slow, show_speedup in pairs:
        speedup = fast["ops"] / slow["ops"] if slow["ops"] else 0
        table.add_row(
            fast["label"],
            f"{fast['ops']:,.0f}",
            f"{fast['ns']:,.0f}",
            f"{speedup:.1f}x" if show_speedup else "",
        )
        table.add_row(
            f"  └ baseline: {slow['label']}",
            f"{slow['ops']:,.0f}",
            f"{slow['ns']:,.0f}",
            "",
            style="dim",
        )

    console.print(table)
    console.print()

    decode_mb_s = (frame_size_mb * r_decode_zc["ops"])
    console.print(
        f"[bold]Decode throughput:[/bold] {decode_mb_s:,.0f} MB/s "
        f"({r_decode_zc['ops']:,.0f} × {frame_size_mb:.2f} MB frames)"
    )
