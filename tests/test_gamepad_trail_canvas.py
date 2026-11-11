import ctypes
import platform
import sys
import tkinter as tk
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _function(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


@unittest.skipUnless(
    sys.platform == "win32"
    and ctypes.sizeof(ctypes.c_void_p) == 8
    and platform.machine().lower() in ("amd64", "x86_64"),
    "x64 Windows",
)
class TrailCanvasSurfaceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        if self.root.tk.eval("package provide Tk") != "8.6.12":
            self.skipTest("Tk 8.6.12")

        self.root.overrideredirect(True)
        self.root.geometry("64x64+-12000+-12000")
        self.root.attributes("-alpha", 0.0)
        self.root.update_idletasks()
        user = ctypes.WinDLL("user32", winmode=0x00000800)
        ptr, integer = ctypes.c_void_p, ctypes.c_int
        get_parent = _function(user, "GetParent", ptr, ptr)
        wrapper = get_parent(self.root.winfo_id())
        self.assertTrue(wrapper)
        get_style = _function(user, "GetWindowLongPtrW", ctypes.c_ssize_t, ptr, integer)
        set_style = _function(user, "SetWindowLongPtrW", ctypes.c_ssize_t, ptr, integer, ctypes.c_ssize_t)
        set_style(wrapper, -20, get_style(wrapper, -20) | 0x08000000 | 0x00000080)
        self.foreground = _function(user, "GetForegroundWindow", ptr)
        self.canvas = tk.Canvas(self.root, width=32, height=32, background="white")
        self.canvas.pack()

    def test_damage_repaints_exact_pixels_and_restores_transparent_background(self):
        from gamepad_trail_canvas import TrailCanvasSurface

        background = self.canvas.create_line(5, 10, 20, 10, fill="#445566")
        surface = TrailCanvasSurface(self.canvas, 32, 32)
        self.addCleanup(surface.close)
        self.canvas.addtag_withtag("trail_bitmap", surface.item)
        foreground_item = self.canvas.create_oval(8, 8, 13, 13, outline="black")
        self.assertEqual(self.canvas.find_all(), (background, surface.item, foreground_item))
        self.assertEqual(self.canvas.find_withtag("trail_bitmap"), (surface.item,))
        surface.pixels[10 * 32 + 10] = 0xFF123456

        gdi = ctypes.WinDLL("gdi32", winmode=0x00000800)
        get_pixel = _function(gdi, "GetPixel", ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int, ctypes.c_int)
        blend = surface._native.blend
        colors = []

        def observe_pixels(dest, x, y, width, height, source, left, top, *args):
            result = blend(dest, x, y, width, height, source, left, top, *args)
            if left <= 10 and left + width >= 12 and top <= 10 < top + height:
                colors.append((get_pixel(dest, x + 10 - left, y + 10 - top),
                               get_pixel(dest, x + 11 - left, y + 10 - top)))
            return result

        original_foreground = self.foreground()
        with patch.object(surface._native, "blend", observe_pixels):
            self.root.deiconify()
            self.root.update_idletasks()
            self.assertTrue(self.canvas.winfo_ismapped())
            self.assertEqual(self.foreground(), original_foreground)
            self.assertEqual(colors[-1], (0x563412, 0x665544))

            surface.pixels[10 * 32 + 10] = 0
            surface.invalidate(10, 10, 12, 11)
            self.root.update_idletasks()
            self.assertEqual(colors[-1], (0x665544, 0x665544))
        self.assertFalse(surface.failed)

    def test_item_deletion_and_canvas_destruction_release_surfaces(self):
        from gamepad_trail_canvas import TrailCanvasSurface

        first = TrailCanvasSurface(self.canvas, 32, 32)
        self.addCleanup(first.close)
        first_pixels = first.pixels
        self.canvas.delete("all")
        self.assertTrue(first.failed)
        first.invalidate(0, 0, 1, 1)
        first.close()
        first_pixels[0] = 0xFF00FF00
        self.assertEqual(first_pixels[0], 0xFF00FF00)

        second = TrailCanvasSurface(self.canvas, 32, 32)
        self.addCleanup(second.close)
        self.assertIn(second.item, self.canvas.find_all())
        self.canvas.destroy()
        self.assertTrue(second.failed)

    def test_wrong_thread_cannot_create_or_update_native_item(self):
        from gamepad_trail_canvas import TrailCanvasSurface

        surface = TrailCanvasSurface(self.canvas, 32, 32)
        self.addCleanup(surface.close)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(RuntimeError):
                pool.submit(TrailCanvasSurface, self.canvas, 32, 32).result(timeout=5)
            with self.assertRaises(RuntimeError):
                pool.submit(surface.invalidate, 0, 0, 1, 1).result(timeout=5)

    def test_failed_native_draw_can_be_removed_for_fallback(self):
        from gamepad_trail_canvas import TrailCanvasSurface

        surface = TrailCanvasSurface(self.canvas, 32, 32)
        self.addCleanup(surface.close)
        with patch.object(surface._native, "blend", return_value=0):
            self.root.deiconify()
            self.root.update_idletasks()
        self.assertTrue(surface.failed)
        surface.close()
        self.assertNotIn(surface.item, self.canvas.find_all())


if __name__ == "__main__":
    unittest.main()
