from __future__ import annotations
from typing import Any
import dearpygui.dearpygui as dpg
from core.window_base import WindowBase
from core.input_output_types import IOTypes


class HelloWorld_win(WindowBase):
    """
    A simple DearPyGui window containing a button that acts as a trigger source.
    """
    label = "Hello World"
    accepted_input_types = []
    outputs = {
        "Out": IOTypes.TEXT,
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        with self.window():
            dpg.add_button(label="Hello World", callback=self.trigger_cb)

        self.update_permission()

    def update_permission(self) -> None:
        """
        Adjust module permissions and UI elements based on the application mode.
        """
        from core.app_state import app_state
        mode = app_state.mode

        if dpg.does_item_exist(self.winID):
            is_user = mode == "user"
            dpg.configure_item(self.winID, no_close=is_user)

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Receive programmatic input trigger and forward it to output.
        """
        self.trigger_cb(*args, **kwargs)

    def trigger_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Emit the hello text value to all connected modules.
        """
        for output_key in self.outputs:
            connected_modules = self.connections.get(output_key, [])
            for module in connected_modules:
                module.input_cb("Hello World")


EXPORTED_CLASS = HelloWorld_win
EXPORTED_NAME = "Hello World"

