"""
Pipeline Editor module for Pipeline Creator.

Provides an interactive GUI tool to open, inspect, edit, and create pipeline JSON layouts.
Users can view and modify modules, connections ("who connects to what"), layout views,
and raw JSON configurations, as well as load edited pipelines directly into the workspace.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.file_explorer import file_explorer
from core.paths import LAYOUTS_DIR, PROJECT_ROOT


class PipelineEditor:
    """
    Interactive GUI editor and inspector for pipeline JSON layouts.
    """

    def __init__(self) -> None:
        self.winID: str = "pipeline_editor_win"
        self.current_filepath: Optional[str] = None
        self.pipeline_data: Dict[str, Any] = {
            "is_relative": True,
            "windows": [],
            "connections": [],
            "views": {},
            "link_nodes": [],
        }
        self.selected_module_idx: int = -1
        self.selected_connection_idx: Optional[int] = None
        self.selected_view_name: Optional[str] = None
        self.selected_view_win_idx: Optional[int] = None
        self.module_filter: str = ""
        self._theme_cache: Dict[Any, Any] = {}
        self.active_tab: str = f"{self.winID}_tab_overview"
        self.json_status: str = ""
        self.json_status_color: Tuple[int, int, int] = (180, 255, 180)
        self._canvas_drag: Dict[str, Any] = {"active": False, "mode": None, "win_idx": -1, "start_mouse": [0, 0], "orig_pos": [0, 0], "orig_size": [0, 0]}
        self._canvas_w: int = 680
        self._canvas_h: int = 360
        self.sync_live_workspace: bool = True
        self.enable_collision: bool = False

    def show(self, filepath: Optional[str] = None) -> None:
        """Open and display the Pipeline Editor window, preselecting the currently loaded pipeline JSON."""
        import core.module_registry as mr

        target_file = filepath or mr.LAST_LOADED_WORKSPACE

        # 1. Explicit filepath or active loaded pipeline JSON
        if target_file and Path(target_file).exists():
            self.load_file(str(target_file))
        # 2. Fallback to first available layout file in layouts directory if no pipeline loaded
        elif LAYOUTS_DIR.exists() and list(LAYOUTS_DIR.glob("*.json")):
            layouts = sorted(list(LAYOUTS_DIR.glob("*.json")), key=lambda p: p.stem.lower())
            self.load_file(str(layouts[0].resolve()))
        # 3. Empty template
        else:
            self._init_empty_pipeline()

        self._build_ui()

    def _import_from_live_workspace(self) -> None:
        """Capture the current live workspace layout, modules, and connections from the running application."""
        try:
            from core.module_registry import MODULES_REGISTRY, export_workspace, LAST_LOADED_WORKSPACE
            from core.main_win import main_win

            node_positions = (
                main_win.node_editor.get_node_positions()
                if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "get_node_positions")
                else None
            )
            data = export_workspace(MODULES_REGISTRY, filepath=None, node_positions=node_positions)

            if (
                hasattr(main_win, "node_editor")
                and hasattr(main_win.node_editor, "serialize_link_nodes")
            ):
                link_nodes = main_win.node_editor.serialize_link_nodes()
                if link_nodes:
                    data["link_nodes"] = link_nodes

            self.pipeline_data = {
                "is_relative": data.get("is_relative", True),
                "windows": data.get("windows", []),
                "connections": data.get("connections", []),
                "views": data.get("views", {}),
                "link_nodes": data.get("link_nodes", []),
            }
            self.current_filepath = (
                str(Path(LAST_LOADED_WORKSPACE).resolve())
                if LAST_LOADED_WORKSPACE and Path(LAST_LOADED_WORKSPACE).exists()
                else None
            )
            self.selected_module_idx = 0 if self.pipeline_data["windows"] else -1
            self.selected_connection_idx = None
            self.selected_view_name = (
                list(self.pipeline_data["views"].keys())[0] if self.pipeline_data["views"] else None
            )
            self.selected_view_win_idx = None
            self.json_status = "Active workspace synchronized."
            self.json_status_color = (180, 255, 180)
            logger.info("Pipeline Editor automatically synchronized with current active workspace.")
        except Exception as e:
            logger.warning(f"Could not import active workspace into Pipeline Editor: {e}")

    def _init_empty_pipeline(self) -> None:
        """Initialize with a clean, empty pipeline template."""
        self.current_filepath = None
        self.pipeline_data = {
            "is_relative": True,
            "windows": [],
            "connections": [],
            "views": {},
            "link_nodes": [],
        }
        self.selected_module_idx = -1
        self.selected_view_name = None
        self.json_status = ""

    def load_file(self, filepath: str) -> bool:
        """Load and parse a pipeline JSON file."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.error(f"Invalid JSON in {filepath}: root must be an object.")
                return False

            self.pipeline_data = {
                "is_relative": data.get("is_relative", True),
                "windows": data.get("windows", []),
                "connections": data.get("connections", []),
                "views": data.get("views", {}),
                "link_nodes": data.get("link_nodes", []),
            }
            self.current_filepath = str(Path(filepath).resolve())
            self.selected_module_idx = 0 if self.pipeline_data["windows"] else -1
            self.selected_connection_idx = None
            self.selected_view_name = (
                list(self.pipeline_data["views"].keys())[0] if self.pipeline_data["views"] else None
            )
            self.json_status = ""

            logger.info(f"Pipeline Editor loaded '{Path(filepath).name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to load pipeline file '{filepath}': {e}")
            return False

    def save_file(self, filepath: Optional[str] = None) -> bool:
        """Save the current pipeline data to a JSON file."""
        target = filepath or self.current_filepath
        if not target:
            target = file_explorer.save_file(
                default_path=str(LAYOUTS_DIR),
                default_name="pipeline.json",
                extensions=[("JSON files", "*.json")],
            )
            if not target:
                return False

        if not target.lower().endswith(".json"):
            target += ".json"

        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self.pipeline_data, f, indent=4)
            self.current_filepath = str(Path(target).resolve())
            logger.success(f"Pipeline saved to '{target}'")
            return True
        except Exception as e:
            logger.error(f"Failed to save pipeline to '{target}': {e}")
            return False

    def _build_ui(self, active_tab: Optional[str] = None) -> None:
        """Create or recreate the Pipeline Editor window."""
        if active_tab:
            self.active_tab = active_tab

        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

        s = display_scaling.scale
        win_w = s(1000)
        win_h = s(750)

        vp_w = dpg.get_viewport_client_width() or 1200
        vp_h = dpg.get_viewport_client_height() or 800
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        with dpg.window(
            label="Pipeline Editor",
            tag=self.winID,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
            no_resize=False,
            no_move=False,
        ):
            # Top File / Pipeline Selection Bar
            self._build_top_bar()
            dpg.add_separator()
            dpg.add_spacer(height=s(4))

            # Main Tab Bar
            with dpg.tab_bar(tag=f"{self.winID}_tabs", callback=self._on_tab_changed):
                with dpg.tab(label="Overview", tag=f"{self.winID}_tab_overview"):
                    self._build_overview_tab()

                with dpg.tab(label="Modules & Windows", tag=f"{self.winID}_tab_modules"):
                    self._build_modules_tab()

                with dpg.tab(label="Connections (Flow)", tag=f"{self.winID}_tab_connections"):
                    self._build_connections_tab()

                with dpg.tab(label="Views", tag=f"{self.winID}_tab_views"):
                    self._build_views_tab()

                with dpg.tab(label="Raw JSON", tag=f"{self.winID}_tab_json"):
                    self._build_json_tab()

            # Ensure the intended tab is active
            if self.active_tab and dpg.does_item_exist(self.active_tab):
                dpg.set_value(f"{self.winID}_tabs", self.active_tab)

            dpg.add_spacer(height=s(6))
            dpg.add_separator()
            # Bottom Action Bar
            self._build_bottom_bar()
            dpg.add_spacer(height=s(4))

        # Bind Window Resize Handler to dynamically scale the Viewport canvas
        win_handler_tag = f"{self.winID}_win_resize_handler"
        if dpg.does_item_exist(win_handler_tag):
            dpg.delete_item(win_handler_tag)

        with dpg.item_handler_registry(tag=win_handler_tag):
            dpg.add_item_resize_handler(callback=self._on_window_resized)

        dpg.bind_item_handler_registry(self.winID, win_handler_tag)

    def _on_tab_changed(self, sender: Any, app_data: Any, user_data: Any = None) -> None:
        """Track active tab and auto-sync raw JSON text when switching to the Raw JSON tab."""
        self.active_tab = app_data
        if app_data == f"{self.winID}_tab_json":
            self._on_sync_json_from_data(silent=True)

    @staticmethod
    def _get_module_color(uuid_or_str: str) -> Tuple[int, int, int]:
        """Generate a vibrant, deterministic RGB color based on UUID/string (golden ratio hue distribution)."""
        if not uuid_or_str:
            return (180, 180, 180)
        val = sum(ord(c) * (i + 1) for i, c in enumerate(str(uuid_or_str)))
        hue = (val * 137.508) % 360.0  # Golden angle
        import colorsys
        r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 0.70, 0.95)
        return int(r * 255), int(g * 255), int(b * 255)

    def _build_top_bar(self) -> None:
        """Top selector bar for choosing, browsing, or creating pipelines."""
        s = display_scaling.scale
        layout_files = (
            sorted([f for f in LAYOUTS_DIR.glob("*.json") if f.is_file()], key=lambda p: p.stem.lower())
            if LAYOUTS_DIR.exists()
            else []
        )
        file_map = {f.stem: str(f.resolve()) for f in layout_files}
        if self.current_filepath and Path(self.current_filepath).exists():
            p = Path(self.current_filepath)
            file_map[p.stem] = str(p.resolve())

        combo_items = list(file_map.keys())

        if self.current_filepath:
            curr_stem = Path(self.current_filepath).stem
        else:
            curr_stem = combo_items[0] if combo_items else "Untitled"

        if curr_stem not in combo_items and curr_stem != "Untitled":
            combo_items.insert(0, curr_stem)

        with dpg.group(horizontal=True):
            dpg.add_text("Pipeline:")
            if combo_items:
                dpg.add_combo(
                    items=combo_items,
                    default_value=curr_stem,
                    width=s(200),
                    callback=lambda s, a, u, *args: self._on_select_pipeline_combo(a, file_map),
                )
            dpg.add_button(label="Open File", callback=self._on_browse_open, width=s(120))
            dpg.add_button(label="New", callback=self._on_new_pipeline, width=s(80))

            path_text = f"File: {Path(self.current_filepath).name}" if self.current_filepath else "File: [New / Unsaved]"
            dpg.add_text(path_text)

    def _build_bottom_bar(self) -> None:
        """Bottom action buttons."""
        s = display_scaling.scale
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save", callback=self._on_save_clicked, width=s(85))
            dpg.add_button(label="Save As", callback=self._on_save_as_clicked, width=s(95))
            dpg.add_button(label="Reload", callback=self._on_reload_clicked, width=s(85))
            dpg.add_spacer(width=s(10))
            dpg.add_button(
                label="Import from Workspace",
                callback=self._on_import_from_workspace_clicked,
                width=s(190),
            )
            with dpg.tooltip(parent=dpg.last_item()):
                dpg.add_text("Capture the currently open modules, links, and positions from the running application.")

            dpg.add_button(
                label="Load into Workspace",
                callback=self._on_load_into_workspace_clicked,
                width=s(190),
            )
            with dpg.tooltip(parent=dpg.last_item()):
                dpg.add_text("Instantly instantiate this pipeline into the main application and Node Editor.")

            dpg.add_spacer(width=s(10))
            dpg.add_button(
                label="Close",
                callback=lambda *a: dpg.delete_item(self.winID) if dpg.does_item_exist(self.winID) else None,
                width=s(80),
            )


    def _build_overview_tab(self) -> None:
        """Overview metrics and summary of who connects to whom."""
        s = display_scaling.scale
        container_tag = f"{self.winID}_overview_container"
        if dpg.does_item_exist(container_tag):
            dpg.delete_item(container_tag)

        with dpg.child_window(tag=container_tag, width=-1, height=-1, border=False, parent=f"{self.winID}_tab_overview"):
            num_windows = len(self.pipeline_data.get("windows", []))
            num_conns = len(self.pipeline_data.get("connections", []))
            num_views = len(self.pipeline_data.get("views", {}))
            num_links = len(self.pipeline_data.get("link_nodes", []))

            # Statistics summary
            with dpg.group(horizontal=True):
                with dpg.child_window(width=s(180), height=s(70), border=True):
                    dpg.add_text("Modules / Windows", color=(150, 200, 255))
                    dpg.add_text(f"{num_windows}", color=(255, 255, 255))
                with dpg.child_window(width=s(180), height=s(70), border=True):
                    dpg.add_text("Connections", color=(150, 255, 180))
                    dpg.add_text(f"{num_conns}", color=(255, 255, 255))
                with dpg.child_window(width=s(180), height=s(70), border=True):
                    dpg.add_text("Views", color=(255, 200, 150))
                    dpg.add_text(f"{num_views}", color=(255, 255, 255))
                with dpg.child_window(width=s(180), height=s(70), border=True):
                    dpg.add_text("Link Nodes", color=(255, 160, 220))
                    dpg.add_text(f"{num_links}", color=(255, 255, 255))

            dpg.add_spacer(height=s(10))
            dpg.add_text("Pipeline Architecture Summary (Modules & Connections):", color=(200, 200, 220))
            dpg.add_separator()
            dpg.add_spacer(height=s(6))

            uuid_to_label = self._get_uuid_to_label_map()

            if not self.pipeline_data.get("windows"):
                dpg.add_text("No modules defined in this pipeline.", color=(160, 160, 160))
            else:
                for win in self.pipeline_data["windows"]:
                    uuid = win.get("uuid", "N/A")
                    label = win.get("params", {}).get("label") or win.get("label") or win.get("class_name", uuid)
                    mod_path = win.get("module", "")
                    vis_str = "Visible" if win.get("visible", True) else "Hidden"

                    # Find outgoing connections
                    outgoing = []
                    for conn in self.pipeline_data.get("connections", []):
                        if conn.get("from") == uuid:
                            tgt_id = conn.get("to")
                            tgt_label = uuid_to_label.get(tgt_id, f"UUID:{tgt_id}")
                            out_key = conn.get("output", 0)
                            outgoing.append(f"{tgt_label} (out: {out_key})")

                    src_color = self._get_module_color(uuid)
                    with dpg.group(horizontal=True):
                        dpg.add_text(f"[{label}]", color=src_color)
                        dpg.add_text(f"({mod_path}, {vis_str})", color=(160, 160, 160))
                        if outgoing:
                            dpg.add_text("->", color=(255, 200, 100))
                            dpg.add_text(", ".join(outgoing), color=(180, 255, 180))
                        else:
                            dpg.add_text("(No outgoing links)", color=(120, 120, 120))

    def _build_modules_tab(self) -> None:
        """Module list on the left, module details & parameter editor on the right."""
        s = display_scaling.scale
        container_tag = f"{self.winID}_modules_container"
        if dpg.does_item_exist(container_tag):
            dpg.delete_item(container_tag)

        with dpg.child_window(tag=container_tag, width=-1, height=-1, border=False, parent=f"{self.winID}_tab_modules"):
            with dpg.group(horizontal=True):
                # Left Pane: Module List
                with dpg.child_window(width=s(280), height=-1, border=True):
                    dpg.add_text("Modules List", color=(200, 200, 220))
                    dpg.add_input_text(
                        hint="Filter modules...",
                        default_value=self.module_filter,
                        width=-1,
                        callback=lambda s, a, u, *args: self._on_module_filter_changed(a),
                    )
                    dpg.add_spacer(height=s(4))

                    with dpg.child_window(tag=f"{self.winID}_module_list_child", height=-s(45), border=False):
                        self._populate_module_list()

                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Add", callback=self._on_add_module_dialog, width=s(80))
                        dpg.add_button(label="Dup.", callback=self._on_duplicate_module, width=s(80))
                        dpg.add_button(label="Del.", callback=self._on_delete_module, width=s(80))

                # Right Pane: Module Details Editor
                with dpg.child_window(tag=f"{self.winID}_module_details_pane", width=-1, height=-1, border=True):
                    self._populate_module_details()

    def _populate_module_list(self) -> None:
        """Render selectable module items in the left list."""
        list_child = f"{self.winID}_module_list_child"
        if not dpg.does_item_exist(list_child):
            return

        children = dpg.get_item_children(list_child, 1) or []
        for child in children:
            dpg.delete_item(child)

        windows = self.pipeline_data.get("windows", [])
        for idx, win in enumerate(windows):
            uuid = str(win.get("uuid", ""))
            label = win.get("params", {}).get("label") or win.get("label") or win.get("class_name", f"Module {idx+1}")
            display_label = f"[{uuid}] {label}"

            if self.module_filter and self.module_filter.lower() not in display_label.lower():
                continue

            is_selected = idx == self.selected_module_idx
            dpg.add_selectable(
                label=display_label,
                default_value=is_selected,
                parent=list_child,
                callback=lambda s, a, u, *args: self._on_select_module(u),
                user_data=idx,
            )

    def _populate_module_details(self) -> None:
        """Render detail fields and params editor for the selected module."""
        pane = f"{self.winID}_module_details_pane"
        if not dpg.does_item_exist(pane):
            return

        children = dpg.get_item_children(pane, 1) or []
        for child in children:
            dpg.delete_item(child)

        windows = self.pipeline_data.get("windows", [])
        if self.selected_module_idx < 0 or self.selected_module_idx >= len(windows):
            dpg.add_text("Select a module from the list to inspect and edit its properties.", parent=pane, color=(160, 160, 160))
            return

        win = windows[self.selected_module_idx]
        s = display_scaling.scale

        with dpg.group(parent=pane):
            label_val = win.get("params", {}).get("label") or win.get("label") or win.get("class_name", "")
            dpg.add_text(f"Editing Module: {label_val}")
            dpg.add_separator()
            dpg.add_spacer(height=s(4))

            # UUID & Label
            with dpg.group(horizontal=True):
                dpg.add_text("UUID:")
                dpg.add_input_text(
                    default_value=str(win.get("uuid", "")),
                    width=s(160),
                    callback=lambda s, a, u, *args: win.__setitem__("uuid", a.strip()),
                )

                dpg.add_text("Label:")
                dpg.add_input_text(
                    default_value=str(label_val),
                    width=-1,
                    callback=lambda s, a, u, *args: self._on_module_label_changed(win, a.strip()),
                )

            # Module class path & Class Name
            with dpg.group(horizontal=True):
                dpg.add_text("Module Path:")
                dpg.add_input_text(
                    default_value=str(win.get("module", "")),
                    width=s(280),
                    callback=lambda s, a, u, *args: win.__setitem__("module", a.strip()),
                )

                dpg.add_text("Class Name:")
                dpg.add_input_text(
                    default_value=str(win.get("class_name", "")),
                    width=-1,
                    callback=lambda s, a, u, *args: win.__setitem__("class_name", a.strip()),
                )

            # Window Position and Size
            pos = list(win.get("pos", [0, 0]))
            size = list(win.get("size", [-1, -1]))
            with dpg.group(horizontal=True):
                dpg.add_text("Window Pos:")
                dpg.add_drag_floatx(
                    default_value=pos,
                    size=2,
                    width=s(200),
                    speed=0.5,
                    callback=lambda s, a, u, *args: win.__setitem__("pos", [round(a[0], 2), round(a[1], 2)]),
                )

                dpg.add_text("Size (W, H):")
                dpg.add_drag_floatx(
                    default_value=size,
                    size=2,
                    width=-1,
                    speed=0.5,
                    callback=lambda s, a, u, *args: win.__setitem__("size", [round(a[0], 2), round(a[1], 2)]),
                )

            # Node Editor Pos & Visibility
            node_pos = list(win.get("node_pos", [0, 0]))
            with dpg.group(horizontal=True):
                dpg.add_text("Node Pos:")
                dpg.add_drag_floatx(
                    default_value=node_pos,
                    size=2,
                    width=s(200),
                    speed=1.0,
                    callback=lambda s, a, u, *args: win.__setitem__("node_pos", [int(a[0]), int(a[1])]),
                )

                dpg.add_checkbox(
                    label="Visible by Default",
                    default_value=win.get("visible", True),
                    callback=lambda s, a, u, *args: win.__setitem__("visible", a),
                )

            dpg.add_spacer(height=s(6))
            dpg.add_text("Parameters (params dict):", color=(200, 200, 220))
            dpg.add_separator()

            params = win.get("params", {})
            params_str = json.dumps(params, indent=4)
            dpg.add_input_text(
                default_value=params_str,
                multiline=True,
                width=-1,
                height=s(140),
                callback=lambda s, a, u, *args: self._on_params_text_changed(win, a),
            )

    def _on_module_label_changed(self, win: dict, new_label: str) -> None:
        """Update label in params as well as top-level label."""
        if "params" in win and isinstance(win["params"], dict):
            win["params"]["label"] = new_label
        win["label"] = new_label
        self._populate_module_list()

    def _on_params_text_changed(self, win: dict, text: str) -> None:
        """Update params dict from JSON string."""
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                win["params"] = parsed
        except Exception:
            pass

    def _on_select_module(self, idx: int) -> None:
        """Select a module and refresh the details pane."""
        self.selected_module_idx = idx
        self._populate_module_list()
        self._populate_module_details()

    def _on_module_filter_changed(self, filter_text: str) -> None:
        """Filter the module list."""
        self.module_filter = filter_text.strip()
        self._populate_module_list()

    def _on_add_module_dialog(self, *args, **kwargs) -> None:
        """Open a dialog to add a new module from available registry."""
        from core.module_registry import get_available_modules

        avail = get_available_modules()
        module_options = sorted(list(avail.keys())) if avail else []

        popup_id = f"{self.winID}_add_module_popup"
        if dpg.does_item_exist(popup_id):
            dpg.delete_item(popup_id)

        s = display_scaling.scale

        def _do_add(sender=None, app_data=None, user_data=None, *a, **kw):
            chosen_mod = dpg.get_value(f"{popup_id}_combo")
            uuid_val = dpg.get_value(f"{popup_id}_uuid").strip()
            label_val = dpg.get_value(f"{popup_id}_label").strip()

            if not uuid_val:
                # Generate unique UUID
                existing = {str(w.get("uuid")) for w in self.pipeline_data.get("windows", [])}
                i = 100
                while str(i) in existing:
                    i += 1
                uuid_val = str(i)

            cls_obj = avail.get(chosen_mod)
            cls_name = cls_obj.__name__ if cls_obj else Path(chosen_mod).stem.title()
            if not label_val:
                label_val = cls_name

            new_win = {
                "module": chosen_mod,
                "class_name": cls_name,
                "uuid": uuid_val,
                "pos": [20.0, 20.0] if self.pipeline_data.get("is_relative", True) else [100, 100],
                "size": [30.0, 30.0] if self.pipeline_data.get("is_relative", True) else [400, 300],
                "visible": True,
                "params": {"label": label_val},
                "node_pos": [100, 100],
            }

            self.pipeline_data.setdefault("windows", []).append(new_win)
            self.selected_module_idx = len(self.pipeline_data["windows"]) - 1
            dpg.delete_item(popup_id)
            self._populate_module_list()
            self._populate_module_details()

        with dpg.window(label="Add Module to Pipeline", modal=True, show=True, tag=popup_id, width=s(420), height=s(260)):
            dpg.add_text("Select Module:")
            dpg.add_combo(
                items=module_options,
                default_value=module_options[0] if module_options else "",
                tag=f"{popup_id}_combo",
                width=-1,
            )

            dpg.add_spacer(height=s(5))
            dpg.add_text("UUID (Optional):")
            dpg.add_input_text(tag=f"{popup_id}_uuid", hint="Auto-generated if empty", width=-1)

            dpg.add_spacer(height=s(5))
            dpg.add_text("Label (Optional):")
            dpg.add_input_text(tag=f"{popup_id}_label", hint="Defaults to class name", width=-1)

            dpg.add_spacer(height=s(10))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Add", callback=_do_add, width=s(110))
                dpg.add_button(label="Cancel", callback=lambda *a: dpg.delete_item(popup_id), width=s(110))

    def _on_duplicate_module(self, *args, **kwargs) -> None:
        """Duplicate the currently selected module."""
        windows = self.pipeline_data.get("windows", [])
        if self.selected_module_idx < 0 or self.selected_module_idx >= len(windows):
            return

        source = windows[self.selected_module_idx]
        dup = copy.deepcopy(source)

        # Generate a new unique UUID
        existing = {str(w.get("uuid")) for w in windows}
        i = 100
        while str(i) in existing:
            i += 1
        dup["uuid"] = str(i)

        label = dup.get("params", {}).get("label") or dup.get("label", "Module")
        new_label = f"{label} (Copy)"
        if "params" in dup and isinstance(dup["params"], dict):
            dup["params"]["label"] = new_label
        dup["label"] = new_label

        windows.append(dup)
        self.selected_module_idx = len(windows) - 1
        self._populate_module_list()
        self._populate_module_details()

    def _on_delete_module(self, *args, **kwargs) -> None:
        """Delete the currently selected module and its associated connections."""
        windows = self.pipeline_data.get("windows", [])
        if self.selected_module_idx < 0 or self.selected_module_idx >= len(windows):
            return

        deleted = windows.pop(self.selected_module_idx)
        deleted_uuid = str(deleted.get("uuid", ""))

        # Remove connections referencing this UUID
        conns = self.pipeline_data.get("connections", [])
        self.pipeline_data["connections"] = [
            c for c in conns if str(c.get("from")) != deleted_uuid and str(c.get("to")) != deleted_uuid
        ]

        # Clamp index
        if self.selected_module_idx >= len(windows):
            self.selected_module_idx = len(windows) - 1

        self._populate_module_list()
        self._populate_module_details()

    def _build_connections_tab(self) -> None:
        """Visual table of all inter-module connections and connection creator."""
        s = display_scaling.scale
        container_tag = f"{self.winID}_conns_container"
        if dpg.does_item_exist(container_tag):
            dpg.delete_item(container_tag)

        with dpg.child_window(tag=container_tag, width=-1, height=-1, border=False, parent=f"{self.winID}_tab_connections"):
            uuid_to_label = self._get_uuid_to_label_map()
            module_items = list(uuid_to_label.keys())

            dpg.add_text("Create New Connection:", color=(200, 200, 220))
            with dpg.group(horizontal=True):
                dpg.add_text("Source:")
                dpg.add_combo(
                    items=[f"[{u}] {uuid_to_label[u]}" for u in module_items],
                    tag=f"{self.winID}_new_conn_from",
                    width=s(210),
                )
                dpg.add_text("Output:")
                dpg.add_input_text(
                    default_value="0",
                    tag=f"{self.winID}_new_conn_out",
                    width=s(70),
                )
                dpg.add_text("Target:")
                dpg.add_combo(
                    items=[f"[{u}] {uuid_to_label[u]}" for u in module_items],
                    tag=f"{self.winID}_new_conn_to",
                    width=s(210),
                )
                dpg.add_button(label="Add Link", callback=self._on_add_connection_clicked, width=s(110))

            dpg.add_spacer(height=s(10))
            dpg.add_text("Existing Connections (Flow):", color=(200, 200, 220))
            dpg.add_separator()
            dpg.add_spacer(height=s(4))

            with dpg.group(tag=f"{self.winID}_conns_table_child"):
                self._populate_connections_table()

    def _populate_connections_table(self) -> None:
        """Render the connections list in a structured table with front delete button and click-to-highlight."""
        table_child = f"{self.winID}_conns_table_child"
        if not dpg.does_item_exist(table_child):
            return

        children = dpg.get_item_children(table_child, 1) or []
        for child in children:
            dpg.delete_item(child)

        uuid_to_label = self._get_uuid_to_label_map()
        conns = self.pipeline_data.get("connections", [])

        if not conns:
            dpg.add_text("No connections in this pipeline.", parent=table_child, color=(160, 160, 160))
            return

        s = display_scaling.scale
        with dpg.table(
            header_row=True,
            borders_innerH=True,
            borders_outerH=True,
            borders_innerV=True,
            borders_outerV=True,
            row_background=True,
            resizable=True,
            scrollY=True,
            tag=f"{self.winID}_conns_table",
            parent=table_child,
            height=-1,
        ):
            dpg.add_table_column(label="Action", width_fixed=True, init_width_or_weight=s(75))
            dpg.add_table_column(label="#", width_fixed=True, init_width_or_weight=s(35))
            dpg.add_table_column(label="Source Module (Start)", init_width_or_weight=s(230))
            dpg.add_table_column(label="Output", width_fixed=True, init_width_or_weight=s(80))
            dpg.add_table_column(label="Target Module (End)", init_width_or_weight=s(230))

            for idx, conn in enumerate(conns):
                src_id = str(conn.get("from", ""))
                tgt_id = str(conn.get("to", ""))
                out_key = conn.get("output", 0)

                src_name = uuid_to_label.get(src_id, f"UUID:{src_id}")
                tgt_name = uuid_to_label.get(tgt_id, f"UUID:{tgt_id}")

                is_selected = (idx == self.selected_connection_idx)

                with dpg.table_row():
                    # 1. Action (Delete)
                    dpg.add_button(
                        label="Delete",
                        callback=lambda s, a, u, *args: self._on_delete_connection(u),
                        user_data=idx,
                        width=s(65),
                    )

                    # 2. #
                    dpg.add_text(f"#{idx+1}", color=(160, 160, 160))

                    # 3. Source Module (Clickable)
                    dpg.add_selectable(
                        label=f"[{src_id}] {src_name}",
                        default_value=is_selected,
                        span_columns=False,
                        callback=lambda s, a, u, *args: self._on_select_connection(u),
                        user_data=idx,
                    )

                    # 4. Output
                    dpg.add_text(f"out: {out_key}", color=(255, 210, 120))

                    # 5. Target Module (Clickable)
                    dpg.add_selectable(
                        label=f"[{tgt_id}] {tgt_name}",
                        default_value=is_selected,
                        span_columns=False,
                        callback=lambda s, a, u, *args: self._on_select_connection(u),
                        user_data=idx,
                    )

    def _on_select_connection(self, idx: int) -> None:
        """Select a connection and highlight its start (source) and end (target) module windows."""
        self.selected_connection_idx = idx
        conns = self.pipeline_data.get("connections", [])
        if 0 <= idx < len(conns):
            conn = conns[idx]
            src_id = str(conn.get("from", ""))
            tgt_id = str(conn.get("to", ""))
            out_key = conn.get("output", 0)
            self._highlight_connection_windows(src_id, tgt_id, out_key)
        self._populate_connections_table()

    def _highlight_connection_windows(self, src_uuid: str, tgt_uuid: str, output_key: Any = 0) -> None:
        """Highlight the source window (start) and target window (end) in the application and Node Editor."""
        from core.module_registry import MODULES_REGISTRY
        from config.theme_manager import theme_manager

        src_theme = theme_manager.create_highlight_theme()

        try:
            out_idx = int(output_key)
        except (ValueError, TypeError):
            out_idx = 1

        tgt_theme, _, _ = theme_manager.get_output_color_theme_from_cache(out_idx, self._theme_cache)

        # Reset all existing module windows first
        for inst in MODULES_REGISTRY.values():
            win_id = getattr(inst, "winID", None)
            if win_id and dpg.does_item_exist(win_id):
                dpg.bind_item_theme(win_id, 0)
            wrapper_id = getattr(inst, "_merge_wrapper_id", None)
            if wrapper_id and dpg.does_item_exist(wrapper_id):
                dpg.bind_item_theme(wrapper_id, theme_manager.global_theme)

        # Apply source theme and target theme to running windows
        for inst in MODULES_REGISTRY.values():
            inst_uuid = str(getattr(inst, "UUID", ""))
            if inst_uuid == str(src_uuid):
                win_id = getattr(inst, "winID", None)
                if win_id and dpg.does_item_exist(win_id):
                    dpg.bind_item_theme(win_id, src_theme)
                wrapper_id = getattr(inst, "_merge_wrapper_id", None)
                if wrapper_id and dpg.does_item_exist(wrapper_id):
                    dpg.bind_item_theme(wrapper_id, src_theme)
            elif inst_uuid == str(tgt_uuid):
                win_id = getattr(inst, "winID", None)
                if win_id and dpg.does_item_exist(win_id):
                    dpg.bind_item_theme(win_id, tgt_theme)
                wrapper_id = getattr(inst, "_merge_wrapper_id", None)
                if wrapper_id and dpg.does_item_exist(wrapper_id):
                    dpg.bind_item_theme(wrapper_id, tgt_theme)

        # Also trigger node highlight in active NodeEditor if present
        try:
            from core.main_win import main_win

            if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "node_map"):
                ne = main_win.node_editor
                src_nid = None
                tgt_nid = None
                for nid, inst in ne.node_map.items():
                    if str(getattr(inst, "UUID", "")) == str(src_uuid):
                        src_nid = nid
                    elif str(getattr(inst, "UUID", "")) == str(tgt_uuid):
                        tgt_nid = nid

                if hasattr(ne, "_clear_highlights"):
                    ne._clear_highlights()
                if hasattr(ne, "_dim_all_items"):
                    ne._dim_all_items()
                if src_nid and hasattr(ne, "_apply_node_style"):
                    ne._apply_node_style(src_nid, src_theme)
                if tgt_nid and hasattr(ne, "_apply_node_style"):
                    ne._apply_node_style(tgt_nid, tgt_theme)
        except Exception as e:
            logger.debug(f"Could not update node editor highlight: {e}")

    def _on_add_connection_clicked(self, *args, **kwargs) -> None:
        """Add a connection from the input fields."""
        src_raw = dpg.get_value(f"{self.winID}_new_conn_from")
        tgt_raw = dpg.get_value(f"{self.winID}_new_conn_to")
        out_raw = dpg.get_value(f"{self.winID}_new_conn_out")

        if not src_raw or not tgt_raw:
            logger.warning("Please select both a source and a target module.")
            return

        # Extract UUID from "[<uuid>] Label"
        src_uuid = src_raw.split("]")[0].replace("[", "").strip()
        tgt_uuid = tgt_raw.split("]")[0].replace("[", "").strip()

        try:
            out_val: Any = int(out_raw)
        except ValueError:
            out_val = out_raw.strip()

        new_conn = {"from": src_uuid, "to": tgt_uuid, "output": out_val}
        self.pipeline_data.setdefault("connections", []).append(new_conn)
        self.selected_connection_idx = len(self.pipeline_data["connections"]) - 1
        self._highlight_connection_windows(src_uuid, tgt_uuid, out_val)
        self._populate_connections_table()

    def _on_delete_connection(self, idx: int) -> None:
        """Remove a connection by index."""
        conns = self.pipeline_data.get("connections", [])
        if 0 <= idx < len(conns):
            conns.pop(idx)
            if self.selected_connection_idx == idx:
                self.selected_connection_idx = None
            elif self.selected_connection_idx is not None and self.selected_connection_idx > idx:
                self.selected_connection_idx -= 1
            self._populate_connections_table()

    def _build_views_tab(self) -> None:
        """View manager: listing views, interactive layout canvas, window layouts per view, add/delete."""
        s = display_scaling.scale
        container_tag = f"{self.winID}_views_container"
        if dpg.does_item_exist(container_tag):
            dpg.delete_item(container_tag)

        with dpg.child_window(tag=container_tag, width=-1, height=-1, border=False, parent=f"{self.winID}_tab_views"):
            with dpg.group(horizontal=True):
                # Left pane: View list
                with dpg.child_window(width=s(230), height=-1, border=True):
                    dpg.add_text("Layout Views", color=(200, 200, 220))
                    dpg.add_separator()

                    with dpg.child_window(tag=f"{self.winID}_views_list_child", height=-s(45), border=False):
                        self._populate_views_list()

                    with dpg.group(horizontal=True):
                        dpg.add_button(label="New", callback=self._on_new_view_dialog, width=s(85))
                        dpg.add_button(label="Delete", callback=self._on_delete_view, width=s(85))

                # Right pane: View Canvas Preview & Details
                with dpg.child_window(tag=f"{self.winID}_view_details_pane", width=-1, height=-1, border=True):
                    self._populate_view_details()

    def _populate_views_list(self) -> None:
        """Populate the list of views in the left pane."""
        list_child = f"{self.winID}_views_list_child"
        if not dpg.does_item_exist(list_child):
            return

        children = dpg.get_item_children(list_child, 1) or []
        for child in children:
            dpg.delete_item(child)

        views = self.pipeline_data.get("views", {})
        if not views:
            dpg.add_text("No views defined.", parent=list_child, color=(160, 160, 160))
            return

        for view_name in views.keys():
            is_selected = view_name == self.selected_view_name
            dpg.add_selectable(
                label=view_name,
                default_value=is_selected,
                parent=list_child,
                callback=lambda s, a, u, *args: self._on_select_view(u),
                user_data=view_name,
            )

    def _populate_view_details(self) -> None:
        """Display interactive canvas and window details for the selected view."""
        pane = f"{self.winID}_view_details_pane"
        if not dpg.does_item_exist(pane):
            return

        children = dpg.get_item_children(pane, 1) or []
        for child in children:
            dpg.delete_item(child)

        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            dpg.add_text(
                "Select a view from the list on the left to inspect, edit and preview its window layout.",
                parent=pane,
                color=(160, 160, 160),
            )
            return

        vdata = views[self.selected_view_name]
        windows = vdata.get("windows", [])
        s = display_scaling.scale
        is_rel = vdata.get("is_relative", self.pipeline_data.get("is_relative", True))

        self._update_canvas_dimensions()

        with dpg.group(parent=pane):
            # Top Controls Header
            with dpg.group(horizontal=True):
                dpg.add_text(f"View: {self.selected_view_name}", color=(255, 205, 90))
                dpg.add_text(f"({len(windows)} windows)", color=(170, 175, 190))
                dpg.add_spacer(width=s(8))
                dpg.add_checkbox(
                    label="Relative (%)",
                    default_value=is_rel,
                    callback=lambda s, a, u, *args: vdata.__setitem__("is_relative", a),
                )
                dpg.add_spacer(width=s(6))
                dpg.add_checkbox(
                    label="Live Sync",
                    default_value=self.sync_live_workspace,
                    callback=lambda s, a, u, *args: setattr(self, "sync_live_workspace", bool(a)),
                )
                with dpg.tooltip(parent=dpg.last_item()):
                    dpg.add_text("Synchronize window movements and resizing live to the running application.")

                dpg.add_spacer(width=s(6))
                dpg.add_checkbox(
                    label="Collision",
                    default_value=self.enable_collision,
                    callback=lambda s, a, u, *args: setattr(self, "enable_collision", bool(a)),
                )
                with dpg.tooltip(parent=dpg.last_item()):
                    dpg.add_text("Prevent windows from overlapping by stopping at contact borders.")

                dpg.add_spacer(width=s(8))
                dpg.add_button(
                    label="\uf067 Add Window...",
                    callback=lambda: self._open_add_window_popup(),
                    width=s(120),
                )
                with dpg.tooltip(parent=dpg.last_item()):
                    dpg.add_text("Add a pipeline module window that is not yet in this view.")

                dpg.add_button(
                    label="Capture Workspace",
                    callback=self._on_capture_workspace_for_view,
                    width=s(135),
                )
                with dpg.tooltip(parent=dpg.last_item()):
                    dpg.add_text("Overwrite this view with current open window positions from live application.")

            dpg.add_text(
                "Left-click: Drag window body to move, drag bottom-right corner to resize. Right-click: Context menu (Add / Remove).",
                color=(140, 150, 170),
            )
            dpg.add_spacer(height=s(4))

            # Canvas Frame Container
            canvas_tag = f"{self.winID}_view_canvas"
            with dpg.child_window(
                tag=f"{self.winID}_canvas_frame",
                width=self._canvas_w + int(s(16)),
                height=self._canvas_h + int(s(16)),
                border=True,
            ):
                with dpg.drawlist(
                    tag=canvas_tag,
                    width=self._canvas_w,
                    height=self._canvas_h,
                ):
                    pass

            # Bind Canvas Mouse Event Handlers
            handler_tag = f"{self.winID}_canvas_item_handler"
            if dpg.does_item_exist(handler_tag):
                dpg.delete_item(handler_tag)

            with dpg.item_handler_registry(tag=handler_tag):
                dpg.add_item_clicked_handler(
                    button=dpg.mvMouseButton_Left,
                    callback=self._on_canvas_left_click,
                )
                dpg.add_item_clicked_handler(
                    button=dpg.mvMouseButton_Right,
                    callback=self._on_canvas_right_click,
                )
                dpg.add_item_active_handler(callback=self._on_canvas_active)

            dpg.bind_item_handler_registry(canvas_tag, handler_tag)

            # Draw initial canvas contents
            self._draw_view_canvas()

    def _update_canvas_dimensions(self) -> None:
        """Dynamically compute the optimal viewport canvas dimensions based on current window size."""
        s = display_scaling.scale
        win_w = dpg.get_item_width(self.winID) if dpg.does_item_exist(self.winID) else 0
        win_h = dpg.get_item_height(self.winID) if dpg.does_item_exist(self.winID) else 0

        if not win_w or win_w <= 0:
            win_w = s(1000)
        if not win_h or win_h <= 0:
            win_h = s(750)

        # Available width for right pane with generous margin on right side
        avail_w = max(s(360), win_w - s(330))
        # Available height for canvas
        avail_h = max(s(240), win_h - s(195))

        # Keep a clean 16:9 aspect ratio
        aspect = 16.0 / 9.0
        cand_w = avail_w
        cand_h = cand_w / aspect

        if cand_h > avail_h:
            cand_h = avail_h
            cand_w = cand_h * aspect

        self._canvas_w = int(max(s(360), cand_w))
        self._canvas_h = int(max(s(200), cand_h))

    def _on_window_resized(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Handle resizing of the Pipeline Editor window to adapt the view canvas in real time."""
        canvas_tag = f"{self.winID}_view_canvas"
        frame_tag = f"{self.winID}_canvas_frame"
        if not dpg.does_item_exist(canvas_tag) or not dpg.does_item_exist(frame_tag):
            return

        s = display_scaling.scale
        self._update_canvas_dimensions()

        dpg.configure_item(frame_tag, width=self._canvas_w + int(s(16)), height=self._canvas_h + int(s(16)))
        dpg.configure_item(canvas_tag, width=self._canvas_w, height=self._canvas_h)
        self._draw_view_canvas()

    def _draw_view_canvas(self) -> None:
        """Redraw all background elements and window boxes on the interactive view canvas."""
        canvas_tag = f"{self.winID}_view_canvas"
        if not dpg.does_item_exist(canvas_tag):
            return

        # Clear canvas
        children = dpg.get_item_children(canvas_tag, 2) or []
        for child in children:
            dpg.delete_item(child)

        cw = self._canvas_w
        ch = self._canvas_h

        # 1. Background dark canvas screen
        dpg.draw_rectangle([0, 0], [cw, ch], fill=(22, 25, 32, 255), color=(60, 68, 85, 255), thickness=1.5, parent=canvas_tag)

        # 2. Subtle coordinate grid (25%, 50%, 75%)
        for pct in [0.25, 0.50, 0.75]:
            gx = cw * pct
            gy = ch * pct
            dpg.draw_line([gx, 0], [gx, ch], color=(45, 52, 68, 100), thickness=1.0, parent=canvas_tag)
            dpg.draw_line([0, gy], [cw, gy], color=(45, 52, 68, 100), thickness=1.0, parent=canvas_tag)

        # 3. Canvas title badge
        dpg.draw_text([10, 8], "VIRTUAL VIEWPORT", color=(100, 115, 145, 180), size=11, parent=canvas_tag)

        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return

        vdata = views[self.selected_view_name]
        windows = vdata.get("windows", [])
        is_rel = vdata.get("is_relative", self.pipeline_data.get("is_relative", True))

        # 4. Draw each window in this view
        for idx, win in enumerate(windows):
            uuid = str(win.get("uuid", ""))
            label = str(win.get("label", win.get("class_name", f"UUID:{uuid}")))
            pos = win.get("pos", [0, 0])
            size = win.get("size", [25, 25])

            if is_rel:
                x = (pos[0] / 100.0) * cw
                y = (pos[1] / 100.0) * ch
                w = (size[0] / 100.0) * cw if size[0] > 0 else cw * 0.25
                h = (size[1] / 100.0) * ch if size[1] > 0 else ch * 0.25
            else:
                x = (pos[0] / 1920.0) * cw
                y = (pos[1] / 1080.0) * ch
                w = (size[0] / 1920.0) * cw if size[0] > 0 else cw * 0.25
                h = (size[1] / 1080.0) * ch if size[1] > 0 else ch * 0.25

            # Clamp drawn box to canvas
            w = max(35.0, w)
            h = max(24.0, h)

            r, g, b = self._get_module_color(uuid)
            is_selected = idx == self.selected_view_win_idx

            border_color = (255, 220, 80, 255) if is_selected else (r, g, b, 230)
            thickness = 2.5 if is_selected else 1.5
            fill_color = (32, 38, 52, 225) if not is_selected else (45, 52, 70, 240)

            # Window body rectangle
            dpg.draw_rectangle([x, y], [x + w, y + h], fill=fill_color, color=border_color, thickness=thickness, rounding=4, parent=canvas_tag)

            # Window header bar
            header_h = min(22.0, h * 0.4)
            dpg.draw_rectangle([x, y], [x + w, y + header_h], fill=(r, g, b, 160), color=border_color, thickness=1.0, rounding=4, parent=canvas_tag)

            # Window label
            dpg.draw_text([x + 5, y + 3], label[:20], color=(255, 255, 255, 255), size=12, parent=canvas_tag)

            # Window dimensions text if space permits
            if h >= 45 and w >= 60:
                dim_text = f"{pos[0]:.0f}%, {pos[1]:.0f}% ({size[0]:.0f}x{size[1]:.0f}%)" if is_rel else f"{pos[0]:.0f},{pos[1]:.0f}"
                dpg.draw_text([x + 5, y + header_h + 4], dim_text, color=(170, 185, 210, 200), size=10, parent=canvas_tag)

            # Resize corner handle (bottom-right grip)
            grip_size = min(14.0, min(w, h) * 0.35)
            p1 = [x + w - grip_size, y + h]
            p2 = [x + w, y + h - grip_size]
            p3 = [x + w, y + h]
            dpg.draw_triangle(p1, p2, p3, fill=(r, g, b, 220), color=border_color, parent=canvas_tag)

    def _hit_test_canvas(self, mx: float, my: float) -> Tuple[int, Optional[str]]:
        """Return (window_index, action_mode) where action_mode is 'resize' or 'move'."""
        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return -1, None

        vdata = views[self.selected_view_name]
        windows = vdata.get("windows", [])
        is_rel = vdata.get("is_relative", self.pipeline_data.get("is_relative", True))
        cw = self._canvas_w
        ch = self._canvas_h

        # Iterate in reverse (topmost first)
        for idx in range(len(windows) - 1, -1, -1):
            win = windows[idx]
            pos = win.get("pos", [0, 0])
            size = win.get("size", [25, 25])

            if is_rel:
                x = (pos[0] / 100.0) * cw
                y = (pos[1] / 100.0) * ch
                w = (size[0] / 100.0) * cw if size[0] > 0 else cw * 0.25
                h = (size[1] / 100.0) * ch if size[1] > 0 else ch * 0.25
            else:
                x = (pos[0] / 1920.0) * cw
                y = (pos[1] / 1080.0) * ch
                w = (size[0] / 1920.0) * cw if size[0] > 0 else cw * 0.25
                h = (size[1] / 1080.0) * ch if size[1] > 0 else ch * 0.25

            w = max(35.0, w)
            h = max(24.0, h)

            # Check resize grip (bottom-right corner)
            grip_size = 16.0
            if (x + w - grip_size <= mx <= x + w + 4) and (y + h - grip_size <= my <= y + h + 4):
                return idx, "resize"

            # Check window body
            if x <= mx <= x + w and y <= my <= y + h:
                return idx, "move"

        return -1, None

    def _on_canvas_left_click(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Handle left mouse click on the view preview canvas."""
        mouse_pos = dpg.get_drawing_mouse_pos()
        mx, my = mouse_pos[0], mouse_pos[1]
        idx, mode = self._hit_test_canvas(mx, my)

        if idx >= 0 and mode:
            views = self.pipeline_data.get("views", {})
            windows = views[self.selected_view_name].get("windows", [])
            win = windows[idx]

            self.selected_view_win_idx = idx
            self._canvas_drag = {
                "active": True,
                "mode": mode,
                "win_idx": idx,
                "start_mouse": [mx, my],
                "orig_pos": list(win.get("pos", [0, 0])),
                "orig_size": list(win.get("size", [25, 25])),
            }
        else:
            self.selected_view_win_idx = None
            self._canvas_drag = {"active": False, "mode": None, "win_idx": -1}

        self._draw_view_canvas()

    def _rects_overlap(self, x1: float, y1: float, w1: float, h1: float, x2: float, y2: float, w2: float, h2: float, eps: float = 0.05) -> bool:
        """Check if two rectangles overlap with an epsilon tolerance."""
        return (x1 < x2 + w2 - eps) and (x1 + w1 > x2 + eps) and (y1 < y2 + h2 - eps) and (y1 + h1 > y2 + eps)

    def _resolve_move_collision(
        self,
        orig_x: float,
        orig_y: float,
        target_x: float,
        target_y: float,
        w: float,
        h: float,
        ignore_idx: int,
        windows: List[Dict[str, Any]],
        max_bound_x: float,
        max_bound_y: float,
    ) -> Tuple[float, float]:
        """Resolve movement collision against other windows and screen boundaries."""
        cand_x = max(0.0, min(max_bound_x - w, target_x))
        cand_y = max(0.0, min(max_bound_y - h, target_y))

        # 1. Sweep & clamp along X axis
        if target_x > orig_x:
            # Moving right -> find closest obstacle to our right with vertical overlap
            for i, other in enumerate(windows):
                if i == ignore_idx:
                    continue
                ox = float(other.get("pos", [0, 0])[0])
                oy = float(other.get("pos", [0, 0])[1])
                ow = float(other.get("size", [20, 20])[0])
                oh = float(other.get("size", [20, 20])[1])

                if min(orig_y + h, oy + oh) - max(orig_y, oy) > 0.05:
                    if ox >= orig_x + w - 0.05:
                        cand_x = min(cand_x, ox - w)
        elif target_x < orig_x:
            # Moving left -> find closest obstacle to our left with vertical overlap
            for i, other in enumerate(windows):
                if i == ignore_idx:
                    continue
                ox = float(other.get("pos", [0, 0])[0])
                oy = float(other.get("pos", [0, 0])[1])
                ow = float(other.get("size", [20, 20])[0])
                oh = float(other.get("size", [20, 20])[1])

                if min(orig_y + h, oy + oh) - max(orig_y, oy) > 0.05:
                    if ox + ow <= orig_x + 0.05:
                        cand_x = max(cand_x, ox + ow)

        cand_x = max(0.0, min(max_bound_x - w, cand_x))

        # 2. Sweep & clamp along Y axis (using cand_x)
        if target_y > orig_y:
            # Moving down -> find closest obstacle below us with horizontal overlap with cand_x
            for i, other in enumerate(windows):
                if i == ignore_idx:
                    continue
                ox = float(other.get("pos", [0, 0])[0])
                oy = float(other.get("pos", [0, 0])[1])
                ow = float(other.get("size", [20, 20])[0])
                oh = float(other.get("size", [20, 20])[1])

                if min(cand_x + w, ox + ow) - max(cand_x, ox) > 0.05:
                    if oy >= orig_y + h - 0.05:
                        cand_y = min(cand_y, oy - h)
        elif target_y < orig_y:
            # Moving up -> find closest obstacle above us with horizontal overlap with cand_x
            for i, other in enumerate(windows):
                if i == ignore_idx:
                    continue
                ox = float(other.get("pos", [0, 0])[0])
                oy = float(other.get("pos", [0, 0])[1])
                ow = float(other.get("size", [20, 20])[0])
                oh = float(other.get("size", [20, 20])[1])

                if min(cand_x + w, ox + ow) - max(cand_x, ox) > 0.05:
                    if oy + oh <= orig_y + 0.05:
                        cand_y = max(cand_y, oy + oh)

        cand_y = max(0.0, min(max_bound_y - h, cand_y))

        return cand_x, cand_y

    def _resolve_resize_collision(
        self,
        pos_x: float,
        pos_y: float,
        orig_w: float,
        orig_h: float,
        target_w: float,
        target_h: float,
        ignore_idx: int,
        windows: List[Dict[str, Any]],
        min_size: float,
        max_bound_x: float,
        max_bound_y: float,
    ) -> Tuple[float, float]:
        """Resolve resizing collision against other windows and screen boundaries."""
        cand_w = max(min_size, min(max_bound_x - pos_x, target_w))
        cand_h = max(min_size, min(max_bound_y - pos_y, target_h))

        # Check obstacles to the right
        for i, other in enumerate(windows):
            if i == ignore_idx:
                continue
            ox = float(other.get("pos", [0, 0])[0])
            oy = float(other.get("pos", [0, 0])[1])
            ow = float(other.get("size", [20, 20])[0])
            oh = float(other.get("size", [20, 20])[1])

            if min(pos_y + orig_h, oy + oh) - max(pos_y, oy) > 0.05:
                if ox >= pos_x + 0.05:
                    cand_w = min(cand_w, max(min_size, ox - pos_x))

        # Check obstacles below with resolved cand_w
        for i, other in enumerate(windows):
            if i == ignore_idx:
                continue
            ox = float(other.get("pos", [0, 0])[0])
            oy = float(other.get("pos", [0, 0])[1])
            ow = float(other.get("size", [20, 20])[0])
            oh = float(other.get("size", [20, 20])[1])

            if min(pos_x + cand_w, ox + ow) - max(pos_x, ox) > 0.05:
                if oy >= pos_y + 0.05:
                    cand_h = min(cand_h, max(min_size, oy - pos_y))

        return cand_w, cand_h

    def _on_canvas_active(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Handle mouse drag while left button is active on canvas."""
        if not dpg.is_mouse_button_down(dpg.mvMouseButton_Left):
            self._canvas_drag["active"] = False
            return

        if not self._canvas_drag.get("active") or self._canvas_drag.get("win_idx", -1) < 0:
            return

        idx = self._canvas_drag["win_idx"]
        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return

        windows = views[self.selected_view_name].get("windows", [])
        if idx < 0 or idx >= len(windows):
            return

        win = windows[idx]
        is_rel = views[self.selected_view_name].get("is_relative", self.pipeline_data.get("is_relative", True))
        mouse_pos = dpg.get_drawing_mouse_pos()
        mx, my = mouse_pos[0], mouse_pos[1]

        dx_px = mx - self._canvas_drag["start_mouse"][0]
        dy_px = my - self._canvas_drag["start_mouse"][1]

        if is_rel:
            dx = (dx_px / self._canvas_w) * 100.0
            dy = (dy_px / self._canvas_h) * 100.0
            orig_p = self._canvas_drag["orig_pos"]
            orig_s = self._canvas_drag["orig_size"]

            if self._canvas_drag["mode"] == "move":
                raw_x = orig_p[0] + dx
                raw_y = orig_p[1] + dy
                if self.enable_collision:
                    new_x, new_y = self._resolve_move_collision(
                        orig_x=orig_p[0],
                        orig_y=orig_p[1],
                        target_x=raw_x,
                        target_y=raw_y,
                        w=orig_s[0],
                        h=orig_s[1],
                        ignore_idx=idx,
                        windows=windows,
                        max_bound_x=100.0,
                        max_bound_y=100.0,
                    )
                else:
                    new_x = max(0.0, min(100.0 - orig_s[0], raw_x))
                    new_y = max(0.0, min(100.0 - orig_s[1], raw_y))
                win["pos"] = [round(new_x, 1), round(new_y, 1)]
            elif self._canvas_drag["mode"] == "resize":
                pos_p = win.get("pos", [0, 0])
                raw_w = orig_s[0] + dx
                raw_h = orig_s[1] + dy
                if self.enable_collision:
                    new_w, new_h = self._resolve_resize_collision(
                        pos_x=pos_p[0],
                        pos_y=pos_p[1],
                        orig_w=orig_s[0],
                        orig_h=orig_s[1],
                        target_w=raw_w,
                        target_h=raw_h,
                        ignore_idx=idx,
                        windows=windows,
                        min_size=5.0,
                        max_bound_x=100.0,
                        max_bound_y=100.0,
                    )
                else:
                    new_w = max(5.0, min(100.0 - pos_p[0], raw_w))
                    new_h = max(5.0, min(100.0 - pos_p[1], raw_h))
                win["size"] = [round(new_w, 1), round(new_h, 1)]
        else:
            dx = (dx_px / self._canvas_w) * 1920.0
            dy = (dy_px / self._canvas_h) * 1080.0
            orig_p = self._canvas_drag["orig_pos"]
            orig_s = self._canvas_drag["orig_size"]

            if self._canvas_drag["mode"] == "move":
                raw_x = orig_p[0] + dx
                raw_y = orig_p[1] + dy
                if self.enable_collision:
                    new_x, new_y = self._resolve_move_collision(
                        orig_x=orig_p[0],
                        orig_y=orig_p[1],
                        target_x=raw_x,
                        target_y=raw_y,
                        w=orig_s[0],
                        h=orig_s[1],
                        ignore_idx=idx,
                        windows=windows,
                        max_bound_x=1920.0,
                        max_bound_y=1080.0,
                    )
                else:
                    new_x = max(0.0, min(1920.0 - orig_s[0], raw_x))
                    new_y = max(0.0, min(1080.0 - orig_s[1], raw_y))
                win["pos"] = [round(new_x, 1), round(new_y, 1)]
            elif self._canvas_drag["mode"] == "resize":
                pos_p = win.get("pos", [0, 0])
                raw_w = orig_s[0] + dx
                raw_h = orig_s[1] + dy
                if self.enable_collision:
                    new_w, new_h = self._resolve_resize_collision(
                        pos_x=pos_p[0],
                        pos_y=pos_p[1],
                        orig_w=orig_s[0],
                        orig_h=orig_s[1],
                        target_w=raw_w,
                        target_h=raw_h,
                        ignore_idx=idx,
                        windows=windows,
                        min_size=50.0,
                        max_bound_x=1920.0,
                        max_bound_y=1080.0,
                    )
                else:
                    new_w = max(50.0, min(1920.0 - pos_p[0], raw_w))
                    new_h = max(50.0, min(1080.0 - pos_p[1], raw_h))
                win["size"] = [round(new_w, 1), round(new_h, 1)]

        # Live synchronize real window if it exists in running workspace
        self._sync_real_window_geometry(win, is_rel)

        self._draw_view_canvas()

    def _sync_real_window_geometry(self, win_dict: Dict[str, Any], is_rel: bool) -> None:
        """If the window exists as an active module in the running application, update its real position and size immediately."""
        if not self.sync_live_workspace:
            return

        try:
            from core.module_registry import MODULES_REGISTRY
            uuid_target = str(win_dict.get("uuid", ""))
            mod_class_path = win_dict.get("module")
            if not uuid_target and not mod_class_path:
                return

            target_inst = None
            for inst in MODULES_REGISTRY.values():
                if str(getattr(inst, "UUID", "")) == uuid_target:
                    target_inst = inst
                    break

            if not target_inst and mod_class_path:
                for inst in MODULES_REGISTRY.values():
                    r_mod = inst.__class__.__module__
                    if r_mod == mod_class_path or r_mod == f"modules.{mod_class_path}" or r_mod.replace("modules.", "") == mod_class_path:
                        target_inst = inst
                        break

            if not target_inst or not hasattr(target_inst, "winID") or not dpg.does_item_exist(target_inst.winID):
                return

            pos = list(win_dict.get("pos", [0, 0]))
            size = list(win_dict.get("size", [-1, -1]))

            vp_w = dpg.get_viewport_client_width() or 1920
            vp_h = dpg.get_viewport_client_height() or 1080

            if is_rel:
                real_x = int((pos[0] / 100.0) * vp_w)
                real_y = int((pos[1] / 100.0) * vp_h)
                real_w = int((size[0] / 100.0) * vp_w) if size[0] > 0 else -1
                real_h = int((size[1] / 100.0) * vp_h) if size[1] > 0 else -1
            else:
                real_x = int(pos[0])
                real_y = int(pos[1])
                real_w = int(size[0]) if size[0] > 0 else -1
                real_h = int(size[1]) if size[1] > 0 else -1

            dpg.set_item_pos(target_inst.winID, [real_x, real_y])
            if real_w > 0:
                dpg.set_item_width(target_inst.winID, real_w)
            if real_h > 0:
                dpg.set_item_height(target_inst.winID, real_h)

            target_inst.pos = [real_x, real_y]
            if real_w > 0:
                target_inst.win_width = real_w
            if real_h > 0:
                target_inst.win_height = real_h
        except Exception as e:
            logger.debug(f"Could not live-sync real window geometry: {e}")

    def _on_canvas_right_click(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Handle right click on the canvas: show context menu to add or remove windows at the exact cursor position."""
        drawing_mpos = dpg.get_drawing_mouse_pos()
        abs_mpos = list(dpg.get_mouse_pos(local=False))
        idx, _ = self._hit_test_canvas(drawing_mpos[0], drawing_mpos[1])

        if idx >= 0:
            self._open_window_context_menu(idx, pos=abs_mpos)
        else:
            self._open_add_window_popup(pos=abs_mpos)

    def _open_window_context_menu(self, idx: int, pos: Optional[List[float]] = None) -> None:
        """Open context menu for an existing window in the view at cursor position."""
        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return

        windows = views[self.selected_view_name].get("windows", [])
        if idx < 0 or idx >= len(windows):
            return

        win = windows[idx]
        uuid = str(win.get("uuid", ""))
        label = str(win.get("label", win.get("class_name", f"UUID:{uuid}")))

        popup_tag = f"{self.winID}_view_context_popup"
        if dpg.does_item_exist(popup_tag):
            dpg.delete_item(popup_tag)

        s = display_scaling.scale
        mpos = list(pos) if pos else list(dpg.get_mouse_pos(local=False))

        with dpg.window(
            tag=popup_tag,
            popup=True,
            show=True,
            no_title_bar=True,
            no_move=True,
            no_resize=True,
            pos=mpos,
            width=s(230),
        ):
            dpg.add_text(f"[{uuid}] {label}", color=(255, 200, 100))
            dpg.add_separator()
            dpg.add_menu_item(
                label="\uf00d Remove from this View",
                callback=lambda: (dpg.delete_item(popup_tag) if dpg.does_item_exist(popup_tag) else None, self._remove_window_from_view(idx)),
            )
            dpg.add_menu_item(
                label="\uf077 Bring to Front",
                callback=lambda: (dpg.delete_item(popup_tag) if dpg.does_item_exist(popup_tag) else None, self._bring_window_to_front(idx)),
            )
            dpg.add_menu_item(
                label="\uf065 Center & Reset (40%x40%)",
                callback=lambda: (dpg.delete_item(popup_tag) if dpg.does_item_exist(popup_tag) else None, self._reset_window_in_view(idx)),
            )

    def _open_add_window_popup(self, pos: Optional[List[float]] = None) -> None:
        """Open context menu/popup listing pipeline modules not yet in this view at cursor position."""
        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return

        available = self._get_available_windows_for_view()
        popup_tag = f"{self.winID}_add_win_to_view_popup"
        if dpg.does_item_exist(popup_tag):
            dpg.delete_item(popup_tag)

        s = display_scaling.scale
        mpos = list(pos) if pos else list(dpg.get_mouse_pos(local=False))

        with dpg.window(
            tag=popup_tag,
            popup=True,
            show=True,
            no_title_bar=True,
            no_move=True,
            no_resize=True,
            pos=mpos,
            width=s(260),
        ):
            dpg.add_text("Add Window to View:", color=(150, 220, 255))
            dpg.add_separator()

            if not available:
                dpg.add_text("(All pipeline windows are already in this view)", color=(160, 160, 160))
            else:
                for win in available:
                    uuid = str(win.get("uuid", ""))
                    label = win.get("params", {}).get("label") or win.get("label") or win.get("class_name", f"UUID:{uuid}")
                    dpg.add_menu_item(
                        label=f"[{uuid}] {label}",
                        user_data=win,
                        callback=lambda s, a, u: (
                            dpg.delete_item(popup_tag) if dpg.does_item_exist(popup_tag) else None,
                            self._add_window_to_view(u),
                        ),
                    )

    def _get_available_windows_for_view(self) -> List[Dict[str, Any]]:
        """Return all pipeline windows that are NOT currently in the selected view (guaranteeing no duplicate presence)."""
        if not self.pipeline_data or not isinstance(self.pipeline_data, dict):
            return []
        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return []

        vdata = views[self.selected_view_name]
        current_view_uuids = {str(w.get("uuid", "")) for w in vdata.get("windows", []) if isinstance(w, dict)}

        available = []
        for win in self.pipeline_data.get("windows", []):
            if isinstance(win, dict):
                uuid = str(win.get("uuid", ""))
                if uuid and uuid not in current_view_uuids:
                    available.append(win)
        return available

    def _add_window_to_view(self, win_dict: Optional[Dict[str, Any]]) -> None:
        """Add a window from the pipeline into the current view layout, checking strictly against duplicates."""
        if not win_dict or not isinstance(win_dict, dict):
            return
        if not self.pipeline_data or not isinstance(self.pipeline_data, dict):
            return
        if not self.selected_view_name:
            return

        views = self.pipeline_data.setdefault("views", {})
        if self.selected_view_name not in views:
            views[self.selected_view_name] = {"is_relative": True, "windows": []}

        vdata = views[self.selected_view_name]
        uuid_str = str(win_dict.get("uuid", ""))
        if not uuid_str:
            return

        existing_uuids = {str(w.get("uuid", "")) for w in vdata.get("windows", []) if isinstance(w, dict)}
        if uuid_str in existing_uuids:
            logger.warning(f"Window [{uuid_str}] is already present in view '{self.selected_view_name}'.")
            return

        label = win_dict.get("params", {}).get("label") or win_dict.get("label") or win_dict.get("class_name", f"UUID:{uuid_str}")
        pos = list(win_dict.get("pos", [20.0, 20.0]))
        size = list(win_dict.get("size", [30.0, 30.0]))

        new_entry = {
            "uuid": uuid_str,
            "label": label,
            "pos": pos,
            "size": size,
        }
        vdata.setdefault("windows", []).append(new_entry)
        self.selected_view_win_idx = len(vdata["windows"]) - 1
        logger.info(f"Added window [{uuid_str}] to view '{self.selected_view_name}'.")
        self._populate_view_details()

    def _remove_window_from_view(self, idx: int) -> None:
        """Remove a window from the current view layout."""
        if not self.pipeline_data or not isinstance(self.pipeline_data, dict):
            return
        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return

        windows = views[self.selected_view_name].get("windows", [])
        if 0 <= idx < len(windows):
            removed = windows.pop(idx)
            self.selected_view_win_idx = None
            logger.info(f"Removed window [{removed.get('uuid')}] from view '{self.selected_view_name}'.")
            self._populate_view_details()

    def _bring_window_to_front(self, idx: int) -> None:
        """Bring the specified window to the front of the view."""
        if not self.pipeline_data or not isinstance(self.pipeline_data, dict):
            return
        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return

        windows = views[self.selected_view_name].get("windows", [])
        if 0 <= idx < len(windows):
            item = windows.pop(idx)
            windows.append(item)
            self.selected_view_win_idx = len(windows) - 1
            self._populate_view_details()

    def _reset_window_in_view(self, idx: int) -> None:
        """Center and reset the position and size of a window in the view."""
        if not self.pipeline_data or not isinstance(self.pipeline_data, dict):
            return
        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return

        windows = views[self.selected_view_name].get("windows", [])
        if 0 <= idx < len(windows):
            win = windows[idx]
            is_rel = views[self.selected_view_name].get("is_relative", self.pipeline_data.get("is_relative", True))
            win["pos"] = [30.0, 30.0]
            win["size"] = [40.0, 40.0]
            self._sync_real_window_geometry(win, is_rel)
            self._draw_view_canvas()

    def _on_select_view_window_row(self, idx: int) -> None:
        """Select a window from the table list and highlight it on the canvas."""
        self.selected_view_win_idx = idx
        self._draw_view_canvas()

    def _on_capture_workspace_for_view(self, *args, **kwargs) -> None:
        """Overwrite the selected view with live window positions from the running workspace."""
        if not self.pipeline_data or not isinstance(self.pipeline_data, dict):
            return
        views = self.pipeline_data.get("views", {})
        if not self.selected_view_name or self.selected_view_name not in views:
            return

        from core.module_registry import export_view

        is_rel = views[self.selected_view_name].get("is_relative", True)
        view_data = export_view(is_relative=is_rel)
        views[self.selected_view_name] = view_data
        self.selected_view_win_idx = None
        logger.success(f"Captured live workspace into view '{self.selected_view_name}'.")
        self._populate_view_details()

    def _on_select_view(self, view_name: str) -> None:
        """Select a view."""
        self.selected_view_name = view_name
        self.selected_view_win_idx = None
        self._populate_views_list()
        self._populate_view_details()

    def _on_new_view_dialog(self, *args, **kwargs) -> None:
        """Open a dialog to add a new view."""
        popup_id = f"{self.winID}_new_view_popup"
        if dpg.does_item_exist(popup_id):
            dpg.delete_item(popup_id)

        s = display_scaling.scale

        def _do_create(sender=None, app_data=None, user_data=None, *a, **kw):
            vname = dpg.get_value(f"{popup_id}_name").strip()
            if not vname:
                return

            use_current_layout = dpg.get_value(f"{popup_id}_current_layout")
            if use_current_layout:
                from core.module_registry import export_view

                view_data = export_view(is_relative=self.pipeline_data.get("is_relative", True))
            else:
                view_data = {"is_relative": self.pipeline_data.get("is_relative", True), "windows": []}

            self.pipeline_data.setdefault("views", {})[vname] = view_data
            self.selected_view_name = vname
            self.selected_view_win_idx = None
            dpg.delete_item(popup_id)
            self._populate_views_list()
            self._populate_view_details()

        with dpg.window(label="Create View", modal=True, show=True, tag=popup_id, width=s(380), height=s(190)):
            dpg.add_text("View Name:")
            dpg.add_input_text(tag=f"{popup_id}_name", hint="e.g. Video, Figures, Minimal", width=-1)
            dpg.add_spacer(height=s(5))
            dpg.add_checkbox(label="Capture current window positions", tag=f"{popup_id}_current_layout", default_value=True)
            dpg.add_spacer(height=s(10))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Create", callback=_do_create, width=s(110))
                dpg.add_button(label="Cancel", callback=lambda *a: dpg.delete_item(popup_id), width=s(110))

    def _on_delete_view(self, *args, **kwargs) -> None:
        """Delete the currently selected view."""
        views = self.pipeline_data.get("views", {})
        if self.selected_view_name and self.selected_view_name in views:
            del views[self.selected_view_name]
            self.selected_view_name = list(views.keys())[0] if views else None
            self.selected_view_win_idx = None
            self._populate_views_list()
            self._populate_view_details()

    def _build_json_tab(self) -> None:
        """Raw JSON editor tab with realtime sync and validation."""
        s = display_scaling.scale
        container_tag = f"{self.winID}_json_container"
        if dpg.does_item_exist(container_tag):
            dpg.delete_item(container_tag)

        with dpg.child_window(tag=container_tag, height=-s(50), border=False, parent=f"{self.winID}_tab_json"):
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Sync from Visual Tabs",
                    callback=lambda *a: self._on_sync_json_from_data(silent=False),
                    width=s(210),
                )
                with dpg.tooltip(parent=dpg.last_item()):
                    dpg.add_text("Regenerate this JSON text from the visual tabs (Overview, Modules, Connections, Views).")

                dpg.add_button(
                    label="Apply JSON to Visual Tabs",
                    callback=self._on_apply_json_to_data,
                    width=s(220),
                )
                with dpg.tooltip(parent=dpg.last_item()):
                    dpg.add_text("Parse this JSON text and update all visual tabs and internal pipeline data.")

                dpg.add_text(
                    self.json_status,
                    tag=f"{self.winID}_json_status",
                    color=self.json_status_color,
                )

            dpg.add_spacer(height=s(4))
            raw_text = json.dumps(self.pipeline_data, indent=4)
            dpg.add_input_text(
                tag=f"{self.winID}_raw_json_input",
                default_value=raw_text,
                multiline=True,
                width=-1,
                height=-1,
            )

    def _on_sync_json_from_data(self, *args, silent: bool = False, **kwargs) -> None:
        """Format the in-memory dictionary to the Raw JSON text input."""
        json_input = f"{self.winID}_raw_json_input"
        status_tag = f"{self.winID}_json_status"
        if dpg.does_item_exist(json_input):
            dpg.set_value(json_input, json.dumps(self.pipeline_data, indent=4))
        if not silent:
            self.json_status = "Synced from Visual Tabs."
            self.json_status_color = (180, 255, 180)
            if dpg.does_item_exist(status_tag):
                dpg.set_value(status_tag, self.json_status)
                dpg.configure_item(status_tag, color=self.json_status_color)

    def _on_apply_json_to_data(self, *args, **kwargs) -> None:
        """Parse the Raw JSON text input and update the in-memory data."""
        json_input = f"{self.winID}_raw_json_input"
        status_tag = f"{self.winID}_json_status"
        if not dpg.does_item_exist(json_input):
            return

        text = dpg.get_value(json_input)
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("JSON root must be an object/dict.")

            # Sanitize and ensure core structure
            self.pipeline_data = {
                "is_relative": parsed.get("is_relative", True),
                "windows": parsed.get("windows", []),
                "connections": parsed.get("connections", []),
                "views": parsed.get("views", {}),
                "link_nodes": parsed.get("link_nodes", []),
            }

            # Sanitize selected indices
            windows = self.pipeline_data.get("windows", [])
            if windows:
                if self.selected_module_idx < 0 or self.selected_module_idx >= len(windows):
                    self.selected_module_idx = 0
            else:
                self.selected_module_idx = -1

            conns = self.pipeline_data.get("connections", [])
            if self.selected_connection_idx is not None and (
                self.selected_connection_idx < 0 or self.selected_connection_idx >= len(conns)
            ):
                self.selected_connection_idx = None

            views = self.pipeline_data.get("views", {})
            if self.selected_view_name not in views:
                self.selected_view_name = list(views.keys())[0] if views else None

            self.json_status = "JSON successfully applied to Visual Tabs!"
            self.json_status_color = (120, 255, 120)
            logger.success("Raw JSON successfully applied to Pipeline Editor visual tabs.")

            # Rebuild UI while preserving the Raw JSON tab active
            self._build_ui(active_tab=f"{self.winID}_tab_json")

        except Exception as e:
            self.json_status = f"Error: {e}"
            self.json_status_color = (255, 120, 120)
            if dpg.does_item_exist(status_tag):
                dpg.set_value(status_tag, self.json_status)
                dpg.configure_item(status_tag, color=self.json_status_color)
            logger.error(f"Invalid JSON in editor: {e}")

    def _on_import_from_workspace_clicked(self, *args, **kwargs) -> None:
        """Import the current live workspace layout, modules, and connections from the running application."""
        self._import_from_live_workspace()
        self._build_ui(active_tab=self.active_tab)

    # ------------------------------------------------------------------
    # Actions & Callbacks
    # ------------------------------------------------------------------

    def _get_uuid_to_label_map(self) -> Dict[str, str]:
        """Return a mapping from UUID string to user-friendly module label."""
        res = {}
        for win in self.pipeline_data.get("windows", []):
            uuid = str(win.get("uuid", ""))
            label = win.get("params", {}).get("label") or win.get("label") or win.get("class_name", f"UUID:{uuid}")
            res[uuid] = str(label)
        return res

    def _on_select_pipeline_combo(self, selected_stem: str, file_map: Dict[str, str]) -> None:
        """Handle selecting a pipeline from the top combo dropdown."""
        if selected_stem in file_map:
            self.load_file(file_map[selected_stem])
            self._build_ui()

    def _on_browse_open(self, *args, **kwargs) -> None:
        """Open a file explorer dialog to select any pipeline JSON file."""
        chosen = file_explorer.select_file(
            default_path=str(LAYOUTS_DIR),
            extensions=[("JSON files", "*.json")],
        )
        if chosen:
            self.load_file(chosen)
            self._build_ui()

    def _on_new_pipeline(self, *args, **kwargs) -> None:
        """Create a new empty pipeline in the manager."""
        self._init_empty_pipeline()
        self._build_ui()

    def _on_save_clicked(self, *args, **kwargs) -> None:
        """Save the pipeline file."""
        self.save_file()
        self._build_ui()

    def _on_save_as_clicked(self, *args, **kwargs) -> None:
        """Save as a new pipeline file."""
        chosen = file_explorer.save_file(
            default_path=str(LAYOUTS_DIR),
            default_name="new_pipeline.json",
            extensions=[("JSON files", "*.json")],
        )
        if chosen:
            self.save_file(chosen)
            self._build_ui()

    def _on_reload_clicked(self, *args, **kwargs) -> None:
        """Reload the currently active file from disk."""
        if self.current_filepath and Path(self.current_filepath).exists():
            self.load_file(self.current_filepath)
            self._build_ui()

    def _on_load_into_workspace_clicked(self, *args, **kwargs) -> None:
        """Load the edited pipeline directly into the active application workspace."""
        try:
            from core.main_win import main_win

            # If there's a saved file path, load from file
            if self.current_filepath and Path(self.current_filepath).exists():
                self.save_file(self.current_filepath)
                main_win.load_workspace_from_path(self.current_filepath)
            else:
                # Load from in-memory dictionary
                from core.module_registry import load_from_dict

                main_win.node_editor.delete_all_nodes()
                new_instances = load_from_dict(self.pipeline_data, start_cleaned=True)
                main_win.node_editor.rebuild_from_instances(new_instances)
                logger.success("Pipeline loaded into workspace from Pipeline Editor memory.")
        except Exception as e:
            logger.error(f"Failed to load pipeline into workspace: {e}")


# Global singleton instance
pipeline_editor: PipelineEditor = PipelineEditor()
