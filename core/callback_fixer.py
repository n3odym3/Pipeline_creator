"""
Automatic callback signature fixer for Pipeline Creator.

Surgically fixes DearPyGui callbacks that lack '*args' / '**kwargs' to ensure
robustness when compiled with Nuitka.

Uses a lightweight, dependency-free Search & Replace technique:
1. Locates the method header via AST line coordinates.
2. Identifies the closing parenthesis of the parameter list using a simple scanner.
3. Surgically inserts '*args, **kwargs' preserving all existing comments, docstrings,
   and formatting.
4. Validates the resulting code with 'ast.parse()' before saving to disk.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple, Union

from loguru import logger

from core.static_checks import (
    FunctionNode,
    ModuleAnalyzer,
    get_fragile_callback_methods,
)


def _find_matching_paren(text: str, start_paren_idx: int) -> int:
    """
    Find the index of the matching closing parenthesis ')' for the '(' at start_paren_idx.
    Correctly ignores parentheses inside strings, quotes, and comments.
    """
    depth = 0
    in_quote: Optional[str] = None
    in_comment = False

    for i in range(start_paren_idx, len(text)):
        ch = text[i]

        if in_comment:
            if ch == "\n":
                in_comment = False
            continue

        if in_quote:
            if ch == in_quote and (i == 0 or text[i - 1] != "\\"):
                in_quote = None
            continue

        if ch == "#":
            in_comment = True
            continue

        if ch in ('"', "'"):
            in_quote = ch
            continue

        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i

    return -1


def _fix_signature_header(header: str, method_node: FunctionNode) -> Optional[str]:
    """
    Surgically insert *args / **kwargs into the parameter list of a function header.

    Preserves formatting, trailing commas, line breaks, and return type annotations.
    """
    name_pos = header.find(method_node.name)
    if name_pos == -1:
        return None

    open_paren = header.find("(", name_pos)
    if open_paren == -1:
        return None

    close_paren = _find_matching_paren(header, open_paren)
    if close_paren == -1:
        return None

    param_text = header[open_paren + 1 : close_paren]
    has_vararg = method_node.args.vararg is not None
    has_kwarg = method_node.args.kwarg is not None

    if has_vararg and has_kwarg:
        return None  # Already fully robust

    # Case 1: **kwargs is already present, only *args is missing
    if has_kwarg and not has_vararg:
        kw_pos = param_text.rfind("**")
        if kw_pos != -1:
            new_param_text = param_text[:kw_pos] + "*args, " + param_text[kw_pos:]
        else:
            new_param_text = "*args, " + param_text

    # Case 2: Missing *args (and potentially missing **kwargs)
    elif not has_vararg:
        # If bare '*' exists (keyword-only separator), convert it to '*args'
        if method_node.args.kwonlyargs:
            new_param_text = re.sub(r"(?<=[,(])\s*\*\s*(?=,)", " *args", param_text)
            if not has_kwarg and "**" not in new_param_text:
                stripped = new_param_text.rstrip()
                ws = new_param_text[len(stripped):]
                insert = " **kwargs" if stripped.endswith(",") else ", **kwargs"
                new_param_text = stripped + insert + ws
        else:
            stripped = param_text.rstrip()
            ws = param_text[len(stripped):]  # Preserves multi-line indentation before ')'
            if stripped == "":
                insert = "*args, **kwargs"
            elif stripped.endswith(","):
                insert = " *args, **kwargs"
            else:
                insert = ", *args, **kwargs"
            new_param_text = stripped + insert + ws
    else:
        return None

    return header[:open_paren + 1] + new_param_text + header[close_paren:]


def fix_callback_signatures_in_file(
    filepath: Union[str, Path],
    create_backup: bool = True,
) -> Tuple[int, List[str]]:
    """
    Surgically fix fragile DearPyGui callback signatures in the specified file.

    Args:
        filepath: Path to the Python file to fix.
        create_backup: If True, writes a .bak copy before modifying.

    Returns:
        (count_of_fixes, list_of_fixed_method_names)
    """
    path = Path(filepath)
    if not path.is_file():
        from core.paths import MODULES_DIR, PROJECT_ROOT
        for base in (MODULES_DIR, PROJECT_ROOT):
            cand = base / path
            if cand.is_file():
                path = cand
                break
        else:
            return 0, []

    analyzer = ModuleAnalyzer.for_file(path)
    if analyzer is None:
        return 0, []

    fragile_methods = get_fragile_callback_methods(analyzer)
    if not fragile_methods:
        return 0, []

    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Could not read {path}: {e}")
        return 0, []

    lines = source.splitlines(keepends=True)
    # Process from bottom to top so line modifications do not alter earlier lines
    fragile_sorted = sorted(fragile_methods, key=lambda item: item[0].lineno, reverse=True)

    modified_methods: List[str] = []

    for method, _ in fragile_sorted:
        start_idx = method.lineno - 1
        # Advance to 'def ' line if lineno pointed to a decorator
        while start_idx < len(lines) and not lines[start_idx].lstrip().startswith(("def ", "async def ")):
            start_idx += 1

        if start_idx >= len(lines):
            continue

        # Accumulate header lines until closing ')' and ':' are found
        accumulated = ""
        end_idx = start_idx
        while end_idx < len(lines):
            accumulated += lines[end_idx]
            name_idx = accumulated.find(method.name)
            if name_idx != -1:
                open_p = accumulated.find("(", name_idx)
                if open_p != -1:
                    close_p = _find_matching_paren(accumulated, open_p)
                    if close_p != -1 and ":" in accumulated[close_p:]:
                        break
            end_idx += 1

        if end_idx >= len(lines):
            continue

        new_header = _fix_signature_header(accumulated, method)
        if new_header and new_header != accumulated:
            lines[start_idx : end_idx + 1] = [new_header]
            modified_methods.append(method.name)

    if not modified_methods:
        return 0, []

    new_source = "".join(lines)

    # Safety validation: parse AST to guarantee zero syntax errors
    try:
        ast.parse(new_source)
    except SyntaxError as e:
        logger.error(f"Syntax validation failed for {path} after fixes: {e}. Aborting modification.")
        return 0, []

    if create_backup:
        try:
            bak_path = path.with_suffix(path.suffix + ".bak")
            bak_path.write_text(source, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not create backup for {path}: {e}")

    try:
        path.write_text(new_source, encoding="utf-8")
        logger.success(f"Fixed {len(modified_methods)} callback signature(s) in {path.name}: {', '.join(modified_methods)}")
        return len(modified_methods), modified_methods
    except Exception as e:
        logger.error(f"Failed to write fixed source to {path}: {e}")
        return 0, []


def fix_all_callbacks(
    target_dir: Optional[Union[str, Path]] = None,
    create_backup: bool = True,
) -> Dict[str, List[str]]:
    """
    Scan and fix fragile DearPyGui callbacks across all modules in target_dir.

    Returns:
        Dictionary mapping file path string to list of fixed method names.
    """
    from core.paths import MODULES_DIR, PROJECT_ROOT

    if target_dir is None:
        base_dir = MODULES_DIR
    else:
        p = Path(target_dir)
        base_dir = p if p.is_absolute() else (PROJECT_ROOT / p)

    results: Dict[str, List[str]] = {}

    if not base_dir.exists():
        logger.warning(f"Target directory '{base_dir}' does not exist for callback fixer")
        return results

    if base_dir.is_file():
        files = [base_dir]
    else:
        files = [
            p for p in base_dir.rglob("*.py")
            if p.name != "__init__.py" and "__pycache__" not in p.parts
        ]

    for py_file in files:
        count, fixed = fix_callback_signatures_in_file(py_file, create_backup=create_backup)
        if count > 0:
            results[str(py_file)] = fixed

    return results
