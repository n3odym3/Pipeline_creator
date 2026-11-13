"""
Sample Benchmark Window Module (High-Throughput Synthetic Sample Generator).

Designed specifically for stress-testing sample streaming, container ingestion,
reordering, line plot rendering, memory scaling, and backpressure handling.
Emits IOTypes.SAMPLE dictionaries with configurable:
  - Emission modes: Controlled rate (1..1000 Hz) or Uncapped maximum CPU throughput
  - ID strategies:
      * Auto-Increment (unique IDs to test container growth and legend scaling)
      * Static / Overwrite (constant ID to test in-place high-frequency curve redrawing)
      * Round-Robin (cycling across N IDs to test multi-curve concurrent updates)
  - Waveforms: Sine Wave, Chirp Sweep, Square Wave, Sawtooth, White Noise, Random Walk, Multi-Sinusoid
  - Lengths: from 16 to 100,000+ data points
  - Pre-rendered ring-buffered generation for zero per-packet allocation overhead
"""

from __future__ import annotations

import collections
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class SampleBenchmark_win(WindowBase):
    """
    High-Performance Synthetic Sample Generator & Stress-Tester.

    Blasts structured numerical samples (x, y coordinates) to Sample Container
    or Line Plot windows with zero per-sample allocation overhead.
    """

    label: str = "Sample Benchmark"
    win_width: int = display_scaling.scale(380)
    win_height: int = display_scaling.scale(520)
    description: str = "High-throughput synthetic sample generator for stress-testing plotters and containers."

    accepted_input_types: list[type] = [
        IOTypes.TRIGGER,
        IOTypes.CMD_DICT,
        IOTypes.NUMBER,
        IOTypes.ANY,
    ]
    outputs: dict[str, type] = {
        "Data": IOTypes.SAMPLE,
        "Trigger": IOTypes.TRIGGER,
    }
    persistent_fields: list[str] = [
        "label",
        "id_mode",
        "base_name",
        "id_pool_size",
        "signal_pattern",
        "length_preset",
        "custom_length",
        "noise_level",
        "frequency",
        "rate_target",
        "uncapped_speed",
        "burst_count",
        "respect_backpressure",
        "universal_any_output",
        "auto_start",
    ]

    _ID_MODES: list[str] = [
        "Auto-Increment",
        "Static / Overwrite",
        "Round-Robin (N IDs)",
    ]

    _LENGTH_PRESETS: dict[str, int] = {
        "64 pts (Ultra-Light)": 64,
        "256 pts (Light)": 256,
        "1,024 pts (Standard)": 1024,
        "4,096 pts (Dense)": 4096,
        "16,384 pts (Heavy)": 16384,
        "65,536 pts (Stress)": 65536,
        "Custom": 1024,
    }

    _PATTERNS: list[str] = [
        "Sine Wave",
        "Chirp Sweep",
        "Square Wave",
        "Sawtooth",
        "White Noise",
        "Random Walk",
        "Multi-Sinusoid",
    ]

    # Persistent defaults
    id_mode: str = "Auto-Increment"
    base_name: str = "Sample"
    id_pool_size: int = 5
    signal_pattern: str = "Sine Wave"
    length_preset: str = "1,024 pts (Standard)"
    custom_length: int = 1024
    noise_level: float = 0.05
    frequency: float = 5.0
    rate_target: float = 60.0
    uncapped_speed: bool = False
    burst_count: int = 100
    respect_backpressure: bool = False
    universal_any_output: bool = False
    auto_start: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.id_mode = str(getattr(self, "id_mode", "Auto-Increment") or "Auto-Increment")
        if self.id_mode not in self._ID_MODES:
            self.id_mode = "Auto-Increment"

        self.base_name = str(getattr(self, "base_name", "Sample") or "Sample").strip()
        self.id_pool_size = max(2, int(getattr(self, "id_pool_size", 5) or 5))
        self.signal_pattern = str(getattr(self, "signal_pattern", "Sine Wave") or "Sine Wave")
        if self.signal_pattern not in self._PATTERNS:
            self.signal_pattern = "Sine Wave"

        self.length_preset = str(getattr(self, "length_preset", "1,024 pts (Standard)") or "1,024 pts (Standard)")
        if self.length_preset not in self._LENGTH_PRESETS:
            self.length_preset = "1,024 pts (Standard)"

        self.custom_length = max(16, int(getattr(self, "custom_length", 1024) or 1024))
        self.noise_level = max(0.0, min(1.0, float(getattr(self, "noise_level", 0.05) or 0.05)))
        self.frequency = max(0.1, float(getattr(self, "frequency", 5.0) or 5.0))
        self.rate_target = max(1.0, float(getattr(self, "rate_target", 60.0) or 60.0))
        self.uncapped_speed = bool(getattr(self, "uncapped_speed", False))
        self.burst_count = max(1, int(getattr(self, "burst_count", 100) or 100))
        self.respect_backpressure = bool(getattr(self, "respect_backpressure", False))
        self.universal_any_output = bool(getattr(self, "universal_any_output", False))
        self.auto_start = bool(getattr(self, "auto_start", False))

        # Runtime tags
        self._tag_btn_toggle = f"sb_btn_toggle_{self.UUID}"
        self._tag_btn_step = f"sb_btn_step_{self.UUID}"
        self._tag_btn_burst = f"sb_btn_burst_{self.UUID}"
        self._tag_burst_count = f"sb_burst_cnt_{self.UUID}"
        self._tag_rate_slider = f"sb_rate_{self.UUID}"
        self._tag_uncapped = f"sb_uncap_{self.UUID}"
        self._tag_id_mode = f"sb_id_mode_{self.UUID}"
        self._tag_base_name = f"sb_base_name_{self.UUID}"
        self._tag_pool_size = f"sb_pool_size_{self.UUID}"
        self._tag_grp_pool = f"sb_grp_pool_{self.UUID}"
        self._tag_pattern = f"sb_pattern_{self.UUID}"
        self._tag_preset = f"sb_preset_{self.UUID}"
        self._tag_custom_len = f"sb_cust_len_{self.UUID}"
        self._tag_grp_cust_len = f"sb_grp_cust_len_{self.UUID}"
        self._tag_noise = f"sb_noise_{self.UUID}"
        self._tag_backpressure = f"sb_backpress_{self.UUID}"
        self._tag_universal = f"sb_universal_{self.UUID}"
        self._tag_txt_status = f"sb_txt_status_{self.UUID}"
        self._tag_txt_rate = f"sb_txt_rate_{self.UUID}"
        self._tag_txt_bw = f"sb_txt_bw_{self.UUID}"
        self._tag_txt_active_id = f"sb_txt_aid_{self.UUID}"

        # State & Threading
        self._running: bool = False
        self._is_bursting: bool = False
        self._is_closing: bool = False
        self._thread: Optional[threading.Thread] = None
        self._burst_thread: Optional[threading.Thread] = None
        self._burst_stop_event = threading.Event()
        self._lock = threading.Lock()

        # Telemetry counters
        self.total_emitted: int = 0
        self.dropped_count: int = 0
        self.measured_rate_hz: float = 0.0
        self.measured_kpts_sec: float = 0.0
        self._last_emitted_name: str = "None"

        # Rate calculations (sliding window)
        self._rate_timestamps: collections.deque[float] = collections.deque(maxlen=240)
        self._rate_lock = threading.Lock()
        self._last_ui_update_ts: float = 0.0

        # Ring buffer for pre-rendered waveforms (32 phase slices for zero runtime CPU strain)
        self._ring_size: int = 32
        self._ring_index: int = 0
        self._sample_ring: list[tuple[np.ndarray, np.ndarray]] = []  # list of (x, y)
        self._active_length: int = 1024
        self._rebuild_ring_buffer()

        # Dynamic output pin sync
        self._update_output_iotype()

        # Build UI
        self.create()

        if self.auto_start:
            self.start_stream()

    # --------------------------------------------------------------------------
    # Ring Buffer Precomputation (Zero Allocation during Streaming)
    # --------------------------------------------------------------------------
    def _effective_length(self) -> int:
        """Resolves active sample point count from preset or custom input."""
        if self.length_preset == "Custom":
            return max(16, int(self.custom_length))
        return self._LENGTH_PRESETS.get(self.length_preset, 1024)

    def _rebuild_ring_buffer(self) -> None:
        """Precomputes waveforms in memory so emission runs at maximum memory-bandwidth speed."""
        with self._lock:
            n_pts = self._effective_length()
            self._active_length = n_pts
            pattern = self.signal_pattern
            noise_amp = self.noise_level
            freq = self.frequency

            x_base = np.linspace(0.0, 1.0, n_pts, endpoint=False, dtype=np.float64)
            new_ring: list[tuple[np.ndarray, np.ndarray]] = []

            for i in range(self._ring_size):
                phase = (2.0 * math.pi * i) / self._ring_size
                t = x_base * (2.0 * math.pi * freq) + phase

                if pattern == "Sine Wave":
                    y = np.sin(t)
                elif pattern == "Chirp Sweep":
                    chirp_t = x_base ** 2 * (2.0 * math.pi * freq * 4.0) + phase
                    y = np.sin(chirp_t)
                elif pattern == "Square Wave":
                    y = np.sign(np.sin(t))
                elif pattern == "Sawtooth":
                    y = 2.0 * (t / (2.0 * math.pi) - np.floor(0.5 + t / (2.0 * math.pi)))
                elif pattern == "White Noise":
                    y = np.random.normal(0.0, 1.0, n_pts)
                elif pattern == "Random Walk":
                    steps = np.random.normal(0.0, 1.0, n_pts)
                    y = np.cumsum(steps) / math.sqrt(n_pts)
                elif pattern == "Multi-Sinusoid":
                    y = np.sin(t) + 0.5 * np.sin(2.5 * t) + 0.25 * np.sin(5.0 * t)
                else:
                    y = np.sin(t)

                if noise_amp > 0.0 and pattern not in ("White Noise", "Random Walk"):
                    noise = np.random.normal(0.0, noise_amp, n_pts)
                    y = y + noise

                new_ring.append((x_base.copy(), y.astype(np.float64)))

            self._sample_ring = new_ring
            self._ring_index = 0

    def _update_output_iotype(self) -> None:
        """Sets node editor pin type according to universal output toggle."""
        self.outputs["Data"] = IOTypes.ANY if self.universal_any_output else IOTypes.SAMPLE

    # --------------------------------------------------------------------------
    # Streaming & Low-Jitter Emission Loop
    # --------------------------------------------------------------------------
    def start_stream(self) -> None:
        """Start background emission thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name=f"SampleBenchmark_{self.UUID}",
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
        """Blast exactly `count` samples as fast as possible in a dedicated background thread."""
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
            name=f"SampleBenchmark_burst_{self.UUID}",
        )
        self._burst_thread.start()
        if dpg.does_item_exist(self._tag_btn_burst):
            dpg.configure_item(self._tag_btn_burst, label="Stop Burst")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, f"Stream Status: Bursting ({n:,} pkts)...")

    def stop_burst(self) -> None:
        """Cancel active burst emission."""
        self._burst_stop_event.set()
        self._is_bursting = False
        if dpg.does_item_exist(self._tag_btn_burst):
            dpg.configure_item(self._tag_btn_burst, label="Send Burst")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, "Stream Status: Stopped")

    def _burst_loop(self, total_n: int) -> None:
        """Blasts total_n samples at maximum speed."""
        t0 = time.perf_counter()
        sent = 0

        while sent < total_n and not self._burst_stop_event.is_set() and not self._is_closing:
            if self.respect_backpressure and not self.is_outputs_ready():
                self.dropped_count += 1
                time.sleep(0.0005)
                continue

            self._emit_single_sample()
            sent += 1
            if (sent & 63) == 0:
                time.sleep(0)  # Yield GIL periodically so GUI and IPC remain smooth

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
        self._update_telemetry_ui()

    def _stream_loop(self) -> None:
        """Continuous emission loop supporting precise pacing or full uncapped speed."""
        next_deadline = time.perf_counter()

        while self._running and not self._is_closing:
            # 1. Backpressure check
            if self.respect_backpressure and not self.is_outputs_ready():
                self.dropped_count += 1
                time.sleep(0.0005)
                next_deadline = time.perf_counter()
                continue

            # 2. Timing pacing
            if not self.uncapped_speed:
                now = time.perf_counter()
                time_left = next_deadline - now
                if time_left > 0.002:
                    time.sleep(time_left - 0.001)
                while time.perf_counter() < next_deadline:
                    time.sleep(0)

            # 3. Emit sample
            self._emit_single_sample()

            # 4. Schedule next deadline
            if not self.uncapped_speed:
                interval = 1.0 / max(1.0, self.rate_target)
                next_deadline += interval
                if next_deadline < time.perf_counter() - interval:
                    next_deadline = time.perf_counter()

    def _emit_single_sample(self) -> None:
        """Constructs sample dictionary and dispatches to connected downstream modules."""
        now = time.perf_counter()

        with self._lock:
            if not self._sample_ring:
                return
            x_arr, y_arr = self._sample_ring[self._ring_index]
            self._ring_index = (self._ring_index + 1) % len(self._sample_ring)
            seq = self.total_emitted + 1
            mode = self.id_mode
            base = self.base_name
            pool_sz = self.id_pool_size

        # Resolve sample name & UUID according to ID strategy
        if mode == "Auto-Increment":
            sample_name = f"{base}_{seq}"
            sample_uuid = f"uuid_{self.UUID[:6]}_{seq}"
        elif mode == "Static / Overwrite":
            sample_name = f"{base}_0"
            sample_uuid = f"uuid_{self.UUID[:6]}_static"
        elif mode == "Round-Robin (N IDs)":
            idx = (seq - 1) % pool_sz
            sample_name = f"{base}_{idx}"
            sample_uuid = f"uuid_{self.UUID[:6]}_{idx}"
        else:
            sample_name = f"{base}_{seq}"
            sample_uuid = f"uuid_{self.UUID[:6]}_{seq}"

        payload: dict[str, Any] = {
            "name": sample_name,
            "uuid": sample_uuid,
            "x": x_arr,
            "y": y_arr,
            "action": "select",
        }

        # Track emission telemetry
        with self._rate_lock:
            self.total_emitted = seq
            self._last_emitted_name = sample_name
            self._rate_timestamps.append(now)

            if len(self._rate_timestamps) >= 2:
                dur = self._rate_timestamps[-1] - self._rate_timestamps[0]
                if dur > 0.0:
                    self.measured_rate_hz = float((len(self._rate_timestamps) - 1) / dur)
                    self.measured_kpts_sec = (self.measured_rate_hz * len(y_arr)) / 1000.0

        # Dispatch to Data output connections
        out_iotype = IOTypes.ANY if self.universal_any_output else IOTypes.SAMPLE
        targets = self.connections.get("Data", [])
        if targets:
            for tgt in list(targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        # Provide both kwargs for universal compatibility (SampleContainer + LinePlot)
                        cb(sample=payload, data=payload, data_type=out_iotype)
                    except Exception as e:
                        logger.debug(f"SampleBenchmark dispatch error to {getattr(tgt, 'winID', tgt)}: {e}")

        # Dispatch Trigger if wired
        trig_targets = self.connections.get("Trigger", [])
        if trig_targets:
            for tgt in list(trig_targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=sample_name, data_type=IOTypes.TRIGGER)
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
            tot = self.total_emitted
            hz = self.measured_rate_hz
            kpts = self.measured_kpts_sec
            last_name = self._last_emitted_name

        bw_mbs = (kpts * 1000.0 * 8.0) / (1024.0 * 1024.0)  # float64 = 8 bytes per pt

        dpg.set_value(self._tag_txt_rate, f"Emitted: {tot:,} pkts  |  Measured: {hz:.1f} Hz")
        dpg.set_value(self._tag_txt_bw, f"Throughput: {kpts:,.1f} kpts/s  |  {bw_mbs:.2f} MB/s")
        dpg.set_value(self._tag_txt_active_id, f"Last Sample: {last_name} ({self._active_length:,} pts)")

    # --------------------------------------------------------------------------
    # DearPyGui UI Construction
    # --------------------------------------------------------------------------
    def create(self) -> None:
        with self.window():
            # Section: Stream Transport Controls
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Start Stream",
                    tag=self._tag_btn_toggle,
                    width=display_scaling.scale(110),
                    callback=self._on_toggle_clicked,
                )
                dpg.add_button(
                    label="Step 1",
                    tag=self._tag_btn_step,
                    width=display_scaling.scale(65),
                    callback=self._on_step_clicked,
                )
                dpg.add_button(
                    label="Reset",
                    width=display_scaling.scale(55),
                    callback=self._on_reset_clicked,
                )

            # Burst / Send N Samples As Fast As Possible
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Send Burst",
                    tag=self._tag_btn_burst,
                    width=display_scaling.scale(150),
                    callback=self._on_send_burst_clicked,
                )
                dpg.add_text("Count:")
                dpg.add_input_int(
                    tag=self._tag_burst_count,
                    default_value=self.burst_count,
                    min_value=1,
                    max_value=1000000,
                    width=display_scaling.scale(200),
                    step=-1,
                    callback=self._on_burst_count_changed,
                )

            # Emission Pacing Controls
            dpg.add_slider_float(
                label="Target Rate",
                tag=self._tag_rate_slider,
                default_value=self.rate_target,
                min_value=1.0,
                max_value=1000.0,
                clamped=True,
                format="%.1f Hz",
                width=display_scaling.scale(180),
                callback=self._on_rate_changed,
            )
            dpg.add_checkbox(
                label="Uncapped (Max Throughput)",
                tag=self._tag_uncapped,
                default_value=self.uncapped_speed,
                callback=self._on_uncapped_changed,
            )

            dpg.add_separator()

            # Section: Sample Identifier Strategy
            with dpg.group(horizontal=True):
                dpg.add_text("ID Mode:")
                dpg.add_combo(
                    items=self._ID_MODES,
                    default_value=self.id_mode,
                    tag=self._tag_id_mode,
                    width=display_scaling.scale(210),
                    callback=self._on_id_mode_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Base Name:")
                dpg.add_input_text(
                    default_value=self.base_name,
                    tag=self._tag_base_name,
                    width=display_scaling.scale(120),
                    callback=self._on_base_name_changed,
                )

            is_pool = self.id_mode == "Round-Robin (N IDs)"
            with dpg.group(tag=self._tag_grp_pool, horizontal=True, show=is_pool):
                dpg.add_text("Pool Size:")
                dpg.add_slider_int(
                    default_value=self.id_pool_size,
                    tag=self._tag_pool_size,
                    min_value=2,
                    max_value=50,
                    clamped=True,
                    width=display_scaling.scale(130),
                    callback=self._on_pool_size_changed,
                )

            dpg.add_separator()

            # Section: Waveform & Payload Specification
            with dpg.group(horizontal=True):
                dpg.add_text("Pattern:")
                dpg.add_combo(
                    items=self._PATTERNS,
                    default_value=self.signal_pattern,
                    tag=self._tag_pattern,
                    width=display_scaling.scale(210),
                    callback=self._on_pattern_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Points:")
                dpg.add_combo(
                    items=list(self._LENGTH_PRESETS.keys()),
                    default_value=self.length_preset,
                    tag=self._tag_preset,
                    width=display_scaling.scale(210),
                    callback=self._on_preset_changed,
                )

            is_custom = self.length_preset == "Custom"
            with dpg.group(tag=self._tag_grp_cust_len, horizontal=True, show=is_custom):
                dpg.add_text("Length:")
                dpg.add_input_int(
                    default_value=self.custom_length,
                    tag=self._tag_custom_len,
                    min_value=16,
                    max_value=1000000,
                    width=display_scaling.scale(130),
                    callback=self._on_custom_len_changed,
                )

            dpg.add_slider_float(
                label="Noise Level",
                default_value=self.noise_level,
                tag=self._tag_noise,
                min_value=0.0,
                max_value=1.0,
                clamped=True,
                format="%.2f",
                width=display_scaling.scale(180),
                callback=self._on_noise_changed,
            )

            dpg.add_separator()

            # Section: Flow Control & Options
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

            # Section: Live Telemetry Display
            dpg.add_text("Live Diagnostics")
            dpg.add_text("Stream Status: Stopped", tag=self._tag_txt_status)
            dpg.add_text("Emitted: 0 pkts  |  Measured: 0.0 Hz", tag=self._tag_txt_rate)
            dpg.add_text("Throughput: 0.0 kpts/s  |  0.00 MB/s", tag=self._tag_txt_bw)
            dpg.add_text(f"Last Sample: None ({self._active_length:,} pts)", tag=self._tag_txt_active_id)

    # --------------------------------------------------------------------------
    # UI Event Callbacks
    # --------------------------------------------------------------------------
    def _on_toggle_clicked(self, *args: Any) -> None:
        if self._running:
            self.stop_stream()
        else:
            self.start_stream()

    def _on_step_clicked(self, *args: Any) -> None:
        self._emit_single_sample()

    def _on_send_burst_clicked(self, *args: Any) -> None:
        if self._is_bursting:
            self.stop_burst()
        else:
            self.start_burst()

    def _on_burst_count_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.burst_count = max(1, int(app_data))

    def _on_reset_clicked(self, *args: Any) -> None:
        with self._rate_lock:
            self.total_emitted = 0
            self.dropped_count = 0
            self._rate_timestamps.clear()
            self.measured_rate_hz = 0.0
            self.measured_kpts_sec = 0.0
            self._last_emitted_name = "None"
        self._update_telemetry_ui()

    def _on_rate_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.rate_target = max(1.0, float(app_data))

    def _on_uncapped_changed(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.uncapped_speed = bool(app_data)

    def _on_id_mode_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.id_mode = str(app_data)
        if dpg.does_item_exist(self._tag_grp_pool):
            dpg.configure_item(self._tag_grp_pool, show=(self.id_mode == "Round-Robin (N IDs)"))

    def _on_base_name_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.base_name = str(app_data).strip() or "Sample"

    def _on_pool_size_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.id_pool_size = max(2, int(app_data))

    def _on_pattern_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.signal_pattern = str(app_data)
        self._rebuild_ring_buffer()
        self._update_telemetry_ui()

    def _on_preset_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.length_preset = str(app_data)
        is_custom = self.length_preset == "Custom"
        if dpg.does_item_exist(self._tag_grp_cust_len):
            dpg.configure_item(self._tag_grp_cust_len, show=is_custom)
        self._rebuild_ring_buffer()
        self._update_telemetry_ui()

    def _on_custom_len_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.custom_length = max(16, int(app_data))
        self._rebuild_ring_buffer()
        self._update_telemetry_ui()

    def _on_noise_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.noise_level = max(0.0, min(1.0, float(app_data)))
        self._rebuild_ring_buffer()

    def _on_backpressure_changed(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.respect_backpressure = bool(app_data)

    def _on_universal_toggled(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.universal_any_output = bool(app_data)
        self._update_output_iotype()

    # --------------------------------------------------------------------------
    # Node Editor Remote Control Callback
    # --------------------------------------------------------------------------
    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """Remote control via Trigger, Cmd Dict, or Number."""
        data = kwargs.get("data") if "data" in kwargs else (args[0] if args else None)
        if isinstance(data, dict):
            if "burst" in data:
                self.start_burst(int(data["burst"]))
            elif "count" in data:
                self.start_burst(int(data["count"]))
            elif "start" in data:
                if data["start"]:
                    self.start_stream()
                else:
                    self.stop_stream()
            if "stop" in data and data["stop"]:
                self.stop_stream()
                self.stop_burst()
            if "rate" in data:
                self.rate_target = max(1.0, float(data["rate"]))
                if dpg.does_item_exist(self._tag_rate_slider):
                    dpg.set_value(self._tag_rate_slider, self.rate_target)
        elif isinstance(data, (int, float)):
            self.rate_target = max(1.0, float(data))
            if dpg.does_item_exist(self._tag_rate_slider):
                dpg.set_value(self._tag_rate_slider, self.rate_target)
        else:
            # Trigger pulse -> emit single sample
            self._emit_single_sample()

    # --------------------------------------------------------------------------
    # Lifecycle Cleanup
    # --------------------------------------------------------------------------
    def on_close(self) -> None:
        self._is_closing = True
        self.stop_stream()
        self.stop_burst()
        if self._thread is not None and self._thread.is_alive() and threading.current_thread() != self._thread:
            self._thread.join(timeout=0.2)
        if self._burst_thread is not None and self._burst_thread.is_alive() and threading.current_thread() != self._burst_thread:
            self._burst_thread.join(timeout=0.2)
        super().on_close()

    def close(self) -> None:
        super().close()

    def destroy(self) -> None:
        self.close()
        super().destroy()


EXPORTED_CLASS = SampleBenchmark_win
EXPORTED_NAME = "Sample Benchmark"
