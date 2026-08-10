import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import config_gui


class DummyVariable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class DummyLabel:
    def __init__(self):
        self.options = {}

    def config(self, **options):
        self.options.update(options)


class GuiPersistenceTests(unittest.TestCase):
    def test_zoom_deadzone_label_uses_numeric_scrubber(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.bind_numeric_scrubber = Mock()
        parent = Mock()
        variable = Mock()
        label = Mock()

        with patch.object(
            config_gui.ttk,
            "Label",
            return_value=label,
        ) as label_factory:
            result = gui._create_stick_zoom_deadzone_label(
                parent,
                "中心死區",
                variable,
            )

        self.assertIs(result, label)
        label_factory.assert_called_once_with(
            parent,
            text="中心死區",
            width=7,
            anchor="w",
        )
        label.pack.assert_called_once_with(side="left")
        gui.bind_numeric_scrubber.assert_called_once_with(
            label,
            variable,
            0.0,
            0.99,
            step=0.01,
            number_format=".2f",
        )

    def test_main_root_stays_hidden_until_gui_is_ready_to_show(self):
        events = []
        root = Mock()
        root.withdraw.side_effect = lambda: events.append("withdraw")
        root.mainloop.side_effect = lambda: events.append("mainloop")
        startup = Mock()
        startup.destroy.side_effect = lambda: events.append(
            "close-startup"
        )
        gui = Mock()
        gui.show_initial_window.side_effect = lambda: events.append("show")
        root.update_idletasks.side_effect = lambda: events.append(
            "paint-main"
        )
        gui._flush_dwm_composition.side_effect = lambda: events.append(
            "compose-main"
        )
        instance = Mock(is_primary=True, error=None)

        def build_gui(received_root):
            self.assertIs(received_root, root)
            events.append("construct")
            return gui

        with (
            patch.object(config_gui.tk, "Tk", return_value=root),
            patch.object(
                config_gui,
                "show_startup_window",
                side_effect=lambda received_root: (
                    self.assertIs(received_root, root),
                    events.append("startup"),
                    startup,
                )[-1],
            ),
            patch.object(config_gui, "ConfigGUI", side_effect=build_gui),
        ):
            config_gui.main(instance=instance)

        self.assertEqual(
            events,
            [
                "withdraw",
                "startup",
                "construct",
                "show",
                "paint-main",
                "compose-main",
                "close-startup",
                "mainloop",
            ],
        )
        instance.close.assert_called_once_with()

    def test_startup_artwork_is_centered_and_painted_before_gui_build(self):
        root = Mock()
        root.winfo_screenwidth.return_value = 1920
        root.winfo_screenheight.return_value = 1080
        window = Mock()
        window.after.return_value = "dots"
        window.winfo_exists.return_value = True
        image = Mock()
        image.width.return_value = 600
        image.height.return_value = 340
        canvas = Mock()
        canvas.create_text.return_value = "loading-text"
        config = Mock()
        config.get.return_value = "en"

        with (
            patch.object(config_gui, "load_config", return_value=config),
            patch.object(config_gui.tk, "Toplevel", return_value=window),
            patch.object(config_gui.tk, "PhotoImage", return_value=image) as photo,
            patch.object(config_gui.tk, "Canvas", return_value=canvas),
        ):
            result = config_gui.show_startup_window(root)

        self.assertIs(result.window, window)
        photo.assert_called_once_with(
            master=window,
            file=str(config_gui.STARTUP_IMAGE_PATH),
        )
        self.assertEqual(
            canvas.create_text.call_args.kwargs["text"],
            "v0.7.5 Starting...",
        )
        window.geometry.assert_called_once_with("600x340+660+370")
        self.assertLess(
            window.method_calls.index(call.withdraw()),
            window.method_calls.index(call.deiconify()),
        )
        root.update.assert_called_once_with()

        animation = window.after.call_args.args[1]
        animation()
        canvas.itemconfigure.assert_called_with(
            "loading-text",
            text="v0.7.5 Starting.",
        )

    def test_initial_window_is_positioned_before_it_is_revealed(self):
        events = []
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = Mock()
        gui.root.update_idletasks.side_effect = (
            lambda: events.append("layout")
        )
        gui.root.deiconify.side_effect = lambda: events.append("show")
        gui.update_adaptive_window = (
            lambda allow_unmapped=False: events.append(
                ("position", allow_unmapped)
            )
        )

        gui.show_initial_window()

        self.assertEqual(
            events,
            ["layout", ("position", True), "show"],
        )

    def test_child_dialog_centers_on_visible_root_not_requested_width(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = Mock()
        gui.root.winfo_width.return_value = 800
        gui.root.winfo_height.return_value = 600
        gui.root.winfo_reqwidth.return_value = 1200
        gui.root.winfo_reqheight.return_value = 900
        gui.root.winfo_rootx.return_value = 100
        gui.root.winfo_rooty.return_value = 100
        gui.get_work_area = Mock(return_value=(0, 0, 1920, 1080))
        window = Mock()

        gui._center_child_window(window, width=200, height=100)

        window.geometry.assert_called_once_with("200x100+400+350")

    def test_mapping_layer_editor_uses_visible_root_width(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = Mock()
        gui.root.winfo_width.return_value = 800
        gui.root.winfo_reqwidth.return_value = 1200

        self.assertEqual(gui._mapping_layer_editor_width(), 800)

    def test_mapping_layer_editor_falls_back_to_requested_width_before_show(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = Mock()
        gui.root.winfo_width.return_value = 1
        gui.root.winfo_reqwidth.return_value = 900

        self.assertEqual(gui._mapping_layer_editor_width(), 900)

    def test_profile_reload_refreshes_stick_mode_after_atomic_variable_merge(self):
        gui = object.__new__(config_gui.ConfigGUI)
        original_mode_var = DummyVariable("XINPUT_LT_LINEAR")
        gui.stick_direction_mode_vars = {"LEFT": original_mode_var}
        gui.profile_name_var = DummyVariable("Old")
        gui.active_profile = "Racing"
        observations = []
        gui.stick_direction_mode_updaters = {
            "LEFT": lambda: observations.append(
                (
                    gui._loading_profile_values,
                    gui.stick_direction_mode_vars["LEFT"].get(),
                )
            )
        }

        def create_reloaded_variables():
            gui.stick_direction_mode_vars = {
                "LEFT": DummyVariable("4WAY")
            }
            gui.profile_name_var = DummyVariable("Temporary")

        def contains_dummy_variable(value):
            if isinstance(value, DummyVariable):
                return True
            if isinstance(value, dict):
                return any(
                    contains_dummy_variable(item) for item in value.values()
                )
            return False

        def merge_dummy_variables(old_value, new_value):
            if (
                isinstance(old_value, DummyVariable)
                and isinstance(new_value, DummyVariable)
            ):
                old_value.set(new_value.get())
                return old_value
            if isinstance(old_value, dict) and isinstance(new_value, dict):
                return {
                    key: merge_dummy_variables(old_value[key], value)
                    if key in old_value else value
                    for key, value in new_value.items()
                }
            return new_value

        gui.create_variables = create_reloaded_variables
        gui._contains_tk_variable = contains_dummy_variable
        gui._merge_tk_variables = merge_dummy_variables
        gui._update_audio_response_state = Mock()
        gui._redraw_stick_curve_editors = Mock()
        gui.refresh_mapping_layer_rows = Mock()
        gui.mapping_layers = []
        gui._capture_layer_folder_snapshot = Mock(return_value={})
        gui.build_parameter_default_registry = Mock()

        gui.reload_profile_variables()

        self.assertIs(gui.stick_direction_mode_vars["LEFT"], original_mode_var)
        self.assertEqual(original_mode_var.get(), "4WAY")
        self.assertEqual(observations, [(False, "4WAY")])

    def test_reentrant_main_window_close_is_ignored(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui._close_in_progress = True
        gui._perform_on_close = Mock()

        gui.on_close()

        gui._perform_on_close.assert_not_called()

    def test_minimized_root_is_restored_for_close_prompt_and_reminimized_on_cancel(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui._close_in_progress = False
        gui.root = Mock()
        gui.root.state.return_value = "iconic"
        gui.root.winfo_exists.return_value = True

        def cancel_close():
            gui._close_in_progress = False

        gui._perform_on_close = Mock(side_effect=cancel_close)

        gui.on_close()

        gui.root.deiconify.assert_called_once_with()
        gui.root.update.assert_called_once_with()
        gui.root.wait_visibility.assert_not_called()
        gui.root.lift.assert_called_once_with()
        gui.root.iconify.assert_called_once_with()

    def test_minimized_root_stays_restored_when_close_is_confirmed(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui._close_in_progress = False
        gui.root = Mock()
        gui.root.state.return_value = "iconic"
        gui._perform_on_close = Mock()

        gui.on_close()

        gui.root.deiconify.assert_called_once_with()
        gui.root.iconify.assert_not_called()

    def test_canceling_close_warning_does_not_save_suppression(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui._close_in_progress = False
        gui.has_unsaved_changes = Mock(return_value=False)
        gui._close_mode_warning_is_suppressed = Mock(return_value=False)
        gui._prompt_close_mode_warning = Mock(return_value=(False, True))
        gui._set_close_mode_warning_suppressed = Mock()
        gui.close_stick_zoom_window = Mock()

        gui.on_close()

        self.assertFalse(gui._close_in_progress)
        gui._set_close_mode_warning_suppressed.assert_not_called()
        gui.close_stick_zoom_window.assert_not_called()

    def test_confirming_close_saves_suppression_once_before_cleanup(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui._close_in_progress = False
        gui.has_unsaved_changes = Mock(return_value=False)
        gui._close_mode_warning_is_suppressed = Mock(return_value=False)
        gui._prompt_close_mode_warning = Mock(return_value=(True, True))
        calls = []
        gui._set_close_mode_warning_suppressed = (
            lambda value: calls.append(("suppressed", value))
        )
        gui.close_stick_zoom_window = (
            lambda: calls.append(("close_zoom", None))
        )
        gui._gamepad_test_startup_job = None
        gui._gamepad_test_exit_job = None
        gui._gamepad_test_reopen_requested = True
        gui._close_gamepad_test_process = (
            lambda: calls.append(("close_tester", None))
        )
        gui.stop_main_process = (
            lambda: calls.append(("stop_bridge", None))
        )
        gui.calibration_process = None
        gui.root = Mock()

        gui.on_close()

        self.assertEqual(
            calls,
            [
                ("suppressed", True),
                ("close_zoom", None),
                ("close_tester", None),
                ("stop_bridge", None),
            ],
        )
        self.assertFalse(gui._gamepad_test_reopen_requested)
        gui.root.destroy.assert_called_once_with()

    def test_close_warning_preference_preserves_newer_config_data(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            stale = config_gui.load_config(config_path)
            latest = config_gui.load_config(config_path)
            if not latest.has_section("gyro"):
                latest.add_section("gyro")
            latest.set("gyro", "bias", "1.0, 2.0, 3.0")
            config_gui.atomic_write_config(latest, config_path)
            gui = object.__new__(config_gui.ConfigGUI)
            gui.config = stale

            with patch.object(config_gui, "CONFIG_PATH", config_path):
                gui._set_close_mode_warning_suppressed(True)

            saved = config_gui.load_config(config_path)
            self.assertTrue(
                saved.getboolean("gui", "close_mode_warning_suppressed")
            )
            self.assertEqual(saved.get("gyro", "bias"), "1.0, 2.0, 3.0")

    def test_reset_defaults_reenables_all_dismissed_prompts(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui._set_hidhide_missing_prompt_dismissed = Mock()
        gui._set_hidhide_setup_prompt_dismissed = Mock()
        gui._set_close_mode_warning_suppressed = Mock()

        gui._reset_prompt_preferences_to_defaults()

        gui._set_hidhide_missing_prompt_dismissed.assert_called_once_with(
            False
        )
        gui._set_hidhide_setup_prompt_dismissed.assert_called_once_with(False)
        gui._set_close_mode_warning_suppressed.assert_called_once_with(False)

    def test_close_mode_warning_has_complete_english_copy(self):
        message = (
            "關閉設定視窗後：\n\n"
            "• 桌面橋接模式會中斷連線。\n"
            "• ESP32 獨立模式會繼續運作。"
        )

        self.assertEqual(
            config_gui.translate_text("關閉程式", "en"),
            "Close Application",
        )
        self.assertIn(
            "Desktop bridge mode will disconnect",
            config_gui.translate_text(message, "en"),
        )
        self.assertEqual(
            config_gui.translate_text("以後不再提示", "en"),
            "Do not show this again",
        )

    def test_gamepad_tester_receives_current_gui_language(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.language = "zh"
        gui.root = Mock()
        gui.gamepad_test_process = None
        gui._gamepad_test_startup_job = None
        gui._show_gamepad_test_loading_window = Mock()
        process = Mock()
        process.stdout.readline.return_value = b""

        with patch.object(
            config_gui.subprocess,
            "Popen",
            return_value=process,
        ) as popen:
            gui.open_gamepad_test_window()

        command = popen.call_args.args[0]
        self.assertEqual(command[-2:], ["--language", "zh"])
        gui._show_gamepad_test_loading_window.assert_called_once_with()
        gui.root.after.assert_called_once()

    def test_gamepad_tester_ready_closes_initialization_window(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = Mock()
        ready_event = Mock(is_set=Mock(return_value=True))
        gui.gamepad_test_process = process
        gui._gamepad_test_startup_job = "startup-job"
        gui._close_gamepad_test_loading_window = Mock()

        gui._check_gamepad_test_startup(process, ready_event)

        self.assertIsNone(gui._gamepad_test_startup_job)
        gui._close_gamepad_test_loading_window.assert_called_once_with()
        process.poll.assert_not_called()

    def test_gamepad_tester_lifecycle_reports_ready_and_closing(self):
        process = Mock()
        process.stdout.readline.side_effect = (
            b"ready\n",
            b"closing\n",
            b"",
        )
        ready_event = Mock()
        closing_event = Mock()

        config_gui.ConfigGUI._watch_gamepad_test_lifecycle(
            process,
            closing_event,
            ready_event,
        )

        ready_event.set.assert_called_once_with()
        closing_event.set.assert_called_once_with()

    def test_language_change_does_not_reopen_a_closing_gamepad_tester(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = Mock()
        process.poll.return_value = None
        gui.gamepad_test_process = process
        gui._gamepad_test_closing_event = Mock(
            is_set=Mock(return_value=True)
        )
        gui._gamepad_test_language_restart_job = "restart-job"
        gui._gamepad_test_startup_job = None
        gui._close_gamepad_test_process = Mock()
        gui.open_gamepad_test_window = Mock()

        gui._restart_gamepad_test_after_language_change(process)

        self.assertIsNone(gui._gamepad_test_language_restart_job)
        gui._close_gamepad_test_process.assert_not_called()
        gui.open_gamepad_test_window.assert_not_called()

    def test_language_change_restarts_a_still_open_gamepad_tester(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = Mock()
        process.poll.return_value = None
        gui.gamepad_test_process = process
        gui._gamepad_test_closing_event = Mock(
            is_set=Mock(return_value=False)
        )
        gui._gamepad_test_language_restart_job = "restart-job"
        gui._gamepad_test_startup_job = None
        gui._close_gamepad_test_process = Mock()
        gui.open_gamepad_test_window = Mock()

        gui._restart_gamepad_test_after_language_change(process)

        self.assertIsNone(gui._gamepad_test_language_restart_job)
        gui._close_gamepad_test_process.assert_called_once_with()
        gui.open_gamepad_test_window.assert_called_once_with()

    def test_gamepad_tester_stderr_is_continuously_drained_and_bounded(self):
        process = Mock()
        process.stderr.readline.side_effect = (
            b"first warning\n",
            b"second warning\n",
            b"",
        )
        lines = deque(maxlen=1)

        config_gui.ConfigGUI._drain_gamepad_test_stderr(process, lines)

        self.assertEqual(list(lines), ["second warning"])
        self.assertEqual(process.stderr.readline.call_count, 3)

    def test_running_gamepad_tester_is_raised_without_duplicate_window(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.gamepad_test_process = Mock()
        gui.gamepad_test_process.poll.return_value = None
        gui._gamepad_test_closing_event = Mock(
            is_set=Mock(return_value=False)
        )
        gui._gamepad_test_reopen_requested = False
        gui._gamepad_test_exit_job = None
        gui.root = Mock()
        gui._allow_gamepad_test_foreground = Mock()

        with patch.object(config_gui.subprocess, "Popen") as popen:
            gui.open_gamepad_test_window()

        popen.assert_not_called()
        gui._allow_gamepad_test_foreground.assert_called_once_with(
            gui.gamepad_test_process
        )
        gui.gamepad_test_process.stdin.write.assert_called_once_with(
            b"show\n"
        )
        gui.gamepad_test_process.stdin.flush.assert_called_once_with()
        gui.root.after.assert_not_called()
        self.assertFalse(gui._gamepad_test_reopen_requested)

    def test_close_time_click_requests_reopen_after_process_exit(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = Mock()
        process.poll.return_value = None
        gui.gamepad_test_process = process
        gui._gamepad_test_closing_event = Mock(
            is_set=Mock(return_value=True)
        )
        gui._gamepad_test_reopen_requested = False
        gui._gamepad_test_exit_job = None
        gui.root = Mock()
        gui.root.after.return_value = "exit-job"
        gui._show_gamepad_test_loading_window = Mock()

        gui.open_gamepad_test_window()

        self.assertTrue(gui._gamepad_test_reopen_requested)
        self.assertEqual(gui._gamepad_test_exit_job, "exit-job")
        gui._show_gamepad_test_loading_window.assert_called_once_with()
        gui.root.after.assert_called_once()

    def test_gamepad_tester_reopens_once_old_process_has_exited(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = Mock()
        process.poll.return_value = 0
        gui.gamepad_test_process = process
        gui._gamepad_test_closing_event = Mock()
        gui._gamepad_test_reopen_requested = True
        gui._gamepad_test_exit_job = "exit-job"
        loading_window = Mock()
        loading_window.winfo_exists.return_value = True
        gui._gamepad_test_loading_window = loading_window
        gui.open_gamepad_test_window = Mock()

        gui._check_gamepad_test_exit(process)

        self.assertIsNone(gui.gamepad_test_process)
        self.assertFalse(gui._gamepad_test_reopen_requested)
        loading_window.destroy.assert_not_called()
        gui.open_gamepad_test_window.assert_called_once_with()

    def test_existing_gamepad_loading_window_is_reused(self):
        gui = object.__new__(config_gui.ConfigGUI)
        window = Mock()
        window.winfo_exists.return_value = True
        gui._gamepad_test_loading_window = window

        with patch.object(config_gui.tk, "Toplevel") as toplevel:
            gui._show_gamepad_test_loading_window()

        window.lift.assert_called_once_with()
        window.destroy.assert_not_called()
        toplevel.assert_not_called()

    def test_early_gamepad_tester_failure_surfaces_stderr(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.tr = lambda text: text
        gui.root = Mock()
        process = Mock()
        process.poll.return_value = 1
        gui.gamepad_test_process = process
        gui._gamepad_test_startup_job = "job"
        gui._gamepad_test_stderr_lines = deque(["startup traceback"])
        gui._gamepad_test_stderr_thread = None

        with patch.object(config_gui.messagebox, "showerror") as showerror:
            gui._check_gamepad_test_startup(process)

        self.assertIsNone(gui.gamepad_test_process)
        self.assertIsNone(gui._gamepad_test_startup_job)
        self.assertIn("startup traceback", showerror.call_args.args[1])

    def test_gamepad_tester_shutdown_requests_clean_exit_before_terminate(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        gui.gamepad_test_process = process

        gui._close_gamepad_test_process()

        process.stdin.write.assert_called_once_with(b"close\n")
        process.stdin.flush.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1.5)
        process.terminate.assert_not_called()
        self.assertIsNone(gui.gamepad_test_process)

    def test_gamepad_tester_shutdown_terminates_only_after_timeout(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = (
            config_gui.subprocess.TimeoutExpired("tester", 1.5),
            0,
        )
        gui.gamepad_test_process = process

        gui._close_gamepad_test_process()

        process.terminate.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    def test_gamepad_tester_hard_timeout_keeps_process_for_later_reap(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = config_gui.subprocess.TimeoutExpired(
            "tester", 0.5
        )
        gui.gamepad_test_process = process
        gui._gamepad_test_exit_job = None
        gui.root = Mock()
        gui.root.after.return_value = "reap-job"

        self.assertFalse(gui._close_gamepad_test_process())

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertIs(gui.gamepad_test_process, process)
        self.assertTrue(gui._gamepad_test_closing_event.is_set())
        self.assertEqual(gui._gamepad_test_exit_job, "reap-job")
        process.stdin.close.assert_not_called()
        process.stdout.close.assert_not_called()
        process.stderr.close.assert_not_called()

    def test_parameter_mousewheel_scrolls_canvas_and_stops_value_binding(self):
        class Widget:
            def __init__(self, widget_class, master=None):
                self.widget_class = widget_class
                self.master = master
                self.scrolls = []

            def winfo_class(self):
                return self.widget_class

            def yview_scroll(self, units, mode):
                self.scrolls.append((units, mode))

        canvas = Widget("Canvas")
        scale = Widget("TScale", master=canvas)
        gui = object.__new__(config_gui.ConfigGUI)

        result = gui._on_parameter_control_mousewheel(
            SimpleNamespace(widget=scale, delta=-120)
        )

        self.assertEqual(result, "break")
        self.assertEqual(canvas.scrolls, [(1, "units")])

    def test_parameter_controls_receive_widget_level_wheel_guard(self):
        class Widget:
            master = None

            def __init__(self):
                self.bindings = []

            def winfo_class(self):
                return "TCombobox"

            def bind(self, sequence, callback, add=None):
                self.bindings.append((sequence, callback, add))

            def __str__(self):
                return ".parameter"

        gui = object.__new__(config_gui.ConfigGUI)
        gui.profile_combo = None
        gui._parameter_wheel_bound_widgets = set()
        gui._parameter_reset_bound_widgets = set()
        gui.parameter_binding_for_widget = lambda _widget: None
        widget = Widget()

        gui._bind_parameter_reset_widget(widget)

        self.assertEqual(len(widget.bindings), 1)
        sequence, callback, add = widget.bindings[0]
        self.assertEqual(sequence, "<MouseWheel>")
        self.assertEqual(callback, gui._on_parameter_control_mousewheel)
        self.assertEqual(add, "+")

    def test_combobox_popdown_list_ignores_mousewheel(self):
        calls = []

        class Tcl:
            def call(self, *args):
                calls.append(args)
                if args[0] == "ttk::combobox::PopdownWindow":
                    return ".parameter.popdown"
                return ""

        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = SimpleNamespace(tk=Tcl())

        gui._bind_combobox_popdown_mousewheel_guard(".parameter")

        self.assertEqual(
            calls,
            [
                (
                    "ttk::combobox::PopdownWindow",
                    ".parameter",
                ),
                (
                    "bind",
                    ".parameter.popdown.f.l",
                    "<MouseWheel>",
                    "break",
                ),
            ],
        )

    def test_esp32_write_requires_unsaved_profile_to_be_saved_first(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.active_profile = "General"
        gui.tr = lambda text: text
        states = iter((True, False))
        gui.has_unsaved_changes = lambda: next(states)
        gui.save_current_profile = unittest.mock.Mock(return_value=True)

        with patch.object(
            config_gui.messagebox, "askyesno", return_value=True
        ) as prompt:
            self.assertTrue(
                gui._confirm_saved_profile_before_esp32_action()
            )

        prompt.assert_called_once()
        gui.save_current_profile.assert_called_once_with(show_message=False)

    def test_esp32_write_stops_when_unsaved_save_is_declined(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.active_profile = "General"
        gui.tr = lambda text: text
        gui.has_unsaved_changes = lambda: True
        gui.save_current_profile = unittest.mock.Mock(return_value=True)

        with patch.object(
            config_gui.messagebox, "askyesno", return_value=False
        ):
            self.assertFalse(
                gui._confirm_saved_profile_before_esp32_action()
            )

        gui.save_current_profile.assert_not_called()

    def test_esp32_write_saves_read_only_profile_as_new_profile(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.active_profile = "System Default"
        gui.tr = lambda text: text
        states = iter((True, False))
        gui.has_unsaved_changes = lambda: next(states)
        gui.save_profile_as = unittest.mock.Mock(return_value=True)

        with patch.object(
            config_gui.messagebox, "askyesno", return_value=True
        ):
            self.assertTrue(
                gui._confirm_saved_profile_before_esp32_action()
            )

        gui.save_profile_as.assert_called_once_with()

    def test_declining_missing_hidhide_prompt_is_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            missing_status = {"state": "not_installed", "installed": False}

            first_gui = object.__new__(config_gui.ConfigGUI)
            first_gui.config = config_gui.load_config(config_path)
            first_gui.hidhide_prompt_shown = False
            first_gui.hidhide_status = None
            first_gui.hidhide_status_label = DummyLabel()
            first_gui.tr = lambda text: text

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "inspect_hidhide", return_value=missing_status),
                patch.object(config_gui.messagebox, "askyesno", return_value=False) as prompt,
            ):
                self.assertTrue(first_gui.prepare_hidhide_before_start())
                prompt.assert_called_once()

            saved = config_gui.load_config(config_path)
            self.assertTrue(
                saved.getboolean("gui", "hidhide_missing_prompt_dismissed")
            )

            second_gui = object.__new__(config_gui.ConfigGUI)
            second_gui.config = saved
            second_gui.hidhide_prompt_shown = False
            second_gui.hidhide_status = None
            second_gui.hidhide_status_label = DummyLabel()
            second_gui.tr = lambda text: text

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "inspect_hidhide", return_value=missing_status),
                patch.object(config_gui.messagebox, "askyesno") as repeated_prompt,
            ):
                self.assertTrue(second_gui.prepare_hidhide_before_start())
                repeated_prompt.assert_not_called()

    def test_declining_installed_hidhide_setup_is_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            setup_status = {
                "state": "disabled",
                "installed": True,
                "cloak_active": False,
                "hidden_count": 0,
            }

            first_gui = object.__new__(config_gui.ConfigGUI)
            first_gui.config = config_gui.load_config(config_path)
            first_gui.hidhide_prompt_shown = False
            first_gui.hidhide_status = None
            first_gui.hidhide_status_label = DummyLabel()
            first_gui.tr = lambda text: text

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "inspect_hidhide", return_value=setup_status),
                patch.object(config_gui.messagebox, "askyesno", return_value=False) as prompt,
            ):
                self.assertTrue(first_gui.prepare_hidhide_before_start())
                prompt.assert_called_once()

            saved = config_gui.load_config(config_path)
            self.assertTrue(
                saved.getboolean("gui", "hidhide_setup_prompt_dismissed")
            )

            second_gui = object.__new__(config_gui.ConfigGUI)
            second_gui.config = saved
            second_gui.hidhide_prompt_shown = False
            second_gui.hidhide_status = None
            second_gui.hidhide_status_label = DummyLabel()
            second_gui.tr = lambda text: text

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "inspect_hidhide", return_value=setup_status),
                patch.object(config_gui.messagebox, "askyesno") as repeated_prompt,
            ):
                self.assertTrue(second_gui.prepare_hidhide_before_start())
                repeated_prompt.assert_not_called()

    def test_clicking_setup_status_reenables_the_prompt(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.hidhide_status = {"state": "setup"}
        gui.hidhide_prompt_shown = True
        dismissed_values = []
        prompt_calls = []
        gui._set_hidhide_setup_prompt_dismissed = dismissed_values.append
        gui.prepare_hidhide_before_start = lambda: prompt_calls.append(True)

        gui.handle_hidhide_status_click()

        self.assertEqual(dismissed_values, [False])
        self.assertFalse(gui.hidhide_prompt_shown)
        self.assertEqual(prompt_calls, [True])

    def test_failed_bundle_restore_recovers_replaced_files_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.ini"
            profile_dir = root / "profiles"
            layer_dir = root / "layers"
            profile_dir.mkdir()
            layer_dir.mkdir()
            profile_path = profile_dir / "General.ini"
            old_layer = layer_dir / "Old.json"
            config_path.write_bytes(b"old config")
            profile_path.write_bytes(b"old profile")
            old_layer.write_bytes(b"old layer")

            gui = object.__new__(config_gui.ConfigGUI)
            gui.mapping_layers = [{"name": "New"}]
            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "PROFILE_DIR", profile_dir),
                patch.object(config_gui, "LAYER_DIR", layer_dir),
            ):
                snapshot = gui.capture_settings_bundle("General")
                config_path.write_bytes(b"new config")
                profile_path.write_bytes(b"new profile")
                old_layer.unlink()
                (layer_dir / "New.json").write_bytes(b"new layer")
                external = layer_dir / "External.json"
                external.write_bytes(b"external race")

                gui.restore_settings_bundle(snapshot)

            self.assertEqual(config_path.read_bytes(), b"old config")
            self.assertEqual(profile_path.read_bytes(), b"old profile")
            self.assertEqual(old_layer.read_bytes(), b"old layer")
            self.assertFalse((layer_dir / "New.json").exists())
            self.assertEqual(external.read_bytes(), b"external race")

    def test_active_profile_rename_rolls_back_when_config_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.ini"
            profile_dir = root / "profiles"
            profile_dir.mkdir()
            config = config_gui.load_config(config_path)
            config.set("gui", "active_profile", "Old")
            config_gui.atomic_write_config(config, config_path)
            config_gui.save_profile(config, "Old", profile_dir)
            original_config = config_path.read_bytes()

            gui = object.__new__(config_gui.ConfigGUI)
            gui.active_profile = "Old"
            gui.config = config
            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "PROFILE_DIR", profile_dir),
                patch.object(
                    config_gui,
                    "atomic_write_config",
                    side_effect=OSError("forced write failure"),
                ),
            ):
                with self.assertRaises(OSError):
                    gui.rename_active_profile_transaction("Old", "New")

            self.assertTrue((profile_dir / "Old.ini").is_file())
            self.assertFalse((profile_dir / "New.ini").exists())
            self.assertEqual(config_path.read_bytes(), original_config)
            self.assertEqual(gui.active_profile, "Old")

    def test_profile_switch_refreshes_active_file_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.ini"
            profile_dir = root / "profiles"
            profile_dir.mkdir()
            config = config_gui.load_config(config_path)
            config_gui.atomic_write_config(config, config_path)
            config_gui.save_profile(config, "General", profile_dir)
            config.set("rumble", "lf_strength", "0.37")
            config_gui.save_profile(config, "Racing", profile_dir)

            gui = object.__new__(config_gui.ConfigGUI)
            gui.active_profile = "General"
            gui.language = "zh_TW"
            gui.profile_name_var = DummyVariable("Racing")
            gui._profile_switch_in_progress = False
            gui.main_process = None
            gui.config = config
            gui._active_profile_content_baseline = "old-profile-hash"
            gui._confirm_profile_transition = lambda: True
            gui.reload_profile_variables = lambda: None
            gui.request_adaptive_window_update = lambda: None

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "PROFILE_DIR", profile_dir),
                patch.object(config_gui.messagebox, "showinfo"),
                patch.object(config_gui.messagebox, "showerror"),
            ):
                gui.apply_selected_profile("Racing")
                expected = gui.profile_file_fingerprint("Racing")

            self.assertEqual(gui.active_profile, "Racing")
            self.assertEqual(gui._active_profile_content_baseline, expected)

    def test_automatic_update_check_respects_shared_toggle(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.check_for_updates = Mock()

        with patch.object(
            config_gui,
            "automatic_update_checks_enabled",
            return_value=False,
        ):
            gui.start_automatic_update_check()

        gui.check_for_updates.assert_not_called()

    def test_automatic_update_check_skips_ignored_version(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = Mock()
        gui.root.winfo_exists.return_value = True
        gui._update_check_in_progress = True
        gui._show_update_available_prompt = Mock()
        release = SimpleNamespace(version="0.7.3")

        with (
            patch.object(
                config_gui, "is_newer_version", return_value=True
            ),
            patch.object(
                config_gui,
                "ignored_update_version",
                return_value="0.7.3",
            ),
        ):
            gui._finish_update_check(release, None, manual=False)

        self.assertFalse(gui._update_check_in_progress)
        gui._show_update_available_prompt.assert_not_called()

    def test_manual_update_check_still_shows_ignored_version(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = Mock()
        gui.root.winfo_exists.return_value = True
        gui._update_check_in_progress = True
        gui._show_update_available_prompt = Mock()
        release = SimpleNamespace(version="0.7.3")

        with (
            patch.object(
                config_gui, "is_newer_version", return_value=True
            ),
            patch.object(
                config_gui,
                "ignored_update_version",
                return_value="0.7.3",
            ),
        ):
            gui._finish_update_check(release, None, manual=True)

        gui._show_update_available_prompt.assert_called_once_with(release)


if __name__ == "__main__":
    unittest.main()
