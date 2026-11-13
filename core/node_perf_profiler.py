from __future__ import annotations

import csv
import json
import os
import platform
import sys
import threading
import time
import types
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from core.file_explorer import file_explorer
from core.module_registry import MODULES_REGISTRY
from core.paths import LOGS_DIR


# ----------------------------------------------------------------------------
# Cross-Platform Memory Inspection (Windows ctypes psapi, Linux /proc, macOS, psutil)
# ----------------------------------------------------------------------------
_HAS_PSUTIL = False
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    pass

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]


def _get_process_memory_mb(pid: int) -> Tuple[float, float]:
    """
    Return (rss_mb, peak_mb) for a given process PID.
    Cross-platform: Windows (psapi.dll via ctypes), Linux (/proc/{pid}/status),
    macOS/BSD (getrusage fallback), and psutil when installed.
    Zero external dependencies required.
    """
    if pid <= 0:
        return 0.0, 0.0

    # 1. psutil fast-path if available
    if _HAS_PSUTIL:
        try:
            p = psutil.Process(pid)
            mem = p.memory_info()
            rss_mb = mem.rss / (1024.0 * 1024.0)
            peak_mb = getattr(mem, "peak_wset", getattr(mem, "vms", mem.rss)) / (1024.0 * 1024.0)
            return float(rss_mb), float(max(rss_mb, peak_mb))
        except Exception:
            pass

    # 2. Windows native (psapi.dll via ctypes)
    if sys.platform == "win32":
        try:
            # PROCESS_QUERY_INFORMATION (0x0400) | PROCESS_VM_READ (0x0010)
            handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
            if handle:
                try:
                    pmc = _PROCESS_MEMORY_COUNTERS()
                    pmc.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
                    if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                        rss_mb = pmc.WorkingSetSize / (1024.0 * 1024.0)
                        peak_mb = pmc.PeakWorkingSetSize / (1024.0 * 1024.0)
                        return float(rss_mb), float(peak_mb)
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

    # 3. Linux native (/proc/{pid}/status or /proc/{pid}/statm)
    elif sys.platform.startswith("linux"):
        try:
            status_path = f"/proc/{pid}/status"
            if os.path.exists(status_path):
                rss_kb = 0.0
                peak_kb = 0.0
                with open(status_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            rss_kb = float(line.split()[1])
                        elif line.startswith("VmHWM:") or line.startswith("VmPeak:"):
                            peak_kb = max(peak_kb, float(line.split()[1]))
                if rss_kb > 0.0:
                    return float(rss_kb / 1024.0), float(max(rss_kb, peak_kb) / 1024.0)

            # Fallback to /proc/{pid}/statm
            statm_path = f"/proc/{pid}/statm"
            if os.path.exists(statm_path):
                with open(statm_path, "r", encoding="utf-8") as f:
                    parts = f.read().split()
                    if len(parts) >= 2:
                        pages = int(parts[1])
                        page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
                        rss_mb = (pages * page_size) / (1024.0 * 1024.0)
                        return float(rss_mb), float(rss_mb)
        except Exception:
            pass

    # 4. POSIX fallback (e.g. Darwin/macOS)
    try:
        import resource
        if pid == os.getpid():
            usage = resource.getrusage(resource.RUSAGE_SELF)
            divisor = (1024.0 * 1024.0) if sys.platform == "darwin" else 1024.0
            rss_mb = usage.ru_maxrss / divisor
            return float(rss_mb), float(rss_mb)
    except Exception:
        pass

    return 0.0, 0.0


def _estimate_module_buffers_mb(obj: Any) -> float:
    """
    Estimate internal data buffer memory (NumPy arrays, ring buffers, sample dicts)
    held in memory by a module instance. Cross-platform.
    """
    if obj is None:
        return 0.0

    # Fast-path: module defines its own exact memory reporter
    mem_fn = getattr(obj, "get_memory_bytes", None)
    if callable(mem_fn):
        try:
            return float(mem_fn() / (1024.0 * 1024.0))
        except Exception:
            pass
    mem_prop = getattr(obj, "memory_bytes", None)
    if isinstance(mem_prop, (int, float)):
        return float(mem_prop / (1024.0 * 1024.0))

    # Generic object graph scanner for NumPy arrays & collections
    seen: Set[int] = set()
    total_bytes = 0

    def _scan(val: Any, depth: int) -> None:
        nonlocal total_bytes
        if depth <= 0 or val is None:
            return
        v_id = id(val)
        if v_id in seen:
            return
        seen.add(v_id)

        if isinstance(val, np.ndarray):
            total_bytes += int(val.nbytes)
            return

        if isinstance(val, (bytes, bytearray)):
            total_bytes += len(val)
            return

        if isinstance(val, dict):
            for k, v in val.items():
                _scan(k, depth - 1)
                _scan(v, depth - 1)
            return

        if isinstance(val, (list, tuple, deque, set)):
            for item in val:
                _scan(item, depth - 1)
            return

        if hasattr(val, "__dict__"):
            skip_attrs = {
                "_profiler", "node_editor", "connections", "master",
                "parent", "_handle_theme", "_thread", "_poll_thread",
                "_process", "proc",
            }
            for k, v in val.__dict__.items():
                if k not in skip_attrs and not k.startswith("_tag_") and not k.startswith("btn_"):
                    _scan(v, depth - 1)

    try:
        _scan(obj, depth=6)
    except Exception:
        pass

    return float(total_bytes / (1024.0 * 1024.0))


def _fmt_ms(val_ms: float) -> str:
    """Format milliseconds with 3 decimals for sub-0.1 ms values so microsecond hops are visible."""
    if 0.0 < abs(val_ms) < 0.1:
        return f"{val_ms:.3f} ms"
    return f"{val_ms:.2f} ms"

# Visual status palette (RGBA / RGB)
COLOR_FLOWING = (90, 240, 125, 255)      # Green: Active & healthy
COLOR_BUSY = (255, 210, 65, 255)         # Yellow: Filling queue / high load
COLOR_CONGESTED = (255, 75, 75, 255)     # Red: Queue full or downstream hold
COLOR_IDLE = (155, 160, 170, 255)        # Muted gray: No recent activity
COLOR_ACCENT = (110, 205, 255, 255)      # Cyan accent

ALL_PORTS_OPTION = "All Ports (Sum)"
SINK_NO_OUTPUT_OPTION = "(No Outputs - Sink)"
_OFFSCREEN = (-999999.0, -999999.0)

AXIS_PRIMARY = "Primary (Left)"
AXIS_SEC1 = "Secondary 1 (Right)"
AXIS_SEC2 = "Secondary 2 (Right)"
_AXIS_OPTIONS = [AXIS_PRIMARY, AXIS_SEC1, AXIS_SEC2]
_AXIS_SHORT = ["Default", "1", "2"]

_IMPLOT_COLORS: List[Tuple[int, int, int, int]] = [
    (76, 114, 176, 255),
    (221, 132, 82, 255),
    (85, 168, 104, 255),
    (196, 78, 82, 255),
    (129, 114, 179, 255),
    (147, 120, 96, 255),
    (218, 139, 195, 255),
    (140, 140, 140, 255),
    (204, 185, 116, 255),
    (100, 181, 205, 255),
]

_PRESET_RATES_BOTH = "Input & Output Rates (/s)"
_PRESET_IN_RATE = "Input Rate (/s)"
_PRESET_OUT_RATE = "Output Rate (/s)"
_PRESET_LATENCY = "In -> Out Latency (ms)"
_PRESET_SELF_EXEC = "Self Exec Time (ms)"
_PRESET_QUEUE = "Queue Fill (%)"
_PRESET_ALL_PORTS = "All Output Ports (/s)"
_PRESET_DUAL_AXIS = "Rates (/s) + Latency (ms) [2 Axes]"
_PRESET_CUSTOM = "Custom (Multi-Series)"

_PLOT_PRESETS = [
    _PRESET_RATES_BOTH,
    _PRESET_IN_RATE,
    _PRESET_OUT_RATE,
    _PRESET_LATENCY,
    _PRESET_SELF_EXEC,
    _PRESET_QUEUE,
    _PRESET_ALL_PORTS,
    _PRESET_DUAL_AXIS,
]


class _TrackedTargetProxy:
    """
    Transparent proxy around a downstream target module inside `source.connections[output_key]`.

    Intercepts `input_cb` calls to record exact per-port and per-link emission events
    and measure downstream execution duration without modifying module code.
    """

    __slots__ = ("_target", "_src_uid", "_out_key", "_tgt_uid", "_profiler", "_target_idx")

    def __init__(
        self,
        target: Any,
        src_uid: str,
        out_key: str,
        tgt_uid: str,
        profiler: Any,
        target_idx: int = 0,
    ) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_src_uid", src_uid)
        object.__setattr__(self, "_out_key", out_key)
        object.__setattr__(self, "_tgt_uid", tgt_uid)
        object.__setattr__(self, "_profiler", profiler)
        object.__setattr__(self, "_target_idx", target_idx)

    def input_cb(self, *args: Any, **kwargs: Any) -> Any:
        target = object.__getattribute__(self, "_target")
        profiler = object.__getattribute__(self, "_profiler")
        src_uid = object.__getattribute__(self, "_src_uid")
        out_key = object.__getattribute__(self, "_out_key")
        tgt_uid = object.__getattribute__(self, "_tgt_uid")
        target_idx = object.__getattribute__(self, "_target_idx")

        t_enter = time.perf_counter()
        profiler._on_proxy_emit_start(src_uid, out_key, tgt_uid, t_enter, target_idx)
        try:
            # Always invoke `target.input_cb` so any external probe (e.g. `FlowMetricsViewer`)
            # attached to `target.input_cb` remains in the call chain and never loses sync.
            return target.input_cb(*args, **kwargs)
        finally:
            t_exit = time.perf_counter()
            dt_ms = (t_exit - t_enter) * 1000.0
            profiler._on_proxy_emit_end(src_uid, out_key, tgt_uid, t_enter, t_exit, dt_ms, target_idx)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_target"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_target"), name, value)

    def __eq__(self, other: Any) -> bool:
        target = object.__getattribute__(self, "_target")
        if isinstance(other, _TrackedTargetProxy):
            return target == object.__getattribute__(other, "_target")
        return target == other

    def __hash__(self) -> int:
        return hash(object.__getattribute__(self, "_target"))

    def __repr__(self) -> str:
        return repr(object.__getattribute__(self, "_target"))


class _NodePerfRecord:
    """
    Per-node real-time telemetry accumulator and rolling time-series store.
    Only instantiated while the Flow Profiler or a Module Report Window is active.
    """

    def __init__(
        self,
        uid: str,
        label: str,
        cls_name: str,
        output_keys: List[str],
        default_tracked_port: Optional[str] = None,
    ) -> None:
        self.uid: str = uid
        self.label: str = label
        self.cls_name: str = cls_name
        self.lock = threading.Lock()

        # Selected output port for Out/s and In -> Out latency computation
        if default_tracked_port and default_tracked_port in output_keys:
            self.tracked_port: str = default_tracked_port
        elif output_keys:
            self.tracked_port = output_keys[0]
        else:
            self.tracked_port = SINK_NO_OUTPUT_OPTION
        self.user_selected_port: bool = False

        # Raw event timestamps for rate calculation (2.5s sliding window)
        self.in_timestamps: deque[float] = deque(maxlen=300)
        self.out_timestamps: deque[float] = deque(maxlen=300)
        self.port_out_timestamps: Dict[str, deque[float]] = {
            k: deque(maxlen=200) for k in output_keys
        }

        # Cumulative counters
        self.total_in: int = 0
        self.total_out: int = 0
        self.port_total_out: Dict[str, int] = {k: 0 for k in output_keys}

        # Last activity timestamps (perf_counter) for Heartbeat visual decay
        self.last_in_ts: float = 0.0
        self.last_out_ts: float = 0.0
        self.last_active_port_idx: int = 0
        self.port_last_emit_ts: Dict[str, float] = {k: 0.0 for k in output_keys}
        self.edge_last_emit_ts: Dict[Tuple[str, str], float] = {}

        # Execution & Per-Port Turnaround timing buffers (ms) — short window (16 samples) for responsive stats
        self.self_exec_ms: deque[float] = deque(maxlen=16)
        self.in_to_out_ms: deque[float] = deque(maxlen=16)
        self.port_in_to_out_ms: Dict[str, deque[float]] = {
            k: deque(maxlen=16) for k in output_keys
        }
        self.port_last_in_to_out_ms: Dict[str, float] = {k: 0.0 for k in output_keys}
        self.port_mean_turn_ms: Dict[str, float] = {k: 0.0 for k in output_keys}
        self.port_p95_turn_ms: Dict[str, float] = {k: 0.0 for k in output_keys}
        self.port_max_turn_ms: Dict[str, float] = {k: 0.0 for k in output_keys}
        self.last_self_ms: float = 0.0
        self.last_in_to_out_ms: float = 0.0

        # Tracking in-flight inputs and worker submissions (for exact internal module Exec time)
        self._in_enter_ts: float = 0.0
        self._in_active_depth: int = 0
        self._child_exec_accum_ms: float = 0.0
        self._pending_in_ts: deque[float] = deque(maxlen=64)
        self.port_pending_in_ts: Dict[str, deque[float]] = {
            k: deque(maxlen=64) for k in output_keys
        }
        self._worker_submit_ts: deque[float] = deque(maxlen=128)
        self._last_worker_t_sub: float = 0.0
        self._prev_worker_done_ts: float = 0.0
        self._curr_worker_done_ts: float = 0.0
        self._last_worker_port_end_ts: float = 0.0
        self._worker_burst_ports: Set[str] = set()
        self._worker_emit_child_ms: float = 0.0
        self._last_unconsumed_in_ts: float = 0.0
        self._last_counted_emit_ts: float = 0.0
        self._has_async_render: bool = False

        # Latest polled state (reflects `self.tracked_port`)
        self.in_rate_hz: float = 0.0
        self.out_rate_hz: float = 0.0
        self.port_rates_hz: Dict[str, float] = {k: 0.0 for k in output_keys}
        self.mean_self_ms: float = 0.0
        self.p95_self_ms: float = 0.0
        self.mean_turnaround_ms: float = 0.0
        self.p95_turnaround_ms: float = 0.0
        self.max_turnaround_ms: float = 0.0

        self.q_size: int = 0
        self.q_max: int = 1
        self.q_fill_pct: float = 0.0
        self.q_has_worker: bool = False
        self.drop_policy: str = "N/A"
        self.dropped_count: int = 0
        self.is_self_ready: bool = True
        self.is_downstream_ready: bool = True
        self.is_auto_triggered: bool = False
        self.is_waiting_val: float = 0.0
        self._saw_wait_since_last_sample: bool = False
        self.flow_state_text: str = "IDLE"
        self.flow_state_color: Tuple[int, int, int, int] = COLOR_IDLE

        # Rolling time-series history (~60s at 5 Hz = 300 samples)
        self.history_maxlen: int = 300
        self.ts_time: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_in_rate: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_out_rate: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_turnaround_ms: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_p95_ms: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_self_ms: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_queue_pct: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_waiting: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_mem_rss: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_mem_buffers: deque[float] = deque(maxlen=self.history_maxlen)
        self.ts_port_rates: Dict[str, deque[float]] = {
            k: deque(maxlen=self.history_maxlen) for k in output_keys
        }
        self.ts_port_turnaround_ms: Dict[str, deque[float]] = {
            k: deque(maxlen=self.history_maxlen) for k in output_keys
        }
        self.ts_port_p95_ms: Dict[str, deque[float]] = {
            k: deque(maxlen=self.history_maxlen) for k in output_keys
        }

        # Memory footprint metrics (cross-platform OS RSS + buffer estimations)
        self.mem_rss_mb: float = 0.0
        self.mem_peak_mb: float = 0.0
        self.mem_buffers_mb: float = 0.0
        self.mem_type_text: str = "N/A"
        self.mem_pid: int = 0
        self._last_mem_check_ts: float = 0.0
        self._last_stats_seq: Tuple[int, int, float, float] = (-1, -1, 0.0, 0.0)
        self._cached_worker: Any = None
        self._cached_worker_checked_ts: float = 0.0

    def ensure_port_registered(self, port_key: str) -> None:
        """Lazily initialize per-port structures if a port key was added dynamically."""
        if port_key not in self.port_out_timestamps:
            self.port_out_timestamps[port_key] = deque(maxlen=200)
            self.port_total_out[port_key] = 0
            self.port_last_emit_ts[port_key] = 0.0
            self.port_rates_hz[port_key] = 0.0
            self.port_in_to_out_ms[port_key] = deque(maxlen=16)
            self.port_last_in_to_out_ms[port_key] = 0.0
            self.port_mean_turn_ms[port_key] = 0.0
            self.port_p95_turn_ms[port_key] = 0.0
            self.port_max_turn_ms[port_key] = 0.0
            self.port_pending_in_ts[port_key] = deque(maxlen=64)
            n_hist = len(self.ts_time)
            self.ts_port_rates[port_key] = deque([0.0] * n_hist, maxlen=self.history_maxlen)
            self.ts_port_turnaround_ms[port_key] = deque([0.0] * n_hist, maxlen=self.history_maxlen)
            self.ts_port_p95_ms[port_key] = deque([0.0] * n_hist, maxlen=self.history_maxlen)

    def get_used_output_ports(self, inst: Any = None) -> List[str]:
        """Return output port keys that are connected to at least one target or have emitted packets."""
        all_ports = list(self.port_out_timestamps.keys())
        if not all_ports:
            return []
        conns = getattr(inst, "connections", {}) if inst is not None else {}
        used: List[str] = []
        for p_key in all_ports:
            targets = conns.get(p_key, []) if isinstance(conns, dict) else []
            p_cnt = self.port_total_out.get(p_key, 0)
            if targets or p_cnt > 0:
                used.append(p_key)
        return used

    def get_available_port_options(self, inst: Any = None) -> List[str]:
        all_ports = list(self.port_out_timestamps.keys())
        if not all_ports:
            return [SINK_NO_OUTPUT_OPTION]
        used = self.get_used_output_ports(inst)
        ports = used if used else all_ports
        if len(ports) == 1:
            return ports
        return ports + [ALL_PORTS_OPTION]

    def reset(self) -> None:
        with self.lock:
            self.in_timestamps.clear()
            self.out_timestamps.clear()
            for dq in self.port_out_timestamps.values():
                dq.clear()
            self.total_in = 0
            self.total_out = 0
            for k in self.port_total_out:
                self.port_total_out[k] = 0
            self.self_exec_ms.clear()
            self.in_to_out_ms.clear()
            for dq in self.port_in_to_out_ms.values():
                dq.clear()
            for k in self.port_last_in_to_out_ms:
                self.port_last_in_to_out_ms[k] = 0.0
                self.port_mean_turn_ms[k] = 0.0
                self.port_p95_turn_ms[k] = 0.0
                self.port_max_turn_ms[k] = 0.0
            self._pending_in_ts.clear()
            for dq in self.port_pending_in_ts.values():
                dq.clear()
            self._worker_submit_ts.clear()
            self._last_worker_t_sub = 0.0
            self._prev_worker_done_ts = 0.0
            self._curr_worker_done_ts = 0.0
            self._last_worker_port_end_ts = 0.0
            self._worker_burst_ports.clear()
            self._worker_emit_child_ms = 0.0
            self._last_unconsumed_in_ts = 0.0
            self.last_self_ms = 0.0
            self.last_in_to_out_ms = 0.0
            self.is_auto_triggered = False
            self.is_waiting_val = 0.0
            self._saw_wait_since_last_sample = False
            self.ts_time.clear()
            self.ts_in_rate.clear()
            self.ts_out_rate.clear()
            self.ts_turnaround_ms.clear()
            self.ts_p95_ms.clear()
            self.ts_self_ms.clear()
            self.ts_queue_pct.clear()
            self.ts_waiting.clear()
            self.ts_mem_rss.clear()
            self.ts_mem_buffers.clear()
            self.mem_peak_mb = self.mem_rss_mb
            self._last_stats_seq = (-1, -1, 0.0, 0.0)
            for dq in self.ts_port_rates.values():
                dq.clear()
            for dq in self.ts_port_turnaround_ms.values():
                dq.clear()
            for dq in self.ts_port_p95_ms.values():
                dq.clear()

    @staticmethod
    def _calc_rate(dq: deque[float], now: float, window_s: float = 2.0) -> float:
        while dq and (now - dq[0]) > window_s:
            dq.popleft()
        n = len(dq)
        if n >= 2:
            span = dq[-1] - dq[0]
            if span > 1e-6:
                # Decay to 0 if no event occurred in the last 1.2s
                if (now - dq[-1]) > max(1.2, span / max(1, n - 1) * 3.0):
                    return 0.0
                return (n - 1) / span
        elif n == 1 and (now - dq[0]) < 1.0:
            return 1.0
        return 0.0


