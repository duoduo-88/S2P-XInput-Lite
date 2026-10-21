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

    def test_duplicate_tester_notifies_existing_window_without_tk(self):
        instance = Mock(is_primary=False)

        with patch.object(gamepad_test_app.tk, "Tk") as tk_root:
            self.assertFalse(gamepad_test_app.main(instance=instance))

        tk_root.assert_not_called()
        instance.notify_existing.assert_called_once_with()
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
