from __future__ import annotations

import ast
import importlib
import importlib.machinery
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, ValuesView

import dearpygui.dearpygui as dpg
from loguru import logger

from core.ast_utils import extract_imports, extract_imports_from_tree
from core.health_cache import health_check_cache, normalize_pkg_name
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

        # Case 1: Top-level import (e.g. "pygrabber")
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

        # Case 2: Submodule import (e.g. "pygrabber.dshow_graph")
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


def _get_module_metadata_statically(
    file_path: str,
    use_cache: bool = True,
    source: Optional[str] = None,
    tree: Optional[ast.Module] = None,
) -> Optional[Dict[str, Any]]:
    """
    Statically extract module EXPORTED_CLASS class name, description/EXPORTED_NAME,
    and class docstring. Returns None if EXPORTED_CLASS is not found.
    """
    if use_cache:
        entry = health_check_cache.get_file_entry(file_path)
        if entry is not None:
            if entry.get("has_exported_class") is False:
                return None
            if isinstance(entry.get("metadata"), dict):
                return dict(entry["metadata"])

    try:
        if tree is None:
            if source is None:
                with open(file_path, "r", encoding="utf-8") as f:
                    source = f.read()
            if "EXPORTED_CLASS" not in source:
                health_check_cache.update_file_entry(file_path, has_exported_class=False)
                return None
            tree = ast.parse(source, filename=file_path)

        exported_class = None
        exported_name = ""
        # Check top-level body first (fast O(top_level_stmts) instead of full ast.walk)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "EXPORTED_CLASS" and isinstance(node.value, ast.Name):
                            exported_class = node.value.id
                        elif target.id == "EXPORTED_NAME" and isinstance(node.value, ast.Constant):
                            exported_name = str(node.value.value)

        if not exported_class:
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            if target.id == "EXPORTED_CLASS" and isinstance(node.value, ast.Name):
                                exported_class = node.value.id
                            elif target.id == "EXPORTED_NAME" and isinstance(node.value, ast.Constant):
                                exported_name = str(node.value.value)

        if not exported_class:
            health_check_cache.update_file_entry(file_path, has_exported_class=False)
            return None

        doc = ""
        description = exported_name
        class_candidates = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == exported_class]
        if not class_candidates:
            class_candidates = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == exported_class]

        for node in class_candidates:
            doc = ast.get_docstring(node) or ""
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name) and t.id in ("description", "DESCRIPTION"):
                            if isinstance(item.value, ast.Constant):
                                description = str(item.value.value)
            break

        meta = {
            "class_name": exported_class,
            "description": description or exported_class,
            "doc": doc,
        }
        health_check_cache.update_file_entry(file_path, has_exported_class=True, metadata=meta)
        return meta
    except Exception as e:
        logger.debug(f"Failed to extract metadata statically from {file_path}: {e}")
        return None


