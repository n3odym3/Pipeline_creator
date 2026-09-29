from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

from loguru import logger

from core.config_manager import config
from core.file_explorer import file_explorer


class WorkingDirectoryManager:
    """
    Singleton class to manage the global working directory.
    Ensures that os.getcwd() is consistent with the user's selection.
    """

    _instance: Optional[WorkingDirectoryManager] = None
    current_directory: Path
    _listeners: List[Callable[[Path], None]]

    def __new__(cls) -> WorkingDirectoryManager:
        if cls._instance is None:
            cls._instance = super(WorkingDirectoryManager, cls).__new__(cls)
            cls._instance.current_directory = Path.cwd()
            cls._instance._listeners = []

        return cls._instance

    def add_listener(self, callback: Callable[[Path], None]) -> None:
        """Register a callback to be called whenever the working directory changes."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Path], None]) -> None:
        """Unregister a working directory change callback."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def notify_listeners(self, path: Optional[Path] = None) -> None:
        """Notify all listeners of the current or given working directory."""
        target_path = path or self.current_directory
        for listener in list(self._listeners):
            try:
                listener(target_path)
            except Exception as e:
                logger.error(f"Error in working directory listener: {e}")

    def apply_default_config(self) -> None:
        """
        Apply the default working directory from the configuration file.
        Reads from Paths.working_directory (fallback: General.working_directory).
        If the value is null/None or empty, do nothing.
        Supports both relative (to project root) and absolute paths.
        """
        try:
            default_wd = config.get("Paths", {}).get("working_directory", None)
            if default_wd is None:
                default_wd = config.get("General", {}).get("working_directory", None)

            if not default_wd:
                return

            clean_wd = default_wd.lstrip("/\\")
            default_path = Path(clean_wd)

            if not default_path.is_absolute():
                from core.paths import PROJECT_ROOT

                default_path = PROJECT_ROOT / default_path

            if default_path.exists():
                self.set_directory(default_path)
            else:
                logger.warning(f"Working directory not found: {default_path}")
        except Exception as e:
            logger.error(f"Failed to apply default working directory config: {e}")

    def set_directory(self, path: Union[str, Path]) -> None:
        """
        Set the global working directory and notify all listeners.

        Args:
            path: The new directory path.
        """
        try:
            new_path = Path(path).resolve()
            if not new_path.exists():
                try:
                    new_path.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    logger.warning(f"Could not create directory {new_path}: {e}")

            if not new_path.is_dir():
                logger.error(f"Invalid directory: {new_path}")
                return

            os.chdir(new_path)
            self.current_directory = new_path
            logger.info(f"Working directory changed to: {self.current_directory}")
            self.notify_listeners(new_path)
        except Exception as e:
            logger.error(f"Failed to set working directory: {e}")

    def get_directory(self) -> Path:
        """
        Get the current working directory.

        Returns:
            Path: The current working directory.
        """
        return self.current_directory

    def select_directory(self, *args: Any, **kwargs: Any) -> None:
        """
        Open a folder selection dialog and set the working directory.
        """
        selected_path = file_explorer.select_folder(default_path=str(self.current_directory))
        if selected_path:
            self.set_directory(selected_path)


working_directory_manager: WorkingDirectoryManager = WorkingDirectoryManager()

