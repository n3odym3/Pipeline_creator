from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import dearpygui.dearpygui as dpg

from core.input_output_types import IOTypes


def _fmt_delta_ms(val_ms: float) -> str:
    """Format propagation delay with microsecond precision (3 decimals) for fast hops."""
    if abs(val_ms) < 10.0:
        return f"{val_ms:.3f} ms"
    if abs(val_ms) < 100.0:
        return f"{val_ms:.2f} ms"
    return f"{val_ms:.1f} ms"


class _PropagationEndpoint:
    """
    Lightweight input pin proxy for either 'Start (A)' or 'Stop (B)' on a Propagation Tester node.

    Because upstream modules in Pipeline Creator dispatch by calling `tgt.input_cb(*args, **kwargs)`
    on each object in `src.connections[output_key]`, having a dedicated endpoint object per input pin
    allows the Propagation Tester to distinguish `Start (A)` vs `Stop (B)` signals with zero overhead,
    even if both pins are connected to the same upstream module or receive identical payloads.
    """

    KIND: str = "propagation_endpoint"
    _is_propagation_endpoint: bool = True

    def __init__(self, owner: _PropagationTesterNode, role: str) -> None:
        self.owner: _PropagationTesterNode = owner
        self.role: str = role  # "A" (Start) or "B" (Stop)
        self.UUID: str = owner.UUID
        self.label: str = f"{owner.label} [{role}]"
        self.accepted_input_types: List[IOTypes] = [IOTypes.ANY]
        self.outputs: Dict[str, Any] = {}
        self.connections: Dict[str, List[Any]] = {}

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        if self.role == "A":
            self.owner.on_start_signal(*args, **kwargs)
        else:
            self.owner.on_stop_signal(*args, **kwargs)

    def is_ready(self) -> bool:
        """Never exert backpressure on the measured pipeline."""
        return True

    def set_acquisition(self, *args: Any, **kwargs: Any) -> None:
        pass


