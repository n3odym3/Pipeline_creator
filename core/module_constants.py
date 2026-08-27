"""
Shared constants and utilities for module management in Pipeline Creator.

Provides automatic and manual package name mappings.
Uses importlib.metadata to auto-detect import -> pip mappings for installed packages,
with a static fallback for packages that are not yet installed.
"""

from __future__ import annotations

import importlib.metadata
from typing import Dict, Optional

from loguru import logger

# Mapping of import names to pip package names (for packages not yet installed)
PACKAGE_MAPPINGS: Dict[str, str] = {
    "cv2": "opencv-python",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
}

# Cached mapping (populated on first call to get_package_name)
_auto_mapping: Optional[Dict[str, str]] = None


def _build_auto_mapping() -> Dict[str, str]:
    """
    Build a mapping of import names to pip package names
    using importlib.metadata (scans all installed packages).

    Returns:
        Dict mapping import name -> pip package name.
    """
    mapping: Dict[str, str] = {}
    try:
        for import_name, dist_names in importlib.metadata.packages_distributions().items():
            if dist_names:
                mapping[import_name] = dist_names[0]
    except Exception as e:
        logger.debug(f"Could not build auto package mapping: {e}")
    return mapping


def get_package_name(import_name: str) -> str:
    """
    Map an import name to its corresponding pip package name.

    Uses auto-detection via importlib.metadata first, then falls back
    to the static PACKAGE_MAPPINGS dict for uninstalled packages.

    Args:
        import_name: The name used in import statements (e.g., 'cv2', 'PIL').

    Returns:
        The pip package name (e.g., 'opencv-python', 'pillow').
        If no mapping exists, returns the import name unchanged.
    """
    global _auto_mapping
    if _auto_mapping is None:
        _auto_mapping = _build_auto_mapping()

    # Check auto-detected mapping first
    if import_name in _auto_mapping:
        return _auto_mapping[import_name]

    # Fallback to manual mapping
    return PACKAGE_MAPPINGS.get(import_name, import_name)


