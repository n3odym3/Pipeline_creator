from __future__ import annotations

import ast
import importlib
import importlib.machinery
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, ValuesView

import dearpygui.dearpygui as dpg
from loguru import logger

from core.ast_utils import extract_imports
from core.module_constants import get_package_name
from core.paths import PROJECT_ROOT


class LocalPackagesFinder:
    def __init__(self, local_packages: set[str]) -> None:
        self.local_packages = local_packages

    def find_spec(
        self,
        fullname: str,
        path: Optional[List[str]] = None,
        target: Optional[Any] = None,
    ) -> Optional[importlib.machinery.ModuleSpec]:
        base_pkg = fullname.split(".")[0]
        if base_pkg not in self.local_packages:
            return None

        parts = fullname.split(".")

        if len(parts) == 1:
            search_paths = sys.path if path is None else path
            for entry in search_paths:
                if not entry:
                    continue
                potential_dir = os.path.join(entry, fullname)
                if os.path.isdir(potential_dir):
                    init_file = os.path.join(potential_dir, "__init__.py")
                    if os.path.isfile(init_file):
                        spec = importlib.util.spec_from_file_location(fullname, init_file)
                        if spec:
                            spec.submodule_search_locations = [potential_dir]
                            return spec

        else:
            submodule_name = parts[-1]
            if path:
                for entry in path:
                    if not entry:
                        continue
                    potential_dir = os.path.join(entry, submodule_name)
                    if os.path.isdir(potential_dir):
                        init_file = os.path.join(potential_dir, "__init__.py")
                        if os.path.isfile(init_file):
                            spec = importlib.util.spec_from_file_location(fullname, init_file)
                            if spec:
                                spec.submodule_search_locations = [potential_dir]
                                return spec

                    potential_file = os.path.join(entry, f"{submodule_name}.py")
                    if os.path.isfile(potential_file):
                        spec = importlib.util.spec_from_file_location(fullname, potential_file)
                        if spec:
                            return spec

        return None


