"""
Theme Factory module for Pipeline Creator.

Provides factory functions for DearPyGui themes, including custom node themes,
link themes, global themes, colorblind-safe color palettes, and Windows title bar styling.
"""

import colorsys
import ctypes
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling

# Cache Windows API signatures at module load time
if sys.platform == "win32":
    import ctypes.wintypes

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi

    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.wintypes.HWND

    dwmapi.DwmSetWindowAttribute.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
    ]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long


def apply_titlebar_color(title: str, r: int, g: int, b: int) -> None:
    """Apply custom color to the Windows title bar."""
    if sys.platform != "win32":
        return
    try:
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return
        DWMWA_CAPTION_COLOR = 35
        color = ctypes.c_int(r | (g << 8) | (b << 16))
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(color), ctypes.sizeof(color))
    except Exception as e:
        logger.warning(f"Failed to set title bar color: {e}")


def _adjust_color(rgb: Tuple[int, ...], offset: int) -> Tuple[int, int, int]:
    """Adjust RGB components by an integer offset within [0, 255]."""
    r, g, b = (max(0, min(255, c + offset)) for c in rgb[:3])
    return (r, g, b)


def build_node_theme(
    title_rgb: Tuple[int, int, int],
    outline_rgb: Tuple[int, int, int],
    alpha: int = 255,
    thickness: float = 4.0,
    dimmed_text: bool = False,
    input_border: Tuple[int, int, int, int] = (40, 40, 40, 255),
    node_bg: Optional[Tuple[int, int, int, int]] = None,
    text_color: Optional[Tuple[int, int, int, int]] = None,
) -> Union[int, str]:
    """Build a node-specific DearPyGui theme with custom title, outline, and background colors."""
    theme = dpg.add_theme()
    title_hover_rgb = _adjust_color(title_rgb, 20)
    with dpg.theme_component(dpg.mvNode, parent=theme):
        dpg.add_theme_color(dpg.mvNodeCol_TitleBar, (*title_rgb, alpha), category=dpg.mvThemeCat_Nodes)
        dpg.add_theme_color(dpg.mvNodeCol_TitleBarHovered, (*title_hover_rgb, alpha), category=dpg.mvThemeCat_Nodes)
        dpg.add_theme_color(dpg.mvNodeCol_TitleBarSelected, (*title_rgb, alpha), category=dpg.mvThemeCat_Nodes)
        dpg.add_theme_color(dpg.mvNodeCol_NodeOutline, (*outline_rgb, alpha), category=dpg.mvThemeCat_Nodes)
        dpg.add_theme_color(dpg.mvNodeCol_BoxSelectorOutline, (*outline_rgb, alpha), category=dpg.mvThemeCat_Nodes)
        if node_bg:
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackground, node_bg, category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeBackgroundHovered,
                _adjust_color(node_bg[:3], 10) + (node_bg[3],),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundSelected, node_bg, category=dpg.mvThemeCat_Nodes)
        try:
            dpg.add_theme_style(dpg.mvNodeStyleVar_NodeBorderThickness, thickness, category=dpg.mvThemeCat_Nodes)
        except AttributeError:
            pass

    with dpg.theme_component(dpg.mvInputText, parent=theme):
        dpg.add_theme_color(dpg.mvThemeCol_Border, input_border, category=dpg.mvThemeCat_Core)
        dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1.0, category=dpg.mvThemeCat_Core)
        if text_color:
            dpg.add_theme_color(dpg.mvThemeCol_Text, text_color, category=dpg.mvThemeCat_Core)

    with dpg.theme_component(dpg.mvWindowAppItem, parent=theme):
        dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (*title_rgb, alpha), category=dpg.mvThemeCat_Core)
        dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (*title_rgb, alpha), category=dpg.mvThemeCat_Core)
        dpg.add_theme_color(dpg.mvThemeCol_Border, (*outline_rgb, alpha), category=dpg.mvThemeCat_Core)
        dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, thickness, category=dpg.mvThemeCat_Core)
        if dimmed_text:
            dpg.add_theme_color(dpg.mvThemeCol_Text, (160, 160, 160, max(0, alpha - 20)), category=dpg.mvThemeCat_Core)

    # Target child_window wrappers (used by merged/fused modules)
    with dpg.theme_component(dpg.mvChildWindow, parent=theme):
        dpg.add_theme_color(dpg.mvThemeCol_Border, (*outline_rgb, alpha), category=dpg.mvThemeCat_Core)
        dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, thickness, category=dpg.mvThemeCat_Core)
        if dimmed_text:
            dpg.add_theme_color(dpg.mvThemeCol_Text, (160, 160, 160, max(0, alpha - 20)), category=dpg.mvThemeCat_Core)

    if text_color and not dimmed_text:
        with dpg.theme_component(dpg.mvAll, parent=theme):
            dpg.add_theme_color(dpg.mvThemeCol_Text, text_color, category=dpg.mvThemeCat_Core)

    if dimmed_text:
        with dpg.theme_component(dpg.mvAll, parent=theme):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (160, 160, 160, max(0, alpha - 20)), category=dpg.mvThemeCat_Core)

    return theme


