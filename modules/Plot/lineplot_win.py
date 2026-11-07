from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
import numpy as np
import pandas as pd
from loguru import logger

from config.display_scaling import display_scaling
from core.window_base import WindowBase
from core.input_output_types import IOTypes
from core.app_state import app_state
from core.tools.clipboard_injector import clipboardinjector

_LINE_SERIES = "mvAppItemType::mvLineSeries"
_OFFSCREEN = (-999999, -999999)

# Canonical axis names and the legacy aliases that map onto them.
AXIS_PRIMARY = "Primary (Left)"
AXIS_SEC1 = "Secondary 1 (Right)"
AXIS_SEC2 = "Secondary 2 (Right)"
_AXIS_OPTIONS = [AXIS_PRIMARY, AXIS_SEC1, AXIS_SEC2]
_AXIS_SHORT = ["Default", "1", "2"]  # legend context menu labels (same order)
_AXIS_ALIASES = {
    "Primary": AXIS_PRIMARY, "Primary (Y1)": AXIS_PRIMARY, "Default": AXIS_PRIMARY,
    "Secondary": AXIS_SEC1, "Secondary (Y2)": AXIS_SEC1, "Secondary 1": AXIS_SEC1, "Secondary (Right)": AXIS_SEC1,
    "Secondary 2": AXIS_SEC2, "Tertiary": AXIS_SEC2, "Y3": AXIS_SEC2,
}


def _canon_axis(name: Optional[str]) -> str:
    """Return the canonical axis name for any known alias (unknown -> primary)."""
    name = name or AXIS_PRIMARY
    return name if name in _AXIS_OPTIONS else _AXIS_ALIASES.get(name, AXIS_PRIMARY)


# ----------------------------------------------------------------------------
# Smoothing filters (pure functions, 1D float arrays -> list)
# ----------------------------------------------------------------------------
def _odd_window(w: int, n: int) -> int:
    """Largest valid odd window <= n, starting from w (rounded up to odd)."""
    w = w if w % 2 else w + 1
    return w if w <= n else (n if n % 2 else n - 1)


def _smooth_rolling_median(arr: np.ndarray, w: int) -> List[float]:
    if w <= 1:
        return arr.tolist()
    if w >= len(arr):
        return [float(np.median(arr))] * len(arr)
    try:
        import pandas as pd
        return pd.Series(arr).rolling(window=w, min_periods=1, center=True).median().to_numpy().tolist()
    except ImportError:
        from scipy.signal import medfilt
        k = _odd_window(w, len(arr))
        return medfilt(arr, kernel_size=k).tolist() if k >= 3 else arr.tolist()


def _smooth_rolling_mean(arr: np.ndarray, w: int) -> List[float]:
    if w >= len(arr):
        return [float(np.mean(arr))] * len(arr)
    if w <= 1:
        return arr.tolist()
    try:
        import pandas as pd
        return pd.Series(arr).rolling(window=w, min_periods=1, center=True).mean().to_numpy().tolist()
    except ImportError:
        half = w // 2
        padded = np.pad(arr, (half, w - 1 - half), mode="edge")
        return np.convolve(padded, np.ones(w) / w, mode="valid").tolist()


def _smooth_ema(arr: np.ndarray, w: int) -> List[float]:
    alpha = 2.0 / (max(1.0, float(w)) + 1.0)
    out = np.empty_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
    return out.tolist()


def _smooth_savgol(arr: np.ndarray, w: int) -> List[float]:
    n = len(arr)
    if n < 3:
        return arr.tolist()
    try:
        from scipy.signal import savgol_filter
    except ImportError:
        logger.error("scipy is required for Savitzky-Golay filter.")
        return arr.tolist()
    w = _odd_window(max(3, w), n)
    if w < 3:
        return arr.tolist()
    try:
        return savgol_filter(arr, window_length=w, polyorder=min(2, w - 1), mode="interp").tolist()
    except Exception as e:
        logger.warning(f"Savitzky-Golay fallback triggered: {e}")
        return arr.tolist()


def _smooth_gaussian(arr: np.ndarray, w: int) -> List[float]:
    sigma = max(0.1, float(w) / 2.0)
    try:
        from scipy.ndimage import gaussian_filter1d
        return gaussian_filter1d(arr, sigma=sigma, mode="nearest").tolist()
    except ImportError:
        radius = max(1, int(w))
        kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
        kernel /= kernel.sum()
        return np.convolve(np.pad(arr, radius, mode="edge"), kernel, mode="valid").tolist()


def _smooth_cma(arr: np.ndarray, w: int) -> List[float]:
    return (np.cumsum(arr, dtype=float) / np.arange(1, len(arr) + 1, dtype=float)).tolist()


_SMOOTHERS: Dict[str, Callable[[np.ndarray, int], List[float]]] = {
    "Rolling Median": _smooth_rolling_median,
    "Rolling Mean": _smooth_rolling_mean,
    "Exponential Moving Average": _smooth_ema,
    "Savitzky-Golay": _smooth_savgol,
    "Gaussian Filter": _smooth_gaussian,
    "Cumulative Moving Average": _smooth_cma,
}


