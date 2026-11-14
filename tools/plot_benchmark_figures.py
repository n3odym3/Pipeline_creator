"""Generate publication-ready figures from ``benchmark/benchmark_report.csv``.

Can be invoked standalone::

    python tools/plot_benchmark_figures.py --csv benchmark/benchmark_report.csv --out-dir benchmark/figures

or imported by ``tools/run_benchmark.py`` via :func:`generate_figures_from_csv`.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import BENCHMARK_DIR

# Okabe-Ito colorblind-safe palette
BLUE = "#0072B2"
VERMILION = "#D55E00"
GREEN = "#009E73"
AMBER = "#E69F00"
PURPLE = "#CC79A7"
SKY = "#56B4E9"
CHARCOAL = "#3B3B3B"
SLATE = "#6C757D"

RowDict = Dict[str, object]
CsvIndex = Dict[Tuple[str, str, str], RowDict]

NUMERIC_FIELDS = (
    "duration_s",
    "emit_ops_s",
    "recv_ops_s",
    "bandwidth_mb_s",
    "count",
    "min_us",
    "mean_us",
    "std_us",
    "p50_us",
    "p95_us",
    "p99_us",
    "max_us",
    "rss_peak_mb",
    "cpu_mean_pct",
    "dpg_fps_mean",
)


def _apply_paper_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
            "font.size": 9.5,
            "axes.titlesize": 10.5,
            "axes.titleweight": "bold",
            "axes.labelsize": 9.5,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.5,
            "figure.titlesize": 12.0,
            "figure.titleweight": "bold",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linestyle": "--",
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.facecolor": "white",
            "figure.facecolor": "white",
        }
    )


def load_benchmark_csv(csv_path: Path) -> CsvIndex:
    """Load ``benchmark_report.csv`` indexed by ``(scenario, mode, metric)``."""
    index: CsvIndex = {}
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row: RowDict = dict(raw)
            for field in NUMERIC_FIELDS:
                val = raw.get(field, "")
                try:
                    row[field] = float(val) if val not in ("", None) else 0.0
                except ValueError:
                    row[field] = 0.0
            key = (str(raw["scenario"]), str(raw["mode"]), str(raw["metric"]))
            index[key] = row
    return index


def _save_figure(fig: plt.Figure, out_dir: Path, stem: str, formats: Sequence[str]) -> List[Path]:
    saved: List[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        ext = fmt.strip().lstrip(".").lower()
        if not ext:
            continue
        target = out_dir / f"{stem}.{ext}"
        fig.savefig(target, format=ext, dpi=300, bbox_inches="tight")
        saved.append(target)
    plt.close(fig)
    return saved


def _fmt_us(val_us: float) -> str:
    if val_us >= 1000.0:
        return f"{val_us / 1000.0:.2f} ms"
    if val_us >= 100.0:
        return f"{val_us:.0f} µs"
    if val_us >= 10.0:
        return f"{val_us:.1f} µs"
    return f"{val_us:.2f} µs"


def _fmt_kops(ops_s: float) -> str:
    if ops_s >= 1000.0:
        return f"{ops_s / 1000.0:.1f}k"
    return f"{ops_s:.0f}"


def _plot_fig1_primitives(index: CsvIndex, out_dir: Path, formats: Sequence[str]) -> List[Path]:
    """Fig 1: Routing & transport primitives (latency vs throughput)."""
    specs = [
        ("hop_1to1_token", "1:1 Direct Link\n(Token)"),
        ("hop_1to1_numpy_1kb", "1:1 Direct Link\n(NumPy 1 KB)"),
        ("hop_1to1_numpy_1mb", "1:1 Direct Link\n(NumPy 1 MB)"),
        ("routing_gate", "Gate\n(Conditional)"),
        ("routing_linkbus", "LinkBus\n(Shared Bus)"),
    ]
    rows: List[Tuple[str, RowDict, Optional[RowDict]]] = []
    for scen, label in specs:
        e2e = index.get((scen, "headless", "e2e_dag_ns"))
        hop = index.get((scen, "headless", "link_hop_ns"))
        if e2e is not None:
            rows.append((label, e2e, hop))

    if not rows:
        return []

    labels = [r[0] for r in rows]
    y = np.arange(len(rows))
    e2e_p50 = np.array([float(r[1]["p50_us"]) for r in rows])
    e2e_p95 = np.array([float(r[1]["p95_us"]) for r in rows])
    hop_p50 = np.array([float(r[2]["p50_us"]) if r[2] else 0.0 for r in rows])
    ops_k = np.array([float(r[1]["recv_ops_s"]) / 1000.0 for r in rows])

    fig, (ax_lat, ax_thr) = plt.subplots(1, 2, figsize=(12.2, 4.4), gridspec_kw={"width_ratios": [1.15, 1.0]})
    fig.subplots_adjust(wspace=0.32)

    h = 0.34
    bars_e2e = ax_lat.barh(y + h / 2, e2e_p50, height=h, color=BLUE, label="End-to-End DAG (p50, whisker = p95)")
    ax_lat.barh(y - h / 2, hop_p50, height=h, color=SKY, label="Single Link Hop (p50)")

    # Asymmetric horizontal whiskers from p50 to p95 on e2e bars
    right_err = np.maximum(0.0, e2e_p95 - e2e_p50)
    ax_lat.errorbar(
        e2e_p50,
        y + h / 2,
        xerr=[np.zeros_like(right_err), right_err],
        fmt="none",
        ecolor=CHARCOAL,
        elinewidth=1.2,
        capsize=3,
    )

    max_lat_x = float(np.max(e2e_p95)) * 1.42 if len(e2e_p95) else 5.0
    ax_lat.set_xlim(0, max_lat_x)
    ax_lat.set_yticks(y)
    ax_lat.set_yticklabels(labels)
    ax_lat.invert_yaxis()
    ax_lat.set_xlabel("Latency (µs, lower is better)")
    ax_lat.set_title("(a) In-Process Dispatch & Routing Latency")
    ax_lat.legend(loc="upper right", frameon=True, framealpha=0.92, fontsize=8.0)

    for idx, bar in enumerate(bars_e2e):
        x_text = max(e2e_p95[idx], e2e_p50[idx]) + max_lat_x * 0.015
        ax_lat.text(
            x_text,
            bar.get_y() + bar.get_height() / 2,
            f"{e2e_p50[idx]:.2f} µs (p95: {e2e_p95[idx]:.2f})",
            va="center",
            ha="left",
            fontsize=8.0,
            color=CHARCOAL,
        )

    # Calibration note if present
    cal_pair = index.get(("calibration_profiler_self_overhead", "headless", "perf_counter_pair_ns"))
    cal_hop = index.get(("calibration_profiler_self_overhead", "headless", "record_hop_call_ns"))
    if cal_pair or cal_hop:
        cal_metrics = []
        if cal_pair:
            cal_metrics.append(f"timer={float(cal_pair['mean_us']):.2f} µs")
        if cal_hop:
            cal_metrics.append(f"record_hop={float(cal_hop['mean_us']):.2f} µs")
        ax_lat.text(
            0.0,
            -0.21,
            "Profiler overhead: " + ", ".join(cal_metrics),
            transform=ax_lat.transAxes,
            fontsize=8.0,
            color=SLATE,
        )

    bar_colors = [GREEN, GREEN, GREEN, AMBER, PURPLE][: len(rows)]
    bars_thr = ax_thr.barh(y, ops_k, height=0.55, color=bar_colors)
    max_thr = float(np.max(ops_k)) * 1.24 if len(ops_k) else 100.0
    ax_thr.set_xlim(0, max_thr)
    ax_thr.set_yticks(y)
    ax_thr.set_yticklabels([""] * len(rows))
    ax_thr.invert_yaxis()
    ax_thr.set_xlabel("Sustained Throughput (k msg/s, higher is better)")
    ax_thr.set_title("(b) Routing & Transport Throughput")

    for idx, bar in enumerate(bars_thr):
        ax_thr.text(
            ops_k[idx] + max_thr * 0.015,
            bar.get_y() + bar.get_height() / 2,
            f"{ops_k[idx]:.1f} k/s",
            va="center",
            ha="left",
            fontsize=8.3,
            fontweight="bold",
            color=CHARCOAL,
        )

    fig.suptitle("Pipeline Creator In-Process Framework Primitives (Headless Mode)", y=1.03)
    return _save_figure(fig, out_dir, "fig1_framework_primitives", formats)


def _plot_fig2_topology_scaling(index: CsvIndex, out_dir: Path, formats: Sequence[str]) -> List[Path]:
    """Fig 2: Topological scaling across chains, fan-out widths, and dense meshes."""
    chain_pts: List[Tuple[int, RowDict]] = []
    for d in (2, 5, 10, 20, 40):
        row = index.get((f"chain_d{d}", "headless", "e2e_dag_ns"))
        if row:
            chain_pts.append((d, row))

    fanout_pts: List[Tuple[int, RowDict]] = []
    for k in (1, 2, 4, 8, 16, 32, 64):
        row = index.get((f"fanout_k{k}", "headless", "e2e_dag_ns"))
        if row:
            fanout_pts.append((k, row))

    mesh_specs = [
        ("mesh_2x4", "2×4\n(8 nodes)"),
        ("mesh_4x4", "4×4\n(16 nodes)"),
        ("mesh_4x8", "4×8\n(32 nodes)"),
    ]
    mesh_pts: List[Tuple[str, RowDict]] = []
    for scen, label in mesh_specs:
        row = index.get((scen, "headless", "e2e_dag_ns"))
        if row:
            mesh_pts.append((label, row))

    if not chain_pts and not fanout_pts and not mesh_pts:
        return []

    fig, axes = plt.subplots(1, 3, figsize=(15.0, 4.6))
    fig.subplots_adjust(wspace=0.38)

    # Panel (a): Serial Chains
    ax = axes[0]
    if chain_pts:
        xs = np.array([p[0] for p in chain_pts])
        p50 = np.array([float(p[1]["p50_us"]) for p in chain_pts])
        p95 = np.array([float(p[1]["p95_us"]) for p in chain_pts])
        ops_k = np.array([float(p[1]["recv_ops_s"]) / 1000.0 for p in chain_pts])

        l1 = ax.plot(xs, p50, marker="o", color=BLUE, linewidth=2.0, label="E2E DAG p50 (µs)")
        band = ax.fill_between(xs, p50, p95, color=BLUE, alpha=0.18, label="p50–p95 tail band")
        ax.set_xlabel("Serial Chain Depth (nodes)")
        ax.set_ylabel("End-to-End DAG Latency (µs)", color=BLUE)
        ax.tick_params(axis="y", labelcolor=BLUE)
        ax.set_xticks(xs)

        ax2 = ax.twinx()
        ax2.spines["top"].set_visible(False)
        ax2.grid(False)
        l2 = ax2.plot(xs, ops_k, marker="s", linestyle="--", color=VERMILION, linewidth=1.8, label="Throughput (k hops/s)")
        ax2.set_ylabel("Aggregate Link Hops (k/s)", color=VERMILION)
        ax2.tick_params(axis="y", labelcolor=VERMILION)
        ax2.set_ylim(0, max(ops_k) * 1.25)

        lines = [*l1, band, *l2]
        ax.legend(lines, [ln.get_label() for ln in lines], loc="upper left", frameon=True, framealpha=0.9)
    ax.set_title("(a) Serial Chain Scaling (D = 2..40)")

    # Panel (b): Parallel Fan-out
    ax = axes[1]
    if fanout_pts:
        xs = np.array([p[0] for p in fanout_pts])
        p50 = np.array([float(p[1]["p50_us"]) for p in fanout_pts])
        p95 = np.array([float(p[1]["p95_us"]) for p in fanout_pts])
        ops_k = np.array([float(p[1]["recv_ops_s"]) / 1000.0 for p in fanout_pts])

        l1 = ax.plot(xs, p50, marker="o", color=BLUE, linewidth=2.0, label="Branch DAG p50 (µs)")
        band = ax.fill_between(xs, p50, p95, color=BLUE, alpha=0.18, label="p50–p95 tail band")
        ax.set_xlabel("Fan-Out Branch Count (K)")
        ax.set_ylabel("End-to-End DAG Latency (µs)", color=BLUE)
        ax.tick_params(axis="y", labelcolor=BLUE)
        ax.set_xscale("log", base=2)
        ax.set_xticks(xs)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda val, _: f"{int(val)}"))

        ax2 = ax.twinx()
        ax2.spines["top"].set_visible(False)
        ax2.grid(False)
        l2 = ax2.plot(xs, ops_k, marker="s", linestyle="--", color=GREEN, linewidth=1.8, label="Throughput (k msg/s)")
        ax2.set_ylabel("Aggregate Deliveries (k/s)", color=GREEN)
        ax2.tick_params(axis="y", labelcolor=GREEN)
        ax2.set_ylim(0, max(ops_k) * 1.25)

        lines = [*l1, band, *l2]
        ax.legend(lines, [ln.get_label() for ln in lines], loc="upper left", frameon=True, framealpha=0.9)
    ax.set_title("(b) Fan-Out Dispatch Scaling (K = 1..64)")

    # Panel (c): Dense All-to-All Layer Meshes
    ax = axes[2]
    if mesh_pts:
        labels = [p[0] for p in mesh_pts]
        x = np.arange(len(mesh_pts))
        p50_ms = np.array([float(p[1]["p50_us"]) / 1000.0 for p in mesh_pts])
        p95_ms = np.array([float(p[1]["p95_us"]) / 1000.0 for p in mesh_pts])
        ops_k = np.array([float(p[1]["recv_ops_s"]) / 1000.0 for p in mesh_pts])

        bars = ax.bar(x - 0.17, p50_ms, width=0.32, color=PURPLE, label="E2E DAG p50 (ms)")
        ax.errorbar(
            x - 0.17,
            p50_ms,
            yerr=[np.zeros_like(p95_ms), np.maximum(0.0, p95_ms - p50_ms)],
            fmt="none",
            ecolor=CHARCOAL,
            elinewidth=1.2,
            capsize=3,
        )
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_xlabel("Dense DAG Topology")
        ax.set_ylabel("End-to-End DAG Latency (ms)", color=PURPLE)
        ax.tick_params(axis="y", labelcolor=PURPLE)
        ax.set_ylim(0, max(p95_ms) * 1.24)

        ax2 = ax.twinx()
        ax2.spines["top"].set_visible(False)
        ax2.grid(False)
        bars2 = ax2.bar(x + 0.17, ops_k, width=0.32, color=AMBER, alpha=0.88, label="Link Deliveries (k/s)")
        ax2.set_ylabel("Aggregate Deliveries (k/s)", color=CHARCOAL)
        ax2.set_ylim(0, max(ops_k) * 1.25)

        for idx, b in enumerate(bars):
            y_lbl = max(p95_ms[idx], p50_ms[idx]) + max(p95_ms) * 0.025
            ax.text(
                b.get_x() + b.get_width() / 2,
                y_lbl,
                f"{p50_ms[idx]:.2f}" if p50_ms[idx] < 1.0 else f"{p50_ms[idx]:.1f}",
                ha="center",
                va="bottom",
                fontsize=7.6,
                color=PURPLE,
                fontweight="bold",
            )

        for idx, b in enumerate(bars2):
            ax2.text(
                b.get_x() + b.get_width() / 2,
                ops_k[idx] + max(ops_k) * 0.02,
                f"{ops_k[idx]:.0f}k",
                ha="center",
                va="bottom",
                fontsize=7.8,
                color=CHARCOAL,
            )

        handles = [bars, bars2]
        ax.legend(handles, [h.get_label() for h in handles], loc="upper left", frameon=True, framealpha=0.9)
    ax.set_title("(c) Dense Layer Mesh Stress Test")

    fig.suptitle("DAG Topological Scaling in Headless Mode (Median Latency & Aggregate Delivery Rate)", y=1.03)
    return _save_figure(fig, out_dir, "fig2_topology_scaling", formats)


def _plot_fig3_ipc_and_workers(index: CsvIndex, out_dir: Path, formats: Sequence[str]) -> List[Path]:
    """Fig 3: ProcessingBase IPC stage decomposition and multi-worker scaling."""
    pingpong_specs = [
        ("proc_pingpong_dict_token", "Dict Token\n(~100 B)"),
        ("proc_pingpong_dict_1kb", "Dict Array\n(1 KB)"),
        ("proc_pingpong_shm_64kb", "ShmPool\n(64 KB)"),
        ("proc_pingpong_shm_256kb", "ShmPool\n(256 KB)"),
        ("proc_pingpong_shm_1mb", "ShmPool\n(1 MB)"),
        ("proc_pingpong_pickle_1mb", "Pure Pickle\n(1 MB, NoShm)"),
        ("proc_pingpong_shm_4mb", "ShmPool\n(4 MB)"),
        ("proc_pingpong_pickle_4mb", "Pure Pickle\n(4 MB, NoShm)"),
    ]
    stage_defs = [
        ("proc_main_encode_ns", "Main Encode / Shm Write", SKY),
        ("proc_ipc_in_ns", "Queue / Pipe In", BLUE),
        ("proc_worker_compute_ns", "Worker Compute", GREEN),
        ("proc_ipc_out_ns", "Pipe Out / Decode", VERMILION),
    ]

    pp_rows: List[Tuple[str, Dict[str, float], float]] = []
    for scen, label in pingpong_specs:
        tot = index.get((scen, "headless", "proc_total_roundtrip_ns"))
        if not tot:
            continue
        stages = {}
        for metric_name, _, _ in stage_defs:
            r = index.get((scen, "headless", metric_name))
            stages[metric_name] = float(r["mean_us"]) / 1000.0 if r else 0.0
        pp_rows.append((label, stages, float(tot["mean_us"]) / 1000.0))

    worker_pts: List[Tuple[int, RowDict]] = []
    for p in (1, 2, 4, 8, 12, 16):
        r = index.get((f"workers_parallel_p{p}", "headless", "proc_total_roundtrip_ns"))
        if r:
            worker_pts.append((p, r))

    if not pp_rows and not worker_pts:
        return []

    fig, (ax_ipc, ax_wrk) = plt.subplots(1, 2, figsize=(13.6, 5.0), gridspec_kw={"width_ratios": [1.12, 1.0]})
    fig.subplots_adjust(wspace=0.34)

    # Panel (a): Stacked IPC stage breakdown (in ms)
    if pp_rows:
        y = np.arange(len(pp_rows))
        left = np.zeros(len(pp_rows))
        for metric_name, stage_label, color in stage_defs:
            vals = np.array([r[1][metric_name] for r in pp_rows])
            ax_ipc.barh(y, vals, left=left, height=0.56, color=color, label=stage_label)
            left += vals

        totals = [r[2] for r in pp_rows]
        max_x = max(totals) * 1.26
        ax_ipc.set_xlim(0, max_x)
        ax_ipc.set_yticks(y)
        ax_ipc.set_yticklabels([r[0] for r in pp_rows])
        ax_ipc.invert_yaxis()
        ax_ipc.set_xlabel("Mean Uncontended Round-Trip Time (ms)")
        ax_ipc.set_title("(a) ProcessingBase IPC Stage Breakdown (Ping-Pong)")
        ax_ipc.legend(loc="upper right", frameon=True, framealpha=0.92, fontsize=8.0)

        for idx, total_ms in enumerate(totals):
            ax_ipc.text(
                total_ms + max_x * 0.015,
                y[idx],
                f"{total_ms:.2f} ms" if total_ms >= 1.0 else f"{total_ms * 1000.0:.0f} µs",
                va="center",
                ha="left",
                fontsize=8.2,
                fontweight="bold",
                color=CHARCOAL,
            )

    # Panel (b): Multi-worker parallel scaling
    if worker_pts:
        ps = np.array([p[0] for p in worker_pts])
        bw = np.array([float(p[1]["bandwidth_mb_s"]) for p in worker_pts])
        ops = np.array([float(p[1]["recv_ops_s"]) for p in worker_pts])
        rss = np.array([float(p[1]["rss_peak_mb"]) for p in worker_pts])

        l1 = ax_wrk.plot(ps, bw, marker="o", color=BLUE, linewidth=2.2, label="Throughput (MB/s)")
        ax_wrk.set_xlabel("Parallel ProcessingBase Workers (P)")
        ax_wrk.set_ylabel("Sustained Zero-Copy Bandwidth (MB/s)", color=BLUE)
        ax_wrk.tick_params(axis="y", labelcolor=BLUE)
        ax_wrk.set_xticks(ps)
        ax_wrk.set_ylim(0, max(bw) * 1.24)

        best_idx = int(np.argmax(bw))
        best_p = float(ps[best_idx])
        text_x = max(float(ps[0]) + 4.2, best_p - 4.8) if best_p >= 8 else best_p + 1.1
        text_y = bw[best_idx] * 1.12 if best_p >= 8 else bw[best_idx] * 0.88
        ax_wrk.set_ylim(0, max(bw) * 1.28)
        ax_wrk.annotate(
            f"Peak: {bw[best_idx]:.1f} MB/s\n({ops[best_idx]:,.0f} ops/s)",
            xy=(ps[best_idx], bw[best_idx]),
            xytext=(text_x, text_y),
            arrowprops=dict(arrowstyle="->", color=CHARCOAL, lw=1.0),
            fontsize=8.0,
            fontweight="bold",
            color=CHARCOAL,
            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=BLUE, alpha=0.95),
        )

        ax_rss = ax_wrk.twinx()
        ax_rss.spines["top"].set_visible(False)
        ax_rss.grid(False)
        l2 = ax_rss.plot(ps, rss, marker="^", linestyle="--", color=VERMILION, linewidth=1.9, label="Peak Total RSS (MB)")
        ax_rss.set_ylabel("Peak Aggregated RSS Memory (MB)", color=VERMILION)
        ax_rss.tick_params(axis="y", labelcolor=VERMILION)
        ax_rss.set_ylim(0, max(rss) * 1.20)

        first_row = worker_pts[0][1]
        bytes_per_op_mb = float(first_row["bandwidth_mb_s"]) / max(1e-9, float(first_row["recv_ops_s"]))
        payload_desc = "1080p RGB (6.22 MB)" if bytes_per_op_mb > 3.0 else "256 KB"
        comp_row = index.get((f"workers_parallel_p{worker_pts[0][0]}", "headless", "proc_worker_compute_ns"))
        comp_desc = f"{float(comp_row['mean_us']) / 1000.0:.1f} ms" if (comp_row and float(comp_row["mean_us"]) >= 1000.0) else (
            f"{float(comp_row['mean_us']):.0f} µs" if comp_row else "2.0 ms"
        )

        lines = l1 + l2
        ax_wrk.legend(lines, [ln.get_label() for ln in lines], loc="upper left", frameon=True, framealpha=0.92)
        ax_wrk.set_title(f"(b) Parallel Worker Scaling ({payload_desc} ShmPool, {comp_desc} Compute)")

    fig.suptitle("Multiprocess Offloading Performance & Memory Footprint (Headless Mode)", y=1.03)
    return _save_figure(fig, out_dir, "fig3_ipc_worker_breakdown", formats)


def _plot_fig4_headless_vs_gui(index: CsvIndex, out_dir: Path, formats: Sequence[str]) -> List[Path]:
    """Fig 4: Paired Headless vs Full DearPyGui GUI throughput and latency."""
    cmp_specs = [
        ("cmp_hop_1to1_stream_uncapped", "1:1 Uncapped Stream"),
        ("cmp_hop_1to1_burst", "1:1 Burst (5k msgs)"),
        ("cmp_chain_d20_burst", "Chain D=20 Burst"),
        ("cmp_fanout_k32_burst", "Fan-Out K=32 Burst"),
        ("cmp_mesh_4x8_burst", "Dense Mesh 4×8"),
        ("cmp_workers_p4_1080p", "4 Workers (1080p RGB)"),
        ("cmp_workers_p4_256kb", "4 Workers (256 KB)"),
        ("cmp_proc_shm_1mb", "1 Worker (1 MB Shm)"),
    ]

    rows_cmp: List[Tuple[str, RowDict, Optional[RowDict], Optional[RowDict]]] = []
    has_live = False
    for scen, label in cmp_specs:
        hl = index.get((scen, "headless", "e2e_dag_ns"))
        gui = index.get((scen, "gui", "e2e_dag_ns"))
        live = index.get((scen, "gui_live_ui", "e2e_dag_ns"))
        if hl and (gui or live):
            if live is not None:
                has_live = True
            rows_cmp.append((label, hl, gui, live))

    if not rows_cmp:
        return []

    y = np.arange(len(rows_cmp))
    labels = [p[0] for p in rows_cmp]
    hl_ops = np.array([float(p[1]["recv_ops_s"]) for p in rows_cmp])
    gui_ops = np.array([float(p[2]["recv_ops_s"]) if p[2] else 0.0 for p in rows_cmp])
    live_ops = np.array([float(p[3]["recv_ops_s"]) if p[3] else 0.0 for p in rows_cmp])

    hl_lat = np.array([float(p[1]["mean_us"]) for p in rows_cmp])
    gui_lat = np.array([float(p[2]["mean_us"]) if p[2] else 0.0 for p in rows_cmp])
    live_lat = np.array([float(p[3]["mean_us"]) if p[3] else 0.0 for p in rows_cmp])

    gui_fps = np.array([float(p[2]["dpg_fps_mean"]) if p[2] else 0.0 for p in rows_cmp])
    live_fps = np.array([float(p[3]["dpg_fps_mean"]) if p[3] else 0.0 for p in rows_cmp])

    fig, (ax_thr, ax_lat) = plt.subplots(1, 2, figsize=(14.4, 5.6))
    fig.subplots_adjust(wspace=0.25)

    # Panel (a): Throughput (log scale)
    if has_live:
        h = 0.25
        ax_thr.barh(y - h, hl_ops, height=h, color=BLUE, label="Headless (no viewport)")
        ax_thr.barh(y, gui_ops, height=h, color=VERMILION, alpha=0.9, label="GUI (passive render)")
        ax_thr.barh(y + h, live_ops, height=h, color=GREEN, alpha=0.9, label="GUI + Live UI (30 Hz)")
        max_thr = float(max(np.max(hl_ops), np.max(gui_ops), np.max(live_ops)))
    else:
        h = 0.36
        ax_thr.barh(y - h / 2, hl_ops, height=h, color=BLUE, label="Headless (no viewport)")
        ax_thr.barh(y + h / 2, gui_ops, height=h, color=VERMILION, alpha=0.9, label="Full GUI (DearPyGui)")
        max_thr = float(max(np.max(hl_ops), np.max(gui_ops)))

    ax_thr.set_xscale("log")
    ax_thr.set_xlim(1e2, max_thr * (22.0 if has_live else 14.0))
    ax_thr.set_yticks(y)
    ax_thr.set_yticklabels(labels)
    ax_thr.invert_yaxis()
    ax_thr.set_xlabel("Sustained Throughput (ops/s, log scale — higher is better)")
    ax_thr.set_title("(a) Delivery Throughput Comparison")
    ax_thr.legend(loc="lower right", frameon=True, framealpha=0.92, fontsize=8.0)

    for idx in range(len(rows_cmp)):
        if has_live:
            top_val = max(hl_ops[idx], gui_ops[idx], live_ops[idx])
            delta_live = ((live_ops[idx] - hl_ops[idx]) / hl_ops[idx] * 100.0) if hl_ops[idx] > 0 else 0.0
            txt = f"{_fmt_kops(hl_ops[idx])} / {_fmt_kops(gui_ops[idx])} / {_fmt_kops(live_ops[idx])} ({delta_live:+.1f}%)"
        else:
            top_val = max(hl_ops[idx], gui_ops[idx])
            delta_pct = ((gui_ops[idx] - hl_ops[idx]) / hl_ops[idx] * 100.0) if hl_ops[idx] > 0 else 0.0
            txt = f"{_fmt_kops(hl_ops[idx])} vs {_fmt_kops(gui_ops[idx])} ({delta_pct:+.1f}%)"
        ax_thr.text(top_val * 1.15, y[idx], txt, va="center", ha="left", fontsize=7.6, color=CHARCOAL)

    # Panel (b): Mean Latency (log scale) + FPS badge
    if has_live:
        h = 0.25
        ax_lat.barh(y - h, hl_lat, height=h, color=BLUE, label="Headless mean (µs)")
        ax_lat.barh(y, gui_lat, height=h, color=VERMILION, alpha=0.9, label="GUI passive (µs)")
        ax_lat.barh(y + h, live_lat, height=h, color=GREEN, alpha=0.9, label="GUI + Live UI (µs)")
        max_lat = float(max(np.max(hl_lat), np.max(gui_lat), np.max(live_lat)))
    else:
        h = 0.36
        ax_lat.barh(y - h / 2, hl_lat, height=h, color=BLUE, label="Headless mean (µs)")
        ax_lat.barh(y + h / 2, gui_lat, height=h, color=VERMILION, alpha=0.9, label="Full GUI mean (µs)")
        max_lat = float(max(np.max(hl_lat), np.max(gui_lat)))

    ax_lat.set_xscale("log")
    ax_lat.set_xlim(0.5, max_lat * (40.0 if has_live else 28.0))
    ax_lat.set_yticks(y)
    ax_lat.set_yticklabels([""] * len(rows_cmp))
    ax_lat.invert_yaxis()
    ax_lat.set_xlabel("Mean End-to-End DAG Latency (µs, log scale — lower is better)")
    ax_lat.set_title("(b) Mean Latency & Live Viewport FPS")
    ax_lat.legend(loc="upper right", frameon=True, framealpha=0.92, fontsize=8.0)

    for idx in range(len(rows_cmp)):
        if has_live:
            top_lat = max(hl_lat[idx], gui_lat[idx], live_lat[idx])
            lat_delta = ((live_lat[idx] - hl_lat[idx]) / hl_lat[idx] * 100.0) if hl_lat[idx] > 0 else 0.0
            fps_disp = live_fps[idx] if live_fps[idx] > 0 else gui_fps[idx]
            txt = f"{_fmt_us(hl_lat[idx])} / {_fmt_us(gui_lat[idx])} / {_fmt_us(live_lat[idx])} ({lat_delta:+.1f}%, {fps_disp:.1f} FPS)"
        else:
            top_lat = max(hl_lat[idx], gui_lat[idx])
            lat_delta = ((gui_lat[idx] - hl_lat[idx]) / hl_lat[idx] * 100.0) if hl_lat[idx] > 0 else 0.0
            txt = f"{_fmt_us(hl_lat[idx])} vs {_fmt_us(gui_lat[idx])} ({lat_delta:+.1f}%, {gui_fps[idx]:.1f} FPS)"
        ax_lat.text(top_lat * 1.18, y[idx], txt, va="center", ha="left", fontsize=7.5, color=CHARCOAL)

    fig.suptitle(
        "Impact of DearPyGui Render Loop & Live UI Updates on Pipeline Performance",
        y=1.02,
    )
    return _save_figure(fig, out_dir, "fig4_headless_vs_gui", formats)


def _extract_stress_points(
    index: CsvIndex,
    prefix: str,
    metric: str,
    mode: str,
) -> List[Tuple[int, str, RowDict]]:
    """Extract sorted `(scale_x, label, row)` stress points for a prefix and mode."""
    pts: List[Tuple[int, str, RowDict]] = []
    for (scen, m, met), row in index.items():
        if m != mode or met != metric or not scen.startswith(prefix):
            continue
        suffix = scen[len(prefix):]
        if prefix == "stress_mesh_" and "x" in suffix:
            parts = suffix.split("x")
            try:
                layers, width = int(parts[0]), int(parts[1])
                scale_x = width ** layers
                label = f"{layers}x{width}"
            except ValueError:
                continue
        else:
            try:
                scale_x = int(suffix)
                label = str(scale_x)
            except ValueError:
                continue
        pts.append((scale_x, label, row))
    pts.sort(key=lambda item: item[0])
    return pts


def _plot_fig5_stress_limits(index: CsvIndex, out_dir: Path, formats: Sequence[str]) -> List[Path]:
    """Fig 5: Progressive stress-test scaling, real-time budgets, and GUI FPS comfort."""
    chain_hl = _extract_stress_points(index, "stress_chain_d", "e2e_dag_ns", "headless")
    chain_gui = _extract_stress_points(index, "stress_chain_d", "e2e_dag_ns", "gui_live_ui")
    fan_hl = _extract_stress_points(index, "stress_fanout_k", "e2e_dag_ns", "headless")
    fan_gui = _extract_stress_points(index, "stress_fanout_k", "e2e_dag_ns", "gui_live_ui")
    mesh_hl = _extract_stress_points(index, "stress_mesh_", "e2e_dag_ns", "headless")
    mesh_gui = _extract_stress_points(index, "stress_mesh_", "e2e_dag_ns", "gui_live_ui")
    wrk_hl = _extract_stress_points(index, "stress_workers_p", "proc_total_roundtrip_ns", "headless")
    wrk_gui = _extract_stress_points(index, "stress_workers_p", "proc_total_roundtrip_ns", "gui_live_ui")

    if not (chain_hl or chain_gui or fan_hl or fan_gui or mesh_hl or mesh_gui or wrk_hl or wrk_gui):
        return []

    fig, axes = plt.subplots(2, 2, figsize=(14.4, 9.6))
    fig.subplots_adjust(wspace=0.38, hspace=0.34)

    def _add_fps_axis(ax: plt.Axes, gui_pts: List[Tuple[int, str, RowDict]], xs_override: Optional[np.ndarray] = None):
        if not gui_pts:
            return None
        ax2 = ax.twinx()
        ax2.spines["top"].set_visible(False)
        ax2.grid(False)
        gx = xs_override if xs_override is not None else np.array([p[0] for p in gui_pts])
        fps = np.array([float(p[2]["dpg_fps_mean"]) for p in gui_pts])
        l_fps = ax2.plot(gx, fps, marker="D", markersize=4.5, linestyle=":", linewidth=1.8, color=GREEN, label="Uncapped GUI FPS")
        ax2.axhline(25.0, color=GREEN, linestyle="-.", linewidth=1.0, alpha=0.65, label="25 FPS Comfort")
        top_fps = max(72.0, float(np.max(fps)) * 1.22) if len(fps) else 72.0
        ax2.set_ylim(0.0, top_fps)
        ax2.set_ylabel("DearPyGui Uncapped FPS", color=GREEN)
        ax2.tick_params(axis="y", labelcolor=GREEN)
        return ax2, l_fps

    # Panel (a): Serial Chain Stress
    ax = axes[0, 0]
    lines_a = []
    max_lat_a = 0.0
    for pts, color, lbl, band_lbl in (
        (chain_hl, BLUE, "Headless mean (ms)", "Headless Mean–p95 jitter"),
        (chain_gui, VERMILION, "Live-UI mean (ms)", "Live-UI Mean–p95 jitter"),
    ):
        if pts:
            xs = np.array([p[0] for p in pts])
            mean_ms = np.array([float(p[2]["mean_us"]) / 1000.0 for p in pts])
            p95_ms = np.array([float(p[2]["p95_us"]) / 1000.0 for p in pts])
            max_lat_a = max(max_lat_a, float(np.max(p95_ms)))
            lines_a.extend(ax.plot(xs, mean_ms, marker="o", linewidth=2.0, color=color, label=lbl))
            band = ax.fill_between(xs, mean_ms, p95_ms, color=color, alpha=0.14, label=band_lbl)
            lines_a.append(band)
    if max_lat_a >= 4.0:
        ax.axhline(16.67, color=AMBER, linestyle="--", linewidth=1.2, label="60 Hz (16.7 ms)")
    fps_res = _add_fps_axis(ax, chain_gui)
    if fps_res:
        lines_a.extend(fps_res[1])
    ax.set_xlabel("Serial Chain Depth D (nodes)")
    ax.set_ylabel("End-to-End DAG Latency (ms)")
    ax.set_title("(a) Serial Chain Stress Scaling (Latency & GUI FPS)")
    if lines_a:
        ax.legend(lines_a, [ln.get_label() for ln in lines_a], loc="upper left", ncol=2, fontsize=6.9, frameon=True, framealpha=0.9)

    # Panel (b): Parallel Fan-Out Stress
    ax = axes[0, 1]
    lines_b = []
    max_lat_b = 0.0
    for pts, color, lbl, band_lbl in (
        (fan_hl, BLUE, "Headless mean (ms)", "Headless Mean–p95 jitter"),
        (fan_gui, VERMILION, "Live-UI mean (ms)", "Live-UI Mean–p95 jitter"),
    ):
        if pts:
            xs = np.array([p[0] for p in pts])
            mean_ms = np.array([float(p[2]["mean_us"]) / 1000.0 for p in pts])
            p95_ms = np.array([float(p[2]["p95_us"]) / 1000.0 for p in pts])
            max_lat_b = max(max_lat_b, float(np.max(p95_ms)))
            lines_b.extend(ax.plot(xs, mean_ms, marker="o", linewidth=2.0, color=color, label=lbl))
            band = ax.fill_between(xs, mean_ms, p95_ms, color=color, alpha=0.14, label=band_lbl)
            lines_b.append(band)
    if fan_hl or fan_gui:
        ax.set_xscale("log", base=2)
        all_k = sorted({p[0] for p in (fan_hl + fan_gui)})
        ax.set_xticks(all_k)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda val, _: f"{int(val)}"))
    if max_lat_b >= 4.0:
        ax.axhline(16.67, color=AMBER, linestyle="--", linewidth=1.2, label="60 Hz (16.7 ms)")
    fps_res = _add_fps_axis(ax, fan_gui)
    if fps_res:
        lines_b.extend(fps_res[1])
    ax.set_xlabel("Fan-Out Branch Count K (log scale)")
    ax.set_ylabel("End-to-End DAG Latency (ms)")
    ax.set_title("(b) Parallel Fan-Out Stress Scaling (Latency & GUI FPS)")
    if lines_b:
        ax.legend(lines_b, [ln.get_label() for ln in lines_b], loc="upper left", ncol=2, fontsize=6.9, frameon=True, framealpha=0.9)

    # Panel (c): Dense Matrix Mesh Stress (breaking real-time budgets)
    ax = axes[1, 0]
    lines_c = []
    mesh_order = sorted({(p[0], p[1]) for p in (mesh_hl + mesh_gui)}, key=lambda t: t[0])
    mesh_pos = {lbl: idx for idx, (_, lbl) in enumerate(mesh_order)}
    hl_mesh_map = {p[1]: p[2] for p in mesh_hl}
    gui_mesh_map = {p[1]: p[2] for p in mesh_gui}
    if mesh_order:
        xs_hl = np.array([mesh_pos[lbl] for _, lbl in mesh_order if lbl in hl_mesh_map])
        ys_hl = np.array([max(1e-3, float(hl_mesh_map[lbl]["mean_us"]) / 1000.0) for _, lbl in mesh_order if lbl in hl_mesh_map])
        if len(xs_hl):
            lines_c.extend(ax.plot(xs_hl, ys_hl, marker="o", linewidth=2.1, color=BLUE, label="Headless mean (ms)"))
        xs_gui = np.array([mesh_pos[lbl] for _, lbl in mesh_order if lbl in gui_mesh_map])
        ys_gui = np.array([max(1e-3, float(gui_mesh_map[lbl]["mean_us"]) / 1000.0) for _, lbl in mesh_order if lbl in gui_mesh_map])
        if len(xs_gui):
            lines_c.extend(ax.plot(xs_gui, ys_gui, marker="s", linewidth=2.1, color=VERMILION, label="Live-UI mean (ms)"))
        l60 = ax.axhline(16.67, color=AMBER, linestyle="--", linewidth=1.4, label="60 Hz Budget (16.7 ms)")
        l30 = ax.axhline(33.33, color=PURPLE, linestyle="--", linewidth=1.4, label="30 Hz Budget (33.3 ms)")
        lines_c.extend([l60, l30])
        ax.set_yscale("log")
        ax.set_xticks(np.arange(len(mesh_order)))
        ax.set_xticklabels([lbl for _, lbl in mesh_order])
        fps_mesh_pts = [(mesh_pos[lbl], lbl, gui_mesh_map[lbl]) for _, lbl in mesh_order if lbl in gui_mesh_map]
        fps_res = _add_fps_axis(ax, fps_mesh_pts, xs_override=xs_gui if len(xs_gui) else None)
        if fps_res:
            lines_c.extend(fps_res[1])
    ax.set_xlabel("Dense Mesh Topology (Layers × Width)")
    ax.set_ylabel("Mean DAG Latency (ms, log scale)")
    ax.set_title("(c) Dense Matrix Breaking Point vs Real-Time Frame Budgets")
    if lines_c:
        ax.legend(lines_c, [ln.get_label() for ln in lines_c], loc="upper left", fontsize=7.6, frameon=True, framealpha=0.9)

    # Panel (d): Parallel 1080p RGB Worker Scaling & GUI FPS / RSS
    ax = axes[1, 1]
    lines_d = []
    for is_gui, pts, color, lbl in (
        (False, wrk_hl, BLUE, "Headless BW (MB/s)"),
        (True, wrk_gui, VERMILION, "Live-UI BW (MB/s)"),
    ):
        if pts:
            xs = np.array([p[0] for p in pts])
            bw = np.array([float(p[2]["bandwidth_mb_s"]) for p in pts])
            rss = np.array([float(p[2]["rss_peak_mb"]) for p in pts])
            lines_d.extend(ax.plot(xs, bw, marker="o", linewidth=2.1, color=color, label=lbl))
            best_i = int(np.argmax(bw))
            if is_gui:
                x_off = -22 if best_i == len(xs) - 1 else 0
                y_off, va = -14, "top"
            else:
                x_off = -16 if best_i == len(xs) - 1 else 0
                y_off, va = 9, "bottom"
            ax.annotate(
                f"Peak {bw[best_i]:.0f} MB/s\n({rss[best_i]:.0f} MB RSS)",
                xy=(xs[best_i], bw[best_i]),
                xytext=(x_off, y_off),
                textcoords="offset points",
                ha="center",
                va=va,
                fontsize=7.6,
                fontweight="bold",
                color=color,
            )
    fps_res = _add_fps_axis(ax, wrk_gui)
    if fps_res:
        lines_d.extend(fps_res[1])
    all_p = sorted({p[0] for p in (wrk_hl + wrk_gui)})
    if all_p:
        ax.set_xticks(all_p)
        ax.set_ylim(0, max([float(p[2]["bandwidth_mb_s"]) for p in (wrk_hl + wrk_gui)] + [100.0]) * 1.36)
    ax.set_xlabel("Parallel 1080p RGB Workers (P)")
    ax.set_ylabel("Aggregate ShmPool Bandwidth (MB/s)")
    ax.set_title("(d) Multiprocess 1080p Scaling, Saturation & GUI Comfort")
    if lines_d:
        ax.legend(lines_d, [ln.get_label() for ln in lines_d], loc="upper left", fontsize=7.8, frameon=True, framealpha=0.9)

    fig.suptitle("Progressive Stress Limits, Real-Time Budgets & Live-GUI Comfort", y=0.99)
    return _save_figure(fig, out_dir, "fig5_stress_limits_and_fps", formats)


def generate_figures_from_csv(
    csv_path: Path,
    out_dir: Optional[Path] = None,
    formats: Sequence[str] = ("png",),
) -> List[Path]:
    """Generate all available benchmark figures from ``csv_path`` into ``out_dir``."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Benchmark CSV not found: {csv_path}")

    target_dir = Path(out_dir) if out_dir is not None else (csv_path.parent / "figures")
    _apply_paper_style()
    index = load_benchmark_csv(csv_path)

    generated: List[Path] = []
    for builder in (
        _plot_fig1_primitives,
        _plot_fig2_topology_scaling,
        _plot_fig3_ipc_and_workers,
        _plot_fig4_headless_vs_gui,
        _plot_fig5_stress_limits,
    ):
        generated.extend(builder(index, target_dir, formats))
    return generated


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate benchmark figures from benchmark CSV.")
    parser.add_argument(
        "--csv",
        default=str(BENCHMARK_DIR / "benchmark_report.csv"),
        help="Path to benchmark_report.csv (default: benchmark/benchmark_report.csv).",
    )
    parser.add_argument(
        "--out-dir",
        default=str(BENCHMARK_DIR / "figures"),
        help="Output directory for generated figures (default: benchmark/figures).",
    )
    parser.add_argument(
        "--formats",
        default="png",
        help="Comma-separated image formats, e.g. png or png,pdf (default: png).",
    )
    args = parser.parse_args()

    formats = tuple(fmt.strip() for fmt in args.formats.split(",") if fmt.strip()) or ("png",)
    paths = generate_figures_from_csv(Path(args.csv), Path(args.out_dir), formats=formats)
    print(f"[Benchmark] Generated {len(paths)} figure(s) in {Path(args.out_dir).resolve()}")
    for path in paths:
        print(f"  - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
