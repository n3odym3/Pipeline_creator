"""
Automation Manager module for Pipeline Creator.

Manages automated JSON script execution, workflow layout loading, workspace directory setup,
and automated step dispatching.
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import dearpygui.dearpygui as dpg
from loguru import logger

from core.module_registry import MODULES_REGISTRY, PROJECT_ROOT
from core.paths import LAYOUTS_DIR
from core.working_directory_manager import working_directory_manager


class AutomationManager:
    """
    Manages automated tasks defined in a JSON script file.
    Can be used to set up the environment, load layouts, and trigger module actions on startup.
    """

    def __init__(self) -> None:
        self.script_path: Optional[str] = None
        self.node_editor: Any = None
        self._pending_tasks: queue.Queue[tuple[Any, threading.Event]] = queue.Queue()
        self._is_running: bool = False

    @property
    def is_running(self) -> bool:
        """Return True if an automation script is currently executing."""
        return self._is_running

    def process_pending_steps(self) -> None:
        """Process queued UI automation tasks on the main thread."""
        while not self._pending_tasks.empty():
            try:
                task_fn, done_event = self._pending_tasks.get_nowait()
                try:
                    task_fn()
                except Exception as exc:
                    logger.error(f"Error executing queued automation step: {exc}")
                finally:
                    if done_event is not None:
                        done_event.set()
            except queue.Empty:
                break

    def post_to_main_thread(self, fn: Any, *args: Any, wait: bool = True, timeout: float = 10.0, **kwargs: Any) -> Any:
        """Execute a callable on the main UI thread safely."""
        if threading.current_thread() is threading.main_thread():
            return fn(*args, **kwargs)

        res_holder = []
        err_holder = []

        def _wrapper() -> None:
            try:
                res_holder.append(fn(*args, **kwargs))
            except Exception as e:
                err_holder.append(e)

        if wait:
            done_event = threading.Event()
            self._pending_tasks.put((_wrapper, done_event))
            if done_event.wait(timeout=timeout):
                if err_holder:
                    raise err_holder[0]
                return res_holder[0] if res_holder else None
            else:
                logger.warning(f"post_to_main_thread timed out waiting for {fn}")
                return None
        else:
            self._pending_tasks.put((_wrapper, None))
            return None

    def parse_args(self) -> None:
        """Parse command line arguments to check for --script."""
        parser = argparse.ArgumentParser(description="Pipeline Creator Automation")
        parser.add_argument("--script", type=str, help="Path to automation script (JSON)", default=None)

        args, _ = parser.parse_known_args()

        if args.script:
            self.script_path = args.script
            logger.info(f"Automation script detected: {self.script_path}")

    def set_script_path(self, path: str) -> None:
        """Programmatically set the script path."""
        self.script_path = path

    def run_script(self, path: str) -> None:
        """Run a specific script immediately."""
        self.run(path)

    def run(self, path: Optional[str] = None) -> None:
        """Run the automation script if one was provided in a separate thread."""
        target_path = path or self.script_path
        if not target_path:
            return

        thread = threading.Thread(target=self._run_task, args=(target_path,), daemon=True)
        thread.start()

    def _run_task(self, target_path: Optional[str] = None) -> None:
        """Threaded worker for script execution."""
        path_str = target_path or self.script_path
        if not path_str:
            return

        path = Path(path_str)
        if not path.exists():
            logger.error(f"Automation script not found: {path}")
            return

        self._is_running = True
        try:
            with open(path, "r", encoding="utf-8") as f:
                script_data = json.load(f)

            logger.info(f"Running automation script: {script_data.get('name', 'Untitled')}")

            steps = script_data.get("steps", [])
            for i, step in enumerate(steps):
                logger.debug(f"Executing step {i+1}/{len(steps)}: {step.get('action')}")
                self._execute_step(step)

            logger.success("Automation script finished")

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in automation script: {e}")
        except Exception as e:
            logger.error(f"Automation failed: {e}")
        finally:
            self._is_running = False

    def _execute_step(self, step: Dict[str, Any]) -> None:
        """Dispatcher for automation steps."""
        action = step.get("action")

        if action == "wait":
            self.wait(step)
            return

        def _do_step() -> None:
            if action == "set_workspace":
                self.set_workspace(step)
            elif action == "load_pipeline":
                self.load_pipeline(step)
            elif action == "fullscreen":
                self.fullscreen(step)
            elif action == "trigger":
                self.trigger(step)
            elif action == "send_command":
                self.send_command(step)
            elif action in ("apply_view", "launch_view"):
                self.apply_view(step)
            else:
                logger.warning(f"Unknown automation action: {action}")

        if threading.current_thread() is threading.main_thread():
            _do_step()
        else:
            done_event = threading.Event()
            self._pending_tasks.put((_do_step, done_event))
            done_event.wait(timeout=30.0)

    def send_command(self, step: Dict[str, Any]) -> None:
        """Send a CMD_DICT to a specific module."""
        module_uuid = str(step.get("module_uuid")) if step.get("module_uuid") is not None else None
        command = step.get("command")

        if not module_uuid:
            logger.warning("send_command action requires 'module_uuid'")
            return

        if not command or not isinstance(command, dict):
            logger.warning("send_command action requires a dictionary 'command'")
            return

        target_module = MODULES_REGISTRY.get(module_uuid)
        if not target_module:
            logger.warning(f"Module not found for send_command: {module_uuid}")
            return
        try:
            from core.input_output_types import IOTypes

            target_module.input_cb(data=command, data_type=IOTypes.CMD_DICT)
            logger.debug(f"Sent command to {target_module.label}: {command}")
        except Exception as e:
            logger.error(f"Failed to send command to {target_module.label}: {e}")

    def set_workspace(self, step: Dict[str, Any]) -> None:
        """
        Set the working directory (CWD) of the application.
        """
        path = step.get("path")
        if path:
            working_directory_manager.set_directory(path)

    def load_pipeline(self, step: Dict[str, Any]) -> None:
        """
        Load a pipeline flow layout file (.json) and reconstruct its node graph.
        """
        path = step.get("path")
        if not path:
            return

        path_clean = str(path).lstrip("/\\")
        filename = Path(path).name

        candidates = [
            LAYOUTS_DIR / filename,
            LAYOUTS_DIR / path_clean,
            PROJECT_ROOT / path_clean,
            working_directory_manager.get_directory() / path_clean,
            Path(path),
        ]

        resolved_path = None
        for cand in candidates:
            if cand.exists() and cand.is_file():
                resolved_path = cand.resolve()
                break

        if not resolved_path:
            logger.error(
                f"Layout file not found: {path} "
                f"(Checked LAYOUTS_DIR: {LAYOUTS_DIR}, Root: {PROJECT_ROOT}, CWD: {working_directory_manager.get_directory()})"
            )
            return

        try:
            from core.main_win import main_win

            main_win.load_workspace_from_path(str(resolved_path))
            logger.info(f"Automation successfully loaded pipeline: {resolved_path.name}")
        except Exception as e:
            logger.error(f"Failed to load layout in automation: {e}")

    def apply_view(self, step: Dict[str, Any]) -> None:
        """
        Apply a saved named view layout preset.
        """
        view_name = step.get("view_name")
        if not view_name:
            logger.warning("apply_view action requires 'view_name'")
            return

        from core.module_registry import apply_named_view

        success = apply_named_view(view_name)
        if not success:
            logger.warning(f"Automation failed to apply view: {view_name}")

    def fullscreen(self, step: Dict[str, Any]) -> None:
        """
        Maximize the application viewport to fullscreen.
        """
        enabled = step.get("enabled", True)
        if enabled:
            dpg.maximize_viewport()
            logger.debug("Toggled fullscreen")

    def trigger(self, step: Dict[str, Any]) -> None:
        """
        Call a method on a target module instance.
        """
        module_uuid = step.get("module_uuid")
        target_module = None

        if module_uuid:
            target_module = MODULES_REGISTRY.get(module_uuid)

        module_type = step.get("module_type")
        if not target_module and module_type:
            for mod in MODULES_REGISTRY.values():
                if mod.__class__.__name__ == module_type:
                    target_module = mod
                    break

        if not target_module:
            logger.warning(f"Module not found for trigger: {step}")
            return

        method_name = step.get("method")
        params = step.get("params", {})

        if method_name and hasattr(target_module, method_name):
            try:
                method = getattr(target_module, method_name)
                method(**params)
                logger.info(f"Triggered {method_name} on {target_module}")
            except Exception as e:
                logger.error(f"Failed to trigger {method_name}: {e}")
        else:
            logger.warning(f"Method {method_name} not found on module {target_module}")

    def wait(self, step: Dict[str, Any]) -> None:
        """
        Introduce a delay (sleep) in the execution thread.
        """
        seconds = step.get("seconds", 1.0)
        time.sleep(seconds)
        logger.info(f"Waited {seconds}s")


# Global singleton instance
automation_manager: AutomationManager = AutomationManager()

