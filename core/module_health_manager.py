"""
Unified Module Health Manager for Pipeline Creator.

Combines dependency checking and module validation into a single dialog.
"""

from __future__ import annotations

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

    Displays a single tabbed dialog when issues are found.
    """

    winID: str
    current_tab: str
    status_tag: str
    pkg_input_tags: List[Union[int, str]]

    def __init__(self) -> None:
        """Initialize the health manager."""
        self.winID = "module_health_win"
        self.current_tab = "dependencies"
        self.status_tag = f"{self.winID}_status"
        self.pkg_input_tags = []

    def show_dialog_if_needed(self, pump_frames: bool = False) -> None:
        """
        Display unified health check dialog if any issues exist.

        Checks both dependency_manager and module_validation_manager.
        Only shows dialog if at least one has issues.

        Args:
            pump_frames: If True, blocks and manually pumps DPG frames until
                        the window is closed (used at startup before main loop).
        """
        try:
            from core.config_manager import config

            general_cfg = config.get("General", {})
            if general_cfg.get("skip_import_validation", False):
                logger.debug("skip_import_validation is True - suppressing module health dialog")
                return

            from core.automation_manager import automation_manager

            if automation_manager.is_running:
                logger.debug("Automation script running - suppressing module health dialog")
                return

            from core.module_registry import get_available_modules

            get_available_modules()

            from core.dependency_manager import dependency_manager
            from core.module_validation_manager import module_validation_manager

            has_dep_issues = bool(dependency_manager.missing_deps)
            has_validation_issues = bool(module_validation_manager.validation_issues)

            logger.debug(
                f"Health check: dependencies={has_dep_issues} ({len(dependency_manager.missing_deps)} modules), "
                f"validation={has_validation_issues} ({len(module_validation_manager.validation_issues)} modules)"
            )

            if not has_dep_issues and not has_validation_issues:
                logger.success("All modules are healthy - no dependency or validation issues")
                return

            # Prevent duplicate windows
            if dpg.does_item_exist(self.winID):
                dpg.delete_item(self.winID)

            logger.info("Showing health dialog")
            self._create_dialog(has_dep_issues, has_validation_issues)

            if pump_frames:
                while dpg.is_dearpygui_running() and dpg.does_item_exist(self.winID):
                    vp_w = dpg.get_viewport_client_width()
                    vp_h = dpg.get_viewport_client_height()
                    win_size = dpg.get_item_rect_size(self.winID)
                    if win_size[0] > 0 and win_size[1] > 0:
                        pos_x = max(0, (vp_w - win_size[0]) // 2)
                        pos_y = max(0, (vp_h - win_size[1]) // 2)
                        dpg.set_item_pos(self.winID, [pos_x, pos_y])

                    automation_manager.process_pending_steps()
                    dpg.render_dearpygui_frame()

                for _ in range(2):
                    if dpg.is_dearpygui_running():
                        dpg.render_dearpygui_frame()
        except Exception as e:
            logger.error(f"Error showing module health dialog: {e}", exc_info=True)

    def _create_dialog(self, has_dep_issues: bool, has_validation_issues: bool) -> None:
        """Create the unified health check dialog."""
        from config.display_scaling import display_scaling
        from core.dependency_manager import dependency_manager
        from core.module_validation_manager import ValidationLevel, module_validation_manager

        s = display_scaling.scale

        win_w = s(900)
        win_h = s(550)

        with dpg.window(
            label="Module Health Check",
            modal=True,
            tag=self.winID,
            width=win_w,
            height=win_h,
            no_close=True,
        ):
            # Header
            issues_summary = []
            if has_dep_issues:
                dep_count = len(dependency_manager.missing_deps)
                issues_summary.append(f"{dep_count} module(s) with missing dependencies")
            if has_validation_issues:
                val_count = len(module_validation_manager.validation_issues)
                issues_summary.append(f"{val_count} module(s) with validation errors")

            dpg.add_text(f"Found issues: {', '.join(issues_summary)}", color=(255, 200, 0))
            dpg.add_separator()

            # Tabs
            with dpg.tab_bar(tag=f"{self.winID}_tabs"):
                # Dependencies tab
                if has_dep_issues:
                    with dpg.tab(label=f"Missing Dependencies ({len(dependency_manager.missing_deps)})"):
                        dpg.add_text("The following modules require additional Python packages:", wrap=760)

                        self.pkg_input_tags = []

                        with dpg.child_window(height=250, border=True):
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
                if has_validation_issues:
                    with dpg.tab(label=f"Validation Errors ({len(module_validation_manager.validation_issues)})"):
                        dpg.add_text(
                            "The following modules have code validation errors and cannot be loaded:",
                            wrap=760,
                            color=(255, 150, 150),
                        )

                        with dpg.child_window(height=250, border=True):
                            for module_path, report in module_validation_manager.validation_issues.items():
                                module_name = Path(module_path).name

                                errors = [i for i in report.issues if i.level == ValidationLevel.ERROR]
                                warnings = [i for i in report.issues if i.level == ValidationLevel.WARNING]

                                header_text = f"{module_name} ({len(errors)} errors, {len(warnings)} warnings)"

                                with dpg.collapsing_header(label=header_text, default_open=True):
                                    dpg.add_text(f"Path: {module_path}", color=(150, 150, 150))
                                    dpg.add_spacer(height=5)

                                    if errors:
                                        dpg.add_text("ERRORS:", color=(255, 100, 100))
                                        for issue in errors:
                                            location = f" [line {issue.line}]" if issue.line else ""
                                            dpg.add_text(f"  [X] {issue.message}{location}", wrap=650, indent=10)
                                            if issue.suggestion:
                                                dpg.add_text(
                                                    f"      -> {issue.suggestion}",
                                                    color=(200, 200, 200),
                                                    wrap=650,
                                                    indent=10,
                                                )
                                        dpg.add_spacer(height=5)

                                    if warnings:
                                        dpg.add_text("WARNINGS:", color=(255, 200, 0))
                                        for issue in warnings:
                                            location = f" [line {issue.line}]" if issue.line else ""
                                            dpg.add_text(f"  [!] {issue.message}{location}", wrap=650, indent=10)
                                            if issue.suggestion:
                                                dpg.add_text(
                                                    f"      -> {issue.suggestion}",
                                                    color=(200, 200, 200),
                                                    wrap=650,
                                                    indent=10,
                                                )
                        dpg.add_spacer(height=10)
                        dpg.add_button(label="Copy Validation Report", callback=self._copy_validation_report)
            dpg.add_separator()
            dpg.add_spacer(height=10)

            # Bottom buttons
            with dpg.group(horizontal=True):
                dpg.add_button(label="Continue Anyway", callback=self._close_dialog, width=200)
                if has_validation_issues:
                    dpg.add_text(
                        "  Warning: Modules with validation errors will not be available",
                        color=(255, 150, 0),
                    )

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