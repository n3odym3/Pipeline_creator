# Creating a UI Node (`WindowBase`)

Visual nodes inherit from `WindowBase`, which handles DPG window creation, UUID management, serialization, and connection event routing automatically.

---

## File Location

Create your module at:
```
modules/<your_category>/<your_module_name>_win.py
```

Convention: suffix the filename with `_win` to indicate it is a visual node.

---

## Minimal Template

This template corresponds to `template_and_doc/module_template.py`:

```python
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger

from core.input_output_types import IOTypes
from core.window_base import WindowBase


class Template_win(WindowBase):
    """
    A basic example module window using the WindowBase system.

    Demonstrates:
    - Declaring window defaults (label, win_width, win_height)
    - Declaring input/output types (checked by Module Health Check)
    - Saving persistent fields (e.g., default_number)
    - Handling incoming data via input_cb
    - Sending messages downstream to connected modules
    - Adapting UI permissions based on app_state mode
    """

    # 1. Window Configuration (Class defaults - can also be passed via kwargs)
    label = "Template"
    win_width = 300
    win_height = 200

    # 2. Input/Output definitions (Required by Module Health Check)
    accepted_input_types = [IOTypes.TEXT]
    outputs = {
        "TEXT": IOTypes.TEXT,
        "NUMBER": IOTypes.NUMBER,
    }

    # 3. Persistent fields saved into pipeline JSON
    persistent_fields = ["label", "default_number"]

    def __init__(self, default_number: int = 10, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.default_number = default_number
        self.btn_tag = f"template_btn_{self.UUID}"
        self.number_tag = f"template_number_{self.UUID}"

        # Build DPG Window UI using the convenient self.window() context manager
        with self.window():
            dpg.add_button(tag=self.btn_tag, label="Hello world", callback=self.trigger_cb)

            dpg.add_input_int(
                tag=self.number_tag,
                default_value=self.default_number,
                callback=lambda sender, value: setattr(self, "default_number", value),
            )

        # Always refresh permissions after UI creation
        self.update_permission()

    def update_permission(self) -> None:
        """
        Adapts the module UI based on the application mode:
        - 'user'     : Restricted access (e.g., read-only fields, hidden buttons).
        - 'advanced' : Standard access.
        - 'dev'      : Full access to all internals.
        """
        from core.app_state import app_state

        mode = app_state.mode

        # Example 1: Hide an element in 'user' mode
        if dpg.does_item_exist(self.btn_tag):
            dpg.configure_item(self.btn_tag, show=(mode != "user"))

        # Example 2: Lock editing in 'user' mode
        if dpg.does_item_exist(self.number_tag):
            is_readonly = mode == "user"
            dpg.configure_item(self.number_tag, readonly=is_readonly, enabled=not is_readonly)

        # Example 3: Hide the entire window if needed (e.g., dev-only module)
        if mode == "user":
            dpg.hide_item(self.winID)

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """Processes incoming data from upstream connected nodes."""
        logger.debug(f"{self.winID} received input: args={args}, kwargs={kwargs}")

    def trigger_cb(self, *args: Any, **kwargs: Any) -> None:
        """Sends data downstream to connected modules."""
        for idx, output_key in enumerate(self.outputs):
            connected_modules = self.connections.get(output_key, [])
            for module in connected_modules:
                if idx == 0:
                    module.input_cb("Hello World")
                elif idx == 1:
                    module.input_cb(self.default_number)


EXPORTED_CLASS = Template_win
EXPORTED_NAME = "Template"
```

---

## `WindowBase` Contract

`WindowBase.__init__()` provides the following attributes that your module can use:

| Attribute | Type | Description |
|---|---|---|
| `self.UUID` | `str` | Unique identifier for this instance |
| `self.winID` | `str` | DPG tag for the top-level window (`f"{label}_{UUID}"`) |
| `self.label` | `str` | Display name of the node |
| `self.pos` | `tuple` | Current (x, y) position in pixels |
| `self.win_width` | `int` | Window width in pixels |
| `self.win_height` | `int` | Window height in pixels |
| `self.visible` | `bool` | Whether the window is currently shown |
| `self.connections` | `dict` | Maps output name → list of downstream UUIDs |
| `self.persistent_fields` | `list` | Field names to save/restore in flow JSON |
| `self.accepted_input_types` | `list` | IOTypes this node accepts as input |
| `self.outputs` | `dict` | Maps output name → IOType |

---

## Fusion (Tab-Docking) Support

If your node should support being "fused" into a `FusionManager` tab, `WindowBase` handles this automatically. The fusion manager will hide the standalone window and embed its children inside a tab panel.

No extra code is required, just ensure all your DPG children are created inside the `with self.window():` context block.

---

## Node in the Node Editor (Graph Tab)

In addition to the floating window, each module instance is represented as a **node card** in the Node Editor canvas. This card shows:
- The module **label**.
- Colored **input attribute** terminals.
- Colored **output attribute** terminals.

The node card is created automatically by the registry when your module is instantiated from the Node Editor popup. You do not need to create it manually.

---

## Testing Your Module

1. Place the file in `modules/<category>/`.
2. Restart Pipeline Creator.
3. Right-click the Node Editor canvas and search for your module name.
4. Verify the node appears, the window opens, and the slider controls work.
5. Connect an upstream node and verify `input_cb` is triggered.
