"""
Module Validation Manager for Pipeline Creator.

Validates modules at startup using AST analysis to ensure they meet project
requirements for both WindowBase (GUI modules) and ProcessingBase (background workers).
Integrates deep static checks (DearPyGui callback signatures for Nuitka robustness,
undefined names, and missing methods). Stores validation errors and warnings
in the validation_issues dictionary.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Type, Union

from loguru import logger

from core.static_checks import ModuleAnalyzer, run_static_checks


class ValidationLevel(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class ModuleKind(Enum):
    WINDOW = "WindowBase"
    PROCESSING = "ProcessingBase"
    VIEWPORT_PROCESS = "ViewportProcessBase"
    UNKNOWN = "Unknown"


@dataclass
class ValidationIssue:
    level: ValidationLevel
    message: str
    line: Optional[int] = None
    suggestion: Optional[str] = None
    category: str = "general"  # "callback", "inheritance", "structure", "runtime", "attribute", "general"


@dataclass
class ValidationReport:
    """Complete validation report for a module."""

    module_path: str
    is_valid: bool = True
    module_kind: ModuleKind = ModuleKind.UNKNOWN
    issues: List[ValidationIssue] = field(default_factory=list)

    def add_error(
        self,
        message: str,
        line: Optional[int] = None,
        suggestion: Optional[str] = None,
        category: str = "general",
    ) -> None:
        self.issues.append(ValidationIssue(ValidationLevel.ERROR, message, line, suggestion, category))
        self.is_valid = False

    def add_warning(
        self,
        message: str,
        line: Optional[int] = None,
        suggestion: Optional[str] = None,
        category: str = "general",
    ) -> None:
        self.issues.append(ValidationIssue(ValidationLevel.WARNING, message, line, suggestion, category))

    def add_info(
        self,
        message: str,
        line: Optional[int] = None,
        category: str = "general",
    ) -> None:
        self.issues.append(ValidationIssue(ValidationLevel.INFO, message, line, category=category))

    @property
    def has_errors(self) -> bool:
        return any(i.level == ValidationLevel.ERROR for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.level == ValidationLevel.WARNING for i in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == ValidationLevel.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == ValidationLevel.WARNING]

    @property
    def has_issues(self) -> bool:
        """Return True if there are any errors or warnings (excludes purely informational notes)."""
        return self.has_errors or self.has_warnings

    @property
    def callback_issues(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.category == "callback"]

    def __str__(self) -> str:
        """Format report for display."""
        lines = [f"\n{'='*80}"]
        lines.append(f"Module Validation Report: {Path(self.module_path).name} [{self.module_kind.value}]")
        lines.append(f"Path: {self.module_path}")
        lines.append(f"Status: {'[VALID]' if self.is_valid else '[INVALID]'}")
        lines.append(f"{'='*80}\n")

        if not self.issues:
            lines.append("[OK] No issues found!")
            return "\n".join(lines)

        errors = self.errors
        warnings = self.warnings
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

    # Requirements specific to WindowBase GUI modules
    WINDOW_REQUIRED_PARAMS = ["label", "win_width", "win_height", "pos", "uuid", "visible"]
    WINDOW_REQUIRED_ATTRIBUTES = ["accepted_input_types", "outputs"]
    WINDOW_REQUIRED_METHODS = ["input_cb"]

    # Requirements specific to ProcessingBase background modules
    PROCESSING_REQUIRED_METHODS = ["_process_data"]

    def __init__(self, project_root: Optional[Union[str, Path]] = None) -> None:
        """Initialize validator."""
        if project_root is None:
            from core.paths import PROJECT_ROOT

            self.project_root = PROJECT_ROOT
        else:
            self.project_root = Path(project_root)
        self.modules_dir = self.project_root / "modules"

    def validate_module(self, module_path: str) -> ValidationReport:
        """Validate a module file against WindowBase or ProcessingBase requirements."""
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

        # Determine module kind (WindowBase vs ProcessingBase) with cross-file resolution
        module_kind = self._detect_and_validate_inheritance(path, class_node, report)
        report.module_kind = module_kind

        # Validate according to the specific base class
        if module_kind == ModuleKind.PROCESSING:
            self._validate_processing_module(class_node, tree, report)
        else:
            # Default to WindowBase requirements
            self._validate_window_module(class_node, tree, report)

        # General checks
        self._check_code_quality(tree, class_node, report)

        # Static analysis: DPG callbacks (Nuitka robustness), undefined names, self calls
        self._run_static_analysis(path, report)

        return report

    def _validate_file_structure(self, path: Path, report: ValidationReport) -> bool:
        """Check if file exists and is properly located."""
        if not path.exists():
            report.add_error(f"File not found: {path}", category="structure")
            return False

        if path.suffix != ".py":
            report.add_error("Module must be a Python file (.py)", category="structure")
            return False

        try:
            path.relative_to(self.modules_dir)
        except ValueError:
            report.add_warning(
                "Module is not in the modules/ directory",
                suggestion="Move module to modules/ for auto-detection",
                category="structure",
            )

        return True

    def _parse_module(self, path: Path, report: ValidationReport) -> Optional[ast.Module]:
        """Parse module file to AST."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            return ast.parse(source, filename=str(path))
        except SyntaxError as e:
            report.add_error(f"Syntax error: {e.msg}", line=e.lineno, category="structure")
            return None
        except Exception as e:
            report.add_error(f"Failed to parse file: {e}", category="structure")
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
                category="structure",
            )
            return None

        report.add_info(f"Found EXPORTED_CLASS = {exported_class}", category="structure")
        return exported_class

    def _find_class(self, tree: ast.Module, class_name: str, report: ValidationReport) -> Optional[ast.ClassDef]:
        """Find the class definition."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node

        report.add_error(f"Class '{class_name}' not found in module", category="structure")
        return None

    def _detect_and_validate_inheritance(
        self,
        path: Path,
        class_node: ast.ClassDef,
        report: ValidationReport,
    ) -> ModuleKind:
        """
        Determine if class inherits from WindowBase, ProcessingBase, or ViewportProcessBase.
        Supports both direct inheritance (ast.Name, ast.Attribute) and deep cross-file hierarchy.
        """
        if not class_node.bases:
            report.add_error(
                "Class must inherit from WindowBase or ProcessingBase",
                line=class_node.lineno,
                suggestion="class YourClass(WindowBase): or class YourClass(ProcessingBase):",
                category="inheritance",
            )
            return ModuleKind.UNKNOWN

        # Extract textual names of immediate bases
        direct_base_names: List[str] = []
        for base in class_node.bases:
            if isinstance(base, ast.Name):
                direct_base_names.append(base.id)
            elif isinstance(base, ast.Attribute):
                direct_base_names.append(base.attr)

        # Quick check on direct bases
        if any(b in ("ProcessingBase",) for b in direct_base_names):
            report.add_info("Inherits directly from ProcessingBase", line=class_node.lineno, category="inheritance")
            return ModuleKind.PROCESSING
        if any(b in ("ViewportProcessBase",) for b in direct_base_names):
            report.add_info("Inherits directly from ViewportProcessBase", line=class_node.lineno, category="inheritance")
            return ModuleKind.VIEWPORT_PROCESS
        if any(b in ("WindowBase",) for b in direct_base_names):
            report.add_info("Inherits directly from WindowBase", line=class_node.lineno, category="inheritance")
            return ModuleKind.WINDOW

        # Deep resolution via ModuleAnalyzer
        analyzer = ModuleAnalyzer.for_file(path)
        if analyzer is not None:
            info = analyzer.class_info(class_node)
            ancestors, unresolved, complete = analyzer.ancestry(info)
            ancestor_names = {a.name for a in ancestors}

            if "ProcessingBase" in ancestor_names:
                report.add_info(
                    f"Inherits from ProcessingBase via {[a.name for a in ancestors]}",
                    line=class_node.lineno,
                    category="inheritance",
                )
                return ModuleKind.PROCESSING

            if "ViewportProcessBase" in ancestor_names:
                report.add_info(
                    f"Inherits from ViewportProcessBase via {[a.name for a in ancestors]}",
                    line=class_node.lineno,
                    category="inheritance",
                )
                return ModuleKind.VIEWPORT_PROCESS

            if "WindowBase" in ancestor_names:
                report.add_info(
                    f"Inherits from WindowBase via {[a.name for a in ancestors]}",
                    line=class_node.lineno,
                    category="inheritance",
                )
                return ModuleKind.WINDOW

            if not complete and unresolved:
                # Some bases could not be resolved statically (e.g. dynamic or external package)
                report.add_warning(
                    f"Class inherits from unresolved base(s): {', '.join(unresolved)}. "
                    "Ensure the base class ultimately inherits from WindowBase or ProcessingBase.",
                    line=class_node.lineno,
                    category="inheritance",
                )
                # Treat as window module tentatively
                return ModuleKind.WINDOW

        report.add_error(
            f"Class must inherit from WindowBase or ProcessingBase, found: {', '.join(direct_base_names)}",
            line=class_node.lineno,
            suggestion="class YourClass(WindowBase): or class YourClass(ProcessingBase):",
            category="inheritance",
        )
        return ModuleKind.UNKNOWN

    def _validate_window_module(
        self,
        class_node: ast.ClassDef,
        tree: ast.Module,
        report: ValidationReport,
    ) -> None:
        """Validate requirements for WindowBase GUI modules."""
        self._validate_window_constructor(class_node, report)
        self._validate_window_attributes(class_node, report)
        self._validate_window_methods(class_node, report)
        self._validate_window_imports(tree, report)

    def _validate_window_constructor(self, class_node: ast.ClassDef, report: ValidationReport) -> None:
        """Validate __init__ for WindowBase modules."""
        init_method = None
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                init_method = node
                break

        if init_method is None:
            # Subclass inherits WindowBase.__init__ directly, valid in Python
            return

        param_names = [arg.arg for arg in init_method.args.args if arg.arg != "self"]
        has_kwarg = init_method.args.kwarg is not None

        # If constructor accepts **kwargs, arbitrary WindowBase parameters are safely forwarded
        if not has_kwarg:
            for required_param in self.WINDOW_REQUIRED_PARAMS:
                if required_param not in param_names:
                    report.add_warning(
                        f"Constructor missing recommended parameter: {required_param}",
                        line=init_method.lineno,
                        suggestion=f"Add {required_param} or **kwargs to __init__",
                        category="structure",
                    )

        has_super_call = self._has_super_init_call(init_method)
        if not has_super_call:
            report.add_error(
                "Constructor must call super().__init__()",
                line=init_method.lineno,
                suggestion="Add super().__init__(...) in __init__",
                category="structure",
            )

    def _validate_window_attributes(self, class_node: ast.ClassDef, report: ValidationReport) -> None:
        """Check for recommended WindowBase attributes."""
        assigned_attrs = set()

        # 1. Check class-level attributes
        for node in class_node.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned_attrs.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assigned_attrs.add(node.target.id)

        # 2. Check instance-level attributes in __init__
        init_method = None
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                init_method = node
                break

        if init_method is not None:
            for node in ast.walk(init_method):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute):
                            if isinstance(target.value, ast.Name) and target.value.id == "self":
                                assigned_attrs.add(target.attr)

        for required_attr in self.WINDOW_REQUIRED_ATTRIBUTES:
            if required_attr not in assigned_attrs:
                report.add_warning(
                    f"Missing recommended attribute: self.{required_attr}",
                    suggestion=f"Add self.{required_attr} in __init__ or at class level",
                    category="structure",
                )

    def _validate_window_methods(self, class_node: ast.ClassDef, report: ValidationReport) -> None:
        """Check for required WindowBase methods (e.g. input_cb)."""
        method_names = [n.name for n in class_node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

        for required_method in self.WINDOW_REQUIRED_METHODS:
            if required_method not in method_names:
                report.add_error(
                    f"Missing required method: {required_method}",
                    line=class_node.lineno,
                    suggestion=f"Add 'def {required_method}(self, *args, **kwargs):' method",
                    category="structure",
                )

    def _validate_window_imports(self, tree: ast.Module, report: ValidationReport) -> None:
        """Check recommended imports for WindowBase modules."""
        imports = self._collect_imports(tree)

        required_imports = ["dearpygui.dearpygui", "core.window_base", "core.input_output_types"]
        for req_import in required_imports:
            if not any(req_import in imp for imp in imports):
                report.add_warning(
                    f"Missing common import: {req_import}",
                    suggestion=f"Most GUI modules need: import {req_import}",
                    category="structure",
                )

    def _validate_processing_module(
        self,
        class_node: ast.ClassDef,
        tree: ast.Module,
        report: ValidationReport,
    ) -> None:
        """Validate requirements for ProcessingBase background worker modules."""
        # 1. Check constructor super call if __init__ is defined
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                if not self._has_super_init_call(node):
                    report.add_error(
                        "Processing module constructor must call super().__init__()",
                        line=node.lineno,
                        suggestion="Add super().__init__(params=params, ...) in __init__",
                        category="structure",
                    )
                break

        # 2. Check that _process_data is overridden
        method_names = [n.name for n in class_node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if "_process_data" not in method_names:
            report.add_error(
                "Processing module must override '_process_data(self, data, params)'",
                line=class_node.lineno,
                suggestion="Define 'def _process_data(self, data: Any, params: Dict[str, Any]) -> Any:'",
                category="structure",
            )
        else:
            report.add_info("Found '_process_data' implementation", category="structure")

        # 3. Check imports
        imports = self._collect_imports(tree)
        if not any("core.processing_base" in imp or "ProcessingBase" in imp for imp in imports):
            report.add_warning(
                "Missing import: core.processing_base",
                suggestion="from core.processing_base import ProcessingBase",
                category="structure",
            )

    def _has_super_init_call(self, func_node: ast.FunctionDef) -> bool:
        """Check whether a function calls super().__init__(...)."""
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "__init__" and isinstance(node.func.value, ast.Call):
                    if isinstance(node.func.value.func, ast.Name) and node.func.value.func.id == "super":
                        return True
        return False

    def _collect_imports(self, tree: ast.Module) -> List[str]:
        """Collect list of import strings from module AST."""
        imports: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
                    for alias in node.names:
                        imports.append(f"{node.module}.{alias.name}")
        return imports

    def _check_code_quality(self, tree: ast.Module, class_node: ast.ClassDef, report: ValidationReport) -> None:
        """Check code quality (docstrings, etc.)."""
        if not ast.get_docstring(class_node):
            report.add_warning(
                "Class missing docstring",
                line=class_node.lineno,
                suggestion="Add docstring to describe module functionality",
                category="general",
            )

    def _run_static_analysis(self, path: Path, report: ValidationReport) -> None:
        """
        Run static checks (DPG callbacks, undefined names, missing self calls).
        Callbacks without *args are recorded as WARNING (non-blocking for standard python,
        crucial warning for Nuitka compiled mode).
        """
        try:
            static_issues = run_static_checks(path)
            for issue in static_issues:
                if issue.category == "callback":
                    if "does not exist" in issue.message:
                        report.add_error(
                            issue.message,
                            line=issue.line,
                            suggestion=issue.suggestion,
                            category="callback",
                        )
                    else:
                        report.add_warning(
                            issue.message,
                            line=issue.line,
                            suggestion=issue.suggestion,
                            category="callback",
                        )
                elif issue.category == "undefined_name":
                    report.add_warning(
                        issue.message,
                        line=issue.line,
                        suggestion=issue.suggestion,
                        category="general",
                    )
                elif issue.category == "missing_attribute":
                    report.add_warning(
                        issue.message,
                        line=issue.line,
                        suggestion=issue.suggestion,
                        category="general",
                    )
        except Exception as e:
            logger.debug(f"Static analysis encountered an error on {path}: {e}")


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

    @property
    def has_blocking_errors(self) -> bool:
        """Check if any module has blocking errors."""
        return any(report.has_errors for report in self.validation_issues.values())

    @property
    def has_warnings(self) -> bool:
        """Check if any module has validation warnings."""
        return any(report.has_warnings for report in self.validation_issues.values())

    @property
    def has_issues(self) -> bool:
        """Check if any module has errors or warnings."""
        return any(report.has_issues for report in self.validation_issues.values())

    @property
    def has_callback_warnings(self) -> bool:
        """Check if any module has callback issues."""
        return any(bool(report.callback_issues) for report in self.validation_issues.values())

    def validate_all_modules(self, modules_path: Optional[Union[str, Path]] = None) -> None:
        """Validate all modules in the given path."""
        self.validation_issues.clear()
        if modules_path is None:
            modules_base = self.validator.modules_dir
        else:
            p = Path(modules_path)
            modules_base = p if p.is_absolute() else (self.validator.project_root / p)

        if not modules_base.exists():
            logger.warning(f"Modules path '{modules_base}' does not exist")
            return

        for module_file in modules_base.rglob("*.py"):
            if module_file.name == "__init__.py" or "__pycache__" in module_file.parts:
                continue

            try:
                content = module_file.read_text(encoding="utf-8")
            except Exception:
                continue

            if "EXPORTED_CLASS" not in content:
                continue

            report = self.validator.validate_module(str(module_file))

            # Store only modules that have actual errors or warnings (exclude purely healthy modules with INFO)
            if report.has_issues:
                self.validation_issues[str(module_file)] = report
                if not report.is_valid:
                    logger.warning(f"Module '{module_file.name}' has validation errors")
                else:
                    logger.info(f"Module '{module_file.name}' has {len(report.warnings)} validation warning(s)")


# Global singleton instance
module_validation_manager: ModuleValidationManager = ModuleValidationManager()
