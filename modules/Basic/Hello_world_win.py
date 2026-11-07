from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class HelloWorld_win(WindowBase):
    """
    A simple DearPyGui window containing a button that acts as a trigger source.
    """

    label: str = "Hello World"
    win_width: int = display_scaling.scale(200)
    win_height: int = display_scaling.scale(100)
    description: str = "Simple button module emitting a 'Hello World' text trigger downstream."

    accepted_input_types: list[str] = [IOTypes.TRIGGER]
    outputs: dict[str, str] = {
        "Out": IOTypes.TEXT,
    }
    persistent_fields: list[str] = ["label"]

    def __init__(self, **kwargs: Any) -> None:
        """
        Initialize the Hello World window module.

        Args:
            **kwargs: Additional parameters forwarded to WindowBase.
        """
        super().__init__(**kwargs)

        self.btn_tag: str = f"hello_world_btn_{self.UUID}"

        with self.window():
            dpg.add_button(
                tag=self.btn_tag,
                label="Hello World",
                callback=self.trigger_cb,
                width=-1,
            )

        self.update_permission()

    def update_permission(self) -> None:
        """
        Adjust module permissions and UI elements based on the application mode.
        """
        from core.app_state import app_state

        is_user = getattr(app_state, "mode", "dev") == "user"
        if dpg.does_item_exist(self.winID):
            dpg.configure_item(self.winID, no_close=is_user)

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Receive programmatic input trigger and forward it to output.
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
        Emit the hello text value to all connected modules.
        """
        for output_key in self.outputs:
            connected_modules = self.connections.get(output_key, [])
            for module in connected_modules:
                try:
                    module.input_cb("Hello World", data="Hello World", data_type=IOTypes.TEXT)
                except Exception as e:
                    logger.error(
                        f"[{self.label}] Error sending to {getattr(module, 'label', 'module')}: {e}"
                    )


EXPORTED_CLASS = HelloWorld_win
EXPORTED_NAME = "Hello World"
