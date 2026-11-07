from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class Text_viewer_win(WindowBase):
    """
    A DearPyGui window for inspecting incoming text, command dictionaries, or triggers
    in a scrollable read-only multiline viewer with copy, clear, and append modes.
    """

    label: str = "Text Viewer"
    win_width: int = display_scaling.scale(400)
    win_height: int = display_scaling.scale(300)
    description: str = (
        "Display incoming text, command dictionaries, or trigger statuses in a scrollable view."
    )

    accepted_input_types: list[str] = [IOTypes.TEXT, IOTypes.CMD_DICT, IOTypes.TRIGGER, IOTypes.ANY]
    outputs: dict[str, str] = {}
    persistent_fields: list[str] = ["label", "content", "append_mode"]

    def __init__(
        self,
        content: str = "(No content)",
        append_mode: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the Text Viewer window module.

        Args:
            content: Initial or restored display text.
            append_mode: Whether incoming data is appended or overwrites the view.
            **kwargs: Additional parameters forwarded to WindowBase.
        """
        super().__init__(**kwargs)

        self.content: str = getattr(self, "content", None) or content
        self.append_mode: bool = (
            getattr(self, "append_mode", None)
            if getattr(self, "append_mode", None) is not None
            else append_mode
        )

        uid = self.UUID
        self.text_tag: str = f"text_viewer_content_{uid}"
        self.clear_btn_tag: str = f"text_viewer_clear_{uid}"
        self.copy_btn_tag: str = f"text_viewer_copy_{uid}"
        self.append_chk_tag: str = f"text_viewer_app_{uid}"

        self._build_ui()
        self.update_permission()

    def _build_ui(self) -> None:
        """Construct DearPyGui widgets for the text viewer."""
        scale = display_scaling.scale

        with self.window():
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Clear",
                    tag=self.clear_btn_tag,
                    callback=self._on_clear_clicked,
                    width=scale(60),
                )
                dpg.add_button(
                    label="Copy",
                    tag=self.copy_btn_tag,
                    callback=self._on_copy_clicked,
                    width=scale(60),
                )
                dpg.add_checkbox(
                    label="Append",
                    tag=self.append_chk_tag,
                    default_value=self.append_mode,
                    callback=self._on_append_toggled,
                )

            dpg.add_separator()

            dpg.add_input_text(
                multiline=True,
                readonly=True,
                tag=self.text_tag,
                default_value=self.content,
                width=-1,
                height=-1,
            )

    def update_permission(self) -> None:
        """
        Adjust module permissions and UI elements based on the application mode.
        """
        from core.app_state import app_state

        is_user = getattr(app_state, "mode", "dev") == "user"
        if dpg.does_item_exist(self.winID):
            dpg.configure_item(self.winID, no_close=is_user)

    def _on_clear_clicked(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Clear viewer text content."""
        self.content = ""
        if dpg.does_item_exist(self.text_tag):
            dpg.set_value(self.text_tag, "")

    def _on_copy_clicked(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Copy current content to system clipboard."""
        if self.content:
            try:
                dpg.set_clipboard_text(self.content)
            except Exception as e:
                logger.error(f"[{self.label}] Failed to copy text to clipboard: {e}")

    def _on_append_toggled(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Toggle between append and replace mode."""
        self.append_mode = bool(app_data)

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Update displayed text with incoming data packet.
        """
        raw_val = kwargs.get("data")
        if raw_val is None:
            raw_val = kwargs.get("content")
        if raw_val is None:
            raw_val = kwargs.get("sample")
        if raw_val is None and args:
            raw_val = args[0]

        if raw_val is None:
            return

        if isinstance(raw_val, dict):
            try:
                formatted_str = json.dumps(raw_val, indent=2)
            except (TypeError, ValueError):
                formatted_str = str(raw_val)
        else:
            formatted_str = str(raw_val)

        if self.append_mode and self.content and self.content != "(No content)":
            new_content = f"{self.content}\n{formatted_str}"
        else:
            new_content = formatted_str

        self.content = new_content

        if dpg.does_item_exist(self.text_tag):
            dpg.set_value(self.text_tag, self.content)
            logger.debug(f"[{self.label}] Received {len(formatted_str)} characters")


EXPORTED_CLASS = Text_viewer_win
EXPORTED_NAME = "Text viewer"
