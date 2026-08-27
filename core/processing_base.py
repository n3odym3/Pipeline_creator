from __future__ import annotations

import multiprocessing as mp
import queue
from typing import Any, Dict, Optional, Tuple

from loguru import logger


class ProcessingBase:
    """
    Base class for multiprocessing-based data processing modules.

    This class manages a background worker process that receives data via an input queue,
    processes it using a subclass-defined `_process_data()` method, and returns the result
    via an output queue.

    Supports different queue drop policies and runtime parameter updates.
    """

    def __init__(
        self,
        params: Optional[Dict[str, Any]] = None,
        *,
        buffer_size: int = 10,
        drop_policy: str = "drop_new",
        daemon: bool = True,
    ) -> None:
        """
        Initialize the processing base.

        Args:
            params: Initial parameters dictionary passed to the worker.
            buffer_size: Max number of items in the input queue.
            drop_policy: 'drop_new', 'drop_oldest', or 'block'.
            daemon: Whether the worker process runs as a daemon.
        """
        self.params: Dict[str, Any] = dict(params) if params else {}
        self._drop_policy = drop_policy

        self._in_queue: mp.Queue[Any] = mp.Queue(maxsize=max(1, buffer_size))
        self._out_queue: mp.Queue[Any] = mp.Queue(1)
        self._ctrl_queue: mp.Queue[Tuple[str, Optional[Dict[str, Any]]]] = mp.Queue()

        self._process: Optional[mp.Process] = None
        self._daemon = daemon

    def start(self) -> None:
        """Start or restart the worker process."""
        if self._process and self._process.is_alive():
            return
        self._process = mp.Process(target=self._worker_entrypoint, daemon=self._daemon)
        self._process.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Request the worker to stop and wait for graceful shutdown."""
        if not self._process or not self._process.is_alive():
            return
        self._ctrl_queue.put(("stop", None))
        self._process.join(timeout)
        if self._process.is_alive():
            logger.warning("Worker did not stop gracefully, terminating...")
            self._process.terminate()
        self._process = None

    def is_ready(self) -> bool:
        """Check if worker is alive and queues are not saturated."""
        return bool(
            self._process
            and self._process.is_alive()
            and not self._in_queue.full()
            and not self._out_queue.full()
        )

    def submit(self, data: Any, timeout: Optional[float] = None) -> bool:
        """
        Try to submit data to the worker for processing.

        Args:
            data: Input to be processed.
            timeout: Timeout if blocking is used (drop_policy='block').

        Returns:
            True if data was successfully submitted, False otherwise.
        """
        try:
            self._in_queue.put_nowait(data)
            return True
        except queue.Full:
            if self._drop_policy == "block":
                self._in_queue.put(data, timeout=timeout)
                return True
            elif self._drop_policy == "drop_oldest":
                try:
                    self._in_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._in_queue.put_nowait(data)
                    return True
                except queue.Full:
                    return False
            elif self._drop_policy == "drop_new":
                return False
            else:
                raise ValueError(f"Invalid drop_policy: {self._drop_policy}")

    def get(self) -> Optional[Any]:
        """
        Get the next processed result from the output queue, if available.

        Returns:
            The processed result or None if no result is available.
        """
        try:
            return self._out_queue.get_nowait()
        except queue.Empty:
            return None

    def queue_size(self) -> int:
        """
        Get the current size of the input queue.

        Returns:
            Number of items in the input queue or -1 if unsupported.
        """
        try:
            return self._in_queue.qsize()
        except NotImplementedError:
            return -1

    def update_params(self, **kwargs: Any) -> None:
        """
        Send a parameter update command to the worker process.

        Args:
            kwargs: Key-value pairs to update.
        """
        self._ctrl_queue.put(("update", kwargs))

    def _worker_entrypoint(self) -> None:
        """
        Internal method run by the worker process.
        Listens for control commands and data to process.
        """
        while True:
            try:
                while True:
                    cmd, payload = self._ctrl_queue.get_nowait()
                    if cmd == "stop":
                        return
                    elif cmd == "update" and payload:
                        self.params.update(payload)
            except queue.Empty:
                pass

            try:
                data = self._in_queue.get(timeout=0.01)
            except queue.Empty:
                continue

            try:
                result = self._process_data(data, self.params)
                self._out_queue.put(result)
            except Exception as e:
                logger.warning(f"Error in worker: {e}")

    def _process_data(self, data: Any, params: Dict[str, Any]) -> Any:
        """
        Method to be overridden by subclasses to perform actual processing.

        Args:
            data: Input data to process.
            params: Current parameters dictionary.

        Returns:
            Processed output to be pushed to the output queue.
        """
        raise NotImplementedError("Subclasses must override _process_data()")

