"""
Session tracking in ``logs/app.log`` (no DearPyGui dependency, safe to import
before the DPG context exists).

A start marker is written at boot; the normal shutdown logs
"Pipeline Creator closed successfully". A session with a start marker but no
clean-exit line is considered a crash.
"""

from __future__ import annotations

import os
from typing import List, Optional

from loguru import logger

from core.paths import LOGS_DIR

SESSION_START_MARKER = "=== SESSION START ==="
CLEAN_EXIT_MARKER = "Pipeline Creator closed successfully"
APP_LOG = LOGS_DIR / "app.log"
_MAX_LOG_BYTES = 16 * 1024 * 1024


def mark_session_start() -> None:
    """Write the session start marker in the log (call once at boot)."""
    logger.info(f"{SESSION_START_MARKER} pid={os.getpid()}")


def _read_log_lines() -> List[str]:
    if not APP_LOG.is_file():
        return []
    try:
        with open(APP_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _MAX_LOG_BYTES))
            data = f.read()
        return data.decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        logger.warning(f"Could not read log file: {e}")
        return []


def _split_sessions(lines: List[str]) -> List[List[str]]:
    starts = [i for i, line in enumerate(lines) if SESSION_START_MARKER in line]
    return [lines[a:b] for a, b in zip(starts, starts[1:] + [len(lines)])]


def previous_session_crashed() -> Optional[bool]:
    """
    Whether the last recorded session ended abnormally.

    Must be called BEFORE :func:`mark_session_start`. Returns None when
    undetermined (no log / no marker yet), True for a crash, False otherwise.
    """
    sessions = _split_sessions(_read_log_lines())
    if not sessions:
        return None
    return not any(CLEAN_EXIT_MARKER in line for line in sessions[-1])


def get_session_log(previous: bool = False) -> List[str]:
    """Return the log lines of the current (or previous) session."""
    sessions = _split_sessions(_read_log_lines())
    idx = -2 if previous else -1
    if len(sessions) < abs(idx):
        return []
    return sessions[idx]


def session_date(lines: List[str]) -> str:
    return lines[0][:19] if lines else "unknown"
