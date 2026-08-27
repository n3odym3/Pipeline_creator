from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from config.theme_manager import theme_manager
from core.input_output_types import IOTypes
from core.module_registry import MODULES_REGISTRY, get_available_modules
from core.node_callbacks import NodeCallbacksMixin
from core.node_highlight import NodeHighlightMixin
from core.node_link_proxies import _LinkInNode, _LinkOutNode
from core.node_popup import NodePopupMixin
from core.node_serialization import NodeSerializationMixin


class NodeEditor(NodeHighlightMixin, NodePopupMixin, NodeCallbacksMixin, NodeSerializationMixin):
    """
    NodeEditor manages a visual node-based interface using DearPyGui.
    It supports dynamic module instantiation, node linking, validation of
    type compatibility, and interactive graph reconstruction from saved instances.

    Native built-in nodes (Link In / Link Out) are managed directly inside
    this class without requiring external module files.

    Responsibilities are split across focused mixins:
        - NodePopupMixin         -> search popup, module registry
        - NodeCallbacksMixin     -> mouse & DPG event callbacks
        - NodeSerializationMixin -> serialize / rebuild / connect
    """

    def __init__(self, label: str = "Node editor") -> None:
        """Initialize the NodeEditor and build the base GUI."""
        self.UUID: str = str(dpg.generate_uuid())
        self.winID: str = f"{label}_{self.UUID}"
        self.node_map: Dict[int, Any] = {}
        self.link_map: Dict[int, Tuple[int, int]] = {}
        self.mouse_pos: List[float] = [0.0, 0.0]
        self._popup_click_handled: bool = False
        self.popup_tag: str = f"node_popup_{self.UUID}"
        self.node_popup_tag: str = f"node_ctx_{self.UUID}"
        self.editor_tag: str = f"node_editor_{self.UUID}"
        self.anchor_node_tag: str = "anchor_node"
        self.show_type_warnings: bool = True
        self.scale = display_scaling.scale

        self.popup_width = 350
        self.popup_height = 400

        # --- Themes ---
        self.highlight_theme = theme_manager.create_highlight_theme()
        self.virtual_link_theme = theme_manager.create_virtual_link_theme()
        self.red_node_theme = theme_manager.create_red_node_theme()
        self.red_link_theme = theme_manager.create_red_link_theme()
        self.dimmed_node_theme = theme_manager.create_dimmed_node_theme()
        self.dimmed_link_theme = theme_manager.create_dimmed_link_theme()

        # --- Highlight state ---
        self.highlight_mode: str = "none"
        self._highlighted_nodes: List[int] = []
        self._virtual_links: List[int] = []
        self._highlighted_downstream_nodes: Dict[int, int] = {}
        self._highlighted_downstream_links: Dict[int, int] = {}
        self._highlighted_text_items: List[int] = []
        self._dimmed_nodes: List[int] = []
        self._dimmed_links: List[int] = []

        # View menu
        self.view_menu_tag: str = f"view_menu_{self.UUID}"

        # Color theme cache: hue (int 0-359) -> (node_theme, link_theme)
        self._color_theme_cache: Dict[int, Tuple[int, int]] = {}

        # --- Build GUI ---
        vp_w = dpg.get_viewport_width() or 1200
        vp_h = dpg.get_viewport_height() or 800
        win_w = 800
        win_h = 600
        init_pos = (max(0, (vp_w - win_w) // 2), max(0, (vp_h - win_h) // 2))

        with dpg.window(label=label, width=win_w, height=win_h, pos=init_pos, tag=self.winID, show=False):
            # Menu bar
            with dpg.menu_bar():
                # Load
                with dpg.menu(label="Load"):
                    dpg.add_menu_item(label="Pipeline...", callback=self._on_load_pressed)
                    with dpg.tooltip(parent=dpg.last_item()):
                        dpg.add_text("Load a full workspace from a JSON file (replaces current)")

                    dpg.add_separator()

                    dpg.add_menu_item(label="Sub-pipeline...", callback=self._on_load_subpipeline_pressed)
                    with dpg.tooltip(parent=dpg.last_item()):
                        dpg.add_text("Append a sub-pipeline to the current workspace")

                    dpg.add_menu_item(
                        label="Reload Last Sub-pipeline",
                        tag=f"ne_reload_subpipeline_{self.UUID}",
                        callback=self._on_reload_subpipeline_pressed,
                        enabled=False,
                    )

                    dpg.add_separator()
                    dpg.add_menu_item(label="From Clipboard...", callback=self._on_load_clipboard_pressed)

                # Save
                with dpg.menu(label="Save"):
                    dpg.add_menu_item(label="Pipeline to File...", callback=self._on_save_pressed)
                    dpg.add_menu_item(label="Pipeline to Clipboard", callback=self._on_save_clipboard_pressed)
                    dpg.add_separator()
                    dpg.add_menu_item(label="Current View to Pipeline...", callback=self._on_export_view_pressed)

                # Clear
                with dpg.menu(label="Clear"):
                    dpg.add_menu_item(label="Delete Selected", shortcut="Del", callback=self._on_clear_selection)
                    with dpg.tooltip(parent=dpg.last_item()):
                        dpg.add_text("Delete selected nodes and links (Delete / Backspace)")

                    dpg.add_menu_item(label="Clear Selection", shortcut="Esc", callback=lambda: self.deselect_all())
                    with dpg.tooltip(parent=dpg.last_item()):
                        dpg.add_text("Deselect all currently selected nodes and links (Escape)")

                    dpg.add_separator()

                    dpg.add_menu_item(label="Clear All Nodes", callback=self.delete_all_nodes)
                    with dpg.tooltip(parent=dpg.last_item()):
                        dpg.add_text("Delete every node and link from the editor")

                # View (populated dynamically)
                with dpg.menu(label="Views", tag=self.view_menu_tag, show=False):
                    pass

                # Highlight
                with dpg.menu(label="Highlight"):
                    for mode_label, mode_value in [
                        ("Show All", "Show All"),
                        ("Closest", "Closest"),
                        ("Selection", "Selection"),
                        ("None", "None"),
                    ]:
                        dpg.add_menu_item(
                            label=mode_label,
                            callback=self._on_highlight_mode_menu,
                            user_data=mode_value,
                        )
                    with dpg.tooltip(parent=dpg.last_item()):
                        dpg.add_text(
                            "Show All  - entire downstream graph\n"
                            "Closest   - directly connected nodes only\n"
                            "Selection - colorize selected nodes & windows\n"
                            "None      - disable highlighting",
                            wrap=280,
                        )

            dpg.add_separator()

            # Node editor canvas
            with dpg.node_editor(
                callback=self.link_callback,
                delink_callback=self.delink_callback,
                tag=self.editor_tag,
                minimap=True,
                minimap_location=dpg.mvNodeMiniMap_Location_BottomRight,
            ):
                # Invisible anchor node for pan-offset calculation
                with dpg.node(label="", draggable=False, tag=self.anchor_node_tag):
                    with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output):
                        dpg.add_text("")

            # Mouse and keyboard event handlers
            with dpg.handler_registry():
                dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Right, callback=self.right_click_callback)
                dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=self.left_click_callback)
                dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Left, callback=self.left_release_callback)
                dpg.add_mouse_double_click_handler(
                    button=dpg.mvMouseButton_Left, callback=self._on_node_double_click
                )
                dpg.add_key_press_handler(
                    key=dpg.mvKey_Delete,
                    callback=self._on_delete_key,
                )
                dpg.add_key_press_handler(
                    key=dpg.mvKey_Back,
                    callback=self._on_delete_key,
                )
                dpg.add_key_press_handler(
                    key=dpg.mvKey_Escape,
                    callback=self._on_escape_key,
                )

            # Build module registry for search
            self._build_module_registry()

            # Searchable popup window
            with dpg.window(
                tag=self.popup_tag,
                show=False,
                no_title_bar=True,
                width=self.scale(self.popup_width),
                height=self.scale(self.popup_height),
            ):
                dpg.add_input_text(
                    hint="Search modules...",
                    tag=f"{self.popup_tag}_search",
                    callback=self._on_search_change,
                    width=-1,
                )
                dpg.add_separator()

                # Built-in nodes section
                with dpg.collapsing_header(
                    label="built-in", default_open=False, tag=f"{self.popup_tag}_builtin_header"
                ):
                    dpg.add_button(
                        label="Link Out",
                        tag=f"{self.popup_tag}_btn_link_out",
                        callback=self._add_link_out_node,
                        width=-1,
                    )
                    with dpg.tooltip(parent=f"{self.popup_tag}_btn_link_out"):
                        dpg.add_text(
                            "Publish incoming data on a named virtual channel.\n"
                            "Any Link In with the same name will receive it.",
                            wrap=300,
                        )
                    dpg.add_button(
                        label="Link In",
                        tag=f"{self.popup_tag}_btn_link_in",
                        callback=self._add_link_in_node,
                        width=-1,
                    )
                    with dpg.tooltip(parent=f"{self.popup_tag}_btn_link_in"):
                        dpg.add_text(
                            "Subscribe to a named virtual channel.\n"
                            "Forwards received data to physically connected nodes.",
                            wrap=300,
                        )

                dpg.add_separator()

                # Module list
                with dpg.child_window(tag=f"{self.popup_tag}_list", height=-1):
                    self._populate_module_list()

        # Node context menu (populated dynamically on right-click)
        with dpg.window(
            tag=self.node_popup_tag,
            show=False,
            no_title_bar=True,
            no_resize=True,
            no_move=True,
            autosize=True,
        ):
            pass


    def show(self) -> None:
        """Show the node editor window, center it in the viewport, and bring it to the front."""
        vp_w = dpg.get_viewport_width() or 1200
        vp_h = dpg.get_viewport_height() or 800

        rect_size = dpg.get_item_rect_size(self.winID)
        win_w = rect_size[0] if rect_size and rect_size[0] > 0 else 800
        win_h = rect_size[1] if rect_size and rect_size[1] > 0 else 600

        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        dpg.configure_item(self.winID, pos=(pos_x, pos_y))
        dpg.show_item(self.winID)
        dpg.focus_item(self.winID)
        dpg.focus_item(self.editor_tag)

    def hide(self) -> None:
        """Hide the node editor window."""
        dpg.hide_item(self.winID)

    def get_mouse_pos(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """
        Calculate mouse position relative to the editor area,
        correcting for internal panning using the anchor node.

        Returns:
            Tuple containing (relative_pos, absolute_pos).
        """
        abs_pos = dpg.get_mouse_pos(local=False)
        ref_screen_pos = dpg.get_item_rect_min(self.anchor_node_tag)
        ref_editor_pos = dpg.get_item_pos(self.anchor_node_tag)
        pan_offset = (ref_editor_pos[0] - ref_screen_pos[0], ref_editor_pos[1] - ref_screen_pos[1])
        rel_pos = (abs_pos[0] + pan_offset[0], abs_pos[1] + pan_offset[1])
        return rel_pos, abs_pos

    def _on_load_pressed(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Fetch main_win and trigger its load workspace logic."""
        try:
            from core.main_win import main_win

            main_win._on_load_workspace()
        except ImportError:
            logger.error("Could not import main_win to load workspace")

    def _on_save_pressed(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Fetch main_win and trigger its save workspace logic."""
        try:
            from core.main_win import main_win

            main_win._on_save_workspace()
        except ImportError:
            logger.error("Could not import main_win to save workspace")

    def _on_save_clipboard_pressed(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Fetch main_win and trigger its save to clipboard logic."""
        try:
            from core.main_win import main_win

            main_win._on_save_clipboard()
        except ImportError:
            logger.error("Could not import main_win to save to clipboard")

    def _on_load_subpipeline_pressed(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Fetch main_win and trigger its load sub-pipeline logic."""
        try:
            from core.main_win import main_win

            main_win._on_load_subpipeline()
        except ImportError:
            logger.error("Could not import main_win to load sub-pipeline")

    def _on_load_clipboard_pressed(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Fetch main_win and trigger its load from clipboard logic."""
        try:
            from core.main_win import main_win

            main_win._on_load_clipboard()
        except ImportError:
            logger.error("Could not import main_win to load from clipboard")

    def refresh_view_menu(self) -> None:
        """Populate the Views menu with available views from the global registry."""
        from core.module_registry import get_available_views

        views = get_available_views()

        children = dpg.get_item_children(self.view_menu_tag, 1) or []
        for child in children:
            dpg.delete_item(child)

        if views:
            for view_name in views:
                dpg.add_menu_item(
                    label=view_name,
                    callback=self._on_view_changed,
                    user_data=view_name,
                    parent=self.view_menu_tag,
                )
            dpg.configure_item(self.view_menu_tag, show=True)
            logger.info(f"Loaded {len(views)} views into Views menu.")
        else:
            dpg.configure_item(self.view_menu_tag, show=False)

    def _on_export_view_pressed(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Trigger main window's export view dialog."""
        try:
            from core.main_win import main_win

            main_win._on_export_view()
        except Exception as e:
            logger.error(f"Could not trigger export view: {e}")

    def _on_view_changed(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Apply the selected view (called from Views menu item)."""
        view_name = user_data if user_data is not None else (args[0] if args else None)
        if not view_name:
            logger.warning("Could not determine view name in _on_view_changed")
            return
        from core.module_registry import apply_named_view

        apply_named_view(view_name)

    def _on_highlight_mode_menu(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Handle Highlight menu item selection."""
        mode_val = user_data if user_data is not None else (args[0] if args else "None")
        self.highlight_mode = str(mode_val).lower().replace(" ", "_")
        if hasattr(self, "_on_highlight_mode_changed"):
            self._on_highlight_mode_changed(sender, mode_val, mode_val)
        logger.debug(f"Highlight mode set to: {self.highlight_mode}")

    def _is_text_input_active(self) -> bool:
        """Check if any text input widget in the editor or search popup is currently focused/active."""
        try:
            search_tag = f"{self.popup_tag}_search"
            if dpg.does_item_exist(search_tag) and (dpg.is_item_active(search_tag) or dpg.is_item_focused(search_tag)):
                return True
            for node_id, proxy in list(self.node_map.items()):
                if hasattr(proxy, "_name_input_tag") and dpg.does_item_exist(proxy._name_input_tag):
                    if dpg.is_item_active(proxy._name_input_tag) or dpg.is_item_focused(proxy._name_input_tag):
                        return True
        except Exception:
            pass
        return False

    def _on_delete_key(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Handles Delete / Backspace key press to delete selected nodes and links if not editing text."""
        if not dpg.does_item_exist(self.winID) or not dpg.is_item_shown(self.winID):
            return
        if not (dpg.is_item_focused(self.winID) or dpg.is_item_hovered(self.editor_tag)):
            return
        if not dpg.does_item_exist(self.editor_tag):
            return
        if self._is_text_input_active():
            return
        self._on_clear_selection()

    def _on_escape_key(self, sender: Any = None, app_data: Any = None, user_data: Any = None) -> None:
        """Handles Escape key press to deselect nodes only when node editor window is focused or hovered."""
        if not dpg.does_item_exist(self.winID) or not dpg.is_item_shown(self.winID):
            return
        if not (dpg.is_item_focused(self.winID) or dpg.is_item_hovered(self.editor_tag)):
            return
        self.deselect_all()

    def deselect_all(self) -> None:
        """Deselect all nodes and links in the node editor."""
        if dpg.does_item_exist(self.editor_tag):
            dpg.clear_selected_nodes(self.editor_tag)
            dpg.clear_selected_links(self.editor_tag)
        self._clear_highlights()

    def _on_clear_selection(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Delete currently selected nodes and links from the editor."""
        if not dpg.does_item_exist(self.editor_tag):
            return

        selected_links = dpg.get_selected_links(self.editor_tag) or []
        for link_id in list(selected_links):
            self.delink_callback(0, link_id)

        selected_nodes = dpg.get_selected_nodes(self.editor_tag) or []
        for node_id in list(selected_nodes):
            if node_id in self.node_map:
                self.delete_node(sender, app_data, node_id)

        if selected_nodes or selected_links:
            logger.info(f"Deleted {len(selected_nodes)} selected node(s) and {len(selected_links)} link(s).")

    def _on_reload_subpipeline_pressed(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Reload the last loaded sub-pipeline."""
        try:
            from core.main_win import main_win

            main_win._on_reload_last_subpipeline()
        except ImportError:
            logger.error("Could not import main_win to reload sub-pipeline")

    def _create_link_out_node(self, pos: Tuple[float, float], proxy: Optional[_LinkOutNode] = None) -> int:
        """
        Create the DPG node widget for a Link Out built-in node.

        Structure:
            node
            ├── node_attribute (Input)  ← receives wired data
            └── node_attribute (Static) → text input for channel name
        """
        if proxy is None:
            proxy = _LinkOutNode()

        with dpg.node(label="Link Out", parent=self.editor_tag, pos=pos) as node_id:
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("In")

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_text(
                    tag=proxy._name_input_tag,
                    default_value=proxy.link_name,
                    hint="channel name",
                    width=140,
                    on_enter=False,
                )

            self.node_map[node_id] = proxy
            return node_id

    def _create_link_in_node(self, pos: Tuple[float, float], proxy: Optional[_LinkInNode] = None) -> int:
        """
        Create the DPG node widget for a Link In built-in node.

        Structure:
            node
            ├── node_attribute (Static) → text input for channel name
            └── node_attribute (Output) → sends data to wired downstream nodes
        """
        if proxy is None:
            proxy = _LinkInNode()

        with dpg.node(label="Link In", parent=self.editor_tag, pos=pos) as node_id:
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_text(
                    tag=proxy._name_input_tag,
                    default_value=proxy.link_name,
                    hint="channel name",
                    width=140,
                    on_enter=False,
                    callback=lambda s, a, u: proxy.on_name_changed(a),
                )

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=f"{node_id}_Out"):
                dpg.add_text("Out")

        self.node_map[node_id] = proxy
        return node_id

    def _add_link_out_node(
        self, sender: int = 0, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Popup button callback — add a Link Out node at the last right-click position."""
        self._popup_click_handled = True
        self._create_link_out_node(self.mouse_pos)
        dpg.configure_item(self.popup_tag, show=False)

    def _add_link_in_node(
        self, sender: int = 0, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Popup button callback — add a Link In node at the last right-click position."""
        self._popup_click_handled = True
        self._create_link_in_node(self.mouse_pos)
        dpg.configure_item(self.popup_tag, show=False)

    def delete_node(self, sender: int, app_data: Any, node_id: int, *args: Any) -> None:
        """Delete a node and all its links from the editor."""
        if node_id not in self.node_map:
            return

        if hasattr(self, "_highlighted_nodes") and node_id in self._highlighted_nodes:
            self._highlighted_nodes.remove(node_id)

        to_remove = [
            link_id
            for link_id, (from_attr, to_attr) in self.link_map.items()
            if dpg.get_item_parent(from_attr) == node_id or dpg.get_item_parent(to_attr) == node_id
        ]

        for link_id in to_remove:
            from_attr, to_attr = self.link_map[link_id]
            from_node = dpg.get_item_parent(from_attr)
            to_node = dpg.get_item_parent(to_attr)
            src = self.node_map.get(from_node)
            tgt = self.node_map.get(to_node)

            if src and tgt:
                src_key = self._find_output_key(from_node, from_attr)
                if src_key and tgt in src.connections.get(src_key, []):
                    src.connections[src_key].remove(tgt)
                dpg.delete_item(link_id)
            if link_id in self.link_map:
                del self.link_map[link_id]

        pinned = getattr(self, "_pinned_node_labels", {})
        if node_id in pinned:
            label = pinned.pop(node_id)
            try:
                from core.main_win import main_win

                main_win.remove_pinned_menu_item(label)
            except ImportError:
                pass

        if hasattr(self.node_map[node_id], "close"):
            self.node_map[node_id].close()
        if dpg.does_item_exist(node_id):
            dpg.delete_item(node_id)
        del self.node_map[node_id]

    def delete_all_nodes(
        self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Delete all nodes and links from the editor and reset mappings."""
        from core.module_registry import clear_registry

        self._clear_highlights()

        for link_id in list(self.link_map.keys()):
            if dpg.does_item_exist(link_id):
                dpg.delete_item(link_id)
        self.link_map.clear()

        for node_id, instance in list(self.node_map.items()):
            try:
                if hasattr(instance, "close"):
                    instance.close()
                if dpg.does_item_exist(node_id):
                    dpg.delete_item(node_id)
            except Exception as e:
                logger.error(f"Failed to delete node {node_id}: {e}")

        clear_registry()
        self.node_map.clear()
        logger.success("Workspace cleared from editor.")

    def _create_node_visual(self, instance: Any, pos: Tuple[float, float]) -> int:
        """
        Create the visual DPG node for a module instance.

        Args:
            instance: The module instance to represent.
            pos:      (x, y) position for the node.

        Returns:
            The DPG node_id created.
        """
        label = getattr(instance, "label", instance.__class__.__name__)

        with dpg.node(label=label, parent=self.editor_tag, pos=pos) as node_id:
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("In")

                if hasattr(instance, "accepted_input_types") and instance.accepted_input_types:
                    with dpg.tooltip(dpg.last_item()):
                        types_str = ", ".join([
                            str(t.value) if hasattr(t, "value") else str(t)
                            for t in instance.accepted_input_types
                        ])
                        dpg.add_text(f"IOType: {types_str}", color=(150, 255, 150))

            self.node_map[node_id] = instance

            for output_name in instance.outputs.keys():
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output, tag=f"{node_id}_{output_name}"):
                    dpg.add_text(output_name)

                with dpg.tooltip(parent=f"{node_id}_{output_name}"):
                    out_type = instance.outputs[output_name]
                    type_str = str(out_type.value) if hasattr(out_type, "value") else str(out_type)
                    dpg.add_text(f"IOType: {type_str}", color=(150, 255, 150))

                    if hasattr(instance, "descriptions") and output_name in instance.descriptions:
                        dpg.add_text(instance.descriptions[output_name], wrap=250)

        return node_id

    def _pin_node_to_menu_bar(self, sender: int, app_data: Any, node_id: int, *args: Any) -> None:
        """Pin this node's module window to the main window's Pinned menu."""
        instance = self.node_map.get(node_id)
        if instance is None:
            return

        label = getattr(instance, "label", instance.__class__.__name__)

        try:
            from core.main_win import main_win
        except ImportError:
            logger.warning("Could not import main_win - menu bar pinning unavailable")
            return

        def _show_and_focus() -> None:
            win_id = getattr(instance, "winID", None)
            if win_id and dpg.does_item_exist(win_id):
                dpg.configure_item(win_id, show=True)
                dpg.focus_item(win_id)

        main_win.add_pinned_menu_item(label, _show_and_focus)
        instance.node_pinned = True

        if not hasattr(self, "_pinned_node_labels"):
            self._pinned_node_labels: Dict[int, str] = {}
        self._pinned_node_labels[node_id] = label

    def add_node(self, sender: int, app_data: Any, module_class: Any, *args: Any) -> None:
        """Add a new node to the editor based on the selected module class."""
        self._popup_click_handled = True
        label = module_class.__name__

        before_uuids = set(MODULES_REGISTRY.keys())

        try:
            instance = module_class()
        except ImportError as e:
            logger.error(f"Failed to instantiate {label}: {e}")
            after_uuids = set(MODULES_REGISTRY.keys())
            for uuid in (after_uuids - before_uuids):
                try:
                    MODULES_REGISTRY[uuid].close()
                except Exception:
                    pass

            with dpg.window(
                label="Missing Dependency",
                modal=True,
                width=500,
                height=200,
                pos=(400, 300),
                no_close=True,
            ):
                dpg.add_text(f"Cannot load module '{label}'", color=(255, 200, 0))
                dpg.add_spacer(height=10)
                dpg.add_text(f"Missing package: {str(e)}", wrap=480)
                dpg.add_spacer(height=15)
                dpg.add_text("Use Tools -> Check Dependencies to install missing packages.", wrap=480)
                dpg.add_spacer(height=15)
                dpg.add_button(
                    label="OK",
                    callback=lambda s, a, u: dpg.delete_item(dpg.get_item_parent(s)),
                    width=-1,
                )

            dpg.configure_item(self.popup_tag, show=False)
            return
        except Exception as e:
            logger.error(f"Failed to create instance of {label}: {e}")
            after_uuids = set(MODULES_REGISTRY.keys())
            for uuid in (after_uuids - before_uuids):
                try:
                    MODULES_REGISTRY[uuid].close()
                except Exception:
                    pass

            dpg.configure_item(self.popup_tag, show=False)
            return

        self._create_node_visual(instance, self.mouse_pos)
        dpg.configure_item(self.popup_tag, show=False)

    def _get_output_IDs(self, node_id: int) -> List[int]:
        """Return all output attribute IDs from a given node."""
        children = dpg.get_item_children(node_id, 1)
        if not children:
            return []

        output_attrs = []
        for attr_id in children:
            item_type = dpg.get_item_type(attr_id)
            if "mvNodeAttribute" in item_type:
                conf = dpg.get_item_configuration(attr_id)
                try:
                    attr_type = int(conf.get("attribute_type", -1))
                    if attr_type == int(dpg.mvNode_Attr_Output):
                        output_attrs.append(attr_id)
                except (ValueError, TypeError):
                    continue
        return output_attrs


    def _find_output_attr(self, node_id: int | str, output_name: str) -> Optional[int | str]:
        """
        Find the DPG attribute ID or tag for a named output on a node.
        """
        tagged = f"{node_id}_{output_name}"
        if dpg.does_item_exist(tagged):
            return tagged

        for attr_id in self._get_output_IDs(node_id):
            children = dpg.get_item_children(attr_id, 1) or []
            for child_id in children:
                if dpg.get_item_type(child_id) == "mvAppItemType::mvText" and dpg.get_value(child_id) == output_name:
                    return attr_id
        return None

    def _find_output_key(self, node_id: int | str, attr_id: int | str) -> Optional[str]:
        """
        Find the output key name corresponding to a given attribute ID or tag.
        """
        instance = self.node_map.get(node_id)
        if not instance:
            return None

        prefix = f"{node_id}_"
        if isinstance(attr_id, str) and attr_id.startswith(prefix):
            key = attr_id[len(prefix):]
            if key in getattr(instance, "outputs", {}):
                return key

        output_ids = self._get_output_IDs(node_id)
        for idx, oid in enumerate(output_ids):
            if oid == attr_id or str(oid) == str(attr_id):
                try:
                    return list(instance.outputs.keys())[idx]
                except IndexError:
                    return None
        return None

    def _get_first_input_attr(self, node_id: int | str) -> Optional[int | str]:
        """Get the first input attribute ID of a node, if any."""
        children = dpg.get_item_children(node_id, 1) or []
        for attr in children:
            try:
                attr_type = int(dpg.get_item_configuration(attr).get("attribute_type", -1))
                if attr_type == int(dpg.mvNode_Attr_Input):
                    return attr
            except (ValueError, TypeError):
                continue
        return None

