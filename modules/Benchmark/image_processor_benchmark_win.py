"""
Image Processor Benchmark Window Module (Multiprocessing & OpenCL GPU Stress Tester).

Designed for stress-testing pipeline throughput, multiprocessing queue backpressure,
and image processing algorithms of varying compute intensities:
  - Pass-Through (Zero-compute baseline for IPC and serialization latency)
  - Light algorithms: Invert/Negative, Thresholding, Light Gaussian Blur
  - Medium algorithms: Sobel gradient, Canny edge detection, Morphological gradient
  - Heavy algorithms: Median filter (large kernel), Bilateral filter, 2D Convolution, CLAHE
  - Stress / Burn workloads: Multi-pass chained blurs (x5, x15) and configurable CPU matrix burn
  - Dual execution engine: Pure CPU vs GPU Hardware Acceleration via OpenCV OpenCL (UMat)
  - Full ProcessingBase multiprocessing integration with runtime-configurable queue capacity and drop policies
  - Universal IOType support matching VideoBenchmark:
      * FRAME (8-bit BGR/Gray/RGBA, with optional accessory name tuple)
      * FRAME12 (12-bit HDR, range 0..4095)
      * FRAME16 (16-bit HDR, range 0..65535)
      * MASK (Binary ROI mask, uint8 0/255)
      * FRAME_MASK_PAIR (Combined (frame, mask) tuple)
      * Universal ANY pin
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.processing_base import ProcessingBase
from core.window_base import WindowBase


class ImageProcessorBenchmarkWorker(ProcessingBase):
    """
    Dedicated multiprocessing worker process for stress-testing image processing algorithms
    under CPU and GPU (OpenCL) workloads.
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
        Receives: (frame, accessory_type, accessory_val, resolved_iotype, t_submit)
        Returns:  (out_frame, accessory_type, accessory_val, resolved_iotype, t_submit, t_worker_in, t_worker_out, compute_ms)
        """
        if not isinstance(data, tuple) or len(data) != 5:
            return data

        frame, accessory_type, accessory_val, resolved_iotype, t_submit = data
        t_worker_in = time.perf_counter()

        algo = str(params.get("algorithm") or "Gaussian Blur (Heavy 25x25)")
        use_opencl = bool(params.get("use_opencl", False))
        raw_k = params.get("kernel_size")
        kernel_size = 25 if raw_k is None else int(raw_k)
        raw_iter = params.get("iterations")
        iterations = 5 if raw_iter is None else int(raw_iter)
        raw_burn = params.get("burn_ms")
        burn_ms = 5.0 if raw_burn is None else float(raw_burn)
        raw_thresh = params.get("thresh_val")
        thresh_val = 128 if raw_thresh is None else int(raw_thresh)
        raw_c1 = params.get("canny_t1")
        canny_t1 = 50 if raw_c1 is None else int(raw_c1)
        raw_c2 = params.get("canny_t2")
        canny_t2 = 150 if raw_c2 is None else int(raw_c2)
        raw_cl = params.get("clahe_clip")
        clahe_clip = 2.0 if raw_cl is None else float(raw_cl)

        # Configure OpenCL in worker process
        if cv2.ocl.haveOpenCL():
            cv2.ocl.setUseOpenCL(use_opencl)

        try:
            out_frame = self._apply_algorithm(
                frame=frame,
                algo=algo,
                use_opencl=use_opencl,
                kernel_size=kernel_size,
                iterations=iterations,
                burn_ms=burn_ms,
                thresh_val=thresh_val,
                canny_t1=canny_t1,
                canny_t2=canny_t2,
                clahe_clip=clahe_clip,
                params=params,
            )
        except Exception as e:
            logger.warning(f"ImageProcessorBenchmarkWorker error in '{algo}': {e}")
            out_frame = frame

        t_worker_out = time.perf_counter()
        compute_ms = max(0.0, (t_worker_out - t_worker_in) * 1000.0)

        return (
            out_frame,
            accessory_type,
            accessory_val,
            resolved_iotype,
            t_submit,
            t_worker_in,
            t_worker_out,
            compute_ms,
        )

    def _apply_algorithm(
        self,
        frame: np.ndarray,
        algo: str,
        use_opencl: bool,
        kernel_size: int,
        iterations: int,
        burn_ms: float,
        thresh_val: int,
        canny_t1: int,
        canny_t2: int,
        clahe_clip: float,
        params: Dict[str, Any],
    ) -> np.ndarray:
        """Applies the selected image processing algorithm, with OpenCL UMat acceleration when active."""

        # 1. Pass-Through (Baseline)
        if algo == "Pass-Through (0ms Baseline)":
            return frame

        # 2. Invert / Negative
        elif algo == "Invert / Negative":
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(frame)
                    return cv2.bitwise_not(u).get()
                except Exception:
                    pass
            return cv2.bitwise_not(frame)

        # 3. Threshold / Binarize
        elif algo == "Threshold / Binarize":
            max_val = 255 if frame.dtype == np.uint8 else 65535
            scaled_thresh = (
                thresh_val
                if frame.dtype == np.uint8
                else (thresh_val * 16 if frame.max() <= 4095 else thresh_val * 256)
            )
            gray = frame
            if frame.ndim == 3:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY if frame.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(gray)
                    _, res = cv2.threshold(u, scaled_thresh, max_val, cv2.THRESH_BINARY)
                    return res.get()
                except Exception:
                    pass
            _, res = cv2.threshold(gray, scaled_thresh, max_val, cv2.THRESH_BINARY)
            return res

        # 4. Gaussian Blur (Light 5x5)
        elif algo == "Gaussian Blur (Light 5x5)":
            k = 5
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(frame)
                    return cv2.GaussianBlur(u, (k, k), 0).get()
                except Exception:
                    pass
            return cv2.GaussianBlur(frame, (k, k), 0)

        # 5. Gaussian Blur (Heavy 25x25)
        elif algo == "Gaussian Blur (Heavy 25x25)":
            k = max(3, kernel_size | 1)
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(frame)
                    return cv2.GaussianBlur(u, (k, k), 0).get()
                except Exception:
                    pass
            return cv2.GaussianBlur(frame, (k, k), 0)

        # 6. Sobel Gradient
        elif algo == "Sobel Gradient":
            k = max(3, min(7, kernel_size | 1))
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(frame)
                    gx = cv2.Sobel(u, cv2.CV_32F, 1, 0, ksize=k)
                    gy = cv2.Sobel(u, cv2.CV_32F, 0, 1, ksize=k)
                    mag = cv2.magnitude(gx, gy)
                    if frame.dtype == np.uint8:
                        return cv2.convertScaleAbs(mag).get()
                    res = np.clip(mag.get(), 0, 65535).astype(frame.dtype)
                    return res
                except Exception:
                    pass
            gx = cv2.Sobel(frame, cv2.CV_32F, 1, 0, ksize=k)
            gy = cv2.Sobel(frame, cv2.CV_32F, 0, 1, ksize=k)
            mag = cv2.magnitude(gx, gy)
            if frame.dtype == np.uint8:
                return cv2.convertScaleAbs(mag)
            return np.clip(mag, 0, 65535).astype(frame.dtype)

        # 7. Canny Edge Detector
        elif algo == "Canny Edge Detector":
            u8 = frame
            if frame.dtype != np.uint8:
                u8 = (frame >> 8 if frame.max() > 4095 else frame >> 4).astype(np.uint8)
            if u8.ndim == 3 and u8.shape[2] == 4:
                u8 = cv2.cvtColor(u8, cv2.COLOR_BGRA2BGR)
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(u8)
                    return cv2.Canny(u, canny_t1, canny_t2).get()
                except Exception:
                    pass
            return cv2.Canny(u8, canny_t1, canny_t2)

        # 8. Morphology (Gradient)
        elif algo == "Morphology (Gradient)":
            k = max(3, kernel_size | 1)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(frame)
                    return cv2.morphologyEx(u, cv2.MORPH_GRADIENT, kernel).get()
                except Exception:
                    pass
            return cv2.morphologyEx(frame, cv2.MORPH_GRADIENT, kernel)

        # 9. Median Blur (Heavy)
        elif algo == "Median Blur (Heavy)":
            k = max(3, min(31, kernel_size | 1))
            # OpenCV medianBlur requires uint8 or uint16 (k<=5 for uint16)
            if frame.dtype == np.uint16 and k > 5:
                u8 = (frame >> 8 if frame.max() > 4095 else frame >> 4).astype(np.uint8)
                res_u8 = cv2.medianBlur(u8, k)
                return (res_u8.astype(np.uint16) << 8) if frame.max() > 4095 else (res_u8.astype(np.uint16) << 4)
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(frame)
                    return cv2.medianBlur(u, k).get()
                except Exception:
                    pass
            return cv2.medianBlur(frame, k)

        # 10. Bilateral Filter (Heavy)
        elif algo == "Bilateral Filter (Heavy)":
            d = max(5, min(15, kernel_size))
            u8 = frame
            if frame.dtype != np.uint8:
                u8 = (frame >> 8 if frame.max() > 4095 else frame >> 4).astype(np.uint8)
            if u8.ndim == 3 and u8.shape[2] == 4:
                u8 = cv2.cvtColor(u8, cv2.COLOR_BGRA2BGR)
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(u8)
                    return cv2.bilateralFilter(u, d, 75, 75).get()
                except Exception:
                    pass
            return cv2.bilateralFilter(u8, d, 75, 75)

        # 11. Convolution 2D (Spatial Filter)
        elif algo == "Convolution 2D":
            k = max(3, min(25, kernel_size | 1))
            kernel = np.ones((k, k), dtype=np.float32) / float(k * k)
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(frame)
                    return cv2.filter2D(u, -1, kernel).get()
                except Exception:
                    pass
            return cv2.filter2D(frame, -1, kernel)

        # 12. CLAHE (Adaptive Histogram)
        elif algo == "CLAHE (Adaptive Histogram)":
            u8 = frame
            if frame.dtype != np.uint8:
                u8 = (frame >> 8 if frame.max() > 4095 else frame >> 4).astype(np.uint8)
            clahe = cv2.createCLAHE(clipLimit=max(0.5, float(clahe_clip)), tileGridSize=(8, 8))
            if u8.ndim == 2:
                return clahe.apply(u8)
            elif u8.ndim == 3:
                if u8.shape[2] == 3:
                    lab = cv2.cvtColor(u8, cv2.COLOR_BGR2LAB)
                    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
                    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
                elif u8.shape[2] == 4:
                    lab = cv2.cvtColor(u8[:, :, :3], cv2.COLOR_BGR2LAB)
                    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
                    bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
                    return cv2.merge([bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2], u8[:, :, 3]])
            return u8

        # 13. Multi-Pass Blur (Stress x5 / x15)
        elif algo in ("Multi-Pass Blur (Stress x5)", "Multi-Pass Blur (Stress x15)"):
            passes = 15 if "x15" in algo else max(1, iterations)
            k = max(3, min(25, kernel_size | 1))
            if use_opencl and cv2.ocl.haveOpenCL():
                try:
                    u = cv2.UMat(frame)
                    for _ in range(passes):
                        u = cv2.GaussianBlur(u, (k, k), 0)
                    return u.get()
                except Exception:
                    pass
            cur = frame
            for _ in range(passes):
                cur = cv2.GaussianBlur(cur, (k, k), 0)
            return cur

        # 14. Artificial CPU Burn
        elif algo == "Artificial CPU Burn":
            target_s = max(0.0, burn_ms / 1000.0)
            if target_s > 0.0:
                deadline = time.perf_counter() + target_s
                arr = frame.astype(np.float32)
                while time.perf_counter() < deadline:
                    arr = arr * 0.9999 + 0.0001
                return arr.astype(frame.dtype)
            return frame

        return frame


class ImageProcessorBenchmark_win(WindowBase):
    """
    High-Performance Image Processor Benchmark & Queue Stress-Tester.

    Features:
      - Multiprocessing execution via ProcessingBase (zero UI blocking)
      - Extensive suite of algorithms spanning light, medium, heavy, and extreme workloads
      - Hardware OpenCL acceleration toggle (UMat) for real-time CPU vs GPU benchmarking
      - Bounded multiprocessing queue with configurable capacity (buffer_size) and drop policies
      - Universal IOType support: FRAME, FRAME12, FRAME16, MASK, FRAME_MASK_PAIR
      - Live telemetry: In/Out FPS, worker execution time (ms), queue fill %, and drop counters
      - Full integration with FlowMetricsViewer (discovered automatically via self.proc)
    """

    label: str = "Image Processor Benchmark"
    win_width: int = display_scaling.scale(380)
    win_height: int = display_scaling.scale(430)
    description: str = "Multiprocessing image processing benchmark & stress-tester with OpenCL GPU support."

    accepted_input_types: list[type] = [
        IOTypes.FRAME,
        IOTypes.FRAME12,
        IOTypes.FRAME16,
        IOTypes.MASK,
        IOTypes.FRAME_MASK_PAIR,
        IOTypes.ANY,
    ]
    outputs: dict[str, type] = {
        "Frame": IOTypes.FRAME,
        "Mask": IOTypes.MASK,
        "Metrics": IOTypes.CMD_DICT,
    }
    persistent_fields: list[str] = [
        "label",
        "algorithm",
        "use_opencl",
        "kernel_size",
        "iterations",
        "burn_ms",
        "thresh_val",
        "canny_t1",
        "canny_t2",
        "clahe_clip",
        "buffer_size",
        "drop_policy",
        "universal_any_output",
    ]

    # Persistent defaults for WindowBase introspection
    algorithm: str = "Gaussian Blur (Heavy 25x25)"
    use_opencl: bool = False
    kernel_size: int = 25
    iterations: int = 5
    burn_ms: float = 5.0
    thresh_val: int = 128
    canny_t1: int = 50
    canny_t2: int = 150
    clahe_clip: float = 2.0
    buffer_size: int = 5
    drop_policy: str = "drop_oldest"
    universal_any_output: bool = False

    _ALGORITHMS: list[str] = [
        "Pass-Through (0ms Baseline)",
        "Invert / Negative",
        "Threshold / Binarize",
        "Gaussian Blur (Light 5x5)",
        "Gaussian Blur (Heavy 25x25)",
        "Sobel Gradient",
        "Canny Edge Detector",
        "Morphology (Gradient)",
        "Median Blur (Heavy)",
        "Bilateral Filter (Heavy)",
        "Convolution 2D",
        "CLAHE (Adaptive Histogram)",
        "Multi-Pass Blur (Stress x5)",
        "Multi-Pass Blur (Stress x15)",
        "Artificial CPU Burn",
    ]

    _DROP_POLICIES: list[str] = [
        "drop_oldest",
        "drop_new",
        "block",
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Configurable Parameters with safe fallback against NoneType
        raw_algo = getattr(self, "algorithm", None)
        self.algorithm = str(raw_algo) if raw_algo in self._ALGORITHMS else "Gaussian Blur (Heavy 25x25)"

        self.use_opencl = bool(getattr(self, "use_opencl", False))

        raw_k = getattr(self, "kernel_size", None)
        self.kernel_size = 25 if raw_k is None else max(3, int(raw_k) | 1)

        raw_iter = getattr(self, "iterations", None)
        self.iterations = 5 if raw_iter is None else max(1, int(raw_iter))

        raw_burn = getattr(self, "burn_ms", None)
        self.burn_ms = 5.0 if raw_burn is None else max(0.0, float(raw_burn))

        raw_thresh = getattr(self, "thresh_val", None)
        self.thresh_val = 128 if raw_thresh is None else max(0, min(255, int(raw_thresh)))

        raw_c1 = getattr(self, "canny_t1", None)
        self.canny_t1 = 50 if raw_c1 is None else max(1, min(255, int(raw_c1)))

        raw_c2 = getattr(self, "canny_t2", None)
        self.canny_t2 = 150 if raw_c2 is None else max(1, min(255, int(raw_c2)))

        raw_cl = getattr(self, "clahe_clip", None)
        self.clahe_clip = 2.0 if raw_cl is None else max(0.5, float(raw_cl))

        raw_buf = getattr(self, "buffer_size", None)
        self.buffer_size = 5 if raw_buf is None else max(1, int(raw_buf))

        raw_policy = getattr(self, "drop_policy", None)
        self.drop_policy = str(raw_policy) if raw_policy in self._DROP_POLICIES else "drop_oldest"

        self.universal_any_output = bool(getattr(self, "universal_any_output", False))

        # FlowMetricsViewer Integration Attributes
        self.max_queue_size: int = self.buffer_size
        self.dropped_count: int = 0
        self._rejected_count: int = 0  # producer-side rejections (queue full)
        self._worker_drop_base: int = 0  # worker drop_count() snapshot at last reset
        self.last_exec_ms: float = 0.0

        # OpenCL Device Detection
        self._has_opencl = cv2.ocl.haveOpenCL()
        self._opencl_device_name = "OpenCL GPU"
        if self._has_opencl:
            try:
                self._opencl_device_name = cv2.ocl.Device.getDefault().name()
            except Exception:
                self._opencl_device_name = "OpenCL GPU"

        # Unique DPG Tags
        self._tag_opencl_info = f"ipb_ocl_info_{self.UUID}"
        self._tag_in_fps = f"ipb_in_fps_{self.UUID}"
        self._tag_out_fps = f"ipb_out_fps_{self.UUID}"
        self._tag_compute_ms = f"ipb_comp_ms_{self.UUID}"
        self._tag_queue_fill = f"ipb_q_fill_{self.UUID}"
        self._tag_drops = f"ipb_drops_{self.UUID}"
        self._tag_kernel_slider = f"ipb_kernel_{self.UUID}"
        self._tag_burn_slider = f"ipb_burn_{self.UUID}"
        self._tag_passes_slider = f"ipb_passes_{self.UUID}"

        # Parameter Section & Group Tags (Dynamic Context-Aware Visibility)
        self._tag_params_section = f"ipb_sec_params_{self.UUID}"
        self._tag_group_kernel = f"ipb_grp_k_{self.UUID}"
        self._tag_group_passes = f"ipb_grp_p_{self.UUID}"
        self._tag_group_burn = f"ipb_grp_b_{self.UUID}"
        self._tag_group_thresh = f"ipb_grp_th_{self.UUID}"
        self._tag_group_canny = f"ipb_grp_c_{self.UUID}"
        self._tag_group_clahe = f"ipb_grp_cl_{self.UUID}"

        # Rate and Timing Telemetry
        self._rate_in_times: collections.deque[float] = collections.deque(maxlen=30)
        self._rate_out_times: collections.deque[float] = collections.deque(maxlen=30)
        self._compute_times: collections.deque[float] = collections.deque(maxlen=30)
        self._last_ui_update_ts: float = 0.0

        # Detected Input State
        self._last_detected_shape: Optional[Tuple[int, ...]] = None
        self._last_detected_dtype: str = "N/A"
        self._last_detected_iotype: Any = IOTypes.FRAME

        # ProcessingBase Multiprocessing Worker & Polling Thread
        self.proc: Optional[ImageProcessorBenchmarkWorker] = None
        self._stop_poll = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None

        self._rebuild_worker()
        self._start_poll_thread()
        self.create()

    # --------------------------------------------------------------------------
    # FlowMetricsViewer Protocol Properties
    # --------------------------------------------------------------------------
    def is_ready(self) -> bool:
        """Indicates if worker process is running and input queue has space."""
        if self.proc is not None:
            return self.proc.is_ready()
        return True

    @property
    def is_waiting(self) -> bool:
        """Returns True if the worker input queue is saturated or waiting downstream."""
        if self.proc is not None:
            try:
                q = self.proc.queue_size()
                return self.proc._in_queue.full() if q < 0 else q >= self.buffer_size
            except Exception:
                return False
        return False

    # --------------------------------------------------------------------------
    # Multiprocessing Worker Management
    # --------------------------------------------------------------------------
    def _build_worker_params(self) -> Dict[str, Any]:
        """Collect current parameter values into a dictionary for the worker."""
        return {
            "algorithm": self.algorithm,
            "use_opencl": self.use_opencl,
            "kernel_size": self.kernel_size,
            "iterations": self.iterations,
            "burn_ms": self.burn_ms,
            "thresh_val": self.thresh_val,
            "canny_t1": self.canny_t1,
            "canny_t2": self.canny_t2,
            "clahe_clip": self.clahe_clip,
        }

    def _rebuild_worker(self) -> None:
        """Instantiate or restart the dedicated child worker process safely."""
        old_proc = self.proc
        self.proc = None
        if old_proc is not None:
            # Keep accumulated drops across worker restarts
            self._rejected_count += max(0, old_proc.drop_count() - self._worker_drop_base)
            self._worker_drop_base = 0
            try:
                old_proc.stop()
            except Exception:
                pass

        self.max_queue_size = self.buffer_size
        new_proc = ImageProcessorBenchmarkWorker(
            params=self._build_worker_params(),
            buffer_size=max(1, int(self.buffer_size)),
            drop_policy=str(self.drop_policy),
        )
        new_proc.start()
        self.proc = new_proc

    def _sync_worker_params(self) -> None:
        """Send updated parameters to running worker without recreating process."""
        if self.proc is not None:
            self.proc.update_params(**self._build_worker_params())

    def _start_poll_thread(self) -> None:
        """Launch background polling thread to fetch results from the worker out_queue."""
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_worker_loop,
            name=f"ImageProcessorBench_poll_{self.UUID}",
            daemon=True,
        )
        self._poll_thread.start()

    def _poll_worker_loop(self) -> None:
        """Background loop retrieving processed results from worker process."""
        while not self._stop_poll.is_set():
            if self.proc is None:
                time.sleep(0.01)
                continue

            proc = self.proc
            try:
                # Blocking get: releases the GIL and wakes up exactly when a result is ready
                # (instead of get_nowait + sleep(1ms) polling which multiplies GIL handoffs).
                item = proc.get(timeout=0.05)
            except Exception:
                item = None

            if item is None:
                continue

            try:
                self._handle_completed_result(item)
            except Exception as e:
                logger.error(f"ImageProcessorBenchmark error in completed result: {e}")

    # --------------------------------------------------------------------------
    # Result Dispatch & Telemetry
    # --------------------------------------------------------------------------
    def _handle_completed_result(self, item: Any) -> None:
        """
        Unpacks worker output, updates rolling stats, and dispatches to downstream connections.
        Item format: (out_frame, accessory_type, accessory_val, resolved_iotype, t_submit, t_worker_in, t_worker_out, compute_ms)
        """
        now = time.perf_counter()
        self._rate_out_times.append(now)

        if not isinstance(item, tuple) or len(item) != 8:
            return

        (
            out_frame,
            accessory_type,
            accessory_val,
            resolved_iotype,
            t_submit,
            t_worker_in,
            t_worker_out,
            compute_ms,
        ) = item

        self.last_exec_ms = compute_ms
        self._compute_times.append(compute_ms)

        # 1. Reconstruct outgoing payload
        if accessory_type == "name" and isinstance(accessory_val, str):
            payload: Any = (out_frame, accessory_val)
        elif accessory_type == "mask" and isinstance(accessory_val, np.ndarray):
            payload = (out_frame, accessory_val)
        else:
            payload = out_frame

        # 2. Dispatch to Frame connections
        out_iotype = IOTypes.ANY if self.universal_any_output else resolved_iotype
        frame_targets = self.connections.get("Frame", [])
        if frame_targets:
            for tgt in list(frame_targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=payload, data_type=out_iotype)
                    except Exception as e:
                        logger.error(f"ImageProcessorBenchmark dispatch error to {getattr(tgt, 'winID', tgt)}: {e}")

        # 3. Dispatch to Mask connections if mask is present
        if accessory_type == "mask" and isinstance(accessory_val, np.ndarray):
            mask_targets = self.connections.get("Mask", [])
            if mask_targets:
                for tgt in list(mask_targets):
                    cb = getattr(tgt, "input_cb", None)
                    if callable(cb):
                        try:
                            cb(data=accessory_val, data_type=IOTypes.MASK)
                        except Exception:
                            pass

        # 4. Dispatch Metrics if wired
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
                "use_opencl": self.use_opencl,
            }
            for tgt in list(metrics_targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=metrics_payload, data_type=IOTypes.CMD_DICT)
                    except Exception:
                        pass

        # 5. Throttled UI Telemetry Update (~5 Hz)
        if (now - self._last_ui_update_ts) >= 0.20:
            self._last_ui_update_ts = now
            self._update_ui_telemetry()

    def _calc_rate(self, dq: collections.deque[float]) -> float:
        """Computes rate in Hz from a timestamps deque."""
        if len(dq) < 2:
            return 0.0
        duration = dq[-1] - dq[0]
        return float((len(dq) - 1) / duration) if duration > 0.0 else 0.0

    def _update_ui_telemetry(self) -> None:
        """Updates text and badges in DearPyGui window without hardcoded colors."""
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
            if dpg.does_item_exist(self._tag_in_fps):
                dpg.set_value(self._tag_in_fps, f"{in_hz:.1f} FPS")

            if dpg.does_item_exist(self._tag_out_fps):
                dpg.set_value(self._tag_out_fps, f"{out_hz:.1f} FPS")

            if dpg.does_item_exist(self._tag_compute_ms):
                dpg.set_value(self._tag_compute_ms, f"{avg_comp:.3f} ms")

            if dpg.does_item_exist(self._tag_queue_fill):
                dpg.set_value(self._tag_queue_fill, f"{q_size} / {max_q} ({fill_pct:.0f}%)")

            if dpg.does_item_exist(self._tag_drops):
                dpg.set_value(self._tag_drops, str(self.dropped_count))
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # Node Editor Input Callback
    # --------------------------------------------------------------------------
    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Receives input visual stream frames and forwards to ProcessingBase worker.
        Accepts:
          - IOTypes.FRAME (8-bit numpy ndarray or (frame, name) tuple)
          - IOTypes.FRAME12 (12-bit uint16 ndarray)
          - IOTypes.FRAME16 (16-bit uint16 ndarray)
          - IOTypes.MASK (Binary ROI uint8 ndarray)
          - IOTypes.FRAME_MASK_PAIR ((frame, mask) tuple)
          - Universal ANY pin inputs
        """
        data = kwargs.get("data") if "data" in kwargs else (args[0] if args else None)
        data_type = kwargs.get("data_type", None)
        if data is None and "sample" in kwargs:
            data = kwargs["sample"]
        if data is None and "frame" in kwargs:
            data = kwargs["frame"]
        if data is None:
            return

        t_submit = time.perf_counter()
        self._rate_in_times.append(t_submit)

        frame = None
        accessory_type = None
        accessory_val = None
        resolved_iotype = data_type or IOTypes.FRAME

        # 1. FRAME_MASK_PAIR: (frame, mask)
        if (
            isinstance(data, (tuple, list))
            and len(data) == 2
            and isinstance(data[0], np.ndarray)
            and isinstance(data[1], np.ndarray)
        ):
            frame = data[0]
            accessory_type = "mask"
            accessory_val = data[1]
            resolved_iotype = IOTypes.FRAME_MASK_PAIR

        # 2. Stream accessory name: (frame, name)
        elif isinstance(data, tuple) and len(data) >= 2 and isinstance(data[0], np.ndarray) and isinstance(data[1], str):
            frame = data[0]
            accessory_type = "name"
            accessory_val = data[1]

        # 3. Direct numpy ndarray
        elif isinstance(data, np.ndarray):
            frame = data

        # 4. Dictionary format: {"frame": ..., "mask": ...}
        elif isinstance(data, dict) and "frame" in data:
            frame = data["frame"]
            if "mask" in data:
                accessory_type = "mask"
                accessory_val = data["mask"]
                resolved_iotype = IOTypes.FRAME_MASK_PAIR

        if frame is None or not isinstance(frame, np.ndarray):
            return

        self._last_detected_shape = frame.shape
        self._last_detected_dtype = str(frame.dtype)
        self._last_detected_iotype = resolved_iotype

        if self.proc is None:
            return

        payload = (frame, accessory_type, accessory_val, resolved_iotype, t_submit)
        submitted = self.proc.submit(payload)

        if not submitted:
            self._rejected_count += 1
        self._refresh_dropped_count()

    def _refresh_dropped_count(self) -> None:
        """dropped = producer-side rejections + worker-side drop_oldest discards (since last reset)."""
        worker_drops = 0
        if self.proc is not None:
            worker_drops = self.proc.drop_count() - self._worker_drop_base
        self.dropped_count = self._rejected_count + max(0, worker_drops)

    # --------------------------------------------------------------------------
    # DearPyGui Window Construction
    # --------------------------------------------------------------------------
    def create(self) -> None:
        with self.window():
            # Section: Algorithm Selection & GPU Acceleration
            with dpg.group(horizontal=True):
                dpg.add_text("Algorithm:")
                dpg.add_combo(
                    items=self._ALGORITHMS,
                    default_value=self.algorithm,
                    callback=self._on_algorithm_changed,
                    width=display_scaling.scale(230),
                )

            # OpenCL GPU Acceleration Toggle
            ocl_label = f"[{self._opencl_device_name}]" if (self._has_opencl and self.use_opencl) else "[CPU]"
            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    label="Use OpenCL (GPU)",
                    default_value=self.use_opencl,
                    callback=self._on_opencl_toggled,
                    enabled=self._has_opencl,
                )
                dpg.add_text(ocl_label, tag=self._tag_opencl_info)

            # Section: Workload Stress Parameters (Dynamically shown only when relevant)
            with dpg.group(tag=self._tag_params_section, show=False):
                dpg.add_separator()
                with dpg.group(tag=self._tag_group_kernel, show=False):
                    dpg.add_slider_int(
                        label="Kernel Size",
                        default_value=self.kernel_size,
                        min_value=3,
                        max_value=51,
                        clamped=True,
                        tag=self._tag_kernel_slider,
                        callback=self._on_kernel_changed,
                        width=display_scaling.scale(200),
                    )

                with dpg.group(tag=self._tag_group_passes, show=False):
                    dpg.add_slider_int(
                        label="Passes",
                        default_value=self.iterations,
                        min_value=1,
                        max_value=20,
                        clamped=True,
                        tag=self._tag_passes_slider,
                        callback=self._on_iterations_changed,
                        width=display_scaling.scale(200),
                    )

                with dpg.group(tag=self._tag_group_burn, show=False):
                    dpg.add_slider_float(
                        label="CPU Burn",
                        default_value=self.burn_ms,
                        min_value=0.0,
                        max_value=100.0,
                        format="%.1f ms",
                        tag=self._tag_burn_slider,
                        callback=self._on_burn_changed,
                        width=display_scaling.scale(200),
                    )

                with dpg.group(tag=self._tag_group_thresh, show=False):
                    dpg.add_slider_int(
                        label="Threshold",
                        default_value=self.thresh_val,
                        min_value=0,
                        max_value=255,
                        clamped=True,
                        callback=self._on_thresh_changed,
                        width=display_scaling.scale(200),
                    )

                with dpg.group(tag=self._tag_group_canny, show=False):
                    dpg.add_slider_int(
                        label="Canny Low",
                        default_value=self.canny_t1,
                        min_value=1,
                        max_value=255,
                        clamped=True,
                        callback=self._on_canny_t1_changed,
                        width=display_scaling.scale(200),
                    )
                    dpg.add_slider_int(
                        label="Canny High",
                        default_value=self.canny_t2,
                        min_value=1,
                        max_value=255,
                        clamped=True,
                        callback=self._on_canny_t2_changed,
                        width=display_scaling.scale(200),
                    )

                with dpg.group(tag=self._tag_group_clahe, show=False):
                    dpg.add_slider_float(
                        label="CLAHE Clip",
                        default_value=self.clahe_clip,
                        min_value=0.5,
                        max_value=10.0,
                        format="%.1f",
                        clamped=True,
                        callback=self._on_clahe_clip_changed,
                        width=display_scaling.scale(200),
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
                max_value=50,
                clamped=True,
                callback=self._on_buffer_size_changed,
                width=display_scaling.scale(160),
            )

            dpg.add_button(
                label="Recreate / Reapply Worker",
                callback=self._on_recreate_worker_clicked,
            )

            dpg.add_separator()

            # Section: Live Performance Telemetry
            dpg.add_text("Live Diagnostics")
            with dpg.group(horizontal=True):
                dpg.add_text("In Rate: ")
                dpg.add_text("0.0 FPS", tag=self._tag_in_fps)
                dpg.add_spacer(width=display_scaling.scale(15))
                dpg.add_text("Out Rate: ")
                dpg.add_text("0.0 FPS", tag=self._tag_out_fps)

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
                    callback=self._on_universal_output_toggled,
                )

        self._update_parameter_visibility()

    # --------------------------------------------------------------------------
    # Parameter Visibility & UI Callbacks
    # --------------------------------------------------------------------------
    def _update_parameter_visibility(self) -> None:
        """Dynamically show/hide parameter sliders based on selected algorithm."""
        if not dpg.is_dearpygui_running():
            return

        algo = self.algorithm
        has_kernel = algo in (
            "Gaussian Blur (Heavy 25x25)",
            "Sobel Gradient",
            "Morphology (Gradient)",
            "Median Blur (Heavy)",
            "Bilateral Filter (Heavy)",
            "Convolution 2D",
            "Multi-Pass Blur (Stress x5)",
            "Multi-Pass Blur (Stress x15)",
        )
        has_passes = algo == "Multi-Pass Blur (Stress x5)"
        has_burn = algo == "Artificial CPU Burn"
        has_thresh = algo == "Threshold / Binarize"
        has_canny = algo == "Canny Edge Detector"
        has_clahe = algo == "CLAHE (Adaptive Histogram)"
        has_any_params = has_kernel or has_passes or has_burn or has_thresh or has_canny or has_clahe

        if dpg.does_item_exist(self._tag_params_section):
            dpg.configure_item(self._tag_params_section, show=has_any_params)

        for tag, shown in (
            (self._tag_group_kernel, has_kernel),
            (self._tag_group_passes, has_passes),
            (self._tag_group_burn, has_burn),
            (self._tag_group_thresh, has_thresh),
            (self._tag_group_canny, has_canny),
            (self._tag_group_clahe, has_clahe),
        ):
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, show=shown)

    def _on_algorithm_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.algorithm = app_data
        self._update_parameter_visibility()
        self._sync_worker_params()

    def _on_opencl_toggled(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.use_opencl = bool(app_data)
        if dpg.does_item_exist(self._tag_opencl_info):
            label = f"[{self._opencl_device_name}]" if (self._has_opencl and self.use_opencl) else "[CPU]"
            dpg.set_value(self._tag_opencl_info, label)
        self._sync_worker_params()

    def _on_kernel_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        val = max(3, int(app_data) | 1)
        self.kernel_size = val
        self._sync_worker_params()

    def _on_iterations_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.iterations = max(1, int(app_data))
        self._sync_worker_params()

    def _on_burn_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.burn_ms = max(0.0, float(app_data))
        self._sync_worker_params()

    def _on_thresh_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.thresh_val = max(0, min(255, int(app_data)))
        self._sync_worker_params()

    def _on_canny_t1_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.canny_t1 = max(1, min(255, int(app_data)))
        self._sync_worker_params()

    def _on_canny_t2_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.canny_t2 = max(1, min(255, int(app_data)))
        self._sync_worker_params()

    def _on_clahe_clip_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.clahe_clip = max(0.5, float(app_data))
        self._sync_worker_params()

    def _on_drop_policy_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.drop_policy = str(app_data)
        if self.proc is not None:
            # Must reach the worker process too (drop_oldest is implemented worker-side)
            self.proc.set_drop_policy(self.drop_policy)

    def _on_buffer_size_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.buffer_size = max(1, int(app_data))

    def _on_recreate_worker_clicked(self, *args: Any) -> None:
        logger.info(
            f"[{self.label}] Rebuilding worker process with buffer_size={self.buffer_size}, drop_policy='{self.drop_policy}'"
        )
        self._rebuild_worker()

    def _on_reset_drops_clicked(self, *args: Any) -> None:
        self.dropped_count = 0
        self._rejected_count = 0
        self._worker_drop_base = self.proc.drop_count() if self.proc is not None else 0
        if dpg.does_item_exist(self._tag_drops):
            dpg.set_value(self._tag_drops, "0")

    def _on_universal_output_toggled(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.universal_any_output = bool(app_data)
        self.outputs["Frame"] = IOTypes.ANY if self.universal_any_output else IOTypes.FRAME

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


EXPORTED_CLASS = ImageProcessorBenchmark_win
EXPORTED_NAME = "Image Processor Benchmark"
