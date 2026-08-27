"""
Module Manager for Pipeline Creator.

Handles both installation and uninstallation of modules.
- Installation: Installs local packages into modules/ (standalone or nested).
- Uninstallation: Lists installed modules, safely identifies target directories/files,
  and deletes them, updating registry lists on-demand.
"""

from __future__ import annotations

import ast
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.file_explorer import file_explorer
from core.module_validation_manager import module_validation_manager
from core.node_editor import NodeEditor
from core.paths import PROJECT_ROOT


class ModuleManager:
    """
    Manages installation, validation, listing and uninstallation of modules.
    """

    def __init__(self, node_editor: Optional[NodeEditor] = None) -> None:
        """
        Initialize the Module Manager.

        Args:
            node_editor: Reference to NodeEditor to update the search popup registry.
        """
        self.winID: str = "module_installer_win"
        self.node_editor: Optional[NodeEditor] = node_editor

        # Installation State
        self.src_path: str = ""
        self.is_valid: bool = False
        self.valid_modules: List[str] = []
        self.standalone: bool = True
        self.parent_dir: str = ""
        self.custom_sub: str = ""

        # Uninstallation State
        self.selected_uninstall_key: str = ""
        self.selected_uninstall_file: Optional[Path] = None
        self.selected_uninstall_target: Optional[Path] = None
        self.uninstall_modules_data: Dict[str, Dict[str, Any]] = {}
        self.uninstall_grouped: Dict[str, Any] = {}
        self.uninstall_buttons_info: List[Dict[str, Any]] = []
        self.uninstall_headers_info: List[Any] = []
        self.mu_button_ids: Dict[str, Any] = {}

    def show_install(self) -> None:
        """Create or recreate the module installer window."""
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

        # Scale layout dimensions
        s = display_scaling.scale
        win_w = s(650)
        win_h = s(420)

        # Center window
        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        # Reset states
        self.src_path = ""
        self.is_valid = False
        self.valid_modules = []
        self.standalone = True
        self.parent_dir = ""
        self.custom_sub = ""

        # Fetch subdirs under modules/
        subdirs = self._get_modules_subdirs()

        with dpg.window(
            label="Install Module",
            tag=self.winID,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
            no_resize=False,
            no_move=False,
        ):
            dpg.add_text("Select and validate a local directory to install as a module.", color=(180, 180, 200, 255))
            dpg.add_separator()
            dpg.add_spacer(height=s(6))

            # 1. Source Folder Group
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Browse...",
                    callback=self._on_browse,
                    width=s(100),
                )
                dpg.add_text("No folder selected", tag="mi_src_path_label", color=(150, 150, 150, 255), wrap=s(480))

            dpg.add_spacer(height=s(4))
            dpg.add_text("Please select a module folder", color=(255, 165, 0, 255), tag="mi_src_status")
            dpg.add_spacer(height=s(6))
            dpg.add_separator()

            # 2. Standalone Checkbox
            dpg.add_checkbox(
                label="Standalone Module (install directly into /modules)",
                default_value=True,
                tag="mi_standalone",
                callback=self._on_standalone_change,
            )
            dpg.add_spacer(height=s(6))

            # 3. Hierarchy Group (hidden by default)
            with dpg.group(tag="mi_hierarchy_group", show=False):
                dpg.add_text("Select installation hierarchy:", color=(150, 150, 170, 255))
                dpg.add_combo(
                    items=subdirs,
                    label="Parent Directory in /modules",
                    tag="mi_parent_dir",
                    callback=self._on_parent_change,
                    width=s(300),
                )
                dpg.add_spacer(height=s(4))
                dpg.add_input_text(
                    label="Custom Subfolder Path (optional, e.g. subfolder/submodule)",
                    tag="mi_custom_sub",
                    callback=self._on_custom_change,
                    width=s(300),
                )
                dpg.add_spacer(height=s(6))

            dpg.add_separator()
            dpg.add_spacer(height=s(6))

            # 4. Destination Preview
            dpg.add_text("Destination Preview:", color=(150, 150, 170, 255))
            dpg.add_text("modules/", color=(150, 150, 255, 255), tag="mi_dest_preview")
            
            dpg.add_spacer(height=s(10))
            dpg.add_separator()
            dpg.add_spacer(height=s(6))

            # 5. Buttons group
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Install",
                    tag="mi_install_btn",
                    callback=self._on_install,
                    width=s(100),
                    enabled=False,
                )
                dpg.add_button(
                    label="Cancel",
                    callback=lambda *a: dpg.delete_item(self.winID) if dpg.does_item_exist(self.winID) else None,
                    width=s(100),
                )

    def _get_modules_subdirs(self) -> List[str]:
        """Scans the modules directory recursively for existing folders."""
        modules_dir = PROJECT_ROOT / "modules"
        subdirs = [""]  # First item is empty (root of modules/)
        if not modules_dir.exists():
            return subdirs

        # Traverse recursively to find directories
        for path in modules_dir.rglob("*"):
            if path.is_dir() and path.name != "__pycache__":
                if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
                    continue
                try:
                    rel = path.relative_to(modules_dir)
                    subdirs.append(str(rel).replace("\\", "/"))
                except ValueError:
                    continue
        return sorted(list(set(subdirs)))

    def _on_browse(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Browse button callback."""
        selected = file_explorer.select_folder(default_path=self.src_path or str(Path.cwd()))
        if not selected:
            return

        self.src_path = selected
        dpg.set_value("mi_src_path_label", selected)
        dpg.configure_item("mi_src_path_label", color=(255, 255, 255, 255))

        # Validate the folder
        is_ok, status_msg, valid_files = self._validate_folder(selected)
        self.is_valid = is_ok
        self.valid_modules = valid_files

        # Update UI
        if is_ok:
            dpg.set_value("mi_src_status", f"[VALID] {status_msg}")
            dpg.configure_item("mi_src_status", color=(50, 205, 50, 255))
        else:
            dpg.set_value("mi_src_status", f"[INVALID] {status_msg}")
            dpg.configure_item("mi_src_status", color=(255, 99, 71, 255))

        self._update_install_preview()

    def _validate_folder(self, folder_path: str) -> Tuple[bool, str, List[str]]:
        """Static AST and structural validation on files inside the folder."""
        p = Path(folder_path)
        if not p.is_dir():
            return False, "Selected path is not a directory.", []

        py_files = list(p.glob("*.py"))
        if not py_files:
            return False, "No Python files (.py) found in directory.", []

        valid_files: List[str] = []
        errors: List[str] = []

        for py_file in py_files:
            if py_file.name == "__init__.py":
                continue
            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Check for EXPORTED_CLASS assignment statically via AST
                tree = ast.parse(content, filename=str(py_file))
                has_exported = False
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == "EXPORTED_CLASS":
                                has_exported = True
                                break

                if not has_exported:
                    continue

                report = module_validation_manager.validator.validate_module(str(py_file))
                if report.is_valid:
                    valid_files.append(py_file.name)
                else:
                    for issue in report.issues:
                        errors.append(f"{py_file.name}: {issue.message}")
            except Exception as e:
                errors.append(f"Failed to parse {py_file.name}: {e}")

        if valid_files:
            return True, f"Found valid module file(s): {', '.join(valid_files)}", valid_files
        else:
            msg = "No files defining EXPORTED_CLASS found, or they failed validation."
            if errors:
                msg += " " + " | ".join(errors[:2])
            return False, msg, []

    def _on_standalone_change(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Standalone checkbox callback."""
        self.standalone = dpg.get_value("mi_standalone")
        dpg.configure_item("mi_hierarchy_group", show=not self.standalone)
        self._update_install_preview()

    def _on_parent_change(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Parent combo box callback."""
        self.parent_dir = dpg.get_value("mi_parent_dir")
        self._update_install_preview()

    def _on_custom_change(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Custom subfolder path callback."""
        self.custom_sub = dpg.get_value("mi_custom_sub")
        self._update_install_preview()

    def _update_install_preview(self) -> None:
        """Updates the destination preview path and toggles Install button state."""
        if not self.src_path:
            dpg.set_value("mi_src_path_label", "No folder selected")
            dpg.configure_item("mi_src_path_label", color=(150, 150, 150, 255))
            dpg.set_value("mi_dest_preview", "modules/")
            dpg.configure_item("mi_install_btn", enabled=False)
            return

        src_name = Path(self.src_path).name

        if self.standalone:
            rel_path = src_name
        else:
            parts = []
            if self.parent_dir:
                parts.append(self.parent_dir)
            if self.custom_sub:
                clean_custom = self.custom_sub.strip("/\\").replace("\\", "/")
                if clean_custom:
                    parts.append(clean_custom)
            parts.append(src_name)
            rel_path = "/".join(parts)

        preview_str = f"modules/{rel_path}"
        dpg.set_value("mi_dest_preview", preview_str)
        dpg.configure_item("mi_install_btn", enabled=self.is_valid)

    def _on_install(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Install button callback."""
        if not self.src_path or not self.is_valid:
            return

        modules_dir = PROJECT_ROOT / "modules"
        src_name = Path(self.src_path).name

        if self.standalone:
            dest_dir = modules_dir / src_name
        else:
            parts = []
            if self.parent_dir:
                parts.append(self.parent_dir)
            if self.custom_sub:
                clean_custom = self.custom_sub.strip("/\\").replace("\\", "/")
                if clean_custom:
                    parts.append(clean_custom)
            parts.append(src_name)
            dest_dir = modules_dir / Path(*parts)

        if dest_dir.exists():
            self._show_overwrite_modal(dest_dir)
        else:
            self._do_copy(dest_dir)

    def _show_overwrite_modal(self, dest_dir: Path) -> None:
        """Show confirmation dialog before overwriting folder."""
        modal_tag = "mi_overwrite_confirm_modal"
        if dpg.does_item_exist(modal_tag):
            dpg.delete_item(modal_tag)

        s = display_scaling.scale
        win_w = s(400)
        win_h = s(160)

        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        try:
            rel_display = "modules/" + str(dest_dir.relative_to(PROJECT_ROOT / "modules")).replace("\\", "/")
        except Exception:
            rel_display = str(dest_dir)

        with dpg.window(
            label="Confirm Overwrite",
            tag=modal_tag,
            modal=True,
            no_resize=True,
            no_move=True,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
        ):
            dpg.add_text("The destination folder already exists:", wrap=s(360))
            dpg.add_text(rel_display, color=(150, 150, 255, 255), wrap=s(360))
            dpg.add_text("Do you want to overwrite it?", color=(255, 165, 0, 255))
            dpg.add_spacer(height=s(6))

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Yes, Overwrite",
                    width=s(120),
                    callback=self._on_overwrite_confirmed,
                    user_data=dest_dir,
                )
                dpg.add_button(
                    label="Cancel",
                    width=s(100),
                    callback=lambda: dpg.delete_item(modal_tag) if dpg.does_item_exist(modal_tag) else None,
                )

    def _on_overwrite_confirmed(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Callback when overwrite is confirmed."""
        modal_tag = "mi_overwrite_confirm_modal"
        if dpg.does_item_exist(modal_tag):
            dpg.delete_item(modal_tag)

        dest_dir = user_data
        self._do_copy(dest_dir)

    def _do_copy(self, dest_dir: Path) -> None:
        """Performs actual file copy and updates registries."""
        try:
            if dest_dir.exists():
                shutil.rmtree(dest_dir)

            dest_dir.parent.mkdir(parents=True, exist_ok=True)

            shutil.copytree(self.src_path, dest_dir)
            logger.info(f"Module installed successfully from {self.src_path} to {dest_dir}")

            from core.module_registry import get_available_modules

            get_available_modules(force_reload=True)
            self._refresh_node_editor()

            if dpg.does_item_exist(self.winID):
                dpg.delete_item(self.winID)

            self._show_install_success_modal(dest_dir)

        except Exception as e:
            logger.error(f"Failed to install module: {e}")
            if dpg.does_item_exist("mi_src_status"):
                dpg.set_value("mi_src_status", f"[ERROR] Installation failed: {e}")
                dpg.configure_item("mi_src_status", color=(255, 0, 0, 255))

    def _show_install_success_modal(self, dest_dir: Path) -> None:
        """Show success modal dialog for installation."""
        modal_tag = "mi_success_modal"
        if dpg.does_item_exist(modal_tag):
            dpg.delete_item(modal_tag)

        s = display_scaling.scale
        win_w = s(500)
        win_h = s(200)

        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        try:
            rel_display = "modules/" + str(dest_dir.relative_to(PROJECT_ROOT / "modules")).replace("\\", "/")
        except Exception:
            rel_display = str(dest_dir)

        with dpg.window(
            label="Installation Success",
            tag=modal_tag,
            modal=True,
            no_resize=True,
            no_move=True,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
        ):
            dpg.add_text("Module installed successfully!", color=(50, 205, 50, 255))
            dpg.add_text("Location:")
            dpg.add_text(rel_display, color=(150, 150, 255, 255), wrap=s(460))
            dpg.add_spacer(height=s(6))

            dpg.add_button(
                label="OK",
                width=s(80),
                callback=lambda: dpg.delete_item(modal_tag) if dpg.does_item_exist(modal_tag) else None,
            )

    def show_uninstall(self) -> None:
        """Create or recreate the module uninstaller window."""
        if dpg.does_item_exist("module_uninstaller_win"):
            dpg.delete_item("module_uninstaller_win")

        # Scale layout dimensions
        s = display_scaling.scale
        win_w = s(600)
        win_h = s(600)

        # Center window
        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        # Reset selection state
        self.selected_uninstall_key = ""
        self.selected_uninstall_file = None
        self.selected_uninstall_target = None

        with dpg.window(
            label="Uninstall Module",
            tag="module_uninstaller_win",
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
            no_resize=False,
            no_move=False,
            no_scrollbar=True,
        ):
            dpg.add_text("Select a module to uninstall from the list below.", color=(180, 180, 200, 255))
            dpg.add_separator()
            dpg.add_spacer(height=s(6))

            # Modules Searchable Hierarchy
            dpg.add_text("Installed Modules:")
            dpg.add_input_text(
                hint="Search modules...",
                tag="mu_search_input",
                callback=self._on_uninstall_search_change,
                width=-1,
            )
            dpg.add_spacer(height=s(2))
            dpg.add_child_window(
                tag="mu_modules_list_container",
                width=-1,
                height=-s(160),
            )
            
            dpg.add_spacer(height=s(6))
            dpg.add_text("Target to Delete:", color=(255, 99, 71, 255))
            dpg.add_text("None", tag="mu_detail_target", wrap=s(560))

            dpg.add_spacer(height=s(10))
            dpg.add_separator()
            dpg.add_spacer(height=s(6))

            # Bottom buttons
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Uninstall",
                    tag="mu_uninstall_btn",
                    callback=self._on_uninstall_click,
                    width=s(130),
                    enabled=False,
                )
                dpg.add_button(
                    label="Cancel",
                    callback=lambda *a: dpg.delete_item("module_uninstaller_win") if dpg.does_item_exist("module_uninstaller_win") else None,
                    width=s(100),
                )
            
            dpg.add_spacer(height=s(6))

        # Load list of modules
        self._load_uninstall_modules()

    def _load_uninstall_modules(self) -> None:
        """Loads modules, builds hierarchy, and populates UI."""
        # 1. Build hierarchy tree and list data
        self._build_uninstall_registry()
        
        # 2. Reset search input value if it exists
        if dpg.does_item_exist("mu_search_input"):
            dpg.set_value("mu_search_input", "")
            
        # 3. Populate collapsing headers and buttons in UI
        self._populate_uninstall_list()

    def _build_uninstall_registry(self) -> None:
        """Build the grouped module registry for uninstallation."""
        from core.module_registry import get_available_modules

        modules_dict = get_available_modules()
        self.uninstall_modules_data = {}

        # Collect all active module file paths for safety checks
        all_module_files = []
        for name, cls in modules_dict.items():
            path_str = getattr(cls, "_source_file", None) or getattr(cls, "_file_path", None)
            if path_str:
                all_module_files.append(Path(path_str))

        # Hierarchical root node
        def new_node() -> Dict[str, Any]:
            return {"__classes__": [], "subfolders": {}}

        self.uninstall_grouped = new_node()
        self.uninstall_buttons_info = []
        self.uninstall_headers_info = []

        for name, cls in modules_dict.items():
            path_str = getattr(cls, "_source_file", None) or getattr(cls, "_file_path", None)
            if not path_str:
                continue

            path = Path(path_str)
            modules_dir = PROJECT_ROOT / "modules"
            try:
                path.relative_to(modules_dir)
            except ValueError:
                continue

            class_name = cls.__name__ if hasattr(cls, "__name__") else str(cls)

            desc = getattr(cls, "description", "") or getattr(cls, "DESCRIPTION", "")
            if not desc:
                desc = getattr(cls, "__doc__", "") or ""
            desc = desc.strip()

            target_to_delete = self._resolve_uninstall_target(path, all_module_files)

            self.uninstall_modules_data[name] = {
                "file_path": path,
                "class_name": class_name,
                "description": desc or "No description provided.",
                "target_to_delete": target_to_delete,
            }

            reg_name = name
            if reg_name.startswith("modules."):
                reg_name = reg_name[len("modules.") :]

            parts = reg_name.split(".")
            display_name = parts[-1]
            folders = parts[:-1]

            curr = self.uninstall_grouped
            for folder in folders:
                if folder not in curr["subfolders"]:
                    curr["subfolders"][folder] = new_node()
                curr = curr["subfolders"][folder]

            curr["__classes__"].append((display_name, name))

    def _populate_uninstall_list(self) -> None:
        """Create the hierarchy of collapsing headers and buttons in the UI."""
        if not dpg.does_item_exist("mu_modules_list_container"):
            return

        dpg.delete_item("mu_modules_list_container", children_only=True)

        self.uninstall_buttons_info = []
        self.uninstall_headers_info = []
        self.mu_button_ids = {}

        def build_ui(
            node: Dict[str, Any],
            parent_tag: Union[str, int],
            parent_headers: List[Union[str, int]],
            path_names: List[str],
        ) -> None:
            for folder_name, subnode in sorted(node["subfolders"].items()):
                with dpg.collapsing_header(label=folder_name, default_open=False, parent=parent_tag) as header:
                    self.uninstall_headers_info.append(header)
                    grp = dpg.add_group(indent=15, parent=header)
                    build_ui(subnode, grp, parent_headers + [header], path_names + [folder_name])

            for display_name, full_key in sorted(node["__classes__"], key=lambda x: x[0]):
                metadata = self.uninstall_modules_data[full_key]
                desc = metadata["description"]

                btn = dpg.add_button(
                    label=display_name,
                    callback=self._on_uninstall_select_button,
                    user_data=full_key,
                    width=-1,
                    parent=parent_tag,
                )

                self.mu_button_ids[full_key] = btn
                self.uninstall_buttons_info.append({
                    "id": btn,
                    "key": full_key,
                    "name_lower": display_name.lower(),
                    "path_lower": " ".join(path_names + [display_name]).lower(),
                    "headers": parent_headers,
                })

                if desc:
                    with dpg.tooltip(parent=btn):
                        dpg.add_text(desc, wrap=300)

        build_ui(self.uninstall_grouped, "mu_modules_list_container", [], [])

    def _resolve_uninstall_target(self, file_path: Path, all_module_files: List[Path]) -> Path:
        """
        Safely identifies the target path to delete.
        Returns parent directory if it's uniquely used by this module.
        Returns the file itself if other modules share the parent directory.
        """
        modules_dir = PROJECT_ROOT / "modules"

        parent = file_path.parent
        if parent == modules_dir:
            return file_path

        other_modules = [f for f in all_module_files if f != file_path and f.parent == parent]

        if other_modules:
            return file_path
        else:
            return parent

    def _on_uninstall_search_change(self, sender: Any = None, app_data: str = "", user_data: Any = None) -> None:
        """Filters the hierarchy of collapsing headers and buttons based on search query."""
        filter_lower = app_data.lower().strip()
        from core.search_utils import fuzzy_score

        for h in self.uninstall_headers_info:
            if dpg.does_item_exist(h):
                if filter_lower:
                    dpg.hide_item(h)
                    dpg.set_value(h, True)
                else:
                    dpg.show_item(h)
                    dpg.set_value(h, False)

        for item in self.uninstall_buttons_info:
            btn_id = item["id"]
            if not dpg.does_item_exist(btn_id):
                continue

            if not filter_lower:
                dpg.show_item(btn_id)
            else:
                score = fuzzy_score(filter_lower, item["name_lower"])
                path_score = fuzzy_score(filter_lower, item["path_lower"])
                if score > 0 or path_score > 50:
                    dpg.show_item(btn_id)
                    for h in item["headers"]:
                        if dpg.does_item_exist(h):
                            dpg.show_item(h)
                else:
                    dpg.hide_item(btn_id)

    def _on_uninstall_select_button(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Callback when a module button is clicked in the uninstaller list."""
        key = user_data
        if not key or key not in self.uninstall_modules_data:
            return

        data = self.uninstall_modules_data[key]
        self.selected_uninstall_key = key
        self.selected_uninstall_file = data["file_path"]
        self.selected_uninstall_target = data["target_to_delete"]

        for k, btn_id in self.mu_button_ids.items():
            if dpg.does_item_exist(btn_id):
                reg_name = k
                if reg_name.startswith("modules."):
                    reg_name = reg_name[len("modules.") :]
                display_name = reg_name.split(".")[-1]

                if k == key:
                    dpg.configure_item(btn_id, label=f"-> {display_name}")
                else:
                    dpg.configure_item(btn_id, label=display_name)

        try:
            rel_display = "modules/" + str(self.selected_uninstall_target.relative_to(PROJECT_ROOT / "modules")).replace("\\", "/")
        except Exception:
            rel_display = str(self.selected_uninstall_target)

        dpg.set_value("mu_detail_target", rel_display)
        dpg.configure_item("mu_uninstall_btn", enabled=True)

    def _on_uninstall_click(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Uninstall button callback - shows confirmation dialog."""
        if not self.selected_uninstall_key or not self.selected_uninstall_target:
            return

        modal_tag = "mu_confirm_modal"
        if dpg.does_item_exist(modal_tag):
            dpg.delete_item(modal_tag)

        s = display_scaling.scale
        win_w = s(520)
        win_h = s(220)

        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        try:
            rel_display = "modules/" + str(self.selected_uninstall_target.relative_to(PROJECT_ROOT / "modules")).replace("\\", "/")
        except Exception:
            rel_display = str(self.selected_uninstall_target)

        is_dir = self.selected_uninstall_target.is_dir()
        target_type = "folder" if is_dir else "file"

        with dpg.window(
            label="Confirm Uninstall",
            tag=modal_tag,
            modal=True,
            no_resize=True,
            no_move=True,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
        ):
            dpg.add_text("Are you sure you want to uninstall this module?", color=(255, 255, 255, 255))
            dpg.add_text(f"This will permanently delete the following {target_type}:", wrap=s(480))
            dpg.add_text(rel_display, color=(255, 99, 71, 255), wrap=s(480))
            dpg.add_spacer(height=s(6))

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Uninstall",
                    width=s(130),
                    callback=self._on_uninstall_confirmed,
                )
                dpg.add_button(
                    label="Cancel",
                    width=s(100),
                    callback=lambda: dpg.delete_item(modal_tag) if dpg.does_item_exist(modal_tag) else None,
                )

    def _on_uninstall_confirmed(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Callback when uninstallation is confirmed."""
        modal_tag = "mu_confirm_modal"
        if dpg.does_item_exist(modal_tag):
            dpg.delete_item(modal_tag)

        key = self.selected_uninstall_key
        target = self.selected_uninstall_target

        if not key or not target:
            return

        try:
            if target.exists():
                parent_to_clean = target.parent
                self._safe_delete_path(target)
                self._cleanup_empty_parents(parent_to_clean)
            logger.info(f"Module '{key}' uninstalled successfully by deleting: {target}")

            for k in list(sys.modules.keys()):
                if k == key or k.startswith(f"{key}."):
                    del sys.modules[k]

            from core.module_registry import get_available_modules

            get_available_modules(force_reload=True)
            self._refresh_node_editor()

            if dpg.does_item_exist("module_uninstaller_win"):
                dpg.delete_item("module_uninstaller_win")

            self._show_uninstall_success_modal(key, target)

        except Exception as e:
            logger.error(f"Failed to uninstall module '{key}': {e}")
            self._show_error_modal(f"Uninstall failed: {e}")

    def _is_empty_or_only_pycache(self, directory: Path) -> bool:
        """Returns True if the directory has no files/folders, or only empty/pycache files."""
        if not directory.exists() or not directory.is_dir():
            return False

        for item in directory.iterdir():
            if item.name == "__pycache__":
                continue
            if item.name.startswith("."):
                continue
            return False

        return True

    def _cleanup_empty_parents(self, start_dir: Path) -> None:
        """Recursively cleans up empty parent directories up to modules/."""
        modules_dir = PROJECT_ROOT / "modules"

        curr = start_dir
        while curr != modules_dir and curr.is_relative_to(modules_dir):
            if curr.exists() and curr.is_dir():
                if self._is_empty_or_only_pycache(curr):
                    pycache_dir = curr / "__pycache__"
                    if pycache_dir.exists():
                        self._safe_delete_path(pycache_dir)

                    try:
                        os.chmod(curr, stat.S_IWRITE)
                        curr.rmdir()
                        logger.info(f"Cleaned up empty parent directory: {curr}")
                    except Exception as e:
                        logger.warning(f"Could not delete empty folder {curr}: {e}")
                        break
                else:
                    break
            else:
                break
            curr = curr.parent

    def _safe_delete_path(self, target: Path) -> None:
        """Safely deletes a file or directory, handling Windows locked files or permissions."""
        if not target.exists():
            return

        if target.is_file():
            try:
                target.unlink()
            except Exception as e:
                logger.warning(f"Could not delete file {target}: {e}")
                try:
                    temp_target = target.with_suffix(".deleted")
                    target.rename(temp_target)
                    logger.debug(f"Renamed locked file {target} to {temp_target}")
                except Exception:
                    pass
        elif target.is_dir():
            for p in list(target.rglob("*.py")):
                try:
                    p.unlink()
                except Exception:
                    pass

            def onerror(func: Any, path: str, exc_info: Any) -> None:
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass

            try:
                shutil.rmtree(target, onerror=onerror)
            except Exception as e:
                logger.warning(f"Completed partial deletion of folder {target}: {e}")

    def _show_uninstall_success_modal(self, module_key: str, target: Path) -> None:
        """Show success modal dialog for uninstallation."""
        modal_tag = "mu_success_modal"
        if dpg.does_item_exist(modal_tag):
            dpg.delete_item(modal_tag)

        s = display_scaling.scale
        win_w = s(500)
        win_h = s(200)

        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        try:
            rel_display = "modules/" + str(target.relative_to(PROJECT_ROOT / "modules")).replace("\\", "/")
        except Exception:
            rel_display = str(target)

        with dpg.window(
            label="Uninstallation Success",
            tag=modal_tag,
            modal=True,
            no_resize=True,
            no_move=True,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
        ):
            dpg.add_text("Module uninstalled successfully!", color=(50, 205, 50, 255))
            dpg.add_text(f"Removed: {module_key}")
            dpg.add_text(rel_display, color=(150, 150, 255, 255), wrap=s(460))
            dpg.add_spacer(height=s(6))

            dpg.add_button(
                label="OK",
                width=s(80),
                callback=lambda: dpg.delete_item(modal_tag) if dpg.does_item_exist(modal_tag) else None,
            )

    def _refresh_node_editor(self) -> None:
        """Tells the Node Editor to rebuild its registries and lists."""
        if self.node_editor:
            try:
                list_tag = f"{self.node_editor.popup_tag}_list"
                if dpg.does_item_exist(list_tag):
                    dpg.delete_item(list_tag, children_only=True)
                if hasattr(self.node_editor, "_build_module_registry"):
                    self.node_editor._build_module_registry()
                if hasattr(self.node_editor, "_populate_module_list"):
                    self.node_editor._populate_module_list()
                logger.debug("Node Editor search popup registry reloaded.")
            except Exception as e:
                logger.error(f"Failed to refresh Node Editor registry: {e}")

    def _show_error_modal(self, message: str) -> None:
        """Show error modal dialog."""
        modal_tag = "mi_error_modal"
        if dpg.does_item_exist(modal_tag):
            dpg.delete_item(modal_tag)

        s = display_scaling.scale
        win_w = s(380)
        win_h = s(140)

        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        with dpg.window(
            label="Error",
            tag=modal_tag,
            modal=True,
            no_resize=True,
            no_move=True,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
        ):
            dpg.add_text("An error occurred:", color=(255, 0, 0, 255))
            dpg.add_text(message, wrap=s(340))
            dpg.add_spacer(height=s(6))

            dpg.add_button(
                label="OK",
                width=s(80),
                callback=lambda: dpg.delete_item(modal_tag) if dpg.does_item_exist(modal_tag) else None,
            )

