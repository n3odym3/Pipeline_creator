from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.file_explorer import file_explorer
from core.input_output_types import IOTypes
from core.window_base import WindowBase
from .video_tools import video


class VideoReader_win(WindowBase):
    """
    Video Reader window module.

    Scans directories for video files (.mp4), provides interactive playback
    controls (Play, Pause, Stop, Seek Scrubber, FPS adjustment), and streams
    frames, triggers, and file paths downstream through node connections.
    """

    label = "Video Reader"
    win_width = display_scaling.scale(360)
    win_height = display_scaling.scale(260)

    accepted_input_types = [IOTypes.FOLDER_PATH, IOTypes.FILE_PATH, IOTypes.CMD_DICT]
    outputs = {
        "Frame": IOTypes.FRAME,
        "Trigger": IOTypes.TRIGGER,
        "File Path": IOTypes.FILE_PATH,
    }
    persistent_fields = ["label", "folder"]

    folder: str = ""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        if not self.folder and "folder_path" in kwargs:
            self.folder = str(kwargs["folder_path"] or "")

        # DPG Tags
        self.winID = f"video_reader_win_{self.UUID}"
        self.table_tag = f"video_reader_table_{self.UUID}"
        self.child_win_table_tag = f"video_reader_child_table_{self.UUID}"
        self.fps_tag = f"video_reader_fps_{self.UUID}"
        self.start_tag = f"video_reader_start_{self.UUID}"
        self.stop_tag = f"video_reader_stop_{self.UUID}"
        self.pause_tag = f"video_reader_pause_{self.UUID}"
        self.video_group_tag = f"video_reader_group_{self.UUID}"
        self.progress_tag = f"video_reader_progress_{self.UUID}"
        self.folder_label_tag = f"video_reader_folder_label_{self.UUID}"

        # State
        self.last_selectable: Optional[Union[int, str]] = None
        self.last_video_selected: Optional[str] = None
        self._current_video_name: str = ""

        self.video_running: bool = False
        self.video_paused: bool = False
        self.video_thread: Optional[threading.Thread] = None
        self._seek_request: Optional[int] = None
        self._ignore_slider_cb: bool = False
        self._is_closing: bool = False

        self.video_keys: List[str] = []
        self.videos: Dict[str, Dict[str, str]] = {}

        self.playback_fps: int = 30
        self.frame_interval: float = 1.0 / self.playback_fps

        # Construct Interface
        scale = display_scaling.scale
        with self.window():
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Browse",
                    width=scale(70),
                    callback=self._on_browse_clicked,
                )
                dpg.add_text("No folder selected", tag=self.folder_label_tag)

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="\uf04b",
                    tag=self.start_tag,
                    width=scale(30),
                    callback=self.start_video,
                )
                dpg.add_button(
                    label="\uf04c",
                    tag=self.pause_tag,
                    width=scale(30),
                    callback=self.toggle_pause,
                )
                dpg.add_button(
                    label="\uf04d",
                    tag=self.stop_tag,
                    width=scale(30),
                    callback=self.stop_video,
                )
                dpg.add_input_int(
                    label="FPS",
                    tag=self.fps_tag,
                    default_value=self.playback_fps,
                    min_value=1,
                    min_clamped=True,
                    width=-1,
                    callback=self.set_playback_fps,
                )

            with dpg.group(horizontal=True):
                dpg.add_slider_int(
                    tag=self.progress_tag,
                    width=-1,
                    default_value=0,
                    callback=self.on_slider_change,
                )

            with dpg.group(tag=self.video_group_tag):
                with dpg.child_window(tag=self.child_win_table_tag):
                    with dpg.table(header_row=True, resizable=True, tag=self.table_tag):
                        dpg.add_table_column(label="Filename")

        # Apply icon font for FontAwesome glyphs
        try:
            from config.theme_manager import theme_manager

            theme_manager.apply_icon_font(self.start_tag)
            theme_manager.apply_icon_font(self.pause_tag)
            theme_manager.apply_icon_font(self.stop_tag)
        except Exception as e:
            logger.debug(f"Could not apply icon font to VideoReader buttons: {e}")

        self.update_permission()

    def update_permission(self) -> None:
        """Update UI permissions based on application mode."""
        from core.app_state import app_state

        is_user = app_state.mode == "user"
        if dpg.does_item_exist(self.winID):
            dpg.configure_item(self.winID, no_close=is_user)

    def is_ready(self) -> bool:
        """Indicate whether the video reader module is initialized and ready."""
        return not self._is_closing

    def _on_browse_clicked(self, *args: Any, **kwargs: Any) -> None:
        """Launch native folder picker dialog."""
        file_explorer.select_folder(
            default_path=self.folder if self.folder else "",
            callback=self._on_folder_selected,
        )

    def _on_folder_selected(self, selected_dir: str, *args: Any, **kwargs: Any) -> None:
        """Process directory selection from file explorer."""
        if selected_dir and os.path.isdir(selected_dir):
            self.input_cb(data=selected_dir, data_type=IOTypes.FOLDER_PATH)

    def update_file_list(self, path: str) -> None:
        """
        Scan directory for video files and populate table.

        Args:
            path: Directory path to scan.
        """
        self.last_selectable = None
        self.last_video_selected = None

        filepaths, filenames = video.list_files(path, file_extension="mp4", sort_by="name")
        self.videos = {name: {"path": fp} for name, fp in zip(filenames, filepaths)}
        self.video_keys = list(self.videos.keys())

        if dpg.does_item_exist(self.child_win_table_tag):
            dpg.delete_item(self.child_win_table_tag)

        if dpg.does_item_exist(self.video_group_tag):
            dpg.push_container_stack(self.video_group_tag)
            with dpg.child_window(tag=self.child_win_table_tag):
                with dpg.table(
                    header_row=True,
                    resizable=True,
                    tag=self.table_tag,
                    borders_innerH=True,
                    borders_innerV=True,
                    borders_outerH=True,
                    borders_outerV=True,
                    row_background=True,
                    policy=dpg.mvTable_SizingStretchProp,
                ):
                    dpg.add_table_column(label="Name")

                    for i, name in enumerate(self.video_keys):
                        with dpg.table_row():
                            sel_tag = f"vr_sel_{self.UUID}_{i}"
                            dpg.add_selectable(
                                label=name,
                                callback=self.on_row_click,
                                user_data=i,
                                tag=sel_tag,
                            )
                            with dpg.popup(parent=sel_tag, mousebutton=dpg.mvMouseButton_Right):
                                dpg.add_menu_item(
                                    label="\uf064  Send path",
                                    callback=lambda s, a, u: self._send_path_cb(u),
                                    user_data=i,
                                )
            dpg.pop_container_stack()

    def _send_path_cb(self, video_index: int) -> None:
        """
        Send selected video path downstream via File Path output.

        Args:
            video_index: Index of video in self.video_keys.
        """
        if video_index < 0 or video_index >= len(self.video_keys):
            return
        name = self.video_keys[video_index]
        path = self.videos[name]["path"]
        basename = os.path.splitext(name)[0]
        payload = (path, basename)
        for module in list(self.connections.get("File Path", [])):
            if hasattr(module, "input_cb") and callable(module.input_cb):
                try:
                    module.input_cb(data=payload, data_type=IOTypes.FILE_PATH)
                except Exception as e:
                    logger.error(f"VideoReader send_path error to {getattr(module, 'winID', module)}: {e}")

    def on_row_click(self, sender: Any, app_data: Any, user_data: int, *args: Any) -> None:
        """
        Handle selection of a video from the file list table.

        Args:
            sender: DPG selectable tag.
            app_data: DPG callback event value.
            user_data: Integer index into self.video_keys.
        """
        if self.last_selectable is not None and dpg.does_item_exist(self.last_selectable):
            dpg.set_value(self.last_selectable, False)

        if sender is not None and dpg.does_item_exist(sender):
            self.last_selectable = sender
            dpg.set_value(sender, True)

        if user_data < 0 or user_data >= len(self.video_keys):
            return

        name = self.video_keys[user_data]
        self.last_video_selected = self.videos[name]["path"]
        self._current_video_name = os.path.splitext(name)[0]

        video.set_video(self.last_video_selected)
        total = video.get_total_frames()
        if dpg.does_item_exist(self.progress_tag):
            dpg.configure_item(self.progress_tag, max_value=total if total > 0 else 100)

        self._ignore_slider_cb = True
        if dpg.does_item_exist(self.progress_tag):
            dpg.set_value(self.progress_tag, 0)
        self._ignore_slider_cb = False

        ret, frame = video.read()
        if ret and frame is not None:
            self.frame_cb(frame=frame)

    def play_loop(self) -> None:
        """Background playback thread loop streaming video frames at target FPS."""
        next_frame_time = time.perf_counter()

        while self.video_running and not self._is_closing:
            if not self.is_outputs_ready():
                time.sleep(0.001)
                continue

            now = time.perf_counter()
            if now < next_frame_time:
                time.sleep(min(0.005, next_frame_time - now))
                continue

            if self._seek_request is not None:
                video.set_current_frame(self._seek_request)
                self._seek_request = None
                if self.video_paused:
                    ret, frame = video.read()
                    if ret and frame is not None:
                        self.frame_cb(frame=frame)
                        current_frame = video.get_current_frame()
                        self._ignore_slider_cb = True
                        if dpg.does_item_exist(self.progress_tag):
                            dpg.set_value(self.progress_tag, current_frame)
                        self._ignore_slider_cb = False

            if self.video_paused:
                time.sleep(0.01)
                next_frame_time = time.perf_counter()
                continue

            ret, frame = video.read()
            if not ret:
                break
            if frame is not None:
                self.frame_cb(frame=frame)

                if self._seek_request is None:
                    current_frame = video.get_current_frame()
                    self._ignore_slider_cb = True
                    if dpg.does_item_exist(self.progress_tag):
                        dpg.set_value(self.progress_tag, current_frame)
                    self._ignore_slider_cb = False

            next_frame_time += self.frame_interval
            if next_frame_time < time.perf_counter():
                next_frame_time = time.perf_counter()

        self.trigger_cb(event="STOP")
        self.video_running = False
        self.video_thread = None

    def start_video(self, *args: Any, **kwargs: Any) -> None:
        """Start or restart video playback from beginning."""
        if not self.last_video_selected:
            logger.warning(f"{self.winID} No video selected to play.")
            return

        self.trigger_cb(event="START")
        video.set_video(self.last_video_selected)
        video.set_current_frame(0)
        self.video_running = True
        self.video_paused = False
        if dpg.does_item_exist(self.pause_tag):
            dpg.set_item_label(self.pause_tag, "\uf04c")

        if self.video_thread is None or not self.video_thread.is_alive():
            self.video_thread = threading.Thread(target=self.play_loop, daemon=True)
            self.video_thread.start()

    def toggle_pause(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Toggle playback pause / resume state."""
        if not self.last_video_selected or not self.video_running:
            return

        self.video_paused = not self.video_paused
        if dpg.does_item_exist(self.pause_tag):
            dpg.set_item_label(self.pause_tag, "\uf04b" if self.video_paused else "\uf04c")

    def stop_video(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Stop video playback and reset transport controls."""
        self.video_running = False
        self.video_paused = False
        if dpg.does_item_exist(self.pause_tag):
            dpg.set_item_label(self.pause_tag, "\uf04c")

    def set_playback_fps(
        self,
        sender: Any,
        app_data: int,
        user_data: Any = None,
        *args: Any,
    ) -> None:
        """Update playback target frame rate."""
        self.playback_fps = max(1, int(app_data))
        self.frame_interval = 1.0 / self.playback_fps

    def on_slider_change(
        self,
        sender: Any,
        app_data: int,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Seek video playback position to slider value."""
        if self._ignore_slider_cb:
            return
        self._seek_request = app_data

        if self.last_video_selected and not self.video_running:
            if video.video is None:
                video.set_video(self.last_video_selected)
            video.set_current_frame(self._seek_request)
            ret, frame = video.read()
            if ret and frame is not None:
                self.frame_cb(frame=frame)
            self._seek_request = None

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Handle incoming data from upstream nodes or external commands.

        Accepts:
            - Directory path string (IOTypes.FOLDER_PATH)
            - File path string or tuple (IOTypes.FILE_PATH)
            - Command dictionary (IOTypes.CMD_DICT)
        """
        data = kwargs.get("data") if kwargs.get("data") is not None else (args[0] if args else None)
        data_type = kwargs.get("data_type")

        cmd = data if (isinstance(data, dict) or data_type == IOTypes.CMD_DICT) else None
        if isinstance(cmd, dict):
            if cmd.get("browse", False):
                self._on_browse_clicked()
            if cmd.get("start", False):
                self.start_video()
            if cmd.get("stop", False):
                self.stop_video()
            if cmd.get("pause", False):
                self.toggle_pause()
            if "fps" in cmd:
                try:
                    val = int(cmd["fps"])
                    if dpg.does_item_exist(self.fps_tag):
                        dpg.set_value(self.fps_tag, val)
                    self.set_playback_fps(None, val)
                except (ValueError, TypeError):
                    pass
            return

        if isinstance(data, (tuple, list)) and len(data) >= 1 and isinstance(data[0], str):
            data = data[0]

        if isinstance(data, str):
            if os.path.isdir(data):
                self.folder = data
                self.update_file_list(data)
                self.video_running = False
                display = data if len(data) <= 35 else f"...{data[-32:]}"
                if dpg.does_item_exist(self.folder_label_tag):
                    dpg.set_value(self.folder_label_tag, display)
            elif os.path.isfile(data):
                folder = os.path.dirname(data)
                self.folder = folder
                self.update_file_list(folder)
                self.video_running = False
                display = folder if len(folder) <= 35 else f"...{folder[-32:]}"
                if dpg.does_item_exist(self.folder_label_tag):
                    dpg.set_value(self.folder_label_tag, display)
                base = os.path.splitext(os.path.basename(data))[0]
                if base in self.video_keys:
                    idx = self.video_keys.index(base)
                    self.on_row_click(sender=None, app_data=None, user_data=idx)

    def frame_cb(self, frame: Any) -> None:
        """
        Transmit decoded video frame downstream.

        Args:
            frame: Video image frame array.
        """
        payload = (frame, self._current_video_name)
        for module in list(self.connections.get("Frame", [])):
            if hasattr(module, "input_cb") and callable(module.input_cb):
                try:
                    module.input_cb(data=payload, data_type=IOTypes.FRAME)
                except Exception as e:
                    logger.error(f"VideoReader frame dispatch error to {getattr(module, 'winID', module)}: {e}")

    def trigger_cb(self, event: Optional[str] = None) -> None:
        """
        Emit playback lifecycle event downstream.

        Args:
            event: Event string ('START' or 'STOP').
        """
        for module in list(self.connections.get("Trigger", [])):
            if hasattr(module, "input_cb") and callable(module.input_cb):
                try:
                    module.input_cb(data=event, data_type=IOTypes.TRIGGER)
                except Exception as e:
                    logger.error(f"VideoReader trigger dispatch error to {getattr(module, 'winID', module)}: {e}")

    def on_close(self) -> None:
        """Stop playback thread and release video capture resources."""
        if getattr(self, "_is_closing", False):
            return
        self._is_closing = True
        self.video_running = False
        if self.video_thread is not None and self.video_thread.is_alive():
            try:
                self.video_thread.join(timeout=2.0)
            except Exception as e:
                logger.warning(f"Error joining video thread in {self.winID}: {e}")
        try:
            video.release()
        except Exception as e:
            logger.warning(f"Error releasing video capture in {self.winID}: {e}")

    def close(self) -> None:
        """Close window and unregister."""
        self.on_close()
        super().close()

    def destroy(self) -> None:
        """Destroy window and release resources."""
        self.close()
        super().destroy()


VideoReaderWin = VideoReader_win
EXPORTED_CLASS = VideoReader_win
EXPORTED_NAME = "Video Reader"
