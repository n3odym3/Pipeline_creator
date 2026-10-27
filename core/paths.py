from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional



def is_compiled() -> bool:
    """
    Return True if running as a compiled standalone executable (sys.frozen / Nuitka).
    """
    return bool(
        getattr(sys, "frozen", False)
        or "__compiled__" in globals()
        or "__compiled__" in sys.modules
        or hasattr(sys, "__nuitka_version__")
    )


IS_COMPILED: bool = is_compiled()
if IS_COMPILED and not getattr(sys, "frozen", False):
    sys.frozen = True


def get_project_root() -> Path:
    """
    Returns the absolute path to the project root.

    Handles two cases:
    1. Running from source: Root is the parent of the 'core' directory.
    2. Running as compiled executable (sys.frozen / Nuitka): Root is the folder
       containing the executable (sys.executable).
    """
    if is_compiled():
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent.parent.resolve()


PROJECT_ROOT: Path = get_project_root()

MODULES_DIR: Path = PROJECT_ROOT / "modules"
PIPELINES_DIR: Path = PROJECT_ROOT / "pipelines"
SCRIPTS_DIR: Path = PROJECT_ROOT / "scripts"
CONFIG_DIR: Path = PROJECT_ROOT / "config"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
RESSOURCES_DIR: Path = PROJECT_ROOT / "ressources"
TUTORIALS_DIR: Path = PROJECT_ROOT / "tutorials"

def load_external_script(module_name: str, file_path: Path) -> Optional[Any]:
    """
    Dynamically loads an external Python script into sys.modules and binds it
    to its parent package in sys.modules if applicable.

    Useful in compiled builds (Nuitka / PyInstaller) where external, user-modifiable
    scripts (like core.input_output_types or custom plugins) need to be injected at runtime.

    Args:
        module_name: Full import name (e.g. "core.input_output_types").
        file_path: Path to the .py file to load.

    Returns:
        The loaded module object, or None if the file doesn't exist or loading failed.
    """
    if not file_path.exists() or not file_path.is_file():
        return None

    try:
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)

            # Bind attribute on parent package if parent is already in sys.modules
            if "." in module_name:
                parent_pkg_name, attr_name = module_name.rsplit(".", 1)
                if parent_pkg_name in sys.modules:
                    setattr(sys.modules[parent_pkg_name], attr_name, mod)

            return mod
    except Exception:
        pass

    return None

# Force package path extension for compiled versions (Nuitka / PyInstaller)
if getattr(sys, "frozen", False) or "__compiled__" in globals():
    load_external_script("core.input_output_types", PROJECT_ROOT / "core" / "input_output_types.py")