class LazyModuleProxy:
    """Proxy object that acts as a class for modules and loads the real class on demand."""

    def __init__(self, module_name: str, file_path: str, class_name: str, description: str, doc: str) -> None:
        self._module_name = module_name
        self._file_path = file_path
        self._class_name = class_name
        self.description = description
        self.__doc__ = doc
        self._source_file = file_path
        self._real_class: Optional[Type[Any]] = None

    @property
    def __name__(self) -> str:
        return self._class_name

    def _load_real_class(self) -> Type[Any]:
        if self._real_class is None:
            cls = load_single_module_dynamic(self._module_name, self._file_path)
            if cls is None:
                raise ImportError(f"Could not load module {self._module_name}")
            self._real_class = cls
        return self._real_class

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        cls = self._load_real_class()
        return cls(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        cls = self._load_real_class()
        return getattr(cls, name)

    def __repr__(self) -> str:
        return f"<LazyModuleProxy for {self._class_name} in {self._module_name}>"


def load_single_module_dynamic(module_name: str, file_path: str) -> Any:
    """Dynamically loads a single module and returns its EXPORTED_CLASS."""
    full_path = Path(file_path)
    try:
        module_dir = str(full_path.parent)
        if module_dir not in sys.path:
            sys.path.insert(0, module_dir)

        parts = module_name.split(".")
        for i in range(1, len(parts)):
            parent_name = ".".join(parts[:i])
            if parent_name not in sys.modules:
                try:
                    importlib.import_module(parent_name)
                except Exception:
                    pass

        spec = importlib.util.spec_from_file_location(module_name, str(full_path))
        if spec is None or spec.loader is None:
            logger.warning(f"Could not load spec for {full_path}")
            return None

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            if module_name in sys.modules:
                del sys.modules[module_name]
            raise

        if hasattr(mod, "EXPORTED_CLASS"):
            cls = mod.EXPORTED_CLASS
            cls._source_file = str(full_path)
            return cls

    except Exception as e:
        logger.error(f"Failed to load module {module_name} on-demand: {e}")

    return None


def _get_module_metadata_statically(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Statically extract module EXPORTED_CLASS class name, description/EXPORTED_NAME,
    and class docstring. Returns None if EXPORTED_CLASS is not found.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=file_path)

        exported_class = None
        exported_name = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "EXPORTED_CLASS" and isinstance(node.value, ast.Name):
                            exported_class = node.value.id
                        elif target.id == "EXPORTED_NAME" and isinstance(node.value, (ast.Constant, ast.Str)):
                            if isinstance(node.value, ast.Constant):
                                exported_name = node.value.value
                            else:
                                exported_name = node.value.s

        if not exported_class:
            return None

        doc = ""
        description = exported_name
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == exported_class:
                doc = ast.get_docstring(node) or ""
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for t in item.targets:
                            if isinstance(t, ast.Name) and t.id in ("description", "DESCRIPTION"):
                                if isinstance(item.value, (ast.Constant, ast.Str)):
                                    if isinstance(item.value, ast.Constant):
                                        description = item.value.value
                                    else:
                                        description = item.value.s
                break

        return {
            "class_name": exported_class,
            "description": description or exported_class,
            "doc": doc,
        }
    except Exception as e:
        logger.debug(f"Failed to extract metadata statically from {file_path}: {e}")
        return None


# Global registry of module instances (UUID -> module instance)
MODULES_REGISTRY: Dict[str, Any] = {}
LAST_LOADED_WORKSPACE: Optional[str] = None
LAST_LOADED_SUBFLOW: Optional[str] = None
AVAILABLE_VIEWS: Dict[str, Any] = {}


def pump_dpg_frames(count: int = 2) -> None:
    """
    Pump a given number of DPG frames on the main thread so ImGui computes geometry,
    viewport dimensions, and widget bounding rects during initial layout loading.
    """
    if dpg.is_dearpygui_running():
        try:
            from core.automation_manager import automation_manager

            for _ in range(count):
                automation_manager.process_pending_steps()
                dpg.render_dearpygui_frame()
        except Exception as e:
            logger.debug(f"pump_dpg_frames skipped: {e}")


def register_module(module: Any) -> None:
    """Register a module instance in the global registry."""
    uuid = getattr(module, "UUID", None)
    if uuid and uuid not in MODULES_REGISTRY:
        MODULES_REGISTRY[uuid] = module


def unregister_module(module: Any) -> None:
    """Unregister a module and remove its references from other modules' connections."""
    uuid = getattr(module, "UUID", None)
    if uuid and uuid in MODULES_REGISTRY:
        del MODULES_REGISTRY[uuid]

    for other in MODULES_REGISTRY.values():
        for targets in getattr(other, "connections", {}).values():
            if module in targets:
                targets.remove(module)


def get_registered_modules() -> ValuesView[Any]:
    """Return the registered module instances."""
    return MODULES_REGISTRY.values()


def clear_registry() -> None:
    """Close all modules, clean up DPG items, and clear the registry."""
    global MODULES_REGISTRY, AVAILABLE_VIEWS, LAST_LOADED_WORKSPACE
    for module in list(MODULES_REGISTRY.values()):
        try:
            if callable(getattr(module, "close", None)):
                module.close()
            elif hasattr(module, "winID") and dpg.does_item_exist(module.winID):
                dpg.delete_item(module.winID)
        except Exception as e:
            logger.error(f"Failed to close module {getattr(module, 'label', module)}: {e}")

    MODULES_REGISTRY.clear()
    AVAILABLE_VIEWS.clear()
    LAST_LOADED_WORKSPACE = None

    try:
        from core.main_win import main_win
        if hasattr(main_win, "refresh_view_menu"):
            main_win.refresh_view_menu()
        if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "refresh_view_menu"):
            main_win.node_editor.refresh_view_menu()
    except Exception:
        pass


_AVAILABLE_MODULES_CACHE: Optional[Dict[str, Type[Any]]] = None


