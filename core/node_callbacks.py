from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from core.input_output_types import IOTypes


class NodeCallbacksMixin:
    """
    Mixin for all DPG event/input callbacks: mouse clicks, node double-click,
    link creation, link deletion, and the incompatible-types warning dialog.
    """

    def right_click_callback(self, sender: int, app_data: Any, user_data: Any = None, *args: Any) -> None:
        """Open popup menu at the mouse location if the editor is hovered."""
        if not dpg.is_item_hovered(self.editor_tag):
            return

        self.mouse_pos, absolute_mouse_pos = self.get_mouse_pos()

        # Check if any node is hovered
        hovered_node = None
        for node_id in self.node_map:
            if dpg.get_item_state(node_id).get("hovered") or dpg.is_item_hovered(node_id):
                hovered_node = node_id
                break

        if hovered_node:
            if dpg.does_item_exist(self.popup_tag):
                dpg.configure_item(self.popup_tag, show=False)

            dpg.delete_item(self.node_popup_tag, children_only=True)

            instance = self.node_map[hovered_node]

            def _del_cb(s: Any, a: Any, u: Any, *args: Any, **kwargs: Any) -> None:
                dpg.configure_item(self.node_popup_tag, show=False)
                self.delete_node(s, a, u)

            def _pin_cb(s: Any, a: Any, u: Any, *args: Any, **kwargs: Any) -> None:
                dpg.configure_item(self.node_popup_tag, show=False)
                self._pin_node_to_menu_bar(s, a, u)

            def _rename_cb(s: Any, a: Any, u: Any, *args: Any, **kwargs: Any) -> None:
                dpg.configure_item(self.node_popup_tag, show=False)
                self._open_rename_node_modal(u)

            tutorial_path = None
            source_file = getattr(instance.__class__, "_source_file", None)
            if source_file:
                potential_path = Path(source_file).parent / "tutorial.json"
                if potential_path.exists():
                    tutorial_path = str(potential_path)

            if tutorial_path:
                from core.tutorial_manager import tutorial_manager

                def _tut_cb(s: Any, a: Any, u: Any, *args: Any, **kwargs: Any) -> None:
                    dpg.configure_item(self.node_popup_tag, show=False)
                    tutorial_manager.load_and_play(u)

                dpg.add_button(
                    label="Show Tutorial",
                    callback=_tut_cb,
                    user_data=tutorial_path,
                    parent=self.node_popup_tag,
                )
                dpg.add_separator(parent=self.node_popup_tag)

            kind = getattr(instance, "KIND", "")
            if kind not in ("link_out", "link_in"):
                dpg.add_button(
                    label="Rename Node",
                    callback=_rename_cb,
                    user_data=hovered_node,
                    parent=self.node_popup_tag,
                )
                dpg.add_separator(parent=self.node_popup_tag)
                dpg.add_button(
                    label="Send to Menu Bar",
                    callback=_pin_cb,
                    user_data=hovered_node,
                    parent=self.node_popup_tag,
                )
                dpg.add_separator(parent=self.node_popup_tag)

            dpg.add_button(
                label="Delete Node",
                callback=_del_cb,
                user_data=hovered_node,
                parent=self.node_popup_tag,
            )
            dpg.configure_item(self.node_popup_tag, pos=absolute_mouse_pos, show=True)
            return

        # No node hovered -> hide node menu, open global search popup
        if hasattr(self, "node_popup_tag") and dpg.does_item_exist(self.node_popup_tag):
            dpg.configure_item(self.node_popup_tag, show=False)

        search_tag = f"{self.popup_tag}_search"
        if dpg.does_item_exist(search_tag):
            dpg.set_value(search_tag, "")
            self._filter_module_list("")

        dpg.configure_item(self.popup_tag, pos=absolute_mouse_pos, show=True)
        dpg.set_item_width(self.popup_tag, self.scale(self.popup_width))
        dpg.set_item_height(self.popup_tag, self.scale(self.popup_height))
        dpg.focus_item(search_tag)

    def _is_any_popup_child_hovered(self) -> bool:
        """Recursively check if any child of the popup windows is hovered."""

        def _check_children(item_id: int) -> bool:
            try:
                if dpg.is_item_hovered(item_id):
                    return True
            except Exception:
                pass
            for slot in (0, 1):
                try:
                    children = dpg.get_item_children(item_id, slot)
                    if children:
                        for child in children:
                            if _check_children(child):
                                return True
                except Exception:
                    pass
            return False

        main_hovered = _check_children(self.popup_tag) if dpg.does_item_exist(self.popup_tag) else False
        node_hovered = False
        if hasattr(self, "node_popup_tag") and dpg.does_item_exist(self.node_popup_tag):
            node_hovered = _check_children(self.node_popup_tag)
        return main_hovered or node_hovered

    def left_click_callback(self, sender: int, app_data: Any, user_data: Any = None, *args: Any) -> None:
        """Handle clicking outside the popup and node highlighting."""
        # 1. Close popup menu when clicking outside it
        if dpg.does_item_exist(self.popup_tag) and dpg.is_item_shown(self.popup_tag):
            if self._popup_click_handled:
                self._popup_click_handled = False
            elif not self._is_any_popup_child_hovered():
                dpg.configure_item(self.popup_tag, show=False)

        if (
            hasattr(self, "node_popup_tag")
            and dpg.does_item_exist(self.node_popup_tag)
            and dpg.is_item_shown(self.node_popup_tag)
        ):
            if not self._is_any_popup_child_hovered():
                dpg.configure_item(self.node_popup_tag, show=False)

        # 2. Only process node selection/highlighting if the node editor window is shown,
        #    and the node editor canvas is hovered (active and in foreground, not obscured by other windows).
        if not dpg.does_item_exist(self.winID) or not dpg.is_item_shown(self.winID):
            return

        if not dpg.does_item_exist(self.editor_tag) or not dpg.is_item_hovered(self.editor_tag):
            return

        self._clear_highlights()

        clicked_node_id = None

        for node_id in self.node_map:
            try:
                rect_min = dpg.get_item_rect_min(node_id)
                rect_max = dpg.get_item_rect_max(node_id)
                mouse = dpg.get_mouse_pos(local=False)
                if (rect_min[0] <= mouse[0] <= rect_max[0]) and (rect_min[1] <= mouse[1] <= rect_max[1]):
                    clicked_node_id = node_id
                    break
            except Exception:
                continue

        if clicked_node_id:
            if getattr(self, "highlight_mode", "none") == "none":
                return

            if getattr(self, "highlight_mode", "none") == "selection":
                selected = dpg.get_selected_nodes(self.editor_tag) if dpg.does_item_exist(self.editor_tag) else []
                if clicked_node_id not in selected:
                    selected = list(selected) + [clicked_node_id]
                self._highlight_selected_nodes(selected)
                return

            self._dim_all_items()
            instance = self.node_map.get(clicked_node_id)
            if not instance:
                return

            kind = getattr(instance, "KIND", "")

            if kind in ("link_out", "link_in"):
                name = instance._get_live_name()
                if name:
                    self._highlight_link_nodes(name, dim=False)

                    if kind == "link_in":
                        bfs_starts = [clicked_node_id]
                    else:
                        bfs_starts = [
                            nid
                            for nid, inst in list(self.node_map.items())
                            if getattr(inst, "KIND", "") == "link_in" and inst._get_live_name() == name
                        ]

                    for start in bfs_starts:
                        self._highlight_physical_from(start)
            else:
                self._highlight_downstream_graph(clicked_node_id, dim=False)

    def left_release_callback(self, sender: int, app_data: Any, user_data: Any = None, *args: Any) -> None:
        """Handle mouse release to update selection highlighting if in Selection mode."""
        if getattr(self, "highlight_mode", "none") != "selection":
            return

        if not dpg.does_item_exist(self.winID) or not dpg.is_item_shown(self.winID):
            return

        if not dpg.does_item_exist(self.editor_tag) or not dpg.is_item_hovered(self.editor_tag):
            return

        selected = dpg.get_selected_nodes(self.editor_tag) if dpg.does_item_exist(self.editor_tag) else []
        if selected:
            self._highlight_selected_nodes(selected)
        else:
            self._clear_highlights()

    def _on_node_double_click(self, sender: int, app_data: Any, user_data: Any = None, *args: Any) -> None:
        """Show and focus the module window when a node is double-clicked."""
        if not dpg.does_item_exist(self.editor_tag):
            return
        if not dpg.is_item_hovered(self.editor_tag):
            return

        try:
            selected_nodes = dpg.get_selected_nodes(self.editor_tag)
        except Exception:
            return

        if not selected_nodes:
            return

        node_id = selected_nodes[0]
        instance = self.node_map.get(node_id)
        if instance is None:
            return

        win_id = getattr(instance, "winID", None)
        if win_id and dpg.does_item_exist(win_id):
            dpg.show_item(win_id)
            dpg.focus_item(win_id)

        sub_windows = getattr(instance, "sub_windows", [])
        for sub_win in sub_windows:
            if dpg.does_item_exist(sub_win):
                dpg.show_item(sub_win)
                dpg.focus_item(sub_win)

    def link_callback(self, sender: int, app_data: Tuple[int, int], user_data: Any = None, *args: Any) -> None:
        """
        Callback triggered when a link is created between two node attributes.
        Validates type compatibility before adding.
        """
        link_id = dpg.generate_uuid()
        attr_a, attr_b = app_data

        node_a = dpg.get_item_parent(attr_a)
        node_b = dpg.get_item_parent(attr_b)

        src_key = self._find_output_key(node_a, attr_a)
        if src_key:
            from_node, to_node = node_a, node_b
            from_attr, to_attr = attr_a, attr_b
        else:
            src_key = self._find_output_key(node_b, attr_b)
            if src_key:
                from_node, to_node = node_b, node_a
                from_attr, to_attr = attr_b, attr_a
            else:
                logger.warning("Failed to resolve output key from attribute.")
                return

        src = self.node_map.get(from_node)
        tgt = self.node_map.get(to_node)

        if src is None or tgt is None:
            return

        src_type = src.outputs.get(src_key)
        tgt_types = getattr(tgt, "accepted_input_types", [])

        is_compatible = (
            not tgt_types
            or src_type == IOTypes.ANY
            or IOTypes.ANY in tgt_types
            or src_type in tgt_types
        )

        if not is_compatible:
            logger.warning(f"Incompatible types: {src_type} -> {tgt_types}")
            self._show_incompatible_types_warning(src_type, tgt_types)
            return

        if tgt not in src.connections[src_key]:
            src.connections[src_key].append(tgt)
            dpg.add_node_link(from_attr, to_attr, parent=self.editor_tag, tag=link_id)
            self.link_map[link_id] = (from_attr, to_attr)

    def delink_callback(self, sender: int, app_data: int, user_data: Any = None, *args: Any) -> None:
        """
        Callback triggered when a link is removed manually.
        Updates connection tracking and internal mappings.

        Virtual links (highlight overlays) are NOT in link_map - if the user
        breaks one, just clear all highlights and bail out gracefully.
        """
        link_id = app_data

        if link_id not in self.link_map:
            if link_id in self._virtual_links:
                self._virtual_links.remove(link_id)
            self._clear_highlights()
            return

        from_attr, to_attr = self.link_map.pop(link_id)
        from_node = dpg.get_item_parent(from_attr)
        to_node = dpg.get_item_parent(to_attr)
        src = self.node_map.get(from_node)
        tgt = self.node_map.get(to_node)

        if src and tgt:
            src_key = self._find_output_key(from_node, from_attr)
            if src_key and tgt in src.connections.get(src_key, []):
                src.connections[src_key].remove(tgt)
            elif src_key is None:
                logger.warning("Unable to disconnect nodes cleanly.")

        dpg.delete_item(link_id)

    def _show_incompatible_types_warning(self, src_type: Any, tgt_types: List[Any]) -> None:
        """Show a popup warning when trying to connect incompatible types."""
        if not self.show_type_warnings:
            return

        src_name = str(src_type.value) if hasattr(src_type, "value") else str(src_type)
        tgt_names = [str(t.value) if hasattr(t, "value") else str(t) for t in tgt_types]

        warning_tag = "incompatible_types_warning"
        checkbox_tag = "dont_show_again_checkbox"

        if dpg.does_item_exist(warning_tag):
            dpg.delete_item(warning_tag)

        mouse_pos = dpg.get_mouse_pos(local=False)

        def on_ok(*args: Any, **kwargs: Any) -> None:
            if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
                self.show_type_warnings = False
            dpg.delete_item(warning_tag)

        with dpg.window(
            label="Incompatible Types",
            modal=False,
            tag=warning_tag,
            width=-1,
            height=-1,
            pos=(mouse_pos[0] - 175, mouse_pos[1] + 10),
        ):
            dpg.add_text("Cannot connect: incompatible types", color=(255, 100, 100))
            dpg.add_spacer(height=5)
            dpg.add_text(f"Output type: {src_name}", color=(255, 200, 100))
            dpg.add_text(f"Accepted types: {', '.join(tgt_names)}", color=(100, 200, 100), wrap=330)
            dpg.add_spacer(height=10)
            dpg.add_checkbox(label="Don't show again", tag=checkbox_tag)
            dpg.add_spacer(height=5)
            dpg.add_button(label="OK", callback=on_ok, width=-1)

    def _open_rename_node_modal(self, node_id: int) -> None:
        """Open a modal dialog to rename the node and its corresponding window."""
        instance = self.node_map.get(node_id)
        if not instance:
            return

        old_label = getattr(instance, "label", instance.__class__.__name__)
        s = self.scale

        if dpg.does_item_exist("rename_node_modal"):
            dpg.delete_item("rename_node_modal")

        viewport_w = dpg.get_viewport_client_width() or 800
        viewport_h = dpg.get_viewport_client_height() or 600
        modal_w = int(s(300))
        modal_h = int(s(120))
        pos = [(viewport_w - modal_w) // 2, (viewport_h - modal_h) // 2]

        with dpg.window(
            label="Rename Node",
            modal=True,
            show=True,
            tag="rename_node_modal",
            no_title_bar=False,
            pos=pos,
            width=modal_w,
            height=modal_h,
        ):
            dpg.add_text(f"Rename node '{old_label}':")
            input_tag = dpg.add_input_text(
                default_value=old_label,
                on_enter=True,
                width=-1,
                callback=lambda s, a, u: self._rename_node_confirmed(node_id, dpg.get_value(input_tag)),
            )
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Rename",
                    width=int(s(80)),
                    callback=lambda: self._rename_node_confirmed(node_id, dpg.get_value(input_tag)),
                )
                dpg.add_button(
                    label="Cancel",
                    width=int(s(80)),
                    callback=lambda: dpg.delete_item("rename_node_modal"),
                )

    def _rename_node_confirmed(self, node_id: int, new_label: str) -> None:
        """Apply the rename operation to the node, its corresponding window, and pinning list."""
        if dpg.does_item_exist("rename_node_modal"):
            dpg.delete_item("rename_node_modal")

        instance = self.node_map.get(node_id)
        if not instance:
            return

        new_label = new_label.strip()
        if not new_label:
            return

        old_label = getattr(instance, "label", instance.__class__.__name__)
        if old_label == new_label:
            return

        # Update the label attribute on the instance
        instance.label = new_label

        # Update the visual node in the editor
        if dpg.does_item_exist(node_id):
            dpg.configure_item(node_id, label=new_label)

        # Update the corresponding window's title
        if hasattr(instance, "winID") and dpg.does_item_exist(instance.winID):
            dpg.configure_item(instance.winID, label=new_label)

        # Update the pinned menu bar entry if applicable
        if hasattr(self, "_pinned_node_labels") and node_id in self._pinned_node_labels:
            old_pinned_label = self._pinned_node_labels[node_id]
            self._pinned_node_labels[node_id] = new_label
            try:
                from core.main_win import main_win

                main_win.rename_pinned_menu_item(old_pinned_label, new_label)
            except (ImportError, AttributeError):
                pass

        logger.success(f"Renamed node/window '{old_label}' to '{new_label}'")


