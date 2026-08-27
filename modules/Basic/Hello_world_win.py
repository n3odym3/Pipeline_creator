from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional
import dearpygui.dearpygui as dpg
from core.window_base import WindowBase
from core.input_output_types import IOTypes


class HelloWorld_win(WindowBase):
    """
    A simple DearPyGui window containing a button that acts as a trigger source.
    """

    def __init__(
        self,
        label: str = "Hello World",
        win_width: int = -1,
        win_height: int = -1,
        pos: Tuple[int, int] = (10, 10),
        uuid: Optional[str] = None,
        visible: bool = True,
    ) -> None:
        super().__init__(
            label=label,
            pos=pos,
            win_width=win_width,
            win_height=win_height,
            uuid=uuid,
            visible=visible,
        )

        self._persistent_fields = ["label"]
        self.accepted_input_types = []
        self.outputs = {
            "Out": IOTypes.TEXT,
        }
        self.connections = {k: [] for k in self.outputs}

        with dpg.window(
            label=self.label,
            width=self.win_width,
            height=self.win_height,
            pos=self.pos,
            tag=self.winID,
            show=self.visible,
        ):
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

