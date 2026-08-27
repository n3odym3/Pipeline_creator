from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional
import dearpygui.dearpygui as dpg
import numpy as np

from core.window_base import WindowBase
from core.input_output_types import IOTypes


class TestSample_win(WindowBase):
    """
    A simple DearPyGui window that generates test sample data on trigger.
    """

    trigger_btn_tag: str

    def __init__(
        self,
        label: str = "Test Sample",
        win_width: int = -1,
        win_height: int = -1,
        pos: Tuple[int, int] = (10, 10),
        uuid: Optional[str] = None,
        outputs: Optional[Dict[str, Any]] = None,
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

        self._persistent_fields = ["label"]

        self.accepted_input_types = []
        self.outputs = {
            "Data": IOTypes.SAMPLE,
        }
        self.connections = {k: [] for k in self.outputs}

        self.trigger_btn_tag = f"test_sample_trig_{self.UUID}"

        with dpg.window(
            label=self.label,
            width=self.win_width,
            height=self.win_height,
            pos=self.pos,
            tag=self.winID,
            show=self.visible,
        ):
            dpg.add_button(
                label="Trigger",
                tag=self.trigger_btn_tag,
                callback=self.trigger_cb,
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
        Programmatic trigger: calls trigger_cb.
        """
        self.trigger_cb(*args, **kwargs)

    def trigger_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Generate and emit random data in SAMPLE format.
        """
        y_data = list(np.random.randint(0, 101, size=100))
        x_data = list(range(len(y_data)))

        sample = {
            "name": "Test Sample",
            "uuid": "test_sample_1",
            "x": x_data,
            "y": y_data,
            "action": "select",
        }

        for output_key in self.outputs:
            for module in self.connections.get(output_key, []):
                module.input_cb(sample=sample, data_type=IOTypes.SAMPLE)


EXPORTED_CLASS = TestSample_win
EXPORTED_NAME = "Test Sample"
