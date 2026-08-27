if __name__ == "__main__":
    import sys
    import time

    # Handle self-execution calls (like sys.executable -m mkdocs) in compiled/frozen mode
    if len(sys.argv) > 2 and sys.argv[1] == "-m":
        module_name = sys.argv[2]

        sys.argv = [sys.argv[0]] + sys.argv[3:]
        try:
            if module_name == "mkdocs":
                # Ensure entry points for mkdocs plugins and themes work in compiled mode
                try:
                    import importlib.metadata
                    from importlib.metadata import EntryPoint

                    _orig_entry_points = importlib.metadata.entry_points

                    def _patched_entry_points(*args, **kwargs):
                        group = kwargs.get("group")
                        if not group and args:
                            group = args[0]
                        try:
                            res = _orig_entry_points(*args, **kwargs)
                        except Exception:
                            res = []

                        if hasattr(res, "select"):
                            eps = list(res.select(group=group)) if group else list(res)
                        elif isinstance(res, dict):
                            eps = list(res.get(group, [])) if group else []
                        else:
                            eps = list(res)

                        if group == "mkdocs.plugins":
                            names = {getattr(ep, "name", "") for ep in eps}
                            if "search" not in names:
                                eps.append(
                                    EntryPoint(
                                        name="search",
                                        value="mkdocs.contrib.search:SearchPlugin",
                                        group="mkdocs.plugins",
                                    )
                                )
                            if "mkdocstrings" not in names:
                                eps.append(
                                    EntryPoint(
                                        name="mkdocstrings",
                                        value="mkdocstrings.plugin:MkdocstringsPlugin",
                                        group="mkdocs.plugins",
                                    )
                                )
                        elif group == "mkdocs.themes":
                            names = {getattr(ep, "name", "") for ep in eps}
                            if "material" not in names:
                                eps.append(EntryPoint(name="material", value="material", group="mkdocs.themes"))
                            if "mkdocs" not in names:
                                eps.append(EntryPoint(name="mkdocs", value="mkdocs.themes.mkdocs", group="mkdocs.themes"))

                        return eps

                    importlib.metadata.entry_points = _patched_entry_points
                except Exception:
                    pass

                cli = None
                try:
                    mkdocs_cli = __import__("mkdocs.cli", fromlist=["cli"])
                    cli = mkdocs_cli.cli
                except Exception:
                    try:
                        mkdocs_main = __import__("mkdocs.__main__", fromlist=["cli"])
                        cli = mkdocs_main.cli
                    except Exception:
                        mkdocs_main = __import__("mkdocs.__main__", fromlist=["main"])
                        cli = mkdocs_main.main

                if cli is not None:
                    cli()
                    sys.exit(0)
                else:
                    raise ImportError("Could not find mkdocs CLI entry point")
            elif module_name == "pip":
                pip_main = None
                try:
                    pip_internal = __import__("pip._internal", fromlist=["main"])
                    pip_main = pip_internal.main
                except Exception:
                    try:
                        pip_module = __import__("pip", fromlist=["main"])
                        pip_main = pip_module.main
                    except Exception:
                        pass

                if pip_main is not None:
                    sys.exit(pip_main())
                else:
                    raise ImportError("Could not find pip entry point")
            else:
                import runpy

                runpy.run_module(module_name, run_name="__main__", alter_sys=True)
                sys.exit(0)
        except Exception as e:
            import traceback

            traceback.print_exc(file=sys.stderr)
            print(f"Error running module {module_name}: {e}", file=sys.stderr)
            sys.exit(1)

    sys.coinit_flags = 2
    try:
        import comtypes.client

        comtypes.client.gen_dir = None
    except ImportError:
        pass
    import ctypes
    import multiprocessing
    from pathlib import Path

    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    multiprocessing.freeze_support()

    from core.config_manager import config

    # Hide/show console window at runtime based on configuration for compiled executables
    if sys.platform == "win32" and (getattr(sys, "frozen", False) or "__compiled__" in globals()):
        try:
            window_cfg = config.get("Window", {})
            if not window_cfg.get("show_console", True):
                hwnd = ctypes.windll.kernel32.GetConsoleWindow()
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass

    from core.paths import LOGS_DIR, PROJECT_ROOT
    from core.splash import close_splash, show_splash, update_splash_status

    # Display splash screen
    splash_cfg = config.get("Splash", {})
    if splash_cfg.get("enabled", True):
        splash_image = splash_cfg.get("image", "ressources/Polypy.png")
        splash_size = splash_cfg.get("size", 400)
        splash_thickness = splash_cfg.get("thickness", 2)
        splash_path = PROJECT_ROOT / splash_image
        show_splash(str(splash_path), max_size=splash_size, thickness=splash_thickness)
        update_splash_status("Loading configuration...", "Starting application")

    from core.log_viewer import log_viewer
    from core.module_registry import MODULES_REGISTRY, clear_registry, load_workspace
    from core.working_directory_manager import working_directory_manager
    from loguru import logger

    update_splash_status("Applying default settings...", "Starting application")
    working_directory_manager.apply_default_config()

    debug_cfg = config.get("Debug", {})
    if not debug_cfg.get("enabled", False):
        logger.disable("__main__")
    else:
        LOGS_DIR.mkdir(exist_ok=True)

        logger.configure(
            handlers=[
                {
                    "sink": sys.stdout,
                    "level": debug_cfg.get("log_level", "INFO"),
                },
                {
                    "sink": str(LOGS_DIR / "app.log"),
                    "level": "DEBUG",
                    "rotation": "10 MB",
                    "retention": "1 month",
                },
                {
                    "sink": log_viewer.sink,
                    "level": debug_cfg.get("log_level", "INFO"),
                },
            ]
        )

    update_splash_status("Initializing DearPyGui...", "Initializing interface")
    import dearpygui.dearpygui as dpg

    dpg.create_context()

    from config.display_scaling import display_scaling

    display_scaling.detect_display()

    vp_width = int(display_scaling.screen_width * 0.85)
    vp_height = int(display_scaling.screen_height * 0.85)

    window_config = config.get("Window", {})
    use_vsync = window_config.get("vsync", False)

    icon_cfg = config.get("Icon", {})
    icon_path = PROJECT_ROOT / icon_cfg.get("path", "ressources/icon.ico")

    general_cfg = config.get("General", {})
    dpg.create_viewport(
        title=general_cfg.get("app_name", "Pipeline Creator"),
        width=vp_width,
        height=vp_height,
        vsync=use_vsync,
        small_icon=str(icon_path) if icon_path.exists() else None,
        large_icon=str(icon_path) if icon_path.exists() else None,
    )

    dpg.setup_dearpygui()

    dpg.configure_app(docking=False)
    dpg.configure_app(win32_alt_enter_fullscreen=True)
    dpg.configure_app(wait_for_input=False)

    update_splash_status("Loading UI theme...", "Initializing interface")
    from config.theme_manager import theme_manager

    ui_cfg = config.get("UI", {})
    _theme_name = ui_cfg.get("theme_name", "DEFAULT_DARK")
    theme_manager.load_theme(_theme_name)

    if sys.platform == "win32":
        try:
            ES_CONTINUOUS = 0x80000000
            ES_DISPLAY_REQUIRED = 0x00000002
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
            logger.debug(f"Windows: sleep prevention enabled, display scale={display_scaling.scale_factor:.2f}")
        except Exception as e:
            logger.warning(f"Failed to set Windows features: {e}")

    update_splash_status("Preloading tutorial mascot assets...", "Loading modules")
    from core.tutorial_manager import tutorial_manager

    try:
        tutorial_manager._init_ui_components()
    except Exception as e:
        logger.error(f"Failed to preload tutorial mascot assets: {e}")

    update_splash_status("Scanning and validating modules...", "Loading modules")
    from core.module_registry import get_available_modules

    get_available_modules(force_reload=True)

    from core.main_win import fusion_manager, main_win, node_editor

    dpg.set_primary_window(main_win.winID, True)

    dpg.bind_item_theme(main_win.winID, theme_manager.create_main_win_theme())

    dpg.show_viewport()
    theme_manager.update_titlebar()

    close_splash()

    dpg.set_viewport_always_top(True)
    dpg.set_viewport_always_top(False)

    from core.module_health_manager import module_health_manager

    module_health_manager.show_dialog_if_needed(pump_frames=True)

    _login_cfg = config.get("Login", {})
    _default_mode = _login_cfg.get("default_mode", "user")

    from core.app_state import app_state

    if _login_cfg.get("enabled", False):
        from core.login_dialog import show_login_dialog

        show_login_dialog(default_mode=_default_mode, pump_frames=True)
    else:
        app_state.username = "User"
        app_state.mode = _default_mode
        app_state.login_done = True
        logger.debug(f"Login skipped - mode='{app_state.mode}'")

    main_win.apply_mode_visibility()

    _system_tray = None
    if window_config.get("minimize_to_tray", False):
        import core.system_tray as st_module
        from core.system_tray import SystemTray

        _system_tray = SystemTray(title=general_cfg.get("app_name", "Pipeline Creator"))
        st_module.system_tray = _system_tray
        _system_tray.start()

    logger.info(f"Starting the app  [user='{app_state.username}'  mode='{app_state.mode}']")

    from core.automation_manager import automation_manager

    automation_manager.parse_args()

    paths_config = config.get("Paths", {})
    loaded_anything = False

    if not automation_manager.script_path:
        default_script = paths_config.get("default_automation_script", None)
        if default_script:
            script_path = Path(default_script.lstrip("/\\"))
            if not script_path.is_absolute():
                script_path = PROJECT_ROOT / script_path
            if script_path.exists():
                automation_manager.set_script_path(str(script_path))
            else:
                logger.warning(f"Default automation script not found: {script_path}")

    if automation_manager.script_path:
        automation_manager.run()
        loaded_anything = True
    else:
        default_pipeline = paths_config.get("default_pipeline", None)
        if default_pipeline:
            pipeline_path = Path(default_pipeline.lstrip("/\\"))
            if not pipeline_path.is_absolute():
                pipeline_path = PROJECT_ROOT / pipeline_path
            if pipeline_path.exists():
                load_workspace(str(pipeline_path))
                node_editor.rebuild_from_instances(MODULES_REGISTRY)
                logger.info(f"Default pipeline loaded: {pipeline_path}")
                loaded_anything = True
            else:
                logger.warning(f"Default pipeline not found: {pipeline_path}")
                node_editor.rebuild_from_instances(MODULES_REGISTRY)
        else:
            node_editor.rebuild_from_instances(MODULES_REGISTRY)

    if not loaded_anything and paths_config.get("show_loader_on_startup", True):
        from core.startup_dialog import show_startup_dialog

        show_startup_dialog(main_win=main_win, pump_frames=True)

    _vp_pos_cache = None
    _last_scaling_check = 0.0
    while dpg.is_dearpygui_running():
        now = time.time()
        if now - _last_scaling_check >= 0.2:
            _last_scaling_check = now
            _vp_pos = dpg.get_viewport_pos()
            if _vp_pos and _vp_pos != _vp_pos_cache:
                display_scaling.adapt_to_display()
                _vp_pos_cache = _vp_pos

        if app_state.close_requested:
            break

        automation_manager.process_pending_steps()
        dpg.render_dearpygui_frame()

    if _system_tray:
        _system_tray.stop()

    try:
        from core.documentation_manager import doc_manager

        doc_manager.stop()
    except Exception as e:
        logger.warning(f"Error stopping documentation server during cleanup: {e}")

    clear_registry()
    dpg.destroy_context()

