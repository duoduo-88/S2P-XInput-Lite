from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config_gui
from test_gui_operation_serialization import _AliveProcess, _DeferredThread, _ScheduledRoot

REAL_THREAD = threading.Thread


class ImmediateThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class MainThreadRoot(_ScheduledRoot):
    def __init__(self):
        super().__init__()
        self.owner = threading.get_ident()

    def after(self, delay, callback):
        if threading.get_ident() != self.owner:
            raise RuntimeError("Tk scheduling attempted by a worker")
        return super().after(delay, callback)


class JoinedThread:
    errors = []

    def __init__(self, target, args=(), **kwargs):
        def run():
            try:
                target(*args)
            except BaseException as error:
                self.errors.append(error)
        self.thread = REAL_THREAD(target=run)

    def start(self):
        self.thread.start()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("test worker did not finish")


class ESP32OperationLifecycleTests(unittest.TestCase):
    def make_gui(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = MainThreadRoot()
        gui.language = "en"
        gui._close_in_progress = False
        gui._main_start_job = None
        gui._calibration_start_job = None
        gui._flash_operation_active = False
        gui.main_process = gui.calibration_process = gui.flash_process = None
        gui.config = config_gui.load_config(config_gui.DEFAULT_CONFIG_PATH, persist=False)
        gui._confirm_saved_profile_before_esp32_action = Mock(return_value=True)
        gui._confirm_esp32_usb_mode_change = Mock(return_value=True)
        gui._current_gui_settings_snapshot = Mock(return_value={})
        gui.profile_name_var = Mock()
        gui.profile_name_var.get.return_value = "Alpha"
        gui.mapping_layers = []
        gui.idle_disconnect_minutes = 5
        gui.prepare_hidhide_before_start = Mock(return_value=True)
        gui.write_esp32_button = Mock()
        return gui

    def setUp(self):
        JoinedThread.errors = []
        self.compiled = SimpleNamespace(issues=(), payload=b"profile", crc32=1)
        for patcher in (
            patch.object(config_gui.messagebox, "askokcancel", return_value=True),
            patch.object(config_gui.messagebox, "showinfo"),
            patch.object(config_gui.messagebox, "showerror"),
            patch.object(config_gui, "compile_standalone_profile", return_value=self.compiled),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_duplicate_mode_requests_start_only_one_worker(self):
        gui = self.make_gui()
        with patch.object(config_gui.threading, "Thread", _DeferredThread):
            gui.set_esp32_bridge_mode()
            gui.set_esp32_bridge_mode()
            gui.write_current_profile_to_esp32()
        # Both modes must share the same busy state before any queued worker runs.
        self.assertEqual(gui._confirm_saved_profile_before_esp32_action.call_count, 1)
        self.assertTrue(getattr(gui, "_esp32_operation_active", False))

    def test_transfer_blocks_other_hardware_operations_and_close(self):
        gui = self.make_gui()
        with patch.object(config_gui.threading, "Thread", _DeferredThread):
            gui.set_esp32_bridge_mode()
        gui.has_unsaved_changes = Mock(return_value=True)
        gui.inspect_esp32_firmware = Mock(return_value=(None, False))
        with patch.object(config_gui.subprocess, "Popen") as popen:
            self.assertFalse(gui.start_main(Path("main.py")))
            gui.run_calibration()
            gui.flash_firmware()
        self.assertEqual(popen.call_count, 0)
        gui.inspect_esp32_firmware.assert_not_called()
        gui._close_in_progress = True
        gui._perform_on_close()
        self.assertFalse(gui._close_in_progress)
        gui.has_unsaved_changes.assert_not_called()

    def test_background_mode_write_never_calls_tk(self):
        gui = self.make_gui()
        gui._schedule_main_start = Mock()
        with (
            patch.object(config_gui.threading, "Thread", JoinedThread),
            patch.object(config_gui, "find_esp32_port", return_value="COM7"),
            patch.object(config_gui, "set_esp32_mode", return_value={}) as write,
        ):
            gui.set_esp32_bridge_mode()
            gui.root.run_all()
        self.assertEqual(JoinedThread.errors, [])
        write.assert_called_once()
        gui._schedule_main_start.assert_called_once()
        self.assertFalse(getattr(gui, "_esp32_operation_active", False))

    def test_profile_write_cancels_pending_connector_start(self):
        gui = self.make_gui()
        gui._schedule_main_start(Path("main.py"), 500)
        with (
            patch.object(config_gui.threading, "Thread", ImmediateThread),
            patch.object(config_gui, "find_esp32_firmware", return_value={"port": "COM7"}),
            patch.object(config_gui, "write_standalone_profile", return_value={}) as write,
            patch.object(config_gui.subprocess, "Popen") as popen,
        ):
            gui.write_current_profile_to_esp32()
            gui.root.run_all()
        self.assertEqual(popen.call_count, 0)
        write.assert_called_once()

    def test_background_profile_write_never_calls_tk(self):
        gui = self.make_gui()
        with (
            patch.object(config_gui.threading, "Thread", JoinedThread),
            patch.object(config_gui, "find_esp32_firmware", return_value={"port": "COM7"}),
            patch.object(config_gui, "write_standalone_profile", return_value={}) as write,
        ):
            gui.write_current_profile_to_esp32()
            gui.root.run_all()
        self.assertEqual(JoinedThread.errors, [])
        write.assert_called_once()
        self.assertFalse(gui._esp32_operation_active)
        gui.write_esp32_button.config.assert_called_with(state="normal")

    def test_canceling_review_restores_pending_restart_for_both_modes(self):
        for action in ("write_current_profile_to_esp32", "set_esp32_bridge_mode"):
            with self.subTest(action=action):
                gui = self.make_gui()
                gui._schedule_main_start(Path("main.py"), 500)
                gui._confirm_saved_profile_before_esp32_action.return_value = False
                with patch.object(config_gui.subprocess, "Popen", return_value=_AliveProcess()) as popen:
                    getattr(gui, action)()
                    self.assertFalse(gui._esp32_operation_active)
                    self.assertEqual(gui.root.active_job_count, 1)
                    gui.root.run_all()
                popen.assert_called_once()
                gui.write_esp32_button.config.assert_called_with(state="normal")

    def test_other_owners_block_both_write_modes_before_review(self):
        for owner in ("firmware", "calibration", "pending_calibration", "closing"):
            for action in ("write_current_profile_to_esp32", "set_esp32_bridge_mode"):
                with self.subTest(owner=owner, action=action):
                    gui = self.make_gui()
                    if owner == "firmware":
                        gui._flash_operation_active = True
                    elif owner == "calibration":
                        gui.calibration_process = _AliveProcess()
                    elif owner == "pending_calibration":
                        gui._schedule_calibration_start(Path("calibration.py"), 500)
                    else:
                        gui._close_in_progress = True
                    getattr(gui, action)()
                    gui._confirm_saved_profile_before_esp32_action.assert_not_called()

    def test_unexpected_worker_and_thread_start_errors_release_the_operation(self):
        for failure in ("worker", "thread"):
            with self.subTest(failure=failure):
                gui = self.make_gui()
                gui._schedule_main_start = Mock()
                factory = ImmediateThread if failure == "worker" else Mock(side_effect=RuntimeError("cannot start thread"))
                with (
                    patch.object(config_gui.threading, "Thread", factory),
                    patch.object(config_gui, "find_esp32_port", side_effect=RuntimeError("unexpected probe error")),
                    patch.object(config_gui.messagebox, "showerror") as error,
                ):
                    gui.set_esp32_bridge_mode()
                    gui.root.run_all()
                error.assert_called_once()
                self.assertFalse(gui._esp32_operation_active)
                gui.write_esp32_button.config.assert_called_with(state="normal")

    def test_stale_start_and_poll_callbacks_preserve_new_operation(self):
        gui = self.make_gui()
        gui.set_esp32_bridge_mode()
        old_generation = gui._esp32_operation_generation
        old_start = gui.root._jobs[gui._esp32_start_job]
        gui._release_esp32_operation()
        gui.set_esp32_bridge_mode()
        new_job = gui._esp32_start_job
        with patch.object(config_gui.threading, "Thread") as factory:
            old_start()
            gui._poll_esp32_operation(old_generation)
        factory.assert_not_called()
        self.assertTrue(gui._esp32_operation_active)
        self.assertEqual(gui._esp32_start_job, new_job)

    def test_busy_and_close_notices_are_bilingual(self):
        for language in ("en", "zh"):
            with self.subTest(language=language):
                gui = self.make_gui()
                gui.language = language
                gui.set_esp32_bridge_mode()
                with patch.object(config_gui.messagebox, "showinfo") as notice:
                    gui.write_current_profile_to_esp32()
                    gui._close_in_progress = True
                    gui._perform_on_close()
                self.assertEqual(notice.call_count, 2)
                for call in notice.call_args_list:
                    text = " ".join(call.args)
                    if language == "en":
                        self.assertNotRegex(text, r"[\u4e00-\u9fff]")
                    else:
                        self.assertRegex(text, r"[\u4e00-\u9fff]")

    def test_completion_during_rejected_close_keeps_required_restart(self):
        for owner in ("esp32", "firmware"):
            with self.subTest(owner=owner):
                gui = self.make_gui()
                if owner == "esp32":
                    self.assertTrue(gui._begin_esp32_operation())
                    generation = gui._esp32_operation_generation
                    gui._esp32_result_queue.put(("finish", True, "mode changed", True))
                else:
                    gui.flash_process = _AliveProcess()
                completed = False

                def show_notice(*args, **kwargs):
                    nonlocal completed
                    if completed:
                        return
                    completed = True
                    if owner == "esp32":
                        gui._poll_esp32_operation(generation)
                    else:
                        gui.finish_firmware_flash(gui.flash_process, 0)

                gui._close_in_progress = True
                with patch.object(config_gui.messagebox, "showinfo", side_effect=show_notice):
                    gui._perform_on_close()
                self.assertFalse(gui._close_in_progress)
                self.assertEqual(gui.root.active_job_count, 1)
                with patch.object(config_gui.subprocess, "Popen", return_value=_AliveProcess()) as popen:
                    gui.root.run_all()
                popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
