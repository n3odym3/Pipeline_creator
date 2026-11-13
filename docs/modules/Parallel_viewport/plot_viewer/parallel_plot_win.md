# Parallel Plot Module

The **Parallel Plot** module creates a dedicated 2D plotting window running in an independent OS subprocess via Python multiprocessing. By offloading DearPyGui rendering and data plotting to a separate process, it ensures that high-frequency data streams and complex curve rendering do not impact the responsiveness or frame rate of the main application.

---

## Features

- ⚡ **Multiprocessing Isolation**: Spawns an independent process (`PlotViewportProcess`) with its own DearPyGui context and event loop.
- 🔄 **Bidirectional Inter-Process Communication (IPC)**:
  - Streams data points, multi-series commands, and configuration packets to the viewport via non-blocking queues.
  - Receives real-time UI interaction events (e.g. dragline movements, window close events) back in the main pipeline.
- 📈 **Independent Plotting Interface**:
  - Embedded controls in the external window for **Autoscale**, **Smooth** (moving average filter with adjustable window size $1-50$), and **Clear Plot**.
  - Interactive red vertical **Dragline** with live position broadcast.
  - Legend, crosshairs, and anti-aliased multi-series display.
- 🎛️ **Main Node Controller**:
  - Live process status indicator (`Running` / `Stopped`), PID display, and event log.
  - Buttons to start, stop, or clear the parallel viewport on demand.

---

## Technical Specifications

### Inputs

| Input Type | Supported Payloads & Actions |
|---|---|
| `IOTypes.DATALIST` | Numerical list or coordinate arrays `(x, y)` to add or update series. |
| `IOTypes.CMD_DICT` | Command dictionary supporting actions: `"add serie"`, `"remove serie"`, `"update serie name"`. |

### Outputs

| Output Name | Type | Description |
|---|---|---|
| `Dragline` | `IOTypes.POSITION` | Emits the X position float value when the vertical drag line is moved in the parallel window. |
| `Events` | `IOTypes.CMD_DICT` | Broadcasts raw UI/lifecycle events received from the viewport subprocess. |

---

## Usage

1. Add the **Parallel Plot** node to your pipeline.
2. The parallel window opens automatically on creation (or can be toggled using **Start Parallel Plot** / **Stop Parallel Plot**).
3. Connect data sources emitting series (`DATALIST` or `CMD_DICT` actions) to feed real-time curves into the detached window.
4. Drag the vertical cursor line in the parallel window to emit position coordinates downstream through the `Dragline` output.
