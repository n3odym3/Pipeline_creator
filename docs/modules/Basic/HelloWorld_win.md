# Hello World Module

A simple trigger node designed for pipeline testing and connectivity validation.

---

## Technical Specifications

- **Inputs**:
  - `TRIGGER`: Programmatic trigger forwarding an emission downstream.
- **Outputs**:
  - `Out` (Type: `TEXT`): Emits the text `"Hello World"`.

---

## Usage

- Click the **Hello World** button in the module window to dispatch `"Hello World"` to connected text nodes.
- Upstream `TRIGGER` inputs will immediately forward the trigger event downstream.
