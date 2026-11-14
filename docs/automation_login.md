# <u>Automation & Login Guide</u>

Pipeline Creator provides two independent, distinct startup options that can be used separately or together :

1. **Login Dialog**: Controls user authentication and privilege levels. If disabled, the application skips the prompt and defaults directly to the privilege level specified by `default_mode` (which defaults to `"dev"`, the highest privilege level).
2. **Workspace Preloading / Automation**: Automatically loads a specific pipeline layout file (`default_pipeline`) and/or runs an automation script (`default_automation_script`) at launch.

---

## <u>Login Dialog</u>

In *config.json*:

```json
"Login": {
    "enabled": true,
    "default_mode": "user"
}
```

- **When `enabled` is `true`**: The dialog is shown at startup before the main window becomes usable.
- **When `enabled` is `false`**: The dialog is skipped entirely. The application defaults directly to the privilege mode specified by `default_mode`.

### <u>Re-triggering at Runtime</u>

Press `Ctrl + Shift + L` to reopen the login dialog without restarting. Useful for switching modes during a session.

```python
# Programmatic trigger (from code)
from core.login_dialog import show_login_dialog

show_login_dialog(
    default_mode="advanced",
    on_confirm=main_win.apply_mode_visibility,
    pump_frames=False   # Must be False at runtime (True only at startup)
)
```

!!! warning "pump_frames at startup only"
    The `pump_frames=True` flag causes the login dialog to block the startup sequence by pumping DPG frames internally until confirmed. This **must not** be used at runtime (after the main loop has started), as it would conflict with the running render loop.

## <u>Pipeline Preloading</u>

To automatically load a specific pipeline layout (.json) at launch:

1. Open `config.json`.
2. Set `"default_pipeline"` under `"Paths"` to the relative path of your pipeline file:
   ```json
   "Paths": {
       "default_pipeline": "pipelines/my_default_pipeline.json"
   }
   ```

When the application starts, it will automatically reconstruct this pipeline on canvas, regardless of whether the Login Dialog is enabled or disabled.

---

## <u>Automation Scripts</u>

Automation scripts are **JSON files** that define a sequence of actions to run automatically at startup. They allow you to configure the working directory, load a specific layout, trigger module methods, and more — all without user interaction.

### <u>Triggering a Script</u>

**Option 1 — via `config.json`** (runs automatically at startup):
```json
"Paths": {
    "default_automation_script": "scripts/my_startup.json"
}
```

**Option 2 — via command line**:
```bash
python main.py --script path/to/my_script.json
```

**Option 3 — from the UI** (Advanced+ mode):
`Main Window → Workspace → Load Automation Script`

---

## <u>Script Structure</u>

An automation script is a JSON object with a `name` and a list of `steps`. Each step has an `action` key that selects which operation to perform.

```json
{
    "name": "My Startup Script",
    "steps": [
        { "action": "set_workspace", "path": "C:/data/my_project" },
        { "action": "load_pipeline", "path": "pipelines/default_pipeline.json" },
        { "action": "fullscreen",    "enabled": true },
        { "action": "wait",          "seconds": 1.5 },
        { "action": "trigger",       "module_type": "MyCameraModule", "method": "start_acquisition" }
    ]
}
```

---

## <u>Action Reference</u>

### set_workspace

Sets the working directory. All relative paths in subsequent steps are resolved against this directory. If `browse` is `true` or no `path` is provided, it opens the interactive folder picker. *(Aliases: `set_working_dir`, `set_working_directory`, `set_directory`.)*

```json
{ "action": "set_workspace", "path": "C:/data/my_project" }
```

| Parameter | Type | Description |
|---|---|---|
| `path` | `str` | Absolute or relative path to the working directory |
| `browse` | `bool` | Optional. If `true`, opens an interactive folder selection dialog instead |

---

### browse_workspace

Opens the interactive folder picker dialog so the operator can choose the working directory at startup. *(Aliases: `browse_working_dir`, `select_workspace`, `select_working_dir`, `browse_directory`.)*

```json
{ "action": "browse_workspace" }
```

---

### load_pipeline

Loads a pipeline JSON file and reconstructs its node graph.

```json
{ "action": "load_pipeline", "path": "pipelines/my_pipeline.json" }
```

| Parameter | Type | Description |
|---|---|---|
| `path` | `str` | Relative to CWD or project root; absolute paths also accepted |

---

### fullscreen

Maximizes the viewport window.

```json
{ "action": "fullscreen", "enabled": true }
```

| Parameter | Type | Description |
|---|---|---|
| `enabled` | `bool` | `true` to maximize; currently only maximize is supported |

---

### wait

Inserts a sleep delay between steps.

