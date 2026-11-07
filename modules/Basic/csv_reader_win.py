from __future__ import annotations

import csv
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.file_explorer import FileExplorer
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class CSVReader_win(WindowBase):
    """
    CSV Reader module that loads CSV files and emits a SAMPLE dict for each column.
    """

    label: str = "CSV Reader"
    win_width: int = display_scaling.scale(300)
    win_height: int = display_scaling.scale(150)
    description: str = "Load CSV files and emit SAMPLE dictionaries for each column downstream."

    accepted_input_types: list[str] = [IOTypes.TRIGGER]
    outputs: dict[str, str] = {
        "Data": IOTypes.SAMPLE,
    }
    persistent_fields: list[str] = ["label", "last_file_path"]

    def __init__(
        self,
        last_file_path: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the CSV Reader module window.

        Args:
            last_file_path: Optional initial or restored CSV file path.
            **kwargs: Additional parameters forwarded to WindowBase.
        """
        super().__init__(**kwargs)

        self.last_file_path: Optional[str] = getattr(self, "last_file_path", None) or last_file_path
        self.file_explorer: FileExplorer = FileExplorer()
        self.load_csv_btn_tag: str = f"csv_load_btn_{self.UUID}"

        self._build_ui()
        self.update_permission()

    def _build_ui(self) -> None:
        """Construct DearPyGui widgets for CSV Reader."""
        with self.window():
            dpg.add_button(
                label="Load CSV File",
                tag=self.load_csv_btn_tag,
                callback=self.select_file,
                width=-1,
            )

        self.autosize_window()

    def update_permission(self) -> None:
        """
        Adjust module permissions and UI elements based on the application mode.
        """
        from core.app_state import app_state

        is_user = getattr(app_state, "mode", "dev") == "user"
        if dpg.does_item_exist(self.winID):
            dpg.configure_item(self.winID, no_close=is_user)

    def select_file(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
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
        if not self.last_file_path or not os.path.exists(self.last_file_path):
            logger.warning(f"[{self.label}] No valid file path provided or file does not exist: {self.last_file_path}")
            return

        try:
            with open(self.last_file_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)

                header = next(reader)
                if len(header) < 2:
                    logger.warning(f"[{self.label}] CSV header must contain at least 2 columns.")
                    return

                sample_names = header[1:]
                x_values: list[float] = []
                y_values: list[list[float]] = [[] for _ in sample_names]

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
                    sample: dict[str, Any] = {
                        "name": name,
                        "uuid": str(uuid.uuid4()),
                        "x": x_values.copy(),
                        "y": y_values[i],
                        "action": "select",
                    }

                    for output_key in self.outputs:
                        for module in self.connections.get(output_key, []):
                            try:
                                module.input_cb(sample=sample, data=sample, data_type=IOTypes.SAMPLE)
                            except Exception as e:
                                logger.error(
                                    f"[{self.label}] Error sending sample to {getattr(module, 'label', 'module')}: {e}"
                                )

        except Exception as e:
            logger.error(f"[{self.label}] Failed to load CSV file: {e}")

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Reload the last file on programmatic trigger or load given file path.
        """
        if args and isinstance(args[0], str):
            self.last_file_path = args[0]
        elif "data" in kwargs and isinstance(kwargs["data"], str):
            self.last_file_path = kwargs["data"]

        self.load_csv()


EXPORTED_CLASS = CSVReader_win
EXPORTED_NAME = "CSV Reader"
