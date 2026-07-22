"""Modular builders for the configuration GUI.

Each builder owns one visual section while ConfigGUI keeps application state
and event handlers.  Builders intentionally receive the ConfigGUI instance to
preserve the existing Tk variable names and callback behavior.
"""

from .layout import build_main_layout
from .stick import build_stick_section
from .rumble import build_rumble_section
from .mapping_buttons import build_mapping_buttons_section
from .stick_mapping import build_stick_mapping_section
from .mapping_layers import build_mapping_layers_section
from .audio_haptics import build_audio_haptics_section
from .gyro import build_gyro_section
from .footer import build_status_and_footer

__all__ = [
    "build_main_layout",
    "build_stick_section",
    "build_rumble_section",
    "build_mapping_buttons_section",
    "build_stick_mapping_section",
    "build_mapping_layers_section",
    "build_audio_haptics_section",
    "build_gyro_section",
    "build_status_and_footer",
]
