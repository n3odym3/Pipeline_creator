from __future__ import annotations

import json
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
    Tutorial Manager core feature for recording and playing step-by-step UI tutorials.
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
        "mvAppItemType::mvMenu"
    }

    def __init__(self):
        self.label = "Tutorial Manager"
        self.win_width = 350
        self.win_height = 480
        self.pos = (100, 100)
        self.winID = "core_tutorial_manager_win"
        
        # Mascot Image Dimensions
        self.mascot_width = 100
        self.mascot_height = 100
        
        self.is_alive = True
        self.last_interaction_time = 0.0
        self.recording_steps = []
        self.description = ""
        self.is_recording = False
        self._is_playing = False
        self.current_step_idx = 0
        self.single_step_mode = False
        self._last_highlighted_item = None
        self._last_focus_time = 0.0
        self._last_mouse_pos = (0, 0)
        self._is_mouse_down = False
        
        # UI Tags
        self.click_handler_reg = dpg.generate_uuid()
        self.overlay_drawlist = dpg.generate_uuid()
        self.overlay_info_win = dpg.generate_uuid()
        
        self._last_step_focused = -1
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

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @is_playing.setter
    def is_playing(self, value: bool) -> None:
        was_playing = self._is_playing
        self._is_playing = value
        if value and not was_playing:
            self._start_worker()
        elif not value and was_playing:
            self._stop_worker()

    def _init_ui_components(self):
        if self._ui_initialized:
            return
            
        from config.theme_manager import theme_manager
        self.highlight_theme = theme_manager.create_highlight_theme()
        # Enhance theme to highlight any widget's border
        with dpg.theme_component(dpg.mvAll, parent=self.highlight_theme):
            # Orange (255, 150, 0)
            dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 150, 0, 255), category=dpg.mvThemeCat_Core)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 2.0, category=dpg.mvThemeCat_Core)
        
        if not dpg.does_item_exist(self.click_handler_reg):
            with dpg.handler_registry(tag=self.click_handler_reg):
                dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=self._on_mouse_click)
                dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Middle, callback=self._on_mouse_click)
                dpg.add_mouse_down_handler(callback=lambda: setattr(self, "_is_mouse_down", True))
                dpg.add_mouse_release_handler(callback=lambda: setattr(self, "_is_mouse_down", False))
            
        if not dpg.does_item_exist(self.overlay_drawlist):
            with dpg.viewport_drawlist(front=True, show=False, tag=self.overlay_drawlist):
                self.overlay_top = dpg.draw_rectangle((0,0), (0,0), fill=(0,0,0,180), color=(0,0,0,0))
                self.overlay_bottom = dpg.draw_rectangle((0,0), (0,0), fill=(0,0,0,180), color=(0,0,0,0))
                self.overlay_left = dpg.draw_rectangle((0,0), (0,0), fill=(0,0,0,180), color=(0,0,0,0))
                self.overlay_right = dpg.draw_rectangle((0,0), (0,0), fill=(0,0,0,180), color=(0,0,0,0))
                self.overlay_mid = dpg.draw_rectangle((0,0), (0,0), fill=(0,0,0,180), color=(0,0,0,0))
                self.overlay_extra1 = dpg.draw_rectangle((0,0), (0,0), fill=(0,0,0,180), color=(0,0,0,0))
                self.overlay_extra2 = dpg.draw_rectangle((0,0), (0,0), fill=(0,0,0,180), color=(0,0,0,0))
                self.overlay_border = dpg.draw_rectangle((0,0), (0,0), fill=(0,0,0,0), color=(255, 150, 0, 255), thickness=4, show=False)
            
        self._setup_mascot()
            
        if not dpg.does_item_exist(self.overlay_info_win):
            with dpg.window(tag=self.overlay_info_win, no_title_bar=True, no_resize=True,
                            no_move=True, show=False, autosize=True, no_scrollbar=True):

                with dpg.group(horizontal=True):
                    default_tex = getattr(self, "_current_mascot_texture_tag", None)
                    if default_tex and dpg.does_item_exist(default_tex):
                        dpg.add_image(default_tex, tag="tutorial_mascot_image_widget", width=self.mascot_width, height=self.mascot_height)

                    with dpg.group():
                        self.overlay_msg = dpg.add_text("Instruction", wrap=400)
                        dpg.add_spacer(height=5)
                        with dpg.group(horizontal=True) as self.nav_buttons_group:
                            self.overlay_prev_btn = dpg.add_button(label="\uf060", callback=self.prev_step, width=40)
                            self.overlay_next_btn = dpg.add_button(label="\uf061", callback=self.manual_next_step, width=40)
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
        # Darken the background by 15% for better visibility
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
            
        # 1. Ensure texture registry exists
        if not dpg.does_item_exist("tutorial_texture_registry"):
            dpg.add_texture_registry(tag="tutorial_texture_registry")
            
        # 2. Find and load ALL mascot images
        assets_dir = PROJECT_ROOT / "ressources" / "tutorial_assets"
        fallback_img = PROJECT_ROOT / "ressources" / "Polypy.png"
        
        # We will collect all image paths to preload
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
                            
        # Preload each image as a static texture
        self._loaded_mascot_textures = {}  # maps subfolder_name -> list of texture_tags, and "fallback" -> tag

        try:
            resample_filter = Image.Resampling.LANCZOS
        except AttributeError:
            resample_filter = Image.LANCZOS
            
        import numpy as np
        for img_path in images_to_load:
            tag = f"tutorial_mascot_tex_{img_path.name}"
            if not dpg.does_item_exist(tag):
                try:
                    # Load with PIL, convert to RGBA, and resize using LANCZOS filter for premium anti-aliasing
                    with Image.open(img_path) as img:
                        img = img.convert("RGBA")
                        img_resized = img.resize((self.mascot_width, self.mascot_height), resample_filter)
                        raw_bytes = img_resized.tobytes()
                        # Convert to normalized float list for Dear PyGui using numpy (extremely fast)
                        data = (np.frombuffer(raw_bytes, dtype=np.uint8) / 255.0).tolist()
                        
                    dpg.add_static_texture(width=self.mascot_width, height=self.mascot_height, default_value=data, 
                                            tag=tag, parent="tutorial_texture_registry")
                except Exception as e:
                    logger.error(f"Failed to preload mascot texture {img_path}: {e}")
                    continue
                    
            # Group by folder name (e.g. "error", "warning", "tutorial", "fallback")
            if img_path == fallback_img:
                folder_key = "fallback"
            else:
                folder_key = img_path.parent.name.lower()
                
            if folder_key not in self._loaded_mascot_textures:
                self._loaded_mascot_textures[folder_key] = []
            self._loaded_mascot_textures[folder_key].append(tag)
                    
        # Load initial texture
        self._load_mascot_texture("tutorial")

    def _load_mascot_texture(self, icon_type=None):
        import random
        
        if not hasattr(self, "_loaded_mascot_textures") or not self._loaded_mascot_textures:
            # Fallback if preloading didn't run or failed
            return
            
        selected_tag = None
        
        # 1. Try specified folder
        if icon_type:
            folder_key = icon_type.lower()
            if folder_key in self._loaded_mascot_textures and self._loaded_mascot_textures[folder_key]:
                selected_tag = random.choice(self._loaded_mascot_textures[folder_key])
                
        # 2. Fallback to all textures in all folders
        if not selected_tag:
            all_tags = []
            for key, tags in self._loaded_mascot_textures.items():
                if key != "fallback":
                    all_tags.extend(tags)
            if all_tags:
                selected_tag = random.choice(all_tags)
                
        # 3. Fallback to fallback texture
        if not selected_tag:
            if "fallback" in self._loaded_mascot_textures and self._loaded_mascot_textures["fallback"]:
                selected_tag = self._loaded_mascot_textures["fallback"][0]
                
        if selected_tag and dpg.does_item_exist(selected_tag):
            self._current_mascot_texture_tag = selected_tag
            # Update the overlay image widget if it exists
            if dpg.does_item_exist("tutorial_mascot_image_widget"):
                try:
                    dpg.configure_item("tutorial_mascot_image_widget", texture_tag=selected_tag)
                except Exception as e:
                    logger.error(f"Failed to configure image widget with texture {selected_tag}: {e}")

    def show(self):
        self._init_ui_components()
        
        if dpg.does_item_exist(self.winID):
            dpg.show_item(self.winID)
            dpg.focus_item(self.winID)
            return

        with dpg.window(label=self.label, pos=self.pos, width=self.win_width, height=self.win_height, tag=self.winID, on_close=self._on_close_window):
            
            dpg.add_text("Tutorial Description:")
            self.desc_input = dpg.add_input_text(multiline=True, width=-1, height=80, default_value=self.description, callback=self._on_desc_change)
            
            with dpg.group(horizontal=True):
                self.btn_record = dpg.add_button(label="Record", callback=self.toggle_record)
                dpg.add_button(label="Save", callback=self.save_tutorial)
                dpg.add_button(label="Load", callback=lambda *_: self.load_tutorial())
                dpg.add_button(label="Clear", callback=self.clear_tutorial)
                self.btn_play = dpg.add_button(label="Play", callback=self.toggle_play)
                
            dpg.add_separator()
            
            with dpg.group(horizontal=True):
                self.main_prev_btn = dpg.add_button(label="\uf060", callback=self.prev_step)
                self.play_status_text = dpg.add_text("Ready")
                self.main_next_btn = dpg.add_button(label="\uf061", callback=self.manual_next_step)
                
            dpg.add_separator()
            
            with dpg.child_window(height=-1):
                self.step_list_group = dpg.add_group()
                
        self.refresh_ui()

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
            dpg.configure_item(self.btn_record, label="Stop Recording")
            logger.info("Tutorial recording started.")
        else:
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
            self._update_status_text()
            logger.info("Tutorial playback started.")
            self._init_ui_components()
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
            
            # Auto-restore if we had an ephemeral error hint
            if hasattr(self, "_backup_steps"):
                self.recording_steps = list(self._backup_steps)
                delattr(self, "_backup_steps")
                if getattr(self, "_ui_initialized", False):
                    self.refresh_ui()
            
        # Immediate refresh to reflect state change
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
            
        self.recording_steps = [{
            "path": path,
            "instruction": f"{instruction}",
            "dim": True,
            "type": "error_hint",
            "icon_type": icon_type
        }]
        
        self.play_single_step(0)

    def play_center_hint(self, instruction, type="error_hint", icon_type="error"):
        """Play a single ephemeral step to show a message at the center of the viewport."""
        if self.is_recording:
            return
        if self.is_playing and not hasattr(self, "_backup_steps"):
            return
                
        if not hasattr(self, "_backup_steps"):
            self._backup_steps = list(self.recording_steps)
            
        self.recording_steps = [{
            "path": [], # Empty path forces center in _update_overlay
            "instruction": f"{instruction}",
            "dim": True,
            "type": type,
            "icon_type": icon_type
        }]
        
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
            
        self.recording_steps = [{
            "path": path,
            "instruction": f"{instruction}",
            "dim": True,
            "type": "success_hint",
            "icon_type": icon_type
        }]
        
        self.play_single_step(0)

    def _hide_overlay_completely(self):
        self._clear_window_highlight()
        if dpg.does_item_exist(getattr(self, "overlay_drawlist", 0)):
            dpg.configure_item(self.overlay_drawlist, show=False)
        if dpg.does_item_exist(getattr(self, "overlay_border", 0)): dpg.configure_item(self.overlay_border, show=False)
        if dpg.does_item_exist(getattr(self, "overlay_info_win", 0)): dpg.hide_item(self.overlay_info_win)
        for itm in [getattr(self, "overlay_top", 0), getattr(self, "overlay_bottom", 0), 
                    getattr(self, "overlay_left", 0), getattr(self, "overlay_right", 0),
                    getattr(self, "overlay_mid", 0), getattr(self, "overlay_extra1", 0),
                    getattr(self, "overlay_extra2", 0)]:
            if dpg.does_item_exist(itm): dpg.configure_item(itm, show=False)

    def _clear_window_highlight(self):
        if self._last_highlighted_item and dpg.does_item_exist(self._last_highlighted_item):
            try:
                dpg.bind_item_theme(self._last_highlighted_item, 0)
            except Exception:
                pass
        self._last_highlighted_item = None

    def save_tutorial(self, *args, **kwargs):
        filepath = file_explorer.save_file(
            default_path=str(self.tutorials_dir),
            default_name="tutorial.json",
            extensions=[("JSON files", "*.json")]
        )
        if not filepath:
            return
        try:
            if not filepath.endswith('.json'):
                filepath += '.json'
            data = {
                "description": self.description,
                "steps": self.recording_steps
            }
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            logger.info(f"Saved tutorial to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save tutorial: {e}")

    def load_tutorial(self, filepath=None, *args, **kwargs):
        if not filepath:
            filepath = file_explorer.select_file(
                default_path=str(self.tutorials_dir),
                extensions=[("JSON files", "*.json")]
            )
        if not filepath:
            return
            
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
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
            self.refresh_ui()
            logger.info(f"Loaded tutorial from {filepath}")
        except Exception as e:
            logger.error(f"Failed to load tutorial: {e}")

    def clear_tutorial(self, *args, **kwargs):
        self.recording_steps = []
        self.description = ""
        if dpg.does_item_exist(getattr(self, "desc_input", 0)):
            dpg.set_value(self.desc_input, "")
        self.current_step_idx = 0
        self.refresh_ui()
        logger.info("Tutorial cleared.")

    def play_single_step(self, idx):
        if self.is_recording: return
        self.is_playing = True
        self.single_step_mode = True
        self.current_step_idx = idx
        btn = getattr(self, "btn_play", 0)
        if dpg.does_item_exist(btn):
            dpg.configure_item(btn, label="Stop Playback")
        self._update_status_text()
        self._init_ui_components()
        logger.info(f"Single step playback started for step {idx+1}")

    def load_and_play(self, filepath):
        self.load_tutorial(filepath)
        threading.Timer(0.1, lambda: self.toggle_play() if not self.is_playing else None).start()

    def _update_status_text(self):
        if self.is_playing:
            m = f"Step {self.current_step_idx + 1}/{max(1, len(self.recording_steps))}"
            if dpg.does_item_exist(getattr(self, "play_status_text", 0)):
                dpg.configure_item(self.play_status_text, default_value=m)
            self._scroll_ui_to_current_step()
            self._on_step_changed()

    def _on_step_changed(self):
        if not self.is_playing or self.current_step_idx >= len(self.recording_steps):
            return
        step = self.recording_steps[self.current_step_idx]
        icon_type = step.get("icon_type")
        if not icon_type:
            step_type = step.get("type", "")
            if "error" in step_type:
                icon_type = "error"
            elif "warning" in step_type:
                icon_type = "warning"
            else:
                icon_type = "tutorial"
        self._load_mascot_texture(icon_type)

    def prev_step(self, *args, **kwargs):
        if self.is_playing and self.current_step_idx > 0:
            self.current_step_idx -= 1
            self._update_status_text()
            self._update_overlay() # Immediate update on interaction

    def manual_next_step(self, *args, **kwargs):
        if self.is_playing:
            self.next_step()

    def next_step(self, *args, **kwargs):
        if self.is_playing:
            self.last_interaction_time = time.time()
            if self.single_step_mode:
                self.toggle_play()
                return
                
            if self.current_step_idx < len(self.recording_steps) - 1:
                self.current_step_idx += 1
                self._update_status_text()
                self._update_overlay() # Immediate update on interaction
            else:
                self.toggle_play()

    def _scroll_ui_to_current_step(self):
        if not dpg.does_item_exist(getattr(self, "step_list_group", 0)): return
        children = dpg.get_item_children(self.step_list_group, 1) or []
        for i, child in enumerate(children):
            items = dpg.get_item_children(child, 1) or []
            if len(items) >= 2:
                lbl = items[1]
                if i == self.current_step_idx:
                    dpg.configure_item(lbl, color=(255, 150, 0))
                else:
                    dpg.configure_item(lbl, color=(150, 150, 150))

    def refresh_ui(self):
        if not dpg.does_item_exist(getattr(self, "step_list_group", 0)): return
        dpg.delete_item(self.step_list_group, children_only=True)
        
        for i, step in enumerate(self.recording_steps):
            with dpg.group(parent=self.step_list_group, horizontal=True):
                dpg.add_text(f"{i+1}.")
                
                name = "Unknown"
                path = step.get("path", [])
                if path:
                    last_item = path[-1]
                    name = last_item.get("label") or str(last_item.get("tag")) or last_item.get("type", "").split("::")[-1]
                    
                label_col = (150, 150, 150)
                dpg.add_text(f"[{name}]", color=label_col)
                
                def _delete(s, a, u, *args, **kwargs):
                    if isinstance(u, int) and 0 <= u < len(self.recording_steps):
                        self.recording_steps.pop(u)
                        if self.current_step_idx >= len(self.recording_steps):
                            self.current_step_idx = max(0, len(self.recording_steps)-1)
                        self.refresh_ui()
                    
                def _run_single(s, a, u, *args, **kwargs):
                    if isinstance(u, int) and 0 <= u < len(self.recording_steps):
                        self.play_single_step(u)

                def _update_dim(s, a, u, *args, **kwargs):
                    if isinstance(u, int) and 0 <= u < len(self.recording_steps):
                        self.recording_steps[u]["dim"] = a

                def _update_instr(s, a, u, *args, **kwargs):
                    if isinstance(u, int) and 0 <= u < len(self.recording_steps):
                        self.recording_steps[u]["instruction"] = a
                
                dpg.add_button(label="X", callback=_delete, user_data=i)
                dpg.add_button(label="Run", callback=_run_single, user_data=i)
                dpg.add_checkbox(label="Dim", default_value=step.get("dim", True), callback=_update_dim, user_data=i)
                dpg.add_input_text(default_value=step.get("instruction", ""), width=-1, callback=_update_instr, user_data=i)

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
                step_changed = (self.current_step_idx != _last_step or self.is_playing != _last_playing)
                if step_changed or self.is_playing:
                    self._update_overlay()
                    _last_step = self.current_step_idx
                    _last_playing = self.is_playing
            except Exception:
                pass
            if self._worker_event:
                self._worker_event.wait(timeout=0.033)

    def _update_overlay(self):
        if not self.is_playing or self.current_step_idx >= len(self.recording_steps):
            self._hide_overlay_completely()
            return
            
        if not self._ui_initialized:
            return

        vw = dpg.get_viewport_client_width()
        vh = dpg.get_viewport_client_height()
        
        step = self.recording_steps[self.current_step_idx]
        should_dim = step.get("dim", True)
        
        now = time.time()
        should_resolve = (
            self.current_step_idx != getattr(self, "_last_resolved_step", -1) or
            now - getattr(self, "_last_resolve_time", 0) > 1.0 or
            not dpg.does_item_exist(getattr(self, "_cached_target", 0))
        )
        
        if should_resolve:
            target = self.resolve_item_path(step.get("path", []))
            self._cached_target = target
            self._last_resolved_step = self.current_step_idx
            self._last_resolve_time = now
        else:
            target = self._cached_target
        
        if dpg.does_item_exist(self.overlay_drawlist):
            dpg.configure_item(self.overlay_drawlist, show=True)
        
        target_found = False
        t_min, t_max = [0, 0], [0, 0]
        is_window = False

        if target and dpg.does_item_exist(target):
            try:
                info = dpg.get_item_info(target)
                is_window = (info.get("type", "") == "mvAppItemType::mvWindowAppItem")
                if is_window:
                    w_pos = dpg.get_item_pos(target)
                    w_size = dpg.get_item_rect_size(target)
                    if w_size[0] <= 0: w_size[0] = dpg.get_item_width(target) or 300
                    if w_size[1] <= 0: w_size[1] = dpg.get_item_height(target) or 200
                    t_min = [w_pos[0], w_pos[1]]
                    t_max = [w_pos[0] + w_size[0], w_pos[1] + w_size[1]]
                else:
                    t_min = list(dpg.get_item_rect_min(target))
                    t_max = list(dpg.get_item_rect_max(target))
                
                if t_max[0] <= t_min[0]: t_max[0] = t_min[0] + 1
                if t_max[1] <= t_min[1]: t_max[1] = t_min[1] + 1
                target_found = True
            except:
                target_found = False

        if target_found:
            if target != self._last_highlighted_item:
                self._clear_window_highlight()
                try:
                    dpg.bind_item_theme(target, self.highlight_theme)
                    self._last_highlighted_item = target
                except: pass
            dpg.configure_item(self.overlay_border, pmin=t_min, pmax=t_max, show=True, 
                               thickness=2 if is_window else 4)
        else:
            self._clear_window_highlight()
            dpg.configure_item(self.overlay_border, show=False)

        # Check for auto-advance on hover for "Observe" steps (but NOT for entire windows)
        if target_found and (step.get("type") == "observe" or step.get("instruction", "").startswith("Observe")):
            try:
                if not is_window and dpg.is_item_hovered(target) and (time.time() - self.last_interaction_time > 0.5):
                    dpg.split_frame()
                    self.next_step()
                    return
            except: pass

        
        # Position HUD Window — resolve real dimensions for clamping
        hud_w_est, hud_h_est = 260, 140
        try:
            _s = dpg.get_item_rect_size(self.overlay_info_win)
            if _s[0] > 10: hud_w_est, hud_h_est = int(_s[0]), int(_s[1])
        except: pass

        if target_found:
            # Preferred: below the target
            tx = t_min[0]
            ty = t_max[1] + 10
            # Fallback: above the target if below would overflow
            if ty + hud_h_est > vh:
                ty = t_min[1] - hud_h_est - 10
            # Last resort: center vertically if above also overflows
            if ty < 0:
                ty = vh // 2 - hud_h_est // 2
        else:
            tx = vw // 2 - hud_w_est // 2
            ty = vh // 2 - hud_h_est // 2

        # Hard clamp to keep HUD fully inside viewport
        tx = max(0, min(tx, vw - hud_w_est))
        ty = max(0, min(ty, vh - hud_h_est))
        
        now = time.time()
        force_focus = (self.current_step_idx != getattr(self, "_last_step_focused", -1))
        periodic_reclaim = (now - getattr(self, "_last_focus_time", 0) > 1.0)
        
        if dpg.does_item_exist(self.overlay_info_win):
            # Always update position and text if playing
            dpg.set_item_pos(self.overlay_info_win, (tx, ty))
            instr = step.get("instruction", "...")
            # if not target_found and step.get("path"):
            #     missing_name = step.get("path")[-1].get("label", "Element")
            #     instr += f"\n(Wait: '{missing_name}' not found)"
            dpg.set_value(self.overlay_msg, instr)
            
            # Toggle button groups based on step type
            is_hint = (step.get("type") in ["error_hint", "success_hint"])
            if getattr(self, "nav_buttons_group", 0) and getattr(self, "error_button_group", 0):
                dpg.configure_item(self.nav_buttons_group, show=not is_hint)
                dpg.configure_item(self.error_button_group, show=is_hint)
            
            # Update navigation button visibility based on step index
            if getattr(self, "overlay_prev_btn", 0):
                dpg.configure_item(self.overlay_prev_btn, show=(self.current_step_idx > 0))
            if getattr(self, "overlay_next_btn", 0):
                dpg.configure_item(self.overlay_next_btn, show=(self.current_step_idx < len(self.recording_steps) - 1))
            
            # Update main window navigation button visibility
            if getattr(self, "main_prev_btn", 0):
                dpg.configure_item(self.main_prev_btn, show=(self.current_step_idx > 0))
            if getattr(self, "main_next_btn", 0):
                dpg.configure_item(self.main_next_btn, show=(self.current_step_idx < len(self.recording_steps) - 1))
            
            if force_focus:
                dpg.show_item(self.overlay_info_win)
                dpg.focus_item(self.overlay_info_win)
                self._last_step_focused = self.current_step_idx
                self._last_focus_time = now
            elif periodic_reclaim and not getattr(self, "_is_mouse_down", False):
                dpg.show_item(self.overlay_info_win)
                dpg.focus_item(self.overlay_info_win)
                self._last_focus_time = now

        h1 = (t_min, t_max)
        h2 = ((tx, ty), (tx + hud_w_est, ty + hud_h_est))
        
        # Sort holes by Y
        if not target_found:
            # Only one hole (the HUD)
            if should_dim:
                dpg.configure_item(self.overlay_top, pmin=(0,0), pmax=(vw, h2[0][1]), show=True)
                dpg.configure_item(self.overlay_bottom, pmin=(0, h2[1][1]), pmax=(vw, vh), show=True)
                dpg.configure_item(self.overlay_left, pmin=(0, h2[0][1]), pmax=(h2[0][0], h2[1][1]), show=True)
                dpg.configure_item(self.overlay_right, pmin=(h2[1][0], h2[0][1]), pmax=(vw, h2[1][1]), show=True)
                for itm in [self.overlay_mid, self.overlay_extra1, self.overlay_extra2]:
                    if dpg.does_item_exist(itm): dpg.configure_item(itm, show=False)
            else:
                for itm in [self.overlay_top, self.overlay_bottom, self.overlay_left, self.overlay_right, 
                            self.overlay_mid, self.overlay_extra1, self.overlay_extra2]:
                    if dpg.does_item_exist(itm): dpg.configure_item(itm, show=False)
            return

        if h1[0][1] < h2[0][1]: h_high, h_low = h1, h2
        else: h_high, h_low = h2, h1
            
        # If they overlap vertically, fallback to a single hole to avoid weird gaps
        if h_high[1][1] > h_low[0][1] - 5:
            # Combined hole (User's previous complaint area, but necessary if overlapping)
            h_min = (min(h1[0][0], h2[0][0]), min(h1[0][1], h2[0][1]))
            h_max = (max(h1[1][0], h2[1][0]), max(h1[1][1], h2[1][1]))
            if should_dim:
                dpg.configure_item(self.overlay_top, pmin=(0,0), pmax=(vw, h_min[1]), show=True)
                dpg.configure_item(self.overlay_bottom, pmin=(0, h_max[1]), pmax=(vw, vh), show=True)
                dpg.configure_item(self.overlay_left, pmin=(0, h_min[1]), pmax=(h_min[0], h_max[1]), show=True)
                dpg.configure_item(self.overlay_right, pmin=(h_max[0], h_min[1]), pmax=(vw, h_max[1]), show=True)
                for itm in [self.overlay_mid, self.overlay_extra1, self.overlay_extra2]:
                    dpg.configure_item(itm, show=False)
        else:
            # 7-Rectangle Logic for 2 non-overlapping holes
            if should_dim:
                # 1. Top bar
                dpg.configure_item(self.overlay_top, pmin=(0,0), pmax=(vw, h_high[0][1]), show=True)
                # 2. Bottom bar
                dpg.configure_item(self.overlay_bottom, pmin=(0, h_low[1][1]), pmax=(vw, vh), show=True)
                # 3. Mid bar (between holes)
                dpg.configure_item(self.overlay_mid, pmin=(0, h_high[1][1]), pmax=(vw, h_low[0][1]), show=True)
                # 4/5. Sides of high hole
                dpg.configure_item(self.overlay_left, pmin=(0, h_high[0][1]), pmax=(h_high[0][0], h_high[1][1]), show=True)
                dpg.configure_item(self.overlay_right, pmin=(h_high[1][0], h_high[0][1]), pmax=(vw, h_high[1][1]), show=True)
                # 6/7. Sides of low hole
                dpg.configure_item(self.overlay_extra1, pmin=(0, h_low[0][1]), pmax=(h_low[0][0], h_low[1][1]), show=True)
                dpg.configure_item(self.overlay_extra2, pmin=(h_low[1][0], h_low[0][1]), pmax=(vw, h_low[1][1]), show=True)
            else:
                for itm in [self.overlay_top, self.overlay_bottom, self.overlay_left, self.overlay_right, 
                            self.overlay_mid, self.overlay_extra1, self.overlay_extra2]:
                    dpg.configure_item(itm, show=False)

    def _on_mouse_click(self, sender=None, app_data=None, user_data=None, *args, **kwargs):
        if not self.is_alive: return
        if time.time() - self.last_interaction_time < 0.2:
            return

        ms = dpg.get_mouse_pos(local=False)
        button = app_data
            
        if self.is_playing:
            self._check_playback_click(ms)
        elif self.is_recording:
            if button == dpg.mvMouseButton_Left:
                self._check_record_click(ms)
            elif button == dpg.mvMouseButton_Middle:
                self._check_observe_record_click(ms)

    def _check_observe_record_click(self, mouse_pos):
        hovered_item = None
        
        # 1. Find the deepest hovered item (could be anything: button, window, text, etc.)
        all_itms = dpg.get_all_items()
        for item in all_itms:
            if not dpg.does_item_exist(item): continue
            try:
                if dpg.is_item_hovered(item):
                    hovered_item = item
            except: pass
            
        if not hovered_item:
            return
            
        # Exclusion: Don't record clicks on the Tutorial Manager's own window/widgets
        path = self.build_item_path(hovered_item)
        for p_step in path:
            if p_step.get("label") == self.label:
                return

        cfg = dpg.get_item_configuration(hovered_item)
        name = cfg.get("label") or str(hovered_item)
        instruction = f"Observe '{name}'"
        
        self.recording_steps.append({
            "path": path,
            "instruction": instruction,
            "dim": True,
            "type": "observe"
        })
        if dpg.does_item_exist(getattr(self, "step_list_group", 0)):
            self.refresh_ui()
            self._scroll_ui_to_current_step()
        self.last_interaction_time = time.time()
        logger.info(f"Recorded observation step: {instruction}")

    def _check_playback_click(self, mouse_pos):
        if len(self.recording_steps) == 0 or self.current_step_idx >= len(self.recording_steps):
            return
            
        step = self.recording_steps[self.current_step_idx]
        target = self.resolve_item_path(step.get("path", []))
        if target and dpg.does_item_exist(target):
            try:
                info = dpg.get_item_info(target)
                is_window = (info.get("type", "") == "mvAppItemType::mvWindowAppItem")
                
                if is_window:
                    w_pos = dpg.get_item_pos(target)
                    w_size = dpg.get_item_rect_size(target)
                    if w_size[0] <= 0: w_size[0] = dpg.get_item_width(target) or 300
                    if w_size[1] <= 0: w_size[1] = dpg.get_item_height(target) or 200
                    min_p = [w_pos[0], w_pos[1]]
                    max_p = [w_pos[0] + w_size[0], w_pos[1] + w_size[1]]
                else:
                    min_p = dpg.get_item_rect_min(target)
                    max_p = dpg.get_item_rect_max(target)
                    
                if min_p[0] <= mouse_pos[0] <= max_p[0] and min_p[1] <= mouse_pos[1] <= max_p[1]:
                    self.next_step()
            except:
                pass

    def _check_record_click(self, mouse_pos):
        hovered_item = None
        
        # We look for the "deepest" hovered interactive item
        # get_all_items returns them in a hierarchy, children are usually later in the list
        all_items = dpg.get_all_items()
        for item in all_items:
            if not dpg.does_item_exist(item):
                continue
            try:
                info = dpg.get_item_info(item)
                if info.get("type", "") in self.INTERACTIVE_TYPES:
                    if dpg.is_item_hovered(item):
                        hovered_item = item
                        # Don't break! We want to find if there's a deeper child hovered
            except:
                pass
                
        if hovered_item:
            path = self.build_item_path(hovered_item)
            
            # Exclusion: Don't record clicks on the Tutorial Manager's own window/widgets
            for p_step in path:
                if p_step.get("label") == self.label:
                    return

            last_item = path[-1]
            # Prioritize: Label > Tag (ID) > Type
            name = last_item.get("label") or str(hovered_item) or last_item.get("type", "").split("::")[-1]
            instruction = f"Interact with '{name}'"
            
            self.recording_steps.append({
                "path": path,
                "instruction": instruction,
                "dim": True,
                "type": "interact"
            })
            if dpg.does_item_exist(getattr(self, "step_list_group", 0)):
                self.refresh_ui()
                self._scroll_ui_to_current_step()
            self.last_interaction_time = time.time()
            logger.info(f"Recorded step: {instruction}")

    def build_item_path(self, item):
        path = []
        curr = item
        while curr:
            if not dpg.does_item_exist(curr):
                break
            info = dpg.get_item_info(curr)
            if not info: break
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
                for slot in [0, 1, 2, 3]:
                    kids = dpg.get_item_children(parent, slot)
                    if kids: siblings.extend(kids)
                        
                for sib in siblings:
                    if sib == curr: break
                    try:
                        if not dpg.does_item_exist(sib): continue
                        s_info = dpg.get_item_info(sib)
                        s_cfg = dpg.get_item_configuration(sib)
                        if s_info.get("type") == t and s_cfg.get("label", "") == label:
                            index += 1
                    except:
                        pass
                        
            path.insert(0, {"type": t, "label": label, "index": index, "tag": str(curr), "alias": alias})
            curr = parent
            
        return path

    def resolve_item_path(self, path_list):
        if not path_list: return None
        
        root_node = path_list[0]
        root_alias = root_node.get("alias")
        if root_alias and dpg.does_alias_exist(root_alias):
            if len(path_list) == 1:
                return dpg.get_alias_id(root_alias)
            target_window = dpg.get_alias_id(root_alias)
            curr = target_window
            for step in path_list[1:]:
                alias = step.get("alias")
                if alias and dpg.does_alias_exist(alias):
                    curr = dpg.get_alias_id(alias)
                    continue
                t = step.get("type")
                label = step.get("label", "")
                index = step.get("index", 0)
                siblings = []
                for slot in [0, 1, 2, 3]:
                    kids = dpg.get_item_children(curr, slot)
                    if kids: siblings.extend(kids)
                found = False
                curr_idx = 0
                for child in siblings:
                    try:
                        c_info = dpg.get_item_info(child)
                        c_cfg = dpg.get_item_configuration(child)
                        if c_info.get("type") == t and c_cfg.get("label", "") == label:
                            if curr_idx == index:
                                curr = child
                                found = True
                                break
                            curr_idx += 1
                    except: continue
                if not found:
                    return None
            return curr

        windows = []
        all_itms = dpg.get_all_items()
        for i in all_itms:
            if dpg.does_item_exist(i):
                try:
                    info = dpg.get_item_info(i)
                    if info and info.get("type") == "mvAppItemType::mvWindowAppItem":
                        windows.append(i)
                except: continue
                
        target_window = None
        for w in windows:
            if not dpg.does_item_exist(w): continue
            try:
                cfg = dpg.get_item_configuration(w)
                if cfg.get("label", "") == root_node.get("label", ""):
                    target_window = w
                    break
            except: continue
                
        if not target_window: return None
        
        curr = target_window
        for step in path_list[1:]:
            # Priority 1: Alias (Tag)
            alias = step.get("alias")
            if alias and dpg.does_alias_exist(alias):
                curr = alias
                continue
                
            # Priority 2: Traditional path resolution
            t = step.get("type")
            label = step.get("label", "")
            index = step.get("index", 0)
            
            siblings = []
            for slot in [0, 1, 2, 3]:
                kids = dpg.get_item_children(curr, slot)
                if kids: siblings.extend(kids)
                
            found = False
            curr_idx = 0
            for child in siblings:
                try:
                    c_info = dpg.get_item_info(child)
                    c_cfg = dpg.get_item_configuration(child)
                    if c_info.get("type") == t and c_cfg.get("label", "") == label:
                        if curr_idx == index:
                            curr = child
                            found = True
                            break
                        curr_idx += 1
                except: continue
                    
            if not found:
                return None
                
        return curr

    def __del__(self):
        try:
            self.is_alive = False
            self._stop_worker()
        except Exception:
            pass

tutorial_manager = TutorialManager()
