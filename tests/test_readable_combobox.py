import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from readable_combobox import MappingCombobox, mapping_target_label
from config_gui import ConfigGUI


class ReadableComboboxTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        # Drain ttk's deferred theme events while their interpreter still exists.
        self.root.update_idletasks()
        self.root.destroy()

    def field(self, value="NONE", **kwargs):
        variable = tk.StringVar(self.root, value=value)
        field = MappingCombobox(
            self.root, textvariable=variable,
            values=("NONE", "A", "CUSTOM_KEYBOARD", "MOUSE:WHEEL_DOWN"),
            formatter=mapping_target_label, width=9, state="readonly", **kwargs,
        )
        field.pack()
        self.root.update_idletasks()
        return field, variable

    def test_selection_writes_original_target_before_capture_handler(self):
        field, variable = self.field()
        observed = []
        field.bind("<<ComboboxSelected>>", lambda _: observed.append(variable.get()))
        field.current(2)
        field.event_generate("<<ComboboxSelected>>")
        self.assertEqual(observed, ["CUSTOM_KEYBOARD"])
        variable.set("KEYBOARD:CTRL+S")
        self.assertEqual(field.get(), "KB:Ctrl+S")
        self.assertEqual(variable.get(), "KEYBOARD:CTRL+S")

    def test_recording_reset_and_unknown_targets_keep_canonical_values(self):
        field, variable = self.field("KEYBOARD:1")
        for target, label in (
            ("MOUSE:WHEEL_DOWN", "M:Wheel↓"),
            ("L_STICK_LEFT", "LS:←"),
            ("KEYBOARD:PAGEUP", "KB:PgUp"),
            ("FUTURE_TARGET", "FUTURE_TARGET"),
            ("NONE", "NONE"),
        ):
            with self.subTest(target=target):
                variable.set(target)
                self.assertEqual(field.get(), label)
                self.assertEqual(variable.get(), target)

    def test_long_current_value_keeps_full_hover_text_without_extra_widgets(self):
        field, variable = self.field("KEYBOARD:CTRL+SHIFT+ALT+WIN+BACKSPACE")
        self.assertEqual(field.get(), "KB:Ctrl+Shift+Alt+Win+Bksp")
        self.assertEqual(field.full_text(), "KEYBOARD:CTRL+SHIFT+ALT+WIN+BACKSPACE")
        self.assertEqual(field.winfo_children(), [])
        variable.set("KEYBOARD:1")
        self.assertEqual(field.get(), "KB:1")
        self.assertEqual(field.full_text(), "KEYBOARD:1")

    def test_refresh_translates_choices_without_changing_setting(self):
        language = ["zh"]
        translate = lambda text: "Custom" if language[0] == "en" else text
        variable = tk.StringVar(self.root, value="CUSTOM_KEYBOARD")
        field = MappingCombobox(
            self.root, textvariable=variable, values=("NONE", "CUSTOM_KEYBOARD"),
            formatter=lambda value: mapping_target_label(value, translate),
        )
        self.assertEqual(field.get(), "自訂按鍵")
        language[0] = "en"
        field.refresh()
        self.assertEqual(field.get(), "Custom")
        self.assertIn("Custom", field.cget("values"))
        self.assertEqual(variable.get(), "CUSTOM_KEYBOARD")

    def test_hover_shows_original_mapping_even_when_compact_label_fits(self):
        field, _variable = self.field("KEYBOARD:1")
        gui = object.__new__(ConfigGUI)
        gui._combobox_overflow_bound_widgets = set()
        gui._combobox_overflow_tooltips = {}
        gui._bind_combobox_overflow_tooltip(field)
        tooltip = gui._combobox_overflow_tooltips[str(field)]
        self.assertFalse(gui.combobox_text_is_truncated(field))
        field.event_generate("<Enter>")
        self.assertIsNotNone(tooltip.window)
        self.assertIn(
            "KEYBOARD:1", [child.cget("text") for child in tooltip.window.winfo_children()]
        )
        field.event_generate("<Leave>")
        self.assertIsNone(tooltip.window)

    def test_destroy_removes_trace_from_persistent_setting(self):
        field, variable = self.field("KEYBOARD:1")
        before = len(variable.trace_info())
        field.destroy()
        self.assertLess(len(variable.trace_info()), before)
        variable.set("KEYBOARD:2")

    def test_open_list_wheel_scrolls_without_committing_or_scrolling_parent(self):
        for combo_class in (ttk.Combobox, MappingCombobox):
            with self.subTest(combo_class=combo_class.__name__):
                variable = tk.StringVar(self.root, value="A")
                values = ("A",) + tuple(f"KEYBOARD:F{index}" for index in range(1, 13))
                combo = combo_class(
                    self.root, textvariable=variable, values=values,
                    height=4, state="readonly",
                )
                combo.pack()
                gui = object.__new__(ConfigGUI)
                gui.root = self.root
                gui._bind_combobox_popdown_mousewheel_guard(combo)
                self.root.tk.call("ttk::combobox::ConfigureListbox", str(combo))
                popup = self.root.tk.call("ttk::combobox::PopdownWindow", str(combo))
                listbox = f"{popup}.f.l"
                parent_scrolls = []
                selected = []
                self.root.bind_all("<MouseWheel>", lambda _: parent_scrolls.append(True))
                combo.bind("<<ComboboxSelected>>", lambda _: selected.append(variable.get()))
                # Physical wheel events are dispatched only to a mapped list.
                self.root.tk.call("wm", "deiconify", popup)
                self.root.update()

                def scroll(delta):
                    self.root.tk.call("event", "generate", listbox, "<MouseWheel>", "-delta", delta)
                    self.root.update_idletasks()
                    return tuple(float(value) for value in self.root.tk.call(listbox, "yview"))

                before = tuple(float(value) for value in self.root.tk.call(listbox, "yview"))
                after = scroll(-120)
                self.assertGreater(after[0], before[0])
                self.assertEqual(scroll(120)[0], before[0])
                self.root.tk.call(listbox, "yview", "moveto", 1)
                bottom = tuple(float(value) for value in self.root.tk.call(listbox, "yview"))
                self.assertEqual(scroll(-120), bottom)
                self.assertEqual(variable.get(), "A")
                self.assertEqual(selected, [])
                self.assertEqual(parent_scrolls, [])

                self.root.tk.call(listbox, "selection", "clear", 0, "end")
                self.root.tk.call(listbox, "selection", "set", 8)
                self.root.tk.call("ttk::combobox::LBSelected", listbox)
                self.root.update()
                self.assertEqual(variable.get(), "KEYBOARD:F8")
                self.assertEqual(selected, ["KEYBOARD:F8"])
                self.root.unbind_all("<MouseWheel>")
                combo.destroy()


if __name__ == "__main__":
    unittest.main()
