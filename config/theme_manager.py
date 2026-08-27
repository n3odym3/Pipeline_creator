"""
Theme Manager module for Pipeline Creator.

Manages application UI palettes, visual theme switching, custom node/link subthemes,
font scaling, and OS window title bar styling.
"""

import sys
from typing import Any, Dict, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.config_manager import config
from core.paths import PROJECT_ROOT


def _discover_palettes() -> Dict[str, Dict[str, Any]]:
    """Auto-discover all palette dictionaries in theme_colors.py."""
    import config.theme_colors as _theme_colors_module

    palettes: Dict[str, Dict[str, Any]] = {}
    for attr_name, palette in vars(_theme_colors_module).items():
        if attr_name.isupper() and not attr_name.startswith("_"):
            if isinstance(palette, dict):
                palettes[attr_name] = palette
    return palettes


PALETTES: Dict[str, Dict[str, Any]] = _discover_palettes()


class ThemeManager:
    """
    Centralized theme and font management.
    Uses the unified 'consola_awesome.ttf' as the sole application font.
    """

    def __init__(self) -> None:
        self.active_palette: Dict[str, Any] = PALETTES.get("DEFAULT", {})
        self.active_palette_name: str = "DEFAULT"
        self.current_palette: Dict[str, Any] = self.active_palette
        self.global_theme: Optional[Union[int, str]] = None
        self.sub_themes: Dict[str, Union[int, str]] = {}
        self.main_font: Optional[Union[int, str]] = None

        self._load_base_font()
        theme_name = config.get("UI", {}).get("theme_name", "DEFAULT")
        self.load_theme(theme_name)

    def _load_base_font(self) -> None:
        """
        Load the unified 'consola_awesome.ttf'.
        This font contains both Consola and FontAwesome icons at correct scale.
        """
        font_path = PROJECT_ROOT / "ressources" / "consola_awesome.ttf"
        base_size = 20.0

        with dpg.font_registry():
            if font_path.exists():
                logger.debug(f"Loading unified super-font: {font_path.name}")
                self.main_font = dpg.add_font(str(font_path), base_size)
                dpg.bind_font(self.main_font)
            else:
                logger.error(f"Super-font missing: {font_path}. Run merge_fonts.py first!")

    def refresh_fonts(self) -> None:
        """
        Adjust the super-font size based on display scaling.
        DPG handles font scaling automatically on size change.
        """
        if not self.main_font:
            self._load_base_font()

        target_size = max(8, int(round(20 * display_scaling.scale_factor)))

        if self.main_font:
            dpg.configure_item(self.main_font, size=target_size)

        dpg.set_global_font_scale(1.0)
        logger.debug(f"UI Font resized to {target_size}px")

    def apply_icon_font(self, item: Union[int, str]) -> None:
        """Kept for backward compatibility with external callers."""
        pass

    def load_theme(self, name: str, force: bool = False) -> None:
        """Switch the UI palette and refresh visual themes."""
        if name not in PALETTES:
            logger.warning(f"Theme '{name}' not found, using DEFAULT")
            name = "DEFAULT"

        if not force and self.active_palette_name == name and self.global_theme is not None:
            logger.debug(f"Theme '{name}' is already loaded, skipping redundant refresh")
            return

        self.active_palette_name = name
        self.active_palette = PALETTES[name]
        self.refresh()

    def refresh(self) -> None:
        """Rebuild all DPG themes using the current palette."""
        from config.theme_factory import build_global_theme, build_standard_subthemes

        ui_config = config.get("UI", {})
        merged_palette = self.active_palette.copy()

        cb_type = ui_config.get("colorblind_type", "none")
        if cb_type != "none":
            merged_palette["is_colorblind"] = True
            merged_palette["colorblind_type"] = cb_type

        self.current_palette = merged_palette
        self.global_theme = build_global_theme(merged_palette)
        self.sub_themes = build_standard_subthemes(merged_palette)

        dpg.bind_theme(self.global_theme)
        self.refresh_fonts()

        # Sync tutorial manager overlay theme if initialized
        try:
            from core.tutorial_manager import tutorial_manager

            if getattr(tutorial_manager, "_ui_initialized", False):
                tutorial_manager._build_overlay_theme()
        except Exception:
            pass

        # Trigger a re-colorization of nodes in the editor on the fly
        if "core.main_win" in sys.modules:
            try:
                from core.main_win import main_win

                if main_win and hasattr(main_win, "node_editor") and main_win.node_editor:
                    main_win.node_editor.recolor_all_nodes()
            except (ImportError, AttributeError):
                pass

    def apply_icon_font_to_parent_only(self, parent_item: Union[int, str]) -> None:
        """Bind the main font to a parent container if it's not already global."""
        if self.main_font:
            dpg.bind_item_font(parent_item, self.main_font)

    # Factory proxies for NodeEditor
    def create_highlight_theme(self) -> Optional[Union[int, str]]:
        return self.sub_themes.get("highlight")

    def create_virtual_link_theme(self) -> Optional[Union[int, str]]:
        return self.sub_themes.get("virtual_link")

    def create_red_node_theme(self) -> Optional[Union[int, str]]:
        return self.sub_themes.get("red_node")

    def create_red_link_theme(self) -> Optional[Union[int, str]]:
        return self.sub_themes.get("red_link")

    def create_dimmed_node_theme(self) -> Optional[Union[int, str]]:
        return self.sub_themes.get("dimmed_node")

    def create_dimmed_link_theme(self) -> Optional[Union[int, str]]:
        return self.sub_themes.get("dimmed_link")

    def get_output_color_theme_from_cache(
        self,
        index: int,
        cache: Dict[Any, Tuple[Union[int, str], Union[int, str], Tuple[int, int, int, int]]],
    ) -> Tuple[Union[int, str], Union[int, str], Tuple[int, int, int, int]]:
        """Used by Node Editor to colorize output ports based on data type."""
        from config.theme_factory import get_configurable_color_theme

        return get_configurable_color_theme(index, cache, getattr(self, "current_palette", self.active_palette))

    def create_main_win_theme(self) -> Union[int, str]:
        """Create and return a specific window theme for the main window."""
        theme = dpg.add_theme()
        mw_bg = self.active_palette.get("main_win_bg", self.active_palette["window_bg"])
        mb_bg = self.active_palette.get("main_menubar_bg", self.active_palette["menubar_bg"])
        with dpg.theme_component(dpg.mvAll, parent=theme):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, mw_bg)
            dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, mb_bg)
        return theme

    def update_from_node_color(self, r: int, g: int, b: int) -> None:
        """
        Dynamically color the UI based on a node's color.
        Used for contextual branding when a node is selected.
        """
        from config.theme_factory import apply_titlebar_color

        app_name = config.get("General", {}).get("app_name", "Node Assistant")
        apply_titlebar_color(app_name, r, g, b)
        logger.debug(f"UI contextually colored to: RGB({r}, {g}, {b})")

    def reset_to_theme_color(self) -> None:
        """Reset the UI colors to the current theme's default palette."""
        self.update_titlebar()
        logger.debug("UI colors reset to theme default.")

    def update_titlebar(self) -> None:
        """Apply the current theme's titlebar color to the OS window."""
        r, g, b, _ = self.active_palette.get("title_bg", (85, 85, 85, 255))
        from config.theme_factory import apply_titlebar_color

        app_name = config.get("General", {}).get("app_name", "Node Assistant")
        apply_titlebar_color(app_name, r, g, b)


# Singular global instance
theme_manager: ThemeManager = ThemeManager()

