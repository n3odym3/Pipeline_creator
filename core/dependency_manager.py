"""
Dependency Manager for Pipeline Creator.

Detects and manages missing Python package dependencies:
- Scans requirements.txt files in module directories
- Stores detected missing dependencies
- Provides package installation functionality via pip
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from core.paths import PROJECT_ROOT


class DependencyManager:
    """
    Manages module dependencies for Pipeline Creator.

    This class provides functionality to:
    - Scan module directories for requirements.txt files
    - Check which packages are missing
    - Install packages individually or in bulk
    """

    # 5 minutes timeout for pip operations
    PIP_TIMEOUT: int = 300

    def __init__(self) -> None:
        """Initialize the dependency manager."""
        self.missing_deps: Dict[str, List[str]] = {}
        self.install_in_progress: bool = False

    def check_all_modules(self, modules_path: str = "modules", clear_existing: bool = False) -> None:
        """
        Scan all modules and check for missing dependencies.

        Searches for requirements.txt files in the given path and checks
        whether each listed package is installed.
        """
        if clear_existing:
            self.missing_deps.clear()

        full_modules_path = Path(modules_path)
        if not full_modules_path.is_absolute():
            full_modules_path = PROJECT_ROOT / modules_path

        if not full_modules_path.exists():
            logger.warning(f"Modules path '{full_modules_path}' does not exist")
            return

        for req_file in full_modules_path.rglob("requirements.txt"):
            module_name = req_file.parent.name
            missing = self._check_requirements(req_file)

            if missing:
                if module_name in self.missing_deps:
                    self.missing_deps[module_name].extend(missing)
                    self.missing_deps[module_name] = list(set(self.missing_deps[module_name]))
                else:
                    self.missing_deps[module_name] = missing
                logger.info(f"Module '{module_name}' has {len(missing)} missing dependencies")

    def _check_requirements(self, req_file: Path) -> List[str]:
        """
        Check a single requirements.txt file for missing packages.
        """
        missing: List[str] = []

        try:
            with open(req_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()

                    # Skip empty lines and comments
                    if not line or line.startswith("#"):
                        continue

                    package_name = self._extract_package_name(line)

                    if not self._is_package_installed(package_name):
                        missing.append(line)
                        logger.debug(f"Missing package: {line}")

        except Exception as e:
            logger.error(f"Error reading {req_file}: {e}")

        return missing

    @staticmethod
    def _extract_package_name(spec: str) -> str:
        """Extract the base package name from a requirement specification."""
        for sep in [">=", "==", "<=", "<", ">", "[", ";"]:
            spec = spec.split(sep)[0]
        return spec.strip()

    @staticmethod
    def _is_package_installed(package_name: str) -> bool:
        """Check if a package is installed by inspecting distribution metadata or importing."""
        import importlib.metadata
        import importlib.util

        # Direct distribution check via importlib.metadata
        try:
            importlib.metadata.distribution(package_name)
            return True
        except Exception:
            pass

        # Check by module import spec (fallback)
        try:
            import_name = package_name.replace("-", "_")
            if importlib.util.find_spec(import_name) is not None:
                return True
        except Exception:
            pass

        return False

    def get_missing_deps(self) -> Dict[str, List[str]]:
        """
        Get current missing dependencies.
        """
        return self.missing_deps.copy()

    def install_package(self, package: str) -> bool:
        """
        Install a single package using pip.
        """
        if self.install_in_progress:
            logger.warning("Installation already in progress")
            return False

        self.install_in_progress = True
        logger.info(f"Installing package: {package}")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                capture_output=True,
                text=True,
                timeout=self.PIP_TIMEOUT,
            )

            success = result.returncode == 0
            if success:
                logger.success(f"Installed {package}")
            else:
                logger.error(f"Failed to install {package}: {result.stderr}")

            return success

        except subprocess.TimeoutExpired:
            logger.error(f"Installation of {package} timed out")
            return False
        except Exception as e:
            logger.error(f"Error installing {package}: {e}")
            return False
        finally:
            self.install_in_progress = False

    def install_packages(self, packages: List[str]) -> Dict[str, bool]:
        """
        Install multiple packages using pip.
        """
        if self.install_in_progress:
            logger.warning("Installation already in progress")
            return {pkg: False for pkg in packages}

        if not packages:
            return {}

        self.install_in_progress = True
        logger.info(f"Installing {len(packages)} packages: {', '.join(packages)}")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", *packages],
                capture_output=True,
                text=True,
                timeout=self.PIP_TIMEOUT,
            )

            success = result.returncode == 0
            if success:
                logger.success("All packages installed successfully")
                return {pkg: True for pkg in packages}
            else:
                logger.error(f"Package installation failed: {result.stderr}")
                return {pkg: False for pkg in packages}

        except subprocess.TimeoutExpired:
            logger.error("Package installation timed out")
            return {pkg: False for pkg in packages}
        except Exception as e:
            logger.error(f"Error during package installation: {e}")
            return {pkg: False for pkg in packages}
        finally:
            self.install_in_progress = False


# Global singleton instance
dependency_manager: DependencyManager = DependencyManager()

