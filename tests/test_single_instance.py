import os
import unittest
import uuid
from unittest.mock import Mock, patch

import config_gui
import gamepad_test_app
from single_instance import SingleInstance, activate_tk_window


class SingleInstanceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows named events required")
    def test_second_instance_signals_the_primary_instance(self):
        name = f"S2P-XInput-Lite.Tests.{uuid.uuid4()}"
        primary = SingleInstance(name)
        duplicate = SingleInstance(name)
        self.addCleanup(primary.close)
        self.addCleanup(duplicate.close)

        self.assertTrue(primary.is_primary)
        self.assertFalse(duplicate.is_primary)
        self.assertFalse(primary.activation_requested())
        self.assertTrue(duplicate.notify_existing())
        self.assertTrue(primary.activation_requested())
        self.assertFalse(primary.activation_requested())

    def test_duplicate_settings_notifies_existing_window_without_tk(self):
        instance = Mock(is_primary=False)

        with patch.object(config_gui.tk, "Tk") as tk_root:
            self.assertFalse(config_gui.main(instance=instance))

        tk_root.assert_not_called()
        instance.notify_existing.assert_called_once_with()
        instance.close.assert_called_once_with()

    @unittest.skipUnless(os.name == "nt", "Windows named events required")
    def test_gyro_settings_action_is_delivered_separately_from_activation(self):
        name = f"S2P-XInput-Lite.Tests.{uuid.uuid4()}"
        primary = SingleInstance(name, action="GyroSettings")
        duplicate = SingleInstance(name, action="GyroSettings")
        self.addCleanup(primary.close)
        self.addCleanup(duplicate.close)
        self.assertTrue(primary.is_primary)
        self.assertFalse(duplicate.is_primary)
        self.assertTrue(duplicate.notify_existing(action=True))
        self.assertFalse(primary.activation_requested())
        self.assertTrue(primary.activation_requested(action=True))
        self.assertFalse(primary.activation_requested(action=True))

    def test_tester_routes_gyro_settings_to_existing_instance_or_launches_it(self):
        host = gamepad_test_app.GamepadTestHost.__new__(gamepad_test_app.GamepadTestHost)
        with (patch.object(gamepad_test_app, "SingleInstance") as guard,
              patch.object(gamepad_test_app.subprocess, "Popen") as launch):
            instance = guard.return_value
            instance.is_primary = False
            instance.notify_existing.return_value = True
            host.open_gyro_settings()
            instance.notify_existing.assert_called_once_with(action=True)
            launch.assert_not_called()
            instance.is_primary = True
            launch.return_value.pid = 1234
            host.open_gyro_settings()
            self.assertTrue(launch.call_args.args[0][1].endswith("config_gui.py"))
            self.assertEqual(launch.call_args.args[0][2], "--gyro-settings")

    def test_duplicate_tester_notifies_existing_window_without_tk(self):
        instance = Mock(is_primary=False)

        with patch.object(gamepad_test_app.tk, "Tk") as tk_root:
            self.assertFalse(gamepad_test_app.main(instance=instance))

        tk_root.assert_not_called()
        instance.notify_existing.assert_called_once_with()
        instance.close.assert_called_once_with()

    def test_tester_exits_even_if_window_cleanup_raises(self):
        instance = Mock(is_primary=True, error=None)
        root = Mock()
        tester = Mock()
        tester.close.side_effect = OSError("renderer cleanup failed")

        def run_close_callback():
            event, callback = tester.window.protocol.call_args.args
            self.assertEqual(event, "WM_DELETE_WINDOW")
            # Tk reports callback exceptions and relies on quit to exit mainloop.
            with self.assertRaisesRegex(OSError, "renderer cleanup failed"):
                callback()
            root.quit.assert_called_once_with()
            root.destroy.assert_called_once_with()

        root.mainloop.side_effect = run_close_callback
        with (
            patch.object(gamepad_test_app.tk, "Tk", return_value=root),
            patch.object(gamepad_test_app.sys, "argv", ["GamepadTester"]),
            patch.object(gamepad_test_app, "configure_windows_taskbar_identity"),
            patch.object(gamepad_test_app, "apply_root_icon"),
            patch.object(gamepad_test_app, "GamepadTestHost"),
            patch.object(gamepad_test_app, "GamepadTestStartupOverlay"),
            patch.object(gamepad_test_app, "GamepadTestWindow", return_value=tester),
        ):
            self.assertTrue(gamepad_test_app.main(instance=instance))

        instance.close.assert_called_once_with()

    def test_tk_window_is_restored_focused_and_not_left_topmost(self):
        window = Mock()
        window.winfo_exists.return_value = True
        window.state.return_value = "iconic"
        scheduled = []
        window.after.side_effect = lambda _delay, callback: scheduled.append(
            callback
        )

        with patch("single_instance.os.name", "posix"):
            self.assertTrue(activate_tk_window(window))

        window.deiconify.assert_called_once_with()
        window.attributes.assert_called_once_with("-topmost", True)
        window.lift.assert_called_once_with()
        window.focus_force.assert_called_once_with()
        self.assertEqual(len(scheduled), 1)
        scheduled[0]()
        self.assertEqual(
            window.attributes.call_args_list[-1].args,
            ("-topmost", False),
        )


if __name__ == "__main__":
    unittest.main()
