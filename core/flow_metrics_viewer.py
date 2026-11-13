from __future__ import annotations

import importlib
import json
import os
import threading
import time
import types
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from core.module_registry import MODULES_REGISTRY
from core.paths import LOGS_DIR


def _load_psutil() -> Any:
    try:
        return importlib.import_module("psutil")
    except Exception:
        return None


_PSUTIL = _load_psutil()

COLOR_OK = (100, 240, 120)        # Green: Normal / Ready / Flowing / Low queue
COLOR_BUSY = (255, 215, 60)       # Yellow: Filling queue / Processing
COLOR_WAITING = (255, 80, 80)     # Red: Downstream holding / Saturated / Full queue / Stopped
COLOR_STANDBY = (120, 210, 255)   # Cyan / Light Blue: UI-Thread / Standby
COLOR_MUTED = (160, 160, 160)     # Gray: Idle / N/A / Default
COLOR_WHITE = (235, 235, 235)


class _ModuleProbe:
    """Non-intrusive runtime telemetry probe attached to a registered module instance."""

    __slots__ = (
        "uuid",
        "call_timestamps",
        "exec_times_ms",
        "render_times_ms",
        "total_calls",
        "last_exec_ms",
        "last_render_ms",
        "hooked",
        "render_hooked",
        "_orig_input_cb",
        "_orig_update_texture",
        "_lock",
    )

    def __init__(self, uuid: str) -> None:
        self.uuid = uuid
        self.call_timestamps: deque[float] = deque(maxlen=200)
        self.exec_times_ms: deque[float] = deque(maxlen=20)
        self.render_times_ms: deque[float] = deque(maxlen=20)
        self.total_calls: int = 0
        self.last_exec_ms: float = 0.0
        self.last_render_ms: float = 0.0
        self.hooked: bool = False
        self.render_hooked: bool = False
        self._orig_input_cb: Any = None
        self._orig_update_texture: Any = None
        self._lock = threading.Lock()

    @staticmethod
    def _chain_has_flow_wrapper( mod: Any, attr_name: str, perf_orig_attr: str) -> bool:
        curr = getattr(mod, attr_name, None)
        if getattr(curr, "_flow_metrics_wrapped", False):
            return True
        perf_orig = getattr(mod, perf_orig_attr, None)
        if getattr(perf_orig, "_flow_metrics_wrapped", False):
            return True
        return False

    def attach(self, mod: Any) -> None:
        # 1. Verify / attach `input_cb` hook (auto-recover if another profiler replaced or restored `input_cb`)
        if self.hooked and not self._chain_has_flow_wrapper(mod, "input_cb", "_perf_orig_input_cb"):
            self.hooked = False
            self._orig_input_cb = None

        if not self.hooked and hasattr(mod, "input_cb"):
            orig = getattr(mod, "input_cb", None)
            if callable(orig) and not getattr(orig, "_flow_metrics_wrapped", False):
                probe = self
                orig_func = orig.__func__ if hasattr(orig, "__func__") else None

                def _wrapped(self_obj: Any, *args: Any, **kwargs: Any) -> Any:
                    t0 = time.perf_counter()
                    try:
                        if orig_func is not None:
                            return orig_func(self_obj, *args, **kwargs)
                        return orig(*args, **kwargs)
                    finally:
                        t1 = time.perf_counter()
                        dt_ms = (t1 - t0) * 1000.0
                        with probe._lock:
                            probe.total_calls += 1
                            probe.last_exec_ms = dt_ms
                            probe.call_timestamps.append(t1)
                            probe.exec_times_ms.append(dt_ms)

                setattr(_wrapped, "_flow_metrics_wrapped", True)
                setattr(_wrapped, "_flow_metrics_probe", self)
                try:
                    self._orig_input_cb = orig
                    setattr(mod, "input_cb", types.MethodType(_wrapped, mod))
                    self.hooked = True
                except Exception:
                    self.hooked = False

        # 2. Verify / attach `_update_texture` hook (for threaded viewers like `VideoViewerWin`)
        if self.render_hooked and not self._chain_has_flow_wrapper(
            mod, "_update_texture", "_perf_orig__update_texture"
        ):
            self.render_hooked = False
            self._orig_update_texture = None

        if not self.render_hooked and hasattr(mod, "_update_texture"):
            orig_ut = getattr(mod, "_update_texture", None)
            if callable(orig_ut) and not getattr(orig_ut, "_flow_metrics_wrapped", False):
                probe = self
                orig_ut_func = orig_ut.__func__ if hasattr(orig_ut, "__func__") else None

                def _wrapped_ut(self_obj: Any, *args: Any, **kwargs: Any) -> Any:
                    t0 = time.perf_counter()
                    try:
                        if orig_ut_func is not None:
                            return orig_ut_func(self_obj, *args, **kwargs)
                        return orig_ut(*args, **kwargs)
                    finally:
                        dt_ms = (time.perf_counter() - t0) * 1000.0
                        with probe._lock:
                            probe.last_render_ms = dt_ms
                            probe.render_times_ms.append(dt_ms)

                setattr(_wrapped_ut, "_flow_metrics_wrapped", True)
                setattr(_wrapped_ut, "_flow_metrics_probe", self)
                try:
                    self._orig_update_texture = orig_ut
                    setattr(mod, "_update_texture", types.MethodType(_wrapped_ut, mod))
                    self.render_hooked = True
                except Exception:
                    self.render_hooked = False

    def detach(self, mod: Any) -> None:
        if self.hooked and self._orig_input_cb is not None:
            try:
                curr = getattr(mod, "input_cb", None)
                if getattr(curr, "_flow_metrics_wrapped", False):
                    setattr(mod, "input_cb", self._orig_input_cb)
                elif getattr(getattr(mod, "_perf_orig_input_cb", None), "_flow_metrics_wrapped", False):
                    setattr(mod, "_perf_orig_input_cb", self._orig_input_cb)
            except Exception:
                pass
        self.hooked = False
        self._orig_input_cb = None

        if self.render_hooked and self._orig_update_texture is not None:
            try:
                curr_ut = getattr(mod, "_update_texture", None)
                if getattr(curr_ut, "_flow_metrics_wrapped", False):
                    setattr(mod, "_update_texture", self._orig_update_texture)
                elif getattr(
                    getattr(mod, "_perf_orig__update_texture", None), "_flow_metrics_wrapped", False
                ):
                    setattr(mod, "_perf_orig__update_texture", self._orig_update_texture)
            except Exception:
                pass
        self.render_hooked = False
        self._orig_update_texture = None

    def stats(self, now: Optional[float] = None) -> Tuple[float, float, float, int, float]:
        """Returns (rate_hz, mean_exec_ms, p95_exec_ms, total_calls, mean_render_ms)."""
        t_now = time.perf_counter() if now is None else now
        with self._lock:
            while self.call_timestamps and (t_now - self.call_timestamps[0]) > 2.5:
                self.call_timestamps.popleft()
            n_t = len(self.call_timestamps)
            if n_t >= 2:
                span = self.call_timestamps[-1] - self.call_timestamps[0]
                rate_hz = (n_t - 1) / span if span > 1e-9 else 0.0
            elif n_t == 1 and (t_now - self.call_timestamps[0]) <= 1.2:
                rate_hz = 1.0
            else:
                rate_hz = 0.0

            if self.exec_times_ms:
                vals = list(self.exec_times_ms)
                mean_ms = sum(vals) / len(vals)
                s_vals = sorted(vals)
                idx = min(len(s_vals) - 1, int(0.95 * (len(s_vals) - 1)))
                p95_ms = s_vals[idx]
            else:
                mean_ms = 0.0
                p95_ms = 0.0

            if self.render_times_ms:
                r_vals = list(self.render_times_ms)
                mean_render_ms = sum(r_vals) / len(r_vals)
            else:
                mean_render_ms = 0.0
            return rate_hz, mean_ms, p95_ms, self.total_calls, mean_render_ms


