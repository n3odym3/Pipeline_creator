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

```python
from __future__ import annotations
from typing import Any, Dict, List, Optional
import dearpygui.dearpygui as dpg
from core.window_base import WindowBase
from core.input_output_types import IOTypes

class MyNode(WindowBase):
    """
    Short description of what this node does.
    """

    # 1. Declarative geometry & default settings (WindowBase uses these automatically)
    label: str = "My Node"
    win_width: int = 300
    win_height: int = 200

    # 2. Accepted input types & output terminals
    accepted_input_types: List[Any] = [IOTypes.NUMBER]
    outputs: Dict[str, Any] = {"RESULT": IOTypes.NUMBER}

    # 3. Persistent fields (automatically saved and restored during pipeline import)
    persistent_fields: List[str] = ["label", "threshold"]

    # 4. Custom attribute defaults
    threshold: float = 0.5

    def __init__(self, threshold: float = 0.5, **kwargs: Any) -> None:
        """
        No need to repeat label, win_width, win_height, pos, uuid, or visible!
        Simply pass custom parameters and forward **kwargs to super().__init__.
        """
        super().__init__(**kwargs)
        self.threshold = threshold

        # Unique DPG tags (always suffix with self.UUID)
        self._slider_tag = f"my_slider_{self.UUID}"

        # 5. Build the DPG window with concise context manager
        with self.window():
            dpg.add_text("Threshold:")
            dpg.add_slider_float(
                tag=self._slider_tag,
                default_value=self.threshold,
                min_value=0.0, max_value=1.0,
                callback=self._on_slider_changed,
            )

        # 6. Apply privilege visibility
        self.update_permission()

    # ── Callbacks ──────────────────────────────────────────────────────────

    def _on_slider_changed(self, sender: Any, value: float) -> None:
        self.threshold = value

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """Called when upstream nodes send data through a wire."""
        data = kwargs.get("data") or (args[0] if args else None)
        if isinstance(data, (int, float)):
            result = data * self.threshold
            self._emit("RESULT", result)

    def update_permission(self) -> None:
        """Show/hide or enable/disable items based on privilege mode."""
        from core.app_state import app_state
        is_user = (app_state.mode == "user")
        if dpg.does_item_exist(self._slider_tag):
            dpg.configure_item(self._slider_tag, enabled=not is_user)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _emit(self, output_name: str, data: Any) -> None:
        """Push data to all downstream nodes connected to output_name."""
        from core.module_registry import MODULES_REGISTRY
        for uuid in self.connections.get(output_name, []):
            target = MODULES_REGISTRY.get(uuid)
            if target:
                target.input_cb(data=data, data_type=self.outputs[output_name])


# Required by the module registry
EXPORTED_CLASS = MyNode
EXPORTED_NAME  = "My Node"
```

---

## `WindowBase` Contract

`WindowBase` provides built-in behaviors and declarative defaults:

### Declarative Class Attributes
| Attribute | Type | Description |
|---|---|---|
| `label` | `str` | Default display name of the window |
| `win_width` | `int` | Default width in pixels (or `-1` for auto) |
| `win_height` | `int` | Default height in pixels (or `-1` for auto) |
| `pos` | `tuple` | Default `(x, y)` position |
| `visible` | `bool` | Initial window visibility |
| `accepted_input_types` | `list` | List of `IOTypes` accepted as inputs |
| `outputs` | `dict` | Maps output port name → `IOTypes` |
| `persistent_fields` | `list` | Attribute names saved in pipeline JSON and restored on reimport |

### Instance Attributes & Helpers
| Member | Description |
|---|---|
| `self.UUID` | Unique identifier generated for this instance |
| `self.winID` | Top-level DPG window tag (`f"{label}_{UUID}"`) |
| `self.connections` | Auto-initialized dictionary `{port_name: [targets]}` matching `outputs` |
| `with self.window(**kwargs):` | Context manager wrapping `dpg.window(...)` configured with this module's geometry |

---

## Fusion (Tab-Docking) Support

If your node should support being "fused" into a `FusionManager` tab, `WindowBase` handles this automatically. The fusion manager will hide the standalone window and embed its children inside a tab panel.

No extra code is required, simply create your widgets inside `with self.window():`.

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