class _PropagationTesterNode:
    """
    Built-in NodeEditor flow speedometer & propagation delay (Delta-t) tester.

    Connects any two points in a pipeline:
      - Pin 'Start (A)': Wired from the upstream/trigger module output (e.g. Module A).
                         Inserted at index 0 of A's connection list so synchronous call chains
                         (A -> B -> C -> D) timestamp 'Start (A)' BEFORE B begins executing.
      - Pin 'Stop (B)' : Wired from the downstream module output (e.g. Module B, C, or D).
                         Timestamps arrival after the chain completes and computes Delta-t = t_B - t_A.

    Renders a live digital speedometer + rolling mini-plot directly inside the node in the NodeEditor,
    and integrates with the NodePerfProfiler Full Report Window on demand.
    """

    KIND: str = "propagation_tester"
    MODE_SERVICE: str = "Service (No Queue Wait)"
    MODE_E2E: str = "End-to-End (Queue + Exec)"
    PAIRING_MODES: List[str] = [MODE_SERVICE, MODE_E2E]

    def __init__(
        self,
        label: str = "Propagation Tester",
        uuid: Optional[str] = None,
        pairing_mode: str = "Service (No Queue Wait)",
    ) -> None:
        self.label: str = label or "Propagation Tester"
        self.UUID: str = uuid or str(dpg.generate_uuid())
        if pairing_mode in ("FIFO (1:1)", self.MODE_E2E):
            self.pairing_mode: str = self.MODE_E2E
        else:
            self.pairing_mode = self.MODE_SERVICE

        self.accepted_input_types: List[IOTypes] = [IOTypes.ANY]
        self.outputs: Dict[str, Any] = {}
        self.connections: Dict[str, List[Any]] = {}

        # Dedicated pin endpoints for Start (A) and Stop (B)
        self.endpoint_a = _PropagationEndpoint(self, "A")
        self.endpoint_b = _PropagationEndpoint(self, "B")

        # High-precision timing state
        self._lock = threading.Lock()
        self._pending_starts: Deque[float] = deque(maxlen=4096)
        self._last_start_ts: float = 0.0
        self._last_stop_ts: float = 0.0

        self.count_a: int = 0
        self.count_b: int = 0
        self.unmatched_count: int = 0
        self.dropped_count: int = 0

        self._rate_a_ts: Deque[float] = deque(maxlen=120)
        self._rate_b_ts: Deque[float] = deque(maxlen=120)
        self._dt_history: Deque[float] = deque(maxlen=240)

        self.last_dt_ms: float = 0.0
        self.last_e2e_dt_ms: float = 0.0
        self.last_service_dt_ms: float = 0.0
        self.mean_dt_ms: float = 0.0
        self.p95_dt_ms: float = 0.0
        self.min_dt_ms: float = 0.0
        self.max_dt_ms: float = 0.0
        self.std_dt_ms: float = 0.0
        self.rate_a_hz: float = 0.0
        self.rate_b_hz: float = 0.0

        self._last_ui_update_ts: float = 0.0
        self.node_id: Optional[int] = None
        self.editor: Optional[Any] = None

        # Source connection labels for display
        self.source_a_name: str = "unconnected"
        self.source_b_name: str = "unconnected"

        # DPG UI tags inside the node
        u = self.UUID
        self._pin_a_text_tag: str = f"_prop_pin_a_{u}"
        self._pin_b_text_tag: str = f"_prop_pin_b_{u}"
        self._pin_a_tooltip_tag: str = f"_prop_pin_a_tt_{u}"
        self._pin_b_tooltip_tag: str = f"_prop_pin_b_tt_{u}"
        self._txt_primary_tag: str = f"_prop_txt_pri_{u}"
        self._txt_secondary_tag: str = f"_prop_txt_sec_{u}"
        self._tooltip_details_tag: str = f"_prop_tt_det_{u}"

    # ------------------------------------------------------------------
    # Attribute / Endpoint Resolution
    # ------------------------------------------------------------------

    def get_in_a_tag(self, node_id: Optional[int] = None) -> str:
        nid = node_id if node_id is not None else self.node_id
        return f"{nid}_InA"

    def get_in_b_tag(self, node_id: Optional[int] = None) -> str:
        nid = node_id if node_id is not None else self.node_id
        return f"{nid}_InB"

    @staticmethod
    def _attr_matches(attr_id_or_tag: Any, expected_tag: str) -> bool:
        if attr_id_or_tag == expected_tag or str(attr_id_or_tag) == str(expected_tag):
            return True
        try:
            id_a = (
                attr_id_or_tag
                if isinstance(attr_id_or_tag, int)
                else (
                    dpg.get_alias_id(attr_id_or_tag)
                    if (isinstance(attr_id_or_tag, str) and dpg.does_alias_exist(attr_id_or_tag))
                    else None
                )
            )
            id_b = (
                expected_tag
                if isinstance(expected_tag, int)
                else (
                    dpg.get_alias_id(expected_tag)
                    if (isinstance(expected_tag, str) and dpg.does_alias_exist(expected_tag))
                    else None
                )
            )
            if id_a is not None and id_b is not None:
                return id_a == id_b
        except Exception:
            pass
        return False

    def is_start_attr(self, to_attr: Any, node_id: Optional[int] = None) -> bool:
        return self._attr_matches(to_attr, self.get_in_a_tag(node_id))

    def is_stop_attr(self, to_attr: Any, node_id: Optional[int] = None) -> bool:
        return self._attr_matches(to_attr, self.get_in_b_tag(node_id))

    def resolve_endpoint(self, to_attr: Any, node_id: Optional[int] = None) -> _PropagationEndpoint:
        """Return `endpoint_a` if `to_attr` is Start (A), else `endpoint_b`."""
        if self.is_start_attr(to_attr, node_id):
            return self.endpoint_a
        return self.endpoint_b

    def update_pin_labels(self, source_a: Optional[str] = None, source_b: Optional[str] = None) -> None:
        if source_a is not None:
            self.source_a_name = source_a
        if source_b is not None:
            self.source_b_name = source_b

        if dpg.does_item_exist(self._pin_a_tooltip_tag):
            try:
                dpg.set_value(
                    self._pin_a_tooltip_tag,
                    f"Start of propagation (Trigger)\nConnected to: {self.source_a_name}",
                )
            except Exception:
                pass
        if dpg.does_item_exist(self._pin_b_tooltip_tag):
            try:
                dpg.set_value(
                    self._pin_b_tooltip_tag,
                    f"End of propagation (Result)\nConnected to: {self.source_b_name}",
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # High-Precision Signal Handlers
    # ------------------------------------------------------------------

    def on_start_signal(self, *args: Any, **kwargs: Any) -> None:
        """Triggered when upstream Module A emits."""
        t_now = time.perf_counter()
        with self._lock:
            # If the pipeline was idle since the last burst, any leftover starts were dropped downstream
            if (
                self._pending_starts
                and (t_now - self._last_start_ts) > 0.35
                and (t_now - self._last_stop_ts) > 0.35
            ):
                self.unmatched_count += len(self._pending_starts)
                self._pending_starts.clear()

            self.count_a += 1
            self._last_start_ts = t_now
            self._rate_a_ts.append(t_now)
            if len(self._pending_starts) == self._pending_starts.maxlen:
                self.unmatched_count += 1
            self.dropped_count = max(0, self.count_a - self.count_b - len(self._pending_starts))
            self._pending_starts.append(t_now)

        # Forward start event to profiler record if Heartbeat / Profiling is active
        editor = self.editor
        if editor is not None:
            rec = getattr(editor, "_perf_records", {}).get(self.UUID)
            if rec is not None:
                with rec.lock:
                    rec.last_in_ts = t_now
                    rec.total_in = self.count_a
                    rec.in_timestamps.append(t_now)

        if (t_now - self._last_ui_update_ts) >= 0.12:
            self._last_ui_update_ts = t_now
            self._refresh_node_ui(t_now)

    def on_stop_signal(self, *args: Any, **kwargs: Any) -> None:
        """Triggered when downstream Module B/C/D finishes and emits."""
        t_now = time.perf_counter()
        dt_ms: Optional[float] = None

        with self._lock:
            self.count_b += 1
            self._rate_b_ts.append(t_now)

            if self._pending_starts:
                # Discard stale timestamps older than 15s so a lost packet doesn't skew stats forever
                while len(self._pending_starts) > 1 and (t_now - self._pending_starts[0]) > 15.0:
                    self._pending_starts.popleft()
                    self.unmatched_count += 1

                t_start = self._pending_starts.popleft()
                e2e_dt_ms = max(0.0, (t_now - t_start) * 1000.0)
                t_srv_start = max(t_start, self._last_stop_ts) if self._last_stop_ts > 0.0 else t_start
                srv_dt_ms = max(0.0, (t_now - t_srv_start) * 1000.0)

                self.last_e2e_dt_ms = e2e_dt_ms
                self.last_service_dt_ms = srv_dt_ms
                dt_ms = srv_dt_ms if self.pairing_mode == self.MODE_SERVICE else e2e_dt_ms

            self._last_stop_ts = t_now
            self.dropped_count = max(0, self.unmatched_count)

            if dt_ms is not None:
                self.last_dt_ms = dt_ms
                if len(self._dt_history) == 0:
                    self.min_dt_ms = dt_ms
                    self.max_dt_ms = dt_ms
                else:
                    if dt_ms < self.min_dt_ms:
                        self.min_dt_ms = dt_ms
                    if dt_ms > self.max_dt_ms:
                        self.max_dt_ms = dt_ms
                self._dt_history.append(dt_ms)

        # Forward completed propagation pair to profiler record if active
        editor = self.editor
        if editor is not None:
            rec = getattr(editor, "_perf_records", {}).get(self.UUID)
            if rec is not None:
                with rec.lock:
                    rec.last_out_ts = t_now
                    rec.total_out = self.count_b
                    rec.out_timestamps.append(t_now)
                    rec.dropped_count = self.dropped_count
                    if dt_ms is not None:
                        rec.last_in_to_out_ms = dt_ms
                        rec.last_self_ms = dt_ms
                        rec.in_to_out_ms.append(dt_ms)
                        rec.self_exec_ms.append(dt_ms)

        if (t_now - self._last_ui_update_ts) >= 0.08:
            self._last_ui_update_ts = t_now
            self._refresh_node_ui(t_now)

    # ------------------------------------------------------------------
    # Statistics & Live Embedded Node UI Update
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_rate(dq: Deque[float], now: float) -> float:
        while dq and (now - dq[0]) > 2.5:
            dq.popleft()
        if len(dq) < 2:
            return 0.0
        span = dq[-1] - dq[0]
        if span <= 1e-9:
            return 0.0
        if (now - dq[-1]) > 1.2:
            return 0.0
        return (len(dq) - 1) / span

    def _refresh_node_ui(self, now: Optional[float] = None) -> None:
        if not dpg.is_dearpygui_running():
            return
        if now is None:
            now = time.perf_counter()

        with self._lock:
            self.rate_a_hz = self._compute_rate(self._rate_a_ts, now)
            self.rate_b_hz = self._compute_rate(self._rate_b_ts, now)
            cnt_a = self.count_a
            cnt_b = self.count_b
            last_dt = self.last_dt_ms
            last_srv = self.last_service_dt_ms
            last_e2e = self.last_e2e_dt_ms
            min_dt = self.min_dt_ms
            max_dt = self.max_dt_ms
            dt_list = list(self._dt_history)

        if dt_list:
            n = len(dt_list)
            mean_dt = sum(dt_list) / n
            sorted_dt = sorted(dt_list)
            p95_dt = sorted_dt[min(n - 1, int(0.95 * (n - 1)))]
            if n > 1:
                var = sum((x - mean_dt) ** 2 for x in dt_list) / n
                std_dt = math.sqrt(var)
            else:
                std_dt = 0.0
        else:
            mean_dt = 0.0
            p95_dt = 0.0
            std_dt = 0.0

        self.mean_dt_ms = mean_dt
        self.p95_dt_ms = p95_dt
        self.std_dt_ms = std_dt

        pass_pct = min(100.0, (cnt_b / cnt_a) * 100.0) if cnt_a > 0 else 100.0

        try:
            if dpg.does_item_exist(self._txt_primary_tag):
                dpg.set_value(self._txt_primary_tag, f"dt: {_fmt_delta_ms(last_dt)}")
            if dpg.does_item_exist(self._txt_secondary_tag):
                dpg.set_value(
                    self._txt_secondary_tag,
                    f"avg {_fmt_delta_ms(mean_dt)}  ({self.rate_b_hz:.1f} Hz)",
                )
            if dpg.does_item_exist(self._tooltip_details_tag):
                details_text = (
                    f"Propagation Diagnostics:\n"
                    f"-------------------------\n"
                    f"Last dt      : {_fmt_delta_ms(last_dt)}\n"
                    f"Mean dt      : {_fmt_delta_ms(mean_dt)}\n"
                    f"P95 dt       : {_fmt_delta_ms(p95_dt)}\n"
                    f"Min / Max    : {_fmt_delta_ms(min_dt)} / {_fmt_delta_ms(max_dt)}\n"
                    f"Jitter (std) : +/-{_fmt_delta_ms(std_dt)}\n"
                    f"Service dt   : {_fmt_delta_ms(last_srv)} (no queue wait)\n"
                    f"Total E2E dt : {_fmt_delta_ms(last_e2e)} (with queue wait)\n"
                    f"Rate A       : {self.rate_a_hz:.1f} Hz ({cnt_a:,} pkts)\n"
                    f"Rate B       : {self.rate_b_hz:.1f} Hz ({cnt_b:,} pkts)\n"
                    f"Yield        : {pass_pct:.0f}% ({self.dropped_count} dropped)\n"
                    f"Mode         : {self.pairing_mode}\n\n"
                    f"Source A: {self.source_a_name}\n"
                    f"Source B: {self.source_b_name}"
                )
                dpg.set_value(self._tooltip_details_tag, details_text)
        except Exception:
            pass

    def reset_metrics(self) -> None:
        """Reset all counters and min/max records."""
        with self._lock:
            self._pending_starts.clear()
            self._last_start_ts = 0.0
            self._last_stop_ts = 0.0
            self.count_a = 0
            self.count_b = 0
            self.unmatched_count = 0
            self.dropped_count = 0
            self._rate_a_ts.clear()
            self._rate_b_ts.clear()
            self._dt_history.clear()
            self.last_dt_ms = 0.0
            self.last_e2e_dt_ms = 0.0
            self.last_service_dt_ms = 0.0
            self.mean_dt_ms = 0.0
            self.p95_dt_ms = 0.0
            self.min_dt_ms = 0.0
            self.max_dt_ms = 0.0
            self.std_dt_ms = 0.0
            self.rate_a_hz = 0.0
            self.rate_b_hz = 0.0

        if self.editor is not None:
            rec = getattr(self.editor, "_perf_records", {}).get(self.UUID)
            if rec is not None:
                rec.reset()

        self._refresh_node_ui()

    def on_mode_changed(self, new_mode: str) -> None:
        if new_mode in self.PAIRING_MODES:
            with self._lock:
                self.pairing_mode = new_mode
                self._dt_history.clear()
                self.min_dt_ms = 0.0
                self.max_dt_ms = 0.0
        self._refresh_node_ui()

    def set_label(self, new_label: str, node_id: Optional[int] = None) -> None:
        clean = new_label.strip() if new_label else "Propagation Tester"
        self.label = clean
        self.endpoint_a.label = f"{clean} [A]"
        self.endpoint_b.label = f"{clean} [B]"
        nid = node_id or self.node_id
        if nid and dpg.does_item_exist(nid):
            try:
                dpg.configure_item(nid, label=self.label)
            except Exception:
                pass

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """Fallback if called directly: treat as Stop (B)."""
        self.on_stop_signal(*args, **kwargs)

    def is_ready(self) -> bool:
        return True

    def serialize(self) -> Dict[str, Any]:
        return {
            "kind": self.KIND,
            "uuid": self.UUID,
            "label": self.label,
            "pairing_mode": self.pairing_mode,
        }

    def close(self) -> None:
        with self._lock:
            self._pending_starts.clear()