class NodePerfProfilerMixin:
    """
    Mixin for NodeEditor providing:
      1. Live Flow Visualizer ("Heartbeat Mode") on the NodeEditor canvas (coloring
         nodes and wires as data is emitted, without coloring module windows).
      2. Lightweight click / right-click Telemetry Tooltip Popup with sparkline and
         live metrics (`Input/s`, `Output/s`, `Exec time ms`, `Queue`, `FPS`).
      3. Multi-instance Standalone Module Performance Report Windows (`View Report`)
         with customizable live scrolling plots and per-port diagnostics.
      4. Zero-overhead lifecycle: hooks and monitoring thread run ONLY while the
         Flow Visualizer mode is active or at least one Report Window is open.
    """

    node_map: Dict[int, Any]
    link_map: Dict[int, Tuple[int, int]]
    editor_tag: str | int
    winID: str
    UUID: str
    dimmed_node_theme: int
    dimmed_link_theme: int
    red_node_theme: int
    red_link_theme: int
    highlight_theme: int
    virtual_link_theme: int

    def _init_perf_profiler(self) -> None:
        """Initialize profiler state attributes (does NOT start threads or hooks)."""
        self.perf_mode_active: bool = False
        self.perf_heartbeat_enabled: bool = True
        self.perf_pulse_duration_s: float = 0.24
        self.perf_pulse_continuous: bool = True
        self.perf_show_virtual_links: bool = True

        self.perf_popup_tag: str = f"ne_perf_popup_{self.UUID}"
        self._perf_popup_node_id: Optional[int] = None
        self._perf_menu_tag: str = f"ne_perf_menu_{self.UUID}"
        self._perf_menu_badge_tag: str = f"ne_perf_badge_{self.UUID}"
        self._perf_menu_toggle_tag: str = f"ne_perf_toggle_{self.UUID}"
        self._perf_heartbeat_toggle_tag: str = f"ne_perf_hb_toggle_{self.UUID}"

        self._perf_records: Dict[str, _NodePerfRecord] = {}
        self._perf_report_windows: Dict[str, Dict[str, Any]] = {}
        self._perf_hooked_instances: Set[int] = set()
        self._perf_hooked_video_objs: List[Any] = []
        self._perf_stack_lock = threading.Lock()
        self._thread_call_stack: Dict[int, List[Dict[str, Any]]] = {}
        self._thread_source_prep_ms: Dict[int, float] = {}

        self._perf_applied_node_themes: Dict[int, int] = {}
        self._perf_applied_link_themes: Dict[int, int] = {}
        self._perf_virtual_links: Dict[Tuple[int, int], int] = {}
        self._perf_link_meta_cache: Dict[Tuple[int, int | str, int | str], Tuple[int, int, str, str, str, int]] = {}
        self._perf_canvas_theme_cache: Dict[Any, Tuple[int, int]] = {}
        self._perf_beat_phase: int = 0
        self._perf_last_hook_sig: Tuple[int, int] = (-1, -1)
        self._perf_last_hook_sync_ts: float = 0.0
        self._perf_last_needed_check_ts: float = 0.0
        self._perf_last_needed_result: bool = False

        self._perf_stop_event = threading.Event()
        self._perf_stop_event.set()
        self._perf_thread: Optional[threading.Thread] = None
        self._perf_t0: float = time.perf_counter()
        self._perf_last_history_ts: float = 0.0

    def _build_perf_profiler_menu(self) -> None:
        """Add the 'Profiling' checkbox and conditional 'Profiling' menu inside NodeEditor's menu bar."""
        dpg.add_checkbox(
            label="Profiling",
            tag=self._perf_menu_toggle_tag,
            default_value=False,
            callback=self._on_toggle_perf_mode,
        )
        with dpg.tooltip(parent=dpg.last_item()):
            dpg.add_text(
                "Enable real-time flow profiling:\n"
                "- Right-click any node for live telemetry & performance report\n"
                "- Optional Heartbeat visualization on the canvas\n"
                "- Zero CPU/memory overhead when unchecked",
                wrap=300,
            )

        with dpg.menu(label="Profiling", tag=self._perf_menu_tag, show=False):
            dpg.add_menu_item(
                label="Show Heartbeat",
                tag=self._perf_heartbeat_toggle_tag,
                check=True,
                default_value=self.perf_heartbeat_enabled,
                callback=self._on_toggle_perf_heartbeat,
            )
            with dpg.tooltip(parent=dpg.last_item()):
                dpg.add_text(
                    "Colorize active nodes and wires on the canvas as data flows.\n"
                    "Uncheck to keep standard node colors while profiling.",
                    wrap=280,
                )

            dpg.add_menu_item(
                label="Pulse on Continuous Streams",
                check=True,
                default_value=self.perf_pulse_continuous,
                callback=lambda s, a, u: setattr(self, "perf_pulse_continuous", bool(a)),
            )

            dpg.add_menu_item(
                label="Show Virtual Links (Link Out -> Link In)",
                check=True,
                default_value=self.perf_show_virtual_links,
                callback=self._on_toggle_perf_virtual_links,
            )

            with dpg.menu(label="Heartbeat Hold Duration"):
                for label_ms, val_s in [
                    ("Fast (150 ms)", 0.15),
                    ("Normal (240 ms)", 0.24),
                    ("Slow (450 ms)", 0.45),
                ]:
                    dpg.add_menu_item(
                        label=label_ms,
                        callback=lambda s, a, u: setattr(self, "perf_pulse_duration_s", float(u)),
                        user_data=val_s,
                    )

            dpg.add_separator()
            dpg.add_menu_item(
                label="Reset All Node Metrics",
                callback=self._on_reset_all_perf_metrics,
            )
            dpg.add_menu_item(
                label="Close All Reports",
                callback=self._close_all_report_windows,
            )

        dpg.add_text(
            "",
            tag=self._perf_menu_badge_tag,
            color=COLOR_FLOWING,
        )

    # =========================================================================
    # LIFECYCLE MANAGEMENT (ZERO OVERHEAD WHEN INACTIVE)
    # =========================================================================

    def _is_profiler_needed(self, force_dpg_check: bool = True) -> bool:
        """Return True if either Heartbeat mode is active or at least one Report Window is open."""
        now = time.perf_counter()
        if not force_dpg_check and (now - self._perf_last_needed_check_ts) < 0.50:
            if self.perf_mode_active or self._perf_popup_node_id is not None or self._perf_report_windows:
                return self._perf_last_needed_result
            return False

        self._perf_last_needed_check_ts = now
        editor_visible = dpg.does_item_exist(self.winID) and dpg.is_item_shown(self.winID)
        if self.perf_mode_active and editor_visible:
            self._perf_last_needed_result = True
            return True
        if self._perf_popup_node_id is not None and dpg.does_item_exist(self.perf_popup_tag) and dpg.is_item_shown(self.perf_popup_tag):
            self._perf_last_needed_result = True
            return True
        # Clean up any report windows that were closed externally
        for uid in list(self._perf_report_windows.keys()):
            info = self._perf_report_windows[uid]
            w_tag = info.get("win_tag")
            if not w_tag or not dpg.does_item_exist(w_tag) or not dpg.is_item_shown(w_tag):
                p_tag = info.get("axes_popup_tag")
                if p_tag and dpg.does_item_exist(p_tag):
                    try:
                        dpg.delete_item(p_tag)
                    except Exception:
                        pass
                if w_tag and dpg.does_item_exist(w_tag):
                    try:
                        dpg.delete_item(w_tag)
                    except Exception:
                        pass
                self._perf_report_windows.pop(uid, None)
        res = len(self._perf_report_windows) > 0
        self._perf_last_needed_result = res
        return res

    def _ensure_profiler_running(self) -> None:
        """Start the background telemetry monitor thread and attach hooks if not already running."""
        self._sync_module_hooks()
        if self._perf_thread is not None and self._perf_thread.is_alive():
            return
        self._perf_stop_event.clear()
        self._perf_t0 = time.perf_counter()
        self._perf_last_history_ts = 0.0
        self._perf_thread = threading.Thread(
            target=self._perf_monitor_loop,
            daemon=True,
            name="NodeEditorPerfProfiler",
        )
        self._perf_thread.start()
        logger.debug("NodeEditor Flow Profiler started.")

    def _check_and_stop_profiler_if_unused(self) -> None:
        """If neither Heartbeat mode nor any Report Window is active, detach all hooks & stop."""
        if not self._is_profiler_needed():
            was_active = (
                (self._perf_thread is not None and self._perf_thread.is_alive())
                or bool(getattr(self, "_perf_hooked_instances", None))
                or bool(getattr(self, "_perf_hooked_video_objs", None))
                or bool(getattr(self, "_perf_records", None))
                or not self._perf_stop_event.is_set()
            )
            self._perf_stop_event.set()
            if was_active:
                self._detach_all_module_hooks()
                self._restore_canvas_themes()
                self._perf_records.clear()
                logger.debug("NodeEditor Flow Profiler stopped and all hooks detached (zero overhead).")

    def _close_all_report_windows(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Close all standalone report windows."""
        for uid, info in list(self._perf_report_windows.items()):
            h_tag = info.get("handler_tag")
            if h_tag and dpg.does_item_exist(h_tag):
                try:
                    dpg.delete_item(h_tag)
                except Exception:
                    pass
            p_tag = info.get("axes_popup_tag")
            if p_tag and dpg.does_item_exist(p_tag):
                try:
                    dpg.delete_item(p_tag)
                except Exception:
                    pass
            w_tag = info.get("win_tag")
            if w_tag and dpg.does_item_exist(w_tag):
                try:
                    dpg.delete_item(w_tag)
                except Exception:
                    pass
        self._perf_report_windows.clear()
        self._check_and_stop_profiler_if_unused()

    def stop_all_perf_profiling(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Turn off Profiling mode, close all open Report Windows, and detach all hooks."""
        self.perf_mode_active = False
        if dpg.does_item_exist(self._perf_menu_toggle_tag):
            dpg.set_value(self._perf_menu_toggle_tag, False)
        if dpg.does_item_exist(self._perf_menu_tag):
            dpg.configure_item(self._perf_menu_tag, show=False)
        if dpg.does_item_exist(self._perf_menu_badge_tag):
            dpg.set_value(self._perf_menu_badge_tag, "")

        # Close popup
        self._perf_popup_node_id = None
        if dpg.does_item_exist(self.perf_popup_tag):
            dpg.configure_item(self.perf_popup_tag, show=False)

        # Close all standalone report windows
        self._close_all_report_windows()

        self._perf_stop_event.set()
        self._detach_all_module_hooks()
        self._restore_canvas_themes()
        self._perf_records.clear()

    def _on_editor_hidden_or_closed(self) -> None:
        """Called when the NodeEditor window itself is closed or hidden."""
        self._perf_popup_node_id = None
        if hasattr(self, "perf_popup_tag") and dpg.does_item_exist(self.perf_popup_tag):
            dpg.configure_item(self.perf_popup_tag, show=False)
        if getattr(self, "perf_mode_active", False):
            self.perf_mode_active = False
            if dpg.does_item_exist(self._perf_menu_toggle_tag):
                dpg.set_value(self._perf_menu_toggle_tag, False)
            if dpg.does_item_exist(self._perf_menu_tag):
                dpg.configure_item(self._perf_menu_tag, show=False)
            if dpg.does_item_exist(self._perf_menu_badge_tag):
                dpg.set_value(self._perf_menu_badge_tag, "")
            self._restore_canvas_themes()
        self._check_and_stop_profiler_if_unused()

    def _on_toggle_perf_mode(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Checkbox callback to toggle Profiling Mode."""
        if app_data is None and dpg.does_item_exist(self._perf_menu_toggle_tag):
            app_data = not self.perf_mode_active
            dpg.set_value(self._perf_menu_toggle_tag, app_data)

        self.perf_mode_active = bool(app_data)

        if dpg.does_item_exist(self._perf_menu_tag):
            dpg.configure_item(self._perf_menu_tag, show=self.perf_mode_active)

        if self.perf_mode_active:
            self._clear_highlights()
            if self.perf_heartbeat_enabled:
                self._init_heartbeat_canvas()
            else:
                self._restore_canvas_themes()
            self._ensure_profiler_running()
        else:
            if dpg.does_item_exist(self._perf_menu_badge_tag):
                dpg.set_value(self._perf_menu_badge_tag, "")
            self._perf_popup_node_id = None
            if dpg.does_item_exist(self.perf_popup_tag):
                dpg.configure_item(self.perf_popup_tag, show=False)
            self._restore_canvas_themes()
            self._check_and_stop_profiler_if_unused()

    def _on_toggle_perf_heartbeat(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Menu item callback to toggle Heartbeat canvas coloring while in Profiling Mode."""
        self.perf_heartbeat_enabled = bool(app_data)
        if self.perf_mode_active:
            if self.perf_heartbeat_enabled:
                self._init_heartbeat_canvas()
            else:
                self._restore_canvas_themes()

    def _on_toggle_perf_virtual_links(self, sender: Any, app_data: bool, user_data: Any = None) -> None:
        self.perf_show_virtual_links = bool(app_data)
        if not self.perf_show_virtual_links:
            self._clear_perf_virtual_links()

    def _on_reset_all_perf_metrics(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        self._perf_t0 = time.perf_counter()
        for rec in list(self._perf_records.values()):
            rec.reset()

    # =========================================================================
    # NON-INTRUSIVE HOOKS (CONNECTIONS PROXY + INPUT_CB + WORKER QUEUES)
    # =========================================================================

    @staticmethod
    def _instance_uid(inst: Any) -> str:
        return str(getattr(inst, "UUID", getattr(inst, "uuid", id(inst))))

    def _get_or_create_record(self, inst: Any) -> _NodePerfRecord:
        uid = self._instance_uid(inst)
        rec = self._perf_records.get(uid)
        out_keys = list(getattr(inst, "outputs", {}).keys())
        conns = getattr(inst, "connections", None)
        first_connected_port: Optional[str] = None
        if isinstance(conns, dict):
            for k in out_keys:
                if conns.get(k):
                    first_connected_port = k
                    break

        if rec is None:
            cls_name = type(inst).__name__
            label = str(getattr(inst, "label", cls_name))
            rec = _NodePerfRecord(
                uid,
                label,
                cls_name,
                out_keys,
                default_tracked_port=first_connected_port,
            )
            self._perf_records[uid] = rec
        else:
            rec.label = str(getattr(inst, "label", rec.cls_name))
            for k in out_keys:
                rec.ensure_port_registered(k)
            if (
                not rec.user_selected_port
                and first_connected_port
                and rec.port_total_out.get(rec.tracked_port, 0) == 0
            ):
                rec.tracked_port = first_connected_port
        return rec

    @staticmethod
    def _find_module_worker(inst: Any) -> Any:
        """Return the internal worker object (`ProcessingBase` or similar) if present on `inst`."""
        for attr in ("proc", "processor", "_worker", "worker", "_proc", "engine"):
            cand = getattr(inst, attr, None)
            if cand is not None and hasattr(cand, "_in_queue"):
                return cand
        if hasattr(inst, "_in_queue"):
            return inst
        return None

    def _get_live_queue_size_for_uid(self, uid: str) -> int:
        """Query the live `_in_queue.qsize()` of the module identified by `uid` right now."""
        for inst in self.node_map.values():
            if inst is not None and self._instance_uid(inst) == uid:
                worker = self._find_module_worker(inst)
                q_fn = getattr(worker, "queue_size", None) if worker is not None else None
                if callable(q_fn):
                    try:
                        q_val = int(q_fn())
                        if q_val >= 0:
                            return q_val
                    except Exception:
                        pass
                in_q = getattr(worker, "_in_queue", None) if worker is not None else None
                if in_q is not None:
                    try:
                        return max(0, int(in_q.qsize()))
                    except Exception:
                        try:
                            return 1 if not in_q.empty() else 0
                        except Exception:
                            return 0
                break
        return 0

    def _sync_module_hooks(self, force: bool = True) -> None:
        """Ensure all active nodes in `node_map` have telemetry hooks & connection proxies attached."""
        now = time.perf_counter()
        sig = (len(self.node_map), len(self.link_map))
        if not force and sig == self._perf_last_hook_sig and (now - self._perf_last_hook_sync_ts) < 1.0:
            return
        self._perf_last_hook_sig = sig
        self._perf_last_hook_sync_ts = now

        current_instances = list(self.node_map.values())
        for inst in current_instances:
            if inst is None:
                continue
            rec = self._get_or_create_record(inst)
            worker = self._find_module_worker(inst)
            rec._cached_worker = worker
            rec._cached_worker_checked_ts = now
            with rec.lock:
                rec.q_has_worker = worker is not None
                if worker is None:
                    rec._worker_submit_ts.clear()
            self._attach_instance_hook(inst)
            self._wrap_instance_connections(inst)

    def _attach_instance_hook(self, inst: Any) -> None:
        """
        Wrap module execution entry points so internal `Exec time` is measured accurately:
          - `inst.input_cb` for standard modules and `LinkOut`
          - `inst._dispatch` for `LinkIn`
          - `inst.frame_cb` / `inst.trigger_cb` + `video.read` for source modules (`VideoReaderWin`)
          - `worker.submit` for `ProcessingBase` worker processes
        """
        if getattr(inst, "KIND", "") in ("propagation_tester", "propagation_endpoint"):
            return
        profiler = self
        uid = self._instance_uid(inst)

        # Always ensure dynamic worker `submit` is hooked if the module created/rebuilt a worker
        worker = self._find_module_worker(inst)
        if worker is not None:
            p_obj = getattr(worker, "_process", None)
            if p_obj is not None and p_obj.is_alive():
                orig_submit = getattr(worker, "submit", None)
                if callable(orig_submit) and not getattr(orig_submit, "_perf_wrapped", False):
                    orig_sub_func = orig_submit.__func__ if hasattr(orig_submit, "__func__") else None

                    def _wrapped_submit(w_obj: Any, *args: Any, **kwargs: Any) -> Any:
                        t_sub = time.perf_counter()
                        res = (
                            orig_sub_func(w_obj, *args, **kwargs)
                            if orig_sub_func is not None
                            else orig_submit(*args, **kwargs)
                        )
                        if res is not False:
                            rec = profiler._perf_records.get(uid)
                            if rec is not None:
                                with rec.lock:
                                    rec._worker_submit_ts.append(t_sub)
                        return res

                    setattr(_wrapped_submit, "_perf_wrapped", True)
                    try:
                        setattr(worker, "_perf_orig_submit", orig_submit)
                        setattr(worker, "submit", types.MethodType(_wrapped_submit, worker))
                    except Exception:
                        pass

        inst_id = id(inst)
        if inst_id in self._perf_hooked_instances:
            return

        # 1. Hook `input_cb`
        orig_cb = getattr(inst, "input_cb", None)
        if callable(orig_cb) and not getattr(orig_cb, "_perf_wrapped", False):
            orig_func = orig_cb.__func__ if hasattr(orig_cb, "__func__") else None

            def _wrapped_input_cb(self_obj: Any, *args: Any, **kwargs: Any) -> Any:
                t_enter = time.perf_counter()
                pushed = profiler._on_direct_input_start(uid, t_enter)
                try:
                    if orig_func is not None:
                        return orig_func(self_obj, *args, **kwargs)
                    return orig_cb(*args, **kwargs)
                finally:
                    t_exit = time.perf_counter()
                    dt_ms = (t_exit - t_enter) * 1000.0
                    profiler._on_direct_input_end(uid, self_obj, t_enter, t_exit, dt_ms, pushed)

            setattr(_wrapped_input_cb, "_perf_wrapped", True)
            try:
                setattr(inst, "_perf_orig_input_cb", orig_cb)
                setattr(inst, "input_cb", types.MethodType(_wrapped_input_cb, inst))
            except Exception:
                pass

        # 2. Hook `_dispatch` (e.g. `_LinkInNode`) and source emission methods (`frame_cb`, `trigger_cb`)
        for m_name, is_input_event in (("_dispatch", True), ("frame_cb", False), ("trigger_cb", False)):
            orig_m = getattr(inst, m_name, None)
            if callable(orig_m) and not getattr(orig_m, "_perf_wrapped", False):
                orig_m_func = orig_m.__func__ if hasattr(orig_m, "__func__") else None

                def _make_source_wrapper(
                    _orig_m: Any, _orig_m_func: Any, _is_in: bool
                ) -> Callable[..., Any]:
                    def _wrapped_source_m(self_obj: Any, *args: Any, **kwargs: Any) -> Any:
                        t_enter = time.perf_counter()
                        profiler._on_source_method_start(uid, t_enter, record_input=_is_in)
                        try:
                            if _orig_m_func is not None:
                                return _orig_m_func(self_obj, *args, **kwargs)
                            return _orig_m(*args, **kwargs)
                        finally:
                            t_exit = time.perf_counter()
                            dt_ms = (t_exit - t_enter) * 1000.0
                            profiler._on_source_method_end(uid, t_enter, t_exit, dt_ms, is_auto=not _is_in)

                    setattr(_wrapped_source_m, "_perf_wrapped", True)
                    return _wrapped_source_m

                try:
                    setattr(inst, f"_perf_orig_{m_name}", orig_m)
                    setattr(
                        inst,
                        m_name,
                        types.MethodType(_make_source_wrapper(orig_m, orig_m_func, is_input_event), inst),
                    )
                except Exception:
                    pass

        # 3. Hook async texture/display work (e.g. `VideoViewerWin._update_texture` in its worker_thread)
        #    so `Exec time` reflects the real frame conversion + GPU texture upload time rather than
        #    only the ~0.04 ms pointer handoff in `input_cb`.
        orig_ut = getattr(inst, "_update_texture", None)
        if callable(orig_ut) and not getattr(orig_ut, "_perf_wrapped", False):
            orig_ut_func = orig_ut.__func__ if hasattr(orig_ut, "__func__") else None
            rec_init = self._get_or_create_record(inst)
            with rec_init.lock:
                rec_init._has_async_render = True

            def _wrapped_update_texture(self_obj: Any, *args: Any, **kwargs: Any) -> Any:
                t0 = time.perf_counter()
                try:
                    if orig_ut_func is not None:
                        return orig_ut_func(self_obj, *args, **kwargs)
                    return orig_ut(*args, **kwargs)
                finally:
                    dt_tex_ms = (time.perf_counter() - t0) * 1000.0
                    rec = profiler._perf_records.get(uid)
                    if rec is not None:
                        with rec.lock:
                            rec._has_async_render = True
                            total_exec_ms = dt_tex_ms + rec.last_self_ms
                            rec.last_in_to_out_ms = total_exec_ms
                            rec.in_to_out_ms.append(total_exec_ms)

            setattr(_wrapped_update_texture, "_perf_wrapped", True)
            try:
                setattr(inst, "_perf_orig__update_texture", orig_ut)
                setattr(inst, "_update_texture", types.MethodType(_wrapped_update_texture, inst))
            except Exception:
                pass

        # 4. If module uses a module-level `video.read()` helper (e.g. `VideoReaderWin`), measure decode time
        try:
            mod = sys.modules.get(type(inst).__module__)
            v_obj = getattr(mod, "video", None) if mod is not None else None
            orig_read = getattr(v_obj, "read", None) if v_obj is not None else None
            if callable(orig_read) and not getattr(orig_read, "_perf_wrapped", False):
                orig_read_func = orig_read.__func__ if hasattr(orig_read, "__func__") else None

                def _wrapped_video_read(v_self: Any, *args: Any, **kwargs: Any) -> Any:
                    t0 = time.perf_counter()
                    try:
                        if orig_read_func is not None:
                            return orig_read_func(v_self, *args, **kwargs)
                        return orig_read(*args, **kwargs)
                    finally:
                        t1 = time.perf_counter()
                        with profiler._perf_stack_lock:
                            profiler._thread_source_prep_ms[threading.get_ident()] = (t1 - t0) * 1000.0

                setattr(_wrapped_video_read, "_perf_wrapped", True)
                setattr(v_obj, "_perf_orig_read", orig_read)
                setattr(v_obj, "read", types.MethodType(_wrapped_video_read, v_obj))
                if v_obj not in self._perf_hooked_video_objs:
                    self._perf_hooked_video_objs.append(v_obj)
        except Exception:
            pass

        self._perf_hooked_instances.add(inst_id)

    def _wrap_instance_connections(self, src_inst: Any) -> None:
        """Ensure every target in `src_inst.connections[out_key]` is wrapped in `_TrackedTargetProxy`."""
        conns = getattr(src_inst, "connections", None)
        if not isinstance(conns, dict):
            return
        src_uid = self._instance_uid(src_inst)
        for out_key, targets in conns.items():
            if not isinstance(targets, list):
                continue
            for idx, tgt in enumerate(targets):
                if isinstance(tgt, _TrackedTargetProxy):
                    object.__setattr__(tgt, "_target_idx", idx)
                    continue
                tgt_uid = self._instance_uid(tgt)
                self._get_or_create_record(tgt)
                self._attach_instance_hook(tgt)
                targets[idx] = _TrackedTargetProxy(
                    tgt, src_uid, str(out_key), tgt_uid, self, target_idx=idx
                )

    def _detach_all_module_hooks(self) -> None:
        """Restore all original methods and unwrap all `_TrackedTargetProxy` entries."""
        all_instances = list(self.node_map.values()) + list(MODULES_REGISTRY.values())
        seen: Set[int] = set()
        for inst in all_instances:
            if inst is None or id(inst) in seen:
                continue
            seen.add(id(inst))

            # 1. Unwrap connections
            conns = getattr(inst, "connections", None)
            if isinstance(conns, dict):
                for out_key, targets in conns.items():
                    if isinstance(targets, list):
                        for idx, tgt in enumerate(targets):
                            if isinstance(tgt, _TrackedTargetProxy):
                                targets[idx] = object.__getattribute__(tgt, "_target")

            # 2. Restore original input_cb, render, and source methods
            for m_name, attr_orig in (
                ("input_cb", "_perf_orig_input_cb"),
                ("_dispatch", "_perf_orig__dispatch"),
                ("frame_cb", "_perf_orig_frame_cb"),
                ("trigger_cb", "_perf_orig_trigger_cb"),
                ("_update_texture", "_perf_orig__update_texture"),
            ):
                orig_m = getattr(inst, attr_orig, None)
                if orig_m is not None:
                    try:
                        setattr(inst, m_name, orig_m)
                        delattr(inst, attr_orig)
                    except Exception:
                        pass

            # 3. Restore worker.submit if wrapped
            worker = self._find_module_worker(inst)
            if worker is not None:
                orig_sub = getattr(worker, "_perf_orig_submit", None)
                if orig_sub is not None:
                    try:
                        setattr(worker, "submit", orig_sub)
                        delattr(worker, "_perf_orig_submit")
                    except Exception:
                        pass

        for v_obj in list(getattr(self, "_perf_hooked_video_objs", [])):
            orig_r = getattr(v_obj, "_perf_orig_read", None)
            if orig_r is not None:
                try:
                    setattr(v_obj, "read", orig_r)
                    delattr(v_obj, "_perf_orig_read")
                except Exception:
                    pass
        if hasattr(self, "_perf_hooked_video_objs"):
            self._perf_hooked_video_objs.clear()
        if hasattr(self, "_perf_stack_lock"):
            with self._perf_stack_lock:
                self._thread_call_stack.clear()
                self._thread_source_prep_ms.clear()

        self._perf_hooked_instances.clear()

    # =========================================================================
    # EVENT RECORDING CALLBACKS (EXACT INTERNAL MODULE EXEC TIME)
    # =========================================================================

    def _push_thread_frame(
        self, uid: str, t_enter: float, prep_ms: float = 0.0, from_proxy: bool = False
    ) -> Dict[str, Any]:
        tid = threading.get_ident()
        frame: Dict[str, Any] = {
            "uid": uid,
            "t_enter": t_enter,
            "child_ms": 0.0,
            "prep_ms": prep_ms,
            "ports": [],
            "port_self_ms": {},
            "from_proxy": from_proxy,
            "entered_cb": False,
        }
        with self._perf_stack_lock:
            stack = self._thread_call_stack.setdefault(tid, [])
            stack.append(frame)
        return frame

    def _pop_thread_frame(self, uid: str, dt_ms: float) -> Optional[Dict[str, Any]]:
        tid = threading.get_ident()
        with self._perf_stack_lock:
            stack = self._thread_call_stack.get(tid)
            if not stack:
                return None
            frame = stack.pop()
            if stack:
                stack[-1]["child_ms"] += dt_ms
            return frame

    def _peek_thread_frame(self, uid: str) -> Optional[Dict[str, Any]]:
        tid = threading.get_ident()
        with self._perf_stack_lock:
            stack = self._thread_call_stack.get(tid)
            if stack:
                for f in reversed(stack):
                    if f["uid"] == uid:
                        return f
        return None

    def _record_completed_sync_frame(
        self, rec: _NodePerfRecord, frame: Optional[Dict[str, Any]], dt_ms: float, is_link_out: bool = False
    ) -> None:
        """Compute and store pure internal module `Exec time` (`dt_ms - child_ms + prep_ms`)."""
        prep_ms = float(frame["prep_ms"]) if frame else 0.0
        child_ms = float(frame["child_ms"]) if frame else 0.0
        self_ms = prep_ms + max(0.0, dt_ms - child_ms)

        with rec.lock:
            rec.last_self_ms = self_ms
            rec.self_exec_ms.append(self_ms)

            if is_link_out:
                rec.last_in_to_out_ms = self_ms
                rec.in_to_out_ms.append(self_ms)
                return

            # If this module uses an async worker process (`ProcessingBase`) and did not emit
            # synchronously during this `input_cb`, its `Exec time` will be recorded when the worker emits.
            ports_emitted = frame["ports"] if frame else []
            if ports_emitted:
                port_self_map: Dict[str, float] = frame.get("port_self_ms", {}) if frame else {}
                max_pre_emit = max(port_self_map.values(), default=0.0)
                tail_ms = max(0.0, self_ms - max_pre_emit)
                for p_key in ports_emitted:
                    p_exec_ms = port_self_map.get(p_key, 0.0) + tail_ms
                    rec.port_last_in_to_out_ms[p_key] = p_exec_ms
                    rec.port_in_to_out_ms[p_key].append(p_exec_ms)
                    rec.in_to_out_ms.append(p_exec_ms)
                    if rec.tracked_port in (p_key, ALL_PORTS_OPTION):
                        rec.last_in_to_out_ms = p_exec_ms
            elif not rec.q_has_worker and not rec._has_async_render and not rec.port_out_timestamps:
                # Pure sink module without async render thread: its Exec time is its internal execution time
                rec.last_in_to_out_ms = self_ms
                rec.in_to_out_ms.append(self_ms)
            elif not rec.q_has_worker and not rec._has_async_render and not any(rec.port_total_out.values()):
                # Module with unconnected output ports and no worker/render thread: Exec time is self_ms
                rec.last_in_to_out_ms = self_ms
                rec.in_to_out_ms.append(self_ms)

    def _on_source_method_start(self, uid: str, t_enter: float, record_input: bool = False) -> None:
        tid = threading.get_ident()
        with self._perf_stack_lock:
            prep_ms = self._thread_source_prep_ms.pop(tid, 0.0)
        self._push_thread_frame(uid, t_enter, prep_ms=prep_ms)
        rec = self._perf_records.get(uid)
        if rec is not None:
            with rec.lock:
                rec._in_active_depth += 1
                if record_input:
                    rec.last_in_ts = t_enter
                    rec.total_in += 1
                    rec.in_timestamps.append(t_enter)
                    rec._last_unconsumed_in_ts = t_enter
                else:
                    rec.is_auto_triggered = True

    def _on_source_method_end(
        self, uid: str, t_enter: float, t_exit: float, dt_ms: float, is_auto: bool = False
    ) -> None:
        frame = self._pop_thread_frame(uid, dt_ms)
        rec = self._perf_records.get(uid)
        if rec is not None:
            with rec.lock:
                rec._in_active_depth = max(0, rec._in_active_depth - 1)
                if is_auto:
                    rec.is_auto_triggered = True
            self._record_completed_sync_frame(rec, frame, dt_ms, is_link_out=False)

    def _on_proxy_emit_start(
        self, src_uid: str, out_key: str, tgt_uid: str, t_enter: float, target_idx: int = 0
    ) -> None:
        src_frame = self._peek_thread_frame(src_uid)
        src_rec = self._perf_records.get(src_uid)
        if src_rec is not None:
            live_q = (
                self._get_live_queue_size_for_uid(src_uid)
                if (src_frame is None and target_idx == 0)
                else 0
            )
            with src_rec.lock:
                src_rec.ensure_port_registered(out_key)
                src_rec.last_out_ts = t_enter
                src_rec.port_last_emit_ts[out_key] = t_enter
                src_rec.edge_last_emit_ts[(out_key, tgt_uid)] = t_enter

                # Only count 1 emission per output port call (target_idx == 0 in connections[out_key])
                if target_idx == 0:
                    src_rec.port_out_timestamps[out_key].append(t_enter)
                    src_rec.port_total_out[out_key] = src_rec.port_total_out.get(out_key, 0) + 1
                    src_rec.out_timestamps.append(t_enter)

                    # Auto-lock onto the first port that actually emits if user hasn't chosen one yet
                    if (
                        not src_rec.user_selected_port
                        and src_rec.tracked_port != ALL_PORTS_OPTION
                        and src_rec.port_total_out.get(src_rec.tracked_port, 0) == 0
                    ):
                        src_rec.tracked_port = out_key

                    if src_frame is not None:
                        # Synchronous or hooked source emission: record cumulative self time up to this port
                        if out_key not in src_frame["ports"]:
                            src_frame["ports"].append(out_key)
                        elapsed_self_ms = float(src_frame["prep_ms"]) + max(
                            0.0,
                            (t_enter - float(src_frame["t_enter"])) * 1000.0 - float(src_frame["child_ms"]),
                        )
                        src_frame["port_self_ms"][out_key] = elapsed_self_ms
                    else:
                        # Asynchronous emission from a worker poll thread (outside `input_cb` / `frame_cb`).
                        # Exclude queue wait time: if an item sat in `_in_queue` while the worker was busy
                        # with the previous item, actual processing started at `max(t_sub, _prev_worker_done_ts)`.
                        is_new_burst = (
                            src_rec._curr_worker_done_ts <= 0.0
                            or out_key in src_rec._worker_burst_ports
                            or (t_enter - src_rec._last_worker_port_end_ts) > 0.002
                        )
                        if is_new_burst:
                            src_rec._prev_worker_done_ts = src_rec._curr_worker_done_ts
                            src_rec._curr_worker_done_ts = t_enter
                            src_rec._worker_emit_child_ms = 0.0
                            src_rec._worker_burst_ports.clear()
                        src_rec._worker_burst_ports.add(out_key)

                        exec_ms: Optional[float] = None
                        if src_rec._worker_submit_ts:
                            src_rec.is_auto_triggered = False
                            idx = max(0, len(src_rec._worker_submit_ts) - 1 - live_q)
                            t_sub = src_rec._worker_submit_ts[idx]
                            src_rec._last_worker_t_sub = t_sub
                            t_work_start = max(t_sub, src_rec._prev_worker_done_ts)
                            exec_ms = max(
                                0.0,
                                (t_enter - t_work_start) * 1000.0 - src_rec._worker_emit_child_ms,
                            )
                        elif (
                            src_rec._last_unconsumed_in_ts > 0.0
                            and (t_enter - src_rec._last_unconsumed_in_ts) <= 2.0
                            and not src_rec.is_auto_triggered
                        ):
                            t_in = src_rec._last_unconsumed_in_ts
                            src_rec._last_worker_t_sub = t_in
                            t_work_start = max(t_in, src_rec._prev_worker_done_ts)
                            exec_ms = max(
                                0.0,
                                (t_enter - t_work_start) * 1000.0 - src_rec._worker_emit_child_ms,
                            )

                        if exec_ms is not None:
                            src_rec.port_last_in_to_out_ms[out_key] = exec_ms
                            src_rec.port_in_to_out_ms[out_key].append(exec_ms)
                            src_rec.in_to_out_ms.append(exec_ms)
                            if src_rec.tracked_port in (out_key, ALL_PORTS_OPTION):
                                src_rec.last_in_to_out_ms = exec_ms

        self._push_thread_frame(tgt_uid, t_enter, prep_ms=0.0, from_proxy=True)
        tgt_rec = self._perf_records.get(tgt_uid)
        if tgt_rec is not None and tgt_rec.cls_name != "_PropagationTesterNode":
            with tgt_rec.lock:
                tgt_rec.last_in_ts = t_enter
                tgt_rec.total_in += 1
                tgt_rec.in_timestamps.append(t_enter)
                tgt_rec._in_enter_ts = t_enter
                tgt_rec._in_active_depth += 1
                tgt_rec._last_unconsumed_in_ts = t_enter

    def _on_proxy_emit_end(
        self,
        src_uid: str,
        out_key: str,
        tgt_uid: str,
        t_enter: float,
        t_exit: float,
        dt_ms: float,
        target_idx: int = 0,
    ) -> None:
        tgt_frame = self._pop_thread_frame(tgt_uid, dt_ms)

        src_rec = self._perf_records.get(src_uid)
        if src_rec is not None:
            with src_rec.lock:
                src_rec._worker_emit_child_ms += dt_ms
                src_rec._last_worker_port_end_ts = t_exit

        tgt_rec = self._perf_records.get(tgt_uid)
        if tgt_rec is not None and tgt_rec.cls_name != "_PropagationTesterNode":
            with tgt_rec.lock:
                tgt_rec._in_active_depth = max(0, tgt_rec._in_active_depth - 1)
            self._record_completed_sync_frame(tgt_rec, tgt_frame, dt_ms, is_link_out=False)

    def _on_direct_input_start(self, uid: str, t_enter: float) -> bool:
        # Avoid duplicate frame if already inside a `_TrackedTargetProxy` call for `uid`
        top = self._peek_thread_frame(uid)
        if top is not None and (
            (top.get("from_proxy") and not top.get("entered_cb"))
            or abs(t_enter - float(top["t_enter"])) < 1e-4
        ):
            top["entered_cb"] = True
            return False
        self._push_thread_frame(uid, t_enter, prep_ms=0.0)
        rec = self._perf_records.get(uid)
        if rec is not None:
            with rec.lock:
                rec.last_in_ts = t_enter
                rec.total_in += 1
                rec.in_timestamps.append(t_enter)
                rec._in_enter_ts = t_enter
                rec._in_active_depth += 1
                rec._last_unconsumed_in_ts = t_enter
        return True

    def _on_direct_input_end(
        self, uid: str, inst: Any, t_enter: float, t_exit: float, dt_ms: float, pushed: bool = True
    ) -> None:
        if not pushed:
            return
        frame = self._pop_thread_frame(uid, dt_ms)
        rec = self._perf_records.get(uid)
        if rec is not None:
            is_link_out = getattr(inst, "KIND", "") == "link_out"
            with rec.lock:
                rec._in_active_depth = max(0, rec._in_active_depth - 1)
                if is_link_out:
                    rec.last_out_ts = t_exit
                    rec.total_out += 1
                    rec.out_timestamps.append(t_exit)
            self._record_completed_sync_frame(rec, frame, dt_ms, is_link_out=is_link_out)

    # =========================================================================
    # CANVAS-ONLY THEME HELPERS (NEVER COLORS MODULE WINDOWS, ZERO IDLE OVERHEAD)
    # =========================================================================

    @staticmethod
    def _build_minimal_canvas_node_theme(
        title_rgb: Tuple[int, int, int],
        outline_rgb: Tuple[int, int, int],
        thickness: float = 4.0,
    ) -> int:
        """
        Build an ultra-lightweight DPG theme containing ONLY `mvNode` colors.
        Unlike `build_node_theme` (which adds `mvAll`, `mvWindowAppItem`, `mvChildWindow`,
        `mvInputText` components that Dear PyGui evaluates on every child widget every frame),
        this touches strictly the `mvNode` item itself with near-zero render-thread cost.
        """
        theme = dpg.add_theme()
        title_hover = tuple(min(255, c + 20) for c in title_rgb)
        with dpg.theme_component(dpg.mvNode, parent=theme):
            dpg.add_theme_color(dpg.mvNodeCol_TitleBar, (*title_rgb, 255), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_TitleBarHovered, (*title_hover, 255), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_TitleBarSelected, (*title_rgb, 255), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_NodeOutline, (*outline_rgb, 255), category=dpg.mvThemeCat_Nodes)
            try:
                dpg.add_theme_style(dpg.mvNodeStyleVar_NodeBorderThickness, thickness, category=dpg.mvThemeCat_Nodes)
            except AttributeError:
                pass
        return theme

    @staticmethod
    def _build_minimal_canvas_link_theme(
        outline_rgb: Tuple[int, int, int],
        thickness: float = 5.0,
    ) -> int:
        """Build an ultra-lightweight DPG theme scoped strictly to `mvNodeLink`."""
        theme = dpg.add_theme()
        link_hover = tuple(min(255, c + 20) for c in outline_rgb)
        comp_type = getattr(dpg, "mvNodeLink", 0)
        with dpg.theme_component(comp_type, parent=theme):
            dpg.add_theme_color(dpg.mvNodeCol_Link, (*outline_rgb, 255), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_LinkHovered, (*link_hover, 255), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_LinkSelected, (*outline_rgb, 255), category=dpg.mvThemeCat_Nodes)
            try:
                dpg.add_theme_style(dpg.mvNodeStyleVar_LinkThickness, thickness, category=dpg.mvThemeCat_Nodes)
            except AttributeError:
                pass
        return theme

    def _get_perf_canvas_color_theme(self, output_index: int) -> Tuple[int, int]:
        """Return cached `(node_theme, link_theme)` scoped strictly to `mvNode`/`mvNodeLink`."""
        if output_index in self._perf_canvas_theme_cache:
            return self._perf_canvas_theme_cache[output_index]
        from config.theme_factory import COLORBLIND_PALETTES, _adjust_color, hsl_to_rgb
        from config.theme_manager import theme_manager

        colors = getattr(theme_manager, "current_palette", getattr(theme_manager, "active_palette", {}))
        if colors.get("is_colorblind"):
            ptype = colors.get("colorblind_type", "universal")
            palette = COLORBLIND_PALETTES.get(ptype, COLORBLIND_PALETTES["universal"])
            r_out, g_out, b_out = palette[output_index % len(palette)]
            r_tit, g_tit, b_tit = _adjust_color((r_out, g_out, b_out), -40)
            thickness = 5.0
        else:
            base_hue = colors.get("base_hue", 120.0)
            sat = colors.get("sat", 0.85)
            hue_deg = (base_hue + output_index * 137.508) % 360
            h = hue_deg / 360.0
            r_tit, g_tit, b_tit = hsl_to_rgb(h, sat, 0.35)
            r_out, g_out, b_out = hsl_to_rgb(h, sat, 0.55)
            thickness = 4.0

        n_th = self._build_minimal_canvas_node_theme((r_tit, g_tit, b_tit), (r_out, g_out, b_out), thickness)
        l_th = self._build_minimal_canvas_link_theme((r_out, g_out, b_out), thickness + 1.0)
        self._perf_canvas_theme_cache[output_index] = (n_th, l_th)
        return n_th, l_th

    def _get_perf_special_themes(self) -> Tuple[int, int, int]:
        """Return cached `(red_node_theme, highlight_node_theme, virtual_link_theme)`."""
        cached = self._perf_canvas_theme_cache.get("_special")
        if cached is not None:
            return cached  # type: ignore[return-value]
        red_n = self._build_minimal_canvas_node_theme((200, 40, 40), (255, 60, 60), 4.0)
        hl_n = self._build_minimal_canvas_node_theme((200, 110, 0), (255, 160, 0), 4.0)
        v_lnk = self._build_minimal_canvas_link_theme((210, 120, 0), 4.0)
        res = (red_n, hl_n, v_lnk)
        self._perf_canvas_theme_cache["_special"] = res  # type: ignore[assignment]
        return res

    def _bind_canvas_node_theme(self, node_id: int, theme: int) -> None:
        """Bind a theme ONLY to the NodeEditor node item when state changes."""
        if self._perf_applied_node_themes.get(node_id, 0) == theme:
            return
        try:
            dpg.bind_item_theme(node_id, theme)
            if theme == 0:
                self._perf_applied_node_themes.pop(node_id, None)
            else:
                self._perf_applied_node_themes[node_id] = theme
        except Exception:
            pass

    def _bind_canvas_link_theme(self, link_id: int, theme: int) -> None:
        """Bind a theme to a NodeEditor link item when state changes."""
        if self._perf_applied_link_themes.get(link_id, 0) == theme:
            return
        try:
            dpg.bind_item_theme(link_id, theme)
            if theme == 0:
                self._perf_applied_link_themes.pop(link_id, None)
            else:
                self._perf_applied_link_themes[link_id] = theme
        except Exception:
            pass

    def _init_heartbeat_canvas(self) -> None:
        """
        Reset canvas to default zero-overhead baseline (`theme = 0`).
        Idle nodes and links keep `theme = 0` so Dear PyGui's OpenGL render loop pays
        zero per-widget theme stack cost at rest; only actively pulsing or congested
        nodes/links receive a lightweight `mvNode`/`mvNodeLink` theme.
        """
        for node_id in list(self._perf_applied_node_themes.keys()):
            try:
                dpg.bind_item_theme(node_id, 0)
            except Exception:
                pass
        for link_id in list(self._perf_applied_link_themes.keys()):
            try:
                dpg.bind_item_theme(link_id, 0)
            except Exception:
                pass
        self._perf_applied_node_themes.clear()
        self._perf_applied_link_themes.clear()
        self._perf_link_meta_cache.clear()

    def _clear_perf_virtual_links(self) -> None:
        for v_link_id in list(self._perf_virtual_links.values()):
            if dpg.does_item_exist(v_link_id):
                try:
                    dpg.delete_item(v_link_id)
                except Exception:
                    pass
            self._perf_applied_link_themes.pop(v_link_id, None)
        self._perf_virtual_links.clear()

    def _restore_canvas_themes(self) -> None:
        """Reset any themed nodes and links back to default theme (0) and remove virtual links."""
        self._clear_perf_virtual_links()
        for node_id in list(self._perf_applied_node_themes.keys()):
            if dpg.does_item_exist(node_id):
                try:
                    dpg.bind_item_theme(node_id, 0)
                except Exception:
                    pass
        for link_id in list(self._perf_applied_link_themes.keys()):
            if dpg.does_item_exist(link_id):
                try:
                    dpg.bind_item_theme(link_id, 0)
                except Exception:
                    pass
        self._perf_applied_node_themes.clear()
        self._perf_applied_link_themes.clear()
        self._perf_link_meta_cache.clear()

    # =========================================================================
    # BACKGROUND TELEMETRY & HEARTBEAT UPDATE LOOP
    # =========================================================================

    @staticmethod
    def _inspect_module_queue(
        inst: Any, rec: Optional[_NodePerfRecord] = None, now: float = 0.0
    ) -> Tuple[bool, int, int, float, str, int]:
        """Return (has_worker, q_size, q_max, fill_pct, drop_policy, dropped_count)."""
        worker = None
        if rec is not None and (now - rec._cached_worker_checked_ts) < 2.0:
            worker = rec._cached_worker
        else:
            for attr in ("proc", "processor", "_worker", "worker", "_proc", "engine"):
                cand = getattr(inst, attr, None)
                if cand is not None and hasattr(cand, "_in_queue"):
                    worker = cand
                    break
            if rec is not None:
                rec._cached_worker = worker
                rec._cached_worker_checked_ts = now

        target = worker if worker is not None else inst
        in_q = getattr(target, "_in_queue", None)
        dropped = int(getattr(inst, "dropped_count", getattr(target, "dropped_count", 0)) or 0)

        if in_q is None:
            return False, 0, 1, 0.0, "N/A", dropped

        max_q: Optional[int] = None
        for src_obj, attr_name in (
            (in_q, "_maxsize"),
            (target, "buffer_size"),
            (inst, "buffer_size"),
            (inst, "max_queue_size"),
            (target, "max_queue_size"),
        ):
            val = getattr(src_obj, attr_name, None)
            if isinstance(val, int) and val > 0:
                max_q = val
                break
        if not max_q or max_q <= 0:
            max_q = 1

        q_size = 0
        q_fn = getattr(target, "queue_size", None)
        try:
            q_val = int(q_fn()) if callable(q_fn) else -1
            q_size = q_val if q_val >= 0 else int(in_q.qsize())
        except Exception:
            try:
                q_size = 1 if not in_q.empty() else 0
            except Exception:
                q_size = 0

        fill_pct = min(100.0, (q_size / max_q) * 100.0) if max_q > 0 else 0.0
        raw_pol = getattr(target, "_drop_policy", getattr(target, "drop_policy", getattr(inst, "drop_policy", "drop_new")))
        if isinstance(raw_pol, bool):
            pol_str = "drop_oldest" if raw_pol else "drop_new"
        else:
            pol_str = str(raw_pol or "drop_new")

        return True, q_size, max_q, fill_pct, pol_str, dropped

    def _update_all_records(self, now: float, record_history: bool) -> None:
        """Compute rolling rates, percentiles, queue states, and optional time-series points."""
        rel_t = now - self._perf_t0
        open_reports = self._perf_report_windows
        popup_nid = self._perf_popup_node_id

        for node_id, inst in list(self.node_map.items()):
            if inst is None:
                continue
            rec = self._get_or_create_record(inst)
            has_w, q_sz, q_max, q_pct, pol, drops = self._inspect_module_queue(inst, rec, now)

            is_self_ready = True
            ready_fn = getattr(inst, "is_ready", None)
            if callable(ready_fn):
                try:
                    is_self_ready = bool(ready_fn())
                except Exception:
                    is_self_ready = True

            is_down_ready = True
            out_ready_fn = getattr(inst, "is_outputs_ready", None)
            if callable(out_ready_fn):
                try:
                    is_down_ready = bool(out_ready_fn())
                except Exception:
                    is_down_ready = True

            with rec.lock:
                in_hz = rec._calc_rate(rec.in_timestamps, now)
                rec.in_rate_hz = in_hz

                stats_seq = (rec.total_in, sum(rec.port_total_out.values()), rec.last_self_ms, rec.last_in_to_out_ms)
                stats_changed = stats_seq != rec._last_stats_seq
                if stats_changed:
                    rec._last_stats_seq = stats_seq

                for p_key, p_dq in rec.port_out_timestamps.items():
                    rec.port_rates_hz[p_key] = rec._calc_rate(p_dq, now)
                    if stats_changed:
                        t_dq = rec.port_in_to_out_ms.get(p_key)
                        if t_dq:
                            pt_vals = sorted(t_dq)
                            rec.port_mean_turn_ms[p_key] = sum(pt_vals) / len(pt_vals)
                            rec.port_p95_turn_ms[p_key] = pt_vals[min(len(pt_vals) - 1, int(0.95 * (len(pt_vals) - 1)))]
                            rec.port_max_turn_ms[p_key] = pt_vals[-1]
                        else:
                            rec.port_mean_turn_ms[p_key] = 0.0
                            rec.port_p95_turn_ms[p_key] = 0.0
                            rec.port_max_turn_ms[p_key] = 0.0

                if stats_changed:
                    if rec.self_exec_ms:
                        s_vals = sorted(rec.self_exec_ms)
                        rec.mean_self_ms = sum(s_vals) / len(s_vals)
                        rec.p95_self_ms = s_vals[min(len(s_vals) - 1, int(0.95 * (len(s_vals) - 1)))]
                    else:
                        rec.mean_self_ms = 0.0
                        rec.p95_self_ms = 0.0

                # Resolve `out_rate_hz`, `total_out`, and `in_to_out` turnaround based on `rec.tracked_port`
                avail_ports = rec.get_available_port_options(inst)
                if not rec.user_selected_port and rec.tracked_port not in avail_ports:
                    rec.tracked_port = avail_ports[0]
                tp = rec.tracked_port
                if tp in rec.port_out_timestamps:
                    out_hz = rec.port_rates_hz.get(tp, 0.0)
                    rec.total_out = rec.port_total_out.get(tp, 0)
                    if rec.port_in_to_out_ms.get(tp):
                        rec.mean_turnaround_ms = rec.port_mean_turn_ms[tp]
                        rec.p95_turnaround_ms = rec.port_p95_turn_ms[tp]
                        rec.max_turnaround_ms = rec.port_max_turn_ms[tp]
                        rec.last_in_to_out_ms = rec.port_last_in_to_out_ms.get(tp, 0.0)
                    elif rec.in_to_out_ms:
                        if stats_changed:
                            t_vals = sorted(rec.in_to_out_ms)
                            rec.mean_turnaround_ms = sum(t_vals) / len(t_vals)
                            rec.p95_turnaround_ms = t_vals[min(len(t_vals) - 1, int(0.95 * (len(t_vals) - 1)))]
                            rec.max_turnaround_ms = t_vals[-1]
                    else:
                        rec.mean_turnaround_ms = rec.mean_self_ms
                        rec.p95_turnaround_ms = rec.p95_self_ms
                        rec.max_turnaround_ms = rec.p95_self_ms
                        rec.last_in_to_out_ms = rec.last_self_ms
                elif tp == ALL_PORTS_OPTION and rec.port_out_timestamps:
                    out_hz = sum(rec.port_rates_hz.values())
                    rec.total_out = sum(rec.port_total_out.values())
                    if rec.in_to_out_ms:
                        if stats_changed:
                            t_vals = sorted(rec.in_to_out_ms)
                            rec.mean_turnaround_ms = sum(t_vals) / len(t_vals)
                            rec.p95_turnaround_ms = t_vals[min(len(t_vals) - 1, int(0.95 * (len(t_vals) - 1)))]
                            rec.max_turnaround_ms = t_vals[-1]
                    else:
                        rec.mean_turnaround_ms = rec.mean_self_ms
                        rec.p95_turnaround_ms = rec.p95_self_ms
                        rec.max_turnaround_ms = rec.p95_self_ms
                else:
                    # Sink module (no outputs) or LinkOut
                    out_hz = rec._calc_rate(rec.out_timestamps, now)
                    if rec.in_to_out_ms:
                        if stats_changed:
                            t_vals = sorted(rec.in_to_out_ms)
                            rec.mean_turnaround_ms = sum(t_vals) / len(t_vals)
                            rec.p95_turnaround_ms = t_vals[min(len(t_vals) - 1, int(0.95 * (len(t_vals) - 1)))]
                            rec.max_turnaround_ms = t_vals[-1]
                    else:
                        rec.mean_turnaround_ms = rec.mean_self_ms
                        rec.p95_turnaround_ms = rec.p95_self_ms
                        rec.max_turnaround_ms = rec.p95_self_ms
                        rec.last_in_to_out_ms = rec.last_self_ms

                # Fallback to module's own reported rate if source doesn't use connections yet
                mod_rate = getattr(inst, "measured_rate_hz", None)
                if out_hz == 0.0 and mod_rate is not None and rec.port_out_timestamps:
                    try:
                        out_hz = float(mod_rate)
                    except Exception:
                        pass

                rec.out_rate_hz = out_hz

                rec.q_has_worker = has_w
                rec.q_size = q_sz
                rec.q_max = q_max
                rec.q_fill_pct = q_pct
                rec.drop_policy = pol
                rec.dropped_count = drops
                rec.is_self_ready = is_self_ready
                rec.is_downstream_ready = is_down_ready

                # Determine Flow State, Auto-Trigger status, and 0/1 Waiting status
                any_out_hz = max(out_hz, max(rec.port_rates_hz.values(), default=0.0))
                if (in_hz < 0.05 and any_out_hz > 0.05) or (
                    rec.total_in <= 1 and rec.total_out > 2 and not has_w
                ):
                    rec.is_auto_triggered = True

                waiting_now = (not is_down_ready) or (has_w and not is_self_ready)
                if waiting_now:
                    rec._saw_wait_since_last_sample = True
                rec.is_waiting_val = 1.0 if waiting_now else 0.0

                recently_active = (
                    (now - max(rec.last_in_ts, rec.last_out_ts)) < 0.8
                    or in_hz > 0.1
                    or any_out_hz > 0.1
                )
                if not is_down_ready:
                    rec.flow_state_text = "WAITING (DOWNSTREAM HOLD)"
                    rec.flow_state_color = COLOR_CONGESTED
                elif has_w and (q_pct >= 95.0 or not is_self_ready):
                    rec.flow_state_text = f"SATURATED ({q_sz}/{q_max})"
                    rec.flow_state_color = COLOR_CONGESTED
                elif has_w and q_pct >= 50.0:
                    rec.flow_state_text = f"BUSY QUEUE ({q_sz}/{q_max})"
                    rec.flow_state_color = COLOR_BUSY
                elif recently_active:
                    rec.flow_state_text = "FLOWING (AUTO)" if rec.is_auto_triggered else "FLOWING"
                    rec.flow_state_color = COLOR_FLOWING
                else:
                    rec.flow_state_text = "IDLE"
                    rec.flow_state_color = COLOR_IDLE

                if record_history:
                    # Expensive OS RSS + object-graph buffer scan ONLY when a Report Window is open for this node (1 Hz max)
                    is_report_open = rec.uid in open_reports
                    if is_report_open and (now - rec._last_mem_check_ts) >= 1.0:
                        rec._last_mem_check_ts = now
                        worker_pid: Optional[int] = None
                        w_obj = rec._cached_worker if rec._cached_worker is not None else inst
                        proc_obj = getattr(w_obj, "_process", getattr(w_obj, "process", None))
                        if proc_obj is not None:
                            try:
                                if getattr(proc_obj, "is_alive", lambda: False)():
                                    pid = getattr(proc_obj, "pid", None)
                                    if isinstance(pid, int) and pid > 0:
                                        worker_pid = pid
                            except Exception:
                                pass
                        if worker_pid is not None:
                            rec.mem_pid = worker_pid
                            rec.mem_type_text = f"Worker Process (PID {worker_pid})"
                        else:
                            rec.mem_pid = os.getpid()
                            rec.mem_type_text = f"Main Process (PID {rec.mem_pid})"

                        rss_mb, peak_mb = _get_process_memory_mb(rec.mem_pid)
                        buf_mb = _estimate_module_buffers_mb(inst)
                        rec.mem_rss_mb = rss_mb
                        rec.mem_peak_mb = max(rec.mem_peak_mb, rss_mb, peak_mb)
                        rec.mem_buffers_mb = buf_mb

                    should_append_ts = (
                        is_report_open
                        or node_id == popup_nid
                        or recently_active
                        or waiting_now
                        or len(rec.ts_time) < 2
                        or (rec.ts_in_rate and rec.ts_in_rate[-1] > 0.0)
                        or (rec.ts_out_rate and rec.ts_out_rate[-1] > 0.0)
                    )
                    if should_append_ts:
                        wait_sample = 1.0 if (waiting_now or rec._saw_wait_since_last_sample) else 0.0
                        rec._saw_wait_since_last_sample = False
                        rec.ts_time.append(rel_t)
                        rec.ts_in_rate.append(in_hz)
                        rec.ts_out_rate.append(out_hz)
                        rec.ts_turnaround_ms.append(rec.last_in_to_out_ms)
                        rec.ts_p95_ms.append(rec.p95_turnaround_ms)
                        rec.ts_self_ms.append(rec.last_self_ms)
                        rec.ts_queue_pct.append(q_pct)
                        rec.ts_waiting.append(wait_sample)
                        rec.ts_mem_rss.append(rec.mem_rss_mb)
                        rec.ts_mem_buffers.append(rec.mem_buffers_mb)
                        for p_key, r_val in rec.port_rates_hz.items():
                            if p_key in rec.ts_port_rates:
                                rec.ts_port_rates[p_key].append(r_val)
                            if p_key in rec.ts_port_turnaround_ms:
                                rec.ts_port_turnaround_ms[p_key].append(
                                    rec.port_last_in_to_out_ms.get(p_key, 0.0)
                                )
                            if p_key in rec.ts_port_p95_ms:
                                rec.ts_port_p95_ms[p_key].append(rec.port_p95_turn_ms.get(p_key, 0.0))

    def _resolve_link_meta(
        self, link_id: int, from_attr: int | str, to_attr: int | str
    ) -> Optional[Tuple[int, int, str, str, str, int]]:
        """
        Resolve and cache `(src_nid, tgt_nid, src_uid, tgt_uid, out_key, out_idx)` for a link
        so `_update_heartbeat_canvas` never calls Dear PyGui C++ introspection APIs on every tick.
        """
        cache_key = (link_id, from_attr, to_attr)
        cached = self._perf_link_meta_cache.get(cache_key)
        if cached is not None:
            src_nid, tgt_nid = cached[0], cached[1]
            if src_nid in self.node_map and tgt_nid in self.node_map:
                return cached
            self._perf_link_meta_cache.pop(cache_key, None)

        if not dpg.does_item_exist(from_attr) or not dpg.does_item_exist(to_attr):
            return None
        src_nid = dpg.get_item_parent(from_attr)
        tgt_nid = dpg.get_item_parent(to_attr)
        src_inst = self.node_map.get(src_nid)
        tgt_inst = self.node_map.get(tgt_nid)
        if src_inst is None or tgt_inst is None:
            return None

        src_uid = self._instance_uid(src_inst)
        tgt_uid = self._instance_uid(tgt_inst)
        out_key = self._find_output_key(src_nid, from_attr) or "Out"
        out_keys_list = list(getattr(src_inst, "outputs", {}).keys())
        out_idx = out_keys_list.index(out_key) if out_key in out_keys_list else 0
        meta = (src_nid, tgt_nid, src_uid, tgt_uid, out_key, out_idx)
        self._perf_link_meta_cache[cache_key] = meta
        return meta

    def _update_heartbeat_canvas(self, now: float) -> None:
        """
        Update node and wire themes in the NodeEditor canvas according to live data emission.
        Uses cached link topology and `0` (default theme) for idle items so zero DPG calls
        and zero per-item theme stack overhead occur when the pipeline is idle.
        """
        if not self.perf_mode_active or not self.perf_heartbeat_enabled:
            return

        self._perf_beat_phase = (self._perf_beat_phase + 1) % 8
        pulse_win = self.perf_pulse_duration_s
        beat_dip = self.perf_pulse_continuous and (self._perf_beat_phase == 0)

        # Idle baseline is theme `0` (default unbound theme -> zero DPG render-thread overhead!)
        desired_node_theme: Dict[int, int] = {nid: 0 for nid in self.node_map}
        desired_link_theme: Dict[int, int] = {lid: 0 for lid in self.link_map}

        red_node_th, hl_node_th, v_link_th = self._get_perf_special_themes()

        # 1. Inspect physical links using pure-Python cached link metadata
        for link_id, (from_attr, to_attr) in list(self.link_map.items()):
            meta = self._resolve_link_meta(link_id, from_attr, to_attr)
            if meta is None:
                continue
            src_nid, tgt_nid, src_uid, tgt_uid, out_key, out_idx = meta
            src_rec = self._perf_records.get(src_uid)
            if src_rec is None:
                continue

            last_edge_ts = src_rec.edge_last_emit_ts.get(
                (out_key, tgt_uid),
                src_rec.port_last_emit_ts.get(out_key, 0.0),
            )
            age = now - last_edge_ts

            if age <= pulse_win:
                port_rate = src_rec.port_rates_hz.get(out_key, src_rec.out_rate_hz)
                is_continuous_dip = beat_dip and (port_rate >= 8.0)
                node_theme, link_theme = self._get_perf_canvas_color_theme(out_idx)

                if not is_continuous_dip:
                    desired_link_theme[link_id] = link_theme
                    desired_node_theme[src_nid] = node_theme
                    desired_node_theme[tgt_nid] = node_theme
                else:
                    desired_link_theme[link_id] = 0
                    desired_node_theme[src_nid] = node_theme

        # 2. Handle Virtual Links (Link Out -> Link In) if enabled
        if self.perf_show_virtual_links:
            link_outs: Dict[str, List[Tuple[int, Any]]] = {}
            link_ins: Dict[str, List[Tuple[int, Any]]] = {}
            for nid, inst in list(self.node_map.items()):
                kind = getattr(inst, "KIND", "")
                if kind == "link_out":
                    ch = inst._get_live_name()
                    if ch:
                        link_outs.setdefault(ch, []).append((nid, inst))
                elif kind == "link_in":
                    ch = inst._get_live_name()
                    if ch:
                        link_ins.setdefault(ch, []).append((nid, inst))

            active_pairs: Set[Tuple[int, int]] = set()
            for ch, outs in link_outs.items():
                ins = link_ins.get(ch, [])
                for out_nid, out_inst in outs:
                    out_rec = self._perf_records.get(self._instance_uid(out_inst))
                    is_active = out_rec is not None and (now - out_rec.last_in_ts) <= pulse_win
                    for in_nid, _ in ins:
                        pair = (out_nid, in_nid)
                        active_pairs.add(pair)
                        v_lid = self._perf_virtual_links.get(pair)
                        if v_lid is None:
                            v_lid = self._create_perf_virtual_link(out_nid, in_nid)
                            if v_lid:
                                self._perf_virtual_links[pair] = v_lid
                        if v_lid:
                            v_theme = v_link_th if (is_active and not beat_dip) else 0
                            self._bind_canvas_link_theme(v_lid, v_theme)
                        if is_active:
                            desired_node_theme[out_nid] = hl_node_th
                            desired_node_theme[in_nid] = hl_node_th

            for pair in list(self._perf_virtual_links.keys()):
                if pair not in active_pairs:
                    v_lid = self._perf_virtual_links.pop(pair)
                    if dpg.does_item_exist(v_lid):
                        try:
                            dpg.delete_item(v_lid)
                        except Exception:
                            pass
                    self._perf_applied_link_themes.pop(v_lid, None)

        # 3. Override with Red Theme for any node experiencing congestion / saturation / backpressure hold
        for nid, inst in list(self.node_map.items()):
            if inst is None:
                continue
            rec = self._perf_records.get(self._instance_uid(inst))
            if rec is None:
                continue
            if not rec.is_downstream_ready or (rec.q_has_worker and rec.q_fill_pct >= 85.0):
                desired_node_theme[nid] = red_node_th
            elif desired_node_theme.get(nid, 0) == 0:
                if (now - max(rec.last_in_ts, rec.last_out_ts)) <= pulse_win:
                    node_theme, _ = self._get_perf_canvas_color_theme(0)
                    desired_node_theme[nid] = node_theme

        # 4. Apply diff-only themes to canvas items (zero DPG calls when unchanged)
        for nid, theme in desired_node_theme.items():
            self._bind_canvas_node_theme(nid, theme)
        for lid, theme in desired_link_theme.items():
            self._bind_canvas_link_theme(lid, theme)

    def _create_perf_virtual_link(self, link_out_id: int, link_in_id: int) -> int:
        """Create a visual link between a Link Out and its matching Link In."""
        try:
            in_attr = self._get_first_input_attr(link_out_id)
            out_attr = f"{link_in_id}_Out"
            if in_attr and dpg.does_item_exist(out_attr):
                return dpg.add_node_link(out_attr, in_attr, parent=self.editor_tag)
        except Exception:
            pass
        return 0

    def _perf_monitor_loop(self) -> None:
        """Background daemon loop running with near-zero UI thread contention while profiling is active."""
        while not self._perf_stop_event.is_set():
            if not dpg.is_dearpygui_running():
                break
            if not self._is_profiler_needed(force_dpg_check=False):
                break

            now = time.perf_counter()
            try:
                self._sync_module_hooks(force=False)

                record_hist = (now - self._perf_last_history_ts) >= 0.25
                if record_hist:
                    self._perf_last_history_ts = now
                    self._update_all_records(now, record_history=True)
                elif self._perf_popup_node_id is not None:
                    self._update_all_records(now, record_history=False)

                if self.perf_mode_active and self.perf_heartbeat_enabled:
                    self._update_heartbeat_canvas(now)

                if self._perf_popup_node_id is not None:
                    self._refresh_perf_popup_ui()

                if record_hist and self._perf_report_windows:
                    self._refresh_all_report_windows()
            except Exception as exc:
                logger.debug(f"NodePerfProfiler loop error: {exc}")

            sleep_s = 0.08 if (self.perf_mode_active and self.perf_heartbeat_enabled) else 0.20
            self._perf_stop_event.wait(sleep_s)

        # Clean exit when no longer needed
        self._check_and_stop_profiler_if_unused()

    # =========================================================================
    # LIGHTWEIGHT RIGHT-CLICK TELEMETRY POPUP (TOOLTIP CARD)
    # =========================================================================

    def _build_perf_popup_window(self) -> None:
        """Build the reusable lightweight telemetry popup window (resizable, adapting plot)."""
        if dpg.does_item_exist(self.perf_popup_tag):
            dpg.delete_item(self.perf_popup_tag)

        with dpg.window(
            tag=self.perf_popup_tag,
            show=False,
            no_title_bar=True,
            no_resize=False,
            width=340,
            height=280,
            min_size=(240, 180),
            pos=(200, 200),
        ):
            # 1. Top: View Full Report button spanning full width
            dpg.add_button(
                label="View Full Report...",
                width=-1,
                callback=self._on_popup_view_report_clicked,
            )
            dpg.add_separator()

            # 2. Status line
            with dpg.group(horizontal=True):
                dpg.add_text("Status:", color=(170, 175, 185))
                dpg.add_text("[IDLE]", tag=f"{self.perf_popup_tag}_state", color=COLOR_IDLE)

            # 3. Line below: Tracked output
            with dpg.group(horizontal=True, tag=f"{self.perf_popup_tag}_port_row"):
                dpg.add_text("Tracked output:", color=(180, 205, 230))
                dpg.add_combo(
                    items=[SINK_NO_OUTPUT_OPTION],
                    default_value=SINK_NO_OUTPUT_OPTION,
                    tag=f"{self.perf_popup_tag}_port_combo",
                    width=-1,
                    callback=self._on_popup_port_selected,
                )

            dpg.add_separator()

            # 4. Stats:
            #    - Exec time : ms (Hz)
            #    - Queue : [policy] Dropped : value (hidden if module has no queue)
            dpg.add_text("Exec time : 0.00 ms (0.0 Hz)", tag=f"{self.perf_popup_tag}_turn")
            dpg.add_text(
                "Queue : [N/A]  Dropped : 0",
                tag=f"{self.perf_popup_tag}_queue",
                show=False,
            )

            # 5. Plot: expands to full available width and remaining height as window is resized
            with dpg.plot(
                height=-1,
                width=-1,
                no_menus=True,
                no_box_select=True,
                no_mouse_pos=True,
                tag=f"{self.perf_popup_tag}_plot",
            ):
                dpg.add_plot_legend(location=dpg.mvPlot_Location_NorthWest)
                dpg.add_plot_axis(
                    dpg.mvXAxis,
                    tag=f"{self.perf_popup_tag}_x_ax",
                    no_tick_labels=True,
                )
                with dpg.plot_axis(dpg.mvYAxis, label="ms", tag=f"{self.perf_popup_tag}_y_ax"):
                    dpg.add_line_series(
                        [], [], label="Exec time (ms)", tag=f"{self.perf_popup_tag}_ser_self"
                    )

    def _on_popup_port_selected(self, sender: Any, app_data: str, user_data: Any = None) -> None:
        """Update the tracked output port for the currently inspected node."""
        node_id = self._perf_popup_node_id
        if node_id is None:
            return
        inst = self.node_map.get(node_id)
        if inst is None:
            return
        rec = self._get_or_create_record(inst)
        with rec.lock:
            rec.tracked_port = str(app_data)
            rec.user_selected_port = True
        ctx = self._perf_report_windows.get(rec.uid)
        if ctx:
            r_combo = f"{ctx['win_tag']}_port_combo"
            if dpg.does_item_exist(r_combo):
                dpg.set_value(r_combo, str(app_data))
        self._refresh_perf_popup_ui()

    def open_node_perf_popup(self, node_id: int, screen_pos: Tuple[float, float]) -> None:
        """Show the lightweight telemetry popup anchored near the right-clicked node."""
        inst = self.node_map.get(node_id)
        if inst is None:
            return
        self._perf_popup_node_id = node_id
        self._ensure_profiler_running()

        if not dpg.does_item_exist(self.perf_popup_tag):
            self._build_perf_popup_window()

        rec = self._get_or_create_record(inst)
        with rec.lock:
            port_items = rec.get_available_port_options(inst)
            if rec.tracked_port not in port_items:
                rec.tracked_port = port_items[0]
            curr_port = rec.tracked_port

        combo_tag = f"{self.perf_popup_tag}_port_combo"
        if dpg.does_item_exist(combo_tag):
            dpg.configure_item(combo_tag, items=port_items)
            dpg.set_value(combo_tag, curr_port)

        px = max(10, int(screen_pos[0]) + 14)
        py = max(10, int(screen_pos[1]) - 10)
        dpg.configure_item(self.perf_popup_tag, pos=(px, py), show=True)
        dpg.focus_item(self.perf_popup_tag)
        self._refresh_perf_popup_ui()

    def _close_perf_popup(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        was_open = (
            getattr(self, "_perf_popup_node_id", None) is not None
            or (dpg.does_item_exist(self.perf_popup_tag) and dpg.is_item_shown(self.perf_popup_tag))
        )
        self._perf_popup_node_id = None
        if dpg.does_item_exist(self.perf_popup_tag):
            dpg.configure_item(self.perf_popup_tag, show=False)
        if was_open:
            self._check_and_stop_profiler_if_unused()

    def cleanup_deleted_node_perf(self, node_id: int) -> None:
        """Clean up popup and report window if a tracked node is deleted."""
        if getattr(self, "_perf_popup_node_id", None) == node_id:
            self._close_perf_popup()
        inst = self.node_map.get(node_id)
        if inst is not None:
            uid = self._instance_uid(inst)
            ctx = getattr(self, "_perf_report_windows", {}).pop(uid, None)
            if ctx:
                h_tag = ctx.get("handler_tag")
                if h_tag and dpg.does_item_exist(h_tag):
                    try:
                        dpg.delete_item(h_tag)
                    except Exception:
                        pass
                w_tag = ctx.get("win_tag")
                if w_tag and dpg.does_item_exist(w_tag):
                    try:
                        dpg.delete_item(w_tag)
                    except Exception:
                        pass
            getattr(self, "_perf_records", {}).pop(uid, None)
        getattr(self, "_perf_applied_node_themes", {}).pop(node_id, None)
        self._check_and_stop_profiler_if_unused()

    def _is_perf_popup_hovered(self) -> bool:
        """Check whether the telemetry popup window or any of its children/widgets is hovered."""
        if not hasattr(self, "perf_popup_tag") or not dpg.does_item_exist(self.perf_popup_tag):
            return False
        if not dpg.is_item_shown(self.perf_popup_tag):
            return False

        try:
            if dpg.is_item_active(self.perf_popup_tag):
                return True
        except Exception:
            pass

        def _check_item(item_id: int | str) -> bool:
            try:
                if dpg.is_item_hovered(item_id) or dpg.is_item_active(item_id):
                    return True
            except Exception:
                pass
            for slot in (0, 1, 2):
                try:
                    children = dpg.get_item_children(item_id, slot)
                    if children:
                        for child in children:
                            if _check_item(child):
                                return True
                except Exception:
                    pass
            return False

        if _check_item(self.perf_popup_tag):
            return True

        try:
            pos = dpg.get_item_pos(self.perf_popup_tag)
            sz = dpg.get_item_rect_size(self.perf_popup_tag)
            mx, my = dpg.get_mouse_pos(local=False)
            if sz and sz[0] > 0 and sz[1] > 0:
                # Include a generous 16px border around the bottom/right for the resize grip
                return (pos[0] - 6 <= mx <= pos[0] + sz[0] + 16) and (pos[1] - 6 <= my <= pos[1] + sz[1] + 16)
        except Exception:
            pass
        return False

    def _refresh_perf_popup_ui(self) -> None:
        node_id = self._perf_popup_node_id
        if node_id is None or not dpg.does_item_exist(self.perf_popup_tag) or not dpg.is_item_shown(self.perf_popup_tag):
            return
        inst = self.node_map.get(node_id)
        if inst is None:
            return
        rec = self._get_or_create_record(inst)

        with rec.lock:
            tp = rec.tracked_port

            dpg.set_value(f"{self.perf_popup_tag}_state", f"[{rec.flow_state_text}]")
            dpg.configure_item(f"{self.perf_popup_tag}_state", color=rec.flow_state_color)

            combo_tag = f"{self.perf_popup_tag}_port_combo"
            if dpg.does_item_exist(combo_tag):
                opts = rec.get_available_port_options(inst)
                dpg.configure_item(combo_tag, items=opts)
                if dpg.get_value(combo_tag) != tp and tp in opts:
                    dpg.set_value(combo_tag, tp)

            rate_hz = (
                rec.in_rate_hz
                if (tp == SINK_NO_OUTPUT_OPTION or rec.out_rate_hz <= 0.0)
                else rec.out_rate_hz
            )
            dpg.set_value(
                f"{self.perf_popup_tag}_turn",
                f"Exec time : {_fmt_ms(rec.last_in_to_out_ms)} ({rate_hz:.1f} Hz)",
            )

            q_tag = f"{self.perf_popup_tag}_queue"
            if dpg.does_item_exist(q_tag):
                if rec.q_has_worker:
                    dpg.set_value(
                        q_tag,
                        f"Queue : [{rec.drop_policy}]  Dropped : {rec.dropped_count}",
                    )
                    dpg.configure_item(q_tag, show=True)
                else:
                    dpg.configure_item(q_tag, show=False)

            if len(rec.ts_time) >= 2:
                t_last = rec.ts_time[-1]
                xs = [t - t_last for t in list(rec.ts_time)[-75:]]
                # True internal Exec time (ms), falling back to self execution time for pure sinks
                if tp in rec.ts_port_turnaround_ms and rec.port_in_to_out_ms.get(tp):
                    ys_turn = list(rec.ts_port_turnaround_ms[tp])[-75:]
                elif rec.ts_turnaround_ms and any(v > 0 for v in rec.ts_turnaround_ms):
                    ys_turn = list(rec.ts_turnaround_ms)[-75:]
                else:
                    ys_turn = list(rec.ts_self_ms)[-75:]
                n_pts = min(len(xs), len(ys_turn))
                if n_pts >= 2:
                    xs = xs[-n_pts:]
                    ys_turn = ys_turn[-n_pts:]
                    dpg.set_value(f"{self.perf_popup_tag}_ser_self", [xs, ys_turn])
                    dpg.set_axis_limits(f"{self.perf_popup_tag}_x_ax", min(-15.0, xs[0]), 0.0)
                    max_y = max(max(ys_turn, default=0.05), 0.005)
                    dpg.set_axis_limits(f"{self.perf_popup_tag}_y_ax", 0.0, max_y * 1.25)
            else:
                dpg.set_value(f"{self.perf_popup_tag}_ser_self", [[], []])

    def _on_popup_view_report_clicked(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        node_id = self._perf_popup_node_id
        if node_id is not None:
            self.open_node_perf_report_window(node_id)
            self._close_perf_popup()

    # =========================================================================
    # MULTI-INSTANCE STANDALONE MODULE REPORT WINDOWS ("VIEW REPORT")
    # =========================================================================

    @staticmethod
    def _build_series_catalog(out_keys: List[str]) -> Dict[str, str]:
        """Return ordered mapping of `series_key -> display_label` available for a module."""
        cat: Dict[str, str] = {
            "in_rate": "Input Rate (/s)",
            "out_rate": "Output Rate (/s)",
            "turn_mean": "Exec Time (ms)",
            "turn_p95": "Exec Time P95 (ms)",
            "self_ms": "input_cb() Time (ms)",
            "queue_pct": "Queue Fill (%)",
            "is_waiting": "Is Waiting (0/1)",
            "mem_rss": "Process Memory (MB)",
            "mem_buffers": "Buffers / Data Size (MB)",
        }
        for k in out_keys:
            cat[f"port_rate:{k}"] = f"Port '{k}' Rate (/s)"
            cat[f"port_turn:{k}"] = f"Port '{k}' Exec Time (ms)"
        return cat

    def open_node_perf_report_window(self, node_id: int) -> None:
        """
        Open or focus a standalone live Performance Report Window for `node_id`.
        Features:
          - Line 1: Static Module Name, Class, and UUID.
          - Line 2: Dynamic Flow Status.
          - Line 3: `Tracked output` selector.
          - Tab 1 (`Live Plot`): Single full-height interactive plot with Resizable Multi-Select
            Series & Y-Axes window, legend right-click menu, `Lineplot_win`-style cursor hover
            annotation, `drag_int` history, and File Explorer export.
          - Tab 2 (`Summary & Diagnostics`): Structured tables (`dpg.table`) for module telemetry
            and active output ports (unused outputs and absent queues are hidden).
        """
        inst = self.node_map.get(node_id)
        if inst is None:
            return
        rec = self._get_or_create_record(inst)
        uid = rec.uid
        self._ensure_profiler_running()

        existing = self._perf_report_windows.get(uid)
        if existing and dpg.does_item_exist(existing["win_tag"]):
            dpg.show_item(existing["win_tag"])
            dpg.focus_item(existing["win_tag"])
            return

        win_tag = f"ne_perf_report_win_{uid}_{dpg.generate_uuid()}"
        offset = (len(self._perf_report_windows) % 6) * 36
        pos = (140 + offset, 90 + offset)
        out_keys = list(getattr(inst, "outputs", {}).keys())
        catalog = self._build_series_catalog(out_keys)

        with rec.lock:
            is_auto = rec.is_auto_triggered or (not getattr(inst, "inputs", {}) and bool(out_keys))
        default_keys = ("out_rate", "turn_mean") if is_auto else ("in_rate", "out_rate")
        active_series: Dict[str, bool] = {k: (k in default_keys) for k in catalog}
        series_axis: Dict[str, str] = {
            k: (
                AXIS_SEC1
                if (k in ("mem_rss", "mem_buffers") or (is_auto and k == "turn_mean"))
                else AXIS_PRIMARY
            )
            for k in catalog
        }
        series_colors: Dict[str, List[int]] = {
            k: list(_IMPLOT_COLORS[idx % len(_IMPLOT_COLORS)])
            for idx, k in enumerate(catalog)
        }

        plot_tag = f"{win_tag}_plot"
        xaxis_tag = f"{win_tag}_x_ax"
        yaxis_tag = f"{win_tag}_y_ax1"
        anot_tag = f"{win_tag}_anot"
        anot_check_tag = f"{win_tag}_anot_chk"
        autoscale_check_tag = f"{win_tag}_autoscale_chk"
        series_btn_tag = f"{win_tag}_series_btn"
        axes_popup_tag = f"{win_tag}_axes_popup"
        axes_popup_content_tag = f"{win_tag}_axes_popup_content"
        annot_combo_tag = f"{win_tag}_annot_combo"
        handler_tag = f"{win_tag}_handlers"

        report_ctx: Dict[str, Any] = {
            "uid": uid,
            "node_id": node_id,
            "win_tag": win_tag,
            "plot_tag": plot_tag,
            "xaxis_tag": xaxis_tag,
            "yaxis_tag": yaxis_tag,
            "secondary_axes": {},
            "anot_tag": anot_tag,
            "anot_check_tag": anot_check_tag,
            "autoscale_check_tag": autoscale_check_tag,
            "series_btn_tag": series_btn_tag,
            "axes_popup_tag": axes_popup_tag,
            "axes_popup_content_tag": axes_popup_content_tag,
            "annot_combo_tag": annot_combo_tag,
            "handler_tag": handler_tag,
            "paused": False,
            "autoscale": True,
            "annot_active": False,
            "selected_serie": None,
            "window_s": 30,
            "catalog": catalog,
            "active_series": active_series,
            "series_axis": series_axis,
            "series_colors": series_colors,
            "bound_series": {},
            "serie_tag_to_key": {},
            "popup_row_tags": {},
            "popup_chk_tags": {},
            "popup_ax_tags": {},
            "annot_label_to_tag": {"Auto (Closest Series)": None},
        }

        def _on_report_close(s: Any = None, a: Any = None, u: Any = None, *args: Any) -> None:
            self._perf_report_windows.pop(uid, None)
            if dpg.does_item_exist(handler_tag):
                try:
                    dpg.delete_item(handler_tag)
                except Exception:
                    pass
            if dpg.does_item_exist(axes_popup_tag):
                try:
                    dpg.delete_item(axes_popup_tag)
                except Exception:
                    pass
            for s_key in list(series_colors):
                th_tag = f"{win_tag}_scolor_{s_key}"
                if dpg.does_item_exist(th_tag):
                    try:
                        dpg.delete_item(th_tag)
                    except Exception:
                        pass
            if dpg.does_item_exist(win_tag):
                try:
                    dpg.delete_item(win_tag)
                except Exception:
                    pass
            self._check_and_stop_profiler_if_unused()

        def _on_toggle_series_popup(s: Any = None, a: Any = None, u: Any = None, *args: Any) -> None:
            if not dpg.does_item_exist(axes_popup_tag):
                return
            if dpg.is_item_shown(axes_popup_tag):
                dpg.configure_item(axes_popup_tag, show=False)
            else:
                try:
                    bx, by = dpg.get_item_rect_min(series_btn_tag)
                    _, bh = dpg.get_item_rect_size(series_btn_tag)
                    dpg.set_item_pos(axes_popup_tag, [int(bx), int(by + bh + 4)])
                except Exception:
                    pass
                dpg.configure_item(axes_popup_tag, show=True)
                dpg.focus_item(axes_popup_tag)

        def _on_toggle_pause(s: Any, a: Any, u: Any = None) -> None:
            report_ctx["paused"] = not report_ctx["paused"]
            if dpg.does_item_exist(f"{win_tag}_pause_btn"):
                dpg.configure_item(
                    f"{win_tag}_pause_btn",
                    label="Resume" if report_ctx["paused"] else "Pause",
                )

        def _on_reset_node(s: Any = None, a: Any = None, u: Any = None) -> None:
            rec.reset()

        def _on_export_report(s: Any = None, a: Any = None, u: Any = None) -> None:
            self._export_single_node_report(rec, win_tag)

        def _on_report_port_selected(s: Any, a: str, u: Any = None) -> None:
            with rec.lock:
                rec.tracked_port = str(a)
                rec.user_selected_port = True
            if self._perf_popup_node_id == node_id:
                p_combo = f"{self.perf_popup_tag}_port_combo"
                if dpg.does_item_exist(p_combo):
                    dpg.set_value(p_combo, str(a))
            self._refresh_single_report_window(report_ctx, force_redraw=True)

        def _on_history_changed(s: Any, val: int, u: Any = None) -> None:
            report_ctx["window_s"] = max(2, int(val))
            self._refresh_single_report_window(report_ctx, force_redraw=True)

        def _on_autoscale_toggled(s: Any, val: bool, u: Any = None) -> None:
            report_ctx["autoscale"] = bool(val)
            if val:
                self._autofit_report_axes(report_ctx, x=False, y=True)
                self._refresh_single_report_window(report_ctx, force_redraw=True)
            else:
                for ax in self._get_all_report_y_axes(report_ctx) + [xaxis_tag]:
                    if dpg.does_item_exist(ax):
                        dpg.set_axis_limits_auto(ax)

        with rec.lock:
            port_options = rec.get_available_port_options(inst)
            if rec.tracked_port not in port_options:
                rec.tracked_port = port_options[0]
            initial_port = rec.tracked_port

        with dpg.window(
            label=f"Performance Report: {rec.label}",
            tag=win_tag,
            width=740,
            height=560,
            pos=pos,
            on_close=_on_report_close,
        ):
            # Line 1: Static identity (Name, Class, UUID) — never shifts
            dpg.add_text(
                f"{rec.label}   |   Class: {rec.cls_name}   |   UUID: {uid}",
                tag=f"{win_tag}_hdr_title",
                color=COLOR_ACCENT,
            )

            # Line 2: Status on its own line
            with dpg.group(horizontal=True):
                dpg.add_text("Status:", color=(170, 175, 185))
                dpg.add_text("[FLOWING]", tag=f"{win_tag}_hdr_state", color=COLOR_FLOWING)

            # Line 3: Tracked output on its own line
            with dpg.group(horizontal=True):
                dpg.add_text("Tracked output:", color=(180, 210, 240))
                dpg.add_combo(
                    items=port_options,
                    default_value=initial_port,
                    tag=f"{win_tag}_port_combo",
                    width=220,
                    callback=_on_report_port_selected,
                )
                dpg.add_spacer(width=8)
                dpg.add_text("", tag=f"{win_tag}_export_msg", color=COLOR_FLOWING)

            dpg.add_separator()

            with dpg.tab_bar(tag=f"{win_tag}_tabs"):
                # =============================================================
                # TAB 1: SINGLE FULL-HEIGHT INTERACTIVE LIVE PLOT
                # =============================================================
                with dpg.tab(label="Live Plot"):
                    with dpg.group(horizontal=True):
                        # Multi-Select Series & Y-Axes Button (opens resizable floating window)
                        dpg.add_button(
                            label="Series  v",
                            tag=series_btn_tag,
                            width=85,
                            callback=_on_toggle_series_popup,
                        )

                        dpg.add_checkbox(
                            label="Annotation",
                            tag=anot_check_tag,
                            default_value=True,
                            callback=lambda s, a: None if a else self._hide_report_annotation(report_ctx, force=True),
                        )
                        dpg.add_checkbox(
                            label="Autoscale",
                            tag=autoscale_check_tag,
                            default_value=True,
                            callback=_on_autoscale_toggled,
                        )
                        dpg.add_drag_int(
                            label="History (s)",
                            default_value=30,
                            min_value=2,
                            max_value=120,
                            width=52,
                            callback=_on_history_changed,
                        )
                        dpg.add_button(
                            label="Pause",
                            tag=f"{win_tag}_pause_btn",
                            width=58,
                            callback=_on_toggle_pause,
                        )
                        dpg.add_button(
                            label="Reset",
                            width=52,
                            callback=_on_reset_node,
                        )
                        dpg.add_button(
                            label="Export...",
                            width=68,
                            callback=_on_export_report,
                        )

                    with dpg.plot(
                        label="",
                        height=-1,
                        width=-1,
                        tag=plot_tag,
                        anti_aliased=True,
                        crosshairs=True,
                    ):
                        dpg.add_plot_legend()
                        dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag=xaxis_tag)
                        dpg.add_plot_axis(dpg.mvYAxis, label="Primary (Y1)", tag=yaxis_tag)
                        dpg.add_plot_annotation(
                            tag=anot_tag,
                            label="",
                            default_value=_OFFSCREEN,
                            offset=(25, -25),
                            color=[255, 255, 0, 255],
                            clamped=False,
                            show=False,
                        )

                # =============================================================
                # TAB 2: SUMMARY & DIAGNOSTICS (ALIGNED TABLES)
                # =============================================================
                with dpg.tab(label="Summary & Diagnostics"):
                    with dpg.child_window(border=False, height=-1, width=-1):
                        dpg.add_spacer(height=4)
                        dpg.add_text("Module Telemetry Summary", color=(180, 210, 240))
                        with dpg.table(
                            header_row=True,
                            row_background=True,
                            borders_innerH=True,
                            borders_outerH=True,
                            borders_innerV=True,
                            borders_outerV=True,
                            policy=dpg.mvTable_SizingStretchProp,
                            tag=f"{win_tag}_kpi_table",
                        ):
                            dpg.add_table_column(label="Metric", init_width_or_weight=1.35)
                            dpg.add_table_column(label="Current / Mean", init_width_or_weight=1.1)
                            dpg.add_table_column(label="P95", init_width_or_weight=0.85)
                            dpg.add_table_column(label="Max / Policy", init_width_or_weight=1.05)
                            dpg.add_table_column(label="Total / Details", init_width_or_weight=1.2)

                            # Row 1: Input Rate
                            with dpg.table_row(tag=f"{win_tag}_row_in"):
                                dpg.add_text("Input Rate", tag=f"{win_tag}_c_in_lbl")
                                dpg.add_text("0.0 Hz", tag=f"{win_tag}_c_in_val")
                                dpg.add_text("--")
                                dpg.add_text("--")
                                dpg.add_text("0 pkts", tag=f"{win_tag}_c_in_tot")

                            # Row 2: Tracked Output Rate (hidden if no output port is used)
                            with dpg.table_row(tag=f"{win_tag}_row_out"):
                                dpg.add_text("Output Rate", tag=f"{win_tag}_c_out_lbl")
                                dpg.add_text("0.0 Hz", tag=f"{win_tag}_c_out_val")
                                dpg.add_text("--")
                                dpg.add_text("--")
                                dpg.add_text("0 pkts", tag=f"{win_tag}_c_out_tot")

                            # Row 3: Module Internal Exec Time (hidden if no output port is used)
                            with dpg.table_row(tag=f"{win_tag}_row_turn"):
                                dpg.add_text("Exec Time", tag=f"{win_tag}_c_turn_lbl")
                                dpg.add_text("0.00 ms", tag=f"{win_tag}_c_turn_mean")
                                dpg.add_text("0.00 ms", tag=f"{win_tag}_c_turn_p95")
                                dpg.add_text("0.00 ms", tag=f"{win_tag}_c_turn_max")
                                dpg.add_text("Last: 0.00 ms", tag=f"{win_tag}_c_turn_last")

                            # Row 4: input_cb() Execution Time (or Frame Prep + Emit Time for Auto-Trigger)
                            with dpg.table_row(tag=f"{win_tag}_row_self"):
                                dpg.add_text("input_cb() Exec Time", tag=f"{win_tag}_c_self_lbl")
                                dpg.add_text("0.00 ms", tag=f"{win_tag}_c_self_mean")
                                dpg.add_text("0.00 ms", tag=f"{win_tag}_c_self_p95")
                                dpg.add_text("--")
                                dpg.add_text("Last: 0.00 ms", tag=f"{win_tag}_c_self_last")

                            # Row 5: Worker Queue (hidden if module has no queue)
                            with dpg.table_row(tag=f"{win_tag}_row_queue", show=False):
                                dpg.add_text("Worker Queue")
                                dpg.add_text("0 / 1 (0%)", tag=f"{win_tag}_c_q_fill")
                                dpg.add_text("--")
                                dpg.add_text("Policy: N/A", tag=f"{win_tag}_c_q_pol")
                                dpg.add_text("Dropped: 0", tag=f"{win_tag}_c_q_drop")

                            # Row 6: Is Waiting (0/1)
                            with dpg.table_row(tag=f"{win_tag}_row_wait"):
                                dpg.add_text("Is Waiting (0/1)")
                                dpg.add_text("0 (READY)", tag=f"{win_tag}_c_wait_val")
                                dpg.add_text("--")
                                dpg.add_text("--")
                                dpg.add_text("Self: OK | Down: OK", tag=f"{win_tag}_c_wait_det")

                            # Row 7: Downstream & UI Status
                            with dpg.table_row(tag=f"{win_tag}_row_sys"):
                                dpg.add_text("Downstream & UI")
                                dpg.add_text("READY", tag=f"{win_tag}_c_sys_down")
                                dpg.add_text("--")
                                dpg.add_text("UI: -- FPS", tag=f"{win_tag}_c_sys_fps")
                                dpg.add_text("FLOWING", tag=f"{win_tag}_c_sys_state")

                            # Row 8: Memory Footprint (OS RSS + Internal Buffers)
                            with dpg.table_row(tag=f"{win_tag}_row_mem"):
                                dpg.add_text("Memory Footprint")
                                dpg.add_text("0.0 MB", tag=f"{win_tag}_c_mem_rss")
                                dpg.add_text("--")
                                dpg.add_text("Peak: 0.0 MB", tag=f"{win_tag}_c_mem_peak")
                                dpg.add_text("Main Process | Buffers: 0.0 MB", tag=f"{win_tag}_c_mem_det")

                        # Active Output Ports Table (only shown when >= 1 output port is used)
                        with dpg.group(tag=f"{win_tag}_ports_section", show=bool(out_keys)):
                            dpg.add_spacer(height=10)
                            dpg.add_text("Active Output Ports & Downstream Links", color=(180, 210, 240))
                            with dpg.table(
                                header_row=True,
                                row_background=True,
                                borders_innerH=True,
                                borders_outerH=True,
                                borders_innerV=True,
                                borders_outerV=True,
                                policy=dpg.mvTable_SizingStretchProp,
                                tag=f"{win_tag}_ports_table",
                            ):
                                dpg.add_table_column(label="Output Port", init_width_or_weight=1.1)
                                dpg.add_table_column(label="Rate (Hz)", init_width_or_weight=0.8)
                                dpg.add_table_column(label="Packets", init_width_or_weight=0.75)
                                dpg.add_table_column(label="Exec Time Mean", init_width_or_weight=0.95)
                                dpg.add_table_column(label="Exec Time P95", init_width_or_weight=0.95)
                                dpg.add_table_column(label="Downstream Targets", init_width_or_weight=1.6)

                                for p_key in out_keys:
                                    with dpg.table_row(tag=f"{win_tag}_prow_{p_key}", show=False):
                                        dpg.add_text(p_key, tag=f"{win_tag}_pc_name_{p_key}")
                                        dpg.add_text("0.0 Hz", tag=f"{win_tag}_pc_rate_{p_key}")
                                        dpg.add_text("0", tag=f"{win_tag}_pc_pkts_{p_key}")
                                        dpg.add_text("0.00 ms", tag=f"{win_tag}_pc_mean_{p_key}")
                                        dpg.add_text("0.00 ms", tag=f"{win_tag}_pc_p95_{p_key}")
                                        dpg.add_text("--", tag=f"{win_tag}_pc_tgts_{p_key}")

        # Resizable floating Series & Y-Axes selector window associated with `series_btn_tag`
        with dpg.window(
            label=f"Series & Y-Axes — {rec.label}",
            tag=axes_popup_tag,
            show=False,
            no_resize=False,
            no_collapse=True,
            width=420,
            height=340,
            min_size=(280, 180),
            pos=(pos[0] + 20, pos[1] + 135),
        ):
            with dpg.group(tag=axes_popup_content_tag):
                self._build_report_series_dropdown_content(report_ctx, rec, inst)

        with dpg.handler_registry(tag=handler_tag):
            dpg.add_mouse_move_handler(
                callback=lambda s, a: self._on_report_plot_mouse_move(report_ctx)
            )
            dpg.add_mouse_click_handler(
                button=dpg.mvMouseButton_Right,
                callback=lambda s, a: self._hide_report_annotation(report_ctx, force=True),
            )

        self._perf_report_windows[uid] = report_ctx
        self._sync_report_plot_series(report_ctx, rec, inst)
        self._refresh_single_report_window(report_ctx, force_redraw=True)

    # =========================================================================
    # REPORT PLOT SERIES, MULTI-AXIS, LEGEND MENU & HOVER ANNOTATION
    # =========================================================================

    def _update_series_dropdown_label(self, ctx: Dict[str, Any]) -> None:
        """Keep the series dropdown button cleanly labeled as 'Series  v'."""
        btn_tag = ctx.get("series_btn_tag")
        if not btn_tag or not dpg.does_item_exist(btn_tag):
            return
        dpg.configure_item(btn_tag, label="Series  v")

    def _build_report_series_dropdown_content(
        self, ctx: Dict[str, Any], rec: _NodePerfRecord, inst: Any = None
    ) -> None:
        """Build the persistent multi-select checkboxes & Y-axis combos inside the resizable Series window."""
        catalog: Dict[str, str] = ctx["catalog"]
        win_tag = ctx["win_tag"]
        used_ports = set(rec.get_used_output_ports(inst))

        dpg.add_text("Displayed Series & Y-Axes", color=(150, 200, 255))
        dpg.add_separator()

        def _on_toggle_serie(s: Any, enabled: bool, key: str) -> None:
            ctx["active_series"][key] = bool(enabled)
            self._sync_report_plot_series(ctx, rec, self.node_map.get(ctx.get("node_id")))
            self._refresh_single_report_window(ctx, force_redraw=True)

        def _on_change_serie_axis(s: Any, ax_name: str, key: str) -> None:
            ctx["series_axis"][key] = str(ax_name)
            ctx["active_series"][key] = True
            chk = ctx["popup_chk_tags"].get(key)
            if chk and dpg.does_item_exist(chk):
                dpg.set_value(chk, True)
            self._sync_report_plot_series(ctx, rec, self.node_map.get(ctx.get("node_id")))
            self._refresh_single_report_window(ctx, force_redraw=True)

        with dpg.table(
            header_row=True,
            borders_innerH=True,
            borders_innerV=True,
            resizable=True,
            policy=dpg.mvTable_SizingStretchProp,
        ):
            dpg.add_table_column(label="Series (Check to Combine)", init_width_or_weight=1.45)
            dpg.add_table_column(label="Y-Axis", init_width_or_weight=1.0)

            for s_key, label in catalog.items():
                is_port_specific = s_key.startswith("port_rate:") or s_key.startswith("port_turn:")
                port_name = s_key.split(":", 1)[1] if is_port_specific else ""
                show_row = True
                if s_key == "queue_pct" and not rec.q_has_worker:
                    show_row = False
                elif is_port_specific and port_name not in used_ports:
                    show_row = False

                row_tag = f"{win_tag}_ddrow_{s_key}"
                chk_tag = f"{win_tag}_ddchk_{s_key}"
                ax_tag = f"{win_tag}_ddax_{s_key}"
                ctx["popup_row_tags"][s_key] = row_tag
                ctx["popup_chk_tags"][s_key] = chk_tag
                ctx["popup_ax_tags"][s_key] = ax_tag

                with dpg.table_row(tag=row_tag, show=show_row):
                    dpg.add_checkbox(
                        label=label,
                        tag=chk_tag,
                        default_value=ctx["active_series"].get(s_key, False),
                        callback=_on_toggle_serie,
                        user_data=s_key,
                    )
                    dpg.add_combo(
                        items=_AXIS_OPTIONS,
                        tag=ax_tag,
                        default_value=ctx["series_axis"].get(s_key, AXIS_PRIMARY),
                        callback=_on_change_serie_axis,
                        user_data=s_key,
                        width=-1,
                    )

        dpg.add_separator()
        dpg.add_text("Annotation Target Series:", color=(150, 200, 255))

        def _on_annot_target_change(s: Any, a: str, u: Any = None) -> None:
            ctx["selected_serie"] = ctx["annot_label_to_tag"].get(a)

        dpg.add_combo(
            items=["Auto (Closest Series)"],
            default_value="Auto (Closest Series)",
            tag=ctx["annot_combo_tag"],
            callback=_on_annot_target_change,
            width=-1,
        )

    def _get_or_create_report_axis(self, ctx: Dict[str, Any], axis_name: str) -> str:
        """Retrieve or create primary or secondary right Y-axis on the report plot."""
        if axis_name not in _AXIS_OPTIONS:
            axis_name = AXIS_PRIMARY
        if axis_name == AXIS_PRIMARY:
            return ctx["yaxis_tag"]

        win_tag = ctx["win_tag"]
        plot_tag = ctx["plot_tag"]
        sec_axes: Dict[str, str] = ctx["secondary_axes"]

        if axis_name == AXIS_SEC2:
            slot_type, label, tag = dpg.mvYAxis3, "Secondary 2 (Y3)", f"{win_tag}_y_ax3"
        else:
            slot_type, label, tag = dpg.mvYAxis2, "Secondary 1 (Y2)", f"{win_tag}_y_ax2"

        if axis_name in sec_axes and dpg.does_item_exist(tag):
            return tag

        if dpg.does_item_exist(tag):
            dpg.set_item_label(tag, label)
        else:
            dpg.add_plot_axis(slot_type, label=label, tag=tag, parent=plot_tag, opposite=True)
            dpg.set_axis_limits_auto(tag)
            dpg.fit_axis_data(tag)
        sec_axes[axis_name] = tag
        return tag

    def _get_all_report_y_axes(self, ctx: Dict[str, Any]) -> List[str]:
        return [ctx["yaxis_tag"]] + [
            t for t in ctx["secondary_axes"].values() if dpg.does_item_exist(t)
        ]

    def _autofit_report_axes(self, ctx: Dict[str, Any], x: bool = False, y: bool = True) -> None:
        """Fit report plot horizontal and vertical axes to active data bounds like Lineplot."""
        axes = ([ctx["xaxis_tag"]] if x else []) + (self._get_all_report_y_axes(ctx) if y else [])
        for tag in axes:
            if dpg.does_item_exist(tag):
                dpg.set_axis_limits_auto(tag)
                dpg.fit_axis_data(tag)

    def _cleanup_unused_report_axes(self, ctx: Dict[str, Any]) -> None:
        used_axes = {
            ctx["series_axis"].get(k, AXIS_PRIMARY)
            for k, enabled in ctx["active_series"].items()
            if enabled
        }
        sec_axes: Dict[str, str] = ctx["secondary_axes"]
        for ax_name in list(sec_axes.keys()):
            if ax_name not in used_axes:
                tag = sec_axes.pop(ax_name)
                if dpg.does_item_exist(tag):
                    try:
                        dpg.delete_item(tag)
                    except Exception:
                        pass

    def _set_report_series_theme(self, ctx: Dict[str, Any], serie_tag: str, s_key: str, color: List[int]) -> None:
        win_tag = ctx["win_tag"]
        theme_tag = f"{win_tag}_scolor_{s_key}"
        if dpg.does_item_exist(theme_tag):
            dpg.delete_item(theme_tag, children_only=True)
        else:
            dpg.add_theme(tag=theme_tag)
        tc = dpg.add_theme_component(dpg.mvLineSeries, parent=theme_tag)
        dpg.add_theme_color(dpg.mvPlotCol_Line, color[:4], category=dpg.mvThemeCat_Plots, parent=tc)
        if dpg.does_item_exist(serie_tag):
            dpg.bind_item_theme(serie_tag, theme_tag)

    def _attach_report_legend_menu(self, ctx: Dict[str, Any], rec: _NodePerfRecord, serie_tag: str, s_key: str) -> None:
        """Attach right-click context menu (Select for annotation, Y-axis, Color) to legend item."""
        cur_color = ctx["series_colors"].get(s_key, list(_IMPLOT_COLORS[0]))
        self._set_report_series_theme(ctx, serie_tag, s_key, cur_color)

        dpg.add_selectable(
            label="Select for Annotation",
            callback=lambda *_: ctx.__setitem__("selected_serie", serie_tag),
            parent=serie_tag,
        )
        dpg.add_separator(parent=serie_tag)

        short_by_axis = dict(zip(_AXIS_OPTIONS, _AXIS_SHORT))
        axis_by_short = dict(zip(_AXIS_SHORT, _AXIS_OPTIONS))

        def _on_leg_axis(s: Any, a: str, key: str) -> None:
            new_ax = axis_by_short.get(a, AXIS_PRIMARY)
            ctx["series_axis"][key] = new_ax
            ax_c = ctx["popup_ax_tags"].get(key)
            if ax_c and dpg.does_item_exist(ax_c):
                dpg.set_value(ax_c, new_ax)
            self._sync_report_plot_series(ctx, rec, self.node_map.get(ctx.get("node_id")))
            self._refresh_single_report_window(ctx, force_redraw=True)

        with dpg.group(horizontal=True, parent=serie_tag):
            dpg.add_text("Y axis :")
            dpg.add_combo(
                items=_AXIS_SHORT,
                default_value=short_by_axis.get(ctx["series_axis"].get(s_key, AXIS_PRIMARY), "Default"),
                callback=_on_leg_axis,
                user_data=s_key,
                width=105,
            )
        dpg.add_separator(parent=serie_tag)

        def _on_leg_color(s: Any, a: Any, u: Tuple[str, str]) -> None:
            key, s_id = u
            raw = list(a)[:3]
            mult = 255 if isinstance(raw[0], float) and max(raw) <= 1.0 else 1
            ctx["series_colors"][key] = [int(c * mult) for c in raw] + [255]
            self._set_report_series_theme(ctx, s_id, key, ctx["series_colors"][key])

        dpg.add_color_edit(
            label="Color",
            default_value=cur_color,
            callback=_on_leg_color,
            user_data=(s_key, serie_tag),
            parent=serie_tag,
            no_inputs=True,
            no_alpha=True,
        )

    def _sync_report_plot_series(self, ctx: Dict[str, Any], rec: _NodePerfRecord, inst: Any = None) -> None:
        """Create, move across axes, or remove DPG line series according to `ctx['active_series']`."""
        win_tag = ctx["win_tag"]
        catalog: Dict[str, str] = ctx["catalog"]
        bound: Dict[str, Dict[str, Any]] = ctx["bound_series"]
        tag_to_key: Dict[str, str] = ctx["serie_tag_to_key"]

        # Remove series that were disabled or changed axis
        for s_key in list(bound.keys()):
            info = bound[s_key]
            s_tag = info["serie_tag"]
            want_enabled = ctx["active_series"].get(s_key, False)
            want_axis_name = ctx["series_axis"].get(s_key, AXIS_PRIMARY)
            if not want_enabled or info.get("axis_name") != want_axis_name:
                if dpg.does_item_exist(s_tag):
                    try:
                        dpg.delete_item(s_tag)
                    except Exception:
                        pass
                tag_to_key.pop(s_tag, None)
                bound.pop(s_key, None)

        # Add enabled series
        for s_key, label in catalog.items():
            if not ctx["active_series"].get(s_key, False):
                continue
            if s_key in bound and dpg.does_item_exist(bound[s_key]["serie_tag"]):
                continue
            ax_name = ctx["series_axis"].get(s_key, AXIS_PRIMARY)
            ax_tag = self._get_or_create_report_axis(ctx, ax_name)
            s_tag = f"{win_tag}_ser_{s_key}"
            if dpg.does_item_exist(s_tag):
                dpg.delete_item(s_tag)
            dpg.add_line_series([], [], label=label, parent=ax_tag, tag=s_tag)
            bound[s_key] = {
                "serie_tag": s_tag,
                "axis_name": ax_name,
                "axis_tag": ax_tag,
                "label": label,
                "xs": np.empty(0, dtype=float),
                "ys": np.empty(0, dtype=float),
            }
            tag_to_key[s_tag] = s_key
            self._attach_report_legend_menu(ctx, rec, s_tag, s_key)

        self._cleanup_unused_report_axes(ctx)
        self._update_series_dropdown_label(ctx)
        if ctx.get("autoscale", True):
            self._autofit_report_axes(ctx, x=False, y=True)

        # Update Annotation Target combo items in-place
        annot_combo = ctx.get("annot_combo_tag")
        if annot_combo and dpg.does_item_exist(annot_combo):
            auto_lbl = "Auto (Closest Series)"
            annot_map: Dict[str, Optional[str]] = {auto_lbl: None}
            cur_lbl = auto_lbl
            for s_key, info in bound.items():
                lbl = info["label"]
                s_tag = info["serie_tag"]
                annot_map[lbl] = s_tag
                if ctx.get("selected_serie") == s_tag:
                    cur_lbl = lbl
            ctx["annot_label_to_tag"] = annot_map
            dpg.configure_item(annot_combo, items=list(annot_map.keys()))
            dpg.set_value(annot_combo, cur_lbl)

    @staticmethod
    def _report_axis_unit_fn(axis_tag: str) -> Optional[Callable[[Any], np.ndarray]]:
        if not dpg.does_item_exist(axis_tag):
            return None
        try:
            lo, hi = dpg.get_axis_limits(axis_tag)
            span = hi - lo
            if not np.isfinite(span) or span == 0:
                return None
            return lambda v: (np.asarray(v, dtype=float) - lo) / span
        except Exception:
            return None

    def _is_mouse_over_report_legend(self, ctx: Dict[str, Any]) -> bool:
        plot_tag = ctx["plot_tag"]
        if not dpg.does_item_exist(plot_tag):
            return False
        try:
            p_min = dpg.get_item_rect_min(plot_tag)
            p_max = dpg.get_item_rect_max(plot_tag)
            mx, my = dpg.get_mouse_pos()
            plot_w, plot_h = p_max[0] - p_min[0], p_max[1] - p_min[1]
            if plot_w <= 0 or plot_h <= 0:
                return False
            n_ser = len(ctx["bound_series"])
            legend_w = min(280, plot_w * 0.40)
            legend_h = min(320, max(90, 28 * (n_ser + 1) + 40))
            return (p_max[0] - legend_w <= mx <= p_max[0]) and (p_min[1] <= my <= p_min[1] + legend_h)
        except Exception:
            return False

    def _hide_report_annotation(self, ctx: Dict[str, Any], force: bool = False) -> None:
        if not (force or ctx.get("annot_active")):
            return
        anot_tag = ctx["anot_tag"]
        if dpg.does_item_exist(anot_tag):
            kw = {"show": False} if force else {}
            dpg.configure_item(anot_tag, label="", default_value=_OFFSCREEN, clamped=False, **kw)
        ctx["annot_active"] = False

    def _on_report_plot_mouse_move(self, ctx: Dict[str, Any]) -> None:
        """Update on-hover point inspection tooltip on the report plot (`Lineplot_win` style)."""
        plot_tag = ctx["plot_tag"]
        if not ctx["bound_series"] or not dpg.does_item_exist(plot_tag) or not dpg.is_item_hovered(plot_tag):
            self._hide_report_annotation(ctx)
            return

        anot_chk = ctx["anot_check_tag"]
        if (
            dpg.is_mouse_button_down(dpg.mvMouseButton_Right)
            or self._is_mouse_over_report_legend(ctx)
            or (dpg.does_item_exist(anot_chk) and not dpg.get_value(anot_chk))
        ):
            self._hide_report_annotation(ctx, force=True)
            return

        try:
            p_min = dpg.get_item_rect_min(plot_tag)
            p_max = dpg.get_item_rect_max(plot_tag)
            w, h = max(1.0, p_max[0] - p_min[0]), max(1.0, p_max[1] - p_min[1])
        except Exception:
            w = h = 1.0

        xaxis_tag = ctx["xaxis_tag"]
        yaxis_tag = ctx["yaxis_tag"]
        fx = self._report_axis_unit_fn(xaxis_tag)
        fy_primary = self._report_axis_unit_fn(yaxis_tag)
        if fx is None or fy_primary is None:
            self._hide_report_annotation(ctx)
            return

        mouse_x, mouse_y = dpg.get_plot_mouse_pos()
        mx = float(fx(mouse_x))
        my = float(fy_primary(mouse_y))

        selected_tag = ctx.get("selected_serie")
        candidates = list(ctx["bound_series"].values())
        if selected_tag:
            filtered = [info for info in candidates if info["serie_tag"] == selected_tag]
            if filtered:
                candidates = filtered

        best = None
        best_d = np.inf
        for info in candidates:
            xs: np.ndarray = info.get("xs", np.empty(0))
            ys: np.ndarray = info.get("ys", np.empty(0))
            if xs.size == 0 or xs.size != ys.size:
                continue
            ax_tag = info.get("axis_tag", yaxis_tag)
            fy = fy_primary if ax_tag == yaxis_tag else (self._report_axis_unit_fn(ax_tag) or fy_primary)
            d = np.hypot((fx(xs) - mx) * w, (fy(ys) - my) * h)
            d = np.where(np.isfinite(d), d, np.inf)
            j = int(np.argmin(d))
            if d[j] < best_d:
                best_d = d[j]
                best = (info, j, float(xs[j]), float(ys[j]))

        if best is None:
            self._hide_report_annotation(ctx)
            return

        info, idx, x_val, y_val = best
        serie_label = info["label"]
        unit_suffix = ""
        if "MB" in serie_label:
            unit_suffix = " MB"
        elif "ms" in serie_label:
            unit_suffix = " ms"
        elif "(/s)" in serie_label or "Rate" in serie_label:
            unit_suffix = " /s"
        elif "(%)" in serie_label or "Fill" in serie_label:
            unit_suffix = " %"

        annot_str = f"[{serie_label}]\nIndex : {idx}\nX : {x_val:.3g} s\nY : {y_val:.4g}{unit_suffix}"

        # Project secondary-axis value onto primary Y1 so annotation sits directly on the curve
        ay = y_val
        ax_tag = info.get("axis_tag", yaxis_tag)
        if ax_tag != yaxis_tag:
            f_src = self._report_axis_unit_fn(ax_tag)
            lo1, hi1 = dpg.get_axis_limits(yaxis_tag)
            if f_src is not None:
                ay = lo1 + float(f_src(y_val)) * (hi1 - lo1)

        anot_tag = ctx["anot_tag"]
        if dpg.does_item_exist(anot_tag):
            dpg.configure_item(
                anot_tag,
                label=annot_str,
                default_value=(x_val, ay),
                clamped=True,
                show=True,
            )
            ctx["annot_active"] = True

    def _refresh_all_report_windows(self) -> None:
        for uid, ctx in list(self._perf_report_windows.items()):
            w_tag = ctx.get("win_tag")
            if not w_tag or not dpg.does_item_exist(w_tag):
                h_tag = ctx.get("handler_tag")
                if h_tag and dpg.does_item_exist(h_tag):
                    try:
                        dpg.delete_item(h_tag)
                    except Exception:
                        pass
                p_tag = ctx.get("axes_popup_tag")
                if p_tag and dpg.does_item_exist(p_tag):
                    try:
                        dpg.delete_item(p_tag)
                    except Exception:
                        pass
                self._perf_report_windows.pop(uid, None)
                continue
            if dpg.is_item_shown(w_tag):
                self._refresh_single_report_window(ctx)

    @staticmethod
    def _extract_series_values(
        rec: _NodePerfRecord, s_key: str, tp: str, start_idx: int
    ) -> List[float]:
        """Extract time-series slice for a given series key `s_key`."""
        if s_key == "in_rate":
            return list(rec.ts_in_rate)[start_idx:]
        if s_key == "out_rate":
            if tp in rec.ts_port_rates:
                return list(rec.ts_port_rates[tp])[start_idx:]
            return list(rec.ts_out_rate)[start_idx:]
        if s_key == "turn_mean":
            if tp in rec.ts_port_turnaround_ms and rec.port_in_to_out_ms.get(tp):
                return list(rec.ts_port_turnaround_ms[tp])[start_idx:]
            return list(rec.ts_turnaround_ms)[start_idx:]
        if s_key == "turn_p95":
            if tp in rec.ts_port_p95_ms and rec.port_in_to_out_ms.get(tp):
                return list(rec.ts_port_p95_ms[tp])[start_idx:]
            return list(rec.ts_p95_ms)[start_idx:]
        if s_key == "self_ms":
            return list(rec.ts_self_ms)[start_idx:]
        if s_key == "queue_pct":
            return list(rec.ts_queue_pct)[start_idx:]
        if s_key == "is_waiting":
            return list(rec.ts_waiting)[start_idx:]
        if s_key == "mem_rss":
            return list(rec.ts_mem_rss)[start_idx:]
        if s_key == "mem_buffers":
            return list(rec.ts_mem_buffers)[start_idx:]
        if s_key.startswith("port_rate:"):
            pk = s_key.split(":", 1)[1]
            if pk in rec.ts_port_rates:
                return list(rec.ts_port_rates[pk])[start_idx:]
            return []
        if s_key.startswith("port_turn:"):
            pk = s_key.split(":", 1)[1]
            if pk in rec.ts_port_turnaround_ms:
                return list(rec.ts_port_turnaround_ms[pk])[start_idx:]
            return []
        return []

    def _refresh_single_report_window(self, ctx: Dict[str, Any], force_redraw: bool = False) -> None:
        uid = ctx["uid"]
        win_tag = ctx["win_tag"]
        rec = self._perf_records.get(uid)
        if rec is None:
            return

        fps = float(dpg.get_frame_rate()) if dpg.is_dearpygui_running() else 0.0
        inst = self.node_map.get(ctx.get("node_id"))

        with rec.lock:
            used_ports = rec.get_used_output_ports(inst)
            used_set = set(used_ports)
            opts = rec.get_available_port_options(inst)
            if not rec.user_selected_port and rec.tracked_port not in opts:
                rec.tracked_port = opts[0]
            tp = rec.tracked_port
            has_used_out = bool(used_ports) or (tp != SINK_NO_OUTPUT_OPTION and rec.out_rate_hz > 0.0)
            port_lbl = f"({tp})" if tp != SINK_NO_OUTPUT_OPTION else ""

            r_combo = f"{win_tag}_port_combo"
            if dpg.does_item_exist(r_combo):
                dpg.configure_item(r_combo, items=opts)
                if dpg.get_value(r_combo) != tp and tp in opts:
                    dpg.set_value(r_combo, tp)

            dpg.set_value(f"{win_tag}_hdr_state", f"[{rec.flow_state_text}]")
            dpg.configure_item(f"{win_tag}_hdr_state", color=rec.flow_state_color)

            # Sync visibility of port-specific and queue rows inside the Series window
            for s_key, row_tag in ctx.get("popup_row_tags", {}).items():
                if not dpg.does_item_exist(row_tag):
                    continue
                if s_key == "queue_pct":
                    dpg.configure_item(row_tag, show=rec.q_has_worker)
                elif s_key.startswith("port_rate:") or s_key.startswith("port_turn:"):
                    p_name = s_key.split(":", 1)[1]
                    dpg.configure_item(row_tag, show=(p_name in used_set))

            # Update Tab 2: Table 1 (Module Telemetry Summary)
            if rec.is_auto_triggered and rec.in_rate_hz < 0.05:
                dpg.set_value(f"{win_tag}_c_in_lbl", "Trigger Mode")
                dpg.set_value(f"{win_tag}_c_in_val", "Auto-Trigger (Source)")
                dpg.set_value(f"{win_tag}_c_in_tot", f"{rec.total_in} cfg pkts")
            else:
                dpg.set_value(f"{win_tag}_c_in_lbl", "Input Rate")
                dpg.set_value(f"{win_tag}_c_in_val", f"{rec.in_rate_hz:.1f} Hz")
                dpg.set_value(f"{win_tag}_c_in_tot", f"{rec.total_in} pkts")

            if dpg.does_item_exist(f"{win_tag}_row_out"):
                dpg.configure_item(f"{win_tag}_row_out", show=has_used_out)
            if has_used_out:
                dpg.set_value(f"{win_tag}_c_out_lbl", f"Output Rate {port_lbl}".strip())
                dpg.set_value(f"{win_tag}_c_out_val", f"{rec.out_rate_hz:.1f} Hz")
                dpg.set_value(f"{win_tag}_c_out_tot", f"{rec.total_out} pkts")

            show_turn = has_used_out or bool(rec.in_to_out_ms) or rec.last_in_to_out_ms > 0.0
            if dpg.does_item_exist(f"{win_tag}_row_turn"):
                dpg.configure_item(f"{win_tag}_row_turn", show=show_turn)
            if show_turn:
                turn_lbl = f"Exec Time {port_lbl}".strip() if has_used_out else "Exec Time"
                dpg.set_value(f"{win_tag}_c_turn_lbl", turn_lbl)
                dpg.set_value(f"{win_tag}_c_turn_mean", _fmt_ms(rec.mean_turnaround_ms))
                dpg.set_value(f"{win_tag}_c_turn_p95", _fmt_ms(rec.p95_turnaround_ms))
                dpg.set_value(f"{win_tag}_c_turn_max", _fmt_ms(rec.max_turnaround_ms))
                dpg.set_value(f"{win_tag}_c_turn_last", f"Last: {_fmt_ms(rec.last_in_to_out_ms)}")

            self_title = "Frame Prep + Emit Time" if rec.is_auto_triggered else "input_cb() Exec Time"
            dpg.set_value(f"{win_tag}_c_self_lbl", self_title)
            dpg.set_value(f"{win_tag}_c_self_mean", _fmt_ms(rec.mean_self_ms))
            dpg.set_value(f"{win_tag}_c_self_p95", _fmt_ms(rec.p95_self_ms))
            dpg.set_value(f"{win_tag}_c_self_last", f"Last: {_fmt_ms(rec.last_self_ms)}")

            if dpg.does_item_exist(f"{win_tag}_row_queue"):
                dpg.configure_item(f"{win_tag}_row_queue", show=rec.q_has_worker)
            if rec.q_has_worker:
                dpg.set_value(
                    f"{win_tag}_c_q_fill",
                    f"{rec.q_size} / {rec.q_max} ({rec.q_fill_pct:.0f}%)",
                )
                dpg.set_value(f"{win_tag}_c_q_pol", f"Policy: {rec.drop_policy}")
                dpg.set_value(f"{win_tag}_c_q_drop", f"Dropped: {rec.dropped_count}")

            if dpg.does_item_exist(f"{win_tag}_row_wait"):
                w_int = int(round(rec.is_waiting_val))
                dpg.set_value(
                    f"{win_tag}_c_wait_val",
                    "1 (WAITING)" if w_int else "0 (READY)",
                )
                dpg.set_value(
                    f"{win_tag}_c_wait_det",
                    f"Self: {'OK' if rec.is_self_ready else 'HOLD'} | Down: {'OK' if rec.is_downstream_ready else 'HOLD'}",
                )

            down_str = "READY" if rec.is_downstream_ready else "HOLD / BLOCKED"
            dpg.set_value(f"{win_tag}_c_sys_down", down_str)
            dpg.set_value(f"{win_tag}_c_sys_fps", f"UI: {fps:.1f} FPS")
            dpg.set_value(f"{win_tag}_c_sys_state", rec.flow_state_text)

            if dpg.does_item_exist(f"{win_tag}_row_mem"):
                dpg.set_value(f"{win_tag}_c_mem_rss", f"{rec.mem_rss_mb:.1f} MB")
                dpg.set_value(f"{win_tag}_c_mem_peak", f"Peak: {rec.mem_peak_mb:.1f} MB")
                buf_str = (
                    f"Buffers: {rec.mem_buffers_mb:.1f} MB"
                    if rec.mem_buffers_mb >= 0.05
                    else "Buffers: < 0.1 MB"
                )
                dpg.set_value(
                    f"{win_tag}_c_mem_det",
                    f"{rec.mem_type_text} | {buf_str}",
                )

            # Update Tab 2: Table 2 (Active Output Ports — hide any unused outputs)
            if dpg.does_item_exist(f"{win_tag}_ports_section"):
                dpg.configure_item(f"{win_tag}_ports_section", show=bool(used_ports))

            conns = getattr(inst, "connections", {}) if inst is not None else {}
            for p_key, p_hz in rec.port_rates_hz.items():
                row_tag = f"{win_tag}_prow_{p_key}"
                if not dpg.does_item_exist(row_tag):
                    continue
                if p_key not in used_set:
                    dpg.configure_item(row_tag, show=False)
                    continue

                dpg.configure_item(row_tag, show=True)
                p_cnt = rec.port_total_out.get(p_key, 0)
                p_turn = rec.port_mean_turn_ms.get(p_key, 0.0)
                p_p95 = rec.port_p95_turn_ms.get(p_key, 0.0)
                targets = conns.get(p_key, []) if isinstance(conns, dict) else []
                tgt_names = []
                for t in targets:
                    t_lbl = getattr(t, "label", type(t).__name__)
                    t_rdy = True
                    r_fn = getattr(t, "is_ready", None)
                    if callable(r_fn):
                        try:
                            t_rdy = bool(r_fn())
                        except Exception:
                            t_rdy = True
                    tgt_names.append(f"{t_lbl} [{'OK' if t_rdy else 'HOLD'}]")
                tgt_str = ", ".join(tgt_names) if tgt_names else "--"
                prefix = "* " if p_key == tp else ""
                dpg.set_value(f"{win_tag}_pc_name_{p_key}", f"{prefix}{p_key}")
                dpg.set_value(f"{win_tag}_pc_rate_{p_key}", f"{p_hz:.1f} Hz")
                dpg.set_value(f"{win_tag}_pc_pkts_{p_key}", f"{p_cnt}")
                dpg.set_value(f"{win_tag}_pc_mean_{p_key}", _fmt_ms(p_turn))
                dpg.set_value(f"{win_tag}_pc_p95_{p_key}", _fmt_ms(p_p95))
                dpg.set_value(f"{win_tag}_pc_tgts_{p_key}", tgt_str)

            if (ctx.get("paused") and not force_redraw) or len(rec.ts_time) < 2:
                return

            win_s = float(ctx.get("window_s", 30))
            t_now_rel = rec.ts_time[-1]
            raw_t = list(rec.ts_time)
            start_idx = 0
            for idx, tv in enumerate(raw_t):
                if (t_now_rel - tv) <= win_s:
                    start_idx = idx
                    break
            xs = [tv - t_now_rel for tv in raw_t[start_idx:]]
            x_min = min(-win_s, xs[0] if xs else -win_s)

            axis_max: Dict[str, float] = {}
            bound: Dict[str, Dict[str, Any]] = ctx["bound_series"]
            for s_key, info in list(bound.items()):
                s_tag = info["serie_tag"]
                if not dpg.does_item_exist(s_tag):
                    continue
                ys = self._extract_series_values(rec, s_key, tp, start_idx)
                n_pts = min(len(xs), len(ys))
                if n_pts >= 2:
                    xs_slice = xs[-n_pts:]
                    ys_slice = ys[-n_pts:]
                    dpg.set_value(s_tag, [xs_slice, ys_slice])
                    info["xs"] = np.asarray(xs_slice, dtype=float)
                    info["ys"] = np.asarray(ys_slice, dtype=float)
                    ax_tag = info["axis_tag"]
                    is_ms_key = s_key in ("turn_mean", "turn_p95", "self_ms") or s_key.startswith("port_turn:")
                    min_ceil = 1.0 if s_key == "is_waiting" else (0.005 if is_ms_key else 0.5)
                    axis_max[ax_tag] = max(axis_max.get(ax_tag, min_ceil), max(ys_slice, default=min_ceil))

            if ctx.get("autoscale", True):
                xaxis_tag = ctx["xaxis_tag"]
                if dpg.does_item_exist(xaxis_tag):
                    dpg.set_axis_limits(xaxis_tag, x_min, 0.0)
                for ax_tag in self._get_all_report_y_axes(ctx):
                    if dpg.does_item_exist(ax_tag):
                        dpg.set_axis_limits_auto(ax_tag)
                        dpg.fit_axis_data(ax_tag)

    def _export_single_node_report(self, rec: _NodePerfRecord, win_tag: str) -> None:
        """Open native OS file browser (`file_explorer.save_file`) to export node telemetry to CSV or JSON."""
        out_dir: Path = LOGS_DIR / "benchmarks"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts_int = int(time.time())
        safe_lbl = "".join(c if c.isalnum() or c in "_-" else "_" for c in rec.label)
        default_name = f"node_report_{safe_lbl}_{rec.uid}_{ts_int}.csv"

        def _on_file_chosen(chosen_path: str, selected_ext: str) -> None:
            if not chosen_path:
                return
            try:
                target = Path(chosen_path)
                ext = target.suffix.lower() or selected_ext.lower() or ".csv"
                if not target.suffix:
                    target = target.with_suffix(ext)

                with rec.lock:
                    tp = rec.tracked_port
                    summary = {
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "uuid": rec.uid,
                        "label": rec.label,
                        "class_name": rec.cls_name,
                        "tracked_output_port": tp,
                        "flow_state": rec.flow_state_text,
                        "is_auto_triggered": rec.is_auto_triggered,
                        "is_waiting": int(round(rec.is_waiting_val)),
                        "memory_target": rec.mem_type_text,
                        "memory_rss_mb": round(rec.mem_rss_mb, 2),
                        "memory_peak_mb": round(rec.mem_peak_mb, 2),
                        "memory_buffers_mb": round(rec.mem_buffers_mb, 2),
                        "in_rate_hz": round(rec.in_rate_hz, 3),
                        "out_rate_hz": round(rec.out_rate_hz, 3),
                        "total_in": rec.total_in,
                        "total_out": rec.total_out,
                        "turnaround_mean_ms": round(rec.mean_turnaround_ms, 3),
                        "turnaround_p95_ms": round(rec.p95_turnaround_ms, 3),
                        "turnaround_max_ms": round(rec.max_turnaround_ms, 3),
                        "self_exec_mean_ms": round(rec.mean_self_ms, 3),
                        "self_exec_p95_ms": round(rec.p95_self_ms, 3),
                        "queue_size": rec.q_size,
                        "queue_max": rec.q_max,
                        "queue_fill_pct": round(rec.q_fill_pct, 1),
                        "drop_policy": rec.drop_policy,
                        "dropped_count": rec.dropped_count,
                        "port_rates_hz": {k: round(v, 3) for k, v in rec.port_rates_hz.items()},
                    }
                    t_list = list(rec.ts_time)
                    in_list = list(rec.ts_in_rate)
                    out_list = (
                        list(rec.ts_port_rates[tp])
                        if tp in rec.ts_port_rates
                        else list(rec.ts_out_rate)
                    )
                    turn_list = (
                        list(rec.ts_port_turnaround_ms[tp])
                        if tp in rec.ts_port_turnaround_ms and rec.port_in_to_out_ms.get(tp)
                        else list(rec.ts_turnaround_ms)
                    )
                    p95_list = (
                        list(rec.ts_port_p95_ms[tp])
                        if tp in rec.ts_port_p95_ms and rec.port_in_to_out_ms.get(tp)
                        else list(rec.ts_p95_ms)
                    )
                    self_list = list(rec.ts_self_ms)
                    q_list = list(rec.ts_queue_pct)
                    w_list = list(rec.ts_waiting)
                    mem_list = list(rec.ts_mem_rss)
                    buf_list = list(rec.ts_mem_buffers)

                n_rows = min(
                    len(t_list),
                    len(in_list),
                    len(out_list),
                    len(turn_list),
                    len(p95_list),
                    len(self_list),
                    len(q_list),
                    len(w_list),
                    len(mem_list),
                    len(buf_list),
                )

                if target.suffix.lower() == ".json":
                    summary["timeseries"] = [
                        {
                            "elapsed_s": round(t_list[i], 3),
                            "in_rate_hz": round(in_list[i], 3),
                            "out_rate_hz": round(out_list[i], 3),
                            "in_to_out_mean_ms": round(turn_list[i], 3),
                            "in_to_out_p95_ms": round(p95_list[i], 3),
                            "self_cb_ms": round(self_list[i], 3),
                            "queue_fill_pct": round(q_list[i], 1),
                            "is_waiting": int(round(w_list[i])),
                            "memory_rss_mb": round(mem_list[i], 2),
                            "memory_buffers_mb": round(buf_list[i], 2),
                        }
                        for i in range(n_rows)
                    ]
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(summary, f, indent=2)
                else:
                    with open(target, "w", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        writer.writerow([
                            "elapsed_s",
                            "in_rate_hz",
                            f"out_rate_hz ({tp})",
                            f"in_to_out_mean_ms ({tp})",
                            f"in_to_out_p95_ms ({tp})",
                            "self_cb_ms",
                            "queue_fill_pct",
                            "is_waiting",
                            "memory_rss_mb",
                            "memory_buffers_mb",
                        ])
                        for i in range(n_rows):
                            writer.writerow([
                                f"{t_list[i]:.2f}",
                                f"{in_list[i]:.2f}",
                                f"{out_list[i]:.2f}",
                                f"{turn_list[i]:.3f}",
                                f"{p95_list[i]:.3f}",
                                f"{self_list[i]:.3f}",
                                f"{q_list[i]:.1f}",
                                f"{int(round(w_list[i]))}",
                                f"{mem_list[i]:.2f}",
                                f"{buf_list[i]:.2f}",
                            ])

                if dpg.does_item_exist(f"{win_tag}_export_msg"):
                    dpg.set_value(f"{win_tag}_export_msg", f"Saved: {target.name}")
                logger.info(f"Exported node performance report to {target}")
            except Exception as exc:
                logger.error(f"Failed to export node performance report: {exc}")

        file_explorer.save_file(
            default_path=str(out_dir),
            default_name=default_name,
            extensions=[("CSV file", "*.csv"), ("JSON file", "*.json")],
            callback=_on_file_chosen,
        )
