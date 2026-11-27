import sys
import tkinter as tk
import unittest
from tkinter import font as tkfont

from dialog_layout import StartupBannerText


def loading_text(dots):
    return "v0.8.3 手把測試程式啟動中" + "." * dots


@unittest.skipUnless(sys.platform == "win32", "The banner font ships with Windows")
class StartupBannerTextTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.scaling = self.root.tk.call("tk", "scaling")
        self.canvas = tk.Canvas(self.root, width=600, height=340)

    def tearDown(self):
        self.root.tk.call("tk", "scaling", self.scaling)
        self.root.destroy()

    def test_every_glyph_uses_the_same_windows_font(self):
        if StartupBannerText.FAMILY not in tkfont.families(self.root):
            self.skipTest("Microsoft JhengHei UI is not installed")
        text = StartupBannerText(self.canvas, 600, 340, loading_text)
        families = {
            self.root.tk.call("font", "actual", text.font, "-family", "--", char)
            for char in loading_text(3) if char != " "
        }
        # Segoe UI left the Chinese glyphs to whichever CJK font the PC had.
        self.assertEqual(families, {StartupBannerText.FAMILY})

    def test_line_keeps_its_left_edge_and_size_while_animating(self):
        text = StartupBannerText(self.canvas, 600, 340, loading_text)
        self.canvas.update_idletasks()
        left, top, right, bottom = self.canvas.bbox(text.item)
        self.assertLessEqual(right, 600 - StartupBannerText.MARGIN_X + 2)
        height = bottom - top
        self.root.tk.call("tk", "scaling", 2.0)
        for dots in (1, 2, 3):
            text.show(dots)
            self.canvas.update_idletasks()
            box = self.canvas.bbox(text.item)
            self.assertEqual(box[0], left)
            self.assertEqual(box[3] - box[1], height)
            self.assertEqual(self.canvas.itemcget(text.item, "text"), loading_text(dots))


if __name__ == "__main__":
    unittest.main()
