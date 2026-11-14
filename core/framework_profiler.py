"""
Lightweight Global Framework Performance Profiler (Benchmark & Stress Suite).

Provides low-overhead nanosecond instrumentation (time.perf_counter_ns) across:
  - Module-to-module link dispatch latency (hop transfer & broadcast fan-out)
  - End-to-end DAG propagation latency (source origin -> terminal sink)
  - ProcessingBase multiprocessing IPC breakdown (main encode, IPC in, worker decode,
    compute, worker encode, IPC out, total roundtrip & pure IPC overhead)
  - LinkBus (LinkOut -> LinkIn) and Gate routing overhead
  - Process/child RSS memory, CPU utilization, and GUI frame rate telemetry
  - Exact percentile aggregation (min, mean, std, p50, p95, p99, max) & JSON/CSV/MD export
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import psutil
except ImportError:
    psutil = None


def _percentile_sorted(sorted_vals: List[float], p: float) -> float:
    """Compute linear-interpolated percentile p in [0, 100] from a sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    pos = (p / 100.0) * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


def summarize_samples_ns(samples_ns: List[int]) -> Dict[str, Any]:
    """Compute exact distribution statistics (in microseconds) from nanosecond samples."""
    n = len(samples_ns)
    if n == 0:
        return {
            "count": 0,
            "min_us": 0.0,
            "mean_us": 0.0,
            "std_us": 0.0,
            "p50_us": 0.0,
            "p95_us": 0.0,
            "p99_us": 0.0,
            "max_us": 0.0,
        }

    # Convert to sorted microseconds once
    vals_us = sorted(v * 1e-3 for v in samples_ns)
    total = sum(vals_us)
    mean = total / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in vals_us) / (n - 1)
        std = math.sqrt(var)
    else:
        std = 0.0

    return {
        "count": n,
        "min_us": round(vals_us[0], 3),
        "mean_us": round(mean, 3),
        "std_us": round(std, 3),
        "p50_us": round(_percentile_sorted(vals_us, 50.0), 3),
        "p95_us": round(_percentile_sorted(vals_us, 95.0), 3),
        "p99_us": round(_percentile_sorted(vals_us, 99.0), 3),
        "max_us": round(vals_us[-1], 3),
    }


@dataclass
class _ScenarioState:
    name: str
    mode: str = "headless"
    metadata: Dict[str, Any] = field(default_factory=dict)
    start_ns: int = 0
    end_ns: int = 0
    max_samples_per_metric: int = 200_000
    metrics_ns: Dict[str, List[int]] = field(default_factory=lambda: defaultdict(list))
    counters: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    sys_samples: List[Dict[str, float]] = field(default_factory=list)
    rendered_frames: int = 0
    last_frame_pump_ns: int = 0
    excluded_duration_ns: int = 0
    frame_fps_samples: List[float] = field(default_factory=list)


