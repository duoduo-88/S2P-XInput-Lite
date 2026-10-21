import os
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config_gui
import system_tray


class SystemTrayBackendTests(unittest.TestCase):
    def test_custom_icon_contains_common_windows_tray_sizes(self):
        icon_data = (ROOT / "image" / "icon.ico").read_bytes()
        reserved, image_type, image_count = struct.unpack_from(
            "<HHH", icon_data
        )
        self.assertEqual((reserved, image_type), (0, 1))
        sizes = []
        for index in range(image_count):
            width, height = struct.unpack_from(
                "<BB", icon_data, 6 + (index * 16)
            )
            sizes.append((width or 256, height or 256))
        self.assertTrue({16, 20, 24, 32, 40, 48, 256}.issubset(
            {width for width, height in sizes if width == height}
        ))

    def test_actions_are_nonblocking_and_fifo(self):
        tray = system_tray.SystemTrayIcon("Test")
        self.assertIsNone(tray.get_action())

        tray._actions.put("show")
        tray._actions.put("exit")

        self.assertEqual(tray.get_action(), "show")
        self.assertEqual(tray.get_action(), "exit")
        self.assertIsNone(tray.get_action())

    def test_menu_labels_can_change_without_restarting_thread(self):
        tray = system_tray.SystemTrayIcon("Test")

        tray.update_labels("顯示設定", "結束程式")

        self.assertEqual(tray._show_label, "顯示設定")
        self.assertEqual(tray._exit_label, "結束程式")

    @unittest.skipUnless(os.name == "nt", "native tray requires Windows")
    def test_native_message_window_starts_and_stops_without_showing_icon(self):
        tray = system_tray.SystemTrayIcon(
            "S2P tray test",
            icon_path=ROOT / "image" / "icon.ico",
        )
        try:
            self.assertTrue(tray.start())
            self.assertTrue(tray.available)
            self.assertFalse(tray.visible)
            self.assertTrue(tray._owned_icons)
        finally:
            tray.stop()
        self.assertFalse(tray.available)


class ConfigGuiTrayTests(unittest.TestCase):
    @staticmethod
    def gui():
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = Mock()
        gui._hidden_to_system_tray = False
        gui._system_tray_poll_job = None
        gui._system_tray_minimize_job = None
        gui._root_restore_pending = False
        gui._hide_restore_splash = Mock()
        gui._begin_root_restore_cycle = Mock(return_value=1)
        return gui

    def test_minimize_hides_only_after_tray_icon_is_confirmed(self):
        gui = self.gui()
        gui.main_process = object()
        gui._system_tray = Mock(available=True)
        gui._system_tray.show.return_value = True
        modal = Mock()
        gui.root.grab_current.return_value = modal

        self.assertTrue(gui._minimize_to_system_tray())

        self.assertTrue(gui._hidden_to_system_tray)
        self.assertTrue(gui._root_restore_pending)
        gui._system_tray.show.assert_called_once_with()
        modal.grab_release.assert_called_once_with()
        gui.root.withdraw.assert_called_once_with()
        self.assertIsNotNone(gui.main_process)

    def test_failed_tray_icon_keeps_settings_in_taskbar(self):
        gui = self.gui()
        gui._system_tray = Mock(available=True)
        gui._system_tray.show.return_value = False

        self.assertFalse(gui._minimize_to_system_tray())

        self.assertFalse(gui._hidden_to_system_tray)
        gui.root.withdraw.assert_not_called()

    def test_lost_tray_backend_schedules_emergency_restore(self):
        gui = self.gui()
        gui._hidden_to_system_tray = True
        gui._system_tray = Mock(available=False)

        gui._schedule_system_tray_poll()

        gui.root.after_idle.assert_called_once_with(
            gui.restore_from_system_tray
        )

    def test_unmap_converts_only_iconic_root_to_tray(self):
        gui = self.gui()
        gui._system_tray = Mock(available=True)
        gui.root.state.return_value = "iconic"
        scheduled = []
        gui.root.after.side_effect = (
            lambda delay, callback: scheduled.append((delay, callback)) or "job"
        )
        gui._minimize_to_system_tray = Mock()

        gui._on_root_unmap()

        self.assertEqual(scheduled[0][0], 30)
        scheduled[0][1]()
        gui._minimize_to_system_tray.assert_called_once_with()

    def test_restore_removes_icon_then_maps_and_activates_root(self):
        gui = self.gui()
        gui._hidden_to_system_tray = True
        gui._system_tray = Mock(available=True)
        scheduled = []
        gui.root.after.side_effect = (
            lambda delay, callback: scheduled.append((delay, callback)) or "job"
        )

        with patch.object(config_gui, "activate_tk_window") as activate:
            self.assertTrue(gui.restore_from_system_tray())
            self.assertFalse(gui._hidden_to_system_tray)
            gui._system_tray.hide.assert_called_once_with()
            gui.root.deiconify.assert_called_once_with()
            self.assertEqual(scheduled[0][0], 75)
            scheduled[0][1]()
            activate.assert_called_once_with(gui.root)

    def test_duplicate_launch_restores_tray_window(self):
        gui = self.gui()
        gui.restore_from_system_tray = Mock(return_value=True)

        with patch.object(config_gui, "activate_tk_window") as activate:
            self.assertTrue(gui.activate_existing_settings())

        activate.assert_not_called()

    def test_tray_exit_restores_before_running_normal_close_flow(self):
        gui = self.gui()
        gui._system_tray = Mock(available=True)
        gui._system_tray.get_action.side_effect = ["exit", None]
        gui.restore_from_system_tray = Mock(return_value=True)
        gui.on_close = Mock()
        gui._schedule_system_tray_poll = Mock()
        scheduled = []
        gui.root.after.side_effect = (
            lambda delay, callback: scheduled.append((delay, callback)) or "job"
        )

        gui._poll_system_tray_actions()

        gui.restore_from_system_tray.assert_called_once_with()
        self.assertEqual(scheduled[0][0], 50)
        scheduled[0][1]()
        gui.on_close.assert_called_once_with()
        gui._schedule_system_tray_poll.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
