from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional
import dearpygui.dearpygui as dpg

from core.window_base import WindowBase
from core.input_output_types import IOTypes
from loguru import logger

class Text_viewer_win(WindowBase):
    """
    A DearPyGui window for displaying incoming text in a read-only multiline field.
    """

    content: str
    text_tag: str

    def __init__(
        self,
        label: str = "Text Viewer",
        win_width: int = 400,
        win_height: int = 300,
        pos: Tuple[int, int] = (10, 10),
        uuid: Optional[str] = None,
        outputs: Optional[Dict[str, Any]] = None,
        content: str = "(No content)",
        visible: bool = True,
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

        self._persistent_fields = ["label", "content"]
        self.accepted_input_types = [IOTypes.TEXT, IOTypes.CMD_DICT, IOTypes.TRIGGER]
        self.outputs = {}
        self.connections = {k: [] for k in self.outputs}

        # Initialize content from serialized state or default
        if not hasattr(self, "content"):
            self.content = content

        self.text_tag = f"text_viewer_content_{self.UUID}"

        with dpg.window(
            label=self.label,
            width=self.win_width,
            height=self.win_height,
            pos=self.pos,
            tag=self.winID,
            show=self.visible,
        ):
            dpg.add_input_text(
                multiline=True,
                readonly=True,
                tag=self.text_tag,
                default_value=self.content,
                width=-1,
                height=-1,
            )

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

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Update the displayed text with the received value.
        """
        text_input = kwargs.get("data")
        if text_input is None:
            text_input = kwargs.get("content")
        if text_input is None:
            text_input = kwargs.get("sample")
        if text_input is None and args:
            text_input = args[0]

        text_input_str = str(text_input) if text_input is not None else ""

        if dpg.does_item_exist(self.text_tag):
            dpg.set_value(self.text_tag, text_input_str)
            logger.debug(f"{self.label} received data: {text_input_str}")
        self.content = text_input_str


EXPORTED_CLASS = Text_viewer_win
EXPORTED_NAME = "Text viewer"
