from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class CMDSender_win(WindowBase):
    """
    A modular window that allows the user to build a dictionary interactively
    and send it downstream on trigger.
    """

    label: str = "CMD Sender"
    win_width: int = display_scaling.scale(350)
    win_height: int = display_scaling.scale(260)
    description: str = "Build key-value command dictionaries interactively and send downstream."

    accepted_input_types: list[str] = [IOTypes.TRIGGER]
    outputs: dict[str, str] = {
        "Dict": IOTypes.CMD_DICT,
        "TXT": IOTypes.TEXT,
    }
    persistent_fields: list[str] = ["label", "status"]

    def __init__(
        self,
        status: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the CMD Sender module window.

        Args:
            status: Optional pre-configured dictionary of key-value pairs.
            **kwargs: Additional parameters forwarded to WindowBase.
        """
        super().__init__(**kwargs)

        self.status: dict[str, str] = getattr(self, "status", None) or (status if status is not None else {})
        self._rows: dict[str, Tuple[str, str]] = {}
        self._row_counter: int = 0

        self.rows_container_tag: str = f"rows_container_{self.UUID}"
        self.add_key_btn_tag: str = f"add_key_btn_{self.UUID}"

        self._build_ui()

        for key, value in list(self.status.items()):
            self._add_key_row(key=key, value=value)

        self.update_permission()

    def _build_ui(self) -> None:
        """Construct the initial DearPyGui window layout."""
        with self.window():
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Add Key",
                    tag=self.add_key_btn_tag,
                    callback=self._on_add_key_clicked,
                )
                dpg.add_button(label="Send", callback=self.trigger_cb)

            with dpg.group(tag=self.rows_container_tag):
                pass

    def update_permission(self) -> None:
        """
        Adjust module permissions and UI elements based on the application mode.
        """
        from core.app_state import app_state

        is_user = getattr(app_state, "mode", "dev") == "user"

        if dpg.does_item_exist(self.winID):
            dpg.configure_item(self.winID, no_close=is_user)

        for row_tag, (key_tag, value_tag) in self._rows.items():
            delete_tag = f"{row_tag}_del"
            if dpg.does_item_exist(delete_tag):
                dpg.configure_item(delete_tag, enabled=not is_user)
            if dpg.does_item_exist(key_tag):
                dpg.configure_item(key_tag, readonly=is_user)
            if dpg.does_item_exist(value_tag):
                dpg.configure_item(value_tag, readonly=is_user)

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Propagate upstream trigger as-is.
        """
        self.trigger_cb(*args, **kwargs)

    def trigger_cb(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Collect key-value entries into a dictionary and send to downstream modules.
        """
        cmd: dict[str, str] = {}

        for key_tag, value_tag in self._rows.values():
            if dpg.does_item_exist(key_tag) and dpg.does_item_exist(value_tag):
                key = dpg.get_value(key_tag).strip()
                value = dpg.get_value(value_tag).strip()
                if key:
                    cmd[key] = value

        logger.debug(f"{self.winID} sending cmd={cmd}")

        for idx, output_key in enumerate(self.outputs):
            connected_modules = self.connections.get(output_key, [])
            for module in connected_modules:
                try:
                    if idx == 0:
                        module.input_cb(cmd, data_type=IOTypes.CMD_DICT)
                    elif idx == 1:
                        module.input_cb(str(cmd), data_type=IOTypes.TEXT)
                    else:
                        logger.warning(f"[{self.label}] Unsupported output index {idx}")
                except Exception as e:
                    logger.error(
                        f"[{self.label}] Error sending to {getattr(module, 'label', 'module')}: {e}"
                    )

    def _on_add_key_clicked(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Callback to add a new key/value row."""
        self._add_key_row()

    def _on_text_changed(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Callback when key or value text changes."""
        self._update_status()

    def _on_delete_row_clicked(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Callback to delete a specified row via user_data."""
        row_tag = str(user_data or "")
        if row_tag:
            self._delete_row(row_tag)

    def _add_key_row(self, key: str = "", value: str = "") -> None:
        """
        Add a new key/value input row with delete capability.
        """
        from core.app_state import app_state

        scale = display_scaling.scale
        is_user = getattr(app_state, "mode", "dev") == "user"

        row_idx = self._row_counter
        self._row_counter += 1

        row_tag = f"{self.winID}_row_{row_idx}"
        key_tag = f"{row_tag}_key"
        value_tag = f"{row_tag}_value"
        delete_tag = f"{row_tag}_del"

        with dpg.group(horizontal=True, parent=self.rows_container_tag, tag=row_tag):
            dpg.add_input_text(
                tag=key_tag,
                width=scale(120),
                hint="key",
                callback=self._on_text_changed,
                default_value=key,
                readonly=is_user,
            )
            dpg.add_input_text(
                tag=value_tag,
                width=scale(120),
                hint="value",
                callback=self._on_text_changed,
                default_value=value,
                readonly=is_user,
            )
            dpg.add_button(
                label="Delete",
                tag=delete_tag,
                user_data=row_tag,
                callback=self._on_delete_row_clicked,
                enabled=not is_user,
            )

        self._rows[row_tag] = (key_tag, value_tag)

    def _delete_row(self, row_tag: str) -> None:
        """
        Delete a row and update internal state.
        """
        if row_tag in self._rows:
            if dpg.does_item_exist(row_tag):
                dpg.delete_item(row_tag)
            del self._rows[row_tag]
            logger.debug(f"{self.winID} removed row {row_tag}")
            self._update_status()

    def _update_status(self) -> None:
        """
        Refresh self.status to reflect current key/value inputs.
        """
        current = {}
        for key_tag, value_tag in self._rows.values():
            if dpg.does_item_exist(key_tag) and dpg.does_item_exist(value_tag):
                key = dpg.get_value(key_tag).strip()
                value = dpg.get_value(value_tag).strip()
                if key:
                    current[key] = value
        self.status = current


EXPORTED_CLASS = CMDSender_win
EXPORTED_NAME = "Dict Sender"
