"""
Unified Module Health Manager for Pipeline Creator.

Combines dependency checking and module validation into a single dialog,
with real-time progress bar feedback for manual scans and persistent caching support.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import dearpygui.dearpygui as dpg
from loguru import logger


class ModuleHealthManager:
    """
    Unified manager for module health checks.

    Aggregates issues from:
    - dependency_manager: missing Python packages
    - module_validation_manager: code validation errors

    Displays a single tabbed dialog when issues are found (or when triggered manually).
    """

    winID: str
    progress_winID: str
    current_tab: str
    status_tag: str
    pkg_input_tags: List[Union[int, str]]
    _scan_in_progress: bool

    def __init__(self) -> None:
        """Initialize the health manager."""
        self.winID = "module_health_win"
        self.progress_winID = "module_health_progress_win"
        self.current_tab = "dependencies"
        self.status_tag = f"{self.winID}_status"
        self.pkg_input_tags = []
        self._scan_in_progress = False

    def run_manual_health_check(self, use_cache: bool = True) -> None:
        """
        Run a full health check manually with a live progress bar modal window.

        Always executes even when `skip_import_validation` is enabled in config.json,
        and displays the health check report dialog upon completion.

        Args:
            use_cache: If True, uses `.cache/health_check_cache.json` for unmodified modules.
                       If False, clears the cache and forces a full rescan of all files.
        """
        if self._scan_in_progress:
            logger.warning("Health check is already in progress")
            return

        self._scan_in_progress = True

        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)
        if dpg.does_item_exist(self.progress_winID):
            dpg.delete_item(self.progress_winID)

        from config.display_scaling import display_scaling

        s = display_scaling.scale
        bar_w = s(480)

        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - bar_w) // 2)
        pos_y = max(0, (vp_h - s(140)) // 2)

        bar_tag = f"{self.progress_winID}_bar"
        item_tag = f"{self.progress_winID}_item"
        stats_tag = f"{self.progress_winID}_stats"

        mode_label = "Cached Smart Scan" if use_cache else "Full Forced Rescan (Cache Cleared)"

        with dpg.window(
            label="Module Health Check - Scanning...",
            tag=self.progress_winID,
            pos=[pos_x, pos_y],
            autosize=True,
            no_scrollbar=True,
            show=True,
            no_close=True,
            no_resize=True,
            no_collapse=True,
        ):
            dpg.add_text(f"Running ({mode_label})...", color=(100, 200, 255))
            dpg.add_spacer(height=s(4))
            dpg.add_progress_bar(
                tag=bar_tag,
                default_value=0.0,
                overlay="",
                width=bar_w,
            )
            dpg.add_spacer(height=s(4))
            dpg.add_text("Discovering modules...", tag=item_tag, color=(200, 200, 200))
            dpg.add_text("Progress: 0%  |  Scanned: 0  |  From cache: 0", tag=stats_tag, color=(150, 150, 150))

        dpg.focus_item(self.progress_winID)

        def _on_progress(
            current_label: str,
            step_idx: int,
            total_steps: int,
            scanned_count: int,
            cached_count: int,
        ) -> None:
            frac = (step_idx / total_steps) if total_steps > 0 else 1.0
            pct = int(frac * 100)
            try:
                if dpg.does_item_exist(bar_tag):
                    dpg.set_value(bar_tag, frac)
                if dpg.does_item_exist(item_tag):
                    dpg.set_value(item_tag, current_label)
                if dpg.does_item_exist(stats_tag):
                    dpg.set_value(
                        stats_tag,
                        f"Progress: {step_idx}/{total_steps} ({pct}%)  |  Scanned: {scanned_count}  |  From cache: {cached_count}",
                    )
            except Exception:
                pass

        def _worker() -> None:
            from core.automation_manager import automation_manager
            from core.module_registry import scan_modules_health

            stats: Dict[str, Any] = {}
            try:
                logger.info(f"Manual health check started (use_cache={use_cache})...")
                stats = scan_modules_health(
                    base_path="modules",
                    use_cache=use_cache,
                    progress_callback=_on_progress,
                )
                # Ensure a brief moment if scan was sub-millisecond so user sees 100% on the progress bar
                if stats.get("elapsed_ms", 0.0) < 120.0:
                    time.sleep(0.12)
            except Exception as e:
                logger.error(f"Error during manual health check scan: {e}", exc_info=True)
            finally:
                def _finish_on_main() -> None:
                    self._scan_in_progress = False
                    if dpg.does_item_exist(self.progress_winID):
                        dpg.delete_item(self.progress_winID)
                    self.show_dialog_if_needed(
                        pump_frames=False,
                        force_show=True,
                        scan_stats=stats,
                    )

                automation_manager.post_to_main_thread(_finish_on_main, wait=False)

        threading.Thread(target=_worker, daemon=True).start()

    def show_dialog_if_needed(
        self,
        pump_frames: bool = False,
        force_show: bool = False,
        scan_stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Display unified health check dialog if any issues exist (or if `force_show` is True).

        Checks both dependency_manager and module_validation_manager.
        At startup (`force_show=False`), respects `skip_import_validation` in config.json.
        When triggered manually (`force_show=True`), always runs and displays the dialog.

        Args:
            pump_frames: If True, blocks and manually pumps DPG frames until
                         the window is closed (used at startup before main loop).
            force_show: If True, ignores `skip_import_validation` and displays the dialog
                        even when all modules are healthy.
            scan_stats: Optional dictionary with scan timing and cache statistics.
        """
        try:
            from core.config_manager import config

            general_cfg = config.get("General", {})
            if not force_show and general_cfg.get("skip_import_validation", False):
                logger.debug("skip_import_validation is True - suppressing startup module health dialog")
                return

            from core.automation_manager import automation_manager

            if not force_show and automation_manager.is_running:
                logger.debug("Automation script running - suppressing module health dialog")
                return

            from core.module_registry import get_available_modules

            get_available_modules()

            from core.dependency_manager import dependency_manager
            from core.module_validation_manager import module_validation_manager
            from core.paths import is_compiled

            compiled = is_compiled()
            has_dep_issues = bool(dependency_manager.missing_deps) if not compiled else False
            has_validation_issues = any(
                report.has_issues for report in module_validation_manager.validation_issues.values()
            )

            issues_count = sum(1 for r in module_validation_manager.validation_issues.values() if r.has_issues)
            logger.debug(
                f"Health check: dependencies={has_dep_issues} ({len(dependency_manager.missing_deps)} modules, compiled={compiled}), "
                f"validation={has_validation_issues} ({issues_count} modules)"
            )

            if not has_dep_issues and not has_validation_issues and not force_show:
                logger.success("All modules are healthy - no dependency or validation issues")
                return

            # Prevent duplicate windows
            if dpg.does_item_exist(self.winID):
                dpg.delete_item(self.winID)

            logger.info("Showing health dialog")
            self._create_dialog(
                has_dep_issues,
                has_validation_issues,
                scan_stats=scan_stats,
                modal=pump_frames,
            )

            if pump_frames:
                while dpg.is_dearpygui_running() and dpg.does_item_exist(self.winID):
                    automation_manager.process_pending_steps()
                    dpg.render_dearpygui_frame()

                    if not dpg.does_item_exist(self.winID):
                        break

                    try:
                        vp_w = dpg.get_viewport_client_width()
                        vp_h = dpg.get_viewport_client_height()
                        win_size = dpg.get_item_rect_size(self.winID)
                        if win_size[0] > 0 and win_size[1] > 0:
                            pos_x = max(0, (vp_w - win_size[0]) // 2)
                            pos_y = max(0, (vp_h - win_size[1]) // 2)
                            dpg.set_item_pos(self.winID, [pos_x, pos_y])
                    except Exception:
                        pass

                for _ in range(2):
                    if dpg.is_dearpygui_running():
                        dpg.render_dearpygui_frame()
        except Exception as e:
            logger.error(f"Error showing module health dialog: {e}", exc_info=True)

    def _create_dialog(
        self,
        has_dep_issues: bool,
        has_validation_issues: bool,
        scan_stats: Optional[Dict[str, Any]] = None,
        modal: bool = False,
    ) -> None:
        """Create the unified health check dialog."""
        from config.display_scaling import display_scaling
        from core.dependency_manager import dependency_manager
        from core.module_validation_manager import ValidationLevel, module_validation_manager

        s = display_scaling.scale

        win_w = s(920)
        win_h = s(580)

        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
        pos_x = max(0, (vp_w - win_w) // 2)
        pos_y = max(0, (vp_h - win_h) // 2)

        # Categorize modules
        modules_with_errors = [
            (path, rep) for path, rep in module_validation_manager.validation_issues.items() if rep.has_errors
        ]
        modules_with_warnings = [
            (path, rep)
            for path, rep in module_validation_manager.validation_issues.items()
            if not rep.has_errors and rep.has_warnings
        ]
        total_callback_issues = sum(
            len(rep.callback_issues) for rep in module_validation_manager.validation_issues.values()
        )

        with dpg.window(
            label="Module Health Check",
            modal=modal,
            show=True,
            tag=self.winID,
            width=win_w,
            height=win_h,
            pos=[pos_x, pos_y],
            no_collapse=True,
        ):
            # Header summary
            issues_summary = []
            if has_dep_issues:
                dep_count = len(dependency_manager.missing_deps)
                issues_summary.append(f"{dep_count} module(s) missing dependencies")
            if modules_with_errors:
                issues_summary.append(f"{len(modules_with_errors)} module(s) with errors")
            if modules_with_warnings:
                issues_summary.append(f"{len(modules_with_warnings)} module(s) with warnings")
            if total_callback_issues > 0:
                issues_summary.append(f"{total_callback_issues} fragile DPG callback(s)")

            if issues_summary:
                dpg.add_text(f"Status: {', '.join(issues_summary)}", color=(255, 200, 0))
            else:
                dpg.add_text("Status: All modules are healthy! No issues detected.", color=(50, 220, 100))

            if scan_stats:
                elapsed = scan_stats.get("elapsed_ms", 0.0)
                scanned = scan_stats.get("scanned", 0)
                cached = scan_stats.get("cached", 0)
                total = scan_stats.get("total_files", scanned + cached)
                dpg.add_text(
                    f"Last scan: {total} files checked in {elapsed:.1f} ms "
                    f"({cached} loaded from cache, {scanned} freshly analyzed)",
                    color=(150, 170, 200),
                )

            dpg.add_separator()

            # Tabs
            with dpg.tab_bar(tag=f"{self.winID}_tabs"):
                if not has_dep_issues and not has_validation_issues:
                    with dpg.tab(label="All Healthy"):
                        dpg.add_spacer(height=s(20))
                        dpg.add_text(
                            "All modules passed dependency, import, and static code validation checks!",
                            color=(50, 220, 100),
                        )
                        dpg.add_spacer(height=s(8))
                        dpg.add_text(
                            "Unmodified modules are cached in .cache/health_check_cache.json to accelerate "
                            "subsequent checks. Use 'Force Full Rescan' below if you want to bypass the cache.",
                            wrap=s(850),
                            color=(180, 180, 180),
                        )

                # Dependencies tab
                if has_dep_issues:
                    with dpg.tab(label=f"Missing Dependencies ({len(dependency_manager.missing_deps)})"):
                        dpg.add_text("The following modules require additional Python packages:", wrap=760)

                        self.pkg_input_tags = []

                        with dpg.child_window(height=280, border=True):
                            for module, packages in dependency_manager.missing_deps.items():
                                dpg.add_text(f"Module: {module}", color=(100, 200, 255))

                                for pkg in packages:
                                    try:
                                        with dpg.group(horizontal=True):
                                            input_tag = dpg.generate_uuid()
                                            self.pkg_input_tags.append(input_tag)

                                            dpg.add_button(
                                                label="Install",
                                                callback=self._install_from_input,
                                                user_data=input_tag,
                                                width=100,
                                            )
                                            dpg.add_input_text(
                                                default_value=pkg,
                                                tag=input_tag,
                                                width=300,
                                                hint="Package name (editable)",
                                            )
                                    except Exception as e:
                                        logger.error(f"Error creating input row for {module} - {pkg}: {e}")

                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Install All", callback=self._install_all)
                            dpg.add_text("  (This may take a moment)")

                        dpg.add_text("", tag=self.status_tag, color=(150, 150, 150))

                # Validation tab
                val_reports = {
                    p: r for p, r in module_validation_manager.validation_issues.items()
                    if r.has_issues
                }
                if has_validation_issues and val_reports:
                    val_tab_label = f"Validation ({len(val_reports)})"
                    with dpg.tab(label=val_tab_label):
                        dpg.add_text(
                            "Modules code validation and diagnostics (WindowBase / ProcessingBase):",
                            wrap=760,
                        )

                        with dpg.child_window(height=280, border=True):
                            for module_path, report in val_reports.items():
                                module_name = Path(module_path).name
                                kind_tag = f"[{report.module_kind.value}]" if report.module_kind else ""
                                cache_badge = " [cached]" if getattr(report, "from_cache", False) else ""

                                errors = report.errors
                                warnings = report.warnings

                                status_prefix = "[X]" if errors else "[!]"
                                header_text = (
                                    f"{status_prefix} {module_name} {kind_tag}{cache_badge} - "
                                    f"{len(errors)} error(s), {len(warnings)} warning(s)"
                                )

                                with dpg.collapsing_header(label=header_text, default_open=bool(errors)):
                                    dpg.add_text(f"Path: {module_path}", color=(150, 150, 150))
                                    dpg.add_spacer(height=5)

                                    if errors:
                                        dpg.add_text("ERRORS (blocking - module cannot be loaded):", color=(255, 100, 100))
                                        for issue in errors:
                                            location = f" [line {issue.line}]" if issue.line else ""
                                            cat = f" [{issue.category}]" if issue.category != "general" else ""
                                            dpg.add_text(f"  [X]{cat} {issue.message}{location}", wrap=650, indent=10)
                                            if issue.suggestion:
                                                dpg.add_text(
                                                    f"      -> {issue.suggestion}",
                                                    color=(200, 200, 200),
                                                    wrap=650,
                                                    indent=10,
                                                )
                                        dpg.add_spacer(height=5)

                                    if warnings:
                                        dpg.add_text("WARNINGS / CODE QUALITY:", color=(255, 200, 0))
                                        for issue in warnings:
                                            location = f" [line {issue.line}]" if issue.line else ""
                                            cat = f" [{issue.category}]" if issue.category != "general" else ""
                                            dpg.add_text(f"  [!]{cat} {issue.message}{location}", wrap=650, indent=10)
                                            if issue.suggestion:
                                                dpg.add_text(
                                                    f"      -> {issue.suggestion}",
                                                    color=(200, 200, 200),
                                                    wrap=650,
                                                    indent=10,
                                                )

                        dpg.add_spacer(height=5)
                        dpg.add_button(label="Copy Validation Report", callback=self._copy_validation_report)

                # Dedicated DPG Callbacks tab if any callback issues exist
                if total_callback_issues > 0:
                    with dpg.tab(label=f"DPG Callbacks ({total_callback_issues})"):
                        dpg.add_text(
                            "DearPyGui Callbacks - Nuitka Compatibility Check:",
                            color=(100, 200, 255),
                        )
                        dpg.add_text(
                            "In standard Python, DearPyGui callbacks can omit '*args'. However, when compiled "
                            "with Nuitka, bound methods are inspected differently by DearPyGui's C extension, "
                            "passing more arguments than expected and causing a runtime TypeError.\n"
                            "Adding '*args, **kwargs' (or '*args') to these callbacks ensures safe execution both in Python and Nuitka.",
                            wrap=760,
                            color=(200, 200, 200),
                        )
                        with dpg.group(horizontal=True):
                            dpg.add_button(
                                label="Auto-fix All Callbacks (*args, **kwargs)",
                                callback=self._auto_fix_all_callbacks,
                            )
                            dpg.add_text("", tag=f"{self.winID}_cb_status", color=(100, 255, 100))
                        dpg.add_spacer(height=5)

                        with dpg.child_window(height=240, border=True):
                            for module_path, report in module_validation_manager.validation_issues.items():
                                cb_issues = report.callback_issues
                                if not cb_issues:
                                    continue

                                module_name = Path(module_path).name
                                with dpg.collapsing_header(label=f"{module_name} ({len(cb_issues)} callback issues)", default_open=True):
                                    with dpg.group(horizontal=True):
                                        dpg.add_text(f"Path: {module_path}", color=(150, 150, 150))
                                        dpg.add_button(
                                            label=f"Fix {module_name}",
                                            callback=self._auto_fix_single_module,
                                            user_data=module_path,
                                        )
                                    dpg.add_spacer(height=3)
                                    for issue in cb_issues:
                                        loc = f" [line {issue.line}]" if issue.line else ""
                                        col = (255, 100, 100) if issue.level == ValidationLevel.ERROR else (255, 200, 0)
                                        dpg.add_text(f"  * {issue.message}{loc}", color=col, wrap=650, indent=10)
                                        if issue.suggestion:
                                            dpg.add_text(f"    Suggested signature: {issue.suggestion}", color=(150, 255, 150), indent=15)
                                    dpg.add_spacer(height=5)

            dpg.add_separator()
            dpg.add_spacer(height=10)

            # Bottom buttons
            with dpg.group(horizontal=True):
                close_label = "Continue Anyway" if (has_dep_issues or has_validation_issues) else "Close"
                dpg.add_button(label=close_label, callback=self._close_dialog, width=s(160))
                dpg.add_button(
                    label="Rescan (Cached)",
                    callback=lambda *a: self.run_manual_health_check(use_cache=True),
                    width=s(140),
                )
                dpg.add_button(
                    label="Force Full Rescan (Clear Cache)",
                    callback=lambda *a: self.run_manual_health_check(use_cache=False),
                    width=s(210),
                )
                if modules_with_errors:
                    dpg.add_text(
                        "  Warning: Modules with validation errors will not be available",
                        color=(255, 100, 100),
                    )
                elif modules_with_warnings or total_callback_issues > 0:
                    dpg.add_text(
                        "  Info: All modules loaded. Warnings do not prevent running in Python.",
                        color=(100, 200, 255),
                    )

        dpg.focus_item(self.winID)

    def _copy_validation_report(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
    ) -> None:
        """Copy full validation report to clipboard."""
        from core.module_validation_manager import module_validation_manager

        report_text = "Module Validation Report\n" + "=" * 80 + "\n\n"

        for _, report in module_validation_manager.validation_issues.items():
            report_text += str(report) + "\n\n"

        try:
            dpg.set_clipboard_text(report_text)
            logger.info("Validation report copied to clipboard")
        except Exception as e:
            logger.error(f"Failed to copy to clipboard: {e}")

    def _auto_fix_all_callbacks(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
    ) -> None:
        """Automatically fix all fragile callbacks across all modules."""
        from core.callback_fixer import fix_all_callbacks
        from core.dependency_manager import dependency_manager
        from core.module_validation_manager import module_validation_manager
        from core.paths import MODULES_DIR

        status_tag = f"{self.winID}_cb_status"
        if dpg.does_item_exist(status_tag):
            dpg.set_value(status_tag, "Applying automatic fixes...")

        results = fix_all_callbacks(MODULES_DIR, create_backup=True)
        total_fixed = sum(len(methods) for methods in results.values())

        # Re-run validation on all modules (modified files automatically bypass cache via mtime)
        module_validation_manager.validate_all_modules(MODULES_DIR, use_cache=True)

        msg = f"Fixed {total_fixed} callback(s) across {len(results)} file(s)!"
        logger.success(msg)

        # Refresh dialog to reflect new clean state
        from core.paths import is_compiled
        has_dep = bool(dependency_manager.missing_deps) if not is_compiled() else False
        has_val = any(rep.has_issues for rep in module_validation_manager.validation_issues.values())
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)
        if has_dep or has_val:
            self._create_dialog(has_dep, has_val)
        else:
            logger.success("All issues resolved - health check passed!")

    def _auto_fix_single_module(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
    ) -> None:
        """Automatically fix callbacks in a single module file."""
        from core.callback_fixer import fix_callback_signatures_in_file
        from core.dependency_manager import dependency_manager
        from core.health_cache import health_check_cache
        from core.module_validation_manager import module_validation_manager

        module_path = user_data
        if not module_path:
            return

        count, fixed = fix_callback_signatures_in_file(module_path, create_backup=True)

        # Re-validate this module (freshly modified, force uncached validation and save cache)
        rep = module_validation_manager.validator.validate_module(module_path, use_cache=False)
        health_check_cache.save()
        if rep.has_issues:
            module_validation_manager.validation_issues[module_path] = rep
        else:
            module_validation_manager.validation_issues.pop(module_path, None)

        logger.success(f"Fixed {count} callback(s) in {Path(module_path).name}: {', '.join(fixed)}")

        # Refresh dialog
        from core.paths import is_compiled
        has_dep = bool(dependency_manager.missing_deps) if not is_compiled() else False
        has_val = any(rep.has_issues for rep in module_validation_manager.validation_issues.values())
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)
        if has_dep or has_val:
            self._create_dialog(has_dep, has_val)
        else:
            logger.success("All issues resolved - health check passed!")

    def _close_dialog(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
    ) -> None:
        """Close the health check dialog."""
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

        logger.info("User dismissed module health check dialog")

    def _install_from_input(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
    ) -> None:
        """Install a single package using the name from the input field."""
        from core.dependency_manager import dependency_manager

        input_tag = user_data
        if not dpg.does_item_exist(input_tag):
            return

        package = dpg.get_value(input_tag).strip()
        if not package:
            self._show_status("Please enter a package name", color=(255, 200, 0))
            return

        dpg.configure_item(input_tag, enabled=False)
        dpg.configure_item(sender, enabled=False)

        self._show_status(f"Installing {package}...", color=(100, 200, 255))

        success = dependency_manager.install_package(package)

        if success:
            self._show_status(f"Installed {package}!", color=(0, 255, 0))
        else:
            self._show_status(f"Failed to install {package}", color=(255, 100, 100))
            dpg.configure_item(sender, enabled=True)
            dpg.configure_item(input_tag, enabled=True)

    def _install_all(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
    ) -> None:
        """Install all missing packages from input fields."""
        from core.dependency_manager import dependency_manager

        all_packages = set()

        if hasattr(self, "pkg_input_tags"):
            for input_tag in self.pkg_input_tags:
                if dpg.does_item_exist(input_tag):
                    value = dpg.get_value(input_tag).strip()
                    if value:
                        all_packages.add(value)

        if not all_packages:
            self._show_status("No packages to install", color=(150, 150, 150))
            return

        package_list = list(all_packages)
        self._show_status(f"Installing {len(package_list)} packages...", color=(100, 200, 255))

        results = dependency_manager.install_packages(package_list)

        failed = [pkg for pkg, success in results.items() if not success]

        if failed:
            self._show_status(f"Installation failed for {len(failed)} packages", color=(255, 100, 100))
        else:
            self._show_status("All packages installed successfully!", color=(0, 255, 0))
            self._show_success_dialog()

    def _show_status(self, message: str, color: Tuple[int, int, int] = (150, 150, 150)) -> None:
        """Update the status text in the dialog."""
        if dpg.does_item_exist(self.status_tag):
            dpg.set_value(self.status_tag, message)
            dpg.configure_item(self.status_tag, color=color)

    def _show_success_dialog(self) -> None:
        """Show a success message after installation."""
        if dpg.does_item_exist(self.winID):
            dpg.delete_item(self.winID)

        success_win = "dependency_success_win"
        if dpg.does_item_exist(success_win):
            dpg.delete_item(success_win)

        with dpg.window(
            label="Installation Complete",
            modal=True,
            tag=success_win,
            width=400,
            height=150,
            pos=(400, 300),
            no_close=True,
        ):
            dpg.add_text("All dependencies installed successfully!")
            dpg.add_spacer(height=10)
            dpg.add_text("Please restart Pipeline Creator to use the new modules.", wrap=380)
            dpg.add_spacer(height=15)
            dpg.add_button(label="OK", callback=lambda: dpg.delete_item(success_win), width=-1)


# Global singleton instance
module_health_manager: ModuleHealthManager = ModuleHealthManager()