def get_available_modules(base_path: str = "modules", force_reload: bool = False) -> Dict[str, Type[Any]]:
    """
    Discover available module files and return a dict mapping module paths to classes.
    Only modules defining `EXPORTED_CLASS` are included.
    Automatically detects and reports missing dependencies using static analysis.
    Validates modules before loading to ensure they meet project requirements.
    """
    global _AVAILABLE_MODULES_CACHE
    if _AVAILABLE_MODULES_CACHE is not None and not force_reload:
        return _AVAILABLE_MODULES_CACHE

    from core.config_manager import config

    general_cfg = config.get("General", {})
    preload = general_cfg.get("preload_modules", True)
    skip_validation = general_cfg.get("skip_import_validation", False)
    if skip_validation:
        logger.warning(
            "Import validation is DISABLED (skip_import_validation=true). "
            "All modules will be loaded regardless of missing dependencies."
        )

    module_registry: Dict[str, Type[Any]] = {}
    missing_deps_detected: Dict[str, List[str]] = {}

    # Initialize validator

    try:
        from core.module_validation_manager import module_validation_manager
        validator = module_validation_manager.validator
    except ImportError:
        logger.warning("Could not import module validator, skipping validation")
        validator = None
        
    # Resolve base_path relative to project root if it's not absolute
    if not Path(base_path).is_absolute():
        search_path = PROJECT_ROOT / base_path
    if not search_path.exists():
        logger.warning(f"Modules path '{search_path}' does not exist")
        return {}

    # Scan requirements.txt files for missing package dependencies
    try:
        from core.dependency_manager import dependency_manager
        dependency_manager.check_all_modules(str(search_path), clear_existing=force_reload)
    except Exception as e:
        logger.warning(f"Could not check requirements.txt files: {e}")
    # Pre-add all module directories to sys.path so their local libraries are immediately importable.
    # We skip directories inside package folders to prevent polluting sys.path and causing import conflicts
    # (especially case-insensitivity issues on Windows).
    for full_path in search_path.rglob("*.py"):
        if full_path.name != "__init__.py":
            is_inside_package = False
            for parent in full_path.parents:
                if parent == search_path or parent == PROJECT_ROOT or not parent.is_relative_to(search_path):
                    break
                if (parent / "__init__.py").exists():
                    is_inside_package = True
                    break
            if is_inside_package:
                continue

            module_dir = str(full_path.parent)
            if module_dir not in sys.path:
                sys.path.insert(0, module_dir)

    # Register a custom meta path finder for local packages (directories with __init__.py).
    # This is critical for Nuitka-compiled builds: --nofollow-import-to excludes these
    # packages from compilation, but Nuitka's import hooks may block them at runtime.
    # By registering LocalPackagesFinder, we bypass Nuitka's import hooks entirely and load them on-demand.
    _local_packages: set = set()
    for init_path in search_path.rglob("__init__.py"):
        pkg_dir = init_path.parent
        pkg_name = pkg_dir.name
        if pkg_name != "__pycache__":
            _local_packages.add(pkg_name)
            
    # Register our custom finder at the beginning of sys.meta_path
    finder_exists = any(isinstance(f, LocalPackagesFinder) for f in sys.meta_path)
    if not finder_exists and _local_packages:
        sys.meta_path.insert(0, LocalPackagesFinder(_local_packages))
        logger.debug(f"Registered LocalPackagesFinder for packages: {', '.join(_local_packages)}")

    for full_path in search_path.rglob("*.py"):
        if full_path.name == "__init__.py":
            continue
            
        # Skip files that are inside a Python package directory (any subdirectory containing __init__.py)
        is_inside_package = False
        for parent in full_path.parents:
            if parent == search_path or parent == PROJECT_ROOT or not parent.is_relative_to(search_path):
                break
            if (parent / "__init__.py").exists():
                is_inside_package = True
                break
        if is_inside_package:
            continue

        try:
            from core.splash import update_splash_status
            update_splash_status(f"Loading: {full_path.stem}")
        except Exception:
            pass

        # Calculate relative path from PROJECT_ROOT to use as module name for proper relative imports
        try:
            try:
                rel_path = full_path.relative_to(PROJECT_ROOT)
            except ValueError:
                rel_path = full_path.relative_to(search_path)
            module_name = ".".join(rel_path.with_suffix("").parts)
            module_folder = full_path.parent.name
        except ValueError:
            continue

        # First, use static analysis to detect ALL missing imports (unless validation is skipped)
        if not skip_validation:
            missing_imports = _check_imports_statically(str(full_path))

            if missing_imports:
                # Retry: force-add this module's directory tree to sys.path and invalidate cache.
                # Skip subdirectories that are packages themselves (contain __init__.py) to prevent pollution.
                module_dir_path = full_path.parent
                dirs_to_add = [str(module_dir_path)]
                for sub in module_dir_path.iterdir():
                    if sub.is_dir() and sub.name != "__pycache__" and not (sub / "__init__.py").exists():
                        dirs_to_add.append(str(sub))
                for d in dirs_to_add:
                    if d not in sys.path:
                        sys.path.insert(0, d)
                # Invalidate cache entries for the missing packages so they get rechecked
                for pkg in missing_imports:
                    _import_check_cache.pop(pkg, None)
                    # Also try to invalidate by import name (get_package_name may have mapped it)
                    for cached_name, cached_val in list(_import_check_cache.items()):
                        if not cached_val and get_package_name(cached_name) in missing_imports:
                            _import_check_cache.pop(cached_name, None)

                missing_imports_retry = _check_imports_statically(str(full_path))
                if missing_imports_retry:
                    if module_folder not in missing_deps_detected:
                        missing_deps_detected[module_folder] = []
                    missing_deps_detected[module_folder].extend(missing_imports_retry)
                    logger.warning(f"Module '{module_name}' has missing packages: {', '.join(missing_imports_retry)}")
                    logger.debug(f"Skipping module '{module_name}' due to missing dependencies (after retry with local paths)")
                    continue
                else:
                    logger.info(f"Module '{module_name}' resolved missing packages after adding local paths to sys.path")

        if not preload:
            metadata = _get_module_metadata_statically(str(full_path))
            if not metadata:
                continue

            if validator:
                report = validator.validate_module(str(full_path))
                if not report.is_valid:
                    module_validation_manager.validation_issues[str(full_path)] = report
                    logger.warning(f"Module '{module_name}' failed validation and will not be loaded")
                    continue

            # Create lazy proxy
            lazy_proxy = LazyModuleProxy(
                module_name=module_name,
                file_path=str(full_path),
                class_name=metadata["class_name"],
                description=metadata["description"],
                doc=metadata["doc"]
            )
            module_registry[module_name] = lazy_proxy
            continue

        try:
            module_dir = str(full_path.parent)
            if module_dir not in sys.path:
                sys.path.insert(0, module_dir)

            # Local libraries are now importable because their parent directory is in sys.path

            # Ensure parent packages are loaded first to avoid circular import issues on relative imports
            parts = module_name.split(".")
            for i in range(1, len(parts)):
                parent_name = ".".join(parts[:i])
                if parent_name not in sys.modules:
                    try:
                        importlib.import_module(parent_name)
                    except Exception:
                        pass

            spec = importlib.util.spec_from_file_location(module_name, str(full_path))
            if spec is None or spec.loader is None:
                logger.warning(f"Could not load spec for {full_path}")
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            try:
                spec.loader.exec_module(mod)  # type: ignore
            except Exception:
                if module_name in sys.modules:
                    del sys.modules[module_name]
                raise

        except ImportError as e:
            # Fallback: extract missing package from runtime error
            missing_package = _extract_missing_package(str(e))
            if missing_package:
                if module_folder not in missing_deps_detected:
                    missing_deps_detected[module_folder] = []
                missing_deps_detected[module_folder].append(missing_package)
                logger.warning(f"Module '{module_name}' failed to load: missing package '{missing_package}'")
            else:
                logger.error(f"Failed to load module {module_name}: {e}")
            if not skip_validation:
                continue
            # When skip_validation is on, fall through to still try registering if EXPORTED_CLASS exists
            logger.warning(f"skip_import_validation is ON: attempting to register '{module_name}' despite ImportError")
            try:
                # Re-attempt with local path reinforcement
                module_dir = str(full_path.parent)
                if module_dir not in sys.path:
                    sys.path.insert(0, module_dir)
                for sub in full_path.parent.iterdir():
                    if sub.is_dir() and sub.name != "__pycache__" and not (sub / "__init__.py").exists() and str(sub) not in sys.path:
                        sys.path.insert(0, str(sub))
                spec = importlib.util.spec_from_file_location(module_name, str(full_path))
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
            except Exception as e2:
                logger.error(f"Module '{module_name}' still failed after retry: {e2}")
                if module_name in sys.modules:
                    del sys.modules[module_name]
                continue
        except Exception as e:
            logger.error(f"Failed to load module {module_name}: {e}")
            continue

        # Only validate and register if module has EXPORTED_CLASS
        if hasattr(mod, "EXPORTED_CLASS"):
            # Validate module AFTER confirming it's an actual module entry point
            if validator:
                report = validator.validate_module(str(full_path))
                if not report.is_valid:
                    # Store validation issues and skip registration
                    module_validation_manager.validation_issues[str(full_path)] = report
                    logger.warning(f"Module '{module_name}' failed validation and will not be loaded")
                    continue
            
            # Module passed validation (or validation disabled), add to registry
            cls = mod.EXPORTED_CLASS
            cls._source_file = str(full_path)
            module_registry[module_name] = cls
    
    # Report detected missing dependencies
    if missing_deps_detected:
        logger.info(f"Detected {len(missing_deps_detected)} module(s) with missing dependencies")
        _report_missing_dependencies(missing_deps_detected)
    
    logger.success(f"Loaded {len(module_registry)} modules successfully")

    _AVAILABLE_MODULES_CACHE = module_registry
    return module_registry

