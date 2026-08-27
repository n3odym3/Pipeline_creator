"""
Documentation Manager for Pipeline Creator.

Handles serving local MkDocs documentation, opening the browser,
and cleaning up background subprocesses.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import dearpygui.dearpygui as dpg
from loguru import logger

from config.display_scaling import display_scaling
from core.config_manager import config
from core.paths import PROJECT_ROOT


class DocumentationManager:
    """
    Manager for launching and managing the local MkDocs server.
    """

    def __init__(self) -> None:
        self.error_win_id: str = "doc_error_win"
        self.process: Optional[subprocess.Popen[str]] = None
        self.port: Optional[int] = None

    def find_free_port(self, start_port: int = 8000) -> int:
        """Find an available port starting from start_port."""
        for port in range(start_port, start_port + 50):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        return start_port

    def is_running(self) -> bool:
        """Check if the mkdocs server process is running."""
        return self.process is not None and self.process.poll() is None

    def start_server(self) -> None:
        """Start the mkdocs local server and open the browser."""
        if self.is_running():
            return

        mkdocs_file = PROJECT_ROOT / "mkdocs.yml"
        if not mkdocs_file.exists():
            logger.error("mkdocs.yml not found in project root.")
            self._show_error_dialog(
                "Configuration Error",
                f"mkdocs.yml was not found in the project root:\n{PROJECT_ROOT}",
            )
            return

        # Dynamically re-index modules documentation before starting the server
        self._reindex_modules_docs()

        self.port = self.find_free_port(8000)
        try:
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE

            # Run Python module mkdocs serve on the found local port
            cmd = [sys.executable, "-m", "mkdocs", "serve", "-a", f"127.0.0.1:{self.port}"]
            logger.info(f"Starting mkdocs server on port {self.port}: {' '.join(cmd)}")

            self.process = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                startupinfo=startupinfo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Wait briefly to see if it immediately fails/exits
            time.sleep(1.5)
            if self.process.poll() is not None:
                _, stderr = self.process.communicate()
                logger.error(f"MkDocs failed to start: {stderr}")
                self.process = None
                self._show_error_dialog(
                    "MkDocs Launch Failed",
                    "Failed to start MkDocs. Please ensure 'mkdocs' and its extensions are installed:\n\n"
                    "pip install mkdocs mkdocs-material mkdocstrings-python\n\n"
                    f"Details:\n{stderr}",
                )
                return

            log_mkdocs = config.get("Debug", {}).get("log_mkdocs", False)

            def consume_stream(stream: Any) -> None:
                try:
                    for line in stream:
                        cleaned = line.strip()
                        if not cleaned or not log_mkdocs:
                            continue
                        if "ERROR" in cleaned or "CRITICAL" in cleaned:
                            logger.bind(mkdocs=True).error(f"[MkDocs] {cleaned}")
                        elif "WARNING" in cleaned:
                            logger.bind(mkdocs=True).warning(f"[MkDocs] {cleaned}")
                        else:
                            logger.bind(mkdocs=True).info(f"[MkDocs] {cleaned}")
                except Exception:
                    pass

            if self.process.stdout:
                threading.Thread(target=consume_stream, args=(self.process.stdout,), daemon=True).start()
            if self.process.stderr:
                threading.Thread(target=consume_stream, args=(self.process.stderr,), daemon=True).start()

            logger.info(f"Opening browser at http://127.0.0.1:{self.port}")
            webbrowser.open(f"http://127.0.0.1:{self.port}")
            self.update_menu_visibility()

        except Exception as e:
            logger.error(f"Failed to spawn mkdocs subprocess: {e}")
            self.process = None
            self._show_error_dialog("Subprocess Error", f"Could not launch the MkDocs process:\n{e}")

    def stop_server(self) -> None:
        """Stop the mkdocs subprocess."""
        if self.process:
            logger.info("Stopping mkdocs server...")
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception as e:
                logger.warning(f"Error terminating mkdocs process gracefully: {e}")
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        self.update_menu_visibility()

    def stop(self) -> None:
        """Stop method mapping for application cleanup on shutdown."""
        self.stop_server()

    def reopen_link(self) -> None:
        """Reopen the documentation website in the default browser."""
        if self.port:
            logger.info(f"Reopening browser at http://127.0.0.1:{self.port}")
            webbrowser.open(f"http://127.0.0.1:{self.port}")

    def update_menu_visibility(self) -> None:
        """Update display status of Documentation menu items dynamically based on server status."""
        running = self.is_running()
        if dpg.does_item_exist("doc_open_item"):
            dpg.configure_item("doc_open_item", show=running)
        if dpg.does_item_exist("doc_start_item"):
            dpg.configure_item("doc_start_item", show=not running)
        if dpg.does_item_exist("doc_stop_item"):
            dpg.configure_item("doc_stop_item", show=running)

    def _show_error_dialog(self, title: str, message: str) -> None:
        """Helper to show a nice error/warning dialog."""
        s = display_scaling.scale
        if dpg.does_item_exist(self.error_win_id):
            dpg.delete_item(self.error_win_id)

        with dpg.window(
            label=title,
            tag=self.error_win_id,
            width=s(450),
            height=s(220),
            modal=True,
            pos=(s(300), s(200)),
        ):
            dpg.add_text("An error occurred:", color=(255, 100, 100))
            dpg.add_spacer(height=s(5))
            dpg.add_text(message, wrap=s(430))
            dpg.add_spacer(height=s(15))
            dpg.add_button(label="OK", callback=lambda: dpg.delete_item(self.error_win_id), width=s(80))

    def _reindex_modules_docs(self) -> None:
        """Dynamically scan the modules directory and update mkdocs.yml navigation."""
        modules_dir = PROJECT_ROOT / "modules"
        docs_modules_dir = PROJECT_ROOT / "docs" / "modules"
        mkdocs_yml_path = PROJECT_ROOT / "mkdocs.yml"

        logger.info("Scanning and indexing modules documentation...")
        try:
            # 1. Clean and recreate docs/modules directory
            if docs_modules_dir.exists():
                try:
                    shutil.rmtree(docs_modules_dir)
                except Exception:
                    for root, dirs, files in os.walk(docs_modules_dir, topdown=False):
                        for name in files:
                            filepath = Path(root) / name
                            try:
                                os.chmod(filepath, stat.S_IWRITE)
                                os.unlink(filepath)
                            except Exception:
                                pass
                        for name in dirs:
                            dirpath = Path(root) / name
                            try:
                                os.rmdir(dirpath)
                            except Exception:
                                pass
            docs_modules_dir.mkdir(parents=True, exist_ok=True)

            if not modules_dir.exists():
                logger.warning(f"Modules directory {modules_dir} does not exist.")
                return

            # 2. Build hierarchical tree of directories containing .md files
            nav_tree: Dict[str, Any] = {"files": {}, "children": {}}

            for root, dirs, files in os.walk(modules_dir):
                dirs[:] = [d for d in dirs if not d.startswith("__") and d != "__pycache__"]

                md_files = [f for f in files if f.lower().endswith(".md")]
                if not md_files:
                    continue

                rel_dir = Path(root).relative_to(modules_dir)
                dst_dir = docs_modules_dir / rel_dir
                dst_dir.mkdir(parents=True, exist_ok=True)

                parts = rel_dir.parts
                current = nav_tree
                for part in parts:
                    current = current["children"].setdefault(part, {"files": {}, "children": {}})

                for f in md_files:
                    src_path = Path(root) / f
                    try:
                        if f.lower() == "readme.md":
                            shutil.copy2(src_path, dst_dir / "index.md")
                            title = rel_dir.name if rel_dir != Path(".") else "Modules Documentation"
                            if rel_dir == Path("."):
                                nav_tree["files"][title] = "modules/index.md"
                            else:
                                current["files"][title] = f"modules/{'/'.join(parts)}/index.md"
                        else:
                            shutil.copy2(src_path, dst_dir / f)
                            title = Path(f).stem
                            if rel_dir == Path("."):
                                nav_tree["files"][title] = f"modules/{f}"
                            else:
                                current["files"][title] = f"modules/{'/'.join(parts)}/{f}"
                    except (PermissionError, OSError) as pe:
                        logger.debug(f"Could not copy module doc file {f} (read-only destination): {pe}")

            # 3. Generate YAML lines recursively
            def node_to_yaml_lines(name: str, node: dict[str, Any], indent_level: int) -> list[str]:
                indent = " " * indent_level
                children = node["children"]
                files = node["files"]

                if not children and not files:
                    return []

                lines = [f"{indent}- {name}:"]
                for f_title in sorted(files.keys()):
                    f_path = files[f_title]
                    lines.append(f"{indent}    - {f_title}: {f_path}")

                for child_name in sorted(children.keys()):
                    child_node = children[child_name]
                    lines.extend(node_to_yaml_lines(child_name, child_node, indent_level + 4))
                return lines

            if not nav_tree["files"] and not nav_tree["children"]:
                logger.info("No module documentation files found to index; keeping existing mkdocs.yml navigation.")
                return

            nav_lines = ["  - Modules Documentation:"]
            for f_title in sorted(nav_tree["files"].keys()):
                nav_lines.append(f"      - {f_title}: {nav_tree['files'][f_title]}")
            for child_name in sorted(nav_tree["children"].keys()):
                child_node = nav_tree["children"][child_name]
                nav_lines.extend(node_to_yaml_lines(child_name, child_node, indent_level=6))

            # 4. Read mkdocs.yml and update content between markers
            with open(mkdocs_yml_path, "r", encoding="utf-8") as f:
                content = f.read()

            pattern = r"(# \[DYNAMIC_MODULES_START\]\n).*?(\n\s*# \[DYNAMIC_MODULES_END\])"
            if not re.search(pattern, content, flags=re.DOTALL):
                logger.error("Could not find dynamic modules markers in mkdocs.yml")
                return

            nav_content = "\n".join(nav_lines)
            new_content = re.sub(pattern, r"\1" + nav_content + r"\2", content, flags=re.DOTALL)

            try:
                with open(mkdocs_yml_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                logger.success("Modules documentation dynamically re-indexed.")
            except (PermissionError, OSError) as pe:
                logger.warning(f"mkdocs.yml is read-only ({pe}). Dynamic re-indexing skipped.")
        except (PermissionError, OSError) as pe:
            logger.warning(
                f"Documentation directory is read-only (e.g. Program Files installation). Skipping dynamic re-indexing: {pe}"
            )
        except Exception as e:
            logger.exception(f"Error dynamically indexing modules documentation: {e}")


# Global singleton instance
doc_manager: DocumentationManager = DocumentationManager()

