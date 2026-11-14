"""Generate accessible end-user benchmark reports from Framework Profiler JSON/CSV outputs.

Produces two complementary deliverables inside ``benchmark/``:
1. ``interactive_report.html`` — Self-contained interactive Plotly dashboard with
   plain-language comfort cards, full Global Summary takeaways, stress-limit synthesis,
   Headless vs GUI impact tables, shaded jitter bands, and multi-filter sortable table.
2. ``benchmark_analysis.ipynb`` — Clean nbformat v4 Jupyter Notebook so any user can
   explore raw CSV/JSON telemetry, regenerate plots, and inspect custom runs.

Can also be executed standalone::

    python tools/generate_user_reports.py
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.framework_profiler import FrameworkProfiler
from core.paths import BENCHMARK_DIR


def _load_report_json(report_json_path: Path) -> Dict[str, Any]:
    path = Path(report_json_path)
    if not path.exists():
        raise FileNotFoundError(f"Report JSON not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = list(data.get("scenarios", []))
    if not isinstance(data.get("comfort_and_limits"), dict) or not data["comfort_and_limits"].get("series"):
        data["comfort_and_limits"] = FrameworkProfiler.compute_comfort_and_limits(scenarios)
    return data


def _categorize_scenario(name: str) -> str:
    if name.startswith("stress_chain_"):
        return "Stress — Chain"
    if name.startswith("stress_fanout_"):
        return "Stress — Fan-Out"
    if name.startswith("stress_mesh_"):
        return "Stress — Matrix"
    if name.startswith("stress_workers_"):
        return "Stress — 1080p Workers"
    if name.startswith("cmp_"):
        return "GUI Comparison"
    if name.startswith("proc_") or name.startswith("workers_"):
        return "Multiprocess & IPC"
    if name.startswith("chain_") or name.startswith("fanout_") or name.startswith("mesh_"):
        return "Topology Scaling"
    if name.startswith("hop_") or name.startswith("routing_") or name.startswith("calibration_"):
        return "Primitives & Routing"
    return "Custom Scenario"


def _fmt_us_or_ms(val_us: float) -> str:
    if val_us >= 1000.0:
        return f"{val_us / 1000.0:.2f} ms"
    return f"{val_us:.2f} µs"


def _extract_ipc_comparison(scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pair ShmPool vs Pure-Pickle scenarios across payload sizes for plotting."""
    pairs = [
        ("64 KB", "proc_shm_numpy_64kb", None),
        ("256 KB", "proc_shm_numpy_256kb", "proc_pickle_numpy_256kb"),
        ("1 MB", "proc_shm_numpy_1mb", "proc_dict_pickle_numpy_1mb"),
        ("4 MB", "proc_shm_numpy_4mb", "proc_pickle_numpy_4mb"),
    ]
    by_name = {str(s.get("scenario", "")): s for s in scenarios if s.get("mode") == "headless"}
    out: List[Dict[str, Any]] = []
    for label, shm_key, pkl_key in pairs:
        s_shm = by_name.get(shm_key)
        s_pkl = by_name.get(pkl_key) if pkl_key else None
        if not s_shm and not s_pkl:
            continue
        m_shm = (s_shm or {}).get("metrics", {}).get("proc_total_roundtrip_ns", {}) if s_shm else {}
        m_pkl = (s_pkl or {}).get("metrics", {}).get("proc_total_roundtrip_ns", {}) if s_pkl else {}
        out.append(
            {
                "label": label,
                "shm_bw_mb_s": round(float((s_shm or {}).get("throughput", {}).get("bandwidth_mb_s", 0.0)), 2),
                "pkl_bw_mb_s": round(float((s_pkl or {}).get("throughput", {}).get("bandwidth_mb_s", 0.0)), 2),
                "shm_ops_s": round(float((s_shm or {}).get("throughput", {}).get("recv_ops_s", 0.0)), 2),
                "pkl_ops_s": round(float((s_pkl or {}).get("throughput", {}).get("recv_ops_s", 0.0)), 2),
                "shm_lat_us": round(float(m_shm.get("mean_us", 0.0)), 2),
                "pkl_lat_us": round(float(m_pkl.get("mean_us", 0.0)), 2),
            }
        )
    return out


