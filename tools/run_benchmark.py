"""
Automated Framework Benchmark & Stress-Test Suite Orchestrator.

Uses Pipeline Creator's native pipeline JSON serialization and `AutomationManager`
scripting system (`main.py --script ...`) to automatically construct pipelines of
varying topology, depth, fan-out, mesh density, routing primitives, and parallel
`ProcessingBase` workers, then executes:
  1. Comprehensive Headless Framework Scaling & IPC Breakdown Suite
  2. Paired Full-App Headless vs Full DearPyGui GUI Comparison Suite
  3. Progressive Stress-Test & Live-UI Comfort Suite

Produces reports in `benchmark/`:
  - `benchmark_report.json`
  - `benchmark_report.csv`
  - `benchmark_report.md`
  - `summary.md`
  - `interactive_report.html`
  - `benchmark_analysis.ipynb`
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.framework_profiler import FrameworkProfiler

BENCH_DIR = PROJECT_ROOT / "benchmark"
GEN_PIPELINES_DIR = BENCH_DIR / "generated" / "pipelines"
GEN_SCRIPTS_DIR = BENCH_DIR / "generated" / "scripts"


# ----------------------------------------------------------------------
# Pipeline JSON Topology Builders
# ----------------------------------------------------------------------
def _win_entry(
    module: str,
    class_name: str,
    uuid: str,
    label: str,
    pos: List[int],
    size: List[int],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    p = {"label": label}
    if params:
        p.update(params)
    return {
        "module": module,
        "class_name": class_name,
        "uuid": uuid,
        "pos": pos,
        "size": size,
        "visible": False,
        "params": p,
    }


def _source_win(uuid: str = "u_src") -> Dict[str, Any]:
    return _win_entry(
        module="modules.benchmark.perf_pulse_source_win",
        class_name="PerfPulseSource_win",
        uuid=uuid,
        label="BenchSource",
        pos=[40, 40],
        size=[320, 240],
    )


def _relay_win(uuid: str, label: str, x: int, y: int, **extra_params: Any) -> Dict[str, Any]:
    return _win_entry(
        module="modules.benchmark.perf_relay_win",
        class_name="PerfRelayNode_win",
        uuid=uuid,
        label=label,
        pos=[x, y],
        size=[260, 140],
        params=extra_params,
    )


def _worker_win(
    uuid: str,
    label: str,
    x: int,
    y: int,
    work_us: int = 0,
    raw_ndarray_submit: bool = True,
    buffer_size: int = 512,
    drop_policy: str = "block",
    use_shared_memory: bool = True,
) -> Dict[str, Any]:
    return _win_entry(
        module="modules.benchmark.perf_worker_win",
        class_name="PerfWorkerNode_win",
        uuid=uuid,
        label=label,
        pos=[x, y],
        size=[290, 170],
        params={
            "work_us": work_us,
            "raw_ndarray_submit": raw_ndarray_submit,
            "use_shared_memory": use_shared_memory,
            "buffer_size": buffer_size,
            "drop_policy": drop_policy,
        },
    )


def build_hop_1to1_pipeline() -> Dict[str, Any]:
    return {
        "windows": [_source_win("u_src"), _relay_win("u_snk", "RelaySink", 420, 40)],
        "connections": [{"from": "u_src", "output": "Out", "to": "u_snk"}],
        "is_relative": False,
    }


def build_chain_pipeline(depth: int) -> Dict[str, Any]:
    windows = [_source_win("u_src")]
    conns: List[Dict[str, str]] = []
    prev = "u_src"
    for i in range(depth):
        uid = f"u_r{i}"
        windows.append(_relay_win(uid, f"Chain_{i}", 380 + (i % 6) * 280, 40 + (i // 6) * 180))
        conns.append({"from": prev, "output": "Out", "to": uid})
        prev = uid
    return {"windows": windows, "connections": conns, "is_relative": False}


def build_fanout_pipeline(k: int) -> Dict[str, Any]:
    windows = [_source_win("u_src")]
    conns: List[Dict[str, str]] = []
    for i in range(k):
        uid = f"u_f{i}"
        windows.append(_relay_win(uid, f"Fan_{i}", 400 + (i % 8) * 270, 40 + (i // 8) * 160))
        conns.append({"from": "u_src", "output": "Out", "to": uid})
    return {"windows": windows, "connections": conns, "is_relative": False}


def build_dense_mesh_pipeline(layers: int, width: int) -> Dict[str, Any]:
    windows = [_source_win("u_src")]
    conns: List[Dict[str, str]] = []
    layer_uids: List[List[str]] = []
    for l_idx in range(layers):
        row: List[str] = []
        for w_idx in range(width):
            uid = f"u_m_{l_idx}_{w_idx}"
            windows.append(_relay_win(uid, f"M_{l_idx}_{w_idx}", 400 + l_idx * 290, 40 + w_idx * 160))
            row.append(uid)
        layer_uids.append(row)

    for uid in layer_uids[0]:
        conns.append({"from": "u_src", "output": "Out", "to": uid})
    for l_idx in range(layers - 1):
        for src_uid in layer_uids[l_idx]:
            for dst_uid in layer_uids[l_idx + 1]:
                conns.append({"from": src_uid, "output": "Out", "to": dst_uid})
    return {"windows": windows, "connections": conns, "is_relative": False}


def build_single_worker_pipeline(
    raw_ndarray_submit: bool = True,
    work_us: int = 0,
    use_shared_memory: bool = True,
) -> Dict[str, Any]:
    return {
        "windows": [
            _source_win("u_src"),
            _worker_win(
                "u_wrk",
                "Worker_0",
                400,
                40,
                work_us=work_us,
                raw_ndarray_submit=raw_ndarray_submit,
                use_shared_memory=use_shared_memory,
            ),
            _relay_win("u_snk", "RelaySink", 760, 40),
        ],
        "connections": [
            {"from": "u_src", "output": "Out", "to": "u_wrk"},
            {"from": "u_wrk", "output": "Out", "to": "u_snk"},
        ],
        "is_relative": False,
    }


def build_parallel_workers_pipeline(
    n_workers: int,
    work_us: int = 500,
    raw_ndarray_submit: bool = True,
    buffer_size: int = 8,
) -> Dict[str, Any]:
    windows = [_source_win("u_src")]
    conns: List[Dict[str, str]] = []
    for i in range(n_workers):
        w_uid = f"u_w{i}"
        s_uid = f"u_s{i}"
        y = 40 + i * 190
        windows.append(
            _worker_win(
                w_uid,
                f"Worker_{i}",
                400,
                y,
                work_us=work_us,
                raw_ndarray_submit=raw_ndarray_submit,
                buffer_size=buffer_size,
            )
        )
        windows.append(_relay_win(s_uid, f"Sink_{i}", 760, y))
        conns.append({"from": "u_src", "output": "Out", "to": w_uid})
        conns.append({"from": w_uid, "output": "Out", "to": s_uid})
    return {"windows": windows, "connections": conns, "is_relative": False}


def write_pipeline(name: str, data: Dict[str, Any]) -> Path:
    GEN_PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    path = (GEN_PIPELINES_DIR / f"{name}.json").resolve()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# AutomationManager Script Step Builders
# ----------------------------------------------------------------------
def _trig(method: str, params: Optional[Dict[str, Any]] = None, module_uuid: str = "u_src") -> Dict[str, Any]:
    step: Dict[str, Any] = {
        "action": "trigger",
        "module_uuid": module_uuid,
        "method": method,
    }
    if params is not None:
        step["params"] = params
    return step


def append_burst_scenario(
    steps: List[Dict[str, Any]],
    pipeline_path: Path,
    scenario: str,
    mode: str,
    count: int,
    profile: str = "token",
    warmup: int = 50,
    has_workers: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    reload_pipeline: bool = True,
    ui_update_hz: float = 0.0,
) -> None:
    if reload_pipeline:
        steps.append({"action": "load_pipeline", "path": str(pipeline_path)})
        if ui_update_hz != 0.0:
            steps.append(_trig("set_cluster_ui_update_hz", {"ui_update_hz": ui_update_hz, "show_windows": True}))
    if has_workers:
        steps.append(_trig("run_burst", {"count": max(2, min(10, warmup)), "profile": profile, "warmup": 0}))
        steps.append(_trig("wait_workers_idle", {"timeout_s": 25.0}))
        warmup_inside = 0
    else:
        warmup_inside = warmup

    steps.append(_trig("profiler_start", {"scenario": scenario, "mode": mode, "metadata": metadata or {}}))
    steps.append(_trig("run_burst", {"count": count, "profile": profile, "warmup": warmup_inside}))
    if has_workers:
        steps.append(_trig("wait_workers_idle", {"timeout_s": 30.0}))
    steps.append(_trig("profiler_stop"))


def append_pingpong_scenario(
    steps: List[Dict[str, Any]],
    pipeline_path: Path,
    scenario: str,
    mode: str,
    count: int,
    profile: str = "token",
    warmup: int = 5,
    metadata: Optional[Dict[str, Any]] = None,
    reload_pipeline: bool = True,
) -> None:
    if reload_pipeline:
        steps.append({"action": "load_pipeline", "path": str(pipeline_path)})
        steps.append(_trig("run_pingpong", {"count": 2, "profile": profile, "warmup": 0}))
    steps.append(_trig("profiler_start", {"scenario": scenario, "mode": mode, "metadata": metadata or {}}))
    steps.append(_trig("run_pingpong", {"count": count, "profile": profile, "warmup": warmup}))
    steps.append(_trig("profiler_stop"))


def append_stream_scenario(
    steps: List[Dict[str, Any]],
    pipeline_path: Path,
    scenario: str,
    mode: str,
    duration_s: float,
    rate_hz: float = 0.0,
    uncapped: bool = True,
    profile: str = "token",
    metadata: Optional[Dict[str, Any]] = None,
    reload_pipeline: bool = True,
    ui_update_hz: float = 0.0,
) -> None:
    if reload_pipeline:
        steps.append({"action": "load_pipeline", "path": str(pipeline_path)})
        if ui_update_hz != 0.0:
            steps.append(_trig("set_cluster_ui_update_hz", {"ui_update_hz": ui_update_hz, "show_windows": True}))
    steps.append(_trig("profiler_start", {"scenario": scenario, "mode": mode, "metadata": metadata or {}}))
    steps.append(
        _trig(
            "run_stream",
            {"duration_s": duration_s, "rate_hz": rate_hz, "profile": profile, "uncapped": uncapped},
        )
    )
    steps.append(_trig("profiler_stop"))


def generate_headless_matrix_script(output_prefix: Path) -> Path:
    """Build the comprehensive headless scaling + IPC breakdown AutomationManager script."""
    steps: List[Dict[str, Any]] = []

    p_hop = write_pipeline("hop_1to1", build_hop_1to1_pipeline())
    p_wrk_raw = write_pipeline("worker_1x_raw", build_single_worker_pipeline(raw_ndarray_submit=True, work_us=0, use_shared_memory=True))
    p_wrk_dict = write_pipeline("worker_1x_dict", build_single_worker_pipeline(raw_ndarray_submit=False, work_us=0, use_shared_memory=True))
    p_wrk_noshm = write_pipeline("worker_1x_noshm", build_single_worker_pipeline(raw_ndarray_submit=False, work_us=0, use_shared_memory=False))

    # 1. Calibration + Direct Hop + Routing Primitives
    steps.append({"action": "load_pipeline", "path": str(p_hop)})
    steps.append(_trig("calibrate_profiler_overhead", {"iterations": 10000}))

    append_burst_scenario(
        steps, p_hop, "hop_1to1_token", "headless", count=5000, profile="token", warmup=200,
        metadata={"nodes": 2, "edges": 1, "payload": "token"}, reload_pipeline=False,
    )
    append_burst_scenario(
        steps, p_hop, "hop_1to1_numpy_1kb", "headless", count=2000, profile="numpy_1kb", warmup=50,
        metadata={"nodes": 2, "edges": 1, "payload": "1KB"}, reload_pipeline=False,
    )
    append_burst_scenario(
        steps, p_hop, "hop_1to1_numpy_1mb", "headless", count=500, profile="numpy_1mb", warmup=20,
        metadata={"nodes": 2, "edges": 1, "payload": "1MB"}, reload_pipeline=False,
    )

    steps.append(_trig("profiler_start", {"scenario": "routing_gate", "mode": "headless", "metadata": {"routing": "Gate"}}))
    steps.append(_trig("run_routing_burst", {"routing_kind": "gate", "count": 3000, "warmup": 100}))
    steps.append(_trig("profiler_stop"))

    steps.append(_trig("profiler_start", {"scenario": "routing_linkbus", "mode": "headless", "metadata": {"routing": "LinkBus"}}))
    steps.append(_trig("run_routing_burst", {"routing_kind": "linkbus", "count": 3000, "warmup": 100}))
    steps.append(_trig("profiler_stop"))

    # 2. Pipeline Depth Scaling
    for d in (2, 5, 10, 20, 40):
        p_chain = write_pipeline(f"chain_d{d}", build_chain_pipeline(d))
        append_burst_scenario(
            steps, p_chain, f"chain_d{d}", "headless", count=2000, profile="token", warmup=100,
            metadata={"depth": d, "nodes": d + 1, "edges": d},
        )

    # 3. Fan-out Scaling
    for k in (1, 2, 4, 8, 16, 32, 64):
        p_fan = write_pipeline(f"fanout_k{k}", build_fanout_pipeline(k))
        append_burst_scenario(
            steps, p_fan, f"fanout_k{k}", "headless", count=1500, profile="token", warmup=50,
            metadata={"fanout": k, "nodes": k + 1, "edges": k},
        )

    # 4. Dense Mesh DAGs
    for layers, width, cnt in ((2, 4, 500), (4, 4, 80), (4, 8, 15)):
        p_mesh = write_pipeline(f"mesh_{layers}x{width}", build_dense_mesh_pipeline(layers, width))
        edges = width + (layers - 1) * width * width
        append_burst_scenario(
            steps, p_mesh, f"mesh_{layers}x{width}", "headless", count=cnt, profile="token", warmup=2,
            metadata={"layers": layers, "width": width, "nodes": 1 + layers * width, "edges": edges},
        )

    # 5a. Uncontended Ping-Pong ProcessingBase Entry/Exit Latency
    append_pingpong_scenario(
        steps, p_wrk_dict, "proc_pingpong_dict_token", "headless", count=150, profile="token", warmup=5,
        metadata={"submit_mode": "dict_pickle", "pattern": "pingpong", "payload": "token"}, reload_pipeline=True,
    )
    append_pingpong_scenario(
        steps, p_wrk_dict, "proc_pingpong_dict_1kb", "headless", count=150, profile="numpy_1kb", warmup=5,
        metadata={"submit_mode": "dict_pickle", "pattern": "pingpong", "payload": "numpy_1kb"}, reload_pipeline=False,
    )
    append_pingpong_scenario(
        steps, p_wrk_noshm, "proc_pingpong_pickle_1mb", "headless", count=60, profile="numpy_1mb", warmup=3,
        metadata={"submit_mode": "pure_pickle_noshm", "pattern": "pingpong", "payload": "numpy_1mb"}, reload_pipeline=True,
    )
    append_pingpong_scenario(
        steps, p_wrk_noshm, "proc_pingpong_pickle_4mb", "headless", count=30, profile="numpy_4mb", warmup=2,
        metadata={"submit_mode": "pure_pickle_noshm", "pattern": "pingpong", "payload": "numpy_4mb"}, reload_pipeline=False,
    )
    append_pingpong_scenario(
        steps, p_wrk_raw, "proc_pingpong_shm_64kb", "headless", count=100, profile="numpy_64kb", warmup=5,
        metadata={"submit_mode": "raw_ndarray_shm", "pattern": "pingpong", "payload": "numpy_64kb"}, reload_pipeline=True,
    )
    append_pingpong_scenario(
        steps, p_wrk_raw, "proc_pingpong_shm_256kb", "headless", count=100, profile="numpy_256kb", warmup=5,
        metadata={"submit_mode": "raw_ndarray_shm", "pattern": "pingpong", "payload": "numpy_256kb"}, reload_pipeline=False,
    )
    append_pingpong_scenario(
        steps, p_wrk_raw, "proc_pingpong_shm_1mb", "headless", count=60, profile="numpy_1mb", warmup=3,
        metadata={"submit_mode": "raw_ndarray_shm", "pattern": "pingpong", "payload": "numpy_1mb"}, reload_pipeline=False,
    )
    append_pingpong_scenario(
        steps, p_wrk_raw, "proc_pingpong_shm_4mb", "headless", count=30, profile="numpy_4mb", warmup=2,
        metadata={"submit_mode": "raw_ndarray_shm", "pattern": "pingpong", "payload": "numpy_4mb"}, reload_pipeline=False,
    )

    # 5b. ProcessingBase Saturated Burst Throughput Breakdown
    for prof, cnt in (("token", 400), ("numpy_1kb", 400)):
        append_burst_scenario(
            steps, p_wrk_dict, f"proc_dict_{prof}", "headless", count=cnt, profile=prof, warmup=10,
            has_workers=True, metadata={"submit_mode": "dict_pickle", "payload": prof},
            reload_pipeline=(prof == "token"),
        )

    for prof, cnt in (("numpy_64kb", 250), ("numpy_256kb", 200), ("numpy_1mb", 150), ("numpy_4mb", 60)):
        append_burst_scenario(
            steps, p_wrk_raw, f"proc_shm_{prof}", "headless", count=cnt, profile=prof, warmup=8,
            has_workers=True, metadata={"submit_mode": "raw_ndarray_shm", "payload": prof},
            reload_pipeline=(prof == "numpy_64kb"),
        )

    for prof, cnt in (("numpy_256kb", 200), ("numpy_1mb", 100), ("numpy_4mb", 60)):
        scen_name = "proc_dict_pickle_numpy_1mb" if prof == "numpy_1mb" else f"proc_pickle_{prof}"
        append_burst_scenario(
            steps, p_wrk_noshm, scen_name, "headless", count=cnt, profile=prof, warmup=5,
            has_workers=True, metadata={"submit_mode": "pure_pickle_noshm", "payload": prof},
            reload_pipeline=(prof == "numpy_256kb"),
        )

    # 6. Parallel ProcessingBase Worker Scaling (P = 1..16) on realistic 1080p RGB frames
    for p_cnt in (1, 2, 4, 8, 12, 16):
        p_par = write_pipeline(
            f"workers_p{p_cnt}",
            build_parallel_workers_pipeline(
                n_workers=p_cnt, work_us=2000, raw_ndarray_submit=True, buffer_size=8
            ),
        )
        append_burst_scenario(
            steps, p_par, f"workers_parallel_p{p_cnt}", "headless", count=30, profile="numpy_1080p_rgb", warmup=10,
            has_workers=True, metadata={"parallel_workers": p_cnt, "work_us": 2000, "buffer_size": 8, "payload": "1080p RGB (6.22 MB)"},
        )

    steps.append(_trig("profiler_export", {"output_prefix": str(output_prefix)}))
    steps.append(_trig("request_exit"))

    GEN_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    script_path = (GEN_SCRIPTS_DIR / "headless_matrix_script.json").resolve()
    script_path.write_text(json.dumps({"name": "Headless Benchmark Matrix", "steps": steps}, indent=2), encoding="utf-8")
    return script_path


def generate_paired_comparison_script(mode: str, output_prefix: Path, ui_update_hz: float = 0.0) -> Path:
    """Build identical paired AutomationManager scripts for Headless vs Passive GUI vs Live-UI GUI."""
    steps: List[Dict[str, Any]] = []

    p_hop = write_pipeline("hop_1to1", build_hop_1to1_pipeline())
    p_chain20 = write_pipeline("chain_d20", build_chain_pipeline(20))
    p_fan32 = write_pipeline("fanout_k32", build_fanout_pipeline(32))
    p_mesh4x8 = write_pipeline("mesh_4x8", build_dense_mesh_pipeline(4, 8))
    p_wrk_raw = write_pipeline("worker_1x_raw", build_single_worker_pipeline(raw_ndarray_submit=True, work_us=0))
    p_par4 = write_pipeline(
        "workers_p4",
        build_parallel_workers_pipeline(4, work_us=2000, raw_ndarray_submit=True, buffer_size=8),
    )

    append_burst_scenario(
        steps, p_hop, "cmp_hop_1to1_burst", mode, count=5000, profile="token", warmup=200,
        metadata={"nodes": 2, "edges": 1, "ui_update_hz": ui_update_hz}, ui_update_hz=ui_update_hz,
    )
    append_stream_scenario(
        steps, p_hop, "cmp_hop_1to1_stream_uncapped", mode, duration_s=1.2, uncapped=True, profile="token",
        metadata={"nodes": 2, "edges": 1, "stream": "uncapped", "ui_update_hz": ui_update_hz},
        reload_pipeline=False, ui_update_hz=ui_update_hz,
    )
    append_burst_scenario(
        steps, p_chain20, "cmp_chain_d20_burst", mode, count=2000, profile="token", warmup=100,
        metadata={"depth": 20, "nodes": 21, "edges": 20, "ui_update_hz": ui_update_hz}, ui_update_hz=ui_update_hz,
    )
    append_burst_scenario(
        steps, p_fan32, "cmp_fanout_k32_burst", mode, count=1500, profile="token", warmup=50,
        metadata={"fanout": 32, "nodes": 33, "edges": 32, "ui_update_hz": ui_update_hz}, ui_update_hz=ui_update_hz,
    )
    append_burst_scenario(
        steps, p_mesh4x8, "cmp_mesh_4x8_burst", mode, count=15, profile="token", warmup=2,
        metadata={"layers": 4, "width": 8, "nodes": 33, "edges": 200, "ui_update_hz": ui_update_hz},
        ui_update_hz=ui_update_hz,
    )
    append_burst_scenario(
        steps, p_wrk_raw, "cmp_proc_shm_1mb", mode, count=120, profile="numpy_1mb", warmup=6,
        has_workers=True, metadata={"submit_mode": "raw_ndarray_shm", "payload": "1MB", "ui_update_hz": ui_update_hz},
        ui_update_hz=ui_update_hz,
    )
    append_burst_scenario(
        steps, p_par4, "cmp_workers_p4_1080p", mode, count=30, profile="numpy_1080p_rgb", warmup=10,
        has_workers=True, metadata={"parallel_workers": 4, "work_us": 2000, "buffer_size": 8, "payload": "1080p RGB (6.22 MB)", "ui_update_hz": ui_update_hz},
        ui_update_hz=ui_update_hz,
    )

    steps.append(_trig("profiler_export", {"output_prefix": str(output_prefix)}))
    steps.append(_trig("request_exit"))

    GEN_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    script_path = (GEN_SCRIPTS_DIR / f"compare_{mode}_script.json").resolve()
    script_path.write_text(json.dumps({"name": f"Paired Comparison ({mode})", "steps": steps}, indent=2), encoding="utf-8")
    return script_path


def generate_stress_test_script(mode: str, output_prefix: Path, ui_update_hz: float = 0.0) -> Path:
    """Build progressive stress-test scripts sweeping chains, fan-outs, meshes, and 1080p workers."""
    steps: List[Dict[str, Any]] = []

    # 1. Serial chain depth stress (D = 2 .. 160)
    for d in (2, 10, 20, 40, 80, 120, 160):
        cnt = 600 if d <= 40 else (300 if d <= 80 else 200)
        p_chain = write_pipeline(f"stress_chain_d{d}", build_chain_pipeline(d))
        append_burst_scenario(
            steps,
            p_chain,
            f"stress_chain_d{d}",
            mode,
            count=cnt,
            profile="token",
            warmup=30,
            metadata={"stress_axis": "chain", "depth": d, "nodes": d + 1, "edges": d, "ui_update_hz": ui_update_hz},
            ui_update_hz=ui_update_hz,
        )

    # 2. Parallel fan-out stress (K = 1 .. 192)
    for k in (1, 4, 16, 32, 64, 128, 192):
        cnt = 500 if k <= 32 else (250 if k <= 64 else 120)
        p_fan = write_pipeline(f"stress_fanout_k{k}", build_fanout_pipeline(k))
        append_burst_scenario(
            steps,
            p_fan,
            f"stress_fanout_k{k}",
            mode,
            count=cnt,
            profile="token",
            warmup=20,
            metadata={"stress_axis": "fanout", "fanout": k, "nodes": k + 1, "edges": k, "ui_update_hz": ui_update_hz},
            ui_update_hz=ui_update_hz,
        )

    # 3. Dense mesh stress (combinatorial edge growth from 2x4 up to 5x8)
    for layers, width, cnt in ((2, 4, 200), (3, 4, 80), (4, 4, 35), (4, 6, 12), (4, 8, 6), (5, 6, 4), (5, 8, 3)):
        deliveries = width ** layers
        edges = width + (layers - 1) * width * width
        p_mesh = write_pipeline(f"stress_mesh_{layers}x{width}", build_dense_mesh_pipeline(layers, width))
        append_burst_scenario(
            steps,
            p_mesh,
            f"stress_mesh_{layers}x{width}",
            mode,
            count=cnt,
            profile="token",
            warmup=1 if layers >= 5 else 2,
            metadata={
                "stress_axis": "mesh",
                "layers": layers,
                "width": width,
                "nodes": 1 + layers * width,
                "edges": edges,
                "deliveries_per_pulse": deliveries,
                "ui_update_hz": ui_update_hz,
            },
            ui_update_hz=ui_update_hz,
        )

    # 4. Parallel ProcessingBase workers on realistic 1080p RGB frames (6.22 MB/frame)
    worker_counts = (1, 2, 4, 8, 12, 16, 20) if mode == "headless" else (1, 2, 4, 8, 12, 16)
    for p_cnt in worker_counts:
        p_par = write_pipeline(
            f"stress_workers_p{p_cnt}",
            build_parallel_workers_pipeline(
                n_workers=p_cnt,
                work_us=2000,
                raw_ndarray_submit=True,
                buffer_size=6,
            ),
        )
        append_burst_scenario(
            steps,
            p_par,
            f"stress_workers_p{p_cnt}",
            mode,
            count=20,
            profile="numpy_1080p_rgb",
            warmup=6,
            has_workers=True,
            metadata={
                "stress_axis": "workers",
                "parallel_workers": p_cnt,
                "work_us": 2000,
                "buffer_size": 6,
                "payload": "1080p RGB (6.22 MB)",
                "ui_update_hz": ui_update_hz,
            },
            ui_update_hz=ui_update_hz,
        )

    steps.append(_trig("profiler_export", {"output_prefix": str(output_prefix)}))
    steps.append(_trig("request_exit"))

    GEN_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    script_path = (GEN_SCRIPTS_DIR / f"stress_{mode}_script.json").resolve()
    script_path.write_text(json.dumps({"name": f"Progressive Stress Suite ({mode})", "steps": steps}, indent=2), encoding="utf-8")
    return script_path


# ----------------------------------------------------------------------
# Runner & Report Synthesizer
# ----------------------------------------------------------------------
def _resolve_exe_path(raw_exe: Optional[str]) -> Optional[Path]:
    """Resolve and validate an optional compiled executable path (or its dist directory)."""
    if not raw_exe:
        return None
    candidate = Path(raw_exe).expanduser().resolve()
    if candidate.is_dir():
        exe_in_dir = candidate / "PipelineCreator.exe"
        if exe_in_dir.is_file():
            return exe_in_dir
        raise FileNotFoundError(f"No PipelineCreator.exe found inside directory: {candidate}")
    if not candidate.is_file():
        raise FileNotFoundError(f"Executable not found: {candidate}")
    return candidate


def _run_app(
    args_list: List[str],
    label: str,
    timeout_s: float = 240.0,
    exe_path: Optional[Path] = None,
) -> Tuple[int, float]:
    if exe_path is not None:
        cmd = [str(exe_path), *args_list]
        run_cwd = str(exe_path.parent)
    else:
        cmd = [sys.executable, str(PROJECT_ROOT / "main.py"), *args_list]
        run_cwd = str(PROJECT_ROOT)
    print(f"[Benchmark] Launching {label}: {' '.join(cmd)}", flush=True)
    env = os.environ.copy()
    env["PC_BENCHMARK_ORCHESTRATOR"] = "1"
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=run_cwd, env=env, timeout=timeout_s)
    dt = time.perf_counter() - t0
    print(f"[Benchmark] Finished {label} in {dt:.2f}s (exit={proc.returncode})", flush=True)
    return proc.returncode, dt


def _primary_metric(sc: Dict[str, Any]) -> Dict[str, Any]:
    m = sc.get("metrics", {})
    return (
        m.get("e2e_dag_ns")
        or m.get("proc_total_roundtrip_ns")
        or m.get("link_hop_ns")
        or {}
    )


def _append_comparison_section(
    extra_sections: List[str],
    title: str,
    hl_cmp: List[Dict[str, Any]],
    other_cmp: List[Dict[str, Any]],
) -> None:
    if not hl_cmp or not other_cmp:
        return
    by_name = {sc["scenario"]: sc for sc in other_cmp}
    extra_sections.extend(
        [
            "",
            f"## {title}",
            "",
            "| Scenario | Headless Ops/s | GUI Ops/s | Throughput Impact | Headless Mean / p95 (us) | GUI Mean / p95 (us) | Latency Overhead | GUI FPS | Headless -> GUI Peak RSS (MB) |",
            "|---|---:|---:|---:|---|---|---:|---:|---|",
        ]
    )
    for h in hl_cmp:
        name = h["scenario"]
        g = by_name.get(name)
        if not g:
            continue
        h_ops = h.get("throughput", {}).get("recv_ops_s", 0.0)
        g_ops = g.get("throughput", {}).get("recv_ops_s", 0.0)
        tp_delta = ((g_ops - h_ops) / h_ops * 100.0) if h_ops > 0 else 0.0
        hm = _primary_metric(h)
        gm = _primary_metric(g)
        h_mean, h_p95 = hm.get("mean_us", 0.0), hm.get("p95_us", 0.0)
        g_mean, g_p95 = gm.get("mean_us", 0.0), gm.get("p95_us", 0.0)
        lat_delta = ((g_mean - h_mean) / h_mean * 100.0) if h_mean > 0 else 0.0
        fps = g.get("system", {}).get("dpg_fps_mean", 0.0)
        h_rss = h.get("system", {}).get("rss_peak_mb", 0.0)
        g_rss = g.get("system", {}).get("rss_peak_mb", 0.0)
        extra_sections.append(
            f"| {name} | {h_ops:.1f} | {g_ops:.1f} | {tp_delta:+.1f}% | "
            f"{h_mean:.2f} / {h_p95:.2f} | {g_mean:.2f} / {g_p95:.2f} | "
            f"{lat_delta:+.1f}% | {fps:.1f} | {h_rss:.1f} -> {g_rss:.1f} |"
        )


def _append_stress_section(
    extra_sections: List[str],
    stress_hl: List[Dict[str, Any]],
    stress_live: List[Dict[str, Any]],
) -> None:
    if not stress_hl and not stress_live:
        return
    live_by_name = {sc["scenario"]: sc for sc in stress_live}
    hl_by_name = {sc["scenario"]: sc for sc in stress_hl}
    ordered_names = [sc["scenario"] for sc in stress_hl]
    for sc in stress_live:
        if sc["scenario"] not in hl_by_name:
            ordered_names.append(sc["scenario"])

    extra_sections.extend(
        [
            "",
            "## Progressive Stress-Test & UI Comfort (Breaking Point Analysis)",
            "",
            "| Stress Scenario | Headless Mean / p95 (ms) | Live-UI Mean / p95 (ms) | Live FPS | UI Comfort | Headless BW (MB/s) | Peak RSS HL -> GUI (MB) |",
            "|---|---|---|---:|---|---:|---|",
        ]
    )
    for name in ordered_names:
        h = hl_by_name.get(name)
        g = live_by_name.get(name)
        hm = _primary_metric(h) if h else {}
        gm = _primary_metric(g) if g else {}
        h_str = f"{hm.get('mean_us', 0.0) / 1000.0:.3f} / {hm.get('p95_us', 0.0) / 1000.0:.3f}" if h else "n/a"
        g_str = f"{gm.get('mean_us', 0.0) / 1000.0:.3f} / {gm.get('p95_us', 0.0) / 1000.0:.3f}" if g else "n/a"
        fps = g.get("system", {}).get("dpg_fps_mean", 0.0) if g else 0.0
        fps_str = f"{fps:.1f}" if g else "n/a"
        comfort = FrameworkProfiler.classify_fps_comfort(fps) if g else "Headless"
        bw = (h or g or {}).get("throughput", {}).get("bandwidth_mb_s", 0.0)
        h_rss = h.get("system", {}).get("rss_peak_mb", 0.0) if h else 0.0
        g_rss = g.get("system", {}).get("rss_peak_mb", 0.0) if g else 0.0
        extra_sections.append(
            f"| `{name}` | {h_str} | {g_str} | {fps_str} | {comfort} | {bw:.1f} | {h_rss:.1f} -> {g_rss:.1f} |"
        )


def build_combined_report(
    matrix_json: Optional[Path],
    cmp_headless_json: Optional[Path],
    cmp_gui_json: Optional[Path],
    boot_times: Dict[str, float],
    out_prefix: Path,
    cmp_gui_live_json: Optional[Path] = None,
    stress_headless_json: Optional[Path] = None,
    stress_gui_live_json: Optional[Path] = None,
) -> Path:
    profiler = FrameworkProfiler()
    env_info: Optional[Dict[str, Any]] = None

    def _load_scenarios(p: Optional[Path]) -> List[Dict[str, Any]]:
        nonlocal env_info
        if p is None or not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        if env_info is None and isinstance(data.get("environment"), dict):
            env_info = data["environment"]
        return list(data.get("scenarios", []))

    matrix_scenarios = _load_scenarios(matrix_json)
    hl_cmp = _load_scenarios(cmp_headless_json)
    gui_cmp = _load_scenarios(cmp_gui_json)
    gui_live_cmp = _load_scenarios(cmp_gui_live_json)
    stress_hl = _load_scenarios(stress_headless_json)
    stress_live = _load_scenarios(stress_gui_live_json)

    for sc in matrix_scenarios + hl_cmp + gui_cmp + gui_live_cmp + stress_hl + stress_live:
        profiler.add_external_result(sc)

    json_path = profiler.export_json(f"{out_prefix}.json", extra_info=env_info)
    csv_path = profiler.export_csv(f"{out_prefix}.csv")

    generated_figs: List[Path] = []
    try:
        from tools.plot_benchmark_figures import generate_figures_from_csv

        generated_figs = generate_figures_from_csv(
            csv_path,
            out_dir=csv_path.parent / "figures",
            formats=("png", "pdf"),
        )
    except Exception as exc:
        print(f"[Benchmark] WARNING: figure generation failed: {exc}")

    interactive_html_path: Optional[Path] = None
    notebook_path: Optional[Path] = None
    try:
        from tools.generate_user_reports import generate_interactive_html, generate_jupyter_notebook

        out_dir = Path(out_prefix).parent
        interactive_html_path = generate_interactive_html(json_path, out_dir / "interactive_report.html")
        notebook_path = generate_jupyter_notebook(json_path, csv_path, out_dir / "benchmark_analysis.ipynb")
    except Exception as exc:
        print(f"[Benchmark] WARNING: user report generation failed: {exc}")

    # Build enriched Markdown report with Paired Headless vs GUI Delta table
    md_path = Path(f"{out_prefix}.md")
    profiler.export_markdown(md_path, title="Pipeline Creator — Performance, Overhead & Stress-Test Report")
    base_md = md_path.read_text(encoding="utf-8")

    extra_sections: List[str] = ["", "## Run Wall-Clock Timings", ""]
    for k, v in boot_times.items():
        extra_sections.append(f"- `{k}` total wall time: `{v:.2f} s`")

    _append_comparison_section(
        extra_sections,
        "Headless vs Full GUI (Passive Modules) Overhead Impact",
        hl_cmp,
        gui_cmp,
    )
    _append_comparison_section(
        extra_sections,
        "Headless vs Full GUI + Live UI Updates (30 Hz) Overhead Impact",
        hl_cmp,
        gui_live_cmp,
    )
    _append_stress_section(extra_sections, stress_hl, stress_live)

    md_path.write_text(base_md + "\n" + "\n".join(extra_sections) + "\n", encoding="utf-8")

    # Also write a concise, 1-page summary report (`summary.md`) with plain takeaways
    summary_path = md_path.parent / "summary.md"
    simple_lines: List[str] = [
        "# Pipeline Creator — Performance Executive Summary",
        "",
        "*(Global synthesis automatically generated from the detailed measurements in `benchmark_report.md`.)*",
        "",
        f"- **Python / OS**: `{env_info.get('python_version') if env_info else 'n/a'}` on `{env_info.get('platform') if env_info else 'n/a'}`",
        f"- **CPU / RAM**: `{env_info.get('cpu_count_logical') if env_info else 'n/a'}` logical cores / `{env_info.get('ram_total_gb') if env_info else 'n/a'} GB RAM`",
        "",
    ]
    simple_lines.extend(FrameworkProfiler._build_simple_summary_lines(profiler.get_completed()))

    if hl_cmp and (gui_cmp or gui_live_cmp):
        gui_by_name = {sc["scenario"]: sc for sc in gui_cmp}
        live_by_name = {sc["scenario"]: sc for sc in gui_live_cmp}
        if gui_live_cmp:
            simple_lines.extend(
                [
                    "",
                    "## GUI Impact (Headless vs Passive GUI vs Realistic Live-UI @ 30 Hz)",
                    "",
                    "| Benchmark | Headless | Passive GUI (Delta) | Live UI 30 Hz (Delta) |",
                    "|---|---:|---:|---:|",
                ]
            )
            for h in hl_cmp:
                name = h["scenario"]
                h_ops = h.get("throughput", {}).get("recv_ops_s", 0.0)
                g = gui_by_name.get(name)
                gl = live_by_name.get(name)
                if g:
                    g_ops = g.get("throughput", {}).get("recv_ops_s", 0.0)
                    g_d = ((g_ops - h_ops) / h_ops * 100.0) if h_ops > 0 else 0.0
                    g_col = f"{g_ops:,.0f} ops/s ({g_d:+.1f}%)"
                else:
                    g_col = "n/a"
                if gl:
                    gl_ops = gl.get("throughput", {}).get("recv_ops_s", 0.0)
                    gl_d = ((gl_ops - h_ops) / h_ops * 100.0) if h_ops > 0 else 0.0
                    gl_col = f"{gl_ops:,.0f} ops/s (**{gl_d:+.1f}%**)"
                else:
                    gl_col = "n/a"
                simple_lines.append(f"| `{name}` | {h_ops:,.0f} ops/s | {g_col} | {gl_col} |")
        else:
            simple_lines.extend(
                [
                    "",
                    "## GUI Impact (Headless vs Full GUI)",
                    "",
                    "| Benchmark | Without UI (Headless) | With UI (Full GUI) | Throughput Delta |",
                    "|---|---:|---:|---:|",
                ]
            )
            for h in hl_cmp:
                name = h["scenario"]
                g = gui_by_name.get(name)
                if not g:
                    continue
                h_ops = h.get("throughput", {}).get("recv_ops_s", 0.0)
                g_ops = g.get("throughput", {}).get("recv_ops_s", 0.0)
                delta = ((g_ops - h_ops) / h_ops * 100.0) if h_ops > 0 else 0.0
                simple_lines.append(
                    f"| `{name}` | {h_ops:,.0f} ops/s | {g_ops:,.0f} ops/s | **{delta:+.1f}%** |"
                )

    if interactive_html_path or notebook_path:
        simple_lines.extend(["", "## Interactive & User-Friendly Reports", ""])
        if interactive_html_path:
            simple_lines.append(
                f"- Interactive Plotly Dashboard → [`{interactive_html_path.name}`]({interactive_html_path.name})"
            )
        if notebook_path:
            simple_lines.append(
                f"- Jupyter Analysis Notebook → [`{notebook_path.name}`]({notebook_path.name})"
            )

    if generated_figs:
        simple_lines.extend(["", "## Generated Figures (`benchmark/figures/`)", ""])
        for fig_path in generated_figs:
            simple_lines.append(f"- `{fig_path.name}` → [`figures/{fig_path.name}`](figures/{fig_path.name})")

    summary_path.write_text("\n".join(simple_lines) + "\n", encoding="utf-8")
    return md_path


def regenerate_all_benchmark_assets() -> Dict[str, Path]:
    """Regenerate all benchmark pipeline layouts and AutomationManager scripts under `benchmark/generated/`."""
    matrix_prefix = (BENCH_DIR / "matrix_headless").resolve()
    cmp_hl_prefix = (BENCH_DIR / "compare_headless").resolve()
    cmp_gui_prefix = (BENCH_DIR / "compare_gui").resolve()
    cmp_gui_live_prefix = (BENCH_DIR / "compare_gui_live").resolve()
    stress_hl_prefix = (BENCH_DIR / "stress_headless").resolve()
    stress_live_prefix = (BENCH_DIR / "stress_gui_live").resolve()

    scripts = {
        "matrix_headless": generate_headless_matrix_script(matrix_prefix),
        "compare_headless": generate_paired_comparison_script("headless", cmp_hl_prefix),
        "compare_gui": generate_paired_comparison_script("gui", cmp_gui_prefix),
        "compare_gui_live": generate_paired_comparison_script("gui_live_ui", cmp_gui_live_prefix, ui_update_hz=30.0),
        "stress_headless": generate_stress_test_script("headless", stress_hl_prefix),
        "stress_gui_live": generate_stress_test_script("gui_live_ui", stress_live_prefix, ui_update_hz=30.0),
    }
    print(f"[Benchmark] Regenerated {len(scripts)} script(s) & pipeline suites in {BENCH_DIR / 'generated'}")
    return scripts


def sync_all_reports_from_disk(fallback_prefix: Optional[Path] = None) -> Optional[Path]:
    """
    Rebuild combined benchmark reports, figures (PNG/PDF), interactive HTML dashboard,
    and Jupyter notebook from benchmark JSON/CSV files present on disk.
    """
    matrix_json = (BENCH_DIR / "matrix_headless.json").resolve()
    cmp_hl_json = (BENCH_DIR / "compare_headless.json").resolve()
    cmp_gui_json = (BENCH_DIR / "compare_gui.json").resolve()
    cmp_gui_live_json = (BENCH_DIR / "compare_gui_live.json").resolve()
    stress_hl_json = (BENCH_DIR / "stress_headless.json").resolve()
    stress_live_json = (BENCH_DIR / "stress_gui_live.json").resolve()
    out_prefix = (BENCH_DIR / "benchmark_report").resolve()

    std_prefixes = {
        "matrix_headless",
        "compare_headless",
        "compare_gui",
        "compare_gui_live",
        "stress_headless",
        "stress_gui_live",
    }

    if fallback_prefix is not None:
        fp = Path(fallback_prefix).resolve()
        fp_json = Path(f"{fp}.json")
        fp_csv = Path(f"{fp}.csv")
        if fp.stem not in std_prefixes and fp_json.exists() and fp_csv.exists():
            from tools.generate_user_reports import generate_interactive_html, generate_jupyter_notebook
            from tools.plot_benchmark_figures import generate_figures_from_csv

            fig_dir = fp.parent / f"{fp.stem}_figures"
            generate_figures_from_csv(fp_csv, out_dir=fig_dir, formats=("png", "pdf"))
            generate_interactive_html(fp_json, fp.parent / f"{fp.stem}_interactive_report.html")
            generate_jupyter_notebook(fp_json, fp_csv, fp.parent / f"{fp.stem}_analysis.ipynb")

    any_std = any(
        p.exists()
        for p in (matrix_json, cmp_hl_json, cmp_gui_json, cmp_gui_live_json, stress_hl_json, stress_live_json)
    )
    if not any_std:
        return None

    return build_combined_report(
        matrix_json=matrix_json if matrix_json.exists() else None,
        cmp_headless_json=cmp_hl_json if cmp_hl_json.exists() else None,
        cmp_gui_json=cmp_gui_json if cmp_gui_json.exists() else None,
        boot_times={},
        out_prefix=out_prefix,
        cmp_gui_live_json=cmp_gui_live_json if cmp_gui_live_json.exists() else None,
        stress_headless_json=stress_hl_json if stress_hl_json.exists() else None,
        stress_gui_live_json=stress_live_json if stress_live_json.exists() else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Pipeline Creator end-to-end benchmark & stress-test suite (regenerates pipelines/scripts, runs benchmarks, builds PNG/PDF figures & HTML/Notebook reports)."
    )
    parser.add_argument("--skip-matrix", action="store_true", help="Skip the 25-scenario headless scaling matrix.")
    parser.add_argument("--skip-gui", action="store_true", help="Skip the Passive Full GUI comparison run.")
    parser.add_argument("--skip-gui-live", action="store_true", help="Skip the Realistic Live-UI (30 Hz) GUI comparison run.")
    parser.add_argument("--skip-headless-cmp", action="store_true", help="Skip the Headless comparison run.")
    parser.add_argument("--skip-stress", action="store_true", help="Skip the progressive stress-test sweeps.")
    parser.add_argument(
        "--stress-only",
        action="store_true",
        help="Run only the progressive stress sweeps (headless + GUI live-UI) and rebuild combined reports.",
    )
    parser.add_argument(
        "--rebuild-only",
        action="store_true",
        help="Regenerate benchmark/generated/ assets, summary.md, benchmark_report.*, HTML/notebook reports, and benchmark/figures/ from existing JSON/CSV results without executing benchmarks.",
    )
    parser.add_argument(
        "--exe",
        type=str,
        default=None,
        help="Path to a compiled PipelineCreator.exe (or its main.dist folder) to benchmark instead of running main.py from source.",
    )
    args = parser.parse_args()

    try:
        exe_path = _resolve_exe_path(args.exe)
    except FileNotFoundError as exc:
        parser.error(str(exc))

    if exe_path is not None:
        print(f"[Benchmark] Target executable: {exe_path}")

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    boot_times: Dict[str, float] = {}

    matrix_prefix = (BENCH_DIR / "matrix_headless").resolve()
    cmp_hl_prefix = (BENCH_DIR / "compare_headless").resolve()
    cmp_gui_prefix = (BENCH_DIR / "compare_gui").resolve()
    cmp_gui_live_prefix = (BENCH_DIR / "compare_gui_live").resolve()
    stress_hl_prefix = (BENCH_DIR / "stress_headless").resolve()
    stress_live_prefix = (BENCH_DIR / "stress_gui_live").resolve()
    out_prefix = (BENCH_DIR / "benchmark_report").resolve()

    scripts = regenerate_all_benchmark_assets()

    if not args.rebuild_only and not args.stress_only and not args.skip_matrix:
        s_matrix = scripts["matrix_headless"]
        rc, dt = _run_app(
            ["--headless", "--benchmark-modules-only", "--max-seconds", "180", "--script", str(s_matrix)],
            "Headless Matrix",
            exe_path=exe_path,
        )
        boot_times["headless_matrix"] = dt
        if rc != 0:
            print(f"[Benchmark] WARNING: Headless Matrix exited with {rc}")

    if not args.rebuild_only and not args.stress_only and not args.skip_headless_cmp:
        s_cmp_hl = scripts["compare_headless"]
        rc, dt = _run_app(
            ["--headless", "--max-seconds", "180", "--script", str(s_cmp_hl)],
            "Paired Comparison (Headless Full Boot)",
            exe_path=exe_path,
        )
        boot_times["paired_headless_full_boot"] = dt
        if rc != 0:
            print(f"[Benchmark] WARNING: Paired Headless exited with {rc}")

    if not args.rebuild_only and not args.stress_only and not args.skip_gui:
        s_cmp_gui = scripts["compare_gui"]
        rc, dt = _run_app(
            ["--max-seconds", "180", "--script", str(s_cmp_gui)],
            "Paired Comparison (Full DearPyGui GUI - Passive)",
            exe_path=exe_path,
        )
        boot_times["paired_gui_full_boot"] = dt
        if rc != 0:
            print(f"[Benchmark] WARNING: Paired GUI exited with {rc}")

    if not args.rebuild_only and not args.stress_only and not args.skip_gui_live:
        s_cmp_live = scripts["compare_gui_live"]
        rc, dt = _run_app(
            ["--max-seconds", "180", "--script", str(s_cmp_live)],
            "Paired Comparison (Full DearPyGui GUI - Live UI 30Hz)",
            exe_path=exe_path,
        )
        boot_times["paired_gui_live_ui_full_boot"] = dt
        if rc != 0:
            print(f"[Benchmark] WARNING: Paired GUI Live-UI exited with {rc}")

    if not args.rebuild_only and not args.skip_stress:
        s_stress_hl = scripts["stress_headless"]
        rc, dt = _run_app(
            ["--headless", "--benchmark-modules-only", "--max-seconds", "240", "--script", str(s_stress_hl)],
            "Progressive Stress Suite (Headless)",
            timeout_s=300.0,
            exe_path=exe_path,
        )
        boot_times["stress_headless"] = dt
        if rc != 0:
            print(f"[Benchmark] WARNING: Progressive Stress (Headless) exited with {rc}")

        if not args.skip_gui_live:
            s_stress_live = scripts["stress_gui_live"]
            rc, dt = _run_app(
                ["--max-seconds", "240", "--script", str(s_stress_live)],
                "Progressive Stress Suite (Full DearPyGui GUI - Live UI 30Hz)",
                timeout_s=300.0,
                exe_path=exe_path,
            )
            boot_times["stress_gui_live_ui"] = dt
            if rc != 0:
                print(f"[Benchmark] WARNING: Progressive Stress (Live UI) exited with {rc}")

    md_path = build_combined_report(
        matrix_json=Path(f"{matrix_prefix}.json") if Path(f"{matrix_prefix}.json").exists() else None,
        cmp_headless_json=Path(f"{cmp_hl_prefix}.json") if Path(f"{cmp_hl_prefix}.json").exists() else None,
        cmp_gui_json=Path(f"{cmp_gui_prefix}.json") if Path(f"{cmp_gui_prefix}.json").exists() else None,
        boot_times=boot_times,
        out_prefix=out_prefix,
        cmp_gui_live_json=Path(f"{cmp_gui_live_prefix}.json") if Path(f"{cmp_gui_live_prefix}.json").exists() else None,
        stress_headless_json=Path(f"{stress_hl_prefix}.json") if Path(f"{stress_hl_prefix}.json").exists() else None,
        stress_gui_live_json=Path(f"{stress_live_prefix}.json") if Path(f"{stress_live_prefix}.json").exists() else None,
    )
    print(f"[Benchmark] Full report written to: {md_path}")
    print(f"[Benchmark] Executive summary:     {BENCH_DIR / 'summary.md'}")
    print(f"[Benchmark] Interactive dashboard: {BENCH_DIR / 'interactive_report.html'}")
    print(f"[Benchmark] Jupyter notebook:      {BENCH_DIR / 'benchmark_analysis.ipynb'}")
    print(f"[Benchmark] Figures (PNG & PDF):   {BENCH_DIR / 'figures'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
