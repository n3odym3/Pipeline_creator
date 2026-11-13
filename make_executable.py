from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Set, Tuple, Union

try:
    import comtypes.client
    comtypes.client.gen_dir = None
except ImportError:
    pass

MAIN_PIPELINE = None
OUTPUT_DIR: str = "C:/Users/gerva/Desktop/PieplineCreator_compil"

IGNORE_PATTERNS = ("__pycache__", "*.pyc", "*.pyo")
INTERNAL_PACKAGES = {"core", "config", "modules"}


def _get_module_metadata_statically_for_build(filepath: Union[str, Path]) -> Optional[Dict[str, str]]:
    """Statically parse module classes/names via AST for legacy label mapping."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=str(filepath))

        exported_class: Optional[str] = None
        exported_name: str = ""
        class_defs: Dict[str, ast.ClassDef] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_defs[node.name] = node
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "EXPORTED_CLASS" and isinstance(node.value, ast.Name):
                            exported_class = node.value.id
                        elif (
                            target.id == "EXPORTED_NAME"
                            and isinstance(node.value, ast.Constant)
                            and isinstance(node.value.value, str)
                        ):
                            exported_name = node.value.value

        if not exported_class:
            return None

        doc = ""
        description = exported_name
        target_cls = class_defs.get(exported_class)
        if target_cls is not None:
            doc = ast.get_docstring(target_cls) or ""
            for item in target_cls.body:
                if isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name) and t.id in ("description", "DESCRIPTION"):
                            if isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                                description = item.value.value

        return {
            "class_name": exported_class,
            "description": description or exported_class,
            "doc": doc,
        }
    except Exception:
        return None


def _build_legacy_module_lookup(modules_path: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Scan modules directory to map class names and descriptions to their subfolder paths."""
    class_to_folder: Dict[str, str] = {}
    label_to_folder: Dict[str, str] = {}

    if not modules_path.is_dir():
        return class_to_folder, label_to_folder

    for root, dirs, files in os.walk(modules_path):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for file in files:
            if file.endswith(".py") and file != "__init__.py":
                file_path = Path(root) / file
                metadata = _get_module_metadata_statically_for_build(file_path)
                if metadata:
                    rel_parts = Path(root).relative_to(modules_path).parts
                    if rel_parts:
                        folder_path = "/".join(rel_parts)
                        class_to_folder[metadata["class_name"]] = folder_path
                        if metadata["description"]:
                            label_to_folder[metadata["description"]] = folder_path

    return class_to_folder, label_to_folder


def get_used_module_folders(pipeline_path: Union[str, Path], modules_dir: Union[str, Path]) -> Set[str]:
    """Parses pipeline file and returns the set of specific module subfolder paths (e.g. Camera/webcam)."""
    used_folders: Set[str] = set()
    try:
        with open(pipeline_path, "r", encoding="utf-8") as f:
            pipeline_data = json.load(f)
    except Exception as e:
        print(f"Error reading pipeline {pipeline_path}: {e}")
        return used_folders

    if not isinstance(pipeline_data, dict):
        print(f"Warning: Invalid pipeline format in {pipeline_path} (expected JSON object).")
        return used_folders

    windows = pipeline_data.get("windows", [])
    if not isinstance(windows, list):
        return used_folders

    modules_path = Path(modules_dir)
    class_to_folder: Optional[Dict[str, str]] = None
    label_to_folder: Optional[Dict[str, str]] = None

    for window in windows:
        if not isinstance(window, dict):
            continue

        module_path = window.get("module")
        if module_path and isinstance(module_path, str):
            if module_path.startswith("modules."):
                module_path = module_path[len("modules."):]
            parts = [p for p in module_path.split(".") if p]
            if len(parts) > 1:
                used_folders.add("/".join(parts[:-1]))
            elif parts:
                used_folders.add(parts[0])
        else:
            # Lazy-build fallback lookup map only if a window entry lacks 'module'
            if class_to_folder is None or label_to_folder is None:
                class_to_folder, label_to_folder = _build_legacy_module_lookup(modules_path)

            class_name = window.get("class_name")
            if class_name and class_name in class_to_folder:
                used_folders.add(class_to_folder[class_name])
            else:
                params = window.get("params")
                label = window.get("label") or (params.get("label") if isinstance(params, dict) else None)
                if label and label in label_to_folder:
                    used_folders.add(label_to_folder[label])
                elif label and label in class_to_folder:
                    used_folders.add(class_to_folder[label])

    return used_folders


