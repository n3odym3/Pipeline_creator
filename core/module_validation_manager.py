"""
Module Validation Manager for Pipeline Creator.

Validates modules at startup using AST analysis to ensure they meet project
requirements (proper inheritance, required methods, etc.). Stores validation
errors in validation_issues dict.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, Union

from loguru import logger


class ValidationLevel(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ValidationIssue:
    level: ValidationLevel
    message: str
    line: Optional[int] = None
    suggestion: Optional[str] = None


@dataclass
class ValidationReport:
    """Complete validation report for a module."""

    module_path: str
    is_valid: bool
    issues: List[ValidationIssue] = field(default_factory=list)

    def add_error(self, message: str, line: Optional[int] = None, suggestion: Optional[str] = None) -> None:
        self.issues.append(ValidationIssue(ValidationLevel.ERROR, message, line, suggestion))
        self.is_valid = False

    def add_warning(self, message: str, line: Optional[int] = None, suggestion: Optional[str] = None) -> None:
        self.issues.append(ValidationIssue(ValidationLevel.WARNING, message, line, suggestion))

    def add_info(self, message: str, line: Optional[int] = None) -> None:
        self.issues.append(ValidationIssue(ValidationLevel.INFO, message, line))

    def __str__(self) -> str:
        """Format report for display."""
        lines = [f"\n{'='*80}"]
        lines.append(f"Module Validation Report: {Path(self.module_path).name}")
        lines.append(f"Path: {self.module_path}")
        lines.append(f"Status: {'[VALID]' if self.is_valid else '[INVALID]'}")
        lines.append(f"{'='*80}\n")

        if not self.issues:
            lines.append("[OK] No issues found!")
            return "\n".join(lines)

        errors = [i for i in self.issues if i.level == ValidationLevel.ERROR]
        warnings = [i for i in self.issues if i.level == ValidationLevel.WARNING]
        infos = [i for i in self.issues if i.level == ValidationLevel.INFO]

        if errors:
            lines.append(f"ERRORS ({len(errors)}):")
            for issue in errors:
                location = f" [line {issue.line}]" if issue.line else ""
                lines.append(f"  [X] {issue.message}{location}")
                if issue.suggestion:
                    lines.append(f"      -> {issue.suggestion}")
            lines.append("")

        if warnings:
            lines.append(f"WARNINGS ({len(warnings)}):")
            for issue in warnings:
                location = f" [line {issue.line}]" if issue.line else ""
                lines.append(f"  [!] {issue.message}{location}")
                if issue.suggestion:
                    lines.append(f"      -> {issue.suggestion}")
            lines.append("")

        if infos:
            lines.append(f"INFO ({len(infos)}):")
            for issue in infos:
                lines.append(f"  [i] {issue.message}")
            lines.append("")

        return "\n".join(lines)


class ModuleValidator:
    """Validates Pipeline Creator modules against project requirements."""

    REQUIRED_PARAMS = ["label", "win_width", "win_height", "pos", "uuid", "outputs", "visible"]
    REQUIRED_ATTRIBUTES = ["accepted_input_types", "outputs", "connections", "_persistent_fields"]
    REQUIRED_METHODS = ["input_cb"]

    def __init__(self, project_root: Optional[Union[str, Path]] = None) -> None:
        """Initialize validator."""
        if project_root is None:
            from core.paths import PROJECT_ROOT

            self.project_root = PROJECT_ROOT
        else:
            self.project_root = Path(project_root)
        self.modules_dir = self.project_root / "modules"

    def validate_module(self, module_path: str) -> ValidationReport:
        """Validate a module file."""
        report = ValidationReport(module_path=module_path, is_valid=True)

        path = Path(module_path)

        if not path.is_absolute():
            candidate1 = self.project_root / path
            candidate2 = self.modules_dir / path

            if candidate1.exists():
                path = candidate1
            elif candidate2.exists():
                path = candidate2
            else:
                path = candidate1

        if not self._validate_file_structure(path, report):
            return report

        tree = self._parse_module(path, report)
        if tree is None:
            return report

        exported_class_name = self._validate_exports(tree, report)
        if exported_class_name is None:
            return report

        class_node = self._find_class(tree, exported_class_name, report)
        if class_node is None:
            return report

        self._validate_class_inheritance(class_node, report)
        self._validate_constructor(class_node, report)
        self._validate_attributes(class_node, report)
        self._validate_methods(class_node, report)
        self._validate_imports(tree, report)
        self._check_code_quality(tree, class_node, report)

        return report

    def _validate_file_structure(self, path: Path, report: ValidationReport) -> bool:
        """Check if file exists and is properly located."""
        if not path.exists():
            report.add_error(f"File not found: {path}")
            return False

        if path.suffix != ".py":
            report.add_error("Module must be a Python file (.py)")
            return False

        try:
            path.relative_to(self.modules_dir)
        except ValueError:
            report.add_warning(
                "Module is not in the modules/ directory",
                suggestion="Move module to modules/ for auto-detection",
            )

        return True

    def _parse_module(self, path: Path, report: ValidationReport) -> Optional[ast.Module]:
        """Parse module file to AST."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            return ast.parse(source, filename=str(path))
        except SyntaxError as e:
            report.add_error(f"Syntax error: {e.msg}", line=e.lineno)
            return None
        except Exception as e:
            report.add_error(f"Failed to parse file: {e}")
            return None

    def _validate_exports(self, tree: ast.Module, report: ValidationReport) -> Optional[str]:
        """Check for EXPORTED_CLASS variable."""
        exported_class = None

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "EXPORTED_CLASS":
                        if isinstance(node.value, ast.Name):
                            exported_class = node.value.id
                        break

        if exported_class is None:
            report.add_error(
                "Missing EXPORTED_CLASS variable",
                suggestion="Add 'EXPORTED_CLASS = YourClassName' at the end of the file",
            )
            return None

        report.add_info(f"Found EXPORTED_CLASS = {exported_class}")
        return exported_class

    def _find_class(self, tree: ast.Module, class_name: str, report: ValidationReport) -> Optional[ast.ClassDef]:
        """Find the class definition."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node

        report.add_error(f"Class '{class_name}' not found in module")
        return None

    def _validate_class_inheritance(self, class_node: ast.ClassDef, report: ValidationReport) -> None:
        """Check if class inherits from WindowBase or ProcessingBase."""
        if not class_node.bases:
            report.add_error(
                "Class must inherit from WindowBase or ProcessingBase",
                line=class_node.lineno,
                suggestion="class YourClass(WindowBase):",
            )
            return

        base_names = []
        for base in class_node.bases:
            if isinstance(base, ast.Name):
                base_names.append(base.id)

        valid_bases = ["WindowBase", "ProcessingBase"]
        if not any(base in valid_bases for base in base_names):
            report.add_error(
                f"Class must inherit from WindowBase or ProcessingBase, found: {', '.join(base_names)}",
                line=class_node.lineno,
            )
        else:
            report.add_info(f"Inherits from: {', '.join(base_names)}")

    def _validate_constructor(self, class_node: ast.ClassDef, report: ValidationReport) -> None:
        """Validate __init__ method."""
        init_method = None
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                init_method = node
                break

        if init_method is None:
            report.add_error("Missing __init__ constructor", line=class_node.lineno)
            return

        param_names = [arg.arg for arg in init_method.args.args if arg.arg != "self"]

        for required_param in self.REQUIRED_PARAMS:
            if required_param not in param_names:
                report.add_warning(
                    f"Constructor missing recommended parameter: {required_param}",
                    line=init_method.lineno,
                    suggestion=f"Add {required_param} parameter to __init__",
                )

        has_super_call = False
        for node in ast.walk(init_method):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Call):
                        if isinstance(node.func.value.func, ast.Name):
                            if node.func.value.func.id == "super" and node.func.attr == "__init__":
                                has_super_call = True
                                break

        if not has_super_call:
            report.add_error(
                "Constructor must call super().__init__()",
                line=init_method.lineno,
                suggestion="Add super().__init__(...) in __init__",
            )

    def _validate_attributes(self, class_node: ast.ClassDef, report: ValidationReport) -> None:
        """Check for required attributes."""
        init_method = None
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                init_method = node
                break

        if init_method is None:
            return

        assigned_attrs = set()
        for node in ast.walk(init_method):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        if isinstance(target.value, ast.Name) and target.value.id == "self":
                            assigned_attrs.add(target.attr)

        for required_attr in self.REQUIRED_ATTRIBUTES:
            if required_attr not in assigned_attrs:
                report.add_warning(
                    f"Missing recommended attribute: self.{required_attr}",
                    suggestion=f"Add self.{required_attr} in __init__",
                )

    def _validate_methods(self, class_node: ast.ClassDef, report: ValidationReport) -> None:
        """Check for required methods."""
        method_names = []
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef):
                method_names.append(node.name)

        for required_method in self.REQUIRED_METHODS:
            if required_method not in method_names:
                report.add_error(
                    f"Missing required method: {required_method}",
                    line=class_node.lineno,
                    suggestion=f"Add 'def {required_method}(self, *args, **kwargs):' method",
                )

    def _validate_imports(self, tree: ast.Module, report: ValidationReport) -> None:
        """Check if imports are valid."""
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        required_imports = ["dearpygui.dearpygui", "core.window_base", "core.input_output_types"]
        for req_import in required_imports:
            if not any(req_import in imp for imp in imports):
                report.add_warning(
                    f"Missing common import: {req_import}",
                    suggestion=f"Most modules need: import {req_import}",
                )

    def _check_code_quality(self, tree: ast.Module, class_node: ast.ClassDef, report: ValidationReport) -> None:
        """Check code quality (warnings only)."""
        if not ast.get_docstring(class_node):
            report.add_warning(
                "Class missing docstring",
                line=class_node.lineno,
                suggestion="Add docstring to describe module functionality",
            )


class ModuleValidationManager:
    """
    Manages module validation for Pipeline Creator.

    Validates modules at startup and displays issues in a modal dialog.
    """

    def __init__(self) -> None:
        """Initialize the validation manager."""
        self.validation_issues: Dict[str, ValidationReport] = {}
        self.validator: ModuleValidator = ModuleValidator()

    def get_validation_issues(self) -> Dict[str, ValidationReport]:
        """
        Get current validation issues.

        Returns:
            Copy of the validation issues dictionary.
        """
        return self.validation_issues.copy()

    def validate_all_modules(self, modules_path: str = "modules") -> None:
        """Validate all modules in the given path."""
        self.validation_issues.clear()
        modules_base = Path(modules_path)

        if not modules_base.exists():
            logger.warning(f"Modules path '{modules_path}' does not exist")
            return

        module_files = list(modules_base.rglob("*_win.py"))

        for module_file in module_files:
            report = self.validator.validate_module(str(module_file))

            if not report.is_valid:
                self.validation_issues[str(module_file)] = report
                logger.warning(f"Module '{module_file.name}' has validation errors")


# Global singleton instance
module_validation_manager: ModuleValidationManager = ModuleValidationManager()