class Lineplot_win(WindowBase):
    """
    Interactive 2D multi-series line plot module.

    Supports continuous real-time streaming, dynamic multi-axis allocation (primary and secondary right axes),
    interactive cursor hover inspection, Draggable vertical marker line, multiple smoothing algorithms,
    logarithmic X-axis scaling, and TSV clipboard export.
    """

    description: str = "Interactive multi-series 2D line plot with smoothing, multi-axis, and cursor annotation."

    # 1. Window Configuration
    label = "Lineplot"
    win_width = display_scaling.scale(300)
    win_height = display_scaling.scale(200)

    # 2. Input/Output definitions
    accepted_input_types = [IOTypes.SAMPLE, IOTypes.ROI_SAMPLE, IOTypes.DATALIST, IOTypes.CMD_DICT]
    outputs = {"Datalist": IOTypes.DATALIST, "DataFrame": IOTypes.DATAFRAME}

    # 3. Persistent fields
    persistent_fields = ["label", "autoscale", "smooth", "smooth_algo", "smooth_window", "log_x"]

    # Persistent field defaults
    autoscale: bool = True
    smooth: bool = False
    smooth_algo: str = "Rolling Median"
    smooth_window: int = 5
    log_x: bool = False

    _IMPLOT_COLORS: List[Tuple[int, int, int, int]] = [
        (76, 114, 176, 255),
        (221, 132, 82, 255),
        (85, 168, 104, 255),
        (196, 78, 82, 255),
        (129, 114, 179, 255),
        (147, 120, 96, 255),
        (218, 139, 195, 255),
        (140, 140, 140, 255),
        (204, 185, 116, 255),
        (100, 181, 205, 255),
    ]

    smooth_algo_items: List[str] = list(_SMOOTHERS)

    def __init__(self, **kwargs: Any) -> None:
        """
        Initialize the Lineplot window module.
        """
        super().__init__(**kwargs)

        u = self.UUID
        self.plot_tag = f"lineplot_plot_{u}"
        self.anot_check_tag = f"lineplot_anot_check_{u}"
        self.autoscale_check_tag = f"lineplot_autoscale_check_{u}"
        self.smooth_check_tag = f"lineplot_smooth_check_{u}"
        self.smooth_algo_tag = f"lineplot_smooth_algo_{u}"
        self.smooth_win_tag = f"lineplot_smooth_win_{u}"
        self.logX_check_tag = f"lineplot_logx_check_{u}"
        self.xaxis_tag = f"lineplot_x_axis_{u}"
        self.yaxis_tag = f"lineplot_y_axis_{u}"
        self.legend_tag = f"lineplot_legend_{u}"
        self.plot_popup_tag = f"lineplot_popup_{u}"
        self.popup_content_tag = f"lineplot_popup_content_{u}"
        self.closest_point_anot_tag = f"closest_point_anot_{u}"
        self.handler_tag = f"lineplot_handler_{u}"
        self.export_btn_tag = f"lineplot_export_btn_{u}"
        self.axes_btn_tag = f"lineplot_axes_btn_{u}"
        self.drag_line_check_tag = f"lineplot_vline_check_{u}"
        self.drag_line_tag = f"lineplot_vline_{u}"

        self.autoscale = bool(self.autoscale if self.autoscale is not None else True)
        self.smooth = bool(self.smooth if self.smooth is not None else False)
        self.smooth_algo = str(self.smooth_algo if self.smooth_algo in self.smooth_algo_items else "Rolling Median")
        self.smooth_window = int(self.smooth_window if self.smooth_window is not None else 5)
        self.log_x = bool(self.log_x if self.log_x is not None else False)

        self._is_closing = False
        self.selected_serie: Optional[Any] = None
        self.raw_data: Dict[str, Dict[str, Any]] = {}
        self._serie_tag_to_uid: Dict[str, str] = {}
        self.secondary_axes: Dict[str, str] = {}  # canonical axis name -> DPG axis tag
        self.series_axis: Dict[str, str] = {}  # series uid -> canonical axis name
        self.series_colors: Dict[str, List[int]] = {}
        self._displayed_colors: Dict[str, Tuple[int, int, int]] = {}  # uid -> RGB currently applied to the curve
        self._annot_active = False

        scale = display_scaling.scale
        with self.window():
            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    label="Anotation",
                    tag=self.anot_check_tag,
                    default_value=True,
                    callback=lambda s, a: None if a else self._on_mouse_right_click(),
                )
                dpg.add_checkbox(
                    label="Drag Line", tag=self.drag_line_check_tag, default_value=False, callback=self.toggle_drag_line
                )
                dpg.add_checkbox(
                    label="Autoscale",
                    tag=self.autoscale_check_tag,
                    default_value=self.autoscale,
                    callback=self.toggle_autoscale,
                )
                dpg.add_checkbox(
                    label="Smooth", tag=self.smooth_check_tag, default_value=self.smooth, callback=self.toggle_smooth
                )
                dpg.add_combo(
                    items=self.smooth_algo_items,
                    default_value=self.smooth_algo,
                    tag=self.smooth_algo_tag,
                    width=scale(120),
                    callback=self.update_smooth_algo,
                )
                dpg.add_drag_int(
                    label="Win",
                    tag=self.smooth_win_tag,
                    default_value=self.smooth_window,
                    min_value=1,
                    max_value=50,
                    width=scale(50),
                    callback=self.update_smooth_win,
                )
                dpg.add_checkbox(
                    label="Log X", tag=self.logX_check_tag, default_value=self.log_x, callback=self.toggle_log_x
                )
                dpg.add_button(
                    label="Axes",
                    tag=self.axes_btn_tag,
                    callback=lambda: (
                        self._refresh_popup_menu(),
                        dpg.show_item(self.plot_popup_tag) if dpg.does_item_exist(self.plot_popup_tag) else None,
                    ),
                )
                with dpg.popup(parent=self.axes_btn_tag, mousebutton=dpg.mvMouseButton_Left, tag=self.plot_popup_tag):
                    with dpg.group(width=scale(280)):
                        dpg.add_text("Y-Axes Configuration", color=(150, 200, 255))
                        dpg.add_separator()
                        dpg.add_group(tag=self.popup_content_tag)
                dpg.add_button(label="Export TSV", tag=self.export_btn_tag, callback=self.export_tsv_cb)
                dpg.add_button(label="Send Plot", callback=self.send_plot_cb)
                dpg.add_button(label="Clear Plot", callback=self.clear_plot)

            with dpg.plot(label="Line Serie", height=-1, width=-1, tag=self.plot_tag):
                dpg.add_plot_legend()
                self._create_base_axes(parent=None)
                dpg.add_plot_annotation(
                    tag=self.closest_point_anot_tag,
                    label="",
                    default_value=_OFFSCREEN,
                    offset=(25, -25),
                    color=[255, 255, 0, 255],
                    clamped=False,
                    show=False,
                )

            with dpg.handler_registry(tag=self.handler_tag):
                dpg.add_mouse_move_handler(callback=self.plot_change_callback)
                dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Right, callback=self._on_mouse_right_click)

            dpg.configure_item(self.plot_tag, anti_aliased=True, crosshairs=True)

        self.update_permission()

    def _create_base_axes(self, parent: Optional[str]) -> None:
        """Create the X axis and the primary Y axis (inside the plot context when parent is None)."""
        kw = {"parent": parent} if parent else {}
        dpg.add_plot_axis(dpg.mvXAxis, label="Time", tag=self.xaxis_tag, log_scale=self.log_x, **kw)
        dpg.add_plot_axis(dpg.mvYAxis, label="Intensity (Y1)", tag=self.yaxis_tag, **kw)

    def get_serie_tag(self, uid: Any) -> str:
        """Return consistent Dear PyGui item tag for a given series."""
        return f"prevplot_{self.UUID}_{uid}"

    def get_or_create_axis(self, axis_name: str) -> str:
        """
        Dynamically retrieve or create an additional Y-axis on the right plot edge.

        Args:
            axis_name: Descriptive name for the axis slot (aliases accepted).

        Returns:
            str: Dear PyGui tag of the Y-axis.
        """
        axis_name = _canon_axis(axis_name)
        if axis_name == AXIS_PRIMARY:
            return self.yaxis_tag

        if axis_name == AXIS_SEC2:
            slot_type, label, tag = dpg.mvYAxis3, "Secondary 2", f"lineplot_dyn_yaxis3_{self.UUID}"
        else:
            slot_type, label, tag = dpg.mvYAxis2, "Secondary 1", f"lineplot_dyn_yaxis2_{self.UUID}"

        if axis_name in self.secondary_axes and dpg.does_item_exist(tag):
            return tag

        if dpg.does_item_exist(tag):
            dpg.set_item_label(tag, label)
        else:
            dpg.add_plot_axis(slot_type, label=label, tag=tag, parent=self.plot_tag, opposite=True)

        self.secondary_axes[axis_name] = tag
        return tag

    def get_all_y_axes(self) -> List[str]:
        """Return all active Y-axis tags (primary left axis plus active right secondary axes)."""
        return [self.yaxis_tag] + [t for t in self.secondary_axes.values() if dpg.does_item_exist(t)]

    def _delete_secondary_axes(self, only_unused: bool = False) -> None:
        """Delete dynamic secondary Y axes (all, or only those without assigned series)."""
        active = set(self.series_axis.values())
        for name in list(self.secondary_axes):
            if only_unused and name in active:
                continue
            tag = self.secondary_axes.pop(name)
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)

    def cleanup_unused_axes(self) -> None:
        """Delete dynamically created secondary Y axes when no series are assigned to them."""
        self._delete_secondary_axes(only_unused=True)

    def set_series_axis(self, uid: str, axis_name: str) -> None:
        """Assign a series to a designated Y-axis and refresh the plot view."""
        self.series_axis[uid] = _canon_axis(axis_name)
        self.replot_all()

    def reset_all_axes(self) -> None:
        """Reset all series back to the primary Y-axis."""
        self.series_axis.clear()
        self.replot_all()

    def _line_series(self) -> List[Any]:
        """Return all line series items across all Y axes."""
        return [
            child
            for axis_tag in self.get_all_y_axes()
            for child in (dpg.get_item_children(axis_tag, 1) or [])
            if dpg.get_item_type(child) == _LINE_SERIES
        ]

    def _collect_series(self) -> List[Dict[str, Any]]:
        """Snapshot of all displayed series as {label, x, y} dicts."""
        out = []
        for child in self._line_series():
            data = dpg.get_value(child)
            out.append({
                "label": dpg.get_item_label(child),
                "x": list(data[0]) if data[0] is not None else [],
                "y": list(data[1]) if data[1] is not None else [],
            })
        return out

    # ------------------------------------------------------------------
    # Series styling / legend menu
    # ------------------------------------------------------------------
    def _set_series_theme(self, serie_id: str, uid: str, color: Any) -> None:
        """Create (or update in place) the line color theme of a series and bind it."""
        self._displayed_colors[uid] = tuple(int(c) for c in color[:3])
        theme_tag = f"lineplot_scolor_{self.UUID}_{uid}"
        if dpg.does_item_exist(theme_tag):
            # DPG deletes items lazily: re-adding the same tag right after delete_item fails.
            dpg.delete_item(theme_tag, children_only=True)
        else:
            dpg.add_theme(tag=theme_tag)
        tc = dpg.add_theme_component(dpg.mvLineSeries, parent=theme_tag)
        dpg.add_theme_color(dpg.mvPlotCol_Line, color[:4], category=dpg.mvThemeCat_Plots, parent=tc)
        if dpg.does_item_exist(serie_id):
            dpg.bind_item_theme(serie_id, theme_tag)

    def _on_color_change(self, sender: Any, app_data: Any, user_data: Any, *args: Any, **kwargs: Any) -> None:
        """Apply custom color selected from legend color picker."""
        uid, serie_id = user_data
        raw = list(app_data)[:3]
        scale = 255 if isinstance(raw[0], float) and max(raw) <= 1.0 else 1
        self.series_colors[uid] = [int(c * scale) for c in raw] + [255]
        self._set_series_theme(serie_id, uid, self.series_colors[uid])

    def _attach_legend_menu(self, serie_id: str, uid: str, name: str) -> None:
        """Attach right-click context menu (select, Y axis, color) to an ImPlot legend item and apply its theme."""
        keys = list(self.raw_data)
        idx = keys.index(uid) if uid in keys else 0
        cur_color = self.series_colors.get(uid, self._IMPLOT_COLORS[idx % len(self._IMPLOT_COLORS)])
        self._set_series_theme(serie_id, uid, cur_color)

        dpg.add_selectable(
            label="Select", callback=lambda *_: self.select_serie(None, None, serie_id), parent=serie_id
        )
        dpg.add_separator(parent=serie_id)

        short_by_axis = dict(zip(_AXIS_OPTIONS, _AXIS_SHORT))
        axis_by_short = dict(zip(_AXIS_SHORT, _AXIS_OPTIONS))
        with dpg.group(horizontal=True, parent=serie_id):
            dpg.add_text("Y axis :")
            dpg.add_combo(
                items=_AXIS_SHORT,
                default_value=short_by_axis[_canon_axis(self.series_axis.get(uid))],
                callback=lambda s, a, u: self.set_series_axis(u, axis_by_short.get(a, AXIS_PRIMARY)),
                user_data=uid,
                width=110,
            )
        dpg.add_separator(parent=serie_id)

        dpg.add_color_edit(
            label="Color",
            default_value=cur_color,
            callback=self._on_color_change,
            user_data=(uid, serie_id),
            parent=serie_id,
            no_inputs=True,
            no_alpha=True,
        )

    def _refresh_popup_menu(self) -> None:
        """Populate the configuration popup with the annotation target series and per-series Y-axis assignment."""
        if not dpg.does_item_exist(self.popup_content_tag):
            return
        parent = self.popup_content_tag
        dpg.delete_item(parent, children_only=True)

        if not self.raw_data:
            dpg.add_text("No active series", parent=parent, color=(160, 160, 160))
            return

        dpg.add_text("Annotation Target Series:", parent=parent, color=(150, 200, 255))

        multi = len(self.raw_data) > 1
        series_map: Dict[str, str] = {}
        current_name = None
        for uid, info in self.raw_data.items():
            s_id = self.get_serie_tag(uid)
            s_name = info.get("name", "Serie")
            disp = f"{s_name} (#{str(uid)[:5]})" if multi else s_name
            series_map[disp] = s_id
            if self.selected_serie in (s_id, uid):
                current_name = disp

        if not current_name:
            current_name = list(series_map)[-1]
            self.selected_serie = series_map[current_name]

        def _on_annot_serie_change(sender: Any, app_data: Any, *args: Any, **kwargs: Any) -> None:
            if app_data in series_map:
                self.selected_serie = series_map[app_data]

        dpg.add_combo(
            items=list(series_map),
            default_value=current_name,
            callback=_on_annot_serie_change,
            parent=parent,
            width=-1,
        )

        dpg.add_separator(parent=parent)
        dpg.add_text("Assign Y-Axis per Series:", parent=parent, color=(220, 220, 220))
        dpg.add_separator(parent=parent)

        for uid, info in self.raw_data.items():
            with dpg.group(horizontal=True, parent=parent):
                name = info["name"]
                dpg.add_text(f"{name[:14]}{'...' if len(name) > 14 else ''}:", color=(200, 200, 200))
                dpg.add_combo(
                    items=_AXIS_OPTIONS,
                    default_value=_canon_axis(self.series_axis.get(uid)),
                    callback=lambda s, a, u: self.set_series_axis(u, a),
                    user_data=uid,
                    width=160,
                )

    # ------------------------------------------------------------------
    # Permissions & toggles
    # ------------------------------------------------------------------
    def update_permission(self) -> None:
        """Adjust module permissions and UI visibility based on application mode."""
        user_mode = app_state.mode == "user"
        for tag in (
            self.anot_check_tag,
            self.drag_line_check_tag,
            self.smooth_check_tag,
            self.smooth_algo_tag,
            self.smooth_win_tag,
        ):
            if dpg.does_item_exist(tag):
                (dpg.hide_item if user_mode else dpg.show_item)(tag)

        if dpg.does_item_exist(self.winID):
            dpg.configure_item(self.winID, no_close=user_mode)

    def toggle_autoscale(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Toggle automatic axis scaling."""
        self.autoscale = bool(app_data)
        if self.autoscale:
            self.autofit_axis()

    def toggle_smooth(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Toggle real-time smoothing filter."""
        self.smooth = bool(app_data)
        self.replot_all()

    def update_smooth_algo(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Update smoothing algorithm selection."""
        self.smooth_algo = str(app_data)
        self.replot_all()

    def update_smooth_win(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Update smoothing window length."""
        self.smooth_window = max(1, int(app_data))
        self.replot_all()

    def toggle_log_x(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Toggle logarithmic scaling for horizontal axis (axes must be rebuilt, then series redrawn)."""
        self.log_x = bool(app_data)

        dpg.delete_item(self.xaxis_tag)
        dpg.delete_item(self.yaxis_tag)
        self._delete_secondary_axes()
        self._create_base_axes(parent=self.plot_tag)

        for uid, info in self.raw_data.items():
            info["bound_axis"] = None
            self._draw_series(uid)
        if self.autoscale:
            self.autofit_axis()

    def toggle_drag_line(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Toggle vertical marker line visibility."""
        if not app_data and dpg.does_item_exist(self.drag_line_tag):
            dpg.delete_item(self.drag_line_tag)

    # ------------------------------------------------------------------
    # Export / send
    # ------------------------------------------------------------------
    def export_tsv_cb(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Export all plot data to clipboard in TSV format."""
        try:
            series = self._collect_series()
            if not series:
                return

            maps = [dict(zip(s["x"], s["y"])) for s in series]
            sorted_x = sorted({xi for s in series for xi in s["x"]})
            max_rows = max(len(sorted_x), max(len(s["x"]) for s in series))

            headers = ["X (Aligned)"] + [s["label"] for s in series]
            for s in series:
                headers += [f"{s['label']} (X)", f"{s['label']} (Y)"]
            lines = ["\t".join(headers)]

            blank = [""] * (1 + len(series))
            for r in range(max_rows):
                if r < len(sorted_x):
                    cx = sorted_x[r]
                    row = [str(cx)] + [str(m[cx]) if cx in m else "" for m in maps]
                else:
                    row = list(blank)
                for s in series:
                    row.append(str(s["x"][r]) if r < len(s["x"]) else "")
                    row.append(str(s["y"][r]) if r < len(s["y"]) else "")
                lines.append("\t".join(row))

            clipboardinjector.send_text("\n".join(lines))
            logger.info(f"Exported {len(series)} series to TSV")
        except Exception as e:
            logger.error(f"Error exporting TSV: {e}")

    def send_plot_cb(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """
        Emit the displayed series via the DataFrame output.

        The DataFrame is in long format with columns `serie` (label), `x`, `y` and `point` (0-based
        position of the measurement inside its series). Plot metadata (title, xlabel, ylabel, x_lim,
        y_lim, log_x) is stored in `df.attrs`.
        """
        try:
            all_series = self._collect_series()
            if not all_series:
                logger.warning("No data to send")
                return

            df = pd.concat(
                [
                    pd.DataFrame({"serie": s["label"], "x": s["x"], "y": s["y"], "point": np.arange(len(s["x"]))})
                    for s in all_series
                ],
                ignore_index=True,
            )
            df.attrs.update(
                colors={
                    info["name"]: "#%02x%02x%02x" % self._displayed_colors[uid]
                    for uid, info in self.raw_data.items()
                    if uid in self._displayed_colors
                },
                title=self.label,
                xlabel=dpg.get_item_label(self.xaxis_tag),
                ylabel=dpg.get_item_label(self.yaxis_tag),
                x_lim=dpg.get_axis_limits(self.xaxis_tag),
                y_lim=dpg.get_axis_limits(self.yaxis_tag),
                log_x=dpg.get_value(self.logX_check_tag),
            )
            self.trigger_cb(out_key="DataFrame", data=df)
            logger.info(f"Sent {len(all_series)} series to DataFrame output")
        except Exception as e:
            logger.error(f"Error sending plot: {e}")

    # ------------------------------------------------------------------
    # Smoothing & drawing
    # ------------------------------------------------------------------
    def apply_smoothing(self, y_data: Any) -> List[float]:
        """Apply active smoothing filter algorithm to 1D y_data array."""
        if not self.smooth or len(y_data) <= 1:
            return y_data if isinstance(y_data, list) else list(y_data)
        smoother = _SMOOTHERS.get(self.smooth_algo, _smooth_rolling_median)
        return smoother(np.asarray(y_data, dtype=float), max(1, int(self.smooth_window)))

    def _draw_series(self, uid: str) -> None:
        """Create or update the DPG line series for `uid` on its assigned Y axis."""
        info = self.raw_data[uid]
        name = info["name"]
        serie_id = self.get_serie_tag(uid)
        self._serie_tag_to_uid[serie_id] = uid
        plot_y = self.apply_smoothing(info["y"])
        info["plot_y"] = plot_y
        info["x_np"] = None
        info["y_np"] = None
        target = self.get_or_create_axis(self.series_axis.get(uid, AXIS_PRIMARY))

        if info.get("bound_axis") == target and dpg.does_item_exist(serie_id):
            dpg.configure_item(serie_id, x=info["x"], y=plot_y)
            if name and info.get("bound_name") != name:
                dpg.set_item_label(serie_id, name)
                info["bound_name"] = name
            return

        if dpg.does_item_exist(serie_id):
            dpg.delete_item(serie_id)  # axis changed: recreate under the new axis

        dpg.add_line_series(x=info["x"], y=plot_y, label=name, parent=target, tag=serie_id)
        info["bound_axis"] = target
        info["bound_name"] = name
        self._attach_legend_menu(serie_id, uid, name)

    def replot_all(self) -> None:
        """Recompute smoothed curves and update all series across their designated Y-axes."""
        for uid in list(self.raw_data):
            self._draw_series(uid)
        self.cleanup_unused_axes()
        if self.autoscale:
            self.autofit_axis()
        self._refresh_popup_menu()

    def autofit_axis(self, x: bool = True, y: bool = True) -> None:
        """Fit plot horizontal and vertical axes to active data bounds."""
        axes = ([self.xaxis_tag] if x else []) + (self.get_all_y_axes() if y else [])
        for tag in axes:
            if dpg.does_item_exist(tag):
                dpg.set_axis_limits_auto(tag)
                dpg.fit_axis_data(tag)

    # ------------------------------------------------------------------
    # Hover annotation
    # ------------------------------------------------------------------
    def find_closest_point(
        self,
        mouse_x: float,
        mouse_y: float,
        x_data: np.ndarray,
        y_data: np.ndarray,
    ) -> Optional[int]:
        """Find point index closest to given mouse coordinates (distance normalized by data range)."""
        if len(x_data) == 0 or len(y_data) == 0:
            return None
        ptp_x, ptp_y = np.ptp(x_data), np.ptp(y_data)
        dx = (x_data - mouse_x) / (ptp_x if ptp_x != 0 else 1.0)
        dy = (y_data - mouse_y) / (ptp_y if ptp_y != 0 else 1.0)
        return int(np.argmin(np.hypot(dx, dy)))

    def _axis_unit_fn(self, axis_tag: str, is_x: bool = False) -> Optional[Callable[[Any], np.ndarray]]:
        """Return a function mapping data values to [0, 1] fractions of the visible range of an axis."""
        lo, hi = dpg.get_axis_limits(axis_tag)
        log = is_x and self.log_x and lo > 0 and hi > 0
        if log:
            lo, hi = np.log10(lo), np.log10(hi)
        span = hi - lo
        if not np.isfinite(span) or span == 0:
            return None
        if log:
            return lambda v: (np.log10(np.clip(v, 1e-300, None)) - lo) / span
        return lambda v: (np.asarray(v, dtype=float) - lo) / span

    def _find_closest_across_series(
        self, series: List[Any], mouse_x: float, mouse_y: float
    ) -> Optional[Tuple[Any, int, np.ndarray, np.ndarray]]:
        """Find the point closest to the cursor (in screen proportion) over all series, per-axis aware."""
        try:
            p_min = dpg.get_item_rect_min(self.plot_tag)
            p_max = dpg.get_item_rect_max(self.plot_tag)
            w, h = max(1.0, p_max[0] - p_min[0]), max(1.0, p_max[1] - p_min[1])
        except Exception:
            w = h = 1.0

        fx = self._axis_unit_fn(self.xaxis_tag, is_x=True)
        fy_primary = self._axis_unit_fn(self.yaxis_tag)
        if fx is None or fy_primary is None:
            return None
        mx = float(fx(mouse_x))
        my = float(fy_primary(mouse_y))

        best = None
        best_d = np.inf
        for s in series:
            uid = self._serie_tag_to_uid.get(s) if isinstance(s, str) else None
            info = self.raw_data.get(uid) if uid is not None else None
            if info is not None:
                xs = info.get("x_np")
                ys = info.get("y_np")
                if xs is None or ys is None:
                    xs = np.asarray(info["x"], dtype=float)
                    ys = np.asarray(info.get("plot_y", info["y"]), dtype=float)
                    info["x_np"] = xs
                    info["y_np"] = ys
                parent = info.get("bound_axis") or self.yaxis_tag
            else:
                data = dpg.get_value(s)
                if not (data and len(data) >= 2 and data[0] is not None and data[1] is not None):
                    continue
                xs, ys = np.asarray(data[0], dtype=float), np.asarray(data[1], dtype=float)
                parent = dpg.get_item_parent(s)

            if xs.size == 0 or xs.size != ys.size:
                continue
            fy = fy_primary if parent == self.yaxis_tag or parent in (self.yaxis_tag, dpg.get_alias_id(self.yaxis_tag)) else (
                self._axis_unit_fn(parent) or fy_primary
            )
            d = np.hypot((fx(xs) - mx) * w, (fy(ys) - my) * h)
            d = np.where(np.isfinite(d), d, np.inf)
            j = int(np.argmin(d))
            if d[j] < best_d:
                best_d, best = d[j], (s, j, xs, ys)
        return best

    def select_serie(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Select active series for on-hover cursor annotation."""
        self.selected_serie = user_data

    def _hide_annotation(self, force: bool = False) -> None:
        """
        Hide the cursor annotation by moving it off-screen.

        The soft mode (default) never toggles DPG 'show' (it would set m_dirty on mvPlot and reset ImPlot legend
        states) and only acts if an annotation is active. `force=True` also sets show=False (used on right-click,
        legend hover and when annotation is disabled).
        """
        if not (force or self._annot_active):
            return
        if dpg.does_item_exist(self.closest_point_anot_tag):
            kw = {"show": False} if force else {}
            dpg.configure_item(
                self.closest_point_anot_tag, label="", default_value=_OFFSCREEN, clamped=False, **kw
            )
        self._annot_active = False

    def _on_mouse_right_click(self, sender: Any = None, app_data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Hide plot inspection tooltip on right-click to avoid blocking ImPlot legend context menus."""
        self._hide_annotation(force=True)

    def _is_mouse_over_legend(self) -> bool:
        """Detect if cursor is hovering near the top-right legend area."""
        if not dpg.does_item_exist(self.plot_tag):
            return False
        try:
            p_min = dpg.get_item_rect_min(self.plot_tag)
            p_max = dpg.get_item_rect_max(self.plot_tag)
            mx, my = dpg.get_mouse_pos()
            plot_w, plot_h = p_max[0] - p_min[0], p_max[1] - p_min[1]
            if plot_w <= 0 or plot_h <= 0:
                return False
            legend_w = min(320, plot_w * 0.45)
            legend_h = min(400, max(120, 35 * (len(self.raw_data) + 1) + 60))
            return (p_max[0] - legend_w <= mx <= p_max[0]) and (p_min[1] <= my <= p_min[1] + legend_h)
        except Exception:
            return False

    def plot_change_callback(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any) -> None:
        """Update on-hover point inspection tooltip when moving mouse across plot canvas."""
        if not self.raw_data or not dpg.does_item_exist(self.plot_tag) or not dpg.is_item_hovered(self.plot_tag):
            self._hide_annotation()
            return

        if (
            dpg.is_mouse_button_down(dpg.mvMouseButton_Right)
            or self._is_mouse_over_legend()
            or not dpg.get_value(self.anot_check_tag)
        ):
            self._hide_annotation(force=True)
            return

        target = self.selected_serie
        if not (target is not None and target in self._serie_tag_to_uid and dpg.does_item_exist(target)):
            all_series = self._line_series()
            if not all_series:
                self._hide_annotation()
                return
            target = self.selected_serie = all_series[-1]

        mouse_x, mouse_y = dpg.get_plot_mouse_pos()  # coordinates relative to the primary axes
        best = self._find_closest_across_series([target], mouse_x, mouse_y)
        if best is None:
            self._hide_annotation()
            return
        target, i, xplot, yplot = best

        uid = self._serie_tag_to_uid.get(target)
        info = self.raw_data.get(uid) if uid is not None else None
        serie_label = info["name"] if info is not None else dpg.get_item_label(target)
        annot = f"[{serie_label}]\nIndex : {i}\nX : {xplot[i]:.4g}\nY : {yplot[i]:.4g}"
        # The annotation lives on the primary axes: project secondary-axis values onto Y1.
        ay = float(yplot[i])
        parent = (info.get("bound_axis") if info is not None else None) or dpg.get_item_parent(target)
        if parent != self.yaxis_tag and parent not in (self.yaxis_tag, dpg.get_alias_id(self.yaxis_tag)):
            f_src, lo1_hi1 = self._axis_unit_fn(parent), dpg.get_axis_limits(self.yaxis_tag)
            if f_src is not None:
                ay = lo1_hi1[0] + float(f_src(ay)) * (lo1_hi1[1] - lo1_hi1[0])
        kw = dict(label=annot, default_value=(float(xplot[i]), ay), clamped=True, show=True)
        if dpg.does_item_exist(self.closest_point_anot_tag):
            dpg.configure_item(self.closest_point_anot_tag, **kw)
        else:
            dpg.add_plot_annotation(
                tag=self.closest_point_anot_tag,
                offset=(25, -25),
                color=[255, 255, 0, 255],
                parent=self.plot_tag,
                **kw,
            )
        self._annot_active = True

    # ------------------------------------------------------------------
    # Data management
    # ------------------------------------------------------------------
    def clear_plot(self, sender: Any = None, app_data: Any = None, user_data: Any = None, *args: Any, **kwargs: Any) -> None:
        """Remove all line series across all Y-axes, clear dynamic secondary axes, and reset selection state."""
        for child in self._line_series():
            dpg.delete_item(child)

        self._delete_secondary_axes()
        self.series_axis.clear()

        for uid in self.series_colors:
            theme_tag = f"lineplot_scolor_{self.UUID}_{uid}"
            if dpg.does_item_exist(theme_tag):
                dpg.delete_item(theme_tag)
        self.series_colors.clear()

        if dpg.does_item_exist(self.closest_point_anot_tag):
            dpg.configure_item(self.closest_point_anot_tag, show=False)
        self._annot_active = False

        self.raw_data.clear()
        self._serie_tag_to_uid.clear()
        self.selected_serie = None
        self._refresh_popup_menu()

    def plot_data(self, x: Any = None, y: Any = None, name: str = "Serie", UUID: Any = None) -> None:
        """
        Add or update a continuous 2D data series on the plot.

        Args:
            x: Array of X coordinates.
            y: Array of Y values.
            name: Display label for the series.
            UUID: Optional unique identifier.
        """
        if y is None:
            return
        y_list = y if isinstance(y, list) else list(y)
        x_list = list(range(len(y_list))) if x is None else (x if isinstance(x, list) else list(x))
        uid = str(UUID) if UUID is not None else name
        serie_id = self.get_serie_tag(uid)
        prev_info = self.raw_data.get(uid)
        is_new_serie = prev_info is None or not dpg.does_item_exist(serie_id)

        self.raw_data[uid] = {
            "x": x_list,
            "y": y_list,
            "name": name,
            "uuid": UUID,
            "bound_axis": prev_info.get("bound_axis") if prev_info else None,
            "bound_name": prev_info.get("bound_name") if prev_info else None,
        }
        self._draw_series(uid)

        if self.autoscale:
            self.autofit_axis()
        if is_new_serie or self.selected_serie is None:
            self.select_serie(None, None, serie_id)
        if is_new_serie and dpg.does_item_exist(self.plot_popup_tag) and dpg.is_item_shown(self.plot_popup_tag):
            self._refresh_popup_menu()

    def _remove_series(self, uid: str) -> None:
        """Remove a series (data and DPG item) and drop unused secondary axes."""
        self.raw_data.pop(uid, None)
        serie_id = self.get_serie_tag(uid)
        self._serie_tag_to_uid.pop(serie_id, None)
        if dpg.does_item_exist(serie_id):
            dpg.delete_item(serie_id)
        self.cleanup_unused_axes()

    def _rename_series(self, uid: str, new_name: Optional[str]) -> None:
        """Rename a series both in raw data and in the legend."""
        if new_name is None:
            return
        if uid in self.raw_data:
            self.raw_data[uid]["name"] = new_name
            self.raw_data[uid]["bound_name"] = new_name
        serie_id = self.get_serie_tag(uid)
        if dpg.does_item_exist(serie_id):
            dpg.set_item_label(serie_id, new_name)

    def is_ready(self) -> bool:
        """
        Check if the module is ready to receive input data.

        Returns:
            bool: Always True.
        """
        return True

    def _set_drag_line(self, position: Optional[float]) -> None:
        """Create/move the vertical marker line, or delete it when position is None."""
        exists = dpg.does_item_exist(self.drag_line_tag)
        if position is None:
            if exists:
                dpg.delete_item(self.drag_line_tag)
        elif exists:
            dpg.set_value(self.drag_line_tag, float(position))
        else:
            dpg.add_drag_line(
                default_value=float(position),
                tag=self.drag_line_tag,
                parent=self.plot_tag,
                color=[255, 0, 0, 255],
                thickness=2,
            )

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Handle incoming samples or command dictionaries from upstream modules.

        Supported inputs:
            - SAMPLE / ROI_SAMPLE: Dict with keys 'x', 'y', 'name', 'uuid', 'action'.
            - CMD_DICT: Commands ('position', 'add serie', 'remove serie', 'update serie name', 'clear').
            - DATALIST: Numerical arrays (x, y).
        """
        sample = kwargs.get("sample")
        if sample:
            action = sample.get("action", "select")
            uid = sample.get("uuid")
            uid = str(uid if uid is not None else sample.get("name", "Sample"))

            if action in ("unselect", "delete"):
                self._remove_series(uid)
            elif action == "clear" or sample.get("clear"):
                self.clear_plot()
            elif action == "rename":
                self._rename_series(uid, sample.get("name", "Sample"))
            else:
                self.plot_data(
                    x=sample.get("x"), y=sample.get("y"), name=sample.get("name", "Sample"), UUID=uid
                )
            return

        if kwargs.get("data_type") == IOTypes.CMD_DICT:
            cmd = kwargs.get("cmd") if kwargs.get("cmd") is not None else kwargs.get("data")
            if not isinstance(cmd, dict):
                return
            action = cmd.get("action")
            data = cmd.get("data", {})

            if action == "position":
                if dpg.does_item_exist(self.drag_line_check_tag) and not dpg.get_value(self.drag_line_check_tag):
                    return
                position = cmd.get("value")
                self._set_drag_line(None if position is None else float(position))
                return

            uuid = data.get("uuid")
            uid = str(uuid if uuid is not None else data.get("name", "Serie"))
            if action == "add serie":
                self.plot_data(x=data.get("x"), y=data.get("y"), name=data.get("name"), UUID=uuid)
            elif action == "remove serie":
                self._remove_series(uid)
            elif action == "update serie name":
                self._rename_series(uid, data.get("name"))

            if action == "clear" or cmd.get("clear"):
                self.clear_plot()
            return

        y = kwargs.get("y") if kwargs.get("y") is not None else (args[0] if args and isinstance(args[0], list) else None)
        x = kwargs.get("x") if kwargs.get("x") is not None else (args[1] if len(args) > 1 and isinstance(args[1], list) else None)
        self.plot_data(x=x, y=y, name=kwargs.get("name", "Serie"), UUID=kwargs.get("uuid"))

    def trigger_cb(self, out_key: Optional[str] = None, data: Any = None) -> None:
        """
        Emit data payload to all connected downstream modules.

        Args:
            out_key: Output port name ("Datalist" or "CMD").
            data: Payload object to emit.
        """
        for module in self.connections.get(out_key, []):
            try:
                module.input_cb(data=data, data_type=self.outputs[out_key])
            except Exception as e:
                logger.error(f"Error triggering output {out_key}: {e}")

    def on_close(self) -> None:
        """Clean up global mouse handlers and series themes before window closure."""
        if getattr(self, "_is_closing", False):
            return
        self._is_closing = True
        if hasattr(self, "handler_tag") and dpg.does_item_exist(self.handler_tag):
            dpg.delete_item(self.handler_tag)
        for uid in set(self.raw_data) | set(self.series_colors) | set(self._displayed_colors):
            theme_tag = f"lineplot_scolor_{self.UUID}_{uid}"
            if dpg.does_item_exist(theme_tag):
                dpg.delete_item(theme_tag)
        super().on_close()

    def close(self) -> None:
        """Clean up resources and close window."""
        self.on_close()
        super().close()


EXPORTED_CLASS = Lineplot_win
EXPORTED_NAME = "Lineplot"