def build_link_theme(outline_rgb: Tuple[int, int, int], alpha: int = 255, thickness: float = 4.0) -> Union[int, str]:
    """Build a node link DearPyGui theme."""
    theme = dpg.add_theme()
    link_hover_rgb = _adjust_color(outline_rgb, 20)
    with dpg.theme_component(0, parent=theme):
        dpg.add_theme_color(dpg.mvNodeCol_Link, (*outline_rgb, alpha), category=dpg.mvThemeCat_Nodes)
        dpg.add_theme_color(dpg.mvNodeCol_LinkHovered, (*link_hover_rgb, alpha), category=dpg.mvThemeCat_Nodes)
        dpg.add_theme_color(dpg.mvNodeCol_LinkSelected, (*outline_rgb, alpha), category=dpg.mvThemeCat_Nodes)
        try:
            dpg.add_theme_style(dpg.mvNodeStyleVar_LinkThickness, thickness, category=dpg.mvThemeCat_Nodes)
        except AttributeError:
            pass
    return theme


def build_global_theme(colors: Dict[str, Any]) -> Union[int, str]:
    """Centralized logic for building the global application theme with full visual fidelity."""
    s = display_scaling.scale
    with dpg.theme() as global_theme:
        with dpg.theme_component(0):
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, s(5))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, s(5))
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, s(5))
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, s(5))
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, s(5))
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, s(20))

            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, s(8), s(8))
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, s(10), s(5))
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, s(10), s(5))
            dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing, s(6), s(5))
            dpg.add_theme_style(dpg.mvStyleVar_IndentSpacing, s(20))
            dpg.add_theme_style(dpg.mvStyleVar_GrabMinSize, s(10))
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)

            # Colors
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, colors.get("window_bg", (40, 40, 50, 255)))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, colors.get("child_bg", (0, 0, 0, 0)))
            dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, colors.get("menubar_bg", (55, 55, 65, 255)))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, colors.get("popup_bg", colors.get("window_bg", (40, 40, 50, 255))))

            # Frames
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, colors.get("frame_bg", (30, 30, 40, 255)))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, colors.get("frame_bg_hovered", (35, 50, 80, 255)))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, colors.get("frame_bg_active", (20, 110, 170, 255)))

            # Buttons
            dpg.add_theme_color(
                dpg.mvThemeCol_Button,
                colors.get("button", (150, 153, 168, 255) if colors.get("is_light") else (45, 45, 48, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_ButtonHovered,
                colors.get("button_hovered", (30, 60, 80, 255) if colors.get("is_light") else (60, 60, 62, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_ButtonActive,
                colors.get("button_active", (108, 122, 148, 255) if colors.get("is_light") else (70, 70, 72, 255)),
            )

            # Headers
            dpg.add_theme_color(
                dpg.mvThemeCol_Header,
                colors.get("header", (155, 205, 135, 255) if colors.get("is_light") else (45, 45, 48, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_HeaderHovered,
                colors.get("header_hovered", (180, 225, 255, 255) if colors.get("is_light") else (60, 60, 62, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_HeaderActive,
                colors.get("header_active", (80, 160, 230, 255) if colors.get("is_light") else (80, 80, 82, 255)),
            )

            # Titles
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg, colors.get("title_bg", (85, 85, 85, 255)))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, colors.get("title_bg_active", (15, 86, 135, 255)))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgCollapsed, colors.get("title_bg_collapsed", (85, 85, 85, 180)))

            # Borders & Resize handles
            dpg.add_theme_color(dpg.mvThemeCol_Border, colors.get("border", (110, 110, 128, 128)))
            dpg.add_theme_color(dpg.mvThemeCol_BorderShadow, (0, 0, 0, 0))
            dpg.add_theme_color(
                dpg.mvThemeCol_ResizeGrip,
                colors.get("resize_grip", (120, 123, 140, 50) if colors.get("is_light") else (45, 45, 55, 50)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_ResizeGripHovered,
                colors.get("resize_grip_hovered", (150, 153, 168, 170) if colors.get("is_light") else (80, 80, 85, 170)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_ResizeGripActive,
                colors.get("resize_grip_active", (75, 110, 175, 230) if colors.get("is_light") else (20, 110, 170, 230)),
            )

            # Text
            dpg.add_theme_color(
                dpg.mvThemeCol_Text,
                colors.get("text", (25, 45, 25, 255) if colors.get("is_light") else (255, 255, 255, 255)),
            )
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, colors.get("text_disabled", (120, 122, 130, 255)))

            # Selectors & Grabs
            dpg.add_theme_color(
                dpg.mvThemeCol_CheckMark,
                colors.get("checkmark", (60, 90, 150, 255) if colors.get("is_light") else (20, 110, 170, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_SliderGrab,
                colors.get("slider_grab", (90, 110, 150, 255) if colors.get("is_light") else (100, 100, 100, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_SliderGrabActive,
                colors.get("slider_grab_active", (65, 90, 135, 255) if colors.get("is_light") else (150, 150, 150, 255)),
            )

            # Scrollbars
            dpg.add_theme_color(
                dpg.mvThemeCol_ScrollbarBg,
                colors.get("scrollbar_bg", (165, 167, 177, 255) if colors.get("is_light") else (45, 45, 55, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_ScrollbarGrab,
                colors.get("scrollbar_grab", (130, 133, 148, 255) if colors.get("is_light") else (80, 80, 80, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_ScrollbarGrabHovered,
                colors.get("scrollbar_grab_hovered", (150, 153, 168, 255) if colors.get("is_light") else (100, 100, 100, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_ScrollbarGrabActive,
                colors.get("scrollbar_grab_active", (170, 173, 188, 255) if colors.get("is_light") else (120, 120, 120, 255)),
            )

            # Separators
            dpg.add_theme_color(
                dpg.mvThemeCol_Separator,
                colors.get("separator", (125, 127, 140, 210) if colors.get("is_light") else (70, 70, 80, 210)),
            )

            # Tables
            dpg.add_theme_color(
                dpg.mvThemeCol_TableHeaderBg,
                colors.get("table_header_bg", (160, 168, 188, 255) if colors.get("is_light") else (50, 50, 60, 255)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_TableBorderLight,
                colors.get("table_border_light", (140, 142, 150, 100) if colors.get("is_light") else (70, 70, 80, 100)),
            )
            dpg.add_theme_color(
                dpg.mvThemeCol_TableBorderStrong,
                colors.get("table_border_strong", (120, 122, 130, 200) if colors.get("is_light") else (90, 90, 100, 200)),
            )

            # Plot specific in general context
            dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2, category=dpg.mvThemeCat_Plots)
            dpg.add_theme_style(dpg.mvPlotStyleVar_FillAlpha, 0.50)
            dpg.add_theme_color(dpg.mvPlotCol_Crosshairs, colors.get("plot_crosshairs", (255, 0, 0, 255)), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_Fill, colors.get("plot_fill", (100, 100, 100, 50)), category=dpg.mvThemeCat_Plots)

        with dpg.theme_component(dpg.mvNode):
            dpg.add_theme_style(dpg.mvNodeStyleVar_NodeBorderThickness, 1.0, category=dpg.mvThemeCat_Nodes)

            node_bg = colors.get("node_bg", (45, 45, 50, 255))
            node_title = colors.get("node_title_bg", (65, 65, 75, 255))

            dpg.add_theme_color(dpg.mvNodeCol_NodeBackground, node_bg, category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeBackgroundHovered,
                _adjust_color(node_bg[:3], 10) + (node_bg[3],),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(dpg.mvNodeCol_NodeBackgroundSelected, node_bg, category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_TitleBar, node_title, category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(
                dpg.mvNodeCol_TitleBarHovered,
                _adjust_color(node_title[:3], 15) + (node_title[3],),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(dpg.mvNodeCol_TitleBarSelected, node_title, category=dpg.mvThemeCat_Nodes)

        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvNodeCol_GridBackground, colors.get("node_grid_bg", (37, 37, 38, 255)), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_GridLine, colors.get("node_grid_line", (50, 50, 50, 255)), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_Link, colors.get("link", (140, 140, 140, 255)), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_LinkHovered, colors.get("link_hovered", (180, 180, 180, 255)), category=dpg.mvThemeCat_Nodes)
            dpg.add_theme_color(dpg.mvNodeCol_LinkSelected, colors.get("link_selected", (220, 220, 220, 255)), category=dpg.mvThemeCat_Nodes)

            dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2, category=dpg.mvThemeCat_Plots)
            dpg.add_theme_style(dpg.mvPlotStyleVar_FillAlpha, 0.50)
            dpg.add_theme_color(dpg.mvPlotCol_Crosshairs, colors.get("plot_crosshairs", (255, 0, 0, 255)), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_Fill, colors.get("plot_fill", (100, 100, 100, 50)), category=dpg.mvThemeCat_Plots)

        with dpg.theme_component(dpg.mvInputText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, colors.get("text", (255, 255, 255, 255)), category=dpg.mvThemeCat_Core)

    return global_theme


def hsl_to_rgb(h: float, s: float, l: float) -> Tuple[int, int, int]:
    """Convert HSL color values to RGB integers [0, 255]."""
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return int(r * 255), int(g * 255), int(b * 255)


# Colorblind-safe palettes for different types of vision deficiencies
COLORBLIND_PALETTES: Dict[str, List[Tuple[int, int, int]]] = {
    "universal": [
        (230, 159, 0),  # Orange
        (86, 180, 233),  # Sky Blue
        (0, 158, 115),  # Bluish Green
        (240, 228, 66),  # Yellow
        (0, 114, 178),  # Blue
        (213, 94, 0),  # Vermillion
        (204, 121, 167),  # Reddish Purple
    ],
    "protanopia": [  # Red-blind
        (0, 78, 162),  # Royal Blue
        (255, 215, 0),  # Gold/Yellow
        (86, 180, 233),  # Sky Blue
        (0, 0, 0),  # Black
        (120, 120, 120),  # Mid Gray
        (255, 255, 255),  # White
        (213, 180, 60),  # Mustard
    ],
    "deuteranopia": [  # Green-blind
        (0, 107, 164),  # Dark Blue
        (255, 188, 121),  # Pale Orange
        (171, 171, 171),  # Light Gray
        (106, 137, 204),  # Lavender Blue
        (162, 103, 38),  # Brownish
        (95, 158, 160),  # Cadet Blue
        (204, 121, 167),  # Reddish Purple
    ],
    "tritanopia": [  # Blue-yellow blind
        (220, 50, 32),  # Bright Red
        (0, 163, 235),  # Cyan
        (255, 192, 203),  # Pink
        (25, 25, 25),  # Dark Gray
        (0, 128, 128),  # Teal
        (128, 0, 0),  # Maroon
        (100, 255, 100),  # Bright Green
    ],
}


def get_configurable_color_theme(
    output_index: int,
    cache: Dict[Any, Tuple[Union[int, str], Union[int, str], Tuple[int, int, int, int]]],
    colors: Dict[str, Any],
) -> Tuple[Union[int, str], Union[int, str], Tuple[int, int, int, int]]:
    """Retrieve or generate a node/link theme pair dynamically from color configuration."""
    if colors.get("is_colorblind"):
        ptype = colors.get("colorblind_type", "universal")
        palette = COLORBLIND_PALETTES.get(ptype, COLORBLIND_PALETTES["universal"])
        idx = output_index % len(palette)
        r_outline, g_outline, b_outline = palette[idx]

        r_title, g_title, b_title = _adjust_color((r_outline, g_outline, b_outline), -40)
        hue_key = f"colorblind_{ptype}_{idx}"
    else:
        base_hue = colors.get("base_hue", 120.0)
        sat = colors.get("sat", 0.85)
        GOLDEN_ANGLE = 137.508
        hue_deg = (base_hue + output_index * GOLDEN_ANGLE) % 360
        hue_key = int(hue_deg)

        h = hue_deg / 360.0
        r_title, g_title, b_title = hsl_to_rgb(h, sat, 0.35)
        r_outline, g_outline, b_outline = hsl_to_rgb(h, sat, 0.55)

    if hue_key in cache:
        return cache[hue_key]

    node_bg = colors.get("node_bg")
    is_light = colors.get("is_light")
    text_border = (180, 180, 180, 255) if is_light else (40, 40, 40, 255)
    text_color = colors.get("text", (255, 255, 255, 255))

    thickness = 5.0 if colors.get("is_colorblind") else 4.0

    node_theme = build_node_theme(
        (r_title, g_title, b_title),
        (r_outline, g_outline, b_outline),
        node_bg=node_bg,
        input_border=text_border,
        thickness=thickness,
        text_color=text_color,
    )
    link_theme = build_link_theme((r_outline, g_outline, b_outline), thickness=thickness + 1)
    port_text_color = (r_outline, g_outline, b_outline, 255)
    cache[hue_key] = (node_theme, link_theme, port_text_color)
    return node_theme, link_theme, port_text_color


def build_standard_subthemes(colors: Dict[str, Any]) -> Dict[str, Union[int, str]]:
    """Return a dictionary of standard sub-themes used by the NodeEditor."""
    is_light = colors.get("is_light", False)
    text_border = (180, 180, 180, 255) if is_light else (40, 40, 40, 255)
    node_bg = colors.get("node_bg")
    text_color = colors.get("text", (255, 255, 255, 255))

    return {
        "highlight": build_node_theme((200, 110, 0), (255, 160, 0), input_border=text_border, node_bg=node_bg, text_color=text_color),
        "virtual_link": build_link_theme((210, 120, 0)),
        "red_node": build_node_theme((200, 40, 40), (255, 60, 60), input_border=text_border, node_bg=node_bg, text_color=text_color),
        "red_link": build_link_theme((255, 60, 60)),
        "dimmed_node": build_node_theme(
            (50, 50, 50) if not is_light else (180, 180, 185),
            (70, 70, 70) if not is_light else (190, 190, 195),
            alpha=160,
            thickness=1.0,
            dimmed_text=True,
            node_bg=node_bg,
            text_color=text_color,
        ),
        "dimmed_link": build_link_theme((50, 50, 50) if not is_light else (190, 190, 195), alpha=140, thickness=1.0),
    }

