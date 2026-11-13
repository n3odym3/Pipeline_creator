"""
Vector Benchmark Window Module (High-Throughput Synthetic Vector Field Generator).

Designed specifically for stress-testing polar distribution plotters, circular spectrograms,
orientation histograms, directional flow algorithms, and vector backpressure pipelines:
  - modules/plot/polar_plot_win.py (Polar Plot Rose diagrams, circular statistics)
  - modules/plot/polar_spectrogram_win.py (Polar Spectrogram temporal orientation heatmaps)

Emits IOTypes.VECTORS and IOTypes.SAMPLE payloads with configurable:
  - Vector field patterns:
      * Rotating Spotlight (continuous angular rotation with von Mises concentration)
      * Uniform Isotropic (completely random white-noise angles)
      * Bimodal Opposing (bidirectional opposing streams)
      * Quad-Modal (orthogonal cross flow)
      * Harmonic Pendulum (angular oscillation back and forth)
      * Brownian Walk (angular drift random walk)
      * Chirp Sweep (accelerating angular rotation)
      * Vortex / Swirl (spiral tangential field)
  - Vector density: 16 to 65,536+ vectors per frame (and custom count)
  - Magnitude distributions: Constant, Rayleigh (thermal), Uniform, Direction-Coupled, Log-Normal
  - Streaming modes:
      * Controlled rate (1..1000 Hz)
      * Uncapped maximum CPU speed
      * Burst Mode (blasts exact N frames as fast as possible)
      * 1-Shot Batch Mode (transmits whole N-frame temporal sequence for instant offline spectrogram)
  - Zero-allocation ring-buffered generation for maximum memory-bandwidth throughput
"""

from __future__ import annotations

import collections
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.window_base import WindowBase

_TWO_PI: float = 2.0 * math.pi


