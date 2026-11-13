"""
Command Benchmark Window Module (High-Throughput Synthetic CMD_DICT Generator).

Designed specifically for stress-testing command dispatch, pipeline RPC, hardware controllers,
parameter sweeps, and backpressure handling across downstream consumer modules:
  - modules/benchmark/hardware_simulator_win.py (Hardware Delay & Response Simulator)
  - modules/ruideng_PSU_control/ruideng_win.py (PSU Control)
  - modules/rigol_dho914/dho914_win.py (Oscilloscope Control)
  - modules/video_reader/video_reader_win.py (Video Playback & Control)
  - modules/plot/sample_container_win.py (Sample Container Actions)

Emits IOTypes.CMD_DICT and IOTypes.TEXT payloads with configurable:
  - Command profiles & parameter sweeps:
      * Hardware Motion (Axis Move / Position Sweep)
      * Power Supply (Voltage / Current Step Ramp)
      * Camera & Trigger (Exposure / Gain Sweep)
      * Ping & Latency Probe (High-precision roundtrip timestamps)
      * Pipeline State Machine (Start / Stop / Pause / Reset)
      * Custom JSON / Template Dict
  - Emission modes:
      * Controlled rate (1..1000 Hz)
      * Uncapped maximum CPU speed
      * Burst mode (blasts exact N commands)
      * Single step (manual trigger)
"""

from __future__ import annotations

