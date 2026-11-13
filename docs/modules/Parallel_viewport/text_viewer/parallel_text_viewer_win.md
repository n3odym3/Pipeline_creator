# Parallel Text Viewer Module

The **Parallel Text Viewer** (or *Parallel Viewer*) module provides an isolated, multi-threaded/multi-process text display window running in a dedicated OS subprocess. It allows visualizing high-throughput textual streams, logs, or raw JSON payloads without freezing or slowing down the primary graphical pipeline interface.

---

## Features

- ⚡ **Multiprocessing Subprocess**: Runs a detached DearPyGui viewport process (`ViewportProcess`) with independent rendering and memory space.
- 📜 **High-Performance Text Streaming**: Safely streams incoming strings and payloads via inter-process message queues without blocking pipeline execution.
- 🎛️ **Main Process Control Node**:
  - Displays real-time connection status (`Running` / `Stopped`), Subprocess PID, and last received event.
  - Quick buttons to start and stop the viewport process.
- 🔁 **Bi-directional Event Flow**: Receives window events (such as viewport closure by user) and echoes output data downstream.

---

## Technical Specifications

### Inputs

| Input Type | Description |
|---|---|
| `IOTypes.TEXT` | Direct text string data to display. |
| `IOTypes.DATALIST` | Numerical list or raw sequence data converted to text representation. |
| `IOTypes.CMD_DICT` | Command dictionary or message packets. |

### Outputs

| Output Name | Type | Description |
|---|---|---|
| `Out` | `IOTypes.TEXT` | Forwards or echoes processed text data downstream. |
| `Events` | `IOTypes.CMD_DICT` | Emits lifecycle and UI event notifications from the subprocess. |

---

## Usage

1. Add the **Parallel Text Viewer** node to your pipeline canvas.
2. The viewport process launches automatically and creates an independent OS window titled `Parallel Viewer (Parallel)`.
3. Connect any text generator, logger, or processor module to its input pin to stream real-time text directly into the external window.
4. Stop or restart the detached viewport at any time using the control buttons on the main editor node.
