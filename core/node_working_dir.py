from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from core.file_explorer import file_explorer
from core.input_output_types import IOTypes
from core.working_directory_manager import working_directory_manager


class _WorkingDirNode:
    """
    Built-in Working Directory node proxy.
    Automatically emits the global workspace directory whenever it is set or changed
    (via MainWin, AutomationManager, scripted steps, or directly on the node).
    """

    KIND: str = "working_dir"

    def __init__(
        self,
        label: str = "Working Directory",
        uuid: Optional[str] = None,
        auto_emit: bool = True,
    ) -> None:
        self.label: str = label or "Working Directory"
        self.UUID: str = uuid or str(dpg.generate_uuid())
        self.auto_emit: bool = bool(auto_emit)
        self.accepted_input_types: List[IOTypes] = [
            IOTypes.FOLDER_PATH,
            IOTypes.TRIGGER,
            IOTypes.CMD_DICT,
            IOTypes.ANY,
        ]
        self.outputs: Dict[str, IOTypes] = {
            "Folder": IOTypes.FOLDER_PATH,
            "Trigger": IOTypes.TRIGGER,
        }
        self.connections: Dict[str, List[Any]] = {
            "Folder": [],
            "Trigger": [],
        }

        # UI Tags
        self._dir_text_tag: str = f"_wd_path_{self.UUID}"
        self.node_id: Optional[int] = None
        self.editor: Optional[Any] = None

        # Register with singleton WorkingDirectoryManager
        working_directory_manager.add_listener(self._on_global_directory_changed)

    def _format_display_path(self, path: Union[str, Path], max_chars: int = 24) -> str:
        p_str = str(path)
        if len(p_str) <= max_chars:
            return p_str
        return f"...{p_str[-(max_chars - 3):]}"

    def _on_global_directory_changed(self, new_path: Path) -> None:
        """Called when working_directory_manager sets a new directory."""
        self._update_ui_text(new_path)
        self.emit_directory(new_path)

    def _update_ui_text(self, path: Optional[Path] = None) -> None:
        target = path or working_directory_manager.get_directory()
        if dpg.does_item_exist(self._dir_text_tag):
            try:
                dpg.set_value(self._dir_text_tag, self._format_display_path(target))
            except Exception:
                pass

    def emit_directory(self, path: Optional[Path] = None) -> None:
        """Emit folder path and trigger to downstream connections."""
        target_dir = str(path or working_directory_manager.get_directory())

        for module in list(self.connections.get("Folder", [])):
            try:
                module.input_cb(data=target_dir, data_type=IOTypes.FOLDER_PATH)
            except Exception as e:
                logger.error(f"WorkingDirNode '{self.label}': error emitting folder to {module} - {e}")

        for module in list(self.connections.get("Trigger", [])):
            try:
                module.input_cb(data="TRIGGER", data_type=IOTypes.TRIGGER)
            except Exception as e:
                logger.error(f"WorkingDirNode '{self.label}': error emitting trigger to {module} - {e}")

    def on_browse_clicked(self) -> None:
        """Opens folder dialog and sets the global working directory."""
        current = str(working_directory_manager.get_directory())
        chosen = file_explorer.select_folder(default_path=current)
        if chosen:
            working_directory_manager.set_directory(chosen)

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Receives input from upstream modules.
        - FOLDER_PATH / string: updates the global working directory (and broadcasts).
        - TRIGGER: re-emits the current working directory.
        - CMD_DICT: handles commands like {'browse': True}, {'path': '...'}, {'trigger': True}.
        """
        data = kwargs.get("data") if kwargs.get("data") is not None else (args[0] if args else None)
        data_type = kwargs.get("data_type")

        # 1. Direct folder kwargs
        direct_folder = kwargs.get("folder_path") or kwargs.get("folder") or kwargs.get("path")
        if direct_folder and isinstance(direct_folder, (str, Path)):
            working_directory_manager.set_directory(direct_folder)
            return

        # 2. Commands
        if isinstance(data, dict) or data_type == IOTypes.CMD_DICT:
            cmd = data if isinstance(data, dict) else {}
            if cmd.get("browse"):
                self.on_browse_clicked()
                return
            if cmd.get("trigger") or cmd.get("resend") or cmd.get("emit"):
                self.emit_directory()
                return
            p = cmd.get("path") or cmd.get("folder") or cmd.get("folder_path") or cmd.get("directory") or cmd.get("working_dir")
            if p:
                working_directory_manager.set_directory(p)
                return

        # 3. Trigger -> Resend
        if data_type == IOTypes.TRIGGER or (isinstance(data, str) and data.upper() in ("TRIGGER", "START", "RUN", "RESEND")):
            self.emit_directory()
            return

        # 4. Folder path
        if data_type == IOTypes.FOLDER_PATH:
            if isinstance(data, (str, Path)):
                working_directory_manager.set_directory(data)
                return
            elif isinstance(data, (tuple, list)) and len(data) > 0 and isinstance(data[0], (str, Path)):
                working_directory_manager.set_directory(data[0])
                return
            elif isinstance(data, dict):
                p = data.get("folder_path") or data.get("folder") or data.get("path")
                if p:
                    working_directory_manager.set_directory(p)
                    return

        if isinstance(data, (str, Path)) and (os.path.isdir(str(data)) or data_type == IOTypes.FOLDER_PATH):
            working_directory_manager.set_directory(data)
            return

        if isinstance(data, (tuple, list)) and len(data) > 0 and isinstance(data[0], (str, Path)):
            if os.path.isdir(str(data[0])):
                working_directory_manager.set_directory(data[0])
                return

    def serialize(self) -> Dict[str, Any]:
        return {
            "kind": self.KIND,
            "uuid": self.UUID,
            "label": self.label,
            "auto_emit": self.auto_emit,
        }

    def close(self) -> None:
        working_directory_manager.remove_listener(self._on_global_directory_changed)
        self.connections.get("Folder", []).clear()
        self.connections.get("Trigger", []).clear()
