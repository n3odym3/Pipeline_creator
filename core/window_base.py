from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from core.module_registry import register_module, unregister_module


class WindowBase:
    """
    Base class for GUI modules in the system.
    Handles window creation, merging, serialization, and connectivity between modules.
    """

    _batch_loading: bool = False

    label: str
    pos: Tuple[int, int]
    win_width: int
    win_height: int
    visible: bool
    outputs: Dict[str, Any]
    UUID: str
    winID: str
    handler_tag: Optional[Union[int, str]]
    accepted_input_types: List[Any]
    description: str
    connections: Dict[str, List[Any]]
    _persistent_fields: List[str]
    _original_children: List[Union[int, str]]
    merged_into: Optional[WindowBase]
    _merge_wrapper_id: Optional[Union[int, str]]

    def __init__(
        self,
        label: str = "Window",
        pos: Tuple[int, int] = (10, 10),
        win_width: int = -1,
        win_height: int = -1,
        uuid: Optional[str] = None,
        outputs: Optional[Dict[str, Any]] = None,
        visible: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Initialize a WindowBase instance.

        Args:
            label: Display label of the window.
            pos: Tuple for initial window position.
            win_width: Initial window width.
            win_height: Initial window height.
            uuid: Optional unique identifier.
            outputs: Dictionary of output types.
            visible: Whether the window is visible at creation.
            **kwargs: Additional persistent fields to apply.
        """
        self.label = label
        self.pos = pos
        self.win_width = win_width
        self.win_height = win_height
        self.visible = visible
        self.outputs = outputs or {}
        self.UUID = uuid or str(dpg.generate_uuid())
        self.winID = f"{label}_{self.UUID}"
        self.handler_tag = None
        self.accepted_input_types = []
        self.description = ""

        if not hasattr(self, "_persistent_fields"):
            self._persistent_fields = ["label"]

        for field in self._persistent_fields:
            if field in kwargs:
                setattr(self, field, kwargs[field])

        self.connections = {k: [] for k in self.outputs}
        self._original_children = []
        self.merged_into = None
        self._merge_wrapper_id = None

        register_module(self)

    def update_permission(self) -> None:
        """
        Adjust module permissions and UI visibility based on app_state.mode.
        Subclasses should override this to hide/show or lock specific widgets.
        """
        pass

    def _list_children(self) -> List[Union[int, str]]:
        """Save the current list of children for potential restoration."""
        if dpg.does_item_exist(self.winID):
            return dpg.get_item_children(self.winID, 1) or []
        return []

    def inform_leaving(self, child_id: List[Union[int, str]]) -> None:
        """Notify the target window that this window is leaving (used in recursive merges)."""
        for child in child_id:
            if child in self._original_children:
                self._original_children.remove(child)

        if self.merged_into is not None:
            self.merged_into.inform_leaving(child_id)

    def merge_into(self, target_window: WindowBase) -> None:
        """
        Move all widgets into another WindowBase instance.
        Restore first if already merged.
        """
        if not isinstance(target_window, WindowBase):
            raise TypeError("target_window must be a WindowBase")

        if self.merged_into is target_window:
            return

        if self.merged_into is not None:
            self.restore_contents()

        childrens = self._list_children()

        if set(childrens) & set(target_window._original_children):
            logger.warning(f"Cannot merge {self.label} into {target_window.label}: cyclic merge detected.")
            return

        self._original_children = childrens

        src_height = 300
        if dpg.does_item_exist(self.winID):
            sz = dpg.get_item_rect_size(self.winID)
            if sz and sz[1] > 50:
                src_height = int(sz[1])

        self._merge_wrapper_id = dpg.generate_uuid()
        with dpg.child_window(
            border=True,
            autosize_x=True,
            height=src_height,
            tag=self._merge_wrapper_id,
            parent=target_window.winID,
        ):
            pass

        for child in self._original_children:
            dpg.move_item(child, parent=self._merge_wrapper_id)

        dpg.hide_item(self.winID)
        self.merged_into = target_window

    def restore_contents(self) -> None:
        """Move back previously moved widgets to this window and remove the wrapper."""
        if self.merged_into is None:
            logger.warning(f"Cannot restore {self.label}: not merged into any window.")
            return

        self.merged_into.inform_leaving(self._original_children)
        for child in self._original_children:
            if dpg.does_item_exist(child):
                dpg.move_item(child, parent=self.winID)

        if self._merge_wrapper_id and dpg.does_item_exist(self._merge_wrapper_id):
            dpg.delete_item(self._merge_wrapper_id)
        self._merge_wrapper_id = None

        self._original_children.clear()
        self.merged_into = None
        dpg.show_item(self.winID)

    def is_merged(self) -> bool:
        """Check whether this window has been merged elsewhere."""
        return bool(self._original_children)

    def absorb(self, source_window: WindowBase) -> None:
        """Merge another window's content into this one."""
        if not isinstance(source_window, WindowBase):
            raise TypeError("source_window must be a WindowBase")
        source_window.merge_into(self)

    def eject(self, absorbed_window: WindowBase) -> None:
        """Restore a previously absorbed window."""
        if not isinstance(absorbed_window, WindowBase):
            raise TypeError("eject() expects a WindowBase instance.")
        absorbed_window.restore_contents()

    def get_merge_target_label(self) -> Optional[str]:
        """Returns the label of the window this one is merged into, or None."""
        return self.merged_into.label if self.merged_into else None

    def connect_to(self, target: Any, output: Optional[Union[str, int]] = None) -> bool:
        """
        Connect this module to another based on output type compatibility.

        Args:
            target: The target WindowBase instance.
            output: Output name (str) or index (int).

        Returns:
            True if the connection is valid and made, False otherwise.
        """
        from core.input_output_types import IOTypes

        if not hasattr(target, "accepted_input_types"):
            logger.error(f"Target {target} has no accepted_input_types")
            return False

        if isinstance(output, int):
            try:
                output_key = list(self.outputs.keys())[output]
            except IndexError:
                logger.error(f"Invalid output index: {output}")
                return False
        elif isinstance(output, str):
            if output not in self.outputs:
                logger.error(f"Output key '{output}' not found in outputs")
                return False
            output_key = output
        else:
            logger.error(f"Output must be a key (str) or index (int), got {type(output)}")
            return False

        output_type = self.outputs[output_key]
        input_types = getattr(target, "accepted_input_types", [])

        is_compatible = (
            not input_types
            or output_type == IOTypes.ANY
            or IOTypes.ANY in input_types
            or output_type in input_types
        )

        if not is_compatible:
            logger.warning(f"Incompatible types: {output_type} -> {input_types}")
            return False

        if output_key not in self.connections:
            self.connections[output_key] = []

        if target not in self.connections[output_key]:
            self.connections[output_key].append(target)

        return True

    def _is_output_compatible_with(self, target: Any) -> bool:
        """Internal: Check if any output type is compatible with target's accepted input types."""
        from core.input_output_types import IOTypes

        input_types = getattr(target, "accepted_input_types", [])
        if not input_types or IOTypes.ANY in input_types:
            return True

        for o in self.outputs.values():
            if o == IOTypes.ANY or o in input_types:
                return True
        return False

    def disconnect_from(self, *windows: WindowBase) -> None:
        """Disconnect this module from the provided windows."""
        for win in windows:
            for targets in self.connections.values():
                while win in targets:
                    targets.remove(win)


    def serialize(self) -> Dict[str, Any]:
        """Serialize window state and configuration for saving/restoration."""
        if dpg.does_item_exist(self.winID):
            self.pos = dpg.get_item_pos(self.winID)
            self.win_width, self.win_height = dpg.get_item_rect_size(self.winID)
            self.visible = dpg.is_item_visible(self.winID)

        params = {field: getattr(self, field) for field in self._persistent_fields}

        if self.merged_into:
            params["merged_into"] = self.merged_into.UUID

        return {
            "module": self.__class__.__module__.replace("modules.", ""),
            "class_name": self.__class__.__name__,
            "uuid": self.UUID,
            "pos": self.pos,
            "size": [self.win_width, self.win_height],
            "visible": self.visible,
            "params": params,
        }

    def close(self) -> None:
        """Clean up and unregister the window from the registry."""
        logger.trace(f"Closing {self.label} ({self.UUID})")
        if hasattr(self, "on_close") and callable(self.on_close):
            try:
                self.on_close()
            except Exception as e:
                logger.error(f"Error during on_close of {self.label}: {e}")

        tags_to_delete = set()
        uuid_str = str(self.UUID)

        for attr_name, val in list(self.__dict__.items()):
            def check_and_add(tag: Any) -> None:
                if isinstance(tag, str) and uuid_str in tag:
                    tags_to_delete.add(tag)

            if isinstance(val, (list, tuple)):
                for item in val:
                    check_and_add(item)
            elif isinstance(val, dict):
                for k, v in val.items():
                    check_and_add(k)
                    check_and_add(v)
            else:
                check_and_add(val)

        try:
            for item in dpg.get_all_items():
                if isinstance(item, str):
                    if uuid_str in item:
                        tags_to_delete.add(item)
                else:
                    alias = dpg.get_item_alias(item)
                    if alias and uuid_str in alias:
                        tags_to_delete.add(item)
        except Exception as e:
            logger.warning(f"Failed to scan DPG items for UUID {self.UUID}: {e}")

        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

        sub_windows = getattr(self, "sub_windows", [])
        for sub_win in sub_windows:
            if dpg.does_item_exist(sub_win):
                dpg.delete_item(sub_win)

        if dpg.does_item_exist(self.handler_tag):
            dpg.delete_item(self.handler_tag)

        for tag in tags_to_delete:
            try:
                if dpg.does_item_exist(tag):
                    dpg.delete_item(tag)
            except Exception as e:
                logger.warning(f"Failed to delete tag {tag}: {e}")

        unregister_module(self)
        self.connections.clear()

    def on_close(self) -> None:
        """Stub method for subclasses to override for custom cleanup logic."""
        pass

    def __del__(self) -> None:
        if logger is not None:
            label = getattr(self, "label", "Unknown")
            uuid = getattr(self, "UUID", "Unknown")
            logger.info(f"WindowBase {label} ({uuid}) has been deleted.")

    def is_outputs_ready(self) -> bool:
        """Check if all connected targets are ready to receive data."""
        for modules in self.connections.values():
            for module in modules:
                is_ready = getattr(module, "is_ready", lambda: True)
                if callable(is_ready) and not is_ready():
                    return False
        return True

    def autosize_window(self, winID: Optional[Union[int, str]] = None) -> None:
        """Triggers a one-shot autosize to fit window content."""
        if WindowBase._batch_loading:
            return
        if winID is None:
            winID = self.winID
        if dpg.does_item_exist(winID):
            dpg.configure_item(winID, autosize=True)
            dpg.split_frame()
            dpg.configure_item(winID, autosize=False)

