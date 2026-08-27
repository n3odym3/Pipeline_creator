"""
ViewportProcess class for parallel viewport - uses ViewportProcessBase for standardized architecture.
Manages a DearPyGUI viewport in a separate process with bidirectional queue communication.
"""
import dearpygui.dearpygui as dpg
from loguru import logger
import ctypes
from core.viewport_process_base import ViewportProcessBase
from typing import Any, Dict, Optional


class ViewportProcess(ViewportProcessBase):
    """
    Viewport process that creates and manages a DearPyGUI viewport.
    Receives data via input queue and sends events via output queue.
    """
    
    def __init__(self,
                 title: str = "Parallel Viewport",
                 width: int = 800,
                 height: int = 600,
                 pos: tuple = (200, 200),
                 **kwargs):
        """
        Initialize the viewport process.
        
        Args:
            title: Window title.
            width: Viewport width.
            height: Viewport height.
            pos: Initial position (x, y).
            **kwargs: Additional parameters passed to ViewportProcessBase.
        """
        # Viewport configuration
        self.title = title
        self.width = width
        self.height = height
        self.pos = pos
        
        # DPG-specific state (will be set in subprocess)
        self.winID = None
        self.text_tag = None
        
        super().__init__(**kwargs)
    
    def _setup_viewport(self) -> None:
        """Setup DearPyGUI context and viewport."""
        self.winID = f"{self.title} (Parallel)"
        
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

        self._build_interface()
        
        dpg.set_primary_window(self.winID, True)
        dpg.show_viewport()
        
        logger.debug(f"Viewport '{self.title}' setup complete")

    def _build_interface(self):
        with dpg.window(tag=self.winID):
            self.text_tag = "display_text"
            dpg.add_input_text(
                tag=self.text_tag,
                multiline=True,
                readonly=True,
                width=-1,
                height=-1,
                default_value="Waiting for data..."
            )
    
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
        
        # Check if viewport was closed by user (not by shutdown event)
        if not self._shutdown_event.is_set():
            logger.info("Viewport closed by user")
            self._send_event({"type": "viewport_closed_by_user"})
        
        logger.debug("Event loop ended")
    
    def _handle_input_data(self, data: Any) -> None:
        """
        Handle incoming data from input queue.
        
        Args:
            data: Data to display in the viewport.
        """
        if not dpg.does_item_exist(self.text_tag):
            logger.warning("Text widget does not exist")
            return
        
        try:
            text_input = str(data)
            dpg.set_value(self.text_tag, text_input)
            
            # Echo the received data back to output queue (for testing/demo)
            self._send_event({
                "type": "output",
                "data": data  # Echo the original data back
            })
            
        except Exception as e:
            logger.error(f"Error handling input data: {e}")
    
    def _cleanup_viewport(self) -> None:
        """Cleanup DearPyGUI resources."""
        try:
            if dpg.does_item_exist(self.winID):
                dpg.delete_item(self.winID)
            dpg.destroy_context()
            logger.debug("Viewport cleanup complete")
        except Exception as e:
            logger.error(f"Error during viewport cleanup: {e}")

