"""
Parallel Plot Module - Plot viewer in a separate OS window using multiprocessing.
Combines lineplot logic with parallel viewport architecture.
"""
import dearpygui.dearpygui as dpg
from core.window_base import WindowBase
from core.input_output_types import IOTypes
from loguru import logger
import threading
import time


class Parallel_plot_win(WindowBase):
    """
    Parallel Plot - Displays plots in a separate OS window.
    Uses multiprocessing with PlotViewportProcess for independent plotting.
    """
    
    def __init__(self,
                label="Parallel Plot",
                win_width=350,
                win_height=250,
                pos=(50, 50),
                uuid=None,
                outputs=None,
                visible=True,
                autoscale=True,
                smooth=False,
                smooth_window=5):
        
        super().__init__(label=label, pos=pos, win_width=win_width, win_height=win_height,
                        uuid=uuid, outputs=outputs or [], visible=visible)
        
        # Plot settings
        self.autoscale = autoscale
        self.smooth = smooth
        self.smooth_window = smooth_window
        
        # Multiprocessing components
        self.process = None
        self.input_queue = None
        self.output_queue = None
        self.shutdown_event = None
        self.process_running = False
        
        # IO
        self.accepted_input_types = [IOTypes.DATALIST, IOTypes.CMD_DICT]
        self.outputs = {
            "Dragline": IOTypes.POSITION,
            "Events": IOTypes.CMD_DICT,
        }
        self.connections = {k: [] for k in self.outputs}
        
        # Monitoring thread (like binarize pattern)
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread_running = False
        
        # Persistent fields
        self._persistent_fields = ["label", "autoscale", "smooth", "smooth_window"]
        
        self._build_interface()
        self.start_parallel_viewport()
    
    def _build_interface(self):
        """Build the control window in main process."""
        with dpg.window(label=self.label, width=self.win_width, height=self.win_height,
                        pos=self.pos, tag=self.winID, show=self.visible):
            
            dpg.add_text("Parallel Plot Control", color=(100, 200, 255))
            dpg.add_separator()
            
            # Status
            self.status_tag = f"status_{self.UUID}"
            dpg.add_text("Status: Not Started", tag=self.status_tag, color=(255, 200, 0))
            
            # PID display
            self.pid_tag = f"pid_{self.UUID}"
            dpg.add_text("PID: -", tag=self.pid_tag)
            
            # Event log
            self.event_log_tag = f"event_log_{self.UUID}"
            dpg.add_text("Last Event: None", tag=self.event_log_tag, wrap=330)
            
            dpg.add_separator()
            dpg.add_text("All plot settings are in the parallel viewport window", wrap=330, color=(150, 150, 150))
            dpg.add_separator()
            
            # Controls
            dpg.add_button(label="Start Parallel Plot", callback=self.start_parallel_viewport, width=-1)
            dpg.add_button(label="Stop Parallel Plot", callback=self.stop_parallel_viewport, width=-1)
            dpg.add_button(label="Clear Plot", callback=self._clear_plot_callback, width=-1)
    
    def _clear_plot_callback(self, *args, **kwargs):
        """Send clear plot command to viewport."""
        if self.process_running and self.input_queue:
            try:
                self.input_queue.put_nowait({"cmd": "clear_plot"})
            except Exception:
                logger.warning("Queue full, cannot clear plot")
    
    def start_parallel_viewport(self, *args, **kwargs):
        """Start the parallel plot viewport process."""
        if self.process_running:
            logger.warning("Parallel plot already running")
            return
        
        # Create PlotViewportProcess instance
        from .plot_viewport_process import PlotViewportProcess
        
        self.viewport = PlotViewportProcess(
            title=f"{self.label}",
            width=900,
            height=700,
            pos=(250, 150),
            autoscale=self.autoscale,
            smooth=self.smooth,
            smooth_window=self.smooth_window,
            input_buffer_size=100,
            output_buffer_size=100,
            daemon=False
        )
        
        # Start the viewport process
        self.viewport.start()
        self.process_running = True
        
        # Keep references to queues for compatibility
        self.input_queue = self.viewport._in_queue
        self.output_queue = self.viewport._out_queue
        self.shutdown_event = self.viewport._shutdown_event
        self.process = self.viewport._process
        
        # Update UI
        dpg.set_value(self.status_tag, "Status: Running")
        dpg.configure_item(self.status_tag, color=(0, 255, 0))
        dpg.set_value(self.pid_tag, f"PID: {self.viewport.pid}")
        
        # Start monitoring thread
        self._monitor_thread_running = True
        if not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()
        
        logger.info(f"Started parallel plot process (PID: {self.viewport.pid})")
    
    def _monitor_loop(self):
        """Monitor loop to poll viewport output queue (like binarize pattern)."""
        while self._monitor_thread_running:
            if self.viewport and self.process_running:
                # Block with timeout to avoid busy-wait
                event = self.viewport.get_output(block=True, timeout=0.1)
                if event is not None:
                    self._handle_viewport_event(event)
            time.sleep(0.01)  # Small sleep to reduce CPU usage
    
    def _handle_viewport_event(self, event):
        """
        Handle an event received from the viewport process.
        
        Args:
            event: Event data (typically a dict with 'type' key).
        """
        if not isinstance(event, dict):
            return
        
        event_type = event.get("type", "unknown")
        
        # Update UI with last event
        if dpg.does_item_exist(self.event_log_tag):
            dpg.set_value(self.event_log_tag, f"Last Event: {event_type}")
        
        logger.debug(f"Plot viewport event: {event}")
        
        # Handle special events
        if event_type == "viewport_closed_by_user":
            logger.info("Plot viewport closed by user, stopping process...")
            self.stop_parallel_viewport()
            return
        
        # Distribute to connected modules based on event type
        if event_type == "dragline_moved":
            position = event.get("position")
            for module in self.connections.get("Dragline", []):
                if hasattr(module, "input_cb"):
                    try:
                        module.input_cb(data=position, data_type=IOTypes.POSITION)
                    except Exception as e:
                        logger.error(f"Error forwarding dragline to {module}: {e}")
        
        # Always send all events to "Events" output
        for module in self.connections.get("Events", []):
            if hasattr(module, "input_cb"):
                try:
                    module.input_cb(data=event, data_type=IOTypes.CMD_DICT)
                except Exception as e:
                    logger.error(f"Error forwarding event to {module}: {e}")
    
    def stop_parallel_viewport(self, *args, **kwargs):
        """Stop the parallel plot viewport process."""
        if not self.process_running:
            logger.warning("Parallel plot not running")
            return
        
        # Stop monitoring thread first
        self._monitor_thread_running = False
        # Only join if we're not in the monitoring thread (avoid "cannot join current thread")
        if self._monitor_thread.is_alive() and threading.current_thread() != self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
        
        # Use ViewportProcess.stop() method
        if hasattr(self, 'viewport') and self.viewport:
            self.viewport.stop(timeout=2.0)
        
        self.process_running = False
        
        # Update UI
        dpg.set_value(self.status_tag, "Status: Stopped")
        dpg.configure_item(self.status_tag, color=(255, 100, 100))
        dpg.set_value(self.pid_tag, "PID: -")
        dpg.set_value(self.event_log_tag, "Last Event: None")
        
        logger.info("Stopped parallel plot process")
    
    def input_cb(self, *args, **kwargs):
        """Receive data and send to parallel plot viewport (matches lineplot_win pattern)."""
        if not self.process_running or not self.input_queue:
            logger.debug("Parallel plot not running, ignoring input")
            return
        
        # Handle CMD_DICT commands (like lineplot_win)
        if kwargs.get("data_type") == IOTypes.CMD_DICT:
            cmd = kwargs.get("cmd")
            
            if cmd and cmd.get("action") == "add serie":
                data = cmd.get("data", {})
                plot_cmd = {
                    "cmd": "add_serie",
                    "x": data.get("x", None),
                    "y": data.get("y", None),
                    "name": data.get("name", "Serie"),
                    "uuid": data.get("uuid", None)
                }
                try:
                    self.input_queue.put_nowait(plot_cmd)
                except Exception:
                    logger.warning("Queue full, dropping data")
            
            elif cmd and cmd.get("action") == "remove serie":
                data = cmd.get("data", {})
                try:
                    self.input_queue.put_nowait({"cmd": "remove_serie", "uuid": data.get("uuid")})
                except Exception:
                    pass
            
            elif cmd and cmd.get("action") == "update serie name":
                data = cmd.get("data", {})
                try:
                    self.input_queue.put_nowait({
                        "cmd": "update_serie_name",
                        "uuid": data.get("uuid"),
                        "name": data.get("name")
                    })
                except Exception:
                    pass
            return
        
        # Handle direct data input (like lineplot_win)
        y = kwargs.get("y") if kwargs.get("y") is not None else (args[0] if args and isinstance(args[0], list) else None)
        x = kwargs.get("x") if kwargs.get("x") is not None else (args[1] if len(args) > 1 and isinstance(args[1], list) else None)
        name = kwargs.get("name", "Serie")
        uuid = kwargs.get("uuid", None)
        
        if y is not None:
            cmd = {
                "cmd": "add_serie",
                "x": x,
                "y": y,
                "name": name,
                "uuid": uuid
            }
            try:
                self.input_queue.put_nowait(cmd)
            except Exception:
                logger.warning("Queue full, dropping data")
    
    def is_ready(self):
        """Check if viewport is ready to receive data."""
        return self.viewport.is_ready() if hasattr(self, 'viewport') and self.viewport else False
    
    def close(self):
        """Cleanup when node is deleted."""
        self._monitor_thread_running = False
        # Only join if we're not in the monitoring thread
        if self._monitor_thread.is_alive() and threading.current_thread() != self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
        self.stop_parallel_viewport()
        super().close()


EXPORTED_CLASS = Parallel_plot_win
EXPORTED_NAME = "Parallel Plot"
