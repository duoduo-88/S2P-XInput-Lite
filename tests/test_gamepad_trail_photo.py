import sys
import tkinter as tk
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@unittest.skipUnless(sys.platform == "win32" and tk.TkVersion == 8.6, "Windows Tk 8.6")
class TrailPhotoUploaderTests(unittest.TestCase):
    def setUp(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        self.addCleanup(root.destroy)
        self.photo = tk.PhotoImage(master=root, width=4, height=3)

    def test_upload_replaces_pixels_and_transparency_in_the_destination(self):
        from gamepad_trail_photo import TrailPhotoUploader

        photo = self.photo
        photo.put("#e08040", to=(0, 0, 4, 3))
        scanlines = bytearray(17 * 3)
        scanlines[22:30] = b"\x10\x20\x30\xff\x00\x00\x00\x00"
        scanlines[39:47] = b"\x70\x80\x90\xff\xa0\xb0\xc0\xff"

        uploader = TrailPhotoUploader()
        self.assertTrue(uploader.upload(photo, scanlines, 17, 1, 1, 2, 2, dest_x=2, dest_y=0))
        self.assertEqual(photo.get(2, 0), (16, 32, 48))
        self.assertTrue(photo.transparency_get(3, 0))
        self.assertEqual(photo.get(2, 1), (112, 128, 144))
        self.assertEqual(photo.get(3, 1), (160, 176, 192))
        self.assertEqual(photo.get(1, 1), (224, 128, 64))
        self.assertTrue(uploader.upload(photo, scanlines, 17, 1, 1, 2, 2))
        self.assertEqual(photo.get(1, 1), (16, 32, 48))
        self.assertTrue(photo.transparency_get(2, 1))

    def test_upload_declines_a_buffer_that_ends_before_the_last_row(self):
        from gamepad_trail_photo import TrailPhotoUploader

        uploader = TrailPhotoUploader()
        self.assertFalse(uploader.upload(self.photo, bytearray(29), 17, 1, 1, 2, 2))
        self.assertTrue(self.photo.transparency_get(1, 1))

    def test_upload_declines_to_call_tk_from_another_thread(self):
        from gamepad_trail_photo import TrailPhotoUploader

        uploader = TrailPhotoUploader()
        scanlines = bytearray(b"\x00\x10\x20\x30\xff")
        self.assertTrue(uploader.upload(self.photo, scanlines, 5, 0, 0, 1, 1))
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(uploader.upload, self.photo, bytearray(5), 5, 0, 0, 1, 1)
            self.assertFalse(result.result(timeout=5))
        self.assertEqual(self.photo.get(0, 0), (16, 32, 48))


if __name__ == "__main__":
    unittest.main()
