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
    - Declaring input/output types
    - Handling incoming data
    - Sending messages to downstream connected modules
    - Saving persistent fields (e.g., label, default_number)
    - Adapting UI permissions based on app_state mode
    """

    default_number: int
    btn_tag: str
    number_tag: str

    def __init__(
        self,
        label: str = "Template",
        win_width: int = 300,
        win_height: int = 200,
        pos: Tuple[int, int] = (10, 10),
        uuid: Optional[str] = None,
        outputs: Optional[Dict[str, Any]] = None,
        visible: bool = True,
        default_number: int = 10,
    ) -> None:
        """
        Initialize a new Template window with optional label, size, and position.

        Args:
            label: The window title and base ID.
            win_width: Width of the window.
            win_height: Height of the window.
            pos: Initial position of the window.
            uuid: Optional fixed UUID for serialization.
            outputs: Optional output dictionary.
            visible: Whether the window is shown on creation.
            default_number: A persistent parameter value example.
        """
        super().__init__(
            label=label,
            pos=pos,
            win_width=win_width,
            win_height=win_height,
            uuid=uuid,
            outputs=outputs,
            visible=visible,
        )

        # Declare which properties should be preserved when the workspace is saved
        self._persistent_fields = ["label", "default_number"]

        # Define input expectations
        self.accepted_input_types = [IOTypes.TEXT]

        # Define output terminals
        self.outputs = {
            "TEXT": IOTypes.TEXT,
            "NUMBER": IOTypes.NUMBER,
        }

        # Initialize connections map for each output terminal
        self.connections = {k: [] for k in self.outputs}

        # Unique DPG tags based on self.UUID to prevent element collision
        self.btn_tag = f"template_btn_{self.UUID}"
        self.number_tag = f"template_number_{self.UUID}"
        self.default_number = default_number

        # Build DPG Window UI
        with dpg.window(
            label=self.label,
            width=self.win_width,
            height=self.win_height,
            pos=self.pos,
            tag=self.winID,
            show=self.visible,
        ):
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

