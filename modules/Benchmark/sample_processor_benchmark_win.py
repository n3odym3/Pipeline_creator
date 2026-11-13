"""
Sample Processor Benchmark Window Module (Multiprocessing Mathematical Workload Stress-Tester).

Designed for stress-testing pipeline throughput, multiprocessing backpressure,
and numerical mathematical signal processing of varying compute intensities:
  - Pass-Through (0ms baseline for measuring serialization and IPC latency)
  - Light operations: Offset & Gain, Rectification & Clamping
  - Medium operations: Moving Average, Exponential Moving Average (EMA), Numerical Derivatives (dy/dx, d2y/dx2)
  - Heavy operations: Savitzky-Golay Polynomial Filter, Butterworth IIR Filter, FFT Power Spectral Density (PSD), Auto-Correlation, Resampling / Spline Interpolation
  - Extreme / Burn stress: Hankel Matrix Singular Value Decomposition (SVD), Chained Multi-Pass Filters (x5, x20), Configurable CPU Burn
  - Full ProcessingBase multiprocessing integration with runtime-configurable queue capacity and drop policies
  - Universal IOTypes support: IOTypes.SAMPLE, IOTypes.DATALIST, and IOTypes.ANY
"""

from __future__ import annotations

import collections
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.processing_base import ProcessingBase
from core.window_base import WindowBase


