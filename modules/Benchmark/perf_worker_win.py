"""
Lightweight ProcessingBase Worker Wrapper Module (Benchmark & Stress Suite).

Measures genuine multi-process IPC entry/compute/exit latency through `ProcessingBase`
(pickle vs ShmPool shared-memory transport, queue transit, worker wakeup, and parallel
multi-worker scaling).
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Any, Deque, Dict, Optional, Tuple

import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.app_state import app_state
from core.framework_profiler import framework_profiler, should_refresh_benchmark_ui
from core.input_output_types import IOTypes
from core.processing_base import ProcessingBase
from core.window_base import WindowBase


class PerfPassWorker(ProcessingBase):
    """Minimal ProcessingBase child process recording entry/exit compute timestamps."""

    def _process_data(self, data: Any, params: Dict[str, Any]) -> Any:
        t3 = time.perf_counter_ns()
        work_us = int(params.get("work_us", 0))
        if work_us > 0:
            target = t3 + work_us * 1000
            while time.perf_counter_ns() < target:
                pass

        if isinstance(data, dict) and data.get("__perf__"):
            t4 = time.perf_counter_ns()
            return {
                "__perf__": True,
                "seq": data.get("seq", 0),
                "t_origin_ns": data.get("t_origin_ns", t3),
                "t_hop_ns": data.get("t_hop_ns", t3),
                "nbytes": data.get("nbytes", 0),
                "data": data.get("data"),
                "__worker_t3__": t3,
                "__worker_t4__": t4,
            }

        # Raw array or arbitrary payload pass-through
        t4 = time.perf_counter_ns()
        return {"__raw_result__": True, "payload": data, "__worker_t3__": t3, "__worker_t4__": t4}


class PerfPassNoShmWorker(PerfPassWorker):
    """Variant of PerfPassWorker with ShmPool explicitly disabled (pure OS-pipe pickle IPC)."""

    use_shared_memory: bool = False


class PerfWorkerNode_win(WindowBase):
    """
    Lightweight WindowBase wrapper around a dedicated `PerfPassWorker` (`ProcessingBase`).

    Captures per-packet IPC breakdown:
      - `main_encode` (submit start -> in_queue.put done)
      - `ipc_in` (in_queue.put -> worker `_process_data` entry)
      - `worker_compute` (`_process_data` execution time)
      - `ipc_out` (worker `_process_data` return -> parent `get()`)
      - `total_roundtrip` and `ipc_overhead` (= roundtrip - compute)
    """

    label: str = "Perf Worker Node"
    win_width: int = display_scaling.scale(290)
    win_height: int = display_scaling.scale(170)
    description: str = "Lightweight ProcessingBase IPC latency & scaling benchmark node."

    accepted_input_types: list[type] = [
        IOTypes.ANY,
        IOTypes.SAMPLE,
        IOTypes.FRAME,
        IOTypes.CMD_DICT,
    ]
    outputs: dict[str, type] = {
        "Out": IOTypes.ANY,
    }
    persistent_fields: list[str] = [
        "label",
        "work_us",
        "buffer_size",
        "drop_policy",
        "raw_ndarray_submit",
        "use_shared_memory",
        "metric_prefix",
        "ui_update_hz",
    ]

    def __init__(
        self,
        label: str = "Perf Worker Node",
        win_width: int = display_scaling.scale(290),
        win_height: int = display_scaling.scale(170),
        pos: list[int] = [420, 260],
        uuid: Optional[str] = None,
        visible: bool = True,
        work_us: int = 0,
        buffer_size: int = 512,
        drop_policy: str = "block",
        raw_ndarray_submit: bool = False,
        use_shared_memory: bool = True,
        metric_prefix: str = "proc",
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
        self.work_us: int = int(work_us)
        self.buffer_size: int = int(buffer_size)
        self.drop_policy: str = str(drop_policy)
        self.raw_ndarray_submit: bool = bool(raw_ndarray_submit)
        self.use_shared_memory: bool = bool(use_shared_memory)
        self.metric_prefix: str = str(metric_prefix)
        self.ui_update_hz: float = float(ui_update_hz)

        self.submitted_count: int = 0
        self.completed_count: int = 0
        self.dropped_count: int = 0
        self._last_ui_update_ns: int = 0

        self._inflight_lock = threading.Lock()
        self._inflight_fifo: Deque[list[int]] = collections.deque()
        self._done_cond = threading.Condition()

        t_spawn0 = time.perf_counter_ns()
        worker_cls = PerfPassWorker if self.use_shared_memory else PerfPassNoShmWorker
        self.worker: Optional[PerfPassWorker] = worker_cls(
            params={"work_us": self.work_us},
            buffer_size=self.buffer_size,
            out_buffer_size=self.buffer_size,
            drop_policy=self.drop_policy,
            daemon=True,
        )
        self.worker.start()
        self.spawn_latency_ms: float = (time.perf_counter_ns() - t_spawn0) * 1e-6

        self._stop_event = threading.Event()
        self._poll_thread = threading.Thread(
            target=self._result_poll_loop,
            daemon=True,
            name=f"PerfWorkerPoll_{self.UUID}",
        )
        self._poll_thread.start()

        self._txt_status_tag: str = f"{self.winID}_status"
        self._txt_stats_tag: str = f"{self.winID}_stats"
        self.create()

    def create(self) -> None:
        """Build minimal DearPyGui status window (skipped when headless)."""
        if app_state.headless:
            return
        with self.window():
            dpg.add_text("Perf Worker (ProcessingBase)", color=(255, 200, 110))
            dpg.add_text(f"Spawn: {self.spawn_latency_ms:.1f} ms", tag=self._txt_status_tag)
            dpg.add_text("Done: 0 | Drop: 0", tag=self._txt_stats_tag)

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
        if dpg.does_item_exist(self._txt_stats_tag):
            dpg.set_value(self._txt_stats_tag, f"Done: {self.completed_count} | Drop: {self.dropped_count}")

    def input_cb(self, data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Submit incoming packet to child ProcessingBase worker and record t0/t1."""
        if self.worker is None:
            return

        t0 = time.perf_counter_ns()
        seq = 0
        t_origin = t0
        t_hop = t0
        nbytes = 0
        submit_obj: Any = data

        if isinstance(data, dict) and data.get("__perf__"):
            seq = int(data.get("seq", 0))
            t_origin = int(data.get("t_origin_ns", t0))
            t_hop = int(data.get("t_hop_ns", t0))
            nbytes = int(data.get("nbytes", 0))
            if self.raw_ndarray_submit and isinstance(data.get("data"), np.ndarray):
                submit_obj = data["data"]

        # Record incoming link hop into the worker wrapper
        if framework_profiler.enabled and isinstance(data, dict) and data.get("__perf__"):
            framework_profiler.record_duration_ns("link_hop_ns", max(0, t0 - t_hop))

        entry = [seq, t0, t_origin, nbytes, 0]
        with self._inflight_lock:
            self._inflight_fifo.append(entry)

        ok = self.worker.submit(submit_obj)
        t1 = time.perf_counter_ns()

        if ok:
            entry[4] = t1
            self.submitted_count += 1
        else:
            self.dropped_count += 1
            framework_profiler.record_counter("dropped_count", 1)
            with self._inflight_lock:
                try:
                    self._inflight_fifo.remove(entry)
                except ValueError:
                    pass

    def _result_poll_loop(self) -> None:
        while not self._stop_event.is_set():
            worker = self.worker
            if worker is None:
                break
            res = worker.get(timeout=0.02)
            if res is None:
                continue

            t6 = time.perf_counter_ns()
            meta: Optional[list[int]] = None
            with self._inflight_lock:
                if self._inflight_fifo:
                    meta = self._inflight_fifo.popleft()

            t3 = None
            t4 = None
            nbytes = 0
            t_origin = meta[2] if meta else t6
            t0 = meta[1] if meta else t6
            t1 = meta[4] if (meta and meta[4] > 0) else None
            seq = meta[0] if meta else 0

            if isinstance(res, dict):
                t3 = res.get("__worker_t3__")
                t4 = res.get("__worker_t4__")
                nbytes = int(res.get("nbytes", meta[3] if meta else 0))
                if meta is None:
                    t_origin = int(res.get("t_origin_ns", t6))

            targets = self.connections.get("Out", [])
            if framework_profiler.enabled:
                framework_profiler.record_processor_stages(
                    {
                        "t0_submit_enter": t0,
                        "t1_submit_queued": t1,
                        "t2_worker_dequeued": None,
                        "t3_worker_decoded": t3,
                        "t4_worker_processed": t4,
                        "t5_worker_published": None,
                        "t6_main_get": t6,
                    },
                    prefix=self.metric_prefix,
                    e2e_ns=max(0, t6 - t_origin) if not targets else None,
                    bytes_count=nbytes if not targets else 0,
                )

            if targets:
                payload = res.get("data") if isinstance(res, dict) and "__perf__" in res else (
                    res.get("payload") if isinstance(res, dict) and "__raw_result__" in res else res
                )
                for tgt in targets:
                    if hasattr(tgt, "input_cb"):
                        fwd = {
                            "__perf__": True,
                            "seq": seq,
                            "t_origin_ns": t_origin,
                            "t_hop_ns": time.perf_counter_ns(),
                            "nbytes": nbytes,
                            "data": payload,
                        }
                        tgt.input_cb(fwd)

            with self._done_cond:
                self.completed_count += 1
                self._done_cond.notify_all()
            self._refresh_ui(force=False)

    def wait_idle(self, expected_count: Optional[int] = None, timeout_s: float = 15.0) -> bool:
        """Block until expected packets (or all submitted packets) have finished IPC roundtrip."""
        deadline = time.perf_counter() + timeout_s
        with self._done_cond:
            while True:
                target = expected_count if expected_count is not None else self.submitted_count
                if self.completed_count >= target:
                    return True
                rem = deadline - time.perf_counter()
                if rem <= 0.0:
                    return False
                self._done_cond.wait(timeout=min(0.05, rem))

    def reset_counters(self) -> None:
        """Reset submitted/completed counters cleanly."""
        with self._done_cond:
            self.submitted_count = 0
            self.completed_count = 0
            self.dropped_count = 0
            with self._inflight_lock:
                self._inflight_fifo.clear()
        self._refresh_ui(force=True)

    def close(self) -> None:
        self._stop_event.set()
        if self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1.0)
        if self.worker is not None:
            try:
                self.worker.stop(timeout=3.0)
            except Exception as e:
                logger.warning(f"Error stopping PerfPassWorker: {e}")
            self.worker = None
        super().close()


EXPORTED_CLASS = PerfWorkerNode_win
EXPORTED_NAME = "Perf Worker Node"
