from __future__ import annotations

from typing import Any, Dict, List, Tuple, Union

import dearpygui.dearpygui as dpg
from loguru import logger


class NodeSerializationMixin:
    """
    Mixin for workspace serialization, deserialization, and programmatic
    node/connection rebuilding.
    """

    def get_node_positions(self) -> Dict[str, Tuple[float, float]]:
        """
        Get the current position of all nodes in the editor.
        Returns a dictionary mapping module UUIDs to (x, y) positions.
        """
        positions = {}
        for node_id, instance in self.node_map.items():
            if hasattr(instance, "UUID"):
                pos = dpg.get_item_pos(node_id)
                if pos:
                    positions[instance.UUID] = (pos[0], pos[1])
        return positions

    def serialize_link_nodes(self) -> List[Dict[str, Any]]:
        """
        Serialize all native Link nodes (Link Out / Link In) to a list of dicts.
        Called by the workspace export so they survive save/reload.
        """
        from core.node_link_proxies import _LinkInNode, _LinkOutNode

        result = []
        for node_id, instance in self.node_map.items():
            if isinstance(instance, (_LinkOutNode, _LinkInNode)):
                data = instance.serialize()
                pos = dpg.get_item_pos(node_id)
                data["node_pos"] = list(pos) if pos else [100, 100]

                connections = {}
                for key, targets in instance.connections.items():
                    connections[key] = [t.UUID for t in targets if hasattr(t, "UUID")]
                data["connections"] = connections

                result.append(data)
        return result

    def rebuild_link_nodes(
        self,
        link_nodes_data: List[Dict[str, Any]],
        uuid_to_instance: Dict[str, Any],
    ) -> None:
        """
        Recreate Link nodes from serialized data and rewire physical connections.

        Args:
            link_nodes_data:  List of dicts from serialize_link_nodes().
            uuid_to_instance: Map of UUID -> module instance (for reconnecting Link In outputs).
        """
        from core.node_link_proxies import _LinkInNode, _LinkOutNode

        uuid_to_node_id: Dict[str, int] = {}
        proxy_by_uuid: Dict[str, Any] = {}

        for data in link_nodes_data:
            kind = data.get("kind")
            uuid = data.get("uuid")
            link_name = data.get("link_name", "")
            pos = tuple(data.get("node_pos", [100, 100]))

            if kind == _LinkOutNode.KIND:
                proxy = _LinkOutNode(link_name=link_name, uuid=uuid)
                node_id = self._create_link_out_node(pos, proxy)
            elif kind == _LinkInNode.KIND:
                proxy = _LinkInNode(link_name=link_name, uuid=uuid)
                node_id = self._create_link_in_node(pos, proxy)
            else:
                logger.warning(f"Unknown link node kind '{kind}' - skipping")
                continue

            uuid_to_node_id[uuid] = node_id
            proxy_by_uuid[uuid] = proxy

        # Rewire physical connections (Link In -> downstream modules)
        for data in link_nodes_data:
            uuid = data.get("uuid")
            proxy = proxy_by_uuid.get(uuid)
            if proxy is None:
                continue

            for key, target_uuids in data.get("connections", {}).items():
                for tgt_uuid in target_uuids:
                    tgt = uuid_to_instance.get(tgt_uuid)
                    if tgt and key in proxy.connections:
                        if tgt not in proxy.connections[key]:
                            proxy.connections[key].append(tgt)

                        src_node_id = uuid_to_node_id.get(uuid)
                        tgt_node_id = None
                        for nid, inst in self.node_map.items():
                            if getattr(inst, "UUID", None) == tgt_uuid:
                                tgt_node_id = nid
                                break

                        if src_node_id and tgt_node_id:
                            from_attr = self._find_output_attr(src_node_id, key)
                            to_attr = self._get_first_input_attr(tgt_node_id)
                            if from_attr and to_attr:
                                lid = dpg.generate_uuid()
                                dpg.add_node_link(from_attr, to_attr, parent=self.editor_tag, tag=lid)
                                self.link_map[lid] = (from_attr, to_attr)

        self.recolor_all_nodes()

    def connect_nodes(self, source_node_id: int, target_node_id: int, output_name: str) -> None:
        """
        Programmatically connect two nodes using an output name as key.
        Validates compatibility and adds visual link.
        """
        from core.input_output_types import IOTypes

        src = self.node_map.get(source_node_id)
        tgt = self.node_map.get(target_node_id)
        if not src or not tgt:
            logger.warning("Invalid source or target node instance.")
            return

        src_type = src.outputs.get(output_name)
        tgt_types = getattr(tgt, "accepted_input_types", [])

        is_compatible = (
            not tgt_types
            or src_type == IOTypes.ANY
            or IOTypes.ANY in tgt_types
            or src_type in tgt_types
        )

        if not is_compatible:
            logger.warning(f"Incompatible types: {src_type} -> {tgt_types}")
            return

        from_attr = self._find_output_attr(source_node_id, output_name)
        if from_attr is None:
            logger.warning(f"Output '{output_name}' not found on node {source_node_id}")
            return

        input_attr = self._get_first_input_attr(target_node_id)
        if input_attr is None:
            logger.warning(f"No input attribute found on node {target_node_id}")
            return

        if tgt in src.connections[output_name]:
            logger.info(f"Connection already exists: {src} -> {tgt}")
            return

        src.connections[output_name].append(tgt)
        link_id = dpg.generate_uuid()
        dpg.add_node_link(from_attr, input_attr, parent=self.editor_tag, tag=link_id)
        self.link_map[link_id] = (from_attr, input_attr)

    def rebuild_from_instances(self, instances: Union[List[Any], Dict[str, Any]]) -> None:
        """
        Reconstruct node graph from a list or dict of module instances.
        Typically called after workspace deserialization.
        """
        if isinstance(instances, dict):
            instances = list(instances.values())

        uuid_to_nodeid: Dict[str, int] = {}

        for win in instances:
            try:
                uuid = getattr(win, "UUID", None)
                if uuid is None:
                    continue

                pos = getattr(win, "pos", (100, 100))
                if hasattr(win, "node_pos"):
                    pos = win.node_pos

                node_id = self._create_node_visual(win, pos)
                uuid_to_nodeid[uuid] = node_id
            except Exception as e:
                logger.error(f"Error creating visual node for {win}: {e}")

        for src_win in instances:
            src_uuid = getattr(src_win, "UUID", None)
            src_node_id = uuid_to_nodeid.get(src_uuid)
            if src_node_id is None:
                continue

            for output_name, targets in getattr(src_win, "connections", {}).items():
                from_attr = self._find_output_attr(src_node_id, output_name)
                if from_attr is None:
                    logger.warning(f"Output '{output_name}' not found on node {src_uuid}")
                    continue

                for tgt_win in targets:
                    tgt_uuid = getattr(tgt_win, "UUID", None)
                    tgt_node_id = uuid_to_nodeid.get(tgt_uuid)
                    if tgt_node_id is None:
                        continue

                    to_attr = self._get_first_input_attr(tgt_node_id)
                    if to_attr is None:
                        logger.warning(f"No input attribute found on node {tgt_uuid}")
                        continue

                    try:
                        link_id = dpg.generate_uuid()
                        dpg.add_node_link(from_attr, to_attr, parent=self.editor_tag, tag=link_id)
                        self.link_map[link_id] = (from_attr, to_attr)
                    except Exception as e:
                        logger.error(f"Error creating link {from_attr} -> {to_attr}: {e}")

        # Restore pinned-to-menu-bar state
        for node_id, instance in list(self.node_map.items()):
            try:
                if getattr(instance, "node_pinned", False):
                    self._pin_node_to_menu_bar(0, None, node_id)
            except Exception as e:
                logger.warning(f"Failed to restore pinned state for {node_id}: {e}")

        try:
            self.recolor_all_nodes()
        except Exception as e:
            logger.warning(f"Failed to recolor nodes: {e}")

