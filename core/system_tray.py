"""
System Tray integration for Pipeline Creator.

Uses pystray to create a system tray icon that allows minimizing the
application to the tray and restoring it. Runs the tray icon in a
background thread to avoid blocking the DearPyGui render loop.

When active, intercepts the native close button (WM_CLOSE) so that
clicking X minimizes to tray instead of quitting.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import threading
from typing import Any

from loguru import logger

system_tray: SystemTray | None = None


class SystemTray:
    """
    Manages the system tray icon and its menu.

    - start(): creates the icon in a background thread
    - stop(): removes the icon
    - minimize_to_tray(): hides the viewport
    - restore_from_tray(): shows the viewport
    """

    title: str
    is_running: bool
    pystray_available: bool
    _icon: Any
    _thread: threading.Thread | None
    _hwnd: int | None
    _hidden: bool
    _force_quit: bool
    _orig_x: int
    _orig_y: int

    def __init__(self, title: str = "Pipeline Creator") -> None:
        self.title = title
        self._icon = None
        self._thread = None
        self._hwnd = None
        self.is_running = False
        self._hidden = False
        self._force_quit = False
        self.pystray_available = False
        self._orig_x = 100
        self._orig_y = 100

    def start(self) -> None:
        """Create and start the tray icon in a background thread."""
        if sys.platform != "win32":
            logger.warning("System tray is only supported on Windows")
            return

        try:
            import pystray
            from PIL import Image

            self.pystray_available = True
        except ImportError:
            logger.error("pystray or Pillow not installed. Run: pip install pystray Pillow")
            return

        self._get_hwnd()
        icon_image = self._create_icon_image()

        menu = pystray.Menu(
            pystray.MenuItem("Restore", self._on_restore, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Move Offscreen",
                self._on_toggle_offscreen,
                checked=self._is_offscreen_enabled,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Close", self._on_quit),
        )

        self._icon = pystray.Icon(
            name="pipeline_creator",
            icon=icon_image,
            title=self.title,
            menu=menu,
        )

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        self.is_running = True
        logger.info("System tray started")

    def stop(self) -> None:
        """Stop and remove the tray icon."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
        self.is_running = False
        logger.info("System tray stopped")

    def minimize_to_tray(self) -> None:
        """Hide or move offscreen the viewport window based on tray_offscreen boolean configuration."""
        if not self.is_running:
            return
        if sys.platform == "win32":
            if not self._hwnd:
                self._get_hwnd()
            if self._hwnd:
                user32 = ctypes.windll.user32
                from core.config_manager import config

                tray_offscreen = config.get("Window", {}).get("tray_offscreen", True)

                if tray_offscreen:
                    rect = ctypes.wintypes.RECT()
                    if user32.GetWindowRect(self._hwnd, ctypes.byref(rect)):
                        if rect.left > -10000:
                            self._orig_x = rect.left
                            self._orig_y = rect.top

                    SWP_NOSIZE = 0x0001
                    SWP_NOZORDER = 0x0004
                    SWP_NOACTIVATE = 0x0010
                    user32.SetWindowPos(self._hwnd, 0, -20000, -20000, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                    self._hidden = True
                    logger.info("Window moved offscreen to tray (tray_offscreen=true)")
                else:
                    SW_HIDE = 0
                    user32.ShowWindow(self._hwnd, SW_HIDE)
                    self._hidden = True
                    logger.info("Window hidden to tray (tray_offscreen=false)")

    def restore_from_tray(self) -> None:
        """Restore the viewport window from tray."""
        if sys.platform == "win32":
            if not self._hwnd:
                self._get_hwnd()
            if self._hwnd:
                user32 = ctypes.windll.user32
                from core.config_manager import config

                tray_offscreen = config.get("Window", {}).get("tray_offscreen", True)

                if tray_offscreen:
                    SWP_NOSIZE = 0x0001
                    SWP_NOZORDER = 0x0004
                    SWP_NOACTIVATE = 0x0010
                    user32.SetWindowPos(self._hwnd, 0, max(0, self._orig_x), max(0, self._orig_y), 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                    user32.ShowWindow(self._hwnd, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(self._hwnd)
                    self._hidden = False
                    logger.info("Window restored from tray (offscreen mode)")
                else:
                    SW_RESTORE = 9
                    user32.ShowWindow(self._hwnd, SW_RESTORE)
                    user32.SetForegroundWindow(self._hwnd)
                    self._hidden = False
                    logger.info("Window restored from tray (SW_HIDE mode)")

    def _get_hwnd(self) -> None:
        """Get the Win32 window handle for the DPG viewport by title."""
        if sys.platform != "win32":
            return
        try:
            user32 = ctypes.windll.user32
            user32.FindWindowW.restype = ctypes.wintypes.HWND
            user32.FindWindowW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR]
            self._hwnd = user32.FindWindowW(None, self.title)
            if not self._hwnd:
                logger.debug(f"FindWindowW: no window with title '{self.title}' found yet")
        except Exception as e:
            logger.warning(f"Failed to get HWND for system tray: {e}")

    def _create_icon_image(self) -> Any:
        """Create a simple icon image for the tray (blue gradient with 'P').
        Loads from 'ressources/icon.ico' if available."""
        from PIL import Image, ImageDraw, ImageFont

        from core.paths import PROJECT_ROOT

        icon_path = PROJECT_ROOT / "ressources" / "icon.ico"
        if icon_path.exists():
            try:
                return Image.open(icon_path).convert("RGBA")
            except Exception as e:
                logger.warning(f"Failed to load user icon {icon_path}: {e}")

        size = 64
        img = Image.new("RGBA", (size, size), (30, 30, 38, 255))
        draw = ImageDraw.Draw(img)

        for i in range(size):
            r = int(20 + (i / size) * 40)
            g = int(80 + (i / size) * 60)
            b = int(180 - (i / size) * 30)
            draw.line([(0, i), (size - 1, i)], fill=(r, g, b, 255))

        try:
            font_path = PROJECT_ROOT / "ressources" / "consola.ttf"
            if font_path.exists():
                font = ImageFont.truetype(str(font_path), 36)
            else:
                font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), "P", font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((size - tw) / 2, (size - th) / 2 - bbox[1]), "P", fill=(255, 255, 255, 255), font=font)

        return img

    def _is_offscreen_enabled(self, item: Any = None) -> bool:
        from core.config_manager import config
        return bool(config.get("Window", {}).get("tray_offscreen", True))

    def _on_toggle_offscreen(self, icon: Any = None, item: Any = None) -> None:
        from core.config_manager import config
        current = config.get("Window", {}).get("tray_offscreen", True)
        new_val = not current
        if "Window" not in config:
            config["Window"] = {}
        config["Window"]["tray_offscreen"] = new_val
        config.save()
        logger.info(f"Tray mode toggled via context menu: tray_offscreen={new_val}")
        if self._hidden:
            self.minimize_to_tray()

    def _on_restore(self, icon: Any = None, item: Any = None) -> None:
        """Tray menu callback: restore the window directly."""
        self.restore_from_tray()

    def _on_quit(self, icon: Any = None, item: Any = None) -> None:
        """Tray menu callback: quit the application directly."""
        self._force_quit = True
        self.restore_from_tray()
        self.stop()
        from core.app_state import app_state

        app_state.close_requested = True

