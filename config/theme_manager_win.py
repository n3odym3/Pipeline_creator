"""
Theme Manager GUI window for Pipeline Creator.

Allows visual inspection, live/real-time editing, creation, and persistence
of UI palettes and plot styling parameters (line weight, marker size, colors, etc.).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from config.theme_manager import PALETTES, theme_manager
from core.paths import PROJECT_ROOT

# Catalogue of all known properties supported by theme_factory
ALL_FACTORY_PROPERTIES: Dict[str, Dict[str, Any]] = {
    # Plots
    "plot_line_weight": {"type": "float", "default": 4.0, "min": 0.5, "max": 20.0, "step": 0.5, "cat": "Plots"},
    "plot_marker_size": {"type": "float", "default": 4.0, "min": 1.0, "max": 30.0, "step": 0.5, "cat": "Plots"},
    "plot_marker_weight": {"type": "float", "default": 1.0, "min": 0.5, "max": 10.0, "step": 0.2, "cat": "Plots"},
    "plot_fill_alpha": {"type": "float", "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05, "cat": "Plots"},
    "plot_crosshairs": {"type": "color", "default": (255, 0, 0, 255), "cat": "Plots"},
    "plot_fill": {"type": "color", "default": (100, 100, 100, 50), "cat": "Plots"},
    # General & Windows
    "is_light": {"type": "bool", "default": False, "cat": "General"},
    "is_colorblind": {"type": "bool", "default": False, "cat": "General"},
    "colorblind_type": {
        "type": "choice",
        "choices": ["universal", "protanopia", "deuteranopia", "tritanopia"],
        "default": "universal",
        "cat": "General",
    },
    "window_bg": {"type": "color", "default": (40, 40, 50, 255), "cat": "General"},
    "main_win_bg": {"type": "color", "default": (20, 20, 25, 255), "cat": "General"},
    "menubar_bg": {"type": "color", "default": (51, 51, 55, 255), "cat": "General"},
    "main_menubar_bg": {"type": "color", "default": (51, 51, 55, 255), "cat": "General"},
    "popup_bg": {"type": "color", "default": (37, 37, 38, 255), "cat": "General"},
    "child_bg": {"type": "color", "default": (0, 0, 0, 0), "cat": "General"},
    "title_bg": {"type": "color", "default": (85, 85, 85, 255), "cat": "General"},
    "title_bg_active": {"type": "color", "default": (15, 86, 135, 255), "cat": "General"},
    "title_bg_collapsed": {"type": "color", "default": (85, 85, 85, 180), "cat": "General"},
    "border": {"type": "color", "default": (110, 110, 128, 128), "cat": "General"},
    "text": {"type": "color", "default": (255, 255, 255, 255), "cat": "General"},
    "text_disabled": {"type": "color", "default": (120, 122, 130, 255), "cat": "General"},
    # Widgets & Controls
    "frame_bg": {"type": "color", "default": (30, 30, 40, 255), "cat": "Widgets"},
    "frame_bg_hovered": {"type": "color", "default": (35, 50, 80, 255), "cat": "Widgets"},
    "frame_bg_active": {"type": "color", "default": (20, 110, 170, 255), "cat": "Widgets"},
    "button": {"type": "color", "default": (51, 51, 55, 255), "cat": "Widgets"},
    "button_hovered": {"type": "color", "default": (30, 60, 80, 255), "cat": "Widgets"},
    "button_active": {"type": "color", "default": (56, 56, 58, 255), "cat": "Widgets"},
    "header": {"type": "color", "default": (50, 50, 50, 255), "cat": "Widgets"},
    "header_hovered": {"type": "color", "default": (30, 60, 80, 255), "cat": "Widgets"},
    "header_active": {"type": "color", "default": (40, 40, 40, 255), "cat": "Widgets"},
    "checkmark": {"type": "color", "default": (20, 110, 170, 255), "cat": "Widgets"},
    "slider_grab": {"type": "color", "default": (100, 100, 100, 255), "cat": "Widgets"},
    "slider_grab_active": {"type": "color", "default": (150, 150, 150, 255), "cat": "Widgets"},
    "separator": {"type": "color", "default": (70, 70, 80, 210), "cat": "Widgets"},
    # Scrollbars & Tables
    "scrollbar_bg": {"type": "color", "default": (45, 45, 55, 255), "cat": "Scrollbars & Tables"},
    "scrollbar_grab": {"type": "color", "default": (80, 80, 80, 255), "cat": "Scrollbars & Tables"},
    "scrollbar_grab_hovered": {"type": "color", "default": (100, 100, 100, 255), "cat": "Scrollbars & Tables"},
    "scrollbar_grab_active": {"type": "color", "default": (120, 120, 120, 255), "cat": "Scrollbars & Tables"},
    "table_header_bg": {"type": "color", "default": (50, 50, 60, 255), "cat": "Scrollbars & Tables"},
    "table_border_light": {"type": "color", "default": (70, 70, 80, 100), "cat": "Scrollbars & Tables"},
    "table_border_strong": {"type": "color", "default": (90, 90, 100, 200), "cat": "Scrollbars & Tables"},
    "resize_grip": {"type": "color", "default": (45, 45, 55, 50), "cat": "Scrollbars & Tables"},
    "resize_grip_hovered": {"type": "color", "default": (80, 80, 85, 170), "cat": "Scrollbars & Tables"},
    "resize_grip_active": {"type": "color", "default": (20, 110, 170, 230), "cat": "Scrollbars & Tables"},
    # Nodes & Links
    "node_bg": {"type": "color", "default": (45, 45, 50, 255), "cat": "Nodes & Canvas"},
    "node_title_bg": {"type": "color", "default": (65, 65, 75, 255), "cat": "Nodes & Canvas"},
    "node_grid_bg": {"type": "color", "default": (37, 37, 38, 255), "cat": "Nodes & Canvas"},
    "node_grid_line": {"type": "color", "default": (50, 50, 50, 255), "cat": "Nodes & Canvas"},
    "link": {"type": "color", "default": (140, 140, 140, 160), "cat": "Nodes & Canvas"},
    "link_hovered": {"type": "color", "default": (180, 180, 180, 255), "cat": "Nodes & Canvas"},
    "link_selected": {"type": "color", "default": (220, 220, 220, 255), "cat": "Nodes & Canvas"},
    "base_hue": {"type": "float", "default": 130.0, "min": 0.0, "max": 360.0, "step": 1.0, "cat": "Nodes & Canvas"},
    "sat": {"type": "float", "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05, "cat": "Nodes & Canvas"},
}


class ThemeManagerWin:
    """
    Visual Theme Manager & Editor window.
    """

    def __init__(self) -> None:
        self.win_id = "theme_manager_win"
        self.selected_theme: str = theme_manager.active_palette_name
        self.real_time: bool = True
        self.search_filter: str = ""
        self._content_container: str = "theme_mgr_content_container"
        self._theme_combo_tag: str = "theme_mgr_theme_combo"
        self._status_text_tag: str = "theme_mgr_status_text"
        self._add_prop_combo_tag: str = "theme_mgr_add_prop_combo"

    def show(self) -> None:
        """Create or bring to front the Theme Manager window."""
        if dpg.does_item_exist(self.win_id):
            dpg.configure_item(self.win_id, show=True)
            dpg.focus_item(self.win_id)
            self._refresh_theme_list()
            self._render_options()
            return

        s = display_scaling.scale
        with dpg.window(
            tag=self.win_id,
            label="Theme Manager",
            width=s(820),
            height=s(680),
            pos=(s(120), s(80)),
            on_close=lambda: dpg.configure_item(self.win_id, show=False),
        ):
            self._build_ui()

    def _build_ui(self) -> None:
        """Build the Theme Manager interface."""
        s = display_scaling.scale

        # ── Top Control Bar ──────────────────────────────────────────
        with dpg.group(horizontal=True):
            dpg.add_text("\uf1fc  Theme Manager", color=(100, 200, 255, 255))
            dpg.add_spacer(width=s(10))
            dpg.add_checkbox(
                label="\uf0e7  Real time update",
                default_value=self.real_time,
                callback=self._on_toggle_real_time,
            )
            dpg.add_spacer(width=s(10))
            dpg.add_checkbox(
                label="\uf0eb  Enable Highlight",
                default_value=theme_manager.highlight_enabled,
                callback=self._on_toggle_highlight_enabled,
            )
            dpg.add_spacer(width=s(15))
            dpg.add_text("", tag=self._status_text_tag, color=(120, 255, 120, 255))

        dpg.add_separator()

        with dpg.group(horizontal=True):
            dpg.add_text("Theme:")
            themes = list(PALETTES.keys())
            if self.selected_theme not in themes and themes:
                self.selected_theme = themes[0]

            dpg.add_combo(
                items=themes,
                default_value=self.selected_theme,
                tag=self._theme_combo_tag,
                width=s(200),
                callback=self._on_select_theme,
            )
            dpg.add_button(label="\uf00c  Apply to UI", callback=self._on_apply_active_theme)
            dpg.add_button(label="\uf067  New Theme...", callback=self._show_new_theme_modal)
            dpg.add_button(label="\uf0c7  Save to File", callback=self._on_save_to_file)

        # ── Filter / Search Bar ──────────────────────────────────────
        with dpg.group(horizontal=True):
            dpg.add_text("\uf002 Filter:")
            dpg.add_input_text(
                hint="plot, node, bg, button...",
                width=s(280),
                callback=self._on_search_change,
            )
            dpg.add_spacer(width=s(10))
            dpg.add_text("Add property:")
            dpg.add_combo(
                items=[],
                tag=self._add_prop_combo_tag,
                width=s(200),
            )
            dpg.add_button(label="\uf067  Add", callback=self._on_add_property)

        dpg.add_separator()

        # ── Scrollable Content Area ──────────────────────────────────
        with dpg.child_window(tag=self._content_container, autosize_x=True, autosize_y=True):
            self._render_options()

    def _on_toggle_real_time(self, sender: Any, app_data: bool, *args, **kwargs) -> None:
        """Toggle real-time updates."""
        self.real_time = bool(app_data)
        if self.real_time:
            self._trigger_live_update()

    def _on_toggle_highlight_enabled(self, sender: Any, app_data: bool, *args, **kwargs) -> None:
        """Toggle global highlight enablement."""
        theme_manager.highlight_enabled = bool(app_data)
        self._show_status(f"Highlight {'enabled' if app_data else 'disabled'}")

    def _on_select_theme(self, sender: Any, app_data: str, *args, **kwargs) -> None:
        """User selected a different theme in the combo."""
        self.selected_theme = app_data
        if self.real_time:
            self._on_apply_active_theme()
        self._render_options()

    def _on_search_change(self, sender: Any, app_data: str, *args, **kwargs) -> None:
        """Filter input changed."""
        self.search_filter = app_data.lower().strip()
        self._render_options()

    def _refresh_theme_list(self) -> None:
        """Update the theme combo dropdown items."""
        if dpg.does_item_exist(self._theme_combo_tag):
            themes = list(PALETTES.keys())
            dpg.configure_item(self._theme_combo_tag, items=themes, default_value=self.selected_theme)

    def _refresh_add_prop_combo(self, current_palette: Dict[str, Any]) -> None:
        """Update the available properties list for adding."""
        if dpg.does_item_exist(self._add_prop_combo_tag):
            available = [k for k in ALL_FACTORY_PROPERTIES.keys() if k not in current_palette]
            dpg.configure_item(self._add_prop_combo_tag, items=available)
            if available:
                dpg.set_value(self._add_prop_combo_tag, available[0])
            else:
                dpg.set_value(self._add_prop_combo_tag, "")

    def _on_add_property(self, *args, **kwargs) -> None:
        """Add a missing property from the catalogue to the current palette."""
        prop_name = dpg.get_value(self._add_prop_combo_tag)
        if not prop_name or prop_name not in ALL_FACTORY_PROPERTIES:
            return

        palette = PALETTES.get(self.selected_theme)
        if palette is None:
            return

        prop_info = ALL_FACTORY_PROPERTIES[prop_name]
        palette[prop_name] = prop_info["default"]

        self._show_status(f"Added '{prop_name}'")
        if self.real_time:
            self._trigger_live_update()
        self._render_options()

    def _on_apply_active_theme(self, *args, **kwargs) -> None:
        """Apply the selected theme to the entire application."""
        if self.selected_theme in PALETTES:
            theme_manager.load_theme(self.selected_theme, force=True)
            theme_manager.update_titlebar()

            if dpg.does_item_exist("main_win"):
                dpg.bind_item_theme("main_win", theme_manager.create_main_win_theme())

            self._show_status(f"Theme '{self.selected_theme}' applied!")

    def _trigger_live_update(self) -> None:
        """Perform a live reload of the theme if real-time is enabled."""
        if not self.real_time:
            return

        # If the currently edited theme is the active one, refresh it
        if theme_manager.active_palette_name == self.selected_theme:
            theme_manager.active_palette = PALETTES[self.selected_theme]
            theme_manager.refresh()
            if dpg.does_item_exist("main_win"):
                dpg.bind_item_theme("main_win", theme_manager.create_main_win_theme())
        else:
            # Switch to this theme
            self._on_apply_active_theme()

    def _show_status(self, msg: str) -> None:
        """Briefly display a status message."""
        if dpg.does_item_exist(self._status_text_tag):
            dpg.set_value(self._status_text_tag, msg)

    def _render_options(self) -> None:
        """Render all options and property editors for the current theme."""
        if not dpg.does_item_exist(self._content_container):
            return

        dpg.delete_item(self._content_container, children_only=True)
        palette = PALETTES.get(self.selected_theme)
        if palette is None:
            with dpg.group(parent=self._content_container):
                dpg.add_text(f"Theme '{self.selected_theme}' not found.", color=(255, 100, 100, 255))
            return

        self._refresh_add_prop_combo(palette)

        # Categorize properties
        categories: Dict[str, List[Tuple[str, Any]]] = {
            "Plots": [],
            "Highlight Style": [],
            "General": [],
            "Widgets": [],
            "Scrollbars & Tables": [],
            "Nodes & Canvas": [],
            "Custom / Other": [],
        }

        for key, val in palette.items():
            if key == "highlight" and isinstance(val, dict):
                for sub_k, sub_v in val.items():
                    if self.search_filter and self.search_filter not in f"highlight_{sub_k}".lower():
                        continue
                    categories["Highlight Style"].append((f"hl_{sub_k}", sub_v))
                continue

            if self.search_filter and self.search_filter not in key.lower():
                continue

            info = ALL_FACTORY_PROPERTIES.get(key)
            cat = info["cat"] if info else "Custom / Other"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append((key, val))

        s = display_scaling.scale

        with dpg.group(parent=self._content_container):
            for cat_name, items in categories.items():
                if not items:
                    continue

                icon = {
                    "Plots": "\uf080",
                    "Highlight Style": "\uf0eb",
                    "General": "\uf2d0",
                    "Widgets": "\uf085",
                    "Scrollbars & Tables": "\uf0ce",
                    "Nodes & Canvas": "\uf1e0",
                    "Custom / Other": "\uf013",
                }.get(cat_name, "\uf07b")

                with dpg.collapsing_header(label=f"{icon}  {cat_name} ({len(items)})", default_open=True):
                    with dpg.table(
                        header_row=True,
                        borders_innerH=True,
                        borders_outerH=True,
                        borders_innerV=False,
                        row_background=True,
                        policy=dpg.mvTable_SizingStretchProp,
                    ):
                        dpg.add_table_column(label="Property", width_fixed=True, init_width_or_weight=s(240))
                        dpg.add_table_column(label="Value / Editor", init_width_or_weight=s(460))

                        for key, val in items:
                            with dpg.table_row():
                                # Column 1: Label
                                dpg.add_text(key)

                                # Column 2: Editor
                                if key.startswith("hl_") and "highlight" in palette:
                                    actual_key = key[3:]
                                    self._build_editor(actual_key, val, palette["highlight"])
                                else:
                                    self._build_editor(key, val, palette)

    def _build_editor(self, key: str, val: Any, palette: Dict[str, Any]) -> None:
        """Create the appropriate DearPyGui control for editing a property."""
        s = display_scaling.scale
        item_tag = f"prop_{self.selected_theme}_{key}"

        def _make_color_cb(prop_key: str):
            def _cb(sender, app_data):
                if not app_data:
                    return
                raw = list(app_data)
                if all(isinstance(c, float) and 0.0 <= c <= 1.0 for c in raw) and any(c > 0 for c in raw):
                    r, g, b, a = [int(round(c * 255)) for c in raw[:4]]
                else:
                    r = max(0, min(255, int(round(raw[0]))))
                    g = max(0, min(255, int(round(raw[1]))))
                    b = max(0, min(255, int(round(raw[2]))))
                    a = max(0, min(255, int(round(raw[3])))) if len(raw) > 3 else 255
                palette[prop_key] = (r, g, b, a)
                self._trigger_live_update()
            return _cb

        def _make_bool_cb(prop_key: str):
            def _cb(sender, app_data):
                palette[prop_key] = bool(app_data)
                self._trigger_live_update()
            return _cb

        def _make_num_cb(prop_key: str, is_int: bool = False):
            def _cb(sender, app_data):
                palette[prop_key] = int(app_data) if is_int else float(app_data)
                self._trigger_live_update()
            return _cb

        def _make_choice_cb(prop_key: str):
            def _cb(sender, app_data):
                palette[prop_key] = str(app_data)
                self._trigger_live_update()
            return _cb

        # Color: tuple or list of 3/4 ints
        if isinstance(val, (tuple, list)) and len(val) in (3, 4) and all(isinstance(x, (int, float)) for x in val):
            # Normalize to float values for DPG
            rgba = [float(val[0]), float(val[1]), float(val[2]), float(val[3]) if len(val) > 3 else 255.0]
            dpg.add_color_edit(
                default_value=rgba,
                no_alpha=False,
                no_label=True,
                width=s(280),
                callback=_make_color_cb(key),
            )
        # Boolean
        elif isinstance(val, bool):
            dpg.add_checkbox(
                default_value=val,
                callback=_make_bool_cb(key),
            )
        # Specific choice
        elif key in ALL_FACTORY_PROPERTIES and ALL_FACTORY_PROPERTIES[key]["type"] == "choice":
            choices = ALL_FACTORY_PROPERTIES[key]["choices"]
            dpg.add_combo(
                items=choices,
                default_value=str(val),
                width=s(200),
                callback=_make_choice_cb(key),
            )
        # Number: float or int
        elif isinstance(val, (int, float)):
            prop_info = ALL_FACTORY_PROPERTIES.get(key, {})
            min_v = prop_info.get("min", 0.0)
            max_v = prop_info.get("max", 100.0)
            step = prop_info.get("step", 0.5)

            if isinstance(val, int) and prop_info.get("type") == "int":
                dpg.add_drag_int(
                    default_value=val,
                    min_value=int(min_v),
                    max_value=int(max_v),
                    width=s(200),
                    callback=_make_num_cb(key, is_int=True),
                )
            else:
                dpg.add_drag_float(
                    default_value=float(val),
                    min_value=min_v,
                    max_value=max_v,
                    speed=step,
                    width=s(200),
                    callback=_make_num_cb(key, is_int=False),
                )
        # String or fallback
        else:
            dpg.add_input_text(
                default_value=str(val),
                width=s(250),
                callback=_make_choice_cb(key),
            )

    # ── Modal for New Theme ──────────────────────────────────────────
    def _show_new_theme_modal(self, *args, **kwargs) -> None:
        """Open a modal popup to create a new theme clone."""
        modal_tag = "theme_mgr_new_modal"
        if dpg.does_item_exist(modal_tag):
            dpg.delete_item(modal_tag)

        s = display_scaling.scale
        with dpg.window(
            tag=modal_tag,
            label="\uf067  Create New Theme",
            modal=True,
            show=True,
            no_resize=True,
            width=s(400),
            height=s(240),
            pos=(s(250), s(200)),
        ):
            dpg.add_text("Theme Name (e.g. DARK_PURPLE):")
            name_input = dpg.add_input_text(hint="MY_CUSTOM_THEME", width=-1)

            dpg.add_spacer(height=s(8))
            dpg.add_text("Clone from existing theme:")
            base_combo = dpg.add_combo(items=list(PALETTES.keys()), default_value=self.selected_theme, width=-1)

            dpg.add_spacer(height=s(15))
            with dpg.group(horizontal=True):
                def _create_cb(*args, **kwargs):
                    raw_name = dpg.get_value(name_input).strip()
                    if not raw_name:
                        return
                    # Format as ALL_CAPS
                    clean_name = re.sub(r"[^A-Za-z0-9_]", "_", raw_name).upper()
                    base_name = dpg.get_value(base_combo)
                    base_palette = PALETTES.get(base_name, {})

                    # Duplicate palette
                    PALETTES[clean_name] = base_palette.copy()
                    self.selected_theme = clean_name
                    self._refresh_theme_list()
                    self._render_options()
                    self._show_status(f"Created new theme '{clean_name}'")
                    if self.real_time:
                        self._on_apply_active_theme()
                    dpg.delete_item(modal_tag)

                dpg.add_button(label="\uf00c  Create", width=s(100), callback=_create_cb)
                dpg.add_button(label="\uf00d  Cancel", width=s(100), callback=lambda: dpg.delete_item(modal_tag))

    # ── Save to File (theme_colors.py) ───────────────────────────────
    def _on_save_to_file(self, *args, **kwargs) -> None:
        """Serialize all palettes into config/theme_colors.py."""
        theme_colors_path = PROJECT_ROOT / "config" / "theme_colors.py"
        try:
            lines = [
                '"""',
                "Centralized Color Palettes for all Node Assistant Themes.",
                "",
                "Themes are defined as all-caps dictionaries. The ThemeManager will",
                "automatically discover any all-caps variable that is a dictionary.",
                '"""',
                "",
                "from typing import Any",
                "",
            ]

            for theme_name, palette in PALETTES.items():
                lines.append(f"{theme_name}: dict[str, Any] = {{")
                for key, val in palette.items():
                    if isinstance(val, dict):
                        lines.append(f'    "{key}": {{')
                        for sub_k, sub_v in val.items():
                            if isinstance(sub_v, (tuple, list)):
                                lines.append(f'        "{sub_k}": {tuple(sub_v)},')
                            elif isinstance(sub_v, str):
                                lines.append(f'        "{sub_k}": "{sub_v}",')
                            else:
                                lines.append(f'        "{sub_k}": {sub_v},')
                        lines.append("    },")
                    elif isinstance(val, (tuple, list)):
                        lines.append(f'    "{key}": {tuple(val)},')
                    elif isinstance(val, str):
                        lines.append(f'    "{key}": "{val}",')
                    else:
                        lines.append(f'    "{key}": {val},')
                # Remove trailing comma on last item or keep clean
                if lines[-1].endswith(","):
                    lines[-1] = lines[-1][:-1]
                lines.append("}\n")

            content = "\n".join(lines).strip() + "\n"
            theme_colors_path.write_text(content, encoding="utf-8")
            self._show_status("Saved palettes to config/theme_colors.py!")
            logger.info("Theme palettes successfully saved to theme_colors.py")
        except Exception as e:
            logger.error(f"Failed to save theme_colors.py: {e}")
            self._show_status(f"Error saving: {e}")


# Global singleton
theme_manager_win: ThemeManagerWin = ThemeManagerWin()
