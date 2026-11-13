"""
Transparent shared-memory transport for large numpy arrays crossing ProcessingBase queues.

Why
---
`multiprocessing.Queue` pickles every payload: a 640x480x3 frame is copied into a pickle buffer, pushed
through an OS pipe in chunks (many GIL release/re-acquire cycles), read back and unpickled. At high frame
rates this saturates the producer process (feeder threads + GIL contention) long before the worker does.

How
---
Each direction (main -> worker, worker -> main) owns a small pool of reusable shared-memory slots.

    producer: encode(obj)  -> every large C-compatible np.ndarray found in obj (also inside tuple / list /
                              dict, a few levels deep) is copied into a free slot and replaced by a tiny
                              ShmRef(slot, name, shape, dtype). Only this descriptor goes through the pipe.
    consumer: decode(obj)  -> arrays are copied out of their slots into regular numpy arrays and the slots
                              are released immediately (the consumer owns ordinary arrays, exactly as with
                              pickle: user code may keep references safely).
    dropped items          -> release_item(obj) frees their slots without reading them.

Everything is best-effort and transparent: small arrays, exotic dtypes, ndarray subclasses, other Python
objects, an exhausted pool or a memory cap reached simply fall back to the regular pickle path.

Slots are allocated lazily by the producing process and re-created (bigger) when a larger array shows up.
Slot ownership is tracked with a shared flag array: a slot is only reused once the consumer released it,
so the producer never overwrites data that is still being read.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import threading
from multiprocessing import shared_memory
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a hard dependency of the app, but stay safe
    np = None  # type: ignore[assignment]

SHM_AVAILABLE: bool = np is not None

_IS_POSIX = os.name == "posix"
_MAX_DEPTH = 4  # container nesting walked when looking for arrays
_MAX_CONTAINER_LEN = 64  # larger containers are left to pickle untouched
_ALLOWED_KINDS = frozenset("biufcmM")  # bool, int, uint, float, complex, timedelta, datetime


class ShmRef(NamedTuple):
    """Descriptor of an array stored in a shared-memory slot (what actually travels through the pipe)."""

    slot: int
    name: str
    shape: Tuple[int, ...]
    dtype: str


class ShmEnvelope:
    """Wraps a payload in which at least one array was moved to shared memory."""

    __slots__ = ("payload", "slots")

    def __init__(self, payload: Any, slots: List[int]) -> None:
        self.payload = payload
        self.slots = slots

    def __reduce__(self) -> Any:
        return (ShmEnvelope, (self.payload, self.slots))


def _round_up(n: int, block: int = 1 << 16) -> int:
    """Round allocation sizes up (64 KiB) so small size variations reuse the same segment."""
    return max(block, ((n + block - 1) // block) * block)


def _attach(name: str) -> shared_memory.SharedMemory:
    """Attach to an existing segment without letting this process' resource tracker own it."""
    try:
        return shared_memory.SharedMemory(name=name, track=False)  # Python >= 3.13
    except TypeError:
        shm = shared_memory.SharedMemory(name=name)
        if _IS_POSIX:  # pre-3.13 POSIX: the tracker would unlink the creator's segment on our exit
            try:
                from multiprocessing import resource_tracker

                resource_tracker.unregister(shm._name, "shared_memory")  # type: ignore[attr-defined]
            except Exception:
                pass
        return shm


def _close_quiet(shm: Optional[shared_memory.SharedMemory], unlink: bool = False) -> None:
    if shm is None:
        return
    try:
        shm.close()
    except Exception:
        pass
    if unlink and _IS_POSIX:
        try:
            shm.unlink()
        except Exception:
            pass


