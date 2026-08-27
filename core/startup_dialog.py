"""
Startup Loader Dialog for Pipeline Creator.

Shown at startup when config["Paths"]["show_loader_on_startup"] is True
and no default pipeline or automation script was loaded.

Encapsulated within StartupDialog class for clean component architecture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import dearpygui.dearpygui as dpg
from loguru import logger

from core.config_manager import config
from core.file_explorer import file_explorer
from core.paths import PROJECT_ROOT

if TYPE_CHECKING:
    from core.main_win import MainWin


class StartupDialog:
    """
    Startup Loader Dialog component class.

    Presents a centered modal dialog at boot allowing the user to select
    and load an Automation Script or a Pipeline / Layout.
    """

    MODE_SCRIPT = "Automation Script"
    MODE_PIPELINE = "Pipeline / Layout"

    def __init__(self, main_win: MainWin) -> None:
        self.main_win: MainWin = main_win
        self.winID: str = "startup_dialog_win"
        self._radio_tag: str = "startup_dialog_radio"
        self._listbox_tag: str = "startup_dialog_listbox"
        self._error_text_tag: str = "startup_dialog_error_text"

        self.selected_file: Optional[Path] = None
        self.file_maps: Dict[str, Dict[str, Path]] = {
            self.MODE_SCRIPT: {},
            self.MODE_PIPELINE: {},
        }

    def show(self, pump_frames: bool = False) -> None:
        """
        Display the startup dialog centered on screen.

        Args:
            pump_frames: If True, blocks and manually pumps DPG frames until
                         the window is closed (used before main loop starts).
        """
        try:
            from config.display_scaling import display_scaling

            display_scaling.adapt_to_display()
        except Exception as exc:
            logger.warning(f"StartupDialog: display_scaling failed: {exc}")

        self._build_dialog()

        if pump_frames:
            while dpg.is_dearpygui_running() and dpg.does_item_exist(self.winID):
                vp_w = dpg.get_viewport_client_width()
                vp_h = dpg.get_viewport_client_height()
                win_size = dpg.get_item_rect_size(self.winID)
                if win_size[0] > 0 and win_size[1] > 0:
                    pos_x = max(0, (vp_w - win_size[0]) // 2)
                    pos_y = max(0, (vp_h - win_size[1]) // 2)
                    dpg.set_item_pos(self.winID, [pos_x, pos_y])

                from core.automation_manager import automation_manager

                automation_manager.process_pending_steps()
                dpg.render_dearpygui_frame()

            for _ in range(2):
                if dpg.is_dearpygui_running():
                    dpg.render_dearpygui_frame()

    def _resolve_folder_path(self, folder_key: str, default_name: str) -> Path:
        """Resolve configured folder path relative to PROJECT_ROOT if needed."""
        paths_cfg = config.get("Paths", {})
        raw_folder = paths_cfg.get(folder_key, default_name) or default_name
        folder_path = Path(raw_folder.lstrip("/\\"))
        if not folder_path.is_absolute():
            folder_path = PROJECT_ROOT / folder_path
        return folder_path

    def _scan_folder_json_files(self, folder_path: Path) -> Dict[str, Path]:
        """Scan folder for .json files and return mapping of display labels to file paths."""
        files_map: Dict[str, Path] = {}
        if not folder_path.exists() or not folder_path.is_dir():
            logger.warning(f"Startup loader directory does not exist: {folder_path}")
            return files_map

        for file_path in sorted(folder_path.glob("*.json"), key=lambda p: p.name.lower()):
            files_map[file_path.name] = file_path

        return files_map

    def _refresh_file_list(self) -> None:
        selected_mode = dpg.get_value(self._radio_tag)
        folder_key = "default_pipeline_folder" if selected_mode == self.MODE_PIPELINE else "default_scripts_folder"
        default_folder_name = "layouts" if selected_mode == self.MODE_PIPELINE else "scripts"
        folder_path = self._resolve_folder_path(folder_key, default_folder_name)

        file_map = self._scan_folder_json_files(folder_path)
        self.file_maps[selected_mode] = file_map

        items = list(file_map.keys())
        if items:
            dpg.configure_item(self._listbox_tag, items=items, num_items=min(8, max(4, len(items))))
            dpg.set_value(self._listbox_tag, items[0])
            self._on_file_selected(None, items[0], None)
        else:
            dpg.configure_item(self._listbox_tag, items=["(No files found)"], num_items=4)
            dpg.set_value(self._listbox_tag, "(No files found)")
            self.selected_file = None

    def _on_mode_change(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if dpg.does_item_exist(self._error_text_tag):
            dpg.configure_item(self._error_text_tag, show=False)
        self._refresh_file_list()

    def _on_file_selected(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if dpg.does_item_exist(self._error_text_tag):
            dpg.configure_item(self._error_text_tag, show=False)
        selected_mode = dpg.get_value(self._radio_tag)
        file_map = self.file_maps.get(selected_mode, {})
        file_name = dpg.get_value(self._listbox_tag)

        if file_name in file_map:
            file_path = file_map[file_name]
            self.selected_file = file_path
        else:
            self.selected_file = None

    def _on_browse(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        selected_mode = dpg.get_value(self._radio_tag)
        folder_key = "default_pipeline_folder" if selected_mode == self.MODE_PIPELINE else "default_scripts_folder"
        default_folder_name = "layouts" if selected_mode == self.MODE_PIPELINE else "scripts"
        folder_path = self._resolve_folder_path(folder_key, default_folder_name)

        path_str = file_explorer.select_file(
            default_path=str(folder_path), extensions=[("JSON files", "*.json")]
        )
        if path_str:
            custom_path = Path(path_str)
            self.selected_file = custom_path

    def _on_confirm(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        selected_path = self.selected_file
        if not selected_path or not selected_path.exists():
            if dpg.does_item_exist(self._error_text_tag):
                dpg.configure_item(self._error_text_tag, show=True)
            return

        selected_mode = dpg.get_value(self._radio_tag)
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

        if selected_mode == self.MODE_PIPELINE:
            logger.info(f"Startup loader: loading pipeline '{selected_path}'")
            self.main_win.load_workspace_from_path(str(selected_path))
        else:
            logger.info(f"Startup loader: running automation script '{selected_path}'")
            from core.automation_manager import automation_manager

            automation_manager.set_script_path(str(selected_path))
            automation_manager.run()

    def _on_skip(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)
        logger.info("Startup loader: skipped, continuing with blank workspace")

    def _build_dialog(self) -> None:
        """Create the DPG startup loader window."""
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

        from config.display_scaling import display_scaling

        s = display_scaling.scale

        vp_w = dpg.get_viewport_width()
        vp_h = dpg.get_viewport_height()
        win_w = s(520)
        win_h = s(420)
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        with dpg.window(
            label="Startup - Load Pipeline / Script",
            tag=self.winID,
            modal=True,
            no_close=True,
            no_resize=False,
            no_move=False,
            autosize=True,
            pos=[pos_x, pos_y],
        ):
            dpg.add_spacer(height=s(6))
            dpg.add_text("Choose a Script or Pipeline / Layout to load on startup:", color=(180, 180, 200, 255))
            dpg.add_separator()
            dpg.add_spacer(height=s(8))

            dpg.add_text("Configuration Type:")
            dpg.add_radio_button(
                tag=self._radio_tag,
                items=[self.MODE_SCRIPT, self.MODE_PIPELINE],
                default_value=self.MODE_SCRIPT,
                horizontal=True,
                callback=self._on_mode_change,
            )

            dpg.add_spacer(height=s(8))

            with dpg.group(horizontal=True):
                dpg.add_text("Available Files:")
                dpg.add_spacer(width=s(180))
                dpg.add_button(label="Browse...", callback=self._on_browse)

            dpg.add_listbox(
                tag=self._listbox_tag,
                items=[],
                width=win_w - s(30),
                num_items=6,
                callback=self._on_file_selected,
            )

            dpg.add_spacer(height=s(4))

            dpg.add_text(
                "Please select a valid .json file to load.",
                tag=self._error_text_tag,
                color=(255, 100, 100, 255),
                show=False,
            )

            dpg.add_spacer(height=s(12))
            dpg.add_separator()
            dpg.add_spacer(height=s(8))

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Load Selected",
                    callback=self._on_confirm,
                    width=s(230),
                )
                dpg.add_button(
                    label="Start Empty Workspace",
                    callback=self._on_skip,
                    width=s(250),
                )

        self._refresh_file_list()


def show_startup_dialog(main_win: MainWin, pump_frames: bool = False) -> None:
    """Helper function to instantiate and show StartupDialog."""
    dialog = StartupDialog(main_win)
    dialog.show(pump_frames=pump_frames)

