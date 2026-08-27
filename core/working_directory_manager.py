from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Union

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

    def __new__(cls) -> WorkingDirectoryManager:
        if cls._instance is None:
            cls._instance = super(WorkingDirectoryManager, cls).__new__(cls)
            cls._instance.current_directory = Path.cwd()

        return cls._instance

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
        Set the global working directory.

        Args:
            path: The new directory path.
        """
        try:
            new_path = Path(path).resolve()
            if not new_path.is_dir():
                logger.error(f"Invalid directory: {new_path}")
                return

            os.chdir(new_path)
            self.current_directory = new_path
            logger.debug(f"Working directory changed to: {self.current_directory}")
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

