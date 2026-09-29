from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple, Union
from loguru import logger
import dearpygui.dearpygui as dpg


class ModuleItemInspector:
    """
    Interactive Inspector Window for editing a module's UI layout and element visibility.
    Displays an interactive arborescence tree of Dear PyGui widgets with:
    - Visibility toggling (show / hide)
    - Highlighting on screen with an overlay bounding box
    - Interactive 'Pick' element mode (hover & click)
    - Search / filter
    - Deterministic item identification and view persistence
    """

    _instances: Dict[str, ModuleItemInspector] = {}

    # -------------------------------------------------------------------------
    # Static & Class Utility Methods
    # -------------------------------------------------------------------------

    @staticmethod
    def clean_item_type(raw_type: str) -> str:
        """
        Format DPG internal type names into clean, readable labels.
        E.g. 'mvAppItemType::mvButton' -> 'Button'
        """
        if not raw_type:
            return "Item"
        if "::mv" in raw_type:
            raw_type = raw_type.split("::mv")[-1]
        elif "mv" in raw_type and raw_type.startswith("mv"):
            raw_type = raw_type[2:]
        return raw_type

    @staticmethod
    def get_item_label_or_value(item_id: int | str) -> str:
        """Retrieve the best descriptive label or value for an item."""
        if not dpg.does_item_exist(item_id):
            return ""
        try:
            conf = dpg.get_item_configuration(item_id)
            if conf.get("label"):
                return str(conf["label"])
            val = dpg.get_value(item_id)
            if val is not None and isinstance(val, (str, int, float, bool)):
                val_str = str(val)
                if len(val_str) > 30:
                    val_str = val_str[:27] + "..."
                return f"val='{val_str}'"
            if conf.get("hint"):
                return f"hint='{conf['hint']}'"
        except Exception:
            pass
        alias = dpg.get_item_alias(item_id) if isinstance(item_id, int) else None
        if alias:
            return f"tag='{alias}'"
        return ""

    @staticmethod
    def get_item_identifier(root_id: int | str, target_id: int | str) -> Optional[str]:
        """
        Generate a deterministic identifier for an item relative to root_id.
        If the item has an explicit string tag or alias, returns 'tag:<alias>'.
        Otherwise, returns a relative hierarchy path 'path:<slot>/<index>/...'.
        """
        if not dpg.does_item_exist(target_id):
            return None

        # Check for alias / tag
        alias = dpg.get_item_alias(target_id) if isinstance(target_id, int) else None
        if alias:
            return f"tag:{alias}"
        if isinstance(target_id, str):
            return f"tag:{target_id}"

        # Target is root itself
        if target_id == root_id or str(target_id) == str(root_id):
            return "root"

        def _find_path(current_id: int | str, path_segments: List[str]) -> Optional[str]:
            if current_id == target_id:
                return "/".join(path_segments)
            for slot in (1, 0, 2):
                children = dpg.get_item_children(current_id, slot) or []
                for idx, child in enumerate(children):
                    result = _find_path(child, path_segments + [f"{slot}_{idx}"])
                    if result is not None:
                        return result
            return None

        rel_path = _find_path(root_id, [])
        if rel_path is not None:
            return f"path:{rel_path}"
        return None

    @staticmethod
    def resolve_item_identifier(root_id: int | str, identifier: str) -> Optional[int | str]:
        """
        Resolve an identifier back to a live DPG item ID.
        Supports 'tag:<tag>', 'path:<slot>_<idx>/...', or direct tags.
        """
        if not identifier:
            return None

        if identifier == "root":
            return root_id if dpg.does_item_exist(root_id) else None

        if identifier.startswith("tag:"):
            tag = identifier[4:]
            if dpg.does_item_exist(tag):
                return tag
            return None

        if identifier.startswith("path:"):
            path_str = identifier[5:]
            segments = path_str.split("/")
            curr = root_id
            for seg in segments:
                if not dpg.does_item_exist(curr):
                    return None
                try:
                    parts = seg.split("_")
                    slot = int(parts[0])
                    idx = int(parts[1])
                    children = dpg.get_item_children(curr, slot) or []
                    if 0 <= idx < len(children):
                        curr = children[idx]
                    else:
                        return None
                except Exception:
                    return None
            return curr if dpg.does_item_exist(curr) else None

        # Fallback direct tag check
        if dpg.does_item_exist(identifier):
            return identifier
        return None

    @classmethod
    def get_hidden_items(cls, module: Any) -> List[str]:
        """
        Collect identifiers of all widgets in the module that are currently hidden.
        """
        win_id = getattr(module, "winID", None)
        if not win_id or not dpg.does_item_exist(win_id):
            return []

        hidden_list: List[str] = []
        roots = [win_id]

        sub_windows = getattr(module, "sub_windows", [])
        for sw in sub_windows:
            if dpg.does_item_exist(sw) and sw not in roots:
                roots.append(sw)

        def _traverse(root: int | str, item: int | str) -> None:
            if item != root:
                is_shown = dpg.is_item_shown(item)
                if not is_shown:
                    ident = cls.get_item_identifier(root, item)
                    if ident:
                        hidden_list.append(ident)

            for slot in (1, 0, 2):
                for child in dpg.get_item_children(item, slot) or []:
                    _traverse(root, child)

        for r in roots:
            _traverse(r, r)

        return hidden_list

    @classmethod
    def apply_hidden_items(cls, module: Any, hidden_identifiers: List[str]) -> None:
        """
        Restore the visibility state of module items according to a saved list of hidden identifiers.
        First unhides items, then applies hide to the specified ones.
        """
        win_id = getattr(module, "winID", None)
        if not win_id or not dpg.does_item_exist(win_id):
            return

        roots = [win_id]
        sub_windows = getattr(module, "sub_windows", [])
        for sw in sub_windows:
            if dpg.does_item_exist(sw) and sw not in roots:
                roots.append(sw)

        # 1. Unhide all items inside root windows (skipping tooltips, popups, and modals)
        def _unhide_all(item: int | str) -> None:
            for slot in (1, 0, 2):
                for child in dpg.get_item_children(item, slot) or []:
                    try:
                        info = dpg.get_item_info(child)
                        item_type = info.get("type", "") if info else ""
                        # NEVER force-show tooltips, popups, or modal windows!
                        if item_type in (
                            "mvAppItemType::mvTooltip",
                            "mvAppItemType::mvPopup",
                            "mvAppItemType::mvModalWindow",
                            "mvAppItemType::mvFileDialog",
                        ):
                            continue
                        dpg.show_item(child)
                    except Exception:
                        pass
                    _unhide_all(child)

        for r in roots:
            _unhide_all(r)

        # 2. Hide specific items
        if not hidden_identifiers:
            if hasattr(module, "on_view_applied"):
                try:
                    module.on_view_applied()
                except Exception:
                    pass
            elif hasattr(module, "_set_step") and hasattr(module, "protocol_step"):
                try:
                    module._set_step(module.protocol_step)
                except Exception:
                    pass
            return

        for ident in hidden_identifiers:
            resolved = None
            for r in roots:
                resolved = cls.resolve_item_identifier(r, ident)
                if resolved is not None:
                    break
            if resolved is not None and dpg.does_item_exist(resolved):
                try:
                    dpg.hide_item(resolved)
                except Exception as e:
                    logger.debug(f"Failed to hide item {ident}: {e}")

        if hasattr(module, "on_view_applied"):
            try:
                module.on_view_applied()
            except Exception:
                pass
        elif hasattr(module, "_set_step") and hasattr(module, "protocol_step"):
            try:
                module._set_step(module.protocol_step)
            except Exception:
                pass

    @classmethod
    def show_all_items(cls, module: Any) -> None:
        """Make all elements in the module's windows visible."""
        cls.apply_hidden_items(module, [])

    # -------------------------------------------------------------------------
    # Instance Lifecycle & GUI
    # -------------------------------------------------------------------------

    @classmethod
    def open_inspector(cls, module: Any) -> ModuleItemInspector:
        """Open or focus an inspector window for the specified module."""
        uuid = str(getattr(module, "UUID", ""))
        if uuid in cls._instances:
            inst = cls._instances[uuid]
            if dpg.does_item_exist(inst.window_tag):
                dpg.show_item(inst.window_tag)
                dpg.focus_item(inst.window_tag)
                inst.refresh_tree()
                return inst
            del cls._instances[uuid]

        inspector = cls(module)
        cls._instances[uuid] = inspector
        return inspector

    @classmethod
    def close_inspector(cls, module: Any) -> None:
        """Close inspector for a module if currently open."""
        uuid = str(getattr(module, "UUID", ""))
        if uuid in cls._instances:
            inst = cls._instances.pop(uuid)
            inst._on_close()
            if dpg.does_item_exist(inst.window_tag):
                try:
                    dpg.delete_item(inst.window_tag)
                except Exception:
                    pass

    @classmethod
    def close_all(cls) -> None:
        """Close and completely destroy all open module inspectors, overlays, and handlers."""
        for uuid in list(cls._instances.keys()):
            inst = cls._instances.pop(uuid, None)
            if inst is not None:
                inst._on_close()
                if dpg.does_item_exist(inst.window_tag):
                    try:
                        dpg.delete_item(inst.window_tag)
                    except Exception:
                        pass

    def __init__(self, module: Any) -> None:
        self.module = module
        self.uuid = str(getattr(module, "UUID", ""))
        self.label = getattr(module, "label", getattr(module, "__class__", type(module)).__name__)
        self.window_tag = f"_module_inspector_{self.uuid}"
        self.tree_container_tag = f"{self.window_tag}_tree"
        self.search_tag = f"{self.window_tag}_search"
        self.status_text_tag = f"{self.window_tag}_status"

        self.pick_mode: bool = False
        self.pick_button_tag = f"{self.window_tag}_pick_btn"
        self.pick_handler_tag = f"{self.window_tag}_pick_reg"
        self.inspector_handler_tag = f"{self.window_tag}_inspector_reg"
        self.badge_theme_tag = f"{self.window_tag}_badge_theme"
        self._overlay_drawlist_tag = f"{self.window_tag}_overlay_drawlist"
        self._overlay_rect_tag = f"{self.window_tag}_overlay_rect"
        self._filter_query: str = ""
        self._highlighted_item: Optional[int | str] = None
        self._selected_item: Optional[int | str] = None
        self._open_ancestors: Set[int | str] = set()
        self._total_elements: int = 0
        self._item_registries: List[str] = []
        self._item_text_tags: Dict[int | str, str] = {}
        self._item_cb_tags: Dict[int | str, str] = {}

        self._init_themes()
        self._init_overlay()
        self._create_window()
        self._enable_inspector_handlers()

    def _init_themes(self) -> None:
        """Create theme for the picked element badge."""
        if not dpg.does_item_exist(self.badge_theme_tag):
            with dpg.theme(tag=self.badge_theme_tag):
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 220, 255, 230))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (50, 235, 255, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (0, 180, 220, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_Text, (10, 15, 20, 255))
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                    dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 5, 1)

    def _init_overlay(self) -> None:
        """Create viewport drawlist overlay for visual highlighting."""
        if dpg.does_item_exist(self._overlay_drawlist_tag):
            try:
                dpg.delete_item(self._overlay_drawlist_tag)
            except Exception:
                pass

        with dpg.viewport_drawlist(front=True, show=True, tag=self._overlay_drawlist_tag):
            dpg.draw_rectangle(
                (0, 0),
                (0, 0),
                color=(0, 230, 255, 255),
                fill=(0, 230, 255, 40),
                thickness=3,
                show=False,
                tag=self._overlay_rect_tag,
            )

    def _create_window(self) -> None:
        """Construct the inspector window."""
        if dpg.does_item_exist(self.window_tag):
            dpg.delete_item(self.window_tag)

        # Focus module window first so it's shown if hidden
        win_id = getattr(self.module, "winID", None)
        if win_id and dpg.does_item_exist(win_id):
            try:
                dpg.show_item(win_id)
                dpg.focus_item(win_id)
            except Exception:
                pass

        with dpg.window(
            label=f"Edit Module UI - {self.label}",
            tag=self.window_tag,
            width=540,
            height=580,
            pos=[120, 100],
            on_close=self._on_close,
        ):
            # Toolbar
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Show All Elements",
                    callback=self._on_show_all_pressed,
                )
                dpg.add_button(
                    label="Refresh Tree",
                    callback=lambda: self.refresh_tree(),
                )
                dpg.add_button(
                    label="Pick Element",
                    tag=self.pick_button_tag,
                    callback=self._toggle_pick_mode,
                )

            dpg.add_separator()

            with dpg.group(horizontal=True):
                dpg.add_text("Filter:")
                dpg.add_input_text(
                    tag=self.search_tag,
                    hint="Search type, label or tag...",
                    width=-1,
                    callback=lambda s, a, u: self._on_filter_changed(a),
                )

            dpg.add_text(
                "Loading elements...",
                tag=self.status_text_tag,
                color=(170, 170, 170),
            )

            dpg.add_separator()

            # Scrollable tree area
            with dpg.child_window(tag=self.tree_container_tag, border=True, horizontal_scrollbar=True):
                pass

        self.refresh_tree()

    def _on_close(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Clean up when the window is closed."""
        self.clear_highlight()
        if dpg.does_item_exist(self._overlay_drawlist_tag):
            try:
                dpg.delete_item(self._overlay_drawlist_tag)
            except Exception:
                pass
        self._disable_pick_handlers()
        self._disable_inspector_handlers()
        self.pick_mode = False
        if dpg.does_item_exist(self.badge_theme_tag):
            try:
                dpg.delete_item(self.badge_theme_tag)
            except Exception:
                pass
        for reg in self._item_registries:
            if dpg.does_item_exist(reg):
                try:
                    dpg.delete_item(reg)
                except Exception:
                    pass
        self._item_registries.clear()
        self._item_text_tags.clear()
        self._item_cb_tags.clear()
        if self.uuid in self._instances:
            del self._instances[self.uuid]
        if dpg.does_item_exist(self.window_tag):
            try:
                dpg.delete_item(self.window_tag)
            except Exception:
                pass

    def _enable_inspector_handlers(self) -> None:
        """Register inspector window handlers for automatic hover highlight clearing."""
        self._disable_inspector_handlers()
        with dpg.handler_registry(tag=self.inspector_handler_tag):
            dpg.add_mouse_move_handler(callback=self._on_mouse_move)
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=self._on_global_click)

    def _disable_inspector_handlers(self) -> None:
        """Remove inspector handlers."""
        if dpg.does_item_exist(self.inspector_handler_tag):
            try:
                dpg.delete_item(self.inspector_handler_tag)
            except Exception:
                pass

    def is_item_row_hovered(self, item_id: int | str) -> bool:
        """Check whether the mouse is currently hovering over the item's label or checkbox in the tree."""
        text_tag = self._item_text_tags.get(item_id) or self._item_text_tags.get(str(item_id))
        cb_tag = self._item_cb_tags.get(item_id) or self._item_cb_tags.get(str(item_id))
        try:
            if text_tag and dpg.does_item_exist(text_tag) and dpg.is_item_hovered(text_tag):
                return True
            if cb_tag and dpg.does_item_exist(cb_tag) and dpg.is_item_hovered(cb_tag):
                return True
        except Exception:
            pass
        return False

    def _on_mouse_move(self, sender: Any, app_data: Any, *args, **kwargs) -> None:
        """Track mouse movement to reliably clear highlight the instant cursor leaves the hovered item."""
        if self.pick_mode:
            self._on_mouse_move_pick(sender, app_data)
            return

        if self._highlighted_item is not None:
            # 1. Clear if cursor is outside tree container bounds
            if not dpg.does_item_exist(self.tree_container_tag):
                self.clear_highlight()
                return

            try:
                p_min = dpg.get_item_rect_min(self.tree_container_tag)
                p_max = dpg.get_item_rect_max(self.tree_container_tag)
                mx, my = dpg.get_mouse_pos(local=False)

                if mx < p_min[0] or mx > p_max[0] or my < p_min[1] or my > p_max[1]:
                    self.clear_highlight()
                    return

                # Cursor is below the last item rendered in the tree container
                bottom_marker = f"{self.window_tag}_tree_bottom"
                if dpg.does_item_exist(bottom_marker):
                    bottom_y = dpg.get_item_rect_max(bottom_marker)[1]
                    if my > bottom_y:
                        self.clear_highlight()
                        return

                # 2. Clear if cursor is no longer hovering the highlighted item's label
                if not self.is_item_row_hovered(self._highlighted_item):
                    self.clear_highlight()
            except Exception:
                self.clear_highlight()

    def _on_global_click(self, sender: Any, app_data: Any, *args, **kwargs) -> None:
        """Clear highlight if user clicks outside the tree container."""
        if self.pick_mode:
            return
        if self._highlighted_item is not None:
            if dpg.does_item_exist(self.tree_container_tag):
                try:
                    p_min = dpg.get_item_rect_min(self.tree_container_tag)
                    p_max = dpg.get_item_rect_max(self.tree_container_tag)
                    mx, my = dpg.get_mouse_pos(local=False)
                    if mx < p_min[0] or mx > p_max[0] or my < p_min[1] or my > p_max[1]:
                        self.clear_highlight()
                except Exception:
                    self.clear_highlight()

    def _on_item_hovered(self, item_id: int | str) -> None:
        """Automatically highlight the hovered element on the module window."""
        if self.pick_mode:
            return

        if self._highlighted_item != item_id:
            self.highlight_item(item_id)

    def _on_filter_changed(self, query: str) -> None:
        self._filter_query = query.strip().lower()
        self.refresh_tree()

    def _on_show_all_pressed(self, *args, **kwargs) -> None:
        self.show_all_items(self.module)
        self.refresh_tree()

    def _toggle_pick_mode(self, *args, **kwargs) -> None:
        self.pick_mode = not self.pick_mode
        if dpg.does_item_exist(self.pick_button_tag):
            if self.pick_mode:
                dpg.configure_item(self.pick_button_tag, label="Cancel Pick (Active)")
                if dpg.does_item_exist(self.status_text_tag):
                    dpg.set_value(
                        self.status_text_tag,
                        "Pick Mode: Hover over any module element and click to select it.",
                    )
                self._enable_pick_handlers()
            else:
                dpg.configure_item(self.pick_button_tag, label="Pick Element")
                self._disable_pick_handlers()
                self.clear_highlight()

    def _enable_pick_handlers(self) -> None:
        """Register global handlers for mouse move and click during pick mode."""
        self._disable_pick_handlers()
        with dpg.handler_registry(tag=self.pick_handler_tag):
            dpg.add_mouse_move_handler(callback=self._on_mouse_move_pick)
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=self._on_mouse_click_pick)

    def _disable_pick_handlers(self) -> None:
        """Remove global pick mouse handlers."""
        if dpg.does_item_exist(self.pick_handler_tag):
            dpg.delete_item(self.pick_handler_tag)

    def _find_item_at_pos(self, mx: float, my: float) -> Optional[int | str]:
        """Find the most specific widget in the module's windows containing (mx, my)."""
        # Ignore if cursor is inside the inspector window itself
        if dpg.does_item_exist(self.window_tag):
            try:
                pos = dpg.get_item_pos(self.window_tag)
                size = dpg.get_item_rect_size(self.window_tag)
                if pos[0] <= mx <= pos[0] + size[0] and pos[1] <= my <= pos[1] + size[1]:
                    return None
            except Exception:
                pass

        win_id = getattr(self.module, "winID", None)
        if not win_id or not dpg.does_item_exist(win_id):
            return None

        roots = [win_id]
        for sw in getattr(self.module, "sub_windows", []):
            if dpg.does_item_exist(sw) and sw not in roots:
                roots.append(sw)

        best_item: Optional[int | str] = None
        best_area: float = float("inf")

        def _check_item(item: int | str, depth: int) -> None:
            nonlocal best_item, best_area
            if not dpg.does_item_exist(item) or not dpg.is_item_shown(item):
                return

            try:
                info = dpg.get_item_info(item)
                is_win = info.get("type") == "mvAppItemType::mvWindowAppItem"
                if is_win:
                    pos = dpg.get_item_pos(item)
                    size = dpg.get_item_rect_size(item)
                    p_min = (pos[0], pos[1])
                    p_max = (pos[0] + size[0], pos[1] + size[1])
                else:
                    p_min = dpg.get_item_rect_min(item)
                    p_max = dpg.get_item_rect_max(item)

                if p_min[0] <= mx <= p_max[0] and p_min[1] <= my <= p_max[1]:
                    area = (p_max[0] - p_min[0]) * (p_max[1] - p_min[1])
                    if 0 < area < best_area:
                        best_area = area
                        best_item = item

                for slot in (1, 0, 2):
                    for ch in dpg.get_item_children(item, slot) or []:
                        _check_item(ch, depth + 1)
            except Exception:
                pass

        for r in roots:
            _check_item(r, 0)

        return best_item

    def _on_mouse_move_pick(self, sender: Any, app_data: Any, *args, **kwargs) -> None:
        """Live overlay tracking under mouse during pick mode."""
        if not self.pick_mode:
            return
        try:
            mx, my = dpg.get_mouse_pos(local=False)
            item = self._find_item_at_pos(mx, my)
            if item is not None:
                self.highlight_item(item)
            else:
                self.clear_highlight()
        except Exception:
            pass

    def _find_ancestor_path(self, target_id: int | str) -> Set[int | str]:
        """Collect all ancestor container/item IDs between roots and target_id."""
        ancestors: Set[int | str] = set()
        curr = target_id
        visited: Set[int | str] = set()
        while curr and curr not in visited:
            visited.add(curr)
            try:
                info = dpg.get_item_info(curr)
                parent = info.get("parent")
                if not parent:
                    break
                ancestors.add(parent)
                curr = parent
            except Exception:
                break
        return ancestors

    def _scroll_to_selected(self, target_tag: str) -> None:
        """Scroll the tree child window so that target_tag is directly visible in view."""
        if not dpg.does_item_exist(self.tree_container_tag) or not dpg.does_item_exist(target_tag):
            return

        def _apply_scroll() -> None:
            if not dpg.does_item_exist(self.tree_container_tag) or not dpg.does_item_exist(target_tag):
                return
            try:
                container_rect_min = dpg.get_item_rect_min(self.tree_container_tag)
                container_rect_max = dpg.get_item_rect_max(self.tree_container_tag)
                item_rect_min = dpg.get_item_rect_min(target_tag)

                container_y = container_rect_min[1]
                container_h = container_rect_max[1] - container_y
                item_y = item_rect_min[1]

                curr_scroll = dpg.get_y_scroll(self.tree_container_tag)
                relative_item_offset = item_y - container_y
                target_scroll = max(0, curr_scroll + relative_item_offset - (container_h / 3.0))

                max_scroll = dpg.get_y_scroll_max(self.tree_container_tag)
                if max_scroll > 0:
                    target_scroll = min(target_scroll, max_scroll)

                dpg.set_y_scroll(self.tree_container_tag, target_scroll)
                dpg.focus_item(target_tag)
            except Exception as e:
                logger.debug(f"Scroll to selected failed: {e}")

        def _thread_scroll() -> None:
            import time
            time.sleep(0.05)
            _apply_scroll()

        import threading
        threading.Thread(target=_thread_scroll, daemon=True).start()

    def _on_mouse_click_pick(self, sender: Any, app_data: Any, *args, **kwargs) -> None:
        """Select element under mouse click during pick mode and navigate directly to its line."""
        if not self.pick_mode:
            return
        try:
            mx, my = dpg.get_mouse_pos(local=False)
            item = self._find_item_at_pos(mx, my)
            if item is not None:
                item_type = self.clean_item_type(dpg.get_item_type(item))
                label = self.get_item_label_or_value(item)
                alias = dpg.get_item_alias(item) if isinstance(item, int) else None
                name = label or alias or str(item)

                if dpg.does_item_exist(self.status_text_tag):
                    dpg.set_value(self.status_text_tag, f"Selected: [{item_type}] {name}")

                # Clear filter query so the full tree hierarchy remains visible
                self._filter_query = ""
                if dpg.does_item_exist(self.search_tag):
                    dpg.set_value(self.search_tag, "")

                # Record selected item and expand its ancestors
                self._selected_item = item
                self._open_ancestors = self._find_ancestor_path(item)

                # Deactivate pick mode
                self._toggle_pick_mode()

                # Rebuild tree with open ancestors, badge, and scroll directly to the selected element
                self.refresh_tree()
        except Exception:
            pass

    def highlight_item(self, item_id: int | str) -> None:
        """Draw an overlay rectangle around the target widget on screen."""
        if not dpg.does_item_exist(item_id):
            self.clear_highlight()
            return

        try:
            # Must be currently shown
            if not dpg.is_item_shown(item_id):
                self.clear_highlight()
                return

            info = dpg.get_item_info(item_id)
            item_type = str(info.get("type", ""))

            # Skip non-visual internal items that cannot be drawn or hovered
            non_visual = (
                "Theme", "Font", "Handler", "Registry", "Drawlist",
                "Value", "Texture", "Colormap", "FileExtension"
            )
            if any(nv in item_type for nv in non_visual):
                self.clear_highlight()
                return

            is_window = "Window" in item_type
            if is_window:
                pos = dpg.get_item_pos(item_id)
                size = dpg.get_item_rect_size(item_id)
                w = size[0] if size[0] > 0 else (dpg.get_item_width(item_id) or 200)
                h = size[1] if size[1] > 0 else (dpg.get_item_height(item_id) or 150)
                p_min = [float(pos[0]), float(pos[1])]
                p_max = [float(pos[0] + w), float(pos[1] + h)]
            else:
                rmin = dpg.get_item_rect_min(item_id)
                rmax = dpg.get_item_rect_max(item_id)
                if not rmin or not rmax:
                    self.clear_highlight()
                    return
                p_min = [float(rmin[0]), float(rmin[1])]
                p_max = [float(rmax[0]), float(rmax[1])]

            if p_max[0] <= p_min[0]:
                p_max[0] = p_min[0] + 10
            if p_max[1] <= p_min[1]:
                p_max[1] = p_min[1] + 10

            # Guard against coordinates outside screen reasonable range
            if p_max[0] < -200 or p_max[1] < -200 or p_min[0] > 10000 or p_min[1] > 10000:
                self.clear_highlight()
                return

            if dpg.does_item_exist(self._overlay_rect_tag):
                dpg.configure_item(self._overlay_rect_tag, pmin=p_min, pmax=p_max, show=True)
                dpg.show_item(self._overlay_rect_tag)
            self._highlighted_item = item_id
        except Exception:
            self.clear_highlight()

    def clear_highlight(self) -> None:
        """Remove visual overlay highlight."""
        if dpg.does_item_exist(self._overlay_rect_tag):
            try:
                dpg.configure_item(self._overlay_rect_tag, pmin=(0, 0), pmax=(0, 0), show=False)
                dpg.hide_item(self._overlay_rect_tag)
            except Exception:
                pass
        self._highlighted_item = None

    def refresh_tree(self) -> None:
        """Re-scan DPG elements of the module and re-populate the arborescence tree."""
        if not dpg.does_item_exist(self.tree_container_tag):
            return

        for reg in self._item_registries:
            if dpg.does_item_exist(reg):
                try:
                    dpg.delete_item(reg)
                except Exception:
                    pass
        self._item_registries.clear()
        self._item_text_tags.clear()
        self._item_cb_tags.clear()
        dpg.delete_item(self.tree_container_tag, children_only=True)

        win_id = getattr(self.module, "winID", None)
        if not win_id or not dpg.does_item_exist(win_id):
            dpg.add_text("Module window not found or closed.", parent=self.tree_container_tag)
            return

        roots = [win_id]
        sub_windows = getattr(self.module, "sub_windows", [])
        for sw in sub_windows:
            if dpg.does_item_exist(sw) and sw not in roots:
                roots.append(sw)

        total_items = 0
        hidden_items = 0

        for r in roots:
            t, h = self._render_node_recursive(r, r, parent_tag=self.tree_container_tag, depth=0)
            total_items += t
            hidden_items += h

        # Add bottom marker to detect when mouse moves below all items in the tree
        bottom_marker = f"{self.window_tag}_tree_bottom"
        if dpg.does_item_exist(bottom_marker):
            dpg.delete_item(bottom_marker)
        dpg.add_spacer(height=2, tag=bottom_marker, parent=self.tree_container_tag)

        self._total_elements = total_items
        if dpg.does_item_exist(self.status_text_tag):
            dpg.set_value(
                self.status_text_tag,
                f"Total elements: {total_items} | Hidden elements: {hidden_items}",
            )

        # If an item was picked, scroll directly to its line
        if self._selected_item is not None:
            self._scroll_to_selected(f"{self.window_tag}_selected_row")

    def _update_status_counts(self) -> None:
        """Quickly update status text without rebuilding the entire widget tree."""
        if dpg.does_item_exist(self.status_text_tag):
            hidden = self.get_hidden_items(self.module)
            total = getattr(self, "_total_elements", 0)
            dpg.set_value(
                self.status_text_tag,
                f"Total elements: {total} | Hidden elements: {len(hidden)}",
            )

    def _render_node_recursive(
        self, root_id: int | str, item_id: int | str, parent_tag: int | str, depth: int
    ) -> Tuple[int, int]:
        """
        Recursively construct tree nodes for the item and its children.
        Returns (count_total, count_hidden).
        """
        if not dpg.does_item_exist(item_id):
            return 0, 0

        item_type = self.clean_item_type(dpg.get_item_type(item_id))
        is_shown = dpg.is_item_shown(item_id)
        label_text = self.get_item_label_or_value(item_id)
        alias = dpg.get_item_alias(item_id) if isinstance(item_id, int) else None

        is_selected = (
            self._selected_item is not None
            and (item_id == self._selected_item or str(item_id) == str(self._selected_item))
        )

        display_name = f">> [{item_type}]" if is_selected else f"[{item_type}]"
        if label_text:
            display_name += f" {label_text}"
        elif alias:
            display_name += f" ({alias})"
        else:
            display_name += f" (ID: {item_id})"

        # Gather children from slots 1 (main), 0 (commands), 2 (menus/popups)
        children_by_slot: Dict[int, List[int | str]] = {}
        has_children = False
        for slot in (1, 0, 2):
            ch = dpg.get_item_children(item_id, slot) or []
            if ch:
                children_by_slot[slot] = ch
                has_children = True

        matches_filter = True
        if self._filter_query:
            query = self._filter_query
            matches_filter = (
                query in item_type.lower()
                or query in label_text.lower()
                or (alias and query in str(alias).lower())
                or query in str(item_id).lower()
            )

        total = 1
        hidden = 1 if not is_shown else 0

        # Build row
        row_tag = f"{self.window_tag}_selected_row" if is_selected else f"{self.window_tag}_row_{item_id}"
        if dpg.does_item_exist(row_tag):
            dpg.delete_item(row_tag)

        row_group = dpg.add_group(horizontal=True, parent=parent_tag, tag=row_tag)

        # Visibility toggle checkbox
        def _toggle_cb(s: Any, val: bool, u: int | str, *args, **kwargs) -> None:
            if dpg.does_item_exist(u):
                if val:
                    dpg.show_item(u)
                else:
                    dpg.hide_item(u)
                if dpg.does_item_exist(text_tag):
                    dpg.configure_item(
                        text_tag,
                        color=(0, 245, 255, 255) if is_selected else ((255, 255, 255, 255) if val else (140, 140, 140, 255)),
                    )
                self._update_status_counts()

        cb = dpg.add_checkbox(
            default_value=is_shown,
            callback=_toggle_cb,
            user_data=item_id,
            parent=row_group,
        )
        self._item_cb_tags[item_id] = cb
        self._item_cb_tags[str(item_id)] = cb

        badge_btn = None
        if is_selected:
            badge_btn = dpg.add_button(
                label="★ PICKED",
                parent=row_group,
                width=78,
                user_data=item_id,
                callback=lambda s, a, u: self.highlight_item(u),
            )
            dpg.bind_item_theme(badge_btn, self.badge_theme_tag)

        # Main text / tree node label
        if is_selected:
            item_text_color = (0, 245, 255, 255)
        else:
            item_text_color = (255, 255, 255, 255) if is_shown else (140, 140, 140, 255)
        text_tag = dpg.add_text(display_name, parent=row_group, color=item_text_color)
        self._item_text_tags[item_id] = text_tag
        self._item_text_tags[str(item_id)] = text_tag

        # Automatic hover highlight bound safely to the text label only
        reg_tag = f"{self.window_tag}_hr_{item_id}"
        if dpg.does_item_exist(reg_tag):
            try:
                dpg.delete_item(reg_tag)
            except Exception:
                pass
        with dpg.item_handler_registry(tag=reg_tag):
            dpg.add_item_hover_handler(
                callback=lambda s, a, u: self._on_item_hovered(u),
                user_data=item_id,
            )
        dpg.bind_item_handler_registry(text_tag, reg_tag)
        self._item_registries.append(reg_tag)

        # If item has children, put them inside a collapsible tree node
        if has_children:
            is_ancestor = (
                item_id in self._open_ancestors
                or str(item_id) in {str(a) for a in self._open_ancestors}
            )
            tree_node = dpg.add_tree_node(
                label=f"Children ({sum(len(v) for v in children_by_slot.values())})",
                parent=parent_tag,
                default_open=bool(self._filter_query or depth < 2 or is_ancestor),
            )
            for slot, children in children_by_slot.items():
                for ch in children:
                    c_tot, c_hid = self._render_node_recursive(root_id, ch, parent_tag=tree_node, depth=depth + 1)
                    total += c_tot
                    hidden += c_hid

        return total, hidden
