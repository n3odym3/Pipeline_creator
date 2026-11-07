# CSV Reader Module

Loads local CSV tabular data and emits structured sample packets for each data column.

---

## Technical Specifications

- **Inputs**:
  - `TRIGGER`: Reloads and re-emits the last selected CSV file. Also accepts a raw file path string.
- **Outputs**:
  - `Data` (Type: `SAMPLE`): Emits a `SAMPLE` dictionary containing `name`, `uuid`, `x`, `y`, and `action` for each column.

---

## Usage

- Click **Load CSV File** to open the local file explorer.
- Select a CSV spreadsheet with at least 2 columns (column 0 is used for X values, subsequent columns for Y values).
- The reader parses columns and streams data packets downstream.
- Can be triggered programmatically by upstream modules via `TRIGGER` or by passing a file path.
