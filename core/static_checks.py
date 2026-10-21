"""
Static (AST-based) code checks for Pipeline Creator modules.

These checks never import or execute the analysed code. They produce
non-blocking warnings that are displayed by the Module Health Manager:

- ``callback``          : DearPyGui callbacks that are fragile once compiled with Nuitka,
                          callbacks that DearPyGui will never call, or callback targets
                          that do not exist.
- ``undefined_name``    : names that are used but never defined/imported (NameError at runtime).
- ``missing_attribute`` : ``self.method()`` calls to methods that do not exist anywhere
                          in the (statically resolvable) class hierarchy.

Background on DearPyGui callbacks (``mvRunCallback`` in DearPyGui sources):
DPG reads ``callback.__code__.co_argcount``, subtracts 1 if ``PyMethod_Check(callback)``
is true, and passes exactly that many positional arguments (sender, app_data, user_data,
then ``None`` padding). Nuitka compiled bound methods are not ``PyMethod`` instances, so
``self`` is counted and DPG passes one argument too many -> ``TypeError`` that only
happens in the compiled build. A ``*args`` parameter absorbs the extra argument.
Callables without ``__code__`` (e.g. ``functools.partial``) are silently never called.
"""

from __future__ import annotations

import ast
import builtins
import symtable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from core.paths import PROJECT_ROOT

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]

CORE_DIR = PROJECT_ROOT / "core"

# Keyword arguments through which DearPyGui receives a callback.
CALLBACK_KEYWORDS = {"callback", "drag_callback", "drop_callback", "delink_callback"}

# DearPyGui functions receiving a callback positionally: name -> positional index.
CALLBACK_POSITIONAL = {
    "set_item_callback": 1,
    "set_item_drag_callback": 1,
    "set_item_drop_callback": 1,
    "set_frame_callback": 1,
    "set_exit_callback": 0,
    "set_viewport_resize_callback": 0,
}

# Base classes that never contribute members relevant to our checks.
_HARMLESS_BASES = {"object", "ABC", "abc.ABC", "Generic", "Protocol", "typing.Generic", "typing.Protocol"}

_ALLOWED_GLOBALS = set(dir(builtins)) | {
    "__file__",
    "__name__",
    "__doc__",
    "__spec__",
    "__loader__",
    "__package__",
    "__path__",
    "__builtins__",
    "__annotations__",
    "__cached__",
    "__dict__",
    "__module__",
    "__qualname__",
    "__class__",
    "__debug__",
    "__compiled__",  # Defined by Nuitka in compiled builds
}


@dataclass
class StaticIssue:
    """A single non-blocking issue found by static analysis."""

    category: str
    message: str
    line: Optional[int] = None
    suggestion: Optional[str] = None


# ---------------------------------------------------------------------------
# Cross-file class resolution
# ---------------------------------------------------------------------------


@dataclass
class ClassInfo:
    """Statically collected information about a class definition."""

    name: str
    node: ast.ClassDef
    analyzer: "ModuleAnalyzer"
    methods: Dict[str, FunctionNode] = field(default_factory=dict)
    members: Set[str] = field(default_factory=set)
    dynamic: bool = False  # Uses __getattr__/setattr with computed names: member checks unreliable

    @property
    def file(self) -> Path:
        return self.analyzer.path

    @property
    def is_core(self) -> bool:
        """True if the class is defined in the application core (out of module validation scope)."""
        try:
            self.file.resolve().relative_to(CORE_DIR.resolve())
            return True
        except ValueError:
            return False


def _expr_name(expr: ast.expr) -> str:
    """Return a dotted textual name for a base-class expression."""
    if isinstance(expr, ast.Subscript):
        return _expr_name(expr.value)
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return f"{_expr_name(expr.value)}.{expr.attr}"
    return ast.unparse(expr) if hasattr(ast, "unparse") else "<expr>"


