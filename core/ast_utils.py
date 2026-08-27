"""
Shared AST analysis utilities for module dependency management in Pipeline Creator.
"""

import ast
from pathlib import Path
from typing import Set, Union
from loguru import logger

def extract_imports(file_path: Union[str, Path], exclude_local: bool = True) -> Set[str]:
    """
    Extract all imported top-level module names from a Python file using AST.

    Args:
        file_path: Path to the Python file to analyze.
        exclude_local: If True, excludes local modules in the same directory.

    Returns:
        Set of base module names (e.g., {'cv2', 'numpy', 'pandas'}).
        Returns empty set on syntax errors or other read exceptions.
    """
    imports: Set[str] = set()
    file_path_obj = Path(file_path)

    try:
        with open(file_path_obj, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name.split(".")[0]

                    # Skip local modules if requested
                    if exclude_local and _is_local_module(file_path_obj, module_name):
                        continue

                    imports.add(module_name)

            elif isinstance(node, ast.ImportFrom):
                # Skip relative imports (they are local by definition)
                if getattr(node, "level", 0) > 0:
                    continue

                if node.module:
                    module_name = node.module.split(".")[0]

                    # Skip local modules if requested
                    if exclude_local and _is_local_module(file_path_obj, module_name):
                        continue

                    imports.add(module_name)

    except SyntaxError:
        logger.debug(f"Syntax error in {file_path_obj}, skipping import extraction")
    except Exception as e:
        logger.debug(f"Error during import extraction from {file_path_obj}: {e}")

    return imports


def _is_local_module(file_path: Path, module_name: str) -> bool:
    """
    Check if a module name corresponds to a local file or directory relative to the file.
    """
    base_dir = file_path.parent
    local_dir_path = base_dir / module_name
    local_file_path = base_dir / f"{module_name}.py"

    return local_dir_path.is_dir() or local_file_path.is_file()

