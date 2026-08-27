"""
Visual Configuration Editor for Pipeline Creator.

Allows graphical editing of config/config.json using annotations defined in
config/config_anotation.json. Fully supports folders, files, dropdowns,
checkboxes, and text inputs, scaling dynamically with the UI.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.config_manager import config
from core.file_explorer import file_explorer
from core.paths import CONFIG_DIR, PROJECT_ROOT


class ConfigEditor:
    """
    Dynamic graphical editor for config.json.
    """

    def __init__(self) -> None:
        self.winID: str = "config_editor_win"
        # Map: DPG tag -> (config_path, original_value_type)
        self._widgets: dict[str | int, tuple[list[str], type]] = {}

    def show(self) -> None:
        """Create or recreate the visual config editor window."""
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

        # Scale layout dimensions
        s = display_scaling.scale
        win_w = s(650)
        win_h = s(550)

        # Center the window dynamically based on viewport size
        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        self._widgets.clear()
        annotations = self._load_annotations()

        with dpg.window(
            label="Visual Configuration Editor",
            tag=self.winID,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
            no_resize=False,
            no_move=False,
        ):
            dpg.add_text("Modify application settings. Hover over labels for details.", color=(180, 180, 200, 255))
            dpg.add_separator()
            dpg.add_spacer(height=s(6))

            # Scrollable child container for settings
            with dpg.child_window(width=-1, height=-s(50), border=False):
                # Build UI recursively based on config.json structure
                for section_name, section_content in config.items():
                    if isinstance(section_content, dict):
                        with dpg.collapsing_header(label=section_name, default_open=True):
                            with dpg.group():
                                self._build_recursive_ui(
                                    section_content,
                                    annotations.get(section_name, {}),
                                    [section_name],
                                )
                    else:
                        logger.warning(f"Root-level config parameter ignored in UI build: {section_name}")

            dpg.add_separator()
            dpg.add_spacer(height=s(6))

            # Bottom action bar
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Save",
                    callback=self._on_save,
                    width=s(100),
                )
                dpg.add_button(
                    label="Cancel",
                    callback=lambda *a: dpg.delete_item(self.winID) if dpg.does_item_exist(self.winID) else None,
                    width=s(100),
                )

    def _load_annotations(self) -> dict[str, Any]:
        """Load the annotations mapping file."""
        anno_path = CONFIG_DIR / "config_anotation.json"
        if anno_path.exists():
            try:
                with open(anno_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load config annotations: {e}")
        return {}

    def _build_recursive_ui(
        self,
        config_data: dict[str, Any],
        annotation_data: dict[str, Any],
        current_path: list[str],
    ) -> None:
        """Recursively build UI elements for dictionary nodes, or individual widgets for leaves."""
        s = display_scaling.scale
        for key, value in config_data.items():
            new_path = current_path + [key]
            sub_anno = annotation_data.get(key, {}) if isinstance(annotation_data, dict) else {}

            if isinstance(value, dict):
                with dpg.tree_node(label=key, default_open=True):
                    self._build_recursive_ui(value, sub_anno, new_path)
            else:
                with dpg.group(horizontal=True):
                    label_tag = dpg.add_text(key.replace("_", " ").title() + ":")

                    description = sub_anno.get("description", "No description available.")
                    with dpg.tooltip(parent=label_tag):
                        dpg.add_text(description, wrap=s(300))

                    self._build_widget(key, value, sub_anno, new_path)

    def _build_widget(self, name: str, value: Any, annotation: dict[str, Any], path: list[str]) -> None:
        """Build the appropriate control widget based on annotations/type constraints."""
        s = display_scaling.scale
        values = annotation.get("values", None)

        is_bool_list = False
        if isinstance(values, list) and len(values) == 2:
            items_lower = [str(x).lower() for x in values]
            if "true" in items_lower and "false" in items_lower:
                is_bool_list = True

        if is_bool_list or (values is None and isinstance(value, bool)):
            tag = dpg.add_checkbox(default_value=value)
            self._widgets[tag] = (path, bool)

        # Folder Browser
        elif values == "folder_browser":
            with dpg.group(horizontal=True):
                input_tag = dpg.generate_uuid()

                def _on_browse_folder(
                    sender: Any = None,
                    app_data: Any = None,
                    user_data: Any = None,
                    *args: Any,
                    **kwargs: Any,
                ) -> None:
                    curr = dpg.get_value(user_data)
                    if curr:
                        p = Path(curr)
                        if not p.is_absolute():
                            p = (PROJECT_ROOT / p).resolve()
                        curr_abs = str(p)
                    else:
                        curr_abs = str(PROJECT_ROOT)

                    selected = file_explorer.select_folder(default_path=curr_abs)
                    if selected:
                        try:
                            rel = Path(selected).relative_to(PROJECT_ROOT)
                            selected = str(rel).replace("\\", "/")
                        except ValueError:
                            pass
                        dpg.set_value(user_data, selected)

                dpg.add_button(label="Browse...", callback=_on_browse_folder, user_data=input_tag)
                dpg.add_input_text(tag=input_tag, default_value=str(value) if value is not None else "", width=s(280))
                self._widgets[input_tag] = (path, type(value))

        # File Browser
        elif values == "file_browser":
            with dpg.group(horizontal=True):
                input_tag = dpg.generate_uuid()

                def _on_browse_file(
                    sender: Any = None,
                    app_data: Any = None,
                    user_data: Any = None,
                    *args: Any,
                    **kwargs: Any,
                ) -> None:
                    curr = dpg.get_value(user_data)
                    if curr:
                        p = Path(curr)
                        if not p.is_absolute():
                            p = (PROJECT_ROOT / p).resolve()
                        curr_abs = str(p)
                    else:
                        curr_abs = str(PROJECT_ROOT)

                    selected = file_explorer.select_file(default_path=curr_abs)
                    if selected:
                        try:
                            rel = Path(selected).relative_to(PROJECT_ROOT)
                            selected = str(rel).replace("\\", "/")
                        except ValueError:
                            pass
                        dpg.set_value(user_data, selected)

                dpg.add_button(label="Browse...", callback=_on_browse_file, user_data=input_tag)
                dpg.add_input_text(tag=input_tag, default_value=str(value) if value is not None else "", width=s(280))
                self._widgets[input_tag] = (path, type(value))

        # Dropdown (Multiple Choice)
        elif isinstance(values, list):
            items = [str(item) for item in values]
            tag = dpg.add_combo(items=items, default_value=str(value) if value is not None else "", width=s(280))
            self._widgets[tag] = (path, type(value))

        # Arbitrary Text Input (Default fallback)
        else:
            tag = dpg.add_input_text(default_value=str(value) if value is not None else "", width=s(280))
            self._widgets[tag] = (path, type(value))

    def _on_save(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Collect form values, validate, cast types, update in-memory dict, and write to file."""
        new_config = copy.deepcopy(config)
        errors = []

        for tag, (path, orig_type) in self._widgets.items():
            if not dpg.does_item_exist(tag):
                continue

            raw_val = dpg.get_value(tag)
            try:
                casted_val = self._cast_value(raw_val, orig_type)
                self._set_nested_val(new_config, path, casted_val)
            except Exception as e:
                path_str = " -> ".join(path)
                errors.append(f"Field '{path_str}': Failed to convert '{raw_val}' ({e})")

        if errors:
            self._show_popup("Validation Error", "\n".join(errors), error=True)
            return

        # Update in-memory config
        config.clear()
        config.update(new_config)

        # Persist to disk
        if config.save():
            logger.success("Visual Config Editor successfully updated config.json")
            self._show_popup(
                "Success",
                "Configuration saved successfully!\nSome settings may require a restart to take effect.",
                error=False,
                on_close=self._close_editor,
            )
        else:
            self._show_popup("File Error", "Failed to save to config.json", error=True)

    def _cast_value(self, val: Any, target_type: type) -> Any:
        """Safely cast string outputs from DPG widgets back to their correct types."""
        val_str = str(val).strip()

        if target_type == bool:
            if isinstance(val, bool):
                return val
            return val_str.lower() in ("true", "1", "yes", "on")

        if target_type == int:
            return int(val_str)

        if target_type == float:
            return float(val_str)

        if target_type == type(None):
            if val_str == "" or val_str.lower() in ("none", "null"):
                return None
            return val_str

        return val_str

    def _set_nested_val(self, d: dict, path: list[str], val: Any) -> None:
        """Helper to write to nested keys in dictionary."""
        for key in path[:-1]:
            d = d.setdefault(key, {})
        d[path[-1]] = val

    def _close_editor(self) -> None:
        """Close the configuration editor window."""
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

    def _show_popup(self, title: str, message: str, error: bool = False, on_close: Any = None) -> None:
        """Show a styled modal alert popup."""
        popup_id = "config_editor_popup"
        if dpg.does_item_exist(popup_id):
            dpg.delete_item(popup_id)

        s = display_scaling.scale
        win_w = s(400)
        win_h = s(180)

        # Center popup on screen
        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        title_color = (255, 100, 100, 255) if error else (100, 255, 100, 255)

        with dpg.window(
            label=title,
            modal=True,
            tag=popup_id,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
            no_resize=True,
        ):
            dpg.add_text(title.upper(), color=title_color)
            dpg.add_separator()
            dpg.add_spacer(height=s(4))
            dpg.add_text(message, wrap=s(380))
            dpg.add_spacer(height=s(10))

            def _callback(*args: Any, **kwargs: Any) -> None:
                dpg.delete_item(popup_id)
                if on_close:
                    on_close()

            dpg.add_button(label="OK", callback=_callback, width=-1, height=s(28))


# Global singleton instance
config_editor: ConfigEditor = ConfigEditor()

