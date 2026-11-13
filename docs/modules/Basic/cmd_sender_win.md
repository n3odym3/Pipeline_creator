# CMD Sender Module

Allows you to construct a custom dictionary interactively and send it to downstream control nodes.

---

## Technical Specifications

- **Inputs**: 
  - `TRIGGER`: Triggers immediate sending of the configured dictionary.
- **Outputs**:
  - `Dict` (Type: `CMD_DICT`): The constructed key-value dictionary.
  - `TXT` (Type: `TEXT`): String representation of the dictionary.

---

## Usage

- Add keys and string values to the dictionary via the interactive key-value table.
- Click **Add Key** to insert a new row, and **Delete** to remove a row.
- Click the **Send** button to dispatch the dictionary downstream.
- Any upstream `TRIGGER` event will also trigger emission.
