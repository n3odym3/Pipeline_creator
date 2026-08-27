"""
Parallel Viewer Module - Displays data in a separate OS window using multiprocessing.
Uses ViewportProcess class for standardized viewport management.
"""
import dearpygui.dearpygui as dpg
from core.window_base import WindowBase
from core.input_output_types import IOTypes
from loguru import logger
import threading
import time

class Parallel_viewer_win(WindowBase):
    """
    Parallel Viewer - Displays data in a separate OS window.
    Uses multiprocessing to create an independent viewport.
    """
    def __init__(self,
                label="Parallel Viewer",
                win_width=400,
                win_height=200,
                pos=(50, 50),
                uuid=None,
                outputs=None,
                visible=True):
        
        super().__init__(label=label, pos=pos, win_width=win_width, win_height=win_height,
                        uuid=uuid, outputs=outputs or [], visible=visible)
        
        # Multiprocessing components
        self.process = None
        self.input_queue = None
        self.output_queue = None  # NEW: Output queue for receiving events
        self.shutdown_event = None
        self.process_running = False
        
        # IO
        self.accepted_input_types = [IOTypes.TEXT, IOTypes.DATALIST, IOTypes.CMD_DICT]
        self.outputs = {
            "Out": IOTypes.TEXT
        }
        self.connections = {k: [] for k in self.outputs}
        
        # Monitoring thread (like binarize pattern)
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread_running = False
        
        self._build_interface()
        self.start_parallel_viewport()
    
    def _build_interface(self):
        """Build the control window in main process."""
        with dpg.window(label=self.label, width=self.win_width, height=self.win_height,
                        pos=self.pos, tag=self.winID, show=self.visible):
            
            dpg.add_text("Parallel Viewer Control", color=(100, 200, 255))
            dpg.add_separator()
            
            # Status
            self.status_tag = f"status_{self.UUID}"
            dpg.add_text("Status: Not Started", tag=self.status_tag, color=(255, 200, 0))
            
            # PID display
            self.pid_tag = f"pid_{self.UUID}"
            dpg.add_text("PID: -", tag=self.pid_tag)
            
            # Event log (NEW)
            self.event_log_tag = f"event_log_{self.UUID}"
            dpg.add_text("Last Event: None", tag=self.event_log_tag, wrap=380)
            
            dpg.add_separator()
            
            # Controls
            dpg.add_button(label="Start Parallel Viewport", callback=self.start_parallel_viewport, width=-1)
            dpg.add_button(label="Stop Parallel Viewport", callback=self.stop_parallel_viewport, width=-1)
    
    def start_parallel_viewport(self, *args, **kwargs):
        """Start the parallel viewport process."""
        if self.process_running:
            logger.warning("Parallel viewport already running")
            return
        
        # Create ViewportProcess instance
        from .text_viewer_process import ViewportProcess
        
        self.viewport = ViewportProcess(
            title=f"{self.label}",
            width=800,
            height=600,
            pos=(200, 200),
            input_buffer_size=100,
            output_buffer_size=100,
            daemon=False
        )
        
        # Start the viewport process (uses ViewportProcessBase.start())
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
        
        logger.info(f"Started parallel viewport process (PID: {self.viewport.pid})")
    
    def _monitor_loop(self):
        while self._monitor_thread_running:
            if self.viewport and self.process_running:
                event = self.viewport.get_output(block=True, timeout=0.1)
                if event is not None:
                    self._handle_viewport_event(event)

            time.sleep(0.1)
    
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
        
        # Handle special events
        if event_type == "viewport_closed_by_user":
            logger.info("Viewport closed by user, stopping process...")
            self.stop_parallel_viewport()
            return
        
        # Distribute to connected modules based on event type (like binarize pattern)
        if event_type == "output":
            # Send echoed data to "echo" output
            output_data = event.get("data")
            for module in self.connections.get("Out", []):
                if hasattr(module, "input_cb"):
                    try:
                        module.input_cb(data=output_data, data_type=IOTypes.TEXT)
                    except Exception as e:
                        logger.error(f"Error forwarding output to {module}: {e}")
        
        # Always send all events to "events" output
        for module in self.connections.get("events", []):
            if hasattr(module, "input_cb"):
                try:
                    module.input_cb(data=event, data_type=IOTypes.CMD_DICT)
                except Exception as e:
                    logger.error(f"Error forwarding event to {module}: {e}")
    
    def stop_parallel_viewport(self, *args, **kwargs):
        """Stop the parallel viewport process."""
        if not self.process_running:
            logger.warning("Parallel viewport not running")
            return
        
        # Stop monitoring thread first (like binarize pattern)
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
        
        logger.info("Stopped parallel viewport process")
    
    def input_cb(self, *args, **kwargs):
        """Receive data and send to parallel viewport."""
        if not self.process_running or not self.input_queue:
            logger.debug("Parallel viewport not running, ignoring input")
            return
        
        # Extract data
        data = None
        if 'data' in kwargs:
            data = kwargs['data']
        elif args:
            data = args[0]
        
        if data is not None:
            try:
                self.input_queue.put_nowait(data)
            except Exception:
                logger.warning("Queue full, dropping data")

    def is_ready(self):
        """Check if viewport is ready to receive data (like binarize pattern)."""
        return self.viewport.is_ready() if hasattr(self, 'viewport') and self.viewport else False
    
    def close(self):
        """Cleanup when node is deleted (like binarize pattern)."""
        self._monitor_thread_running = False
        # Only join if we're not in the monitoring thread
        if self._monitor_thread.is_alive() and threading.current_thread() != self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
        self.stop_parallel_viewport()
        super().close()


EXPORTED_CLASS = Parallel_viewer_win
EXPORTED_NAME = "Parallel Viewer"
