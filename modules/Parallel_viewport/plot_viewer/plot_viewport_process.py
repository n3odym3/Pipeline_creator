"""
Plot Viewport Process - DearPyGUI plotting in parallel viewport.
Combines lineplot_win logic with ViewportProcessBase architecture.
"""
import dearpygui.dearpygui as dpg
from loguru import logger
import ctypes
import numpy as np
from core.viewport_process_base import ViewportProcessBase
from typing import Any, Dict, Optional


class PlotViewportProcess(ViewportProcessBase):
    """
    Viewport process for plotting data with DearPyGUI.
    Handles multiple series, autoscale, smoothing, and mouse interactions.
    """
    
    def __init__(self,
                 title: str = "Parallel Plot",
                 width: int = 800,
                 height: int = 600,
                 pos: tuple = (200, 200),
                 autoscale: bool = True,
                 smooth: bool = False,
                 smooth_window: int = 5,
                 **kwargs):
        """
        Initialize the plot viewport process.
        
        Args:
            title: Window title.
            width: Viewport width.
            height: Viewport height.
            pos: Initial position (x, y).
            autoscale: Enable autoscaling.
            smooth: Enable smoothing.
            smooth_window: Smoothing window size.
            **kwargs: Additional parameters passed to ViewportProcessBase.
        """
        self.title = title
        self.width = width
        self.height = height
        self.pos = pos
        
        # Plot settings
        self.autoscale = autoscale
        self.smooth = smooth
        self.smooth_window = smooth_window
        
        # DPG tags (will be set in subprocess)
        self.winID = None
        self.plot_tag = None
        self.xaxis_tag = None
        self.yaxis_tag = None
        self.dragline_tag = None
        
        # Series storage
        self.series = {}  # uuid -> series_tag
        
        super().__init__(**kwargs)
    
    def _setup_viewport(self) -> None:
        """Setup DearPyGUI context and plot viewport."""
        # Set DPI awareness on Windows
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception as e:
            logger.debug(f"Could not set DPI awareness: {e}")
        
        # Create DPG context
        dpg.create_context()
        
        # Apply theme if available
        try:
            from config.theme_manager import theme_manager
            dpg.bind_theme(theme_manager.global_theme)
        except Exception as e:
            logger.debug(f"Theme not available: {e}")
        
        # Create viewport
        dpg.create_viewport(
            title=self.title,
            width=self.width,
            height=self.height,
            x_pos=self.pos[0],
            y_pos=self.pos[1]
        )
        dpg.setup_dearpygui()
        
        # Create main window with plot
        self.winID = f"{self.title}_main"
        with dpg.window(tag=self.winID):
            # Add controls at top
            with dpg.group(horizontal=True):
                self.autoscale_check_tag = f"{self.title}_autoscale"
                dpg.add_checkbox(
                    label="Autoscale",
                    tag=self.autoscale_check_tag,
                    default_value=self.autoscale,
                    callback=self._toggle_autoscale
                )
                
                self.smooth_check_tag = f"{self.title}_smooth"
                dpg.add_checkbox(
                    label="Smooth",
                    tag=self.smooth_check_tag,
                    default_value=self.smooth,
                    callback=self._toggle_smooth
                )
                
                self.smooth_win_tag = f"{self.title}_smooth_win"
                dpg.add_slider_int(
                    label="Window",
                    tag=self.smooth_win_tag,
                    default_value=self.smooth_window,
                    min_value=1,
                    max_value=50,
                    width=150,
                    callback=self._update_smooth_window
                )
                
                dpg.add_button(label="Clear Plot", callback=self._clear_plot_ui)
            
            dpg.add_separator()
            
            # Plot
            self.plot_tag = f"{self.title}_plot"
            with dpg.plot(label="Line Plot", height=-1, width=-1, tag=self.plot_tag):
                dpg.add_plot_legend()
                
                self.xaxis_tag = f"{self.title}_xaxis"
                self.yaxis_tag = f"{self.title}_yaxis"
                dpg.add_plot_axis(dpg.mvXAxis, label="X", tag=self.xaxis_tag)
                dpg.add_plot_axis(dpg.mvYAxis, label="Y", tag=self.yaxis_tag)
                
                self.dragline_tag = f"{self.title}_dragline"
                dpg.add_drag_line(
                    label="dragline",
                    tag=self.dragline_tag,
                    color=[255, 0, 0, 255],
                    callback=self._dragline_callback
                )
            
            dpg.configure_item(self.plot_tag, anti_aliased=True)
            dpg.configure_item(self.plot_tag, crosshairs=True)
        
        dpg.set_primary_window(self.winID, True)
        dpg.show_viewport()
        
        logger.debug(f"Plot viewport '{self.title}' setup complete")
    
    def _run_event_loop(self) -> None:
        """Main event loop for the viewport."""
        while dpg.is_dearpygui_running() and not self._shutdown_event.is_set():
            # Process control commands
            if not self._process_control_commands():
                break
            
            # Process input data
            data = self._process_input_data()
            if data is not None:
                self._handle_input_data(data)
            
            # Render frame
            dpg.render_dearpygui_frame()
        
        # Check if viewport was closed by user
        if not self._shutdown_event.is_set():
            logger.info("Plot viewport closed by user")
            self._send_event({"type": "viewport_closed_by_user"})
        
        logger.debug("Event loop ended")
    
    def _handle_input_data(self, data: Any) -> None:
        """
        Handle incoming commands from input queue.
        
        Args:
            data: Command dict or data to plot.
        """
        if not isinstance(data, dict):
            return
        
        cmd = data.get("cmd")
        
        try:
            if cmd == "add_serie":
                self._add_serie(data)
            elif cmd == "remove_serie":
                self._remove_serie(data)
            elif cmd == "update_serie_name":
                self._update_serie_name(data)
            elif cmd == "clear_plot":
                self._clear_plot()
            elif cmd == "set_autoscale":
                self.autoscale = data.get("value", True)
            elif cmd == "set_smooth":
                self.smooth = data.get("value", False)
                self.smooth_window = data.get("window", self.smooth_window)
            else:
                logger.warning(f"Unknown command: {cmd}")
        except Exception as e:
            logger.error(f"Error handling command {cmd}: {e}")
    
    def _add_serie(self, data: Dict) -> None:
        """Add or update a series in the plot."""
        x = data.get("x")
        y = data.get("y")
        name = data.get("name", "Serie")
        uuid = data.get("uuid", name)
        
        if y is None:
            return
        
        if x is None:
            x = list(range(len(y)))
        
        # Apply smoothing if enabled
        if self.smooth and len(y) > self.smooth_window:
            y = self._rolling_average(np.array(y), self.smooth_window).tolist()
        
        serie_tag = f"serie_{uuid}"
        
        if dpg.does_item_exist(serie_tag):
            # Update existing series
            dpg.configure_item(serie_tag, x=x, y=y)
        else:
            # Add new series
            dpg.add_line_series(
                x=x, y=y,
                label=name,
                parent=self.yaxis_tag,
                tag=serie_tag
            )
            self.series[uuid] = serie_tag
        
        # Autoscale if enabled
        if self.autoscale:
            dpg.fit_axis_data(self.xaxis_tag)
            dpg.fit_axis_data(self.yaxis_tag)
        
        # Send confirmation event
        self._send_event({
            "type": "serie_updated",
            "uuid": uuid,
            "name": name
        })
    
    def _remove_serie(self, data: Dict) -> None:
        """Remove a series from the plot."""
        uuid = data.get("uuid")
        if uuid and uuid in self.series:
            serie_tag = self.series[uuid]
            if dpg.does_item_exist(serie_tag):
                dpg.delete_item(serie_tag)
            del self.series[uuid]
            
            self._send_event({
                "type": "serie_removed",
                "uuid": uuid
            })
    
    def _update_serie_name(self, data: Dict) -> None:
        """Update series name."""
        uuid = data.get("uuid")
        new_name = data.get("name")
        
        if uuid and new_name and uuid in self.series:
            serie_tag = self.series[uuid]
            if dpg.does_item_exist(serie_tag):
                dpg.set_item_label(serie_tag, new_name)
    
    def _clear_plot(self) -> None:
        """Clear all series from the plot."""
        for uuid, serie_tag in list(self.series.items()):
            if dpg.does_item_exist(serie_tag):
                dpg.delete_item(serie_tag)
        self.series.clear()
        
        self._send_event({"type": "plot_cleared"})
    
    def _rolling_average(self, data: np.ndarray, window_size: int) -> np.ndarray:
        """Apply rolling average smoothing."""
        if window_size < 2 or len(data) < window_size:
            return data
        
        kernel = np.ones(window_size) / window_size
        pad_len = window_size - 1
        pad_left = pad_len // 2
        pad_right = pad_len - pad_left
        
        padded_data = np.pad(data, (pad_left, pad_right), mode='edge')
        return np.convolve(padded_data, kernel, mode='valid')
    
    def _toggle_autoscale(self, sender, app_data, user_data=None, *args):
        """Toggle autoscale setting."""
        self.autoscale = app_data
    
    def _toggle_smooth(self, sender, app_data, user_data=None, *args):
        """Toggle smooth setting."""
        self.smooth = app_data
    
    def _update_smooth_window(self, sender, app_data, user_data=None, *args):
        """Update smooth window size."""
        self.smooth_window = app_data
    
    def _clear_plot_ui(self, *args, **kwargs):
        """Clear plot from UI button."""
        self._clear_plot()
    
    def _dragline_callback(self, sender, app_data, user_data=None, *args):
        """Callback when dragline is moved."""
        position = dpg.get_value(self.dragline_tag)
        self._send_event({
            "type": "dragline_moved",
            "position": position
        })
    
    def _cleanup_viewport(self) -> None:
        """Cleanup DearPyGUI resources."""
        try:
            if dpg.does_item_exist(self.winID):
                dpg.delete_item(self.winID)
            dpg.destroy_context()
            logger.debug("Plot viewport cleanup complete")
        except Exception as e:
            logger.error(f"Error during viewport cleanup: {e}")