def _target_names(target: ast.expr) -> List[str]:
    """Collect plain names bound by an assignment target."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: List[str] = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


class ModuleAnalyzer:
    """Parsed view of one source file with lightweight cross-file class resolution."""

    _cache: Dict[str, Tuple[float, "ModuleAnalyzer"]] = {}
    _MAX_IMPORT_DEPTH = 6

    def __init__(self, path: Path, source: str, tree: ast.Module) -> None:
        self.path = path
        self.source = source
        self.tree = tree
        self.classes: Dict[str, ast.ClassDef] = {}
        self.from_imports: Dict[str, Tuple[str, str, int]] = {}  # alias -> (module, name, level)
        self.module_aliases: Dict[str, Tuple[str, int]] = {}  # alias -> (module, level)
        self._class_infos: Dict[int, ClassInfo] = {}

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self.classes[node.name] = node

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.module_aliases[alias.asname] = (alias.name, 0)
                    else:
                        top = alias.name.split(".")[0]
                        self.module_aliases.setdefault(top, (top, 0))
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name
                    self.from_imports[local] = (node.module or "", alias.name, node.level or 0)

    # -- construction --------------------------------------------------------

    @classmethod
    def for_file(cls, path: Union[str, Path]) -> Optional["ModuleAnalyzer"]:
        """Return a (cached) analyzer for a file, or None if it cannot be parsed."""
        file_path = Path(path)
        try:
            key = str(file_path.resolve())
            mtime = file_path.stat().st_mtime
        except OSError:
            return None

        cached = cls._cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]

        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError, OSError, ValueError):
            return None

        analyzer = cls(file_path, source, tree)
        cls._cache[key] = (mtime, analyzer)
        return analyzer

    # -- classes ---------------------------------------------------------------

    def class_info(self, node: ast.ClassDef) -> ClassInfo:
        """Build (or return cached) ClassInfo for a class node of this file."""
        cached = self._class_infos.get(id(node))
        if cached is not None:
            return cached

        info = ClassInfo(name=node.name, node=node, analyzer=self)
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info.methods[stmt.name] = stmt
                info.members.add(stmt.name)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    info.members.update(_target_names(target))
            elif isinstance(stmt, ast.AnnAssign):
                info.members.update(_target_names(stmt.target))
            elif isinstance(stmt, ast.ClassDef):
                info.members.add(stmt.name)

        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.ctx, ast.Store)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "self"
            ):
                info.members.add(sub.attr)
            elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "setattr":
                if len(sub.args) >= 2 and isinstance(sub.args[0], ast.Name) and sub.args[0].id == "self":
                    if isinstance(sub.args[1], ast.Constant) and isinstance(sub.args[1].value, str):
                        info.members.add(sub.args[1].value)
                    else:
                        info.dynamic = True

        if "__getattr__" in info.methods or "__getattribute__" in info.methods:
            info.dynamic = True

        self._class_infos[id(node)] = info
        return info

    def get_class(self, name: str) -> Optional[ClassInfo]:
        """Return a top-level class of this file by name."""
        node = self.classes.get(name)
        return self.class_info(node) if node is not None else None

    # -- resolution ------------------------------------------------------------

    def _find_module_file(self, module: str, level: int) -> Optional[Path]:
        parts = [p for p in module.split(".") if p] if module else []
        if level > 0:
            base = self.path.parent
            for _ in range(level - 1):
                base = base.parent
            bases = [base]
        else:
            # Module directories are added to sys.path by the registry, so sibling files are importable.
            bases = [self.path.parent, PROJECT_ROOT]

        for base in bases:
            candidate = base.joinpath(*parts) if parts else base
            if parts and candidate.with_suffix(".py").is_file():
                return candidate.with_suffix(".py")
            if (candidate / "__init__.py").is_file():
                return candidate / "__init__.py"
        return None

    def _resolve_name(self, name: str, depth: int = 0) -> Optional[ClassInfo]:
        if name in self.classes:
            return self.get_class(name)
        if depth >= self._MAX_IMPORT_DEPTH or name not in self.from_imports:
            return None
        module, original, level = self.from_imports[name]
        file_path = self._find_module_file(module, level)
        if file_path is None:
            return None
        other = ModuleAnalyzer.for_file(file_path)
        return other._resolve_name(original, depth + 1) if other else None

    def resolve_base(self, expr: ast.expr) -> Optional[ClassInfo]:
        """Resolve a base-class expression to a ClassInfo, or None if not statically resolvable."""
        if isinstance(expr, ast.Subscript):
            expr = expr.value
        if isinstance(expr, ast.Name):
            return self._resolve_name(expr.id)
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            alias = expr.value.id
            file_path: Optional[Path] = None
            if alias in self.module_aliases:
                module, level = self.module_aliases[alias]
                file_path = self._find_module_file(module, level)
            elif alias in self.from_imports:
                # "from core import window_base" then "window_base.WindowBase"
                module, original, level = self.from_imports[alias]
                file_path = self._find_module_file(f"{module}.{original}" if module else original, level)
            if file_path is not None:
                other = ModuleAnalyzer.for_file(file_path)
                return other._resolve_name(expr.attr, 1) if other else None
        return None

    def ancestry(self, info: ClassInfo) -> Tuple[List[ClassInfo], List[str], bool]:
        """
        Resolve the class hierarchy of ``info``.

        Returns:
            (resolved ancestors in DFS order, names of unresolved bases, True if fully resolved)
        """
        ancestors: List[ClassInfo] = []
        unresolved: List[str] = []
        seen = {id(info.node)}

        def visit(current: ClassInfo) -> None:
            for base in current.node.bases:
                base_name = _expr_name(base)
                if base_name in _HARMLESS_BASES:
                    continue
                target = current.analyzer.resolve_base(base)
                if target is None:
                    unresolved.append(base_name)
                    continue
                if id(target.node) in seen:
                    continue
                seen.add(id(target.node))
                ancestors.append(target)
                visit(target)

        visit(info)
        return ancestors, unresolved, not unresolved


# ---------------------------------------------------------------------------
# Callback checks
# ---------------------------------------------------------------------------


class _CallbackCollector(ast.NodeVisitor):
    """Collect every expression registered as a DearPyGui callback, with its enclosing class."""

    def __init__(self) -> None:
        self.class_stack: List[ast.ClassDef] = []
        self.found: List[Tuple[ast.expr, int, Optional[ast.ClassDef]]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        current_class = self.class_stack[-1] if self.class_stack else None
        for kw in node.keywords:
            if kw.arg in CALLBACK_KEYWORDS:
                self.found.append((kw.value, kw.value.lineno, current_class))

        func_name = None
        if isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id
        if func_name in CALLBACK_POSITIONAL:
            index = CALLBACK_POSITIONAL[func_name]
            if len(node.args) > index:
                self.found.append((node.args[index], node.args[index].lineno, current_class))

        self.generic_visit(node)


def _is_partial_call(expr: ast.expr) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    return (isinstance(func, ast.Name) and func.id == "partial") or (
        isinstance(func, ast.Attribute) and func.attr == "partial"
    )


def _decorator_names(func: FunctionNode) -> Set[str]:
    names = set()
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        names.add(_expr_name(target).split(".")[-1])
    return names


def _robust_signature(func: FunctionNode) -> str:
    """Return the suggested signature with *args/**kwargs added at the correct position."""
    args = ast.arguments(
        posonlyargs=list(func.args.posonlyargs),
        args=list(func.args.args),
        vararg=func.args.vararg or ast.arg(arg="args"),
        kwonlyargs=list(func.args.kwonlyargs),
        kw_defaults=list(func.args.kw_defaults),
        kwarg=func.args.kwarg or ast.arg(arg="kwargs"),
        defaults=list(func.args.defaults),
    )
    prefix = "async def" if isinstance(func, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {func.name}({ast.unparse(args)}):"


def _find_method(
    info: ClassInfo, ancestors: List[ClassInfo], name: str
) -> Tuple[Optional[FunctionNode], Optional[ClassInfo]]:
    for owner in [info] + ancestors:
        if name in owner.methods:
            return owner.methods[name], owner
    return None, None


def check_callbacks(analyzer: ModuleAnalyzer) -> List[StaticIssue]:
    """Check every DearPyGui callback registration of a file."""
    collector = _CallbackCollector()
    collector.visit(analyzer.tree)

    issues: List[StaticIssue] = []
    # Group references per method to report each fragile method once
    fragile: Dict[int, Tuple[FunctionNode, ClassInfo, List[int]]] = {}
    missing: Dict[Tuple[int, str], List[int]] = {}

    for expr, ref_line, class_node in collector.found:
        if _is_partial_call(expr):
            issues.append(
                StaticIssue(
                    "callback",
                    "functools.partial used as callback: it has no __code__, so DearPyGui silently never calls it.",
                    line=ref_line,
                    suggestion="Use a method or lambda and pass extra data through user_data=...",
                )
            )
            continue

        # Lambdas and plain functions are not bound methods: DPG arg counting is correct for them.
        if not (
            isinstance(expr, ast.Attribute)
            and isinstance(expr.value, ast.Name)
            and expr.value.id == "self"
            and class_node is not None
        ):
            continue

        info = analyzer.class_info(class_node)
        ancestors, _, complete = analyzer.ancestry(info)
        method, owner = _find_method(info, ancestors, expr.attr)

        if method is None:
            all_members = set(info.members)
            for ancestor in ancestors:
                all_members |= ancestor.members
            dynamic = info.dynamic or any(a.dynamic for a in ancestors)
            if complete and not dynamic and expr.attr not in all_members:
                missing.setdefault((id(class_node), expr.attr), []).append(ref_line)
            continue

        if owner is None or owner.is_core:
            continue  # Core methods are out of the module validation scope

        decorators = _decorator_names(method)
        if "staticmethod" in decorators or "property" in decorators:
            continue  # staticmethod -> plain function, property -> value

        if method.args.vararg is None:
            entry = fragile.setdefault(id(method), (method, owner, []))
            entry[2].append(ref_line)

    for method, owner, ref_lines in fragile.values():
        where = "" if owner.file == analyzer.path else f" (in {owner.file.name})"
        refs = ", ".join(str(n) for n in sorted(set(ref_lines)))
        issues.append(
            StaticIssue(
                "callback",
                f"Callback '{owner.name}.{method.name}'{where} has no *args (used line {refs}). "
                "Works in Python, but in a Nuitka build DearPyGui passes one extra argument -> TypeError.",
                line=method.lineno if owner.file == analyzer.path else min(ref_lines),
                suggestion=_robust_signature(method),
            )
        )

    for (_, attr), ref_lines in missing.items():
        for ref_line in sorted(set(ref_lines)):
            issues.append(
                StaticIssue(
                    "callback",
                    f"Callback target 'self.{attr}' does not exist in the class hierarchy (AttributeError).",
                    line=ref_line,
                    suggestion=f"Define method '{attr}' or fix the callback name",
                )
            )

    return issues


def get_fragile_callback_methods(analyzer: ModuleAnalyzer) -> List[Tuple[FunctionNode, ClassInfo]]:
    """Return all fragile callback methods defined in the analyzed file that need *args."""
    collector = _CallbackCollector()
    collector.visit(analyzer.tree)
    fragile: Dict[int, Tuple[FunctionNode, ClassInfo]] = {}

    for expr, ref_line, class_node in collector.found:
        if not (
            isinstance(expr, ast.Attribute)
            and isinstance(expr.value, ast.Name)
            and expr.value.id == "self"
            and class_node is not None
        ):
            continue

        info = analyzer.class_info(class_node)
        ancestors, _, complete = analyzer.ancestry(info)
        method, owner = _find_method(info, ancestors, expr.attr)

        if method is None or owner is None or owner.is_core:
            continue
        if owner.file.resolve() != analyzer.path.resolve():
            continue

        decorators = _decorator_names(method)
        if "staticmethod" in decorators or "property" in decorators:
            continue

        if method.args.vararg is None:
            fragile[id(method)] = (method, owner)

    return list(fragile.values())



# ---------------------------------------------------------------------------
# Undefined names
# ---------------------------------------------------------------------------


class _LoadNameCollector(ast.NodeVisitor):
    """Collect loaded names with their lines, ignoring annotations (not evaluated at runtime)."""

    def __init__(self) -> None:
        self.lines: Dict[str, List[int]] = {}

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.lines.setdefault(node.id, []).append(node.lineno)

    def visit_arg(self, node: ast.arg) -> None:
        return  # Skip parameter annotations

    def _visit_function(self, node: FunctionNode) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        for stmt in node.body:
            self.visit(stmt)

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)


def _iter_tables(table: symtable.SymbolTable):
    yield table
    for child in table.get_children():
        yield from _iter_tables(child)


def check_undefined_names(analyzer: ModuleAnalyzer) -> List[StaticIssue]:
    """Report global names that are used but never defined, imported or built in."""
    tree = analyzer.tree
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return []  # Star imports make the analysis unreliable
    if "globals()" in analyzer.source or "exec(" in analyzer.source:
        return []

    try:
        top = symtable.symtable(analyzer.source, str(analyzer.path), "exec")
    except (SyntaxError, ValueError):
        return []

    bound: Set[str] = set()
    for sym in top.get_symbols():
        if sym.is_assigned() or sym.is_imported() or sym.is_namespace() or sym.is_parameter():
            bound.add(sym.get_name())

    candidates: Set[str] = set()
    for table in _iter_tables(top):
        is_module = table is top
        for sym in table.get_symbols():
            name = sym.get_name()
            if sym.is_declared_global() and sym.is_assigned():
                bound.add(name)
            if not sym.is_referenced():
                continue
            if is_module or sym.is_global():
                candidates.add(name)

    undefined = {n for n in candidates if n not in bound and n not in _ALLOWED_GLOBALS}
    if not undefined:
        return []

    collector = _LoadNameCollector()
    collector.visit(tree)

    issues: List[StaticIssue] = []
    for name in sorted(undefined):
        lines = collector.lines.get(name)
        if not lines:
            continue  # Only referenced in annotations: not evaluated at runtime
        more = f" (and {len(lines) - 1} more)" if len(lines) > 1 else ""
        issues.append(
            StaticIssue(
                "undefined_name",
                f"Undefined name '{name}'{more} -> NameError when executed.",
                line=min(lines),
                suggestion=f"Import or define '{name}', or fix the typo",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Missing self.<method>() targets
# ---------------------------------------------------------------------------


def check_self_calls(analyzer: ModuleAnalyzer) -> List[StaticIssue]:
    """Report ``self.x(...)`` calls where ``x`` is defined nowhere in a fully resolvable hierarchy."""
    issues: List[StaticIssue] = []
    for class_node in (n for n in ast.walk(analyzer.tree) if isinstance(n, ast.ClassDef)):
        info = analyzer.class_info(class_node)
        ancestors, _, complete = analyzer.ancestry(info)
        if not complete or info.dynamic or any(a.dynamic for a in ancestors):
            continue

        members = set(info.members)
        for ancestor in ancestors:
            members |= ancestor.members

        reported: Set[str] = set()
        for method in info.methods.values():
            for sub in ast.walk(method):
                if not (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "self"
                ):
                    continue
                attr = sub.func.attr
                if attr in members or attr in reported or (attr.startswith("__") and attr.endswith("__")):
                    continue
                reported.add(attr)
                issues.append(
                    StaticIssue(
                        "missing_attribute",
                        f"'self.{attr}()' is called in class '{info.name}' but is not defined "
                        "in the class or its bases -> AttributeError when executed.",
                        line=sub.lineno,
                        suggestion=f"Define method '{attr}' or fix the name",
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_static_checks(path: Union[str, Path]) -> List[StaticIssue]:
    """
    Run all static checks on a module file.

    Returns an empty list if the file cannot be parsed (syntax errors are
    reported by the structural validator / loader instead).
    """
    analyzer = ModuleAnalyzer.for_file(path)
    if analyzer is None:
        return []

    issues: List[StaticIssue] = []
    issues.extend(check_callbacks(analyzer))
    issues.extend(check_undefined_names(analyzer))
    issues.extend(check_self_calls(analyzer))
    issues.sort(key=lambda i: (i.line or 0))
    return issues
