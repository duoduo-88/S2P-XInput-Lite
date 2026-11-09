import ctypes
import sys
import unittest
from unittest.mock import patch


class FakeDisplay:
    device = r"\\.\DISPLAY2"
    handle = 53
    origin = (-2560, 100)
    bounds = (-2560, 100, 0, 1540)

    def __init__(self, current, modes):
        self.current = current
        self.modes = modes
        self.changes = []
        self.rejected_refreshes = set()
        self.fail_read_after_apply = False
        self.apply_failure = None
        self.reject_restore = False
        self.active_origin = None
        self.active_bounds = None

    def monitor(self, hwnd):
        if hwnd != 42:
            raise OSError("Unknown window")
        return self.handle, *self.monitor_info(self.handle)

    def monitor_info(self, handle):
        if handle != self.handle:
            raise OSError("Wrong monitor handle")
        if self.active_bounds is not None and self.changes and self.changes[-1][1] == 4:
            return self.device, self.active_bounds
        return self.device, self.bounds

    def read_mode(self, device, index):
        if device != self.device:
            raise OSError("Wrong monitor")
        if index == 0xFFFFFFFF:
            if self.fail_read_after_apply and self.changes and self.changes[-1][1] == 4:
                return None
            return self.current
        return self.modes[index] if index < len(self.modes) else None

    def change(self, device, mode, flags):
        self.changes.append((device, flags, bytes(mode)))
        if flags == 2 and mode.dmDisplayFrequency in self.rejected_refreshes:
            return -2
        if flags == 4 and self.apply_failure is not None:
            if isinstance(self.apply_failure, OSError):
                raise self.apply_failure
            return self.apply_failure
        if flags == 0 and self.reject_restore:
            return -1
        if flags != 2:
            self.current = type(mode).from_buffer_copy(mode)
        if flags == 4 and self.active_origin is not None:
            self.current.dmFields |= 0x20
            self.current.dmPosition.x, self.current.dmPosition.y = self.active_origin
        return 0


def make_mode(width, height, hz=144, *, bits=32, rotation=0, interlaced=False):
    from gamepad_aim_display import _DevMode

    mode = _DevMode()
    mode.dmSize = ctypes.sizeof(mode)
    mode.dmFields = 0x007C00A0
    mode.dmPelsWidth = width
    mode.dmPelsHeight = height
    mode.dmBitsPerPel = bits
    mode.dmDisplayFrequency = hz
    mode.dmDisplayOrientation = rotation
    mode.dmDisplayFlags = 2 if interlaced else 0
    mode.dmPosition.x, mode.dmPosition.y = FakeDisplay.origin
    return mode


