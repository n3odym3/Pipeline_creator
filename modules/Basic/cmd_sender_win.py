from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional
import dearpygui.dearpygui as dpg
from loguru import logger

from core.window_base import WindowBase
from core.input_output_types import IOTypes


class CMDSender_win(WindowBase):
    """
    A modular window that allows the user to build a dictionary interactively
    and send it downstream on trigger.
    """

    status: Dict[str, str]
    _rows: Dict[str, Tuple[str, str]]
    _row_counter: int
    rows_container_tag: str
    add_key_btn_tag: str

    def __init__(
        self,
        label: str = "CMD Sender",
        win_width: int = -1,
        win_height: int = -1,
        pos: Tuple[int, int] = (10, 10),
        uuid: Optional[str] = None,
        outputs: Optional[Dict[str, Any]] = None,
        visible: bool = True,
        status: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(
            label=label,
            pos=pos,
            win_width=win_width,
            win_height=win_height,
            uuid=uuid,
            outputs=outputs,
            visible=visible,
        )

        self._persistent_fields = ["label", "status"]
        self.status = status if status is not None else {}

        self.accepted_input_types = [IOTypes.TRIGGER]
        self.outputs = {
            "Dict": IOTypes.CMD_DICT,
            "TXT": IOTypes.TEXT,
        }
        self.connections = {k: [] for k in self.outputs}

        self._rows = {}
        self._row_counter = 0

        self.rows_container_tag = f"rows_container_{self.UUID}"
        self.add_key_btn_tag = f"add_key_btn_{self.UUID}"

        with dpg.window(
            label=self.label,
            width=self.win_width,
            height=self.win_height,
            pos=self.pos,
            tag=self.winID,
            show=self.visible,
        ):
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Add Key",
                    tag=self.add_key_btn_tag,
                    callback=lambda: self._add_key_row(),
                )
                dpg.add_button(label="Send", callback=self.trigger_cb)

            with dpg.group(tag=self.rows_container_tag):
                pass

        for key, value in self.status.items():
            self._add_key_row(key=key, value=value)

        self.update_permission()

    def update_permission(self) -> None:
        """
        Adjust module permissions and UI elements based on the application mode.
        """
        from core.app_state import app_state
        mode = app_state.mode
        is_user = mode == "user"

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

    def trigger_cb(self, *args: Any, **kwargs: Any) -> None:
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
                if idx == 0:
                    module.input_cb(cmd)
                elif idx == 1:
                    module.input_cb(str(cmd))
                else:
                    logger.warning(f"[{self.label}] Unsupported output index {idx}")

    def _add_key_row(self, key: str = "", value: str = "") -> None:
        """
        Add a new key/value input row with delete capability.
        """
        row_idx = self._row_counter
        self._row_counter += 1

        row_tag = f"{self.winID}_row_{row_idx}"
        key_tag = f"{row_tag}_key"
        value_tag = f"{row_tag}_value"
        delete_tag = f"{row_tag}_del"

        from core.app_state import app_state
        is_user = app_state.mode == "user"

        with dpg.group(horizontal=True, parent=self.rows_container_tag, tag=row_tag):
            dpg.add_input_text(
                tag=key_tag,
                width=120,
                hint="key",
                callback=lambda *_: self._update_status(),
                default_value=key,
                readonly=is_user,
            )
            dpg.add_input_text(
                tag=value_tag,
                width=120,
                hint="value",
                callback=lambda *_: self._update_status(),
                default_value=value,
                readonly=is_user,
            )
            dpg.add_button(
                label="Delete",
                tag=delete_tag,
                callback=lambda: self._delete_row(row_tag),
                enabled=not is_user,
            )

        self._rows[row_tag] = (key_tag, value_tag)

    def _delete_row(self, row_tag: str) -> None:
        """
        Delete a row and update internal state.
        """
        if row_tag in self._rows:
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