def _discover_module_files(search_path: Path) -> Tuple[List[Path], List[Path]]:
    """
    Single-pass discovery of candidate module .py files and requirements.txt files,
    while registering local packages and adding module directories to sys.path.
    Uses os.walk with in-place directory pruning for maximum speed on Windows/OneDrive.
    """
    candidate_py: List[Path] = []
    req_files: List[Path] = []
    local_packages: Set[str] = set()
    sys_path_set = set(sys.path)
    dirs_to_prepend: List[str] = []

    for root, dirs, files in os.walk(search_path):
        # Prune __pycache__ and hidden directories immediately
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]

        root_path = Path(root)
        file_set = set(files)

        # If this directory is a Python package (contains __init__.py) and is not search_path itself:
        if "__init__.py" in file_set and root_path != search_path:
            local_packages.add(root_path.name)
            # Still check if requirements.txt exists inside, then prune package sub-tree from module scan
            if "requirements.txt" in file_set:
                req_files.append(root_path / "requirements.txt")
            dirs[:] = []
            continue

        if "requirements.txt" in file_set:
            req_files.append(root_path / "requirements.txt")

        has_py = False
        for fname in files:
            if fname.endswith(".py") and fname != "__init__.py":
                candidate_py.append(root_path / fname)
                has_py = True

        if has_py:
            mod_dir = str(root_path)
            if mod_dir not in sys_path_set:
                sys_path_set.add(mod_dir)
                dirs_to_prepend.append(mod_dir)

            # Also add non-package immediate subdirectories so local sibling helpers resolve cleanly
            for d in dirs:
                sub_path = root_path / d
                if not (sub_path / "__init__.py").exists():
                    sub_str = str(sub_path)
                    if sub_str not in sys_path_set:
                        sys_path_set.add(sub_str)
                        dirs_to_prepend.append(sub_str)

    for d in reversed(dirs_to_prepend):
        sys.path.insert(0, d)

    # Register LocalPackagesFinder once
    finder_exists = any(isinstance(f, LocalPackagesFinder) for f in sys.meta_path)
    if not finder_exists and local_packages:
        sys.meta_path.insert(0, LocalPackagesFinder(local_packages))
        logger.debug(f"Registered LocalPackagesFinder for packages: {', '.join(local_packages)}")
    elif finder_exists and local_packages:
        for f in sys.meta_path:
            if isinstance(f, LocalPackagesFinder):
                f.local_packages.update(local_packages)

    return candidate_py, req_files


# Global registry of module instances (UUID -> module instance)
MODULES_REGISTRY: Dict[str, Any] = {}
LAST_LOADED_WORKSPACE: Optional[str] = None
LAST_LOADED_SUBFLOW: Optional[str] = None
AVAILABLE_VIEWS: Dict[str, Any] = {}


_IN_FRAME_PUMP: bool = False


def pump_dpg_frames(count: int = 2) -> None:
    """
    Pump a given number of DPG frames on the main thread so ImGui computes geometry,
    viewport dimensions, and widget bounding rects during initial pipeline loading.
    """
    global _IN_FRAME_PUMP
    if _IN_FRAME_PUMP:
        logger.debug("pump_dpg_frames skipped: recursive frame render avoided")
        return

    if dpg.is_dearpygui_running():
        _IN_FRAME_PUMP = True
        try:
            from core.automation_manager import automation_manager

            for _ in range(count):
                automation_manager.process_pending_steps()
                dpg.render_dearpygui_frame()
        except Exception as e:
            logger.debug(f"pump_dpg_frames skipped: {e}")
        finally:
            _IN_FRAME_PUMP = False


def register_module(module: Any) -> None:
    """Register a module instance in the global registry."""
    uuid = getattr(module, "UUID", None)
    if uuid and uuid not in MODULES_REGISTRY:
        MODULES_REGISTRY[uuid] = module
    try:
        from core.data_stream_interceptor import data_stream_interceptor

        data_stream_interceptor.hook_module(module)
    except Exception:
        pass


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
        from core.tutorial_manager import tutorial_manager
        tutorial_manager._update_step_toolbar_state()
    except Exception:
        pass


_AVAILABLE_MODULES_CACHE: Optional[Dict[str, Type[Any]]] = None


