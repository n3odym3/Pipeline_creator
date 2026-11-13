"""
Shared AST analysis utilities for module dependency management in Pipeline Creator.
"""

import ast
import os
from pathlib import Path
from typing import Dict, Optional, Set, Union
from loguru import logger

_DIR_LOCAL_MODULES_CACHE: Dict[str, Set[str]] = {}


def _get_local_modules_in_dir(base_dir: Path) -> Set[str]:
    """Return set of local module/package names in `base_dir`, cached per directory."""
    key = str(base_dir)
    cached = _DIR_LOCAL_MODULES_CACHE.get(key)
    if cached is not None:
        return cached

    names: Set[str] = set()
    try:
        with os.scandir(base_dir) as it:
            for entry in it:
                if entry.is_dir():
                    names.add(entry.name)
                elif entry.is_file() and entry.name.endswith(".py"):
                    names.add(entry.name[:-3])
    except OSError:
        pass

    _DIR_LOCAL_MODULES_CACHE[key] = names
    return names


def extract_imports_from_tree(
    tree: ast.Module,
    file_path: Union[str, Path],
    exclude_local: bool = True,
) -> Set[str]:
    """
    Extract all imported top-level module names from an already parsed AST.

    Args:
        tree: Parsed AST module node.
        file_path: Path to the Python file being analyzed (for local module exclusion).
        exclude_local: If True, excludes local modules in the same directory.

    Returns:
        Set of base module names (e.g., {'cv2', 'numpy', 'pandas'}).
    """
    imports: Set[str] = set()
    file_path_obj = Path(file_path)
    local_modules = _get_local_modules_in_dir(file_path_obj.parent) if exclude_local else set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split(".")[0]
                if exclude_local and module_name in local_modules:
                    continue
                imports.add(module_name)

        elif isinstance(node, ast.ImportFrom):
            if getattr(node, "level", 0) > 0:
                continue

            if node.module:
                module_name = node.module.split(".")[0]
                if exclude_local and module_name in local_modules:
                    continue
                imports.add(module_name)

    return imports


def extract_imports(
    file_path: Union[str, Path],
    exclude_local: bool = True,
    tree: Optional[ast.Module] = None,
) -> Set[str]:
    """
    Extract all imported top-level module names from a Python file using AST.

    Args:
        file_path: Path to the Python file to analyze.
        exclude_local: If True, excludes local modules in the same directory.
        tree: Optional pre-parsed AST module to avoid re-reading and re-parsing the file.

    Returns:
        Set of base module names (e.g., {'cv2', 'numpy', 'pandas'}).
        Returns empty set on syntax errors or other read exceptions.
    """
    file_path_obj = Path(file_path)

    try:
        if tree is None:
            with open(file_path_obj, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=str(file_path_obj))

        return extract_imports_from_tree(tree, file_path_obj, exclude_local=exclude_local)

    except SyntaxError:
        logger.debug(f"Syntax error in {file_path_obj}, skipping import extraction")
    except Exception as e:
        logger.debug(f"Error during import extraction from {file_path_obj}: {e}")

    return set()


def _is_local_module(file_path: Path, module_name: str) -> bool:
    """
    Check if a module name corresponds to a local file or directory relative to the file.
    """
    return module_name in _get_local_modules_in_dir(file_path.parent)
