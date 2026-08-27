"""
Automation Editor module for Pipeline Creator.

Provides an interactive GUI tool to create, inspect, edit, and run automation scripts (.json).
Users can visually configure sequential automation steps (fullscreen, workspace dir, load_pipeline,
send_command, trigger, apply_view, wait) with real-time validation, layout module inspection,
and direct testing.
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
from core.paths import LAYOUTS_DIR, PROJECT_ROOT, SCRIPTS_DIR
from core.automation_manager import automation_manager


AVAILABLE_ACTIONS: List[Tuple[str, str, str]] = [
    ("fullscreen", "Fullscreen", "Toggle application fullscreen mode"),
    ("set_workspace", "Set Workspace", "Set the working directory (CWD)"),
    ("load_pipeline", "Load Pipeline", "Load and reconstruct a pipeline layout (.json)"),
    ("apply_view", "Apply View", "Apply a named view layout preset"),
    ("send_command", "Send Command", "Send a CMD_DICT message to a specific module"),
    ("trigger", "Trigger Method", "Call a specific method on a module"),
    ("wait", "Wait / Delay", "Pause execution for specified duration (seconds)"),
]

class AutomationEditor:
    """
    Interactive GUI editor and manager for automation scripts.
    """

    def __init__(self) -> None:
        self.winID: str = "automation_editor_win"
        self.current_filepath: Optional[str] = None
        self.script_data: Dict[str, Any] = {
            "name": "New Automation",
            "steps": [],
        }
        self.selected_step_idx: int = -1
        self.active_tab: str = f"{self.winID}_tab_steps"
        self.json_status: str = ""
        self.json_status_color: Tuple[int, int, int] = (180, 255, 180)

    def show(self, filepath: Optional[str] = None) -> None:
        """Open and display the Automation Editor window."""
        # 1. Target file passed
        if filepath and Path(filepath).exists():
            self.load_file(filepath)
        # 2. Currently active script in automation manager
        elif automation_manager.script_path and Path(automation_manager.script_path).exists():
            self.load_file(str(automation_manager.script_path))
        # 3. First script in scripts/ directory
        elif SCRIPTS_DIR.exists() and list(SCRIPTS_DIR.glob("*.json")):
            scripts = sorted(list(SCRIPTS_DIR.glob("*.json")), key=lambda p: p.stem.lower())
            self.load_file(str(scripts[0].resolve()))
        # 4. Clean template
        else:
            self._init_empty_script()

        self._build_ui()

    def _init_empty_script(self) -> None:
        """Initialize with a clean, default automation script template."""
        self.current_filepath = None
        self.script_data = {
            "name": "Untitled Automation",
            "steps": [],
        }
        self.selected_step_idx = 0

    def load_file(self, filepath: str) -> bool:
        """Load and parse an automation script JSON file."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.error(f"Invalid JSON in {filepath}: root must be an object.")
                return False

            self.script_data = {
                "name": data.get("name", Path(filepath).stem),
                "steps": data.get("steps", []),
            }
            self.current_filepath = str(Path(filepath).resolve())
            self.selected_step_idx = 0 if self.script_data["steps"] else -1
            self.json_status = ""

            logger.info(f"Automation Editor loaded '{Path(filepath).name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to load automation script '{filepath}': {e}")
            return False

    def save_file(self, filepath: Optional[str] = None) -> bool:
        """Save current automation script to JSON."""
        target = filepath or self.current_filepath
        if not target:
            target = file_explorer.save_file(
                default_path=str(SCRIPTS_DIR),
                default_name="automation_script.json",
                extensions=[("JSON files", "*.json")],
            )
            if not target:
                return False

        if not target.lower().endswith(".json"):
            target += ".json"

        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self.script_data, f, indent=4)
            self.current_filepath = str(Path(target).resolve())
            logger.success(f"Automation script saved to '{target}'")
            return True
        except Exception as e:
            logger.error(f"Failed to save automation script to '{target}': {e}")
            return False

    def _get_pipeline_context(self) -> Dict[str, Any]:
        """Find the most relevant layout loaded in this script and extract its modules and views."""
        layout_path_str = None
        steps = self.script_data.get("steps", [])

        # 1. Search for any load_pipeline step in the script
        for step in steps:
            if step.get("action") == "load_pipeline" and step.get("path"):
                layout_path_str = step.get("path")
                break

        # 2. Fallback to LAST_LOADED_WORKSPACE if no load_pipeline step is present
        if not layout_path_str:
            try:
                from core.module_registry import LAST_LOADED_WORKSPACE
                if LAST_LOADED_WORKSPACE:
                    layout_path_str = str(LAST_LOADED_WORKSPACE)
            except Exception:
                pass

        if not layout_path_str:
            return {"layout_name": None, "modules": [], "views": []}

        # Resolve layout path
        path_clean = str(layout_path_str).lstrip("/\\")
        filename = Path(layout_path_str).name
        candidates = [
            LAYOUTS_DIR / filename,
            LAYOUTS_DIR / path_clean,
            PROJECT_ROOT / path_clean,
            Path(layout_path_str),
        ]

        resolved = None
        for c in candidates:
            if c.exists() and c.is_file():
                resolved = c.resolve()
                break

        if not resolved:
            return {"layout_name": filename, "modules": [], "views": []}

        try:
            with open(resolved, "r", encoding="utf-8") as f:
                data = json.load(f)

            modules = []
            for win in data.get("windows", []):
                if isinstance(win, dict):
                    uuid = str(win.get("uuid", ""))
                    label = win.get("params", {}).get("label") or win.get("label") or win.get("class_name", f"UUID:{uuid}")
                    mod_path = win.get("module", "")
                    cls_name = win.get("class_name", "")
                    modules.append({
                        "uuid": uuid,
                        "label": label,
                        "module": mod_path,
                        "class_name": cls_name,
                    })

            views = list(data.get("views", {}).keys()) if isinstance(data.get("views"), dict) else []
            return {
                "layout_name": resolved.name,
                "layout_path": str(resolved),
                "modules": modules,
                "views": views,
            }
        except Exception as e:
            logger.debug(f"Could not parse layout for automation context: {e}")
            return {"layout_name": filename, "modules": [], "views": []}

    def _on_pick_module_for_step(self, step: Dict[str, Any], chosen_item: str, mods: List[Dict[str, Any]]) -> None:
        """Set module_uuid and module_type from layout picker."""
        for m in mods:
            item_label = f"[{m['uuid']}] {m['label']} ({m['class_name'] or m['module']})"
            if item_label == chosen_item:
                step["module_uuid"] = m["uuid"]
                if m.get("class_name"):
                    step["module_type"] = m["class_name"]
                elif m.get("module"):
                    step["module_type"] = m["module"].split(".")[-1]
                break
        self._populate_steps_list()
        self._populate_step_details()

    def _build_ui(self, active_tab: Optional[str] = None) -> None:
        """Create or recreate the Automation Editor window."""
        if active_tab:
            self.active_tab = active_tab

        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

        s = display_scaling.scale
        win_w = s(920)
        win_h = s(680)

        vp_w = dpg.get_viewport_client_width() or 1200
        vp_h = dpg.get_viewport_client_height() or 800
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        with dpg.window(
            label="Automation Editor",
            tag=self.winID,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
            no_resize=False,
            no_move=False,
        ):
            # Top Selection Bar
            self._build_top_bar()
            dpg.add_separator()
            dpg.add_spacer(height=s(4))

            # Main Tabs Container (leaves room for the bottom action bar so it never gets pushed off-screen)
            with dpg.child_window(tag=f"{self.winID}_main_content", width=-1, height=-s(52), border=False):
                with dpg.tab_bar(tag=f"{self.winID}_tabs", callback=self._on_tab_changed):
                    with dpg.tab(label="Steps Flow", tag=f"{self.winID}_tab_steps"):
                        self._build_steps_tab()

                    with dpg.tab(label="Raw JSON", tag=f"{self.winID}_tab_json"):
                        self._build_json_tab()

            # Bottom Bar (Pinned at bottom of window)
            dpg.add_separator()
            dpg.add_spacer(height=s(4))
            self._build_bottom_bar()

        if self.active_tab and dpg.does_item_exist(self.active_tab):
            dpg.set_value(f"{self.winID}_tabs", self.active_tab)

    def _on_tab_changed(self, sender: Any, app_data: Any, user_data: Any) -> None:
        self.active_tab = app_data
        if app_data == f"{self.winID}_tab_json":
            self._sync_to_raw_json()
        elif app_data == f"{self.winID}_tab_steps":
            self._populate_steps_list()
            self._populate_step_details()

    def _build_top_bar(self) -> None:
        """Top selector bar for choosing, creating, or running automation scripts."""
        s = display_scaling.scale
        script_files = (
            sorted([f for f in SCRIPTS_DIR.glob("*.json") if f.is_file()], key=lambda p: p.stem.lower())
            if SCRIPTS_DIR.exists()
            else []
        )
        file_map = {f.stem: str(f.resolve()) for f in script_files}
        if self.current_filepath and Path(self.current_filepath).exists():
            p = Path(self.current_filepath)
            file_map[p.stem] = str(p.resolve())

        combo_items = list(file_map.keys())
        curr_stem = Path(self.current_filepath).stem if self.current_filepath else (combo_items[0] if combo_items else "Untitled")
        if curr_stem not in combo_items and curr_stem != "Untitled":
            combo_items.insert(0, curr_stem)

        with dpg.group(horizontal=True):
            dpg.add_text("Script:")
            if combo_items:
                dpg.add_combo(
                    items=combo_items,
                    default_value=curr_stem,
                    width=s(250),
                    callback=lambda s, a, u, *args: self._on_select_script_combo(a, file_map),
                )
            dpg.add_button(label="Open", callback=self._on_browse_open, width=s(70))
            dpg.add_button(label="New", callback=self._on_new_script, width=s(70))

            dpg.add_spacer(width=s(10))
            dpg.add_text("Name:")
            dpg.add_input_text(
                default_value=str(self.script_data.get("name", "")),
                width=s(200),
                callback=lambda s, a, u, *args: self.script_data.__setitem__("name", a.strip()),
            )

            dpg.add_spacer(width=s(10))
            path_text = f"File: {Path(self.current_filepath).name}" if self.current_filepath else "File: [Unsaved]"
            dpg.add_text(path_text, color=(160, 180, 200))

    def _build_bottom_bar(self) -> None:
        """Bottom action buttons."""
        s = display_scaling.scale
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save", callback=self._on_save_clicked, width=s(85))
            dpg.add_button(label="Save As", callback=self._on_save_as_clicked, width=s(95))
            dpg.add_button(label="Reload", callback=self._on_reload_clicked, width=s(85))
            dpg.add_spacer(width=s(20))
            dpg.add_button(
                label="\uf04b Run Automation Script",
                callback=self._on_run_script_clicked,
                width=s(200),
            )
            with dpg.tooltip(parent=dpg.last_item()):
                dpg.add_text("Execute all steps of this automation script immediately.")

    def _build_steps_tab(self) -> None:
        """Visual sequence of automation steps on the left, step detail inspector on the right."""
        s = display_scaling.scale
        container_tag = f"{self.winID}_steps_container"
        if dpg.does_item_exist(container_tag):
            dpg.delete_item(container_tag)

        with dpg.child_window(tag=container_tag, width=-1, height=-1, border=False, parent=f"{self.winID}_tab_steps"):
            with dpg.group(horizontal=True):
                # Left Pane: Step Sequence List
                with dpg.child_window(width=s(320), height=-1, border=True):
                    dpg.add_text("Execution Steps Sequence", color=(200, 200, 220))
                    dpg.add_separator()
                    dpg.add_spacer(height=s(2))

                    with dpg.child_window(tag=f"{self.winID}_step_list_child", height=-s(45), border=False):
                        self._populate_steps_list()

                    with dpg.group(horizontal=True):
                        dpg.add_button(label="\uf067", callback=self._on_add_step_dialog, width=s(38))
                        with dpg.tooltip(parent=dpg.last_item()):
                            dpg.add_text("Add a new step")

                        dpg.add_button(label="\uf077", callback=self._on_move_step_up, width=s(38))
                        with dpg.tooltip(parent=dpg.last_item()):
                            dpg.add_text("Move selected step up")

                        dpg.add_button(label="\uf078", callback=self._on_move_step_down, width=s(38))
                        with dpg.tooltip(parent=dpg.last_item()):
                            dpg.add_text("Move selected step down")

                        dpg.add_button(label="\uf0c5", callback=self._on_duplicate_step, width=s(38))
                        with dpg.tooltip(parent=dpg.last_item()):
                            dpg.add_text("Duplicate selected step")

                        dpg.add_button(label="\uf00d", callback=self._on_delete_step, width=s(38))
                        with dpg.tooltip(parent=dpg.last_item()):
                            dpg.add_text("Delete selected step")

                # Right Pane: Step Inspector & Properties Editor
                with dpg.child_window(tag=f"{self.winID}_step_details_pane", width=-1, height=-1, border=True):
                    self._populate_step_details()

    def _get_step_summary(self, idx: int, step: Dict[str, Any]) -> str:
        """Produce a descriptive single-line label for a step in the sequence list."""
        action = step.get("action", "unknown")
        if action == "fullscreen":
            state = "Enabled" if step.get("enabled", True) else "Disabled"
            return f"[{idx+1}] Fullscreen ({state})"
        elif action == "set_workspace":
            p = Path(step.get("path", "")).name or step.get("path", "N/A")
            return f"[{idx+1}] Set Workspace: {p}"
        elif action == "load_pipeline":
            p = Path(step.get("path", "")).name or step.get("path", "N/A")
            return f"[{idx+1}] Load Pipeline: {p}"
        elif action in ("apply_view", "launch_view"):
            v = step.get("view_name", "N/A")
            return f"[{idx+1}] Apply View: {v}"
        elif action == "send_command":
            uid = step.get("module_uuid", "N/A")
            cmd = step.get("command", {})
            keys = ", ".join(cmd.keys()) if isinstance(cmd, dict) else str(cmd)
            return f"[{idx+1}] Send Cmd (UUID {uid}): {keys}"
        elif action == "trigger":
            tgt = step.get("module_uuid") or step.get("module_type", "N/A")
            meth = step.get("method", "call")
            return f"[{idx+1}] Trigger {tgt}.{meth}()"
        elif action == "wait":
            sec = step.get("seconds", 1.0)
            return f"[{idx+1}] Wait {sec}s"
        else:
            return f"[{idx+1}] {action}"

    def _populate_steps_list(self) -> None:
        """Render selectable step items in the left sequence list."""
        list_child = f"{self.winID}_step_list_child"
        if not dpg.does_item_exist(list_child):
            return

        children = dpg.get_item_children(list_child, 1) or []
        for child in children:
            dpg.delete_item(child)

        steps = self.script_data.get("steps", [])
        if not steps:
            dpg.add_text("No steps in this script.\nClick '+ Add' to create one.", parent=list_child, color=(160, 160, 160))
            return

        for idx, step in enumerate(steps):
            display_label = self._get_step_summary(idx, step)
            is_selected = idx == self.selected_step_idx
            dpg.add_selectable(
                label=display_label,
                default_value=is_selected,
                parent=list_child,
                callback=lambda s, a, u, *args: self._on_select_step(u),
                user_data=idx,
            )

    def _populate_step_details(self) -> None:
        """Render dynamic step inspector based on selected action type."""
        pane = f"{self.winID}_step_details_pane"
        if not dpg.does_item_exist(pane):
            return

        children = dpg.get_item_children(pane, 1) or []
        for child in children:
            dpg.delete_item(child)

        steps = self.script_data.get("steps", [])
        if self.selected_step_idx < 0 or self.selected_step_idx >= len(steps):
            dpg.add_text("Select a step from the list on the left to inspect and edit its parameters.", parent=pane, color=(160, 160, 160))
            return

        step = steps[self.selected_step_idx]
        s = display_scaling.scale
        action = step.get("action", "fullscreen")
        ctx = self._get_pipeline_context()
        layout_name = ctx.get("layout_name")
        mods = ctx.get("modules", [])
        views_list = ctx.get("views", [])

        with dpg.group(parent=pane):
            with dpg.group(horizontal=True):
                dpg.add_text(f"Editing Step #{self.selected_step_idx + 1}: {action}", color=(255, 205, 90))
                if layout_name:
                    dpg.add_spacer(width=s(10))
                    dpg.add_text(f"(Layout: {layout_name} - {len(mods)} modules, {len(views_list)} views)", color=(140, 190, 240))

            dpg.add_separator()
            dpg.add_spacer(height=s(4))

            # Action Selector
            with dpg.group(horizontal=True):
                dpg.add_text("Action Type:")
                action_keys = [a[0] for a in AVAILABLE_ACTIONS]
                dpg.add_combo(
                    items=action_keys,
                    default_value=action,
                    width=s(180),
                    callback=lambda s, a, u, *args: self._on_step_action_type_changed(a),
                )

            dpg.add_spacer(height=s(8))
            dpg.add_text("Action Parameters:", color=(180, 200, 220))
            dpg.add_separator()
            dpg.add_spacer(height=s(4))

            if action == "fullscreen":
                dpg.add_checkbox(
                    label="Maximize Viewport (Fullscreen)",
                    default_value=step.get("enabled", True),
                    callback=lambda s, a, u, *args: (step.__setitem__("enabled", bool(a)), self._populate_steps_list()),
                )

            elif action == "set_workspace":
                with dpg.group(horizontal=True):
                    dpg.add_text("Directory Path:")
                    dpg.add_input_text(
                        default_value=str(step.get("path", "")),
                        width=s(320),
                        callback=lambda s, a, u, *args: (step.__setitem__("path", a.strip()), self._populate_steps_list()),
                    )
                    dpg.add_button(
                        label="Browse...",
                        callback=lambda: self._on_browse_workspace_path(step),
                        width=s(90),
                    )

            elif action == "load_pipeline":
                layout_files = (
                    sorted([f.name for f in LAYOUTS_DIR.glob("*.json") if f.is_file()], key=str.lower)
                    if LAYOUTS_DIR.exists()
                    else []
                )
                curr_path = str(step.get("path", ""))
                curr_layout_name = Path(curr_path).name

                with dpg.group(horizontal=True):
                    dpg.add_text("Select Layout:")
                    if layout_files:
                        dpg.add_combo(
                            items=layout_files,
                            default_value=curr_layout_name if curr_layout_name in layout_files else (layout_files[0] if layout_files else ""),
                            width=s(240),
                            callback=lambda s, a, u, *args: (step.__setitem__("path", f"/layouts/{a}"), self._populate_steps_list(), self._populate_step_details()),
                        )
                    dpg.add_button(
                        label="Browse",
                        callback=lambda: self._on_browse_pipeline_path(step),
                        width=s(100),
                    )

                with dpg.group(horizontal=True):
                    dpg.add_text("Resolved Path:")
                    dpg.add_input_text(
                        default_value=curr_path,
                        width=-1,
                        callback=lambda s, a, u, *args: (step.__setitem__("path", a.strip()), self._populate_steps_list()),
                    )

            elif action in ("apply_view", "launch_view"):
                curr_view = str(step.get("view_name", ""))
                if views_list:
                    with dpg.group(horizontal=True):
                        dpg.add_text("Pick Layout View:")
                        dpg.add_combo(
                            items=views_list,
                            default_value=curr_view if curr_view in views_list else (views_list[0] if views_list else ""),
                            width=s(220),
                            callback=lambda s, a, u, *args: (step.__setitem__("view_name", a), self._populate_steps_list(), self._populate_step_details()),
                        )

                with dpg.group(horizontal=True):
                    dpg.add_text("View Name:")
                    dpg.add_input_text(
                        default_value=curr_view,
                        width=s(240),
                        callback=lambda s, a, u, *args: (step.__setitem__("view_name", a.strip()), self._populate_steps_list()),
                    )

            elif action == "send_command":
                curr_uuid = str(step.get("module_uuid", ""))
                if mods:
                    mod_items = [f"[{m['uuid']}] {m['label']} ({m['class_name'] or m['module']})" for m in mods]
                    curr_mod_display = next((f"[{m['uuid']}] {m['label']} ({m['class_name'] or m['module']})" for m in mods if m['uuid'] == curr_uuid), "")

                    with dpg.group(horizontal=True):
                        dpg.add_text("Pick Module from Layout:")
                        dpg.add_combo(
                            items=mod_items,
                            default_value=curr_mod_display,
                            width=s(320),
                            callback=lambda s, a, u, *args: self._on_pick_module_for_step(step, a, mods),
                        )

                with dpg.group(horizontal=True):
                    dpg.add_text("Target Module UUID:")
                    dpg.add_input_text(
                        default_value=curr_uuid,
                        width=s(140),
                        callback=lambda s, a, u, *args: (step.__setitem__("module_uuid", a.strip()), self._populate_steps_list()),
                    )
                    dpg.add_text("Module Type (Optional):")
                    dpg.add_input_text(
                        default_value=str(step.get("module_type", "")),
                        width=s(180),
                        callback=lambda s, a, u, *args: step.__setitem__("module_type", a.strip()),
                    )

                dpg.add_spacer(height=s(4))
                dpg.add_text("Command Dictionary (CMD_DICT Payload):", color=(180, 200, 220))

                cmd_obj = step.get("command", {})
                cmd_json_str = json.dumps(cmd_obj, indent=2) if isinstance(cmd_obj, (dict, list)) else str(cmd_obj)

                with dpg.group(horizontal=True):
                    dpg.add_text("Presets:")
                    dpg.add_button(label="init: true", callback=lambda: self._set_command_preset(step, {"init": True}), width=s(90))
                    dpg.add_button(label="connect: true", callback=lambda: self._set_command_preset(step, {"connect": True}), width=s(105))
                    dpg.add_button(label="enabled: true", callback=lambda: self._set_command_preset(step, {"enabled": True}), width=s(105))
                    dpg.add_button(label="enabled: false", callback=lambda: self._set_command_preset(step, {"enabled": False}), width=s(105))

                dpg.add_spacer(height=s(2))
                dpg.add_input_text(
                    default_value=cmd_json_str,
                    multiline=True,
                    height=s(140),
                    width=-1,
                    callback=lambda s, a, u, *args: self._on_command_json_edited(step, a),
                )

            elif action == "trigger":
                curr_uuid = str(step.get("module_uuid", ""))
                if mods:
                    mod_items = [f"[{m['uuid']}] {m['label']} ({m['class_name'] or m['module']})" for m in mods]
                    curr_mod_display = next((f"[{m['uuid']}] {m['label']} ({m['class_name'] or m['module']})" for m in mods if m['uuid'] == curr_uuid), "")

                    with dpg.group(horizontal=True):
                        dpg.add_text("Pick Module from Layout:")
                        dpg.add_combo(
                            items=mod_items,
                            default_value=curr_mod_display,
                            width=s(320),
                            callback=lambda s, a, u, *args: self._on_pick_module_for_step(step, a, mods),
                        )

                with dpg.group(horizontal=True):
                    dpg.add_text("Module UUID:")
                    dpg.add_input_text(
                        default_value=curr_uuid,
                        width=s(140),
                        callback=lambda s, a, u, *args: (step.__setitem__("module_uuid", a.strip()), self._populate_steps_list()),
                    )
                    dpg.add_text("Module Type:")
                    dpg.add_input_text(
                        default_value=str(step.get("module_type", "")),
                        width=s(180),
                        callback=lambda s, a, u, *args: step.__setitem__("module_type", a.strip()),
                    )

                with dpg.group(horizontal=True):
                    dpg.add_text("Method Name:")
                    dpg.add_input_text(
                        default_value=str(step.get("method", "")),
                        width=s(200),
                        callback=lambda s, a, u, *args: (step.__setitem__("method", a.strip()), self._populate_steps_list()),
                    )

                dpg.add_spacer(height=s(4))
                dpg.add_text("Method Parameters (JSON Dict):")
                params_obj = step.get("params", {})
                params_str = json.dumps(params_obj, indent=2) if isinstance(params_obj, dict) else str(params_obj)
                dpg.add_input_text(
                    default_value=params_str,
                    multiline=True,
                    height=s(100),
                    width=-1,
                    callback=lambda s, a, u, *args: self._on_params_json_edited(step, a),
                )

            elif action == "wait":
                with dpg.group(horizontal=True):
                    dpg.add_text("Duration (seconds):")
                    dpg.add_drag_float(
                        default_value=float(step.get("seconds", 1.0)),
                        width=s(120),
                        min_value=0.05,
                        max_value=300.0,
                        speed=0.1,
                        format="%.2f s",
                        callback=lambda s, a, u, *args: (step.__setitem__("seconds", round(float(a), 2)), self._populate_steps_list()),
                    )

    def _on_select_step(self, idx: int) -> None:
        self.selected_step_idx = idx
        self._populate_steps_list()
        self._populate_step_details()

    def _on_step_action_type_changed(self, new_action: str) -> None:
        steps = self.script_data.get("steps", [])
        if 0 <= self.selected_step_idx < len(steps):
            step = steps[self.selected_step_idx]
            step["action"] = new_action
            if new_action == "fullscreen" and "enabled" not in step:
                step["enabled"] = True
            elif new_action == "load_pipeline" and "path" not in step:
                step["path"] = ""
            elif new_action == "apply_view" and "view_name" not in step:
                step["view_name"] = "default"
            elif new_action == "send_command":
                step.setdefault("module_uuid", "1")
                step.setdefault("command", {"init": True})
            elif new_action == "trigger":
                step.setdefault("method", "run")
                step.setdefault("params", {})
            elif new_action == "wait" and "seconds" not in step:
                step["seconds"] = 1.0

            self._populate_steps_list()
            self._populate_step_details()

    def _set_command_preset(self, step: Dict[str, Any], preset: Dict[str, Any]) -> None:
        step["command"] = copy.deepcopy(preset)
        self._populate_step_details()
        self._populate_steps_list()

    def _on_command_json_edited(self, step: Dict[str, Any], text: str) -> None:
        try:
            parsed = json.loads(text)
            step["command"] = parsed
            self._populate_steps_list()
        except Exception:
            step["command"] = text

    def _on_params_json_edited(self, step: Dict[str, Any], text: str) -> None:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                step["params"] = parsed
        except Exception:
            pass

    def _on_add_step_dialog(self, *args, **kwargs) -> None:
        """Append a new step to the automation script."""
        steps = self.script_data.setdefault("steps", [])
        new_step = {"action": "wait", "seconds": 1.0}
        steps.append(new_step)
        self.selected_step_idx = len(steps) - 1
        self._populate_steps_list()
        self._populate_step_details()

    def _on_move_step_up(self, *args, **kwargs) -> None:
        """Move selected step one position earlier."""
        steps = self.script_data.get("steps", [])
        idx = self.selected_step_idx
        if idx > 0 and idx < len(steps):
            steps[idx - 1], steps[idx] = steps[idx], steps[idx - 1]
            self.selected_step_idx = idx - 1
            self._populate_steps_list()
            self._populate_step_details()

    def _on_move_step_down(self, *args, **kwargs) -> None:
        """Move selected step one position later."""
        steps = self.script_data.get("steps", [])
        idx = self.selected_step_idx
        if 0 <= idx < len(steps) - 1:
            steps[idx + 1], steps[idx] = steps[idx], steps[idx + 1]
            self.selected_step_idx = idx + 1
            self._populate_steps_list()
            self._populate_step_details()

    def _on_duplicate_step(self, *args, **kwargs) -> None:
        """Duplicate the currently selected step."""
        steps = self.script_data.get("steps", [])
        if 0 <= self.selected_step_idx < len(steps):
            dup = copy.deepcopy(steps[self.selected_step_idx])
            steps.insert(self.selected_step_idx + 1, dup)
            self.selected_step_idx += 1
            self._populate_steps_list()
            self._populate_step_details()

    def _on_delete_step(self, *args, **kwargs) -> None:
        """Delete the currently selected step."""
        steps = self.script_data.get("steps", [])
        if 0 <= self.selected_step_idx < len(steps):
            steps.pop(self.selected_step_idx)
            if self.selected_step_idx >= len(steps):
                self.selected_step_idx = len(steps) - 1
            self._populate_steps_list()
            self._populate_step_details()

    def _on_browse_workspace_path(self, step: Dict[str, Any]) -> None:
        chosen = file_explorer.select_directory(default_path=str(PROJECT_ROOT))
        if chosen:
            step["path"] = str(chosen)
            self._populate_steps_list()
            self._populate_step_details()

    def _on_browse_pipeline_path(self, step: Dict[str, Any]) -> None:
        chosen = file_explorer.select_file(
            default_path=str(LAYOUTS_DIR),
            extensions=[("JSON files", "*.json")],
        )
        if chosen:
            p = Path(chosen)
            try:
                rel = p.relative_to(PROJECT_ROOT)
                step["path"] = f"/{rel.as_posix()}"
            except ValueError:
                step["path"] = str(p.resolve())
            self._populate_steps_list()
            self._populate_step_details()

    def _build_json_tab(self) -> None:
        """Raw JSON editing tab with format validation and live synchronization."""
        s = display_scaling.scale
        container_tag = f"{self.winID}_json_container"
        if dpg.does_item_exist(container_tag):
            dpg.delete_item(container_tag)

        with dpg.child_window(tag=container_tag, width=-1, height=-1, border=False, parent=f"{self.winID}_tab_json"):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Apply from Raw JSON", callback=self._on_apply_raw_json, width=s(180))
                dpg.add_button(label="Format JSON", callback=self._on_format_raw_json, width=s(120))
                dpg.add_text(
                    self.json_status,
                    tag=f"{self.winID}_json_status_text",
                    color=self.json_status_color,
                )

            dpg.add_spacer(height=s(4))
            json_str = json.dumps(self.script_data, indent=4)
            dpg.add_input_text(
                tag=f"{self.winID}_raw_json_input",
                default_value=json_str,
                multiline=True,
                height=-1,
                width=-1,
                tab_input=True,
            )

    def _sync_to_raw_json(self) -> None:
        input_tag = f"{self.winID}_raw_json_input"
        if dpg.does_item_exist(input_tag):
            json_str = json.dumps(self.script_data, indent=4)
            dpg.set_value(input_tag, json_str)

    def _on_apply_raw_json(self, *args, **kwargs) -> None:
        input_tag = f"{self.winID}_raw_json_input"
        if not dpg.does_item_exist(input_tag):
            return

        text = dpg.get_value(input_tag)
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("JSON root must be an object/dict with 'name' and 'steps' keys.")

            self.script_data = {
                "name": parsed.get("name", "Untitled Automation"),
                "steps": parsed.get("steps", []),
            }
            self.selected_step_idx = 0 if self.script_data["steps"] else -1
            self.json_status = "JSON applied successfully!"
            self.json_status_color = (180, 255, 180)
            logger.success("Automation Editor raw JSON updated.")
            self._update_json_status_text()
        except Exception as e:
            self.json_status = f"JSON Error: {e}"
            self.json_status_color = (255, 120, 120)
            logger.error(f"Invalid raw JSON: {e}")
            self._update_json_status_text()

    def _on_format_raw_json(self, *args, **kwargs) -> None:
        input_tag = f"{self.winID}_raw_json_input"
        if not dpg.does_item_exist(input_tag):
            return
        text = dpg.get_value(input_tag)
        try:
            parsed = json.loads(text)
            dpg.set_value(input_tag, json.dumps(parsed, indent=4))
            self.json_status = "JSON Formatted."
            self.json_status_color = (180, 255, 180)
            self._update_json_status_text()
        except Exception as e:
            self.json_status = f"Formatting Error: {e}"
            self.json_status_color = (255, 120, 120)
            self._update_json_status_text()

    def _update_json_status_text(self) -> None:
        status_tag = f"{self.winID}_json_status_text"
        if dpg.does_item_exist(status_tag):
            dpg.set_value(status_tag, self.json_status)
            dpg.configure_item(status_tag, color=self.json_status_color)

    def _on_select_script_combo(self, chosen_name: str, file_map: Dict[str, str]) -> None:
        if chosen_name in file_map:
            self.load_file(file_map[chosen_name])
            self._build_ui(active_tab=self.active_tab)

    def _on_browse_open(self, *args, **kwargs) -> None:
        chosen = file_explorer.select_file(
            default_path=str(SCRIPTS_DIR),
            extensions=[("JSON files", "*.json")],
        )
        if chosen:
            self.load_file(chosen)
            self._build_ui(active_tab=self.active_tab)

    def _on_new_script(self, *args, **kwargs) -> None:
        self._init_empty_script()
        self._build_ui(active_tab=f"{self.winID}_tab_steps")

    def _on_save_clicked(self, *args, **kwargs) -> None:
        self.save_file()
        self._build_ui(active_tab=self.active_tab)

    def _on_save_as_clicked(self, *args, **kwargs) -> None:
        chosen = file_explorer.save_file(
            default_path=str(SCRIPTS_DIR),
            default_name="automation_script.json",
            extensions=[("JSON files", "*.json")],
        )
        if chosen:
            self.save_file(chosen)
            self._build_ui(active_tab=self.active_tab)

    def _on_reload_clicked(self, *args, **kwargs) -> None:
        if self.current_filepath:
            self.load_file(self.current_filepath)
            self._build_ui(active_tab=self.active_tab)

    def _on_run_script_clicked(self, *args, **kwargs) -> None:
        """Run the edited automation script immediately."""
        try:
            # If saved file exists, run target file
            if self.current_filepath and Path(self.current_filepath).exists():
                self.save_file(self.current_filepath)
                automation_manager.run(self.current_filepath)
                logger.success(f"Running automation script from '{Path(self.current_filepath).name}'")
            else:
                # Save temporarily to run
                temp_path = SCRIPTS_DIR / "temp_automation.json"
                SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self.script_data, f, indent=4)
                automation_manager.run(str(temp_path))
                logger.success("Running in-memory automation script.")
        except Exception as e:
            logger.error(f"Failed to execute automation script: {e}")


# Global singleton instance
automation_editor: AutomationEditor = AutomationEditor()