def get_available_modules(
    base_path: str = "modules",
    force_reload: bool = False,
    force_validation: bool = False,
    use_cache: bool = True,
) -> Dict[str, Type[Any]]:
    """
    Discover available module files and return a dict mapping module paths to classes.
    Only modules defining `EXPORTED_CLASS` are included.
    Automatically detects and reports missing dependencies using static analysis.
    Validates modules before loading to ensure they meet project requirements.

    Args:
        base_path: Directory containing modules.
        force_reload: Reload module registry instead of returning in-memory cache.
        force_validation: If True, forces full dependency and code validation even if
                         `skip_import_validation` is enabled in config.json (used for manual checks).
        use_cache: If True, uses the persistent disk cache (.cache/health_check_cache.json)
                   for unmodified files.
    """
    global _AVAILABLE_MODULES_CACHE
    if _AVAILABLE_MODULES_CACHE is not None and not force_reload:
        return _AVAILABLE_MODULES_CACHE

    from core.config_manager import config

    general_cfg = config.get("General", {})
    preload = general_cfg.get("preload_modules", True)
    skip_validation = general_cfg.get("skip_import_validation", False) and not force_validation
    if skip_validation:
        logger.info(
            "Startup health check is DISABLED (skip_import_validation=true). "
            "Skipping requirements.txt scan, import checks, and AST validation."
        )

    module_registry: Dict[str, Type[Any]] = {}
    missing_deps_detected: Dict[str, List[str]] = {}

    validator = None
    if not skip_validation:
        try:
            from core.module_validation_manager import module_validation_manager
            validator = module_validation_manager.validator
        except ImportError:
            logger.warning("Could not import module validator, skipping validation")
            validator = None

    # Resolve base_path relative to project root if it's not absolute
    if not Path(base_path).is_absolute():
        search_path = PROJECT_ROOT / base_path
    else:
        search_path = Path(base_path)
    if not search_path.exists():
        logger.warning(f"Modules path '{search_path}' does not exist")
        return {}

    candidate_py, req_files = _discover_module_files(search_path)

    # Scan requirements.txt files for missing package dependencies (only when validation is enabled)
    if not skip_validation:
        try:
            from core.dependency_manager import dependency_manager
            from core.paths import is_compiled
            if not is_compiled():
                dependency_manager.check_all_modules(
                    str(search_path),
                    clear_existing=force_reload,
                    use_cache=use_cache,
                    req_files=req_files,
                )
        except Exception as e:
            logger.warning(f"Could not check requirements.txt files: {e}")

    for full_path in candidate_py:
        try:
            from core.splash import update_splash_status
            update_splash_status(f"Loading: {full_path.stem}")
        except Exception:
            pass

        try:
            try:
                rel_path = full_path.relative_to(PROJECT_ROOT)
            except ValueError:
                rel_path = full_path.relative_to(search_path)
            module_name = ".".join(rel_path.with_suffix("").parts)
            module_folder = full_path.parent.name
        except ValueError:
            continue

        source: Optional[str] = None
        tree: Optional[ast.Module] = None

        # First, use static analysis to detect ALL missing imports (unless validation is skipped)
        if not skip_validation:
            missing_imports, source, tree = _check_imports_statically_with_ast(
                str(full_path),
                use_cache=use_cache,
                source=source,
                tree=tree,
            )

            if missing_imports:
                from core.paths import is_compiled
                if is_compiled():
                    logger.info(
                        f"Module '{module_name}' ignored (incompatible: missing dependencies in compiled mode: "
                        f"{', '.join(missing_imports)})"
                    )
                else:
                    if module_folder not in missing_deps_detected:
                        missing_deps_detected[module_folder] = []
                    for pkg in missing_imports:
                        if pkg not in missing_deps_detected[module_folder]:
                            missing_deps_detected[module_folder].append(pkg)
                    logger.warning(f"Module '{module_name}' has missing packages: {', '.join(missing_imports)}")
                continue

        if not preload:
            metadata = _get_module_metadata_statically(
                str(full_path),
                use_cache=use_cache,
                source=source,
                tree=tree,
            )
            if not metadata:
                continue

            if validator:
                report = validator.validate_module(
                    str(full_path),
                    use_cache=use_cache,
                    source=source,
                    tree=tree,
                )
                if report.has_issues:
                    module_validation_manager.validation_issues[str(full_path)] = report
                if not report.is_valid:
                    logger.warning(f"Module '{module_name}' failed validation and will not be loaded")
                    continue

            # Create lazy proxy
            lazy_proxy = LazyModuleProxy(
                module_name=module_name,
                file_path=str(full_path),
                class_name=metadata["class_name"],
                description=metadata["description"],
                doc=metadata["doc"],
            )
            module_registry[module_name] = lazy_proxy
            continue

        # Fast skip for non-module helper files before executing them when cached
        if use_cache:
            cached_entry = health_check_cache.get_file_entry(full_path)
            if cached_entry is not None and cached_entry.get("has_exported_class") is False:
                continue

        try:
            module_dir = str(full_path.parent)
            if module_dir not in sys.path:
                sys.path.insert(0, module_dir)

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
            missing_package = _extract_missing_package(str(e))
            if missing_package:
                from core.paths import is_compiled
                if is_compiled():
                    logger.info(
                        f"Module '{module_name}' ignored (incompatible: missing package '{missing_package}' in compiled mode)"
                    )
                else:
                    if module_folder not in missing_deps_detected:
                        missing_deps_detected[module_folder] = []
                    if missing_package not in missing_deps_detected[module_folder]:
                        missing_deps_detected[module_folder].append(missing_package)
                    logger.warning(f"Module '{module_name}' failed to load: missing package '{missing_package}'")
            else:
                logger.error(f"Failed to load module {module_name}: {e}")
                if not skip_validation:
                    from core.module_validation_manager import ValidationReport, module_validation_manager
                    rep = module_validation_manager.validation_issues.get(str(full_path))
                    if rep is None:
                        rep = ValidationReport(module_path=str(full_path), is_valid=False)
                        module_validation_manager.validation_issues[str(full_path)] = rep
                    rep.add_error(f"ImportError during load: {e}", category="runtime")
            if not skip_validation:
                continue
            logger.warning(f"skip_import_validation is ON: attempting to register '{module_name}' despite ImportError")
            try:
                module_dir = str(full_path.parent)
                if module_dir not in sys.path:
                    sys.path.insert(0, module_dir)
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
            if not skip_validation:
                from core.module_validation_manager import ValidationReport, module_validation_manager
                rep = module_validation_manager.validation_issues.get(str(full_path))
                if rep is None:
                    rep = ValidationReport(module_path=str(full_path), is_valid=False)
                    module_validation_manager.validation_issues[str(full_path)] = rep
                rep.add_error(f"Runtime loading exception: {e}", category="runtime")
            continue

        # Only validate and register if module has EXPORTED_CLASS
        if hasattr(mod, "EXPORTED_CLASS"):
            if validator:
                report = validator.validate_module(
                    str(full_path),
                    use_cache=use_cache,
                    source=source,
                    tree=tree,
                )
                if report.has_issues:
                    module_validation_manager.validation_issues[str(full_path)] = report
                if not report.is_valid:
                    logger.warning(f"Module '{module_name}' failed validation and will not be loaded")
                    continue
            else:
                health_check_cache.update_file_entry(full_path, has_exported_class=True)

            cls = mod.EXPORTED_CLASS
            cls._source_file = str(full_path)
            module_registry[module_name] = cls
        else:
            health_check_cache.update_file_entry(full_path, has_exported_class=False)

    # Report detected missing dependencies
    from core.paths import is_compiled
    if not skip_validation and missing_deps_detected and not is_compiled():
        logger.info(f"Detected {len(missing_deps_detected)} module(s) with missing dependencies")
        _report_missing_dependencies(missing_deps_detected)

    health_check_cache.save()
    logger.success(f"Loaded {len(module_registry)} modules successfully")

    _AVAILABLE_MODULES_CACHE = module_registry
    return module_registry


