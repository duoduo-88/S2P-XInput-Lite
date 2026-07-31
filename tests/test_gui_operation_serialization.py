import unittest
from unittest.mock import Mock, patch

import config_gui


class _AliveProcess:
    def poll(self):
        return None


class _ScheduledRoot:
    def __init__(self):
        self._next_id = 0
        self._jobs = {}

    def after(self, _delay_ms, callback):
        self._next_id += 1
        job = f"job-{self._next_id}"
        self._jobs[job] = callback
        return job

    def after_cancel(self, job):
        self._jobs.pop(job, None)

    def run_all(self):
        while self._jobs:
            _job, callback = self._jobs.popitem()
            callback()

    @property
    def active_job_count(self):
        return len(self._jobs)


class GuiOperationSerializationTests(unittest.TestCase):
    def make_gui(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = _ScheduledRoot()
        gui._close_in_progress = False
        gui._main_start_job = None
        gui._calibration_start_job = None
        gui._flash_detection_job = None
        gui._flash_operation_active = False
        gui.main_process = None
        gui.calibration_process = None
        gui.flash_process = None
        gui.tr = lambda text: text
        return gui

    def test_repeated_restart_keeps_only_one_pending_start(self):
        gui = self.make_gui()
        gui.main_process = _AliveProcess()
        gui.has_unsaved_changes = Mock(return_value=False)
        gui.refresh_vigembus_status = Mock(return_value=True)
        gui.stop_main_process = Mock(
            side_effect=lambda: setattr(gui, "main_process", None)
        )
        gui.start_main = Mock(return_value=True)

        gui.restart_main()
        gui.restart_main()

        self.assertEqual(gui.root.active_job_count, 1)
        gui.root.run_all()
        gui.start_main.assert_called_once()

    def test_repeated_calibration_keeps_only_one_pending_start(self):
        gui = self.make_gui()
        gui.main_process = _AliveProcess()
        gui.stop_main_process = Mock(
            side_effect=lambda: setattr(gui, "main_process", None)
        )
        gui._start_calibration_process = Mock(return_value=True)

        with patch.object(config_gui.messagebox, "showinfo"):
            gui.run_calibration()
            gui.run_calibration()

        self.assertEqual(gui.root.active_job_count, 1)
        gui.root.run_all()
        gui._start_calibration_process.assert_called_once()

    def test_repeated_flash_request_does_not_duplicate_detection_poll(self):
        gui = self.make_gui()
        gui.stop_main_process = Mock()
        gui.get_serial_ports = Mock(return_value=set())

        with (
            patch.object(config_gui.messagebox, "askyesno", return_value=True),
            patch.object(config_gui.messagebox, "showinfo"),
        ):
            gui.flash_firmware()
            gui.flash_firmware()

        self.assertTrue(gui._flash_operation_active)
        self.assertEqual(gui.root.active_job_count, 1)
        self.assertEqual(gui.get_serial_ports.call_count, 2)

    def test_live_connector_blocks_direct_duplicate_start(self):
        gui = self.make_gui()
        gui.main_process = _AliveProcess()
        gui.prepare_hidhide_before_start = Mock(return_value=True)

        self.assertFalse(gui.start_main(config_gui.Path("main.py")))
        gui.prepare_hidhide_before_start.assert_not_called()

    def test_connector_starts_in_a_visible_console(self):
        gui = self.make_gui()
        gui.prepare_hidhide_before_start = Mock(return_value=True)
        process = Mock()

        with patch.object(
            config_gui.subprocess, "Popen", return_value=process
        ) as popen:
            self.assertTrue(gui.start_main(config_gui.Path("main.py")))

        self.assertIs(gui.main_process, process)
        options = popen.call_args.kwargs
        console_flag = getattr(config_gui.subprocess, "CREATE_NEW_CONSOLE", 0)
        self.assertEqual(
            options["creationflags"] & console_flag,
            console_flag,
        )
        self.assertNotIn("startupinfo", options)


if __name__ == "__main__":
    unittest.main()
