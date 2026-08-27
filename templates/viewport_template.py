from __future__ import annotations

import ctypes
import sys
from typing import Any, Dict, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from core.viewport_process_base import ViewportProcessBase


class Viewport_Template(ViewportProcessBase):
    """
    A template for building parallel viewport processes in an isolated OS window.

    Subclasses ViewportProcessBase to leverage separate process rendering (DPG loop),
    input data queues, control command queues, and event callbacks back to the main UI.
    """

    def __init__(
        self,
        title: str = "Viewport Template",
        width: int = 600,
        height: int = 400,
        pos: Tuple[int, int] = (200, 200),
        params: Optional[Dict[str, Any]] = None,
        *,
        input_buffer_size: int = 100,
        output_buffer_size: int = 100,
        daemon: bool = False,
    ) -> None:
        """
        Initialize the parallel viewport process.

        Args:
            title: Title of the separate OS window viewport.
            width: Viewport width in pixels.
            height: Viewport height in pixels.
            pos: Initial viewport (x, y) position on screen.
            params: Initial configuration parameters dictionary.
            input_buffer_size: Maximum size of the input queue.
            output_buffer_size: Maximum size of the output event queue.
            daemon: Whether the background process is a daemon.
        """
        self.title = title
        self.width = width
        self.height = height
        self.pos = pos

        # Internal DPG element tags (initialized in subprocess context)
        self.winID: Optional[str] = None
        self.text_widget_tag: Optional[str] = None

        super().__init__(
            params=params,
            input_buffer_size=input_buffer_size,
            output_buffer_size=output_buffer_size,
            daemon=daemon,
        )

    def _setup_viewport(self) -> None:
        """
        Setup DearPyGUI context and create the independent viewport window.
        This runs in the isolated child process.
        """
        # Enable Per-Monitor DPI awareness on Windows if supported
        if sys.platform == "win32":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception as e:
                logger.debug(f"Could not set DPI awareness: {e}")

        # Create isolated DearPyGUI context
        dpg.create_context()

        # Apply global theme if available
        try:
            from config.theme_manager import theme_manager

            dpg.bind_theme(theme_manager.global_theme)
        except Exception as e:
            logger.debug(f"Theme not available in subprocess: {e}")

        # Create separate OS viewport window
        dpg.create_viewport(
            title=self.title,
            width=self.width,
            height=self.height,
            x_pos=self.pos[0],
            y_pos=self.pos[1],
        )
        dpg.setup_dearpygui()

        # Build viewport window UI
        self.winID = f"{self.title}_main_win"
        self.text_widget_tag = f"{self.title}_text_display"

        with dpg.window(tag=self.winID, label=self.title):
            dpg.add_text("Parallel Viewport Process Running...")
            dpg.add_separator()
            dpg.add_text(
                "Waiting for data...",
                tag=self.text_widget_tag,
                wrap=self.width - 40,
            )

        dpg.set_primary_window(self.winID, True)
        dpg.show_viewport()

    def _run_event_loop(self) -> None:
        """
        Main rendering loop executed in the subprocess.
        Pumps DPG frames while processing control commands and incoming data.
        """
        while dpg.is_dearpygui_running() and not self._shutdown_event.is_set():
            # 1. Process control commands (e.g. stop or parameter updates)
            if not self._process_control_commands():
                break

            # 2. Process incoming data packets from main process
            data = self._process_input_data()
            if data is not None:
                self._handle_incoming_data(data)

            # 3. Render frame
            dpg.render_dearpygui_frame()

    def _handle_incoming_data(self, data: Any) -> None:
        """
        Custom logic to render or handle data packets inside the viewport.

        Args:
            data: Incoming data object sent via send_input().
        """
        text_str = str(data)
        if self.text_widget_tag and dpg.does_item_exist(self.text_widget_tag):
            dpg.set_value(self.text_widget_tag, f"Received: {text_str}")

        # Example: Notify main process that data was rendered
        self._send_event({"type": "data_rendered", "payload": text_str})

    def _cleanup_viewport(self) -> None:
        """
        Clean up DearPyGUI context and resources upon process exit.
        """
        try:
            if dpg.is_dearpygui_running():
                dpg.destroy_context()
        except Exception as e:
            logger.debug(f"Viewport cleanup exception: {e}")
