import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import parameter_controls
from gamepad_test_window import GamepadTestWindow
from parameter_controls import bind_drag_adjust, bind_restore_menu, show_restore_menu


class FakeVariable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWidget:
    def __init__(self, disabled=False):
        self.callbacks = {}
        self.options = {}
        self.disabled = disabled

    def configure(self, **options):
        self.options.update(options)

    def bind(self, sequence, callback, add=None):
        self.callbacks.setdefault(sequence, []).append(callback)

    def instate(self, states):
        return self.disabled and "disabled" in states

    def fire(self, sequence, event):
        result = None
        for callback in self.callbacks[sequence]:
            result = callback(event)
        return result


START = SimpleNamespace(x_root=10, y_root=10)


def moved(dx=0, dy=0):
    return SimpleNamespace(x_root=10 + dx, y_root=10 + dy)


class DragAdjustTests(unittest.TestCase):
    def test_dragging_right_or_up_adds_one_step_per_eight_pixels(self):
        widget = FakeWidget()
        variable = FakeVariable(10)
        applied = []
        bind_drag_adjust(widget, variable, 0, 100, applied.append, step=1)

        widget.fire("<ButtonPress-1>", START)
        widget.fire("<B1-Motion>", moved(dx=24))
        widget.fire("<B1-Motion>", moved(dy=-40))

        self.assertEqual(applied, [13, 15])
        self.assertEqual(widget.options["cursor"], "sb_h_double_arrow")

    def test_click_without_drag_runs_click_command_only(self):
        widget = FakeWidget()
        clicked = Mock()
        applied = Mock()
        bind_drag_adjust(widget, FakeVariable(1), 0, 5, applied, step=1, click_command=clicked)

        widget.fire("<ButtonPress-1>", START)
        widget.fire("<ButtonRelease-1>", START)

        clicked.assert_called_once_with()
        applied.assert_not_called()

    def test_disabled_control_blocks_dragging(self):
        widget = FakeWidget()
        control = FakeWidget(disabled=True)
        applied = Mock()
        bind_drag_adjust(widget, FakeVariable(1), 0, 5, applied, step=1, state_widget=control)

        widget.fire("<ButtonPress-1>", START)
        widget.fire("<B1-Motion>", moved(dx=40))

        applied.assert_not_called()

    def test_allowed_values_step_through_the_list(self):
        widget = FakeWidget()
        applied = []
        bind_drag_adjust(
            widget, FakeVariable("10"), 5, 60, applied.append, allowed_values=(5, 10, 30, 60),
        )

        widget.fire("<ButtonPress-1>", START)
        widget.fire("<B1-Motion>", moved(dx=16))

        self.assertEqual(applied, [60])


class RestoreMenuTests(unittest.TestCase):
    def _popup(self, saved, default):
        event = SimpleNamespace(widget=FakeWidget(), x_root=5, y_root=6)
        event.widget.winfo_toplevel = Mock()
        menu = Mock()
        apply = Mock()
        with patch.object(parameter_controls.tk, "Menu", return_value=menu):
            result = show_restore_menu(event, lambda text: text, apply, saved, default)
        self.assertEqual(result, "break")
        return menu, apply

    def test_menu_restores_saved_and_default_values(self):
        menu, apply = self._popup(4, 9)
        saved_item, default_item = menu.add_command.call_args_list

        self.assertEqual(saved_item.kwargs["label"], "還原上次儲存")
        self.assertEqual(saved_item.kwargs["state"], "normal")
        self.assertEqual(default_item.kwargs["label"], "還原系統預設")
        saved_item.kwargs["command"]()
        default_item.kwargs["command"]()
        self.assertEqual([call.args for call in apply.call_args_list], [(4,), (9,)])
        menu.tk_popup.assert_called_once_with(5, 6)

    def test_unsaved_parameter_disables_restore_saved(self):
        menu, _apply = self._popup(None, 9)

        self.assertEqual(menu.add_command.call_args_list[0].kwargs["state"], "disabled")

    def test_menu_is_bound_to_every_widget_in_the_row(self):
        label, scale = FakeWidget(), FakeWidget()
        bind_restore_menu((label, None, scale), str, Mock(), 1, 2)

        self.assertIn("<Button-3>", label.callbacks)
        self.assertIn("<Button-3>", scale.callbacks)


class TesterParameterRowTests(unittest.TestCase):
    def test_name_label_drags_without_opening_the_editor_and_rows_get_a_restore_menu(self):
        tester = object.__new__(GamepadTestWindow)
        tester._open_parameter_editor = Mock()
        tester.gui = SimpleNamespace(tr=lambda text: text)
        value_label, name_label, scale = FakeWidget(), FakeWidget(), FakeWidget()
        variable = FakeVariable(2.5)
        changed = Mock()

        tester._bind_parameter_control(
            value_label, variable, "軌跡", 0.5, 5.0,
            step=0.1, number_format=".1f", on_change=changed,
            state_widget=scale, name_label=name_label, default=2.5,
        )
        name_label.fire("<ButtonPress-1>", START)
        name_label.fire("<B1-Motion>", moved(dx=16))
        name_label.fire("<ButtonRelease-1>", moved(dx=16))

        self.assertAlmostEqual(variable.value, 2.7)
        changed.assert_called_once_with()
        tester._open_parameter_editor.assert_not_called()
        for widget in (value_label, name_label, scale):
            self.assertIn("<Button-3>", widget.callbacks)


class SavedValueTests(unittest.TestCase):
    def test_compensation_restore_saved_follows_the_config_after_a_tester_save(self):
        import configparser
        from config_gui import ConfigGUI

        gui = object.__new__(ConfigGUI)
        gui.config = configparser.ConfigParser()
        gui.config.read_dict({"gyro_mapping": {"x_compensation": "1.00", "y_compensation": "1.00"}})
        # A stale registry must not win over the config the tester updated.
        gui.parameter_saved_registry = {}
        gui.config.set("gyro_mapping", "x_compensation", "1.25")

        self.assertEqual(gui._saved_gyro_compensation(), {"X": "1.25", "Y": "1.00"})

    def test_preview_restore_saved_follows_the_last_apply_to_aim_test(self):
        from gyro_compensation_window import GyroCompensationPreview

        preview = object.__new__(GyroCompensationPreview)
        preview.tr = lambda text: text
        preview.measurement = None
        preview.apply_to_aim_test = Mock()
        preview.status = FakeVariable("")
        preview._saved_speeds = [180, 120]
        preview._read_adjusted_speeds = lambda: (240, 150)

        preview.apply_adjusted_to_aim_test()

        self.assertEqual(preview._saved_speeds, [240, 150])


if __name__ == "__main__":
    unittest.main()
