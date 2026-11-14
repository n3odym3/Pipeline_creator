"""
Lightweight Performance Relay / Sink Node Module (Benchmark & Stress Suite).

Measures exact inter-module hop transfer latency, fan-out / serial chain propagation,
and end-to-end DAG completion latency via global time.perf_counter_ns() with minimal
compute or UI overhead.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import dearpygui.dearpygui as dpg

from config.display_scaling import display_scaling
from core.app_state import app_state
from core.framework_profiler import framework_profiler, should_refresh_benchmark_ui
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class PerfRelayNode_win(WindowBase):
    """
    Ultra-lightweight pass-through relay and terminal sink for framework profiling.

    When downstream connections exist on `Out`, timestamps each hop and forwards;
    when no downstream connections exist (terminal sink), records end-to-end DAG latency.
    """

    label: str = "Perf Relay Node"
    win_width: int = display_scaling.scale(260)
    win_height: int = display_scaling.scale(140)
    description: str = "Zero-compute perf_counter relay and sink for measuring framework link & graph overhead."

    accepted_input_types: list[type] = [
        IOTypes.ANY,
        IOTypes.TRIGGER,
        IOTypes.SAMPLE,
        IOTypes.FRAME,
        IOTypes.CMD_DICT,
    ]
    outputs: dict[str, type] = {
        "Out": IOTypes.ANY,
    }
    persistent_fields: list[str] = [
        "label",
        "hop_metric_key",
        "force_terminal_e2e",
        "ui_update_hz",
    ]

    def __init__(
        self,
        label: str = "Perf Relay Node",
        win_width: int = display_scaling.scale(260),
        win_height: int = display_scaling.scale(140),
        pos: list[int] = [420, 60],
        uuid: Optional[str] = None,
        visible: bool = True,
        hop_metric_key: str = "link_hop_ns",
        force_terminal_e2e: bool = False,
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
        self.hop_metric_key: str = str(hop_metric_key)
        self.force_terminal_e2e: bool = bool(force_terminal_e2e)
        self.ui_update_hz: float = float(ui_update_hz)
        self.recv_count: int = 0
        self.last_hop_us: float = 0.0
        self._last_ui_update_ns: int = 0
        self._txt_recv_tag: str = f"{self.winID}_recv"
        self._txt_lat_tag: str = f"{self.winID}_lat"

        self.create()

    def create(self) -> None:
        """Build minimal DearPyGui status window (skipped when headless)."""
        if app_state.headless:
            return
        with self.window():
            dpg.add_text("Perf Relay / Sink", color=(150, 235, 160))
            dpg.add_text("Recv: 0", tag=self._txt_recv_tag)
            dpg.add_text("Last hop: 0.0 us", tag=self._txt_lat_tag)

    def set_ui_update_hz(self, hz: float) -> None:
        """Configure live DearPyGui widget refresh cadence (0 = passive when profiling)."""
        self.ui_update_hz = float(hz)
        self._last_ui_update_ns = 0

    def _refresh_ui(self, force: bool = False) -> None:
        now_ns = should_refresh_benchmark_ui(
            self.ui_update_hz, self._last_ui_update_ns, app_state.headless, force=force
        )
        if now_ns is None:
            return
        self._last_ui_update_ns = now_ns
        if dpg.does_item_exist(self._txt_recv_tag):
            dpg.set_value(self._txt_recv_tag, f"Recv: {self.recv_count}")
        if dpg.does_item_exist(self._txt_lat_tag):
            dpg.set_value(self._txt_lat_tag, f"Last hop: {self.last_hop_us:.2f} us")

    def input_cb(self, data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Receive packet, record hop & E2E timestamps, and forward downstream if connected."""
        t_enter = time.perf_counter_ns()
        self.recv_count += 1
        targets = self.connections.get("Out", [])
        is_sink = self.force_terminal_e2e or (len(targets) == 0)

        if isinstance(data, dict) and data.get("__perf__"):
            t_hop = int(data.get("t_hop_ns", t_enter))
            t_origin = int(data.get("t_origin_ns", t_enter))
            nbytes = int(data.get("nbytes", 0))
            hop_ns = max(0, t_enter - t_hop)
            e2e_ns = max(0, t_enter - t_origin) if is_sink else None
            self.last_hop_us = hop_ns * 1e-3

            if not targets:
                if framework_profiler.enabled:
                    framework_profiler.record_hop(
                        hop_ns=hop_ns,
                        e2e_ns=e2e_ns,
                        hop_key=self.hop_metric_key,
                        bytes_count=nbytes if is_sink else 0,
                        exec_ns=time.perf_counter_ns() - t_enter,
                    )
                self._refresh_ui(force=False)
                return

            if framework_profiler.enabled:
                framework_profiler.record_hop(
                    hop_ns=hop_ns,
                    e2e_ns=e2e_ns,
                    hop_key=self.hop_metric_key,
                    bytes_count=nbytes if is_sink else 0,
                )

            for tgt in targets:
                if hasattr(tgt, "input_cb"):
                    fwd = dict(data)
                    fwd["t_hop_ns"] = time.perf_counter_ns()
                    tgt.input_cb(fwd)
        else:
            if framework_profiler.enabled:
                framework_profiler.record_counter("received_count", 1)
            for tgt in targets:
                if hasattr(tgt, "input_cb"):
                    tgt.input_cb(data)

        if framework_profiler.enabled:
            framework_profiler.record_duration_ns("relay_exec_ns", time.perf_counter_ns() - t_enter)

        self._refresh_ui(force=False)

    def reset_counters(self) -> None:
        """Reset per-node packet counter."""
        self.recv_count = 0
        self.last_hop_us = 0.0
        self._refresh_ui(force=True)


EXPORTED_CLASS = PerfRelayNode_win
EXPORTED_NAME = "Perf Relay Node"
