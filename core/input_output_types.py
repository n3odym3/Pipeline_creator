"""
Input and Output Types module for Pipeline Creator.

Defines the standard I/O data types exchanged between pipeline modules.
Each IOTypes member provides a unique key, expected Python data structure hint,
and description.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class IOTypes(str, Enum):
    """
    Standardization of I/O data types for Pipeline Creator modules.

    This Enum defines the types of data that can be passed between modules.
    Each member consists of a unique string key, a data type hint, and
    a human-readable description.
    """

    def __new__(cls, value: str, dtype: str, description: str) -> IOTypes:
        """
        Create a new IOTypes member.

        Args:
            value: The internal string value/key used for serialization.
            dtype: A string representing the expected Python type or structure.
            description: A short explanation of the data type's purpose.
        """
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj._dtype = dtype
        obj._description = description
        return obj

    @property
    def dtype(self) -> str:
        """Returns the data type hint for this I/O type."""
        return self._dtype

    @property
    def description(self) -> str:
        """Returns the human-readable description for this I/O type."""
        return self._description

    @classmethod
    def get_by_value(cls, value: str) -> Optional[IOTypes]:
        """
        Safely retrieve an IOTypes member by its string value.

        Args:
            value: The string value to search for.

        Returns:
            The matching IOTypes member, or None if not found.
        """
        for item in cls:
            if item.value == value:
                return item
        return None

    # General Types
    TRIGGER = ("trigger", "str or None", "Trigger event with or without attached data")
    TEXT = ("text", "str", "Standard string-based messaging")
    NUMBER = ("number", "int|float", "Numerical value for calculations or thresholds")
    POSITION = ("position", "int|float", "1D coordinate or distance value")

    # Path Types
    FILE_PATH = ("file_path", "str", "Absolute or relative path to a file")
    FOLDER_PATH = ("folder_path", "str", "Absolute or relative path to a directory")

    # Visual / Processing Types
    FRAME = ("frame", "np.ndarray or tuple(np.ndarray, str)", "8-bit image frame. Can be (frame, name).")
    FRAME12 = ("frame12", "np.ndarray or tuple(np.ndarray, str)", "12-bit HDR image. Can be (frame, name).")
    FRAME16 = ("frame16", "np.ndarray or tuple(np.ndarray, str)", "16-bit HDR image. Can be (frame, name).")
    MASK = ("mask", "np.ndarray", "Binary/grayscale image mask for region of interest")
    FRAME_MASK_PAIR = ("frame_mask_pair", "tuple(np.ndarray, np.ndarray)", "Combined frame and its associated mask")
    TRACKING = ("tracking", "dict", "Object tracking metadata (ID, coordinates, dimensions)")
    CONTOURS = ("contours", "list", "List of detected contours or bounding boxes")

    # Data Types
    CMD_DICT = ("cmd_dict", "dict", "Action command encapsulated in a dictionary")
    CMD_LIST = ("cmd_list", "list", "Sequential list of processing commands")
    STATUS_DICT = ("status_dict", "dict", "Module health and operational status metadata")
    DATALIST = ("datalist", "list", "Structured X/Y data pairs for plotting or analysis")
    SAMPLE = ("sample", "dict", "Data sample with name, uuid, x, y, and optional action (select/unselect/rename)")
    POINT_LIST = ("point_list", "list", "List of 2D coordinates [[x, y], ...]")
    ROI_SAMPLE = ("roi_sample", "dict", "ROI data: {uuid, name, rect: [x,y,w,h], action, ...}")
    COVERAGE = ("coverage", "float", "Mask coverage ratio as a percentage (0–100) of the image area")
    FRAME_LIST = (
        "frame_list",
        "list[dict]",
        "Ordered list of frame dicts, each containing 'image' (np.ndarray), optional 'filename', 'x', etc.",
    )
    VECTORS = ("vectors", "dict", "Vector field and orientation data ({'angles': [...], 'magnitudes': [...], ...})")
    ANY = ("any", "Any", "Generic data type for flexible connections")

