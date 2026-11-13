"""
Hardware Delay Simulator Window Module (Synthetic Hardware Instrument & Latency Mock).

Simulates a physical hardware peripheral / instrument (motorized stage, power supply,
sensor, camera shutter, or serial/USB device) with realistic:
  - Command execution latency & transit delays (base delay + configurable jitter)
  - Hardware busy state machine (IDLE -> BUSY -> READY / ERROR)
  - Real backpressure hold (is_ready() returns False while hardware is busy)
  - Input command queue buffer & drop policies (drop_new, drop_oldest, queue_block)
  - Intermittent hardware error / timeout injection
  - Rich responses & telemetry:
      * Response (IOTypes.CMD_DICT): Command ACK, return values, timestamps, roundtrip latency
      * Sample (IOTypes.SAMPLE): Simulated physical trajectories or sensor readouts for plotting
      * Status (IOTypes.STATUS_DICT): Device state, temperature, voltage
      * Trigger (IOTypes.TRIGGER): Pulse emitted upon operation completion
"""

from __future__ import annotations

import collections
import json
import math
import queue
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class HardwareSimulator_win(WindowBase):
    """
    Simulated Hardware Instrument with Realistic Latency, Backpressure & ACKs.

    Acts as a downstream device sink and actuator, introducing realistic
    hardware execution delays to test pipeline flow control and queue saturation.
    """

    label: str = "Hardware Simulator"
    win_width: int = display_scaling.scale(420)
    win_height: int = display_scaling.scale(620)
    description: str = "Simulates hardware devices with realistic execution latency, backpressure, and responses."

    accepted_input_types: list[type] = [
        IOTypes.CMD_DICT,
        IOTypes.TRIGGER,
        IOTypes.TEXT,
        IOTypes.ANY,
    ]
    outputs: dict[str, type] = {
        "Response": IOTypes.CMD_DICT,
        "Sample": IOTypes.SAMPLE,
        "Status": IOTypes.STATUS_DICT,
        "Trigger": IOTypes.TRIGGER,
    }
    persistent_fields: list[str] = [
        "label",
        "device_type",
        "delay_profile",
        "base_delay_ms",
        "jitter_ms",
        "buffer_capacity",
        "drop_policy",
        "error_rate_pct",
        "emit_samples",
        "universal_any_output",
    ]

    _DEVICE_TYPES: list[str] = [
        "Motorized Stage (XYZ Motion)",
        "Power Supply (Volt / Curr)",
        "Camera / Detector Trigger",
        "Generic Echo / Actuator",
    ]

    _DELAY_PROFILES: list[str] = [
        "Base Delay + Jitter",
        "Proportional (Param Scaled)",
        "Spiky (10% Bus Contention)",
        "Zero Delay (Passthrough)",
    ]

    _DROP_POLICIES: list[str] = [
        "drop_new (Reject when Busy)",
        "drop_oldest (Latest Wins)",
        "queue_block (Sequential FIFO)",
    ]

    # Persistent configuration defaults
    device_type: str = "Motorized Stage (XYZ Motion)"
    delay_profile: str = "Base Delay + Jitter"
    base_delay_ms: float = 40.0
    jitter_ms: float = 8.0
    buffer_capacity: int = 4
    drop_policy: str = "drop_new (Reject when Busy)"
    error_rate_pct: float = 0.0
    emit_samples: bool = True
    universal_any_output: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Worker thread & Internal Queue
        self._work_queue: queue.Queue[Tuple[Dict[str, Any], float]] = queue.Queue(maxsize=max(1, self.buffer_capacity))
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_running: bool = True
        self._lock = threading.Lock()

        # Hardware State Machine
        self.device_state: str = "IDLE"
        self._is_busy: bool = False
        self._current_cmd_name: str = "None"
        self._busy_start_ts: float = 0.0
        self._total_busy_time_s: float = 0.0
        self._init_ts: float = time.perf_counter()

        # Simulated Device Hardware State Variables
        self.sim_pos_x: float = 0.0
        self.sim_pos_y: float = 0.0
        self.sim_pos_z: float = 0.0
        self.sim_voltage: float = 0.0
        self.sim_current: float = 0.0
        self.sim_output_on: bool = False
        self.sim_temp_c: float = 24.5

        # Telemetry & Metrics
        self.total_received: int = 0
        self.total_completed: int = 0
        self.total_dropped: int = 0
        self.total_errors: int = 0
        self.last_latency_ms: float = 0.0
        self.mean_latency_ms: float = 0.0
        self.max_latency_ms: float = 0.0
        self._latencies: collections.deque[float] = collections.deque(maxlen=100)
        self._last_response_preview: str = "{}"
        self._last_ui_update_ts: float = 0.0

        # UI Tags
        self._tag_device_type = f"hw_dev_type_{self.UUID}"
        self._tag_delay_profile = f"hw_del_prof_{self.UUID}"
        self._tag_base_delay = f"hw_base_del_{self.UUID}"
        self._tag_jitter = f"hw_jitter_{self.UUID}"
        self._tag_capacity = f"hw_capacity_{self.UUID}"
        self._tag_drop_policy = f"hw_drop_pol_{self.UUID}"
        self._tag_error_rate = f"hw_err_rate_{self.UUID}"
        self._tag_emit_samples = f"hw_emit_samples_{self.UUID}"
        self._tag_universal = f"hw_universal_{self.UUID}"
        self._tag_btn_reset = f"hw_btn_reset_{self.UUID}"
        self._tag_txt_state = f"hw_txt_state_{self.UUID}"
        self._tag_txt_counters = f"hw_txt_counters_{self.UUID}"
        self._tag_txt_latency = f"hw_txt_latency_{self.UUID}"
        self._tag_txt_duty = f"hw_txt_duty_{self.UUID}"
        self._tag_txt_last_resp = f"hw_txt_last_resp_{self.UUID}"

        self._update_output_iotype()
        self.create()

        # Start simulated hardware background execution thread
        self._start_hardware_worker()

    def _update_output_iotype(self) -> None:
        """Sets node editor pin type according to universal output toggle."""
        self.outputs["Response"] = IOTypes.ANY if self.universal_any_output else IOTypes.CMD_DICT
        self.outputs["Sample"] = IOTypes.ANY if self.universal_any_output else IOTypes.SAMPLE

    # --------------------------------------------------------------------------
    # Flow Control / Backpressure Hook
    # --------------------------------------------------------------------------
    def is_ready(self) -> bool:
        """
        Signals to upstream modules and the Performance Profiler whether this hardware
        is currently able to accept new operations.
        When hardware is BUSY and buffer policy is 'drop_new', returns False to test upstream hold.
        """
        if "drop_new" in self.drop_policy:
            return not self._is_busy and not self._work_queue.full()
        return not self._work_queue.full()

    # --------------------------------------------------------------------------
    # Background Hardware Execution Engine
    # --------------------------------------------------------------------------
    def _start_hardware_worker(self) -> None:
        """Start daemon thread that executes queued hardware operations."""
        self._worker_running = True
        self._worker_thread = threading.Thread(
            target=self._hardware_loop,
            daemon=True,
            name=f"HardwareSimulator_{self.UUID}",
        )
        self._worker_thread.start()

    def _hardware_loop(self) -> None:
        """Sequential hardware worker loop simulating physical execution delay."""
        while self._worker_running:
            try:
                cmd_item, arrival_ts = self._work_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            # Transition state: BUSY
            with self._lock:
                self._is_busy = True
                self.device_state = "BUSY"
                self._busy_start_ts = time.perf_counter()
                self._current_cmd_name = str(cmd_item.get("action", "command"))

            t0 = time.perf_counter()

            # 1. Compute execution delay for this specific command
            exec_delay_s = self._calculate_delay_seconds(cmd_item)

            # 2. Simulate physical delay (sleep with periodic check)
            if exec_delay_s > 0:
                target_end = t0 + exec_delay_s
                while time.perf_counter() < target_end and self._worker_running:
                    time.sleep(min(0.005, max(0.0001, target_end - time.perf_counter())))

            actual_dur_ms = max(0.001, (time.perf_counter() - t0) * 1000.0)

            # 3. Simulate fault / error injection
            is_error = False
            err_prob = max(0.0, min(100.0, float(self.error_rate_pct)))
            if err_prob > 0.0 and (random.random() * 100.0) < err_prob:
                is_error = True

            # 4. Update simulated physical device state
            with self._lock:
                self._update_simulated_device_state(cmd_item, is_error)
                self._total_busy_time_s += (actual_dur_ms / 1000.0)
                self.total_completed += 1
                if is_error:
                    self.total_errors += 1
                self.last_latency_ms = actual_dur_ms
                self._latencies.append(actual_dur_ms)
                if self._latencies:
                    self.mean_latency_ms = float(sum(self._latencies) / len(self._latencies))
                    self.max_latency_ms = float(max(self._latencies))

                # Transition state: IDLE / READY
                self._is_busy = False
                self.device_state = "ERROR" if is_error else "IDLE"

            # 5. Build response and dispatch outputs
            self._dispatch_hardware_outputs(cmd_item, arrival_ts, actual_dur_ms, is_error)

            self._work_queue.task_done()

    def _calculate_delay_seconds(self, cmd: Dict[str, Any]) -> float:
        """Resolves physical delay in seconds according to selected delay profile."""
        profile = self.delay_profile
        base_ms = max(0.0, float(self.base_delay_ms))
        jit_ms = max(0.0, float(self.jitter_ms))

        if profile == "Zero Delay (Passthrough)":
            return 0.0

        jitter_val = random.uniform(-jit_ms, jit_ms) if jit_ms > 0 else 0.0
        total_ms = max(0.0, base_ms + jitter_val)

        if profile == "Proportional (Param Scaled)":
            # Scale with movement distance or exposure time if provided
            if "position" in cmd:
                try:
                    delta = abs(float(cmd["position"]))
                    total_ms += delta * 0.5  # 0.5 ms per unit
                except Exception:
                    pass
            elif "exposure_ms" in cmd:
                try:
                    total_ms += float(cmd["exposure_ms"])
                except Exception:
                    pass
            elif "voltage" in cmd:
                try:
                    total_ms += float(cmd["voltage"]) * 0.8
                except Exception:
                    pass

        elif profile == "Spiky (10% Bus Contention)":
            if random.random() < 0.10:
                total_ms *= 3.5  # Heavy spike simulating collision or queue delay

        return total_ms / 1000.0

    def _update_simulated_device_state(self, cmd: Dict[str, Any], is_error: bool) -> None:
        """Mutate internal physical variables (e.g. stage coordinates or voltage)."""
        if is_error:
            return

        action = str(cmd.get("action", "")).lower()

        # Motion stage updates
        if "move" in action or "pos" in action:
            axis = str(cmd.get("axis", "X")).upper()
            step = float(cmd.get("position", cmd.get("step", 1.0)))
            if "rel" in action:
                if axis == "X":
                    self.sim_pos_x += step
                elif axis == "Y":
                    self.sim_pos_y += step
                else:
                    self.sim_pos_z += step
            else:
                if axis == "X":
                    self.sim_pos_x = step
                elif axis == "Y":
                    self.sim_pos_y = step
                else:
                    self.sim_pos_z = step

        # Power supply updates
        if "volt" in cmd or "voltage" in cmd:
            try:
                self.sim_voltage = float(cmd.get("voltage", cmd.get("volt", self.sim_voltage)))
            except Exception:
                pass
        if "current" in cmd or "amp" in cmd:
            try:
                self.sim_current = float(cmd.get("current", cmd.get("amp", self.sim_current)))
            except Exception:
                pass
        if "state" in cmd or "power" in cmd:
            st = str(cmd.get("state", cmd.get("power", "ON"))).upper()
            self.sim_output_on = st in ("ON", "1", "TRUE")

        # Slight ambient temperature drift
        self.sim_temp_c = round(24.5 + random.uniform(-0.3, 0.4), 2)

    def _dispatch_hardware_outputs(
        self,
        cmd: Dict[str, Any],
        arrival_ts: float,
        actual_dur_ms: float,
        is_error: bool,
    ) -> None:
        """Construct ACK response, sensor sample curve, and trigger output."""
        now = time.perf_counter()
        roundtrip_ms = (now - arrival_ts) * 1000.0
        seq = cmd.get("seq", self.total_completed)

        # 1. Response CMD_DICT payload
        status_code = "ERROR" if is_error else "OK"
        resp: Dict[str, Any] = {
            "status": status_code,
            "cmd": cmd.get("action", "unknown"),
            "seq": seq,
            "exec_time_ms": round(actual_dur_ms, 3),
            "roundtrip_ms": round(roundtrip_ms, 3),
            "device": self.device_type,
            "timestamp": now,
        }

        if is_error:
            resp["error"] = "HARDWARE_TIMEOUT_OR_FAULT"
            resp["code"] = 504
        else:
            # Attach simulated device physical measurements
            if "Motorized" in self.device_type:
                resp["pos_x"] = round(self.sim_pos_x, 3)
                resp["pos_y"] = round(self.sim_pos_y, 3)
                resp["pos_z"] = round(self.sim_pos_z, 3)
            elif "Power" in self.device_type:
                resp["voltage_out"] = round(self.sim_voltage + random.gauss(0, 0.02), 3)
                resp["current_out"] = round(self.sim_current + random.gauss(0, 0.005), 3)
                resp["power_on"] = self.sim_output_on
            elif "Camera" in self.device_type:
                resp["frame_ready"] = True
                resp["exp_done_ms"] = round(actual_dur_ms, 2)

        prev_str = json.dumps(resp, separators=(", ", ": "))
        if len(prev_str) > 65:
            prev_str = prev_str[:62] + "..."
        self._last_response_preview = prev_str

        # Dispatch to Response output connections
        out_iotype = IOTypes.ANY if self.universal_any_output else IOTypes.CMD_DICT
        for tgt in list(self.connections.get("Response", [])):
            cb = getattr(tgt, "input_cb", None)
            if callable(cb):
                try:
                    cb(cmd=resp, data=resp, data_type=out_iotype)
                except Exception as e:
                    logger.debug(f"HardwareSimulator dispatch error to {getattr(tgt, 'winID', tgt)}: {e}")

        # 2. Dispatch Sample curve if enabled (e.g. for LinePlot or SampleContainer)
        if self.emit_samples and not is_error:
            n_pts = 64
            t_curve = np.linspace(0.0, 1.0, n_pts, dtype=np.float64)
            if "Motorized" in self.device_type:
                # S-curve step response trajectory
                y_curve = float(self.sim_pos_x) * (1.0 / (1.0 + np.exp(-10.0 * (t_curve - 0.5))))
                default_name = "Stage_Pos"
            elif "Power" in self.device_type:
                # Voltage step response with tiny overshoot
                y_curve = float(self.sim_voltage) * (1.0 - np.exp(-5.0 * t_curve) * np.cos(15.0 * t_curve))
                default_name = "PSU_V"
            else:
                y_curve = np.sin(2.0 * math.pi * t_curve) * actual_dur_ms
                default_name = "HW_Latency"

            # Do not increment sample ID: reuse static name and constant UUID so downstream
            # plots or containers update the trace in-place rather than accumulating new curves.
            sample_name = str(cmd.get("sample_name") or cmd.get("name") or default_name)
            sample_uuid = str(cmd.get("uuid") or f"hw_sample_{self.UUID[:6]}")

            sample_payload = {
                "name": sample_name,
                "uuid": sample_uuid,
                "x": t_curve,
                "y": y_curve,
                "action": "select",
            }
            for tgt in list(self.connections.get("Sample", [])):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(sample=sample_payload, data=sample_payload, data_type=IOTypes.SAMPLE)
                    except Exception:
                        pass

        # 3. Dispatch Status Dict
        status_dict = {
            "state": self.device_state,
            "temp_c": self.sim_temp_c,
            "busy": self._is_busy,
            "completed": self.total_completed,
            "errors": self.total_errors,
        }
        for tgt in list(self.connections.get("Status", [])):
            cb = getattr(tgt, "input_cb", None)
            if callable(cb):
                try:
                    cb(data=status_dict, data_type=IOTypes.STATUS_DICT)
                except Exception:
                    pass

        # 4. Dispatch Trigger on operation completion
        for tgt in list(self.connections.get("Trigger", [])):
            cb = getattr(tgt, "input_cb", None)
            if callable(cb):
                try:
                    cb(data=f"DONE_{seq}", data_type=IOTypes.TRIGGER)
                except Exception:
                    pass

    # --------------------------------------------------------------------------
    # Pipeline Input Callback (Incoming Command Ingestion)
    # --------------------------------------------------------------------------
    def input_cb(self, data: Any = None, *args: Any, **kwargs: Any) -> bool:
        """Handle incoming command dictionaries from upstream benchmark or controller modules."""
        payload = (
            data
            if data is not None
            else (kwargs.get("cmd") or kwargs.get("trigger") or (args[0] if args else None))
        )

        now = time.perf_counter()
        self.total_received += 1

        # Normalize payload into dictionary
        if isinstance(payload, dict):
            cmd_item = payload
        elif isinstance(payload, str):
            cmd_item = {"action": payload, "raw": payload}
        else:
            cmd_item = {"action": "execute", "val": payload}

        # Check queue capacity and drop policy
        if self._work_queue.full():
            policy = self.drop_policy
            if "drop_new" in policy:
                self.total_dropped += 1
                self.dropped_count = self.total_dropped
                return True
            elif "drop_oldest" in policy:
                try:
                    self._work_queue.get_nowait()
                    self.total_dropped += 1
                    self.dropped_count = self.total_dropped
                except queue.Empty:
                    pass

        try:
            self._work_queue.put((cmd_item, now), block=("queue_block" in self.drop_policy), timeout=0.01)
        except queue.Full:
            self.total_dropped += 1
            self.dropped_count = self.total_dropped

        # Throttled UI Telemetry Update (~6 Hz)
        if (now - self._last_ui_update_ts) >= 0.16:
            self._last_ui_update_ts = now
            self._update_telemetry_ui()

        return True

    def _update_telemetry_ui(self) -> None:
        """Update live telemetry labels in UI without hardcoded colors."""
        if not dpg.does_item_exist(self._tag_txt_state):
            return

        with self._lock:
            state = self.device_state
            is_busy = self._is_busy
            curr_cmd = self._current_cmd_name
            rx = self.total_received
            done = self.total_completed
            drop = self.total_dropped
            err = self.total_errors
            last_ms = self.last_latency_ms
            mean_ms = self.mean_latency_ms
            max_ms = self.max_latency_ms
            busy_s = self._total_busy_time_s
            prev_resp = self._last_response_preview

        total_elapsed = max(0.001, time.perf_counter() - self._init_ts)
        busy_pct = min(100.0, (busy_s / total_elapsed) * 100.0)

        busy_suffix = f" (Exec: {curr_cmd})" if is_busy else ""
        dpg.set_value(self._tag_txt_state, f"State: [{state}]{busy_suffix}")
        dpg.set_value(
            self._tag_txt_counters,
            f"Rx: {rx:,}  |  Done: {done:,}  |  Drop: {drop:,}  |  Err: {err:,}",
        )
        dpg.set_value(
            self._tag_txt_latency,
            f"Latency: Last: {last_ms:.3f} ms  |  Mean: {mean_ms:.3f} ms  |  Max: {max_ms:.3f} ms",
        )
        dpg.set_value(self._tag_txt_duty, f"Duty Cycle: {busy_pct:.1f}% Busy")
        dpg.set_value(self._tag_txt_last_resp, f"Last ACK: {prev_resp}")

    # --------------------------------------------------------------------------
    # DearPyGui UI Construction
    # --------------------------------------------------------------------------
    def create(self) -> None:
        """Construct the DearPyGui window controls."""
        scale = display_scaling.scale

        with self.window():
            # Section: Hardware Model & Device Type
            with dpg.group(horizontal=True):
                dpg.add_text("Device:")
                dpg.add_combo(
                    items=self._DEVICE_TYPES,
                    default_value=self.device_type,
                    tag=self._tag_device_type,
                    width=scale(260),
                    callback=self._on_device_type_changed,
                )

            dpg.add_separator()

            # Section: Latency & Transit Delay Profile
            with dpg.group(horizontal=True):
                dpg.add_text("Profile:")
                dpg.add_combo(
                    items=self._DELAY_PROFILES,
                    default_value=self.delay_profile,
                    tag=self._tag_delay_profile,
                    width=scale(260),
                    callback=self._on_delay_profile_changed,
                )

            dpg.add_slider_float(
                label="Base Delay",
                tag=self._tag_base_delay,
                default_value=self.base_delay_ms,
                min_value=0.0,
                max_value=1000.0,
                format="%.1f ms",
                width=scale(200),
                callback=self._on_base_delay_changed,
            )

            dpg.add_slider_float(
                label="Jitter (+/-)",
                tag=self._tag_jitter,
                default_value=self.jitter_ms,
                min_value=0.0,
                max_value=200.0,
                format="%.1f ms",
                width=scale(200),
                callback=self._on_jitter_changed,
            )

            dpg.add_separator()

            # Section: Queue Buffer & Drop Policy
            with dpg.group(horizontal=True):
                dpg.add_text("Queue Size:")
                dpg.add_slider_int(
                    default_value=self.buffer_capacity,
                    tag=self._tag_capacity,
                    min_value=1,
                    max_value=50,
                    width=scale(120),
                    callback=self._on_capacity_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Drop Policy:")
                dpg.add_combo(
                    items=self._DROP_POLICIES,
                    default_value=self.drop_policy,
                    tag=self._tag_drop_policy,
                    width=scale(240),
                    callback=self._on_drop_policy_changed,
                )

            # Fault Injection
            dpg.add_slider_float(
                label="Fault Rate",
                tag=self._tag_error_rate,
                default_value=self.error_rate_pct,
                min_value=0.0,
                max_value=50.0,
                format="%.1f %%",
                width=scale(200),
                callback=self._on_error_rate_changed,
            )

            dpg.add_separator()

            # Section: Output Options
            dpg.add_checkbox(
                label="Emit Trajectory / Sensor Waveform (on Sample)",
                tag=self._tag_emit_samples,
                default_value=self.emit_samples,
                callback=self._on_emit_samples_changed,
            )
            dpg.add_checkbox(
                label="Universal Pin (IOType.ANY on node)",
                tag=self._tag_universal,
                default_value=self.universal_any_output,
                callback=self._on_universal_toggled,
            )

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Reset Counters",
                    tag=self._tag_btn_reset,
                    width=scale(130),
                    callback=self._on_reset_clicked,
                )

            dpg.add_separator()

            # Section: Live Diagnostics & Hardware Status
            dpg.add_text("Hardware Telemetry")
            dpg.add_text("State: [IDLE]", tag=self._tag_txt_state)
            dpg.add_text("Rx: 0  |  Done: 0  |  Drop: 0  |  Err: 0", tag=self._tag_txt_counters)
            dpg.add_text("Latency: Last: 0.000 ms  |  Mean: 0.000 ms  |  Max: 0.000 ms", tag=self._tag_txt_latency)
            dpg.add_text("Duty Cycle: 0.0% Busy", tag=self._tag_txt_duty)
            dpg.add_text("Last ACK: {}", tag=self._tag_txt_last_resp)

    # --------------------------------------------------------------------------
    # UI Callbacks
    # --------------------------------------------------------------------------
    def _on_device_type_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.device_type = str(app_data)

    def _on_delay_profile_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.delay_profile = str(app_data)

    def _on_base_delay_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.base_delay_ms = max(0.0, float(app_data))

    def _on_jitter_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.jitter_ms = max(0.0, float(app_data))

    def _on_capacity_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.buffer_capacity = max(1, int(app_data))
        # Recreate internal queue with new capacity
        with self._lock:
            old_items = []
            while not self._work_queue.empty():
                try:
                    old_items.append(self._work_queue.get_nowait())
                except queue.Empty:
                    break
            self._work_queue = queue.Queue(maxsize=self.buffer_capacity)
            for item in old_items[:self.buffer_capacity]:
                self._work_queue.put(item)

    def _on_drop_policy_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.drop_policy = str(app_data)

    def _on_error_rate_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.error_rate_pct = max(0.0, min(100.0, float(app_data)))

    def _on_emit_samples_changed(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.emit_samples = bool(app_data)

    def _on_universal_toggled(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.universal_any_output = bool(app_data)
        self._update_output_iotype()

    def _on_reset_clicked(self, *args: Any) -> None:
        with self._lock:
            self.total_received = 0
            self.total_completed = 0
            self.total_dropped = 0
            self.total_errors = 0
            self.last_latency_ms = 0.0
            self.mean_latency_ms = 0.0
            self.max_latency_ms = 0.0
            self._latencies.clear()
            self._total_busy_time_s = 0.0
            self._init_ts = time.perf_counter()
            self._last_response_preview = "{}"
            self.device_state = "IDLE"
            self._is_busy = False
        self._update_telemetry_ui()

    # --------------------------------------------------------------------------
    # Lifecycle & Cleanup
    # --------------------------------------------------------------------------
    def close(self) -> None:
        """Ensure background worker stops cleanly."""
        self._worker_running = False
        super().close()


EXPORTED_CLASS = HardwareSimulator_win
EXPORTED_NAME = "Hardware Simulator"
DESCRIPTION = "Simulates hardware devices with realistic execution latency, backpressure, and responses."
