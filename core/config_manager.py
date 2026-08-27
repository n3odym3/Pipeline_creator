"""
Global Configuration Manager for Pipeline Creator.

Loads, manages, and persists application configuration settings from config.json.
Provides dictionary-compatible access along with helper methods.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from core.paths import CONFIG_DIR


class ConfigManager(dict[str, Any]):
    """
    Configuration manager.
    Inherits from dict for transparent backwards compatibility with config["key"].
    """

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path: Path = config_path
        self.load()

    def load(self) -> None:
        """Load or reload configuration settings from disk."""
        try:
            if self.config_path.is_file():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.clear()
                    if isinstance(data, dict):
                        self.update(data)
            else:
                logger.warning(f"Config file not found at {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load config from {self.config_path}: {e}")

    def save(self) -> bool:
        """Persist in-memory configuration back to disk."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self, f, indent=4)
            logger.success(f"Configuration saved to {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save config to {self.config_path}: {e}")
            return False

    def get_nested(self, *keys: str, default: Any = None) -> Any:
        """
        Safely fetch a deeply nested configuration value.
        """
        current: Any = self
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current


# Global singleton configuration object
config: ConfigManager = ConfigManager(CONFIG_DIR / "config.json")