def _extract_missing_package(error_msg: str) -> Optional[str]:
    """
    Extract the package name from an ImportError message.

    Examples:
        "No module named 'cv2'" -> "opencv-python"
        "No module named 'PIL'" -> "pillow"
    """
    match = re.search(r"No module named ['\"]([^'\"]+)['\"]", error_msg)
    if match:
        module_name = match.group(1).split(".")[0]
        return get_package_name(module_name)

    return None


# Cache for import availability checks: module_name -> True (available) / False (missing)
_import_check_cache: Dict[str, bool] = {}


def _check_imports_statically(file_path: str) -> List[str]:
    """
    Use AST to extract all imports from a file and check which are missing.
    Results are cached per package to avoid redundant __import__() calls.

    Args:
        file_path: Path to the Python file to analyze.

    Returns:
        List of missing package names.
    """
    missing: List[str] = []

    imports = extract_imports(file_path, exclude_local=True)

    for module_name in imports:
        if sys.platform != "win32" and module_name in (
            "win32clipboard",
            "win32gui",
            "win32con",
            "win32api",
            "win32process",
            "pygrabber",
        ):
            continue

        if module_name not in _import_check_cache:
            try:
                exists = importlib.util.find_spec(module_name) is not None
                _import_check_cache[module_name] = exists
            except Exception:
                try:
                    __import__(module_name)
                    _import_check_cache[module_name] = True
                except ImportError:
                    _import_check_cache[module_name] = False

        if not _import_check_cache[module_name]:
            pkg_name = get_package_name(module_name)
            if pkg_name not in missing:
                missing.append(pkg_name)

    return missing