class SampleProcessorBenchmarkWorker(ProcessingBase):
    """
    Dedicated multiprocessing child worker for computing mathematical sample operations
    under isolated background processes without blocking the main UI thread.
    """

    def __getstate__(self) -> Dict[str, Any]:
        """Custom pickling state: strip unpicklable process handles and dynamically wrapped methods."""
        state = self.__dict__.copy()
        state.pop("submit", None)
        state.pop("_perf_orig_submit", None)
        state["_process"] = None
        return state

    def __setstate__(self, state: Dict[str, Any]) -> None:
        """Restore state in child process."""
        self.__dict__.update(state)

    def _process_data(self, data: Any, params: Dict[str, Any]) -> Any:
        """
        Executes in dedicated child process.
        Receives: (x, y, name, uuid, action, t_submit)
        Returns:  (x_out, y_out, name, uuid, action, t_submit, t_worker_in, t_worker_out, compute_ms)
        """
        if not isinstance(data, tuple) or len(data) != 6:
            return data

        x, y, name, uuid, action, t_submit = data
        t_worker_in = time.perf_counter()

        algo = str(params.get("algorithm") or "Moving Average (Window)")
        raw_w = params.get("window_size")
        window_size = 15 if raw_w is None else max(3, int(raw_w) | 1)

        raw_gain = params.get("gain")
        gain = 1.0 if raw_gain is None else float(raw_gain)

        raw_offset = params.get("offset")
        offset = 0.0 if raw_offset is None else float(raw_offset)

        raw_burn = params.get("burn_ms")
        burn_ms = 5.0 if raw_burn is None else max(0.0, float(raw_burn))

        raw_resample = params.get("resample_factor")
        resample_factor = 2 if raw_resample is None else max(2, int(raw_resample))

        raw_cutoff = params.get("cutoff_freq")
        cutoff_freq = 0.2 if raw_cutoff is None else max(0.01, min(0.49, float(raw_cutoff)))

        raw_poly = params.get("poly_order")
        poly_order = 2 if raw_poly is None else max(1, min(5, int(raw_poly)))

        try:
            x_out, y_out = self._apply_algorithm(
                x=x,
                y=y,
                algo=algo,
                window_size=window_size,
                gain=gain,
                offset=offset,
                burn_ms=burn_ms,
                resample_factor=resample_factor,
                cutoff_freq=cutoff_freq,
                poly_order=poly_order,
            )
        except Exception as e:
            logger.error(f"SampleProcessorWorker algorithm '{algo}' failed: {e}")
            x_out, y_out = x, y

        t_worker_out = time.perf_counter()
        compute_ms = (t_worker_out - t_worker_in) * 1000.0

        return (x_out, y_out, name, uuid, action, t_submit, t_worker_in, t_worker_out, compute_ms)

    # --------------------------------------------------------------------------
    # Mathematical Processing Algorithms
    # --------------------------------------------------------------------------
    def _apply_algorithm(
        self,
        x: np.ndarray,
        y: np.ndarray,
        algo: str,
        window_size: int,
        gain: float,
        offset: float,
        burn_ms: float,
        resample_factor: int,
        cutoff_freq: float,
        poly_order: int,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Dispatch numerical signal processing algorithms."""

        # 1. Zero-compute baseline
        if algo == "Pass-Through (0ms Baseline)":
            return x, y

        # 2. Offset & Gain
        if algo == "Offset & Gain":
            return x, (y * gain) + offset

        # 3. Rectify & Clip
        if algo == "Rectify & Clip":
            return x, np.clip(np.abs(y), 0.0, 1.0)

        # 4. Moving Average (Rolling Boxcar)
        if algo == "Moving Average":
            w = max(3, window_size if window_size % 2 == 1 else window_size + 1)
            if len(y) < w:
                return x, y
            kernel = np.ones(w, dtype=np.float64) / w
            y_smooth = np.convolve(y, kernel, mode="same")
            return x, y_smooth

        # 5. Exponential Moving Average (EMA)
        if algo == "Exponential Moving Avg (EMA)":
            alpha = 2.0 / (max(2.0, float(window_size)) + 1.0)
            y_ema = np.empty_like(y, dtype=np.float64)
            if len(y) > 0:
                y_ema[0] = y[0]
                for i in range(1, len(y)):
                    y_ema[i] = alpha * y[i] + (1.0 - alpha) * y_ema[i - 1]
            return x, y_ema

        # 6. Savitzky-Golay Polynomial Filter
        if algo == "Savitzky-Golay Filter":
            w = max(poly_order + 2, window_size if window_size % 2 == 1 else window_size + 1)
            w = min(w, len(y) if len(y) % 2 == 1 else len(y) - 1)
            if w >= 3 and len(y) >= w:
                try:
                    from scipy.signal import savgol_filter
                    y_sg = savgol_filter(y, window_length=w, polyorder=min(poly_order, w - 1), mode="interp")
                    return x, y_sg
                except Exception:
                    pass
            # Fallback boxcar if scipy is missing
            kernel = np.ones(min(5, len(y)), dtype=np.float64) / min(5, len(y))
            return x, np.convolve(y, kernel, mode="same")

        # 7. Butterworth IIR Filter
        if algo == "Butterworth Filter":
            if len(y) > 15:
                try:
                    from scipy.signal import butter, sosfilt
                    sos = butter(4, cutoff_freq, btype="lowpass", output="sos")
                    y_filt = sosfilt(sos, y)
                    return x, y_filt
                except Exception:
                    pass
            return x, y

        # 8. FFT Power Spectral Density (PSD)
        if algo == "FFT Power Spectrum (PSD)":
            n = len(y)
            if n > 2:
                fft_vals = np.abs(np.fft.rfft(y))
                dx = float(x[1] - x[0]) if len(x) > 1 and (x[1] - x[0]) > 0 else 1.0 / n
                freqs = np.fft.rfftfreq(n, d=dx)
                return freqs, fft_vals
            return x, y

        # 9. Auto-Correlation
        if algo == "Auto-Correlation":
            n = len(y)
            if n > 2:
                y_centered = y - np.mean(y)
                corr = np.correlate(y_centered, y_centered, mode="full")
                corr = corr[len(corr) // 2 :]  # single-sided
                denom = corr[0] if corr[0] != 0 else 1.0
                corr = corr / denom
                x_lags = np.arange(len(corr), dtype=np.float64)
                return x_lags, corr
            return x, y

        # 10. Resampling / Interpolation
        if algo == "Resampling & Interpolation":
            factor = max(2, resample_factor)
            n_new = len(y) * factor
            x_new = np.linspace(x[0], x[-1], n_new, dtype=np.float64)
            y_new = np.interp(x_new, x, y)
            return x_new, y_new

        # 11. Numerical Derivatives (dy/dx)
        if algo == "Numerical Derivative (dy/dx)":
            if len(y) > 2:
                dx = float(x[1] - x[0]) if len(x) > 1 and (x[1] - x[0]) > 0 else 1.0
                dy = np.gradient(y, dx)
                return x, dy
            return x, y

        # 12. Hankel Matrix SVD Stress
        if algo == "Hankel Matrix SVD Stress":
            n = len(y)
            # Create Hankel trajectory matrix L x K where L ~ min(120, n // 2)
            l_dim = min(120, max(10, n // 2))
            k_dim = n - l_dim + 1
            if k_dim > 10:
                h_mat = np.empty((l_dim, k_dim), dtype=np.float64)
                for r in range(l_dim):
                    h_mat[r, :] = y[r : r + k_dim]
                # Compute Singular Value Decomposition
                u, s, vh = np.linalg.svd(h_mat, full_matrices=False)
                # Reconstruct low-rank approximation (first 3 singular values)
                rank = min(3, len(s))
                s_reduced = np.zeros_like(s)
                s_reduced[:rank] = s[:rank]
                h_rec = np.dot(u * s_reduced, vh)
                # Anti-diagonal averaging
                y_rec = np.zeros(n, dtype=np.float64)
                counts = np.zeros(n, dtype=np.float64)
                for r in range(l_dim):
                    y_rec[r : r + k_dim] += h_rec[r, :]
                    counts[r : r + k_dim] += 1.0
                y_out = y_rec / np.maximum(counts, 1.0)
                return x, y_out
            return x, y

        # 13. Multi-Pass Filter (Stress x5)
        if algo == "Multi-Pass Filter (Stress x5)":
            w = max(3, window_size if window_size % 2 == 1 else window_size + 1)
            kernel = np.ones(w, dtype=np.float64) / w
            y_curr = y
            for _ in range(5):
                y_curr = np.convolve(y_curr, kernel, mode="same")
            return x, y_curr

        # 14. Multi-Pass Filter (Stress x20)
        if algo == "Multi-Pass Filter (Stress x20)":
            w = max(3, window_size if window_size % 2 == 1 else window_size + 1)
            kernel = np.ones(w, dtype=np.float64) / w
            y_curr = y
            for _ in range(20):
                y_curr = np.convolve(y_curr, kernel, mode="same")
            return x, y_curr

        # 15. Artificial CPU Burn
        if algo == "Artificial CPU Burn":
            target_sec = burn_ms / 1000.0
            t_start = time.perf_counter()
            # Perform matrix dot products in chunks to burn CPU accurately
            m = np.random.normal(0.0, 1.0, (128, 128))
            while (time.perf_counter() - t_start) < target_sec:
                m = np.dot(m, m) * 0.001
            return x, y

        return x, y


class SampleProcessorBenchmark_win(WindowBase):
    """
    Multiprocessing Sample Processor Benchmark & Stress-Tester.

    Processes incoming 1D numerical signals through configurable mathematical
    filters and transformations, validating system backpressure, queue saturation,
    and downstream rendering pipeline responsiveness.
    """

    label: str = "Sample Processor Benchmark"
    win_width: int = display_scaling.scale(380)
    win_height: int = display_scaling.scale(440)
    description: str = "Multiprocessing sample processor & mathematical pipeline stress-tester."

    accepted_input_types: list[type] = [
        IOTypes.SAMPLE,
        IOTypes.DATALIST,
        IOTypes.ANY,
    ]
    outputs: dict[str, type] = {
        "Data": IOTypes.SAMPLE,
        "Metrics": IOTypes.CMD_DICT,
    }
    persistent_fields: list[str] = [
        "label",
        "algorithm",
        "window_size",
        "gain",
        "offset",
        "burn_ms",
        "resample_factor",
        "cutoff_freq",
        "poly_order",
        "buffer_size",
        "drop_policy",
        "universal_any_output",
    ]

    _ALGORITHMS: list[str] = [
        "Pass-Through (0ms Baseline)",
        "Offset & Gain",
        "Rectify & Clip",
        "Moving Average",
        "Exponential Moving Avg (EMA)",
        "Savitzky-Golay Filter",
        "Butterworth Filter",
        "FFT Power Spectrum (PSD)",
        "Auto-Correlation",
        "Resampling & Interpolation",
        "Numerical Derivative (dy/dx)",
        "Hankel Matrix SVD Stress",
        "Multi-Pass Filter (Stress x5)",
        "Multi-Pass Filter (Stress x20)",
        "Artificial CPU Burn",
    ]

    _DROP_POLICIES: list[str] = [
        "drop_oldest",
        "drop_new",
        "block",
    ]

    # Persistent defaults
    algorithm: str = "Moving Average"
    window_size: int = 15
    gain: float = 1.0
    offset: float = 0.0
    burn_ms: float = 5.0
    resample_factor: int = 2
    cutoff_freq: float = 0.2
    poly_order: int = 2
    buffer_size: int = 5
    drop_policy: str = "drop_oldest"
    universal_any_output: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        raw_algo = getattr(self, "algorithm", None)
        self.algorithm = str(raw_algo) if raw_algo in self._ALGORITHMS else "Moving Average"

        raw_w = getattr(self, "window_size", None)
        self.window_size = 15 if raw_w is None else max(3, int(raw_w) | 1)

        raw_gain = getattr(self, "gain", None)
        self.gain = 1.0 if raw_gain is None else float(raw_gain)

        raw_offset = getattr(self, "offset", None)
        self.offset = 0.0 if raw_offset is None else float(raw_offset)

        raw_burn = getattr(self, "burn_ms", None)
        self.burn_ms = 5.0 if raw_burn is None else max(0.0, float(raw_burn))

        raw_resample = getattr(self, "resample_factor", None)
        self.resample_factor = 2 if raw_resample is None else max(2, int(raw_resample))

        raw_cutoff = getattr(self, "cutoff_freq", None)
        self.cutoff_freq = 0.2 if raw_cutoff is None else max(0.01, min(0.49, float(raw_cutoff)))

        raw_poly = getattr(self, "poly_order", None)
        self.poly_order = 2 if raw_poly is None else max(1, min(5, int(raw_poly)))

        raw_buf = getattr(self, "buffer_size", None)
        self.buffer_size = 5 if raw_buf is None else max(1, int(raw_buf))

        raw_policy = getattr(self, "drop_policy", None)
        self.drop_policy = str(raw_policy) if raw_policy in self._DROP_POLICIES else "drop_oldest"

        self.universal_any_output = bool(getattr(self, "universal_any_output", False))

        # FlowMetricsViewer Integration Attributes
        self.max_queue_size: int = self.buffer_size
        self.dropped_count: int = 0
        self.last_exec_ms: float = 0.0

        # Unique DPG Tags
        self._tag_in_hz = f"spb_in_hz_{self.UUID}"
        self._tag_out_hz = f"spb_out_hz_{self.UUID}"
        self._tag_compute_ms = f"spb_comp_ms_{self.UUID}"
        self._tag_queue_fill = f"spb_q_fill_{self.UUID}"
        self._tag_drops = f"spb_drops_{self.UUID}"

        # Parameter Section & Group Tags
        self._tag_params_section = f"spb_sec_params_{self.UUID}"
        self._tag_grp_window = f"spb_grp_w_{self.UUID}"
        self._tag_grp_gain = f"spb_grp_g_{self.UUID}"
        self._tag_grp_burn = f"spb_grp_b_{self.UUID}"
        self._tag_grp_resample = f"spb_grp_rs_{self.UUID}"
        self._tag_grp_cutoff = f"spb_grp_cf_{self.UUID}"
        self._tag_grp_poly = f"spb_grp_po_{self.UUID}"

        # Rolling Timings
        self._rate_in_times: collections.deque[float] = collections.deque(maxlen=30)
        self._rate_out_times: collections.deque[float] = collections.deque(maxlen=30)
        self._compute_times: collections.deque[float] = collections.deque(maxlen=30)
        self._last_ui_update_ts: float = 0.0

        # Multiprocessing Worker & Polling Thread
        self.proc: Optional[SampleProcessorBenchmarkWorker] = None
        self._stop_poll = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None

        self._rebuild_worker()
        self._start_poll_thread()
        self.create()

    # --------------------------------------------------------------------------
    # FlowMetricsViewer Protocol Properties
    # --------------------------------------------------------------------------
    def is_ready(self) -> bool:
        if self.proc is not None:
            return self.proc.is_ready()
        return True

    @property
    def is_waiting(self) -> bool:
        if self.proc is not None:
            try:
                return self.proc._in_queue.full()
            except Exception:
                return False
        return False

    # --------------------------------------------------------------------------
    # Multiprocessing Worker Lifecycle
    # --------------------------------------------------------------------------
    def _build_worker_params(self) -> Dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "window_size": self.window_size,
            "gain": self.gain,
            "offset": self.offset,
            "burn_ms": self.burn_ms,
            "resample_factor": self.resample_factor,
            "cutoff_freq": self.cutoff_freq,
            "poly_order": self.poly_order,
        }

    def _rebuild_worker(self) -> None:
        """Instantiate or restart the dedicated child worker process cleanly."""
        old_proc = self.proc
        self.proc = None
        if old_proc is not None:
            try:
                old_proc.stop()
            except Exception:
                pass

        self.max_queue_size = self.buffer_size
        new_proc = SampleProcessorBenchmarkWorker(
            params=self._build_worker_params(),
            buffer_size=max(1, int(self.buffer_size)),
            out_buffer_size=max(1, int(self.buffer_size)),
            drop_policy=str(self.drop_policy),
        )
        new_proc.start()
        self.proc = new_proc

    def _sync_worker_params(self) -> None:
        """Forward updated parameters to active child worker without process recreation."""
        if self.proc is not None:
            self.proc.update_params(**self._build_worker_params())

    def _start_poll_thread(self) -> None:
        """Launch background polling thread to retrieve processed results."""
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_worker_loop,
            name=f"SampleProcessorBench_poll_{self.UUID}",
            daemon=True,
        )
        self._poll_thread.start()

    def _poll_worker_loop(self) -> None:
        """Background loop reading from worker process out_queue with zero-latency burst drain."""
        while not self._stop_poll.is_set():
            if self.proc is None:
                time.sleep(0.01)
                continue

            try:
                item = self.proc.get(timeout=0.005)
            except Exception:
                item = None

            if item is None:
                continue

            try:
                self._handle_completed_result(item)
                # Rapid burst drain: consume all currently available results without sleeping
                while not self._stop_poll.is_set():
                    try:
                        next_item = self.proc.get()
                    except Exception:
                        break
                    if next_item is None:
                        break
                    self._handle_completed_result(next_item)
            except Exception as e:
                logger.error(f"SampleProcessorBenchmark error in completed result: {e}")

    # --------------------------------------------------------------------------
    # Result Dispatch & Telemetry
    # --------------------------------------------------------------------------
    def _handle_completed_result(self, item: Any) -> None:
        """Unpack worker result and forward downstream."""
        now = time.perf_counter()
        self._rate_out_times.append(now)

        if not isinstance(item, tuple) or len(item) != 9:
            return

        (
            x_out,
            y_out,
            name,
            uuid,
            action,
            t_submit,
            t_worker_in,
            t_worker_out,
            compute_ms,
        ) = item

        self.last_exec_ms = compute_ms
        self._compute_times.append(compute_ms)

        out_sample = {
            "name": name,
            "uuid": uuid,
            "x": x_out,
            "y": y_out,
            "action": action,
        }

        # 1. Dispatch to Data connections
        out_iotype = IOTypes.ANY if self.universal_any_output else IOTypes.SAMPLE
        targets = self.connections.get("Data", [])
        if targets:
            for tgt in list(targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(sample=out_sample, data=out_sample, data_type=out_iotype)
                    except Exception as e:
                        logger.debug(f"SampleProcessor dispatch error: {e}")

        # 2. Dispatch Metrics if wired
        metrics_targets = self.connections.get("Metrics", [])
        if metrics_targets:
            ipc_in_ms = max(0.0, (t_worker_in - t_submit) * 1000.0)
            ipc_out_ms = max(0.0, (now - t_worker_out) * 1000.0)
            total_latency_ms = max(0.0, (now - t_submit) * 1000.0)
            metrics_payload = {
                "compute_ms": compute_ms,
                "ipc_in_ms": ipc_in_ms,
                "ipc_out_ms": ipc_out_ms,
                "total_latency_ms": total_latency_ms,
                "dropped_count": self.dropped_count,
                "queue_size": self.proc.queue_size() if self.proc else 0,
                "buffer_size": self.buffer_size,
                "algorithm": self.algorithm,
            }
            for tgt in list(metrics_targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=metrics_payload, data_type=IOTypes.CMD_DICT)
                    except Exception:
                        pass

        # 3. Throttled UI Telemetry Update (~5 Hz)
        if (now - self._last_ui_update_ts) >= 0.20:
            self._last_ui_update_ts = now
            self._update_ui_telemetry()

    def _calc_rate(self, dq: collections.deque[float]) -> float:
        if len(dq) < 2:
            return 0.0
        dur = dq[-1] - dq[0]
        return float((len(dq) - 1) / dur) if dur > 0.0 else 0.0

    def _update_ui_telemetry(self) -> None:
        """Update live telemetry metrics without hardcoded colors."""
        if not dpg.is_dearpygui_running():
            return

        in_hz = self._calc_rate(self._rate_in_times)
        out_hz = self._calc_rate(self._rate_out_times)
        avg_comp = float(np.mean(self._compute_times)) if self._compute_times else 0.0

        q_size = 0
        if self.proc is not None:
            q_size = max(0, self.proc.queue_size())
        max_q = max(1, self.buffer_size)
        fill_pct = min(100.0, (q_size / max_q) * 100.0)

        try:
            if dpg.does_item_exist(self._tag_in_hz):
                dpg.set_value(self._tag_in_hz, f"{in_hz:.1f} Hz")
            if dpg.does_item_exist(self._tag_out_hz):
                dpg.set_value(self._tag_out_hz, f"{out_hz:.1f} Hz")
            if dpg.does_item_exist(self._tag_compute_ms):
                dpg.set_value(self._tag_compute_ms, f"{avg_comp:.3f} ms")
            if dpg.does_item_exist(self._tag_queue_fill):
                dpg.set_value(self._tag_queue_fill, f"{q_size} / {max_q} ({fill_pct:.0f}%)")
            total_drops = self.dropped_count + (self.proc.drop_count() if self.proc else 0)
            if dpg.does_item_exist(self._tag_drops):
                dpg.set_value(self._tag_drops, str(total_drops))
        except Exception:
            pass

    def is_ready(self) -> bool:
        """Check whether the worker process and downstream connections can accept data."""
        if self.proc is None or not self.proc.is_ready():
            return False
        return self.is_outputs_ready()

    # --------------------------------------------------------------------------
    # Node Editor Input Callback
    # --------------------------------------------------------------------------
    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """Accepts incoming sample payloads and submits them to the worker process."""
        sample = kwargs.get("sample") or kwargs.get("data") or (args[0] if args else None)
        if not sample:
            return

        t_submit = time.perf_counter()
        self._rate_in_times.append(t_submit)

        x_raw = None
        y_raw = None
        name = "Sample"
        uid = f"uuid_{self.UUID[:6]}"
        action = "select"

        if isinstance(sample, dict):
            if "y" in sample:
                y_raw = sample["y"]
                x_raw = sample.get("x")
                name = str(sample.get("name") or "Sample")
                uid = str(sample.get("uuid") or name)
                action = str(sample.get("action") or "select")
            elif "data" in sample and isinstance(sample["data"], (tuple, list)) and len(sample["data"]) == 2:
                x_raw, y_raw = sample["data"]
        elif isinstance(sample, (tuple, list)) and len(sample) == 2:
            x_raw, y_raw = sample[0], sample[1]
        elif isinstance(sample, np.ndarray):
            y_raw = sample

        if y_raw is None:
            return

        # Ensure numpy 1D float arrays
        try:
            y = np.asarray(y_raw, dtype=np.float64)
            if x_raw is not None:
                x = np.asarray(x_raw, dtype=np.float64)
                if len(x) != len(y):
                    x = np.arange(len(y), dtype=np.float64)
            else:
                x = np.arange(len(y), dtype=np.float64)
        except Exception:
            return

        if self.proc is None:
            return

        payload = (x, y, name, uid, action, t_submit)
        block_timeout = 5.0 if self.drop_policy == "block" else None
        submitted = self.proc.submit(payload, timeout=block_timeout)

        if not submitted:
            self.dropped_count += 1

    # --------------------------------------------------------------------------
    # DearPyGui Window Construction
    # --------------------------------------------------------------------------
    def create(self) -> None:
        with self.window():
            # Section: Algorithm Selection
            with dpg.group(horizontal=True):
                dpg.add_text("Algorithm:")
                dpg.add_combo(
                    items=self._ALGORITHMS,
                    default_value=self.algorithm,
                    callback=self._on_algorithm_changed,
                    width=display_scaling.scale(230),
                )

            # Section: Tunable Algorithm Parameters (Context-Aware Visibility)
            with dpg.group(tag=self._tag_params_section, show=False):
                dpg.add_separator()
                with dpg.group(tag=self._tag_grp_window, show=False):
                    dpg.add_slider_int(
                        label="Window Size",
                        default_value=self.window_size,
                        min_value=3,
                        max_value=101,
                        clamped=True,
                        callback=self._on_window_changed,
                        width=display_scaling.scale(180),
                    )

                with dpg.group(tag=self._tag_grp_gain, show=False):
                    dpg.add_slider_float(
                        label="Gain",
                        default_value=self.gain,
                        min_value=-5.0,
                        max_value=5.0,
                        format="%.2f",
                        callback=self._on_gain_changed,
                        width=display_scaling.scale(180),
                    )
                    dpg.add_slider_float(
                        label="Offset",
                        default_value=self.offset,
                        min_value=-10.0,
                        max_value=10.0,
                        format="%.2f",
                        callback=self._on_offset_changed,
                        width=display_scaling.scale(180),
                    )

                with dpg.group(tag=self._tag_grp_resample, show=False):
                    dpg.add_slider_int(
                        label="Factor (x)",
                        default_value=self.resample_factor,
                        min_value=2,
                        max_value=16,
                        clamped=True,
                        callback=self._on_resample_changed,
                        width=display_scaling.scale(180),
                    )

                with dpg.group(tag=self._tag_grp_cutoff, show=False):
                    dpg.add_slider_float(
                        label="Cutoff (Norm)",
                        default_value=self.cutoff_freq,
                        min_value=0.01,
                        max_value=0.49,
                        format="%.2f",
                        callback=self._on_cutoff_changed,
                        width=display_scaling.scale(180),
                    )

                with dpg.group(tag=self._tag_grp_poly, show=False):
                    dpg.add_slider_int(
                        label="Poly Order",
                        default_value=self.poly_order,
                        min_value=1,
                        max_value=5,
                        clamped=True,
                        callback=self._on_poly_changed,
                        width=display_scaling.scale(180),
                    )

                with dpg.group(tag=self._tag_grp_burn, show=False):
                    dpg.add_slider_float(
                        label="CPU Burn",
                        default_value=self.burn_ms,
                        min_value=0.0,
                        max_value=100.0,
                        format="%.1f ms",
                        callback=self._on_burn_changed,
                        width=display_scaling.scale(180),
                    )

            dpg.add_separator()

            # Section: Multiprocessing Queue & Drop Policy Stress Config
            dpg.add_text("Queue & Stress Config")
            dpg.add_combo(
                items=self._DROP_POLICIES,
                default_value=self.drop_policy,
                label="Drop Policy",
                callback=self._on_drop_policy_changed,
                width=display_scaling.scale(160),
            )
            dpg.add_slider_int(
                label="Queue Capacity",
                default_value=self.buffer_size,
                min_value=1,
                max_value=500,
                clamped=True,
                callback=self._on_buffer_size_changed,
                width=display_scaling.scale(160),
            )
            dpg.add_button(
                label="Recreate / Reapply Worker",
                callback=self._on_recreate_worker_clicked,
            )

            dpg.add_separator()

            # Section: Live Diagnostics (Clean, No Colors)
            dpg.add_text("Live Diagnostics")
            with dpg.group(horizontal=True):
                dpg.add_text("In Rate: ")
                dpg.add_text("0.0 Hz", tag=self._tag_in_hz)
                dpg.add_spacer(width=display_scaling.scale(15))
                dpg.add_text("Out Rate: ")
                dpg.add_text("0.0 Hz", tag=self._tag_out_hz)

            with dpg.group(horizontal=True):
                dpg.add_text("Worker Compute: ")
                dpg.add_text("0.000 ms", tag=self._tag_compute_ms)

            with dpg.group(horizontal=True):
                dpg.add_text("Queue Fill: ")
                dpg.add_text("0 / 5 (0%)", tag=self._tag_queue_fill)
                dpg.add_spacer(width=display_scaling.scale(15))
                dpg.add_text("Drops: ")
                dpg.add_text("0", tag=self._tag_drops)

            with dpg.group(horizontal=True):
                dpg.add_button(label="Reset Drops", callback=self._on_reset_drops_clicked)
                dpg.add_checkbox(
                    label="Universal Output (ANY)",
                    default_value=self.universal_any_output,
                    callback=self._on_universal_toggled,
                )

        self._update_parameter_visibility()

    # --------------------------------------------------------------------------
    # Parameter Visibility & Callbacks
    # --------------------------------------------------------------------------
    def _update_parameter_visibility(self) -> None:
        """Dynamically toggle slider groups without placeholder text."""
        if not dpg.is_dearpygui_running():
            return

        algo = self.algorithm
        has_window = algo in (
            "Moving Average",
            "Exponential Moving Avg (EMA)",
            "Savitzky-Golay Filter",
            "Multi-Pass Filter (Stress x5)",
            "Multi-Pass Filter (Stress x20)",
        )
        has_gain = algo == "Offset & Gain"
        has_resample = algo == "Resampling & Interpolation"
        has_cutoff = algo == "Butterworth Filter"
        has_poly = algo == "Savitzky-Golay Filter"
        has_burn = algo == "Artificial CPU Burn"
        has_any_params = has_window or has_gain or has_resample or has_cutoff or has_poly or has_burn

        if dpg.does_item_exist(self._tag_params_section):
            dpg.configure_item(self._tag_params_section, show=has_any_params)

        for tag, shown in (
            (self._tag_grp_window, has_window),
            (self._tag_grp_gain, has_gain),
            (self._tag_grp_resample, has_resample),
            (self._tag_grp_cutoff, has_cutoff),
            (self._tag_grp_poly, has_poly),
            (self._tag_grp_burn, has_burn),
        ):
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, show=shown)

    def _on_algorithm_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.algorithm = str(app_data)
        self._update_parameter_visibility()
        self._sync_worker_params()

    def _on_window_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.window_size = max(3, int(app_data) | 1)
        self._sync_worker_params()

    def _on_gain_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.gain = float(app_data)
        self._sync_worker_params()

    def _on_offset_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.offset = float(app_data)
        self._sync_worker_params()

    def _on_resample_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.resample_factor = max(2, int(app_data))
        self._sync_worker_params()

    def _on_cutoff_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.cutoff_freq = max(0.01, min(0.49, float(app_data)))
        self._sync_worker_params()

    def _on_poly_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.poly_order = max(1, min(5, int(app_data)))
        self._sync_worker_params()

    def _on_burn_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.burn_ms = max(0.0, float(app_data))
        self._sync_worker_params()

    def _on_drop_policy_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.drop_policy = str(app_data)
        if self.proc is not None:
            self.proc.set_drop_policy(self.drop_policy)

    def _on_buffer_size_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.buffer_size = max(1, int(app_data))
        self.max_queue_size = self.buffer_size

    def _on_recreate_worker_clicked(self, *args: Any) -> None:
        logger.info(
            f"[{self.label}] Rebuilding worker process with buffer_size={self.buffer_size}, drop_policy='{self.drop_policy}'"
        )
        self._rebuild_worker()

    def _on_reset_drops_clicked(self, *args: Any) -> None:
        self.dropped_count = 0
        if dpg.does_item_exist(self._tag_drops):
            dpg.set_value(self._tag_drops, "0")

    def _on_universal_toggled(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.universal_any_output = bool(app_data)
        self.outputs["Data"] = IOTypes.ANY if self.universal_any_output else IOTypes.SAMPLE

    # --------------------------------------------------------------------------
    # Lifecycle Cleanup
    # --------------------------------------------------------------------------
    def on_close(self) -> None:
        self._stop_poll.set()
        if self.proc is not None:
            try:
                self.proc.stop()
            except Exception:
                pass
            self.proc = None
        if self._poll_thread is not None and self._poll_thread.is_alive() and threading.current_thread() != self._poll_thread:
            self._poll_thread.join(timeout=0.2)
        super().on_close()

    def close(self) -> None:
        super().close()

    def destroy(self) -> None:
        self.close()
        super().destroy()


EXPORTED_CLASS = SampleProcessorBenchmark_win
EXPORTED_NAME = "Sample Processor Benchmark"
