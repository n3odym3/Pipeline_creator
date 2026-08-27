"""
Base class for viewport processes running in parallel.
Provides queue management, lifecycle control, and standardized event handling.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import traceback
from typing import Any, Dict, Optional, Tuple

from loguru import logger


class ViewportProcessBase:
    """
    Base class for multiprocessing-based viewport processes.

    Manages a background viewport process that:
    - Receives data via an input queue
    - Sends events/info back via an output queue
    - Handles control commands via a control queue
    - Manages proper lifecycle (start, stop, cleanup)
    """

    def __init__(
        self,
        params: Optional[Dict[str, Any]] = None,
        *,
        input_buffer_size: int = 100,
        output_buffer_size: int = 100,
        daemon: bool = False,
    ) -> None:
        """
        Initialize the viewport process base.

        Args:
            params: Initial parameters dictionary for the viewport.
            input_buffer_size: Max items in input queue.
            output_buffer_size: Max items in output queue.
            daemon: Whether the process runs as daemon.
        """
        self.params: Dict[str, Any] = dict(params) if params else {}
        self._daemon = daemon

        # Queues for communication
        self._in_queue: mp.Queue[Any] = mp.Queue(maxsize=max(1, input_buffer_size))
        self._out_queue: mp.Queue[Any] = mp.Queue(maxsize=max(1, output_buffer_size))
        self._ctrl_queue: mp.Queue[Tuple[str, Optional[Dict[str, Any]]]] = mp.Queue()

        # Shutdown event
        self._shutdown_event: mp.Event = mp.Event()

        # Process reference
        self._process: Optional[mp.Process] = None

    def start(self) -> None:
        """Start or restart the viewport process."""
        if self._process and self._process.is_alive():
            logger.warning("Viewport process already running")
            return

        self._shutdown_event.clear()
        self._process = mp.Process(target=self._worker_entrypoint, daemon=self._daemon)
        self._process.start()
        logger.debug(f"Started viewport process (PID: {self._process.pid})")

    def stop(self, timeout: float = 5.0) -> None:
        """Request the viewport to stop and wait for graceful shutdown."""
        if not self._process or not self._process.is_alive():
            return

        # Signal shutdown
        self._shutdown_event.set()
        self._ctrl_queue.put(("stop", None))

        # Wait for graceful shutdown
        self._process.join(timeout)
        if self._process.is_alive():
            logger.warning("Viewport process did not stop gracefully, terminating...")
            self._process.terminate()
            self._process.join(timeout=1.0)

        # Cleanup queues
        self._cleanup_queue(self._in_queue)
        self._cleanup_queue(self._out_queue)

        self._process = None
        logger.debug("Viewport process stopped")

    def is_alive(self) -> bool:
        """Check if the viewport process is running."""
        return bool(self._process and self._process.is_alive())

    def is_ready(self) -> bool:
        """Check if viewport is alive and queues are not saturated."""
        return self.is_alive() and not self._in_queue.full() and not self._out_queue.full()

    def send_input(self, data: Any, block: bool = False, timeout: Optional[float] = None) -> bool:
        """
        Send data to the viewport for display/processing.

        Args:
            data: Data to send to the viewport.
            block: Whether to block if queue is full.
            timeout: Timeout for blocking send.

        Returns:
            True if data was sent successfully, False otherwise.
        """
        try:
            if block:
                self._in_queue.put(data, timeout=timeout)
            else:
                self._in_queue.put_nowait(data)
            return True
        except queue.Full:
            logger.debug("Input queue full, dropping data")
            return False

    def get_output(self, block: bool = False, timeout: Optional[float] = None) -> Optional[Any]:
        """
        Get the next event/message from the viewport.

        Args:
            block: Whether to block waiting for output.
            timeout: Timeout for blocking get.

        Returns:
            Output data/event or None if no data available.
        """
        try:
            if block:
                return self._out_queue.get(timeout=timeout)
            else:
                return self._out_queue.get_nowait()
        except queue.Empty:
            return None

    def update_params(self, **kwargs: Any) -> None:
        """
        Send parameter update command to the viewport process.

        Args:
            kwargs: Key-value pairs to update.
        """
        self._ctrl_queue.put(("update", kwargs))

    @property
    def pid(self) -> Optional[int]:
        """Get the process ID of the viewport process."""
        return self._process.pid if self._process else None

    def _cleanup_queue(self, q: mp.Queue[Any]) -> None:
        """Drain and close a queue."""
        try:
            while not q.empty():
                q.get_nowait()
        except Exception:
            pass
        try:
            q.close()
        except Exception:
            pass

    def _worker_entrypoint(self) -> None:
        """
        Internal method run by the viewport process.
        Sets up the viewport and runs the event loop.
        """
        try:
            self._setup_viewport()
            self._send_event({"type": "viewport_ready"})
            self._run_event_loop()
        except Exception as e:
            logger.error(f"Error in viewport process: {e}")
            traceback.print_exc()
            self._send_event({"type": "viewport_error", "error": str(e)})
        finally:
            self._cleanup_viewport()
            self._send_event({"type": "viewport_closed"})

    def _setup_viewport(self) -> None:
        """
        Setup the viewport (create DPG context, window, etc.).
        Must be overridden by subclass.
        """
        raise NotImplementedError("Subclasses must implement _setup_viewport()")

    def _run_event_loop(self) -> None:
        """
        Main event loop for the viewport.
        Must be overridden by subclass.
        """
        raise NotImplementedError("Subclasses must implement _run_event_loop()")

    def _cleanup_viewport(self) -> None:
        """
        Cleanup viewport resources.
        Should be overridden by subclass if needed.
        """
        pass

    def _send_event(self, event: Any) -> None:
        """
        Send an event to the main process via output queue.

        Args:
            event: Event data to send (typically a dict).
        """
        try:
            self._out_queue.put_nowait(event)
        except queue.Full:
            logger.debug(f"Output queue full, dropping event: {event}")

    def _process_control_commands(self) -> bool:
        """
        Process pending control commands.

        Returns:
            True if should continue, False if should stop.
        """
        try:
            while True:
                cmd, payload = self._ctrl_queue.get_nowait()
                if cmd == "stop":
                    return False
                elif cmd == "update" and payload:
                    self.params.update(payload)
        except queue.Empty:
            pass
        return True

    def _process_input_data(self) -> Optional[Any]:
        """
        Get next input data if available.

        Returns:
            Input data or None if no data available.
        """
        try:
            return self._in_queue.get_nowait()
        except queue.Empty:
            return None

