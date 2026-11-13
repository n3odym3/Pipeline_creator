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
| [Programmatic Pipeline](dev_programmatic.md) | Construct pipelines and connect modules programmatically via code |
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

This method is called automatically whenever the user switches privilege modes in the application. Pipeline Creator defines three standard user modes via `core.app_state.app_state.mode`:

- `"user"`: Operator mode. Restrict sensitive settings, lock calibration sliders, prevent window closure (`no_close=True`), and hide developer buttons.
- `"advanced"`: Power user / technician mode. Expose tuning parameters, offsets, and calibration controls.
- `"dev"`: Full developer access. Reveal raw debug buttons, internal inspectors, manual override triggers, and terminal output.

`update_permission()` allows you to dynamically adjust the operational freedom and complexity of your interface to match the user's role:

```python
def update_permission(self) -> None:
    from core.app_state import app_state
    
    is_user = (app_state.mode == "user")
    is_dev = (app_state.mode == "dev")
    
    # 1. Prevent accidental window closure in operator mode
    if dpg.does_item_exist(self.winID):
        dpg.configure_item(self.winID, no_close=is_user)
        
    # 2. Lock or enable sensitive calibration controls
    if dpg.does_item_exist(self.calib_slider_tag):
        dpg.configure_item(self.calib_slider_tag, enabled=not is_user)
        
    # 3. Show or hide internal debugging panels
    if dpg.does_item_exist(self.debug_group_tag):
        (dpg.show_item if is_dev else dpg.hide_item)(self.debug_group_tag)
        
    # 4. Enforce safe default values when dropping to user mode
    if is_user and hasattr(self, "power_limit"):
        self.power_limit = min(self.power_limit, 50.0)
```

### 3. Declare `persistent_fields`

Fields listed in `persistent_fields` are automatically serialized into the flow JSON when the workspace is saved, and restored when the pipeline is reloaded.

Each string in the list must match an exact attribute name on `self` (`self.attribute_name`):

```python
class MyFilterWin(WindowBase):
    label = "My Filter"
    
    # Declare class-level defaults
    threshold_val: float = 42.5
    default_path: str = "C:/data/records"
    enable_filter: bool = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        
        # Attributes to serialize when saving the pipeline
        self.persistent_fields = ["label", "threshold_val", "default_path", "enable_filter"]
```

#### How It Serializes in the Flow JSON
When the user saves the pipeline, `WindowBase.serialize()` extracts every attribute listed in `self.persistent_fields` and places them into the `"params"` block of the module:

```json
{
    "module": "my_category.my_filter_win",
    "class_name": "MyFilterWin",
    "uuid": "7a3f81e2-b94d-4819-a1b2-c0e812d34567",
    "pos": [140, 90],
    "size": [420, 280],
    "visible": true,
    "params": {
        "label": "My Filter",
        "threshold_val": 42.5,
        "default_path": "C:/data/records",
        "enable_filter": true
    }
}
```

When reloaded, `WindowBase` passes `"params"` as keyword arguments to `__init__(**params)` and automatically restores their values onto `self`.

### 4. Implement `close()` for Cleanup & Hardware Safety

The `close()` method is called automatically in three scenarios:

1. **Application Exit**: When the program is closed.
2. **Node Deletion**: When the user deletes the module or node from the canvas.
3. **Module Error / Crash**: When an unhandled error triggers a safe teardown.

In your implementation:

- **Stop and join background threads or worker processes** to prevent hanging or orphaned Python processes.
- **Send final safety shutdown commands to connected external devices**: If your module controls hardware (e.g. lasers, illumination LEDs, heating elements, high-voltage stages, motors, or serial/network sockets), always emit an explicit turn-off or safe-state command during `close()` so the physical device is not left running unattended.
- **Always call `super().close()`** to clean up DearPyGui tags and unregister from the module registry.

```python
def close(self) -> None:
    # 1. Hardware safety shutdown
    if hasattr(self, "_serial_device") and self._serial_device:
        try:
            self._serial_device.write(b"SHUTDOWN_HEATER\n")
            self._serial_device.close()
        except Exception as e:
            logger.error(f"[{self.label}] Error shutting down hardware: {e}")

    # 2. Stop multiprocessing worker or background threads
    if hasattr(self, "_processor") and self._processor:
        self._processor.stop()

    # 3. Always finalize through super()
    super().close()
```