def scan_modules_health(
    base_path: str = "modules",
    use_cache: bool = True,
    progress_callback: Optional[Callable[[str, int, int, int, int], None]] = None,
) -> Dict[str, Any]:
    """
    Perform a complete health check (dependencies, static imports, and AST/callback validation)
    across all modules, regardless of `skip_import_validation` in config.json.

    Does NOT re-execute `exec_module` on already-loaded modules, making it fast and safe
    to run from a background thread with real-time UI progress updates.

    Args:
        base_path: Directory containing modules.
        use_cache: If True, reuses cached results from `.cache/health_check_cache.json`
                   for unmodified files. If False, clears and rebuilds the cache.
        progress_callback: Optional callable(current_label, step_idx, total_steps, scanned_count, cached_count).

    Returns:
        Dictionary with scan statistics: `total_files`, `scanned`, `cached`, `elapsed_ms`.
    """
    global _AVAILABLE_MODULES_CACHE
    t0 = time.perf_counter()

    from core.dependency_manager import dependency_manager
    from core.module_validation_manager import module_validation_manager
    from core.paths import is_compiled

    if not use_cache:
        health_check_cache.clear()
        _import_check_cache.clear()

    dependency_manager.missing_deps.clear()
    module_validation_manager.validation_issues.clear()

    if not Path(base_path).is_absolute():
        search_path = PROJECT_ROOT / base_path
    else:
        search_path = Path(base_path)

    if not search_path.exists():
        return {"total_files": 0, "scanned": 0, "cached": 0, "elapsed_ms": 0.0}

    candidate_py, req_files = _discover_module_files(search_path)
    compiled = is_compiled()

    total_steps = (len(req_files) if not compiled else 0) + len(candidate_py)
    current_step = 0
    scanned_total = 0
    cached_total = 0

    # Step 1: Check all requirements.txt files
    if not compiled and req_files:
        def _req_progress(item_name: str, idx: int, tot: int, from_cache: bool) -> None:
            nonlocal current_step, scanned_total, cached_total
            current_step += 1
            if from_cache:
                cached_total += 1
            else:
                scanned_total += 1
            if progress_callback:
                progress_callback(f"Dependencies: {item_name}", current_step, total_steps, scanned_total, cached_total)

        dependency_manager.check_all_modules(
            str(search_path),
            clear_existing=False,
            use_cache=use_cache,
            req_files=req_files,
            progress_callback=_req_progress,
        )

    # Step 2: Check static imports and validate all candidate .py files
    missing_deps_detected: Dict[str, List[str]] = {}
    validator = module_validation_manager.validator

    for full_path in candidate_py:
        current_step += 1
        try:
            try:
                rel_path = full_path.relative_to(PROJECT_ROOT)
            except ValueError:
                rel_path = full_path.relative_to(search_path)
            module_name = ".".join(rel_path.with_suffix("").parts)
            module_folder = full_path.parent.name
        except ValueError:
            continue

        file_was_cached = False
        entry = health_check_cache.get_file_entry(full_path) if use_cache else None
        env_fp = health_check_cache.get_env_fingerprint()
        val_fp = health_check_cache.get_validator_fingerprint()

        imports_from_cache = (
            entry is not None
            and entry.get("env_fingerprint") == env_fp
            and isinstance(entry.get("missing_imports"), list)
        )

        # Fast-path: complete cache hit for this file (0 extra stat() or dict lookups)
        if imports_from_cache and entry is not None:
            missing_imports = list(entry["missing_imports"])
            if missing_imports and not compiled:
                if module_folder not in missing_deps_detected:
                    missing_deps_detected[module_folder] = []
                for pkg in missing_imports:
                    if pkg not in missing_deps_detected[module_folder]:
                        missing_deps_detected[module_folder].append(pkg)

            if entry.get("has_exported_class") is False:
                file_was_cached = True
            elif (
                isinstance(entry.get("metadata"), dict)
                and entry.get("validator_fingerprint") == val_fp
                and isinstance(entry.get("validation_report"), dict)
            ):
                from core.module_validation_manager import ValidationReport
                metadata = entry["metadata"]
                report = ValidationReport.from_dict(str(full_path), entry["validation_report"], from_cache=True)
                file_was_cached = True
                if report.has_issues:
                    module_validation_manager.validation_issues[str(full_path)] = report
                if _AVAILABLE_MODULES_CACHE is not None and report.is_valid and not missing_imports:
                    if module_name not in _AVAILABLE_MODULES_CACHE:
                        _AVAILABLE_MODULES_CACHE[module_name] = LazyModuleProxy(
                            module_name=module_name,
                            file_path=str(full_path),
                            class_name=metadata["class_name"],
                            description=metadata["description"],
                            doc=metadata["doc"],
                        )

        if not file_was_cached:
            source: Optional[str] = None
            tree: Optional[ast.Module] = None

            missing_imports, source, tree = _check_imports_statically_with_ast(
                str(full_path),
                use_cache=use_cache,
                source=source,
                tree=tree,
            )

            if missing_imports and not compiled:
                if module_folder not in missing_deps_detected:
                    missing_deps_detected[module_folder] = []
                for pkg in missing_imports:
                    if pkg not in missing_deps_detected[module_folder]:
                        missing_deps_detected[module_folder].append(pkg)

            metadata = _get_module_metadata_statically(
                str(full_path),
                use_cache=use_cache,
                source=source,
                tree=tree,
            )

            if metadata:
                report = validator.validate_module(
                    str(full_path),
                    use_cache=use_cache,
                    source=source,
                    tree=tree,
                )
                if report.has_issues:
                    module_validation_manager.validation_issues[str(full_path)] = report

                if _AVAILABLE_MODULES_CACHE is not None and report.is_valid and not missing_imports:
                    if module_name not in _AVAILABLE_MODULES_CACHE:
                        _AVAILABLE_MODULES_CACHE[module_name] = LazyModuleProxy(
                            module_name=module_name,
                            file_path=str(full_path),
                            class_name=metadata["class_name"],
                            description=metadata["description"],
                            doc=metadata["doc"],
                        )

        if file_was_cached:
            cached_total += 1
        else:
            scanned_total += 1

        if progress_callback:
            tag_str = "cached" if file_was_cached else "scanned"
            progress_callback(
                f"Module: {full_path.name} ({tag_str})",
                current_step,
                total_steps,
                scanned_total,
                cached_total,
            )

    if missing_deps_detected and not compiled:
        _report_missing_dependencies(missing_deps_detected)

    health_check_cache.save()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        f"Health check completed in {elapsed_ms:.1f} ms "
        f"(total={total_steps}, scanned={scanned_total}, cached={cached_total})"
    )
    return {
        "total_files": total_steps,
        "scanned": scanned_total,
        "cached": cached_total,
        "elapsed_ms": elapsed_ms,
    }


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


