"""
High-Performance Video Viewer Window Module.

Provides a DearPyGui-based low-latency video streaming display canvas supporting
multithreaded texture rendering, automatic frame format conversion (Grayscale, RGB, RGBA),
dual FPS measurement (Read and Display), transparency blending, and GPU texture streaming.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.config_manager import config
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class VideoViewerWin(WindowBase):
    """
    Robust and high-performance video viewer window for DearPyGui.

    Features:
    - Dedicated worker thread rendering to prevent UI blocking (600+ FPS capability)
    - Automatic GPU texture allocation and aspect ratio scaling
    - Dual FPS telemetry (stream ingestion rate and actual GPU display rate) embedded directly in the class
    - Full support for Grayscale, BGR, BGRA, and RGBA pixel formats
    - Transparent overlay support for masks and segmentation layers
    - Dynamic monitor scaling via display_scaling
    """

    label: str = "Video Viewer"
    win_width: int = display_scaling.scale(800)
    win_height: int = display_scaling.scale(600)
    description: str = "Low-latency threaded video viewer with FPS telemetry."

    accepted_input_types: list[type] = [
        IOTypes.FRAME,
        IOTypes.MASK,
        IOTypes.FRAME_MASK_PAIR,
    ]
    outputs: dict[str, type] = {
        "Frame": IOTypes.FRAME,
        "Mask": IOTypes.MASK,
    }
    persistent_fields: list[str] = [
        "label",
        "fps_counter_enabled",
    ]

    # Persistent field defaults
    fps_counter_enabled: bool = True
    texture_mode: str = "raw"
    preserve_aspect_ratio: bool = True

    def __init__(self, **kwargs: Any) -> None:
        """
        Initialize the Video Viewer module window.

        Args:
            **kwargs: Extra configuration options forwarded to WindowBase.
        """
        super().__init__(**kwargs)

        self.use_rgba: bool = True

        raw_fps = getattr(self, "fps_counter_enabled", None)
        if raw_fps is None and "fps_counter" in kwargs:
            raw_fps = kwargs["fps_counter"]
        self.fps_counter_enabled: bool = bool(raw_fps) if raw_fps is not None else True

        viewer_cfg = config.get("Viewers", {}).get("video_viewer", {})
        default_mode = viewer_cfg if isinstance(viewer_cfg, str) else viewer_cfg.get("texture_mode", "raw")
        self.texture_mode = str(default_mode).lower() if default_mode else "raw"
        if self.texture_mode not in ("raw", "static"):
            self.texture_mode = "raw"

        # Aspect ratio configuration from config.json (fallback: True)
        preserve_aspect_ratio = None
        if isinstance(viewer_cfg, dict):
            preserve_aspect_ratio = viewer_cfg.get("preserve_aspect_ratio")
        if preserve_aspect_ratio is None:
            preserve_aspect_ratio = config.get("Viewers", {}).get("preserve_aspect_ratio", True)

        if kwargs.get("stretch", False):
            self.preserve_aspect_ratio = False
        elif isinstance(preserve_aspect_ratio, str):
            self.preserve_aspect_ratio = preserve_aspect_ratio.strip().lower() in ("true", "1", "yes", "preserve")
        else:
            self.preserve_aspect_ratio = bool(preserve_aspect_ratio)

        # Unique Tags for DPG items
        self.texture_tag = f"video_viewer_texture_{self.UUID}"
        self.series_tag = f"video_viewer_series_{self.UUID}"
        self.plot_tag = f"video_viewer_plot_{self.UUID}"
        self.xaxis_tag = f"video_viewer_xaxis_{self.UUID}"
        self.yaxis_tag = f"video_viewer_yaxis_{self.UUID}"
        self.texture_registry_tag = f"video_viewer_tex_reg_{self.UUID}"
        self.fps_display_tag = f"video_viewer_display_fps_{self.UUID}"
        self.fps_read_tag = f"video_viewer_read_fps_{self.UUID}"

        # State Variables
        self.texture_width = 0
        self.texture_height = 0
        self._texture_initialized = False
        self._texture_buffer = None
        self._thread_lock = threading.Lock()
        self._running = True
        self._last_source_name = None

        # Threading & Latest Frame approach (no queue accumulation)
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self._update_pending = False
        self.update_event = threading.Event()

        # FPS Telemetry Logic (Internal to VideoViewerWin)
        self._fps_smoothing_window: int = 10

        self._read_last_time: Optional[float] = None
        self._read_raw_fps: float = 0.0
        self._read_smoothed_fps: float = 0.0
        self._read_samples: deque[float] = deque(maxlen=self._fps_smoothing_window)
        self._read_call_count: int = 0

        self._display_last_time: Optional[float] = None
        self._display_raw_fps: float = 0.0
        self._display_smoothed_fps: float = 0.0
        self._display_samples: deque[float] = deque(maxlen=self._fps_smoothing_window)
        self._display_call_count: int = 0

        # Performance optimization: frame counter for FPS display
        self._frame_count: int = 0
        self._fps_update_interval: int = 10

        # Initialize UI using self.window context manager
        self._build_interface()
        self.update_permission()

        # Start Render Thread
        self.worker_thread = threading.Thread(
            target=self._render_loop,
            daemon=True,
            name=f"VideoViewerWorker_{self.UUID}",
        )
        self.worker_thread.start()

    def calc_read_fps(self) -> Tuple[float, float]:
        """
        Record timestamp and compute instantaneous and smoothed Read FPS.

        Returns:
            Tuple of (raw_fps, smoothed_fps).
        """
        now = time.time()
        if self._read_last_time is not None:
            delta = now - self._read_last_time
            if delta > 0:
                self._read_raw_fps = 1.0 / delta
                self._read_samples.append(self._read_raw_fps)
                self._read_call_count += 1
                if (
                    self._read_call_count % self._fps_smoothing_window == 0
                    and len(self._read_samples) == self._fps_smoothing_window
                ):
                    self._read_smoothed_fps = sum(self._read_samples) / self._fps_smoothing_window
        self._read_last_time = now
        return self._read_raw_fps, self._read_smoothed_fps

    def calc_display_fps(self) -> Tuple[float, float]:
        """
        Record timestamp and compute instantaneous and smoothed Display FPS.

        Returns:
            Tuple of (raw_fps, smoothed_fps).
        """
        now = time.time()
        if self._display_last_time is not None:
            delta = now - self._display_last_time
            if delta > 0:
                self._display_raw_fps = 1.0 / delta
                self._display_samples.append(self._display_raw_fps)
                self._display_call_count += 1
                if (
                    self._display_call_count % self._fps_smoothing_window == 0
                    and len(self._display_samples) == self._fps_smoothing_window
                ):
                    self._display_smoothed_fps = sum(self._display_samples) / self._fps_smoothing_window
        self._display_last_time = now
        return self._display_raw_fps, self._display_smoothed_fps

    @property
    def read_fps_counter(self) -> Any:
        """Compatibility property providing get_fps() for Read FPS."""
        class _ReadFPSProxy:
            get_fps = self.calc_read_fps
        return _ReadFPSProxy()

    @property
    def display_fps_counter(self) -> Any:
        """Compatibility property providing get_fps() for Display FPS."""
        class _DisplayFPSProxy:
            get_fps = self.calc_display_fps
        return _DisplayFPSProxy()

    def update_permission(self) -> None:
        """Update UI permissions based on application mode."""
        from core.app_state import app_state

        is_user = app_state.mode == "user"
        if dpg.does_item_exist(self.winID):
            dpg.configure_item(self.winID, no_close=is_user)

    def is_ready(self) -> bool:
        """Indicate whether the video viewer is running and able to accept new frames."""
        return self._running

    def _apply_transparency_theme(self) -> None:
        """Applies transparent plot background theme so alpha=0 pixels show window background."""
        if not hasattr(self, "_plot_transparent_theme") or not dpg.does_item_exist(self._plot_transparent_theme):
            with dpg.theme() as self._plot_transparent_theme:
                with dpg.theme_component(dpg.mvPlot):
                    dpg.add_theme_color(dpg.mvPlotCol_PlotBg, (0, 0, 0, 0), category=dpg.mvThemeCat_Plots)
                    dpg.add_theme_color(dpg.mvPlotCol_FrameBg, (0, 0, 0, 0), category=dpg.mvThemeCat_Plots)

        if dpg.does_item_exist(self.plot_tag):
            dpg.bind_item_theme(self.plot_tag, self._plot_transparent_theme)

    def _on_toggle_rgba(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Retained for backward compatibility. Viewer operates permanently in RGBA."""
        self.use_rgba = True

    def _build_interface(self) -> None:
        """Builds the initial DPG interface elements."""
        if not dpg.does_item_exist(self.texture_registry_tag):
            dpg.add_texture_registry(tag=self.texture_registry_tag, show=False)

        with self.window():
            if self.fps_counter_enabled:
                with dpg.group(horizontal=True):
                    dpg.add_text("Read FPS: ")
                    dpg.add_text("0.0", tag=self.fps_read_tag)
                    dpg.add_spacer(width=display_scaling.scale(20))
                    dpg.add_text("Display FPS: ")
                    dpg.add_text("0.0", tag=self.fps_display_tag)

            self.init_viewer(1280, 720)
            self._apply_transparency_theme()

    def _convert_to_texture_data(self, frame: np.ndarray) -> np.ndarray:
        """
        Convert frame to normalized float32 RGBA array for DearPyGui raw texture.
        Automatically converts Grayscale, RGB, BGR, or BGRA inputs to RGBA.

        Args:
            frame: Input numpy array image.

        Returns:
            Flat float32 array normalized between 0.0 and 1.0.
        """
        h, w = frame.shape[:2]
        expected_size = h * w * 4

        if self._texture_buffer is None or self._texture_buffer.size != expected_size:
            self._texture_buffer = np.zeros(expected_size, dtype=np.float32)

        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)

        if len(frame.shape) == 2 or (len(frame.shape) == 3 and frame.shape[2] == 1):
            conv = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGBA)
        elif frame.shape[2] == 4:
            conv = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
        elif frame.shape[2] == 3:
            conv = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        else:
            conv = frame

        flat = conv.ravel()
        if len(self._texture_buffer) == len(flat):
            np.multiply(flat, 1.0 / 255.0, out=self._texture_buffer, casting="unsafe")
        else:
            self._texture_buffer = flat.astype(np.float32) * (1.0 / 255.0)

        return self._texture_buffer

    def init_viewer(self, width: int, height: int) -> None:
        """
        Initialize DearPyGui image display area within a zoomable/pannable plot.

        Dispatches execution to the main thread if called asynchronously.

        Args:
            width: Texture width in pixels.
            height: Texture height in pixels.
        """
        from core.automation_manager import automation_manager

        if threading.current_thread() is threading.main_thread():
            self._init_viewer_internal(width, height)
        else:
            automation_manager.post_to_main_thread(self._init_viewer_internal, width, height, wait=True)

    def _init_viewer_internal(self, width: int, height: int) -> None:
        """Construct or resize DPG texture and plot image series."""
        with self._thread_lock:
            self._texture_initialized = False
            self.texture_width = width
            self.texture_height = height

            if dpg.does_item_exist(self.series_tag):
                try:
                    dpg.delete_item(self.series_tag)
                except Exception as e:
                    logger.warning(f"Error deleting series {self.series_tag}: {e}")

            if dpg.does_item_exist(self.texture_tag):
                try:
                    dpg.delete_item(self.texture_tag)
                except Exception as e:
                    logger.warning(f"Error deleting texture {self.texture_tag}: {e}")

            expected_size = height * width * 4
            if self._texture_buffer is None or self._texture_buffer.size != expected_size:
                self._texture_buffer = np.zeros(expected_size, dtype=np.float32)

            if self.texture_mode == "static":
                dpg.add_static_texture(
                    width=width,
                    height=height,
                    default_value=self._texture_buffer,
                    tag=self.texture_tag,
                    parent=self.texture_registry_tag,
                )
            else:
                dpg.add_raw_texture(
                    width=width,
                    height=height,
                    default_value=self._texture_buffer,
                    format=dpg.mvFormat_Float_rgba,
                    tag=self.texture_tag,
                    parent=self.texture_registry_tag,
                )

            if not dpg.does_item_exist(self.plot_tag):
                plot_tag = dpg.add_plot(
                    tag=self.plot_tag,
                    no_menus=True,
                    no_title=True,
                    width=-1,
                    height=-1,
                    equal_aspects=self.preserve_aspect_ratio,
                    parent=self.winID,
                )
                dpg.add_plot_legend(parent=plot_tag)
                dpg.add_plot_axis(
                    dpg.mvXAxis,
                    no_gridlines=True,
                    no_tick_labels=True,
                    no_tick_marks=True,
                    tag=self.xaxis_tag,
                    parent=plot_tag,
                )
                dpg.add_plot_axis(
                    dpg.mvYAxis,
                    no_gridlines=True,
                    no_tick_labels=True,
                    no_tick_marks=True,
                    tag=self.yaxis_tag,
                    parent=plot_tag,
                )
                self._apply_transparency_theme()
            else:
                dpg.configure_item(self.plot_tag, equal_aspects=self.preserve_aspect_ratio)

            dpg.add_image_series(
                self.texture_tag,
                [0, 0],
                [width, height],
                tag=self.series_tag,
                parent=self.yaxis_tag,
            )

            if dpg.does_item_exist(self.xaxis_tag) and dpg.does_item_exist(self.yaxis_tag):
                dpg.set_axis_limits_auto(self.xaxis_tag)
                dpg.set_axis_limits_auto(self.yaxis_tag)
                dpg.fit_axis_data(self.xaxis_tag)
                dpg.fit_axis_data(self.yaxis_tag)

            self._texture_initialized = True

    def update_image(self, frame: np.ndarray) -> None:
        """
        Public method to submit a new frame for rendering.

        Args:
            frame: Incoming image array.
        """
        _, smoothed_fps = self.calc_read_fps()
        if (
            self.fps_counter_enabled
            and self._read_call_count % self._fps_smoothing_window == 0
            and dpg.does_item_exist(self.fps_read_tag)
        ):
            dpg.set_value(self.fps_read_tag, f"{smoothed_fps:.1f}")

        if frame is None:
            return

        with self.frame_lock:
            self.latest_frame = frame
        self.update_event.set()

    def _render_loop(self) -> None:
        """Internal render loop that displays the latest available frame without blocking input."""
        while self._running:
            self.update_event.wait(timeout=0.1)
            self.update_event.clear()

            frame_to_render = None
            with self.frame_lock:
                if self.latest_frame is not None:
                    frame_to_render = self.latest_frame
                    self.latest_frame = None

            if frame_to_render is not None:
                try:
                    self._update_texture(frame_to_render)
                except Exception as e:
                    logger.warning(f"Error in VideoViewer render loop: {e}")

    def _update_texture(self, frame: np.ndarray) -> None:
        """
        Converts frame and uploads to GPU texture (raw or static).

        Args:
            frame: Image array to render.
        """
        h, w = frame.shape[:2]
        texture_data = self._convert_to_texture_data(frame)

        if self.texture_mode == "static":
            dim_changed = (
                h != self.texture_height or w != self.texture_width or not self._texture_initialized
            )
            self.texture_width = w
            self.texture_height = h

            def _recreate_static() -> None:
                with self._thread_lock:
                    if not self._running:
                        return
                    if dpg.does_item_exist(self.series_tag):
                        try:
                            dpg.delete_item(self.series_tag)
                        except Exception:
                            pass
                    if dpg.does_item_exist(self.texture_tag):
                        try:
                            dpg.delete_item(self.texture_tag)
                        except Exception:
                            pass
                    dpg.add_static_texture(
                        width=w,
                        height=h,
                        default_value=texture_data,
                        tag=self.texture_tag,
                        parent=self.texture_registry_tag,
                    )
                    dpg.add_image_series(
                        self.texture_tag,
                        [0, 0],
                        [w, h],
                        tag=self.series_tag,
                        parent=self.yaxis_tag,
                    )
                    if dim_changed and dpg.does_item_exist(self.xaxis_tag) and dpg.does_item_exist(self.yaxis_tag):
                        dpg.set_axis_limits_auto(self.xaxis_tag)
                        dpg.set_axis_limits_auto(self.yaxis_tag)
                        dpg.fit_axis_data(self.xaxis_tag)
                        dpg.fit_axis_data(self.yaxis_tag)
                    self._texture_initialized = True

            from core.automation_manager import automation_manager

            if threading.current_thread() is threading.main_thread():
                _recreate_static()
            else:
                automation_manager.post_to_main_thread(_recreate_static, wait=True)

        else:
            if not self._texture_initialized or h != self.texture_height or w != self.texture_width:
                self.init_viewer(w, h)

            with self._thread_lock:
                if self._texture_initialized and dpg.does_item_exist(self.texture_tag):
                    dpg.set_value(self.texture_tag, texture_data)

        # Update FPS display only every N frames (reduce overhead)
        self._frame_count += 1
        raw_fps, smoothed_fps = self.calc_display_fps()
        if self._frame_count >= self._fps_update_interval:
            self._frame_count = 0
            if self.fps_counter_enabled and dpg.does_item_exist(self.fps_display_tag):
                dpg.set_value(self.fps_display_tag, f"{smoothed_fps:.1f}")

    def set_texture_mode(self, mode: str) -> None:
        """
        Switch texture rendering mode ('raw' or 'static').

        Args:
            mode: 'raw' for high-throughput memory buffers, or 'static' for fallback.
        """
        mode_clean = mode.lower()
        if mode_clean in ("raw", "static") and mode_clean != self.texture_mode:
            with self._thread_lock:
                self.texture_mode = mode_clean
                self._texture_initialized = False

    def set_preserve_aspect_ratio(self, preserve: bool) -> None:
        """
        Dynamically toggle aspect ratio preservation / stretching.

        Args:
            preserve: True to maintain square 1:1 pixel aspect ratio, False to stretch to fill canvas.
        """
        self.preserve_aspect_ratio = bool(preserve)

        def _apply() -> None:
            if dpg.does_item_exist(self.plot_tag):
                dpg.configure_item(self.plot_tag, equal_aspects=self.preserve_aspect_ratio)
                if dpg.does_item_exist(self.xaxis_tag) and dpg.does_item_exist(self.yaxis_tag):
                    dpg.set_axis_limits_auto(self.xaxis_tag)
                    dpg.set_axis_limits_auto(self.yaxis_tag)
                    dpg.fit_axis_data(self.xaxis_tag)
                    dpg.fit_axis_data(self.yaxis_tag)

        from core.automation_manager import automation_manager
        if threading.current_thread() is threading.main_thread():
            _apply()
        else:
            automation_manager.post_to_main_thread(_apply)

    def on_close(self) -> None:
        """Cleanup background thread and GPU texture resources when window is closed."""
        if getattr(self, "_is_closing", False):
            return
        self._is_closing = True
        self._running = False
        self.update_event.set()

        if hasattr(self, "worker_thread") and self.worker_thread.is_alive() and threading.current_thread() != self.worker_thread:
            self.worker_thread.join(timeout=0.2)

        if hasattr(self, "_plot_transparent_theme") and dpg.does_item_exist(self._plot_transparent_theme):
            try:
                dpg.delete_item(self._plot_transparent_theme)
            except Exception:
                pass

        with self._thread_lock:
            self._texture_initialized = False
            if dpg.does_item_exist(self.series_tag):
                try:
                    dpg.delete_item(self.series_tag)
                except Exception:
                    pass
            if dpg.does_item_exist(self.texture_tag):
                try:
                    dpg.delete_item(self.texture_tag)
                except Exception:
                    pass
            if dpg.does_item_exist(self.texture_registry_tag):
                try:
                    dpg.delete_item(self.texture_registry_tag)
                except Exception:
                    pass
            self._texture_buffer = None
        super().on_close()

    def close(self) -> None:
        """Close window and unregister."""
        self.on_close()
        super().close()

    def destroy(self) -> None:
        """Destroy window and release resources."""
        self.close()

    @staticmethod
    def _compose_frame_with_mask(raw_frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Combine a frame and a mask into a 4-channel BGRA image."""
        h, w = raw_frame.shape[:2]
        if mask.dtype == bool:
            alpha = np.where(mask, np.uint8(255), np.uint8(0))
        elif mask.dtype == np.uint8 and mask.max() > 1:
            alpha = mask
        elif mask.max() <= 1 and mask.dtype != np.uint8:
            alpha = (mask * 255).astype(np.uint8)
        else:
            alpha = np.where(mask > 0, np.uint8(255), np.uint8(0)) if mask.dtype != np.uint8 else mask

        if alpha.shape[:2] != (h, w):
            alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_NEAREST)

        if raw_frame.ndim == 2 or (raw_frame.ndim == 3 and raw_frame.shape[2] == 1):
            bgra = cv2.cvtColor(raw_frame, cv2.COLOR_GRAY2BGRA)
        elif raw_frame.shape[2] == 4:
            bgra = raw_frame.copy()
        else:
            bgra = cv2.cvtColor(raw_frame, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = alpha
        return bgra

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Node editor input callback.

        Accepts:
          - A raw np.ndarray frame
          - A (frame, source_name) tuple [emitted by VideoReader, IDS, Hamamatsu, etc.]
          - A (frame, mask) FRAME_MASK_PAIR tuple/list
          - A mask np.ndarray
          - A dict with 'frame' and optional 'mask'
        """
        data = kwargs.get("data") if "data" in kwargs else (args[0] if args else None)
        data_type = kwargs.get("data_type", None)

        if data is None and "sample" in kwargs:
            data = kwargs["sample"]
        if data is None and "frame" in kwargs:
            data = kwargs["frame"]

        if data is None:
            return

        source_name = None
        frame = None

        # Check for FRAME_MASK_PAIR: (frame, mask)
        is_frame_mask_pair = data_type == IOTypes.FRAME_MASK_PAIR or (
            isinstance(data, (tuple, list))
            and len(data) == 2
            and isinstance(data[0], np.ndarray)
            and isinstance(data[1], np.ndarray)
        )

        if is_frame_mask_pair:
            frame = self._compose_frame_with_mask(data[0], data[1])

        elif isinstance(data, tuple) and len(data) >= 2 and isinstance(data[1], str):
            frame = data[0]
            source_name = data[1]
            if data_type == IOTypes.MASK and isinstance(frame, np.ndarray):
                mask = frame
                frame = np.where(mask > 0, np.uint8(255), np.uint8(0)) if mask.dtype != np.uint8 else mask

        elif isinstance(data, np.ndarray):
            if data_type == IOTypes.MASK:
                mask = data
                frame = np.where(mask > 0, np.uint8(255), np.uint8(0)) if mask.dtype != np.uint8 else mask
            else:
                frame = data

        elif isinstance(data, dict) and "frame" in data:
            frame = data["frame"]
            if "mask" in data and data["mask"] is not None:
                frame = self._compose_frame_with_mask(frame, data["mask"])

        if frame is None:
            return

        # Update window title with source name when it changes
        if source_name and source_name != getattr(self, "_last_source_name", None):
            self._last_source_name = source_name
            try:
                dpg.set_item_label(self.winID, f"{self.label} – {source_name}")
            except Exception:
                pass

        if isinstance(frame, np.ndarray):
            self.update_image(frame)

VideoViewerWindow = VideoViewerWin
EXPORTED_CLASS = VideoViewerWin
EXPORTED_NAME = "Video Viewer"