def _extract_gui_comparison(scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract paired Headless vs Passive GUI vs Live-UI (30 Hz) metrics."""
    by_sm: Dict[tuple, Dict[str, Any]] = {}
    ordered_names: List[str] = []
    for sc in scenarios:
        name = str(sc.get("scenario", ""))
        mode = str(sc.get("mode", ""))
        if not name.startswith("cmp_"):
            continue
        by_sm[(name, mode)] = sc
        if mode == "headless" and name not in ordered_names:
            ordered_names.append(name)

    rows: List[Dict[str, Any]] = []
    for name in ordered_names:
        hl = by_sm.get((name, "headless"))
        if not hl:
            continue
        gui = by_sm.get((name, "gui"))
        live = by_sm.get((name, "gui_live_ui"))
        if not gui and not live:
            continue

        def _row_stats(
            sc: Optional[Dict[str, Any]],
            hl_ops: Optional[float] = None,
            hl_mean_us: Optional[float] = None,
        ) -> Dict[str, Any]:
            if not sc:
                return {
                    "present": False,
                    "ops_s": 0.0,
                    "tp_delta_pct": None,
                    "mean_us": 0.0,
                    "p95_us": 0.0,
                    "lat_delta_pct": None,
                    "fps": 0.0,
                    "comfort": "n/a",
                    "rss_mb": 0.0,
                }
            m = sc.get("metrics", {})
            km = m.get("e2e_dag_ns") or m.get("proc_total_roundtrip_ns") or m.get("link_hop_ns") or {}
            ops_s = float(sc.get("throughput", {}).get("recv_ops_s", 0.0))
            mean_us = float(km.get("mean_us", 0.0))
            p95_us = float(km.get("p95_us", 0.0))
            fps = float(sc.get("system", {}).get("dpg_fps_mean", 0.0))
            rss_mb = float(sc.get("system", {}).get("rss_peak_mb", 0.0))
            mode_sc = str(sc.get("mode", ""))
            tp_delta = ((ops_s - hl_ops) / hl_ops * 100.0) if (hl_ops is not None and hl_ops > 0) else None
            lat_delta = ((mean_us - hl_mean_us) / hl_mean_us * 100.0) if (hl_mean_us is not None and hl_mean_us > 0) else None
            return {
                "present": True,
                "ops_s": round(ops_s, 1),
                "tp_delta_pct": round(tp_delta, 1) if tp_delta is not None else None,
                "mean_us": round(mean_us, 2),
                "p95_us": round(p95_us, 2),
                "lat_delta_pct": round(lat_delta, 1) if lat_delta is not None else None,
                "fps": round(fps, 1),
                "comfort": FrameworkProfiler.classify_fps_comfort(fps) if mode_sc != "headless" else "Headless",
                "rss_mb": round(rss_mb, 1),
            }

        hl_stats = _row_stats(hl)
        rows.append(
            {
                "scenario": name,
                "headless": hl_stats,
                "gui": _row_stats(gui, hl_stats["ops_s"], hl_stats["mean_us"]),
                "gui_live_ui": _row_stats(live, hl_stats["ops_s"], hl_stats["mean_us"]),
            }
        )
    return rows


def _extract_summary_tables(
    scenarios: List[Dict[str, Any]],
    comfort: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Build structured equivalents of `summary.md` Global Summary and Stress Limits tables."""
    by_name: Dict[str, Dict[str, Any]] = {}
    for sc in scenarios:
        by_name.setdefault(str(sc.get("scenario", "")), sc)

    def _fmt_lat(m: Optional[Dict[str, Any]]) -> str:
        if not m:
            return "n/a"
        return (
            f"{m.get('mean_us', 0.0):.2f} µs "
            f"(p50: {m.get('p50_us', 0.0):.2f} µs, p95: {m.get('p95_us', 0.0):.2f} µs)"
        )

    global_takeaways: List[Dict[str, str]] = []
    hop_sc = by_name.get("hop_1to1_token") or by_name.get("cmp_hop_1to1_burst")
    if hop_sc:
        m = hop_sc.get("metrics", {})
        hop_m = m.get("link_hop_ns")
        e2e_m = m.get("e2e_dag_ns")
        ops = hop_sc.get("throughput", {}).get("recv_ops_s", 0.0)
        if hop_m:
            global_takeaways.append(
                {
                    "metric": "Direct transfer between 2 modules (<code>link_hop_ns</code>)",
                    "latency": _fmt_lat(hop_m),
                    "detail": f"{ops:,.0f} msg/s",
                }
            )
        if e2e_m:
            global_takeaways.append(
                {
                    "metric": "End-to-end Source &rarr; Sink (<code>e2e_dag_ns</code>)",
                    "latency": _fmt_lat(e2e_m),
                    "detail": "Zero-copy (Python ref)",
                }
            )

    for key, label in (
        ("routing_gate", "Conditional <code>Gate</code> routing"),
        ("routing_linkbus", "Shared <code>LinkOut &rarr; LinkBus &rarr; LinkIn</code> bus"),
    ):
        sc = by_name.get(key)
        if sc:
            m = sc.get("metrics", {})
            ops = sc.get("throughput", {}).get("recv_ops_s", 0.0)
            global_takeaways.append(
                {
                    "metric": label,
                    "latency": _fmt_lat(m.get("e2e_dag_ns") or m.get("link_hop_ns")),
                    "detail": f"{ops:,.0f} msg/s",
                }
            )

    pp_tok = by_name.get("proc_pingpong_dict_token")
    if pp_tok:
        pm = pp_tok.get("metrics", {})
        enc = pm.get("proc_main_encode_ns", {}).get("mean_us", 0.0)
        ipc_in = pm.get("proc_ipc_in_ns", {}).get("mean_us", 0.0)
        comp = pm.get("proc_worker_compute_ns", {}).get("mean_us", 0.0)
        ipc_out = pm.get("proc_ipc_out_ns", {}).get("mean_us", 0.0)
        global_takeaways.append(
            {
                "metric": "Multiprocess <code>ProcessingBase</code> round-trip (token)",
                "latency": _fmt_lat(pm.get("proc_total_roundtrip_ns")),
                "detail": f"IPC in ~{enc + ipc_in:.0f} µs, Compute ~{comp:.1f} µs, IPC out ~{ipc_out:.0f} µs",
            }
        )

    pp_256 = by_name.get("proc_pingpong_shm_256kb") or by_name.get("proc_shm_numpy_256kb")
    if pp_256:
        pm = pp_256.get("metrics", {})
        bw = pp_256.get("throughput", {}).get("bandwidth_mb_s", 0.0)
        global_takeaways.append(
            {
                "metric": "Multiprocess <code>ShmPool</code> transfer (256 KB)",
                "latency": _fmt_lat(pm.get("proc_total_roundtrip_ns")),
                "detail": f"{bw:.1f} MB/s",
            }
        )

    shm_1m = by_name.get("proc_pingpong_shm_1mb")
    pkl_1m = by_name.get("proc_pingpong_pickle_1mb")
    if shm_1m and pkl_1m:
        shm_ms = shm_1m.get("metrics", {}).get("proc_total_roundtrip_ns", {}).get("mean_us", 0.0) / 1000.0
        pkl_ms = pkl_1m.get("metrics", {}).get("proc_total_roundtrip_ns", {}).get("mean_us", 0.0) / 1000.0
        speedup = (pkl_ms / shm_ms) if shm_ms > 0 else 0.0
        global_takeaways.append(
            {
                "metric": "<code>ShmPool</code> vs Pure Pickle (1 MB array)",
                "latency": f"{shm_ms:.2f} ms vs {pkl_ms:.2f} ms",
                "detail": f"{speedup:.2f}x faster round-trip with ShmPool",
            }
        )

    shm_4m = by_name.get("proc_shm_numpy_4mb")
    pkl_4m = by_name.get("proc_pickle_numpy_4mb")
    if shm_4m and pkl_4m:
        shm_bw = shm_4m.get("throughput", {}).get("bandwidth_mb_s", 0.0)
        pkl_bw = pkl_4m.get("throughput", {}).get("bandwidth_mb_s", 0.0)
        bw_gain = (shm_bw / pkl_bw) if pkl_bw > 0 else 0.0
        global_takeaways.append(
            {
                "metric": "<code>ShmPool</code> vs Pure Pickle (4 MB burst)",
                "latency": f"{shm_bw:.1f} vs {pkl_bw:.1f} MB/s",
                "detail": f"{bw_gain:.2f}x higher throughput with ShmPool",
            }
        )

    par_scs = [
        sc
        for name, sc in by_name.items()
        if name.startswith("workers_parallel_p") or name.startswith("stress_workers_p")
    ]
    if par_scs:
        best = max(par_scs, key=lambda s: s.get("throughput", {}).get("recv_ops_s", 0.0))
        best_meta = best.get("metadata", {})
        p_cnt = best_meta.get("parallel_workers", "?")
        payload_lbl = best_meta.get("payload", "1080p RGB")
        best_ops = best.get("throughput", {}).get("recv_ops_s", 0.0)
        best_bw = best.get("throughput", {}).get("bandwidth_mb_s", 0.0)
        global_takeaways.append(
            {
                "metric": f"Optimal parallel workers (<code>ProcessingBase</code>, {payload_lbl})",
                "latency": f"P = {p_cnt} workers",
                "detail": f"{best_ops:,.0f} ops/s ({best_bw:.1f} MB/s)",
            }
        )

    if not global_takeaways and scenarios:
        for sc in scenarios[:6]:
            m = sc.get("metrics", {})
            km = m.get("e2e_dag_ns") or m.get("proc_total_roundtrip_ns") or m.get("link_hop_ns") or m.get("module_input_cb_ns")
            ops = sc.get("throughput", {}).get("recv_ops_s", 0.0)
            global_takeaways.append(
                {
                    "metric": f"Scenario <code>{sc.get('scenario')}</code> ({sc.get('mode')})",
                    "latency": _fmt_lat(km),
                    "detail": f"{ops:,.0f} ops/s",
                }
            )

    # Practical PC Limits & UI Comfort table (all 4 stress axes)
    series = comfort.get("series") or {}
    stress_limits_rows: List[Dict[str, str]] = []
    if any(series.get(k) for k in ("chain", "fanout", "mesh", "workers")):
        ch_hl = [p for p in series.get("chain", []) if p.get("mode") == "headless"]
        ch_30 = max((p["scale_x"] for p in ch_hl if p["mean_us"] <= 33_333.0), default=None)
        ch_top = (comfort.get("chain_limits", {}).get("max_tested") or {})
        ch_rt = comfort.get("max_chain_depth_realtime")
        ch_ui = comfort.get("max_chain_depth_ui_comfort")
        stress_limits_rows.append(
            {
                "axis": "Serial Chain (<code>D</code> modules)",
                "rt60": f"Up to D = {ch_rt}" if ch_rt is not None else "n/a",
                "rt30": f"Up to D = {ch_30}" if ch_30 is not None else "n/a",
                "ui_comfort": f"Up to D = {ch_ui}" if ch_ui is not None else "n/a",
                "observation": (
                    f"Max tested <code>D={ch_top.get('label', 'n/a')}</code> "
                    f"({ch_top.get('mean_us', 0.0) / 1000.0:.2f} ms, {ch_top.get('dpg_fps_mean', 0.0):.1f} FPS)"
                    if ch_top
                    else "n/a"
                ),
            }
        )

        fn_hl = [p for p in series.get("fanout", []) if p.get("mode") == "headless"]
        fn_30 = max((p["scale_x"] for p in fn_hl if p["mean_us"] <= 33_333.0), default=None)
        fn_top = (comfort.get("fanout_limits", {}).get("max_tested") or {})
        fn_rt = comfort.get("max_fanout_k_realtime")
        fn_ui = comfort.get("max_fanout_k_ui_comfort")
        stress_limits_rows.append(
            {
                "axis": "Parallel Fan-Out (<code>K</code> modules)",
                "rt60": f"Up to K = {fn_rt}" if fn_rt is not None else "n/a",
                "rt30": f"Up to K = {fn_30}" if fn_30 is not None else "n/a",
                "ui_comfort": f"Up to K = {fn_ui}" if fn_ui is not None else "n/a",
                "observation": (
                    f"Max tested <code>K={fn_top.get('label', 'n/a')}</code> "
                    f"({fn_top.get('mean_us', 0.0) / 1000.0:.2f} ms, {fn_top.get('dpg_fps_mean', 0.0):.1f} FPS)"
                    if fn_top
                    else "n/a"
                ),
            }
        )

        m_hl = [p for p in series.get("mesh", []) if p.get("mode") == "headless"]
        m_30_pts = [p for p in m_hl if p["mean_us"] <= 33_333.0]
        m_30 = max(m_30_pts, key=lambda p: p["scale_x"]) if m_30_pts else None
        m_rt = comfort.get("max_mesh_realtime")
        m_brk = comfort.get("first_mesh_over_rt60")
        m_brk30 = comfort.get("first_mesh_over_rt30")
        m_ui = comfort.get("max_mesh_ui_comfort")
        obs_parts: List[str] = []
        if m_brk:
            obs_parts.append(f"Breaks 60 Hz at <strong>{m_brk['label']}</strong> ({m_brk['mean_us'] / 1000.0:.1f} ms)")
        else:
            obs_parts.append("All tested meshes &le; 16.7 ms")
        if m_brk30:
            obs_parts.append(f"Breaks 30 Hz at <strong>{m_brk30['label']}</strong> ({m_brk30['mean_us'] / 1000.0:.1f} ms)")
        stress_limits_rows.append(
            {
                "axis": "Dense Matrix (<code>L &times; W</code>)",
                "rt60": m_rt["label"] if m_rt else "None",
                "rt30": m_30["label"] if m_30 else "None",
                "ui_comfort": (
                    f"{m_ui['label']} ({m_ui['dpg_fps_mean']:.1f} FPS)" if m_ui else "n/a"
                ),
                "observation": " &bull; ".join(obs_parts),
            }
        )

        opt_w = comfort.get("optimal_workers_1080p")
        opt_wg = comfort.get("optimal_workers_1080p_gui")
        pk_wg = comfort.get("peak_workers_1080p_gui")
        if opt_w:
            if opt_wg:
                gui_str = f"Up to {opt_wg['label']} @ {opt_wg['dpg_fps_mean']:.1f} FPS ({opt_wg['bandwidth_mb_s']:.0f} MB/s)"
            elif pk_wg:
                gui_str = f"Peak {pk_wg['label']} @ {pk_wg['dpg_fps_mean']:.1f} FPS (<25 FPS)"
            else:
                gui_str = "n/a"
            wrk_hl = [p for p in series.get("workers", []) if p.get("mode") == "headless"]
            wrk_30 = [p for p in wrk_hl if p["mean_us"] <= 33_333.0]
            wrk_60 = [p for p in wrk_hl if p["mean_us"] <= 16_667.0]
            stress_limits_rows.append(
                {
                    "axis": "Multiprocess 1080p Workers (<code>P</code>)",
                    "rt60": f"Peak BW: {opt_w['label']} ({opt_w['bandwidth_mb_s']:.1f} MB/s)" + (f" &bull; Latency &le;16.7ms: up to P={max(p['scale_x'] for p in wrk_60)}" if wrk_60 else ""),
                    "rt30": f"Latency &le;33.3 ms at {', '.join(p['label'] for p in wrk_30)}" if wrk_30 else "Round-trip >33.3 ms",
                    "ui_comfort": gui_str,
                    "observation": f"Peak RSS <strong>{opt_w['rss_peak_mb']:.0f} MB</strong> ({opt_w['recv_ops_s']:.0f} frames/s)",
                }
            )

    return {
        "global_takeaways": global_takeaways,
        "stress_limits_rows": stress_limits_rows,
    }


def _extract_scenario_table_rows(scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten every scenario and every recorded sub-metric into filterable table records."""
    rows: List[Dict[str, Any]] = []
    priority = ("e2e_dag_ns", "proc_total_roundtrip_ns", "link_hop_ns", "module_input_cb_ns")

    for sc in scenarios:
        metrics = sc.get("metrics", {}) or {}
        primary_name = ""
        for candidate in priority:
            if candidate in metrics:
                primary_name = candidate
                break
        if not primary_name and metrics:
            primary_name = next(iter(metrics.keys()))

        sys_info = sc.get("system", {}) or {}
        tp = sc.get("throughput", {}) or {}
        name = str(sc.get("scenario", ""))
        mode = str(sc.get("mode", ""))
        fps = float(sys_info.get("dpg_fps_mean", 0.0))
        category = _categorize_scenario(name)
        meta_str = ", ".join(f"{k}={v}" for k, v in (sc.get("metadata") or {}).items())

        metric_items = list(metrics.items()) if metrics else [("none", {})]
        for m_name, m_stats in metric_items:
            mean_us = float(m_stats.get("mean_us", 0.0))
            is_primary = (m_name == primary_name) or (not metrics)
            rows.append(
                {
                    "scenario": name,
                    "category": category,
                    "mode": mode,
                    "metric": m_name or "n/a",
                    "is_primary": is_primary,
                    "count": int(m_stats.get("count", 0)),
                    "min_us": round(float(m_stats.get("min_us", 0.0)), 2),
                    "mean_us": round(mean_us, 2),
                    "std_us": round(float(m_stats.get("std_us", 0.0)), 2),
                    "p50_us": round(float(m_stats.get("p50_us", 0.0)), 2),
                    "p95_us": round(float(m_stats.get("p95_us", 0.0)), 2),
                    "p99_us": round(float(m_stats.get("p99_us", 0.0)), 2),
                    "recv_ops_s": round(float(tp.get("recv_ops_s", 0.0)), 1),
                    "bandwidth_mb_s": round(float(tp.get("bandwidth_mb_s", 0.0)), 2),
                    "rss_peak_mb": round(float(sys_info.get("rss_peak_mb", 0.0)), 1),
                    "cpu_mean_pct": round(float(sys_info.get("cpu_mean_pct", 0.0)), 1),
                    "fps_mean": round(fps, 1),
                    "ui_comfort": FrameworkProfiler.classify_fps_comfort(fps) if mode != "headless" else "Headless",
                    "rt_status": FrameworkProfiler.classify_latency_realtime(mean_us),
                    "metadata": meta_str,
                }
            )
    return rows


def generate_interactive_html(report_json_path: Path, out_html_path: Path) -> Path:
    """Generate a self-contained interactive Plotly HTML report from ``benchmark_report.json``."""
    data = _load_report_json(report_json_path)
    env = data.get("environment") or {}
    scenarios = list(data.get("scenarios", []))
    comfort = data.get("comfort_and_limits") or {}

    by_hm = {(str(s.get("scenario", "")), str(s.get("mode", ""))): s for s in scenarios}
    hop_sc = by_hm.get(("hop_1to1_token", "headless")) or by_hm.get(("cmp_hop_1to1_burst", "headless"))
    hop_m = (hop_sc or {}).get("metrics", {}).get("link_hop_ns", {})
    hop_ops = float((hop_sc or {}).get("throughput", {}).get("recv_ops_s", 0.0))

    shm_4m = by_hm.get(("proc_shm_numpy_4mb", "headless"))
    pkl_4m = by_hm.get(("proc_pickle_numpy_4mb", "headless"))
    shm_bw = float((shm_4m or {}).get("throughput", {}).get("bandwidth_mb_s", 0.0))
    pkl_bw = float((pkl_4m or {}).get("throughput", {}).get("bandwidth_mb_s", 0.0))
    shm_speedup = (shm_bw / pkl_bw) if pkl_bw > 0 else 0.0

    summary_tables = _extract_summary_tables(scenarios, comfort)
    table_rows = _extract_scenario_table_rows(scenarios)
    all_metrics = sorted({r["metric"] for r in table_rows if r["metric"] not in ("none", "n/a")})
    all_categories = sorted({r["category"] for r in table_rows})

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "environment": env,
        "scenario_count": len(scenarios),
        "cards": {
            "hop_mean_us": round(float(hop_m.get("mean_us", 0.0)), 2),
            "hop_p95_us": round(float(hop_m.get("p95_us", 0.0)), 2),
            "hop_ops_s": round(hop_ops, 0),
            "max_chain_rt": comfort.get("max_chain_depth_realtime"),
            "max_chain_ui": comfort.get("max_chain_depth_ui_comfort"),
            "max_fanout_rt": comfort.get("max_fanout_k_realtime"),
            "max_fanout_ui": comfort.get("max_fanout_k_ui_comfort"),
            "max_mesh_rt": comfort.get("max_mesh_realtime"),
            "first_mesh_over_rt60": comfort.get("first_mesh_over_rt60"),
            "first_mesh_over_rt30": comfort.get("first_mesh_over_rt30"),
            "max_mesh_ui": comfort.get("max_mesh_ui_comfort"),
            "opt_workers": comfort.get("optimal_workers_1080p"),
            "opt_workers_gui": comfort.get("optimal_workers_1080p_gui"),
            "peak_workers_gui": comfort.get("peak_workers_1080p_gui"),
            "shm_bw": round(shm_bw, 1),
            "pkl_bw": round(pkl_bw, 1),
            "shm_speedup": round(shm_speedup, 2),
        },
        "global_takeaways": summary_tables["global_takeaways"],
        "stress_limits_rows": summary_tables["stress_limits_rows"],
        "series": comfort.get("series", {}),
        "ipc": _extract_ipc_comparison(scenarios),
        "gui_cmp": _extract_gui_comparison(scenarios),
        "categories": all_categories,
        "metrics": all_metrics,
        "table_rows": table_rows,
    }

    safe_json = json.dumps(payload).replace("</", "<\\/")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Pipeline Creator — Interactive Performance &amp; Stress Report</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {{
    --bg: #f5f7fb;
    --card: #ffffff;
    --ink: #1e293b;
    --muted: #64748b;
    --border: #e2e8f0;
    --blue: #0072B2;
    --vermilion: #D55E00;
    --green: #009E73;
    --amber: #E69F00;
    --purple: #CC79A7;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    background: var(--bg);
    color: var(--ink);
    line-height: 1.5;
  }}
  header {{
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%);
    color: #fff;
    padding: 28px 24px 22px;
  }}
  .wrap {{ max-width: 1380px; margin: 0 auto; padding: 0 20px; }}
  header h1 {{ margin: 0 0 6px; font-size: 1.75rem; }}
  .env-strip {{
    display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; font-size: 0.85rem; color: #cbd5e1;
  }}
  .env-pill {{
    background: rgba(255,255,255,0.12); padding: 4px 10px; border-radius: 999px;
  }}
  section {{ margin: 28px 0; }}
  h2 {{ font-size: 1.25rem; margin: 0 0 12px; border-left: 4px solid var(--blue); padding-left: 10px; }}
  .cards-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px;
  }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 18px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
  }}
  .card-title {{ font-size: 0.82rem; font-weight: 700; text-transform: uppercase; color: var(--muted); letter-spacing: 0.04em; }}
  .card-value {{ font-size: 1.45rem; font-weight: 800; margin: 6px 0; color: var(--blue); }}
  .card-sub {{ font-size: 0.88rem; color: #334155; }}
  .guide-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 14px;
  }}
  .guide-item {{
    background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px;
  }}
  .guide-item h3 {{ margin: 0 0 6px; font-size: 0.98rem; color: #0f172a; }}
  .guide-item p {{ margin: 0; font-size: 0.88rem; color: #475569; }}
  .charts-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(560px, 1fr)); gap: 18px;
  }}
  .chart-card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
  }}
  .chart-box {{ width: 100%; height: 375px; }}
  .chart-note {{ font-size: 0.84rem; color: var(--muted); margin-top: 6px; }}
  .chip-row {{
    display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px;
  }}
  .chip {{
    border: 1px solid var(--border); background: #fff; color: var(--ink);
    padding: 5px 12px; border-radius: 999px; font-size: 0.84rem; font-weight: 600;
    cursor: pointer; transition: all 0.15s ease;
  }}
  .chip:hover {{ border-color: var(--blue); color: var(--blue); }}
  .chip.active {{ background: var(--blue); border-color: var(--blue); color: #fff; }}
  .controls {{
    display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; align-items: center;
  }}
  input[type="text"], select {{
    padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 0.88rem; background: #fff;
  }}
  .btn-reset {{
    padding: 7px 12px; border: 1px solid var(--border); border-radius: 6px; background: #f8fafc;
    color: var(--ink); font-size: 0.86rem; font-weight: 600; cursor: pointer;
  }}
  .btn-reset:hover {{ background: #e2e8f0; }}
  .table-wrap {{
    overflow-x: auto; background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }}
  th {{ background: #f8fafc; font-weight: 700; color: #334155; position: sticky; top: 0; user-select: none; }}
  th.sortable {{ cursor: pointer; }}
  th.sortable:hover {{ background: #eef2f7; color: var(--blue); }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.wrap-cell {{ white-space: normal; min-width: 190px; }}
  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.76rem; font-weight: 700;
  }}
  .b-smooth {{ background: #dcfce7; color: #166534; }}
  .b-comfort {{ background: #dbeafe; color: #1e40af; }}
  .b-sluggish {{ background: #fef3c7; color: #92400e; }}
  .b-critical {{ background: #fee2e2; color: #991b1b; }}
  .b-neutral {{ background: #f1f5f9; color: #475569; }}
  .b-cat {{ background: #eff6ff; color: #1d4ed8; }}
  .delta-pos {{ color: #15803d; font-weight: 600; }}
  .delta-neg {{ color: #b91c1c; font-weight: 600; }}
  .offline-warn {{
    background: #fef3c7; color: #92400e; padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; display: none;
  }}
  @media (max-width: 640px) {{
    .charts-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>Pipeline Creator — PC Capacity, Stress Limits &amp; Performance Report</h1>
    <div>Interactive synthesis built from recorded profiler telemetry — find where latency or UI comfort breaks on your machine.</div>
    <div class="env-strip" id="envStrip"></div>
  </div>
</header>

<main class="wrap">
  <section>
    <h2>At a Glance: What Your PC Can Handle</h2>
    <div class="cards-grid" id="cardsGrid"></div>
  </section>

  <section id="globalSummarySection">
    <h2>Global Summary (Key Takeaways — Primitives, Routing, IPC &amp; Workers)</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Benchmark Primitive / Operation</th>
            <th>Mean Latency (median p50 / p95) or Metric</th>
            <th>Throughput / Breakdown Detail</th>
          </tr>
        </thead>
        <tbody id="globalSummaryBody"></tbody>
      </table>
    </div>
  </section>

  <section id="stressLimitsSection">
    <h2>Practical PC Limits &amp; UI Comfort (Stress-Test Synthesis)</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Stress Axis</th>
            <th>Real-Time 60 Hz (&le;16.7 ms)</th>
            <th>Real-Time 30 Hz (&le;33.3 ms)</th>
            <th>GUI Live-UI Comfort (&ge;25 FPS)</th>
            <th>Observation / Peak &amp; Breaking Point</th>
          </tr>
        </thead>
        <tbody id="stressLimitsBody"></tbody>
      </table>
    </div>
  </section>

  <section id="guiImpactSection">
    <h2>GUI Impact Summary (Headless vs Passive GUI vs Live-UI @ 30 Hz)</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Paired Scenario</th>
            <th class="num">Headless Ops/s</th>
            <th class="num">Passive GUI Ops/s</th>
            <th class="num">Live UI 30 Hz Ops/s</th>
            <th>Mean / p95 Latency (HL &rarr; Passive &rarr; Live)</th>
            <th class="num">Live / GUI FPS</th>
            <th class="num">Peak RSS HL &rarr; GUI (MB)</th>
          </tr>
        </thead>
        <tbody id="guiImpactBody"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>How to Interpret These Numbers (Beginner Guide)</h2>
    <div class="guide-grid">
      <div class="guide-item">
        <h3>1. Message Hop vs Graph Depth</h3>
        <p>Connecting two nodes passes Python object references (zero-copy) in a few microseconds. Even 40–160 chained modules stay fast unless many all-to-all mesh edges explode the total hop count (<code>O(L &middot; W&sup2;)</code>).</p>
      </div>
      <div class="guide-item">
        <h3>2. Real-Time Budgets (16.7 ms &amp; 33.3 ms)</h3>
        <p>To keep up with a <strong>60 Hz</strong> camera/sensor stream, end-to-end graph propagation must finish under <strong>16.7 ms</strong> (or <strong>33.3 ms</strong> at 30 Hz). Above that, frames start queuing or dropping.</p>
      </div>
      <div class="guide-item">
        <h3>3. GUI Viewport FPS &amp; Comfort Zones</h3>
        <p><strong>Smooth (&ge; 45 FPS):</strong> snappy editor &amp; telemetry. <strong>Comfortable (25–45 FPS):</strong> usable live monitoring. <strong>Sluggish (12–25 FPS) / Critical (&lt; 12 FPS):</strong> UI thread saturated by synchronous graph bursts or dense widget refreshes.</p>
      </div>
      <div class="guide-item">
        <h3>4. Shared Memory (ShmPool) &amp; Workers</h3>
        <p>For large NumPy frames (&ge; 64 KB / 1080p RGB), <code>ShmPool</code> skips OS pipe pickling and shares RAM pages directly. Adding worker processes speeds up heavy frames until RAM-copy bandwidth caps out and RSS grows.</p>
      </div>
      <div class="guide-item">
        <h3>5. Line Series Shading (Mean&rarr;p95 &amp; p50&rarr;p95 Jitter Bands)</h3>
        <p>The translucent shaded envelope above a latency curve spans from the <strong>mean (or median p50)</strong> up to the <strong>95th percentile (p95 tail latency)</strong>—the slowest 5% of pulses caused by Python GC, OS scheduling, or UI widget refreshes. A wider shading band indicates higher latency jitter.</p>
      </div>
    </div>
  </section>

  <section>
    <h2>Stress Scaling &amp; Breaking Points</h2>
    <div id="offlineWarn" class="offline-warn">
      Plotly.js CDN could not be loaded (offline environment). All executive tables, comfort cards, guide explanations, and filterable scenario telemetry remain 100% functional.
    </div>
    <div class="charts-grid">
      <div class="chart-card">
        <div id="chart-chain" class="chart-box"></div>
        <div class="chart-note">Serial chain depth D (2 &rarr; 160 nodes): Headless vs Live GUI latency + shaded Mean&ndash;p95 jitter band (left) and DearPyGui FPS (right).</div>
      </div>
      <div class="chart-card">
        <div id="chart-fanout" class="chart-box"></div>
        <div class="chart-note">1-to-K broadcast fan-out (1 &rarr; 192 consumers): dispatch latency + shaded Mean&ndash;p95 jitter band vs UI FPS.</div>
      </div>
      <div class="chart-card">
        <div id="chart-mesh" class="chart-box"></div>
        <div class="chart-note">Dense L&times;W mesh DAGs: combinatorial edge explosion vs 16.7 ms (60 Hz) and 33.3 ms (30 Hz) thresholds.</div>
      </div>
      <div class="chart-card">
        <div id="chart-workers" class="chart-box"></div>
        <div class="chart-note">1080p RGB multiprocess workers P: parallel bandwidth (MB/s), GUI FPS, and hover RSS memory footprint.</div>
      </div>
      <div class="chart-card">
        <div id="chart-ipc" class="chart-box"></div>
        <div class="chart-note">Zero-copy <code>ShmPool</code> vs Pure Pickle cross-process transfer bandwidth across frame sizes.</div>
      </div>
      <div class="chart-card">
        <div id="chart-gui-impact" class="chart-box"></div>
        <div class="chart-note">Headless vs Passive GUI vs Live UI (30 Hz) throughput &amp; viewport FPS on representative pipelines.</div>
      </div>
    </div>
  </section>

  <section>
    <h2>Explore All Recorded Scenarios &amp; Sub-Metrics</h2>
    <div class="chip-row" id="quickChips">
      <button class="chip active" data-preset="all">All</button>
      <button class="chip" data-preset="primitives">&#9889; Primitives &amp; Routing</button>
      <button class="chip" data-preset="topology">&#128279; Topology Matrix</button>
      <button class="chip" data-preset="ipc">&#128640; IPC &amp; ShmPool</button>
      <button class="chip" data-preset="gui_cmp">&#128421;&#65039; GUI Comparisons</button>
      <button class="chip" data-preset="stress">&#128293; Stress Suite</button>
      <button class="chip" data-preset="bottlenecks">&#9888;&#65039; Bottlenecks (&lt;25 FPS or &gt;16.7 ms)</button>
    </div>
    <div class="controls">
      <input type="text" id="searchBox" placeholder="Search scenario, category, metric, or metadata..." style="min-width: 240px;" />
      <select id="categoryFilter" title="Filter by benchmark category">
        <option value="all">All categories</option>
      </select>
      <select id="modeFilter" title="Filter by execution mode">
        <option value="all">All modes</option>
        <option value="headless">headless</option>
        <option value="gui">gui (passive)</option>
        <option value="gui_live_ui">gui_live_ui (30 Hz)</option>
        <option value="any_gui">Any GUI (gui + gui_live_ui)</option>
      </select>
      <select id="comfortFilter" title="Filter by UI comfort threshold">
        <option value="all">All comfort levels</option>
        <option value="smooth">Smooth (&ge;45 FPS)</option>
        <option value="comfortable">Comfortable (25–45 FPS)</option>
        <option value="sluggish">Sluggish (12–25 FPS)</option>
        <option value="critical">Critical (&lt;12 FPS)</option>
        <option value="degraded">Degraded (&lt;25 FPS)</option>
        <option value="headless">Headless only</option>
      </select>
      <select id="rtFilter" title="Filter by real-time frame budget">
        <option value="all">All real-time budgets</option>
        <option value="rt60">Real-Time 60 Hz (&le;16.7 ms)</option>
        <option value="rt30">Real-Time 30 Hz (16.7–33.3 ms)</option>
        <option value="over60">Over 60 Hz (&gt;16.7 ms)</option>
        <option value="over30">Over 30 Hz (&gt;33.3 ms)</option>
      </select>
      <select id="metricScopeFilter" title="Choose primary scenario metric or inspect internal stages">
        <option value="primary">Primary Metric per Scenario (Default)</option>
        <option value="all">All Sub-Metrics (Detailed Stages)</option>
      </select>
      <select id="unitSelect" title="Display latency in µs or ms">
        <option value="us">Latency in µs</option>
        <option value="ms">Latency in ms</option>
      </select>
      <button class="btn-reset" id="resetFiltersBtn">Reset Filters</button>
      <span id="rowCount" style="color: var(--muted); font-size: 0.86rem; font-weight: 600;"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="sortable" data-sort="scenario">Scenario <span class="sort-ind"></span></th>
            <th class="sortable" data-sort="category">Category <span class="sort-ind"></span></th>
            <th class="sortable" data-sort="mode">Mode <span class="sort-ind"></span></th>
            <th class="sortable" data-sort="metric">Metric <span class="sort-ind"></span></th>
            <th class="num sortable" data-sort="mean_us">Mean <span class="unit-lbl">(µs)</span> <span class="sort-ind"></span></th>
            <th class="num sortable" data-sort="p50_us">p50 <span class="unit-lbl">(µs)</span> <span class="sort-ind"></span></th>
            <th class="num sortable" data-sort="p95_us">p95 <span class="unit-lbl">(µs)</span> <span class="sort-ind"></span></th>
            <th class="num sortable" data-sort="p99_us">p99 <span class="unit-lbl">(µs)</span> <span class="sort-ind"></span></th>
            <th class="num sortable" data-sort="recv_ops_s">Throughput (ops/s) <span class="sort-ind"></span></th>
            <th class="num sortable" data-sort="bandwidth_mb_s">Bandwidth (MB/s) <span class="sort-ind"></span></th>
            <th class="num sortable" data-sort="rss_peak_mb">Peak RSS (MB) <span class="sort-ind"></span></th>
            <th class="num sortable" data-sort="fps_mean">GUI FPS <span class="sort-ind"></span></th>
            <th class="sortable" data-sort="ui_comfort">UI Comfort <span class="sort-ind"></span></th>
            <th class="sortable" data-sort="rt_status">Real-Time <span class="sort-ind"></span></th>
          </tr>
        </thead>
        <tbody id="tableBody"></tbody>
      </table>
    </div>
  </section>
</main>

<script>
const REPORT = {safe_json};

function badgeClass(label) {{
  if (!label) return "b-neutral";
  if (label.startsWith("Smooth")) return "b-smooth";
  if (label.startsWith("Comfortable")) return "b-comfort";
  if (label.startsWith("Sluggish")) return "b-sluggish";
  if (label.startsWith("Critical")) return "b-critical";
  return "b-neutral";
}}

(function renderStaticUI() {{
  const env = REPORT.environment || {{}};
  const pills = [
    `OS: ${{env.platform || "n/a"}}`,
    `Python: ${{env.python_version || "n/a"}}`,
    `Cores: ${{env.cpu_count_logical || "?"}} logical / ${{env.cpu_count_physical || "?"}} physical`,
    `RAM: ${{env.ram_total_gb || "?"}} GB`,
    `Scenarios: ${{REPORT.scenario_count}}`,
    `Generated: ${{REPORT.generated_at}}`,
  ];
  document.getElementById("envStrip").innerHTML = pills.map(p => `<span class="env-pill">${{p}}</span>`).join("");

  const c = REPORT.cards || {{}};
  const optW = c.opt_workers;
  const optWg = c.opt_workers_gui || c.peak_workers_gui;
  const meshRt = c.max_mesh_rt ? c.max_mesh_rt.label : "None";
  const meshBrk = c.first_mesh_over_rt60 ? `${{c.first_mesh_over_rt60.label}} (${{(c.first_mesh_over_rt60.mean_us/1000).toFixed(1)}} ms)` : "All &le; 16.7 ms";
  const meshUi = c.max_mesh_ui ? `${{c.max_mesh_ui.label}} (${{c.max_mesh_ui.dpg_fps_mean.toFixed(1)}} FPS)` : "n/a";

  const cards = [
    {{
      title: "1:1 Module Transfer Speed",
      val: `${{c.hop_mean_us.toFixed(2)}} µs`,
      sub: `p95: ${{c.hop_p95_us.toFixed(2)}} µs &bull; ${{(c.hop_ops_s/1000).toFixed(1)}}k msg/s (zero-copy Python ref)`,
    }},
    {{
      title: "Chain & Fan-Out Limits (60 Hz)",
      val: `Chain D &le; ${{c.max_chain_rt ?? "n/a"}} &bull; Fan-Out K &le; ${{c.max_fanout_rt ?? "n/a"}}`,
      sub: `Live-UI comfort (&ge;25 FPS): D &le; ${{c.max_chain_ui ?? "n/a"}}, K &le; ${{c.max_fanout_ui ?? "n/a"}}`,
    }},
    {{
      title: "Dense Matrix Breaking Point",
      val: meshRt,
      sub: `60 Hz limit exceeded at: ${{meshBrk}} &bull; GUI comfort: ${{meshUi}}`,
    }},
    {{
      title: "1080p Workers & SharedMemory",
      val: optW ? `${{optW.label}} &rarr; ${{optW.bandwidth_mb_s.toFixed(0)}} MB/s` : "n/a",
      sub: `ShmPool speedup (4 MB): ${{c.shm_speedup ? c.shm_speedup + "x" : "n/a"}}` +
           (optWg ? ` &bull; GUI @ ${{optWg.label}}: ${{optWg.dpg_fps_mean.toFixed(1)}} FPS` : "") +
           (optW ? ` &bull; RSS: ${{optW.rss_peak_mb.toFixed(0)}} MB` : ""),
    }},
  ];
  document.getElementById("cardsGrid").innerHTML = cards.map(card => `
    <div class="card">
      <div class="card-title">${{card.title}}</div>
      <div class="card-value">${{card.val}}</div>
      <div class="card-sub">${{card.sub}}</div>
    </div>
  `).join("");

  // Global Summary Takeaways table
  const takeaways = REPORT.global_takeaways || [];
  if (takeaways.length) {{
    document.getElementById("globalSummaryBody").innerHTML = takeaways.map(row => `
      <tr>
        <td class="wrap-cell">${{row.metric}}</td>
        <td><strong>${{row.latency}}</strong></td>
        <td class="wrap-cell">${{row.detail}}</td>
      </tr>
    `).join("");
  }} else {{
    document.getElementById("globalSummarySection").style.display = "none";
  }}

  // Practical PC Limits & UI Comfort table
  const stressRows = REPORT.stress_limits_rows || [];
  if (stressRows.length) {{
    document.getElementById("stressLimitsBody").innerHTML = stressRows.map(row => `
      <tr>
        <td>${{row.axis}}</td>
        <td>${{row.rt60}}</td>
        <td>${{row.rt30}}</td>
        <td>${{row.ui_comfort}}</td>
        <td class="wrap-cell">${{row.observation}}</td>
      </tr>
    `).join("");
  }} else {{
    document.getElementById("stressLimitsSection").style.display = "none";
  }}

  // GUI Impact Summary table
  const guiRows = REPORT.gui_cmp || [];
  if (guiRows.length) {{
    const fmtDelta = (val) => {{
      if (val === null || val === undefined) return "";
      const cls = val >= 0 ? "delta-pos" : "delta-neg";
      const sign = val >= 0 ? "+" : "";
      return ` <span class="${{cls}}">(${{sign}}${{val.toFixed(1)}}%)</span>`;
    }};
    const fmtLatVal = (us) => us >= 1000 ? `${{(us/1000).toFixed(2)}} ms` : `${{us.toFixed(1)}} µs`;
    document.getElementById("guiImpactBody").innerHTML = guiRows.map(r => {{
      const h = r.headless;
      const g = r.gui;
      const l = r.gui_live_ui;
      const gCell = g.present ? `${{g.ops_s.toLocaleString()}}${{fmtDelta(g.tp_delta_pct)}}` : "n/a";
      const lCell = l.present ? `${{l.ops_s.toLocaleString()}}${{fmtDelta(l.tp_delta_pct)}}` : "n/a";
      const latParts = [fmtLatVal(h.mean_us)];
      if (g.present) latParts.push(fmtLatVal(g.mean_us));
      if (l.present) latParts.push(fmtLatVal(l.mean_us));
      const fpsSrc = l.present ? l : g;
      const rssGui = Math.max(g.present ? g.rss_mb : 0, l.present ? l.rss_mb : 0);
      return `
        <tr>
          <td><code>${{r.scenario}}</code></td>
          <td class="num">${{h.ops_s.toLocaleString()}}</td>
          <td class="num">${{gCell}}</td>
          <td class="num">${{lCell}}</td>
          <td>${{latParts.join(" &rarr; ")}}</td>
          <td class="num">${{fpsSrc.present ? fpsSrc.fps.toFixed(1) : "—"}} <span class="badge ${{badgeClass(fpsSrc.comfort)}}">${{fpsSrc.comfort}}</span></td>
          <td class="num">${{h.rss_mb.toFixed(0)}} &rarr; ${{rssGui.toFixed(0)}}</td>
        </tr>
      `;
    }}).join("");
  }} else {{
    document.getElementById("guiImpactSection").style.display = "none";
  }}
}})();

(function setupTable() {{
  const tbody = document.getElementById("tableBody");
  const searchEl = document.getElementById("searchBox");
  const catEl = document.getElementById("categoryFilter");
  const modeEl = document.getElementById("modeFilter");
  const comfortEl = document.getElementById("comfortFilter");
  const rtEl = document.getElementById("rtFilter");
  const scopeEl = document.getElementById("metricScopeFilter");
  const unitEl = document.getElementById("unitSelect");
  const resetBtn = document.getElementById("resetFiltersBtn");
  const countEl = document.getElementById("rowCount");
  const chips = Array.from(document.querySelectorAll("#quickChips .chip"));
  const headers = Array.from(document.querySelectorAll("th.sortable"));

  (REPORT.categories || []).forEach(cat => {{
    const opt = document.createElement("option");
    opt.value = cat;
    opt.textContent = cat;
    catEl.appendChild(opt);
  }});
  (REPORT.metrics || []).forEach(met => {{
    const opt = document.createElement("option");
    opt.value = `metric:${{met}}`;
    opt.textContent = `Metric: ${{met}}`;
    scopeEl.appendChild(opt);
  }});

  let activePreset = "all";
  let sortCol = null;
  let sortAsc = true;

  function fmtLat(valUs, unit) {{
    if (unit === "ms") return (valUs / 1000.0).toFixed(3);
    return valUs.toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
  }}

  function setActiveChip(presetName) {{
    activePreset = presetName;
    chips.forEach(ch => ch.classList.toggle("active", ch.dataset.preset === presetName));
  }}

  chips.forEach(chip => {{
    chip.addEventListener("click", () => {{
      setActiveChip(chip.dataset.preset);
      catEl.value = "all";
      modeEl.value = "all";
      comfortEl.value = "all";
      rtEl.value = "all";
      if (activePreset === "primitives") catEl.value = "Primitives & Routing";
      else if (activePreset === "topology") catEl.value = "Topology Scaling";
      else if (activePreset === "ipc") catEl.value = "Multiprocess & IPC";
      else if (activePreset === "gui_cmp") catEl.value = "GUI Comparison";
      render();
    }});
  }});

  headers.forEach(th => {{
    th.addEventListener("click", () => {{
      const col = th.dataset.sort;
      if (sortCol === col) {{
        sortAsc = !sortAsc;
      }} else {{
        sortCol = col;
        sortAsc = (col === "scenario" || col === "category" || col === "mode" || col === "metric" || col === "mean_us" || col === "p95_us");
      }}
      headers.forEach(h => {{
        const ind = h.querySelector(".sort-ind");
        if (ind) ind.textContent = h === th ? (sortAsc ? "▲" : "▼") : "";
      }});
      render();
    }});
  }});

  resetBtn.addEventListener("click", () => {{
    searchEl.value = "";
    catEl.value = "all";
    modeEl.value = "all";
    comfortEl.value = "all";
    rtEl.value = "all";
    scopeEl.value = "primary";
    unitEl.value = "us";
    sortCol = null;
    headers.forEach(h => {{
      const ind = h.querySelector(".sort-ind");
      if (ind) ind.textContent = "";
    }});
    setActiveChip("all");
    render();
  }});

  function render() {{
    const q = searchEl.value.trim().toLowerCase();
    const cat = catEl.value;
    const m = modeEl.value;
    const comf = comfortEl.value;
    const rt = rtEl.value;
    const scope = scopeEl.value;
    const unit = unitEl.value;

    document.querySelectorAll(".unit-lbl").forEach(el => {{
      el.textContent = unit === "ms" ? "(ms)" : "(µs)";
    }});

    let rows = REPORT.table_rows.filter(r => {{
      if (scope === "primary" && !r.is_primary) return false;
      if (scope.startsWith("metric:") && r.metric !== scope.slice(7)) return false;

      if (activePreset === "stress" && !r.scenario.startsWith("stress_")) return false;
      if (activePreset === "bottlenecks") {{
        const badFps = r.mode !== "headless" && r.fps_mean > 0 && r.fps_mean < 25.0;
        const badLat = r.mean_us > 16667.0;
        if (!badFps && !badLat) return false;
      }}

      if (cat !== "all" && r.category !== cat) return false;

      if (m === "any_gui") {{
        if (r.mode === "headless") return false;
      }} else if (m !== "all" && r.mode !== m) {{
        return false;
      }}

      if (comf !== "all") {{
        if (comf === "smooth" && !r.ui_comfort.startsWith("Smooth")) return false;
        if (comf === "comfortable" && !r.ui_comfort.startsWith("Comfortable")) return false;
        if (comf === "sluggish" && !r.ui_comfort.startsWith("Sluggish")) return false;
        if (comf === "critical" && !r.ui_comfort.startsWith("Critical")) return false;
        if (comf === "degraded" && !(r.ui_comfort.startsWith("Sluggish") || r.ui_comfort.startsWith("Critical"))) return false;
        if (comf === "headless" && r.mode !== "headless") return false;
      }}

      if (rt !== "all") {{
        if (rt === "rt60" && r.mean_us > 16667.0) return false;
        if (rt === "rt30" && (r.mean_us <= 16667.0 || r.mean_us > 33333.0)) return false;
        if (rt === "over60" && r.mean_us <= 16667.0) return false;
        if (rt === "over30" && r.mean_us <= 33333.0) return false;
      }}

      if (q) {{
        const hay = `${{r.scenario}} ${{r.category}} ${{r.metric}} ${{r.metadata}}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }}
      return true;
    }});

    if (sortCol) {{
      rows = [...rows].sort((a, b) => {{
        const va = a[sortCol];
        const vb = b[sortCol];
        if (typeof va === "number" && typeof vb === "number") {{
          return sortAsc ? va - vb : vb - va;
        }}
        return sortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
      }});
    }}

    const totalForScope = scope === "primary"
      ? REPORT.table_rows.filter(r => r.is_primary).length
      : REPORT.table_rows.length;
    countEl.textContent = `Showing ${{rows.length}} of ${{totalForScope}} rows`;

    tbody.innerHTML = rows.map(r => `
      <tr title="${{r.metadata}}">
        <td><code>${{r.scenario}}</code></td>
        <td><span class="badge b-cat">${{r.category}}</span></td>
        <td>${{r.mode}}</td>
        <td><code>${{r.metric}}</code></td>
        <td class="num">${{fmtLat(r.mean_us, unit)}}</td>
        <td class="num">${{fmtLat(r.p50_us, unit)}}</td>
        <td class="num">${{fmtLat(r.p95_us, unit)}}</td>
        <td class="num">${{fmtLat(r.p99_us, unit)}}</td>
        <td class="num">${{r.recv_ops_s.toLocaleString()}}</td>
        <td class="num">${{r.bandwidth_mb_s.toFixed(2)}}</td>
        <td class="num">${{r.rss_peak_mb.toFixed(1)}}</td>
        <td class="num">${{r.mode === "headless" ? "—" : r.fps_mean.toFixed(1)}}</td>
        <td><span class="badge ${{badgeClass(r.ui_comfort)}}">${{r.ui_comfort}}</span></td>
        <td>${{r.rt_status}}</td>
      </tr>
    `).join("");
  }}

  [searchEl, catEl, modeEl, comfortEl, rtEl, scopeEl, unitEl].forEach(el => {{
    const ev = el.tagName === "INPUT" ? "input" : "change";
    el.addEventListener(ev, () => {{
      if (el === catEl && activePreset !== "bottlenecks" && activePreset !== "stress") setActiveChip("all");
      render();
    }});
  }});
  render();
}})();

(function renderPlotlyCharts() {{
  if (typeof Plotly === "undefined") {{
    document.getElementById("offlineWarn").style.display = "block";
    return;
  }}

  const baseLayout = (title, xlab, ylabLeft, ylabRight) => {{
    const layout = {{
      title: {{ text: title, font: {{ size: 14 }} }},
      margin: {{ l: 60, r: ylabRight ? 58 : 20, t: 46, b: 56 }},
      hovermode: "x unified",
      legend: {{ orientation: "h", y: -0.24 }},
      xaxis: {{ title: xlab, gridcolor: "#e2e8f0" }},
      yaxis: {{ title: ylabLeft, gridcolor: "#e2e8f0" }},
      paper_bgcolor: "#fff",
      plot_bgcolor: "#fff",
    }};
    if (ylabRight) {{
      layout.yaxis2 = {{
        title: ylabRight,
        overlaying: "y",
        side: "right",
        rangemode: "tozero",
        showgrid: false,
      }};
    }}
    return layout;
  }};

  const plotAxis = (divId, axisKey, title, xlab, logY = false) => {{
    const pts = (REPORT.series && REPORT.series[axisKey]) || [];
    const hl = pts.filter(p => p.mode === "headless");
    const gui = pts.filter(p => p.mode !== "headless");
    const traces = [];
    if (hl.length) {{
      traces.push({{
        x: hl.map(p => p.scale_x),
        y: hl.map(p => +(p.mean_us / 1000.0).toFixed(3)),
        text: hl.map(p => `${{p.label}} | p95=${{(p.p95_us/1000).toFixed(2)}} ms | ${{p.recv_ops_s.toFixed(0)}} ops/s`),
        name: "Headless Mean (ms)",
        type: "scatter",
        mode: "lines+markers",
        line: {{ color: "#0072B2", width: 2.5 }},
      }});
      if (!logY) {{
        traces.push({{
          x: hl.map(p => p.scale_x),
          y: hl.map(p => +(p.p95_us / 1000.0).toFixed(3)),
          name: "Headless Mean–p95 jitter",
          type: "scatter",
          mode: "lines",
          line: {{ color: "rgba(0,114,178,0.22)", width: 0.8 }},
          fill: "tonexty",
          fillcolor: "rgba(0,114,178,0.14)",
        }});
      }}
    }}
    if (gui.length) {{
      traces.push({{
        x: gui.map(p => p.scale_x),
        y: gui.map(p => +(p.mean_us / 1000.0).toFixed(3)),
        text: gui.map(p => `${{p.label}} (${{p.mode}}) | p95=${{(p.p95_us/1000).toFixed(2)}} ms | ${{p.ui_comfort}}`),
        name: "Live-UI Mean (ms)",
        type: "scatter",
        mode: "lines+markers",
        line: {{ color: "#D55E00", width: 2.5 }},
      }});
      if (!logY) {{
        traces.push({{
          x: gui.map(p => p.scale_x),
          y: gui.map(p => +(p.p95_us / 1000.0).toFixed(3)),
          name: "Live-UI Mean–p95 jitter",
          type: "scatter",
          mode: "lines",
          line: {{ color: "rgba(213,94,0,0.22)", width: 0.8 }},
          fill: "tonexty",
          fillcolor: "rgba(213,94,0,0.14)",
        }});
      }}
      traces.push({{
        x: gui.map(p => p.scale_x),
        y: gui.map(p => p.dpg_fps_mean),
        text: gui.map(p => `${{p.ui_comfort}}`),
        name: "GUI Viewport FPS",
        yaxis: "y2",
        type: "scatter",
        mode: "lines+markers",
        line: {{ color: "#009E73", width: 2, dash: "dot" }},
        marker: {{ symbol: "diamond" }},
      }});
    }}
    const layout = baseLayout(title, xlab, "Latency (ms)", gui.length ? "Viewport FPS" : null);
    if (logY) layout.yaxis.type = "log";
    if (axisKey === "mesh") {{
      const src = hl.length ? hl : gui;
      if (src.length) {{
        layout.xaxis.type = "log";
        layout.xaxis.tickvals = src.map(p => p.scale_x);
        layout.xaxis.ticktext = src.map(p => p.label);
      }}
    }}
    layout.shapes = [
      {{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 16.67, y1: 16.67, line: {{ color: "#E69F00", dash: "dash", width: 1.3 }} }},
      {{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 33.33, y1: 33.33, line: {{ color: "#CC79A7", dash: "dash", width: 1.3 }} }},
    ];
    if (gui.length) {{
      layout.shapes.push({{
        type: "line", xref: "paper", yref: "y2", x0: 0, x1: 1, y0: 25, y1: 25,
        line: {{ color: "#009E73", dash: "dashdot", width: 1.2 }}
      }});
    }}
    Plotly.newPlot(divId, traces, layout, {{ responsive: true }});
  }};

  plotAxis("chart-chain", "chain", "Serial Chain Depth Scaling (D)", "Chain Depth D (hops)", false);
  plotAxis("chart-fanout", "fanout", "Parallel Fan-Out Scaling (K)", "Fan-Out Branches K", false);
  plotAxis("chart-mesh", "mesh", "Dense Matrix Saturation (L x W)", "Mesh Topology (fan-in/out deliveries per pulse, log)", true);

  // Workers 1080p chart
  {{
    const pts = (REPORT.series && REPORT.series.workers) || [];
    const hl = pts.filter(p => p.mode === "headless");
    const gui = pts.filter(p => p.mode !== "headless");
    const traces = [];
    if (hl.length) {{
      traces.push({{
        x: hl.map(p => p.scale_x),
        y: hl.map(p => p.bandwidth_mb_s),
        text: hl.map(p => `${{p.recv_ops_s.toFixed(0)}} FPS | Peak RSS: ${{p.rss_peak_mb.toFixed(0)}} MB`),
        name: "Headless Bandwidth (MB/s)",
        type: "scatter",
        mode: "lines+markers",
        line: {{ color: "#0072B2", width: 2.5 }},
      }});
    }}
    if (gui.length) {{
      traces.push({{
        x: gui.map(p => p.scale_x),
        y: gui.map(p => p.bandwidth_mb_s),
        text: gui.map(p => `${{p.recv_ops_s.toFixed(0)}} FPS | Peak RSS: ${{p.rss_peak_mb.toFixed(0)}} MB`),
        name: "Live-UI Bandwidth (MB/s)",
        type: "scatter",
        mode: "lines+markers",
        line: {{ color: "#D55E00", width: 2.5 }},
      }});
      traces.push({{
        x: gui.map(p => p.scale_x),
        y: gui.map(p => p.dpg_fps_mean),
        name: "GUI Viewport FPS",
        yaxis: "y2",
        type: "scatter",
        mode: "lines+markers",
        line: {{ color: "#009E73", width: 2, dash: "dot" }},
      }});
    }}
    const layout = baseLayout("1080p RGB Multiprocess Scaling", "Worker Subprocesses P", "Throughput (MB/s)", gui.length ? "Viewport FPS" : null);
    Plotly.newPlot("chart-workers", traces, layout, {{ responsive: true }});
  }}

  // IPC comparison chart
  {{
    const ipc = REPORT.ipc || [];
    const traces = [
      {{
        x: ipc.map(r => r.label),
        y: ipc.map(r => r.shm_bw_mb_s),
        text: ipc.map(r => `${{r.shm_ops_s.toFixed(0)}} ops/s | ${{r.shm_lat_us.toFixed(0)}} µs`),
        name: "ShmPool Zero-Copy (MB/s)",
        type: "bar",
        marker: {{ color: "#0072B2" }},
      }},
      {{
        x: ipc.map(r => r.label),
        y: ipc.map(r => r.pkl_bw_mb_s),
        text: ipc.map(r => `${{r.pkl_ops_s.toFixed(0)}} ops/s | ${{r.pkl_lat_us.toFixed(0)}} µs`),
        name: "Pure Pickle IPC (MB/s)",
        type: "bar",
        marker: {{ color: "#D55E00" }},
      }},
    ];
    const layout = baseLayout("Shared Memory (ShmPool) vs Pure Pickle", "NumPy Payload Size", "Bandwidth (MB/s)", null);
    layout.barmode = "group";
    Plotly.newPlot("chart-ipc", traces, layout, {{ responsive: true }});
  }}

  // Headless vs Passive GUI vs Live UI chart
  {{
    const cmp = REPORT.gui_cmp || [];
    const names = cmp.map(r => r.scenario.replace(/^cmp_/, ""));
    const traces = [
      {{ x: names, y: cmp.map(r => r.headless.ops_s), name: "Headless (ops/s)", type: "bar", marker: {{ color: "#0072B2" }} }},
      {{ x: names, y: cmp.map(r => r.gui.ops_s), name: "Passive GUI (ops/s)", type: "bar", marker: {{ color: "#E69F00" }} }},
      {{ x: names, y: cmp.map(r => r.gui_live_ui.ops_s), name: "Live UI 30Hz (ops/s)", type: "bar", marker: {{ color: "#009E73" }} }},
    ];
    const layout = baseLayout("UI Overhead Comparison (Paired Scenarios)", "Scenario", "Throughput (ops/s, log)", null);
    layout.barmode = "group";
    layout.yaxis.type = "log";
    Plotly.newPlot("chart-gui-impact", traces, layout, {{ responsive: true }});
  }}
}})();
</script>
</body>
</html>
"""
    out_path = Path(out_html_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _md_cell(lines: List[str]) -> Dict[str, Any]:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": [line if line.endswith("\n") else line + "\n" for line in lines],
    }


def _code_cell(lines: List[str]) -> Dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [line if line.endswith("\n") else line + "\n" for line in lines],
    }


def generate_jupyter_notebook(
    report_json_path: Path,
    report_csv_path: Path,
    out_ipynb_path: Path,
) -> Path:
    """Create a clean nbformat v4 Jupyter Notebook for interactive exploration."""
    data = _load_report_json(report_json_path)
    env = data.get("environment") or {}
    json_name = Path(report_json_path).name
    csv_name = Path(report_csv_path).name

    intro_md = _md_cell([
        "# Pipeline Creator — Interactive Performance & Stress Analysis",
        "",
        f"- **Machine**: `{env.get('platform', 'n/a')}` | **CPU**: `{env.get('cpu_count_logical', '?')}` logical / `{env.get('cpu_count_physical', '?')}` physical cores | **RAM**: `{env.get('ram_total_gb', '?')} GB` | **Python**: `{env.get('python_version', 'n/a')}`",
        f"- **Recorded Scenarios**: `{len(data.get('scenarios', []))}` (exported `{env.get('timestamp', 'n/a')}`)",
        "",
        "## Plain-Language Interpretation Guide",
        "1. **Message Passing (`link_hop_ns` / `e2e_dag_ns`)**: Direct link hops pass Python object references with zero copy (~1–3 µs). Real-time 60 Hz cameras have a **16.7 ms** frame budget (30 Hz = **33.3 ms**).",
        "2. **Stress Breaking Point**: As chain depth (`D`), fan-out (`K`), or dense matrix (`L × W`) edges grow (`O(L·W²)`), latency eventually crosses `16.7 ms` or UI FPS drops below comfortable thresholds.",
        "3. **GUI Viewport FPS**: `>= 45 FPS` Smooth, `25–45 FPS` Comfortable, `12–25 FPS` Sluggish, `< 12 FPS` Critical UI starvation.",
        "4. **Shared Memory (`ShmPool`) vs Pure Pickle**: Frames `>= 64 KB` avoid pipe serialization via `ShmPool`; scaling multiple 1080p RGB workers boosts throughput until host memory bandwidth saturates.",
        "5. **Line Series Shading (`Mean -> p95` / `p50 -> p95`)**: Shaded bands above latency curves span to the 95th percentile tail latency (slowest 5% of pulses due to GC, OS scheduling, or UI refreshes).",
    ])

    load_code = _code_cell([
        "import json",
        "from pathlib import Path",
        "import pandas as pd",
        "import matplotlib.pyplot as plt",
        "",
        "def find_artifact(filename):",
        "    for base in (Path('.'), Path('benchmark'), Path('..') / 'benchmark'):",
        "        cand = base / filename",
        "        if cand.exists():",
        "            return cand",
        f"    raise FileNotFoundError(f'Could not locate {{filename}}')",
        "",
        f"json_path = find_artifact('{json_name}')",
        f"csv_path = find_artifact('{csv_name}')",
        "report = json.loads(json_path.read_text(encoding='utf-8'))",
        "df = pd.read_csv(csv_path)",
        "print(f'Loaded {{len(df)}} metric rows across {{df[\"scenario\"].nunique()}} scenarios from {{csv_path}}')",
        "df[['scenario', 'mode', 'metric', 'mean_us', 'p95_us', 'recv_ops_s', 'bandwidth_mb_s', 'dpg_fps_mean', 'rss_peak_mb']].head(12)",
    ])

    limits_code = _code_cell([
        "# PC Capacity & Bottleneck Summary",
        "comfort = report.get('comfort_and_limits', {})",
        "print('=== PC Capacity Diagnostic ===')",
        "print(f\"Max Chain Depth under 60 Hz (16.7 ms): D = {comfort.get('max_chain_depth_realtime')}\")",
        "print(f\"Max Chain Depth with GUI >= 25 FPS:    D = {comfort.get('max_chain_depth_ui_comfort')}\")",
        "print(f\"Max Fan-Out under 60 Hz (16.7 ms):     K = {comfort.get('max_fanout_k_realtime')}\")",
        "print(f\"Max Fan-Out with GUI >= 25 FPS:        K = {comfort.get('max_fanout_k_ui_comfort')}\")",
        "m_rt = comfort.get('max_mesh_realtime')",
        "m_brk = comfort.get('first_mesh_over_rt60')",
        "print(f\"Max Real-Time Mesh (<= 16.7 ms):       {m_rt['label'] if m_rt else 'None'}\")",
        "if m_brk:",
        "    print(f\"First Mesh Exceeding 60 Hz Budget:     {m_brk['label']} ({m_brk['mean_us']/1000:.2f} ms)\")",
        "opt_w = comfort.get('optimal_workers_1080p')",
        "if opt_w:",
        "    print(f\"Peak 1080p RGB Workers:                {opt_w['label']} -> {opt_w['bandwidth_mb_s']:.1f} MB/s ({opt_w['recv_ops_s']:.0f} FPS, RSS {opt_w['rss_peak_mb']:.0f} MB)\")",
    ])

    stress_plot_code = _code_cell([
        "# Stress-Limit Visualization: Latency (ms), Mean-p95 Jitter Shading & Viewport FPS",
        "series = report.get('comfort_and_limits', {}).get('series', {})",
        "fig, axes = plt.subplots(2, 2, figsize=(13, 8))",
        "specs = [",
        "    ('chain', 'Serial Chain Depth (D)', 'Chain Depth D'),",
        "    ('fanout', 'Parallel Fan-Out (K)', 'Fan-Out Branches K'),",
        "    ('mesh', 'Dense Matrix Saturation (L x W)', 'Total Mesh Nodes'),",
        "    ('workers', '1080p RGB Multiprocess Workers (P)', 'Workers P'),",
        "]",
        "for ax, (key, title, xlab) in zip(axes.flat, specs):",
        "    pts = series.get(key, [])",
        "    for mode, col in (('headless', '#0072B2'), ('gui_live_ui', '#D55E00')):",
        "        sub = [p for p in pts if p['mode'] == mode]",
        "        if not sub:",
        "            continue",
        "        xs = [p['scale_x'] for p in sub]",
        "        ys = [p['bandwidth_mb_s'] if key == 'workers' else p['mean_us'] / 1000.0 for p in sub]",
        "        ax.plot(xs, ys, marker='o', label=f'{mode} (BW MB/s)' if key == 'workers' else f'{mode} mean (ms)', color=col)",
        "        if key in ('chain', 'fanout'):",
        "            p95s = [p['p95_us'] / 1000.0 for p in sub]",
        "            ax.fill_between(xs, ys, p95s, color=col, alpha=0.14, label=f'{mode} Mean–p95 jitter')",
        "    gui_pts = [p for p in pts if p['mode'] != 'headless']",
        "    if gui_pts:",
        "        ax2 = ax.twinx()",
        "        ax2.plot([p['scale_x'] for p in gui_pts], [p['dpg_fps_mean'] for p in gui_pts], 'g--d', label='GUI FPS')",
        "        ax2.axhline(25.0, color='green', linestyle=':', alpha=0.6)",
        "        ax2.set_ylabel('Viewport FPS', color='green')",
        "    if key != 'workers':",
        "        ax.axhline(16.67, color='#E69F00', linestyle='--', alpha=0.7, label='60 Hz (16.7 ms)')",
        "        ax.axhline(33.33, color='#CC79A7', linestyle='--', alpha=0.7, label='30 Hz (33.3 ms)')",
        "    ax.set_title(title)",
        "    ax.set_xlabel(xlab)",
        "    ax.set_ylabel('Bandwidth (MB/s)' if key == 'workers' else 'Mean Latency (ms)')",
        "    ax.grid(alpha=0.25)",
        "    ax.legend(loc='upper left', fontsize=8)",
        "plt.tight_layout()",
        "plt.show()",
    ])

    custom_code = _code_cell([
        "# ShmPool vs Pure Pickle + Custom Scenario Inspector",
        "ipc_rows = df[df['scenario'].str.contains('proc_shm|proc_pickle|proc_dict_pickle', na=False) & (df['metric'] == 'proc_total_roundtrip_ns')]",
        "display_cols = ['scenario', 'mean_us', 'p95_us', 'recv_ops_s', 'bandwidth_mb_s', 'rss_peak_mb']",
        "print('=== ShmPool vs Pure Pickle IPC Comparison ===')",
        "print(ipc_rows[display_cols].to_string(index=False))",
        "",
        "# Tip: Set FILTER_SCENARIO = 'my_custom_pipeline' to inspect any user-recorded profiler session",
        "FILTER_SCENARIO = ''",
        "if FILTER_SCENARIO:",
        "    display_df = df[df['scenario'].str.contains(FILTER_SCENARIO, na=False)]",
        "    print(display_df[['scenario', 'mode', 'metric', 'mean_us', 'p95_us', 'recv_ops_s', 'dpg_fps_mean']])",
    ])

    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "version": env.get("python_version", "3.12"),
            },
        },
        "cells": [intro_md, load_code, limits_code, stress_plot_code, custom_code],
    }

    out_path = Path(out_ipynb_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(notebook, indent=2), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate interactive HTML & Jupyter Notebook benchmark reports.")
    parser.add_argument("--json", default=str(BENCHMARK_DIR / "benchmark_report.json"), help="Path to benchmark_report.json")
    parser.add_argument("--csv", default=str(BENCHMARK_DIR / "benchmark_report.csv"), help="Path to benchmark_report.csv")
    parser.add_argument("--out-html", default=str(BENCHMARK_DIR / "interactive_report.html"), help="Output HTML path")
    parser.add_argument("--out-ipynb", default=str(BENCHMARK_DIR / "benchmark_analysis.ipynb"), help="Output notebook path")
    args = parser.parse_args()

    html_p = generate_interactive_html(Path(args.json), Path(args.out_html))
    print(f"[Reports] Wrote interactive HTML: {html_p}")
    nb_p = generate_jupyter_notebook(Path(args.json), Path(args.csv), Path(args.out_ipynb))
    print(f"[Reports] Wrote Jupyter Notebook: {nb_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