def get_imports_from_file(filepath: Union[str, Path]) -> Set[str]:
    """Extracts non-relative top-level imports from a Python file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(filepath))
    except Exception as e:
        print(f"Warning: Failed to parse {filepath}: {e}")
        return set()

    imports: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_pkg = alias.name.split(".")[0]
                if top_pkg:
                    imports.add(top_pkg)
        elif isinstance(node, ast.ImportFrom):
            # Ignore relative imports (e.g. from . import foo)
            if getattr(node, "level", 0) > 0:
                continue
            if node.module:
                top_pkg = node.module.split(".")[0]
                if top_pkg:
                    imports.add(top_pkg)
    return imports


def get_local_packages(modules_dir: Union[str, Path]) -> Set[str]:
    """Finds all local package names inside modules (directories containing __init__.py)."""
    local_packages: Set[str] = set()
    modules_path = Path(modules_dir)
    if not modules_path.is_dir():
        return local_packages

    for root, dirs, files in os.walk(modules_path):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        if "__init__.py" in files:
            local_packages.add(Path(root).name)
    return local_packages


def _is_package_available(pkg_name: str) -> bool:
    """Checks if a Python package is importable without executing it if possible."""
    try:
        if importlib.util.find_spec(pkg_name) is not None:
            return True
    except (ImportError, ValueError, AttributeError):
        pass

    try:
        __import__(pkg_name)
        return True
    except Exception:
        return False


def get_hidden_imports(
    modules_dir: Union[str, Path],
    used_module_folders: Optional[Set[str]] = None,
    local_packages: Optional[Set[str]] = None,
) -> Set[str]:
    """
    Scans modules directory for imports that need to be explicitly included.
    Filters out standard library, internal project packages, and local modules.
    If used_module_folders is provided, only scans those specific subfolders.
    """
    all_imports: Set[str] = set()
    local_module_files: Set[str] = set()
    modules_path = Path(modules_dir)

    if not modules_path.is_dir():
        return set()

    stdlib = (
        set(sys.stdlib_module_names)
        if hasattr(sys, "stdlib_module_names")
        else set(sys.builtin_module_names)
    )

    for root, dirs, files in os.walk(modules_path):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]

        if used_module_folders is not None:
            try:
                rel_parts = Path(root).relative_to(modules_path).parts
                rel_path_str = "/".join(rel_parts)
                is_used = any(
                    rel_path_str == used_f
                    or rel_path_str.startswith(used_f + "/")
                    or used_f.startswith(rel_path_str + "/")
                    for used_f in used_module_folders
                )
                if not is_used:
                    continue
            except ValueError:
                continue

        for file in files:
            if file.endswith(".py"):
                if file != "__init__.py":
                    local_module_files.add(Path(file).stem)
                file_imports = get_imports_from_file(Path(root) / file)
                all_imports.update(file_imports)

    if local_packages is None:
        local_packages = get_local_packages(modules_path)

    excluded_names = stdlib | local_packages | local_module_files | INTERNAL_PACKAGES

    required_packages: Set[str] = set()
    for imp in sorted(all_imports):
        if not imp or imp.startswith(".") or imp in excluded_names:
            continue

        if _is_package_available(imp):
            required_packages.add(imp)
        else:
            print(f"Warning: Detected import '{imp}' but could not import it. Skipping.")

    return required_packages


def _resolve_pipeline_path(project_root: Path, pipeline_setting: str) -> Path:
    """
    Resolves MAIN_PIPELINE to an existing pipeline JSON file.
    Supports:
    - Absolute paths: "C:/.../pipelines/MotilitometerV2.json"
    - Relative paths from project root: "pipelines/MotilitometerV2.json" or "pipelines/MotilitometerV2"
    - Pipeline names directly inside 'pipelines/': "MotilitometerV2.json" or "MotilitometerV2"
    """
    raw = pipeline_setting.strip()
    candidate = Path(raw)

    if candidate.is_absolute():
        if candidate.is_file():
            return candidate
        if candidate.suffix.lower() != ".json":
            with_json = candidate.with_suffix(".json")
            if with_json.is_file():
                return with_json
        return candidate

    rel_clean = raw.lstrip("/\\")
    rel_path = Path(rel_clean)
    pipelines_dir = project_root / "pipelines"

    # Candidate paths to check in priority order
    search_candidates = [
        project_root / rel_path,
        pipelines_dir / rel_path,
    ]
    if rel_path.suffix.lower() != ".json":
        rel_with_json = rel_path.with_suffix(".json")
        search_candidates.extend([
            project_root / rel_with_json,
            pipelines_dir / rel_with_json,
        ])

    for path_option in search_candidates:
        if path_option.is_file():
            return path_option

    # Fallback default path for warning messages if file does not exist
    return pipelines_dir / (rel_path if rel_path.suffix.lower() == ".json" else rel_path.with_suffix(".json"))


def create_executable() -> None:
    """
    Builds the Pipeline Creator executable using Nuitka.
    """
    project_root = Path(__file__).resolve().parent
    modules_dir = project_root / "modules"
    output_dir_path = Path(OUTPUT_DIR)

    used_module_folders: Optional[Set[str]] = None
    if MAIN_PIPELINE:
        pipeline_path = _resolve_pipeline_path(project_root, MAIN_PIPELINE)
        if pipeline_path.is_file():
            print(f"Parsing pipeline: {pipeline_path}...")
            used_module_folders = get_used_module_folders(pipeline_path, modules_dir)
            if used_module_folders:
                print(f"Detected used modules: {', '.join(sorted(used_module_folders))}")
            else:
                print(f"Warning: No modules detected in pipeline '{pipeline_path}'.")
        else:
            print(f"Warning: MAIN_PIPELINE '{MAIN_PIPELINE}' not found. Compiling all modules.")

    # Automatically find all local package names inside modules to tell Nuitka not to compile them
    local_packages = get_local_packages(modules_dir)

    print("Scanning for dependencies in 'modules'...")
    hidden_imports = get_hidden_imports(modules_dir, used_module_folders, local_packages=local_packages)
    print(f"Usage detection found extra packages: {', '.join(sorted(hidden_imports))}")

    print("Building executable with Nuitka...")

    # Base Nuitka arguments
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--main=main.py",
        "--windows-console-mode=force",
        "--windows-icon-from-ico=ressources/Icon.ico",
        "--output-filename=PipelineCreator.exe",
        f"--output-dir={output_dir_path}",
        "--include-windows-runtime-dlls=yes",
        "--assume-yes-for-downloads",
        "--include-package=core",
        "--include-package=config",
        "--enable-plugin=tk-inter",
        "--recompile-extension-modules=cv2:never",
        "--nofollow-import-to=zmq",
        "--nofollow-import-to=core.input_output_types",
        "--no-deployment-flag=excluded-module-usage",
        "--no-deployment-flag=self-execution",
        "--python-flag=no_asserts",
    ]

    # Include data directories if they exist
    data_dirs = [
        "pipelines",
        "scripts",
        "config",
        "ressources",
        "template_and_doc",
        "tutorials",
        "docs",
    ]
    for d in data_dirs:
        if (project_root / d).is_dir():
            cmd.append(f"--include-data-dir={d}={d}")

    # Include individual files if they exist
    data_files = [
        "README.md",
        "LICENSE",
        "core/input_output_types.py",
        "mkdocs.yml",
    ]
    for f in data_files:
        if (project_root / f).is_file():
            cmd.append(f"--include-data-files={f}={f}")

    # Exclude all local packages from Nuitka compilation
    for pkg in sorted(local_packages):
        cmd.append(f"--nofollow-import-to={pkg}")

    # Add discovered packages
    for pkg in sorted(hidden_imports):
        if pkg == "webview":
            # Nuitka has a pywebview plugin which conflicts with --include-package=webview
            continue
        cmd.append(f"--include-package={pkg}")

    # Add MkDocs and its dependencies/extensions if installed
    mkdocs_packages = [
        "mkdocs",
        "pymdownx",
        "mkdocstrings",
        "mkdocstrings_python",
        "mkdocstrings_handlers",
        "material",
        "jinja2",
        "markdown",
        "yaml",
        "pygments",
    ]
    for pkg in mkdocs_packages:
        if importlib.util.find_spec(pkg) is not None:
            print(f"Forcing Nuitka compilation inclusion for documentation package: {pkg}")
            cmd.append(f"--include-package={pkg}")
            cmd.append(f"--include-package-data={pkg}")

    print(f"Running command: {' '.join(cmd)}")

    try:
        subprocess.check_call(cmd, cwd=str(project_root))
        print("Nuitka build complete.")

        dist_dir = output_dir_path / "main.dist"

        # Automate copy of modules (including .py files) to main.dist/modules
        dst_modules_dir = dist_dir / "modules"
        print(f"Automating modules copy to: {dst_modules_dir}...")

        if dst_modules_dir.exists():
            shutil.rmtree(dst_modules_dir)
        dst_modules_dir.mkdir(parents=True, exist_ok=True)

        # Determine folders to copy
        if used_module_folders is not None:
            folders_to_copy = sorted(used_module_folders)
        elif modules_dir.is_dir():
            folders_to_copy = sorted(
                f.name
                for f in modules_dir.iterdir()
                if f.is_dir() and f.name != "__pycache__" and not f.name.startswith(".")
            )
        else:
            folders_to_copy = []

        ignore_fn = shutil.ignore_patterns(*IGNORE_PATTERNS)

        for folder in folders_to_copy:
            src_folder = modules_dir / folder
            dst_folder = dst_modules_dir / folder
            if src_folder.exists() and src_folder.is_dir():
                print(f"Copying module folder: modules/{folder}...")
                dst_folder.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    src_folder,
                    dst_folder,
                    ignore=ignore_fn,
                    dirs_exist_ok=True,
                )
            else:
                print(f"Warning: Module folder not found, skipping: {src_folder}")

        # Copy core and config source files for mkdocstrings introspection
        for folder_name in ("core", "config"):
            src_dir = project_root / folder_name
            if not src_dir.is_dir():
                continue
            dst_dir = dist_dir / folder_name
            print(f"Copying {folder_name} source files for documentation to: {dst_dir}...")
            shutil.copytree(
                src_dir,
                dst_dir,
                ignore=ignore_fn,
                dirs_exist_ok=True,
            )

        print("Modules, core and config copying complete. Build is fully complete.")
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error code {e.returncode}")
        sys.exit(e.returncode or 1)


if __name__ == "__main__":
    create_executable()
