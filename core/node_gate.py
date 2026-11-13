from __future__ import annotations

from typing import Any, Dict, List, Optional

import dearpygui.dearpygui as dpg
from loguru import logger

from core.input_output_types import IOTypes


class _GateNode:
    """
    Built-in Gate node proxy.
    Controls whether incoming wired data is forwarded downstream based on its pass-through state.
    Supports dynamic IOType deduction from connected inputs and outputs.
    """

    KIND: str = "gate"

    def __init__(
        self,
        label: str = "Gate",
        uuid: Optional[str] = None,
        is_open: bool = True,
        io_type: Optional[IOTypes | str] = None,
    ) -> None:
        self.label: str = label or "Gate"
        self.UUID: str = uuid or str(dpg.generate_uuid())
        self.is_open: bool = bool(is_open)

        # Resolve initial IOType
        if isinstance(io_type, str):
            try:
                resolved_type = IOTypes[io_type]
            except KeyError:
                resolved_type = IOTypes.ANY
        elif isinstance(io_type, IOTypes):
            resolved_type = io_type
        else:
            resolved_type = IOTypes.ANY

        self.io_type: IOTypes = resolved_type
        self.accepted_input_types: List[IOTypes] = [self.io_type]
        self.outputs: Dict[str, IOTypes] = {"Out": self.io_type}
        self.connections: Dict[str, List[Any]] = {"Out": []}

        # UI Tags
        self._checkbox_tag: str = f"_gate_open_{self.UUID}"
        self._name_input_tag: str = f"_gate_name_{self.UUID}"
        self._in_tooltip_text_tag: str = f"_gate_in_tt_{self.UUID}"
        self._out_tooltip_text_tag: str = f"_gate_out_tt_{self.UUID}"

        self.node_id: Optional[int] = None
        self.editor: Optional[Any] = None

    def get_type_str(self) -> str:
        """Get formatted string for current IOType (e.g. 'Frame', 'Any')."""
        return str(self.io_type.value) if hasattr(self.io_type, "value") else str(self.io_type)

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Receives data from an upstream module.
        If pass-through is active (is_open=True), forwards data downstream.
        Otherwise, blocks the transmission.
        """
        if not self.is_open:
            return

        for module in self.connections.get("Out", []):
            try:
                module.input_cb(*args, **kwargs)
            except Exception as e:
                logger.error(f"Gate '{self.label}': error dispatching to {module} - {e}")

    def is_ready(self) -> bool:
        """Check if downstream targets are ready to receive data when the gate is open."""
        if not self.is_open:
            return True
        for module in self.connections.get("Out", []):
            is_ready = getattr(module, "is_ready", lambda: True)
            if callable(is_ready) and not is_ready():
                return False
        return True

    def set_acquisition(self, *args: Any, **kwargs: Any) -> None:
        """Forward acquisition metadata (fps, calibration) to downstream targets."""
        if not self.is_open:
            return
        for module in self.connections.get("Out", []):
            fn = getattr(module, "set_acquisition", None)
            if callable(fn):
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Gate '{self.label}': set_acquisition error - {e}")

    def set_open(self, is_open: bool, node_id: Optional[int] = None, editor: Optional[Any] = None) -> None:
        """Set the pass-through state of the gate and update checkbox widget."""
        self.is_open = bool(is_open)

        # Update checkbox widget if it exists and differs
        if dpg.does_item_exist(self._checkbox_tag):
            try:
                if dpg.get_value(self._checkbox_tag) != self.is_open:
                    dpg.set_value(self._checkbox_tag, self.is_open)
            except Exception:
                pass

        # If editor is provided or stored, refresh active highlights
        active_editor = editor or self.editor
        if active_editor:
            highlighted = getattr(active_editor, "_highlighted_nodes", [])
            if highlighted and hasattr(active_editor, "_highlight_downstream_graph"):
                try:
                    active_editor._highlight_downstream_graph(highlighted[0], dim=True)
                except Exception:
                    pass
            elif hasattr(active_editor, "recolor_all_nodes"):
                try:
                    active_editor.recolor_all_nodes()
                except Exception:
                    pass

    def set_label(self, label: str, node_id: Optional[int] = None) -> None:
        """Set the user-facing label/name of the gate."""
        clean_label = label.strip() if label else "Gate"
        self.label = clean_label

        nid = node_id or self.node_id
        if nid and dpg.does_item_exist(nid):
            try:
                dpg.configure_item(nid, label=self.label)
            except Exception:
                pass

    def set_io_type(self, new_type: IOTypes) -> None:
        """
        Dynamically update the gate's inferred IOType and update tooltip annotations.
        """
        if not isinstance(new_type, IOTypes):
            return

        self.io_type = new_type
        self.accepted_input_types = [new_type]
        self.outputs["Out"] = new_type

        # Update tooltip annotations
        type_str = str(new_type.value) if hasattr(new_type, "value") else str(new_type)

        if dpg.does_item_exist(self._in_tooltip_text_tag):
            try:
                dpg.set_value(self._in_tooltip_text_tag, f"IOType: {type_str}")
            except Exception:
                pass

        if dpg.does_item_exist(self._out_tooltip_text_tag):
            try:
                dpg.set_value(self._out_tooltip_text_tag, f"IOType: {type_str}")
            except Exception:
                pass

    def serialize(self) -> Dict[str, Any]:
        """Serialize the Gate node configuration."""
        if dpg.does_item_exist(self._name_input_tag):
            try:
                name_val = dpg.get_value(self._name_input_tag).strip()
                if name_val:
                    self.label = name_val
            except Exception:
                pass

        if dpg.does_item_exist(self._checkbox_tag):
            try:
                self.is_open = bool(dpg.get_value(self._checkbox_tag))
            except Exception:
                pass

        return {
            "kind": self.KIND,
            "uuid": self.UUID,
            "label": self.label,
            "is_open": self.is_open,
            "io_type": self.io_type.name,
        }

    def close(self) -> None:
        """Cleanup hook."""
        self.connections["Out"].clear()
