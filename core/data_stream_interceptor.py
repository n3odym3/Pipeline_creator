"""
Global Data Stream Interceptor and Universal I/O Codec for Tutorial Manager.

Provides non-intrusive runtime interception of module-to-module data transmissions
(`module.input_cb(*args, **kwargs)`), UI widget action scripting, module resolution,
and lossless serialization/deserialization across all 26 `IOTypes` defined in
`core/input_output_types.py`.
"""

from __future__ import annotations

import base64
import functools
import inspect
import io
import json
import os
import pickle
import sys
import threading
import uuid
import weakref
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from core.input_output_types import IOTypes


class UniversalDataCodec:
    """
    Universal serializer and deserializer for all `IOTypes` payloads passed
    through `module.input_cb(*args, **kwargs)`.

    Supports:
    - Tier 1 (JSON-native): TRIGGER, TEXT, NUMBER, POSITION, FILE_PATH, FOLDER_PATH,
      CMD_DICT, CMD_LIST, STATUS_DICT, SAMPLE, POINT_LIST, ROI_SAMPLE, COVERAGE, PLAYBACK.
    - Tier 2 (Tabular / Vectors): DATAFRAME (with `df.attrs`), DATALIST, VECTORS, CONTOURS.
    - Tier 3 (Binary / Tensors): FRAME, FRAME12, FRAME16, MASK, FRAME_MASK_PAIR, FRAME_LIST.
    - Tier 4 (Generic): ANY (via recursive structural encoding or base64 pickle fallback).
    """

    INLINE_NDARRAY_MAX_ELEMENTS: int = 5000

    def __init__(self) -> None:
        # In-memory cache mapping asset_id -> np.ndarray so unsaved tutorials replay with zero disk I/O
        self._memory_assets: Dict[str, np.ndarray] = {}

    def clear_memory_assets(self) -> None:
        """Clear cached in-memory binary assets."""
        self._memory_assets.clear()

    def flush_assets_to_dir(self, steps: List[Dict[str, Any]], tutorial_filepath: str | Path) -> None:
        """
        Write any in-memory or base64 ndarray assets referenced by `steps` into a companion
        `<tutorial_stem>_assets/` directory next to the saved tutorial JSON file.
        """
        tut_path = Path(tutorial_filepath).resolve()
        assets_dir_name = f"{tut_path.stem}_assets"
        assets_dir = tut_path.parent / assets_dir_name

        def _walk_and_flush(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("__pc_type__") == "ndarray_asset":
                    asset_id = node.get("asset_id", "")
                    arr = self._memory_assets.get(asset_id)
                    if arr is None and node.get("inline_b64"):
                        arr = self._decode_npz_b64(node["inline_b64"])
                        if arr is not None and asset_id:
                            self._memory_assets[asset_id] = arr

                    if arr is not None:
                        try:
                            assets_dir.mkdir(parents=True, exist_ok=True)
                            filename = f"{asset_id}.npz"
                            file_path = assets_dir / filename
                            np.savez_compressed(file_path, data=arr)
                            node["asset_file"] = f"{assets_dir_name}/{filename}"
                            # Keep file lightweight by removing inline_b64 once persisted on disk
                            node.pop("inline_b64", None)
                        except Exception as e:
                            logger.error(f"Failed to write tutorial asset {asset_id} to disk: {e}")
                    return

                for v in node.values():
                    _walk_and_flush(v)
            elif isinstance(node, list):
                for item in node:
                    _walk_and_flush(item)

        _walk_and_flush(steps)

    @staticmethod
    def _encode_npz_b64(arr: np.ndarray) -> str:
        buf = io.BytesIO()
        np.savez_compressed(buf, data=arr)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _decode_npz_b64(b64_str: str) -> Optional[np.ndarray]:
        try:
            raw = base64.b64decode(b64_str.encode("ascii"))
            buf = io.BytesIO(raw)
            with np.load(buf, allow_pickle=False) as npz:
                return np.array(npz["data"], copy=True)
        except Exception as e:
            logger.error(f"Failed to decode inline npz base64 array: {e}")
            return None

    def serialize_value(self, obj: Any) -> Any:
        """Recursively serialize an arbitrary Python value into a JSON-compatible structure."""
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj

        if isinstance(obj, IOTypes):
            return {"__pc_type__": "IOTypes", "value": obj.value}

        if isinstance(obj, str):
            return obj

        if isinstance(obj, np.generic):
            return {
                "__pc_type__": "np_scalar",
                "dtype": str(obj.dtype),
                "value": obj.item(),
            }

        if isinstance(obj, np.ndarray):
            if (
                obj.ndim <= 2
                and obj.size <= self.INLINE_NDARRAY_MAX_ELEMENTS
                and obj.dtype.kind in ("f", "i", "u", "b")
            ):
                return {
                    "__pc_type__": "ndarray_inline",
                    "dtype": str(obj.dtype),
                    "shape": list(obj.shape),
                    "data": obj.tolist(),
                }
            asset_id = f"arr_{uuid.uuid4().hex[:12]}"
            arr_copy = np.array(obj, copy=True)
            self._memory_assets[asset_id] = arr_copy
            return {
                "__pc_type__": "ndarray_asset",
                "asset_id": asset_id,
                "dtype": str(obj.dtype),
                "shape": list(obj.shape),
                "asset_file": None,
                "inline_b64": self._encode_npz_b64(arr_copy),
            }

        if isinstance(obj, Path):
            return {"__pc_type__": "Path", "path": str(obj)}

        if isinstance(obj, (bytes, bytearray)):
            return {
                "__pc_type__": "bytes",
                "b64": base64.b64encode(bytes(obj)).decode("ascii"),
            }

        if isinstance(obj, tuple):
            return {
                "__pc_type__": "tuple",
                "items": [self.serialize_value(item) for item in obj],
            }

        if isinstance(obj, set):
            return {
                "__pc_type__": "set",
                "items": [self.serialize_value(item) for item in obj],
            }

        if isinstance(obj, list):
            return [self.serialize_value(item) for item in obj]

        if isinstance(obj, dict):
            if "__pc_type__" in obj:
                return {
                    "__pc_type__": "escaped_dict",
                    "entries": [[self.serialize_value(k), self.serialize_value(v)] for k, v in obj.items()],
                }
            return {str(k): self.serialize_value(v) for k, v in obj.items()}

        # Check for pandas DataFrame / Series lazily
        cls_name = obj.__class__.__name__
        mod_name = getattr(obj.__class__, "__module__", "")
        if "pandas" in mod_name:
            if cls_name == "DataFrame":
                try:
                    return {
                        "__pc_type__": "dataframe",
                        "split_dict": self.serialize_value(obj.to_dict(orient="split")),
                        "dtypes": {str(col): str(dt) for col, dt in obj.dtypes.items()},
                        "index_name": self.serialize_value(obj.index.name),
                        "attrs": self.serialize_value(dict(getattr(obj, "attrs", {}) or {})),
                    }
                except Exception as e:
                    logger.warning(f"DataFrame split serialization fallback to pickle: {e}")
            elif cls_name == "Series":
                try:
                    return {
                        "__pc_type__": "series",
                        "data": self.serialize_value(obj.tolist()),
                        "index": self.serialize_value(obj.index.tolist()),
                        "dtype": str(obj.dtype),
                        "name": self.serialize_value(obj.name),
                        "attrs": self.serialize_value(dict(getattr(obj, "attrs", {}) or {})),
                    }
                except Exception as e:
                    logger.warning(f"Series serialization fallback to pickle: {e}")

        # Fallback for custom Python objects (IOTypes.ANY)
        try:
            raw_pickle = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
            return {
                "__pc_type__": "pickle_b64",
                "b64": base64.b64encode(raw_pickle).decode("ascii"),
                "repr": repr(obj)[:200],
            }
        except Exception as e:
            logger.warning(f"Could not pickle custom object {type(obj)}, storing string repr: {e}")
            return repr(obj)

    def deserialize_value(self, data: Any, tutorial_dir: Optional[Path] = None) -> Any:
        """Recursively reconstruct a Python object from its JSON-serialized representation."""
        if data is None or isinstance(data, (bool, int, float, str)):
            return data

        if isinstance(data, list):
            return [self.deserialize_value(item, tutorial_dir=tutorial_dir) for item in data]

        if isinstance(data, dict):
            pc_type = data.get("__pc_type__")
            if not pc_type:
                return {k: self.deserialize_value(v, tutorial_dir=tutorial_dir) for k, v in data.items()}

            if pc_type == "IOTypes":
                val_str = data.get("value", "")
                return IOTypes.get_by_value(val_str) or val_str

            if pc_type == "np_scalar":
                dtype = np.dtype(data.get("dtype", "float64"))
                return dtype.type(data.get("value", 0))

            if pc_type == "ndarray_inline":
                dtype = np.dtype(data.get("dtype", "float64"))
                arr = np.array(data.get("data", []), dtype=dtype)
                shape = data.get("shape")
                if shape:
                    try:
                        arr = arr.reshape(shape)
                    except Exception:
                        pass
                return arr

            if pc_type == "ndarray_asset":
                asset_id = data.get("asset_id", "")
                if asset_id and asset_id in self._memory_assets:
                    return np.array(self._memory_assets[asset_id], copy=True)

                asset_file = data.get("asset_file")
                if asset_file:
                    candidates = []
                    if tutorial_dir:
                        candidates.append(tutorial_dir / asset_file)
                    from core.paths import TUTORIALS_DIR
                    candidates.append(TUTORIALS_DIR / asset_file)
                    for cand in candidates:
                        if cand.exists() and cand.is_file():
                            try:
                                with np.load(cand, allow_pickle=False) as npz:
                                    arr = np.array(npz["data"], copy=True)
                                    if asset_id:
                                        self._memory_assets[asset_id] = arr
                                    return np.array(arr, copy=True)
                            except Exception as e:
                                logger.error(f"Failed loading ndarray asset file {cand}: {e}")

                if data.get("inline_b64"):
                    arr = self._decode_npz_b64(data["inline_b64"])
                    if arr is not None:
                        if asset_id:
                            self._memory_assets[asset_id] = arr
                        return np.array(arr, copy=True)

                # Last resort empty array with expected shape & dtype
                shape = data.get("shape", [1])
                dtype = np.dtype(data.get("dtype", "uint8"))
                return np.zeros(shape, dtype=dtype)

            if pc_type == "Path":
                return Path(data.get("path", ""))

            if pc_type == "bytes":
                return base64.b64decode(data.get("b64", "").encode("ascii"))

            if pc_type == "tuple":
                return tuple(
                    self.deserialize_value(item, tutorial_dir=tutorial_dir)
                    for item in data.get("items", [])
                )

            if pc_type == "set":
                return set(
                    self.deserialize_value(item, tutorial_dir=tutorial_dir)
                    for item in data.get("items", [])
                )

            if pc_type == "escaped_dict":
                return {
                    self.deserialize_value(k, tutorial_dir=tutorial_dir): self.deserialize_value(
                        v, tutorial_dir=tutorial_dir
                    )
                    for k, v in data.get("entries", [])
                }

            if pc_type == "dataframe":
                try:
                    import pandas as pd

                    split_dict = self.deserialize_value(data.get("split_dict", {}), tutorial_dir=tutorial_dir)
                    df = pd.DataFrame(
                        data=split_dict.get("data", []),
                        index=split_dict.get("index", []),
                        columns=split_dict.get("columns", []),
                    )
                    for col, dt_str in (data.get("dtypes") or {}).items():
                        if col in df.columns:
                            try:
                                df[col] = df[col].astype(dt_str)
                            except Exception:
                                pass
                    df.index.name = self.deserialize_value(data.get("index_name"), tutorial_dir=tutorial_dir)
                    attrs = self.deserialize_value(data.get("attrs", {}), tutorial_dir=tutorial_dir)
                    if isinstance(attrs, dict):
                        df.attrs.update(attrs)
                    return df
                except Exception as e:
                    logger.error(f"Failed to deserialize DataFrame: {e}")
                    return {}

            if pc_type == "series":
                try:
                    import pandas as pd

                    s = pd.Series(
                        data=self.deserialize_value(data.get("data", []), tutorial_dir=tutorial_dir),
                        index=self.deserialize_value(data.get("index", []), tutorial_dir=tutorial_dir),
                        dtype=data.get("dtype", None),
                        name=self.deserialize_value(data.get("name"), tutorial_dir=tutorial_dir),
                    )
                    attrs = self.deserialize_value(data.get("attrs", {}), tutorial_dir=tutorial_dir)
                    if isinstance(attrs, dict):
                        s.attrs.update(attrs)
                    return s
                except Exception as e:
                    logger.error(f"Failed to deserialize Series: {e}")
                    return []

            if pc_type == "pickle_b64":
                try:
                    raw = base64.b64decode(data.get("b64", "").encode("ascii"))
                    return pickle.loads(raw)
                except Exception as e:
                    logger.error(f"Failed to deserialize pickled object: {e}")
                    return data.get("repr", None)

        return data

    def serialize_call(self, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Serialize positional `args` and keyword `kwargs` passed to `input_cb(*args, **kwargs)`,
        deduplicating shared object references (e.g. `input_cb(sample=s, data=s)`).
        """
        seen_top_level: Dict[int, str] = {}
        serialized_args: List[Any] = []
        serialized_kwargs: Dict[str, Any] = {}

        for idx, arg in enumerate(args):
            obj_id = id(arg)
            if isinstance(arg, (dict, list, np.ndarray)) and obj_id in seen_top_level:
                serialized_args.append({"__pc_type__": "arg_ref", "target": seen_top_level[obj_id]})
            else:
                if isinstance(arg, (dict, list, np.ndarray)):
                    seen_top_level[obj_id] = f"arg:{idx}"
                serialized_args.append(self.serialize_value(arg))

        for k, v in kwargs.items():
            obj_id = id(v)
            if isinstance(v, (dict, list, np.ndarray)) and obj_id in seen_top_level:
                serialized_kwargs[k] = {"__pc_type__": "arg_ref", "target": seen_top_level[obj_id]}
            else:
                if isinstance(v, (dict, list, np.ndarray)):
                    seen_top_level[obj_id] = f"kwarg:{k}"
                serialized_kwargs[k] = self.serialize_value(v)

        return {
            "args": serialized_args,
            "kwargs": serialized_kwargs,
        }

    def deserialize_call(
        self, payload: Dict[str, Any], tutorial_dir: Optional[Path] = None
    ) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
        """
        Reconstruct fresh `(args, kwargs)` ready to be passed to `target_module.input_cb(*args, **kwargs)`.
        Resolves any `arg_ref` shared references within the call.
        """
        raw_args = payload.get("args", [])
        raw_kwargs = payload.get("kwargs", {})

        resolved_map: Dict[str, Any] = {}
        out_args: List[Any] = []
        out_kwargs: Dict[str, Any] = {}

        for idx, item in enumerate(raw_args):
            if isinstance(item, dict) and item.get("__pc_type__") == "arg_ref":
                out_args.append(item)
            else:
                val = self.deserialize_value(item, tutorial_dir=tutorial_dir)
                resolved_map[f"arg:{idx}"] = val
                out_args.append(val)

        for k, item in raw_kwargs.items():
            if isinstance(item, dict) and item.get("__pc_type__") == "arg_ref":
                out_kwargs[k] = item
            else:
                val = self.deserialize_value(item, tutorial_dir=tutorial_dir)
                resolved_map[f"kwarg:{k}"] = val
                out_kwargs[k] = val

        # Second pass for shared references
        for idx, item in enumerate(out_args):
            if isinstance(item, dict) and item.get("__pc_type__") == "arg_ref":
                target_key = item.get("target", "")
                out_args[idx] = resolved_map.get(target_key)

        for k, item in list(out_kwargs.items()):
            if isinstance(item, dict) and item.get("__pc_type__") == "arg_ref":
                target_key = item.get("target", "")
                out_kwargs[k] = resolved_map.get(target_key)

        return tuple(out_args), out_kwargs


def infer_io_type(
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
    sender_mod: Any = None,
    target_mod: Any = None,
) -> str:
    """Infer the `IOTypes` string value for an `input_cb(*args, **kwargs)` invocation."""
    dt = kwargs.get("data_type")
    if dt is not None:
        val_str = str(dt.value) if hasattr(dt, "value") else str(dt)
        matched = IOTypes.get_by_value(val_str)
        if matched is not None:
            return matched.value
        return val_str

    if "sample" in kwargs and isinstance(kwargs["sample"], dict):
        if "rect" in kwargs["sample"]:
            return IOTypes.ROI_SAMPLE.value
        return IOTypes.SAMPLE.value

    if "cmd" in kwargs and isinstance(kwargs["cmd"], dict):
        return IOTypes.CMD_DICT.value

    if "folder_path" in kwargs:
        return IOTypes.FOLDER_PATH.value

    if "path" in kwargs:
        p = kwargs["path"]
        if isinstance(p, str) and os.path.isdir(p):
            return IOTypes.FOLDER_PATH.value
        return IOTypes.FILE_PATH.value

    primary = kwargs.get("data") if "data" in kwargs else (args[0] if args else None)

    if primary is None:
        return IOTypes.TRIGGER.value

    if isinstance(primary, np.ndarray):
        if primary.dtype == np.uint16:
            return IOTypes.FRAME16.value
        return IOTypes.FRAME.value

    if isinstance(primary, tuple) and len(primary) == 2:
        if isinstance(primary[0], np.ndarray) and isinstance(primary[1], np.ndarray):
            return IOTypes.FRAME_MASK_PAIR.value
        if isinstance(primary[0], np.ndarray) and isinstance(primary[1], str):
            return IOTypes.FRAME.value
        if isinstance(primary[0], str) and isinstance(primary[1], str):
            return IOTypes.FILE_PATH.value

    if isinstance(primary, dict):
        if "x" in primary and "y" in primary:
            return IOTypes.SAMPLE.value
        if "rect" in primary:
            return IOTypes.ROI_SAMPLE.value
        if "angles" in primary or "magnitudes" in primary:
            return IOTypes.VECTORS.value
        if "frame_idx" in primary and "total_frames" in primary:
            return IOTypes.PLAYBACK.value
        return IOTypes.CMD_DICT.value

    if isinstance(primary, list):
        if primary and isinstance(primary[0], dict) and "image" in primary[0]:
            return IOTypes.FRAME_LIST.value
        return IOTypes.DATALIST.value

    if isinstance(primary, (int, float)):
        return IOTypes.NUMBER.value

    if isinstance(primary, str):
        return IOTypes.TEXT.value

    cls_name = primary.__class__.__name__
    if cls_name == "DataFrame":
        return IOTypes.DATAFRAME.value

    # Check sender outputs if single output exists
    if sender_mod is not None:
        outputs = getattr(sender_mod, "outputs", {})
        if isinstance(outputs, dict) and len(outputs) == 1:
            out_val = next(iter(outputs.values()))
            return str(out_val.value) if hasattr(out_val, "value") else str(out_val)

    return IOTypes.ANY.value


def summarize_payload(io_type: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
    """Build a short human-readable summary of a captured data transmission."""
    try:
        if io_type in (IOTypes.SAMPLE.value, IOTypes.ROI_SAMPLE.value):
            s = kwargs.get("sample") or kwargs.get("data") or (args[0] if args else {})
            if isinstance(s, dict):
                name = s.get("name", "Sample")
                action = s.get("action", "select")
                y = s.get("y")
                pts = len(y) if hasattr(y, "__len__") else 0
                return f"{io_type.upper()} '{name}' ({pts} pts, action={action})"

        primary = kwargs.get("data") if "data" in kwargs else (
            kwargs.get("cmd") if "cmd" in kwargs else (
                kwargs.get("path") if "path" in kwargs else (args[0] if args else None)
            )
        )

        if isinstance(primary, np.ndarray):
            return f"{io_type.upper()} shape={list(primary.shape)} dtype={primary.dtype}"
        if isinstance(primary, tuple) and primary and isinstance(primary[0], np.ndarray):
            return f"{io_type.upper()} tuple(shape={list(primary[0].shape)}, ...)"
        if isinstance(primary, dict):
            keys = list(primary.keys())[:4]
            return f"{io_type.upper()} keys={keys}"
        if isinstance(primary, (str, int, float, bool)):
            s_val = str(primary)
            if len(s_val) > 40:
                s_val = s_val[:37] + "..."
            return f"{io_type.upper()} ({s_val})"
        if primary is not None and primary.__class__.__name__ == "DataFrame":
            return f"DATAFRAME ({primary.shape[0]}x{primary.shape[1]})"
    except Exception:
        pass
    return f"{io_type.upper()}"


def build_module_selector(module: Any) -> Dict[str, str]:
    """Create a multi-key semantic identifier dictionary for a module instance."""
    if module is None:
        return {}
    return {
        "uuid": str(getattr(module, "UUID", "")),
        "class_name": module.__class__.__name__,
        "module_path": module.__class__.__module__.replace("modules.", ""),
        "label": str(getattr(module, "label", "")),
        "win_id": str(getattr(module, "winID", "")),
    }


def resolve_module_selector(selector: Optional[Dict[str, Any]]) -> Optional[Any]:
    """
    Resolve a `module_selector` dictionary to a live module instance in `MODULES_REGISTRY`.

    Resolution hierarchy:
    1. Exact UUID + Class match
    2. Exact UUID match
    3. Class Name + Label match
    4. Unique Class Name match
    5. Exact Label match
    """
    if not selector or not isinstance(selector, dict):
        return None

    from core.module_registry import MODULES_REGISTRY

    req_uuid = str(selector.get("uuid", "")).strip()
    req_class = str(selector.get("class_name", "")).strip()
    req_label = str(selector.get("label", "")).strip()

    # 1 & 2. Direct UUID lookup
    if req_uuid and req_uuid in MODULES_REGISTRY:
        cand = MODULES_REGISTRY[req_uuid]
        if not req_class or cand.__class__.__name__ == req_class:
            return cand

    # 3. Class Name + Label match
    if req_class and req_label:
        for mod in MODULES_REGISTRY.values():
            if mod.__class__.__name__ == req_class and str(getattr(mod, "label", "")) == req_label:
                return mod

    # 4. Class Name match
    if req_class:
        for mod in MODULES_REGISTRY.values():
            if mod.__class__.__name__ == req_class:
                return mod

    # 5. Label match
    if req_label:
        for mod in MODULES_REGISTRY.values():
            if str(getattr(mod, "label", "")) == req_label:
                return mod

    return None


def find_module_for_dpg_item(item_id: Any, path_list: Optional[List[Dict[str, Any]]] = None) -> Tuple[Optional[Any], str]:
    """
    Given a clicked DearPyGui item ID and its path list, locate the owning module instance
    and the bound callback method name (if any).
    """
    from core.module_registry import MODULES_REGISTRY

    method_name = ""
    owner_mod = None

    # 1. Inspect DPG item callback directly (most accurate: works even for merged windows!)
    if item_id and dpg.does_item_exist(item_id):
        try:
            cfg = dpg.get_item_configuration(item_id)
            cb = cfg.get("callback")
            if cb is not None:
                cb_self = getattr(cb, "__self__", None)
                cb_name = getattr(cb, "__name__", "")
                if cb_self is not None and hasattr(cb_self, "UUID"):
                    owner_mod = cb_self
                    method_name = cb_name
        except Exception:
            pass

    # 2. Check item alias suffix `_{UUID}`
    if owner_mod is None and item_id and dpg.does_item_exist(item_id):
        try:
            alias = dpg.get_item_alias(item_id) or str(item_id)
            for mod_uuid, mod in MODULES_REGISTRY.items():
                if alias.endswith(f"_{mod_uuid}") or f"_{mod_uuid}_" in alias:
                    owner_mod = mod
                    break
        except Exception:
            pass

    # 3. Check root window from path_list
    if owner_mod is None and path_list:
        root = path_list[0]
        root_alias = str(root.get("alias") or root.get("tag") or "")
        root_label = str(root.get("label") or "")
        for mod_uuid, mod in MODULES_REGISTRY.items():
            if getattr(mod, "winID", "") == root_alias or root_alias.endswith(f"_{mod_uuid}"):
                owner_mod = mod
                break
        if owner_mod is None and root_label:
            for mod in MODULES_REGISTRY.values():
                if getattr(mod, "label", "") == root_label:
                    owner_mod = mod
                    break

    return owner_mod, method_name


class DataStreamInterceptor:
    """
    Intercepts `module.input_cb(*args, **kwargs)` across all active modules in `MODULES_REGISTRY`.
    Coordinates with `TutorialManager` to capture root data flows during recording and
    to inject or substitute recorded payloads during tutorial playback.
    """

    def __init__(self) -> None:
        self.codec = UniversalDataCodec()
        self.is_active: bool = False
        self._tls = threading.local()
        self._hooked_modules: weakref.WeakSet[Any] = weakref.WeakSet()
        self._lock = threading.RLock()

    @property
    def _call_depth(self) -> int:
        return getattr(self._tls, "call_depth", 0)

    @_call_depth.setter
    def _call_depth(self, value: int) -> None:
        self._tls.call_depth = max(0, value)

    @property
    def _in_direct_injection(self) -> bool:
        return getattr(self._tls, "in_direct_injection", False)

    @_in_direct_injection.setter
    def _in_direct_injection(self, value: bool) -> None:
        self._tls.in_direct_injection = bool(value)

    def hook_module(self, module: Any) -> None:
        """Wrap `module.input_cb` with the non-intrusive interceptor hook if not already wrapped."""
        if module is None or not hasattr(module, "input_cb"):
            return
        if getattr(module, "_pc_input_cb_hooked", False):
            return

        orig_cb = getattr(module, "input_cb", None)
        if not callable(orig_cb):
            return

        mod_ref = weakref.ref(module)

        @functools.wraps(orig_cb)
        def _intercepted_input_cb(*args: Any, **kwargs: Any) -> Any:
            if not self.is_active:
                return orig_cb(*args, **kwargs)

            target_instance = mod_ref()
            if target_instance is None:
                return orig_cb(*args, **kwargs)

            return self._handle_call(target_instance, orig_cb, args, kwargs)

        try:
            object.__setattr__(module, "_pc_orig_input_cb", orig_cb)
            object.__setattr__(module, "input_cb", _intercepted_input_cb)
            object.__setattr__(module, "_pc_input_cb_hooked", True)
            self._hooked_modules.add(module)
        except Exception as e:
            logger.debug(f"Could not hook input_cb on {module}: {e}")

    def ensure_all_hooked(self) -> None:
        """Ensure every currently registered module in `MODULES_REGISTRY` has its `input_cb` hooked."""
        from core.module_registry import MODULES_REGISTRY

        for mod in list(MODULES_REGISTRY.values()):
            self.hook_module(mod)

    def _find_sender_from_stack(self, target_module: Any) -> Tuple[Optional[Any], str]:
        """
        Walk up the Python call stack to identify the upstream module instance and method
        that called `target_module.input_cb(...)`.
        """
        from core.module_registry import MODULES_REGISTRY

        try:
            frame = sys._getframe(2)
            while frame is not None:
                self_obj = frame.f_locals.get("self")
                if self_obj is not None and self_obj is not target_module:
                    uid = getattr(self_obj, "UUID", None)
                    if uid and uid in MODULES_REGISTRY:
                        return self_obj, frame.f_code.co_name
                frame = frame.f_back
        except Exception:
            pass
        return None, ""

    def _handle_call(
        self,
        target_module: Any,
        orig_cb: Callable[..., Any],
        args: Tuple[Any, ...],
        kwargs: Dict[str, Any],
    ) -> Any:
        """Handle an intercepted `target_module.input_cb(*args, **kwargs)` call."""
        from core.tutorial_manager import tutorial_manager

        # If TutorialManager is directly injecting a recorded payload, pass straight through
        if self._in_direct_injection:
            self._call_depth += 1
            try:
                return orig_cb(*args, **kwargs)
            finally:
                self._call_depth -= 1

        depth = self._call_depth
        self._call_depth = depth + 1
        try:
            # Only intercept Root Data Transmissions (depth == 0 before increment)
            # Downstream cascades (depth > 0) happen naturally as a result of the root call
            if depth == 0:
                sender_mod, sender_method = self._find_sender_from_stack(target_module)

                if tutorial_manager.is_recording and getattr(tutorial_manager, "record_data_enabled", True):
                    self._capture_for_recording(
                        tutorial_manager, sender_mod, sender_method, target_module, args, kwargs
                    )

                elif tutorial_manager.is_playing:
                    substituted = tutorial_manager.on_live_data_during_playback(
                        target_module, sender_mod, args, kwargs
                    )
                    if substituted is not None:
                        sub_args, sub_kwargs = substituted
                        return orig_cb(*sub_args, **sub_kwargs)

            return orig_cb(*args, **kwargs)
        finally:
            self._call_depth = depth

    def _capture_for_recording(
        self,
        tutorial_manager: Any,
        sender_mod: Optional[Any],
        sender_method: str,
        target_module: Any,
        args: Tuple[Any, ...],
        kwargs: Dict[str, Any],
    ) -> None:
        """Serialize the root `input_cb` call and forward the packet to `TutorialManager`."""
        try:
            io_type = infer_io_type(args, kwargs, sender_mod=sender_mod, target_mod=target_module)
            summary = summarize_payload(io_type, args, kwargs)
            serialized_payload = self.codec.serialize_call(args, kwargs)

            packet = {
                "sender_selector": build_module_selector(sender_mod),
                "sender_method": sender_method,
                "target_selector": build_module_selector(target_module),
                "io_type": io_type,
                "summary": summary,
                "payload": serialized_payload,
            }
            tutorial_manager.on_data_flow_captured(packet, sender_mod, sender_method, target_module)
        except Exception as e:
            logger.error(f"Error capturing data flow for tutorial: {e}")

    def inject_data_packet(
        self, packet: Dict[str, Any], tutorial_dir: Optional[Path] = None
    ) -> bool:
        """
        Reconstruct `(args, kwargs)` from a recorded `packet` and invoke `input_cb`
        on the target module resolved from `packet["target_selector"]`.
        """
        target_selector = packet.get("target_selector")
        target_mod = resolve_module_selector(target_selector)
        if target_mod is None or not hasattr(target_mod, "input_cb"):
            logger.warning(f"Tutorial replay: target module not found for selector {target_selector}")
            return False

        payload = packet.get("payload")
        if not isinstance(payload, dict):
            return False

        try:
            args, kwargs = self.codec.deserialize_call(payload, tutorial_dir=tutorial_dir)
            self._in_direct_injection = True
            try:
                target_mod.input_cb(*args, **kwargs)
            finally:
                self._in_direct_injection = False
            logger.info(
                f"Tutorial replay: injected {packet.get('io_type', 'data')} into '{getattr(target_mod, 'label', target_mod)}'"
            )
            return True
        except Exception as e:
            logger.error(f"Tutorial replay: failed injecting data into {target_mod}: {e}")
            return False

    def execute_ui_action(self, step: Dict[str, Any]) -> bool:
        """
        Programmatically execute a recorded UI widget interaction (`action_meta` / `path`)
        such as clicking 'Clear samples', toggling a checkbox, selecting a combo item,
        or changing a slider/input value.
        """
        from core.tutorial_manager import tutorial_manager

        action_meta = step.get("action_meta") or {}
        path_list = step.get("path") or []
        target_item = tutorial_manager.resolve_item_path(path_list) if path_list else None

        has_value = "value" in action_meta and action_meta["value"] is not None
        rec_value = action_meta.get("value")
        widget_type = str(action_meta.get("widget_type", ""))

        # 1. If the DPG widget exists, update its value (for non-buttons) and invoke its DPG callback
        if target_item and dpg.does_item_exist(target_item):
            try:
                info = dpg.get_item_info(target_item)
                actual_type = info.get("type", widget_type)
                is_button = "mvButton" in actual_type or "mvMenuItem" in actual_type

                if not is_button and has_value:
                    try:
                        dpg.set_value(target_item, rec_value)
                    except Exception:
                        pass

                cfg = dpg.get_item_configuration(target_item)
                cb = cfg.get("callback")
                user_data = cfg.get("user_data")
                app_data = rec_value if (not is_button and has_value) else (
                    None if is_button else dpg.get_value(target_item)
                )

                if callable(cb):
                    self._invoke_dpg_callback(cb, target_item, app_data, user_data)
                    logger.info(f"Tutorial replay: executed widget callback on '{action_meta.get('widget_label') or target_item}'")
                    return True
            except Exception as e:
                logger.warning(f"Tutorial replay: DPG widget callback failed, trying module fallback: {e}")

        # 2. Fallback: invoke bound method directly on the target module instance
        target_selector = action_meta.get("target_selector")
        method_name = action_meta.get("method_name")
        if target_selector and method_name:
            mod = resolve_module_selector(target_selector)
            if mod is not None and hasattr(mod, method_name):
                fn = getattr(mod, method_name)
                if callable(fn):
                    try:
                        self._invoke_dpg_callback(fn, target_item, rec_value, None)
                        logger.info(f"Tutorial replay: executed {method_name} on '{getattr(mod, 'label', mod)}'")
                        return True
                    except Exception as e:
                        logger.error(f"Tutorial replay: failed calling {method_name} on {mod}: {e}")

        return False

    @staticmethod
    def _invoke_dpg_callback(cb: Callable[..., Any], sender: Any, app_data: Any, user_data: Any) -> Any:
        """Safely invoke a DearPyGui callback regardless of its parameter arity."""
        try:
            sig = inspect.signature(cb)
            params = list(sig.parameters.values())
            has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            pos_params = [
                p for p in params
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            if has_varargs or len(pos_params) >= 3:
                return cb(sender, app_data, user_data)
            if len(pos_params) == 2:
                return cb(sender, app_data)
            if len(pos_params) == 1:
                return cb(sender)
            return cb()
        except TypeError:
            try:
                return cb(sender, app_data, user_data)
            except TypeError:
                try:
                    return cb(sender, app_data)
                except TypeError:
                    return cb()


# Singleton instance
data_stream_interceptor = DataStreamInterceptor()