import collections
import json
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class CmdBenchmark_win(WindowBase):
    """
    High-Throughput Synthetic Command Generator & RPC Stress-Tester.

    Dispatches structured command dictionaries (CMD_DICT) with dynamic
    parameter sweeps, burst blasters, and latency probes.
    """

    label: str = "Command Benchmark"
    win_width: int = display_scaling.scale(390)
    win_height: int = display_scaling.scale(560)
    description: str = "High-throughput synthetic command dictionary generator for pipeline and hardware testing."

    accepted_input_types: list[type] = [
        IOTypes.TRIGGER,
        IOTypes.CMD_DICT,
        IOTypes.NUMBER,
        IOTypes.ANY,
    ]
    outputs: dict[str, type] = {
        "CMD": IOTypes.CMD_DICT,
        "Text": IOTypes.TEXT,
        "Trigger": IOTypes.TRIGGER,
    }
    persistent_fields: list[str] = [
        "label",
        "cmd_profile",
        "custom_template_str",
        "sweep_min",
        "sweep_max",
        "sweep_steps",
        "rate_target",
        "uncapped_speed",
        "burst_count",
        "respect_backpressure",
        "universal_any_output",
        "auto_start",
    ]

    _PROFILES: list[str] = [
        "Hardware Motion (Move / Pos)",
        "Power Supply (Volt / Curr)",
        "Camera Trigger (Exposure)",
        "Ping / Latency Probe",
        "Pipeline State (Control)",
        "Custom Template (JSON)",
    ]

    # Persistent configuration defaults
    cmd_profile: str = "Hardware Motion (Move / Pos)"
    custom_template_str: str = '{"action": "custom_cmd", "channel": 1, "val": {val}, "seq": {seq}}'
    sweep_min: float = 0.0
    sweep_max: float = 100.0
    sweep_steps: int = 50
    rate_target: float = 20.0
    uncapped_speed: bool = False
    burst_count: int = 200
    respect_backpressure: bool = True
    universal_any_output: bool = False
    auto_start: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Threading & Control
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
        self.dropped_count: int = 0
        self.measured_rate_hz: float = 0.0
        self.last_prep_ms: float = 0.0
        self.last_payload_preview: str = "{}"
        self._rate_timestamps: collections.deque[float] = collections.deque(maxlen=60)
        self._rate_lock = threading.Lock()
        self._last_ui_update_ts: float = 0.0
        self._sweep_index: int = 0

        # UI Element Tags
        self._tag_btn_toggle = f"cb_btn_toggle_{self.UUID}"
        self._tag_btn_burst = f"cb_btn_burst_{self.UUID}"
        self._tag_burst_count = f"cb_burst_count_{self.UUID}"
        self._tag_rate_slider = f"cb_rate_slider_{self.UUID}"
        self._tag_uncapped = f"cb_uncapped_{self.UUID}"
        self._tag_profile = f"cb_profile_{self.UUID}"
        self._tag_grp_sweep = f"cb_grp_sweep_{self.UUID}"
        self._tag_sweep_min = f"cb_sweep_min_{self.UUID}"
        self._tag_sweep_max = f"cb_sweep_max_{self.UUID}"
        self._tag_sweep_steps = f"cb_sweep_steps_{self.UUID}"
        self._tag_grp_custom = f"cb_grp_custom_{self.UUID}"
        self._tag_custom_json = f"cb_custom_json_{self.UUID}"
        self._tag_backpressure = f"cb_backpressure_{self.UUID}"
        self._tag_universal = f"cb_universal_{self.UUID}"
        self._tag_txt_status = f"cb_txt_status_{self.UUID}"
        self._tag_txt_rate = f"cb_txt_rate_{self.UUID}"
        self._tag_txt_preview = f"cb_txt_preview_{self.UUID}"
        self._tag_txt_perf = f"cb_txt_perf_{self.UUID}"

        self._update_output_iotype()
        self.create()

        if self.auto_start:
            self.start_stream()

    def _update_output_iotype(self) -> None:
        """Sets node editor pin type according to universal output toggle."""
        self.outputs["CMD"] = IOTypes.ANY if self.universal_any_output else IOTypes.CMD_DICT

    # --------------------------------------------------------------------------
    # Payload Construction
    # --------------------------------------------------------------------------
    def _build_next_payload(self) -> Dict[str, Any]:
        """Construct the next command dictionary based on selected profile and sweep phase."""
        self.seq_counter += 1
        seq = self.seq_counter
        now = time.perf_counter()

        profile = self.cmd_profile
        s_min = float(self.sweep_min)
        s_max = float(self.sweep_max)
        s_steps = max(2, int(self.sweep_steps))

        # Triangle / Sawtooth sweep parameter value
        phase = (self._sweep_index % s_steps) / float(s_steps - 1)
        self._sweep_index += 1
        # Triangular wave between min and max
        val = s_min + (s_max - s_min) * (2.0 * phase if phase <= 0.5 else 2.0 * (1.0 - phase))

        if profile == "Hardware Motion (Move / Pos)":
            axis = "X" if (seq % 2 == 1) else "Y"
            payload = {
                "action": "move_rel",
                "axis": axis,
                "position": round(val, 2),
                "speed": 15.0,
                "accel": 50.0,
                "seq": seq,
                "timestamp": now,
                "source": self.label,
            }

        elif profile == "Power Supply (Volt / Curr)":
            v_val = round(max(0.0, val), 2)
            i_limit = round(1.0 + (seq % 5) * 0.5, 2)
            payload = {
                "action": "set_output",
                "voltage": v_val,
                "volt": v_val,
                "current": i_limit,
                "amp": i_limit,
                "state": "ON" if (seq % 20 != 0) else "OFF",
                "seq": seq,
                "timestamp": now,
                "source": self.label,
            }

        elif profile == "Camera Trigger (Exposure)":
            exp_ms = round(1.0 + val * 0.5, 1)
            payload = {
                "action": "trigger",
                "exposure_ms": exp_ms,
                "gain": 1.0 + ((seq % 4) * 0.5),
                "frame_idx": seq,
                "seq": seq,
                "timestamp": now,
                "source": self.label,
            }

        elif profile == "Ping / Latency Probe":
            payload = {
                "action": "ping",
                "client_ts": now,
                "seq": seq,
                "payload_bytes": 64,
                "source": self.label,
            }

        elif profile == "Pipeline State (Control)":
            actions = ["start", "pause", "resume", "step", "status"]
            chosen = actions[(seq // 10) % len(actions)]
            payload = {
                "action": chosen,
                "target": "pipeline",
                "seq": seq,
                "timestamp": now,
                "source": self.label,
            }

        elif profile == "Custom Template (JSON)":
            raw_tpl = str(self.custom_template_str or "{}")
            rendered = (
                raw_tpl.replace("{seq}", str(seq))
                .replace("{val}", str(round(val, 3)))
                .replace("{t}", f"{now:.4f}")
            )
            try:
                payload = json.loads(rendered)
                if not isinstance(payload, dict):
                    payload = {"action": "custom", "data": payload, "seq": seq}
            except Exception:
                payload = {"action": "parse_error", "raw": rendered, "seq": seq}
        else:
            payload = {"action": "default", "val": val, "seq": seq, "timestamp": now}

        return payload

    # --------------------------------------------------------------------------
    # Streaming & Burst Execution
    # --------------------------------------------------------------------------
    def start_stream(self) -> None:
        """Start background command streaming thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name=f"CmdBenchmark_{self.UUID}",
        )
        self._thread.start()
        if dpg.does_item_exist(self._tag_btn_toggle):
            dpg.configure_item(self._tag_btn_toggle, label="Stop Stream")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, "Stream Status: Running")

    def stop_stream(self) -> None:
        """Stop background command streaming thread."""
        self._running = False
        if dpg.does_item_exist(self._tag_btn_toggle):
            dpg.configure_item(self._tag_btn_toggle, label="Start Stream")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, "Stream Status: Stopped")

    def start_burst(self, count: Optional[int] = None) -> None:
        """Blast exactly `count` command dictionaries as fast as possible."""
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
            name=f"CmdBenchmark_burst_{self.UUID}",
        )
        self._burst_thread.start()
        if dpg.does_item_exist(self._tag_btn_burst):
            dpg.configure_item(self._tag_btn_burst, label="Stop Burst")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, f"Stream Status: Bursting ({n:,} cmds)...")

    def stop_burst(self) -> None:
        """Cancel active burst execution."""
        self._burst_stop_event.set()
        self._is_bursting = False
        if dpg.does_item_exist(self._tag_btn_burst):
            dpg.configure_item(self._tag_btn_burst, label="Send Burst")
        if dpg.does_item_exist(self._tag_txt_status):
            dpg.set_value(self._tag_txt_status, "Stream Status: Stopped")

    def _burst_loop(self, total_n: int) -> None:
        """Blasts total_n commands at maximum speed."""
        t0 = time.perf_counter()
        sent = 0

        while sent < total_n and not self._burst_stop_event.is_set() and not self._is_closing:
            if self.respect_backpressure and not self.is_outputs_ready():
                self.dropped_count += 1
                time.sleep(0.0005)
                continue

            self._emit_single_command()
            sent += 1
            if (sent & 63) == 0:
                time.sleep(0)  # Yield GIL periodically

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

    def _stream_loop(self) -> None:
        """Precise low-jitter streaming loop supporting controlled pacing or uncapped speed."""
        next_t = time.perf_counter()

        while self._running and not self._is_closing:
            now = time.perf_counter()

            if self.respect_backpressure and not self.is_outputs_ready():
                self.dropped_count += 1
                time.sleep(0.001)
                next_t = time.perf_counter()
                continue

            self._emit_single_command()

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

    def _emit_single_command(self) -> None:
        """Constructs command dictionary and dispatches to connected downstream modules."""
        t_prep_0 = time.perf_counter()
        payload = self._build_next_payload()
        t_prep_1 = time.perf_counter()
        self.last_prep_ms = max(0.0001, (t_prep_1 - t_prep_0) * 1000.0)

        now = time.perf_counter()
        seq = payload.get("seq", self.seq_counter)

        # String preview
        preview_str = json.dumps(payload, separators=(", ", ": "))
        if len(preview_str) > 60:
            preview_str = preview_str[:57] + "..."
        self.last_payload_preview = preview_str

        # Track telemetry
        with self._rate_lock:
            self.total_emitted = seq
            self._rate_timestamps.append(now)

            if len(self._rate_timestamps) >= 2:
                dur = self._rate_timestamps[-1] - self._rate_timestamps[0]
                if dur > 0.0:
                    self.measured_rate_hz = float((len(self._rate_timestamps) - 1) / dur)

        # Dispatch to CMD output connections
        out_iotype = IOTypes.ANY if self.universal_any_output else IOTypes.CMD_DICT
        targets_cmd = self.connections.get("CMD", [])
        if targets_cmd:
            for tgt in list(targets_cmd):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        # Provide cmd, data, and data_type for universal compatibility
                        cb(cmd=payload, data=payload, data_type=out_iotype)
                    except Exception as e:
                        logger.debug(f"CmdBenchmark dispatch error to {getattr(tgt, 'winID', tgt)}: {e}")

        # Dispatch to Text output connections
        targets_txt = self.connections.get("Text", [])
        if targets_txt:
            json_str = json.dumps(payload)
            for tgt in list(targets_txt):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=json_str, data_type=IOTypes.TEXT)
                    except Exception:
                        pass

        # Dispatch Trigger if wired
        trig_targets = self.connections.get("Trigger", [])
        if trig_targets:
            for tgt in list(trig_targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=f"CMD_{seq}", data_type=IOTypes.TRIGGER)
                    except Exception:
                        pass

        # Throttled UI Telemetry Update (~6 Hz)
        if (now - self._last_ui_update_ts) >= 0.16:
            self._last_ui_update_ts = now
            self._update_telemetry_ui()

    def _update_telemetry_ui(self) -> None:
        """Update live telemetry labels in UI without hardcoded colors."""
        if not dpg.does_item_exist(self._tag_txt_rate):
            return

        with self._rate_lock:
            tot = self.total_emitted
            hz = self.measured_rate_hz
            prep_ms = self.last_prep_ms
            prev_str = self.last_payload_preview

        dpg.set_value(self._tag_txt_rate, f"Emitted: {tot:,} cmds  |  Measured: {hz:.1f} Hz")
        dpg.set_value(self._tag_txt_preview, f"Last: {prev_str}")
        dpg.set_value(self._tag_txt_perf, f"Prep Latency: {prep_ms:.3f} ms")

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
                    width=scale(150),
                    callback=self._on_send_burst_clicked,
                )
                dpg.add_text("Count:")
                dpg.add_input_int(
                    tag=self._tag_burst_count,
                    default_value=self.burst_count,
                    min_value=1,
                    max_value=1000000,
                    width=scale(200),
                    step=-1,
                    callback=self._on_burst_count_changed,
                )

            # Section: Pacing Controls
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

            # Section: Command Profile Selection
            with dpg.group(horizontal=True):
                dpg.add_text("Profile:")
                dpg.add_combo(
                    items=self._PROFILES,
                    default_value=self.cmd_profile,
                    tag=self._tag_profile,
                    width=scale(240),
                    callback=self._on_profile_changed,
                )

            # Sub-Section: Parameter Sweep Range
            is_custom = self.cmd_profile == "Custom Template (JSON)"
            with dpg.group(tag=self._tag_grp_sweep, show=not is_custom):
                with dpg.group(horizontal=True):
                    dpg.add_text("Sweep Min:")
                    dpg.add_input_float(
                        default_value=self.sweep_min,
                        tag=self._tag_sweep_min,
                        width=scale(90),
                        callback=self._on_sweep_min_changed,
                    )
                    dpg.add_text("Max:")
                    dpg.add_input_float(
                        default_value=self.sweep_max,
                        tag=self._tag_sweep_max,
                        width=scale(90),
                        callback=self._on_sweep_max_changed,
                    )
                with dpg.group(horizontal=True):
                    dpg.add_text("Steps:")
                    dpg.add_slider_int(
                        default_value=self.sweep_steps,
                        tag=self._tag_sweep_steps,
                        min_value=2,
                        max_value=500,
                        width=scale(180),
                        callback=self._on_sweep_steps_changed,
                    )

            # Sub-Section: Custom Template JSON
            with dpg.group(tag=self._tag_grp_custom, show=is_custom):
                dpg.add_text("JSON Template ({seq}, {val}, {t}):")
                dpg.add_input_text(
                    default_value=self.custom_template_str,
                    tag=self._tag_custom_json,
                    width=-1,
                    callback=self._on_custom_json_changed,
                )

            dpg.add_separator()

            # Section: Flow Control
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
            dpg.add_text("Emitted: 0 cmds  |  Measured: 0.0 Hz", tag=self._tag_txt_rate)
            dpg.add_text("Last: {}", tag=self._tag_txt_preview)
            dpg.add_text("Prep Latency: 0.000 ms", tag=self._tag_txt_perf)

    # --------------------------------------------------------------------------
    # UI Callbacks
    # --------------------------------------------------------------------------
    def _on_toggle_clicked(self, *args: Any) -> None:
        if self._running:
            self.stop_stream()
        else:
            self.start_stream()

    def _on_step_clicked(self, *args: Any) -> None:
        self._emit_single_command()

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
            self.seq_counter = 0
            self.dropped_count = 0
            self._rate_timestamps.clear()
            self.measured_rate_hz = 0.0
            self.last_prep_ms = 0.0
            self.last_payload_preview = "{}"
        self._update_telemetry_ui()

    def _on_rate_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.rate_target = max(1.0, float(app_data))

    def _on_uncapped_changed(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.uncapped_speed = bool(app_data)

    def _on_profile_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.cmd_profile = str(app_data)
        is_custom = self.cmd_profile == "Custom Template (JSON)"
        if dpg.does_item_exist(self._tag_grp_sweep):
            dpg.configure_item(self._tag_grp_sweep, show=not is_custom)
        if dpg.does_item_exist(self._tag_grp_custom):
            dpg.configure_item(self._tag_grp_custom, show=is_custom)

    def _on_sweep_min_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.sweep_min = float(app_data)

    def _on_sweep_max_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.sweep_max = float(app_data)

    def _on_sweep_steps_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.sweep_steps = max(2, int(app_data))

    def _on_custom_json_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.custom_template_str = str(app_data)

    def _on_backpressure_changed(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.respect_backpressure = bool(app_data)

    def _on_universal_toggled(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.universal_any_output = bool(app_data)
        self._update_output_iotype()

    # --------------------------------------------------------------------------
    # Pipeline Input Callback (Automation & Remote Controls)
    # --------------------------------------------------------------------------
    def input_cb(self, data: Any = None, *args: Any, **kwargs: Any) -> bool:
        """Remote automation control for automated benchmarks and testing suites."""
        payload = (
            data
            if data is not None
            else (kwargs.get("cmd") or kwargs.get("trigger") or (args[0] if args else None))
        )

        if isinstance(payload, str):
            cmd = payload.strip().upper()
            if cmd in ("START", "RUN"):
                self.start_stream()
            elif cmd in ("STOP", "PAUSE"):
                self.stop_stream()
            elif cmd in ("BURST", "BLAST"):
                self.start_burst()
            elif cmd == "STEP":
                self._emit_single_command()
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
            elif action == "step":
                self._emit_single_command()
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


EXPORTED_CLASS = CmdBenchmark_win
EXPORTED_NAME = "Command Benchmark"
DESCRIPTION = "High-throughput synthetic command dictionary generator for pipeline and hardware testing."
