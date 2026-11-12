import ctypes
import sys
import unittest
from ctypes import wintypes
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "win32", "Direct2D requires Windows")
class Direct2DAimRendererTests(unittest.TestCase):
    def test_draws_complete_frames_to_a_native_window_and_resizes(self):
        from gamepad_aim_gpu import Direct2DAimRenderer

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.CreateWindowExW.argtypes = (
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        )
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = (wintypes.HWND,)
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.MoveWindow.argtypes = (wintypes.HWND, *([ctypes.c_int] * 4), wintypes.BOOL)
        user32.MoveWindow.restype = wintypes.BOOL
        hwnd = user32.CreateWindowExW(
            0,
            "Static",
            "Aim renderer test",
            0x80000000,
            0,
            0,
            160,
            120,
            None,
            None,
            None,
            None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.addCleanup(user32.DestroyWindow, hwnd)

        try:
            renderer = Direct2DAimRenderer(hwnd, 160, 120)
        except OSError as error:
            hresult = getattr(error, "winerror", 0) & 0xFFFFFFFF
            if hresult in {
                0x887A0004,  # DXGI_ERROR_UNSUPPORTED
                0x88990003,  # D2DERR_UNSUPPORTED_OPERATION
                0x88990009,  # D2DERR_DISPLAY_FORMAT_NOT_SUPPORTED
                0x8899000B,  # D2DERR_NO_HARDWARE_DEVICE
                0x88990026,  # D2DERR_INSUFFICIENT_DEVICE_CAPABILITIES
            }:
                self.skipTest(f"No Direct2D hardware target available: {error}")
            raise
        self.addCleanup(renderer.close)

        ellipses = []
        fill_ellipse = renderer._fill_ellipse

        def record_ellipse(ellipse, brush):
            ellipses.append((ellipse._obj.radius_x, brush))
            fill_ellipse(ellipse, brush)

        renderer._fill_ellipse = record_ellipse
        strokes = []
        draw_line = renderer._draw_line

        def record_line(start, end, brush, width, style):
            strokes.append((start.x, start.y, width))
            draw_line(start, end, brush, width, style)

        renderer._draw_line = record_line
        triangle = (ctypes.c_float * 6)(0, 0, 159, 0, 159, 119)
        room = (("#123456", triangle, 1),)
        overlays = ((5, 70, 155, 115, "#101825", 0.8),)
        labels = (
            (8, 70, 150, 87, "瞄準", "#ffffff", 14, True, "left"),
            (8, 87, 150, 105, "命中 🎯", "#ffffff", 14, False, "center"),
            (8, 105, 150, 120, "1/10", "#ffffff", 13, False, "right"),
        )
        renderer.draw(
            160,
            120,
            room=room,
            balls=((40, 35, 8, "#123456"), (62, 36, 6, "#abcdef")),
            reticle=(80, 60, 5, "#ffffff"),
            fragments=((50, 52, 3, "#abcdef"),),
            overlays=overlays,
            labels=labels,
        )
        self.assertEqual([width for _, _, width in strokes], [2, 2])
        self.assertEqual([radius for radius, _ in ellipses], [8, 7, 6, 5])
        self.assertNotEqual(ellipses[0][1], ellipses[2][1])
        self.assertNotEqual(ellipses[0][1], ellipses[1][1])
        self.assertNotEqual(ellipses[2][1], ellipses[3][1])
        with (
            patch.object(renderer, "_brush", wraps=renderer._brush) as brush,
            patch.object(renderer, "_create_layout", wraps=renderer._create_layout) as create_layout,
            patch.object(renderer, "_create_mesh", wraps=renderer._create_mesh) as create_mesh,
        ):
            renderer.draw(160, 120, room=room, overlays=overlays, labels=labels)
            brush.assert_not_called()
            create_layout.assert_not_called()
            create_mesh.assert_not_called()

            moved = (ctypes.c_float * 6)(2.25, 4.75, 159, 0, 159, 119)
            moved_room = (("#123456", moved, 1),)
            renderer.draw(160, 120, room=moved_room, overlays=overlays, labels=labels)
            create_mesh.assert_called_once()
            brush.assert_called_once_with("#123456")

            changed = (*labels[:2], (8, 105, 150, 120, "2/10 🎯", "#ffffff", 13, False, "right"))
            renderer.draw(160, 120, room=moved_room, overlays=overlays, labels=changed)
            create_layout.assert_called_once()
            self.assertEqual(create_layout.call_args.args[:2], ("2/10 🎯", 7))
        ellipses.clear()
        renderer.draw(160, 120, balls=((40, 35, 8, "#123456"),), ball_outlines=False)
        self.assertEqual([radius for radius, _ in ellipses], [8])
        self.assertFalse(renderer._room_meshes)
        self.assertTrue(user32.MoveWindow(hwnd, 0, 0, 220, 160, False))
        renderer.draw(220, 160)
        self.assertFalse(renderer._layouts)
        renderer.close()
        renderer.close()
        with self.assertRaises(OSError):
            renderer.draw(220, 160)

    def test_rejects_a_missing_window(self):
        from gamepad_aim_gpu import Direct2DAimRenderer

        with self.assertRaises(OSError):
            Direct2DAimRenderer(0, 160, 120)