class TemporaryDisplayModeTests(unittest.TestCase):
    def setUp(self):
        import gamepad_aim_display as display

        self.display = display
        self.current = make_mode(2560, 1440)
        self.fake = FakeDisplay(self.current, [])
        patcher = patch.object(display, "_WindowsDisplay", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lists_distinct_sizes_with_current_color_depth_and_orientation(self):
        self.fake.modes = [
            make_mode(2176, 1224),
            make_mode(2176, 1224, 60),
            make_mode(1920, 1080),
            make_mode(3840, 2160, 30),
            make_mode(1800, 1000, bits=16),
            make_mode(1440, 2560, rotation=1),
        ]
        self.fake.rejected_refreshes = {30}
        self.assertEqual(
            self.display.available_resolutions(42), ("2560x1440", "2176x1224", "1920x1080")
        )
        self.assertTrue(self.fake.changes)
        self.assertTrue(all(flags == 2 for _, flags, _ in self.fake.changes))
        self.assertEqual(bytes(self.fake.current), bytes(self.current))

    def test_current_resolution_does_not_write_a_mode(self):
        self.assertIsNone(self.display.activate_fullscreen_mode(42, (2560, 1440)))
        self.assertEqual(self.fake.changes, [])

    def test_fullscreen_monitor_bounds_preserve_negative_desktop_coordinates(self):
        self.assertEqual(self.display.fullscreen_monitor_bounds(42), (-2560, 100, 0, 1540))
        self.assertEqual(self.fake.changes, [])

    def test_validates_best_progressive_modes_and_restores_captured_mode(self):
        preferred = make_mode(2176, 1224, 144)
        selected = make_mode(2176, 1224, 120)
        self.fake.modes = [
            make_mode(2176, 1224, 144, interlaced=True),
            selected,
            make_mode(2176, 1224, 60),
            preferred,
        ]
        self.fake.rejected_refreshes = {144}
        self.fake.active_origin = (-2176, 100)
        self.fake.active_bounds = (-1450, 100, 0, 916)

        session = self.display.activate_fullscreen_mode(42, (2176, 1224))
        self.addCleanup(session.close)
        self.assertEqual(session.bounds, (-1450, 100, 0, 916))
        session.close()
        session.close()

        calls = [
            (device, flags, self.display._DevMode.from_buffer_copy(raw))
            for device, flags, raw in self.fake.changes
        ]
        self.assertEqual(
            [(device, flags, mode.dmDisplayFrequency) for device, flags, mode in calls],
            [
                (r"\\.\DISPLAY2", 2, 144),
                (r"\\.\DISPLAY2", 2, 120),
                (r"\\.\DISPLAY2", 4, 120),
                (r"\\.\DISPLAY2", 0, 144),
            ],
        )
        self.assertEqual(bytes(calls[-1][2]), bytes(self.current))

    def test_unsupported_modes_never_apply_or_fall_back_to_other_sizes(self):
        self.fake.modes = [make_mode(1920, 1080), make_mode(2176, 1224, 60)]
        self.fake.rejected_refreshes = {60}
        with self.assertRaises(OSError):
            self.display.activate_fullscreen_mode(42, (2176, 1224))
        self.assertEqual([flags for _, flags, _ in self.fake.changes], [2])

    def test_verification_failure_after_apply_restores_the_original_mode(self):
        self.fake.modes = [make_mode(2176, 1224)]
        self.fake.fail_read_after_apply = True
        with self.assertRaises(OSError):
            self.display.activate_fullscreen_mode(42, (2176, 1224))
        self.assertEqual([flags for _, flags, _ in self.fake.changes], [2, 4, 0])
        self.assertEqual(bytes(self.fake.current), bytes(self.current))

    def test_rejected_apply_does_not_block_future_fullscreen_attempts(self):
        self.fake.modes = [make_mode(2176, 1224)]
        for failure in (-2, PermissionError("Display mode change denied")):
            with self.subTest(failure=failure):
                self.fake.changes.clear()
                self.fake.apply_failure = failure
                self.fake.reject_restore = True
                try:
                    with self.assertRaises(OSError) as error:
                        self.display.activate_fullscreen_mode(42, (2176, 1224))
                    self.assertIsNone(self.display.activate_fullscreen_mode(42, (2560, 1440)))
                    self.assertEqual([flags for _, flags, _ in self.fake.changes], [2, 4])
                    if isinstance(failure, OSError):
                        self.assertIs(error.exception, failure)
                    else:
                        self.assertIn("Cannot apply", str(error.exception))
                finally:
                    self.fake.reject_restore = False
                    leaked = self.display._ACTIVE_MODES.get(self.fake.device)
                    if leaked is not None:
                        leaked.close()

    def test_retries_pending_restore_before_a_new_fullscreen_attempt(self):
        self.fake.modes = [make_mode(2176, 1224)]
        self.fake.fail_read_after_apply = True
        self.fake.reject_restore = True
        try:
            with self.assertRaises(OSError):
                self.display.activate_fullscreen_mode(42, (2176, 1224))
            self.fake.reject_restore = False
            self.assertIsNone(self.display.activate_fullscreen_mode(42, (2560, 1440)))
            self.assertEqual(bytes(self.fake.current), bytes(self.current))
        finally:
            self.fake.reject_restore = False
            leaked = self.display._ACTIVE_MODES.get(self.fake.device)
            if leaked is not None:
                leaked.close()

    def test_rejects_replacement_of_an_active_fullscreen_mode(self):
        self.fake.modes = [make_mode(2176, 1224)]
        session = self.display.activate_fullscreen_mode(42, (2176, 1224))
        self.addCleanup(session.close)
        with self.assertRaises(OSError):
            self.display.activate_fullscreen_mode(42, (2560, 1440))
        self.assertEqual(self.fake.current.size, (2176, 1224))

    def test_rejects_invalid_dimensions_as_an_os_error(self):
        for size in ((0, 1440), (2560,), (2560, "1440")):
            with self.subTest(size=size), self.assertRaises(OSError):
                self.display.activate_fullscreen_mode(42, size)
        self.assertEqual(self.fake.changes, [])

    def test_failed_restore_remains_registered_for_exit(self):
        self.fake.modes = [make_mode(2176, 1224)]
        with (
            patch.object(self.display.atexit, "register") as register,
            patch.object(self.display.atexit, "unregister") as unregister,
        ):
            session = self.display.activate_fullscreen_mode(42, (2176, 1224))
            self.addCleanup(session.close)
            self.fake.reject_restore = True
            try:
                with self.assertRaises(OSError):
                    session.close()
                unregister.assert_not_called()
            finally:
                self.fake.reject_restore = False
            register.call_args.args[0]()
            unregister.assert_called_once_with(session.close)
        self.assertEqual(bytes(self.fake.current), bytes(self.current))


@unittest.skipUnless(sys.platform == "win32", "Display modes require Windows")
class NativeDisplayModeTests(unittest.TestCase):
    def test_enumerates_and_validates_modes_without_applying_a_change(self):
        from ctypes import wintypes

        from gamepad_aim_display import _WindowsDisplay, _compatible_modes, available_resolutions

        get_desktop = ctypes.WinDLL("user32").GetDesktopWindow
        get_desktop.argtypes = ()
        get_desktop.restype = wintypes.HWND
        hwnd = get_desktop()
        api = _WindowsDisplay()
        _, device, _bounds = api.monitor(hwnd)
        current = api.read_mode(device, 0xFFFFFFFF)
        self.assertIsNotNone(current)
        self.assertIn(f"{current.dmPelsWidth}x{current.dmPelsHeight}", available_resolutions(hwnd))
        try:
            self.assertEqual(api.change(device, current, 2), 0)  # CDS_TEST only
        except OSError as error:
            if error.winerror == 5:  # ERROR_ACCESS_DENIED in a restricted Windows process
                self.skipTest(str(error))
            raise

        alternatives = [
            mode for mode in _compatible_modes(api, device, current) if mode.size != current.size
        ]
        if alternatives:
            for mode in alternatives:
                mode.dmFields &= ~0x20  # Keep the desktop arrangement.
            self.assertTrue(any(api.change(device, mode, 2) == 0 for mode in alternatives))
        self.assertEqual(bytes(api.read_mode(device, 0xFFFFFFFF)), bytes(current))
