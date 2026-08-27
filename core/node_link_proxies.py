from __future__ import annotations

from typing import Any, Dict, List, Optional

import dearpygui.dearpygui as dpg
from loguru import logger

from core.input_output_types import IOTypes
from core.link_bus import link_bus


class _LinkOutNode:
    """Built-in LinkOut node proxy. Publishes incoming wired data to LinkBus."""

    KIND: str = "link_out"

    def __init__(self, link_name: str = "", uuid: Optional[str] = None) -> None:
        self.link_name: str = link_name
        self.UUID: str = uuid or str(dpg.generate_uuid())
        self.label: str = "Link Out"
        self.accepted_input_types: List[IOTypes] = [IOTypes.ANY]
        self.outputs: Dict[str, Any] = {}
        self.connections: Dict[str, List[Any]] = {}
        self._name_input_tag: str = f"_link_out_name_{self.UUID}"

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        name = self._get_live_name()
        if name:
            link_bus.publish(name, *args, **kwargs)

    def _get_live_name(self) -> str:
        if dpg.does_item_exist(self._name_input_tag):
            return dpg.get_value(self._name_input_tag).strip()
        return self.link_name.strip()

    def serialize(self) -> Dict[str, Any]:
        self.link_name = self._get_live_name()
        return {"kind": self.KIND, "uuid": self.UUID, "link_name": self.link_name}

    def close(self) -> None:
        pass


class _LinkInNode:
    """Built-in LinkIn node proxy. Subscribes to LinkBus and forwards data to downstream nodes."""

    KIND: str = "link_in"

    def __init__(self, link_name: str = "", uuid: Optional[str] = None) -> None:
        self.link_name: str = link_name
        self.UUID: str = uuid or str(dpg.generate_uuid())
        self.label: str = "Link In"
        self.accepted_input_types: List[IOTypes] = []
        self.outputs: Dict[str, IOTypes] = {"Out": IOTypes.ANY}
        self.connections: Dict[str, List[Any]] = {"Out": []}
        self._name_input_tag: str = f"_link_in_name_{self.UUID}"
        self._subscribed_name: str = ""

        if link_name:
            self._subscribe(link_name)

    def _subscribe(self, name: str) -> None:
        if name and name != self._subscribed_name:
            if self._subscribed_name:
                link_bus.unsubscribe(self._subscribed_name, self._dispatch)
            link_bus.subscribe(name, self._dispatch)
            self._subscribed_name = name

    def _unsubscribe(self) -> None:
        if self._subscribed_name:
            link_bus.unsubscribe(self._subscribed_name, self._dispatch)
            self._subscribed_name = ""

    def on_name_changed(self, new_name: str) -> None:
        new_name_clean = new_name.strip()
        self.link_name = new_name_clean
        self._subscribe(new_name_clean)

    def _dispatch(self, *args: Any, **kwargs: Any) -> None:
        for module in self.connections.get("Out", []):
            try:
                module.input_cb(*args, **kwargs)
            except Exception as e:
                logger.error(f"LinkIn '{self.link_name}': dispatch error - {e}")

    def _get_live_name(self) -> str:
        """Get the current channel name from the DPG widget or fallback to link_name."""
        if dpg.does_item_exist(self._name_input_tag):
            return dpg.get_value(self._name_input_tag).strip()
        return self.link_name.strip()

    def serialize(self) -> Dict[str, Any]:
        self.link_name = self._get_live_name()
        self.on_name_changed(self.link_name)
        return {"kind": self.KIND, "uuid": self.UUID, "link_name": self.link_name}

    def close(self) -> None:
        self._unsubscribe()

