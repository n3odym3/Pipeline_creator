"""
Global session state for Pipeline Creator.
Stores user session metadata.
"""

from __future__ import annotations
from dataclasses import dataclass

@dataclass
class AppState:
    """Flat session-state container for user session data."""
    username: str = ""
    mode: str = "dev"
    login_done: bool = False
    close_requested: bool = False


# Global singleton instance
app_state: AppState = AppState()
