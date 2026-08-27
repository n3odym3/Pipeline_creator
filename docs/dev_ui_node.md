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
from typing import Any, Dict, Tuple, Optional
import dearpygui.dearpygui as dpg
from core.window_base import WindowBase
from core.input_output_types import IOTypes

class MyNode(WindowBase):
    """
    Short description of what this node does.
    """

    def __init__(
        self,
        label: str = "My Node",
        win_width: int = 300,
        win_height: int = 200,
        pos: Tuple[int, int] = (50, 50),
        uuid: Optional[str] = None,
        outputs: Optional[Dict[str, Any]] = None,
        visible: bool = True,
        # ↓ Add your own persistent parameters here
        threshold: float = 0.5,
    ) -> None:

        super().__init__(
            label=label, pos=pos,
            win_width=win_width, win_height=win_height,
            uuid=uuid, outputs=outputs, visible=visible
        )

        # 1. Persistent fields (saved to flow JSON)
        self._persistent_fields = ["label", "threshold"]

        # 2. Accepted input types
        self.accepted_input_types = [IOTypes.NUMBER]

        # 3. Output terminals
        self.outputs = {"RESULT": IOTypes.NUMBER}
        self.connections = {k: [] for k in self.outputs}

        # 4. Unique DPG tags
        self._slider_tag = f"my_slider_{self.UUID}"
        self.threshold = threshold

        # 5. Build the DPG window
        with dpg.window(
            label=self.label,
            width=self.win_width,
            height=self.win_height,
            pos=self.pos,
            tag=self.winID,
            show=self.visible,
        ):
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
| `self._persistent_fields` | `list` | Field names to save/restore in flow JSON |
| `self.accepted_input_types` | `list` | IOTypes this node accepts as input |
| `self.outputs` | `dict` | Maps output name → IOType |

---

## Fusion (Tab-Docking) Support

If your node should support being "fused" into a `FusionManager` tab, `WindowBase` handles this automatically. The fusion manager will hide the standalone window and embed its children inside a tab panel.

No extra code is required, just ensure all your DPG children are created inside the `dpg.window(tag=self.winID)` block.

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
