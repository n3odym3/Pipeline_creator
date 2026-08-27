"""
File Explorer module for Pipeline Creator.

Provides utility functions for native OS file and directory selection dialogs.
Wraps tkinter.filedialog in thread-safe containers.
"""

from __future__ import annotations

from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, List, Optional, Tuple, Union


class FileExplorer:
    """
    Utility class for handling system file and directory dialogs.

    Wraps tkinter's filedialog to provide a simplified, thread-safe interface
    for selecting, saving files, and selecting directories.
    """

    def __init__(self) -> None:
        self._is_dialog_open: bool = False
        self._active_dialog_hwnd: Optional[int] = None
        self._lock: threading.Lock = threading.Lock()

    @property
    def is_dialog_open(self) -> bool:
        """True if a file or directory dialog is currently open."""
        return self._is_dialog_open

    def get_active_dialog_info(self) -> Tuple[Optional[int], Optional[Tuple[int, int, int, int]]]:
        """
        Returns (hwnd, (left, top, width, height)) of the currently active file dialog,
        or (None, None) if no dialog is open.
        """
        if not self._is_dialog_open:
            return None, None

        try:
            import ctypes
            import ctypes.wintypes

            user32 = ctypes.windll.user32
            hwnd = self._active_dialog_hwnd

            if not hwnd or not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                fg = user32.GetForegroundWindow()
                if fg and user32.IsWindow(fg) and user32.IsWindowVisible(fg):
                    hwnd = fg

            if hwnd and user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd):
                rect = ctypes.wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                if w > 100 and h > 100:
                    return hwnd, (rect.left, rect.top, w, h)
        except Exception:
            pass

        return None, None

    def _prepare_path(self, default_path: str) -> Tuple[str, str]:
        """
        Prepare initial directory and filename from a default path string.

        Args:
            default_path: The default path string.

        Returns:
            Tuple[str, str]: A tuple of (initial_dir, initial_file).
        """
        if not default_path:
            return str(Path.cwd()), ""

        try:
            path = Path(str(default_path))
            if path.is_dir():
                return str(path), ""
            if path.parent.exists():
                return str(path.parent), path.name
        except Exception:
            pass

        return str(Path.cwd()), ""

    def _run_dialog(
        self,
        func: Callable[..., Any],
        callback: Optional[Callable[[str, str], None]] = None,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """
        Helper to run a Tkinter dialog in an isolated Tk instance.
        If callback is None, runs synchronously and blocks until closed.
        If callback is provided, runs asynchronously in a daemon thread.
        """

        def _task() -> str:
            with self._lock:
                self._is_dialog_open = True
                self._active_dialog_hwnd = None

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            root.focus_force()

            root_hwnd = None
            try:
                root_hwnd = int(root.winfo_id())
            except Exception:
                pass

            filetypes = kwargs.get("filetypes")
            type_var = None
            if filetypes:
                type_var = tk.StringVar(root)
                kwargs["typevariable"] = type_var

            kwargs["parent"] = root

            import time

            def _track_hwnd():
                import ctypes
                import ctypes.wintypes
                user32 = ctypes.windll.user32

                start_time = time.time()
                while self._is_dialog_open and (time.time() - start_time < 300.0):
                    try:
                        popup = None
                        if root_hwnd:
                            popup = user32.GetLastActivePopup(root_hwnd)
                        if not popup or popup == root_hwnd:
                            popup = user32.GetForegroundWindow()

                        if popup and user32.IsWindow(popup) and user32.IsWindowVisible(popup):
                            rect = ctypes.wintypes.RECT()
                            user32.GetWindowRect(popup, ctypes.byref(rect))
                            w = rect.right - rect.left
                            h = rect.bottom - rect.top
                            if w > 100 and h > 100:
                                with self._lock:
                                    self._active_dialog_hwnd = popup
                    except Exception:
                        pass
                    time.sleep(0.04)

            threading.Thread(target=_track_hwnd, daemon=True).start()

            try:
                res = func(*args, **kwargs)
            finally:
                with self._lock:
                    self._is_dialog_open = False
                    self._active_dialog_hwnd = None
                try:
                    root.destroy()
                except Exception:
                    pass

            path = res or ""

            # Ensure default extension is applied if specified in kwargs
            defaultext = kwargs.get("defaultextension", "")
            if path and defaultext and not path.lower().endswith(defaultext.lower()):
                path += defaultext

            # Detect selected extension from filetypes filter
            selected_ext = ""
            if filetypes and callback:
                path_lower = path.lower()
                for desc, pattern in filetypes:
                    ext = pattern.replace("*", "")
                    if ext and path_lower.endswith(ext.lower()):
                        selected_ext = ext
                        break
                if not selected_ext and type_var is not None:
                    selected_label = type_var.get()
                    for desc, pattern in filetypes:
                        if desc == selected_label:
                            selected_ext = pattern.replace("*", "")
                            break
                if not selected_ext:
                    for desc, pattern in filetypes:
                        if pattern != "*.*":
                            selected_ext = pattern.replace("*", "")
                            break

            if callback:
                callback(path, selected_ext)

            try:
                root.destroy()
            except Exception:
                pass

            return path

        if callback:
            threading.Thread(target=_task, daemon=True).start()
            return ""
        else:
            return _task()

    def select_file(
        self,
        default_path: str = "",
        extensions: Optional[List[Tuple[str, str]]] = None,
        callback: Optional[Callable[[str, str], None]] = None,
    ) -> str:
        """
        Open a dialog to select an existing file.

        Args:
            default_path: Initial path or directory to open.
            extensions: List of allowed file types.
            callback: Optional callback(path, extension) invoked on selection.

        Returns:
            str: The selected file path (if synchronous) or empty string (if callback used).
        """
        init_dir, init_file = self._prepare_path(default_path)
        filetypes = extensions if extensions else [("All files", "*.*")]

        return self._run_dialog(
            filedialog.askopenfilename,
            callback=callback,
            initialdir=init_dir,
            initialfile=init_file,
            filetypes=filetypes,
        )

    def save_file(
        self,
        default_path: str = "",
        default_name: str = "",
        extensions: Optional[List[Tuple[str, str]]] = None,
        callback: Optional[Callable[[str, str], None]] = None,
    ) -> str:
        """
        Open a dialog to specify a file for saving.

        Args:
            default_path: Initial directory to open.
            default_name: Default filename to suggest.
            extensions: List of allowed file types.
            callback: Optional callback(path, extension) invoked on selection.

        Returns:
            str: The chosen file path (if synchronous) or empty string (if callback used).
        """
        init_dir, init_file = self._prepare_path(default_path)
        if default_name:
            init_file = default_name
        filetypes = extensions if extensions else [("All files", "*.*")]

        defaultext = ""
        if extensions:
            for desc, pattern in extensions:
                if pattern != "*.*":
                    defaultext = pattern.replace("*", "")
                    break
        if not defaultext and init_file and "." in init_file:
            defaultext = Path(init_file).suffix

        kwargs: dict[str, Any] = {
            "initialdir": init_dir,
            "initialfile": init_file,
            "filetypes": filetypes,
        }
        if defaultext:
            kwargs["defaultextension"] = defaultext

        return self._run_dialog(
            filedialog.asksaveasfilename,
            callback=callback,
            **kwargs,
        )

    def select_folder(
        self,
        default_path: str = "",
        callback: Optional[Callable[[str, str], None]] = None,
    ) -> str:
        """
        Open a dialog to select a directory.

        Args:
            default_path: Initial directory to open.
            callback: Optional callback(path, extension) invoked on selection.

        Returns:
            str: The selected directory path (if synchronous) or empty string (if callback used).
        """
        init_dir = str(Path(default_path)) if default_path else str(Path.cwd())
        return self._run_dialog(filedialog.askdirectory, callback=callback, initialdir=init_dir)


# Global singleton instance
file_explorer: FileExplorer = FileExplorer()