def _is_module_importable(module_name: str) -> bool:
    """Fast check if a top-level module name is importable without executing heavy packages."""
    if module_name in _import_check_cache:
        return _import_check_cache[module_name]

    if module_name in sys.modules or module_name in sys.builtin_module_names:
        _import_check_cache[module_name] = True
        return True

    try:
        exists = importlib.util.find_spec(module_name) is not None
        if exists:
            _import_check_cache[module_name] = True
            return True
    except Exception:
        pass

    # Fallback: check if mapped package distribution is installed in metadata
    pkg_name = get_package_name(module_name)
    installed_dists = health_check_cache.get_installed_packages()
    if normalize_pkg_name(pkg_name) in installed_dists or normalize_pkg_name(module_name) in installed_dists:
        _import_check_cache[module_name] = True
        return True

    _import_check_cache[module_name] = False
    return False


def _check_imports_statically_with_ast(
    file_path: str,
    use_cache: bool = True,
    source: Optional[str] = None,
    tree: Optional[ast.Module] = None,
) -> Tuple[List[str], Optional[str], Optional[ast.Module]]:
    """
    Check all imports in `file_path`, reusing or returning `(source, tree)` and leveraging
    `health_check_cache` when unmodified.
    """
    entry = health_check_cache.get_file_entry(file_path) if use_cache else None
    env_fp = health_check_cache.get_env_fingerprint()

    if (
        entry is not None
        and entry.get("env_fingerprint") == env_fp
        and isinstance(entry.get("missing_imports"), list)
    ):
        return list(entry["missing_imports"]), source, tree

    imports: Optional[List[str]] = None
    if entry is not None and isinstance(entry.get("imports"), list):
        imports = entry["imports"]
    else:
        try:
            if tree is None:
                if source is None:
                    with open(file_path, "r", encoding="utf-8") as f:
                        source = f.read()
                tree = ast.parse(source, filename=file_path)
            imports = sorted(extract_imports_from_tree(tree, file_path, exclude_local=True))
        except Exception as e:
            logger.debug(f"Could not parse {file_path} for import extraction: {e}")
            imports = []

    missing: List[str] = []
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

        if not _is_module_importable(module_name):
            pkg_name = get_package_name(module_name)
            if pkg_name not in missing:
                missing.append(pkg_name)

    health_check_cache.update_file_entry(
        file_path,
        imports=imports,
        missing_imports=missing,
    )
    return missing, source, tree


