"""
Main Window for Pipeline Creator.

This module defines the primary application window with the main menu bar.
It provides access to workspace management, module tools, and debugging utilities.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from config.theme_manager import theme_manager
from core.app_state import app_state
from core.dependency_manager import dependency_manager
from core.file_explorer import file_explorer
from core.fusion_manager import FusionManager
from core.module_registry import (
    LAST_LOADED_SUBFLOW,
    LAST_LOADED_WORKSPACE,
    MODULES_REGISTRY,
    apply_view,
    clear_registry,
    export_view,
    export_workspace,
    get_available_modules,
    load_workspace,
)
from core.module_validation_manager import module_validation_manager
from core.node_editor import NodeEditor
from core.working_directory_manager import working_directory_manager


class MainWin:
    """
    Main application window with menu bar for Pipeline Creator.

    This window serves as the primary container and provides:
    - Workspace save/load functionality
    - Access to Node Editor and Fusion Manager
    - Dependency checking tools
    - DearPyGui debugging utilities

    Attributes:
        winID: The DearPyGui tag for this window.
        node_editor: Reference to the NodeEditor instance.
    """

    
    def __init__(self, node_editor: NodeEditor) -> None:
        """
        Initialize the main window.

        Args:
            node_editor: The NodeEditor instance to use for node operations.
        """
        self.winID: str = "main_win"
        self.node_editor: NodeEditor = node_editor

        # Map from item label -> menu_item tag for pinned entries
        self._pinned_items: dict = {}
        self._pinned_menu_tag: str = "main_win_pinned_menu"

        # Tags for mode-sensitive menu items: feature_key -> dpg tag
        self._mode_tags: dict[str, Any] = {}

        from core.module_manager import ModuleManager
        self.module_manager = ModuleManager(node_editor=self.node_editor)

        self._create_window()
        self._register_keyboard_shortcuts()

        self.apply_mode_visibility()
    
    @staticmethod
    def _mode_allows(feature: str, mode: str) -> bool:
        """
        Return True if *feature* should be visible in *mode*.

        Feature catalogue
        -----------------
        user     : theme, tutorial, pinned, tools, adapt_display, 
                     minimize_tray, fullscreen
        advanced : + workspace, node_editor, fusion_manager, logs, edit_config, install_module, uninstall_module
        dev      : + check_deps, debug, font_manager, item_registry,
                     metrics, about
        """
        _user     = {"theme", "tutorial", "pinned", "tools", "adapt_display", "minimize_tray", "fullscreen"}
        _advanced = _user | {
            "workspace", "node_editor", "fusion_manager", "logs", "edit_config", "install_module", "uninstall_module",
        }
        _dev      = _advanced | {
            "check_deps", "debug", "font_manager",
            "item_registry", "metrics", "about",
        }
        if mode == "user":
            return feature in _user
        if mode == "advanced":
            return feature in _advanced
        return feature in _dev   # dev

    def apply_mode_visibility(self) -> None:
        """
        Show/hide menu items based on the current app_state.mode.

        Call this after any login (or re-login) to refresh the bar.
        """
        mode = app_state.mode
        for feature, tag in self._mode_tags.items():
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, show=self._mode_allows(feature, mode))
        
        # Refresh permissions for all active modules
        from core.module_registry import get_registered_modules
        for module in get_registered_modules():
            if hasattr(module, "update_permission"):
                module.update_permission()

        logger.debug(f"Menu bar and module visibility applied for mode='{mode}'")

        # Apply the view layout for this mode (no-op if no view is defined)
        apply_view(mode)

    def _register_keyboard_shortcuts(self) -> None:
        """Register global keyboard shortcuts on the main window."""
        with dpg.handler_registry():
            # CTRL + SHIFT + L  →  re-open the login dialog
            dpg.add_key_press_handler(
                key=dpg.mvKey_L,
                callback=self._on_relogin_shortcut,
            )

    def _on_relogin_shortcut(self, sender, app_data, user_data, *args, **kwargs) -> None:
        """Fire the login dialog again when CTRL+SHIFT+L is pressed."""
        if dpg.is_key_down(dpg.mvKey_LControl) and dpg.is_key_down(dpg.mvKey_LShift):
            self._trigger_relogin()

    def _trigger_relogin(self) -> None:
        """Show the login dialog and refresh menu visibility afterwards."""
        from core.login_dialog import show_login_dialog
        from core.config_manager import config
        _default = config.get("Login", {}).get("default_mode", "user")
        
        # At runtime, we must NOT pump frames (it would crash)
        show_login_dialog(
            default_mode=app_state.mode or _default,
            on_confirm=self.apply_mode_visibility,
            pump_frames=False
        )

    def _create_window(self) -> None:
        """Create the main window and menu bar."""
        with dpg.window(tag=self.winID):
            with dpg.menu_bar():
                self._create_workspace_menu()

                # Node Editor  (advanced+)
                _ne_tag = dpg.generate_uuid()
                self._mode_tags["node_editor"] = _ne_tag
                dpg.add_menu_item(
                    tag=_ne_tag,
                    label="Node Editor",
                    callback=lambda *args: self.node_editor.show()
                )

                # Fusion Manager  (advanced+)
                _fm_tag = dpg.generate_uuid()
                self._mode_tags["fusion_manager"] = _fm_tag
                from core.main_win import fusion_manager
                dpg.add_menu_item(
                    tag=_fm_tag,
                    label="Fusion Manager",
                    callback=lambda *args: fusion_manager.show()
                )

                self._create_tools_menu()
                self._create_help_menu()
                dpg.add_menu(label="Pinned", tag=self._pinned_menu_tag, show=False)
    
    def _create_workspace_menu(self) -> None:
        """Create the Workspace menu."""
        _ws_tag = dpg.generate_uuid()
        self._mode_tags["workspace"] = _ws_tag
        with dpg.menu(label="Workspace", tag=_ws_tag):
            dpg.add_menu_item(label="Select Working Dir.", callback=working_directory_manager.select_directory)
            dpg.add_separator()
            dpg.add_menu_item(label="Export Pipeline", callback=self._on_save_workspace)
            dpg.add_menu_item(label="Load Pipeline", callback=self._on_load_workspace)
            dpg.add_menu_item(label="Pipeline Editor", callback=self._on_pipeline_editor)
            dpg.add_separator()
            dpg.add_menu_item(label="Load Automation Script", callback=self._on_load_automation_script)
            dpg.add_menu_item(label="Automation Editor", callback=self._on_automation_editor)
            dpg.add_separator()
            self.view_menu_tag = "main_win_views_menu"
            with dpg.menu(label="Views", tag=self.view_menu_tag, show=False):
                pass
            dpg.add_separator(tag="main_win_views_separator", show=False)
            dpg.add_menu_item(label="Export View...", callback=self._on_export_view,
                              tag="menu_export_view")
    
    def _create_tools_menu(self) -> None:
        """Create the Tools menu with debugging utilities."""
        _tools_tag = dpg.generate_uuid()
        self._mode_tags["tools"] = _tools_tag
        with dpg.menu(label="Tools", tag=_tools_tag):
            # Check Dependencies  (dev)
            _cd_tag = dpg.generate_uuid()
            self._mode_tags["check_deps"] = _cd_tag
            dpg.add_menu_item(tag=_cd_tag, label="Check Dependencies", callback=self._on_check_dependencies)
            dpg.add_separator()

            # Adapt Display Scaling  (user+)
            _ads_tag = dpg.generate_uuid()
            self._mode_tags["adapt_display"] = _ads_tag
            dpg.add_menu_item(tag=_ads_tag, label="Adapt Display Scaling", callback=self._on_adapt_display)
            dpg.add_separator()

            # Toggle Fullscreen  (user+)
            _fs_tag = dpg.generate_uuid()
            self._mode_tags["fullscreen"] = _fs_tag
            dpg.add_menu_item(tag=_fs_tag, label="Toggle Fullscreen", callback=lambda *args: dpg.toggle_viewport_fullscreen())
            dpg.add_separator()

            # Theme sub-menu  (user+  – always visible)
            self._create_theme_submenu()
            dpg.add_separator()
            
            from core.config_manager import config
            if config.get('Window', {}).get('minimize_to_tray', False):
                _mt_tag = dpg.generate_uuid()
                self._mode_tags["minimize_tray"] = _mt_tag
                dpg.add_menu_item(tag=_mt_tag, label="Minimize to Tray", callback=self._on_minimize_to_tray)
                dpg.add_separator()

            # Show Logs  (advanced+)
            _log_tag = dpg.generate_uuid()
            self._mode_tags["logs"] = _log_tag
            dpg.add_menu_item(tag=_log_tag, label="Show Logs", callback=self._on_show_logs)

            # Edit Config  (advanced+)
            _ec_tag = dpg.generate_uuid()
            self._mode_tags["edit_config"] = _ec_tag
            dpg.add_menu_item(tag=_ec_tag, label="Edit Config", callback=self._on_edit_config)

            # Install Module  (advanced+)
            _im_tag = dpg.generate_uuid()
            self._mode_tags["install_module"] = _im_tag
            dpg.add_menu_item(tag=_im_tag, label="Install Module", callback=self._on_install_module)

            # Uninstall Module  (advanced+)
            _un_tag = dpg.generate_uuid()
            self._mode_tags["uninstall_module"] = _un_tag
            dpg.add_menu_item(tag=_un_tag, label="Uninstall Module", callback=self._on_uninstall_module)

            dpg.add_separator()

            # ── Dev-only items ──────────────────────────────────────────
            _dbg_tag = dpg.generate_uuid()
            self._mode_tags["debug"] = _dbg_tag
            dpg.add_menu_item(tag=_dbg_tag, label="Show Debug", callback=lambda *args: dpg.show_tool(dpg.mvTool_Debug))

            _font_tag = dpg.generate_uuid()
            self._mode_tags["font_manager"] = _font_tag
            dpg.add_menu_item(tag=_font_tag, label="Show Font Manager", callback=lambda *args: dpg.show_tool(dpg.mvTool_Font))

            _ir_tag = dpg.generate_uuid()
            self._mode_tags["item_registry"] = _ir_tag
            dpg.add_menu_item(tag=_ir_tag, label="Show Item Registry", callback=lambda *args: dpg.show_tool(dpg.mvTool_ItemRegistry))

            _met_tag = dpg.generate_uuid()
            self._mode_tags["metrics"] = _met_tag
            dpg.add_menu_item(tag=_met_tag, label="Show Metrics", callback=lambda *args: dpg.show_tool(dpg.mvTool_Metrics))

            _abt_tag = dpg.generate_uuid()
            self._mode_tags["about"] = _abt_tag
            dpg.add_menu_item(tag=_abt_tag, label="Show About", callback=lambda *args: dpg.show_tool(dpg.mvTool_About))

    def _create_help_menu(self) -> None:
        """Create the Help menu with Tutorials."""
        with dpg.menu(label="Help"):
            import importlib.util
            required_modules = ["mkdocs", "pymdownx", "mkdocstrings"]
            missing_modules = [mod for mod in required_modules if importlib.util.find_spec(mod) is None]
            mkdocs_installed = len(missing_modules) == 0
            if not mkdocs_installed:
                logger.warning(
                    f"MkDocs or required extensions ({', '.join(missing_modules)}) are not installed. "
                    "Documentation option will be disabled."
                )

            from core.documentation_manager import doc_manager
            with dpg.menu(label="Documentation", tag="menu_documentation", enabled=mkdocs_installed):
                dpg.add_menu_item(label="Open in Browser", tag="doc_open_item", callback=lambda *args: doc_manager.reopen_link(), show=False)
                dpg.add_menu_item(label="Start Server", tag="doc_start_item", callback=lambda *args: doc_manager.start_server(), show=True)
                dpg.add_menu_item(label="Stop Server", tag="doc_stop_item", callback=lambda *args: doc_manager.stop_server(), show=False)
            doc_manager.update_menu_visibility()
            dpg.add_separator()

            from core.tutorial_manager import tutorial_manager
            dpg.add_menu_item(label="Open Tutorial Manager", callback=lambda *args: tutorial_manager.show())
            dpg.add_separator()
            
            with dpg.menu(label="Tutorials"):
                from core.paths import TUTORIALS_DIR
                import json
                
                if TUTORIALS_DIR.exists():
                    for filepath in TUTORIALS_DIR.glob("*.json"):
                        desc = "No description provided."
                        try:
                            with open(filepath, 'r', encoding='utf-8') as f_in:
                                data = json.load(f_in)
                                if isinstance(data, dict):
                                    desc = data.get("description", desc)
                        except Exception:
                            pass
                        
                        item_label = filepath.stem
                            
                        def _make_cb(fp):
                            return lambda *a: tutorial_manager.load_and_play(fp)
                            
                        mi = dpg.add_menu_item(label=item_label, callback=_make_cb(filepath))
                        if mi and desc and desc != "No description provided.":
                            with dpg.tooltip(parent=mi):
                                dpg.add_text(desc, wrap=300)

    def _create_theme_submenu(self) -> None:
        """Populate the Theme sub-menu with all auto-discovered palettes."""
        from config.theme_manager import PALETTES, theme_manager
        with dpg.menu(label="Theme"):
            for theme_name in PALETTES.keys():
                label = theme_name.title()
                dpg.add_menu_item(
                    label=label,
                    callback=self._on_switch_theme,
                    user_data=theme_name,
                )
            dpg.add_separator()
            with dpg.menu(label="Colorblind Palette"):
                modes = ["none", "universal", "protanopia", "deuteranopia", "tritanopia"]
                for mode in modes:
                    dpg.add_menu_item(
                        label=mode.title(),
                        callback=self._on_switch_colorblind,
                        user_data=mode,
                    )

    def _on_switch_theme(self, sender=None, app_data=None, user_data=None, *args, **kwargs) -> None:
        """Switch the active theme and persist the choice to config.json."""
        from config.theme_manager import PALETTES, theme_manager
        from core.config_manager import config
        import json
        from core.paths import PROJECT_ROOT

        theme_name = user_data
        if theme_name not in PALETTES:
            logger.warning(f"Theme '{theme_name}' not found.")
            return

        # Apply the new theme at runtime
        theme_manager.load_theme(theme_name)
        theme_manager.update_titlebar()

        # Re-apply the main window's specific background
        if dpg.does_item_exist("main_win"):
            new_mw_theme = theme_manager.create_main_win_theme()
            dpg.bind_item_theme("main_win", new_mw_theme)

        # Persist to config.json
        self._save_ui_config("theme_name", theme_name)
        logger.info(f"Theme switched to '{theme_name}'")

    def _on_switch_colorblind(self, sender=None, app_data=None, user_data=None, *args, **kwargs) -> None:
        """Switch the global colorblind override and persist."""
        from config.theme_manager import theme_manager
        
        mode = user_data
        self._save_ui_config("colorblind_type", mode)
        
        # Refresh theme with new override
        theme_manager.refresh()
        logger.info(f"Colorblind palette override set to '{mode}'")

    def _save_ui_config(self, key: str, value: Any) -> None:
        """Helper to update a key in UI section of config.json."""
        from core.config_manager import config
        import json
        from core.paths import PROJECT_ROOT
        
        config.setdefault("UI", {})[key] = value
        try:
            cfg_path = PROJECT_ROOT / "config" / "config.json"
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            logger.warning(f"Could not persist {key} to config.json: {e}")

    def add_pinned_menu_item(self, label: str, show_callback) -> None:
        """
        Add an entry to the Pinned menu.

        Args:
            label: Display name for the menu item.
            show_callback: Zero-argument callable that shows/focuses the target window.
        """
        if label in self._pinned_items:
            return  # Already pinned

        tag = dpg.generate_uuid()
        dpg.add_menu_item(
            label=label,
            callback=lambda *a: show_callback(),
            parent=self._pinned_menu_tag,
            tag=tag
        )
        self._pinned_items[label] = tag

        # Make the Pinned menu visible as soon as there is at least one entry
        dpg.configure_item(self._pinned_menu_tag, show=True)
        logger.debug(f"Pinned '{label}' to menu bar")

    def remove_pinned_menu_item(self, label: str) -> None:
        """
        Remove a previously pinned entry from the Pinned menu.

        Args:
            label: The label that was used when the item was added.
        """
        tag = self._pinned_items.pop(label, None)
        if tag is not None and dpg.does_item_exist(tag):
            dpg.delete_item(tag)
            logger.debug(f"Removed pinned item '{label}' from menu bar")

        # Hide the menu if it is now empty
        if not self._pinned_items:
            dpg.configure_item(self._pinned_menu_tag, show=False)

    def rename_pinned_menu_item(self, old_label: str, new_label: str) -> None:
        """
        Rename a pinned entry in the Pinned menu.

        Args:
            old_label: The previous label of the menu item.
            new_label: The new label of the menu item.
        """
        tag = self._pinned_items.pop(old_label, None)
        if tag is not None:
            self._pinned_items[new_label] = tag
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, label=new_label)
                logger.debug(f"Renamed pinned item '{old_label}' to '{new_label}' in menu bar")

    def _on_save_workspace(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """
        Save the current workspace layout to a JSON file.
        
        Opens a file dialog and exports all module positions, connections,
        and persistent fields to the selected file.
        """
        import json
        from core.paths import LAYOUTS_DIR
        
        path = file_explorer.save_file(
            default_path=str(LAYOUTS_DIR),
            default_name="manual_layout.json",
            extensions=[("JSON files", "*.json")]
        )
        if path:
            if not path.lower().endswith(".json"):
                path += ".json"
            node_positions = self.node_editor.get_node_positions()
            export_workspace(MODULES_REGISTRY, path, node_positions=node_positions)

            # Append native link nodes to the JSON
            link_nodes = self.node_editor.serialize_link_nodes()
            if link_nodes:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["link_nodes"] = link_nodes
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)

            logger.success(f"Pipeline saved to {path}")

    def _on_export_pipeline(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Alias for _on_save_workspace."""
        return self._on_save_workspace(sender, app_data, user_data, *args, **kwargs)

    def _on_save_clipboard(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """
        Export the current workspace to a JSON string and copy it to the clipboard.
        """
        import json
        node_positions = self.node_editor.get_node_positions()
        data = export_workspace(MODULES_REGISTRY, filepath=None, node_positions=node_positions)

        # Append native link nodes
        link_nodes = self.node_editor.serialize_link_nodes()
        if link_nodes:
            data["link_nodes"] = link_nodes

        json_str = json.dumps(data, indent=4)
        
        try:
            import pyperclip
            pyperclip.copy(json_str)
            logger.success("Workspace copied to clipboard!")
        except Exception as e:
            logger.warning(f"Could not copy workspace to clipboard: {e}")
            # Fallback: display JSON in a popup for manual copying
            popup_id = "clipboard_save_popup"
            if dpg.does_item_exist(popup_id):
                dpg.delete_item(popup_id)
            with dpg.window(label="Copy Layout to Clipboard", modal=True, show=True, tag=popup_id, width=600, height=400):
                dpg.add_text("System clipboard copy failed or 'pyperclip' missing. Please copy the JSON below manually:")
                dpg.add_input_text(multiline=True, width=-1, height=-40, default_value=json_str, readonly=True)
                dpg.add_button(label="Close", callback=lambda: dpg.delete_item(popup_id))
    
    def _on_load_workspace(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """
        Load a workspace layout from a JSON file.
        
        Opens a file dialog, clears the current workspace, and loads
        all modules and connections from the selected file.
        """
        from core.paths import LAYOUTS_DIR

        path = file_explorer.select_file(
            default_path=str(LAYOUTS_DIR), 
            extensions=[("JSON files", "*.json")]
        )
        
        if not path:
            return
            
        self.load_workspace_from_path(path)

    def _on_pipeline_editor(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Open the interactive Pipeline Editor tool."""
        from core.pipeline_editor import pipeline_editor
        pipeline_editor.show()

    def _on_automation_editor(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Open the interactive Automation Editor tool."""
        from core.automation_editor import automation_editor
        automation_editor.show()

    def load_workspace_from_path(self, path: str) -> None:
        """Load a workspace directly from a file path."""
        import json
        
        # Clear and reload
        self.node_editor.delete_all_nodes()
        load_workspace(path)
        self.node_editor.rebuild_from_instances(MODULES_REGISTRY)

        # Restore native link nodes
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            link_nodes = raw.get("link_nodes", [])
            if link_nodes:
                uuid_to_inst = {getattr(inst, 'UUID', None): inst for inst in MODULES_REGISTRY.values()}
                self.node_editor.rebuild_link_nodes(link_nodes, uuid_to_inst)
        except Exception as e:
            logger.warning(f"Could not restore link nodes: {e}")
        # Load views if they exist
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            views = raw.get("views", {})
            
            # Inject the default layout as a view
            views["Default"] = {
                "windows": raw.get("windows", []),
                "is_relative": raw.get("is_relative", False)
            }
            
            from core.module_registry import register_views
            register_views(views)
            self.refresh_view_menu()
            self.node_editor.refresh_view_menu()
        except Exception as e:
            logger.warning(f"Could not restore views: {e}")

    def refresh_view_menu(self) -> None:
        """Populate the Views submenu in Workspace with available views from the global registry."""
        from core.module_registry import get_available_views

        if not hasattr(self, "view_menu_tag") or not dpg.does_item_exist(self.view_menu_tag):
            return

        views = get_available_views()

        children = dpg.get_item_children(self.view_menu_tag, 1) or []
        for child in children:
            dpg.delete_item(child)

        if views:
            for view_name in views:
                dpg.add_menu_item(
                    label=view_name,
                    callback=self._on_view_changed,
                    user_data=view_name,
                    parent=self.view_menu_tag,
                )
            dpg.configure_item(self.view_menu_tag, show=True)
            if dpg.does_item_exist("main_win_views_separator"):
                dpg.configure_item("main_win_views_separator", show=True)
            logger.info(f"Loaded {len(views)} views into Main Window Views menu.")
        else:
            dpg.configure_item(self.view_menu_tag, show=False)
            if dpg.does_item_exist("main_win_views_separator"):
                dpg.configure_item("main_win_views_separator", show=False)

    def _on_view_changed(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Apply the selected view (called from Views menu item)."""
        view_name = user_data if user_data is not None else (args[0] if args else None)
        if not view_name:
            logger.warning("Could not determine view name in _on_view_changed")
            return
        from core.module_registry import apply_named_view

        apply_named_view(view_name)

    def _on_export_view(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """
        Open a dialog to export the current window layout as a view into an existing pipeline JSON file.

        Allows choosing the pipeline file (from layouts/ or custom file), typing a view name
        (e.g., 'Video', 'Figures'), and injects the view into the pipeline's 'views' dictionary.
        """
        import json
        from core.paths import LAYOUTS_DIR
        from core.module_registry import LAST_LOADED_WORKSPACE, export_view, AVAILABLE_VIEWS
        from config.display_scaling import display_scaling

        popup_id = "export_view_popup"
        if dpg.does_item_exist(popup_id):
            dpg.delete_item(popup_id)

        # Discover all layout files in LAYOUTS_DIR
        layout_files = sorted(
            [f for f in LAYOUTS_DIR.glob("*.json") if f.is_file()],
            key=lambda p: p.stem.lower()
        ) if LAYOUTS_DIR.exists() else []

        # Find initial selected file
        default_file = ""
        if LAST_LOADED_WORKSPACE and Path(LAST_LOADED_WORKSPACE).exists():
            default_file = str(Path(LAST_LOADED_WORKSPACE).resolve())
        elif layout_files:
            default_file = str(layout_files[0].resolve())

        # Map display name -> absolute path string
        file_map = {f.stem: str(f.resolve()) for f in layout_files}
        combo_items = list(file_map.keys())

        # Current selected stem
        initial_stem = Path(default_file).stem if default_file else (combo_items[0] if combo_items else "")

        path_input_tag = f"{popup_id}_path_input"
        view_name_tag = f"{popup_id}_name_input"
        existing_views_text_tag = f"{popup_id}_existing_views_text"
        combo_tag = f"{popup_id}_pipeline_combo"

        def _get_existing_views_for_path(filepath: str) -> list[str]:
            if not filepath or not Path(filepath).exists():
                return []
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return list(data.get("views", {}).keys())
            except Exception:
                return []

        def _update_existing_views_display(filepath: str) -> None:
            if not dpg.does_item_exist(existing_views_text_tag):
                return
            views = _get_existing_views_for_path(filepath)
            if views:
                dpg.set_value(existing_views_text_tag, f"Existing views in pipeline: {', '.join(views)}")
            else:
                dpg.set_value(existing_views_text_tag, "No views currently saved in this pipeline.")

        def _on_combo_select(sender=None, app_data=None, user_data=None, *args, **kwargs):
            selected_stem = app_data if app_data is not None else sender
            if selected_stem in file_map:
                new_path = file_map[selected_stem]
                dpg.set_value(path_input_tag, new_path)
                _update_existing_views_display(new_path)

        def _on_browse_file(sender=None, app_data=None, user_data=None, *args, **kwargs):
            chosen = file_explorer.select_file(
                default_path=str(LAYOUTS_DIR),
                extensions=[("JSON files", "*.json")]
            )
            if chosen:
                dpg.set_value(path_input_tag, chosen)
                _update_existing_views_display(chosen)
                stem = Path(chosen).stem
                if stem in combo_items and dpg.does_item_exist(combo_tag):
                    dpg.set_value(combo_tag, stem)

        def _on_save(sender=None, app_data=None, user_data=None, *args, **kwargs):
            target_path = dpg.get_value(path_input_tag).strip()
            view_name = dpg.get_value(view_name_tag).strip()

            if not target_path:
                logger.error("Please select a target pipeline file.")
                return

            if not Path(target_path).exists():
                logger.error(f"Target pipeline file '{target_path}' does not exist.")
                return

            if not view_name:
                logger.error("Please enter a name for the view.")
                return

            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    pipeline_data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read target pipeline JSON: {e}")
                return

            if not isinstance(pipeline_data, dict):
                logger.error("Invalid pipeline JSON structure (expected a JSON object).")
                return

            # Ensure "views" dict exists
            if "views" not in pipeline_data or not isinstance(pipeline_data["views"], dict):
                pipeline_data["views"] = {}

            # Generate view data from current visible windows
            view_data = export_view(is_relative=True)
            pipeline_data["views"][view_name] = view_data

            try:
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(pipeline_data, f, indent=4)
                logger.success(
                    f"View '{view_name}' saved into pipeline '{Path(target_path).name}' ({len(view_data.get('windows', []))} windows)"
                )
            except Exception as e:
                logger.error(f"Failed to write to pipeline file '{target_path}': {e}")
                return

            # Update in-memory views if target_path is the currently active workspace
            is_active_ws = False
            if LAST_LOADED_WORKSPACE:
                try:
                    is_active_ws = Path(LAST_LOADED_WORKSPACE).resolve() == Path(target_path).resolve()
                except Exception:
                    pass

            if is_active_ws:
                AVAILABLE_VIEWS[view_name] = view_data
                self.refresh_view_menu()
                self.node_editor.refresh_view_menu()

            if dpg.does_item_exist(popup_id):
                dpg.delete_item(popup_id)

        s = display_scaling.scale
        win_w = s(520)
        win_h = s(340)

        with dpg.window(label="Export View to Pipeline", modal=True, show=True,
                        tag=popup_id, width=win_w, height=win_h, no_resize=False):
            dpg.add_text("Save current window positions and sizes into a pipeline JSON.")
            dpg.add_separator()

            dpg.add_text("1. Select Pipeline:")
            if combo_items:
                dpg.add_combo(
                    items=combo_items,
                    default_value=initial_stem if initial_stem in combo_items else combo_items[0],
                    tag=combo_tag,
                    callback=_on_combo_select,
                    width=-1
                )

            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=path_input_tag,
                    default_value=default_file,
                    hint="Path to pipeline JSON file...",
                    width=-s(100),
                    callback=lambda *args: _update_existing_views_display(dpg.get_value(path_input_tag))
                )
                dpg.add_button(label="Browse...", callback=_on_browse_file, width=-1)

            # Existing views info
            init_views = _get_existing_views_for_path(default_file)
            init_text = f"Existing views in pipeline: {', '.join(init_views)}" if init_views else "No views currently saved in this pipeline."
            dpg.add_text(init_text, tag=existing_views_text_tag, color=(160, 160, 160), wrap=win_w - s(30))

            dpg.add_spacer(height=5)
            dpg.add_separator()
            dpg.add_spacer(height=5)

            dpg.add_text("2. View Name:")
            dpg.add_input_text(
                tag=view_name_tag,
                hint="e.g. Video, Figures, Compact...",
                default_value="",
                width=-1,
                on_enter=True,
                callback=_on_save
            )

            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save View", callback=_on_save, width=s(140))
                dpg.add_button(label="Cancel", callback=lambda *a: dpg.delete_item(popup_id), width=s(100))
    def _on_load_subpipeline(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """
        Load a sub-pipeline layout from a JSON file and append it to the current workspace.
        """
        # Calculate absolute path to layouts directory
        from core.paths import LAYOUTS_DIR
        
        path = file_explorer.select_file(
            default_path=str(LAYOUTS_DIR), 
            extensions=[("JSON files", "*.json")]
        )
        
        if not path:
            return
        
        # Load without clearing
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read sub-pipeline file {path}: {e}")
            return
            
        from core.module_registry import load_from_dict
        new_instances = load_from_dict(data, start_cleaned=False)
        self.node_editor.rebuild_from_instances(list(new_instances.values()))
        logger.info(f"Sub-pipeline loaded from {path}")
        
        # Track last loaded sub-pipeline
        import core.module_registry as _reg
        _reg.LAST_LOADED_SUBFLOW = path
        _reg.LAST_LOADED_SUBFLOW_DATA = data  # keep data for views
        self._update_reload_subpipeline_menu(path)
        
        
        # Load sub-pipeline views if they exist
        views = data.get("views", {})
        
        # Inject the default sub-pipeline layout as a view
        filename = Path(path).stem
        views[f"Default ({filename})"] = {
            "windows": data.get("windows", []),
            "is_relative": data.get("is_relative", False)
        }
        
        from core.module_registry import register_views
        register_views(views, merge=True)
        self.refresh_view_menu()
        self.node_editor.refresh_view_menu()

    def _on_reload_last_subpipeline(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """Reload the positions/visibility of the last loaded sub-pipeline."""
        import core.module_registry as _reg
        path = _reg.LAST_LOADED_SUBFLOW
        data = getattr(_reg, 'LAST_LOADED_SUBFLOW_DATA', None)
        if not path or not data:
            logger.warning("No sub-pipeline has been loaded yet.")
            return
        
        from core.module_registry import load_positions_from_dict
        load_positions_from_dict(data)
        logger.info(f"Reloaded positions from last sub-pipeline: {path}")

    def _update_reload_subpipeline_menu(self, path: Optional[str] = None) -> None:
        """Update the 'Reload Last Sub-pipeline' menu item in the NodeEditor."""
        tag = f"ne_reload_subpipeline_{self.node_editor.UUID}"
        if not dpg.does_item_exist(tag):
            return
        if path:
            filename = Path(path).name
            dpg.configure_item(tag, label=f"Reload Last Sub-pipeline ({filename})", enabled=True)
        else:
            dpg.configure_item(tag, label="Reload Last Sub-pipeline", enabled=False)

    def _on_clear_workspace(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """Clear the current workspace."""
        self.node_editor.delete_all_nodes()
        clear_registry()
        logger.success("Workspace cleared")
        self.refresh_view_menu()
        self.node_editor.refresh_view_menu()

    def _on_load_clipboard(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """
        Open a popup to paste layout JSON and load it.
        """
        popup_id = "clipboard_load_popup"
        text_input_id = f"{popup_id}_text"
        
        if dpg.does_item_exist(popup_id):
            dpg.delete_item(popup_id)
            
        with dpg.window(label="Load Layout from Clipboard", modal=True, show=True, tag=popup_id, width=600, height=400):
            dpg.add_text("Paste your layout JSON here:")
            dpg.add_input_text(tag=text_input_id, multiline=True, width=-1, height=-50)
            
            def _do_load(*args, **kwargs):
                import json
                from core.module_registry import load_from_dict, MODULES_REGISTRY
                
                content = dpg.get_value(text_input_id)
                try:
                    data = json.loads(content)
                    
                    # Clear and reload (assume clipboard load is full replacement like file load for now)
                    self.node_editor.delete_all_nodes()
                    load_from_dict(data)
                    self.node_editor.rebuild_from_instances(MODULES_REGISTRY)
                    logger.success("Workspace loaded from clipboard data")
                    
                    dpg.delete_item(popup_id)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {e}")
                except Exception as e:
                    logger.error(f"Error loading layout: {e}")

            with dpg.group(horizontal=True):
                dpg.add_button(label="Load", callback=_do_load, width=100)
                dpg.add_button(label="Cancel", callback=lambda *args: dpg.delete_item(popup_id), width=100)
    
    def _on_load_automation_script(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """Open a file dialog to select and run an automation script."""
        # Calculate absolute path to scripts directory
        from core.paths import SCRIPTS_DIR
        
        path = file_explorer.select_file(
            default_path=str(SCRIPTS_DIR),
            extensions=[("JSON files", "*.json")]
        )
        if path:
            from core.automation_manager import automation_manager
            automation_manager.run_script(path)

    def _check_for_missing_deps_after_load(self) -> None:
        """Check if any modules failed to load due to missing dependencies or validation."""
        from core.automation_manager import automation_manager
        if automation_manager.is_running:
            return

        from core.module_health_manager import module_health_manager
        
        if dependency_manager.missing_deps or module_validation_manager.validation_issues:
            # Check if any missing deps / validation issues actually relate to modules in the active workspace
            loaded_module_names = {
                inst.__class__.__module__.split('.')[-1] for inst in MODULES_REGISTRY.values()
            }
            has_workspace_dep_issue = any(
                mod_name in loaded_module_names for mod_name in dependency_manager.missing_deps
            )
            has_workspace_val_issue = any(
                Path(file_path).stem in loaded_module_names
                for file_path in module_validation_manager.validation_issues
            )

            if has_workspace_dep_issue or has_workspace_val_issue:
                logger.warning("Loaded workspace contains modules with health issues")
                module_health_manager.show_dialog_if_needed()
    
    def _on_check_dependencies(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args) -> None:
        """
        Manually trigger a full health check (dependencies + validation).
        
        Scans all modules for requirements.txt files, missing imports, and validation errors,
        then displays the unified health dialog if any issues are found.
        """
        from core.module_health_manager import module_health_manager

        # Clear previous detections and rescan from scratch
        dependency_manager.missing_deps.clear()
        module_validation_manager.validation_issues.clear()
        
        # Unified scan: triggers requirements.txt check, AST import analysis, and validation with force_reload=True
        logger.info("Scanning modules for dependencies and validation issues...")
        _ = get_available_modules('modules', force_reload=True)
        
        # Show unified health dialog
        module_health_manager.show_dialog_if_needed()

    def _on_adapt_display(self, sender=None, app_data=None, user_data=None, *args):
        """Open a popup to adjust UI scale multiplier and re-apply display scaling."""
        from config.display_scaling import display_scaling

        popup_id = "display_scaling_popup"
        slider_id = f"{popup_id}_slider"

        if dpg.does_item_exist(popup_id):
            dpg.delete_item(popup_id)

        def _apply(*args):
            new_mult = dpg.get_value(slider_id)
            display_scaling._user_multiplier = new_mult
            display_scaling.adapt_to_display()
            logger.info(f"Display scaling applied: multiplier={new_mult:.2f}, "
                        f"scale_factor={display_scaling.scale_factor:.2f}")
            dpg.delete_item(popup_id)

        s = display_scaling.scale
        with dpg.window(label="Display Scaling", modal=True, show=True,
                        tag=popup_id, width=s(350), height=s(200)):
            dpg.add_text(f"Screen: {display_scaling.screen_width}x{display_scaling.screen_height}")
            dpg.add_text(f"Curr. scale factor: {display_scaling.scale_factor:.2f}")
            dpg.add_drag_float(
                tag=slider_id,
                default_value=display_scaling._user_multiplier,
                min_value=0.25, max_value=2.0,
                speed=0.05,
                width=-1
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="Apply", callback=_apply, width=s(150))
                dpg.add_button(label="Cancel",
                               callback=lambda *a: dpg.delete_item(popup_id), width=s(150))

    def _on_minimize_to_tray(self, sender=None, app_data=None, user_data=None, *args):
        """Minimize the application to the system tray."""
        try:
            from core.system_tray import system_tray
            if system_tray and system_tray.is_running:
                system_tray.minimize_to_tray()
        except (ImportError, AttributeError) as e:
            logger.warning(f"System tray not available: {e}")

    def _on_show_logs(self, sender, app_data, user_data, *args):
        """Show the log viewer."""
        from core.log_viewer import log_viewer
        log_viewer.show()

    def _on_edit_config(self, sender=None, app_data=None, user_data=None, *args):
        """Open the Visual Config Editor window."""
        from core.config_editor import config_editor
        config_editor.show()

    def _on_install_module(self, sender=None, app_data=None, user_data=None, *args):
        """Open the Module Installer window."""
        self.module_manager.show_install()

    def _on_uninstall_module(self, sender=None, app_data=None, user_data=None, *args):
        """Open the Module Uninstaller window."""
        self.module_manager.show_uninstall()

fusion_manager: FusionManager = FusionManager()
node_editor: NodeEditor = NodeEditor()
main_win: MainWin = MainWin(node_editor=node_editor)
