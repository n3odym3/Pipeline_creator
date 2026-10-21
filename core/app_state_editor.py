"""
App State Inspector & Editor for Pipeline Creator.

Dynamic DearPyGui window that inspects and allows editing of the global
session state in `core/app_state.py`. Automatically detects data types
(bool, int, float, str, dict, list, None) and renders intuitive,
specialized controls for each attribute.
"""

from __future__ import annotations

import datetime
from dataclasses import fields, is_dataclass
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.app_state import app_state


class AppStateEditor:
    """
    Dynamic graphical inspector and editor for the global AppState singleton.
    """

    CORE_FIELDS = {"username", "mode", "login_done", "close_requested"}

    def __init__(self) -> None:
        self.winID: str = "app_state_editor_win"
        self.content_container_tag: str = "app_state_editor_content"
        self.status_tag: str = "app_state_status_text"
        self.filter_tag: str = "app_state_filter_input"
        self.add_section_tag: str = "app_state_add_section"

        # UI state
        self._filter_query: str = ""
        self._show_add_section: bool = False

    def show(self) -> None:
        """Create or bring to front the App State window."""
        if dpg.does_item_exist(self.winID):
            dpg.focus_item(self.winID)
            self._rebuild_content()
            return

        s = display_scaling.scale
        win_w = s(620)
        win_h = s(580)

        # Center in viewport
        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        with dpg.window(
            label="App State Inspector & Editor",
            tag=self.winID,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
            no_collapse=False,
            on_close=self._on_window_close,
        ):
            # Header
            with dpg.group(horizontal=True):
                dpg.add_text("App State", color=(100, 200, 255))
                dpg.add_text("— Global Session State Inspector & Editor", color=(180, 180, 180))

            dpg.add_separator()
            dpg.add_spacer(height=s(4))

            # Toolbar: Filter, Refresh, Toggle Add Attribute
            with dpg.group(horizontal=True):
                dpg.add_text("Filter:")
                dpg.add_input_text(
                    tag=self.filter_tag,
                    hint="Search attribute...",
                    width=s(180),
                    callback=self._on_filter_changed,
                )
                dpg.add_button(
                    label="Refresh",
                    callback=lambda *a: self._rebuild_content(),
                    width=s(80),
                )
                dpg.add_button(
                    label="+ Add Attribute",
                    callback=self._toggle_add_section,
                    width=s(120),
                )

            # Collapsible Add Attribute Panel
            with dpg.group(tag=self.add_section_tag, show=self._show_add_section):
                dpg.add_spacer(height=s(4))
                with dpg.child_window(height=s(110), border=True):
                    dpg.add_text("Add New Session Attribute", color=(120, 220, 140))
                    with dpg.group(horizontal=True):
                        dpg.add_input_text(
                            tag="new_attr_name",
                            hint="Attribute name (e.g. detector)",
                            width=s(160),
                        )
                        dpg.add_combo(
                            tag="new_attr_type",
                            items=["str", "int", "float", "bool", "dict", "list"],
                            default_value="str",
                            width=s(90),
                            callback=self._on_new_attr_type_changed,
                        )
                        dpg.add_input_text(
                            tag="new_attr_val",
                            hint="Initial value",
                            width=s(160),
                        )
                        dpg.add_button(
                            label="Add",
                            callback=self._on_add_attribute_submit,
                            width=s(60),
                        )

            dpg.add_spacer(height=s(4))
            dpg.add_separator()
            dpg.add_spacer(height=s(4))

            # Scrollable container for dynamic attribute rows
            with dpg.child_window(
                tag=self.content_container_tag,
                width=-1,
                height=-s(55),
                border=False,
            ):
                pass  # Populated by _rebuild_content()

            dpg.add_separator()
            dpg.add_spacer(height=s(4))

            # Bottom bar: Status and Close
            with dpg.group(horizontal=True):
                dpg.add_text("Ready", tag=self.status_tag, color=(160, 200, 160))
                dpg.add_spacer(width=s(10))
                dpg.add_button(
                    label="Close",
                    callback=lambda *a: dpg.delete_item(self.winID) if dpg.does_item_exist(self.winID) else None,
                    width=s(90),
                )

        # Build initial content
        self._rebuild_content()

    def _on_window_close(self) -> None:
        """Handle window close."""
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

    def _set_status(self, message: str, is_error: bool = False) -> None:
        """Update the status text with timestamp."""
        if not dpg.does_item_exist(self.status_tag):
            return
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        color = (255, 120, 120) if is_error else (140, 220, 160)
        dpg.set_value(self.status_tag, f"[{now_str}] {message}")
        dpg.configure_item(self.status_tag, color=color)

    def _toggle_add_section(self) -> None:
        """Toggle the visibility of the Add Attribute panel."""
        self._show_add_section = not self._show_add_section
        if dpg.does_item_exist(self.add_section_tag):
            dpg.configure_item(self.add_section_tag, show=self._show_add_section)

    def _on_new_attr_type_changed(self, sender: Any, app_data: str) -> None:
        """Update default placeholder when attribute type changes."""
        if not dpg.does_item_exist("new_attr_val"):
            return
        defaults = {
            "str": "",
            "int": "0",
            "float": "0.0",
            "bool": "False",
            "dict": "{}",
            "list": "[]",
        }
        dpg.set_value("new_attr_val", defaults.get(app_data, ""))

    def _on_add_attribute_submit(self) -> None:
        """Validate and add a new dynamic attribute to app_state."""
        name = dpg.get_value("new_attr_name").strip()
        val_type = dpg.get_value("new_attr_type")
        raw_val = dpg.get_value("new_attr_val").strip()

        if not name:
            self._set_status("Error: attribute name cannot be empty", is_error=True)
            return
        if not name.isidentifier():
            self._set_status(f"Error: '{name}' is not a valid Python identifier", is_error=True)
            return

        try:
            if val_type == "bool":
                val = raw_val.lower() in ("true", "1", "yes", "t")
            elif val_type == "int":
                val = int(raw_val) if raw_val else 0
            elif val_type == "float":
                val = float(raw_val) if raw_val else 0.0
            elif val_type == "dict":
                val = json.loads(raw_val) if raw_val else {}
                if not isinstance(val, dict):
                    raise ValueError("Must be a JSON object (dict)")
            elif val_type == "list":
                val = json.loads(raw_val) if raw_val else []
                if not isinstance(val, list):
                    raise ValueError("Must be a JSON array (list)")
            else:  # str
                val = raw_val
        except Exception as e:
            self._set_status(f"Error parsing value: {e}", is_error=True)
            return

        setattr(app_state, name, val)
        logger.info(f"AppState: added dynamic attribute '{name}' = {val!r}")
        self._set_status(f"Added attribute '{name}'")
        dpg.set_value("new_attr_name", "")
        dpg.set_value("new_attr_val", "")
        self._rebuild_content()

    def _on_filter_changed(self, sender: Any, app_data: str) -> None:
        """Trigger filter update."""
        self._filter_query = (app_data or "").strip().lower()
        self._rebuild_content()

    def _get_all_attributes(self) -> Dict[str, Any]:
        """Collect all attributes currently present on app_state."""
        attrs: Dict[str, Any] = {}

        # 1. Dataclass fields
        if is_dataclass(app_state):
            for f in fields(app_state):
                attrs[f.name] = getattr(app_state, f.name, None)

        # 2. Dynamic instance attributes
        if hasattr(app_state, "__dict__"):
            for k, v in app_state.__dict__.items():
                if not k.startswith("_") and not callable(v):
                    attrs[k] = v

        return attrs

    def _rebuild_content(self) -> None:
        """Rebuild the UI rows inside content_container_tag."""
        if not dpg.does_item_exist(self.content_container_tag):
            return

        dpg.delete_item(self.content_container_tag, children_only=True)

        all_attrs = self._get_all_attributes()

        # Split into core vs dynamic
        core_attrs = {k: v for k, v in all_attrs.items() if k in self.CORE_FIELDS}
        dynamic_attrs = {k: v for k, v in all_attrs.items() if k not in self.CORE_FIELDS}

        # Filter if search query active
        if self._filter_query:
            core_attrs = {k: v for k, v in core_attrs.items() if self._filter_query in k.lower()}
            dynamic_attrs = {k: v for k, v in dynamic_attrs.items() if self._filter_query in k.lower()}

        s = display_scaling.scale

        with dpg.group(parent=self.content_container_tag):
            # Section 1: Core Dataclass State
            with dpg.collapsing_header(
                label=f"Core Session State ({len(core_attrs)})",
                default_open=True,
            ):
                if core_attrs:
                    for key, val in core_attrs.items():
                        self._render_attribute_row(key, val, is_core=True)
                else:
                    dpg.add_text("No matching core attributes", color=(150, 150, 150))

            dpg.add_spacer(height=s(6))

            # Section 2: Dynamic Attributes
            with dpg.collapsing_header(
                label=f"Dynamic Attributes ({len(dynamic_attrs)})",
                default_open=True,
            ):
                if dynamic_attrs:
                    for key, val in dynamic_attrs.items():
                        self._render_attribute_row(key, val, is_core=False)
                else:
                    dpg.add_text(
                        "No dynamic attributes currently set. Use '+ Add Attribute' to define new ones.",
                        color=(150, 150, 150),
                    )

    def _render_attribute_row(self, key: str, val: Any, is_core: bool) -> None:
        """
        Dynamically render an interactive row tailored to the value's type.
        """
        s = display_scaling.scale
        val_type = type(val).__name__

        type_color = {
            "bool": (130, 200, 255),
            "int": (255, 180, 100),
            "float": (255, 210, 120),
            "str": (160, 230, 160),
            "dict": (220, 150, 230),
            "list": (240, 180, 220),
            "tuple": (240, 180, 220),
            "NoneType": (180, 180, 180),
        }.get(val_type, (200, 200, 200))

        # Distinct styling container for row
        with dpg.group():
            with dpg.group(horizontal=True):
                # Badge: type
                dpg.add_text(f"[{val_type}]", color=type_color)

                # Attribute Name
                dpg.add_text(key, color=(240, 240, 240))

                # Delete button for dynamic attributes
                if not is_core:
                    dpg.add_button(
                        label="x",
                        user_data=key,
                        callback=self._on_delete_dynamic_attribute,
                        width=s(20),
                    )

            # Interactive Editor based on type
            self._render_type_widget(key, val)

            dpg.add_spacer(height=s(3))
            dpg.add_separator()
            dpg.add_spacer(height=s(3))

    def _render_type_widget(self, key: str, val: Any) -> None:
        """Render the specialized input widget based on data type."""
        s = display_scaling.scale

        # 1. Boolean
        if isinstance(val, bool):
            dpg.add_checkbox(
                label=f"Enable {key}",
                default_value=val,
                user_data=key,
                callback=self._on_bool_changed,
            )

        # 2. Integer
        elif isinstance(val, int) and not isinstance(val, bool):
            dpg.add_input_int(
                default_value=val,
                step=1,
                width=s(220),
                user_data=key,
                callback=self._on_int_changed,
                on_enter=False,
            )

        # 3. Float
        elif isinstance(val, float):
            dpg.add_input_float(
                default_value=val,
                format="%.4f",
                step=0.1,
                width=s(220),
                user_data=key,
                callback=self._on_float_changed,
                on_enter=False,
            )

        # 4. String
        elif isinstance(val, str):
            # Special case: mode dropdown
            if key == "mode":
                dpg.add_combo(
                    items=["user", "advanced", "dev"],
                    default_value=val if val in ["user", "advanced", "dev"] else "dev",
                    width=s(220),
                    user_data=key,
                    callback=self._on_mode_changed,
                )
            # Check if path-like
            elif "/" in val or "\\" in val or (val and Path(val).exists()):
                with dpg.group(horizontal=True):
                    tag_id = dpg.generate_uuid()
                    dpg.add_input_text(
                        tag=tag_id,
                        default_value=val,
                        width=s(320),
                        user_data=(key, tag_id),
                        callback=self._on_str_changed,
                    )
                    dpg.add_button(
                        label="Browse...",
                        user_data=(key, tag_id),
                        callback=self._on_browse_path,
                        width=s(80),
                    )
            # Multiline if long or contains newlines
            elif "\n" in val or len(val) > 70:
                dpg.add_input_text(
                    default_value=val,
                    multiline=True,
                    height=s(60),
                    width=s(420),
                    user_data=key,
                    callback=self._on_str_changed,
                )
            else:
                dpg.add_input_text(
                    default_value=val,
                    width=s(320),
                    user_data=key,
                    callback=self._on_str_changed,
                )

        # 5. Dict (JSON Editor)
        elif isinstance(val, dict):
            with dpg.collapsing_header(label=f"JSON Editor ({len(val)} keys)", default_open=False):
                try:
                    pretty_json = json.dumps(val, indent=2, ensure_ascii=False)
                except Exception:
                    pretty_json = str(val)

                json_input_tag = dpg.generate_uuid()
                dpg.add_input_text(
                    tag=json_input_tag,
                    default_value=pretty_json,
                    multiline=True,
                    height=s(120),
                    width=-1,
                )
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="Apply JSON",
                        user_data=(key, json_input_tag),
                        callback=self._on_apply_json_dict,
                        width=s(100),
                    )
                    dpg.add_button(
                        label="Format",
                        user_data=json_input_tag,
                        callback=self._on_format_json,
                        width=s(80),
                    )

        # 6. List / Tuple
        elif isinstance(val, (list, tuple)):
            with dpg.collapsing_header(label=f"Array ({len(val)} items)", default_open=False):
                try:
                    pretty_json = json.dumps(list(val), indent=2, ensure_ascii=False)
                except Exception:
                    pretty_json = str(val)

                arr_input_tag = dpg.generate_uuid()
                dpg.add_input_text(
                    tag=arr_input_tag,
                    default_value=pretty_json,
                    multiline=True,
                    height=s(100),
                    width=-1,
                )
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="Apply Array",
                        user_data=(key, arr_input_tag, isinstance(val, tuple)),
                        callback=self._on_apply_json_list,
                        width=s(100),
                    )
                    dpg.add_button(
                        label="Format",
                        user_data=arr_input_tag,
                        callback=self._on_format_json,
                        width=s(80),
                    )

        # 7. NoneType
        elif val is None:
            with dpg.group(horizontal=True):
                dpg.add_text("<None>", color=(160, 160, 160))
                dpg.add_combo(
                    items=["(Keep None)", "Set as str", "Set as int", "Set as float", "Set as bool", "Set as dict", "Set as list"],
                    default_value="(Keep None)",
                    width=s(130),
                    user_data=key,
                    callback=self._on_convert_none_type,
                )

        # 8. Fallback / Object
        else:
            dpg.add_text(f"Representation: {val!r}", color=(180, 180, 180))

    # ------------------------------------------------------------------
    # Callbacks for interactive updates
    # ------------------------------------------------------------------

    def _on_bool_changed(self, sender: Any, app_data: bool, user_data: str) -> None:
        """Handle boolean toggle."""
        key = user_data
        setattr(app_state, key, bool(app_data))
        logger.info(f"AppState: updated '{key}' -> {app_data}")
        self._set_status(f"Updated '{key}' -> {app_data}")

    def _on_int_changed(self, sender: Any, app_data: int, user_data: str) -> None:
        """Handle integer value update."""
        key = user_data
        try:
            val = int(app_data)
            setattr(app_state, key, val)
            logger.info(f"AppState: updated '{key}' -> {val}")
            self._set_status(f"Updated '{key}' -> {val}")
        except Exception as e:
            self._set_status(f"Invalid integer: {e}", is_error=True)

    def _on_float_changed(self, sender: Any, app_data: float, user_data: str) -> None:
        """Handle float value update."""
        key = user_data
        try:
            val = float(app_data)
            setattr(app_state, key, val)
            logger.info(f"AppState: updated '{key}' -> {val}")
            self._set_status(f"Updated '{key}' -> {val}")
        except Exception as e:
            self._set_status(f"Invalid float: {e}", is_error=True)

    def _on_str_changed(self, sender: Any, app_data: str, user_data: Any) -> None:
        """Handle string value update."""
        key = user_data[0] if isinstance(user_data, (tuple, list)) else user_data
        val = str(app_data)
        setattr(app_state, key, val)
        logger.info(f"AppState: updated '{key}' -> '{val}'")
        self._set_status(f"Updated '{key}'")

    def _on_mode_changed(self, sender: Any, app_data: str, user_data: str) -> None:
        """Handle mode combo change and synchronize main menu visibility."""
        new_mode = str(app_data)
        app_state.mode = new_mode
        logger.info(f"AppState: mode changed to '{new_mode}'")
        self._set_status(f"Mode set to '{new_mode}' (applying UI permissions)")

        # Synchronize main window menu bar and active module permissions
        try:
            from core.main_win import main_win
            main_win.apply_mode_visibility()
        except Exception as e:
            logger.warning(f"Could not automatically sync main_win visibility: {e}")

    def _on_browse_path(self, sender: Any, app_data: Any, user_data: Tuple[str, Any]) -> None:
        """Open system file explorer to choose a file or folder."""
        key, tag_id = user_data
        try:
            from core.file_explorer import file_explorer
            current_val = getattr(app_state, key, "")
            chosen = file_explorer.select_file(default_path=str(current_val))
            if chosen:
                dpg.set_value(tag_id, chosen)
                setattr(app_state, key, chosen)
                logger.info(f"AppState: updated '{key}' path -> '{chosen}'")
                self._set_status(f"Updated path '{key}'")
        except Exception as e:
            self._set_status(f"Browse error: {e}", is_error=True)

    def _on_apply_json_dict(self, sender: Any, app_data: Any, user_data: Tuple[str, Any]) -> None:
        """Parse and commit JSON dict to app_state."""
        key, input_tag = user_data
        raw_text = dpg.get_value(input_tag)
        try:
            parsed = json.loads(raw_text)
            if not isinstance(parsed, dict):
                raise ValueError("Must be a JSON object ({})")
            setattr(app_state, key, parsed)
            logger.info(f"AppState: updated dict '{key}' ({len(parsed)} keys)")
            self._set_status(f"Successfully saved dict '{key}'")
        except Exception as e:
            self._set_status(f"JSON Error in '{key}': {e}", is_error=True)

    def _on_apply_json_list(self, sender: Any, app_data: Any, user_data: Tuple[str, Any, bool]) -> None:
        """Parse and commit JSON list/tuple to app_state."""
        key, input_tag, is_tuple = user_data
        raw_text = dpg.get_value(input_tag)
        try:
            parsed = json.loads(raw_text)
            if not isinstance(parsed, list):
                raise ValueError("Must be a JSON array ([])")
            val = tuple(parsed) if is_tuple else parsed
            setattr(app_state, key, val)
            logger.info(f"AppState: updated array '{key}' ({len(val)} items)")
            self._set_status(f"Successfully saved array '{key}'")
        except Exception as e:
            self._set_status(f"JSON Error in '{key}': {e}", is_error=True)

    def _on_format_json(self, sender: Any, app_data: Any, user_data: Any) -> None:
        """Beautify the JSON in an input widget."""
        input_tag = user_data
        try:
            raw = dpg.get_value(input_tag)
            parsed = json.loads(raw)
            formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
            dpg.set_value(input_tag, formatted)
            self._set_status("JSON formatted successfully")
        except Exception as e:
            self._set_status(f"Formatting failed: {e}", is_error=True)

    def _on_convert_none_type(self, sender: Any, app_data: str, user_data: str) -> None:
        """Convert a NoneType attribute to a concrete default value."""
        key = user_data
        choice = app_data
        defaults = {
            "Set as str": "",
            "Set as int": 0,
            "Set as float": 0.0,
            "Set as bool": False,
            "Set as dict": {},
            "Set as list": [],
        }
        if choice in defaults:
            new_val = defaults[choice]
            setattr(app_state, key, new_val)
            logger.info(f"AppState: converted '{key}' from None to {type(new_val).__name__}")
            self._set_status(f"Converted '{key}' to {type(new_val).__name__}")
            self._rebuild_content()

    def _on_delete_dynamic_attribute(self, sender: Any, app_data: Any, user_data: str) -> None:
        """Delete a dynamic attribute from app_state."""
        key = user_data
        if hasattr(app_state, key):
            try:
                delattr(app_state, key)
                logger.info(f"AppState: removed dynamic attribute '{key}'")
                self._set_status(f"Deleted dynamic attribute '{key}'")
                self._rebuild_content()
            except Exception as e:
                self._set_status(f"Failed to delete '{key}': {e}", is_error=True)


# Singleton instance and helper function
app_state_editor: AppStateEditor = AppStateEditor()


def show_app_state_editor() -> None:
    """Convenience function to open the App State Inspector & Editor."""
    app_state_editor.show()
