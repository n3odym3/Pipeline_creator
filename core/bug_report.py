"""
Bug / crash reporting for Pipeline Creator.

Provides:
- Session tracking inside ``logs/app.log`` (a start marker is written at boot,
  the normal shutdown writes "Pipeline Creator closed successfully"). A session
  with a start marker but no clean-exit line is considered a crash.
- A "Bug report" window (Help menu): description, optional module selection,
  log attachment (current or previous session), file attachments and clipboard
  image paste (Ctrl+V / button).
- A "something went wrong" popup shown at boot when the previous session did
  not end cleanly (can be disabled with ``General.check_crash_on_boot``).

Each report is stored in ``logs/crash_report/<timestamp>/``.

Note: DearPyGui has no native file drag & drop support, so files are attached
through the file browser button or by pasting (Ctrl+V) files/images copied to
the clipboard (requires Pillow for clipboard access).
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import dearpygui.dearpygui as dpg
from loguru import logger

from core.crash_detection import get_session_log, session_date as _session_date
from core.paths import LOGS_DIR

def _scale():
    from config.display_scaling import display_scaling

    return display_scaling.scale


REPORTS_DIR = LOGS_DIR / "crash_report"


# ----------------------------------------------------------------------
# Clipboard helpers
# ----------------------------------------------------------------------


def _grab_clipboard() -> List[Path]:
    """Return files from the clipboard (image saved to temp, or copied files)."""
    try:
        from PIL import Image, ImageGrab
    except Exception:
        logger.warning("Pillow is required for clipboard paste")
        return []
    try:
        content = ImageGrab.grabclipboard()
    except Exception as e:
        logger.debug(f"Clipboard read failed: {e}")
        return []
    if content is None:
        return []
    if isinstance(content, Image.Image):
        tmp = Path(tempfile.mkdtemp(prefix="bugreport_clip_"))
        name = f"clipboard_{datetime.datetime.now():%H%M%S}.png"
        path = tmp / name
        content.save(path)
        return [path]
    if isinstance(content, list):
        return [Path(p) for p in content if Path(p).is_file()]
    return []


# ----------------------------------------------------------------------
# Bug report window
# ----------------------------------------------------------------------


class BugReportWindow:
    """Bug report dialog and report writer."""

    WIN = "bug_report_win"
    HANDLERS = "bug_report_handlers"

    def __init__(self) -> None:
        self._attachments: List[Path] = []
        self._selected_modules: set[str] = set()

    # -- public ---------------------------------------------------------

    def show(self, crash: bool = False) -> None:
        """Open the window. ``crash=True`` pre-fills it for a crash report."""
        if dpg.does_item_exist(self.WIN):
            dpg.focus_item(self.WIN)
            return

        self._attachments = []
        self._selected_modules = set()
        s = _scale()
        w, h = s(640), s(720)
        vp_w, vp_h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()

        has_prev = bool(get_session_log(previous=True))

        with dpg.window(
            label="Bug report",
            tag=self.WIN,
            width=w,
            height=h,
            pos=[max(0, (vp_w - w) // 2), max(0, (vp_h - h) // 2)],
            on_close=self._close,
        ):
            dpg.add_text("Describe the problem (what you did, what happened):")
            dpg.add_input_text(
                tag="bug_report_desc",
                multiline=True,
                width=-1,
                height=s(150),
                default_value=(
                    "The application crashed.\n\n" if crash else ""
                ),
            )

            with dpg.group(horizontal=True):
                dpg.add_text("Type:")
                dpg.add_combo(
                    ["Bug", "Crash", "Suggestion"],
                    default_value="Crash" if crash else "Bug",
                    width=s(120),
                    tag="bug_report_type",
                )

            dpg.add_separator()
            dpg.add_checkbox(label="Attach log", tag="bug_report_attach_log", default_value=True)
            with dpg.group(horizontal=True, indent=s(20)):
                dpg.add_radio_button(
                    ["Current session", "Previous session"],
                    horizontal=True,
                    tag="bug_report_log_which",
                    default_value="Previous session" if (crash and has_prev) else "Current session",
                )

            dpg.add_separator()
            dpg.add_text("Related modules (optional):")
            dpg.add_input_text(
                hint="Search modules...",
                width=-1,
                callback=lambda s_, a, u: dpg.set_value("bug_report_filter", a),
            )
            with dpg.child_window(height=s(130), border=True):
                with dpg.filter_set(tag="bug_report_filter"):
                    for name in self._available_module_names():
                        dpg.add_checkbox(
                            label=name,
                            filter_key=name,
                            callback=self._on_module_toggle,
                            user_data=name,
                        )

            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_text("Attachments (images, text files...):")
                dpg.add_button(label="Browse...", callback=self._on_browse)
                dpg.add_button(label="Paste (Ctrl+V)", callback=self._on_paste)
            with dpg.child_window(tag="bug_report_attach_list", height=s(90), border=True):
                pass
            self._refresh_attachments()

            dpg.add_separator()
            dpg.add_text("", tag="bug_report_status", wrap=w - s(30))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Send report", tag="bug_report_submit", callback=self._on_submit)
                dpg.add_button(label="Cancel", callback=self._close)

        with dpg.handler_registry(tag=self.HANDLERS):
            dpg.add_key_press_handler(dpg.mvKey_V, callback=self._on_key_v)

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _available_module_names() -> List[str]:
        try:
            from core.module_registry import get_available_modules

            return sorted(get_available_modules().keys())
        except Exception as e:
            logger.warning(f"Bug report: cannot list modules: {e}")
            return []

    @staticmethod
    def _ctrl_down() -> bool:
        for attr in ("mvKey_ModCtrl", "mvKey_Control", "mvKey_LControl", "mvKey_RControl"):
            key = getattr(dpg, attr, None)
            if key is not None and dpg.is_key_down(key):
                return True
        return False

    def _status(self, msg: str, color=(200, 200, 200)) -> None:
        if dpg.does_item_exist("bug_report_status"):
            dpg.set_value("bug_report_status", msg)
            dpg.configure_item("bug_report_status", color=color)

    def _close(self, *args: Any) -> None:
        for tag in (self.WIN, self.HANDLERS):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)

    def _on_module_toggle(self, sender, app_data, user_data) -> None:
        (self._selected_modules.add if app_data else self._selected_modules.discard)(user_data)

    # -- attachments ----------------------------------------------------

    def _add_attachments(self, paths: List[Path]) -> None:
        for p in paths:
            if p not in self._attachments:
                self._attachments.append(p)
        self._refresh_attachments()

    def _refresh_attachments(self) -> None:
        if not dpg.does_item_exist("bug_report_attach_list"):
            return
        dpg.delete_item("bug_report_attach_list", children_only=True)
        if not self._attachments:
            dpg.add_text("No attachment", parent="bug_report_attach_list", color=(140, 140, 140))
        for p in self._attachments:
            with dpg.group(horizontal=True, parent="bug_report_attach_list"):
                dpg.add_button(label="X", callback=self._on_remove, user_data=p)
                dpg.add_text(p.name)

    def _on_remove(self, sender, app_data, user_data) -> None:
        if user_data in self._attachments:
            self._attachments.remove(user_data)
        self._refresh_attachments()

    def _on_browse(self, *args: Any) -> None:
        from core.file_explorer import file_explorer

        def _picked(path: str, ext: str = "") -> None:
            if path:
                self._add_attachments([Path(path)])

        file_explorer.select_file(callback=_picked)

    def _on_paste(self, *args: Any) -> None:
        files = _grab_clipboard()
        if files:
            self._add_attachments(files)
            self._status(f"{len(files)} item(s) pasted from clipboard.")
        else:
            self._status("No image or file found in the clipboard.", (230, 180, 80))

    def _on_key_v(self, *args: Any) -> None:
        if not dpg.does_item_exist(self.WIN) or not self._ctrl_down():
            return
        if dpg.is_item_focused(self.WIN) or dpg.is_item_hovered(self.WIN):
            files = _grab_clipboard()  # text clipboard -> empty -> ignored
            if files:
                self._add_attachments(files)
                self._status(f"{len(files)} item(s) pasted from clipboard.")

    # -- submit ---------------------------------------------------------

    def _on_submit(self, *args: Any) -> None:
        desc = dpg.get_value("bug_report_desc").strip()
        if not desc:
            self._status("Please describe the problem first.", (230, 120, 120))
            return
        try:
            folder = self._write_report(desc)
        except Exception as e:
            logger.exception(f"Failed to write bug report: {e}")
            self._status(f"Failed to save report: {e}", (230, 120, 120))
            return
        logger.info(f"Bug report saved: {folder}")
        self._close()
        self._show_done(folder)

    def _write_report(self, desc: str) -> Path:
        from core.app_state import app_state
        from core.module_registry import MODULES_REGISTRY

        now = datetime.datetime.now()
        folder = REPORTS_DIR / f"{now:%Y%m%d_%H%M%S}"
        n = 1
        while folder.exists():
            n += 1
            folder = REPORTS_DIR / f"{now:%Y%m%d_%H%M%S}_{n}"
        folder.mkdir(parents=True)

        (folder / "description.txt").write_text(desc, encoding="utf-8")

        log_file: Optional[str] = None
        log_session = None
        if dpg.get_value("bug_report_attach_log"):
            previous = dpg.get_value("bug_report_log_which") == "Previous session"
            lines = get_session_log(previous=previous)
            if lines:
                (folder / "session.log").write_text("\n".join(lines), encoding="utf-8")
                log_file = "session.log"
                log_session = {"which": "previous" if previous else "current", "started": _session_date(lines)}
            else:
                logger.warning("Bug report: no session log found to attach")

        attached: List[str] = []
        if self._attachments:
            att_dir = folder / "attachments"
            att_dir.mkdir()
            for src in self._attachments:
                try:
                    dst = att_dir / src.name
                    i = 1
                    while dst.exists():
                        i += 1
                        dst = att_dir / f"{src.stem}_{i}{src.suffix}"
                    shutil.copy2(src, dst)
                    attached.append(dst.name)
                except Exception as e:
                    logger.warning(f"Could not attach {src}: {e}")

        info: Dict[str, Any] = {
            "created": now.isoformat(timespec="seconds"),
            "type": dpg.get_value("bug_report_type"),
            "description_file": "description.txt",
            "log_file": log_file,
            "log_session": log_session,
            "selected_modules": sorted(self._selected_modules),
            "active_modules": sorted(str(k) for k in MODULES_REGISTRY.keys()),
            "attachments": attached,
            "user": getattr(app_state, "username", None),
            "mode": getattr(app_state, "mode", None),
            "system": {
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "dearpygui": getattr(dpg, "get_dearpygui_version", lambda: "?")(),
            },
        }
        (folder / "report.json").write_text(json.dumps(info, indent=4, ensure_ascii=False), encoding="utf-8")
        return folder

    def _show_done(self, folder: Path) -> None:
        tag = "bug_report_done"
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
        s = _scale()
        vp_w, vp_h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        with dpg.window(
            label="Report saved", tag=tag, modal=True, width=s(460), height=s(150),
            pos=[max(0, (vp_w - s(460)) // 2), max(0, (vp_h - s(150)) // 2)],
        ):
            dpg.add_text("Thank you! Your report was saved in:")
            dpg.add_text(str(folder), wrap=s(430), color=(150, 200, 255))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Open folder", callback=lambda *a: os.startfile(folder))
                dpg.add_button(label="Close", callback=lambda *a: dpg.delete_item(tag))


bug_report_window = BugReportWindow()


# ----------------------------------------------------------------------
# Crash popup at boot
# ----------------------------------------------------------------------


def show_crash_popup(pump_frames: bool = False) -> None:
    """
    Modal popup informing that the previous session ended abnormally.

    Same boot contract as ``login_dialog``: with ``pump_frames=True`` it blocks
    and pumps DPG frames until the user answers (call it at startup, before
    the main loop).
    """
    tag = "bug_report_crash_popup"
    if dpg.does_item_exist(tag):
        dpg.delete_item(tag)
    try:
        from config.display_scaling import display_scaling

        display_scaling.adapt_to_display()
    except Exception as exc:
        logger.warning(f"crash popup: display_scaling failed: {exc}")

    s = _scale()
    w = s(480)
    vp_w, vp_h = dpg.get_viewport_width(), dpg.get_viewport_height()
    open_report = [False]

    def _finish(*_: Any) -> None:
        if dpg.get_value("bug_report_crash_dontask"):
            from core.config_manager import config

            config.setdefault("General", {})["check_crash_on_boot"] = False
            config.save()
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)

    def _report(*_: Any) -> None:
        open_report[0] = True
        _finish()
        if not pump_frames:
            bug_report_window.show(crash=True)

    with dpg.window(
        label="Something went wrong",
        tag=tag,
        modal=True,
        no_close=True,
        autosize=True,
        pos=[max(0, (vp_w - w) // 2), max(0, (vp_h - s(190)) // 2)],
    ):
        dpg.add_text(
            "The application did not close properly last time (crash or forced exit).\n"
            "Would you like to report it? The log of the previous session can be attached.",
            wrap=w,
        )
        dpg.add_spacer(height=s(6))
        dpg.add_checkbox(label="Don't check for crashes at startup", tag="bug_report_crash_dontask")
        dpg.add_spacer(height=s(6))
        with dpg.group(horizontal=True):
            dpg.add_button(label="Report crash", callback=_report)
            dpg.add_button(label="Ignore", callback=_finish)

    if pump_frames:
        while dpg.is_dearpygui_running() and dpg.does_item_exist(tag):
            dpg.render_dearpygui_frame()
            try:
                vp_w, vp_h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
                ww, wh = dpg.get_item_rect_size(tag)
                if ww > 0 and wh > 0:
                    dpg.set_item_pos(tag, [max(0, (vp_w - ww) // 2), max(0, (vp_h - wh) // 2)])
            except Exception:
                pass
        for _ in range(2):
            if dpg.is_dearpygui_running():
                dpg.render_dearpygui_frame()
        if open_report[0]:
            bug_report_window.show(crash=True)
