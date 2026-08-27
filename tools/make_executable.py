import subprocess
import os
import sys
import ast
import json
from pathlib import Path

MAIN_PIPELINE = None
OUTPUT_DIR = "C:/Users/gerva/Desktop/Pipeline_creator_compil"

def _get_module_metadata_statically_for_build(filepath):
    """Statically parse module classes/names via AST for legacy label mapping."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
        
        exported_class = None
        exported_name = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id == 'EXPORTED_CLASS' and isinstance(node.value, ast.Name):
                            exported_class = node.value.id
                        elif target.id == 'EXPORTED_NAME' and isinstance(node.value, ast.Constant):
                            exported_name = node.value.value
                            
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
                            if isinstance(t, ast.Name) and t.id in ('description', 'DESCRIPTION'):
                                if isinstance(item.value, ast.Constant):
                                    description = item.value.value
                break
                
        return {
            "class_name": exported_class,
            "description": description or exported_class,
            "doc": doc
        }
    except Exception:
        return None

def get_used_module_folders(layout_path, modules_dir):
    """Parses layout file and returns the set of specific subfolder paths (e.g. Camera/webcam)."""
    used_folders = set()
    try:
        with open(layout_path, 'r', encoding='utf-8') as f:
            layout_data = json.load(f)
    except Exception as e:
        print(f"Error reading layout {layout_path}: {e}")
        return used_folders

    # Scan to build a map of class name / label to specific folder (fallback)
    class_to_folder = {}
    label_to_folder = {}
    
    modules_path = Path(modules_dir)
    for root, _, files in os.walk(modules_path):
        for file in files:
            if file.endswith(".py") and file != "__init__.py":
                file_path = os.path.join(root, file)
                metadata = _get_module_metadata_statically_for_build(file_path)
                if metadata:
                    rel_parts = Path(root).relative_to(modules_path).parts
                    if rel_parts:
                        folder_path = "/".join(rel_parts)
                        class_to_folder[metadata["class_name"]] = folder_path
                        if metadata["description"]:
                            label_to_folder[metadata["description"]] = folder_path
                            
    for window in layout_data.get("windows", []):
        module_path = window.get("module")
        if module_path:
            if module_path.startswith("modules."):
                module_path = module_path[len("modules."):]
            parts = module_path.split('.')
            if len(parts) > 1:
                used_folders.add("/".join(parts[:-1]))
            elif parts:
                used_folders.add(parts[0])
        else:
            class_name = window.get("class_name")
            if class_name and class_name in class_to_folder:
                used_folders.add(class_to_folder[class_name])
            else:
                label = window.get("label") or window.get("params", {}).get("label")
                if label and label in label_to_folder:
                    used_folders.add(label_to_folder[label])
                elif label and label in class_to_folder:
                    used_folders.add(class_to_folder[label])
        
    return used_folders

def get_imports_from_file(filepath):
    """Extracts top-level imports from a Python file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except Exception as e:
        print(f"Warning: Failed to parse {filepath}: {e}")
        return set()

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split('.')[0])
    return imports

def get_hidden_imports(modules_dir, used_module_folders=None):
    """
    Scans modules directory for imports that need to be explicitly included.
    Filters out standard library and local project modules.
    If used_module_folders is provided, only scans those specific subfolders.
    """
    all_imports = set()
    modules_path = Path(modules_dir)
    
    # Get standard library names
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else set(sys.builtin_module_names)

    for root, _, files in os.walk(modules_path):
        if used_module_folders is not None:
            try:
                rel_parts = Path(root).relative_to(modules_path).parts
                rel_path_str = "/".join(rel_parts)
                is_used = False
                for used_f in used_module_folders:
                    if rel_path_str == used_f or rel_path_str.startswith(used_f + "/") or used_f.startswith(rel_path_str + "/"):
                        is_used = True
                        break
                if not is_used:
                    continue
            except ValueError:
                continue

        for file in files:
            if file.endswith(".py"):
                file_imports = get_imports_from_file(os.path.join(root, file))
                all_imports.update(file_imports)
    
    # Find all local package names inside modules (directories with __init__.py)
    local_packages = set()
    for root, dirs, files in os.walk(modules_path):
        if "__init__.py" in files:
            local_packages.add(Path(root).name)

    # Filter imports
    required_packages = set()
    
    for imp in all_imports:
        if imp not in stdlib and not imp.startswith('.') and imp not in local_packages:
            # Check if it's installable/importable
            try:
                # Basic check to see if it causes an error
                __import__(imp)
                required_packages.add(imp)
            except ImportError:
                print(f"Warning: Detected import '{imp}' but could not import it. Skipping.")
                pass
            except Exception:
                pass
                
    return required_packages