def _check_imports_statically(file_path: str, use_cache: bool = True) -> List[str]:
    """
    Use AST to extract all imports from a file and check which are missing.
    Results are cached per file and per package.

    Args:
        file_path: Path to the Python file to analyze.
        use_cache: Whether to use the persistent health check cache.

    Returns:
        List of missing package names.
    """
    missing, _, _ = _check_imports_statically_with_ast(file_path, use_cache=use_cache)
    return missing


def _report_missing_dependencies(missing_deps: Dict[str, List[str]]) -> None:
    """
    Report missing dependencies to the dependency manager.
    This allows the UI dialog to show them for installation.
    """
    from core.paths import is_compiled
    if is_compiled():
        return

    try:
        from core.dependency_manager import dependency_manager

        for module, packages in missing_deps.items():
            if module in dependency_manager.missing_deps:
                for p in packages:
                    if p not in dependency_manager.missing_deps[module]:
                        dependency_manager.missing_deps[module].append(p)
            else:
                dependency_manager.missing_deps[module] = list(packages)

        logger.info(
            f"Detected {sum(len(p) for p in missing_deps.values())} missing dependencies "
            f"across {len(missing_deps)} modules"
        )
    except ImportError:
        logger.warning("Could not report missing dependencies: dependency_manager not available")


