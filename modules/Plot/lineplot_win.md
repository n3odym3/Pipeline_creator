# Lineplot Module

The **Lineplot** module is an interactive 2D data visualization window designed to display, inspect, and analyze continuous time-series or XY curve signals in real time.

---

## Features

- **Multi-Series Rendering**: Plot and manage multiple overlaid curves with customized labels, distinct UUID tracking, and legends.
- **Interactive Cursor Annotation**: Dynamically detects and inspects the closest data point on mouse hover, displaying its index, X coordinate, and Y coordinate.
- **Vertical Reference Line**: Add an interactive draggable vertical line (`Drag Line`) for thresholding or time synchronization, controllable manually or programmatically.
- **Signal Smoothing**: Built-in moving average filter (rolling window convolution) with a configurable window size ($1$ to $50$) to filter out noise on the fly.
- **Logarithmic Scaling**: Switch between linear and logarithmic X-axis scales (`Log X`).
- **Autoscale / Autofit**: Automatic axis scaling on data updates to keep series in view.
- **TSV Clipboard Export**: One-click export of all plotted series into Tab-Separated Values (TSV) directly to the system clipboard for immediate pasting into Excel, Prism, or Python.
- **Downstream Plot Streaming**: Broadcast current plot data, axis limits, and configuration via the `CMD` output (`Send Plot`).

---

## Technical Specifications

### Inputs

| Input Type | Supported Payload Formats & Actions |
|---|---|
| `IOTypes.SAMPLE` / `IOTypes.ROI_SAMPLE` | Dictionary containing `x`, `y`, `name`, `uuid`, and `action` (`"select"`, `"unselect"`, `"delete"`, `"rename"`, `"clear"`). |
| `IOTypes.CMD_DICT` | Command dictionary supporting actions: `"position"` (moves drag line), `"add serie"`, `"remove serie"`, `"update serie name"`, `"clear"`. |
| `IOTypes.DATALIST` | Numerical list or coordinate arrays `(x, y)` for legacy streaming. |

### Outputs

| Output Name | Type | Description |
|---|---|---|
| `Datalist` | `IOTypes.DATALIST` | Emits data as a list of points. |
| `CMD` | `IOTypes.CMD_DICT` | Emits structured plot packets with `action: "plot_data"`, series list, title, labels, and axis limits. |

---

## UI Controls

- **Anotation** (Checkbox): Enables hovering tooltips showing the nearest point's `Index`, `X`, and `Y`.
- **Drag Line** (Checkbox): Toggles the interactive vertical cursor line.
- **Autoscale** (Checkbox): Automatically fits X and Y axes when new data is received.
- **Smooth** (Checkbox) & **Win** (Slider): Toggles moving average smoothing and sets the convolution window width ($1-50$).
- **Log X** (Checkbox): Toggles logarithmic scaling on the horizontal axis.
- **Export TSV** (Button): Copies all displayed series in column format (`X`, `Series1`, `Series2`...) to the clipboard.
- **Send Plot** (Button): Packages the current graph state and transmits it to connected modules via `CMD`.
- **Clear Plot** (Button): Removes all series and annotations from the canvas.
