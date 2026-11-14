"""
Global session state for Pipeline Creator.
Stores user session metadata.
"""

from __future__ import annotations
from dataclasses import dataclass
import sys
from typing import Any


@dataclass
class AppState:
    """Flat session-state container for user session data."""
    username: str = ""
    mode: str = "dev"
    login_done: bool = False
    close_requested: bool = False
    headless: bool = False
    gui_running: bool = False
    exit_on_script_complete: bool = False

    def get(self, key: str, default: Any = None) -> Any:
        """Safely retrieve an attribute with an optional fallback default."""
        return getattr(self, key, default)

    def is_non_interactive(self) -> bool:
        """Return True in headless mode or while an automation script is active/queued."""
        if self.headless or "--script" in sys.argv:
            return True
        from core.automation_manager import automation_manager

        return bool(automation_manager.is_running or automation_manager.script_path)


# Global singleton instance
app_state: AppState = AppState()
