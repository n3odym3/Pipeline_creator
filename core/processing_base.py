from __future__ import annotations

import collections
import multiprocessing as mp
import queue
import threading
from typing import Any, Deque, Dict, Optional, Tuple

from loguru import logger

from core.shm_transport import SHM_AVAILABLE, ShmPool

_VALID_POLICIES = ("drop_new", "drop_oldest", "block")


class ProcessingBase:
    """
    Base class for multiprocessing-based data processing modules.

    This class manages a background worker process that receives data via an input queue,
    processes it using a subclass-defined `_process_data()` method, and returns the result
    via an output queue.

    Drop policies:
        - 'drop_new':    when the input queue is full, the incoming item is rejected (cheap, producer side).
        - 'block':       when the input queue is full, the producer blocks until space is available.
        - 'drop_oldest': "latest wins". Implemented on the WORKER side: a receiver thread inside the
                         worker process continuously drains the IPC queue into a local bounded buffer and
                         discards the oldest items. The producer never has to read (unpickle) frames back
                         from the pipe just to throw them away, which used to stall the main process.

    Supports runtime parameter updates and runtime drop-policy changes (`set_drop_policy`).

    Shared-memory transport (transparent):
        Large numpy arrays found in submitted data or in results (directly, or inside tuple / list / dict)
        are passed through reusable shared-memory slots instead of being pickled through the pipe
        (see core/shm_transport.py). Subclasses keep receiving / returning ordinary numpy arrays.
        Tunables (class attributes): `use_shared_memory`, `shm_threshold_bytes`, `shm_max_pool_bytes`.
    """
    buffer_size: int = 10
    drop_policy: str = "drop_new"
    daemon: bool = True

    use_shared_memory: bool = True
    shm_threshold_bytes: int = 64 * 1024  # arrays smaller than this keep the regular pickle path
    shm_max_pool_bytes: int = 512 * 1024 * 1024  # per direction cap; beyond it -> pickle fallback

    def __init__(
        self,
        params: Optional[Dict[str, Any]] = None,
        *,
        buffer_size: Optional[int] = None,
        out_buffer_size: Optional[int] = None,
        drop_policy: Optional[str] = None,
        daemon: Optional[bool] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the processing base.

        Args:
            params: Initial parameters dictionary passed to the worker.
            buffer_size: Max number of items in the input queue (arg -> class attribute -> 10).
            out_buffer_size: Max number of items in the output queue (arg -> class attribute -> buffer_size if block else 1).
            drop_policy: 'drop_new', 'drop_oldest', or 'block' (arg -> class attribute -> 'drop_new').
            daemon: Whether the worker process runs as a daemon (arg -> class attribute -> True).
            **kwargs: Extra arguments forwarded safely.
        """
        cls = type(self)
        self.params: Dict[str, Any] = dict(params) if params else {}
        self._drop_policy = (
            drop_policy if drop_policy is not None else getattr(cls, "drop_policy", "drop_new")
        )
        resolved_buffer_size = (
            buffer_size if buffer_size is not None else getattr(cls, "buffer_size", 10)
        )
        self._buffer_size: int = max(1, int(resolved_buffer_size))
        out_buf = out_buffer_size if out_buffer_size is not None else getattr(cls, "out_buffer_size", None)
        if out_buf is not None:
            self._out_buffer_size: int = max(1, int(out_buf))
        else:
            self._out_buffer_size = self._buffer_size if self._drop_policy == "block" else 1

        self._daemon = daemon if daemon is not None else getattr(cls, "daemon", True)

        self._in_queue: mp.Queue[Any] = mp.Queue(maxsize=self._buffer_size)
        self._out_queue: mp.Queue[Any] = mp.Queue(maxsize=self._out_buffer_size)
        self._ctrl_queue: mp.Queue[Tuple[str, Optional[Dict[str, Any]]]] = mp.Queue()

        # Shared counters (written by the worker, read by the main process)
        self._worker_dropped = mp.Value("q", 0)  # items discarded by the worker-side drop_oldest buffer
        self._worker_pending = mp.Value("i", 0, lock=False)  # items waiting in the worker-side local buffer

        # Transparent shared-memory transport for large numpy arrays (one pool per direction)
        self._in_pool: Optional[ShmPool] = None
        self._out_pool: Optional[ShmPool] = None
        if SHM_AVAILABLE and bool(getattr(cls, "use_shared_memory", True)):
            try:
                threshold = int(getattr(cls, "shm_threshold_bytes", 64 * 1024))
                cap = int(getattr(cls, "shm_max_pool_bytes", 512 * 1024 * 1024))
                # In flight: IPC queue + worker-side buffer + 1 being decoded, up to 2 arrays per item.
                self._in_pool = ShmPool(min(128, 2 * (2 * self._buffer_size + 2)), threshold, cap)
                out_slots = min(128, max(8, 2 * (self._out_buffer_size + 2)))
                self._out_pool = ShmPool(out_slots, threshold, cap)
            except Exception as e:
                logger.warning(f"Shared-memory transport disabled: {e}")
                self._in_pool = None
                self._out_pool = None

        self._process: Optional[mp.Process] = None

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

    # ------------------------------------------------------------------
    # Main-process API
    # ------------------------------------------------------------------
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
        # Items still pending in in_queue will never be read: don't let the feeder thread block the
        # main process at interpreter exit while trying to flush them into the pipe.
        try:
            self._in_queue.cancel_join_thread()
        except Exception:
            pass
        # Worker is gone: no shared-memory slot can be in use anymore.
        for q in (self._in_queue, self._out_queue):
            try:
                while True:
                    q.get_nowait()
            except Exception:
                pass
        for pool in (self._in_pool, self._out_pool):
            if pool is not None:
                pool.close()
                pool.reset()

    # -- shared-memory helpers (no-ops when the transport is disabled) --
    def _encode_in(self, data: Any) -> Any:
        pool = self._in_pool
        return pool.encode(data) if pool is not None else data

    def _release_in(self, item: Any) -> None:
        if self._in_pool is not None:
            self._in_pool.release_item(item)

    def _release_out(self, item: Any) -> None:
        if self._out_pool is not None:
            self._out_pool.release_item(item)

    def is_ready(self) -> bool:
        """Check if worker is alive and queues (IPC + worker-side buffer) are not saturated."""
        if not (self._process and self._process.is_alive()):
            return False
        if self._out_queue.full():
            return False
        size = self.queue_size()
        if size < 0:  # qsize() unsupported on this platform
            return not self._in_queue.full()
        return size < self._buffer_size

    def submit(self, data: Any, timeout: Optional[float] = None) -> bool:
        """
        Try to submit data to the worker for processing.

        Args:
            data: Input to be processed.
            timeout: Timeout if blocking is used (drop_policy='block').

        Returns:
            True if data was successfully submitted, False otherwise.
        """
        policy = self._drop_policy
        # Cheap pre-check: don't copy a frame into shared memory just to reject it right after.
        if policy != "block":
            try:
                if self._in_queue.full():
                    return False
            except Exception:
                pass

        item = self._encode_in(data)
        try:
            self._in_queue.put_nowait(item)
            return True
        except queue.Full:
            if policy == "block":
                try:
                    self._in_queue.put(item, timeout=timeout)
                    return True
                except queue.Full:
                    self._release_in(item)
                    return False
            elif policy in ("drop_oldest", "drop_new"):
                # drop_oldest: the worker's receiver thread keeps the IPC queue drained, so a full IPC
                # queue means the worker cannot even keep up with receiving. Reject the item instead of
                # reading a stale one back from the pipe in the producer process (very expensive).
                self._release_in(item)
                return False
            else:
                self._release_in(item)
                raise ValueError(f"Invalid drop_policy: {policy}")
        except BaseException:
            self._release_in(item)
            raise

    def set_drop_policy(self, policy: str) -> None:
        """Change the drop policy at runtime (applied on both producer and worker side)."""
        if policy not in _VALID_POLICIES:
            raise ValueError(f"Invalid drop_policy: {policy}")
        self._drop_policy = policy
        try:
            self._ctrl_queue.put(("policy", {"drop_policy": policy}))
        except Exception:
            pass

    def get(self, timeout: Optional[float] = None) -> Optional[Any]:
        """
        Get the next processed result from the output queue.

        Args:
            timeout: None or <= 0 -> non-blocking (returns None immediately if empty).
                     > 0 -> block up to `timeout` seconds. Blocking releases the GIL and wakes up
                     exactly when a result arrives (preferred over get() + sleep() polling loops).

        Returns:
            The processed result or None if no result is available.
        """
        try:
            if timeout is None or timeout <= 0:
                item = self._out_queue.get_nowait()
            else:
                item = self._out_queue.get(timeout=timeout)
        except queue.Empty:
            return None
        except (EOFError, OSError):
            return None
        pool = self._out_pool
        return pool.decode(item) if pool is not None else item

    # Alias to match documentation and hybrid node patterns
    poll_result = get
    get_results = get

    def has_data(self) -> bool:
        """Check if output queue has at least one processed result ready."""
        try:
            return not self._out_queue.empty()
        except Exception:
            return False

    def queue_size(self) -> int:
        """
        Get the current number of pending items (IPC input queue + worker-side buffer).

        Returns:
            Number of pending items or -1 if unsupported.
        """
        try:
            ipc = self._in_queue.qsize()
        except NotImplementedError:
            return -1
        return ipc + max(0, int(self._worker_pending.value))

    def drop_count(self) -> int:
        """Number of items discarded inside the worker by the drop_oldest policy."""
        try:
            return int(self._worker_dropped.value)
        except Exception:
            return 0

    def clear(self) -> None:
        """Clear all pending items in in_queue and out_queue."""
        while not self._in_queue.empty():
            try:
                self._release_in(self._in_queue.get_nowait())
            except (queue.Empty, Exception):
                break
        while not self._out_queue.empty():
            try:
                self._release_out(self._out_queue.get_nowait())
            except (queue.Empty, Exception):
                break
        try:
            self._ctrl_queue.put_nowait(("clear", None))
        except Exception:
            pass

    def update_params(self, **kwargs: Any) -> None:
        """
        Send a parameter update command to the worker process.

        Args:
            kwargs: Key-value pairs to update.
        """
        self._ctrl_queue.put(("update", kwargs))

    # ------------------------------------------------------------------
    # Worker-process internals
    # ------------------------------------------------------------------
    def _drain_ctrl_queue(self) -> bool:
        """Apply all pending control commands (worker side). Returns True if a stop was requested."""
        try:
            while True:
                cmd, payload = self._ctrl_queue.get_nowait()
                if cmd == "stop":
                    return True
                elif cmd == "update" and payload:
                    self.params.update(payload)
                elif cmd == "policy" and payload:
                    self._drop_policy = payload.get("drop_policy", self._drop_policy)
                elif cmd == "clear":
                    self._clear_local_buffer()
                    while not self._in_queue.empty():
                        try:
                            self._release_in(self._in_queue.get_nowait())
                        except (queue.Empty, Exception):
                            break
        except (queue.Empty, Exception):
            pass
        return False

    def _init_worker_state(self) -> None:
        """Create worker-only objects (threads, locks, local buffer). Never pickled."""
        self._local_buf: Deque[Any] = collections.deque()
        self._local_cv = threading.Condition()
        self._receiver_thread: Optional[threading.Thread] = None

    def _ensure_receiver(self) -> None:
        """Start the drop_oldest receiver thread if needed (worker side)."""
        t = self._receiver_thread
        if t is not None and t.is_alive():
            return
        self._receiver_thread = threading.Thread(
            target=self._receiver_loop, name="ProcessingBase_receiver", daemon=True
        )
        self._receiver_thread.start()

    def _receiver_loop(self) -> None:
        """
        Worker-side receiver for drop_oldest: drains the IPC queue as fast as possible into a local
        bounded buffer, discarding the oldest items. Exits when the policy changes.
        """
        while self._drop_policy == "drop_oldest":
            try:
                item = self._in_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                return
            with self._local_cv:
                while len(self._local_buf) >= self._buffer_size:
                    # Discarded without decoding: just free its shared-memory slots.
                    self._release_in(self._local_buf.popleft())
                    with self._worker_dropped.get_lock():
                        self._worker_dropped.value += 1
                self._local_buf.append(item)
                self._worker_pending.value = len(self._local_buf)
                self._local_cv.notify()

    def _clear_local_buffer(self) -> None:
        cv = getattr(self, "_local_cv", None)
        if cv is None:
            return
        with cv:
            for item in self._local_buf:
                self._release_in(item)
            self._local_buf.clear()
            self._worker_pending.value = 0

    def _next_item(self, timeout: float) -> Any:
        """Fetch the next item to process (worker side). Raises queue.Empty on timeout."""
        # Items already buffered locally are always served first (also after a policy switch).
        with self._local_cv:
            if self._drop_policy == "drop_oldest":
                self._ensure_receiver()
                if not self._local_buf:
                    self._local_cv.wait(timeout)
            if self._local_buf:
                item = self._local_buf.popleft()
                self._worker_pending.value = len(self._local_buf)
                return item
            if self._drop_policy == "drop_oldest":
                raise queue.Empty
        return self._in_queue.get(timeout=timeout)

    def _worker_entrypoint(self) -> None:
        """
        Internal method run by the worker process.
        Listens for control commands and data to process.
        """
        self._init_worker_state()
        try:
            self._worker_loop()
        finally:
            # A result still buffered in the out_queue feeder (nobody reads it anymore) would make the
            # process hang at exit while flushing the pipe -> "did not stop gracefully" + terminate().
            try:
                self._out_queue.cancel_join_thread()
            except Exception:
                pass
            for pool in (self._in_pool, self._out_pool):
                if pool is not None:
                    pool.close()

    def _publish_result(self, result: Any) -> None:
        """Push a result to the out_queue, replacing a stale unread one if drop_policy is not 'block'."""
        item = self._out_pool.encode(result) if self._out_pool is not None else result
        try:
            self._out_queue.put_nowait(item)
            return
        except queue.Full:
            pass
        except Exception:
            self._release_out(item)
            return

        # In lossless 'block' mode, never discard completed results; block until out_queue is read
        if self._drop_policy == "block":
            try:
                self._out_queue.put(item)
                return
            except Exception:
                self._release_out(item)
                return

        # Lossy modes (drop_oldest / drop_new): replace stale unread result
        try:
            self._release_out(self._out_queue.get_nowait())
        except (queue.Empty, Exception):
            pass
        try:
            self._out_queue.put_nowait(item)
        except (queue.Full, Exception):
            self._release_out(item)

    def _worker_loop(self) -> None:
        while True:
            if self._drain_ctrl_queue():
                return

            try:
                item = self._next_item(timeout=0.01)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                return

            # Params updates sent right before this item was submitted may not have been read yet
            # (the worker was blocked on the input queue): apply them before processing.
            if self._drain_ctrl_queue():
                self._release_in(item)
                return

            try:
                data = self._in_pool.decode(item) if self._in_pool is not None else item
                result = self._process_data(data, self.params)
                self._publish_result(result)
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
