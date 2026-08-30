import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import config_gui


class _AliveProcess:
    def poll(self):
        return None


class _ImmediateThread:
    def __init__(self, target, **_kwargs):
        self._target = target

    def start(self):
        self._target()


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
        gui.get_serial_port_infos = Mock(return_value=())
        gui.inspect_esp32_firmware = Mock(return_value=(
            {"product": "S2P-FW", "version": "1.0.4"},
            False,
        ))

        with (
            patch.object(config_gui.messagebox, "askyesno", return_value=True),
            patch.object(config_gui.messagebox, "showinfo"),
        ):
            gui.flash_firmware()
            gui.flash_firmware()

        self.assertTrue(gui._flash_operation_active)
        self.assertEqual(gui.root.active_job_count, 1)
        self.assertEqual(gui.get_serial_ports.call_count, 1)
        self.assertEqual(gui.get_serial_port_infos.call_count, 1)

    def test_matching_firmware_version_can_cancel_without_flash_prompt(self):
        gui = self.make_gui()
        gui.inspect_esp32_firmware = Mock(return_value=(
            {"product": "S2P-FW", "version": "1.0.4"},
            False,
        ))
        gui.stop_main_process = Mock()
        gui.detect_flash_port = Mock()

        with patch.object(
            config_gui.messagebox, "askyesno", return_value=False
        ) as confirm:
            gui.flash_firmware()

        message = confirm.call_args.args[1]
        self.assertIn("S2P-FW v1.0.4", message)
        self.assertIn("目前已是相同版本", message)
        gui.stop_main_process.assert_not_called()
        gui.detect_flash_port.assert_not_called()

    def test_cancel_resumes_connector_stopped_for_version_probe(self):
        gui = self.make_gui()
        gui.inspect_esp32_firmware = Mock(return_value=(
            {"product": "S2P-FW", "version": "1.0.1"},
            True,
        ))
        gui._resume_connector_after_firmware_check = Mock()

        with patch.object(
            config_gui.messagebox, "askyesno", return_value=False
        ):
            gui.flash_firmware()

        gui._resume_connector_after_firmware_check.assert_called_once_with()

    def test_confirmed_version_check_enters_flash_detection(self):
        gui = self.make_gui()
        gui.inspect_esp32_firmware = Mock(return_value=(
            {"product": "S2P-FW", "version": "1.0.1"},
            False,
        ))
        gui.stop_main_process = Mock()
        gui.get_serial_ports = Mock(return_value={"COM7"})
        gui.detect_flash_port = Mock()

        with (
            patch.object(
                config_gui.messagebox, "askyesno", return_value=True
            ),
            patch.object(config_gui.messagebox, "showinfo"),
        ):
            gui.flash_firmware()

        self.assertTrue(gui._flash_operation_active)
        gui.stop_main_process.assert_called_once_with()
        gui.detect_flash_port.assert_called_once_with()

    def test_successful_flash_schedules_bridge_reconnect(self):
        gui = self.make_gui()
        process = Mock()
        gui.flash_process = process
        gui._schedule_main_start = Mock()

        with patch.object(config_gui.messagebox, "showinfo"):
            gui.finish_firmware_flash(process, 0)

        self.assertIsNone(gui.flash_process)
        gui._schedule_main_start.assert_called_once()
        self.assertEqual(gui._schedule_main_start.call_args.args[1], 1800)

    def test_return_to_bridge_starts_connector_when_previously_stopped(self):
        gui = self.make_gui()
        gui.config = Mock()
        gui.config.getint.return_value = 2_000_000
        gui._confirm_saved_profile_before_esp32_action = Mock(
            return_value=True
        )
        gui._schedule_main_start = Mock()

        with (
            patch.object(
                config_gui.messagebox, "askokcancel", return_value=True
            ),
            patch.object(config_gui.messagebox, "showinfo"),
            patch.object(config_gui, "find_esp32_port", return_value="COM7"),
            patch.object(
                config_gui,
                "set_esp32_mode",
                return_value={"restart_required": True},
            ),
            patch.object(config_gui.threading, "Thread", _ImmediateThread),
        ):
            gui.set_esp32_bridge_mode()
            gui.root.run_all()

        gui._schedule_main_start.assert_called_once()
        self.assertEqual(gui._schedule_main_start.call_args.args[1], 1800)

    def test_existing_esp32s3_bootloader_port_is_accepted(self):
        gui = self.make_gui()
        gui._flash_operation_active = True
        gui.ports_before_flash = {"COM4"}
        gui.get_serial_port_infos = Mock(return_value=(
            SimpleNamespace(device="COM4", vid=0x303A, pid=0x1001),
        ))
        gui.start_firmware_flash = Mock()

        with patch.object(config_gui.messagebox, "showinfo"):
            gui.detect_flash_port()

        gui.start_firmware_flash.assert_called_once_with("COM4")
        self.assertEqual(gui.root.active_job_count, 0)

    def test_flash_detection_waits_when_only_existing_bridge_is_present(self):
        gui = self.make_gui()
        gui._flash_operation_active = True
        gui.flash_detect_attempts = 0
        gui.ports_before_flash = {"COM7"}
        gui.get_serial_port_infos = Mock(return_value=(
            SimpleNamespace(device="COM7", vid=0x303A, pid=0x4001),
        ))
        gui.start_firmware_flash = Mock()
        gui.detect_flash_port()

        gui.start_firmware_flash.assert_not_called()
        self.assertEqual(gui.root.active_job_count, 1)

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
