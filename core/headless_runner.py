"""
Headless Execution Runner for Pipeline Creator.

Allows running pipelines and AutomationManager scripts without creating a DearPyGui
viewport, OpenGL renderer, or frame loop (`dpg.render_dearpygui_frame`).

Existing WindowBase modules continue to instantiate and run inside a viewportless
DearPyGui context without requiring per-module refactoring, enabling direct
apples-to-apples performance comparison between Headless and Full GUI execution.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable, Dict, Optional

from loguru import logger

from core.app_state import app_state

_SHIMS_INSTALLED: bool = False


def strip_headless_cli_flags(argv: Optional[list[str]] = None) -> Dict[str, Any]:
    """
    Extract custom headless/benchmark CLI flags from sys.argv before
    AutomationManager.parse_args() runs so argparse does not reject them.
    """
    args = sys.argv if argv is None else argv
    opts: Dict[str, Any] = {
        "headless": False,
        "exit_on_complete": False,
        "benchmark_modules_only": False,
        "max_seconds": 0.0,
    }
    cleaned = [args[0]] if args else []
    i = 1
    while i < len(args):
        tok = args[i]
        if tok == "--headless":
            opts["headless"] = True
            app_state.headless = True
        elif tok == "--exit-on-complete":
            opts["exit_on_complete"] = True
            app_state.exit_on_script_complete = True
        elif tok == "--benchmark-modules-only":
            opts["benchmark_modules_only"] = True
        elif tok == "--max-seconds" and i + 1 < len(args):
            try:
                opts["max_seconds"] = float(args[i + 1])
            except ValueError:
                pass
            i += 1
        else:
            cleaned.append(tok)
        i += 1

    if argv is None:
        sys.argv[:] = cleaned
    return opts


def install_headless_dpg_shims() -> None:
    """
    Install safe viewport-query fallbacks on DearPyGui when running without a viewport.
    Also disables LogViewer DPG item allocation on TRACE logs to eliminate UI spam.
    """
    global _SHIMS_INSTALLED
    if _SHIMS_INSTALLED:
        return
    _SHIMS_INSTALLED = True

    import dearpygui.dearpygui as dpg

    def _wrap_viewport_getter(fn: Callable[..., Any], fallback: Any) -> Callable[..., Any]:
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            if app_state.headless:
                return fallback
            try:
                val = fn(*args, **kwargs)
                return fallback if val is None else val
            except Exception:
                return fallback

        return _wrapped

    def _wrap_noop(fn: Callable[..., Any], return_value: Any = None) -> Callable[..., Any]:
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            if app_state.headless:
                return return_value
            try:
                return fn(*args, **kwargs)
            except Exception:
                return return_value

        return _wrapped

    dpg.get_viewport_width = _wrap_viewport_getter(dpg.get_viewport_width, 1920)
    dpg.get_viewport_height = _wrap_viewport_getter(dpg.get_viewport_height, 1080)
    dpg.get_viewport_client_width = _wrap_viewport_getter(dpg.get_viewport_client_width, 1920)
    dpg.get_viewport_client_height = _wrap_viewport_getter(dpg.get_viewport_client_height, 1080)
    dpg.get_viewport_pos = _wrap_viewport_getter(dpg.get_viewport_pos, [0, 0])

    for name, retval in (
        ("set_viewport_pos", None),
        ("set_viewport_width", None),
        ("set_viewport_height", None),
        ("set_viewport_always_top", None),
        ("set_viewport_title", None),
        ("configure_viewport", None),
        ("maximize_viewport", None),
        ("toggle_viewport_fullscreen", None),
        ("show_viewport", None),
        ("is_viewport_ok", False),
        ("is_dearpygui_running", False),
        ("get_frame_rate", 0.0),
    ):
        if hasattr(dpg, name):
            setattr(dpg, name, _wrap_noop(getattr(dpg, name), return_value=retval))

    # Prevent LogViewer DPG sink from calling dpg.does_item_exist / dpg.add_text in headless mode
    try:
        from core.log_viewer import log_viewer

        log_viewer.ui_sink_enabled = False
        log_viewer.sink = lambda *args, **kwargs: None
    except Exception:
        pass


def load_benchmark_modules_only() -> int:
    """
    Fast discovery helper: load only modules under `modules/benchmark` into
    `module_registry._AVAILABLE_MODULES_CACHE` so headless micro-benchmarks boot rapidly.
    """
    from pathlib import Path
    import core.module_registry as mr
    from core.paths import MODULES_DIR

    modules_root = Path(MODULES_DIR)
    bench_dir = modules_root / "benchmark"
    if not bench_dir.exists() and modules_root.exists():
        for cand in sorted(modules_root.iterdir()):
            if cand.is_dir() and cand.name.lower() == "benchmark":
                bench_dir = cand
                break
    loaded: Dict[str, Any] = {}
    if not bench_dir.exists():
        mr._AVAILABLE_MODULES_CACHE = loaded
        return 0
    for py_file in sorted(bench_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        mod_path = f"modules.{bench_dir.name}.{py_file.stem}"
        try:
            cls = mr.load_single_module_dynamic(mod_path, str(py_file))
            if cls is not None:
                loaded[mod_path] = cls
        except Exception as e:
            logger.warning(f"Failed fast-load of {mod_path}: {e}")
    mr._AVAILABLE_MODULES_CACHE = loaded
    return len(loaded)


def run_headless_app(opts: Optional[Dict[str, Any]] = None) -> None:
    """
    Execute the Pipeline Creator runtime in headless mode inside an active DPG context.

    Caller must have already called `dpg.create_context()`.
    """
    import dearpygui.dearpygui as dpg
    from core.automation_manager import automation_manager
    from core.module_registry import clear_registry, get_available_modules

    opts = opts or {}
    app_state.headless = True
    install_headless_dpg_shims()

    logger.info("Starting Pipeline Creator in HEADLESS mode (no viewport / no GPU render loop)")

    # Discover modules (fast benchmark subset if requested, else full parity)
    t_disc_start = time.perf_counter()
    if opts.get("benchmark_modules_only"):
        n_mods = load_benchmark_modules_only()
        logger.info(f"Fast-loaded {n_mods} benchmark modules in {time.perf_counter() - t_disc_start:.3f}s")
    else:
        get_available_modules(force_reload=True)
        logger.info(f"Full module discovery completed in {time.perf_counter() - t_disc_start:.3f}s")

    # Instantiate core managers cleanly using viewportless shims
    import core.main_win  # noqa: F401

    automation_manager.parse_args()
    if not automation_manager.script_path:
        logger.warning("Headless mode invoked without --script; exiting cleanly.")
        clear_registry()
        dpg.destroy_context()
        return

    from pathlib import Path
    from core.paths import PROJECT_ROOT

    _sp = Path(automation_manager.script_path)
    if not _sp.is_absolute() and (PROJECT_ROOT / _sp).exists():
        automation_manager.script_path = str((PROJECT_ROOT / _sp).resolve())

    automation_manager.run()

    max_seconds = float(opts.get("max_seconds") or 0.0)
    exit_on_complete = bool(opts.get("exit_on_complete") or app_state.exit_on_script_complete)
    t_loop_start = time.perf_counter()
    try:
        while not app_state.close_requested:
            automation_manager.process_pending_steps()
            if exit_on_complete and automation_manager.has_finished and automation_manager._pending_tasks.empty():
                logger.info("Headless runner exiting cleanly after automation script completion.")
                break
            if max_seconds > 0.0 and (time.perf_counter() - t_loop_start) >= max_seconds:
                logger.warning(f"Headless runner reached max_seconds={max_seconds}s; exiting.")
                break
            time.sleep(0.0005)
    except KeyboardInterrupt:
        logger.info("Headless runner interrupted by KeyboardInterrupt")
    finally:
        try:
            clear_registry()
        except Exception as e:
            logger.warning(f"Headless cleanup error: {e}")
        try:
            dpg.destroy_context()
        except Exception:
            pass
        logger.info("Headless Pipeline Creator session terminated cleanly.")
        logger.info("Pipeline Creator closed successfully")
