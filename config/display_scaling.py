"""
Display Scaling Module for Pipeline Creator.

Detects the current screen resolution and computes a scale factor
relative to the reference resolution (1920x1080). This scale factor
is used to adjust font size and UI style values so the application
looks consistent on displays ranging from 720p to 4K.

Supports multi-monitor setups: adapt_to_display() detects which
monitor the DPG viewport is currently on.
"""

import ctypes
import sys
from typing import Any, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from core.config_manager import config

# Reference resolution
REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080

# Minimum and maximum scale factors to prevent extreme results
MIN_SCALE = 0.5
MAX_SCALE = 3.0

# Win32 Specific Structs and API signatures defined once at module scope
if sys.platform == "win32":
    import ctypes.wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("rcMonitor", ctypes.wintypes.RECT),
            ("rcWork", ctypes.wintypes.RECT),
            ("dwFlags", ctypes.wintypes.DWORD),
        ]

    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.argtypes = [POINT, ctypes.wintypes.DWORD]
    user32.MonitorFromPoint.restype = ctypes.wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL

    try:
        shcore = ctypes.windll.shcore
        shcore.GetDpiForMonitor.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
    except AttributeError:
        shcore = None


class DisplayScaling:
    """
    Ddisplay scaling manager.

    Detects display resolution and provides helper functions to scale
    pixel values proportionally. Supports multi-monitor setups by
    detecting which monitor the viewport is currently on.
    """

    def __init__(self) -> None:
        self._scale_factor: float = 1.0
        self._screen_width: int = REFERENCE_WIDTH
        self._screen_height: int = REFERENCE_HEIGHT
        self._dpi: int = 96
        self._detected: bool = False
        self._user_multiplier: float = 1.0

        try:
            self._user_multiplier = float(config.get("General", {}).get("ui_scale_multiplier", 1.0))
        except Exception:
            self._user_multiplier = 1.0

    def detect_display(self) -> None:
        """
        Detect the primary monitor resolution (used at startup before viewport exists).

        On Windows, uses ctypes to query real pixel dimensions.
        Falls back to 1920x1080 if detection fails.
        """
        try:
            if sys.platform == "win32":
                pt = POINT(0, 0)
                hMonitor = user32.MonitorFromPoint(pt, 1)  # MONITOR_DEFAULTTOPRIMARY

                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(MONITORINFO)

                if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                    self._screen_width = mi.rcMonitor.right - mi.rcMonitor.left
                    self._screen_height = mi.rcMonitor.bottom - mi.rcMonitor.top

                if shcore:
                    try:
                        dpix, dpiy = ctypes.c_uint(), ctypes.c_uint()
                        shcore.GetDpiForMonitor(hMonitor, 0, ctypes.byref(dpix), ctypes.byref(dpiy))
                        self._dpi = dpix.value
                    except Exception:
                        self._dpi = 96
                else:
                    self._dpi = 96
            else:
                try:
                    from screeninfo import get_monitors

                    monitor = get_monitors()[0]
                    self._screen_width = monitor.width
                    self._screen_height = monitor.height
                except ImportError:
                    logger.warning("screeninfo not available, using reference resolution")

            self._compute_scale_factor()
            self._detected = True
            logger.debug(
                f"Display detected: {self._screen_width}x{self._screen_height} "
                f"-> scale_factor={self._scale_factor:.3f}"
            )
        except Exception as e:
            logger.warning(f"Failed to detect display: {e}. Using scale_factor=1.0")
            self._scale_factor = 1.0
            self._detected = True

    def _detect_viewport_monitor(self) -> Optional[Tuple[int, int, int]]:
        """
        Detect the resolution of the monitor the DPG viewport is currently on.

        Uses Win32 MonitorFromPoint + GetMonitorInfo to find the correct
        monitor in a multi-monitor setup.

        Returns:
            tuple[int, int, int] | None: (width, height, dpi) of the monitor, or None if detection fails.
        """
        if sys.platform != "win32":
            return None

        try:
            vp_pos = dpg.get_viewport_pos()
            if not vp_pos:
                return None
            vp_x, vp_y = int(vp_pos[0]), int(vp_pos[1])
            vp_w = dpg.get_viewport_width()
            vp_h = dpg.get_viewport_height()

            # Find the monitor containing the viewport center
            center_x = vp_x + vp_w // 2
            center_y = vp_y + vp_h // 2
            pt = POINT(center_x, center_y)
            hMonitor = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)

            dpi = 96
            if shcore:
                try:
                    dpix = ctypes.c_uint()
                    dpiy = ctypes.c_uint()
                    shcore.GetDpiForMonitor(hMonitor, 0, ctypes.byref(dpix), ctypes.byref(dpiy))
                    dpi = dpix.value
                except Exception:
                    pass

            if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                width = mi.rcMonitor.right - mi.rcMonitor.left
                height = mi.rcMonitor.bottom - mi.rcMonitor.top
                return (width, height, dpi)

        except Exception as e:
            logger.warning(f"Failed to detect viewport monitor: {e}")

        return None

    def _compute_scale_factor(self) -> None:
        """
        Compute scale factor based on screen height relative to reference.

        Using absolute height handles ultra-wide monitors and provides a
        density ratio that matches the window constraints natively.
        """
        raw = self._screen_height / REFERENCE_HEIGHT
        raw *= self._user_multiplier
        self._scale_factor = max(MIN_SCALE, min(MAX_SCALE, raw))

    def scale(self, value: float) -> int:
        """
        Scale a reference pixel value by the current scale factor.
        """
        return max(1, int(round(value * self._scale_factor)))

    def scale_float(self, value: float) -> float:
        """
        Scale a reference value and return as float (for alpha, weights, etc.)
        """
        return value * self._scale_factor

    @property
    def scale_factor(self) -> float:
        """Current scale factor."""
        return self._scale_factor

    @property
    def screen_width(self) -> int:
        """Detected screen width in pixels."""
        return self._screen_width

    @property
    def screen_height(self) -> int:
        """Detected screen height in pixels."""
        return self._screen_height

    def adapt_to_display(self) -> float:
        """
        Re-detect the display based on the viewport's current monitor
        and re-apply font scaling + theme.
        """
        old_factor = self._scale_factor
        monitor_res = self._detect_viewport_monitor()

        try:
            old_vp_w = dpg.get_viewport_client_width()
            old_vp_h = dpg.get_viewport_client_height()
        except Exception:
            old_vp_w, old_vp_h = None, None

        if monitor_res:
            self._screen_width, self._screen_height, self._dpi = monitor_res
            self._compute_scale_factor()
            if abs(old_factor - self._scale_factor) > 0.01:
                logger.info(
                    f"Viewport monitor: {self._screen_width}x{self._screen_height} "
                    f"@ {self._dpi} DPI -> scale_factor={self._scale_factor:.3f}"
                )
        else:
            self.detect_display()

        if abs(old_factor - self._scale_factor) > 0.01:
            logger.info(f"Display scaling changed: {old_factor:.3f} -> {self._scale_factor:.3f}")

            # Rebuild the theme with new scale
            from config.theme_manager import theme_manager

            theme_manager.refresh()

            # Scale all open windows proportionally
            if old_vp_w and old_vp_h:
                try:
                    new_vp_w = dpg.get_viewport_client_width()
                    new_vp_h = dpg.get_viewport_client_height()

                    ratio_w = new_vp_w / old_vp_w if old_vp_w > 0 else 1.0
                    ratio_h = new_vp_h / old_vp_h if old_vp_h > 0 else 1.0

                    from core.module_registry import get_registered_modules

                    for win in get_registered_modules():
                        if hasattr(win, "winID") and dpg.does_item_exist(win.winID):
                            pos = dpg.get_item_pos(win.winID)
                            w, h = dpg.get_item_rect_size(win.winID)

                            new_pos = [int(pos[0] * ratio_w), int(pos[1] * ratio_h)]
                            new_w = int(w * ratio_w)
                            new_h = int(h * ratio_h)

                            dpg.set_item_pos(win.winID, new_pos)
                            dpg.set_item_width(win.winID, new_w)
                            dpg.set_item_height(win.winID, new_h)

                            win.pos = new_pos
                            win.win_width = new_w
                            win.win_height = new_h
                except Exception as e:
                    logger.error(f"Failed to resize/reposition windows during display adapt: {e}")

        return self._scale_factor

display_scaling: DisplayScaling = DisplayScaling()