```json
{ "action": "wait", "seconds": 2.0 }
```

| Parameter | Type | Description |
|---|---|---|
| `seconds` | `float` | Number of seconds to sleep |

!!! note
    The script runs in a **background thread**, so `wait` blocks that thread, not the UI.

---

### trigger

Calls a method on a specific active module instance.

```json
{
    "action": "trigger",
    "module_type": "MyCameraModule",
    "method": "start_acquisition",
    "params": { "fps": 30 }
}
```

You can target a module either by **type name** or by **UUID**:

| Parameter | Type | Description |
|---|---|---|
| `module_type` | `str` | Class name of the target module (first matching instance) |
| `module_uuid` | `str` | Exact UUID of the target instance (more precise) |
| `method` | `str` | Method name to call on the module |
| `params` | `dict` | Keyword arguments passed to the method |

---

### send_command

Sends a `CMD_DICT` typed data packet to a module's `input_cb`.

```json
{
    "action": "send_command",
    "module_uuid": "41680190-3cb8-4a57-b08f-287df5d1bf2b",
    "command": { "cmd": "set_gain", "value": 2.0 }
}
```

| Parameter | Type | Description |
|---|---|---|
| `module_uuid` | `str` | UUID of the target module |
| `command` | `dict` | The `CMD_DICT` payload passed to `input_cb` |

---

### apply_view

Applies a saved named view layout preset.

```json
{ "action": "apply_view", "view_name": "user" }
```

| Parameter | Type | Description |
|---|---|---|
| `view_name` | `str` | Name of the view to apply (e.g., `"user"`, `"advanced"`, or a custom name) |

---

## <u>Headless Execution Mode (`--headless`)</u>

Pipeline Creator can run pipelines and `AutomationManager` scripts completely **headless**—without opening a splash screen, OS viewport window, GPU/OpenGL swapchain, or interactive startup dialogs (`ModuleHealthManager`, crash recovery popup, `LoginDialog`, or `StartupDialog`).

This is useful for CI testing, server/background processing, batch pipelines, and benchmarking pure data-plane throughput without UI rendering overhead.

### How Headless Mode Works

When launched with `--headless`:

1. `main.py` skips the `tkinter` splash screen and calls `strip_headless_cli_flags()` + `run_headless_app()` (`core/headless_runner.py`) immediately after `dpg.create_context()`.
2. `install_headless_dpg_shims()` wraps DearPyGui viewport getters (returning a `1920x1080` virtual fallback) and viewport mutation calls as safe no-ops, and disables the `LogViewer` UI item sink so existing `WindowBase` and `ProcessingBase` modules can instantiate and communicate over `connections` / `LinkBus` without refactoring. Modules may also check `app_state.headless` to skip creating DPG widgets altogether.
3. `AutomationManager` runs the JSON script provided via `--script` and processes main-thread steps inside a lightweight polling loop instead of `dpg.render_dearpygui_frame()`.

### CLI Examples & Execution Flags

```bash
# Standard headless pipeline execution (auto-exits when script finishes)
python main.py --headless --exit-on-complete --script scripts/my_startup.json

# Fast benchmark-module discovery + safety watchdog timeout (60s)
python main.py --headless --benchmark-modules-only --exit-on-complete --max-seconds 60 --script benchmark/generated/scripts/headless_matrix_script.json
```

| Flag | Applies To | Description |
|---|---|---|
| `--headless` | `main.py` | Launches via `core/headless_runner.py` with `app_state.headless = True` (no viewport, no GPU render loop). |
| `--script <path>` | GUI & Headless | Path to an `AutomationManager` JSON script to execute on startup (mandatory for headless runs; exits immediately if omitted). Also sets `vsync=False` in GUI mode. |
| `--exit-on-complete` | Headless | Exits cleanly as soon as the automation script has finished (`automation_manager.has_finished`) and all queued main-thread tasks have drained. |
| `--benchmark-modules-only` | Headless | Fast discovery mode that loads only `modules/benchmark/` instead of scanning every module directory, reducing headless startup time for benchmarks and CI. |
| `--max-seconds <float>` | GUI & Headless | Safety watchdog timeout (in seconds) after which the main loop terminates cleanly (`0` = disabled). |

### Lifecycle & Clean Shutdown

A headless session terminates cleanly via any of the following triggers, running `clear_registry()` (stopping all background `ProcessingBase` workers) and `dpg.destroy_context()`:

- Script completion when `--exit-on-complete` is set.
- A module or script setting `app_state.close_requested = True` (e.g., `PerfPulseSource_win.request_exit`).
- Exceeding the `--max-seconds` timeout.
- User interrupt (`Ctrl+C` / `KeyboardInterrupt`).
