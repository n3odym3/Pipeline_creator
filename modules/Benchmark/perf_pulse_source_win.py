"""
Lightweight Performance Pulse Source Module (Benchmark & Stress Suite).

Generates timestamped micro-packets or NumPy payloads (small pickle vs large ShmPool)
with nanosecond perf_counter instrumentation and zero-allocation fast paths.
Designed to run cleanly in both Full GUI and --headless modes.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.app_state import app_state
from core.framework_profiler import (
    framework_profiler,
    pump_gui_frame_if_needed,
    should_refresh_benchmark_ui,
)
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class PerfPulseSource_win(WindowBase):
    """
    Ultra-lightweight synthetic stimulus and orchestrator node for framework-scale profiling.

    Emits stamped benchmark packets to downstream connected modules and exposes
    AutomationManager-callable methods (`run_burst`, `run_stream`, `profiler_start`,
    `profiler_stop`, `profiler_export`, `request_exit`).
    """

    label: str = "Perf Pulse Source"
    win_width: int = display_scaling.scale(320)
    win_height: int = display_scaling.scale(240)
    description: str = "Lightweight perf_counter packet generator for framework benchmarking."

    accepted_input_types: list[type] = [IOTypes.TRIGGER, IOTypes.CMD_DICT, IOTypes.ANY]
    outputs: dict[str, type] = {
        "Out": IOTypes.ANY,
        "Trigger": IOTypes.TRIGGER,
    }
    persistent_fields: list[str] = [
        "label",
        "payload_profile",
        "burst_count",
        "rate_hz",
        "duration_s",
        "ui_update_hz",
    ]

    _PROFILES: Dict[str, int] = {
        "token": 0,
        "numpy_1kb": 1024,
        "numpy_64kb": 65536,
        "numpy_256kb": 262144,
        "numpy_1mb": 1048576,
        "numpy_4mb": 4194304,
        "numpy_1080p_rgb": 1920 * 1080 * 3,
    }

    def __init__(
        self,
        label: str = "Perf Pulse Source",
        win_width: int = display_scaling.scale(320),
        win_height: int = display_scaling.scale(240),
        pos: list[int] = [60, 60],
        uuid: Optional[str] = None,
        visible: bool = True,
        payload_profile: str = "token",
        burst_count: int = 2000,
        rate_hz: float = 1000.0,
        duration_s: float = 2.0,
        ui_update_hz: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            label=label,
            win_width=win_width,
            win_height=win_height,
            pos=pos,
            uuid=uuid,
            visible=visible,
            **kwargs,
        )
        self.payload_profile: str = str(payload_profile)
        self.burst_count: int = int(burst_count)
        self.rate_hz: float = float(rate_hz)
        self.duration_s: float = float(duration_s)
        self.ui_update_hz: float = float(ui_update_hz)

        self._seq: int = 0
        self._running: bool = False
        self._stop_event = threading.Event()
        self._stream_thread: Optional[threading.Thread] = None
        self._preallocated_arrays: Dict[str, np.ndarray] = {}
        self._last_ui_update_ns: int = 0
        self._txt_status_tag: str = f"{self.winID}_status"
        self._txt_stats_tag: str = f"{self.winID}_stats"

        self.create()

    def create(self) -> None:
        """Build minimal DearPyGui window (skipped when running headless)."""
        if app_state.headless:
            return
        with self.window():
            dpg.add_text("Perf Pulse Source (Benchmark)", color=(110, 210, 255))
            dpg.add_text("Status: Idle", tag=self._txt_status_tag)
            dpg.add_text("Emitted: 0", tag=self._txt_stats_tag)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Burst", callback=self._on_burst_clicked)
                dpg.add_button(label="Stream", callback=self._on_stream_clicked)
                dpg.add_button(label="Stop", callback=self._on_stop_clicked)

    def set_ui_update_hz(self, hz: float) -> None:
        """Configure live DearPyGui widget refresh cadence (0 = passive when profiling)."""
        self.ui_update_hz = float(hz)
        self._last_ui_update_ns = 0

    def _refresh_ui(self, status: Optional[str] = None, force: bool = False) -> None:
        now_ns = should_refresh_benchmark_ui(
            self.ui_update_hz, self._last_ui_update_ns, app_state.headless, force=force
        )
        if now_ns is None:
            return
        self._last_ui_update_ns = now_ns
        if status is not None and dpg.does_item_exist(self._txt_status_tag):
            dpg.set_value(self._txt_status_tag, f"Status: {status}")
        if dpg.does_item_exist(self._txt_stats_tag):
            dpg.set_value(self._txt_stats_tag, f"Emitted: {self._seq}")

    def _sync_cluster_ui_manual(self) -> None:
        """Force final exact counter sync across Perf nodes during interactive manual runs."""
        if app_state.headless or framework_profiler.enabled:
            return
        from core.module_registry import MODULES_REGISTRY

        for mod in list(MODULES_REGISTRY.values()):
            fn = getattr(mod, "_refresh_ui", None)
            if callable(fn):
                try:
                    fn(force=True)
                except Exception:
                    pass

    def _launch_async_manual(self, target: Any) -> None:
        if self._stream_thread is not None and self._stream_thread.is_alive():
            return
        self._stream_thread = threading.Thread(target=target, daemon=True, name=f"PerfSrc_{self.UUID}")
        self._stream_thread.start()

    def _on_burst_clicked(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        self._launch_async_manual(lambda: self.run_burst(count=self.burst_count, profile=self.payload_profile))

    def _on_stream_clicked(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        self._launch_async_manual(
            lambda: self.run_stream(duration_s=self.duration_s, rate_hz=self.rate_hz, profile=self.payload_profile, uncapped=False)
        )

    def _on_stop_clicked(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        self.stop_stream()
        self._refresh_ui(status="Stopped", force=True)

    def _get_payload(self, profile: str) -> tuple[Optional[np.ndarray], int]:
        nbytes = self._PROFILES.get(profile, 0)
        if nbytes <= 0:
            return None, 0
        arr = self._preallocated_arrays.get(profile)
        if arr is None:
            if profile == "numpy_1080p_rgb":
                arr = np.full((1080, 1920, 3), 128, dtype=np.uint8)
            else:
                n_floats = max(1, nbytes // 4)
                arr = np.linspace(0.0, 1.0, n_floats, dtype=np.float32)
            self._preallocated_arrays[profile] = arr
        return arr, int(arr.nbytes)

    def _emit_one(self, payload: Optional[np.ndarray], nbytes: int, copy_per_target: bool = True) -> None:
        self._seq += 1
        conns = self.connections
        targets = conns["Out"] if "Out" in conns else ()
        t_start = time.perf_counter_ns()

        for tgt in targets:
            cb = getattr(tgt, "input_cb", None)
            if cb is not None:
                t_hop = time.perf_counter_ns()
                pkt = {
                    "__perf__": True,
                    "seq": self._seq,
                    "t_origin_ns": t_start,
                    "t_hop_ns": t_hop,
                    "nbytes": nbytes,
                    "data": payload,
                }
                cb(pkt)

        if "Trigger" in conns:
            for tgt in conns["Trigger"]:
                cb = getattr(tgt, "input_cb", None)
                if cb is not None:
                    cb(True)

        framework_profiler.record_dispatch("source_dispatch_ns", time.perf_counter_ns() - t_start)
        self._refresh_ui(force=False)

    def input_cb(self, data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Accept closed-loop echo packets or external trigger commands."""
        if isinstance(data, dict) and data.get("__perf__"):
            now = time.perf_counter_ns()
            t_origin = int(data.get("t_origin_ns", now))
            t_hop = int(data.get("t_hop_ns", now))
            nbytes = int(data.get("nbytes", 0))
            framework_profiler.record_hop(
                hop_ns=now - t_hop,
                e2e_ns=now - t_origin,
                hop_key="closed_loop_echo_ns",
                bytes_count=nbytes,
            )
            return

        if data is not None:
            self._launch_async_manual(lambda: self.run_burst(count=self.burst_count, profile=self.payload_profile))

    # ------------------------------------------------------------------
    # Script / Automation Callable Control API
    # ------------------------------------------------------------------
    def profiler_start(
        self,
        scenario: str = "custom",
        mode: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Start global framework profiler recording."""
        resolved_mode = mode or ("headless" if app_state.headless else "gui")
        framework_profiler.start_scenario(name=scenario, mode=resolved_mode, metadata=metadata)

    def profiler_stop(self, extra_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Stop global framework profiler and finalize current scenario."""
        return framework_profiler.stop_scenario(extra_metadata=extra_metadata)

    def profiler_export(self, output_prefix: str = "benchmark/report", build_reports: bool = True) -> None:
        """Export accumulated profiler scenarios to JSON, CSV, Markdown, and (when standalone) figures & user reports."""
        import os
        from pathlib import Path
        from core.paths import PROJECT_ROOT

        p = Path(output_prefix)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        framework_profiler.export_json(f"{p}.json")
        framework_profiler.export_csv(f"{p}.csv")
        framework_profiler.export_markdown(f"{p}.md")
        logger.info(f"Exported benchmark reports to {p}.{{json,csv,md}}")

        if build_reports and os.environ.get("PC_BENCHMARK_ORCHESTRATOR") != "1":
            try:
                from tools.run_benchmark import sync_all_reports_from_disk

                sync_all_reports_from_disk(fallback_prefix=p)
                logger.info("Automatically updated benchmark figures, interactive HTML dashboard, and notebook.")
            except Exception as exc:
                logger.warning(f"Failed to automatically build benchmark figures/reports: {exc}")

    def _run_uncapped_fps_probe(self, frames: int = 12) -> None:
        """Render uncapped frames (VSync off) under active pipeline load to measure max UI FPS."""
        if not framework_profiler.enabled or app_state.headless or not app_state.gui_running:
            return
        pulse_ns = framework_profiler.get_active_metric_mean_ns("source_dispatch_ns")
        probe_t0 = time.perf_counter()
        for _ in range(max(1, int(frames))):
            if self._stop_event.is_set():
                break
            ui_ns = 0
            if self.ui_update_hz != 0.0:
                u0 = time.perf_counter_ns()
                self._sync_cluster_ui_manual()
                ui_ns = time.perf_counter_ns() - u0
            pump_gui_frame_if_needed(
                min_interval_ns=0,
                force=True,
                extra_cycle_ns=pulse_ns + ui_ns,
                exclude_probe_time=True,
                extra_excluded_ns=ui_ns,
            )
            if (time.perf_counter() - probe_t0) >= 0.25:
                break

    def run_burst(
        self,
        count: Optional[int] = None,
        profile: Optional[str] = None,
        warmup: int = 50,
    ) -> None:
        """Synchronously blast `count` packets (after `warmup` untimed iterations)."""
        n = int(count if count is not None else self.burst_count)
        prof = str(profile if profile is not None else self.payload_profile)
        payload, nbytes = self._get_payload(prof)

        self._stop_event.clear()
        self._running = True
        self._refresh_ui(status="Bursting...", force=True)

        try:
            was_enabled = framework_profiler.enabled
            if warmup > 0:
                framework_profiler.enabled = False
                for _ in range(warmup):
                    if self._stop_event.is_set():
                        break
                    self._emit_one(payload, nbytes)
                self.wait_workers_idle(timeout_s=10.0)
                framework_profiler.enabled = was_enabled

            for _ in range(n):
                if self._stop_event.is_set():
                    break
                self._emit_one(payload, nbytes)
            self._run_uncapped_fps_probe(frames=12)
        finally:
            self._running = False
            self._refresh_ui(status="Idle", force=True)
            self._sync_cluster_ui_manual()

    def run_pingpong(
        self,
        count: int = 100,
        profile: str = "token",
        warmup: int = 5,
    ) -> None:
        """
        Emit `count` packets sequentially, waiting for all downstream ProcessingBase workers
        to complete each packet before sending the next (uncontended IPC latency).
        """
        payload, nbytes = self._get_payload(profile)
        was_enabled = framework_profiler.enabled
        if warmup > 0:
            framework_profiler.enabled = False
            for _ in range(warmup):
                self._emit_one(payload, nbytes)
                self.wait_workers_idle(timeout_s=10.0)
                pump_gui_frame_if_needed()
            framework_profiler.enabled = was_enabled

        for _ in range(int(count)):
            self._emit_one(payload, nbytes)
            self.wait_workers_idle(timeout_s=10.0)
            pump_gui_frame_if_needed()

        if not app_state.headless and dpg.does_item_exist(self._txt_stats_tag):
            dpg.set_value(self._txt_stats_tag, f"Emitted: {self._seq}")

    def run_stream(
        self,
        duration_s: Optional[float] = None,
        rate_hz: Optional[float] = None,
        profile: Optional[str] = None,
        uncapped: bool = False,
    ) -> None:
        """Emit packets for `duration_s` synchronously at `rate_hz` or uncapped."""
        dur = float(duration_s if duration_s is not None else self.duration_s)
        hz = float(rate_hz if rate_hz is not None else self.rate_hz)
        prof = str(profile if profile is not None else self.payload_profile)
        payload, nbytes = self._get_payload(prof)

        self._stop_event.clear()
        self._running = True
        self._refresh_ui(status="Streaming...", force=True)
        period = (1.0 / hz) if (not uncapped and hz > 0.0) else 0.0
        t_end = time.perf_counter() + dur
        next_tick = time.perf_counter()
        stream_idx = 0

        try:
            while not self._stop_event.is_set() and time.perf_counter() < t_end:
                self._emit_one(payload, nbytes)
                stream_idx += 1
                if period > 0.0:
                    next_tick += period
                    while not self._stop_event.is_set():
                        rem = next_tick - time.perf_counter()
                        if rem <= 0.0015:
                            break
                        if rem > 0.009 and pump_gui_frame_if_needed(min_interval_ns=16_000_000):
                            continue
                        sleep_s = min(rem - 0.001, 0.008)
                        if sleep_s > 0.0:
                            time.sleep(sleep_s)
                    while time.perf_counter() < next_tick:
                        pass
                elif (stream_idx & 255) == 0:
                    pump_gui_frame_if_needed(min_interval_ns=16_000_000)
            self._run_uncapped_fps_probe(frames=10)
        finally:
            self._running = False
            self._refresh_ui(status="Idle", force=True)
            self._sync_cluster_ui_manual()

    def stop_stream(self) -> None:
        """Stop any running stream or burst."""
        self._stop_event.set()
        self._running = False

    def set_cluster_ui_update_hz(self, ui_update_hz: float = 30.0, show_windows: bool = True) -> None:
        """Configure live UI widget update cadence and visibility across all loaded Perf nodes."""
        from core.module_registry import MODULES_REGISTRY

        hz = float(ui_update_hz)
        for mod in list(MODULES_REGISTRY.values()):
            fn = getattr(mod, "set_ui_update_hz", None)
            if callable(fn):
                fn(hz)
            if not app_state.headless and show_windows:
                win_id = getattr(mod, "winID", None)
                if win_id and dpg.does_item_exist(win_id):
                    try:
                        dpg.configure_item(win_id, show=(hz != 0.0))
                    except Exception:
                        pass

    def run_routing_burst(
        self,
        routing_kind: str = "gate",
        count: int = 2000,
        warmup: int = 50,
    ) -> None:
        """
        Measure routing primitive overhead (`gate` or `linkbus`) between this source
        and its first connected downstream target without altering the saved graph.
        """
        from core.node_link_proxies import _GateNode, _LinkInNode, _LinkOutNode

        orig_targets = list(self.connections.get("Out", []))
        if not orig_targets:
            logger.warning("run_routing_burst requires at least one downstream target connected on 'Out'")
            return

        payload, nbytes = self._get_payload("token")
        cleanup_objs: list[Any] = []

        if routing_kind == "gate":
            gate = _GateNode(label="BenchGate", uuid="bench_gate_tmp", is_open=True, io_type="ANY")
            gate.connections["Out"] = list(orig_targets)
            self.connections["Out"] = [gate]
            cleanup_objs.append(gate)
        elif routing_kind == "linkbus":
            link_name = f"bench_bus_{self.UUID}"
            l_out = _LinkOutNode(link_name=link_name, uuid="bench_lout_tmp")
            l_in = _LinkInNode(link_name=link_name, uuid="bench_lin_tmp")
            l_in.connections["Out"] = list(orig_targets)
            self.connections["Out"] = [l_out]
            cleanup_objs.extend([l_out, l_in])
        else:
            logger.warning(f"Unknown routing_kind '{routing_kind}'")
            return

        try:
            self.run_burst(count=count, profile="token", warmup=warmup)
        finally:
            self.connections["Out"] = orig_targets
            for obj in cleanup_objs:
                close_fn = getattr(obj, "close", None)
                if callable(close_fn):
                    try:
                        close_fn()
                    except Exception:
                        pass

    def wait_workers_idle(self, timeout_s: float = 20.0) -> None:
        """Wait until all PerfWorkerNode_win instances in MODULES_REGISTRY drain in-flight items."""
        import threading
        from core.module_registry import MODULES_REGISTRY

        workers = [mod for mod in list(MODULES_REGISTRY.values()) if callable(getattr(mod, "wait_idle", None))]
        if not workers:
            return
        can_pump = (
            not app_state.headless
            and app_state.gui_running
            and threading.current_thread() is threading.main_thread()
        )
        deadline = time.perf_counter() + float(timeout_s)
        if not can_pump:
            for mod in workers:
                rem = max(0.01, deadline - time.perf_counter())
                mod.wait_idle(timeout_s=rem)
            return

        while time.perf_counter() < deadline:
            if all(mod.wait_idle(timeout_s=0.0005) for mod in workers):
                break
            ui_ns = 0
            if self.ui_update_hz != 0.0:
                u0 = time.perf_counter_ns()
                self._sync_cluster_ui_manual()
                ui_ns = time.perf_counter_ns() - u0
            pump_gui_frame_if_needed(
                min_interval_ns=0,
                force=True,
                extra_cycle_ns=ui_ns,
                exclude_probe_time=False,
            )

    def attach_registry_probes(self) -> None:
        """
        Attach lightweight perf_counter input_cb probes to all non-Perf modules in
        MODULES_REGISTRY so existing unmodified pipelines can be benchmarked cleanly.
        """
        from core.module_registry import MODULES_REGISTRY

        for mod in list(MODULES_REGISTRY.values()):
            if getattr(mod, "_fw_probe_orig_cb", None) is not None:
                continue
            cls_name = type(mod).__name__
            if cls_name.startswith("Perf"):
                continue
            orig_cb = getattr(mod, "input_cb", None)
            if not callable(orig_cb):
                continue

            def _make_probe(orig: Any) -> Any:
                def _probed_cb(*args: Any, **kwargs: Any) -> Any:
                    t0 = time.perf_counter_ns()
                    try:
                        return orig(*args, **kwargs)
                    finally:
                        if framework_profiler.enabled:
                            framework_profiler.record_duration_ns(
                                "module_input_cb_ns", time.perf_counter_ns() - t0
                            )
                            framework_profiler.record_counter("received_count", 1)

                return _probed_cb

            mod._fw_probe_orig_cb = orig_cb
            mod.input_cb = _make_probe(orig_cb)

    def detach_registry_probes(self) -> None:
        """Restore original input_cb methods wrapped by `attach_registry_probes`."""
        from core.module_registry import MODULES_REGISTRY

        for mod in list(MODULES_REGISTRY.values()):
            orig_cb = getattr(mod, "_fw_probe_orig_cb", None)
            if orig_cb is not None:
                mod.input_cb = orig_cb
                delattr(mod, "_fw_probe_orig_cb")

    def calibrate_profiler_overhead(self, iterations: int = 5000) -> Dict[str, Any]:
        """Measure self-overhead of time.perf_counter_ns() + FrameworkProfiler.record_hop."""
        prev_enabled = framework_profiler.enabled
        framework_profiler.enabled = True
        framework_profiler.start_scenario("calibration_profiler_self_overhead", mode=("headless" if app_state.headless else "gui"))
        for _ in range(int(iterations)):
            t0 = time.perf_counter_ns()
            t1 = time.perf_counter_ns()
            framework_profiler.record_hop(hop_ns=max(0, t1 - t0), e2e_ns=max(0, t1 - t0), hop_key="perf_counter_pair_ns")
            t2 = time.perf_counter_ns()
            framework_profiler.record_duration_ns("record_hop_call_ns", max(0, t2 - t1))
        res = framework_profiler.stop_scenario({"iterations": int(iterations)})
        framework_profiler.enabled = prev_enabled
        return res

    def request_exit(self) -> None:
        """Signal the application main loop to terminate cleanly."""
        self.stop_stream()
        app_state.close_requested = True

    def close(self) -> None:
        self.stop_stream()
        super().close()


EXPORTED_CLASS = PerfPulseSource_win
EXPORTED_NAME = "Perf Pulse Source"
