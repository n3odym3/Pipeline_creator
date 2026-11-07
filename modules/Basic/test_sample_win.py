from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg
import numpy as np
from loguru import logger

from config.display_scaling import display_scaling
from core.input_output_types import IOTypes
from core.window_base import WindowBase


class TestSample_win(WindowBase):
    """
    Test sample generator supporting multiple waveforms (Sine, Cosine, Square,
    Sawtooth, Triangle, Sigmoid, Gaussian Peak, Random) with tunable points,
    amplitude, frequency, offset, and Gaussian noise.
    """

    label: str = "Test Sample"
    win_width: int = display_scaling.scale(280)
    win_height: int = display_scaling.scale(290)
    description: str = (
        "Generate configurable mathematical signals and noise in SAMPLE format."
    )

    accepted_input_types: list[str] = [IOTypes.TRIGGER, IOTypes.CMD_DICT]
    outputs: dict[str, str] = {
        "Data": IOTypes.SAMPLE,
    }
    persistent_fields: list[str] = [
        "label",
        "waveform",
        "num_points",
        "amplitude",
        "frequency",
        "offset",
        "noise_level",
        "sample_name",
    ]

    def __init__(
        self,
        waveform: str = "Sine",
        num_points: int = 100,
        amplitude: float = 10.0,
        frequency: float = 1.0,
        offset: float = 0.0,
        noise_level: float = 0.0,
        sample_name: str = "Test Sample",
        **kwargs: Any,
    ) -> None:
        """
        Initialize the Test Sample window module.

        Args:
            waveform: Selected waveform generator ("Sine", "Cosine", "Square", etc.).
            num_points: Total number of data samples to generate.
            amplitude: Signal peak amplitude.
            frequency: Cycles across the full sample range.
            offset: Vertical baseline offset.
            noise_level: Standard deviation of additive Gaussian noise.
            sample_name: Descriptive name of the emitted sample packet.
            **kwargs: Additional parameters forwarded to WindowBase.
        """
        super().__init__(**kwargs)

        self.waveform: str = getattr(self, "waveform", None) or waveform
        self.num_points: int = (
            getattr(self, "num_points", None)
            if getattr(self, "num_points", None) is not None
            else num_points
        )
        self.amplitude: float = (
            getattr(self, "amplitude", None)
            if getattr(self, "amplitude", None) is not None
            else amplitude
        )
        self.frequency: float = (
            getattr(self, "frequency", None)
            if getattr(self, "frequency", None) is not None
            else frequency
        )
        self.offset: float = (
            getattr(self, "offset", None)
            if getattr(self, "offset", None) is not None
            else offset
        )
        self.noise_level: float = (
            getattr(self, "noise_level", None)
            if getattr(self, "noise_level", None) is not None
            else noise_level
        )
        self.sample_name: str = getattr(self, "sample_name", None) or sample_name

        self._sample_counter: int = 0

        # UI Tags
        uid = self.UUID
        self.waveform_combo_tag: str = f"ts_wave_{uid}"
        self.points_tag: str = f"ts_pts_{uid}"
        self.amplitude_tag: str = f"ts_amp_{uid}"
        self.frequency_tag: str = f"ts_freq_{uid}"
        self.offset_tag: str = f"ts_off_{uid}"
        self.noise_tag: str = f"ts_noise_{uid}"
        self.name_tag: str = f"ts_name_{uid}"
        self.trigger_btn_tag: str = f"ts_btn_{uid}"

        self._build_ui()
        self.update_permission()

    def _build_ui(self) -> None:
        """Construct DearPyGui widgets for waveform generation."""
        scale = display_scaling.scale

        with self.window():
            dpg.add_text("Waveform:")
            dpg.add_combo(
                items=[
                    "Sine",
                    "Cosine",
                    "Square",
                    "Sawtooth",
                    "Triangle",
                    "Sigmoid",
                    "Gaussian Peak",
                    "Random",
                ],
                default_value=self.waveform,
                tag=self.waveform_combo_tag,
                width=-1,
                callback=self._on_waveform_changed,
            )

            with dpg.group(horizontal=True):
                dpg.add_text("Points:")
                dpg.add_slider_int(
                    default_value=self.num_points,
                    min_value=10,
                    max_value=2000,
                    tag=self.points_tag,
                    width=-1,
                    callback=self._on_points_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Amplitude:")
                dpg.add_drag_float(
                    default_value=self.amplitude,
                    speed=0.5,
                    min_value=0.0,
                    max_value=10000.0,
                    tag=self.amplitude_tag,
                    width=-1,
                    callback=self._on_amplitude_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Frequency:")
                dpg.add_drag_float(
                    default_value=self.frequency,
                    speed=0.1,
                    min_value=0.1,
                    max_value=100.0,
                    tag=self.frequency_tag,
                    width=-1,
                    callback=self._on_frequency_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Offset:")
                dpg.add_drag_float(
                    default_value=self.offset,
                    speed=0.5,
                    tag=self.offset_tag,
                    width=-1,
                    callback=self._on_offset_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Noise:")
                dpg.add_slider_float(
                    default_value=self.noise_level,
                    min_value=0.0,
                    max_value=20.0,
                    tag=self.noise_tag,
                    width=-1,
                    callback=self._on_noise_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("Name:")
                dpg.add_input_text(
                    default_value=self.sample_name,
                    tag=self.name_tag,
                    width=-1,
                    callback=self._on_name_changed,
                )

            dpg.add_spacer(height=scale(4))
            dpg.add_button(
                label="Emit Sample",
                tag=self.trigger_btn_tag,
                callback=self._on_trigger_clicked,
                width=-1,
            )

        self.autosize_window()

    def update_permission(self) -> None:
        """Adjust module permissions and UI elements based on the application mode."""
        from core.app_state import app_state

        is_user = getattr(app_state, "mode", "dev") == "user"
        if dpg.does_item_exist(self.winID):
            dpg.configure_item(self.winID, no_close=is_user)

    def _on_waveform_changed(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Handle waveform combo selection."""
        if app_data:
            self.waveform = str(app_data)

    def _on_points_changed(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Handle sample count changes."""
        if app_data is not None:
            self.num_points = int(app_data)

    def _on_amplitude_changed(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Handle amplitude value changes."""
        if app_data is not None:
            self.amplitude = float(app_data)

    def _on_frequency_changed(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Handle frequency value changes."""
        if app_data is not None:
            self.frequency = float(app_data)

    def _on_offset_changed(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Handle DC baseline offset changes."""
        if app_data is not None:
            self.offset = float(app_data)

    def _on_noise_changed(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Handle noise level changes."""
        if app_data is not None:
            self.noise_level = float(app_data)

    def _on_name_changed(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Handle sample name text changes."""
        self.sample_name = str(app_data or "")

    def _on_trigger_clicked(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Handle manual trigger button click."""
        self.trigger_cb()

    def generate_sample(self) -> Dict[str, Any]:
        """
        Synthesize x and y data points based on current waveform configuration.
        """
        n = max(2, int(self.num_points))
        t = np.linspace(0.0, 1.0, n, endpoint=False)
        freq = max(0.01, float(self.frequency))
        amp = float(self.amplitude)
        offset = float(self.offset)

        if self.waveform == "Sine":
            y = amp * np.sin(2.0 * np.pi * freq * t) + offset
        elif self.waveform == "Cosine":
            y = amp * np.cos(2.0 * np.pi * freq * t) + offset
        elif self.waveform == "Square":
            y = amp * np.sign(np.sin(2.0 * np.pi * freq * t)) + offset
        elif self.waveform == "Sawtooth":
            phase = (t * freq) % 1.0
            y = amp * (2.0 * phase - 1.0) + offset
        elif self.waveform == "Triangle":
            phase = (t * freq) % 1.0
            y = amp * (2.0 * np.abs(2.0 * phase - 1.0) - 1.0) + offset
        elif self.waveform == "Sigmoid":
            k = 10.0 * freq
            y = amp / (1.0 + np.exp(-np.clip(k * (t - 0.5), -50.0, 50.0))) + offset
        elif self.waveform == "Gaussian Peak":
            sigma = 0.15 / freq
            y = amp * np.exp(-((t - 0.5) ** 2) / (2.0 * (sigma ** 2))) + offset
        elif self.waveform == "Random":
            y = amp * (np.random.rand(n) - 0.5) * 2.0 + offset
        else:
            y = amp * np.sin(2.0 * np.pi * freq * t) + offset

        if self.noise_level > 0.0:
            y = y + np.random.normal(0.0, float(self.noise_level), n)

        self._sample_counter += 1
        sample_id = f"test_sample_{self.UUID}_{self._sample_counter}"

        return {
            "name": self.sample_name or "Test Sample",
            "uuid": sample_id,
            "x": [float(i) for i in range(n)],
            "y": [float(v) for v in y],
            "action": "select",
        }

    def input_cb(self, *args: Any, **kwargs: Any) -> None:
        """
        Handle programmatic trigger or incoming parameter dictionary.
        """
        data = kwargs.get("data") if "data" in kwargs else (args[0] if args else None)

        if isinstance(data, dict):
            if "waveform" in data:
                self.waveform = str(data["waveform"])
                if dpg.does_item_exist(self.waveform_combo_tag):
                    dpg.set_value(self.waveform_combo_tag, self.waveform)
            if "num_points" in data or "points" in data:
                self.num_points = int(data.get("num_points", data.get("points", 100)))
                if dpg.does_item_exist(self.points_tag):
                    dpg.set_value(self.points_tag, self.num_points)
            if "amplitude" in data:
                self.amplitude = float(data["amplitude"])
                if dpg.does_item_exist(self.amplitude_tag):
                    dpg.set_value(self.amplitude_tag, self.amplitude)
            if "frequency" in data:
                self.frequency = float(data["frequency"])
                if dpg.does_item_exist(self.frequency_tag):
                    dpg.set_value(self.frequency_tag, self.frequency)
            if "offset" in data:
                self.offset = float(data["offset"])
                if dpg.does_item_exist(self.offset_tag):
                    dpg.set_value(self.offset_tag, self.offset)
            if "noise" in data or "noise_level" in data:
                self.noise_level = float(data.get("noise_level", data.get("noise", 0.0)))
                if dpg.does_item_exist(self.noise_tag):
                    dpg.set_value(self.noise_tag, self.noise_level)
            if "name" in data or "sample_name" in data:
                self.sample_name = str(data.get("sample_name", data.get("name", "Test Sample")))
                if dpg.does_item_exist(self.name_tag):
                    dpg.set_value(self.name_tag, self.sample_name)

        self.trigger_cb(*args, **kwargs)

    def trigger_cb(
        self,
        sender: Any = None,
        app_data: Any = None,
        user_data: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Generate and emit sample data to all connected modules.
        """
        sample = self.generate_sample()

        for output_key in self.outputs:
            for module in self.connections.get(output_key, []):
                try:
                    module.input_cb(sample=sample, data=sample, data_type=IOTypes.SAMPLE)
                except Exception as e:
                    logger.error(
                        f"[{self.label}] Error sending sample to {getattr(module, 'label', 'module')}: {e}"
                    )


EXPORTED_CLASS = TestSample_win
EXPORTED_NAME = "Test Sample"
