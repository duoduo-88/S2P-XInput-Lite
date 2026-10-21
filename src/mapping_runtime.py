"""Precompiled controller mapping runtimes and layer transition state.

The module contains no I/O, native output, timing, or transport code.  It only
compiles static mappings and selects the effective prebuilt runtime when the
physical button mask changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from mapping_layers import LayerSelector


MAP_NONE = 0
MAP_MOUSE = 1
MAP_KEYBOARD = 2
MAP_LX = 3
MAP_LY = 4
MAP_RX = 5
MAP_RY = 6
MAP_LT = 7
MAP_RT = 8
MAP_BUTTON = 9


@dataclass(frozen=True)
class MappingRuntime:
    compiled_mapping: tuple
    stick_direction_config: dict
    stick_direction_mapping_enabled: dict


@dataclass(frozen=True)
class MappingTransition:
    changed: bool
    first_report: bool
    layer_id: str | None
    runtime: MappingRuntime


def compile_button_mapping(
    mapping, switch_buttons, xbox_buttons, mouse_button_flags,
):
    """Compile config strings into branch-friendly hot-path operations."""
    compiled = []
    axis_targets = {
        "L_STICK_LEFT": (MAP_LX, -1.0),
        "L_STICK_RIGHT": (MAP_LX, 1.0),
        "L_STICK_DOWN": (MAP_LY, -1.0),
        "L_STICK_UP": (MAP_LY, 1.0),
        "R_STICK_LEFT": (MAP_RX, -1.0),
        "R_STICK_RIGHT": (MAP_RX, 1.0),
        "R_STICK_DOWN": (MAP_RY, -1.0),
        "R_STICK_UP": (MAP_RY, 1.0),
    }
    for name, target in mapping.items():
        kind = MAP_NONE
        value = None
        if target.startswith("MOUSE:"):
            kind = MAP_MOUSE
            candidate = target[len("MOUSE:"):]
            value = candidate if candidate in mouse_button_flags else None
        elif target.startswith("KEYBOARD:"):
            kind = MAP_KEYBOARD
            value = target[len("KEYBOARD:"):].strip()
        elif target in axis_targets:
            kind, value = axis_targets[target]
        elif target == "LT":
            kind = MAP_LT
        elif target == "RT":
            kind = MAP_RT
        elif target in xbox_buttons:
            kind = MAP_BUTTON
            value = int(xbox_buttons[target])
        compiled.append(
            (name, switch_buttons.get(name), kind, value, "BUTTON:" + name)
        )
    return tuple(compiled)


class MappingRuntimeManager:
    """Own effective layer selection without touching output side effects."""

    def __init__(self, base_runtime, layer_runtimes, layers, button_masks):
        self.base_runtime = base_runtime
        self.layer_runtimes = dict(layer_runtimes)
        self._selector = LayerSelector(layers, button_masks)
        self.active_id = None
        self._last_buttons = None

    @property
    def current_runtime(self):
        return self.layer_runtimes.get(self.active_id, self.base_runtime)

    def reset(self):
        self._selector.reset()
        self.active_id = None
        self._last_buttons = None
        return self.base_runtime

    def update(self, buttons):
        buttons = int(buttons)
        if buttons == self._last_buttons:
            return None
        first_report = self._last_buttons is None
        self._last_buttons = buttons
        layer_id = self._selector.update(buttons)
        runtime = self.layer_runtimes.get(layer_id, self.base_runtime)
        if layer_id == self.active_id:
            return MappingTransition(
                changed=False,
                first_report=first_report,
                layer_id=layer_id,
                runtime=runtime,
            )
        self.active_id = layer_id
        return MappingTransition(
            changed=True,
            first_report=first_report,
            layer_id=layer_id,
            runtime=runtime,
        )
