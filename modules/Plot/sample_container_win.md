# Sample Container Module

The **Sample Container** module is a centralized repository and manager for datasets (`SAMPLE` format). It allows storing, previewing, reordering, filtering, importing, and exporting multiple data series before streaming them to downstream visualization or analysis modules (such as Lineplot, Boxplot, or data processors).

---

## Features

- **Sample Registry & List**: Displays all loaded samples with individual toggles, names, and mini plot preview tooltips.
- **Interactive Drag-and-Drop Reordering**: Easily reorder stored samples using drag handles to adjust their display and export sequence.
- **Mini-Plot Tooltip**: Hovering over any sample's checkbox renders a thumbnail graph of the signal curve for quick visual inspection.
- **Duplicate Management**:
  - `override`: Overwrites existing sample data when a sample with the same name is ingested.
  - `append`: Automatically appends a numeric suffix (e.g. `Sample_1`, `Sample_2`) to preserve both entries.
- **Batch Actions**: One-click **Select all**, **Deselect all**, and **Clear samples** operations.
- **Flexible CSV Export**:
  - **X Axis Reference Modes**:
    - `index`: Exports uniform integer indexing ($0, 1, 2\dots$) as the primary X axis.
    - `individual`: Generates separate paired columns (`Name_X`, `Name_Y`) for each selected sample.
    - *Sample Reference*: Uses a specific sample's X array as the shared timebase for all exported columns.
  - Export selected samples via dialog or auto-save headlessly.
- **Robust CSV Import**: Automatically detects single X or multi-column paired (`_X`/`_Y`) spreadsheet layouts and registers samples into the container.
- **Reactive Event Streaming**: Emits atomic events (`action`: `"select"`, `"unselect"`, `"rename"`) whenever sample selection or naming changes.

---

## Technical Specifications

### Inputs

| Input Type | Description |
|---|---|
| `IOTypes.SAMPLE` | Ingests sample data (`x`, `y`, `name`, `action`). |
| `IOTypes.FOLDER_PATH` | Configures the default working folder for CSV import and export. |
| `IOTypes.TRIGGER` | Triggers automated CSV export when receiving `"save"` or trigger pulse. |
| `IOTypes.CMD_DICT` | Command dictionary supporting actions like `{"export": True}`. |

### Outputs

| Output Name | Type | Description |
|---|---|---|
| `Data` | `IOTypes.SAMPLE` | Emits active sample data payloads with action tags (`"select"`, `"unselect"`, `"rename"`). |

---

## UI Controls

- **Import/export** (Collapsing Header):
  - **X axis reference** (Dropdown): Selects the reference mode (`index`, `individual`, or a specific sample).
  - **Import CSV** (Button): Opens file explorer to import series from a CSV file.
  - **Export CSV** (Button): Opens file explorer to save selected samples into a CSV spreadsheet.
  - **Duplicate** (Dropdown): Selects conflict resolution strategy (`override` vs `append`).
- **Clear samples** (Button): Clears all samples from memory and sends unselect signals.
- **Select all** / **Deselect all** (Buttons): Toggles selection state for all items simultaneously.
- **Sample List Items**:
  - **Drag Handle** (`☰`): Drag to reorder sample position in the list.
  - **Checkbox & Tooltip**: Toggles sample activation (emits `select` / `unselect`) and displays mini-plot preview on hover.
  - **Delete Button** (`🗑`): Removes the individual sample.
  - **Name Input**: Rename the sample inline (emits `rename` update to connected nodes).