def _report_missing_dependencies(missing_deps: Dict[str, List[str]]) -> None:
    """
    Report missing dependencies to the dependency manager.
    This allows the UI dialog to show them for installation.
    """
    try:
        from core.dependency_manager import dependency_manager

        for module, packages in missing_deps.items():
            if module in dependency_manager.missing_deps:
                dependency_manager.missing_deps[module].extend(packages)
            else:
                dependency_manager.missing_deps[module] = packages

        logger.info(
            f"Detected {sum(len(p) for p in missing_deps.values())} missing dependencies "
            f"across {len(missing_deps)} modules"
        )
    except ImportError:
        logger.warning("Could not report missing dependencies: dependency_manager not available")


def export_workspace(
    instances: Optional[Any] = None,
    filepath: Optional[str] = "layout.json",
    node_positions: Optional[Dict[str, Tuple[float, float]]] = None,
    is_relative: bool = True,
) -> Dict[str, Any]:
    """Export the layout and connections of all modules to a JSON file and return the dict."""
    if instances is None:
        inst_list = list(get_registered_modules())
    elif isinstance(instances, dict):
        inst_list = list(instances.values())
    else:
        inst_list = list(instances)

    windows_data = []

    viewport_width = None
    viewport_height = None
    if is_relative:
        viewport_width = dpg.get_viewport_client_width()
        viewport_height = dpg.get_viewport_client_height()

    for win in inst_list:
        w_dict = win.serialize()
        if node_positions and hasattr(win, "UUID") and win.UUID in node_positions:
            w_dict["node_pos"] = node_positions[win.UUID]

        if getattr(win, "node_pinned", False):
            w_dict["pinned"] = True

        if is_relative and viewport_width and viewport_height:
            px, py = w_dict["pos"]
            w_dict["pos"] = [(px / viewport_width) * 100.0, (py / viewport_height) * 100.0]

            sx, sy = w_dict["size"]
            if sx != -1:
                sx = (sx / viewport_width) * 100.0
            if sy != -1:
                sy = (sy / viewport_height) * 100.0
            w_dict["size"] = [sx, sy]

        windows_data.append(w_dict)

    data = {
        "is_relative": is_relative,
        "windows": windows_data,
        "connections": [
            {"from": win.UUID, "output": out, "to": tgt.UUID}
            for win in inst_list
            for out, targets in getattr(win, "connections", {}).items()
            for tgt in targets
        ],
    }

    if filepath:
        if not filepath.lower().endswith(".json"):
            filepath += ".json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        logger.success(f"Workspace exported to {filepath}")

    return data



def load_workspace(
    filepath: str = "layout.json",
    module_registry: Optional[Dict[str, Type[Any]]] = None,
    start_cleaned: bool = True,
    only_position: bool = False
) -> Dict[str, Any]:
    """
    Load a workspace layout from a JSON file.
    If only_position is True, the function will only reposition existing windows
    based on the JSON without instantiating or destroying them.
    """
    global LAST_LOADED_WORKSPACE
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read workspace file {filepath}: {e}")
        return {}

    if only_position:
        result = load_positions_from_dict(data)
    else:
        result = load_from_dict(data, module_registry, start_cleaned=start_cleaned)
        try:
            from core.app_state import app_state
            apply_view(app_state.mode)
        except Exception as e:
            logger.warning(f"load_workspace: could not apply view after load: {e}")

    # Set LAST_LOADED_WORKSPACE AFTER load_from_dict/clear_registry so it is not wiped
    if not only_position:
        LAST_LOADED_WORKSPACE = str(Path(filepath).resolve())
        logger.info(f"Workspace loaded. Active pipeline set to: {LAST_LOADED_WORKSPACE}")

    return result

