# Programmatic Interface Prototyping

While the standard workflow in Pipeline Creator is to build, prototype, or load layouts using the visual **Node Editor**, it is also possible to bypass the GUI canvas completely and instantiate and connect modules **purely programmatically** using Python.

---

## Standard Workflow vs. Programmatic Prototyping

1. **Standard Workflow**: Prototyping by adding nodes dynamically in the Node Editor, connecting terminals visually with wires, or loading pre-defined pipeline layouts (`.json` files) at startup or from the menu.
2. **Programmatic Prototyping**: Writing a Python script that instantiates module classes as code, assigns their labels, dimensions, and window coordinates, and links them via programmatic connection methods.

---

## Programmatic Hello World Example

To connect a **Hello World** module to a **Text Viewer** module in code (mirroring what you would do in the visual Node Editor):

1. Instantiate the module classes (`HelloWorld_win` and `Text_viewer_win`).
2. Pass arguments like `label`, `pos`, `win_width`, and `win_height`.
3. Call `helloworld.connect_to(text_viewer, input_pin_index)` to link them.

Here is the helper script from `template_and_doc/manual_layout.py`:

```python
from modules.basic_ui.text_viewer_win import Text_viewer_win
from modules.basic_ui.Hello_world_win import HelloWorld_win

def create_windows() -> None:
    # 1. Instantiate modules with custom coordinates and labels
    helloworld = HelloWorld_win(label="Hello World", pos=(50, 50), win_width=300, win_height=200)
    text_viewer = Text_viewer_win(label="Text Viewer", pos=(350, 50), win_width=400, win_height=300)

    # 2. Programmatically connect output from helloworld to input index 0 of text_viewer
    helloworld.connect_to(text_viewer, 0)
```

---

## Loading a Programmatic Layout

To run your programmatic layout in the application, import and run it during startup. For example, in the initialization phase (e.g. inside `main_win` or `main.py` when preparing the UI):

```python
from template_and_doc import manual_layout
manual_layout.create_windows()
```

This will instantiate the window items, set up their callbacks, draw the visual connection line in the background context, and render them on screen at launch.

---

## Exporting a Programmatically Built Pipeline

If you build a pipeline programmatically, you can serialize and save it directly to a pipeline JSON file so that it can be loaded later via the Node Editor. Call `export_workspace` from `core.module_registry`:

```python
from core.module_registry import export_workspace

# Passing None as the first argument defaults to the global MODULES_REGISTRY
export_workspace(None, 'pipelines/manual_layout.json')
```