class VectorBenchmark_win(WindowBase):
    """
    High-Performance Synthetic Vector Field Generator & Polar Stress-Tester.

    Blasts structured vector fields (angles in [0, 2pi), magnitudes, frame indices)
    to Polar Plot, Polar Spectrogram, or Sample Container windows.
    """

    label: str = "Vector Benchmark"
    win_width: int = display_scaling.scale(400)
    win_height: int = display_scaling.scale(580)
    description: str = "High-throughput synthetic vector field generator for stress-testing polar and circular plots."

    accepted_input_types: list[type] = [
        IOTypes.TRIGGER,
        IOTypes.CMD_DICT,
        IOTypes.NUMBER,
        IOTypes.ANY,
    ]
    outputs: dict[str, type] = {
        "Vectors": IOTypes.VECTORS,
        "Data": IOTypes.SAMPLE,
        "Trigger": IOTypes.TRIGGER,
    }
    persistent_fields: list[str] = [
        "label",
        "vector_pattern",
        "density_preset",
        "custom_count",
        "magnitude_mode",
        "mean_magnitude",
        "angular_dispersion_deg",
        "rotation_freq",
        "rate_target",
        "uncapped_speed",
        "burst_count",
        "batch_frames_count",
        "respect_backpressure",
        "universal_any_output",
        "auto_start",
    ]

    _PATTERNS: list[str] = [
        "Rotating Spotlight",
        "Uniform Isotropic",
        "Bimodal Opposing",
        "Quad-Modal (Cross)",
        "Harmonic Pendulum",
        "Brownian Walk (Drift)",
        "Chirp Sweep",
        "Vortex / Swirl",
    ]

    _DENSITY_PRESETS: dict[str, int] = {
        "16 vectors (Ultra-Light)": 16,
        "64 vectors (Light)": 64,
        "256 vectors (Standard)": 256,
        "1,024 vectors (Dense Field)": 1024,
        "4,096 vectors (Heavy)": 4096,
        "16,384 vectors (Stress)": 16384,
        "65,536 vectors (Extreme Stress)": 65536,
        "Custom": 1024,
    }

    _MAGNITUDE_MODES: list[str] = [
        "Constant (1.0)",
        "Rayleigh (Thermal / Diffusive)",
        "Uniform [0.1, 2.0x]",
        "Direction-Coupled (Faster in peak)",
        "Log-Normal (Intermittent)",
    ]

    # Persistent configuration defaults
    vector_pattern: str = "Rotating Spotlight"
    density_preset: str = "256 vectors (Standard)"
    custom_count: int = 1024
    magnitude_mode: str = "Rayleigh (Thermal / Diffusive)"
    mean_magnitude: float = 5.0
    angular_dispersion_deg: float = 25.0
    rotation_freq: float = 0.5
    rate_target: float = 30.0
    uncapped_speed: bool = False
    burst_count: int = 300
    batch_frames_count: int = 100
    respect_backpressure: bool = True
    universal_any_output: bool = False
    auto_start: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Internal state & thread handles
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._is_bursting: bool = False
        self._burst_thread: Optional[threading.Thread] = None
        self._burst_stop_event = threading.Event()
        self._is_closing: bool = False
        self._lock = threading.Lock()

        # Telemetry & Pacing
        self.seq_counter: int = 0
        self.total_emitted: int = 0
        self.total_vectors_emitted: int = 0
        self.dropped_count: int = 0
        self.measured_rate_hz: float = 0.0
        self.measured_kvect_sec: float = 0.0
        self.last_prep_ms: float = 0.0
        self.last_mean_deg: float = 0.0
        self.last_dir_r: float = 0.0
        self._rate_timestamps: collections.deque[float] = collections.deque(maxlen=60)
        self._rate_lock = threading.Lock()
        self._last_ui_update_ts: float = 0.0

        # Ring buffer precomputation (128 pre-rendered frames)
        self._ring_size: int = 128
        self._vector_ring: list[tuple[np.ndarray, np.ndarray, float, float]] = []
        self._ring_index: int = 0
        self._active_count: int = 256
        self._brownian_angle: float = 0.0

        # UI Tags
        self._tag_btn_toggle = f"vb_btn_toggle_{self.UUID}"
        self._tag_btn_burst = f"vb_btn_burst_{self.UUID}"
        self._tag_burst_count = f"vb_burst_count_{self.UUID}"
        self._tag_btn_batch = f"vb_btn_batch_{self.UUID}"
        self._tag_batch_frames = f"vb_batch_frames_{self.UUID}"
        self._tag_rate_slider = f"vb_rate_slider_{self.UUID}"
        self._tag_uncapped = f"vb_uncapped_{self.UUID}"
        self._tag_pattern = f"vb_pattern_{self.UUID}"
        self._tag_preset = f"vb_preset_{self.UUID}"
        self._tag_grp_custom = f"vb_grp_custom_{self.UUID}"
        self._tag_custom_len = f"vb_custom_len_{self.UUID}"
        self._tag_dispersion = f"vb_dispersion_{self.UUID}"
        self._tag_rot_freq = f"vb_rot_freq_{self.UUID}"
        self._tag_mag_mode = f"vb_mag_mode_{self.UUID}"
        self._tag_mean_mag = f"vb_mean_mag_{self.UUID}"
        self._tag_backpressure = f"vb_backpressure_{self.UUID}"
        self._tag_universal = f"vb_universal_{self.UUID}"
        self._tag_txt_status = f"vb_txt_status_{self.UUID}"
        self._tag_txt_rate = f"vb_txt_rate_{self.UUID}"
        self._tag_txt_throughput = f"vb_txt_throughput_{self.UUID}"
        self._tag_txt_stats = f"vb_txt_stats_{self.UUID}"

        # Initialize
        self._rebuild_ring_buffer()
        self._update_output_iotype()
        self.create()

        if self.auto_start:
            self.start_stream()

    # --------------------------------------------------------------------------
    # Ring Buffer Precomputation (Zero Allocation during Streaming)
    # --------------------------------------------------------------------------
    def _effective_count(self) -> int:
        """Resolves active vector count from preset or custom input."""
        if self.density_preset == "Custom":
            return max(4, int(self.custom_count))
        return self._DENSITY_PRESETS.get(self.density_preset, 256)

    @staticmethod
    def _calc_von_mises_kappa(dispersion_deg: float) -> float:
        """Map angular standard deviation (degrees) to von Mises concentration parameter kappa."""
        sig_rad = max(0.01, math.radians(dispersion_deg))
        if sig_rad < 0.15:
            return 1.0 / (sig_rad * sig_rad)
        if sig_rad < 1.0:
            return float(1.0 / (sig_rad * sig_rad + 0.1))
        return max(0.01, float(1.0 / (sig_rad * 2.0)))

    def _generate_frame_vectors(self, step_idx: int, total_steps: int) -> Tuple[np.ndarray, np.ndarray, float, float]:
        """Generate (angles, magnitudes, mean_angle_deg, directionality_r) for a single time step."""
        n_vec = self._active_count
        pattern = self.vector_pattern
        mag_mode = self.magnitude_mode
        mean_v = max(0.1, float(self.mean_magnitude))
        disp_deg = max(1.0, float(self.angular_dispersion_deg))
        kappa = self._calc_von_mises_kappa(disp_deg)
        f_rot = float(self.rotation_freq)

        # Base phase in [0, 2pi)
        phase_frac = (step_idx % total_steps) / float(total_steps)
        theta_base = (2.0 * math.pi * phase_frac * f_rot * 2.0) % _TWO_PI

        # 1. Generate Angles according to pattern
        if pattern == "Rotating Spotlight":
            angles = np.random.vonmises(theta_base, kappa, n_vec).astype(np.float32)

        elif pattern == "Uniform Isotropic":
            angles = np.random.uniform(0.0, _TWO_PI, n_vec).astype(np.float32)

        elif pattern == "Bimodal Opposing":
            n_half = n_vec // 2
            a1 = np.random.vonmises(theta_base, kappa, n_half)
            a2 = np.random.vonmises((theta_base + math.pi) % _TWO_PI, kappa, n_vec - n_half)
            angles = np.concatenate([a1, a2]).astype(np.float32)

        elif pattern == "Quad-Modal (Cross)":
            angles_list: List[np.ndarray] = []
            chunk = max(1, n_vec // 4)
            for k in range(4):
                cnt = chunk if k < 3 else (n_vec - chunk * 3)
                mu_k = (theta_base + k * (math.pi / 2.0)) % _TWO_PI
                angles_list.append(np.random.vonmises(mu_k, kappa, cnt))
            angles = np.concatenate(angles_list).astype(np.float32)

        elif pattern == "Harmonic Pendulum":
            # Oscillate between -60° and +60° around base center
            amp = math.radians(60.0)
            osc_center = math.pi
            mu_t = (osc_center + amp * math.sin(2.0 * math.pi * phase_frac * f_rot * 4.0)) % _TWO_PI
            angles = np.random.vonmises(mu_t, kappa, n_vec).astype(np.float32)

        elif pattern == "Brownian Walk (Drift)":
            self._brownian_angle = (self._brownian_angle + np.random.normal(0.0, math.radians(disp_deg * 0.2))) % _TWO_PI
            angles = np.random.vonmises(self._brownian_angle, kappa, n_vec).astype(np.float32)

        elif pattern == "Chirp Sweep":
            # Frequency accelerates quadratically
            chirp_phase = (2.0 * math.pi * (phase_frac ** 2) * f_rot * 6.0) % _TWO_PI
            angles = np.random.vonmises(chirp_phase, kappa, n_vec).astype(np.float32)

        elif pattern == "Vortex / Swirl":
            # Tangential swirl angles
            base_grid = np.linspace(0.0, _TWO_PI, n_vec, endpoint=False)
            angles = (base_grid + (math.pi / 2.0) + np.random.normal(0.0, math.radians(disp_deg), n_vec)) % _TWO_PI
            angles = angles.astype(np.float32)

        else:
            angles = np.random.vonmises(theta_base, kappa, n_vec).astype(np.float32)

        # Wrap cleanly in [0, 2pi)
        angles = np.mod(angles, _TWO_PI).astype(np.float32)

        # 2. Generate Magnitudes according to mode
        if mag_mode == "Constant (1.0)":
            mags = np.full(n_vec, mean_v, dtype=np.float32)

        elif mag_mode == "Rayleigh (Thermal / Diffusive)":
            # Rayleigh mean is sigma * sqrt(pi/2) -> sigma = mean / 1.2533
            sig = mean_v / 1.2533
            mags = np.random.rayleigh(scale=sig, size=n_vec).astype(np.float32)

        elif mag_mode == "Uniform [0.1, 2.0x]":
            mags = np.random.uniform(0.1, 2.0 * mean_v, n_vec).astype(np.float32)

        elif mag_mode == "Direction-Coupled (Faster in peak)":
            # Speed boosts along theta_base
            alignment = np.maximum(0.0, np.cos(angles - theta_base))
            mags = (mean_v * (0.25 + 1.5 * (alignment ** 1.5))).astype(np.float32)

        elif mag_mode == "Log-Normal (Intermittent)":
            mags = np.random.lognormal(mean=math.log(mean_v), sigma=0.6, size=n_vec).astype(np.float32)

        else:
            mags = np.full(n_vec, mean_v, dtype=np.float32)

        # 3. Calculate true circular statistics
        cos_sum = float(np.sum(np.cos(angles)))
        sin_sum = float(np.sum(np.sin(angles)))
        mean_ang_rad = math.atan2(sin_sum, cos_sum) % _TWO_PI
        mean_ang_deg = math.degrees(mean_ang_rad)
        dir_r = math.hypot(cos_sum, sin_sum) / max(1, n_vec)

        return angles, mags, mean_ang_deg, dir_r

    def _rebuild_ring_buffer(self) -> None:
        """Precomputes vector frames in memory for zero-allocation streaming."""
        with self._lock:
            n_vec = self._effective_count()
            self._active_count = n_vec
            new_ring: list[tuple[np.ndarray, np.ndarray, float, float]] = []

            for i in range(self._ring_size):
                ang, mag, mean_deg, dir_r = self._generate_frame_vectors(i, self._ring_size)
                new_ring.append((ang, mag, mean_deg, dir_r))

            self._vector_ring = new_ring
            self._ring_index = 0

    def _update_output_iotype(self) -> None:
        """Sets node editor pin type according to universal output toggle."""
        self.outputs["Data"] = IOTypes.ANY if self.universal_any_output else IOTypes.SAMPLE
        self.outputs["Vectors"] = IOTypes.ANY if self.universal_any_output else IOTypes.VECTORS

    # --------------------------------------------------------------------------
    # Streaming & Low-Jitter Emission Loops
    # --------------------------------------------------------------------------
    def start_stream(self) -> None:
        """Start background emission thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name=f"VectorBenchmark_{self.UUID}",
        )
        self._thread.start()
        if dpg.does_item_exist(self._tag_btn_toggle):
            dpg.configure_item(self._tag_btn_toggle, label="Stop Stream")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, "Stream Status: Running")

    def stop_stream(self) -> None:
        """Stop background emission thread."""
        self._running = False
        if dpg.does_item_exist(self._tag_btn_toggle):
            dpg.configure_item(self._tag_btn_toggle, label="Start Stream")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, "Stream Status: Stopped")

    def start_burst(self, count: Optional[int] = None) -> None:
        """Blast exactly `count` vector frames as fast as possible in a dedicated background thread."""
        if self._is_bursting:
            return
        if self._running:
            self.stop_stream()

        n = int(count if count is not None else self.burst_count)
        if n <= 0:
            return

        self._is_bursting = True
        self._burst_stop_event.clear()
        self._burst_thread = threading.Thread(
            target=self._burst_loop,
            args=(n,),
            daemon=True,
            name=f"VectorBenchmark_burst_{self.UUID}",
        )
        self._burst_thread.start()
        if dpg.does_item_exist(self._tag_btn_burst):
            dpg.configure_item(self._tag_btn_burst, label="Stop Burst")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, f"Stream Status: Bursting ({n:,} frames)...")

    def stop_burst(self) -> None:
        """Cancel active burst emission."""
        self._burst_stop_event.set()
        self._is_bursting = False
        if dpg.does_item_exist(self._tag_btn_burst):
            dpg.configure_item(self._tag_btn_burst, label="Send Burst")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, "Stream Status: Stopped")

    def _burst_loop(self, total_n: int) -> None:
        """Blasts total_n frames at maximum CPU memory-bandwidth speed."""
        t0 = time.perf_counter()
        sent = 0

        while sent < total_n and not self._burst_stop_event.is_set() and not self._is_closing:
            if self.respect_backpressure and not self.is_outputs_ready():
                self.dropped_count += 1
                time.sleep(0.0005)
                continue

            self._emit_single_frame()
            sent += 1
            if (sent & 63) == 0:
                time.sleep(0)  # Yield GIL periodically so GUI and IPC remain fluid

        t1 = time.perf_counter()
        dur_ms = max(0.001, (t1 - t0) * 1000.0)
        hz = (sent / (dur_ms / 1000.0)) if dur_ms > 0 else 0.0

        self._is_bursting = False
        if dpg.does_item_exist(self._tag_btn_burst):
            dpg.configure_item(self._tag_btn_burst, label="Send Burst")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(
                self._tag_txt_status,
                f"Burst Done: {sent:,}/{total_n:,} in {dur_ms:.1f} ms ({hz:,.0f} Hz)",
            )

    def send_batch_vectors(self, n_frames: Optional[int] = None) -> None:
        """
        Package exactly `n_frames` into a 1-shot batch payload and transmit immediately.
        Tests offline / multi-frame batch ingestion in Polar Plot and Polar Spectrogram.
        """
        count = int(n_frames if n_frames is not None else self.batch_frames_count)
        if count <= 0:
            return

        t0 = time.perf_counter()
        frames_list: List[Dict[str, Any]] = []

        for i in range(count):
            ang, mag, _, _ = self._generate_frame_vectors(i, count)
            frames_list.append({
                "angles": ang,
                "magnitudes": mag,
                "frame_idx": i,
            })

        all_angles = np.concatenate([f["angles"] for f in frames_list])
        all_mags = np.concatenate([f["magnitudes"] for f in frames_list])

        batch_payload: Dict[str, Any] = {
            "action": "batch_vectors",
            "fps": float(self.rate_target),
            "delta_t": float(1.0 / max(0.1, self.rate_target)),
            "frames": frames_list,
            "angles": all_angles,
            "magnitudes": all_mags,
            "source": self.label,
        }

        # Dispatch
        out_iotype = IOTypes.ANY if self.universal_any_output else IOTypes.VECTORS
        for port in ("Vectors", "Data"):
            for tgt in list(self.connections.get(port, [])):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=batch_payload, sample=batch_payload, data_type=out_iotype)
                    except Exception as e:
                        logger.debug(f"VectorBenchmark batch dispatch error to {getattr(tgt, 'winID', tgt)}: {e}")

        dur_ms = (time.perf_counter() - t0) * 1000.0
        tot_vecs = int(all_angles.size)
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(
                self._tag_txt_status,
                f"Batch Sent: {count} frames ({tot_vecs:,} vectors) in {dur_ms:.1f} ms",
            )
        logger.success(f"VectorBenchmark sent 1-shot batch of {count} frames ({tot_vecs:,} vectors) to pipeline.")

    def _stream_loop(self) -> None:
        """Precise low-jitter streaming loop supporting controlled pacing or uncapped throughput."""
        next_t = time.perf_counter()

        while self._running and not self._is_closing:
            now = time.perf_counter()

            if self.respect_backpressure and not self.is_outputs_ready():
                self.dropped_count += 1
                time.sleep(0.001)
                next_t = time.perf_counter()
                continue

            self._emit_single_frame()

            if self.uncapped_speed:
                time.sleep(0)  # Maximum throughput with cooperative GIL yield
                next_t = time.perf_counter()
            else:
                interval = 1.0 / max(1.0, float(self.rate_target))
                next_t += interval
                sleep_dur = next_t - time.perf_counter()

                if sleep_dur > 0.002:
                    time.sleep(sleep_dur - 0.001)
                    while time.perf_counter() < next_t:
                        pass
                elif sleep_dur > 0:
                    while time.perf_counter() < next_t:
                        pass
                else:
                    if (now - next_t) > (interval * 3.0):
                        next_t = now

    def _emit_single_frame(self) -> None:
        """Pulls precomputed or dynamic vector field from ring buffer and dispatches to connected nodes."""
        t_prep_0 = time.perf_counter()

        with self._lock:
            if not self._vector_ring:
                self._rebuild_ring_buffer()
            ang_arr, mag_arr, mean_deg, dir_r = self._vector_ring[self._ring_index]
            self._ring_index = (self._ring_index + 1) % len(self._vector_ring)

        t_prep_1 = time.perf_counter()
        self.last_prep_ms = max(0.0001, (t_prep_1 - t_prep_0) * 1000.0)
        self.last_mean_deg = mean_deg
        self.last_dir_r = dir_r

        self.seq_counter += 1
        seq = self.seq_counter
        now = time.perf_counter()

        # Build payload
        payload: Dict[str, Any] = {
            "angles": ang_arr,
            "magnitudes": mag_arr,
            "frame_idx": seq,
            "timestamp": now,
            "source": self.label,
            "x": mag_arr,
            "y": ang_arr,
            "name": f"Vectors_{seq}",
            "uuid": f"vec_{self.UUID[:6]}_{seq}",
        }

        # Track telemetry
        with self._rate_lock:
            self.total_emitted = seq
            self.total_vectors_emitted += len(ang_arr)
            self._rate_timestamps.append(now)

            if len(self._rate_timestamps) >= 2:
                dur = self._rate_timestamps[-1] - self._rate_timestamps[0]
                if dur > 0.0:
                    self.measured_rate_hz = float((len(self._rate_timestamps) - 1) / dur)
                    self.measured_kvect_sec = (self.measured_rate_hz * len(ang_arr)) / 1000.0

        # Dispatch to Vectors output connections
        out_iotype = IOTypes.ANY if self.universal_any_output else IOTypes.VECTORS
        targets_vec = self.connections.get("Vectors", [])
        if targets_vec:
            for tgt in list(targets_vec):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=payload, sample=payload, data_type=out_iotype)
                    except Exception as e:
                        logger.debug(f"VectorBenchmark dispatch error to {getattr(tgt, 'winID', tgt)}: {e}")

        # Dispatch to Data output connections (e.g. LinePlot or SampleContainer)
        targets_data = self.connections.get("Data", [])
        if targets_data:
            for tgt in list(targets_data):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=payload, sample=payload, data_type=IOTypes.SAMPLE)
                    except Exception:
                        pass

        # Dispatch Trigger if wired
        trig_targets = self.connections.get("Trigger", [])
        if trig_targets:
            for tgt in list(trig_targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=f"FRAME_{seq}", data_type=IOTypes.TRIGGER)
                    except Exception:
                        pass

        # Throttled UI Telemetry Update (~6 Hz)
        if (now - self._last_ui_update_ts) >= 0.16:
            self._last_ui_update_ts = now
            self._update_telemetry_ui()

    def _update_telemetry_ui(self) -> None:
        """Update live performance telemetry labels in UI without hardcoded colors."""
        if not dpg.does_item_exist(self._tag_txt_rate):
            return

        with self._rate_lock:
            tot_frames = self.total_emitted
            tot_vecs = self.total_vectors_emitted
            hz = self.measured_rate_hz
            kvect = self.measured_kvect_sec
            prep_ms = self.last_prep_ms
            mean_deg = self.last_mean_deg
            dir_r = self.last_dir_r

        bw_mbs = (kvect * 1000.0 * 8.0) / (1024.0 * 1024.0)  # 2x float32 = 8 bytes per vector

        dpg.set_value(self._tag_txt_rate, f"Emitted: {tot_frames:,} frames  |  Measured: {hz:.1f} Hz")
        dpg.set_value(self._tag_txt_throughput, f"Throughput: {kvect:,.1f} kvec/s  |  {bw_mbs:.2f} MB/s")
        dpg.set_value(
            self._tag_txt_stats,
            f"Active: {self._active_count:,} vecs  |  Mean: {mean_deg:.1f}° (R={dir_r:.2f})  |  Prep: {prep_ms:.3f} ms",
        )

    # --------------------------------------------------------------------------
    # DearPyGui UI Construction
    # --------------------------------------------------------------------------
    def create(self) -> None:
        """Construct the DearPyGui window controls."""
        scale = display_scaling.scale

        with self.window():
            # Section: Stream Execution Controls
            with dpg.group(horizontal=True):
                btn_lbl = "Stop Stream" if self._running else "Start Stream"
                dpg.add_button(
                    label=btn_lbl,
                    tag=self._tag_btn_toggle,
                    width=scale(120),
                    callback=self._on_toggle_clicked,
                )
                dpg.add_button(
                    label="Step",
                    width=scale(65),
                    callback=self._on_step_clicked,
                )
                dpg.add_button(
                    label="Reset",
                    width=scale(65),
                    callback=self._on_reset_clicked,
                )

            # Section: Burst Blaster
            with dpg.group(horizontal=True):
                burst_lbl = "Stop Burst" if self._is_bursting else "Send Burst"
                dpg.add_button(
                    label=burst_lbl,
                    tag=self._tag_btn_burst,
                    width=scale(120),
                    callback=self._on_send_burst_clicked,
                )
                dpg.add_text("Count:")
                dpg.add_input_int(
                    tag=self._tag_burst_count,
                    default_value=self.burst_count,
                    min_value=1,
                    max_value=1000000,
                    width=scale(100),
                    callback=self._on_burst_count_changed,
                )

            # Section: 1-Shot Multi-Frame Batch Importer
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Send Batch",
                    tag=self._tag_btn_batch,
                    width=scale(120),
                    callback=self._on_send_batch_clicked,
                )
                dpg.add_text("Frames:")
                dpg.add_input_int(
                    tag=self._tag_batch_frames,
                    default_value=self.batch_frames_count,
                    min_value=1,
                    max_value=5000,
                    width=scale(100),
                    callback=self._on_batch_frames_changed,
                )

            # Section: Emission Pacing Controls
            dpg.add_slider_float(
                label="Target Rate",
                tag=self._tag_rate_slider,
                default_value=self.rate_target,
                min_value=1.0,
                max_value=1000.0,
                clamped=True,
                format="%.1f Hz",
                width=scale(190),
                callback=self._on_rate_changed,
            )
            dpg.add_checkbox(
                label="Uncapped (Max Throughput)",
                tag=self._tag_uncapped,
                default_value=self.uncapped_speed,
                callback=self._on_uncapped_changed,
            )

            dpg.add_separator()

            # Section: Pattern & Spatial Distribution
            with dpg.group(horizontal=True):
                dpg.add_text("Pattern:")
                dpg.add_combo(
                    items=self._PATTERNS,
                    default_value=self.vector_pattern,
                    tag=self._tag_pattern,
                    width=scale(220),
                    callback=self._on_pattern_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Density:")
                dpg.add_combo(
                    items=list(self._DENSITY_PRESETS.keys()),
                    default_value=self.density_preset,
                    tag=self._tag_preset,
                    width=scale(220),
                    callback=self._on_preset_changed,
                )

            is_custom = self.density_preset == "Custom"
            with dpg.group(tag=self._tag_grp_custom, horizontal=True, show=is_custom):
                dpg.add_text("Custom Count:")
                dpg.add_input_int(
                    default_value=self.custom_count,
                    tag=self._tag_custom_len,
                    min_value=4,
                    max_value=1000000,
                    width=scale(150),
                    callback=self._on_custom_len_changed,
                )

            dpg.add_slider_float(
                label="Dispersion",
                tag=self._tag_dispersion,
                default_value=self.angular_dispersion_deg,
                min_value=2.0,
                max_value=120.0,
                format="%.1f deg",
                width=scale(190),
                callback=self._on_dispersion_changed,
            )

            dpg.add_slider_float(
                label="Rotation Freq",
                tag=self._tag_rot_freq,
                default_value=self.rotation_freq,
                min_value=0.05,
                max_value=5.0,
                format="%.2f Hz",
                width=scale(190),
                callback=self._on_rot_freq_changed,
            )

            dpg.add_separator()

            # Section: Magnitudes / Speed Profiles
            with dpg.group(horizontal=True):
                dpg.add_text("Magnitude:")
                dpg.add_combo(
                    items=self._MAGNITUDE_MODES,
                    default_value=self.magnitude_mode,
                    tag=self._tag_mag_mode,
                    width=scale(220),
                    callback=self._on_mag_mode_changed,
                )

            dpg.add_slider_float(
                label="Mean Speed",
                tag=self._tag_mean_mag,
                default_value=self.mean_magnitude,
                min_value=0.5,
                max_value=50.0,
                format="%.1f um/s",
                width=scale(190),
                callback=self._on_mean_mag_changed,
            )

            dpg.add_separator()

            # Section: Flow Control & Universal Output Pin
            dpg.add_checkbox(
                label="Respect Backpressure (Hold on busy downstream)",
                tag=self._tag_backpressure,
                default_value=self.respect_backpressure,
                callback=self._on_backpressure_changed,
            )
            dpg.add_checkbox(
                label="Universal Pin (IOType.ANY on node)",
                tag=self._tag_universal,
                default_value=self.universal_any_output,
                callback=self._on_universal_toggled,
            )

            dpg.add_separator()

            # Section: Live Diagnostics
            dpg.add_text("Live Diagnostics")
            dpg.add_text("Stream Status: Stopped", tag=self._tag_txt_status)
            dpg.add_text("Emitted: 0 frames  |  Measured: 0.0 Hz", tag=self._tag_txt_rate)
            dpg.add_text("Throughput: 0.0 kvec/s  |  0.00 MB/s", tag=self._tag_txt_throughput)
            dpg.add_text(
                f"Active: {self._active_count:,} vecs  |  Mean: 0.0° (R=0.00)  |  Prep: 0.000 ms",
                tag=self._tag_txt_stats,
            )

    # --------------------------------------------------------------------------
    # UI Event Callbacks
    # --------------------------------------------------------------------------
    def _on_toggle_clicked(self, *args: Any) -> None:
        if self._running:
            self.stop_stream()
        else:
            self.start_stream()

    def _on_step_clicked(self, *args: Any) -> None:
        self._emit_single_frame()

    def _on_send_burst_clicked(self, *args: Any) -> None:
        if self._is_bursting:
            self.stop_burst()
        else:
            self.start_burst()

    def _on_burst_count_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.burst_count = max(1, int(app_data))

    def _on_send_batch_clicked(self, *args: Any) -> None:
        self.send_batch_vectors()

    def _on_batch_frames_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.batch_frames_count = max(1, int(app_data))

    def _on_reset_clicked(self, *args: Any) -> None:
        with self._rate_lock:
            self.total_emitted = 0
            self.total_vectors_emitted = 0
            self.seq_counter = 0
            self.dropped_count = 0
            self._rate_timestamps.clear()
            self.measured_rate_hz = 0.0
            self.measured_kvect_sec = 0.0
            self.last_prep_ms = 0.0
        self._update_telemetry_ui()

    def _on_rate_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.rate_target = max(1.0, float(app_data))

    def _on_uncapped_changed(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.uncapped_speed = bool(app_data)

    def _on_pattern_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.vector_pattern = str(app_data)
        self._rebuild_ring_buffer()
        self._update_telemetry_ui()

    def _on_preset_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.density_preset = str(app_data)
        is_custom = self.density_preset == "Custom"
        if dpg.does_item_exist(self._tag_grp_custom):
            dpg.configure_item(self._tag_grp_custom, show=is_custom)
        self._rebuild_ring_buffer()
        self._update_telemetry_ui()

    def _on_custom_len_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.custom_count = max(4, int(app_data))
        self._rebuild_ring_buffer()
        self._update_telemetry_ui()

    def _on_dispersion_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.angular_dispersion_deg = max(1.0, float(app_data))
        self._rebuild_ring_buffer()

    def _on_rot_freq_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.rotation_freq = max(0.01, float(app_data))
        self._rebuild_ring_buffer()

    def _on_mag_mode_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.magnitude_mode = str(app_data)
        self._rebuild_ring_buffer()

    def _on_mean_mag_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.mean_magnitude = max(0.1, float(app_data))
        self._rebuild_ring_buffer()

    def _on_backpressure_changed(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.respect_backpressure = bool(app_data)

    def _on_universal_toggled(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.universal_any_output = bool(app_data)
        self._update_output_iotype()

    # --------------------------------------------------------------------------
    # Pipeline Input Callback (Remote Triggering & Automation)
    # --------------------------------------------------------------------------
    def input_cb(self, data: Any = None, *args: Any, **kwargs: Any) -> bool:
        """Remote automation control for automated benchmarks and testing suites."""
        payload = (
            data
            if data is not None
            else (kwargs.get("cmd") or kwargs.get("trigger") or kwargs.get("sample") or (args[0] if args else None))
        )

        if isinstance(payload, str):
            cmd = payload.strip().upper()
            if cmd in ("START", "RUN"):
                self.start_stream()
            elif cmd in ("STOP", "PAUSE"):
                self.stop_stream()
            elif cmd in ("BURST", "BLAST"):
                self.start_burst()
            elif cmd in ("BATCH", "IMPORT"):
                self.send_batch_vectors()
            elif cmd == "STEP":
                self._emit_single_frame()
            elif cmd == "RESET":
                self._on_reset_clicked()
            return True

        if isinstance(payload, dict):
            action = str(payload.get("action", "")).lower()
            if action in ("start", "run"):
                self.start_stream()
            elif action in ("stop", "pause"):
                self.stop_stream()
            elif action == "burst":
                cnt = payload.get("count", self.burst_count)
                self.start_burst(cnt)
            elif action == "batch":
                cnt = payload.get("frames", self.batch_frames_count)
                self.send_batch_vectors(cnt)
            elif action == "step":
                self._emit_single_frame()
            elif action == "reset":
                self._on_reset_clicked()
            return True

        if isinstance(payload, (int, float)):
            self.rate_target = max(1.0, float(payload))
            if dpg.does_item_exist(self._tag_rate_slider):
                dpg.set_value(self._tag_rate_slider, self.rate_target)
            return True

        return True

    # --------------------------------------------------------------------------
    # Lifecycle & Cleanup
    # --------------------------------------------------------------------------
    def close(self) -> None:
        """Ensure threads terminate cleanly on node or editor close."""
        self._is_closing = True
        self._running = False
        self._burst_stop_event.set()
        self._is_bursting = False
        super().close()


EXPORTED_CLASS = VectorBenchmark_win
EXPORTED_NAME = "Vector Benchmark"
DESCRIPTION = "High-throughput synthetic vector field generator for stress-testing polar and circular plots."