def load_positions_from_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update the positions and dimensions of already registered modules based on their UUIDs or class names.
    Does NOT instantiate or destroy any modules.
    """
    is_global_relative = data.get("is_relative", False)
    windows_data = data.get("windows", [])
    
    # Try to calculate viewports for relative conversions
    try:
        viewport_width = dpg.get_viewport_client_width()
        viewport_height = dpg.get_viewport_client_height()
    except Exception:
        viewport_width, viewport_height = None, None

    registered = list(get_registered_modules())
    
    for wdata in windows_data:
        uuid = wdata.get("uuid")
        mod_class_path = wdata.get("module")
        
        target_win = None
        for r in registered:
            if r.UUID == uuid:
                target_win = r
                break
                
        # Fallback to class matching if UUID not found
        if not target_win:
            for r in registered:
                r_mod = r.__class__.__module__
                if r_mod == mod_class_path or r_mod == f"modules.{mod_class_path}" or r_mod.replace("modules.", "") == mod_class_path:
                    target_win = r
                    break
                    
        if target_win and hasattr(target_win, "winID") and dpg.does_item_exist(target_win.winID):
            pos = list(wdata.get("pos", (10, 10)))
            win_size = list(wdata.get("size", [-1, -1]))
            is_relative = is_global_relative or wdata.get("is_relative", False)
            
            if is_relative and viewport_width and viewport_height:
                pos[0] = (pos[0] * viewport_width) / 100.0
                pos[1] = (pos[1] * viewport_height) / 100.0
                if win_size[0] != -1:
                    win_size[0] = (win_size[0] * viewport_width) / 100.0
                if win_size[1] != -1:
                    win_size[1] = (win_size[1] * viewport_height) / 100.0
                    
            try:
                new_pos = [int(pos[0]), int(pos[1])]
                new_w = int(win_size[0])
                new_h = int(win_size[1])
                
                dpg.set_item_pos(target_win.winID, new_pos)
                if new_w != -1:
                    dpg.set_item_width(target_win.winID, new_w)
                if new_h != -1:
                    dpg.set_item_height(target_win.winID, new_h)
                    
                target_win.pos = new_pos
                target_win.win_width = new_w
                target_win.win_height = new_h
                
                # Re-show the window if it was hidden
                dpg.show_item(target_win.winID)
                target_win.visible = True
            except Exception as e:
                logger.warning(f"Failed to reposition window {target_win.label}: {e}")

    logger.success("Workspace positions reloaded from dictionary")
    # Return the dictionary of updated instances just in case it's needed
    return {win.UUID: win for win in registered if getattr(win, "UUID", None)}


def load_from_dict(
    data: Dict[str, Any],
    module_registry: Optional[Dict[str, Type[Any]]] = None,
    start_cleaned: bool = True
) -> Dict[str, Any]:
    """
    Load a workspace layout from a dictionary and recreate modules, merges, and connections.
    Returns a dict mapping UUID → instance.
    
    Args:
        data: The dictionary containing 'windows' and 'connections' keys.
        module_registry: Optional dict mapping module names to classes.
        start_cleaned: If True, clears the existing workspace before loading.
    """
    if module_registry is None:
        module_registry = get_available_modules()

    if start_cleaned:
        clear_registry()
        
    is_global_relative = data.get("is_relative", False)
        
    instances: Dict[str, Any] = {}
    merge_requests: List[tuple[str, str]] = []
    
    # UUID Remapping for subflows
    uuid_map: Dict[str, str] = {}
    
    # Generate new UUIDs if not starting cleaned to avoid collisions
    if not start_cleaned:
        for wdata in data.get("windows", []):
            old_uuid = wdata["uuid"]
            new_uuid = str(dpg.generate_uuid())
            uuid_map[old_uuid] = new_uuid
            wdata["uuid"] = new_uuid
            
        # Update connection references
        for conn in data.get("connections", []):
            if conn["from"] in uuid_map:
                conn["from"] = uuid_map[conn["from"]]
            
            tgt_entry = conn.get("to")
            if isinstance(tgt_entry, list):
                conn["to"] = [uuid_map.get(uid, uid) for uid in tgt_entry]
            elif isinstance(tgt_entry, str) and tgt_entry in uuid_map:
                conn["to"] = uuid_map[tgt_entry]

        # Update merge references
        for wdata in data.get("windows", []):
             params = wdata.get("params", {})
             if "merged_into" in params and params["merged_into"] in uuid_map:
                 params["merged_into"] = uuid_map[params["merged_into"]]
                 
        # Update view references
        if "views" in data:
            for view_name, view_data in data["views"].items():
                for wdata in view_data.get("windows", []):
                    if wdata.get("uuid") in uuid_map:
                        wdata["uuid"] = uuid_map[wdata["uuid"]]

    # Recreate modules
    from core.window_base import WindowBase
    WindowBase._batch_loading = True
    for i, wdata in enumerate(data.get("windows", [])):
        module_key = wdata["module"]
        cls = module_registry.get(module_key)
        
        # Fallback for older layouts where "modules." might have been omitted
        if not cls and not module_key.startswith("modules."):
            cls = module_registry.get(f"modules.{module_key}")
            
        if not cls:
            msg = f"Unknown module in layout: {module_key}"
            logger.warning(msg)
            continue

        params = dict(wdata.get("params", {}))
        merge_target_uuid = params.pop("merged_into", None)

        pos = list(wdata.get("pos", (10, 10)))
        win_size = list(wdata.get("size", [-1, -1]))
        is_relative = is_global_relative or wdata.get("is_relative", False)
        
        if is_relative:
            try:
                viewport_width = dpg.get_viewport_client_width()
                viewport_height = dpg.get_viewport_client_height()
                if viewport_width and viewport_height:
                    pos[0] = (pos[0] * viewport_width) / 100.0
                    pos[1] = (pos[1] * viewport_height) / 100.0
                    if win_size[0] != -1:
                        win_size[0] = (win_size[0] * viewport_width) / 100.0
                    if win_size[1] != -1:
                        win_size[1] = (win_size[1] * viewport_height) / 100.0
            except Exception as e:
                logger.error(f"Failed to calculate relative position/size: {e}")

        try:
            win = cls(
                uuid=wdata["uuid"],
                pos=tuple(pos),
                win_width=int(win_size[0]),
                win_height=int(win_size[1]),
                visible=wdata.get("visible", True),
                **params,
            )
            win.is_relative = is_relative
            
            # Restore node position if available
            if "node_pos" in wdata:
                win.node_pos = tuple(wdata["node_pos"])
            
            # Restore pinned-to-menu-bar state
            win.node_pinned = wdata.get("pinned", False)
                
        except ImportError as e:
            logger.error(f"Failed to load module {wdata['module']}: missing dependency - {e}")
            
            # Clean up if partially initialized and registered
            uuid_to_fail = wdata["uuid"]
            if uuid_to_fail in MODULES_REGISTRY:
                try:
                    MODULES_REGISTRY[uuid_to_fail].close()
                except Exception as cleanup_err:
                    logger.error(f"Failed to cleanup failed instantiation of {wdata['module']}: {cleanup_err}")

            # Collect missing dependencies
            module_name = wdata.get('module', 'unknown').split('.')[-1]
            missing_package = _extract_missing_package(str(e))
            
            if missing_package:
                try:
                    from core.dependency_manager import dependency_manager
                    if module_name not in dependency_manager.missing_deps:
                        dependency_manager.missing_deps[module_name] = []
                    if missing_package not in dependency_manager.missing_deps[module_name]:
                        dependency_manager.missing_deps[module_name].append(missing_package)
                except ImportError:
                    pass
            
            continue
        except Exception as e:
            logger.error(f"Failed to instantiate {cls} with UUID {wdata['uuid']}: {e}")
            
            # Clean up if partially initialized and registered
            uuid_to_fail = wdata["uuid"]
            if uuid_to_fail in MODULES_REGISTRY:
                try:
                    MODULES_REGISTRY[uuid_to_fail].close()
                except Exception as cleanup_err:
                    logger.error(f"Failed to cleanup failed instantiation of {cls}: {cleanup_err}")
            continue

        register_module(win)
        instances[wdata["uuid"]] = win

        if merge_target_uuid:
            merge_requests.append((wdata["uuid"], merge_target_uuid))

        if dpg.does_item_exist(win.winID):
            dpg.set_item_pos(win.winID, win.pos)
            dpg.set_item_width(win.winID, win.win_width)
            dpg.set_item_height(win.winID, win.win_height)
            dpg.configure_item(win.winID, show=win.visible)
        
        # Permissions take precedence over JSON visibility
        if hasattr(win, "update_permission"):
            win.update_permission()

    WindowBase._batch_loading = False

    # Pump frames so ImGui evaluates real rect sizes of created windows before merging
    pump_dpg_frames(2)

    # Reapply merges
    for src_uuid, tgt_uuid in merge_requests:
        src, tgt = instances.get(src_uuid), instances.get(tgt_uuid)
        if src and tgt:
            src.merge_into(tgt)
    
    for conn in data.get("connections", []):
        src = instances.get(conn["from"])
        tgt_entry = conn.get("to")
        output_key = conn.get("output")

        if isinstance(tgt_entry, list):  # Legacy support
            for tgt_uuid in tgt_entry:
                if src and (tgt := instances.get(tgt_uuid)):
                    src.connect_to(tgt, output=0)
            continue

        if src and (tgt := instances.get(tgt_entry)) and output_key is not None:
            src.connect_to(tgt, output=output_key)

    # Final frame pump to settle layout geometry after merging
    pump_dpg_frames(1)

    # Load and register views if they exist in layout data
    views = dict(data.get("views", {}))
    views["Default"] = {
        "windows": data.get("windows", []),
        "is_relative": data.get("is_relative", False)
    }
    register_views(views, merge=(not start_cleaned))

    try:
        from core.main_win import main_win
        if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "refresh_view_menu"):
            main_win.node_editor.refresh_view_menu()
    except Exception:
        pass

    logger.success(f"Workspace loaded from dictionary ({len(instances)} modules)")
    return instances


def apply_view_data(view_data: dict) -> None:
    """
    Apply a view layout directly from a dictionary.
    Hides all registered windows, then shows and repositions only the listed ones.
    """
    # Step 1 – hide every registered window
    for win in get_registered_modules():
        if hasattr(win, "winID") and dpg.does_item_exist(win.winID):
            try:
                dpg.hide_item(win.winID)
                win.visible = False
            except Exception:
                pass

    # Step 2 – show and reposition only the listed windows
    load_positions_from_dict(view_data)



def register_views(views: Dict[str, Any], merge: bool = False) -> None:
    """Register views globally so they can be accessed by any module."""
    if merge:
        AVAILABLE_VIEWS.update(views)
    else:
        AVAILABLE_VIEWS.clear()
        AVAILABLE_VIEWS.update(views)

    try:
        from core.main_win import main_win
        if hasattr(main_win, "refresh_view_menu"):
            main_win.refresh_view_menu()
        if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "refresh_view_menu"):
            main_win.node_editor.refresh_view_menu()
    except Exception:
        pass


def get_available_views() -> Dict[str, Any]:
    """Get the currently registered available views."""
    return AVAILABLE_VIEWS


def apply_named_view(view_name: str) -> bool:
    """Apply a view by its name from the globally registered AVAILABLE_VIEWS."""
    if view_name in AVAILABLE_VIEWS:
        apply_view_data(AVAILABLE_VIEWS[view_name])
        logger.info(f"Applied view: {view_name}")
        return True
    
    logger.warning(f"View '{view_name}' not found in available views.")
    return False


def apply_view(mode: str) -> None:
    """
    Apply a secondary layout view for the given mode.

    Strategy:
      1. Hide ALL currently registered windows.
      2. Show and reposition ONLY the windows listed in ``views.<mode>``.

    This means the view JSON only needs to list visible windows.
    Unlisted windows are automatically hidden.

    Args:
        mode: The app mode key (``"user"``, ``"advanced"``, ...).
              If no matching view exists the call is a safe no-op.
    """
    if not LAST_LOADED_WORKSPACE:
        return

    try:
        with open(LAST_LOADED_WORKSPACE, encoding="utf-8") as f:
            layout_data = json.load(f)
    except Exception as e:
        logger.warning(f"apply_view: could not read last workspace: {e}")
        return

    views = layout_data.get("views", {})
    view = views.get(mode)
    if not view:
        logger.debug(f"apply_view: no view defined for mode='{mode}', keeping current layout")
        return

    apply_view_data(view)

    logger.info(f"Applied view layout for mode='{mode}' ({len(view.get('windows', []))} windows shown)")


def export_view(is_relative: bool = True) -> dict:
    """
    Capture the current positions and sizes of all **visible** registered
    windows and return them as a simplified view dictionary.

    Only visible windows are exported (the view contract is: listed = visible).
    Keys ``"visible"`` and ``"module"`` are omitted; only ``"uuid"``, ``"label"``,
    ``"pos"``, and ``"size"`` are kept for human readability.

    The returned dict can be inserted manually under
    ``layout_json["views"]["<mode>"]``.
    """
    viewport_width = None
    viewport_height = None
    if is_relative:
        try:
            viewport_width = dpg.get_viewport_client_width()
            viewport_height = dpg.get_viewport_client_height()
        except Exception:
            pass

    windows = []
    for win in get_registered_modules():
        if not hasattr(win, "winID") or not dpg.does_item_exist(win.winID):
            continue

        try:
            # Only export visible windows
            if not dpg.is_item_shown(win.winID):
                continue
            pos = dpg.get_item_pos(win.winID)
            w   = dpg.get_item_width(win.winID)
            h   = dpg.get_item_height(win.winID)
        except Exception:
            continue

        entry = {
            "uuid":  str(getattr(win, "UUID", "")),
            "label": getattr(win, "label", ""),
            "pos":   list(pos),
            "size":  [w, h],
        }

        if is_relative and viewport_width and viewport_height:
            entry["pos"]  = [(pos[0] / viewport_width) * 100.0,
                             (pos[1] / viewport_height) * 100.0]
            entry["size"] = [(w / viewport_width) * 100.0 if w != -1 else -1,
                             (h / viewport_height) * 100.0 if h != -1 else -1]

        windows.append(entry)

    return {"is_relative": is_relative, "windows": windows}
