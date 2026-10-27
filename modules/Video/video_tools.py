from __future__ import annotations

import glob
import os
import re
import threading
import time
from collections import deque
from typing import List, Optional, Tuple, Union

import cv2
import numpy as np


class Video_tools:
    """
    Video utility class managing video capture, frame reading,
    file/directory scanning, and FPS computation.
    """

    def __init__(self, smoothing_window: int = 10) -> None:
        """
        Initialize video capture manager and FPS tracking state.

        Args:
            smoothing_window: Number of samples used for FPS averaging.
        """
        self.video: Optional[cv2.VideoCapture] = None
        self.last_frame: Optional[np.ndarray] = None

        self.fps: float = 30.0
        self.playback_fps: int = 30
        self.last_read_ts: float = time.perf_counter()
        self.lock = threading.Lock()

        # FPS counter state
        self.last_time: Optional[float] = None
        self.raw_fps: float = 0.0
        self.smoothed_fps: float = 0.0
        self.samples: deque[float] = deque(maxlen=smoothing_window)
        self.smoothing_window: int = smoothing_window
        self.call_count: int = 0

    def set_video(self, path: str) -> None:
        """
        Open a video file with OpenCV using the FFMPEG backend.

        Args:
            path: Absolute or relative file path to the video file.
        """
        with self.lock:
            if self.video is not None:
                self.video.release()
            self.video = cv2.VideoCapture(path, cv2.CAP_FFMPEG)

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Read the next frame from the current video.

        Returns:
            Tuple of (success_flag, frame_array).
        """
        with self.lock:
            if self.video is not None:
                return self.video.read()
        return False, None

    def read_first_frame(self, file: str) -> Optional[np.ndarray]:
        """
        Extract the first frame of a given video file.

        Args:
            file: Path to the target video file.

        Returns:
            The first frame as a NumPy array if successful, None otherwise.
        """
        with self.lock:
            cap = cv2.VideoCapture(file, cv2.CAP_FFMPEG)
            ret, frame = cap.read()
            cap.release()
        return frame if ret else None

    def get_total_frames(self) -> int:
        """
        Get the total number of frames in the current video.

        Returns:
            Total frame count, or 0 if no video is loaded.
        """
        with self.lock:
            if self.video is not None:
                return int(self.video.get(cv2.CAP_PROP_FRAME_COUNT))
        return 0

    def get_current_frame(self) -> int:
        """
        Get the current 0-indexed frame position.

        Returns:
            Current frame index, or 0 if no video is loaded.
        """
        with self.lock:
            if self.video is not None:
                return int(self.video.get(cv2.CAP_PROP_POS_FRAMES))
        return 0

    def set_current_frame(self, frame_number: int) -> None:
        """
        Seek to a specific frame number in the loaded video.

        Args:
            frame_number: 0-indexed target frame position.
        """
        with self.lock:
            if self.video is not None:
                self.video.set(cv2.CAP_PROP_POS_FRAMES, frame_number)

    def release(self) -> None:
        """Release the underlying OpenCV video capture resource."""
        with self.lock:
            if self.video is not None:
                self.video.release()
                self.video = None

    @staticmethod
    def list_files(
        folder: str,
        file_extension: str = "mp4",
        sort_by: str = "name",
    ) -> Tuple[List[str], List[str]]:
        """
        Search and list files with given extension in directory, sorted by natural name or date.

        Args:
            folder: Directory to search.
            file_extension: File extension without leading dot (e.g. 'mp4').
            sort_by: 'name' for natural alphanumeric sort or 'date' for modification time.

        Returns:
            Tuple of (list of full filepaths, list of base filenames without extension).
        """
        search_pattern = os.path.join(folder, f"*.{file_extension}")
        filepaths = glob.glob(search_pattern)

        if sort_by == "name":
            filepaths.sort(key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)])
        elif sort_by == "date":
            filepaths.sort(key=os.path.getmtime)
        else:
            raise ValueError("sort_by must be 'name' or 'date'")

        filenames = [os.path.splitext(os.path.basename(file))[0] for file in filepaths]
        return filepaths, filenames

    def get_fps(self) -> Tuple[float, float]:
        """
        Calculate instantaneous and smoothed FPS. Call once per read frame.

        Returns:
            Tuple of (raw_fps, smoothed_fps).
        """
        now = time.time()

        if self.last_time is not None:
            delta = now - self.last_time
            if delta > 0:
                self.raw_fps = 1.0 / delta
                self.samples.append(self.raw_fps)
                self.call_count += 1

                if self.call_count % self.smoothing_window == 0 and len(self.samples) == self.smoothing_window:
                    self.smoothed_fps = sum(self.samples) / self.smoothing_window

        self.last_time = now
        return self.raw_fps, self.smoothed_fps

    def reset_fps(self) -> None:
        """Reset FPS tracking history."""
        self.last_time = None
        self.raw_fps = 0.0
        self.smoothed_fps = 0.0
        self.samples.clear()
        self.call_count = 0


VideoTools = Video_tools
video = Video_tools()