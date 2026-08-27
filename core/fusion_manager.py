"""
Fusion Manager module for Pipeline Creator.

Manages the merging (fusion) and separation of module windows in the DearPyGui interface,
providing visual control of window hierarchies and nesting presets.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from config.theme_manager import theme_manager
from core.module_registry import get_registered_modules
from core.window_base import WindowBase

# Aesthetic signature color palette for merge groups (RGB)
SIGNATURE_COLORS: List[Tuple[int, int, int]] = [
    (52, 152, 219),  # Ocean Blue
    (155, 89, 182),  # Amethyst Purple
    (230, 126, 34),  # Orange Accent
    (231, 76, 60),  # Coral Red
    (26, 188, 156),  # Turquoise Teal
    (253, 121, 168),  # Pink Rose
    (241, 196, 15),  # Sun Yellow
    (52, 73, 94),  # Wet Asphalt / Slate
]


class FusionManager:
    """
    UI window to manage the fusion (merging) of modules/windows in a DearPyGui interface.

    This manager provides a control panel to visually merge windows together, view
    current hierarchies, and restore merged contents to their original standalone windows.
    """

    def __init__(self, label: str = "Fusion Manager") -> None:
        """
        Initialize the FusionManager window and its UI table.

        Args:
            label: The display title for the FusionManager window.
        """
        self.winID: str = f"fusion_manager_{label}"
        self.table_id: str = f"{self.winID}_table"
        self.tag_to_module: Dict[str, WindowBase] = {} 

        # Reusable static themes to avoid leaking handles
        self.theme_refresh_btn: Union[int, str] = dpg.add_theme()
        self.theme_restore_all_btn: Union[int, str] = dpg.add_theme()

        # Dynamic row themes: UUID -> {"btn": theme, "status": theme, "restore": theme}
        self.row_themes: Dict[str, Dict[str, Union[int, str]]] = {}

        with dpg.window(
            label=label,
            width=display_scaling.scale(640),
            height=display_scaling.scale(400),
            tag=self.winID,
            pos=(25, 25),
            show=False,
        ):
            # Control Toolbar
            with dpg.group(horizontal=True):
                btn_ref = dpg.add_button(
                    label="\uf021  Refresh List",
                    callback=self.refresh,
                    width=display_scaling.scale(200),
                )
                dpg.bind_item_theme(btn_ref, self.theme_refresh_btn)

                btn_rest_all = dpg.add_button(
                    label="\uf0e2  Restore All",
                    callback=self._restore_all,
                    width=display_scaling.scale(200),
                )
                dpg.bind_item_theme(btn_rest_all, self.theme_restore_all_btn)

            dpg.add_spacer(height=10)

            with dpg.table(
                tag=self.table_id,
                header_row=True,
                resizable=True,
                policy=dpg.mvTable_SizingStretchProp,
                borders_innerH=True,
                borders_outerH=True,
                borders_innerV=True,
                borders_outerV=True,
            ):
                dpg.add_table_column(label="Module (Draggable Hierarchy)", width_fixed=True)
                dpg.add_table_column(label="Merged Into (Nesting Status)", width_stretch=True)
                dpg.add_table_column(label="Actions", width_fixed=True)

        self.refresh()

    def show(self) -> None:
        """Show and bring the FusionManager window to focus."""
        if dpg.does_item_exist(self.winID):
            dpg.show_item(self.winID)
            dpg.focus_item(self.winID)

    def _update_static_themes(self) -> None:
        """Update static themes based on active theme palette."""
        palette = theme_manager.active_palette

        for t in [self.theme_refresh_btn, self.theme_restore_all_btn]:
            if dpg.does_item_exist(t):
                for child in dpg.get_item_children(t, 1) or []:
                    dpg.delete_item(child)

        btn_bg = palette.get("button", (51, 51, 55, 255))
        btn_hover = palette.get("button_hovered", (30, 60, 80, 255))
        btn_active = palette.get("button_active", (56, 56, 58, 255))
        text_col = palette.get("text", (255, 255, 255, 255))

        # Refresh Button Theme
        with dpg.theme_component(dpg.mvButton, parent=self.theme_refresh_btn):
            dpg.add_theme_color(dpg.mvThemeCol_Button, btn_bg)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, btn_hover)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, btn_active)
            dpg.add_theme_color(dpg.mvThemeCol_Text, text_col)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4.0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 12, 6)

        # Restore All Button Theme (soft red warning)
        with dpg.theme_component(dpg.mvButton, parent=self.theme_restore_all_btn):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (85, 45, 45, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (145, 55, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (185, 65, 65, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 220, 220, 255))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4.0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 12, 6)

    def _update_row_theme(self, module: WindowBase, depth: int, has_group: bool, group_idx: int) -> None:
        """Update or create dynamic themes for a specific module row based on nesting."""
        palette = theme_manager.active_palette

        if module.UUID not in self.row_themes:
            self.row_themes[module.UUID] = {
                "btn": dpg.add_theme(),
                "status": dpg.add_theme(),
                "restore": dpg.add_theme(),
            }

        themes = self.row_themes[module.UUID]
        for t in themes.values():
            if dpg.does_item_exist(t):
                for child in dpg.get_item_children(t, 1) or []:
                    dpg.delete_item(child)

        btn_bg = palette.get("button", (51, 51, 55, 255))
        btn_hover = palette.get("button_hovered", (30, 60, 80, 255))
        btn_active = palette.get("button_active", (56, 56, 58, 255))
        border_col = palette.get("border", (110, 110, 128, 128))
        text_col = palette.get("text", (255, 255, 255, 255))

        if has_group:
            base_color = SIGNATURE_COLORS[group_idx % len(SIGNATURE_COLORS)]
            alpha = max(100, 255 - depth * 55)

            btn_val = (*base_color, alpha)
            btn_h = (
                min(255, int(base_color[0] * 1.15)),
                min(255, int(base_color[1] * 1.15)),
                min(255, int(base_color[2] * 1.15)),
                min(255, alpha + 30),
            )
            btn_a = (
                int(base_color[0] * 0.85),
                int(base_color[1] * 0.85),
                int(base_color[2] * 0.85),
                alpha,
            )
            border_val = (*base_color, min(255, alpha + 40))

            with dpg.theme_component(dpg.mvButton, parent=themes["btn"]):
                dpg.add_theme_color(dpg.mvThemeCol_Button, btn_val)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, btn_h)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, btn_a)
                dpg.add_theme_color(dpg.mvThemeCol_Border, border_val)
                dpg.add_theme_color(dpg.mvThemeCol_Text, text_col)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4.0)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 10, 6)
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1.0)

            with dpg.theme_component(dpg.mvText, parent=themes["status"]):
                dpg.add_theme_color(dpg.mvThemeCol_Text, btn_val)

            with dpg.theme_component(dpg.mvButton, parent=themes["restore"]):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (180, 70, 70, max(45, alpha - 75)))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (220, 80, 80, 180))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (250, 100, 100, 220))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 220, 220, 255))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4.0)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 4)
        else:
            with dpg.theme_component(dpg.mvButton, parent=themes["btn"]):
                dpg.add_theme_color(dpg.mvThemeCol_Button, btn_bg)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, btn_hover)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, btn_active)
                dpg.add_theme_color(dpg.mvThemeCol_Border, border_col)
                dpg.add_theme_color(dpg.mvThemeCol_Text, text_col)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4.0)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 10, 6)
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1.0)

            with dpg.theme_component(dpg.mvText, parent=themes["status"]):
                dpg.add_theme_color(dpg.mvThemeCol_Text, (46, 204, 113, 200))

    def _build_flat_tree(self) -> List[Tuple[WindowBase, int, List[WindowBase], int]]:
        """
        Builds a flat list representing the tree structure of all modules.
        Each element is a tuple: (module, depth, path_to_root, group_index)
        """
        modules = get_registered_modules()
        roots = [m for m in modules if m.merged_into is None]
        roots.sort(key=lambda m: m.label.lower())

        children_map: Dict[str, List[WindowBase]] = {}
        for m in modules:
            if m.merged_into is not None:
                parent_uuid = m.merged_into.UUID
                children_map.setdefault(parent_uuid, []).append(m)

        for parent_uuid in children_map:
            children_map[parent_uuid].sort(key=lambda m: m.label.lower())

        flat_list: List[Tuple[WindowBase, int, List[WindowBase], int]] = []

        def traverse(module: WindowBase, depth: int, path: List[WindowBase], group_idx: int) -> None:
            flat_list.append((module, depth, path, group_idx))
            parent_uuid = module.UUID
            if parent_uuid in children_map:
                for child in children_map[parent_uuid]:
                    traverse(child, depth + 1, [module] + path, group_idx)

        for idx, root in enumerate(roots):
            traverse(root, 0, [], idx)

        return flat_list

    def refresh(
        self,
        sender: Optional[Union[int, str]] = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Refresh the table content with all currently registered modules.

        Clears the current table rows and rebuilds them based on MODULES_REGISTRY.
        """
        if not dpg.does_item_exist(self.table_id):
            return

        self._update_static_themes()

        flat_tree = self._build_flat_tree()

        current_uuids = {item[0].UUID for item in flat_tree}
        for uuid in list(self.row_themes.keys()):
            if uuid not in current_uuids:
                deleted_themes = self.row_themes.pop(uuid)
                for t_id in deleted_themes.values():
                    if dpg.does_item_exist(t_id):
                        dpg.delete_item(t_id)

        group_sizes: Dict[int, int] = {}
        for _, _, _, group_idx in flat_tree:
            group_sizes[group_idx] = group_sizes.get(group_idx, 0) + 1

        for child in dpg.get_item_children(self.table_id, 1):
            dpg.delete_item(child)

        self.tag_to_module.clear()

        for module, depth, path, group_idx in flat_tree:
            btn_tag = f"fusion_btn_{module.UUID}"
            self.tag_to_module[btn_tag] = module

            has_group = group_sizes[group_idx] > 1

            prefix = ""
            if depth > 0:
                prefix = "      " * (depth - 1) + "  \u2514\u2500 "

            self._update_row_theme(module, depth, has_group, group_idx)
            themes = self.row_themes[module.UUID]

            with dpg.table_row(parent=self.table_id):
                dpg.add_button(
                    label=f"{prefix}\uf0c9  {module.label}",
                    tag=btn_tag,
                    drop_callback=self._on_drop,
                    payload_type="windows_fusion",
                    user_data=module,
                    callback=self._focus_window,
                )
                dpg.bind_item_theme(btn_tag, themes["btn"])

                with dpg.tooltip(parent=btn_tag):
                    dpg.add_text(
                        "Left-Click: Focus window\nRight-Click: Auto-size window\nDrag & Drop: Drop onto another module button to merge"
                    )

                dpg.add_drag_payload(
                    parent=btn_tag,
                    payload_type="windows_fusion",
                    drag_data=module.UUID,
                )

                handler_tag = f"handler_{btn_tag}"
                if dpg.does_item_exist(handler_tag):
                    dpg.delete_item(handler_tag)

                with dpg.item_handler_registry(tag=handler_tag):
                    dpg.add_item_clicked_handler(
                        button=dpg.mvMouseButton_Right,
                        callback=self._on_right_click_module,
                        user_data=module,
                    )
                dpg.bind_item_handler_registry(btn_tag, handler_tag)

                if depth == 0:
                    if has_group:
                        txt = dpg.add_text(f"\uf0c0  Merge Host ({group_sizes[group_idx] - 1} nested)")
                    else:
                        txt = dpg.add_text("\uf05d  Standalone")
                else:
                    chain_str = " \uf061 ".join(p.label for p in path[::-1])
                    txt = dpg.add_text(f"\uf0c1  inside {chain_str}")

                dpg.bind_item_theme(txt, themes["status"])

                if module.merged_into:
                    btn_restore = dpg.add_button(
                        label="\uf0e2  Restore",
                        width=display_scaling.scale(150),
                        user_data=module,
                        callback=lambda s, a, u: self._restore(u),
                    )
                    dpg.bind_item_theme(btn_restore, themes["restore"])
                    with dpg.tooltip(parent=btn_restore):
                        dpg.add_text("Separate this module and restore it back as a standalone window.")
                else:
                    dpg.add_spacer(width=0, height=0)

    def _on_drop(self, sender: Union[int, str], app_data: Any, user_data: Any) -> None:
        """
        Handle the 'drop' event to merge modules.

        Args:
            sender: The tag of the item receiving the drop (the target).
            app_data: The payload data (UUID of the source module).
        """
        target_module = self.tag_to_module.get(str(sender))
        source_uuid = app_data

        if not target_module or not source_uuid:
            return

        source_module = next((m for m in get_registered_modules() if m.UUID == source_uuid), None)

        if not source_module or source_module == target_module:
            return

        try:
            source_module.merge_into(target_module)
            self.refresh()
        except Exception as e:
            logger.error(f"Failed to merge {source_module.label} into {target_module.label}: {e}")

    def _restore(self, module: WindowBase) -> None:
        """Restore a merged module to its original state."""
        if hasattr(module, "restore_contents"):
            module.restore_contents()
            self.refresh()

    def _restore_all(
        self,
        sender: Optional[Union[int, str]] = None,
        app_data: Any = None,
        user_data: Any = None,
    ) -> None:
        """Restore all merged modules back to standalone windows."""
        restored_count = 0
        for module in get_registered_modules():
            if module.merged_into:
                if hasattr(module, "restore_contents"):
                    try:
                        module.restore_contents()
                        restored_count += 1
                    except Exception as e:
                        logger.error(f"Failed to restore {module.label} during mass restore: {e}")
        if restored_count > 0:
            logger.info(f"Mass restore completed. Restored {restored_count} module(s).")
        self.refresh()

    def _focus_window(
        self,
        sender: Union[int, str],
        app_data: Any,
        user_data: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Focus the window associated with the module."""
        win_id = getattr(user_data, "winID", None)
        if win_id and dpg.does_item_exist(win_id):
            dpg.show_item(win_id)
            dpg.focus_item(win_id)

    def _on_right_click_module(
        self,
        sender: Union[int, str],
        app_data: Any,
        user_data: Optional[WindowBase],
        *args: Any,
    ) -> None:
        """Trigger autosize on the target module window when right-clicked."""
        if user_data:
            user_data.autosize_window()

