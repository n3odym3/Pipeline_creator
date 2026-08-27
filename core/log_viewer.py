"""
Log Viewer module for Pipeline Creator.

Captures loguru logs via a custom sink and presents them in a real-time DearPyGui window
with level color-coding, filtering, and auto-scrolling.
"""

from __future__ import annotations

from collections import deque
import threading
from typing import Any, Dict, Optional, Tuple, Union

import dearpygui.dearpygui as dpg


class LogViewer:
    """
    Class to capture and display logs in a DearPyGui window.
    """

    # Color mapping for different log levels (RGB)
    LEVEL_COLORS: Dict[str, Tuple[int, int, int]] = {
        "TRACE": (128, 128, 128),  # Grey
        "DEBUG": (64, 128, 255),  # Soft Blue
        "INFO": (255, 255, 255),  # White
        "SUCCESS": (0, 255, 0),  # Green
        "WARNING": (255, 255, 0),  # Yellow
        "ERROR": (255, 50, 50),  # Red
        "CRITICAL": (255, 0, 0),  # Bright Red
    }

    def __init__(self) -> None:
        self.logs: deque[Tuple[str, str]] = deque(maxlen=5000)
        self.lock: threading.Lock = threading.Lock()
        self.win_tag: str = "log_viewer_win"
        self.log_container_tag: str = "log_viewer_container"
        self.auto_scroll: bool = True
        self.filter_text: str = ""

    def sink(self, message: Any) -> None:
        """
        Loguru sink method.
        """
        text = str(message)

        try:
            level_name = message.record["level"].name
            is_mkdocs = "mkdocs" in message.record.get("extra", {})
        except AttributeError:
            level_name = "INFO"
            is_mkdocs = False

        with self.lock:
            self.logs.append((text, level_name))

            # Avoid calling DearPyGui functions from background threads for mkdocs logs to prevent thread-safety crashes
            if not is_mkdocs and dpg.does_item_exist(self.log_container_tag):
                if not self.filter_text or self.filter_text.lower() in text.lower():
                    color = self.LEVEL_COLORS.get(level_name, (255, 255, 255))
                    dpg.add_text(text.strip(), parent=self.log_container_tag, color=color)

                if self.auto_scroll:
                    y_max = dpg.get_y_scroll_max(self.log_container_tag)
                    dpg.set_y_scroll(self.log_container_tag, y_max)

    def show(self) -> None:
        """Show the log viewer window."""
        if dpg.does_item_exist(self.win_tag):
            dpg.focus_item(self.win_tag)
            return

        with dpg.window(label="Log Viewer", tag=self.win_tag, width=800, height=600):
            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    label="Auto Scroll",
                    default_value=self.auto_scroll,
                    callback=lambda s, a: setattr(self, "auto_scroll", a),
                )
                dpg.add_button(label="Clear View", callback=self._clear_view)
                dpg.add_input_text(label="Filter", width=200, callback=self._on_filter)

            dpg.add_separator()

            with dpg.child_window(tag=self.log_container_tag, autosize_x=True, autosize_y=True):
                self._populate_logs()

    def _clear_view(self, *args: Any) -> None:
        if dpg.does_item_exist(self.log_container_tag):
            dpg.delete_item(self.log_container_tag, children_only=True)

    def _on_filter(self, sender: Union[int, str], app_data: str, user_data: Optional[Any] = None, *args: Any) -> None:
        self.filter_text = app_data
        self._populate_logs()

    def _populate_logs(self) -> None:
        if not dpg.does_item_exist(self.log_container_tag):
            return

        dpg.delete_item(self.log_container_tag, children_only=True)

        with self.lock:
            current_logs = list(self.logs)

        for log_entry in current_logs:
            if isinstance(log_entry, tuple):
                text, level_name = log_entry
            else:
                text, level_name = log_entry, "INFO"

            if not self.filter_text or self.filter_text.lower() in text.lower():
                color = self.LEVEL_COLORS.get(level_name, (255, 255, 255))
                dpg.add_text(text.strip(), parent=self.log_container_tag, color=color)

        if self.auto_scroll:
            y_max = dpg.get_y_scroll_max(self.log_container_tag)
            dpg.set_y_scroll(self.log_container_tag, y_max)


# Global singleton instance
log_viewer: LogViewer = LogViewer()