class FlowMetricsViewer:
    """
    Real-Time Flow, Concurrency & Backpressure Diagnostics Window (Core Tool).

    Inspects active modules in MODULES_REGISTRY:
      - Normalized CPU % (0-100% across all cores) and Host CPU %,
      - Actual bounded queue capacities (buffer_size / _maxsize / max_queue_size),
      - Downstream backpressure holds via `is_outputs_ready()`,
      - Module self-readiness via `is_ready()`,
      - In-place colored status rows showing real-time flowing / waiting / saturated states.
    """

    def __init__(self) -> None:
        self.window_tag = "core_flow_metrics_viewer_win"
        self.table_tag = "core_flow_metrics_table"

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._refresh_interval_s: float = 0.5
        self._probe_enabled: bool = True

        self._probes: Dict[str, _ModuleProbe] = {}
        self._row_UUIDs: List[str] = []
        self._cell_tags: Dict[Tuple[str, str], int | str] = {}

        self._main_proc: Any = None
        self._child_procs: Dict[int, Any] = {}
        if _PSUTIL is not None:
            try:
                self._main_proc = _PSUTIL.Process(os.getpid())
                self._main_proc.cpu_percent(interval=None)
            except Exception:
                self._main_proc = None

    def show(self) -> None:
        """Creates or focuses the Flow Metrics Viewer window."""
        if dpg.does_item_exist(self.window_tag):
            dpg.show_item(self.window_tag)
            dpg.focus_item(self.window_tag)
            self._start_monitor()
            return

        with dpg.window(
            label="Flow & Concurrency Metrics (Real-Time Diagnostics)",
            tag=self.window_tag,
            width=1080,
            height=600,
            pos=[90, 80],
            on_close=self._on_close,
        ):
            with dpg.group(horizontal=True):
                dpg.add_text("UI FPS: --", tag=f"{self.window_tag}_fps")
                dpg.add_spacer(width=12)
                dpg.add_text("CPU: --%", tag=f"{self.window_tag}_cpu")
                dpg.add_spacer(width=12)
                dpg.add_text("RAM: -- MB", tag=f"{self.window_tag}_ram")
                dpg.add_spacer(width=12)
                dpg.add_text("Modules: 0", tag=f"{self.window_tag}_mod_count")
                dpg.add_spacer(width=12)
                dpg.add_text("Active Links: 0", tag=f"{self.window_tag}_link_count")
                dpg.add_spacer(width=12)
                dpg.add_text("Worker Processes: 0", tag=f"{self.window_tag}_worker_count")

            dpg.add_separator()

            with dpg.group(horizontal=True):
                dpg.add_slider_float(
                    label="Refresh (s)",
                    default_value=self._refresh_interval_s,
                    min_value=0.1,
                    max_value=2.0,
                    width=130,
                    callback=self._on_interval_change,
                )
                dpg.add_checkbox(
                    label="Instrument input_cb (Hz & exec ms)",
                    default_value=self._probe_enabled,
                    callback=self._on_toggle_probe,
                )
                dpg.add_button(
                    label="Export Snapshot (JSON)",
                    callback=self._on_export_snapshot,
                )
                dpg.add_text("", tag=f"{self.window_tag}_export_status", color=COLOR_OK)

            with dpg.group(horizontal=True):
                dpg.add_text("Visual Legend:", color=COLOR_MUTED)
                dpg.add_text("READY / FLOWING", color=COLOR_OK)
                dpg.add_text("|", color=COLOR_MUTED)
                dpg.add_text("BUSY / QUEUE FILL", color=COLOR_BUSY)
                dpg.add_text("|", color=COLOR_MUTED)
                dpg.add_text("WAITING (DOWNSTREAM BLOCKED) / SATURATED", color=COLOR_WAITING)

            dpg.add_separator()

            with dpg.table(
                tag=self.table_tag,
                header_row=True,
                resizable=True,
                policy=dpg.mvTable_SizingStretchProp,
                borders_outerH=True,
                borders_innerV=True,
                borders_innerH=True,
                borders_outerV=True,
                row_background=True,
                scrollY=True,
                height=-1,
            ):
                dpg.add_table_column(label="UUID", init_width_or_weight=0.6)
                dpg.add_table_column(label="Module Label (Class)", init_width_or_weight=1.5)
                dpg.add_table_column(label="Flow State (Ready / Waiting)", init_width_or_weight=1.3)
                dpg.add_table_column(label="Links Out", init_width_or_weight=0.5)
                dpg.add_table_column(label="In Rate (Hz)", init_width_or_weight=0.7)
                dpg.add_table_column(label="input_cb (ms)", init_width_or_weight=0.9)
                dpg.add_table_column(label="Worker Status", init_width_or_weight=0.9)
                dpg.add_table_column(label="In Queue", init_width_or_weight=0.7)
                dpg.add_table_column(label="Drop Policy", init_width_or_weight=0.7)
                dpg.add_table_column(label="Telemetry / Drops", init_width_or_weight=1.3)

        self._start_monitor()

    def _on_interval_change(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        try:
            self._refresh_interval_s = max(0.1, float(app_data))
        except Exception:
            pass

    def _on_toggle_probe(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        self._probe_enabled = bool(app_data)
        if not self._probe_enabled:
            self._detach_all_probes()

    def _on_close(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        self._stop_event.set()
        self._detach_all_probes()

    def _detach_all_probes(self) -> None:
        registry_snapshot = dict(MODULES_REGISTRY)
        for uid, probe in list(self._probes.items()):
            mod = registry_snapshot.get(uid)
            if mod is not None:
                probe.detach(mod)
        self._probes.clear()

    def _start_monitor(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="FlowMetricsMonitor",
        )
        self._thread.start()

    def _discover_processor(self, mod: Any) -> Optional[Any]:
        """Finds any ProcessingBase or ViewportProcessBase attached to a module."""
        if hasattr(mod, "_in_queue") and hasattr(mod, "_process"):
            return mod
        for attr in ("proc", "processor", "_proc", "_processor", "worker"):
            cand = getattr(mod, attr, None)
            if cand is not None and (hasattr(cand, "_in_queue") or hasattr(cand, "_process")):
                return cand
        try:
            for val in vars(mod).values():
                if val is not None and hasattr(val, "_in_queue") and hasattr(val, "_process"):
                    return val
        except Exception:
            pass
        return None

    def _collect_process_usage(self) -> Tuple[float, float, float, int]:
        """
        Returns (proc_cpu_norm_pct, sys_cpu_pct, total_rss_mb, worker_proc_count).
        All CPU values are normalized to 0-100% across all cores.
        """
        if self._main_proc is None or _PSUTIL is None:
            return 0.0, 0.0, 0.0, 0
        try:
            cores = os.cpu_count() or 1
            cpu_raw_total = float(self._main_proc.cpu_percent(interval=None))
            rss_total = float(self._main_proc.memory_info().rss)
            children = self._main_proc.children(recursive=True)
            active_pids = set()
            for ch in children:
                active_pids.add(ch.pid)
                if ch.pid not in self._child_procs:
                    self._child_procs[ch.pid] = ch
                    try:
                        ch.cpu_percent(interval=None)
                    except Exception:
                        pass
                else:
                    cached = self._child_procs[ch.pid]
                    try:
                        cpu_raw_total += float(cached.cpu_percent(interval=None))
                        rss_total += float(cached.memory_info().rss)
                    except Exception:
                        pass
            for stale_pid in list(self._child_procs.keys()):
                if stale_pid not in active_pids:
                    self._child_procs.pop(stale_pid, None)

            # Normalize process tree CPU across all logical cores so it never exceeds 100%
            proc_cpu_norm = max(0.0, min(100.0, cpu_raw_total / cores))

            # System-wide overall CPU (0-100%)
            sys_cpu = 0.0
            try:
                sys_cpu = float(_PSUTIL.cpu_percent(interval=None))
            except Exception:
                sys_cpu = proc_cpu_norm

            return proc_cpu_norm, sys_cpu, rss_total / (1024.0 * 1024.0), len(active_pids)
        except Exception:
            return 0.0, 0.0, 0.0, 0

    def _extract_queue_details(self, mod: Any, proc: Optional[Any]) -> Tuple[str, str, float, int, int]:
        """
        Extracts actual queue fill string, drop policy, fill ratio, qsize, and maxsize.
        Checks buffer_size, _maxsize, and max_queue_size across proc and mod.
        """
        target = proc if proc is not None else mod
        in_q = getattr(target, "_in_queue", None)
        if in_q is None:
            return "N/A", "N/A", 0.0, 0, 0

        # Resolve real maximum queue capacity
        max_q: Optional[int] = None
        # 1. mp.Queue internal _maxsize attribute
        if hasattr(in_q, "_maxsize") and isinstance(in_q._maxsize, int) and in_q._maxsize > 0:
            max_q = in_q._maxsize
        # 2. buffer_size on proc or mod (ProcessingBase convention)
        if max_q is None and hasattr(target, "buffer_size") and isinstance(target.buffer_size, int) and target.buffer_size > 0:
            max_q = target.buffer_size
        if max_q is None and hasattr(mod, "buffer_size") and isinstance(mod.buffer_size, int) and mod.buffer_size > 0:
            max_q = mod.buffer_size
        # 3. max_queue_size attribute
        if max_q is None and hasattr(mod, "max_queue_size") and isinstance(mod.max_queue_size, int) and mod.max_queue_size > 0:
            max_q = mod.max_queue_size
        if max_q is None and hasattr(target, "max_queue_size") and isinstance(target.max_queue_size, int) and target.max_queue_size > 0:
            max_q = target.max_queue_size
        if max_q is None or max_q <= 0:
            max_q = 1

        # Resolve current queue size (prefer target.queue_size() so worker-side drop_oldest buffers are included)
        q_size = 0
        q_size_str = "0"
        q_fn = getattr(target, "queue_size", None)
        try:
            q_val = int(q_fn()) if callable(q_fn) else -1
            if q_val >= 0:
                q_size = q_val
            else:
                q_size = in_q.qsize()
            q_size_str = str(q_size)
        except (NotImplementedError, AttributeError, Exception):
            try:
                q_size = 1 if not in_q.empty() else 0
                q_size_str = f"~{q_size}"
            except Exception:
                q_size = 0
                q_size_str = "?"

        fill_ratio = (q_size / max_q) if max_q > 0 else 0.0
        q_fill_str = f"{q_size_str} / {max_q}"

        raw_policy = getattr(
            target,
            "_drop_policy",
            getattr(target, "drop_policy", getattr(mod, "drop_policy", "drop_new")),
        )
        if isinstance(raw_policy, bool):
            policy_str = "drop_oldest" if raw_policy else "drop_new"
        else:
            policy_str = str(raw_policy or "drop_new")

        return q_fill_str, policy_str, fill_ratio, q_size, max_q

    def _collect_snapshot(self) -> Dict[str, Any]:
        """Gathers a point-in-time snapshot of all registered modules and system state."""
        now = time.perf_counter()
        fps = float(dpg.get_frame_rate()) if dpg.is_dearpygui_running() else 0.0
        registry_snapshot = dict(MODULES_REGISTRY)

        # Clean up probes for removed modules
        for uid in list(self._probes.keys()):
            if uid not in registry_snapshot:
                self._probes.pop(uid, None)

        total_links = 0
        worker_count = 0
        modules_info: List[Dict[str, Any]] = []

        for uuid_key, mod in registry_snapshot.items():
            uid_str = str(uuid_key)
            cls_name = type(mod).__name__
            label = getattr(mod, "label", cls_name)

            if self._probe_enabled:
                probe = self._probes.get(uid_str)
                if probe is None:
                    probe = _ModuleProbe(uid_str)
                    self._probes[uid_str] = probe
                probe.attach(mod)
                in_rate_hz, mean_exec_ms, p95_exec_ms, total_calls, mean_render_ms = probe.stats(now)
            else:
                in_rate_ms_Attr = getattr(mod, "last_exec_ms", 0.0)
                in_rate_hz, mean_exec_ms, p95_exec_ms, total_calls, mean_render_ms = (
                    0.0,
                    float(in_rate_ms_Attr),
                    0.0,
                    0,
                    0.0,
                )

            # Fallback if module tracks its own rate (e.g. source generators or threaded viewers)
            mod_rate = getattr(mod, "measured_rate_hz", None)
            if in_rate_hz == 0.0 and mod_rate is not None:
                try:
                    in_rate_hz = float(mod_rate)
                except Exception:
                    pass
            if in_rate_hz == 0.0:
                read_last_t = getattr(mod, "_read_last_time", None)
                if isinstance(read_last_t, (int, float)) and (time.time() - float(read_last_t)) <= 2.0:
                    in_rate_hz = float(
                        getattr(mod, "_read_smoothed_fps", 0.0)
                        or getattr(mod, "_read_raw_fps", 0.0)
                    )

            # Count active outgoing links
            mod_links = 0
            connections = getattr(mod, "connections", None)
            if isinstance(connections, dict):
                for targets in connections.values():
                    if isinstance(targets, (list, dict)):
                        mod_links += len(targets)
            total_links += mod_links

            # Downstream backpressure check via WindowBase.is_outputs_ready()
            outputs_ready = True
            if hasattr(mod, "is_outputs_ready") and callable(mod.is_outputs_ready):
                try:
                    outputs_ready = bool(mod.is_outputs_ready())
                except Exception:
                    outputs_ready = True

            # Inspect attached worker process if any
            proc = self._discover_processor(mod)
            worker_status = "UI-Thread"
            worker_color = COLOR_STANDBY
            self_ready = True

            if proc is not None and hasattr(proc, "is_ready") and callable(proc.is_ready):
                try:
                    self_ready = bool(proc.is_ready())
                except Exception:
                    self_ready = True
            elif hasattr(mod, "is_ready") and callable(mod.is_ready):
                try:
                    self_ready = bool(mod.is_ready())
                except Exception:
                    self_ready = True

            q_fill_str, drop_policy, fill_ratio, q_size, max_q = self._extract_queue_details(mod, proc)

            if proc is not None:
                p_obj = getattr(proc, "_process", None)
                if p_obj is not None:
                    try:
                        if p_obj.is_alive():
                            worker_status = f"Alive (PID {p_obj.pid})"
                            worker_color = COLOR_OK
                            worker_count += 1
                        else:
                            worker_status = f"Stopped (exit={p_obj.exitcode})"
                            worker_color = COLOR_WAITING
                    except Exception:
                        worker_status = "Error"
                        worker_color = COLOR_WAITING
                else:
                    worker_status = "Idle"
                    worker_color = COLOR_MUTED

            # Determine Flow State and its visual alert color
            if mod_links > 0 and not outputs_ready:
                flow_state = "WAITING (Blocked Downstream)"
                flow_color = COLOR_WAITING
            elif not self_ready:
                flow_state = "SATURATED (Not Ready)"
                flow_color = COLOR_WAITING
            elif max_q > 0 and fill_ratio >= 1.0:
                flow_state = "QUEUE FULL"
                flow_color = COLOR_WAITING
            elif max_q > 0 and fill_ratio >= 0.5:
                flow_state = "BUSY (Queue Filling)"
                flow_color = COLOR_BUSY
            elif proc is not None:
                flow_state = "FLOWING (Worker)"
                flow_color = COLOR_OK
            elif in_rate_hz > 0:
                flow_state = "FLOWING (UI-Thread)"
                flow_color = COLOR_OK
            else:
                flow_state = "READY (Standby)"
                flow_color = COLOR_STANDBY

            # Color for queue column
            if q_fill_str == "N/A":
                q_color = COLOR_MUTED
            elif fill_ratio >= 1.0:
                q_color = COLOR_WAITING
            elif fill_ratio >= 0.5:
                q_color = COLOR_BUSY
            else:
                q_color = COLOR_OK

            # Inspect custom benchmark/telemetry attributes if exposed by module
            lat_ms = getattr(mod, "last_latency_ms", None)
            drops = getattr(mod, "dropped_count", None)
            custom_parts: List[str] = []
            if mean_render_ms > 0.0:
                disp_fps = float(
                    getattr(mod, "_display_smoothed_fps", 0.0)
                    or getattr(mod, "_display_raw_fps", 0.0)
                )
                custom_parts.append(f"tex={mean_render_ms:.2f}ms ({disp_fps:.0f}fps)")
            if lat_ms is not None:
                try:
                    custom_parts.append(f"lat={float(lat_ms):.2f}ms")
                except Exception:
                    pass
            if drops is not None:
                try:
                    custom_parts.append(f"drops={int(drops)}")
                except Exception:
                    pass
            if not custom_parts and total_calls > 0:
                custom_parts.append(f"calls={total_calls}")

            has_drops = bool(drops is not None and int(drops) > 0)
            telemetry_color = COLOR_WAITING if has_drops else COLOR_MUTED

            links_color = COLOR_WAITING if (mod_links > 0 and not outputs_ready) else COLOR_WHITE

            row_data = {
                "uuid": (uid_str, COLOR_MUTED),
                "label": (f"{label} ({cls_name})", COLOR_WHITE),
                "flow_state": (flow_state, flow_color),
                "links_out": (f"{mod_links} {'(HOLD)' if not outputs_ready and mod_links > 0 else ''}", links_color),
                "in_rate_hz": (f"{in_rate_hz:.1f} Hz", COLOR_WHITE),
                "exec_ms": (f"{mean_exec_ms:.2f} / {p95_exec_ms:.2f}", COLOR_WHITE),
                "worker_status": (worker_status, worker_color),
                "in_queue": (q_fill_str, q_color),
                "drop_policy": (drop_policy, COLOR_WHITE),
                "telemetry": (", ".join(custom_parts) if custom_parts else "-", telemetry_color),
            }

            modules_info.append(
                {
                    "uuid": uid_str,
                    "label": f"{label} ({cls_name})",
                    "flow_state": flow_state,
                    "links_out": mod_links,
                    "in_rate_hz": round(in_rate_hz, 2),
                    "exec_mean_ms": round(mean_exec_ms, 3),
                    "exec_p95_ms": round(p95_exec_ms, 3),
                    "worker_status": worker_status,
                    "in_queue": q_fill_str,
                    "drop_policy": drop_policy,
                    "telemetry": ", ".join(custom_parts) if custom_parts else "-",
                    "row_data": row_data,
                }
            )

        proc_cpu_norm, sys_cpu, mem_mb, child_count = self._collect_process_usage()

        return {
            "timestamp_epoch": time.time(),
            "ui_fps": round(fps, 1),
            "module_count": len(modules_info),
            "active_links": total_links,
            "worker_processes": max(worker_count, child_count),
            "proc_cpu_percent": round(proc_cpu_norm, 1),
            "sys_cpu_percent": round(sys_cpu, 1),
            "memory_rss_mb": round(mem_mb, 1),
            "modules": modules_info,
        }

    def _rebuild_table_rows(self, modules: List[Dict[str, Any]]) -> None:
        if not dpg.does_item_exist(self.table_tag):
            return
        children = dpg.get_item_children(self.table_tag, 1)
        if children:
            for row_id in children:
                dpg.delete_item(row_id)

        self._cell_tags.clear()
        self._row_UUIDs = [m["uuid"] for m in modules]
        cols = (
            "uuid",
            "label",
            "flow_state",
            "links_out",
            "in_rate_hz",
            "exec_ms",
            "worker_status",
            "in_queue",
            "drop_policy",
            "telemetry",
        )
        for m in modules:
            uid = m["uuid"]
            with dpg.table_row(parent=self.table_tag):
                for col in cols:
                    txt_id = dpg.add_text("")
                    self._cell_tags[(uid, col)] = txt_id

    def _update_ui(self, snap: Dict[str, Any]) -> None:
        if not dpg.does_item_exist(self.window_tag):
            return

        dpg.set_value(f"{self.window_tag}_fps", f"UI FPS: {snap['ui_fps']:.1f}")
        dpg.set_value(
            f"{self.window_tag}_cpu",
            f"CPU: {snap['proc_cpu_percent']:.1f}% (Host: {snap['sys_cpu_percent']:.1f}%)",
        )
        dpg.set_value(f"{self.window_tag}_ram", f"RAM: {snap['memory_rss_mb']:.1f} MB")
        dpg.set_value(f"{self.window_tag}_mod_count", f"Modules: {snap['module_count']}")
        dpg.set_value(f"{self.window_tag}_link_count", f"Active Links: {snap['active_links']}")
        dpg.set_value(f"{self.window_tag}_worker_count", f"Worker Processes: {snap['worker_processes']}")

        modules = snap["modules"]
        current_uuids = [m["uuid"] for m in modules]
        if current_uuids != self._row_UUIDs:
            self._rebuild_table_rows(modules)

        for m in modules:
            uid = m["uuid"]
            row_data = m["row_data"]
            for col, (text_val, color_val) in row_data.items():
                tag = self._cell_tags.get((uid, col))
                if tag is not None and dpg.does_item_exist(tag):
                    dpg.set_value(tag, text_val)
                    if color_val is not None:
                        dpg.configure_item(tag, color=color_val)

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            if not dpg.is_dearpygui_running() or not dpg.does_item_exist(self.window_tag):
                break
            try:
                if dpg.is_item_shown(self.window_tag):
                    snap = self._collect_snapshot()
                    self._update_ui(snap)
            except Exception as exc:
                logger.debug(f"FlowMetricsViewer update error: {exc}")

            self._stop_event.wait(self._refresh_interval_s)

    def _on_export_snapshot(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        try:
            snap = self._collect_snapshot()
            out_dir: Path = LOGS_DIR / "benchmarks"
            out_dir.mkdir(parents=True, exist_ok=True)
            filename = out_dir / f"flow_metrics_snapshot_{int(time.time())}.json"
            clean_snap = dict(snap)
            # Remove non-serializable UI tuples from export
            clean_modules = []
            for m in snap["modules"]:
                cm = dict(m)
                cm.pop("row_data", None)
                clean_modules.append(cm)
            clean_snap["modules"] = clean_modules

            with open(filename, "w", encoding="utf-8") as f:
                json.dump(clean_snap, f, indent=2)
            if dpg.does_item_exist(f"{self.window_tag}_export_status"):
                dpg.set_value(f"{self.window_tag}_export_status", f"Saved: {filename.name}")
            logger.info(f"Exported flow metrics snapshot to {filename}")
        except Exception as exc:
            logger.error(f"Failed to export flow metrics snapshot: {exc}")


flow_metrics_viewer: FlowMetricsViewer = FlowMetricsViewer()

