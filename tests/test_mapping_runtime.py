import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapping_runtime import MappingRuntime, MappingRuntimeManager


MASKS = {"GL": 1, "GR": 2}


def runtime(name):
    return MappingRuntime(((name,),), {}, {})


class MappingRuntimeTests(unittest.TestCase):
    def test_unchanged_button_mask_does_not_allocate_transition(self):
        base = runtime("base")
        manager = MappingRuntimeManager(base, {}, [], MASKS)

        first = manager.update(0)

        self.assertIsNotNone(first)
        self.assertTrue(first.first_report)
        self.assertIsNone(manager.update(0))

    def test_hold_transition_selects_and_releases_layer(self):
        base = runtime("base")
        layer = runtime("hold")
        layers = [{
            "id": "hold",
            "enabled": True,
            "mode": "HOLD",
            "activation_buttons": ["GL"],
        }]
        manager = MappingRuntimeManager(
            base, {"hold": layer}, layers, MASKS
        )

        selected = manager.update(MASKS["GL"])
        released = manager.update(0)

        self.assertTrue(selected.changed)
        self.assertIs(selected.runtime, layer)
        self.assertTrue(released.changed)
        self.assertIs(released.runtime, base)

    def test_reset_clears_toggle_state(self):
        base = runtime("base")
        layer = runtime("toggle")
        layers = [{
            "id": "toggle",
            "enabled": True,
            "mode": "TOGGLE",
            "activation_buttons": ["GR"],
        }]
        manager = MappingRuntimeManager(
            base, {"toggle": layer}, layers, MASKS
        )
        manager.update(MASKS["GR"])
        manager.update(0)
        self.assertIs(manager.current_runtime, layer)

        self.assertIs(manager.reset(), base)
        self.assertIs(manager.current_runtime, base)
        self.assertIsNone(manager.active_id)


if __name__ == "__main__":
    unittest.main()
