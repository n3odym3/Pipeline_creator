from __future__ import annotations
import csv
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from core.window_base import WindowBase
from core.input_output_types import IOTypes
from core.file_explorer import file_explorer
from core.app_state import app_state
from .clipboard_injector import clipboardinjector


class Sample_container_win(WindowBase):
    """
    Centralized dataset and sample registry module.

    Stores, manages, previews, and serializes multiple 1D/2D data series (`SAMPLE` payloads).
    Supports drag-and-drop list reordering, mini-plot hover inspection, conflict resolution
    (override vs append), flexible multi-column CSV/TSV import and export, and reactive
    event broadcasting (select, unselect, rename).
    """

    description: str = "Centralized data series container with reordering, CSV import/export, and preview."

    label: str = "Sample Container"
    win_width: int = 300
    win_height: int = 200
    persistent_fields: List[str] = ["label", "export_path", "duplicate_mode"]

    accepted_input_types: List[IOTypes] = [
        IOTypes.SAMPLE,
        IOTypes.FOLDER_PATH,
        IOTypes.TRIGGER,
        IOTypes.CMD_DICT,
    ]
    
    outputs: Dict[str, Any] = {
        "Data": IOTypes.SAMPLE,
    }

    export_path: str = ""
    duplicate_mode: str = "override"
    samples_dict: Dict[str, Dict[str, Any]]
    samples_order: List[str]
    _handle_to_uuid: Dict[str, str]

    clear_samples_tag: str
    samples_group_tag: str
    export_header_tag: str
    x_axis_combo_tag: str
    import_btn_tag: str
    export_btn_tag: str
    export_tsv_btn_tag: str
    duplicate_mode_tag: str
    select_all_btn_tag: str
    deselect_all_btn_tag: str
    _handle_theme: int
    _combo_uuid_map: Dict[str, str]
    _last_combo_items: List[str]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.clear_samples_tag = f"clear_samples_{self.UUID}"
        self.samples_group_tag = f"samples_group_{self.UUID}"

        self.samples_dict = {}
        self.samples_order = []
        self._handle_to_uuid = {}

        # Export UI tags
        self.export_header_tag = f"export_header_{self.UUID}"
        self.x_axis_combo_tag = f"x_axis_combo_{self.UUID}"
        self.import_btn_tag = f"import_btn_{self.UUID}"
        self.export_btn_tag = f"export_btn_{self.UUID}"
        self.export_tsv_btn_tag = f"export_tsv_btn_{self.UUID}"
        self.duplicate_mode_tag = f"duplicate_mode_{self.UUID}"
        self.select_all_btn_tag = f"select_all_btn_{self.UUID}"
        self.deselect_all_btn_tag = f"deselect_all_btn_{self.UUID}"
        self._combo_uuid_map = {"individual": "individual", "index": "index"}
        self._last_combo_items = ["individual", "index"]

        # Handle theme (grey -> green on hover)
        self._handle_theme = dpg.add_theme()
        with dpg.theme_component(dpg.mvButton, parent=self._handle_theme):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (60, 60, 60, 180), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (60, 180, 80, 220), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (40, 150, 60, 255), category=dpg.mvThemeCat_Core)

        with self.window():
            with dpg.collapsing_header(label="Import/export", tag=self.export_header_tag):
                dpg.add_text("X axis reference:")
                dpg.add_combo(items=["individual", "index"], default_value="individual", tag=self.x_axis_combo_tag, width=-1)

                dpg.add_button(label="Import CSV", tag=self.import_btn_tag, callback=self._import_csv_cb, width=-1)
                dpg.add_button(label="Export CSV", tag=self.export_btn_tag, callback=self._export_csv_cb, width=-1)
                dpg.add_button(label="Export TSV", tag=self.export_tsv_btn_tag, callback=self.export_tsv_cb, width=-1)
                with dpg.tooltip(self.export_tsv_btn_tag):
                    dpg.add_text("Export selected samples to clipboard in TSV format (aligned + individual)")

                dpg.add_text("Duplicate:")
                dpg.add_combo(
                    items=["override", "append"],
                    default_value=self.duplicate_mode,
                    tag=self.duplicate_mode_tag,
                    width=-1,
                    callback=self._on_duplicate_mode_changed,
                )
                with dpg.tooltip(self.duplicate_mode_tag):
                    dpg.add_text("Action when a sample has the same name:\n- 'override': replaces existing data\n- 'append': adds a new sample with a suffix")

            dpg.add_button(label="Clear samples", width=-1, tag=self.clear_samples_tag, callback=self.clear_samples_cb)
            dpg.add_button(label="Select all", width=-1, tag=self.select_all_btn_tag, callback=self.select_all_samples_cb)
            dpg.add_button(label="Deselect all", width=-1, tag=self.deselect_all_btn_tag, callback=self.deselect_all_samples_cb)
            dpg.add_separator()

            dpg.add_group(tag=self.samples_group_tag)

        self.update_permission()

    def update_permission(self) -> None:
        """
        Adjust module permissions and UI visibility based on the application mode.
        """
        mode = app_state.mode
        if dpg.does_item_exist(self.winID):
            dpg.configure_item(self.winID, no_close=(mode == "user"))

    def is_ready(self) -> bool:
        """
        Check if the module is ready to receive input data.

        Returns:
            bool: Always True.
        """
        return True

    def _get_base_dir(self) -> str:
        """
        Retrieve working base directory from self.export_path.
        """
        if self.export_path:
            if os.path.isdir(self.export_path):
                return self.export_path
            try:
                return os.path.dirname(self.export_path)
            except Exception:
                pass
        return ""

    def _on_duplicate_mode_changed(self, sender: Any = None, value: str = "", *args, **kwargs) -> None:
        """Callback triggered when the duplicate mode combo selection changes."""
        if value:
            self.duplicate_mode = value
        elif dpg.does_item_exist(self.duplicate_mode_tag):
            self.duplicate_mode = dpg.get_value(self.duplicate_mode_tag)

    def add_or_update_sample(
        self,
        name: str,
        x_data: Any,
        y_data: Any,
        selected: bool = False,
    ) -> str:
        """
        Add a sample to the registry or update it if already registered.

        Args:
            name: Sample identifier name.
            x_data: Array of X coordinates.
            y_data: Array of measured Y values.
            selected: Whether to initially check/activate the sample.

        Returns:
            str: Unique UUID assigned to the sample.
        """
        mode = dpg.get_value(self.duplicate_mode_tag) if dpg.does_item_exist(self.duplicate_mode_tag) else self.duplicate_mode
        self.duplicate_mode = mode

        existing_uuid = None
        for k, v in self.samples_dict.items():
            if v["name"] == name:
                existing_uuid = k
                break

        if existing_uuid and mode == "override":
            self.samples_dict[existing_uuid]["x"] = x_data
            self.samples_dict[existing_uuid]["y"] = y_data

            plot_tag = f"sample_plot_{existing_uuid}"
            if dpg.does_item_exist(plot_tag) and y_data is not None:
                dpg.set_value(plot_tag, y_data)

            checkbox_tag = f"sample_checkbox_{existing_uuid}"
            if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
                self._send_sample(self.samples_dict[existing_uuid], action="select")
            self._update_x_axis_combo()
            return existing_uuid

        new_uuid = str(uuid.uuid4())
        resolved_name = name
        if mode == "append" and existing_uuid:
            base = name
            counter = 1
            existing_names = {s["name"] for s in self.samples_dict.values()}
            while f"{base}_{counter}" in existing_names:
                counter += 1
            resolved_name = f"{base}_{counter}"

        self.samples_dict[new_uuid] = {
            "x": x_data,
            "y": y_data,
            "name": resolved_name,
            "uuid": new_uuid,
        }

        self.samples_order.append(new_uuid)
        self._append_sample_row(new_uuid, selected=selected)
        self._update_x_axis_combo()
        if selected:
            self._send_sample(self.samples_dict[new_uuid], action="select")
        return new_uuid

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Accept inputs and route them based on input types (SAMPLE, FOLDER_PATH, TRIGGER, or CMD_DICT).
        """
        data_type = kwargs.get("data_type")
        data = kwargs.get("data") or kwargs.get("folder_path") or kwargs.get("path") or (args[0] if args else None)

        cmd = None
        if data_type == IOTypes.CMD_DICT:
            cmd = data
        elif isinstance(data, dict) and "y" not in data:
            cmd = data

        if cmd and isinstance(cmd, dict):
            if cmd.get("export") == "tsv":
                self.export_tsv_cb()
            elif cmd.get("export"):
                self._auto_save_csv()
            return

        if data_type == IOTypes.FOLDER_PATH or (isinstance(data, str) and os.path.isdir(data)):
            self.export_path = data
            logger.info(f"[{self.label}] Export/import folder path configured to: {data}")
            return

        if data_type == IOTypes.TRIGGER or (isinstance(data, str) and data.lower() == "save"):
            if isinstance(data, str) and data.lower() != "save":
                pass
            else:
                self._auto_save_csv()
                return

        sample = kwargs.get("sample") or (data if isinstance(data, dict) and "y" in data else None)
        if not sample:
            return

        input_name = sample.get("name", "Sample")
        action = sample.get("action", "select")

        existing_uuid = None
        for k, v in self.samples_dict.items():
            if v["name"] == input_name:
                existing_uuid = k
                break

        if action in ["unselect", "delete"]:
            if existing_uuid:
                self.delete_sample_cb(None, None, existing_uuid)
            return

        self.add_or_update_sample(input_name, sample.get("x"), sample.get("y"), selected=True)

    def _append_sample_row(self, sample_uuid: str, selected: bool = True) -> None:
        """
        Add a single sample list item row widget into the scrollable UI.
        """
        group_tag = f"sample_group_{sample_uuid}"
        if dpg.does_item_exist(group_tag):
            return

        sample = self.samples_dict.get(sample_uuid)
        if not sample:
            return
        input_name = sample["name"]

        dpg.add_group(horizontal=True, tag=group_tag, parent=self.samples_group_tag)

        handle_tag = f"sample_handle_{sample_uuid}"
        dpg.add_button(
            label="\uf0c9",
            tag=handle_tag,
            parent=group_tag,
            drop_callback=self._on_drop_cb,
            payload_type="SAMPLE_REORDER",
            user_data=sample_uuid,
        )
        dpg.add_drag_payload(
            parent=handle_tag,
            payload_type="SAMPLE_REORDER",
            drag_data=sample_uuid,
        )
        dpg.bind_item_theme(handle_tag, self._handle_theme)
        self._handle_to_uuid[handle_tag] = sample_uuid

        checkbox_tag = f"sample_checkbox_{sample_uuid}"
        dpg.add_checkbox(
            tag=checkbox_tag,
            default_value=selected,
            callback=self.sample_checkbox_cb,
            user_data=sample_uuid,
            parent=group_tag,
        )

        with dpg.tooltip(checkbox_tag):
            y_data = sample.get("y")
            if y_data is None:
                y_data = []
            dpg.add_simple_plot(
                tag=f"sample_plot_{sample_uuid}",
                default_value=y_data,
                height=100,
                width=200,
            )

        with dpg.popup(checkbox_tag, mousebutton=dpg.mvMouseButton_Right):
            dpg.add_menu_item(label="Delete", user_data=sample_uuid, callback=self.delete_sample_cb)

        dpg.add_button(label="\uf2ed", callback=self.delete_sample_cb, user_data=sample_uuid, parent=group_tag)

        dpg.add_input_text(
            tag=f"sample_name_{sample_uuid}",
            default_value=input_name,
            callback=self.sample_name_cb,
            user_data=sample_uuid,
            parent=group_tag,
            width=-1,
        )
        with dpg.popup(dpg.last_item(), mousebutton=dpg.mvMouseButton_Right):
            dpg.add_menu_item(label="Delete", user_data=sample_uuid, callback=self.delete_sample_cb)

    def _rebuild_ui(self) -> None:
        """
        Reconstruct the entire sample list UI according to current reordering.
        """
        self._handle_to_uuid.clear()
        state = {}
        for uid in list(self.samples_order):
            cb_tag = f"sample_checkbox_{uid}"
            name_tag = f"sample_name_{uid}"
            state[uid] = {
                "selected": dpg.get_value(cb_tag) if dpg.does_item_exist(cb_tag) else True,
                "name": dpg.get_value(name_tag) if dpg.does_item_exist(name_tag) else self.samples_dict[uid].get("name", ""),
            }
            group_tag = f"sample_group_{uid}"
            if dpg.does_item_exist(group_tag):
                dpg.delete_item(group_tag)

        for uid in list(self.samples_order):
            if uid not in self.samples_dict:
                continue
            s = state.get(uid, {})
            name = s.get("name", self.samples_dict[uid].get("name", ""))
            selected = s.get("selected", True)
            self.samples_dict[uid]["name"] = name
            self._append_sample_row(uid, selected=selected)
        self._update_x_axis_combo()

    def _on_drop_cb(self, sender: Any, app_data: Any, user_data: Any, *args, **kwargs) -> None:
        """
        Handle drag and drop reordering of sample rows.
        """
        target_uuid = self._handle_to_uuid.get(str(sender))
        if target_uuid is None:
            logger.warning(f"DROP: unknown handle sender={sender!r}")
            return

        source_uuid = app_data
        if source_uuid == target_uuid:
            return

        if source_uuid not in self.samples_order or target_uuid not in self.samples_order:
            logger.warning(f"DROP: UUID not in samples_order. source={source_uuid!r} target={target_uuid!r}")
            return

        self.samples_order.remove(source_uuid)
        target_idx = self.samples_order.index(target_uuid)
        self.samples_order.insert(target_idx, source_uuid)
        self._rebuild_ui()

    def _update_x_axis_combo(self) -> None:
        """
        Refresh the X-axis reference dropdown combo items.
        """
        if not dpg.does_item_exist(self.x_axis_combo_tag):
            return
        items = ["individual", "index"]
        self._combo_uuid_map = {"individual": "individual", "index": "index"}
        for uid in self.samples_order:
            s = self.samples_dict.get(uid)
            if not s:
                continue
            item_label = f"{s['name']}##xaxis_{s['uuid']}"
            items.append(item_label)
            self._combo_uuid_map[item_label] = s["name"]

        if items == self._last_combo_items:
            return
        self._last_combo_items = items
        current_val = dpg.get_value(self.x_axis_combo_tag)
        dpg.configure_item(self.x_axis_combo_tag, items=items)
        if current_val in items:
            dpg.set_value(self.x_axis_combo_tag, current_val)
        else:
            dpg.set_value(self.x_axis_combo_tag, "individual")

    def _get_selected_samples(self) -> List[Dict[str, Any]]:
        """
        Return a list of all currently checked sample dictionaries.
        """
        selected = []
        for sample_uuid in self.samples_order:
            sample = self.samples_dict.get(sample_uuid)
            if not sample:
                continue
            checkbox_tag = f"sample_checkbox_{sample_uuid}"
            if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
                selected.append(sample)
        return selected

    def _write_csv(self, file_path: str, delimiter: str = ",") -> bool:
        """
        Write selected samples to disk as CSV or TSV.
        """
        selected_samples = self._get_selected_samples()
        if not selected_samples:
            logger.warning("Export aborted: no samples selected.")
            return False

        x_ref_label = dpg.get_value(self.x_axis_combo_tag) if dpg.does_item_exist(self.x_axis_combo_tag) else "individual"
        x_ref = self._combo_uuid_map.get(x_ref_label, x_ref_label)

        parent_dir = os.path.dirname(file_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        if x_ref == "individual":
            headers = []
            for s in selected_samples:
                headers.extend([f"{s['name']}_X", f"{s['name']}_Y"])

            max_len = 0
            for s in selected_samples:
                x_raw = s.get("x")
                y_raw = s.get("y")
                len_x = len(x_raw) if x_raw is not None else 0
                len_y = len(y_raw) if y_raw is not None else 0
                max_len = max(max_len, len_x, len_y)

            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=delimiter)
                writer.writerow(headers)
                for i in range(max_len):
                    row = []
                    for s in selected_samples:
                        x_raw = s.get("x")
                        y_raw = s.get("y")
                        x_list = list(x_raw) if x_raw is not None else []
                        y_list = list(y_raw) if y_raw is not None else []
                        x_val = x_list[i] if i < len(x_list) else ""
                        y_val = y_list[i] if i < len(y_list) else ""
                        row.extend([x_val, y_val])
                    writer.writerow(row)
        else:
            x_values = None
            x_header = "Index"
            if x_ref != "index":
                for s in self.samples_dict.values():
                    if s["name"] == x_ref:
                        x_raw = s.get("x")
                        if x_raw is not None:
                            try:
                                x_values = list(x_raw)
                            except Exception:
                                x_values = [x_raw]
                        x_header = s["name"]
                        break

            max_len = 0
            for s in selected_samples:
                y_raw = s.get("y")
                if y_raw is not None:
                    try:
                        max_len = max(max_len, len(y_raw))
                    except Exception:
                        max_len = max(max_len, 1)

            if x_values is None:
                x_values = list(range(max_len))

            if len(x_values) < max_len:
                last_val = x_values[-1] if x_values else 0
                x_values = x_values + [last_val] * (max_len - len(x_values))
            elif len(x_values) > max_len:
                x_values = x_values[:max_len]

            headers = [x_header] + [s["name"] for s in selected_samples]

            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=delimiter)
                writer.writerow(headers)
                for i in range(max_len):
                    row = [x_values[i]]
                    for s in selected_samples:
                        y_raw = s.get("y")
                        y_list = list(y_raw) if y_raw is not None else []
                        y_val = y_list[i] if i < len(y_list) else ""
                        row.append(y_val)
                    writer.writerow(row)

        logger.success(f"Samples successfully exported to {file_path}")
        return True

    def export_tsv_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Export selected samples to system clipboard in aligned + individual TSV columns.
        """
        selected_samples = self._get_selected_samples()
        if not selected_samples:
            logger.warning("Export aborted: no samples selected.")
            return

        all_x_set = set()
        series_dicts = []
        for s in selected_samples:
            s_map = {}
            x_raw = s.get("x")
            y_raw = s.get("y")
            if x_raw is not None and y_raw is not None:
                for xi, yi in zip(x_raw, y_raw):
                    s_map[xi] = yi
                    all_x_set.add(xi)
            series_dicts.append(s_map)

        sorted_x = sorted(list(all_x_set))
        max_rows = max(len(sorted_x), max((len(s.get("x") or []) for s in selected_samples), default=0))

        tsv_lines = []
        headers = ["X (Aligned)"] + [s["name"] for s in selected_samples]
        for s in selected_samples:
            headers.append(f"{s['name']} (X)")
            headers.append(f"{s['name']} (Y)")
        tsv_lines.append("\t".join(headers))

        for r in range(max_rows):
            row = []
            if r < len(sorted_x):
                cur_x = sorted_x[r]
                row.append(str(cur_x))
                for s_map in series_dicts:
                    row.append(str(s_map[cur_x]) if cur_x in s_map else "")
            else:
                row.append("")
                for _ in series_dicts:
                    row.append("")

            for s in selected_samples:
                x_data = s.get("x") or []
                y_data = s.get("y") or []
                row.append(str(x_data[r]) if r < len(x_data) else "")
                row.append(str(y_data[r]) if r < len(y_data) else "")

            tsv_lines.append("\t".join(row))

        clipboardinjector.send_text("\n".join(tsv_lines))
        logger.success(f"Exported {len(selected_samples)} samples to TSV clipboard.")

    def _auto_save_csv(self) -> None:
        """
        Headlessly export selected samples into the pre-configured export_path.
        """
        target_dir = self._get_base_dir()
        if not target_dir:
            logger.warning(f"[{self.label}] Auto-save failed: No export directory configured.")
            return

        output_file = os.path.join(target_dir, f"{self.label.replace(' ', '_')}_export.csv")
        self._write_csv(output_file)

    def _export_csv_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Prompt user with file dialog to export selected samples to CSV.
        """
        selected_samples = self._get_selected_samples()
        if not selected_samples:
            logger.warning("Export aborted: no samples selected.")
            return

        def save_callback(file_path: str, selected_ext: str = "") -> None:
            if file_path:
                if not file_path.lower().endswith(".csv") and not file_path.lower().endswith(".tsv"):
                    file_path += ".csv"
                self._write_csv(file_path)

        file_explorer.save_file(
            default_path=self._get_base_dir(),
            extensions=[("CSV files", "*.csv"), ("TSV files", "*.tsv"), ("All files", "*.*")],
            callback=save_callback,
        )

    def _import_csv_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Prompt user with file dialog to load samples from CSV or TSV files.
        """
        def load_callback(file_path: str, selected_ext: str = "") -> None:
            if not file_path or not os.path.exists(file_path):
                return
            try:
                delimiter = "\t" if file_path.lower().endswith(".tsv") else ","
                with open(file_path, "r", encoding="utf-8") as f:
                    dialect = csv.Sniffer().sniff(f.read(2048))
                    delimiter = dialect.delimiter
            except Exception:
                pass

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    reader = csv.reader(f, delimiter=delimiter)
                    headers = next(reader)
                    columns: List[List[Any]] = [[] for _ in headers]
                    for row in reader:
                        for col_idx, cell in enumerate(row):
                            if col_idx < len(columns):
                                columns[col_idx].append(cell.strip())

                # Detect if layout is paired (_X and _Y) or single X shared
                paired_detected = False
                for idx, h in enumerate(headers):
                    if h.endswith("_X") and (idx + 1 < len(headers)) and headers[idx + 1].endswith("_Y"):
                        paired_detected = True
                        break

                if paired_detected:
                    idx = 0
                    while idx < len(headers):
                        h = headers[idx]
                        if h.endswith("_X") and (idx + 1 < len(headers)) and headers[idx + 1].endswith("_Y"):
                            sample_name = h[:-2]
                            x_col = [float(v) for v in columns[idx] if v != ""]
                            y_col = [float(v) for v in columns[idx + 1] if v != ""]
                            self.add_or_update_sample(sample_name, x_col, y_col, selected=True)
                            idx += 2
                        else:
                            idx += 1
                else:
                    x_vals = [float(v) for v in columns[0] if v != ""] if len(columns) > 0 else []
                    for col_idx in range(1, len(headers)):
                        y_vals = [float(v) for v in columns[col_idx] if v != ""]
                        sample_name = headers[col_idx]
                        self.add_or_update_sample(sample_name, x_vals, y_vals, selected=True)

                logger.success(f"Imported samples from {file_path}")
            except Exception as e:
                logger.error(f"Failed to import samples from {file_path}: {e}")

        file_explorer.select_file(
            default_path=self._get_base_dir(),
            extensions=[("CSV / TSV files", "*.csv;*.tsv"), ("CSV files", "*.csv"), ("TSV files", "*.tsv"), ("All files", "*.*")],
            callback=load_callback,
        )

    def sample_checkbox_cb(self, sender: Any, app_data: Any, user_data: Any, *args: Any) -> None:
        """
        Toggle selection state of an individual sample and emit select/unselect event.
        """
        sample_uuid = user_data
        sample = self.samples_dict.get(sample_uuid)
        if not sample:
            return

        action = "select" if app_data else "unselect"
        self._send_sample(sample, action=action)

    def sample_name_cb(self, sender: Any, app_data: Any, user_data: Any, *args: Any) -> None:
        """
        Update sample label inline and emit rename event.
        """
        sample_uuid = user_data
        new_name = str(app_data).strip()
        if new_name and sample_uuid in self.samples_dict:
            self.samples_dict[sample_uuid]["name"] = new_name
            checkbox_tag = f"sample_checkbox_{sample_uuid}"
            if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
                self._send_sample(self.samples_dict[sample_uuid], action="rename")
            self._update_x_axis_combo()

    def _send_sample(self, sample: Dict[str, Any], action: str = "select") -> None:
        """
        Emit sample payload to connected downstream nodes.

        Args:
            sample: Sample dictionary with keys 'name', 'uuid', 'x', 'y'.
            action: Action event string ('select', 'unselect', 'rename').
        """
        output_sample = {
            "name": sample["name"],
            "uuid": sample["uuid"],
            "x": sample["x"],
            "y": sample["y"],
            "action": action,
        }
        for output_key in self.outputs:
            for module in self.connections.get(output_key, []):
                try:
                    module.input_cb(sample=output_sample, data_type=IOTypes.SAMPLE)
                except Exception as e:
                    logger.debug(f"Sample emit error: {e}")

    def delete_sample_cb(self, sender: Any, app_data: Any, user_data: Any, *args: Any) -> None:
        """
        Delete a single sample from memory and UI list.
        """
        sample_uuid = user_data
        if sample_uuid in self.samples_dict:
            checkbox_tag = f"sample_checkbox_{sample_uuid}"
            if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
                self._send_sample(self.samples_dict[sample_uuid], action="unselect")

            del self.samples_dict[sample_uuid]
            if sample_uuid in self.samples_order:
                self.samples_order.remove(sample_uuid)
            dpg.delete_item(f"sample_group_{sample_uuid}")

        self._update_x_axis_combo()

    def select_all_samples_cb(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """
        Select all registered samples simultaneously.
        """
        for sample_uuid in self.samples_dict:
            checkbox_tag = f"sample_checkbox_{sample_uuid}"
            if dpg.does_item_exist(checkbox_tag):
                dpg.set_value(checkbox_tag, True)
                self.sample_checkbox_cb(checkbox_tag, True, sample_uuid)

    def deselect_all_samples_cb(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """
        Deselect all registered samples simultaneously.
        """
        for sample_uuid in self.samples_dict:
            checkbox_tag = f"sample_checkbox_{sample_uuid}"
            if dpg.does_item_exist(checkbox_tag):
                dpg.set_value(checkbox_tag, False)
                self.sample_checkbox_cb(checkbox_tag, False, sample_uuid)

    def clear_samples_cb(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """
        Clear all samples from memory, emit unselect events, and clear UI list.
        """
        for sample_uuid in list(self.samples_dict.keys()):
            checkbox_tag = f"sample_checkbox_{sample_uuid}"
            if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
                self._send_sample(self.samples_dict[sample_uuid], action="unselect")
            dpg.delete_item(f"sample_group_{sample_uuid}")
            del self.samples_dict[sample_uuid]

        self.samples_order.clear()
        self._update_x_axis_combo()


EXPORTED_CLASS = Sample_container_win
EXPORTED_NAME = "Sample Container"
