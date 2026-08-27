# <u>Configuration Reference</u>

All application settings are stored in `config/config.json`. This file is loaded at startup and can be edited manually.


## <u>Full Schema</u>

```json
{
    "General": {
        "app_name": "Pipeline creator",
        "ui_scale_multiplier": 0.75,
        "preload_modules": true
    },
    "Splash": {
        "enabled": true,
        "image": "ressources/tutorial_assets/tutorial/Tutorial3.png",
        "size": 400,
        "thickness": 2
    },
    "Icon": {
        "path": "ressources/icon.ico"
    },
    "Login": {
        "enabled": false,
        "default_mode": "dev"
    },
    "UI": {
        "theme_name": "DEFAULT",
        "colorblind_type": "none",
        "mascot": true
    },
    "Debug": {
        "enabled": true,
        "log_level": "TRACE",
        "log_mkdocs": false
    },
    "Window": {
        "vsync": true,
        "minimize_to_tray": false,
        "show_console": true
    },
    "Paths": {
        "working_directory": "experiments",
        "default_pipeline": null,
        "default_automation_script": null,
        "show_loader_on_startup": true,
        "default_pipeline_folder": "layouts",
        "default_scripts_folder": "scripts"
    },
    "Cameras": {
        "IDS": {
            "rotation": 90
        },
        "Alvium": {
            "rotation": 90
        }
    }
}
```

## <u>Section Reference</u>

### General

| Key | Type | Default | Description |
|---|---|---|---|
| `app_name` | `str` | `"Pipeline Creator"` | The title displayed in the viewport title bar |
| `ui_scale_multiplier` | `float` | `1.0` | Global multiplier applied on top of DPI auto-scaling. Use `0.75` to reduce UI size on all screens. |
| `preload_modules` | `bool` | `true` | If `true`, pre-imports all library files for all modules on startup (fast module instantiation but slower app launch). If `false` (lazy mode), imports are deferred until module instantiation (fast app launch but slower first creation of a module). |

### Splash

Controls the animated splash screen shown while the application loads.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `true` | Show the splash screen on startup |
| `image` | `str` | — | Relative path to the splash image (PNG) |
| `size` | `int` | `400` | Size of the splash window in pixels |
| `thickness` | `int` | `2` | Border thickness of the splash ring animation |

### Icon

| Key | Type | Default | Description |
|---|---|---|---|
| `path` | `str` | `"ressources/icon.ico"` | Path to the application icon file (`.ico`) |

### Login

Controls the login/profile dialog shown at startup.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | If `true`, show the login dialog at startup before the main window appears |
| `default_mode` | `str` | `"user"` | Pre-selected privilege mode in the dialog. Options: `"user"`, `"advanced"`, `"dev"` |

!!! tip "Deploying to operators"
    Set `enabled: true` and `default_mode: "user"` to force a login on every launch. Users will only see controls appropriate to their selected mode.

### UI

| Key | Type | Default | Description |
|---|---|---|---|
| `theme_name` | `str` | `"DEFAULT"` | Name of the active color palette (must match a dict name in `theme_colors.py`) |
| `colorblind_type` | `str` | `"none"` | Colorblind adjustment: `"none"`, `"deuteranopia"`, `"protanopia"`, `"tritanopia"` |
| `mascot` | `bool` | `true` | Show the application mascot graphic |

### Debug

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Enable extended debug logging |
| `log_level` | `str` | `"INFO"` | Loguru log level. Options: `"TRACE"`, `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"` |
| `log_mkdocs` | `bool` | `false` | If `true`, redirect standard output and error streams of the MkDocs server subprocess to the main logger using log levels `DEBUG` and `WARNING`. |

### Window

| Key | Type | Default | Description |
|---|---|---|---|
| `vsync` | `bool` | `true` | Enable vertical sync in the DPG render loop (prevents tearing) |
| `minimize_to_tray` | `bool` | `false` | If `true`, a "Minimize to Tray" menu item appears in Tools |
| `show_console` | `bool` | `true` | If `false`, hides the terminal/console window at runtime for compiled executables |

### Paths

| Key | Type | Default | Description |
|---|---|---|---|
| `working_directory` | `str` | `"experiments"` | Default working directory set on startup (relative to project root) |
| `default_pipeline` | `str \| null` | `null` | Path to a `.json` flow file to load automatically at startup |
| `default_automation_script` | `str \| null` | `null` | Path to an automation `.json` script to run automatically at startup |

!!! note "Auto-loading a pipeline"
    Set `default_pipeline` to a relative path (e.g. `"layouts/my_flow.json"`) and Pipeline Creator will reconstruct that graph every time it starts, bypassing the need to manually load it.

### Cameras

This section is a good example showing that you can append other module-specific settings here. These settings are read directly by individual camera hardware modules.

| Key | Type | Default | Description |
|---|---|---|---|
| `IDS.rotation` | `int` | `90` | Image rotation angle in degrees (e.g., `0`, `90`, `180`, `270`) for IDS camera hardware modules. |
| `Alvium.rotation` | `int` | `90` | Image rotation angle in degrees (e.g., `0`, `90`, `180`, `270`) for Allied Vision Alvium camera hardware modules. |


## <u>Accessing Config in Code</u>

Use the `config` singleton to read values safely anywhere in the codebase:

```python
from config.config import config

app_name = config.get("General", {}).get("app_name", "Pipeline Creator")
log_level = config.get("Debug", {}).get("log_level", "INFO")
```

The loader applies fallback defaults if a key is missing, so it is safe to call `.get()` with a default value.