def create_executable():
    """
    Builds the Pipeline creator executable using Nuitka.
    """
    project_root = Path(__file__).resolve().parent.parent
    modules_dir = project_root / "modules"
    
    used_module_folders = None
    if MAIN_PIPELINE:
        pipeline_path = project_root / MAIN_PIPELINE
        if pipeline_path.exists():
            print(f"Parsing pipeline layout: {MAIN_PIPELINE}...")
            used_module_folders = get_used_module_folders(pipeline_path, modules_dir)
            print(f"Detected used modules: {', '.join(used_module_folders)}")
        else:
            print(f"Warning: MAIN_PIPELINE layout '{MAIN_PIPELINE}' not found. Compiling all modules.")

    print("Scanning for dependencies in 'modules'...")
    hidden_imports = get_hidden_imports(modules_dir, used_module_folders)
    print(f"Usage detection found extra packages: {', '.join(hidden_imports)}")

    # Automatically find all local package names inside modules to tell Nuitka not to compile them
    local_packages = set()
    for root, dirs, files in os.walk(modules_dir):
        if "__init__.py" in files:
            local_packages.add(Path(root).name)

    print("Building executable with Nuitka...")
    
    # Nuitka arguments
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",                
        "--main=main.py",
        "--windows-console-mode=force",
        "--windows-icon-from-ico=ressources/icon.ico",
        "--output-filename=Polymager.exe",
        f"--output-dir={OUTPUT_DIR}",           
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

    # Include existing data directories
    for data_dir in ("pipelines", "layouts", "scripts", "config", "ressources", "templates", "tutorials"):
        if (project_root / data_dir).exists():
            cmd.append(f"--include-data-dir={data_dir}={data_dir}")

    # Include existing data files
    for data_file in ("README.md", "LICENSE", "core/input_output_types.py"):
        if (project_root / data_file).exists():
            cmd.append(f"--include-data-files={data_file}={data_file}")
    
    # Exclude all local packages from Nuitka compilation
    for pkg in local_packages:
        cmd.append(f"--nofollow-import-to={pkg}")
    
    # Add discovered packages
    for pkg in hidden_imports:
        if pkg == "webview":
            # Nuitka has a pywebview plugin which conflicts with --include-package=webview
            continue
        cmd.append(f"--include-package={pkg}")
    
    # Include docs folder and mkdocs.yml if they exist
    if (project_root / "docs").exists():
        cmd.append("--include-data-dir=docs=docs")
    if (project_root / "mkdocs.yml").exists():
        cmd.append("--include-data-files=mkdocs.yml=mkdocs.yml")

    # Add MkDocs and its dependencies/extensions if installed
    import importlib.util
    mkdocs_packages = ["mkdocs", "pymdownx", "mkdocstrings", "mkdocstrings_python", "mkdocstrings_handlers", "material", "jinja2", "markdown", "yaml", "pygments"]
    for pkg in mkdocs_packages:
        if importlib.util.find_spec(pkg) is not None:
            print(f"Forcing Nuitka compilation inclusion for documentation package: {pkg}")
            cmd.append(f"--include-package={pkg}")
            cmd.append(f"--include-package-data={pkg}")
    
    print(f"Running command: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd, cwd=str(project_root))
        print("Nuitka build complete.")
        
        # Automate copy of modules (including .py files) to main.dist/modules
        import shutil
        dst_modules_dir = Path(OUTPUT_DIR) / "main.dist" / "modules"
        print(f"Automating modules copy to: {dst_modules_dir}...")
        
        # Ensure destination directory exists
        dst_modules_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine folders to copy
        if used_module_folders is not None:
            folders_to_copy = used_module_folders
        else:
            folders_to_copy = [f.name for f in modules_dir.iterdir() if f.is_dir()]
            
        for folder in folders_to_copy:
            src_folder = modules_dir / folder
            dst_folder = dst_modules_dir / folder
            if src_folder.exists() and src_folder.is_dir():
                print(f"Copying module folder: modules/{folder}...")
                if dst_folder.exists():
                    shutil.rmtree(dst_folder)
                # Ensure the parent directory in the destination exists
                dst_folder.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    src_folder,
                    dst_folder,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo')
                )
        
        # Copy core and config source files for mkdocstrings introspection
        for folder_name in ("core", "config"):
            dst_dir = Path(OUTPUT_DIR) / "main.dist" / folder_name
            print(f"Copying {folder_name} source files for documentation to: {dst_dir}...")
            shutil.copytree(
                project_root / folder_name,
                dst_dir,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'),
                dirs_exist_ok=True
            )
        print("Modules, core and config copying complete. Build is fully complete.")
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error code {e.returncode}")
        sys.exit(1)

if __name__ == "__main__":
    create_executable()