def export_workspace(
    instances: Optional[Any] = None,
    filepath: Optional[str] = "pipeline.json",
    node_positions: Optional[Dict[str, Tuple[float, float]]] = None,
    is_relative: bool = True,
) -> Dict[str, Any]:
    """Export the pipeline and connections of all modules to a JSON file and return the dict."""
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
        if not str(filepath).lower().endswith(".json"):
            filepath = f"{filepath}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        logger.success(f"Workspace exported to {filepath}")

    return data



def load_workspace(
    filepath: str = "pipeline.json",
    module_registry: Optional[Dict[str, Type[Any]]] = None,
    start_cleaned: bool = True,
    only_position: bool = False
) -> Dict[str, Any]:
    """
    Load a workspace pipeline from a JSON file.
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

                # Apply element-level visibility overrides from view
                if "hidden_items" in wdata and wdata["hidden_items"]:
                    try:
                        from core.module_item_inspector import ModuleItemInspector
                        ModuleItemInspector.apply_hidden_items(target_win, wdata["hidden_items"])
                    except Exception as e:
                        logger.debug(f"Failed to restore hidden items for {target_win.label}: {e}")

                # Notify module that view positioning/visibility was updated
                if hasattr(target_win, "on_view_applied"):
                    try:
                        target_win.on_view_applied()
                    except Exception as e:
                        logger.debug(f"Failed in on_view_applied for {target_win.label}: {e}")
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
    Load a workspace pipeline from a dictionary and recreate modules, merges, and connections.
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
        
        # Fallback for older pipelines where "modules." might have been omitted
        if not cls and not module_key.startswith("modules."):
            cls = module_registry.get(f"modules.{module_key}")
            
        if not cls:
            msg = f"Unknown module in pipeline: {module_key}"
            logger.warning(msg)
            continue

        params = dict(wdata.get("params", {}))
        merge_target_uuid = params.pop("merged_into", None)

        # Fallback to params if pos, size or visible are nested in params
        raw_pos = wdata.get("pos", params.pop("pos", (10, 10)))
        pos = list(raw_pos)

        raw_size = wdata.get("size", None)
        if raw_size is None:
            raw_w = params.pop("win_width", -1)
            raw_h = params.pop("win_height", -1)
            raw_size = [raw_w, raw_h]
        win_size = list(raw_size)

        vis = wdata.get("visible", params.pop("visible", True))

        # Discard duplicate standard kwargs from params to prevent 'got multiple values for keyword argument'
        for k in ("win_width", "win_height", "pos", "size", "visible", "uuid"):
            params.pop(k, None)

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
                visible=vis,
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
            
            from core.paths import is_compiled
            if missing_package:
                if is_compiled():
                    logger.info(
                        f"Module '{module_name}' ignored during workspace load "
                        f"(incompatible: missing package '{missing_package}' in compiled mode)"
                    )
                else:
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

        if "hidden_items" in wdata and wdata["hidden_items"]:
            try:
                from core.module_item_inspector import ModuleItemInspector
                ModuleItemInspector.apply_hidden_items(win, wdata["hidden_items"])
            except Exception as e:
                logger.debug(f"Failed to apply hidden items to {win.label}: {e}")

    WindowBase._batch_loading = False

    # Pump frames so ImGui evaluates real rect sizes of created windows before merging (only if merges exist)
    if merge_requests:
        pump_dpg_frames(2)

    # Reapply merges
    for src_uuid, tgt_uuid in merge_requests:
        src, tgt = instances.get(src_uuid), instances.get(tgt_uuid)
        if src and tgt:
            src.merge_into(tgt)
    
    raw_conns = list(data.get("connections", []))
    if not raw_conns and "links" in data:
        for lk in data.get("links", []):
            raw_conns.append({
                "from": lk.get("from", lk.get("source")),
                "to": lk.get("to", lk.get("target")),
                "output": lk.get("output", 0),
            })

    for conn in raw_conns:
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

    # Final frame pump to settle pipeline geometry after merging (only if merges exist)
    if merge_requests:
        pump_dpg_frames(1)

    # Load and register views if they exist in pipeline data
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
    Apply a view pipeline directly from a dictionary.
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

    # Step 3 – apply Gate states if present in view
    if "gates" in view_data:
        try:
            from core.main_win import main_win
            if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "set_gate_states"):
                main_win.node_editor.set_gate_states(view_data["gates"])
        except Exception as e:
            logger.warning(f"apply_view_data: could not apply gate states: {e}")



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
        from core.tutorial_manager import tutorial_manager
        tutorial_manager._update_step_toolbar_state()
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
    Apply a secondary pipeline view for the given mode.

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

        try:
            from core.module_item_inspector import ModuleItemInspector
            hidden = ModuleItemInspector.get_hidden_items(win)
            if hidden:
                entry["hidden_items"] = hidden
        except Exception as e:
            logger.debug(f"export_view: could not retrieve hidden items for {win.label}: {e}")

        if is_relative and viewport_width and viewport_height:
            entry["pos"]  = [(pos[0] / viewport_width) * 100.0,
                             (pos[1] / viewport_height) * 100.0]
            entry["size"] = [(w / viewport_width) * 100.0 if w != -1 else -1,
                             (h / viewport_height) * 100.0 if h != -1 else -1]

        windows.append(entry)

    view_dict: Dict[str, Any] = {"is_relative": is_relative, "windows": windows}
    try:
        from core.main_win import main_win
        if hasattr(main_win, "node_editor") and hasattr(main_win.node_editor, "get_gate_states"):
            gates = main_win.node_editor.get_gate_states()
            if gates:
                view_dict["gates"] = gates
    except Exception as e:
        logger.debug(f"export_view: could not retrieve gate states: {e}")

    return view_dict
