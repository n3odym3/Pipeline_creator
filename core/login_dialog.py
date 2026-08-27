"""
Login / Profile-selection dialog for Pipeline Creator.

Shown at startup when config["Login"]["enabled"] is True.
Can be re-triggered at any time via show_login_dialog() — main_win binds it to CTRL+SHIFT+L.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

import dearpygui.dearpygui as dpg

from loguru import logger

from core.app_state import app_state

# Tags
_WIN_TAG = "login_dialog_win"
_NAME_TAG = "login_dialog_name"
_BTN_TAG = "login_dialog_confirm"

_MODES = ["User", "Advanced", "Dev"]
_MODE_DESCS = {
    "User": "Simplified interface - essential tools only.",
    "Advanced": "Full workflow tools - no debug internals.",
    "Dev": "Everything exposed - debug, metrics, registries.",
}


def show_login_dialog(
    default_mode: str = "user",
    on_confirm: Optional[Callable[[], None]] = None,
    pump_frames: bool = False,
) -> None:
    """
    Display the login dialog.
    """
    try:
        from config.display_scaling import display_scaling

        display_scaling.adapt_to_display()
    except Exception as exc:
        logger.warning(f"login_dialog: display_scaling failed: {exc}")

    _build_dialog(default_mode, on_confirm)

    if pump_frames:
        while dpg.is_dearpygui_running() and dpg.does_item_exist(_WIN_TAG):
            vp_w = dpg.get_viewport_client_width()
            vp_h = dpg.get_viewport_client_height()
            win_size = dpg.get_item_rect_size(_WIN_TAG)
            if win_size[0] > 0 and win_size[1] > 0:
                pos_x = max(0, (vp_w - win_size[0]) // 2)
                pos_y = max(0, (vp_h - win_size[1]) // 2)
                dpg.set_item_pos(_WIN_TAG, [pos_x, pos_y])

            from core.automation_manager import automation_manager

            automation_manager.process_pending_steps()
            dpg.render_dearpygui_frame()

        for _ in range(2):
            if dpg.is_dearpygui_running():
                dpg.render_dearpygui_frame()

        logger.info(f"Login complete user='{app_state.username}' mode='{app_state.mode}'")


def _build_dialog(default_mode: str, on_confirm_extra: Optional[Callable[[], None]] = None) -> None:
    """Create (or recreate) the DPG modal window."""
    if dpg.does_item_exist(_WIN_TAG):
        dpg.delete_item(_WIN_TAG)

    from config.display_scaling import display_scaling

    s = display_scaling.scale

    vp_w = dpg.get_viewport_width()
    vp_h = dpg.get_viewport_height()
    win_w = s(420)
    win_h = s(310)
    pos_x = max(0, (vp_w - win_w) // 2)
    pos_y = max(0, (vp_h - win_h) // 2)

    normalized_default = default_mode.capitalize() if isinstance(default_mode, str) else "User"
    if normalized_default not in _MODES:
        normalized_default = "User"

    if app_state.login_done and app_state.mode:
        saved_mode = app_state.mode.capitalize()
        if saved_mode in _MODES:
            default_mode = saved_mode
        else:
            default_mode = normalized_default
    else:
        default_mode = normalized_default

    with dpg.window(
        label="Login",
        tag=_WIN_TAG,
        modal=True,
        no_close=True,
        no_resize=False,
        no_move=False,
        autosize=True,
        pos=[pos_x, pos_y],
    ):
        dpg.add_spacer(height=s(6))
        dpg.add_text("Please identify yourself to continue.", color=(180, 180, 200, 255))
        dpg.add_separator()
        dpg.add_spacer(height=s(8))

        def _on_name_change(sender: Any, app_data: str, user_data: Any) -> None:
            if app_data.strip():
                if dpg.does_item_exist("login_error_text"):
                    dpg.configure_item("login_error_text", show=False)

        dpg.add_text("Username")
        dpg.add_input_text(
            tag=_NAME_TAG,
            hint="Your username…",
            default_value=app_state.username,
            width=-1,
            callback=_on_name_change,
        )

        dpg.add_spacer(height=s(4))
        dpg.add_text(
            "Please enter a username to continue.",
            tag="login_error_text",
            color=(255, 100, 100, 255),
            show=False,
        )

        dpg.add_spacer(height=s(12))

        dpg.add_text("Interface complexity")
        dpg.add_spacer(height=s(4))

        _mode_tag_map: Dict[str, Union[int, str]] = {}
        _sel: List[str] = [default_mode]

        def _make_rb_cb(mode_key: str) -> Callable[[Any, Any, Any], None]:
            def _cb(sender: Any, app_data: Any, user_data: Any) -> None:
                _sel[0] = mode_key
                for mk, mt in _mode_tag_map.items():
                    if mk != mode_key and dpg.does_item_exist(mt):
                        dpg.set_value(mt, False)

            return _cb

        for mode in _MODES:
            rb_tag = dpg.generate_uuid()
            _mode_tag_map[mode] = rb_tag
            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    tag=rb_tag,
                    default_value=(mode == default_mode),
                    callback=_make_rb_cb(mode),
                )
                dpg.add_text(mode)
            dpg.add_text(f"  {_MODE_DESCS[mode]}", color=(140, 140, 160, 255))
            dpg.add_spacer(height=s(2))

        dpg.add_spacer(height=s(12))
        dpg.add_separator()
        dpg.add_spacer(height=s(8))

        def _on_confirm(*args: Any) -> None:
            raw_name = dpg.get_value(_NAME_TAG).strip()
            if not raw_name:
                if dpg.does_item_exist("login_error_text"):
                    dpg.configure_item("login_error_text", show=True)
                return
            app_state.username = raw_name
            app_state.mode = _sel[0].lower()
            app_state.login_done = True
            logger.debug(f"Login confirmed: username='{app_state.username}' mode='{app_state.mode}'")
            if dpg.does_item_exist(_WIN_TAG):
                dpg.delete_item(_WIN_TAG)

            if on_confirm_extra:
                try:
                    on_confirm_extra()
                except Exception as e:
                    logger.error(f"Login on_confirm callback failed: {e}")

        dpg.add_button(
            tag=_BTN_TAG,
            label="  Confirm  ",
            callback=_on_confirm,
            width=-1,
            height=s(32),
        )

