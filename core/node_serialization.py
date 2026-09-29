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
        Serialize all native built-in nodes (Link Out, Link In, Gate, Working Directory) to a list of dicts.
        Called by the workspace export so they survive save/reload.
        """
        from core.node_link_proxies import _GateNode, _LinkInNode, _LinkOutNode, _WorkingDirNode

        result = []
        for node_id, instance in self.node_map.items():
            if isinstance(instance, (_LinkOutNode, _LinkInNode, _GateNode, _WorkingDirNode)):
                data = instance.serialize()
                pos = dpg.get_item_pos(node_id)
                data["node_pos"] = list(pos) if pos else [100, 100]

                connections = {}
                for key, targets in instance.connections.items():
                    connections[key] = [t.UUID for t in targets if hasattr(t, "UUID")]
                data["connections"] = connections

                # For Gate nodes, also capture incoming connection details
                if isinstance(instance, _GateNode):
                    gate_in_attr = f"{node_id}_In"
                    incoming = None
                    for lid, (f_attr, t_attr) in self.link_map.items():
                        if t_attr == gate_in_attr or str(t_attr) == str(gate_in_attr):
                            src_nid = dpg.get_item_parent(f_attr)
                            src_inst = self.node_map.get(src_nid)
                            if src_inst and hasattr(src_inst, "UUID"):
                                src_key = self._find_output_key(src_nid, f_attr)
                                incoming = {
                                    "source_uuid": src_inst.UUID,
                                    "output_key": src_key or "Out",
                                }
                                break
                    if incoming:
                        data["incoming"] = incoming

                result.append(data)
        return result

    def rebuild_link_nodes(
        self,
        link_nodes_data: List[Dict[str, Any]],
        uuid_to_instance: Dict[str, Any],
    ) -> None:
        """
        Recreate built-in nodes (Link Out, Link In, Gate, Working Directory) from serialized data and rewire connections.

        Args:
            link_nodes_data:  List of dicts from serialize_link_nodes().
            uuid_to_instance: Map of UUID -> module instance.
        """
        from core.node_link_proxies import _GateNode, _LinkInNode, _LinkOutNode, _WorkingDirNode

        uuid_to_node_id: Dict[str, int] = {}
        proxy_by_uuid: Dict[str, Any] = {}

        # 1. Instantiate all built-in nodes
        for data in link_nodes_data:
            kind = data.get("kind")
            uuid = data.get("uuid")
            pos = tuple(data.get("node_pos", [100, 100]))

            if kind == _LinkOutNode.KIND:
                link_name = data.get("link_name", "")
                proxy = _LinkOutNode(link_name=link_name, uuid=uuid)
                node_id = self._create_link_out_node(pos, proxy)
            elif kind == _LinkInNode.KIND:
                link_name = data.get("link_name", "")
                proxy = _LinkInNode(link_name=link_name, uuid=uuid)
                node_id = self._create_link_in_node(pos, proxy)
            elif kind == _GateNode.KIND:
                label = data.get("label", "Gate")
                is_open = data.get("is_open", True)
                io_type = data.get("io_type", "ANY")
                proxy = _GateNode(label=label, uuid=uuid, is_open=is_open, io_type=io_type)
                node_id = self._create_gate_node(pos, proxy)
            elif kind == _WorkingDirNode.KIND:
                label = data.get("label", "Working Directory")
                auto_emit = data.get("auto_emit", True)
                proxy = _WorkingDirNode(label=label, uuid=uuid, auto_emit=auto_emit)
                node_id = self._create_working_dir_node(pos, proxy)
            else:
                logger.warning(f"Unknown built-in node kind '{kind}' - skipping")
                continue

            uuid_to_node_id[uuid] = node_id
            proxy_by_uuid[uuid] = proxy
            uuid_to_instance[uuid] = proxy

        # 2. Rewire outgoing connections (built-in node -> downstream modules / gates)
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

        # 3. Rewire incoming connections for Gate nodes
        for data in link_nodes_data:
            if data.get("kind") == _GateNode.KIND and "incoming" in data:
                inc = data["incoming"]
                src_uuid = inc.get("source_uuid")
                src_key = inc.get("output_key", "Out")
                gate_uuid = data.get("uuid")
                gate_proxy = proxy_by_uuid.get(gate_uuid)
                src_inst = uuid_to_instance.get(src_uuid)

                if gate_proxy and src_inst:
                    if src_key not in src_inst.connections:
                        src_inst.connections[src_key] = []
                    if gate_proxy not in src_inst.connections[src_key]:
                        src_inst.connections[src_key].append(gate_proxy)

                    src_node_id = None
                    for nid, inst in self.node_map.items():
                        if getattr(inst, "UUID", None) == src_uuid:
                            src_node_id = nid
                            break

                    gate_node_id = uuid_to_node_id.get(gate_uuid)
                    if src_node_id and gate_node_id:
                        from_attr = self._find_output_attr(src_node_id, src_key)
                        gate_in_attr = f"{gate_node_id}_In"
                        if from_attr and dpg.does_item_exist(gate_in_attr):
                            lid = dpg.generate_uuid()
                            dpg.add_node_link(from_attr, gate_in_attr, parent=self.editor_tag, tag=lid)
                            self.link_map[lid] = (from_attr, gate_in_attr)

        # 4. Refresh all Gate IOTypes
        for proxy in proxy_by_uuid.values():
            if getattr(proxy, "KIND", "") == _GateNode.KIND and hasattr(self, "_refresh_gate_io_type"):
                self._refresh_gate_io_type(proxy)

        # 5. Broadcast working directory for WorkingDir nodes
        for proxy in proxy_by_uuid.values():
            if getattr(proxy, "KIND", "") == _WorkingDirNode.KIND:
                proxy._update_ui_text()
                if getattr(proxy, "auto_emit", True):
                    proxy.emit_directory()

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

