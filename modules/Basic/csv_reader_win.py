from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional
import csv
import uuid
import dearpygui.dearpygui as dpg
from loguru import logger

from core.window_base import WindowBase
from core.input_output_types import IOTypes
from core.file_explorer import FileExplorer


class CSVReader_win(WindowBase):
    """
    CSV Reader module that loads CSV files and emits a SAMPLE dict for each column.
    """

    label: str = "CSV Reader"
    win_width: int = 300
    win_height: int = 150
    persistent_fields: List[str] = ["label", "last_file_path"]

    accepted_input_types: List[IOTypes] = []
    outputs: Dict[str, Any] = {
        "Data": IOTypes.SAMPLE,
    }

    last_file_path: Optional[str] = None
    file_explorer: FileExplorer
    load_csv_btn_tag: str

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.file_explorer = FileExplorer()
        self.load_csv_btn_tag = f"csv_load_btn_{self.UUID}"

        with self.window():
            dpg.add_button(
                label="Load CSV File",
                tag=self.load_csv_btn_tag,
                callback=self.select_file,
                width=-1,
            )

        self.autosize_window()
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

    def select_file(self, *args: Any) -> None:
        """
        Open file picker and load selected CSV.
        """
        file_path = self.file_explorer.select_file(
            default_path=self.last_file_path or "",
            extensions=[("CSV files", "*.csv"), ("All files", "*.*")],
        )

        if file_path:
            self.last_file_path = file_path
            self.load_csv()

    def load_csv(self) -> None:
        """
        Load CSV file and emit a SAMPLE dict for each column.
        """
        if not self.last_file_path:
            return

        try:
            with open(self.last_file_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)

                header = next(reader)
                if len(header) < 2:
                    logger.warning(f"[{self.label}] CSV header must contain at least 2 columns.")
                    return

                sample_names = header[1:]
                x_values = []
                y_values = [[] for _ in sample_names]

                for row in reader:
                    if not row or len(row) < 2:
                        continue

                    try:
                        x_val = float(row[0])
                        x_values.append(x_val)
                    except ValueError:
                        continue

                    for i, val in enumerate(row[1:]):
                        if i < len(y_values):
                            try:
                                y_val = float(val)
                                y_values[i].append(y_val)
                            except ValueError:
                                y_values[i].append(0.0)

                for i, name in enumerate(sample_names):
                    sample = {
                        "name": name,
                        "uuid": str(uuid.uuid4()),
                        "x": x_values.copy(),
                        "y": y_values[i],
                        "action": "select",
                    }

                    for output_key in self.outputs:
                        for module in self.connections.get(output_key, []):
                            module.input_cb(sample=sample, data_type=IOTypes.SAMPLE)

        except Exception as e:
            logger.error(f"[{self.label}] Failed to load CSV file: {e}")

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Reload the last file on programmatic trigger.
        """
        self.load_csv()


EXPORTED_CLASS = CSVReader_win
EXPORTED_NAME = "CSV Reader"
