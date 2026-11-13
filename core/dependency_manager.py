"""
Dependency Manager for Pipeline Creator.

Detects and manages missing Python package dependencies:
- Scans requirements.txt files in module directories
- Stores detected missing dependencies
- Provides package installation functionality via pip
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from loguru import logger

from core.health_cache import health_check_cache, normalize_pkg_name
from core.paths import PROJECT_ROOT


class DependencyManager:
    """
    Manages module dependencies for Pipeline Creator.

    This class provides functionality to:
    - Scan module directories for requirements.txt files
    - Check which packages are missing
    - Install packages individually or in bulk

    Attributes:
        missing_deps: Dict mapping module names to lists of missing packages.
        install_in_progress: Flag to prevent concurrent installations.
    """

    # 5 minutes timeout for pip operations
    PIP_TIMEOUT: int = 300

    def __init__(self) -> None:
        """Initialize the dependency manager."""
        self.missing_deps: Dict[str, List[str]] = {}
        self.install_in_progress: bool = False
        self._pkg_installed_cache: Dict[str, bool] = {}

    def check_all_modules(
        self,
        modules_path: str = "modules",
        clear_existing: bool = False,
        use_cache: bool = True,
        req_files: Optional[List[Path]] = None,
        progress_callback: Optional[Callable[[str, int, int, bool], None]] = None,
    ) -> Tuple[int, int]:
        """
        Scan all modules and check for missing dependencies.

        Searches for requirements.txt files in the given path and checks
        whether each listed package is installed.

        Args:
            modules_path: Base path where modules are located.
            clear_existing: If True, clears existing missing_deps before scanning.
                           If False, merges with existing auto-detected dependencies.
            use_cache: If True, uses persistent health_check_cache for unmodified files.
            req_files: Optional pre-discovered list of requirements.txt paths.
            progress_callback: Optional callback(item_name, current_idx, total_count, from_cache).

        Returns:
            Tuple of (scanned_count, cached_count).
        """
        if clear_existing:
            self.missing_deps.clear()
            self._pkg_installed_cache.clear()

        from core.paths import is_compiled
        if is_compiled():
            logger.debug("Compiled executable mode: skipping requirements.txt scanning (incompatible modules are ignored)")
            return (0, 0)

        if req_files is None:
            full_modules_path = Path(modules_path)
            if not full_modules_path.is_absolute():
                full_modules_path = PROJECT_ROOT / modules_path

            if not full_modules_path.exists():
                logger.warning(f"Modules path '{full_modules_path}' does not exist")
                return (0, 0)

            req_files = list(full_modules_path.rglob("requirements.txt"))

        scanned_count = 0
        cached_count = 0
        total = len(req_files)

        for idx, req_file in enumerate(req_files, start=1):
            module_name = req_file.parent.name
            from_cache = False
            missing: Optional[List[str]] = None

            if use_cache:
                missing = health_check_cache.get_requirements_entry(req_file)
                if missing is not None:
                    from_cache = True
                    cached_count += 1

            if missing is None:
                missing = self._check_requirements(req_file)
                health_check_cache.set_requirements_entry(req_file, missing)
                scanned_count += 1

            if progress_callback:
                try:
                    progress_callback(f"{module_name}/requirements.txt", idx, total, from_cache)
                except Exception:
                    pass

            if missing:
                if module_name in self.missing_deps:
                    for m in missing:
                        if m not in self.missing_deps[module_name]:
                            self.missing_deps[module_name].append(m)
                else:
                    self.missing_deps[module_name] = list(missing)
                logger.info(f"Module '{module_name}' has {len(missing)} missing dependencies")

        health_check_cache.save()
        return (scanned_count, cached_count)

    def _check_requirements(self, req_file: Path) -> List[str]:
        """
        Check a single requirements.txt file for missing packages.

        Args:
            req_file: Path to the requirements.txt file.

        Returns:
            List of missing package specifications.
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
        for sep in [">=", "==", "<=", "~=", "!=", "<", ">", "[", ";"]:
            spec = spec.split(sep)[0]
        return spec.strip()

    def _is_package_installed(self, package_name: str) -> bool:
        """Check if a package is installed using pre-indexed distribution metadata or find_spec."""
        norm_name = normalize_pkg_name(package_name)
        if norm_name in self._pkg_installed_cache:
            return self._pkg_installed_cache[norm_name]

        # 1. Fast O(1) lookup in pre-indexed installed distributions
        installed_dists = health_check_cache.get_installed_packages()
        if norm_name in installed_dists or package_name.lower() in installed_dists:
            self._pkg_installed_cache[norm_name] = True
            return True

        # 2. Direct distribution check fallback
        try:
            importlib.metadata.distribution(package_name)
            self._pkg_installed_cache[norm_name] = True
            return True
        except Exception:
            pass

        # 3. Check by module import spec (fallback)
        try:
            import_name = package_name.replace("-", "_")
            if importlib.util.find_spec(import_name) is not None:
                self._pkg_installed_cache[norm_name] = True
                return True
        except Exception:
            pass

        self._pkg_installed_cache[norm_name] = False
        return False

    def get_missing_deps(self) -> Dict[str, List[str]]:
        """
        Get current missing dependencies.

        Returns:
            Copy of the missing dependencies dictionary.
        """
        return self.missing_deps.copy()

    def install_package(self, package: str) -> bool:
        """
        Install a single package using pip.

        Args:
            package: Package name to install.

        Returns:
            True if installation was successful, False otherwise.
        """
        from core.paths import is_compiled
        if is_compiled():
            logger.error(f"Cannot install package '{package}': pip is not supported in compiled executable mode")
            return False

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
                self._pkg_installed_cache.clear()
                health_check_cache.invalidate_env_cache()
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

        Args:
            packages: List of package names to install.

        Returns:
            Dictionary mapping package names to success status.
        """
        from core.paths import is_compiled
        if is_compiled():
            logger.error("Cannot install packages: pip is not supported in compiled executable mode")
            return {pkg: False for pkg in packages}

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
                self._pkg_installed_cache.clear()
                health_check_cache.invalidate_env_cache()
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