class FrameworkProfiler:
    """
    Thread-safe global micro-profiler using monotonic perf_counter_ns timestamps.

    When `enabled` is False, instrumentation checks resolve in a single attribute lookup
    so non-benchmark runs incur virtually zero runtime cost.
    """

    def __init__(self) -> None:
        self.enabled: bool = False
        self._lock = threading.Lock()
        self._current: Optional[_ScenarioState] = None
        self._completed: List[Dict[str, Any]] = []
        self._sampler_thread: Optional[threading.Thread] = None
        self._sampler_stop = threading.Event()
        self._proc = psutil.Process(os.getpid()) if psutil is not None else None
        self._stage_key_cache: Dict[str, Tuple[str, str, str, str, str, str, str, str]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start_scenario(
        self,
        name: str,
        mode: str = "headless",
        metadata: Optional[Dict[str, Any]] = None,
        sample_sys_interval_s: float = 0.25,
    ) -> None:
        """Activate global profiling for a named benchmark scenario."""
        self.stop_sampler()
        with self._lock:
            start_ns = time.perf_counter_ns()
            self._current = _ScenarioState(
                name=name,
                mode=mode,
                metadata=dict(metadata or {}),
                start_ns=start_ns,
            )
            self.enabled = True
        global _LAST_GUI_PUMP_NS
        _LAST_GUI_PUMP_NS = start_ns

        # Prime psutil CPU percent measurement
        self._sample_process_tree()
        if sample_sys_interval_s > 0:
            self._sampler_stop.clear()
            self._sampler_thread = threading.Thread(
                target=self._sys_sampler_loop,
                args=(sample_sys_interval_s,),
                daemon=True,
                name="FrameworkProfilerSysSampler",
            )
            self._sampler_thread.start()

    def stop_sampler(self) -> None:
        """Stop the background CPU/RSS telemetry sampler thread if running."""
        self._sampler_stop.set()
        if self._sampler_thread is not None and self._sampler_thread.is_alive():
            self._sampler_thread.join(timeout=1.0)
        self._sampler_thread = None

    def stop_scenario(self, extra_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Finalize the active scenario, compute statistics, and store the report entry."""
        end_ns = time.perf_counter_ns()
        self.enabled = False
        self.stop_sampler()
        # Capture one final system sample
        final_sys = self._sample_process_tree()

        with self._lock:
            sc = self._current
            if sc is None:
                return {}
            sc.end_ns = end_ns
            if extra_metadata:
                sc.metadata.update(extra_metadata)
            if final_sys:
                sc.sys_samples.append(final_sys)

            raw_duration_ns = max(sc.end_ns - sc.start_ns - sc.excluded_duration_ns, 1)
            duration_s = max(raw_duration_ns * 1e-9, 1e-9)
            metrics_summary: Dict[str, Dict[str, Any]] = {}
            for key, samples in sc.metrics_ns.items():
                metrics_summary[key] = summarize_samples_ns(samples)

            counters = dict(sc.counters)
            # Derived throughput metrics
            received = counters.get("received_count", 0)
            emitted = counters.get("emitted_count", 0)
            bytes_tx = counters.get("bytes_transferred", 0)
            throughput_rx_hz = received / duration_s
            throughput_tx_hz = emitted / duration_s
            throughput_mb_s = (bytes_tx / (1024.0 * 1024.0)) / duration_s

            sys_summary = self._summarize_sys(
                sc.sys_samples,
                rendered_frames=sc.rendered_frames,
                duration_s=duration_s,
                frame_fps_samples=sc.frame_fps_samples,
            )

            result: Dict[str, Any] = {
                "scenario": sc.name,
                "mode": sc.mode,
                "duration_s": round(duration_s, 4),
                "metadata": sc.metadata,
                "counters": counters,
                "throughput": {
                    "emit_ops_s": round(throughput_tx_hz, 2),
                    "recv_ops_s": round(throughput_rx_hz, 2),
                    "bandwidth_mb_s": round(throughput_mb_s, 3),
                },
                "metrics": metrics_summary,
                "system": sys_summary,
            }
            self._completed.append(result)
            self._current = None
            return result

    def clear_completed(self) -> None:
        """Clear previously completed scenario summaries."""
        with self._lock:
            self._completed.clear()

    def get_completed(self) -> List[Dict[str, Any]]:
        """Return a shallow copy of all completed scenario reports."""
        with self._lock:
            return list(self._completed)

    def add_external_result(self, result: Dict[str, Any]) -> None:
        """Append an externally computed scenario result (e.g. merged subprocess run)."""
        with self._lock:
            self._completed.append(result)

    def get_active_metric_mean_ns(self, metric_name: str) -> int:
        """Return truncated mean (ns) of recorded samples for `metric_name` in the active scenario."""
        if not self.enabled:
            return 0
        with self._lock:
            sc = self._current
            if sc is None:
                return 0
            vals = sc.metrics_ns.get(metric_name)
            if not vals:
                return 0
            return int(sum(vals) / len(vals))

    def notify_frame_rendered(
        self,
        render_ns: int = 0,
        exclude_from_duration_ns: int = 0,
    ) -> None:
        """Record a rendered DearPyGui viewport frame during an active scenario."""
        global _LAST_GUI_PUMP_NS
        now_ns = time.perf_counter_ns()
        _LAST_GUI_PUMP_NS = now_ns
        if not self.enabled:
            return
        with self._lock:
            sc = self._current
            if sc is None:
                return
            sc.rendered_frames += 1
            if exclude_from_duration_ns > 0:
                sc.excluded_duration_ns += int(exclude_from_duration_ns)
            if render_ns > 0:
                sc.frame_fps_samples.append(1_000_000_000.0 / float(render_ns))
            elif sc.last_frame_pump_ns > 0 and now_ns > sc.last_frame_pump_ns:
                sc.frame_fps_samples.append(1_000_000_000.0 / float(now_ns - sc.last_frame_pump_ns))
            sc.last_frame_pump_ns = now_ns

    # ------------------------------------------------------------------
    # Recording API (hot-path optimized)
    # ------------------------------------------------------------------
    def record_duration_ns(self, metric_name: str, duration_ns: int) -> None:
        """Record a single nanosecond latency/duration sample under `metric_name`."""
        if not self.enabled or duration_ns < 0:
            return
        with self._lock:
            sc = self._current
            if sc is None:
                return
            buf = sc.metrics_ns[metric_name]
            if len(buf) < sc.max_samples_per_metric:
                buf.append(int(duration_ns))

    def record_counter(self, counter_name: str, delta: int = 1) -> None:
        """Increment an event/byte counter for the active scenario."""
        if not self.enabled:
            return
        with self._lock:
            sc = self._current
            if sc is not None:
                sc.counters[counter_name] += int(delta)

    def record_dispatch(
        self,
        metric_name: str,
        duration_ns: int,
        counter_name: str = "emitted_count",
        delta: int = 1,
    ) -> None:
        """Record a dispatch duration and increment a counter in one lock acquisition."""
        if not self.enabled:
            return
        with self._lock:
            sc = self._current
            if sc is None:
                return
            sc.counters[counter_name] += int(delta)
            if duration_ns >= 0:
                buf = sc.metrics_ns[metric_name]
                if len(buf) < sc.max_samples_per_metric:
                    buf.append(int(duration_ns))

    def record_hop(
        self,
        hop_ns: int,
        e2e_ns: Optional[int] = None,
        hop_key: str = "link_hop_ns",
        bytes_count: int = 0,
        exec_ns: Optional[int] = None,
        exec_key: str = "relay_exec_ns",
    ) -> None:
        """Record a module-to-module hop latency, optional E2E latency, and optional exec time."""
        if not self.enabled:
            return
        with self._lock:
            sc = self._current
            if sc is None:
                return
            cap = sc.max_samples_per_metric
            metrics = sc.metrics_ns
            if hop_ns >= 0:
                buf = metrics[hop_key]
                if len(buf) < cap:
                    buf.append(int(hop_ns))
                if hop_key != "link_hop_ns":
                    gbuf = metrics["link_hop_ns"]
                    if len(gbuf) < cap:
                        gbuf.append(int(hop_ns))
            if e2e_ns is not None and e2e_ns >= 0:
                ebuf = metrics["e2e_dag_ns"]
                if len(ebuf) < cap:
                    ebuf.append(int(e2e_ns))
            if exec_ns is not None and exec_ns >= 0:
                xbuf = metrics[exec_key]
                if len(xbuf) < cap:
                    xbuf.append(int(exec_ns))
            sc.counters["received_count"] += 1
            if bytes_count > 0:
                sc.counters["bytes_transferred"] += int(bytes_count)

    def record_processor_stages(
        self,
        stages: Dict[str, int],
        prefix: str = "proc",
        e2e_ns: Optional[int] = None,
        bytes_count: int = 0,
    ) -> None:
        """
        Record detailed ProcessingBase IPC stage timestamps (t0..t6 in perf_counter_ns).

        Expected keys in `stages`:
          t0_submit_enter, t1_submit_queued, t2_worker_dequeued,
          t3_worker_decoded, t4_worker_processed, t5_worker_published, t6_main_get
        """
        if not self.enabled:
            return
        t0 = stages.get("t0_submit_enter")
        t1 = stages.get("t1_submit_queued")
        t2 = stages.get("t2_worker_dequeued")
        t3 = stages.get("t3_worker_decoded")
        t4 = stages.get("t4_worker_processed")
        t5 = stages.get("t5_worker_published")
        t6 = stages.get("t6_main_get")

        keys = self._stage_key_cache.get(prefix)
        if keys is None:
            keys = (
                f"{prefix}_main_encode_ns",
                f"{prefix}_ipc_in_ns",
                f"{prefix}_worker_decode_ns",
                f"{prefix}_worker_compute_ns",
                f"{prefix}_worker_encode_ns",
                f"{prefix}_ipc_out_ns",
                f"{prefix}_total_roundtrip_ns",
                f"{prefix}_ipc_overhead_ns",
            )
            self._stage_key_cache[prefix] = keys
        (
            k_enc,
            k_ipc_in,
            k_wdec,
            k_comp,
            k_wenc,
            k_ipc_out,
            k_tot,
            k_ovh,
        ) = keys

        t_in_end = t2 if t2 is not None else t3
        t_out_start = t5 if t5 is not None else t4

        with self._lock:
            sc = self._current
            if sc is None:
                return
            metrics = sc.metrics_ns
            cap = sc.max_samples_per_metric

            for key, a, b in (
                (k_enc, t0, t1),
                (k_ipc_in, t1, t_in_end),
                (k_wdec, t2, t3),
                (k_wenc, t4, t5),
                (k_ipc_out, t_out_start, t6),
            ):
                if a is not None and b is not None and b >= a:
                    buf = metrics[key]
                    if len(buf) < cap:
                        buf.append(int(b - a))

            compute_dt: Optional[int] = int(t4 - t3) if (t3 is not None and t4 is not None and t4 >= t3) else None
            if compute_dt is not None:
                buf = metrics[k_comp]
                if len(buf) < cap:
                    buf.append(compute_dt)

            total_dt: Optional[int] = int(t6 - t0) if (t0 is not None and t6 is not None and t6 >= t0) else None
            if total_dt is not None:
                buf = metrics[k_tot]
                if len(buf) < cap:
                    buf.append(total_dt)
                if compute_dt is not None and total_dt >= compute_dt:
                    obuf = metrics[k_ovh]
                    if len(obuf) < cap:
                        obuf.append(total_dt - compute_dt)
            sc.counters["proc_completed_count"] += 1
            if e2e_ns is not None and e2e_ns >= 0:
                ebuf = metrics["e2e_dag_ns"]
                if len(ebuf) < cap:
                    ebuf.append(int(e2e_ns))
                sc.counters["received_count"] += 1
                if bytes_count > 0:
                    sc.counters["bytes_transferred"] += int(bytes_count)

    # ------------------------------------------------------------------
    # System Telemetry Sampling
    # ------------------------------------------------------------------
    def _sample_process_tree(self) -> Optional[Dict[str, float]]:
        if self._proc is None:
            return None
        try:
            rss = self._proc.memory_info().rss
            cpu = self._proc.cpu_percent(interval=None)
            children = self._proc.children(recursive=True)
            for ch in children:
                try:
                    rss += ch.memory_info().rss
                    cpu += ch.cpu_percent(interval=None)
                except Exception:
                    pass
            fps = 0.0
            try:
                from core.app_state import app_state

                if app_state.gui_running and not app_state.headless and "dearpygui.dearpygui" in sys.modules:
                    import dearpygui.dearpygui as dpg

                    if dpg.is_dearpygui_running():
                        fps = float(dpg.get_frame_rate())
            except Exception:
                pass
            return {
                "rss_mb": rss / (1024.0 * 1024.0),
                "cpu_pct": float(cpu),
                "child_procs": float(len(children)),
                "threads": float(self._proc.num_threads()),
                "dpg_fps": fps,
            }
        except Exception:
            return None

    def _sys_sampler_loop(self, interval_s: float) -> None:
        while not self._sampler_stop.wait(interval_s):
            snap = self._sample_process_tree()
            if snap is None:
                continue
            with self._lock:
                if self._current is not None:
                    self._current.sys_samples.append(snap)

    @staticmethod
    def _summarize_sys(
        samples: List[Dict[str, float]],
        rendered_frames: int = 0,
        duration_s: float = 0.0,
        frame_fps_samples: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        if not samples and rendered_frames <= 0:
            return {}
        rss_vals = [s["rss_mb"] for s in samples] if samples else [0.0]
        cpu_vals = [s["cpu_pct"] for s in samples] if samples else [0.0]
        fps_vals = [s["dpg_fps"] for s in samples if s.get("dpg_fps", 0.0) > 0.0]
        children_max = max((int(s["child_procs"]) for s in samples), default=0)
        threads_max = max((int(s["threads"]) for s in samples), default=0)

        if frame_fps_samples:
            dpg_fps_mean = round(sum(frame_fps_samples) / len(frame_fps_samples), 2)
            dpg_fps_min = round(min(frame_fps_samples), 2)
        elif fps_vals:
            dpg_fps_mean = round(sum(fps_vals) / len(fps_vals), 2)
            dpg_fps_min = round(min(fps_vals), 2)
        else:
            dpg_fps_mean = 0.0
            dpg_fps_min = 0.0

        return {
            "rss_mean_mb": round(sum(rss_vals) / len(rss_vals), 2),
            "rss_peak_mb": round(max(rss_vals), 2),
            "cpu_mean_pct": round(sum(cpu_vals) / len(cpu_vals), 2),
            "cpu_peak_pct": round(max(cpu_vals), 2),
            "max_child_procs": children_max,
            "max_threads": threads_max,
            "dpg_fps_mean": dpg_fps_mean,
            "dpg_fps_min": dpg_fps_min,
            "rendered_frames": int(rendered_frames),
        }

    # ------------------------------------------------------------------
    # Exporters (JSON, CSV, Markdown Report)
    # ------------------------------------------------------------------
    def export_json(self, output_path: str | Path, extra_info: Optional[Dict[str, Any]] = None) -> Path:
        """Export all completed scenarios + host environment info + diagnostics as JSON."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        scenarios = self.get_completed()
        payload = {
            "environment": extra_info or self._collect_env_info(),
            "comfort_and_limits": self.compute_comfort_and_limits(scenarios),
            "scenarios": scenarios,
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return out

    def export_csv(self, output_path: str | Path) -> Path:
        """Export flattened scenario metrics into a publication-ready CSV table."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        scenarios = self.get_completed()
        fieldnames = [
            "scenario",
            "mode",
            "duration_s",
            "emit_ops_s",
            "recv_ops_s",
            "bandwidth_mb_s",
            "metric",
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
        ]
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for sc in scenarios:
                tp = sc.get("throughput", {})
                sys_m = sc.get("system", {})
                metrics = sc.get("metrics", {})
                if not metrics:
                    writer.writerow(
                        {
                            "scenario": sc.get("scenario"),
                            "mode": sc.get("mode"),
                            "duration_s": sc.get("duration_s"),
                            "emit_ops_s": tp.get("emit_ops_s", 0.0),
                            "recv_ops_s": tp.get("recv_ops_s", 0.0),
                            "bandwidth_mb_s": tp.get("bandwidth_mb_s", 0.0),
                            "metric": "none",
                            "count": 0,
                            "rss_peak_mb": sys_m.get("rss_peak_mb", 0.0),
                            "cpu_mean_pct": sys_m.get("cpu_mean_pct", 0.0),
                            "dpg_fps_mean": sys_m.get("dpg_fps_mean", 0.0),
                        }
                    )
                    continue
                for m_name, m_stats in metrics.items():
                    writer.writerow(
                        {
                            "scenario": sc.get("scenario"),
                            "mode": sc.get("mode"),
                            "duration_s": sc.get("duration_s"),
                            "emit_ops_s": tp.get("emit_ops_s", 0.0),
                            "recv_ops_s": tp.get("recv_ops_s", 0.0),
                            "bandwidth_mb_s": tp.get("bandwidth_mb_s", 0.0),
                            "metric": m_name,
                            "count": m_stats.get("count", 0),
                            "min_us": m_stats.get("min_us", 0.0),
                            "mean_us": m_stats.get("mean_us", 0.0),
                            "std_us": m_stats.get("std_us", 0.0),
                            "p50_us": m_stats.get("p50_us", 0.0),
                            "p95_us": m_stats.get("p95_us", 0.0),
                            "p99_us": m_stats.get("p99_us", 0.0),
                            "max_us": m_stats.get("max_us", 0.0),
                            "rss_peak_mb": sys_m.get("rss_peak_mb", 0.0),
                            "cpu_mean_pct": sys_m.get("cpu_mean_pct", 0.0),
                            "dpg_fps_mean": sys_m.get("dpg_fps_mean", 0.0),
                        }
                    )
        return out

    def export_markdown(self, output_path: str | Path, title: str = "Pipeline Creator Framework Benchmark Report") -> Path:
        """Export a structured Markdown report summarizing framework, IPC, scaling, and GUI impact."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        env = self._collect_env_info()
        scenarios = self.get_completed()

        lines: List[str] = [
            f"# {title}",
            "",
            "## Environment",
            f"- Python: `{env.get('python_version')}` on `{env.get('platform')}`",
            f"- CPU logical cores: `{env.get('cpu_count_logical')}` (physical: `{env.get('cpu_count_physical')}`)",
            f"- RAM total: `{env.get('ram_total_gb')} GB`",
            f"- Timestamp: `{env.get('timestamp')}`",
            "",
        ]
        lines.extend(self._build_simple_summary_lines(scenarios))
        lines.extend(
            [
                "",
                "## Scenario Summary",
                "",
                "| Scenario | Mode | Duration (s) | Recv Ops/s | Bandwidth (MB/s) | Key Latency Mean / p50 / p95 / p99 (us) | Peak RSS (MB) | FPS |",
                "|---|---|---:|---:|---:|---|---:|---:|",
            ]
        )

        for sc in scenarios:
            tp = sc.get("throughput", {})
            sys_m = sc.get("system", {})
            metrics = sc.get("metrics", {})
            primary = (
                metrics.get("e2e_dag_ns")
                or metrics.get("proc_total_roundtrip_ns")
                or metrics.get("link_hop_ns")
                or next(iter(metrics.values()), None)
            )
            if primary:
                lat_str = (
                    f"{primary['mean_us']:.2f} / {primary['p50_us']:.2f} / "
                    f"{primary['p95_us']:.2f} / {primary['p99_us']:.2f}"
                )
            else:
                lat_str = "n/a"
            lines.append(
                f"| {sc.get('scenario')} | {sc.get('mode')} | {sc.get('duration_s', 0):.3f} | "
                f"{tp.get('recv_ops_s', 0):.1f} | {tp.get('bandwidth_mb_s', 0):.2f} | "
                f"{lat_str} | {sys_m.get('rss_peak_mb', 0):.1f} | {sys_m.get('dpg_fps_mean', 0):.1f} |"
            )

        lines.extend(["", "## Detailed Metric Breakdown", ""])
        for sc in scenarios:
            lines.append(f"### {sc.get('scenario')} (`{sc.get('mode')}`)")
            if sc.get("metadata"):
                lines.append(f"- Metadata: `{json.dumps(sc['metadata'])}`")
            if sc.get("counters"):
                lines.append(f"- Counters: `{json.dumps(sc['counters'])}`")
            metrics = sc.get("metrics", {})
            if metrics:
                lines.extend(
                    [
                        "",
                        "| Metric | Count | Min (us) | Mean (us) | Std (us) | p50 (us) | p95 (us) | p99 (us) | Max (us) |",
                        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
                    ]
                )
                for m_name, m in metrics.items():
                    lines.append(
                        f"| `{m_name}` | {m['count']} | {m['min_us']:.3f} | {m['mean_us']:.3f} | "
                        f"{m['std_us']:.3f} | {m['p50_us']:.3f} | {m['p95_us']:.3f} | "
                        f"{m['p99_us']:.3f} | {m['max_us']:.3f} |"
                    )
            lines.append("")

        out.write_text("\n".join(lines), encoding="utf-8")
        return out

    @staticmethod
    def classify_fps_comfort(fps: float) -> str:
        """Classify DearPyGui interactive frame rate into human-readable comfort levels."""
        if fps >= 45.0:
            return "Smooth"
        if fps >= 25.0:
            return "Comfortable"
        if fps >= 12.0:
            return "Sluggish"
        return "Critical / Breaking Point"

    @staticmethod
    def classify_latency_realtime(lat_us: float) -> str:
        """Classify mean/p95 E2E latency against 60 Hz (16.7 ms) and 30 Hz (33.3 ms) budgets."""
        if lat_us <= 16_667.0:
            return "Real-Time 60 Hz"
        if lat_us <= 33_333.0:
            return "Real-Time 30 Hz"
        return "Over Real-Time Budget (>33.3 ms)"

    @classmethod
    def compute_comfort_and_limits(cls, scenarios: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze stress & scaling scenarios to extract hardware limits and UI comfort thresholds."""
        series: Dict[str, List[Dict[str, Any]]] = {
            "chain": [],
            "fanout": [],
            "mesh": [],
            "workers": [],
        }

        for sc in scenarios:
            name = str(sc.get("scenario", ""))
            mode = str(sc.get("mode", "headless"))
            meta = sc.get("metadata", {}) or {}
            metrics = sc.get("metrics", {}) or {}
            primary = (
                metrics.get("e2e_dag_ns")
                or metrics.get("proc_total_roundtrip_ns")
                or metrics.get("link_hop_ns")
                or {}
            )
            mean_us = float(primary.get("mean_us", 0.0))
            p95_us = float(primary.get("p95_us", 0.0))
            tp = sc.get("throughput", {}) or {}
            sys_m = sc.get("system", {}) or {}
            fps = float(sys_m.get("dpg_fps_mean", 0.0))
            rss_mb = float(sys_m.get("rss_peak_mb", 0.0))
            ops_s = float(tp.get("recv_ops_s", 0.0))
            bw_mb_s = float(tp.get("bandwidth_mb_s", 0.0))

            axis: Optional[str] = None
            scale_x: Optional[int] = None
            label: str = name

            if name.startswith("stress_chain_d") or name.startswith("chain_d"):
                axis = "chain"
                suffix = name.split("_d")[-1]
                scale_x = int(meta.get("scale_x") or meta.get("depth") or (int(suffix) if suffix.isdigit() else 0))
                label = f"D={scale_x}"
            elif name.startswith("stress_fanout_k") or name.startswith("fanout_k"):
                axis = "fanout"
                suffix = name.split("_k")[-1]
                scale_x = int(meta.get("scale_x") or meta.get("fanout") or (int(suffix) if suffix.isdigit() else 0))
                label = f"K={scale_x}"
            elif name.startswith("stress_mesh_") or name.startswith("mesh_"):
                axis = "mesh"
                topo = name.split("mesh_")[-1]
                layers = int(meta.get("layers", 0) or (topo.split("x")[0] if "x" in topo else 0))
                width = int(meta.get("width", 0) or (topo.split("x")[1] if "x" in topo else 0))
                scale_x = int(meta.get("deliveries_per_pulse") or (width ** layers if layers and width else layers * width))
                label = f"{layers}x{width}"
            elif name.startswith("stress_workers_p") or name.startswith("workers_parallel_p"):
                axis = "workers"
                suffix = name.split("_p")[-1]
                scale_x = int(meta.get("parallel_workers") or meta.get("scale_x") or (int(suffix) if suffix.isdigit() else 0))
                label = f"P={scale_x}"

            if axis is None or not scale_x:
                continue

            entry = {
                "scenario": name,
                "mode": mode,
                "is_stress": name.startswith("stress_"),
                "scale_x": scale_x,
                "label": label,
                "mean_us": mean_us,
                "p95_us": p95_us,
                "recv_ops_s": ops_s,
                "bandwidth_mb_s": bw_mb_s,
                "rss_peak_mb": rss_mb,
                "dpg_fps_mean": fps,
                "dpg_fps_min": float(sys_m.get("dpg_fps_min", fps)),
                "latency_status": cls.classify_latency_realtime(mean_us),
                "ui_comfort": cls.classify_fps_comfort(fps) if mode != "headless" else "Headless",
                "metadata": meta,
            }
            series[axis].append(entry)

        # Deduplicate keeping stress_* over legacy standard if same (scale_x, mode) unless distinct
        for axis_key in series:
            dedup: Dict[Tuple[int, str], Dict[str, Any]] = {}
            for pt in series[axis_key]:
                k = (pt["scale_x"], pt["mode"])
                if k not in dedup or pt["is_stress"]:
                    dedup[k] = pt
            series[axis_key] = sorted(dedup.values(), key=lambda r: (r["mode"], r["scale_x"]))

        def _best_limit(axis_name: str, mode_filter: str) -> Dict[str, Any]:
            pts = [p for p in series[axis_name] if p["mode"] == mode_filter]
            if not pts:
                pts = [p for p in series[axis_name] if p["mode"] == "headless"]
            rt60 = [p for p in pts if p["mean_us"] <= 16_667.0]
            rt30 = [p for p in pts if p["mean_us"] <= 33_333.0]
            ui_ok = [p for p in pts if p["mode"] != "headless" and p["dpg_fps_mean"] >= 25.0]
            breaking = [p for p in pts if p["mean_us"] > 33_333.0 or (p["mode"] != "headless" and p["dpg_fps_mean"] < 12.0)]
            return {
                "mode": pts[0]["mode"] if pts else mode_filter,
                "max_tested": pts[-1] if pts else None,
                "max_rt60": rt60[-1] if rt60 else None,
                "max_rt30": rt30[-1] if rt30 else None,
                "max_ui_comfortable": ui_ok[-1] if ui_ok else None,
                "first_breaking": breaking[0] if breaking else None,
            }

        wrk_hl = [p for p in series["workers"] if p["mode"] == "headless"]
        wrk_gui = [p for p in series["workers"] if p["mode"] == "gui_live_ui"]
        opt_wrk = max(wrk_hl, key=lambda p: p["bandwidth_mb_s"]) if wrk_hl else (
            max(series["workers"], key=lambda p: p["bandwidth_mb_s"]) if series["workers"] else None
        )
        wrk_gui_ok = [p for p in wrk_gui if p["dpg_fps_mean"] >= 25.0]
        opt_wrk_gui = max(wrk_gui_ok, key=lambda p: p["bandwidth_mb_s"]) if wrk_gui_ok else None
        peak_wrk_gui = max(wrk_gui, key=lambda p: p["bandwidth_mb_s"]) if wrk_gui else None

        chain_hl = [p for p in series["chain"] if p["mode"] == "headless"]
        chain_gui = [p for p in series["chain"] if p["mode"] == "gui_live_ui"]
        fan_hl = [p for p in series["fanout"] if p["mode"] == "headless"]
        fan_gui = [p for p in series["fanout"] if p["mode"] == "gui_live_ui"]
        mesh_hl = [p for p in series["mesh"] if p["mode"] == "headless"]
        mesh_gui = [p for p in series["mesh"] if p["mode"] == "gui_live_ui"]

        return {
            "series": series,
            "max_chain_depth_realtime": max((p["scale_x"] for p in chain_hl if p["mean_us"] <= 16_667.0), default=None),
            "max_chain_depth_ui_comfort": max((p["scale_x"] for p in chain_gui if p["dpg_fps_mean"] >= 25.0), default=None),
            "max_fanout_k_realtime": max((p["scale_x"] for p in fan_hl if p["mean_us"] <= 16_667.0), default=None),
            "max_fanout_k_ui_comfort": max((p["scale_x"] for p in fan_gui if p["dpg_fps_mean"] >= 25.0), default=None),
            "max_mesh_realtime": (
                max((p for p in mesh_hl if p["mean_us"] <= 16_667.0), key=lambda p: p["scale_x"])
                if any(p["mean_us"] <= 16_667.0 for p in mesh_hl)
                else None
            ),
            "first_mesh_over_rt60": next((p for p in mesh_hl if p["mean_us"] > 16_667.0), None),
            "first_mesh_over_rt30": next((p for p in mesh_hl if p["mean_us"] > 33_333.0), None),
            "max_mesh_ui_comfort": (
                max((p for p in mesh_gui if p["dpg_fps_mean"] >= 25.0), key=lambda p: p["scale_x"])
                if any(p["dpg_fps_mean"] >= 25.0 for p in mesh_gui)
                else None
            ),
            "optimal_workers_1080p": opt_wrk,
            "optimal_workers_1080p_gui": opt_wrk_gui,
            "peak_workers_1080p_gui": peak_wrk_gui,
            "chain_limits": _best_limit("chain", "gui_live_ui"),
            "fanout_limits": _best_limit("fanout", "gui_live_ui"),
            "mesh_limits": _best_limit("mesh", "gui_live_ui"),
        }

    @classmethod
    def _build_simple_summary_lines(cls, scenarios: List[Dict[str, Any]]) -> List[str]:
        """Build a concise plain-language summary of global framework overheads."""
        by_name: Dict[str, Dict[str, Any]] = {}
        for sc in scenarios:
            by_name.setdefault(str(sc.get("scenario", "")), sc)

        summary_rows: List[Tuple[str, str, str]] = []

        def _fmt_lat(m: Optional[Dict[str, Any]]) -> str:
            if not m:
                return "n/a"
            return f"{m.get('mean_us', 0.0):.2f} us (p50: {m.get('p50_us', 0.0):.2f} us, p95: {m.get('p95_us', 0.0):.2f} us)"

        hop_sc = by_name.get("hop_1to1_token") or by_name.get("cmp_hop_1to1_burst")
        if hop_sc:
            m = hop_sc.get("metrics", {})
            hop_m = m.get("link_hop_ns")
            e2e_m = m.get("e2e_dag_ns")
            ops = hop_sc.get("throughput", {}).get("recv_ops_s", 0.0)
            if hop_m:
                summary_rows.append((
                    "Direct transfer between 2 modules (`link_hop_ns`)",
                    _fmt_lat(hop_m),
                    f"{ops:,.0f} msg/s",
                ))
            if e2e_m:
                summary_rows.append((
                    "End-to-end Source -> Sink (`e2e_dag_ns`)",
                    _fmt_lat(e2e_m),
                    "Zero-copy (Python ref)",
                ))

        for key, label in (("routing_gate", "Conditional `Gate` routing"), ("routing_linkbus", "Shared `LinkOut -> LinkBus -> LinkIn` bus")):
            sc = by_name.get(key)
            if sc:
                m = sc.get("metrics", {})
                ops = sc.get("throughput", {}).get("recv_ops_s", 0.0)
                summary_rows.append((label, _fmt_lat(m.get("e2e_dag_ns") or m.get("link_hop_ns")), f"{ops:,.0f} msg/s"))

        # Process / IPC breakdown
        pp_tok = by_name.get("proc_pingpong_dict_token")
        if pp_tok:
            pm = pp_tok.get("metrics", {})
            enc = pm.get("proc_main_encode_ns", {}).get("mean_us", 0.0)
            ipc_in = pm.get("proc_ipc_in_ns", {}).get("mean_us", 0.0)
            comp = pm.get("proc_worker_compute_ns", {}).get("mean_us", 0.0)
            ipc_out = pm.get("proc_ipc_out_ns", {}).get("mean_us", 0.0)
            tot = pm.get("proc_total_roundtrip_ns")
            summary_rows.append((
                "Multiprocess `ProcessingBase` round-trip (token)",
                _fmt_lat(tot),
                f"IPC in ~{enc + ipc_in:.0f} us, Compute ~{comp:.1f} us, IPC out ~{ipc_out:.0f} us",
            ))

        pp_256 = by_name.get("proc_pingpong_shm_256kb") or by_name.get("proc_shm_numpy_256kb")
        if pp_256:
            pm = pp_256.get("metrics", {})
            bw = pp_256.get("throughput", {}).get("bandwidth_mb_s", 0.0)
            summary_rows.append((
                "Multiprocess `ShmPool` transfer (256 KB)",
                _fmt_lat(pm.get("proc_total_roundtrip_ns")),
                f"{bw:.1f} MB/s",
            ))

        shm_1m = by_name.get("proc_pingpong_shm_1mb")
        pkl_1m = by_name.get("proc_pingpong_pickle_1mb")
        if shm_1m and pkl_1m:
            shm_ms = shm_1m.get("metrics", {}).get("proc_total_roundtrip_ns", {}).get("mean_us", 0.0) / 1000.0
            pkl_ms = pkl_1m.get("metrics", {}).get("proc_total_roundtrip_ns", {}).get("mean_us", 0.0) / 1000.0
            speedup = (pkl_ms / shm_ms) if shm_ms > 0 else 0.0
            summary_rows.append((
                "`ShmPool` vs Pure Pickle (1 MB array)",
                f"{shm_ms:.2f} ms vs {pkl_ms:.2f} ms",
                f"{speedup:.2f}x faster round-trip with ShmPool",
            ))

        shm_4m = by_name.get("proc_shm_numpy_4mb")
        pkl_4m = by_name.get("proc_pickle_numpy_4mb")
        if shm_4m and pkl_4m:
            shm_bw = shm_4m.get("throughput", {}).get("bandwidth_mb_s", 0.0)
            pkl_bw = pkl_4m.get("throughput", {}).get("bandwidth_mb_s", 0.0)
            bw_gain = (shm_bw / pkl_bw) if pkl_bw > 0 else 0.0
            summary_rows.append((
                "`ShmPool` vs Pure Pickle (4 MB burst)",
                f"{shm_bw:.1f} vs {pkl_bw:.1f} MB/s",
                f"{bw_gain:.2f}x higher throughput with ShmPool",
            ))

        # Parallel worker scaling optimum
        par_scs = [sc for name, sc in by_name.items() if name.startswith("workers_parallel_p") or name.startswith("stress_workers_p")]
        if par_scs:
            best = max(par_scs, key=lambda s: s.get("throughput", {}).get("recv_ops_s", 0.0))
            best_meta = best.get("metadata", {})
            p_cnt = best_meta.get("parallel_workers", "?")
            payload_lbl = best_meta.get("payload", "1080p RGB")
            best_ops = best.get("throughput", {}).get("recv_ops_s", 0.0)
            best_bw = best.get("throughput", {}).get("bandwidth_mb_s", 0.0)
            summary_rows.append((
                f"Optimal parallel workers (`ProcessingBase`, {payload_lbl})",
                f"P = {p_cnt} workers",
                f"{best_ops:,.0f} ops/s ({best_bw:.1f} MB/s)",
            ))

        # Generic fallback for user custom runs without matrix scenario names
        if not summary_rows and scenarios:
            for sc in scenarios[:5]:
                m = sc.get("metrics", {})
                key_m = m.get("e2e_dag_ns") or m.get("proc_total_roundtrip_ns") or m.get("link_hop_ns") or m.get("module_input_cb_ns")
                ops = sc.get("throughput", {}).get("recv_ops_s", 0.0)
                summary_rows.append((
                    f"Scenario `{sc.get('scenario')}` ({sc.get('mode')})",
                    _fmt_lat(key_m),
                    f"{ops:,.0f} ops/s",
                ))

        block: List[str] = []
        if summary_rows:
            block.extend([
                "## Global Summary (Key Takeaways)",
                "",
                "| Metric | Mean Latency (median / p95) | Throughput / Detail |",
                "|---|---|---|",
            ])
            for label, lat, detail in summary_rows:
                block.append(f"| {label} | {lat} | {detail} |")

        # Add Practical PC Limits & UI Comfort section if stress scenarios exist
        has_stress = any(str(s.get("scenario", "")).startswith("stress_") for s in scenarios)
        if has_stress:
            diag = cls.compute_comfort_and_limits(scenarios)
            block.extend([
                "",
                "## Practical PC Limits & UI Comfort (Stress Test)",
                "",
                "| Axis | Real-Time 60 Hz Limit (<=16.7 ms) | GUI Live-UI Comfort Limit (>=25 FPS) | Observation / Peak |",
                "|---|---|---|---|",
            ])
            ch_max = diag.get("max_chain_depth_realtime")
            ch_ui = diag.get("max_chain_depth_ui_comfort")
            ch_top = (diag.get("chain_limits", {}).get("max_tested") or {})
            block.append(
                f"| Serial Chain (`D` modules) | Up to `D = {ch_max}` | "
                f"{f'Up to D = {ch_ui}' if ch_ui is not None else 'n/a'} | "
                f"Max tested `{ch_top.get('label', 'n/a')}` ({ch_top.get('mean_us', 0.0) / 1000.0:.2f} ms, {ch_top.get('dpg_fps_mean', 0.0):.1f} FPS) |"
            )

            fn_max = diag.get("max_fanout_k_realtime")
            fn_ui = diag.get("max_fanout_k_ui_comfort")
            fn_top = (diag.get("fanout_limits", {}).get("max_tested") or {})
            block.append(
                f"| Parallel Fan-Out (`K` modules) | Up to `K = {fn_max}` | "
                f"{f'Up to K = {fn_ui}' if fn_ui is not None else 'n/a'} | "
                f"Max tested `{fn_top.get('label', 'n/a')}` ({fn_top.get('mean_us', 0.0) / 1000.0:.2f} ms, {fn_top.get('dpg_fps_mean', 0.0):.1f} FPS) |"
            )

            m_rt = diag.get("max_mesh_realtime")
            m_brk = diag.get("first_mesh_over_rt60")
            m_ui = diag.get("max_mesh_ui_comfort")
            m_brk_str = (
                f"Breaks 60 Hz budget at {m_brk['label']} ({m_brk['mean_us'] / 1000.0:.1f} ms)"
                if m_brk
                else "All tested meshes < 16.7 ms"
            )
            block.append(
                f"| Dense Matrix (`L x W`) | {m_rt['label'] if m_rt else 'None'} | "
                f"{m_ui['label'] if m_ui else 'n/a'} | "
                f"{m_brk_str} |"
            )

            opt_w = diag.get("optimal_workers_1080p")
            opt_wg = diag.get("optimal_workers_1080p_gui")
            pk_wg = diag.get("peak_workers_1080p_gui")
            if opt_w:
                if opt_wg:
                    gui_str = (
                        f"Up to {opt_wg['label']} @ {opt_wg['dpg_fps_mean']:.1f} FPS ({opt_wg['bandwidth_mb_s']:.0f} MB/s)"
                    )
                elif pk_wg:
                    gui_str = f"Peak {pk_wg['label']} @ {pk_wg['dpg_fps_mean']:.1f} FPS (<25 FPS)"
                else:
                    gui_str = "n/a"
                block.extend([
                    f"| Multiprocess 1080p Workers (`P`) | Peak `{opt_w['label']}` ({opt_w['bandwidth_mb_s']:.1f} MB/s) | "
                    f"{gui_str} | Peak RSS `{opt_w['rss_peak_mb']:.0f} MB` ({opt_w['recv_ops_s']:.0f} frames/s) |"
                ])

            block.extend([
                "",
                "### Quick Interpretation Guide",
                "- **`link_hop_ns` / `e2e_dag_ns`**: Overhead of passing Python references between connected nodes (< 5 us per hop is negligible even at thousands of FPS).",
                "- **16.7 ms (60 Hz) & 33.3 ms (30 Hz) Budgets**: A pipeline stays responsive for real-time capture as long as end-to-end DAG propagation remains under the frame interval.",
                "- **GUI Live FPS**: Values `>= 45 FPS` feel completely fluid; `25-45 FPS` remain comfortable; `< 12 FPS` indicates UI thread saturation from high-frequency widget updates or synchronous Python loops.",
                "- **`ShmPool` vs Pure Pickle**: Shared memory bypasses OS pipe pickling for NumPy frames >= 64 KB; scaling multiple 1080p worker processes improves throughput up to the host's memory-copy bandwidth ceiling while increasing aggregated RSS.",
            ])

        return block

    @staticmethod
    def _collect_env_info() -> Dict[str, Any]:
        import platform
        import sys

        info: Dict[str, Any] = {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count_logical": os.cpu_count(),
            "cpu_count_physical": psutil.cpu_count(logical=False) if psutil else None,
            "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 2) if psutil else None,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return info


def should_refresh_benchmark_ui(
    ui_update_hz: float,
    last_update_ns: int,
    is_headless: bool,
    force: bool = False,
) -> Optional[int]:
    """Return a timestamp (ns) if a benchmark node UI widget should refresh now, else ``None``.

    When ``ui_update_hz == 0.0`` and ``framework_profiler.enabled`` is True, returns ``None``
    so passive GUI benchmarks incur zero widget-update overhead. When the profiler is disabled
    (interactive manual testing), defaults to a responsive 15 Hz cadence.
    """
    if is_headless:
        return None
    hz = ui_update_hz
    if hz == 0.0:
        if framework_profiler.enabled:
            return None
        hz = 15.0
    now_ns = time.perf_counter_ns()
    if hz > 0.0 and not force:
        if now_ns - last_update_ns < int(1_000_000_000.0 / hz):
            return None
    return now_ns


_LAST_GUI_PUMP_NS: int = 0


def pump_gui_frame_if_needed(
    min_interval_ns: int = 0,
    force: bool = False,
    extra_cycle_ns: int = 0,
    exclude_probe_time: bool = False,
    extra_excluded_ns: int = 0,
) -> bool:
    """Pump one DearPyGui viewport frame on the main thread when throttled interval has elapsed."""
    global _LAST_GUI_PUMP_NS
    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        from core.app_state import app_state

        if app_state.headless or not app_state.gui_running:
            return False
    except Exception:
        return False

    now_ns = time.perf_counter_ns()
    if not force and min_interval_ns > 0 and (now_ns - _LAST_GUI_PUMP_NS < min_interval_ns):
        return False
    if "dearpygui.dearpygui" not in sys.modules:
        return False
    try:
        import dearpygui.dearpygui as dpg

        if not dpg.is_dearpygui_running():
            return False
        t0 = time.perf_counter_ns()
        dpg.render_dearpygui_frame()
        t1 = time.perf_counter_ns()
        _LAST_GUI_PUMP_NS = t1
        render_cost_ns = (t1 - t0) + max(0, int(extra_cycle_ns))
        excl_ns = ((t1 - t0) + max(0, int(extra_excluded_ns))) if exclude_probe_time else 0
        framework_profiler.notify_frame_rendered(
            render_ns=render_cost_ns,
            exclude_from_duration_ns=excl_ns,
        )
        return True
    except Exception:
        return False


framework_profiler: FrameworkProfiler = FrameworkProfiler()