class ShmPool:
    """
    Pool of shared-memory slots for ONE direction (one producer process, one consumer process).

    Picklable at process-spawn time (shared flag array + lock); per-process handles are rebuilt locally.
    """

    def __init__(self, n_slots: int, threshold_bytes: int, max_total_bytes: int) -> None:
        self.n_slots = max(1, int(n_slots))
        self.threshold_bytes = max(1, int(threshold_bytes))
        self.max_total_bytes = max(0, int(max_total_bytes))
        self._flags = mp.RawArray("b", self.n_slots)  # 0 = free, 1 = in flight
        self._lock = mp.Lock()
        self._init_local()

    # ------------------------------------------------------------------ pickling / per-process state
    def _init_local(self) -> None:
        self._local_lock = threading.Lock()
        self._owned: Dict[int, Tuple[shared_memory.SharedMemory, int]] = {}  # producer side
        self._owned_bytes = 0
        self._attached: Dict[int, shared_memory.SharedMemory] = {}  # consumer side
        self._hint = 0

    def __getstate__(self) -> Dict[str, Any]:
        state = self.__dict__.copy()
        for key in ("_local_lock", "_owned", "_owned_bytes", "_attached", "_hint"):
            state.pop(key, None)
        return state

    def __setstate__(self, state: Dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._init_local()

    # ------------------------------------------------------------------ slot bookkeeping
    def _acquire_slot(self) -> int:
        flags = self._flags
        n = self.n_slots
        with self._lock:
            start = self._hint
            for k in range(n):
                i = (start + k) % n
                if flags[i] == 0:
                    flags[i] = 1
                    self._hint = (i + 1) % n
                    return i
        return -1

    def release(self, slot: int) -> None:
        if 0 <= slot < self.n_slots:
            self._flags[slot] = 0

    def release_item(self, obj: Any) -> None:
        """Free the slots of an item that will never be decoded (dropped / cleared)."""
        if type(obj) is ShmEnvelope:
            for s in obj.slots:
                self.release(s)

    def reset(self) -> None:
        """Mark every slot free (only when no item can be in flight anymore, e.g. worker stopped)."""
        with self._lock:
            for i in range(self.n_slots):
                self._flags[i] = 0

    def in_flight(self) -> int:
        return sum(1 for i in range(self.n_slots) if self._flags[i])

    # ------------------------------------------------------------------ producer side
    def _owned_segment(self, slot: int, nbytes: int) -> Optional[shared_memory.SharedMemory]:
        with self._local_lock:
            entry = self._owned.get(slot)
            if entry is not None and entry[1] >= nbytes:
                return entry[0]
            need = _round_up(nbytes)
            cur = entry[1] if entry is not None else 0
            if self._owned_bytes - cur + need > self.max_total_bytes:
                return None
            # The slot is ours (flag set) and was released by the consumer: safe to replace it.
            if entry is not None:
                _close_quiet(entry[0], unlink=True)
                self._owned_bytes -= cur
                del self._owned[slot]
            shm = shared_memory.SharedMemory(create=True, size=need)
            self._owned[slot] = (shm, need)
            self._owned_bytes += need
            return shm

    def _put_array(self, arr: Any) -> Optional[ShmRef]:
        slot = self._acquire_slot()
        if slot < 0:
            return None
        try:
            shm = self._owned_segment(slot, arr.nbytes)
            if shm is None:
                self.release(slot)
                return None
            dst = np.ndarray(arr.shape, dtype=arr.dtype, buffer=shm.buf)
            np.copyto(dst, arr, casting="no")
            del dst  # drop the buffer export so the segment can be closed later
            return ShmRef(slot, shm.name, tuple(arr.shape), arr.dtype.str)
        except Exception:
            self.release(slot)
            return None

    def _enc(self, x: Any, slots: List[int], depth: int) -> Any:
        t = type(x)
        if t is np.ndarray:
            if x.nbytes >= self.threshold_bytes and x.dtype.kind in _ALLOWED_KINDS and not x.dtype.hasobject:
                ref = self._put_array(x)
                if ref is not None:
                    slots.append(ref.slot)
                    return ref
            return x
        if depth >= _MAX_DEPTH:
            return x
        if t is tuple or t is list:
            if len(x) > _MAX_CONTAINER_LEN:
                return x
            changed = False
            items = []
            for v in x:
                nv = self._enc(v, slots, depth + 1)
                changed = changed or (nv is not v)
                items.append(nv)
            if not changed:
                return x
            return tuple(items) if t is tuple else items
        if t is dict:
            if len(x) > _MAX_CONTAINER_LEN:
                return x
            changed = False
            out: Dict[Any, Any] = {}
            for k, v in x.items():
                nv = self._enc(v, slots, depth + 1)
                changed = changed or (nv is not v)
                out[k] = nv
            return out if changed else x
        return x

    def encode(self, obj: Any) -> Any:
        """Move large arrays of `obj` to shared memory. Returns `obj` unchanged if nothing was moved."""
        if np is None:
            return obj
        slots: List[int] = []
        try:
            payload = self._enc(obj, slots, 0)
        except Exception:
            for s in slots:
                self.release(s)
            return obj
        if not slots:
            return obj
        return ShmEnvelope(payload, slots)

    # ------------------------------------------------------------------ consumer side
    def _read(self, ref: ShmRef) -> Any:
        with self._local_lock:
            shm = self._attached.get(ref.slot)
            if shm is None or shm.name != ref.name:
                _close_quiet(shm)
                shm = _attach(ref.name)
                self._attached[ref.slot] = shm
        view = np.ndarray(ref.shape, dtype=np.dtype(ref.dtype), buffer=shm.buf)
        out = view.copy()
        del view
        return out

    def _dec(self, x: Any) -> Any:
        t = type(x)
        if t is ShmRef:
            return self._read(x)
        if t is tuple:
            return tuple(self._dec(v) for v in x)
        if t is list:
            return [self._dec(v) for v in x]
        if t is dict:
            return {k: self._dec(v) for k, v in x.items()}
        return x

    def decode(self, obj: Any) -> Any:
        """Rebuild the original payload (arrays copied out) and release its slots."""
        if type(obj) is not ShmEnvelope:
            return obj
        try:
            return self._dec(obj.payload)
        finally:
            for s in obj.slots:
                self.release(s)

    # ------------------------------------------------------------------ teardown
    def close(self) -> None:
        """Close this process' handles (and unlink owned segments on POSIX)."""
        lock = getattr(self, "_local_lock", None)
        if lock is None:
            return
        with lock:
            for shm, _cap in self._owned.values():
                _close_quiet(shm, unlink=True)
            self._owned.clear()
            self._owned_bytes = 0
            for shm in self._attached.values():
                _close_quiet(shm)
            self._attached.clear()
