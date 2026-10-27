from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from core.input_output_types import IOTypes
from core.window_base import WindowBase


class Template_win(WindowBase):
    """
    A basic example module window using the WindowBase system.

    This template demonstrates:
    - Declaring window defaults (label, win_width, win_height)
    - Declaring input/output types (checked by Module Health Check)
    - Saving persistent fields (e.g., default_number)
    - Handling incoming data via input_cb
    - Sending messages downstream to connected modules
    - Adapting UI permissions based on app_state mode
    """

    # 1. Window Configuration (Optional class defaults - can also be passed via kwargs)
    label = "Template"
    win_width = 300
    win_height = 200

    # 2. Input/Output definitions (Required by Module Health Check)
    accepted_input_types = [IOTypes.TEXT]
    outputs = {
        "TEXT": IOTypes.TEXT,
        "NUMBER": IOTypes.NUMBER,
    }

    # 3. Persistent fields saved into pipeline JSON (WindowBase automatically preserves 'label')
    persistent_fields = ["label", "default_number"]

    def __init__(self, default_number: int = 10, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.default_number = default_number
        self.btn_tag = f"template_btn_{self.UUID}"
        self.number_tag = f"template_number_{self.UUID}"

        # Build DPG Window UI using the convenient self.window() context manager
        with self.window():
            dpg.add_button(tag=self.btn_tag, label="Hello world", callback=self.trigger_cb)

            dpg.add_input_int(
                tag=self.number_tag,
                default_value=self.default_number,
                callback=lambda sender, value: setattr(self, "default_number", value),
            )

        # Always refresh permissions after UI creation
        self.update_permission()

    def update_permission(self) -> None:
        """
        Adapts the module UI based on the application mode.
        - 'user'     : Restricted access (e.g., read-only fields, hidden buttons).
        - 'advanced' : Standard access.
        - 'dev'      : Full access to all internals.
        """
        from core.app_state import app_state

        mode = app_state.mode

        # Example 1: Hide an element in 'user' mode
        if dpg.does_item_exist(self.btn_tag):
            dpg.configure_item(self.btn_tag, show=(mode != "user"))

        # Example 2: Lock editing in 'user' mode
        if dpg.does_item_exist(self.number_tag):
            is_readonly = mode == "user"
            dpg.configure_item(self.number_tag, readonly=is_readonly, enabled=not is_readonly)

        # Example 3: Hide the entire window if needed (e.g., dev-only module)
        if mode == "user":
            dpg.hide_item(self.winID)

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Default input callback: processes incoming arguments from upstream nodes.
        """
        logger.debug(f"{self.winID} received input: args={args}, kwargs={kwargs}")

    def trigger_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Triggered when the button is pressed.
        Sends data to downstream connected modules.
        """
        for idx, output_key in enumerate(self.outputs):
            connected_modules = self.connections.get(output_key, [])
            for module in connected_modules:
                if idx == 0:
                    module.input_cb("Hello World")
                elif idx == 1:
                    module.input_cb(self.default_number)


EXPORTED_CLASS = Template_win
EXPORTED_NAME = "Template"

