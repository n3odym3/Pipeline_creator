"""
Global session state for Pipeline Creator.
Stores user session metadata.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class AppState:
    """Flat session-state container for user session data."""
    username: str = ""
    mode: str = "dev"
    login_done: bool = False
    close_requested: bool = False

    def get(self, key: str, default: Any = None) -> Any:
        """Safely retrieve an attribute with an optional fallback default."""
        return getattr(self, key, default)


# Global singleton instance
app_state: AppState = AppState()
