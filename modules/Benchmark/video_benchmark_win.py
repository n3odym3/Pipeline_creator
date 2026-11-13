"""
Video Benchmark Window Module (High-Performance Synthetic Camera / Video Stream Mockup).

Designed specifically for stress-testing pipeline throughput, frame pacing, latency,
and backpressure handling with zero-allocation ring-buffered generation.
Supports all visual IOTypes from core/input_output_types.py:
  - FRAME (8-bit uint8 BGR/Gray/RGBA, with optional accessory name tuple)
  - FRAME12 (12-bit uint16 HDR, range 0..4095)
  - FRAME16 (16-bit uint16 HDR, range 0..65535)
  - MASK (Binary/Grayscale ROI mask, uint8 0/255)
  - FRAME_MASK_PAIR (Combined (frame, mask) tuple for segmentation and overlay testing)
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class VideoBenchmark_win(WindowBase):
    """
    High-Performance Video Benchmark & Stream Mockup.

    Emits pre-rendered synthetic video frames across configurable resolutions,
    bit-depths (8-bit, 12-bit HDR, 16-bit HDR, Masks, Frame-Mask pairs),
    color spaces, and framerates (up to 500+ FPS or uncapped maximum throughput)
    with zero per-frame heap allocations.
    """

    label: str = "Video Benchmark"
    win_width: int = display_scaling.scale(380)
    win_height: int = display_scaling.scale(560)
    description: str = "Zero-allocation high-throughput synthetic video stream generator for benchmarking."

    accepted_input_types: list[type] = [
        IOTypes.TRIGGER,
        IOTypes.CMD_DICT,
        IOTypes.NUMBER,
        IOTypes.ANY,
    ]
    outputs: dict[str, type] = {
        "Frame": IOTypes.FRAME,
        "Trigger": IOTypes.TRIGGER,
    }
    persistent_fields: list[str] = [
        "label",
        "frame_type",
        "attach_stream_name",
        "stream_name",
        "universal_any_output",
        "resolution_preset",
        "custom_width",
        "custom_height",
        "color_format",
        "pattern",
        "fps_target",
        "uncapped_speed",
        "burst_count",
        "respect_backpressure",
        "auto_start",
    ]

    _FRAME_TYPES: Dict[str, IOTypes] = {
        "FRAME (8-bit)": IOTypes.FRAME,
        "FRAME12 (12-bit HDR)": IOTypes.FRAME12,
        "FRAME16 (16-bit HDR)": IOTypes.FRAME16,
        "MASK (Binary ROI)": IOTypes.MASK,
        "FRAME_MASK_PAIR": IOTypes.FRAME_MASK_PAIR,
    }

    _PRESETS: Dict[str, Tuple[int, int]] = {
        "QVGA (320x240)": (240, 320),
        "VGA (640x480)": (480, 640),
        "HD (1280x720)": (720, 1280),
        "FHD (1920x1080)": (1080, 1920),
        "2K QHD (2560x1440)": (1440, 2560),
        "4K UHD (3840x2160)": (2160, 3840),
        "Custom": (480, 640),
    }

    _PATTERNS: List[str] = [
        "Gradient + Scanline",
        "Bouncing Box",
        "Color Cycle",
        "Solid Test Card",
        "Static Noise",
    ]

    _FORMATS: List[str] = [
        "BGR (3 ch)",
        "Grayscale (1 ch)",
        "RGBA (4 ch)",
    ]

    # Persistent defaults
    frame_type: str = "FRAME (8-bit)"
    attach_stream_name: bool = True
    stream_name: str = "BenchCam"
    universal_any_output: bool = False

    resolution_preset: str = "VGA (640x480)"
    custom_width: int = 640
    custom_height: int = 480
    color_format: str = "BGR (3 ch)"
    pattern: str = "Gradient + Scanline"
    fps_target: float = 60.0
    uncapped_speed: bool = False
    burst_count: int = 100
    respect_backpressure: bool = False
    auto_start: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.stream_name = str(getattr(self, "stream_name", "BenchCam") or f"Stream_{self.UUID[:6]}")
        self.fps_target = max(1.0, float(getattr(self, "fps_target", 60.0)))
        self.burst_count = max(1, int(getattr(self, "burst_count", 100) or 100))
        self.custom_width = max(16, int(getattr(self, "custom_width", 640)))
        self.custom_height = max(16, int(getattr(self, "custom_height", 480)))
        self.frame_type = str(getattr(self, "frame_type", "FRAME (8-bit)"))
        if self.frame_type not in self._FRAME_TYPES:
            self.frame_type = "FRAME (8-bit)"

        # DPG widget tags
        self.btn_toggle_tag = f"vbench_btn_toggle_{self.UUID}"
        self.btn_step_tag = f"vbench_btn_step_{self.UUID}"
        self.btn_burst_tag = f"vbench_btn_burst_{self.UUID}"
        self.burst_count_tag = f"vbench_burst_cnt_{self.UUID}"
        self.frame_type_combo_tag = f"vbench_type_{self.UUID}"
        self.attach_name_chk_tag = f"vbench_att_name_{self.UUID}"
        self.stream_name_in_tag = f"vbench_st_name_{self.UUID}"
        self.universal_chk_tag = f"vbench_univ_{self.UUID}"
        self.fps_slider_tag = f"vbench_fps_slider_{self.UUID}"
        self.uncapped_tag = f"vbench_uncapped_{self.UUID}"
        self.preset_combo_tag = f"vbench_preset_{self.UUID}"
        self.custom_group_tag = f"vbench_custom_grp_{self.UUID}"
        self.w_input_tag = f"vbench_w_in_{self.UUID}"
        self.h_input_tag = f"vbench_h_in_{self.UUID}"
        self.format_combo_tag = f"vbench_format_{self.UUID}"
        self.pattern_combo_tag = f"vbench_pattern_{self.UUID}"
        self.backpressure_tag = f"vbench_backpress_{self.UUID}"
        self.txt_status_tag = f"vbench_txt_status_{self.UUID}"
        self.txt_rate_tag = f"vbench_txt_rate_{self.UUID}"
        self.txt_bandwidth_tag = f"vbench_txt_bw_{self.UUID}"
        self.txt_type_info_tag = f"vbench_txt_tinfo_{self.UUID}"

        # State & Threading
        self._running: bool = False
        self._is_bursting: bool = False
        self._is_closing: bool = False
        self._thread: Optional[threading.Thread] = None
        self._burst_thread: Optional[threading.Thread] = None
        self._burst_stop_event = threading.Event()
        self._lock = threading.Lock()

        # Telemetry
        self.total_emitted: int = 0
        self.dropped_count: int = 0
        self.measured_rate_hz: float = 0.0
        self.measured_bandwidth_mbs: float = 0.0

        # High-resolution rate calculation (sliding window)
        self._rate_timestamps: deque[float] = deque(maxlen=240)
        self._rate_lock = threading.Lock()
        self._last_ui_update_ts: float = 0.0

        # Synchronize dynamic output port IOType with selected frame_type
        self._update_output_iotype()

        # Ring buffer (zero allocation at emission time)
        self._ring_size: int = 16
        self._frame_ring: List[Any] = []  # List[np.ndarray] or List[Tuple[np.ndarray, np.ndarray]]
        self._active_shape: Tuple[int, int, int] = (480, 640, 3)
        self._active_dtype: str = "uint8"
        self._frame_bytes: int = 480 * 640 * 3
        self._rebuild_ring_buffer()

        # Build UI
        self._build_interface()

        if self.auto_start:
            self.start_stream()

    def _update_output_iotype(self) -> None:
        """Update self.outputs['Frame'] so node editor connections validate cleanly."""
        if getattr(self, "universal_any_output", False):
            target_type = IOTypes.ANY
        else:
            ft = getattr(self, "frame_type", "FRAME (8-bit)")
            target_type = self._FRAME_TYPES.get(ft, IOTypes.FRAME)

        self.outputs["Frame"] = target_type

        # Auto-update tooltip and pin in NodeEditor if node was already created
        try:
            from core.node_editor import node_editor

            for nid, inst in getattr(node_editor, "node_map", {}).items():
                if inst is self:
                    attr_tag = f"{nid}_Frame"
                    if dpg.does_item_exist(attr_tag):
                        t_str = str(target_type.value) if hasattr(target_type, "value") else str(target_type)
                        tip_children = dpg.get_item_children(attr_tag, 3) or []
                        for tip in tip_children:
                            tip_texts = dpg.get_item_children(tip, 1) or []
                            for tid in tip_texts:
                                if "IOType:" in str(dpg.get_value(tid)):
                                    dpg.set_value(tid, f"IOType: {t_str}")
                    break
        except Exception:
            pass

    def _resolve_geometry(self) -> Tuple[int, int, int, np.dtype, int]:
        """
        Resolve current (height, width, channels, numpy_dtype, max_value).
        Adjusts bit-depth and channels according to `self.frame_type`.
        """
        preset = getattr(self, "resolution_preset", "VGA (640x480)")
        if preset in self._PRESETS and preset != "Custom":
            h, w = self._PRESETS[preset]
        else:
            w = max(16, int(getattr(self, "custom_width", 640)))
            h = max(16, int(getattr(self, "custom_height", 480)))

        ft = getattr(self, "frame_type", "FRAME (8-bit)")
        fmt = getattr(self, "color_format", "BGR (3 ch)")

        if ft == "FRAME12 (12-bit HDR)":
            dtype = np.uint16
            max_v = 4095
            ch = 1 if "Grayscale" in fmt else 3
        elif ft == "FRAME16 (16-bit HDR)":
            dtype = np.uint16
            max_v = 65535
            ch = 1 if "Grayscale" in fmt else 3
        elif ft == "MASK (Binary ROI)":
            dtype = np.uint8
            max_v = 255
            ch = 1  # Standard binary mask is 1-channel
        elif ft == "FRAME_MASK_PAIR":
            dtype = np.uint8
            max_v = 255
            ch = 1 if "Grayscale" in fmt else (4 if "RGBA" in fmt else 3)
        else:  # FRAME (8-bit)
            dtype = np.uint8
            max_v = 255
            if "Grayscale" in fmt:
                ch = 1
            elif "RGBA" in fmt:
                ch = 4
            else:
                ch = 3

        return h, w, ch, dtype, max_v

    def _rebuild_ring_buffer(self) -> None:
        """Pre-generate N ring-buffer frames for instantaneous zero-overhead indexing."""
        h, w, ch, dtype, max_v = self._resolve_geometry()
        ft = getattr(self, "frame_type", "FRAME (8-bit)")
        pat = getattr(self, "pattern", "Gradient + Scanline")
        n_frames = self._ring_size
        ring: List[Any] = []

        try:
            if ft == "MASK (Binary ROI)":
                # Generate high-contrast binary moving shapes (circle / aperture or box)
                radius = max(12, min(h, w) // 5)
                for i in range(n_frames):
                    t = (i / n_frames) * 2.0 * math.pi
                    cx = int((w // 2) + (w // 3) * math.cos(t))
                    cy = int((h // 2) + (h // 3) * math.sin(t))
                    mask = np.zeros((h, w), dtype=np.uint8)
                    cv2.circle(mask, (cx, cy), radius, 255, -1)
                    # Add small secondary ROI rectangle
                    rx = int((w // 2) - (w // 4) * math.sin(t))
                    ry = int((h // 2) - (h // 4) * math.cos(t))
                    cv2.rectangle(mask, (rx - 16, ry - 16), (rx + 16, ry + 16), 255, -1)
                    ring.append(mask)

            elif ft == "FRAME_MASK_PAIR":
                # Generate paired (frame, mask) tuples
                radius = max(16, min(h, w) // 5)
                y_grad = np.linspace(20, 200, h, dtype=np.uint8)[:, None]
                x_grad = np.linspace(20, 200, w, dtype=np.uint8)[None, :]
                base_b = np.broadcast_to(x_grad, (h, w))
                base_g = np.broadcast_to(y_grad, (h, w))

                for i in range(n_frames):
                    t = (i / n_frames) * 2.0 * math.pi
                    cx = int((w // 2) + (w // 3) * math.cos(t))
                    cy = int((h // 2) + (h // 3) * math.sin(t))

                    # 1. Base image frame
                    if ch == 1:
                        img = np.roll(base_g, shift=i * 12, axis=1).copy()
                        cv2.circle(img, (cx, cy), radius, 245, -1)
                    elif ch == 4:
                        img = np.zeros((h, w, 4), dtype=np.uint8)
                        img[:, :, 0] = np.roll(base_b, shift=i * 16, axis=1)
                        img[:, :, 1] = np.roll(base_g, shift=i * 12, axis=0)
                        img[:, :, 2] = np.uint8((i * 15) % 255)
                        img[:, :, 3] = 255
                        cv2.circle(img, (cx, cy), radius, (255, 255, 50, 255), -1)
                    else:
                        img = np.zeros((h, w, 3), dtype=np.uint8)
                        img[:, :, 0] = np.roll(base_b, shift=i * 16, axis=1)
                        img[:, :, 1] = np.roll(base_g, shift=i * 12, axis=0)
                        img[:, :, 2] = np.uint8((i * 15) % 255)
                        cv2.circle(img, (cx, cy), radius, (255, 255, 50), -1)

                    # 2. Binary mask matching the moving object
                    mask = np.zeros((h, w), dtype=np.uint8)
                    cv2.circle(mask, (cx, cy), radius, 255, -1)

                    ring.append((img, mask))

            elif pat == "Gradient + Scanline":
                lo_v = int(0.08 * max_v)
                hi_v = int(0.88 * max_v)
                y_grad = np.linspace(lo_v, hi_v, h, dtype=dtype)[:, None]
                x_grad = np.linspace(lo_v, hi_v, w, dtype=dtype)[None, :]
                base_r = np.broadcast_to(x_grad, (h, w))
                base_g = np.broadcast_to(y_grad, (h, w))

                for i in range(n_frames):
                    bar_y = int(((i * h) / n_frames)) % max(1, h - 8)
                    if ch == 1:
                        frame = np.roll(base_g, shift=i * (w // n_frames), axis=1).copy()
                        frame[bar_y : bar_y + 8, :] = max_v
                    elif ch == 4:
                        frame = np.zeros((h, w, 4), dtype=dtype)
                        frame[:, :, 0] = np.roll(base_r, shift=i * 16, axis=1)
                        frame[:, :, 1] = np.roll(base_g, shift=i * 12, axis=0)
                        frame[:, :, 2] = dtype((i * (max_v // n_frames)) % (max_v + 1))
                        frame[:, :, 3] = max_v
                        frame[bar_y : bar_y + 8, :, :3] = max_v
                    else:
                        frame = np.zeros((h, w, 3), dtype=dtype)
                        frame[:, :, 0] = np.roll(base_r, shift=i * 16, axis=1)
                        frame[:, :, 1] = np.roll(base_g, shift=i * 12, axis=0)
                        frame[:, :, 2] = dtype((i * (max_v // n_frames)) % (max_v + 1))
                        frame[bar_y : bar_y + 8, :, :] = max_v
                    ring.append(frame)

            elif pat == "Bouncing Box":
                box_sz = max(16, min(h, w) // 6)
                bg_val = int(0.12 * max_v)
                fg_val = int(0.95 * max_v)
                for i in range(n_frames):
                    t = (i / n_frames) * 2.0 * math.pi
                    bx = int((w - box_sz) * (0.5 + 0.45 * math.sin(t)))
                    by = int((h - box_sz) * (0.5 + 0.45 * math.cos(t)))
                    if ch == 1:
                        frame = np.full((h, w), bg_val, dtype=dtype)
                        frame[by : by + box_sz, bx : bx + box_sz] = fg_val
                    elif ch == 4:
                        frame = np.zeros((h, w, 4), dtype=dtype)
                        frame[:, :, 3] = max_v
                        frame[by : by + box_sz, bx : bx + box_sz, 0] = fg_val
                        frame[by : by + box_sz, bx : bx + box_sz, 1] = int(0.7 * max_v)
                    else:
                        frame = np.zeros((h, w, 3), dtype=dtype)
                        frame[by : by + box_sz, bx : bx + box_sz, 0] = fg_val
                        frame[by : by + box_sz, bx : bx + box_sz, 1] = int(0.7 * max_v)
                    ring.append(frame)

            elif pat == "Color Cycle":
                scale_up = max_v / 255.0
                for i in range(n_frames):
                    hue = int((i * 180) / n_frames)
                    if ch == 1:
                        val = int((i * max_v) / n_frames)
                        frame = np.full((h, w), val, dtype=dtype)
                    else:
                        hsv = np.zeros((h, w, 3), dtype=np.uint8)
                        hsv[:, :, 0] = hue
                        hsv[:, :, 1] = 220
                        hsv[:, :, 2] = 230
                        bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
                        if dtype == np.uint8:
                            frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA) if ch == 4 else bgr
                        else:
                            bgr_scaled = (bgr.astype(np.float32) * scale_up).astype(dtype)
                            if ch == 4:
                                frame = np.zeros((h, w, 4), dtype=dtype)
                                frame[:, :, :3] = bgr_scaled
                                frame[:, :, 3] = max_v
                            else:
                                frame = bgr_scaled
                    ring.append(frame)

            elif pat == "Static Noise":
                for i in range(n_frames):
                    rng = np.random.default_rng(seed=i)
                    if ch == 1:
                        frame = rng.integers(0, max_v + 1, (h, w), dtype=dtype)
                    elif ch == 4:
                        frame = np.empty((h, w, 4), dtype=dtype)
                        frame[:, :, :3] = rng.integers(0, max_v + 1, (h, w, 3), dtype=dtype)
                        frame[:, :, 3] = max_v
                    else:
                        frame = rng.integers(0, max_v + 1, (h, w, 3), dtype=dtype)
                    ring.append(frame)

            else:  # Solid Test Card
                card_val = int(0.55 * max_v)
                for i in range(n_frames):
                    if ch == 1:
                        frame = np.full((h, w), card_val, dtype=dtype)
                    elif ch == 4:
                        frame = np.full((h, w, 4), (card_val, card_val // 2, card_val // 3, max_v), dtype=dtype)
                    else:
                        frame = np.full((h, w, 3), (card_val, card_val // 2, card_val // 3), dtype=dtype)
                    ring.append(frame)

        except Exception as e:
            logger.warning(f"VideoBenchmark error generating pattern {pat} for {ft}: {e}")
            ring = [np.zeros((h, w, ch), dtype=dtype) for _ in range(n_frames)]

        # Calculate exact memory footprint per frame
        item_bytes = (h * w * ch * dtype().itemsize)
        if ft == "FRAME_MASK_PAIR":
            item_bytes += (h * w * 1)  # plus mask bytes

        with self._lock:
            self._frame_ring = ring
            self._active_shape = (h, w, ch)
            self._active_dtype = np.dtype(dtype).name
            self._frame_bytes = item_bytes

    def _build_interface(self) -> None:
        scale = display_scaling.scale
        with self.window():
            # Transport Controls
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Start Stream",
                    tag=self.btn_toggle_tag,
                    width=scale(120),
                    callback=self._on_toggle_stream,
                )
                dpg.add_button(
                    label="Step 1 Frame",
                    tag=self.btn_step_tag,
                    width=scale(95),
                    callback=self._on_step_one_frame,
                )
                dpg.add_button(
                    label="Reset",
                    width=scale(65),
                    callback=self._on_reset_counters,
                )

            # Burst / Send N Frames As Fast As Possible
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Send Burst",
                    tag=self.btn_burst_tag,
                    width=scale(150),
                    callback=self._on_send_burst_clicked,
                )
                dpg.add_text("Count:")
                dpg.add_input_int(
                    tag=self.burst_count_tag,
                    default_value=self.burst_count,
                    min_value=1,
                    max_value=1000000,
                    width=scale(200),
                    step=-1,
                    callback=self._on_burst_count_changed,
                )

            dpg.add_spacer(height=scale(4))

            # FPS controls
            dpg.add_slider_float(
                label="Target FPS",
                tag=self.fps_slider_tag,
                default_value=self.fps_target,
                min_value=1.0,
                max_value=500.0,
                clamped=True,
                format="%.1f FPS",
                width=scale(190),
                callback=self._on_fps_changed,
            )
            dpg.add_checkbox(
                label="Uncapped (Max Throughput)",
                tag=self.uncapped_tag,
                default_value=self.uncapped_speed,
                callback=self._on_uncapped_changed,
            )

            dpg.add_separator()

            # -----------------------------------------------------------------
            # Visual IOType Selection (FRAME, FRAME12, FRAME16, MASK, FRAME_MASK_PAIR)
            # -----------------------------------------------------------------
            dpg.add_text("Output Specification:")
            dpg.add_combo(
                label="Frame Type",
                tag=self.frame_type_combo_tag,
                items=list(self._FRAME_TYPES.keys()),
                default_value=self.frame_type,
                width=scale(190),
                callback=self._on_frame_type_changed,
            )

            # Stream Accessory Name Configuration: (frame, name)
            is_pair = self.frame_type == "FRAME_MASK_PAIR"
            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    label="Attach Stream Name: (frame, name)",
                    tag=self.attach_name_chk_tag,
                    default_value=self.attach_stream_name,
                    callback=self._on_attach_name_toggled,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Stream Name:")
                dpg.add_input_text(
                    tag=self.stream_name_in_tag,
                    default_value=self.stream_name,
                    width=scale(140),
                    callback=self._on_stream_name_changed,
                )

            # Universal ANY Output Option (allows wiring to any node without strict type filter)
            dpg.add_checkbox(
                label="Universal Pin (IOType.ANY on node)",
                tag=self.universal_chk_tag,
                default_value=self.universal_any_output,
                callback=self._on_universal_toggled,
            )
            with dpg.tooltip(self.universal_chk_tag):
                dpg.add_text("When checked, sets node editor output to IOType.ANY so you can connect to ANY module.")

            dpg.add_separator()

            # Resolution Presets
            dpg.add_combo(
                label="Resolution",
                tag=self.preset_combo_tag,
                items=list(self._PRESETS.keys()),
                default_value=self.resolution_preset,
                width=scale(190),
                callback=self._on_preset_changed,
            )

            # Custom Width / Height Inputs
            is_custom = self.resolution_preset == "Custom"
            with dpg.group(tag=self.custom_group_tag, horizontal=True, show=is_custom):
                dpg.add_input_int(
                    label="W",
                    tag=self.w_input_tag,
                    default_value=self.custom_width,
                    min_value=16,
                    max_value=7680,
                    width=scale(85),
                    callback=self._on_custom_size_changed,
                )
                dpg.add_input_int(
                    label="H",
                    tag=self.h_input_tag,
                    default_value=self.custom_height,
                    min_value=16,
                    max_value=4320,
                    width=scale(85),
                    callback=self._on_custom_size_changed,
                )

            # Pattern and Color Format
            dpg.add_combo(
                label="Pattern",
                tag=self.pattern_combo_tag,
                items=self._PATTERNS,
                default_value=self.pattern,
                width=scale(190),
                callback=self._on_pattern_changed,
            )
            dpg.add_combo(
                label="Color Format",
                tag=self.format_combo_tag,
                items=self._FORMATS,
                default_value=self.color_format,
                width=scale(190),
                callback=self._on_format_changed,
            )

            dpg.add_separator()

            # Flow Control & Safety
            dpg.add_checkbox(
                label="Respect Backpressure (Hold on busy downstream)",
                tag=self.backpressure_tag,
                default_value=self.respect_backpressure,
                callback=self._on_backpressure_changed,
            )

            dpg.add_separator()

            # Live Telemetry Display
            dpg.add_text(
                f"Active Payload: {self.frame_type} ({self._active_dtype})",
                tag=self.txt_type_info_tag,
            )
            dpg.add_text("Stream Status: Stopped", tag=self.txt_status_tag)
            dpg.add_text("Frames: 0 pkts  |  Measured: 0.0 FPS", tag=self.txt_rate_tag)
            dpg.add_text("Frame Size: 640x480 (0.9 MB)  |  0.0 MB/s", tag=self.txt_bandwidth_tag)

    def _on_toggle_stream(self, *args: Any) -> None:
        if self._running:
            self.stop_stream()
        else:
            self.start_stream()

    def _on_step_one_frame(self, *args: Any) -> None:
        self._emit_single_frame()

    def _on_send_burst_clicked(self, *args: Any) -> None:
        if self._is_bursting:
            self.stop_burst()
        else:
            self.start_burst()

    def _on_burst_count_changed(self, sender: Any, app_data: int, *args: Any) -> None:
        self.burst_count = max(1, int(app_data))

    def _on_reset_counters(self, *args: Any) -> None:
        with self._rate_lock:
            self.total_emitted = 0
            self.dropped_count = 0
            self._rate_timestamps.clear()
            self.measured_rate_hz = 0.0
            self.measured_bandwidth_mbs = 0.0
        self._update_telemetry_ui(force=True)

    def _on_fps_changed(self, sender: Any, app_data: float, *args: Any) -> None:
        self.fps_target = max(1.0, float(app_data))

    def _on_uncapped_changed(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.uncapped_speed = bool(app_data)

    def _on_frame_type_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.frame_type = str(app_data)
        self._update_output_iotype()
        self._rebuild_ring_buffer()
        self._update_telemetry_ui(force=True)

    def _on_attach_name_toggled(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.attach_stream_name = bool(app_data)

    def _on_stream_name_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.stream_name = str(app_data).strip() or "BenchCam"

    def _on_universal_toggled(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.universal_any_output = bool(app_data)
        self._update_output_iotype()

    def _on_preset_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.resolution_preset = str(app_data)
        is_custom = self.resolution_preset == "Custom"
        if dpg.does_item_exist(self.custom_group_tag):
            dpg.configure_item(self.custom_group_tag, show=is_custom)
        self._rebuild_ring_buffer()
        self._update_telemetry_ui(force=True)

    def _on_custom_size_changed(self, *args: Any) -> None:
        if dpg.does_item_exist(self.w_input_tag):
            self.custom_width = max(16, int(dpg.get_value(self.w_input_tag)))
        if dpg.does_item_exist(self.h_input_tag):
            self.custom_height = max(16, int(dpg.get_value(self.h_input_tag)))
        self._rebuild_ring_buffer()
        self._update_telemetry_ui(force=True)

    def _on_pattern_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.pattern = str(app_data)
        self._rebuild_ring_buffer()

    def _on_format_changed(self, sender: Any, app_data: str, *args: Any) -> None:
        self.color_format = str(app_data)
        self._rebuild_ring_buffer()
        self._update_telemetry_ui(force=True)

    def _on_backpressure_changed(self, sender: Any, app_data: bool, *args: Any) -> None:
        self.respect_backpressure = bool(app_data)

    def start_stream(self) -> None:
        """Start continuous video stream playback thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name=f"VideoBenchmark_{self.UUID}",
        )
        self._thread.start()
        if dpg.does_item_exist(self.btn_toggle_tag):
            dpg.configure_item(self.btn_toggle_tag, label="Stop Stream")
        if dpg.does_item_exist(self.txt_status_tag):
            dpg.set_value(self.txt_status_tag, "Stream Status: Running")

    def stop_stream(self) -> None:
        """Stop video stream playback thread."""
        self._running = False
        if dpg.does_item_exist(self.btn_toggle_tag):
            dpg.configure_item(self.btn_toggle_tag, label="Start Stream")
        if dpg.does_item_exist(self.txt_status_tag):
            dpg.set_value(self.txt_status_tag, "Stream Status: Stopped")

    def start_burst(self, count: Optional[int] = None) -> None:
        """Blast exactly `count` frames as fast as possible in a dedicated background thread."""
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
            name=f"VideoBenchmark_burst_{self.UUID}",
        )
        self._burst_thread.start()
        if dpg.does_item_exist(self.btn_burst_tag):
            dpg.configure_item(self.btn_burst_tag, label="Stop Burst")
        if dpg.does_item_exist(self.txt_status_tag):
            dpg.set_value(self.txt_status_tag, f"Stream Status: Bursting ({n:,} frames)...")

    def stop_burst(self) -> None:
        """Cancel active burst emission."""
        self._burst_stop_event.set()
        self._is_bursting = False
        if dpg.does_item_exist(self.btn_burst_tag):
            dpg.configure_item(self.btn_burst_tag, label="Send Burst")
        if dpg.does_item_exist(self.txt_status_tag):
            dpg.set_value(self.txt_status_tag, "Stream Status: Stopped")

    def _burst_loop(self, total_n: int) -> None:
        """Blasts total_n frames at maximum uncapped speed."""
        t0 = time.perf_counter()
        sent = 0

        while sent < total_n and not self._burst_stop_event.is_set() and not self._is_closing:
            if self.respect_backpressure and not self.is_outputs_ready():
                self.dropped_count += 1
                time.sleep(0.0005)
                continue

            self._emit_single_frame()
            sent += 1
            if (sent & 63) == 0:
                time.sleep(0)  # Yield GIL periodically

        t1 = time.perf_counter()
        dur_ms = max(0.001, (t1 - t0) * 1000.0)
        fps = (sent / (dur_ms / 1000.0)) if dur_ms > 0 else 0.0

        self._is_bursting = False
        if dpg.does_item_exist(self.btn_burst_tag):
            dpg.configure_item(self.btn_burst_tag, label="Send Burst")
        if dpg.does_item_exist(self.txt_status_tag):
            dpg.set_value(
                self.txt_status_tag,
                f"Burst Done: {sent:,}/{total_n:,} in {dur_ms:.1f} ms ({fps:,.0f} FPS)",
            )
        self._update_telemetry_ui(force=True)

    def _stream_loop(self) -> None:
        """Low-jitter continuous emission loop."""
        next_deadline = time.perf_counter()

        while self._running and not self._is_closing:
            # 1. Backpressure check
            if self.respect_backpressure and not self.is_outputs_ready():
                self.dropped_count += 1
                time.sleep(0.001)
                next_deadline = time.perf_counter()
                continue

            # 2. Timing pacing
            # IMPORTANT: never busy-wait with a bare `pass` loop. A pure-Python spin holds the GIL
            # continuously and starves every other thread of the main process (DearPyGui render,
            # worker result polling, mp.Queue feeder threads), each GIL handoff then costing up to
            # sys.getswitchinterval() (5 ms). time.sleep(0) explicitly yields the GIL on each spin.
            if not self.uncapped_speed:
                now = time.perf_counter()
                time_left = next_deadline - now
                if time_left > 0.002:
                    time.sleep(time_left - 0.001)
                while time.perf_counter() < next_deadline:
                    time.sleep(0)
            else:
                # Uncapped: still yield the GIL once per frame so the UI stays responsive.
                time.sleep(0)

            # 3. Emit frame
            self._emit_single_frame()

            # 4. Schedule next frame deadline
            if not self.uncapped_speed:
                interval = 1.0 / max(1.0, self.fps_target)
                next_deadline += interval
                # Catch up without spiral of death if system falls behind
                if next_deadline < time.perf_counter() - interval:
                    next_deadline = time.perf_counter()

    def _emit_single_frame(self) -> None:
        """Fetch frame from ring buffer and dispatch to downstream connections."""
        now = time.perf_counter()

        with self._lock:
            ring = self._frame_ring
            if not ring:
                return
            idx = self.total_emitted % len(ring)
            item = ring[idx]
            frame_bytes = self._frame_bytes
            shape = self._active_shape
            ft = self.frame_type
            att_name = self.attach_stream_name
            st_name = self.stream_name

        self.total_emitted += 1

        # Track emission rate
        with self._rate_lock:
            self._rate_timestamps.append(now)
            n_samples = len(self._rate_timestamps)
            if n_samples >= 2:
                dt = now - self._rate_timestamps[0]
                self.measured_rate_hz = ((n_samples - 1) / dt) if dt > 1e-6 else 0.0
            else:
                self.measured_rate_hz = 0.0
            self.measured_bandwidth_mbs = (self.measured_rate_hz * frame_bytes) / (1024.0 * 1024.0)

        if ft == "FRAME_MASK_PAIR":
            # Direct (frame, mask) tuple as standardized by IOTypes.FRAME_MASK_PAIR
            payload = item
            out_iotype = IOTypes.FRAME_MASK_PAIR
        else:
            out_iotype = self._FRAME_TYPES.get(ft, IOTypes.FRAME)
            if att_name and st_name:
                # Format: (frame, source_name) tuple
                payload = (item, st_name)
            else:
                # Format: bare np.ndarray
                payload = item

        # 1. Dispatch Frame downstream
        frame_targets = self.connections.get("Frame", [])
        if frame_targets:
            for tgt in list(frame_targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=payload, data_type=out_iotype)
                    except Exception as e:
                        logger.error(f"VideoBenchmark dispatch error to {getattr(tgt, 'winID', tgt)}: {e}")

        # 2. Dispatch Trigger event if wired
        trig_targets = self.connections.get("Trigger", [])
        if trig_targets:
            for tgt in list(trig_targets):
                cb = getattr(tgt, "input_cb", None)
                if callable(cb):
                    try:
                        cb(data=self.total_emitted, data_type=IOTypes.TRIGGER)
                    except Exception:
                        pass

        # Throttle UI text updates to ~5 Hz to eliminate DearPyGui overhead
        if (now - self._last_ui_update_ts) >= 0.20:
            self._last_ui_update_ts = now
            self._update_telemetry_ui(shape=shape, frame_bytes=frame_bytes)

    def _update_telemetry_ui(
        self,
        force: bool = False,
        shape: Optional[Tuple[int, int, int]] = None,
        frame_bytes: Optional[int] = None,
    ) -> None:
        """Update live status labels in UI."""
        if not dpg.does_item_exist(self.txt_rate_tag):
            return

        with self._lock:
            sh = shape or self._active_shape
            fb = frame_bytes or self._frame_bytes
            dt_name = self._active_dtype
            ft = self.frame_type

        with self._rate_lock:
            tot = self.total_emitted
            hz = self.measured_rate_hz
            bw = self.measured_bandwidth_mbs

        if dpg.does_item_exist(self.txt_type_info_tag):
            dpg.set_value(self.txt_type_info_tag, f"Active Payload: {ft} ({dt_name})")

        dpg.set_value(
            self.txt_rate_tag,
            f"Frames: {tot:,} pkts  |  Measured: {hz:.1f} FPS",
        )
        mb_per_frame = fb / (1024.0 * 1024.0)
        h, w = sh[0], sh[1]
        dpg.set_value(
            self.txt_bandwidth_tag,
            f"Frame Size: {w}x{h} ({mb_per_frame:.2f} MB)  |  {bw:.1f} MB/s",
        )


    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Handle incoming inputs:
          - IOTypes.TRIGGER: Emits 1 frame on each trigger pulse.
          - IOTypes.CMD_DICT: Controls start, stop, fps, burst, type remotely.
          - IOTypes.NUMBER: Sets target FPS dynamically.
        """
        data = kwargs.get("data") if "data" in kwargs else (args[0] if args else None)

        if isinstance(data, dict):
            if "burst" in data:
                self.start_burst(int(data["burst"]))
                return
            if "count" in data:
                self.start_burst(int(data["count"]))
                return
            if "start" in data:
                if data["start"]:
                    self.start_stream()
                else:
                    self.stop_stream()
            if "stop" in data and data["stop"]:
                self.stop_stream()
                self.stop_burst()
            if "fps" in data:
                val = float(data["fps"])
                self.fps_target = max(1.0, val)
                if dpg.does_item_exist(self.fps_slider_tag):
                    dpg.set_value(self.fps_slider_tag, self.fps_target)
            if "type" in data:
                req_t = str(data["type"])
                for k in self._FRAME_TYPES:
                    if req_t.lower() in k.lower():
                        self.frame_type = k
                        self._update_output_iotype()
                        self._rebuild_ring_buffer()
                        if dpg.does_item_exist(self.frame_type_combo_tag):
                            dpg.set_value(self.frame_type_combo_tag, k)
                        break
            if "uncapped" in data:
                self.uncapped_speed = bool(data["uncapped"])
                if dpg.does_item_exist(self.uncapped_tag):
                    dpg.set_value(self.uncapped_tag, self.uncapped_speed)
            return

        if isinstance(data, (int, float)):
            val = float(data)
            if val > 0:
                self.fps_target = max(1.0, val)
                if dpg.does_item_exist(self.fps_slider_tag):
                    dpg.set_value(self.fps_slider_tag, self.fps_target)
            return

        # Trigger event: emit single frame
        self._emit_single_frame()


    def on_close(self) -> None:
        """Clean shutdown of worker threads."""
        self._is_closing = True
        self._running = False
        self.stop_burst()
        if self._thread is not None and self._thread.is_alive():
            try:
                self._thread.join(timeout=0.2)
            except Exception:
                pass
        if self._burst_thread is not None and self._burst_thread.is_alive():
            try:
                self._burst_thread.join(timeout=0.2)
            except Exception:
                pass
        with self._lock:
            self._frame_ring.clear()
        super().on_close()

    def close(self) -> None:
        self.on_close()
        super().close()

    def destroy(self) -> None:
        self.close()
        super().destroy()


VideoBenchmarkWin = VideoBenchmark_win
EXPORTED_CLASS = VideoBenchmark_win
EXPORTED_NAME = "Video Benchmark"