### 5. Receive Data via `input_cb()` & Validate Data Structures

When an upstream module sends data through a connection wire, your node's `input_cb` is executed.

Incoming payloads can arrive by position (`args[0]`) or by keyword argument (`kwargs.get("data")`). Always unpack both safely:

```python
def input_cb(self, *args: Any, **kwargs: Any) -> None:
    data = kwargs.get("data") if "data" in kwargs else (args[0] if args else None)
    data_type = kwargs.get("data_type", None)
    if data is None:
        return
```

#### Important: Validate Underlying Data Structures
While `IOTypes` (e.g. `IOTypes.FRAME`, `IOTypes.SAMPLE`, `IOTypes.DATALIST`) identifies the semantic data contract, the underlying Python or NumPy structures can vary:

- **Image Frames**: A `FRAME` can be 2D Grayscale `(H, W)`, 3D BGR `(H, W, 3)`, or 4D BGRA `(H, W, 4)`. The data type may be `np.uint8` (standard 8-bit), `np.uint16` (12/16-bit HDR), or `np.float32`. Furthermore, upstream camera modules often wrap frames in an accessory tuple `(frame, source_name)`.
- **Numerical Arrays**: Samples or vector lists may vary in length, shape, or dimension.

Always check and validate incoming dimensions before processing:

```python
def input_cb(self, *args: Any, **kwargs: Any) -> None:
    data = kwargs.get("data") if "data" in kwargs else (args[0] if args else None)
    data_type = kwargs.get("data_type", None)
    if data is None:
        return

    # 1. Unpack accessory envelope if present
    source_name = None
    if isinstance(data, tuple) and len(data) >= 2 and isinstance(data[0], np.ndarray):
        frame, source_name = data[0], data[1]
    elif isinstance(data, np.ndarray):
        frame = data
    else:
        logger.warning(f"[{self.label}] Expected numpy array, got {type(data)}")
        return

    # 2. Check and validate matrix structure
    if frame.ndim == 2:
        # Grayscale (H, W) -> Convert to 3-channel if required by your algorithm
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    elif frame.ndim == 3:
        h, w, channels = frame.shape
        if channels == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    else:
        logger.error(f"[{self.label}] Incompatible frame shape: {frame.shape}")
        return

    # 3. Proceed with processing
    self.process_frame(frame)
```

### 6. Send Data Downstream via `trigger_cb()`

In `WindowBase`, all output terminals declared in `self.outputs` are mapped to a connection dictionary `self.connections = {terminal_name: [downstream_module, ...]}`. When a user connects a wire on the canvas from output `terminal_name` of Node A to Node B, Node B is added to Node A's `self.connections[terminal_name]`.

To emit data to all connected downstream modules:

```python
class MyProducerWin(WindowBase):
    label = "Measurement Producer"
    
    # Declare available output terminals and their IOTypes
    outputs = {
        "Value": IOTypes.NUMBER,
        "Filtered": IOTypes.FRAME,
    }

    def emit_measurement(self, value: float) -> None:
        # Check if any modules are connected before doing heavy formatting
        targets = self.connections.get("Value", [])
        if not targets:
            return  # No downstream receivers: skip computation

        dtype = self.outputs.get("Value")
        for module in targets:
            try:
                # Downstream nodes receive the payload in input_cb
                module.input_cb(data=value, data_type=dtype)
            except Exception as e:
                logger.error(f"[{self.label}] Error sending to {getattr(module, 'label', 'module')}: {e}")
```

### 7. Zero-Wire Broadcasting (Link Bus)

While terminal wires are ideal for direct pipelined dataflow, drawing physical wires across the canvas for global notifications, emergency shutdowns, or cross-module synchronization can clutter the workspace.

Pipeline Creator provides **`link_bus`** (`from core.link_bus import link_bus`), a lightweight global Publish/Subscribe (Pub/Sub) event broker:

- **When to Use**: Global emergency stop commands, master run/pause triggers, parameter synchronization across multiple windows, or event broadcasting.
- **Publishing**: Any module can broadcast a message to a topic string.
- **Subscribing**: Modules subscribe by passing a callback function.
- **Unsubscribing**: **Crucial!** Always unsubscribe in `close()` to prevent memory leaks and zombie callback invocations on destroyed windows.

