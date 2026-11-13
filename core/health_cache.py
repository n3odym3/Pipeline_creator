"""
Persistent Cache for Module Health Checks (Dependencies, Imports, and AST Validation).

Stores validation reports, extracted imports, module metadata, and requirements.txt check
results in `.cache/health_check_cache.json`. Avoids re-parsing and re-validating unmodified
files across startups and manual health checks.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from core.paths import PROJECT_ROOT

CACHE_SCHEMA_VERSION = 1
CACHE_DIR = PROJECT_ROOT / ".cache"
CACHE_FILE = CACHE_DIR / "health_check_cache.json"

_PEP503_RE = re.compile(r"[-_.]+")


def normalize_pkg_name(name: str) -> str:
    """Normalize a Python package name according to PEP 503."""
    return _PEP503_RE.sub("-", name.strip()).lower()


class HealthCheckCache:
    """
    Manages persistent caching of module validation and dependency checks.
    """

    def __init__(self, cache_path: Path = CACHE_FILE) -> None:
        self.cache_path = cache_path
        self._lock = threading.RLock()
        self._loaded = False
        self._dirty = False
        self._data: Dict[str, Any] = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "validator_fingerprint": "",
            "files": {},
            "requirements": {},
        }
        self._installed_pkgs_cache: Optional[Set[str]] = None
        self._env_fingerprint_cache: Optional[str] = None
        self._validator_fingerprint_cache: Optional[str] = None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self.cache_path.exists():
                return
            try:
                raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("schema_version") == CACHE_SCHEMA_VERSION:
                    self._data = {
                        "schema_version": CACHE_SCHEMA_VERSION,
                        "validator_fingerprint": raw.get("validator_fingerprint", ""),
                        "files": raw.get("files", {}) if isinstance(raw.get("files"), dict) else {},
                        "requirements": (
                            raw.get("requirements", {}) if isinstance(raw.get("requirements"), dict) else {}
                        ),
                    }
            except Exception as e:
                logger.debug(f"Could not load health check cache ({self.cache_path}): {e}")

    def save(self) -> None:
        """Persist cache to disk if modified."""
        with self._lock:
            if not self._dirty:
                return
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self._data["validator_fingerprint"] = self.get_validator_fingerprint()
                tmp_path = self.cache_path.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
                tmp_path.replace(self.cache_path)
                self._dirty = False
            except Exception as e:
                logger.debug(f"Failed to save health check cache: {e}")

    def clear(self) -> None:
        """Clear all cached entries in memory and on disk."""
        with self._lock:
            self._data = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "validator_fingerprint": self.get_validator_fingerprint(),
                "files": {},
                "requirements": {},
            }
            self._loaded = True
            self._dirty = True
            self.invalidate_env_cache()
            self.save()

    def invalidate_env_cache(self) -> None:
        """Invalidate in-memory installed packages and environment fingerprint (e.g. after pip install)."""
        with self._lock:
            self._installed_pkgs_cache = None
            self._env_fingerprint_cache = None

    def get_installed_packages(self) -> Set[str]:
        """Return a set of normalized installed distribution names (fast O(1) lookup)."""
        with self._lock:
            if self._installed_pkgs_cache is not None:
                return self._installed_pkgs_cache

            installed: Set[str] = set()
            try:
                for dist in importlib.metadata.distributions():
                    try:
                        name = dist.metadata["Name"]
                    except Exception:
                        name = getattr(dist, "name", None)
                    if name:
                        installed.add(normalize_pkg_name(name))
                        installed.add(name.strip().lower())
            except Exception as e:
                logger.debug(f"Error enumerating distributions: {e}")

            self._installed_pkgs_cache = installed
            return installed

    def get_env_fingerprint(self) -> str:
        """Compute a fast fingerprint of the installed packages and Python version."""
        with self._lock:
            if self._env_fingerprint_cache is not None:
                return self._env_fingerprint_cache

            h = hashlib.sha256()
            h.update(sys.version.encode("utf-8"))
            try:
                pkgs = sorted(self.get_installed_packages())
                h.update(",".join(pkgs).encode("utf-8"))
            except Exception:
                pass

            self._env_fingerprint_cache = h.hexdigest()[:16]
            return self._env_fingerprint_cache

    def get_validator_fingerprint(self) -> str:
        """Compute a fingerprint of core validation logic files so rule updates invalidate cache."""
        with self._lock:
            if self._validator_fingerprint_cache is not None:
                return self._validator_fingerprint_cache

            core_files = [
                PROJECT_ROOT / "core" / "module_validation_manager.py",
                PROJECT_ROOT / "core" / "static_checks.py",
                PROJECT_ROOT / "core" / "ast_utils.py",
                PROJECT_ROOT / "core" / "window_base.py",
                PROJECT_ROOT / "core" / "processing_base.py",
            ]
            h = hashlib.sha256()
            for cf in core_files:
                try:
                    st = cf.stat()
                    h.update(f"{cf.name}:{st.st_mtime_ns}:{st.st_size};".encode("utf-8"))
                except OSError:
                    pass

            self._validator_fingerprint_cache = h.hexdigest()[:16]
            return self._validator_fingerprint_cache

    @staticmethod
    def _normalize_key(path: Path | str) -> str:
        p = Path(path)
        try:
            if p.is_absolute():
                return str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
            return str(p).replace("\\", "/")
        except ValueError:
            try:
                return str(p.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/")
            except Exception:
                return str(p).replace("\\", "/")

    @staticmethod
    def _file_stat_sig(path: Path) -> Optional[Tuple[int, int]]:
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    # ── Requirements.txt Cache ────────────────────────────────────────────────

    def get_requirements_entry(self, req_file: Path) -> Optional[List[str]]:
        """
        Return cached missing packages for `req_file` if unmodified and environment matches.
        Returns None on cache miss.
        """
        self._ensure_loaded()
        sig = self._file_stat_sig(req_file)
        if sig is None:
            return None

        key = self._normalize_key(req_file)
        env_fp = self.get_env_fingerprint()

        with self._lock:
            entry = self._data["requirements"].get(key)
            if not isinstance(entry, dict):
                return None
            if (
                entry.get("mtime_ns") == sig[0]
                and entry.get("size") == sig[1]
                and entry.get("env_fingerprint") == env_fp
            ):
                missing = entry.get("missing")
                if isinstance(missing, list):
                    return list(missing)
        return None

    def set_requirements_entry(self, req_file: Path, missing: List[str]) -> None:
        """Store requirements.txt check result in cache."""
        self._ensure_loaded()
        sig = self._file_stat_sig(req_file)
        if sig is None:
            return

        key = self._normalize_key(req_file)
        env_fp = self.get_env_fingerprint()

        with self._lock:
            self._data["requirements"][key] = {
                "mtime_ns": sig[0],
                "size": sig[1],
                "env_fingerprint": env_fp,
                "missing": list(missing),
            }
            self._dirty = True

    # ── Python Module File Cache ──────────────────────────────────────────────

    def get_file_entry(self, file_path: Path | str) -> Optional[Dict[str, Any]]:
        """
        Return the cached record for a Python file if its mtime_ns and size still match
        (and any recorded local ancestor files are also unchanged).
        """
        self._ensure_loaded()
        p = Path(file_path)
        sig = self._file_stat_sig(p)
        if sig is None:
            return None

        key = self._normalize_key(p)
        with self._lock:
            entry = self._data["files"].get(key)
            if not isinstance(entry, dict):
                return None
            if entry.get("mtime_ns") != sig[0] or entry.get("size") != sig[1]:
                return None

            # Verify local ancestor files if any were recorded
            ancestors = entry.get("ancestor_files")
            if isinstance(ancestors, dict):
                for anc_key, anc_mtime in ancestors.items():
                    anc_path = PROJECT_ROOT / anc_key
                    if not anc_path.exists():
                        anc_path = Path(anc_key)
                    anc_sig = self._file_stat_sig(anc_path)
                    if anc_sig is None or anc_sig[0] != anc_mtime:
                        return None

            return dict(entry)

    def update_file_entry(
        self,
        file_path: Path | str,
        *,
        has_exported_class: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None,
        imports: Optional[List[str]] = None,
        missing_imports: Optional[List[str]] = None,
        validation_report_dict: Optional[Dict[str, Any]] = None,
        ancestor_files: Optional[Dict[str, int]] = None,
    ) -> None:
        """Update or create the cache entry for a Python file."""
        self._ensure_loaded()
        p = Path(file_path)
        sig = self._file_stat_sig(p)
        if sig is None:
            return

        key = self._normalize_key(p)
        with self._lock:
            existing = self._data["files"].get(key)
            if not isinstance(existing, dict) or existing.get("mtime_ns") != sig[0] or existing.get("size") != sig[1]:
                existing = {
                    "mtime_ns": sig[0],
                    "size": sig[1],
                }

            if has_exported_class is not None:
                existing["has_exported_class"] = has_exported_class
            if metadata is not None:
                existing["metadata"] = metadata
            if imports is not None:
                existing["imports"] = list(imports)
            if missing_imports is not None:
                existing["missing_imports"] = list(missing_imports)
                existing["env_fingerprint"] = self.get_env_fingerprint()
            if validation_report_dict is not None:
                existing["validation_report"] = validation_report_dict
                existing["validator_fingerprint"] = self.get_validator_fingerprint()
            if ancestor_files is not None:
                existing["ancestor_files"] = ancestor_files

            self._data["files"][key] = existing
            self._dirty = True


health_check_cache = HealthCheckCache()
