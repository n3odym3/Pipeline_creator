from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger


class NodeHighlightMixin:
    """
    Mixin that provides all node/link colorization, highlighting, and dimming
    logic for the NodeEditor. Assumes NodeEditor.__init__ has initialized the
    required theme attributes and state collections.
    """

    node_map: Dict[int, Any]
    link_map: Dict[int, Tuple[int, int]]
    editor_tag: str | int
    highlight_theme: int
    virtual_link_theme: int
    red_node_theme: int
    red_link_theme: int
    dimmed_node_theme: int
    dimmed_link_theme: int
    _highlighted_nodes: List[int]
    _highlighted_downstream_nodes: Dict[int, int]
    _virtual_links: List[int]
    _highlighted_downstream_links: Dict[int, int]
    _dimmed_nodes: List[int]
    _dimmed_links: List[int]
    _highlighted_text_items: List[int]
    _color_theme_cache: Dict[int, Tuple[int, int]]

    def recolor_all_nodes(self) -> None:
        """
        Refresh all themes and re-apply styles to current highlights.
        Ensures colorblind overrides are applied live.
        """
        from config.theme_manager import theme_manager

        self._color_theme_cache.clear()

        self.highlight_theme = theme_manager.create_highlight_theme()
        self.virtual_link_theme = theme_manager.create_virtual_link_theme()
        self.red_node_theme = theme_manager.create_red_node_theme()
        self.red_link_theme = theme_manager.create_red_link_theme()
        self.dimmed_node_theme = theme_manager.create_dimmed_node_theme()
        self.dimmed_link_theme = theme_manager.create_dimmed_link_theme()

        for node_id in self._highlighted_nodes:
            self._apply_node_style(node_id, self.highlight_theme)
        for node_id, idx in self._highlighted_downstream_nodes.items():
            node_theme, _, _ = self._get_output_color_theme(idx)
            self._apply_node_style(node_id, node_theme)
        for link_id in self._virtual_links:
            self._apply_link_style(link_id, self.virtual_link_theme)
        for link_id, idx in self._highlighted_downstream_links.items():
            _, link_theme, _ = self._get_output_color_theme(idx)
            self._apply_link_style(link_id, link_theme)
        for node_id in self._dimmed_nodes:
            self._apply_node_style(node_id, self.dimmed_node_theme)
        for link_id in self._dimmed_links:
            self._apply_link_style(link_id, self.dimmed_link_theme)

    def _apply_node_style(self, node_id: int, theme: int = 0) -> None:
        """
        Apply a DPG theme to a node and its corresponding module window.
        Use theme=0 to reset to default.

        Fusion-aware: when a module has been merged into another window its own
        winID is hidden. Instead we bind the theme directly to the module's
        top-level content items (_original_children) so they override the host
        window's inherited theme.
        """
        if not dpg.does_item_exist(node_id):
            return
        dpg.bind_item_theme(node_id, theme)

        instance = self.node_map.get(node_id)
        if not instance:
            return

        merged_into = getattr(instance, "merged_into", None)
        wrapper_id = getattr(instance, "_merge_wrapper_id", None)

        if merged_into and wrapper_id and dpg.does_item_exist(wrapper_id):
            bind_theme = theme
            if theme == 0:
                from config.theme_manager import theme_manager

                bind_theme = theme_manager.global_theme
            dpg.bind_item_theme(wrapper_id, bind_theme)
        else:
            win_id = getattr(instance, "winID", None)
            if win_id and dpg.does_item_exist(win_id):
                dpg.bind_item_theme(win_id, theme)

            sub_windows = getattr(instance, "sub_windows", [])
            for sub_win in sub_windows:
                if dpg.does_item_exist(sub_win):
                    dpg.bind_item_theme(sub_win, theme)

    def _apply_link_style(self, link_id: int, theme: int = 0) -> None:
        """
        Apply a DPG theme to a link.
        Use theme=0 to reset to default.
        """
        if dpg.does_item_exist(link_id):
            dpg.bind_item_theme(link_id, theme)

    def _clear_highlights(self) -> None:
        """Remove temporary visual links and highlights."""
        for link_id in self._virtual_links:
            if dpg.does_item_exist(link_id):
                dpg.delete_item(link_id)
        self._virtual_links.clear()

        for node_id in self._highlighted_nodes:
            self._apply_node_style(node_id, 0)
        self._highlighted_nodes.clear()

        for node_id in self._highlighted_downstream_nodes.keys():
            self._apply_node_style(node_id, 0)
        self._highlighted_downstream_nodes.clear()

        for link_id in self._highlighted_downstream_links.keys():
            self._apply_link_style(link_id, 0)
        self._highlighted_downstream_links.clear()

        for t_id in self._highlighted_text_items:
            if dpg.does_item_exist(t_id):
                dpg.configure_item(t_id, color=[-255, 0, 0, 0])
        self._highlighted_text_items.clear()

        for n_id in self._dimmed_nodes:
            self._apply_node_style(n_id, 0)
        self._dimmed_nodes.clear()

        for l_id in self._dimmed_links:
            self._apply_link_style(l_id, 0)
        self._dimmed_links.clear()

    def _dim_all_items(self) -> None:
        """Apply dimmed style to all nodes and links in the editor."""
        if not hasattr(self, "_dimmed_nodes"):
            self._dimmed_nodes = []
        if not hasattr(self, "_dimmed_links"):
            self._dimmed_links = []

        for node_id in self.node_map:
            self._apply_node_style(node_id, self.dimmed_node_theme)
            if node_id not in self._dimmed_nodes:
                self._dimmed_nodes.append(node_id)

        for link_id in self.link_map:
            self._apply_link_style(link_id, self.dimmed_link_theme)
            if link_id not in self._dimmed_links:
                self._dimmed_links.append(link_id)

    def _draw_virtual_link(self, link_out_id: int, link_in_id: int, theme_override: int = 0) -> int:
        """Create a temporary visual link between a Link Out and its matching Link In."""
        in_attr = None
        for child in dpg.get_item_children(link_out_id, 1):
            if dpg.get_item_type(child) == "mvAppItemType::mvNodeAttribute":
                try:
                    attr_type = int(dpg.get_item_configuration(child).get("attribute_type", -1))
                    if attr_type == int(dpg.mvNode_Attr_Input):
                        in_attr = child
                        break
                except (ValueError, TypeError):
                    continue

        out_attr = f"{link_in_id}_Out"
        if in_attr and dpg.does_item_exist(out_attr):
            link_id = dpg.add_node_link(out_attr, in_attr, parent=self.editor_tag)
            theme = theme_override if theme_override != 0 else self.virtual_link_theme
            self._apply_link_style(link_id, theme)
            self._virtual_links.append(link_id)
            return link_id
        return 0

    def _highlight_link_nodes(self, link_name: str, dim: bool = True) -> None:
        """Highlight Link nodes sharing a channel name and draw virtual links between them."""
        if not link_name:
            return

        if dim:
            self._dim_all_items()

        link_out_nodes = []
        link_in_nodes = []

        for node_id, instance in self.node_map.items():
            if getattr(instance, "KIND", "") in ("link_out", "link_in"):
                if instance._get_live_name() == link_name:
                    self._apply_node_style(node_id, self.highlight_theme)
                    if node_id not in self._highlighted_nodes:
                        self._highlighted_nodes.append(node_id)

                    if getattr(instance, "KIND", "") == "link_out":
                        link_out_nodes.append(node_id)
                    else:
                        link_in_nodes.append(node_id)

        for out_node in link_out_nodes:
            for in_node in link_in_nodes:
                self._draw_virtual_link(out_node, in_node)

    def _get_output_color_theme(self, output_index: int) -> Tuple[int, int, Tuple[int, int, int, int]]:
        """
        Return (node_theme, link_theme, text_color) for a given output index.
        Themes are cached so they are only created once per hue.
        """
        from config.theme_manager import theme_manager

        return theme_manager.get_output_color_theme_from_cache(output_index, self._color_theme_cache)

    def _on_highlight_mode_changed(self, sender: Any, app_data: str, user_data: Any = None) -> None:
        """Callback when the highlight mode combo changes."""
        mapping = {
            "Show All": "show_all",
            "Closest": "closest",
            "Selection": "selection",
            "None": "none",
        }
        self.highlight_mode = mapping.get(app_data, str(app_data).lower().replace(" ", "_"))
        self._clear_highlights()
        if self.highlight_mode == "selection" and dpg.does_item_exist(self.editor_tag):
            selected = dpg.get_selected_nodes(self.editor_tag)
            if selected:
                self._highlight_selected_nodes(selected)

    def _highlight_selected_nodes(self, selected_node_ids: Optional[List[int]] = None) -> None:
        """
        Highlight selected nodes and their corresponding module windows.
        Each selected node and its associated window receives a distinct color theme.
        """
        if selected_node_ids is None:
            selected_node_ids = dpg.get_selected_nodes(self.editor_tag) if dpg.does_item_exist(self.editor_tag) else []

        valid_nodes = [nid for nid in selected_node_ids if nid in self.node_map]

        self._clear_highlights()

        if not valid_nodes:
            return

        self._dim_all_items()

        for idx, node_id in enumerate(valid_nodes):
            node_theme, _, _ = self._get_output_color_theme(idx)
            self._apply_node_style(node_id, node_theme)
            self._highlighted_downstream_nodes[node_id] = idx

        # Also highlight links between any two selected nodes
        valid_set = set(valid_nodes)
        for link_id, (from_attr, to_attr) in self.link_map.items():
            if dpg.does_item_exist(from_attr) and dpg.does_item_exist(to_attr):
                from_node = dpg.get_item_parent(from_attr)
                to_node = dpg.get_item_parent(to_attr)
                if from_node in valid_set and to_node in valid_set:
                    idx = self._highlighted_downstream_nodes.get(from_node, 0)
                    _, link_theme, _ = self._get_output_color_theme(idx)
                    self._apply_link_style(link_id, link_theme)
                    self._highlighted_downstream_links[link_id] = idx

    def _highlight_downstream_graph(self, start_node_id: int, dim: bool = True) -> None:
        """Highlight downstream nodes (including virtual hops) according to highlight_mode."""
        mode = getattr(self, "highlight_mode", "closest")
        if mode == "none":
            return

        if dim:
            self._dim_all_items()

        if mode == "show_all":
            self._highlight_output_dependent(start_node_id, max_depth=-1)
        elif mode == "closest":
            self._highlight_output_dependent(start_node_id, max_depth=1)

    def _highlight_output_dependent(self, start_node_id: int, max_depth: int = -1) -> None:
        """
        Highlight downstream nodes with branch coloring and virtual hops.
        For regular (non-link) node starts only.
        """
        instance = self.node_map.get(start_node_id)
        if instance is None:
            return

        output_keys = list(getattr(instance, "outputs", {}).keys())
        colored_nodes: Dict[int, int] = {}
        colored_links: Dict[int, int] = {}
        visited = {start_node_id}
        to_process: List[Tuple[int, int, int]] = []

        for out_idx, out_key in enumerate(output_keys):
            out_attr = self._find_output_attr(start_node_id, out_key)

            if out_attr is not None:
                for child in dpg.get_item_children(out_attr, 1):
                    if dpg.get_item_type(child) == "mvAppItemType::mvText" and dpg.get_value(child) == out_key:
                        _, _, text_color = self._get_output_color_theme(out_idx)
                        dpg.configure_item(child, color=text_color)
                        self._highlighted_text_items.append(child)
                        break

            for link_id, (from_attr, to_attr) in self.link_map.items():
                if not dpg.does_item_exist(to_attr):
                    continue
                if dpg.get_item_parent(from_attr) != start_node_id:
                    continue
                if self._find_output_key(start_node_id, from_attr) != out_key:
                    continue
                colored_links[link_id] = out_idx
                target = dpg.get_item_parent(to_attr)
                target_inst = self.node_map.get(target)
                next_depth = 0 if getattr(target_inst, "KIND", "") in ("link_in", "link_out") else 1
                if target not in visited:
                    visited.add(target)
                    colored_nodes[target] = out_idx
                    to_process.append((target, out_idx, next_depth))

        while to_process:
            current, out_idx, depth = to_process.pop(0)
            curr_inst = self.node_map.get(current)

            if getattr(curr_inst, "KIND", "") == "link_out":
                link_name = curr_inst._get_live_name()
                if link_name:
                    for node_id, inst in self.node_map.items():
                        if getattr(inst, "KIND", "") == "link_in" and inst._get_live_name() == link_name:
                            _, link_theme, _ = self._get_output_color_theme(out_idx)
                            self._draw_virtual_link(current, node_id, theme_override=link_theme)
                            if node_id not in visited:
                                visited.add(node_id)
                                colored_nodes[node_id] = out_idx
                                to_process.append((node_id, out_idx, depth))

            if max_depth != -1 and depth >= max_depth:
                continue

            for link_id, (from_attr, to_attr) in self.link_map.items():
                if dpg.get_item_parent(from_attr) == current and dpg.does_item_exist(to_attr):
                    colored_links[link_id] = out_idx
                    target = dpg.get_item_parent(to_attr)
                    target_inst = self.node_map.get(target)
                    next_depth = depth if getattr(target_inst, "KIND", "") in ("link_out", "link_in") else depth + 1
                    if target not in visited:
                        visited.add(target)
                        colored_nodes[target] = out_idx
                        to_process.append((target, out_idx, next_depth))

        self._apply_node_style(start_node_id, self.red_node_theme)
        if start_node_id not in self._highlighted_nodes:
            self._highlighted_nodes.append(start_node_id)

        for n_id, idx in colored_nodes.items():
            node_theme, _, _ = self._get_output_color_theme(idx)
            self._apply_node_style(n_id, node_theme)
            self._highlighted_downstream_nodes[n_id] = idx

        for l_id, idx in colored_links.items():
            _, link_theme, _ = self._get_output_color_theme(idx)
            self._apply_link_style(l_id, link_theme)
            self._highlighted_downstream_links[l_id] = idx

    def _highlight_physical_from(self, start_node_id: int) -> None:
        """
        BFS downstream from start_node_id following physical and virtual hops.
        Does NOT repaint the start node (caller is responsible for its color).
        Used when the start node is a Link In already styled by _highlight_link_nodes.
        """
        mode = getattr(self, "highlight_mode", "closest")
        if mode == "none":
            return

        max_depth = -1 if mode == "show_all" else 1

        instance = self.node_map.get(start_node_id)
        if instance is None:
            return

        output_keys = list(getattr(instance, "outputs", {}).keys())
        colored_nodes: Dict[int, int] = {}
        colored_links: Dict[int, int] = {}
        visited = {start_node_id}
        to_process: List[Tuple[int, int, int]] = []

        for out_idx, out_key in enumerate(output_keys):
            out_attr = self._find_output_attr(start_node_id, out_key)

            if out_attr is not None:
                for child in dpg.get_item_children(out_attr, 1):
                    if dpg.get_item_type(child) == "mvAppItemType::mvText" and dpg.get_value(child) == out_key:
                        dpg.configure_item(child, color=[180, 180, 180, 255])
                        self._highlighted_text_items.append(child)
                        break

            for link_id, (from_attr, to_attr) in self.link_map.items():
                if not dpg.does_item_exist(to_attr):
                    continue
                if dpg.get_item_parent(from_attr) != start_node_id:
                    continue
                if self._find_output_key(start_node_id, from_attr) != out_key:
                    continue
                colored_links[link_id] = out_idx
                target = dpg.get_item_parent(to_attr)
                target_inst = self.node_map.get(target)
                next_depth = 0 if getattr(target_inst, "KIND", "") in ("link_out", "link_in") else 1
                if target not in visited:
                    visited.add(target)
                    colored_nodes[target] = out_idx
                    to_process.append((target, out_idx, next_depth))

        while to_process:
            current, out_idx, depth = to_process.pop(0)
            curr_inst = self.node_map.get(current)

            if getattr(curr_inst, "KIND", "") == "link_out":
                link_name = curr_inst._get_live_name()
                if link_name:
                    for node_id, inst in self.node_map.items():
                        if getattr(inst, "KIND", "") == "link_in" and inst._get_live_name() == link_name:
                            self._draw_virtual_link(current, node_id)
                            if node_id not in visited:
                                visited.add(node_id)
                                colored_nodes[node_id] = out_idx
                                to_process.append((node_id, out_idx, depth))

            if max_depth != -1 and depth >= max_depth:
                continue

            for link_id, (from_attr, to_attr) in self.link_map.items():
                if dpg.get_item_parent(from_attr) == current and dpg.does_item_exist(to_attr):
                    colored_links[link_id] = out_idx
                    target = dpg.get_item_parent(to_attr)
                    target_inst = self.node_map.get(target)
                    next_depth = depth if getattr(target_inst, "KIND", "") in ("link_out", "link_in") else depth + 1
                    if target not in visited:
                        visited.add(target)
                        colored_nodes[target] = out_idx
                        to_process.append((target, out_idx, next_depth))

        for n_id, idx in colored_nodes.items():
            node_theme, _, _ = self._get_output_color_theme(idx)
            self._apply_node_style(n_id, node_theme)
            self._highlighted_downstream_nodes[n_id] = idx

        for l_id, idx in colored_links.items():
            _, link_theme, _ = self._get_output_color_theme(idx)
            self._apply_link_style(l_id, link_theme)
            self._highlighted_downstream_links[l_id] = idx