```python
from core.link_bus import link_bus

class MyControllerWin(WindowBase):
    label = "Global Controller"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        
        # Subscribe to a global topic channel
        link_bus.subscribe("system/emergency_stop", self._on_emergency_stop)

    def _on_emergency_stop(self, *args: Any, **kwargs: Any) -> None:
        logger.warning(f"[{self.label}] Emergency stop received!")
        self.stop_all_hardware()

    def broadcast_preset_change(self, preset_name: str) -> None:
        # Publish event to all listening modules across the application
        link_bus.publish("presets/active_preset", preset=preset_name, sender=self.UUID)

    def close(self) -> None:
        # ALWAYS unsubscribe when the module is closed or destroyed
        link_bus.unsubscribe("system/emergency_stop", self._on_emergency_stop)
        super().close()
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
├── module_template.py       ← Clean WindowBase UI module template (using self.window())
├── processor_template.py    ← Background multiprocessing worker template (ProcessingBase)
└── viewport_template.py     ← Parallel DearPyGui viewport in an isolated OS window
```

Use these clean templates as your starting point when creating a new module.

---

## Logging

Pipeline Creator uses [loguru](https://github.com/Delgan/loguru) as its structured logging framework. Standardized logs are formatted and printed to the terminal console, automatically written to rolling timestamped log files in the `/logs` directory, and captured live by the in-app **Log Manager** (`Log Viewer` window, accessible via **Tools → Show Logs**), which allows operators and developers to monitor and filter events in real time by severity level.

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

## Stress Testing & Benchmarking Your Module

To verify your custom module's stability, maximum throughput, queue overflow behavior, and memory footprint under heavy loads, Pipeline Creator includes a comprehensive suite of **Benchmark modules** located in `modules/benchmark/`.

Always test your module before deploying it into production pipelines:

- **As a Producer (Stress-test your module's inputs)**: Connect a Benchmark Producer (`Video Benchmark`, `Sample Benchmark`, `Vector Benchmark`, `CMD Benchmark`) to your module's input terminals. You can blast data at configurable frequencies (1–1000 Hz or uncapped CPU speed), testing variable payload sizes, HDR bit depths (8/12/16-bit), or waveform patterns. This allows you to evaluate your node's saturation limits, verify that your queue `drop_policy` (`drop_oldest`, `drop_new`, `block`) behaves as expected, and ensure memory remains stable.
- **As a Consumer (Stress-test your module's outputs)**: Connect your module's outputs to a Benchmark Processor (`Image Processor Benchmark`, `Sample Processor Benchmark`, `Hardware Simulator`). These mock sinks execute parameterized workloads (from zero-compute pass-through to heavy CPU/GPU matrix burns) to observe how downstream backpressure and queue delays affect your node.
- **In-Canvas Speedometer & Profiling**: Pair your tests with the [Propagation Tester](timing_benchmarks_and_safety.md#in-flow-propagation-tester-speedometer-node), [Flow Metrics Viewer](timing_benchmarks_and_safety.md#flow-metrics-viewer), and in-canvas [Node Profiling](timing_benchmarks_and_safety.md#node-editor-in-canvas-profiling) to measure precise transmission times (Delta-t) and queue states in real time.

For full details on the available benchmark modules and testing architecture, see [Module Stress Testing & Benchmark Suite](timing_benchmarks_and_safety.md#module-stress-testing-benchmark-suite).

---

## Creating Documentation

Pipeline Creator features an auto-discovery system for module documentation. When you write documentation for your custom modules, it is automatically discovered, copied, and integrated into the sidebar navigation layout when the documentation server is launched.

### How to Add Documentation to a Module

To write documentation for your custom module:

1. Create a `README.md` file in standard Markdown format directly inside the module's subdirectory (e.g., `modules/my_category/my_module/README.md`).
2. Launch or restart the local documentation server from the application menu (**Help** -> **Documentation** -> **Start Server**).

The documentation manager dynamically scans the `/modules` folder hierarchy, duplicates `README.md` contents into the wiki workspace, and rebuilds the navigation links under the **Modules Documentation** category to match your folder hierarchy.
