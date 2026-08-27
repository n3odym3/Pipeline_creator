# Developer Guide

Welcome to the Pipeline Creator developer documentation. This guide covers everything you need to know to build new node modules — from a simple UI panel to a fully isolated background processor.

---

## Quick Start

All modules live inside the `/modules` directory, organized into category subdirectories:

```
modules/
├── basic_ui/
│   ├── csv_reader_win.py      ← UI node (WindowBase)
│   └── image_viewer_win.py
├── processing/
│   ├── signal_filter_proc.py  ← Processor (ProcessingBase)
│   └── data_normalizer_proc.py
└── my_category/
    └── my_new_module_win.py   ← Your new module goes here
```

Every module file needs to expose at module level:

```python
EXPORTED_CLASS = MyModuleClass
EXPORTED_NAME  = "My Module Name"
```

The registry auto-discovers these at startup.

---

## Module Types

| Guide | Description |
|---|---|
| [Creating a UI Node](dev_ui_node.md) | Build an interactive panel using `WindowBase` |
| [Creating a Processor](dev_processor_node.md) | Offload CPU work using `ProcessingBase` |
| [Creating a Theme](dev_theme.md) | Add a custom color palette |

---

## Advanced Topics

| Guide | Description |
|---|---|
| [Managing Tutorials](dev_tutorial.md) | Integrate step-by-step guides and workflow hints |
| [Programmatic Layout](dev_programmatic.md) | Construct layouts and connect modules programmatically via code |
| [Compile Program](dev_compile.md) | Build standalone binaries using Nuitka |

---

## Developer Best Practices

### 1. Always Suffix DPG Tags with `self.UUID`

DearPyGui maintains a **global item registry**. If two instances of the same module use identical string tags, DPG will crash with a duplicate ID error.

```python
# ❌ Bad — crashes if two instances exist
dpg.add_button(tag="confirm_button")

# ✅ Good — UUID guarantees uniqueness
dpg.add_button(tag=f"confirm_button_{self.UUID}")
```

`self.UUID` is set automatically by `WindowBase.__init__()`.

### 2. Implement `update_permission()`

This method is called automatically when the user switches privilege modes. Use it to hide or disable controls:

```python
def update_permission(self) -> None:
    from core.app_state import app_state
    is_user = (app_state.mode == "user")
    if dpg.does_item_exist(self.slider_tag):
        dpg.configure_item(self.slider_tag, enabled=not is_user)
```

### 3. Declare `_persistent_fields`

Fields listed in `_persistent_fields` are automatically serialized into the flow JSON when the workspace is saved:

```python
self._persistent_fields = ["label", "threshold_val", "default_path"]
```

The values are restored when the workspace is reloaded.

### 4. Implement `close()` for Cleanup

If your module spawns threads, background processes, or opens files, implement `close()`:

```python
def close(self) -> None:
    if hasattr(self, '_processor') and self._processor:
        self._processor.stop()
    super().close()   # Always call super
```

The registry calls `close()` on all modules during application shutdown.

### 5. Receive Data via `input_cb()`

When an upstream node sends data through a wire connection, `input_cb` is called:

```python
def input_cb(self, *args: Any, **kwargs: Any) -> None:
    data = kwargs.get("data") or (args[0] if args else None)
    data_type = kwargs.get("data_type")
    # Process incoming data...
```

### 6. Send Data Downstream

To push data to connected downstream nodes:

```python
from core.module_registry import MODULES_REGISTRY

def _emit_result(self, result: Any) -> None:
    for downstream_uuid in self.connections.get("OUTPUT_NAME", []):
        target = MODULES_REGISTRY.get(downstream_uuid)
        if target:
            target.input_cb(data=result, data_type=IOTypes.NUMBER)
```

### 7. Zero-Wire Broadcasting (Link Bus)

For optional, loose coupling without physical wires:

```python
from core.link_bus import link_bus

# Publishing
link_bus.publish("my_channel", data=my_result)

# Subscribing (typically in __init__)
link_bus.subscribe("my_channel", self._on_data_received)

# Unsubscribing (in close())
link_bus.unsubscribe("my_channel", self._on_data_received)
```

---

## IOTypes Reference

All terminal connections use typed `IOTypes`. Declare what types your node accepts and emits:

```python
from core.input_output_types import IOTypes

# In __init__:
self.accepted_input_types = [IOTypes.NUMBER, IOTypes.FRAME]
self.outputs = {
    "RESULT": IOTypes.NUMBER,
    "PREVIEW": IOTypes.FRAME,
}
```

Here are a few common examples of available I/O types:

```python
# General Types
TRIGGER = ("trigger", "str or None", "Trigger event with or without attached data")
TEXT    = ("text", "str", "Standard string-based messaging")
NUMBER  = ("number", "int|float", "Numerical value for calculations or thresholds")

# Visual / Processing Types
FRAME   = ("frame", "np.ndarray or tuple(np.ndarray, str)", "8-bit image frame. Can be (frame, name).")

# Data / Control Types
CMD_DICT = ("cmd_dict", "dict", "Action command encapsulated in a dictionary")
ANY      = ("any", "Any", "Generic data type for flexible connections")
```

For the complete list of types, refer directly to the source file `core/input_output_types.py`.

---

## Template Files

Ready-to-copy starter templates are available in the `template_and_doc/` folder:

```
template_and_doc/
├── demo_window_module.py       ← Full WindowBase template with comments
└── demo_processing_module.py   ← Full ProcessingBase template with comments
```

Use these as your starting point when creating a new module.

---

## Logging

Pipeline Creator uses **`loguru`** as its logging framework. Standardized logs are printed to the console, saved to log files in the `/logs` directory, and automatically captured by the in-app Log Viewer.

### How to Import and Log

To log events from your custom modules, import `logger` from `loguru` and call the method corresponding to the desired severity level:

```python
from loguru import logger

# TRACE: Very granular diagnostic information
logger.trace("Entering low-level computation loop")

# DEBUG: Helpful developer information
logger.debug("Applying filter matrix with size 3x3")

# INFO: General operational messages
logger.info("Camera connection initialized successfully")

# SUCCESS: Successful milestones
logger.success("CSV file successfully exported to data/output.csv")

# WARNING: Potential issues or recovery scenarios
logger.warning("Network connection latency is higher than expected")

# ERROR: Recoverable failures
logger.error("Failed to read frame from sensor, retrying...")

# CRITICAL: Fatal crashes or system instability
logger.critical("Failed to bind to communication socket. Shutting down.")
```

### Logging Exceptions

`loguru` has excellent support for logging exceptions with full stack traces using `logger.exception()`:

```python
try:
    with open("data.csv", "r") as f:
        content = f.read()
except FileNotFoundError as e:
    logger.exception("Failed to load required CSV file:")
```

---

## Creating Documentation

Pipeline Creator features an auto-discovery system for module documentation. When you write documentation for your custom modules, it is automatically discovered, copied, and integrated into the sidebar navigation layout when the documentation server is launched.

### How to Add Documentation to a Module

To write documentation for your custom module:
1. Create a `README.md` file in standard Markdown format directly inside the module's subdirectory (e.g., `modules/my_category/my_module/README.md`).
2. Launch or restart the local documentation server from the application menu (**Help** -> **Documentation** -> **Start Server**).

The documentation manager dynamically scans the `/modules` folder hierarchy, duplicates `README.md` contents into the wiki workspace, and rebuilds the navigation links under the **Modules Documentation** category to match your folder hierarchy.
