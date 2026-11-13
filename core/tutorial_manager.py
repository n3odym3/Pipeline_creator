from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import dearpygui.dearpygui as dpg
from loguru import logger
from PIL import Image

from core.config_manager import config
from core.file_explorer import file_explorer
from core.paths import PROJECT_ROOT, TUTORIALS_DIR


class TutorialManager:
    """
    Tutorial Manager core feature for recording and playing step-by-step UI tutorials,
    inter-module data flow capture/injection (across all `IOTypes`), and scripted UI actions.
    """

    INTERACTIVE_TYPES = {
        "mvAppItemType::mvButton",
        "mvAppItemType::mvCheckbox",
        "mvAppItemType::mvCombo",
        "mvAppItemType::mvInputText",
        "mvAppItemType::mvInputInt",
        "mvAppItemType::mvInputFloat",
        "mvAppItemType::mvDragInt",
        "mvAppItemType::mvDragFloat",
        "mvAppItemType::mvDragMultiInt",
        "mvAppItemType::mvDragMultiFloat",
        "mvAppItemType::mvSliderInt",
        "mvAppItemType::mvSliderFloat",
        "mvAppItemType::mvSliderMultiInt",
        "mvAppItemType::mvSliderMultiFloat",
        "mvAppItemType::mvSelectable",
        "mvAppItemType::mvMenuItem",
        "mvAppItemType::mvMenu",
    }

    STEP_BADGES = {
        "interact": "CLICK",
        "script": "AUTO",
        "data": "DATA",
        "observe": "HOVER",
        "center": "POPUP",
    }

    def __init__(self):
        self.label = "Tutorial Manager"
        self.win_width = 460
        self.win_height = 600
        self.pos = (100, 100)
        self.winID = "core_tutorial_manager_win"
        self.inspector_win_id = "core_tutorial_data_inspector_win"
        self.builder_win_id = "core_tutorial_step_builder_win"

        # Mascot Image Dimensions
        self.mascot_width = 100
        self.mascot_height = 100

        self.is_alive = True
        self.last_interaction_time = 0.0
        self.recording_steps: List[Dict[str, Any]] = []
        self.description = ""
        self._is_recording = False
        self._is_playing = False
        self.current_step_idx = 0
        self.selected_step_idx = 0
        self.single_step_mode = False
        self._last_highlighted_item = None
        self._last_focus_time = 0.0
        self._last_mouse_pos = (0, 0)
        self._is_mouse_down = False

        # Recording flags (both active by default for seamless capture)
        self.record_ui_enabled: bool = True
        self.record_data_enabled: bool = True
        self.current_tutorial_path: Optional[Path] = None

        # Tracking state for correlating UI clicks with subsequent data emissions
        self._last_ui_record_time: float = 0.0
        self._last_recorded_widget_id: Any = None
        self._last_recorded_step_ref: Optional[Dict[str, Any]] = None

        # Playback execution tracking for the current active step
        self._step_action_executed: bool = False
        self._consumed_packet_indices: Set[int] = set()
        self._pending_playback_advance: bool = False
        self._pending_playback_step_idx: int = -1

        # UI Tags & Render State Caches
        self.click_handler_reg = dpg.generate_uuid()
        self.overlay_drawlist = dpg.generate_uuid()
        self.overlay_info_win = dpg.generate_uuid()

        self._last_step_focused = -1
        self._last_rendered_step_ui = -1
        self._last_overlay_geom = None
        self._last_rendered_links_key = None
        self._tutorial_desc_cache: Dict[str, Tuple[float, str]] = {}
        self._step_label_items: List[Any] = []
        self._row_selectables: List[Tuple[Any, Any]] = []
        self._row_widgets: List[Tuple[Any, Any, Any]] = []
        self._ui_initialized = False

        self.tutorials_dir = TUTORIALS_DIR
        if not self.tutorials_dir.exists():
            try:
                self.tutorials_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"Could not create tutorials directory: {e}")

        # Worker thread fields (started dynamically on playback)
        self._worker_thread = None
        self._worker_event = None
        self._overlay_lock = threading.RLock()

    def _sync_active_hooks(self) -> None:
        """Enable DPG global mouse handlers & input_cb interceptor ONLY when recording or playing."""
        active = bool(self._is_recording or self._is_playing)
        try:
            from core.data_stream_interceptor import data_stream_interceptor

            data_stream_interceptor.is_active = active
            if active:
                data_stream_interceptor.ensure_all_hooked()
        except Exception:
            pass

        if dpg.does_item_exist(getattr(self, "click_handler_reg", 0)):
            try:
                dpg.configure_item(self.click_handler_reg, show=active)
            except Exception:
                pass

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @is_recording.setter
    def is_recording(self, value: bool) -> None:
        self._is_recording = bool(value)
        self._sync_active_hooks()

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @is_playing.setter
    def is_playing(self, value: bool) -> None:
        was_playing = self._is_playing
        self._is_playing = bool(value)
        self._sync_active_hooks()
        if self._is_playing and not was_playing:
            self._last_rendered_step_ui = -1
            self._last_overlay_geom = None
            self._start_worker()
        elif not self._is_playing and was_playing:
            self._pending_playback_advance = False
            self._pending_playback_step_idx = -1
            self._last_rendered_step_ui = -1
            self._last_overlay_geom = None
            self._stop_worker()

    def _get_tutorial_dir(self) -> Path:
        if self.current_tutorial_path and self.current_tutorial_path.parent.exists():
            return self.current_tutorial_path.parent
        return self.tutorials_dir

    def _init_ui_components(self):
        if self._ui_initialized:
            return

        from config.theme_manager import theme_manager
        self.highlight_theme = theme_manager.create_highlight_theme()
        with dpg.theme_component(dpg.mvAll, parent=self.highlight_theme):
            dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 150, 0, 255), category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 2.0, category=dpg.mvThemeCat_Core)

        if not dpg.does_item_exist(self.click_handler_reg):
            with dpg.handler_registry(tag=self.click_handler_reg, show=bool(self._is_recording or self._is_playing)):
                dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=self._on_mouse_click)
                dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Middle, callback=self._on_mouse_click)
                dpg.add_mouse_down_handler(callback=lambda: setattr(self, "_is_mouse_down", True))
                dpg.add_mouse_release_handler(callback=self._on_mouse_release)

        if not dpg.does_item_exist(self.overlay_drawlist):
            with dpg.viewport_drawlist(front=True, show=False, tag=self.overlay_drawlist):
                self.overlay_top = dpg.draw_rectangle((0, 0), (0, 0), fill=(0, 0, 0, 180), color=(0, 0, 0, 0))
                self.overlay_bottom = dpg.draw_rectangle((0, 0), (0, 0), fill=(0, 0, 0, 180), color=(0, 0, 0, 0))
                self.overlay_left = dpg.draw_rectangle((0, 0), (0, 0), fill=(0, 0, 0, 180), color=(0, 0, 0, 0))
                self.overlay_right = dpg.draw_rectangle((0, 0), (0, 0), fill=(0, 0, 0, 180), color=(0, 0, 0, 0))
                self.overlay_mid = dpg.draw_rectangle((0, 0), (0, 0), fill=(0, 0, 0, 180), color=(0, 0, 0, 0))
                self.overlay_extra1 = dpg.draw_rectangle((0, 0), (0, 0), fill=(0, 0, 0, 180), color=(0, 0, 0, 0))
                self.overlay_extra2 = dpg.draw_rectangle((0, 0), (0, 0), fill=(0, 0, 0, 180), color=(0, 0, 0, 0))
                self.overlay_border = dpg.draw_rectangle(
                    (0, 0), (0, 0), fill=(0, 0, 0, 0), color=(255, 150, 0, 255), thickness=4, show=False
                )

        self._setup_mascot()

        if not dpg.does_item_exist(self.overlay_info_win):
            with dpg.window(
                tag=self.overlay_info_win,
                no_title_bar=True,
                no_resize=True,
                no_move=True,
                show=False,
                autosize=True,
                no_scrollbar=True,
            ):
                with dpg.group(horizontal=True):
                    default_tex = getattr(self, "_current_mascot_texture_tag", None)
                    if default_tex and dpg.does_item_exist(default_tex):
                        dpg.add_image(
                            default_tex,
                            tag="tutorial_mascot_image_widget",
                            width=self.mascot_width,
                            height=self.mascot_height,
                        )

                    with dpg.group():
                        self.overlay_msg = dpg.add_text("Instruction", wrap=400)
                        self.overlay_data_badge = dpg.add_text("", color=(100, 220, 255), show=False, wrap=400)
                        dpg.add_spacer(height=5)
                        self.overlay_links_group = dpg.add_group(show=False)
                        with dpg.group(horizontal=True) as self.nav_buttons_group:
                            self.overlay_prev_btn = dpg.add_button(label="\uf060", callback=self.prev_step, width=40)
                            self.overlay_replay_btn = dpg.add_button(
                                label="Replay Action",
                                callback=self.replay_current_step_action,
                                show=False,
                            )
                            with dpg.tooltip(parent=self.overlay_replay_btn):
                                dpg.add_text("Re-send captured data or re-run scripted action for this step")
                            self.overlay_next_btn = dpg.add_button(
                                label="\uf061", callback=self.manual_next_step, width=40
                            )
                            dpg.add_button(label="Close", callback=self.toggle_play)

                        with dpg.group(horizontal=True, show=False) as self.error_button_group:
                            dpg.add_button(label="OK", callback=self.toggle_play, width=120)

            self.overlay_win_theme = dpg.add_theme()
            self._build_overlay_theme()
            dpg.bind_item_theme(self.overlay_info_win, self.overlay_win_theme)

        self._ui_initialized = True

    def _build_overlay_theme(self):
        """Rebuild the overlay popup theme using the current palette colors."""
        from config.theme_manager import theme_manager
        palette = getattr(theme_manager, "current_palette", None) or theme_manager.active_palette
        bg = palette.get("popup_bg", palette.get("window_bg", (40, 40, 50, 255)))
        darken_factor = 0.85
        bg = (int(bg[0] * darken_factor), int(bg[1] * darken_factor), int(bg[2] * darken_factor), 245)
        if not dpg.does_item_exist(getattr(self, "overlay_win_theme", 0)):
            return
        dpg.delete_item(self.overlay_win_theme, children_only=True)
        with dpg.theme_component(dpg.mvWindowAppItem, parent=self.overlay_win_theme):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, bg)
            dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 150, 0, 255))
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 2.0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8.0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 15, 15)

    def _setup_mascot(self):
        if not config.get("UI", {}).get("mascot", False):
            return

        if not dpg.does_item_exist("tutorial_texture_registry"):
            dpg.add_texture_registry(tag="tutorial_texture_registry")

        assets_dir = PROJECT_ROOT / "ressources" / "tutorial_assets"
        fallback_img = PROJECT_ROOT / "ressources" / "Polypy.png"

        images_to_load = []
        if fallback_img.exists():
            images_to_load.append(fallback_img)

        if assets_dir.exists() and assets_dir.is_dir():
            valid_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tga"}
            for sub in assets_dir.iterdir():
                if sub.is_dir():
                    for f in sub.iterdir():
                        if f.is_file() and f.suffix.lower() in valid_extensions:
                            images_to_load.append(f)

        self._loaded_mascot_textures = {}

        try:
            resample_filter = Image.Resampling.LANCZOS
        except AttributeError:
            resample_filter = Image.LANCZOS

        import numpy as np
        for img_path in images_to_load:
            tag = f"tutorial_mascot_tex_{img_path.name}"
            if not dpg.does_item_exist(tag):
                try:
                    with Image.open(img_path) as img:
                        img = img.convert("RGBA")
                        img_resized = img.resize((self.mascot_width, self.mascot_height), resample_filter)
                        raw_bytes = img_resized.tobytes()
                        data = np.frombuffer(raw_bytes, dtype=np.uint8).astype(np.float32) * (1.0 / 255.0)

                    dpg.add_static_texture(
                        width=self.mascot_width,
                        height=self.mascot_height,
                        default_value=data,
                        tag=tag,
                        parent="tutorial_texture_registry",
                    )
                except Exception as e:
                    logger.error(f"Failed to preload mascot texture {img_path}: {e}")
                    continue

            if img_path == fallback_img:
                folder_key = "fallback"
            else:
                folder_key = img_path.parent.name.lower()

            if folder_key not in self._loaded_mascot_textures:
                self._loaded_mascot_textures[folder_key] = []
            self._loaded_mascot_textures[folder_key].append(tag)

        self._load_mascot_texture("tutorial")

    def _load_mascot_texture(self, icon_type=None):
        import random

        if not hasattr(self, "_loaded_mascot_textures") or not self._loaded_mascot_textures:
            return

        selected_tag = None

        if icon_type:
            folder_key = icon_type.lower()
            if folder_key in self._loaded_mascot_textures and self._loaded_mascot_textures[folder_key]:
                selected_tag = random.choice(self._loaded_mascot_textures[folder_key])

        if not selected_tag:
            all_tags = []
            for key, tags in self._loaded_mascot_textures.items():
                if key != "fallback":
                    all_tags.extend(tags)
            if all_tags:
                selected_tag = random.choice(all_tags)

        if not selected_tag:
            if "fallback" in self._loaded_mascot_textures and self._loaded_mascot_textures["fallback"]:
                selected_tag = self._loaded_mascot_textures["fallback"][0]

        if selected_tag and dpg.does_item_exist(selected_tag):
            self._current_mascot_texture_tag = selected_tag
            if dpg.does_item_exist("tutorial_mascot_image_widget"):
                try:
                    dpg.configure_item("tutorial_mascot_image_widget", texture_tag=selected_tag)
                except Exception as e:
                    logger.error(f"Failed to configure image widget with texture {selected_tag}: {e}")

    # ------------------------------------------------------------------
    # Main Tutorial Manager Window (Clean 3-Zone Layout)
    # ------------------------------------------------------------------
    def show(self):
        self._init_ui_components()

        if dpg.does_item_exist(self.winID):
            self._update_step_toolbar_state()
            dpg.show_item(self.winID)
            dpg.focus_item(self.winID)
            return

        with dpg.window(
            label=self.label,
            pos=self.pos,
            width=self.win_width,
            height=self.win_height,
            tag=self.winID,
            on_close=self._on_close_window,
        ):
            # --- ZONE 1: Global Controls & Step Creation ---
            dpg.add_text("Tutorial Description:")
            self.desc_input = dpg.add_input_text(
                multiline=True,
                width=-1,
                height=55,
                default_value=self.description,
                callback=self._on_desc_change,
            )

            with dpg.group(horizontal=True):
                self.btn_record = dpg.add_button(label="Record", callback=self.toggle_record, width=90)
                with dpg.tooltip(parent=self.btn_record):
                    dpg.add_text(
                        "Record your interactions across the app:\n"
                        "- Left-click a widget: records the click + captures any data sent between modules\n"
                        "- Middle-click an element: records an 'Observe (Hover)' step"
                    )
                self.btn_play = dpg.add_button(label="Play", callback=self.toggle_play, width=80)
                dpg.add_button(label="Save", callback=lambda *_: self.save_tutorial(), width=65)
                dpg.add_button(label="Load", callback=lambda *_: self.load_tutorial(), width=65)
                dpg.add_button(label="Clear", callback=self.clear_tutorial, width=-1)

            with dpg.group(horizontal=True):
                dpg.add_button(label="+ Central Popup", callback=self.add_center_step, width=210)
                dpg.add_button(label="+ Manual Data / Action", callback=self.show_step_builder, width=-1)

            dpg.add_separator()

            with dpg.group(horizontal=True):
                self.main_prev_btn = dpg.add_button(label="\uf060", callback=self.prev_step)
                self.play_status_text = dpg.add_text("Ready")
                self.main_next_btn = dpg.add_button(label="\uf061", callback=self.manual_next_step)

            dpg.add_separator()

            # --- ZONE 2: Contextual Settings for the Selected Step ---
            with dpg.group(horizontal=True):
                self.selected_step_title = dpg.add_text("Selected Step:")
                self.btn_step_up = dpg.add_button(
                    label="\uf062", callback=lambda *_: self.move_selected_step(-1), width=28
                )
                with dpg.tooltip(parent=self.btn_step_up):
                    dpg.add_text("Move step up")
                self.btn_step_down = dpg.add_button(
                    label="\uf063", callback=lambda *_: self.move_selected_step(1), width=28
                )
                with dpg.tooltip(parent=self.btn_step_down):
                    dpg.add_text("Move step down")
                self.btn_step_run = dpg.add_button(label="Test Step", callback=self.run_selected_step)
                with dpg.tooltip(parent=self.btn_step_run):
                    dpg.add_text("Play and test only the selected step")
                self.btn_step_del = dpg.add_button(label="Delete", callback=self.delete_selected_step)

                dpg.add_text("View:")
                self.step_view_combo = dpg.add_combo(
                    items=["(None)"],
                    default_value="(None)",
                    width=-1,
                    callback=self._on_step_view_changed,
                )
                with dpg.tooltip(parent=self.step_view_combo):
                    dpg.add_text("Optional workspace view to activate when this step starts")

            # Dynamic panel that ONLY renders controls valid for the selected step's type (manually resizable height)
            with dpg.child_window(height=115, resizable_y=True, border=True):
                self.step_context_group = dpg.add_group()

            # --- ZONE 3: Step List Table ---
            with dpg.child_window(height=-1):
                self.step_list_group = dpg.add_group()

        self.refresh_ui()

    def _render_selected_step_context_panel(self) -> None:
        """
        Populate `self.step_context_group` with ONLY the options that make sense
        for the currently selected step (Popup vs Observe vs Click/Action/Data).
        """
        ctx = getattr(self, "step_context_group", 0)
        if not ctx or not dpg.does_item_exist(ctx):
            return
        dpg.delete_item(ctx, children_only=True)

        n = len(self.recording_steps)
        if not (0 <= self.selected_step_idx < n):
            dpg.add_text(
                "No step selected. Click 'Record' and interact with modules, or add a step above.",
                color=(150, 150, 150),
                wrap=410,
                parent=ctx,
            )
            return

        step_idx = self.selected_step_idx
        step = self.recording_steps[step_idx]
        step_type = step.get("type", "interact")
        packets = step.get("data_packets") or []
        action_meta = step.get("action_meta") or {}

        # CASE 1: Central Popup Step -> Only show '+ Add Link to Another Tutorial'
        if step_type == "center":
            dpg.add_text(
                "Step Type: Central Popup (message displayed in the middle of the screen)",
                color=(220, 200, 120),
                parent=ctx,
            )
            dpg.add_spacer(height=4, parent=ctx)
            dpg.add_button(
                label="+ Add Link to Another Tutorial",
                callback=self.add_link_to_selected_step,
                width=-1,
                parent=ctx,
            )
            return

        # CASE 2: Observe Step (recorded via middle-click)
        if step_type == "observe":
            dpg.add_text(
                "Step Type: Observe Target (advances automatically when the user hovers the element)",
                color=(180, 220, 180),
                wrap=410,
                parent=ctx,
            )
            return

        # CASE 3: Ephemeral Error / Success Hint
        if step_type in ("error_hint", "success_hint"):
            dpg.add_text(f"Step Type: {step_type}", color=(255, 150, 100), parent=ctx)
            return

        # CASE 4: Interactive UI / Scripted Action / Data Flow Step ('interact', 'script', 'data')
        is_auto = step_type in ("script", "data")
        is_direct_data = step_type == "data"

        # 4a. Show summary of what was captured (Action and/or Data Flow)
        if packets:
            pkt = packets[0]
            tgt_lbl = (pkt.get("target_selector") or {}).get("label") or "Target Module"
            summary_txt = pkt.get("summary") or pkt.get("io_type", "data")
            with dpg.group(horizontal=True, parent=ctx):
                dpg.add_text(f"Captured Data: {summary_txt} -> {tgt_lbl}", color=(100, 220, 255))
                dpg.add_button(label="Inspect / Edit Data", callback=self.show_step_inspector)
        elif action_meta.get("method_name"):
            tgt_lbl = (action_meta.get("target_selector") or {}).get("label") or "Module"
            m_name = action_meta.get("method_name")
            dpg.add_text(
                f"Captured Action: {tgt_lbl} -> {m_name}()",
                color=(130, 230, 150),
                parent=ctx,
            )
        else:
            dpg.add_text("Step Type: UI Widget Interaction", color=(200, 200, 200), parent=ctx)

        # 4b. Clear checkboxes controlling how this step executes on Play
        def _on_toggle_auto(s, val, *_):
            if not (0 <= self.selected_step_idx < len(self.recording_steps)):
                return
            st = self.recording_steps[self.selected_step_idx]
            if val:
                # If no UI widget path exists (pure data step), use 'data', else 'script'
                st["type"] = "data" if (not st.get("path") and st.get("data_packets")) else "script"
            else:
                st["type"] = "interact"
            self._last_resolved_step = -1
            self._update_single_row_summary(self.selected_step_idx)
            self._render_selected_step_context_panel()

        chk_auto = dpg.add_checkbox(
            label="Auto-execute silently & advance to next step (no popup)",
            default_value=is_auto,
            callback=_on_toggle_auto,
            parent=ctx,
        )
        with dpg.tooltip(parent=chk_auto):
            dpg.add_text(
                "Unchecked (CLICK): Shows the tutorial popup/highlight and waits for the user to click.\n"
                "Checked (AUTO): Executes the action / sends the data silently without showing a popup, and immediately advances to the next step."
            )

        # If this step captured data sent to another module, allow injecting directly into the receiver
        if packets and step.get("path"):
            pkt = packets[0]
            tgt_lbl = (pkt.get("target_selector") or {}).get("label") or "Receiving Module"

            def _on_toggle_direct_data(s, val, *_):
                if not (0 <= self.selected_step_idx < len(self.recording_steps)):
                    return
                st = self.recording_steps[self.selected_step_idx]
                st["type"] = "data" if val else "script"
                self._last_resolved_step = -1
                self._update_single_row_summary(self.selected_step_idx)
                self._render_selected_step_context_panel()

            chk_direct = dpg.add_checkbox(
                label=f"Auto-send data directly into '{tgt_lbl}' (bypass source button)",
                default_value=is_direct_data,
                callback=_on_toggle_direct_data,
                parent=ctx,
            )
            with dpg.tooltip(parent=chk_direct):
                dpg.add_text(
                    f"When checked (DATA), silently injects the captured variable directly into '{tgt_lbl}.input_cb'\n"
                    f"without calling the source widget callback, then immediately advances to the next step."
                )

    def _on_desc_change(self, sender, app_data, *args, **kwargs):
        self.description = app_data

    def _on_close_window(self, sender=None, app_data=None, *args, **kwargs):
        dpg.hide_item(self.winID)
        self.is_recording = False
        if dpg.does_item_exist(getattr(self, "btn_record", 0)):
            dpg.configure_item(self.btn_record, label="Record")

        self.is_playing = False
        if dpg.does_item_exist(getattr(self, "btn_play", 0)):
            dpg.configure_item(self.btn_play, label="Play")
        self._hide_overlay_completely()

    def toggle_record(self, *args, **kwargs):
        if self.is_playing:
            return

        self.is_recording = not self.is_recording
        if self.is_recording:
            try:
                from core.data_stream_interceptor import data_stream_interceptor
                data_stream_interceptor.ensure_all_hooked()
            except Exception as e:
                logger.error(f"Failed hooking modules for data recording: {e}")

            if dpg.does_item_exist(getattr(self, "btn_record", 0)):
                dpg.configure_item(self.btn_record, label="Stop Recording")
            logger.info("Tutorial recording started.")
        else:
            self._finalize_pending_widget_value()
            if dpg.does_item_exist(getattr(self, "btn_record", 0)):
                dpg.configure_item(self.btn_record, label="Record")
            logger.info("Tutorial recording stopped.")

    def toggle_play(self, *args, **kwargs):
        if self.is_recording:
            return

        if len(self.recording_steps) == 0 and not self.is_playing:
            logger.warning("No tutorial steps to play.")
            return

        self.is_playing = not self.is_playing
        if self.is_playing:
            btn = getattr(self, "btn_play", 0)
            if dpg.does_item_exist(btn):
                dpg.configure_item(btn, label="Stop Playback")
            self.current_step_idx = 0
            self._init_ui_components()
            self._update_status_text()
            logger.info("Tutorial playback started.")
        else:
            btn = getattr(self, "btn_play", 0)
            if dpg.does_item_exist(btn):
                dpg.configure_item(btn, label="Play")
            self._hide_overlay_completely()
            self.single_step_mode = False
            status_txt = getattr(self, "play_status_text", 0)
            if dpg.does_item_exist(status_txt):
                dpg.configure_item(status_txt, default_value="Tutorial Finished")
            logger.info("Tutorial playback stopped.")

            if hasattr(self, "_backup_steps"):
                self.recording_steps = list(self._backup_steps)
                delattr(self, "_backup_steps")
                if getattr(self, "_ui_initialized", False):
                    self.refresh_ui()

        self._update_overlay()

    def play_error_hint(self, item_tag, instruction, icon_type="warning"):
        """Play a single ephemeral step to highlight a UI element for an error/hint."""
        if self.is_recording:
            return
        if self.is_playing and not hasattr(self, "_backup_steps"):
            logger.warning("Tutorial is currently active, skipping error hint to avoid interruption.")
            return

        path = self.build_item_path(item_tag)
        if not path:
            return

        if not hasattr(self, "_backup_steps"):
            self._backup_steps = list(self.recording_steps)

        self.recording_steps = [
            {
                "path": path,
                "instruction": f"{instruction}",
                "dim": True,
                "type": "error_hint",
                "icon_type": icon_type,
            }
        ]

        self.play_single_step(0)

    def play_center_hint(self, instruction, type="error_hint", icon_type="error"):
        """Play a single ephemeral step to show a message at the center of the viewport."""
        if self.is_recording:
            return
        if self.is_playing and not hasattr(self, "_backup_steps"):
            return

        if not hasattr(self, "_backup_steps"):
            self._backup_steps = list(self.recording_steps)

        self.recording_steps = [
            {
                "path": [],
                "instruction": f"{instruction}",
                "dim": True,
                "type": type,
                "icon_type": icon_type,
            }
        ]

        self.play_single_step(0)

    def play_success_hint(self, item_tag, instruction, icon_type="success"):
        """Play a single ephemeral step to highlight a UI element for a success message."""
        if self.is_recording:
            return
        if self.is_playing and not hasattr(self, "_backup_steps"):
            logger.warning("Tutorial is currently active, skipping success hint to avoid interruption.")
            return

        path = self.build_item_path(item_tag)
        if not path:
            return

        if not hasattr(self, "_backup_steps"):
            self._backup_steps = list(self.recording_steps)

        self.recording_steps = [
            {
                "path": path,
                "instruction": f"{instruction}",
                "dim": True,
                "type": "success_hint",
                "icon_type": icon_type,
            }
        ]

        self.play_single_step(0)

    def _hide_overlay_completely(self):
        with self._overlay_lock:
            self._hide_overlay_completely_impl()

    def _hide_overlay_completely_impl(self):
        self._clear_window_highlight()
        self._last_overlay_geom = None
        if dpg.does_item_exist(getattr(self, "overlay_drawlist", 0)):
            dpg.configure_item(self.overlay_drawlist, show=False)
        if dpg.does_item_exist(getattr(self, "overlay_border", 0)):
            dpg.configure_item(self.overlay_border, show=False)
        if dpg.does_item_exist(getattr(self, "overlay_info_win", 0)):
            dpg.hide_item(self.overlay_info_win)
        for itm in (
            getattr(self, "overlay_top", 0),
            getattr(self, "overlay_bottom", 0),
            getattr(self, "overlay_left", 0),
            getattr(self, "overlay_right", 0),
            getattr(self, "overlay_mid", 0),
            getattr(self, "overlay_extra1", 0),
            getattr(self, "overlay_extra2", 0),
        ):
            if dpg.does_item_exist(itm):
                dpg.configure_item(itm, show=False)

    def _clear_window_highlight(self):
        if self._last_highlighted_item and dpg.does_item_exist(self._last_highlighted_item):
            try:
                dpg.bind_item_theme(self._last_highlighted_item, 0)
            except Exception:
                pass
        self._last_highlighted_item = None

    def _get_cached_tutorial_desc(self, filepath: Path) -> str:
        """Read and cache a tutorial file's description keyed by path and mtime."""
        try:
            key = str(filepath)
            mtime = filepath.stat().st_mtime
            cached = self._tutorial_desc_cache.get(key)
            if cached and cached[0] == mtime:
                return cached[1]
            desc = ""
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    desc = data.get("description", "")
            self._tutorial_desc_cache[key] = (mtime, desc)
            return desc
        except Exception:
            return ""

    def _format_rel_tutorial_path(self, filepath: str | Path) -> str:
        """Format a tutorial path relative to TUTORIALS_DIR (or PROJECT_ROOT) using forward slashes."""
        p = Path(filepath)
        try:
            return p.resolve().relative_to(self.tutorials_dir.resolve()).as_posix()
        except Exception:
            pass
        try:
            return p.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
        except Exception:
            return p.as_posix()

    def _resolve_tutorial_link(self, link_entry: Any) -> Tuple[str, Optional[Path], str]:
        """
        Resolve a tutorial link entry (str or dict with 'label'/'path') to:
        (display_label, resolved_Path_or_None, description)
        """
        if isinstance(link_entry, dict):
            raw_path = str(link_entry.get("path", "")).strip()
            custom_label = str(link_entry.get("label", "")).strip()
        else:
            raw_path = str(link_entry).strip()
            custom_label = ""

        if not raw_path:
            return (custom_label or "Unknown", None, "")

        candidates: List[Path] = []
        raw_p = Path(raw_path)
        if raw_p.is_absolute():
            candidates.append(raw_p)
        else:
            candidates.append(self.tutorials_dir / raw_p)
            if raw_p.suffix.lower() != ".json":
                candidates.append(self.tutorials_dir / f"{raw_path}.json")
            candidates.append(PROJECT_ROOT / raw_p)
            if raw_p.suffix.lower() != ".json":
                candidates.append(PROJECT_ROOT / f"{raw_path}.json")

        resolved: Optional[Path] = None
        for cand in candidates:
            if cand.exists() and cand.is_file():
                resolved = cand
                break

        if resolved is None and self.tutorials_dir.exists():
            target_stem = raw_p.stem.lower()
            for found in self.tutorials_dir.rglob("*.json"):
                if found.stem.lower() == target_stem:
                    resolved = found
                    break

        display_label = custom_label or (resolved.stem if resolved else raw_p.stem or raw_path)
        desc = self._get_cached_tutorial_desc(resolved) if (resolved and resolved.exists()) else ""
        return (display_label, resolved, desc)

    def _update_overlay_links(self, step: Dict[str, Any]) -> None:
        """Rebuild the clickable tutorial links inside the overlay HUD when the step's links change."""
        links_group = getattr(self, "overlay_links_group", 0)
        if not links_group or not dpg.does_item_exist(links_group):
            return

        links = step.get("links") or []
        key = (
            self.current_step_idx,
            tuple((e.get("label", ""), e.get("path", "")) if isinstance(e, dict) else ("", str(e)) for e in links),
        )

        if key == getattr(self, "_last_rendered_links_key", None):
            return

        self._last_rendered_links_key = key
        dpg.delete_item(links_group, children_only=True)

        if not links:
            dpg.configure_item(links_group, show=False)
            return

        dpg.configure_item(links_group, show=True)
        for link_entry in links:
            label, resolved_path, desc = self._resolve_tutorial_link(link_entry)

            def _on_link_click(s, a, u, *args, **kwargs):
                if u and Path(u).exists():
                    self.load_and_play(u)
                else:
                    logger.warning(f"Linked tutorial not found: {u}")

            btn = dpg.add_button(
                label=f"\uf061  {label}",
                callback=_on_link_click,
                user_data=str(resolved_path) if resolved_path else "",
                enabled=(resolved_path is not None and resolved_path.exists()),
                width=-1,
                parent=links_group,
            )
            tooltip_txt = (
                desc
                if desc
                else (
                    str(resolved_path.relative_to(self.tutorials_dir))
                    if (resolved_path and resolved_path.is_relative_to(self.tutorials_dir))
                    else ""
                )
            )
            if btn and tooltip_txt:
                with dpg.tooltip(parent=btn):
                    dpg.add_text(tooltip_txt, wrap=300)

        dpg.add_spacer(height=5, parent=links_group)

    def populate_tutorials_menu(self, parent_menu_tag: Any = "menu_tutorials") -> None:
        """Recursively populate the Tutorials menu from TUTORIALS_DIR."""
        if not dpg.does_item_exist(parent_menu_tag):
            return
        dpg.delete_item(parent_menu_tag, children_only=True)
        if not self.tutorials_dir.exists():
            return

        def _populate_dir(directory: Path, parent_tag: Any) -> None:
            try:
                entries = list(directory.iterdir())
            except Exception:
                return

            subdirs = sorted(
                [
                    e
                    for e in entries
                    if e.is_dir()
                    and not e.name.startswith(".")
                    and e.name != "__pycache__"
                    and not e.name.endswith("_assets")
                ],
                key=lambda p: p.name.lower(),
            )
            files = sorted(
                [e for e in entries if e.is_file() and e.suffix.lower() == ".json"],
                key=lambda p: p.stem.lower(),
            )

            for subdir in subdirs:
                if any(subdir.rglob("*.json")):
                    submenu = dpg.add_menu(label=subdir.name, parent=parent_tag)
                    _populate_dir(subdir, submenu)

            for filepath in files:
                desc = self._get_cached_tutorial_desc(filepath)

                def _make_cb(fp: Path):
                    return lambda *a: self.load_and_play(fp)

                mi = dpg.add_menu_item(label=filepath.stem, callback=_make_cb(filepath), parent=parent_tag)
                if mi and desc and desc != "No description provided.":
                    with dpg.tooltip(parent=mi):
                        dpg.add_text(desc, wrap=300)

        _populate_dir(self.tutorials_dir, parent_menu_tag)

    def save_tutorial(self, filepath: Optional[str | Path] = None, *args, **kwargs):
        if not isinstance(filepath, (str, Path)) or dpg.does_item_exist(filepath):
            filepath = None
        if not filepath:
            default_dir = str(self._get_tutorial_dir())
            default_name = self.current_tutorial_path.name if self.current_tutorial_path else "tutorial.json"
            filepath = file_explorer.save_file(
                default_path=default_dir,
                default_name=default_name,
                extensions=[("JSON files", "*.json")],
            )
        if not filepath:
            return
        try:
            filepath_str = str(filepath)
            if not filepath_str.endswith(".json"):
                filepath_str += ".json"
            self.current_tutorial_path = Path(filepath_str).resolve()

            # Flush any binary/large ndarray assets to `<tutorial_stem>_assets/`
            try:
                from core.data_stream_interceptor import data_stream_interceptor
                data_stream_interceptor.codec.flush_assets_to_dir(self.recording_steps, self.current_tutorial_path)
            except Exception as e:
                logger.warning(f"Failed flushing binary tutorial assets: {e}")

            from core import module_registry
            data = {
                "description": self.description,
                "pipeline_hint": getattr(module_registry, "LAST_LOADED_WORKSPACE", None),
                "steps": self.recording_steps,
            }
            with open(self.current_tutorial_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            self.populate_tutorials_menu("menu_tutorials")
            logger.info(f"Saved tutorial to {self.current_tutorial_path}")
        except Exception as e:
            logger.error(f"Failed to save tutorial: {e}")

    def load_tutorial(self, filepath=None, *args, **kwargs):
        if not isinstance(filepath, (str, Path)) or dpg.does_item_exist(filepath):
            filepath = None
        if not filepath:
            filepath = file_explorer.select_file(
                default_path=str(self._get_tutorial_dir()),
                extensions=[("JSON files", "*.json")],
            )
        if not filepath:
            return

        try:
            self.current_tutorial_path = Path(filepath).resolve()
            with open(self.current_tutorial_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                self.recording_steps = data
                self.description = ""
            else:
                self.recording_steps = data.get("steps", [])
                self.description = data.get("description", "")

            desc_widget = getattr(self, "desc_input", 0)
            if dpg.does_item_exist(desc_widget):
                dpg.set_value(desc_widget, self.description)

            self.current_step_idx = 0
            self.selected_step_idx = 0
            self._last_resolved_step = -1
            self._last_step_focused = -1
            self._last_rendered_links_key = None
            self.refresh_ui()
            logger.info(f"Loaded tutorial from {self.current_tutorial_path}")
        except Exception as e:
            logger.error(f"Failed to load tutorial: {e}")

    def add_center_step(self, *args, **kwargs):
        """Append a step showing a popup at the center of the viewport, not bound to any UI element."""
        if self.is_playing:
            return
        self.recording_steps.append(
            {
                "path": [],
                "instruction": "Message",
                "dim": True,
                "type": "center",
                "links": [],
            }
        )
        self.selected_step_idx = len(self.recording_steps) - 1
        self.refresh_ui()
        self._scroll_ui_to_current_step()
        logger.info("Added central popup step.")

    def clear_tutorial(self, *args, **kwargs):
        self.recording_steps = []
        self.description = ""
        self._last_recorded_widget_id = None
        self._last_recorded_step_ref = None
        try:
            from core.data_stream_interceptor import data_stream_interceptor
            data_stream_interceptor.codec.clear_memory_assets()
        except Exception:
            pass
        if dpg.does_item_exist(getattr(self, "desc_input", 0)):
            dpg.set_value(self.desc_input, "")
        self.current_step_idx = 0
        self.selected_step_idx = 0
        self._last_rendered_links_key = None
        self.refresh_ui()
        logger.info("Tutorial cleared.")

    def select_step(self, idx: int) -> None:
        """Select a step row in the table and update the contextual step panel."""
        if not self.recording_steps:
            self.selected_step_idx = 0
        else:
            self.selected_step_idx = max(0, min(idx, len(self.recording_steps) - 1))

        for i, (sel_num, sel_name) in enumerate(getattr(self, "_row_selectables", [])):
            is_sel = i == self.selected_step_idx
            if dpg.does_item_exist(sel_num):
                dpg.set_value(sel_num, is_sel)
            if dpg.does_item_exist(sel_name):
                dpg.set_value(sel_name, is_sel)

        self._update_step_toolbar_state()
        if dpg.does_item_exist(self.inspector_win_id) and dpg.is_item_shown(self.inspector_win_id):
            self._populate_step_inspector()

    def _update_step_toolbar_state(self) -> None:
        """Enable or disable the step toolbar buttons and rebuild the contextual options panel."""
        n = len(self.recording_steps)
        has_sel = 0 <= self.selected_step_idx < n
        sel_step = self.recording_steps[self.selected_step_idx] if has_sel else {}

        title_lbl = getattr(self, "selected_step_title", 0)
        if dpg.does_item_exist(title_lbl):
            dpg.set_value(title_lbl, f"Step #{self.selected_step_idx + 1}:" if has_sel else "Step:")

        if dpg.does_item_exist(getattr(self, "btn_step_up", 0)):
            dpg.configure_item(self.btn_step_up, enabled=(has_sel and self.selected_step_idx > 0))
        if dpg.does_item_exist(getattr(self, "btn_step_down", 0)):
            dpg.configure_item(self.btn_step_down, enabled=(has_sel and self.selected_step_idx < n - 1))
        if dpg.does_item_exist(getattr(self, "btn_step_run", 0)):
            dpg.configure_item(self.btn_step_run, enabled=has_sel)
        if dpg.does_item_exist(getattr(self, "btn_step_del", 0)):
            dpg.configure_item(self.btn_step_del, enabled=has_sel)

        combo = getattr(self, "step_view_combo", 0)
        if dpg.does_item_exist(combo):
            from core.module_registry import get_available_views
            avail_views = list(get_available_views().keys())
            curr_view = sel_step.get("view", "") if has_sel else ""
            if curr_view and curr_view not in avail_views:
                avail_views.append(curr_view)
            items = ["(None)"] + avail_views
            dpg.configure_item(combo, items=items, enabled=has_sel)
            dpg.set_value(combo, curr_view if curr_view else "(None)")

        self._render_selected_step_context_panel()

    def _on_step_view_changed(self, sender, app_data, *args, **kwargs) -> None:
        if 0 <= self.selected_step_idx < len(self.recording_steps):
            step = self.recording_steps[self.selected_step_idx]
            if not app_data or app_data == "(None)":
                step.pop("view", None)
            else:
                step["view"] = app_data
            self._update_single_row_summary(self.selected_step_idx)

    def move_selected_step(self, delta: int) -> None:
        idx = self.selected_step_idx
        new_idx = idx + delta
        if 0 <= idx < len(self.recording_steps) and 0 <= new_idx < len(self.recording_steps):
            steps = self.recording_steps
            steps[idx], steps[new_idx] = steps[new_idx], steps[idx]
            if self.current_step_idx == idx:
                self.current_step_idx = new_idx
            elif self.current_step_idx == new_idx:
                self.current_step_idx = idx
            self.selected_step_idx = new_idx
            self.refresh_ui()
            self._scroll_ui_to_current_step()

    def run_selected_step(self, *args, **kwargs) -> None:
        if 0 <= self.selected_step_idx < len(self.recording_steps):
            self.play_single_step(self.selected_step_idx)

    def delete_selected_step(self, *args, **kwargs) -> None:
        if 0 <= self.selected_step_idx < len(self.recording_steps):
            self.recording_steps.pop(self.selected_step_idx)
            if self.selected_step_idx >= len(self.recording_steps):
                self.selected_step_idx = max(0, len(self.recording_steps) - 1)
            if self.current_step_idx >= len(self.recording_steps):
                self.current_step_idx = max(0, len(self.recording_steps) - 1)
            self.refresh_ui()

    def add_link_to_selected_step(self, *args, **kwargs) -> None:
        if 0 <= self.selected_step_idx < len(self.recording_steps):
            step = self.recording_steps[self.selected_step_idx]
            if step.get("type") != "center":
                return
            selected = file_explorer.select_file(
                default_path=str(self.tutorials_dir),
                extensions=[("JSON files", "*.json")],
            )
            if selected:
                rel_path = self._format_rel_tutorial_path(selected)
                default_lbl = Path(selected).stem
                links_list = step.setdefault("links", [])
                links_list.append({"label": default_lbl, "path": rel_path})
                self._last_rendered_links_key = None
                self.refresh_ui()

    def play_single_step(self, idx):
        if self.is_recording:
            return
        self.is_playing = True
        self.single_step_mode = True
        self.current_step_idx = idx
        self.selected_step_idx = idx
        btn = getattr(self, "btn_play", 0)
        if dpg.does_item_exist(btn):
            dpg.configure_item(btn, label="Stop Playback")
        self._init_ui_components()
        self._update_status_text()
        logger.info(f"Single step playback started for step {idx + 1}")

    def load_and_play(self, filepath):
        if self.is_playing:
            self.toggle_play()
        self.load_tutorial(filepath)
        threading.Timer(0.1, lambda: self.toggle_play() if not self.is_playing else None).start()

    def _update_status_text(self):
        with self._overlay_lock:
            if self.is_playing:
                m = f"Step {self.current_step_idx + 1}/{max(1, len(self.recording_steps))}"
                if dpg.does_item_exist(getattr(self, "play_status_text", 0)):
                    dpg.configure_item(self.play_status_text, default_value=m)
                if dpg.does_item_exist(self.winID) and dpg.is_item_shown(self.winID):
                    self._scroll_ui_to_current_step()
                self._on_step_changed()

    def _on_step_changed(self):
        if not self.is_playing or self.current_step_idx >= len(self.recording_steps):
            return

        self._step_action_executed = False
        self._consumed_packet_indices.clear()
        self._pending_playback_advance = False
        self._pending_playback_step_idx = -1
        self._last_resolved_step = -1
        self._last_step_focused = -1
        self._last_rendered_links_key = None

        step = self.recording_steps[self.current_step_idx]
        view_name = step.get("view")
        if view_name:
            try:
                from core.module_registry import apply_named_view
                if apply_named_view(view_name):
                    self._last_resolved_step = -1
                    self.last_interaction_time = time.time()
            except Exception as e:
                logger.warning(f"Failed to apply tutorial step view '{view_name}': {e}")

        # Auto-execute silently & advance to next step if step is in 'script' or 'data' mode
        step_type = step.get("type", "interact")
        if step_type in ("script", "data") or step.get("auto_exec", False):
            self._hide_overlay_completely()
            self._execute_step_payload(step, auto_advance=True)
            return

        icon_type = step.get("icon_type")
        if not icon_type:
            if "error" in step_type:
                icon_type = "error"
            elif "warning" in step_type:
                icon_type = "warning"
            else:
                icon_type = "tutorial"
        self._load_mascot_texture(icon_type)

    def replay_current_step_action(self, *args, **kwargs) -> None:
        """Manually trigger/re-inject the current step's captured data or scripted UI action."""
        if 0 <= self.current_step_idx < len(self.recording_steps):
            step = self.recording_steps[self.current_step_idx]
            self._execute_step_payload(step, force=True, auto_advance=False)

    def _execute_step_payload(
        self, step: Dict[str, Any], force: bool = False, auto_advance: bool = False
    ) -> None:
        """
        Execute the scripted UI action (`action_meta`) and/or inject captured `data_packets`
        associated with `step` on the DPG main thread.
        When `auto_advance=True`, automatically advances to the next tutorial step after execution.
        """
        if not force and self._step_action_executed:
            if auto_advance and self.is_playing:
                self.next_step()
            return

        action_meta = step.get("action_meta")
        data_packets = step.get("data_packets") or []
        if not action_meta and not data_packets:
            self._step_action_executed = True
            if auto_advance and self.is_playing:
                self.next_step()
            return

        expected_step_idx = self.current_step_idx

        def _run_on_main() -> None:
            from core.data_stream_interceptor import data_stream_interceptor

            data_stream_interceptor.ensure_all_hooked()
            step_type = step.get("type", "interact")
            tut_dir = self._get_tutorial_dir()

            if force:
                self._consumed_packet_indices.clear()

            # 1. Pure 'data' injection mode: directly inject captured data packets into target module's input_cb
            if step_type == "data":
                injected_any = False
                for idx, pkt in enumerate(data_packets):
                    if not force and idx in self._consumed_packet_indices:
                        continue
                    if data_stream_interceptor.inject_data_packet(pkt, tutorial_dir=tut_dir):
                        self._consumed_packet_indices.add(idx)
                        injected_any = True
                if not injected_any and action_meta:
                    data_stream_interceptor.execute_ui_action(step)
                self._step_action_executed = True
                if auto_advance and self.is_playing and self.current_step_idx == expected_step_idx:
                    self.next_step()
                return

            # 2. 'script' or 'interact' fallback:
            # First run the UI callback if recorded (e.g., 'clear_samples_cb' or '_on_trigger_clicked').
            # While the UI callback runs, `on_live_data_during_playback` automatically intercepts any
            # emitted input_cb call and substitutes the exact recorded payload!
            action_ran = False
            if action_meta:
                action_ran = data_stream_interceptor.execute_ui_action(step)

            # 3. For any data packets that were NOT consumed during the UI action
            # (e.g. if the sender widget was not available or step only has data packets), inject them directly.
            for idx, pkt in enumerate(data_packets):
                if not force and idx in self._consumed_packet_indices:
                    continue
                if action_ran and pkt.get("is_side_effect_of_action", False):
                    self._consumed_packet_indices.add(idx)
                    continue
                if data_stream_interceptor.inject_data_packet(pkt, tutorial_dir=tut_dir):
                    self._consumed_packet_indices.add(idx)

            self._step_action_executed = True
            if auto_advance and self.is_playing and self.current_step_idx == expected_step_idx:
                self.next_step()

        try:
            from core.automation_manager import automation_manager
            automation_manager.post_to_main_thread(_run_on_main, wait=False)
        except Exception:
            _run_on_main()

    def on_live_data_during_playback(
        self,
        target_module: Any,
        sender_mod: Optional[Any],
        args: Tuple[Any, ...],
        kwargs: Dict[str, Any],
    ) -> Optional[Tuple[Tuple[Any, ...], Dict[str, Any]]]:
        """
        Called by `DataStreamInterceptor` when a live `target_module.input_cb(*args, **kwargs)`
        occurs while `self.is_playing` is True.
        If the current step has an unconsumed `data_packet` targeting `target_module`,
        substitutes the recorded `(args, kwargs)` so replay is 100% deterministic!
        """
        if not self.is_playing or not (0 <= self.current_step_idx < len(self.recording_steps)):
            return None

        from core.data_stream_interceptor import data_stream_interceptor, resolve_module_selector

        step = self.recording_steps[self.current_step_idx]
        packets = step.get("data_packets") or []
        if not packets:
            return None

        tut_dir = self._get_tutorial_dir()

        for idx, pkt in enumerate(packets):
            if idx in self._consumed_packet_indices:
                continue
            expected_target = resolve_module_selector(pkt.get("target_selector"))
            if expected_target is target_module:
                self._consumed_packet_indices.add(idx)
                self._step_action_executed = True

                if pkt.get("is_side_effect_of_action", False):
                    return None

                payload = pkt.get("payload")
                if isinstance(payload, dict):
                    try:
                        rec_args, rec_kwargs = data_stream_interceptor.codec.deserialize_call(
                            payload, tutorial_dir=tut_dir
                        )
                        logger.info(
                            f"Tutorial playback: substituted live {pkt.get('io_type', 'data')} "
                            f"with recorded payload for '{getattr(target_module, 'label', target_module)}'"
                        )
                        return rec_args, rec_kwargs
                    except Exception as e:
                        logger.error(f"Failed substituting recorded payload during playback: {e}")
                return None

        return None

    def on_data_flow_captured(
        self,
        packet: Dict[str, Any],
        sender_mod: Optional[Any],
        sender_method: str,
        target_module: Any,
    ) -> None:
        """
        Callback invoked by `DataStreamInterceptor` when a root `input_cb` transmission
        is captured during tutorial recording.
        """
        if not self.is_recording or not self.record_data_enabled:
            return

        now = time.time()
        recent_step = (
            self.recording_steps[-1]
            if (
                self.recording_steps
                and (now - self._last_ui_record_time < 1.0)
                and self.recording_steps[-1].get("type") in ("interact", "script")
            )
            else None
        )

        # Check if this data transmission was triggered by the just-recorded UI click
        if recent_step is not None:
            action_meta = recent_step.get("action_meta") or {}
            action_mod_uuid = (action_meta.get("target_selector") or {}).get("uuid", "")
            sender_uuid = getattr(sender_mod, "UUID", "") if sender_mod else ""
            action_method = action_meta.get("method_name", "")

            is_side_effect = False
            if action_mod_uuid and action_mod_uuid == sender_uuid and action_method:
                if (
                    "clear" in action_method.lower()
                    or "delete" in action_method.lower()
                    or "deselect" in action_method.lower()
                ):
                    is_side_effect = True
                elif "unselect" in str(packet.get("summary", "")).lower():
                    is_side_effect = True

            packet["is_side_effect_of_action"] = is_side_effect
            pkt_list = recent_step.setdefault("data_packets", [])
            if is_side_effect and len(pkt_list) >= 3:
                return
            pkt_list.append(packet)

            if dpg.does_item_exist(getattr(self, "step_list_group", 0)):
                self.refresh_ui()
            logger.info(
                f"Attached captured data [{packet.get('summary')}] to UI step '{recent_step.get('instruction')}'"
            )
            return

        # Standalone Data Injection step (when data comes from an external/async trigger without a UI click)
        from core.data_stream_interceptor import build_module_selector

        target_win_id = getattr(target_module, "winID", None)
        path = self.build_item_path(target_win_id) if (target_win_id and dpg.does_item_exist(target_win_id)) else []
        sender_lbl = getattr(sender_mod, "label", "") if sender_mod else ""
        target_lbl = getattr(target_module, "label", "Module")
        io_type = packet.get("io_type", "data")

        if sender_lbl:
            instruction = f"Data [{io_type}] sent from '{sender_lbl}' to '{target_lbl}'"
        else:
            instruction = f"Inject [{io_type}] into '{target_lbl}'"

        new_step = {
            "path": path,
            "instruction": instruction,
            "dim": True,
            "type": "data",
            "target_selector": build_module_selector(target_module),
            "data_packets": [packet],
        }
        self.recording_steps.append(new_step)
        self.selected_step_idx = len(self.recording_steps) - 1
        self.last_interaction_time = now
        if dpg.does_item_exist(getattr(self, "step_list_group", 0)):
            self.refresh_ui()
            self._scroll_ui_to_current_step()
        logger.info(f"Recorded standalone data step: {instruction} ({packet.get('summary')})")

    def prev_step(self, *args, **kwargs):
        with self._overlay_lock:
            if self.is_playing and self.current_step_idx > 0:
                target_idx = self.current_step_idx - 1
                while target_idx > 0:
                    st = self.recording_steps[target_idx]
                    if st.get("type", "interact") in ("script", "data") or st.get("auto_exec", False):
                        target_idx -= 1
                    else:
                        break
                self.current_step_idx = target_idx
                self._update_status_text()
                self._update_overlay_impl()

    def manual_next_step(self, *args, **kwargs):
        if self.is_playing:
            if 0 <= self.current_step_idx < len(self.recording_steps):
                step = self.recording_steps[self.current_step_idx]
                if not self._step_action_executed and (step.get("data_packets") or step.get("action_meta")):
                    self._execute_step_payload(step)
            self.next_step()

    def next_step(self, *args, **kwargs):
        with self._overlay_lock:
            if self.is_playing:
                self.last_interaction_time = time.time()
                self._pending_playback_advance = False
                self._pending_playback_step_idx = -1
                if self.single_step_mode:
                    self.toggle_play()
                    return

                if self.current_step_idx < len(self.recording_steps) - 1:
                    self.current_step_idx += 1
                    self._update_status_text()
                    self._update_overlay_impl()
                else:
                    self.toggle_play()

    def _scroll_ui_to_current_step(self):
        if self.is_playing and 0 <= self.current_step_idx < len(self.recording_steps):
            self.select_step(self.current_step_idx)

    def _get_step_summary_badge(self, step: Dict[str, Any]) -> Tuple[str, str]:
        """Return (type_badge, target_display_name) for a step row in the tutorial table."""
        step_type = step.get("type", "interact")
        badge = self.STEP_BADGES.get(step_type, step_type.upper()[:5])
        primary_packets = [
            p for p in (step.get("data_packets") or []) if not p.get("is_side_effect_of_action", False)
        ]

        name = "Unknown"
        path = step.get("path", [])
        if step_type == "data" and step.get("data_packets"):
            pkt = step["data_packets"][0]
            tgt_lbl = (pkt.get("target_selector") or {}).get("label") or "Target"
            io_t = pkt.get("io_type", "data")
            name = f"{tgt_lbl} ({io_t})"
        elif path:
            last_item = path[-1]
            name = (
                last_item.get("label")
                or str(last_item.get("alias") or last_item.get("tag"))
                or last_item.get("type", "").split("::")[-1]
            )
            if primary_packets:
                io_t = primary_packets[0].get("io_type", "data")
                name = f"{name} [{io_t}]"
        elif step.get("action_meta"):
            am = step["action_meta"]
            tgt_lbl = (am.get("target_selector") or {}).get("label") or "Module"
            m_name = am.get("method_name") or "action"
            name = f"{tgt_lbl}.{m_name}"
        elif step_type == "center":
            name = "Center Popup"

        step_view = step.get("view")
        if step_view:
            name = f"{name} [{step_view}]"

        return badge, name

    @staticmethod
    def _get_badge_color(badge: str) -> Tuple[int, int, int]:
        if badge == "DATA":
            return (100, 220, 255)
        if badge == "AUTO":
            return (130, 230, 150)
        if badge == "POPUP":
            return (230, 200, 120)
        return (200, 200, 200)

    def _update_single_row_summary(self, idx: int) -> None:
        """Update a single table row's badge and target label in-place without rebuilding the entire table."""
        if not (0 <= idx < len(self.recording_steps)):
            return
        if not (0 <= idx < len(getattr(self, "_row_widgets", []))):
            self.refresh_ui()
            return
        _, badge_item, sel_name = self._row_widgets[idx]
        badge, name = self._get_step_summary_badge(self.recording_steps[idx])
        if dpg.does_item_exist(badge_item):
            dpg.set_value(badge_item, badge)
            dpg.configure_item(badge_item, color=self._get_badge_color(badge))
        if dpg.does_item_exist(sel_name):
            dpg.configure_item(sel_name, label=f"{name}")

    def refresh_ui(self):
        if not dpg.does_item_exist(getattr(self, "step_list_group", 0)):
            return
        dpg.delete_item(self.step_list_group, children_only=True)
        self._step_label_items = []
        self._row_selectables = []
        self._row_widgets = []

        if self.recording_steps:
            self.selected_step_idx = max(0, min(self.selected_step_idx, len(self.recording_steps) - 1))
        else:
            self.selected_step_idx = 0

        self._update_step_toolbar_state()

        with dpg.table(
            parent=self.step_list_group,
            header_row=True,
            row_background=True,
            borders_innerH=True,
            borders_outerH=True,
            borders_innerV=True,
            borders_outerV=True,
            resizable=True,
            policy=dpg.mvTable_SizingStretchProp,
        ):
            dpg.add_table_column(label="Dim", width_fixed=True, init_width_or_weight=28)
            dpg.add_table_column(label="#", width_fixed=True, init_width_or_weight=24)
            dpg.add_table_column(label="Type", width_fixed=True, init_width_or_weight=48)
            dpg.add_table_column(label="Target", width_fixed=True, init_width_or_weight=120)
            dpg.add_table_column(label="Instruction", width_stretch=True)

            for i, step in enumerate(self.recording_steps):
                badge, name = self._get_step_summary_badge(step)
                is_selected = i == self.selected_step_idx

                def _on_select_row(s, a, u, *args, **kwargs):
                    if isinstance(u, int):
                        self.select_step(u)

                def _update_dim(s, a, u, *args, **kwargs):
                    if isinstance(u, int) and 0 <= u < len(self.recording_steps):
                        self.recording_steps[u]["dim"] = a
                        self.select_step(u)

                def _update_instr(s, a, u, *args, **kwargs):
                    if isinstance(u, int) and 0 <= u < len(self.recording_steps):
                        self.recording_steps[u]["instruction"] = a
                        if u == self.current_step_idx:
                            self._last_rendered_step_ui = -1
                        self.select_step(u)

                with dpg.table_row():
                    dpg.add_checkbox(default_value=step.get("dim", True), callback=_update_dim, user_data=i)
                    sel_num = dpg.add_selectable(
                        label=f"{i + 1}",
                        default_value=is_selected,
                        callback=_on_select_row,
                        user_data=i,
                    )
                    badge_item = dpg.add_text(badge, color=self._get_badge_color(badge))
                    sel_name = dpg.add_selectable(
                        label=f"{name}",
                        default_value=is_selected,
                        callback=_on_select_row,
                        user_data=i,
                    )
                    self._row_selectables.append((sel_num, sel_name))
                    self._row_widgets.append((sel_num, badge_item, sel_name))

                    with dpg.group():
                        dpg.add_input_text(
                            default_value=step.get("instruction", ""),
                            width=-1,
                            callback=_update_instr,
                            user_data=i,
                        )
                        links = step.get("links") or []
                        for j, link_entry in enumerate(links):
                            disp_lbl, _, _ = self._resolve_tutorial_link(link_entry)
                            raw_path = link_entry.get("path", "") if isinstance(link_entry, dict) else str(link_entry)

                            def _del_link(s, a, u, *args, **kwargs):
                                step_idx, link_idx = u
                                if 0 <= step_idx < len(self.recording_steps):
                                    l_list = self.recording_steps[step_idx].get("links", [])
                                    if 0 <= link_idx < len(l_list):
                                        l_list.pop(link_idx)
                                        self._last_rendered_links_key = None
                                        self.select_step(step_idx)
                                        self.refresh_ui()

                            def _upd_link_label(s, a, u, *args, **kwargs):
                                step_idx, link_idx = u
                                if 0 <= step_idx < len(self.recording_steps):
                                    l_list = self.recording_steps[step_idx].get("links", [])
                                    if 0 <= link_idx < len(l_list):
                                        cur = l_list[link_idx]
                                        if isinstance(cur, dict):
                                            cur["label"] = a
                                        else:
                                            l_list[link_idx] = {"label": a, "path": str(cur)}
                                        self._last_rendered_links_key = None

                            with dpg.group(horizontal=True):
                                dpg.add_text("\uf061", color=(255, 150, 0))
                                dpg.add_button(label="X", callback=_del_link, user_data=(i, j))
                                dpg.add_input_text(
                                    default_value=disp_lbl,
                                    width=90,
                                    callback=_upd_link_label,
                                    user_data=(i, j),
                                )
                                dpg.add_text(f"({raw_path})", color=(150, 150, 150))

    # ------------------------------------------------------------------
    # Step Data & Action Inspector Window
    # ------------------------------------------------------------------
    def show_step_inspector(self, *args, **kwargs) -> None:
        """Open a window to inspect, test, or edit the selected step's data packets & scripted UI actions."""
        if not (0 <= self.selected_step_idx < len(self.recording_steps)):
            return

        if dpg.does_item_exist(self.inspector_win_id):
            dpg.show_item(self.inspector_win_id)
            dpg.focus_item(self.inspector_win_id)
        else:
            with dpg.window(
                label="Step Data & Script Inspector",
                width=520,
                height=480,
                pos=(self.pos[0] + 60, self.pos[1] + 40),
                tag=self.inspector_win_id,
            ):
                self.inspector_content_group = dpg.add_group()

        self._populate_step_inspector()

    def _populate_step_inspector(self) -> None:
        group = getattr(self, "inspector_content_group", 0)
        if not group or not dpg.does_item_exist(group):
            return
        dpg.delete_item(group, children_only=True)

        if not (0 <= self.selected_step_idx < len(self.recording_steps)):
            dpg.add_text("No step selected.", parent=group)
            return

        step_idx = self.selected_step_idx
        step = self.recording_steps[step_idx]

        dpg.add_button(
            label="Test / Execute This Step Now",
            width=-1,
            parent=group,
            callback=lambda *_: self._execute_step_payload(step, force=True),
        )
        dpg.add_separator(parent=group)

        packets = step.get("data_packets") or []
        if not packets:
            dpg.add_text("No data packets attached to this step.", color=(160, 160, 160), parent=group)
            return

        for pkt_idx, pkt in enumerate(packets):
            src_sel = pkt.get("sender_selector") or {}
            tgt_sel = pkt.get("target_selector") or {}
            src_name = f"{src_sel.get('label') or src_sel.get('class_name') or 'Source'}"
            tgt_name = f"{tgt_sel.get('label') or tgt_sel.get('class_name') or 'Target'}"
            summary = pkt.get("summary", pkt.get("io_type", "data"))

            with dpg.collapsing_header(
                label=f"{src_name} -> {tgt_name}  [{summary}]",
                default_open=(pkt_idx == 0),
                parent=group,
            ):
                with dpg.group(horizontal=True):
                    def _test_pkt(s, a, u, *args):
                        p = u
                        from core.data_stream_interceptor import data_stream_interceptor
                        from core.automation_manager import automation_manager
                        automation_manager.post_to_main_thread(
                            lambda: data_stream_interceptor.inject_data_packet(p, tutorial_dir=self._get_tutorial_dir()),
                            wait=False,
                        )

                    def _del_pkt(s, a, u, *args):
                        s_i, p_i = u
                        if 0 <= s_i < len(self.recording_steps):
                            p_list = self.recording_steps[s_i].get("data_packets", [])
                            if 0 <= p_i < len(p_list):
                                p_list.pop(p_i)
                                self.refresh_ui()
                                self._populate_step_inspector()

                    dpg.add_button(label="Send Packet Now", callback=_test_pkt, user_data=pkt)
                    dpg.add_button(label="Remove Packet", callback=_del_pkt, user_data=(step_idx, pkt_idx))

                payload_dict = pkt.get("payload", {})
                try:
                    payload_json_str = json.dumps(payload_dict, indent=2)
                except Exception:
                    payload_json_str = str(payload_dict)

                editor_tag = dpg.generate_uuid()
                dpg.add_input_text(
                    multiline=True,
                    default_value=payload_json_str,
                    width=-1,
                    height=200,
                    tag=editor_tag,
                )

                def _save_payload_edits(s, a, u, *args):
                    ed_tag, target_pkt = u
                    if dpg.does_item_exist(ed_tag):
                        raw_txt = dpg.get_value(ed_tag)
                        try:
                            parsed = json.loads(raw_txt)
                            if isinstance(parsed, dict):
                                target_pkt["payload"] = parsed
                                logger.info("Updated tutorial data packet JSON payload.")
                        except Exception as err:
                            logger.error(f"Invalid JSON in payload editor: {err}")

                dpg.add_button(
                    label="Save JSON Changes",
                    callback=_save_payload_edits,
                    user_data=(editor_tag, pkt),
                    width=-1,
                )

    # ------------------------------------------------------------------
    # Manual Data / Script Step Builder Window
    # ------------------------------------------------------------------
    def show_step_builder(self, *args, **kwargs) -> None:
        """Open a dialog to manually create a Data Injection or Scripted Module Action step."""
        if dpg.does_item_exist(self.builder_win_id):
            dpg.delete_item(self.builder_win_id)

        from core.module_registry import MODULES_REGISTRY
        from core.input_output_types import IOTypes
        from core.data_stream_interceptor import build_module_selector

        mod_items: List[str] = []
        mod_map: Dict[str, Any] = {}
        for uid, mod in MODULES_REGISTRY.items():
            key = f"{getattr(mod, 'label', 'Module')} ({mod.__class__.__name__} [{uid}])"
            mod_items.append(key)
            mod_map[key] = mod

        io_items = [item.value for item in IOTypes]

        default_sample_template = json.dumps(
            {
                "name": "Tutorial Sample",
                "uuid": "tut_sample_1",
                "x": [0.0, 1.0, 2.0, 3.0, 4.0],
                "y": [0.0, 5.0, 10.0, 5.0, 0.0],
                "action": "select",
            },
            indent=2,
        )

        with dpg.window(
            label="Add Manual Data / Action Step",
            width=460,
            height=430,
            pos=(self.pos[0] + 50, self.pos[1] + 50),
            tag=self.builder_win_id,
        ):
            dpg.add_text("Target Module:")
            mod_combo = dpg.add_combo(
                items=mod_items,
                default_value=mod_items[0] if mod_items else "",
                width=-1,
            )

            dpg.add_text("Action Type:")
            kind_combo = dpg.add_combo(
                items=["Send Data to Module Input (input_cb)", "Run Module Function (e.g. clear_samples_cb)"],
                default_value="Send Data to Module Input (input_cb)",
                width=-1,
            )

            data_group = dpg.add_group(show=True)
            with dpg.group(parent=data_group):
                dpg.add_text("Data Type (IOTypes):")
                io_combo = dpg.add_combo(items=io_items, default_value="sample", width=-1)
                dpg.add_text("JSON Payload:")
                payload_input = dpg.add_input_text(
                    multiline=True,
                    default_value=default_sample_template,
                    width=-1,
                    height=160,
                )

            method_group = dpg.add_group(show=False)
            with dpg.group(parent=method_group):
                dpg.add_text("Function to call on module:")
                method_combo = dpg.add_combo(items=[], default_value="", width=-1)

            def _refresh_methods(*_):
                sel_key = dpg.get_value(mod_combo)
                mod = mod_map.get(sel_key)
                methods: List[str] = []
                if mod is not None:
                    for attr in dir(mod):
                        if attr.startswith("__"):
                            continue
                        if attr.endswith("_cb") or attr in ("clear_plot", "reset", "trigger"):
                            try:
                                if callable(getattr(mod, attr)):
                                    methods.append(attr)
                            except Exception:
                                pass
                dpg.configure_item(method_combo, items=methods, default_value=methods[0] if methods else "")

            def _on_kind_change(s, a, *_):
                is_data = a == "Send Data to Module Input (input_cb)"
                dpg.configure_item(data_group, show=is_data)
                dpg.configure_item(method_group, show=not is_data)
                if not is_data:
                    _refresh_methods()

            dpg.set_item_callback(kind_combo, _on_kind_change)
            dpg.set_item_callback(mod_combo, lambda *_: _refresh_methods())
            _refresh_methods()

            dpg.add_spacer(height=8)

            def _on_add_clicked(*_):
                sel_key = dpg.get_value(mod_combo)
                target_mod = mod_map.get(sel_key)
                if target_mod is None:
                    logger.warning("No target module selected.")
                    return

                win_id = getattr(target_mod, "winID", None)
                path = self.build_item_path(win_id) if (win_id and dpg.does_item_exist(win_id)) else []
                selector = build_module_selector(target_mod)
                kind = dpg.get_value(kind_combo)

                if kind == "Send Data to Module Input (input_cb)":
                    io_val = dpg.get_value(io_combo) or "sample"
                    raw_json = dpg.get_value(payload_input)
                    try:
                        parsed_data = json.loads(raw_json)
                    except Exception:
                        parsed_data = raw_json

                    kwargs_dict: Dict[str, Any] = {
                        "data": parsed_data,
                        "data_type": {"__pc_type__": "IOTypes", "value": io_val},
                    }
                    if io_val in ("sample", "roi_sample") and isinstance(parsed_data, dict):
                        kwargs_dict["sample"] = parsed_data
                    elif io_val == "cmd_dict" and isinstance(parsed_data, dict):
                        kwargs_dict["cmd"] = parsed_data
                    elif io_val in ("file_path", "folder_path"):
                        kwargs_dict["path"] = parsed_data

                    pkt = {
                        "sender_selector": {},
                        "sender_method": "manual_tutorial_step",
                        "target_selector": selector,
                        "io_type": io_val,
                        "summary": f"{io_val.upper()} (Manual)",
                        "payload": {"args": [], "kwargs": kwargs_dict},
                    }
                    self.recording_steps.append(
                        {
                            "path": path,
                            "instruction": f"Send [{io_val}] into '{getattr(target_mod, 'label', 'Module')}'",
                            "dim": True,
                            "type": "data",
                            "target_selector": selector,
                            "data_packets": [pkt],
                        }
                    )
                else:
                    m_name = dpg.get_value(method_combo).strip()
                    if not m_name:
                        return
                    self.recording_steps.append(
                        {
                            "path": path,
                            "instruction": f"Run '{m_name}' on '{getattr(target_mod, 'label', 'Module')}'",
                            "dim": True,
                            "type": "script",
                            "action_meta": {
                                "target_selector": selector,
                                "widget_type": "mvAppItemType::mvButton",
                                "widget_alias": "",
                                "widget_label": m_name,
                                "method_name": m_name,
                                "value": None,
                            },
                        }
                    )

                self.selected_step_idx = len(self.recording_steps) - 1
                self.refresh_ui()
                self._scroll_ui_to_current_step()
                if dpg.does_item_exist(self.builder_win_id):
                    dpg.delete_item(self.builder_win_id)

            dpg.add_button(label="Add Step to Tutorial", callback=_on_add_clicked, width=-1)

    def _start_worker(self) -> None:
        try:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._worker_event = threading.Event()
                self._worker_event.set()
                self._worker_thread = threading.Thread(target=self._thread_loop, daemon=True)
                self._worker_thread.start()
                if logger is not None:
                    logger.debug("TutorialManager worker thread started.")
        except Exception:
            pass

    def _stop_worker(self) -> None:
        try:
            if self._worker_event:
                self._worker_event.clear()
            self._worker_thread = None
            if logger is not None:
                logger.debug("TutorialManager worker thread stopped.")
        except Exception:
            pass

    def _thread_loop(self):
        _last_step = -1
        _last_playing = False
        while self.is_alive and self._is_playing and self._worker_event and self._worker_event.is_set():
            try:
                step_changed = self.current_step_idx != _last_step or self.is_playing != _last_playing
                if step_changed or self.is_playing:
                    self._update_overlay()
                    _last_step = self.current_step_idx
                    _last_playing = self.is_playing
            except Exception:
                pass
            if self._worker_event:
                self._worker_event.wait(timeout=0.033)

    def _resolve_step_fallback_window(self, step: Dict[str, Any]) -> Any:
        """
        If a step is a 'data' step or its widget path cannot be found, resolve the
        target module's window (`winID`) so the tutorial highlights the target module window.
        """
        from core.data_stream_interceptor import resolve_module_selector

        selector = step.get("target_selector")
        if not selector and step.get("data_packets"):
            selector = step["data_packets"][0].get("target_selector")
        if not selector and step.get("action_meta"):
            selector = step["action_meta"].get("target_selector")

        if selector:
            mod = resolve_module_selector(selector)
            if mod is not None:
                win_id = getattr(mod, "winID", None)
                if win_id and dpg.does_item_exist(win_id):
                    return win_id
        return None

    def _update_overlay(self):
        with self._overlay_lock:
            self._update_overlay_impl()

    def _update_overlay_impl(self):
        if not self.is_playing or self.current_step_idx >= len(self.recording_steps):
            self._hide_overlay_completely_impl()
            return

        if not self._ui_initialized:
            return

        step = self.recording_steps[self.current_step_idx]
        should_dim = step.get("dim", True)
        step_type = step.get("type", "interact")

        # Auto-executed steps ('script' / 'data') run silently without showing any popup or overlay
        if step_type in ("script", "data") or step.get("auto_exec", False):
            self._hide_overlay_completely_impl()
            return

        vw = dpg.get_viewport_client_width()
        vh = dpg.get_viewport_client_height()

        now = time.time()
        should_resolve = (
            self.current_step_idx != getattr(self, "_last_resolved_step", -1)
            or now - getattr(self, "_last_resolve_time", 0) > 1.0
            or not dpg.does_item_exist(getattr(self, "_cached_target", 0))
        )

        if should_resolve:
            if step_type == "data":
                target = self._resolve_step_fallback_window(step) or self.resolve_item_path(step.get("path", []))
            else:
                target = self.resolve_item_path(step.get("path", [])) or self._resolve_step_fallback_window(step)
            self._cached_target = target
            self._last_resolved_step = self.current_step_idx
            self._last_resolve_time = now
            self._cached_target_is_window = False
            if target and dpg.does_item_exist(target):
                try:
                    info = dpg.get_item_info(target)
                    self._cached_target_is_window = info.get("type", "") == "mvAppItemType::mvWindowAppItem"
                except Exception:
                    self._cached_target_is_window = False
        else:
            target = self._cached_target

        if dpg.does_item_exist(self.overlay_drawlist):
            dpg.configure_item(self.overlay_drawlist, show=True)

        target_found = False
        t_min, t_max = [0, 0], [0, 0]
        is_window = getattr(self, "_cached_target_is_window", False)

        if target and dpg.does_item_exist(target):
            try:
                if is_window:
                    w_pos = dpg.get_item_pos(target)
                    w_size = dpg.get_item_rect_size(target)
                    if w_size[0] <= 0:
                        w_size[0] = dpg.get_item_width(target) or 300
                    if w_size[1] <= 0:
                        w_size[1] = dpg.get_item_height(target) or 200
                    t_min = [w_pos[0], w_pos[1]]
                    t_max = [w_pos[0] + w_size[0], w_pos[1] + w_size[1]]
                else:
                    t_min = list(dpg.get_item_rect_min(target))
                    t_max = list(dpg.get_item_rect_max(target))

                if t_max[0] <= t_min[0]:
                    t_max[0] = t_min[0] + 1
                if t_max[1] <= t_min[1]:
                    t_max[1] = t_min[1] + 1
                target_found = True
            except Exception:
                target_found = False

        if target_found:
            if target != self._last_highlighted_item:
                self._clear_window_highlight()
                try:
                    dpg.bind_item_theme(target, self.highlight_theme)
                    self._last_highlighted_item = target
                except Exception:
                    pass
            dpg.configure_item(
                self.overlay_border, pmin=t_min, pmax=t_max, show=True, thickness=2 if is_window else 4
            )
        else:
            self._clear_window_highlight()
            dpg.configure_item(self.overlay_border, show=False)

        # Check for auto-advance on hover for "Observe" steps (but NOT for entire windows)
        if target_found and (step_type == "observe" or step.get("instruction", "").startswith("Observe")):
            try:
                if not is_window and dpg.is_item_hovered(target) and (now - self.last_interaction_time > 0.5):
                    dpg.split_frame()
                    self.next_step()
                    return
            except Exception:
                pass

        # Position HUD Window — resolve real dimensions for clamping
        if dpg.does_item_exist(self.overlay_info_win):
            self._update_overlay_links(step)

        hud_w_est, hud_h_est = 260, 140
        try:
            _s = dpg.get_item_rect_size(self.overlay_info_win)
            if _s[0] > 10:
                hud_w_est, hud_h_est = int(_s[0]), int(_s[1])
        except Exception:
            pass

        if target_found:
            tx = t_min[0]
            ty = t_max[1] + 10
            if ty + hud_h_est > vh:
                ty = t_min[1] - hud_h_est - 10
            if ty < 0:
                ty = vh // 2 - hud_h_est // 2
        else:
            tx = vw // 2 - hud_w_est // 2
            ty = vh // 2 - hud_h_est // 2

        tx = max(0, min(tx, vw - hud_w_est))
        ty = max(0, min(ty, vh - hud_h_est))

        force_focus = self.current_step_idx != getattr(self, "_last_step_focused", -1)
        periodic_reclaim = now - getattr(self, "_last_focus_time", 0) > 1.0

        if dpg.does_item_exist(self.overlay_info_win):
            dpg.set_item_pos(self.overlay_info_win, (tx, ty))
            instr = step.get("instruction", "...")
            dpg.set_value(self.overlay_msg, instr)

            badge_widget = getattr(self, "overlay_data_badge", 0)
            if badge_widget and dpg.does_item_exist(badge_widget):
                packets = [
                    p for p in (step.get("data_packets") or []) if not p.get("is_side_effect_of_action", False)
                ]
                action_meta = step.get("action_meta") or {}
                if packets:
                    summaries = ", ".join(p.get("summary", p.get("io_type", "data")) for p in packets[:2])
                    dpg.set_value(badge_widget, f"[Data: {summaries}]")
                    dpg.configure_item(badge_widget, show=True)
                elif step_type == "script" and action_meta.get("method_name"):
                    dpg.set_value(badge_widget, f"[Auto-Action: {action_meta.get('method_name')}]")
                    dpg.configure_item(badge_widget, show=True)
                else:
                    dpg.configure_item(badge_widget, show=False)

            is_hint = step_type in ("error_hint", "success_hint")
            if getattr(self, "nav_buttons_group", 0) and getattr(self, "error_button_group", 0):
                dpg.configure_item(self.nav_buttons_group, show=not is_hint)
                dpg.configure_item(self.error_button_group, show=is_hint)

            has_prev = self.current_step_idx > 0
            has_next = self.current_step_idx < len(self.recording_steps) - 1
            if getattr(self, "overlay_prev_btn", 0):
                dpg.configure_item(self.overlay_prev_btn, show=has_prev)
            if getattr(self, "overlay_replay_btn", 0):
                has_payload = bool(step.get("data_packets") or step.get("action_meta"))
                dpg.configure_item(self.overlay_replay_btn, show=has_payload)
            if getattr(self, "overlay_next_btn", 0):
                dpg.configure_item(self.overlay_next_btn, show=has_next)
            if getattr(self, "main_prev_btn", 0) and dpg.does_item_exist(self.main_prev_btn):
                dpg.configure_item(self.main_prev_btn, show=has_prev)
            if getattr(self, "main_next_btn", 0) and dpg.does_item_exist(self.main_next_btn):
                dpg.configure_item(self.main_next_btn, show=has_next)

            if force_focus and not getattr(self, "_is_mouse_down", False):
                dpg.show_item(self.overlay_info_win)
                dpg.focus_item(self.overlay_info_win)
                self._last_step_focused = self.current_step_idx
                self._last_focus_time = now
            elif periodic_reclaim and not getattr(self, "_is_mouse_down", False) and not self._pending_playback_advance:
                dpg.show_item(self.overlay_info_win)
                dpg.focus_item(self.overlay_info_win)
                self._last_focus_time = now

        h1 = (t_min, t_max)
        h2 = ((tx, ty), (tx + hud_w_est, ty + hud_h_est))

        if not target_found:
            if should_dim:
                dpg.configure_item(self.overlay_top, pmin=(0, 0), pmax=(vw, h2[0][1]), show=True)
                dpg.configure_item(self.overlay_bottom, pmin=(0, h2[1][1]), pmax=(vw, vh), show=True)
                dpg.configure_item(self.overlay_left, pmin=(0, h2[0][1]), pmax=(h2[0][0], h2[1][1]), show=True)
                dpg.configure_item(self.overlay_right, pmin=(h2[1][0], h2[0][1]), pmax=(vw, h2[1][1]), show=True)
                for itm in (self.overlay_mid, self.overlay_extra1, self.overlay_extra2):
                    if dpg.does_item_exist(itm):
                        dpg.configure_item(itm, show=False)
            else:
                for itm in (
                    self.overlay_top,
                    self.overlay_bottom,
                    self.overlay_left,
                    self.overlay_right,
                    self.overlay_mid,
                    self.overlay_extra1,
                    self.overlay_extra2,
                ):
                    if dpg.does_item_exist(itm):
                        dpg.configure_item(itm, show=False)
            return

        if h1[0][1] < h2[0][1]:
            h_high, h_low = h1, h2
        else:
            h_high, h_low = h2, h1

        if h_high[1][1] > h_low[0][1] - 5:
            h_min = (min(h1[0][0], h2[0][0]), min(h1[0][1], h2[0][1]))
            h_max = (max(h1[1][0], h2[1][0]), max(h1[1][1], h2[1][1]))
            if should_dim:
                dpg.configure_item(self.overlay_top, pmin=(0, 0), pmax=(vw, h_min[1]), show=True)
                dpg.configure_item(self.overlay_bottom, pmin=(0, h_max[1]), pmax=(vw, vh), show=True)
                dpg.configure_item(self.overlay_left, pmin=(0, h_min[1]), pmax=(h_min[0], h_max[1]), show=True)
                dpg.configure_item(self.overlay_right, pmin=(h_max[0], h_min[1]), pmax=(vw, h_max[1]), show=True)
                for itm in (self.overlay_mid, self.overlay_extra1, self.overlay_extra2):
                    dpg.configure_item(itm, show=False)
        else:
            if should_dim:
                dpg.configure_item(self.overlay_top, pmin=(0, 0), pmax=(vw, h_high[0][1]), show=True)
                dpg.configure_item(self.overlay_bottom, pmin=(0, h_low[1][1]), pmax=(vw, vh), show=True)
                dpg.configure_item(self.overlay_mid, pmin=(0, h_high[1][1]), pmax=(vw, h_low[0][1]), show=True)
                dpg.configure_item(
                    self.overlay_left, pmin=(0, h_high[0][1]), pmax=(h_high[0][0], h_high[1][1]), show=True
                )
                dpg.configure_item(
                    self.overlay_right, pmin=(h_high[1][0], h_high[0][1]), pmax=(vw, h_high[1][1]), show=True
                )
                dpg.configure_item(
                    self.overlay_extra1, pmin=(0, h_low[0][1]), pmax=(h_low[0][0], h_low[1][1]), show=True
                )
                dpg.configure_item(
                    self.overlay_extra2, pmin=(h_low[1][0], h_low[0][1]), pmax=(vw, h_low[1][1]), show=True
                )
            else:
                for itm in (
                    self.overlay_top,
                    self.overlay_bottom,
                    self.overlay_left,
                    self.overlay_right,
                    self.overlay_mid,
                    self.overlay_extra1,
                    self.overlay_extra2,
                ):
                    dpg.configure_item(itm, show=False)

    def _on_mouse_click(self, sender=None, app_data=None, user_data=None, *args, **kwargs):
        if not self.is_alive or not (self._is_playing or self._is_recording):
            return
        if time.time() - self.last_interaction_time < 0.2:
            return

        ms = dpg.get_mouse_pos(local=False)
        button = app_data

        if self._is_playing:
            self._check_playback_click(ms)
        elif self._is_recording:
            if button == dpg.mvMouseButton_Left and self.record_ui_enabled:
                self._check_record_click(ms)
            elif button == dpg.mvMouseButton_Middle:
                self._check_observe_record_click(ms)

    def _on_mouse_release(self, sender=None, app_data=None, user_data=None, *args, **kwargs):
        self._is_mouse_down = False
        if not self.is_alive or not (self._is_playing or self._is_recording):
            return

        if self._is_recording:
            try:
                from core.automation_manager import automation_manager
                automation_manager.post_to_main_thread(self._finalize_pending_widget_value, wait=False)
            except Exception:
                self._finalize_pending_widget_value()

        elif self._is_playing and self._pending_playback_advance:
            expected_idx = self._pending_playback_step_idx

            def _complete_advance() -> None:
                if not self.is_playing or self.current_step_idx != expected_idx:
                    return
                if 0 <= expected_idx < len(self.recording_steps):
                    step = self.recording_steps[expected_idx]
                    if not self._step_action_executed and (step.get("data_packets") or step.get("action_meta")):
                        self._execute_step_payload(step)
                self.next_step()

            try:
                from core.automation_manager import automation_manager
                automation_manager.post_to_main_thread(_complete_advance, wait=False)
            except Exception:
                _complete_advance()

    def _finalize_pending_widget_value(self) -> None:
        """Capture the final widget value after mouse release (e.g. after dragging a slider or toggling a checkbox)."""
        widget_id = self._last_recorded_widget_id
        step_ref = self._last_recorded_step_ref
        if not widget_id or not step_ref or not dpg.does_item_exist(widget_id):
            return
        try:
            action_meta = step_ref.get("action_meta")
            if isinstance(action_meta, dict):
                w_type = str(action_meta.get("widget_type", ""))
                if "mvButton" not in w_type and "mvMenuItem" not in w_type:
                    val = dpg.get_value(widget_id)
                    if val is not None:
                        action_meta["value"] = val
        except Exception:
            pass

    def _is_own_tutorial_window_path(self, path: List[Dict[str, Any]]) -> bool:
        """Return True if `path` belongs to TutorialManager's own control/inspector/builder windows."""
        own_tags = {self.winID, self.inspector_win_id, self.builder_win_id, str(self.overlay_info_win)}
        own_labels = {self.label, "Step Data & Script Inspector", "Add Manual Data / Action Step"}
        for p_step in path:
            if p_step.get("label") in own_labels:
                return True
            if str(p_step.get("tag")) in own_tags or str(p_step.get("alias")) in own_tags:
                return True
        return False

    def _check_observe_record_click(self, mouse_pos):
        hovered_item = None

        for item in dpg.get_all_items():
            try:
                if dpg.is_item_hovered(item):
                    hovered_item = item
            except Exception:
                pass

        if not hovered_item:
            return

        path = self.build_item_path(hovered_item)
        if self._is_own_tutorial_window_path(path):
            return

        cfg = dpg.get_item_configuration(hovered_item)
        name = cfg.get("label") or str(hovered_item)
        instruction = f"Observe '{name}'"

        self.recording_steps.append(
            {
                "path": path,
                "instruction": instruction,
                "dim": True,
                "type": "observe",
            }
        )
        self.selected_step_idx = len(self.recording_steps) - 1
        if dpg.does_item_exist(getattr(self, "step_list_group", 0)):
            self.refresh_ui()
            self._scroll_ui_to_current_step()
        self.last_interaction_time = time.time()
        logger.info(f"Recorded observation step: {instruction}")

    def _check_playback_click(self, mouse_pos):
        if len(self.recording_steps) == 0 or self.current_step_idx >= len(self.recording_steps):
            return

        step = self.recording_steps[self.current_step_idx]
        step_type = step.get("type", "interact")
        target = getattr(self, "_cached_target", None)
        if not target or not dpg.does_item_exist(target):
            if step_type == "data":
                target = self._resolve_step_fallback_window(step) or self.resolve_item_path(step.get("path", []))
            else:
                target = self.resolve_item_path(step.get("path", [])) or self._resolve_step_fallback_window(step)

        if target and dpg.does_item_exist(target):
            try:
                info = dpg.get_item_info(target)
                is_window = info.get("type", "") == "mvAppItemType::mvWindowAppItem"

                if is_window:
                    w_pos = dpg.get_item_pos(target)
                    w_size = dpg.get_item_rect_size(target)
                    if w_size[0] <= 0:
                        w_size[0] = dpg.get_item_width(target) or 300
                    if w_size[1] <= 0:
                        w_size[1] = dpg.get_item_height(target) or 200
                    min_p = [w_pos[0], w_pos[1]]
                    max_p = [w_pos[0] + w_size[0], w_pos[1] + w_size[1]]
                else:
                    min_p = dpg.get_item_rect_min(target)
                    max_p = dpg.get_item_rect_max(target)

                if min_p[0] <= mouse_pos[0] <= max_p[0] and min_p[1] <= mouse_pos[1] <= max_p[1]:
                    self._pending_playback_advance = True
                    self._pending_playback_step_idx = self.current_step_idx
            except Exception:
                pass

    def _check_record_click(self, mouse_pos):
        hovered_item = None

        # Check `is_item_hovered` FIRST (fast C boolean) before allocating `get_item_info` dicts
        for item in dpg.get_all_items():
            try:
                if dpg.is_item_hovered(item):
                    info = dpg.get_item_info(item)
                    if info and info.get("type", "") in self.INTERACTIVE_TYPES:
                        hovered_item = item
            except Exception:
                pass

        if hovered_item:
            self._finalize_pending_widget_value()

            path = self.build_item_path(hovered_item)
            if self._is_own_tutorial_window_path(path):
                return

            last_item = path[-1]
            name = last_item.get("label") or str(hovered_item) or last_item.get("type", "").split("::")[-1]
            instruction = f"Interact with '{name}'"

            from core.data_stream_interceptor import build_module_selector, find_module_for_dpg_item

            owner_mod, method_name = find_module_for_dpg_item(hovered_item, path)
            w_type = last_item.get("type", "")
            initial_val = None
            if "mvButton" not in w_type and "mvMenuItem" not in w_type:
                try:
                    initial_val = dpg.get_value(hovered_item)
                except Exception:
                    initial_val = None

            action_meta = {
                "target_selector": build_module_selector(owner_mod),
                "widget_type": w_type,
                "widget_alias": str(last_item.get("alias") or ""),
                "widget_label": str(last_item.get("label") or ""),
                "method_name": method_name,
                "value": initial_val,
            }

            new_step: Dict[str, Any] = {
                "path": path,
                "instruction": instruction,
                "dim": True,
                "type": "interact",
                "action_meta": action_meta,
            }
            self.recording_steps.append(new_step)
            self._last_ui_record_time = time.time()
            self._last_recorded_widget_id = hovered_item
            self._last_recorded_step_ref = new_step

            self.selected_step_idx = len(self.recording_steps) - 1
            if dpg.does_item_exist(getattr(self, "step_list_group", 0)):
                self.refresh_ui()
                self._scroll_ui_to_current_step()
            self.last_interaction_time = time.time()
            logger.info(f"Recorded step: {instruction} [method={method_name or 'dpg_cb'}]")

    def build_item_path(self, item):
        path = []
        curr = item
        while curr:
            if not dpg.does_item_exist(curr):
                break
            info = dpg.get_item_info(curr)
            if not info:
                break
            t = info.get("type", "")
            cfg = dpg.get_item_configuration(curr)
            label = cfg.get("label", "")
            alias = dpg.get_item_alias(curr)

            if t == "mvAppItemType::mvWindowAppItem":
                path.insert(0, {"type": t, "label": label, "tag": curr, "alias": alias})
                break

            parent = info.get("parent")
            index = 0
            if parent:
                siblings = []
                for slot in (0, 1, 2, 3):
                    kids = dpg.get_item_children(parent, slot)
                    if kids:
                        siblings.extend(kids)

                for sib in siblings:
                    if sib == curr:
                        break
                    try:
                        if not dpg.does_item_exist(sib):
                            continue
                        s_info = dpg.get_item_info(sib)
                        s_cfg = dpg.get_item_configuration(sib)
                        if s_info.get("type") == t and s_cfg.get("label", "") == label:
                            index += 1
                    except Exception:
                        pass

            path.insert(0, {"type": t, "label": label, "index": index, "tag": str(curr), "alias": alias})
            curr = parent

        return path

    @staticmethod
    def _walk_children_path(start_item: Any, child_steps: List[Dict[str, Any]]) -> Any:
        curr = start_item
        for step in child_steps:
            alias = step.get("alias")
            if alias and dpg.does_alias_exist(alias):
                curr = dpg.get_alias_id(alias)
                continue
            t = step.get("type")
            label = step.get("label", "")
            index = step.get("index", 0)
            siblings = []
            for slot in (0, 1, 2, 3):
                kids = dpg.get_item_children(curr, slot)
                if kids:
                    siblings.extend(kids)
            found = False
            curr_idx = 0
            for child in siblings:
                try:
                    c_info = dpg.get_item_info(child)
                    if c_info.get("type") != t:
                        continue
                    c_cfg = dpg.get_item_configuration(child)
                    if c_cfg.get("label", "") == label:
                        if curr_idx == index:
                            curr = child
                            found = True
                            break
                        curr_idx += 1
                except Exception:
                    continue
            if not found:
                return None
        return curr

    def resolve_item_path(self, path_list):
        if not path_list:
            return None

        # 1. Fast O(1) leaf alias check
        leaf_alias = path_list[-1].get("alias")
        if leaf_alias and dpg.does_alias_exist(leaf_alias):
            leaf_id = dpg.get_alias_id(leaf_alias)
            if dpg.does_item_exist(leaf_id):
                return leaf_id

        root_node = path_list[0]
        root_alias = root_node.get("alias")
        root_label = root_node.get("label", "")
        target_window = None

        # 2. Fast O(1) root alias check
        if root_alias and dpg.does_alias_exist(root_alias):
            target_window = dpg.get_alias_id(root_alias)
            if not dpg.does_item_exist(target_window):
                target_window = None

        # 3. Fast O(1) root tag check
        if target_window is None:
            raw_tag = root_node.get("tag")
            if raw_tag is not None and dpg.does_item_exist(raw_tag):
                try:
                    if not root_label or dpg.get_item_configuration(raw_tag).get("label", "") == root_label:
                        target_window = raw_tag
                except Exception:
                    pass

        # 4. Fast O(N_modules) check in MODULES_REGISTRY (~10 modules vs ~10,000 DPG items)
        if target_window is None and root_label:
            try:
                from core.module_registry import MODULES_REGISTRY

                for mod in MODULES_REGISTRY.values():
                    if getattr(mod, "label", "") == root_label:
                        win_id = getattr(mod, "winID", None)
                        if win_id and dpg.does_item_exist(win_id):
                            target_window = dpg.get_alias_id(win_id) if dpg.does_alias_exist(win_id) else win_id
                            break
            except Exception:
                pass

        # 5. Fallback scan over top-level windows if not in MODULES_REGISTRY (e.g. core dialogs)
        if target_window is None and root_label:
            for i in dpg.get_all_items():
                try:
                    info = dpg.get_item_info(i)
                    if info and info.get("type") == "mvAppItemType::mvWindowAppItem":
                        if dpg.get_item_configuration(i).get("label", "") == root_label:
                            target_window = i
                            break
                except Exception:
                    continue

        if not target_window:
            return None

        if len(path_list) == 1:
            return target_window

        return self._walk_children_path(target_window, path_list[1:])

    def __del__(self):
        try:
            self.is_alive = False
            self._stop_worker()
        except Exception:
            pass


tutorial_manager = TutorialManager()
