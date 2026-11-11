import tkinter as tk
from tkinter import font as tkfont
import unittest


class AimGpuOverlayTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.canvas = tk.Canvas(self.root, highlightthickness=0)

    def test_hidden_panels_and_unfilled_button_faces_are_omitted(self):
        from gamepad_aim_gpu_overlay import capture_overlay

        image = tk.PhotoImage(master=self.root, width=80, height=30)
        panel = self.canvas.create_image(10, 15, anchor="nw", image=image)
        hidden_panel = self.canvas.create_image(0, 0, image=image, state="hidden")
        idle_face = self.canvas.create_rectangle(11, 17, 30, 41, fill="", outline="")
        hover_face = self.canvas.create_rectangle(
            40.5, 18.5, 78.5, 41.5, fill="#274863", outline="",
        )

        self.assertFalse(self.canvas.winfo_ismapped())
        overlays, labels = capture_overlay(
            self.canvas, (panel, hidden_panel, idle_face, hover_face),
        )

        self.assertEqual(overlays, (
            (10, 15, 90, 45, "#0d1724", 184 / 255),
            (40.5, 18.5, 78.5, 41.5, "#274863", 1.0),
        ))
        self.assertEqual(labels, ())

    def test_anchored_labels_omit_horizontal_bbox_padding(self):
        from gamepad_aim_gpu_overlay import capture_overlay

        text = "測試完成"
        actual_font = tkfont.nametofont("TkDefaultFont", root=self.canvas)
        pixel_font = tkfont.Font(root=self.canvas, family="Segoe UI", size=-14, weight="bold")
        right = self.canvas.create_text(
            100, 20, anchor="ne", text=text, fill="#d0dcec", font=actual_font,
        )
        heading = self.canvas.create_text(200, 80, text=text, fill="white", font=pixel_font)
        left = self.canvas.create_text(
            30, 120, anchor="nw", text="Start", fill="#edf4fc", font=("Segoe UI", -14),
        )
        hint = self.canvas.create_text(
            200, 155, anchor="n", text=text, fill="#d0dcec", font=actual_font,
        )
        blank = self.canvas.create_text(0, 0, text="  ")
        hidden = self.canvas.create_text(0, 0, text="hidden", state="hidden")

        overlays, labels = capture_overlay(self.canvas, (right, heading, left, hint, blank, hidden))

        self.assertEqual(overlays, ())
        self.assertEqual(len(labels), 4)
        right_label, heading_label, left_label, hint_label = labels
        self.assertEqual(right_label[:4], (
            100 - actual_font.measure(text), 20, 100, 20 + actual_font.metrics("linespace"),
        ))
        self.assertEqual(right_label[4:6], (text, "#d0dcec"))
        self.assertAlmostEqual(
            right_label[6],
            actual_font.actual("size") * self.canvas.winfo_fpixels("1p"),
            delta=0.5,
        )
        self.assertEqual(right_label[7:], (False, "right"))
        self.assertAlmostEqual((heading_label[0] + heading_label[2]) / 2, 200, delta=0.5)
        self.assertAlmostEqual((heading_label[1] + heading_label[3]) / 2, 80, delta=0.5)
        self.assertEqual(heading_label[4:], (text, "#ffffff", 14, True, "center"))
        self.assertEqual(left_label[:2], (30, 120))
        self.assertEqual(left_label[4:], ("Start", "#edf4fc", 14, False, "left"))
        self.assertAlmostEqual((hint_label[0] + hint_label[2]) / 2, 200, delta=0.5)
        self.assertEqual(hint_label[1], 155)
        self.assertEqual(hint_label[8], "center")

    def test_wrap_preserves_tk_line_breaks_and_spacing(self):
        from gamepad_aim_gpu_overlay import capture_overlay

        font = tkfont.Font(root=self.canvas, family="Segoe UI", size=-16)
        line = font.metrics("linespace")
        width = max(font.measure("測試完成"), font.measure("按 Enter "))
        item = self.canvas.create_text(
            200, 30, text="測試完成\n\n按 Enter 再測",
            font=font, anchor="ne", justify="right", width=width, fill="#edf4fc",
        )

        _, labels = capture_overlay(self.canvas, (item,))

        self.assertEqual([label[4] for label in labels], ["測試完成", "按 Enter", "再測"])
        self.assertEqual([label[1] for label in labels], [30, 30 + 2 * line, 30 + 3 * line])
        self.assertTrue(all(label[2] == 200 and label[8] == "right" for label in labels))


if __name__ == "__main__":
    unittest.main()
