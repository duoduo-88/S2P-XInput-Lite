import tempfile
import tkinter as tk
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from tkinter import ttk
from unittest.mock import patch


class AimSettingsTests(unittest.TestCase):
    def test_centered_dialog_restores_defaults_and_saves_display_choices(self):
        from gamepad_aim_settings import AimSettings, show_aim_settings

        try:
            owner = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(owner.destroy)
        owner.attributes("-alpha", 0)
        owner.geometry("700x650+170+120")
        owner.update()
        saved = []
        supported = ("1280x720", "1600x900", "1706x960", "1920x1080")
        with patch("gamepad_aim_settings.available_resolutions", return_value=supported):
            dialog = show_aim_settings(
                owner, str, AimSettings(
                    target_count=37, target_color="#ff0000", depth=91, sound=False,
                    window_size="960x540", fullscreen=True, fullscreen_size="1920x1080",
                ),
                saved.append,
            )
        owner.update()
        body = dialog.winfo_children()[0]
        seed_input = body.grid_slaves(row=3, column=1)[0]
        randomize = body.grid_slaves(row=3, column=2)[0]
        self.assertEqual(randomize.cget("text"), "↻")
        randomize.invoke()
        self.assertGreater(int(seed_input.get()), 0)
        self.assertEqual(dialog.title(), "測試設定")
        controls = [
            child for container in body.winfo_children()
            if isinstance(container, ttk.Frame) for child in container.winfo_children()
        ]
        size_picker = next(child for child in controls if isinstance(child, ttk.Combobox))
        fullscreen = next(child for child in controls
                          if isinstance(child, ttk.Checkbutton) and child.cget("text") == "全螢幕")
        shading = next(child for child in body.winfo_children()
                       if isinstance(child, ttk.Checkbutton) and child.cget("text") == "加深遠近效果")
        self.assertTrue(shading.instate(["selected"]))
        shading.invoke()
        self.assertEqual(size_picker.cget("values"), ("自動（最高）", *supported))
        self.assertEqual(size_picker.get(), "1920x1080")
        self.assertTrue(size_picker.instate(["readonly", "!disabled"]))
        fullscreen.invoke()
        self.assertTrue(size_picker.instate(["readonly", "!disabled"]))
        self.assertIn("960x540", size_picker.cget("values"))
        self.assertNotIn("2560x1440", size_picker.cget("values"))
        self.assertIn("自動（最高）", size_picker.cget("values"))
        self.assertEqual(size_picker.get(), "960x540")
        fullscreen.invoke()
        self.assertTrue(size_picker.instate(["readonly", "!disabled"]))
        self.assertEqual(size_picker.get(), "1920x1080")
        for dimension, origin in (("width", "rootx"), ("height", "rooty")):
            center = lambda widget: getattr(widget, f"winfo_{origin}")() + (
                getattr(widget, f"winfo_{dimension}")() / 2
            )
            self.assertLess(abs(center(dialog) - center(owner)), 25)
        buttons = {child.cget("text"): child for child in controls if isinstance(child, ttk.Button)}
        size_picker.set("960x540")
        buttons["儲存"].invoke()
        self.assertEqual(saved, [])
        self.assertTrue(dialog.winfo_exists())
        buttons["恢復預設"].invoke()
        self.assertEqual(saved, [])
        self.assertTrue(shading.instate(["selected"]))
        self.assertEqual(size_picker.get(), "自動（最高）")
        self.assertTrue(size_picker.instate(["readonly", "!disabled"]))
        self.assertTrue(fullscreen.instate(["selected"]))
        size_picker.set("1706x960")
        shading.invoke()
        buttons["儲存"].invoke()
        self.assertEqual(saved, [AimSettings(fullscreen=True, fullscreen_size="1706x960", depth_shading=False)])

        with patch("gamepad_aim_settings.available_resolutions", side_effect=OSError):
            dialog = show_aim_settings(owner, str, AimSettings(fullscreen=True), saved.append)
        controls = (
            child for frame in dialog.winfo_children()[0].winfo_children()
            if isinstance(frame, ttk.Frame) for child in frame.winfo_children()
        )
        size_picker = next(child for child in controls if isinstance(child, ttk.Combobox))
        self.assertEqual(size_picker.cget("values"), ("自動（最高）",))

    def test_history_basis_only_uses_settings_that_apply_to_the_mode(self):
        from gamepad_aim_settings import AimSettings

        settings = AimSettings(target_count=24, target_seconds=40, tracking_seconds=55,
                               seed=17, depth=80)
        basis = settings.basis("tracking", (700, 500))
        self.assertEqual((basis["targets"], basis["seconds"], basis["depth"]), (0, 55, 0))
        self.assertEqual(basis["seed"], 17)
        self.assertEqual(basis["viewport"], [700, 500])
        self.assertEqual(
            AimSettings(window_size="1920x1080", fullscreen=True).basis("tracking", (700, 500)),
            AimSettings().basis("tracking", (700, 500)),
        )
        point = settings.basis("shooting", (700, 500))
        self.assertEqual((point["targets"], point["seconds"], point["depth"]), (24, 40, 80))
        self.assertNotEqual(point, settings.basis("shooting", (1920, 1080)))
        self.assertNotEqual(point, settings.basis("cursor-stick", (700, 500)))
        custom = AimSettings(reticle_size=26, target_size_2d=14)
        self.assertEqual(custom.basis("cursor-stick", (700, 500))["reticle_size"], 26)
        self.assertEqual(custom.basis("cursor-mouse", (700, 500))["target_size_2d"], 14)
        self.assertNotIn("reticle_size", custom.basis("cursor-mouse", (700, 500)))
        self.assertNotIn("target_size_2d", custom.basis("shooting", (700, 500)))

    def test_depth_shading_keeps_the_comparison_basis_and_new_3d_hit_order_separates_it(self):
        from gamepad_aim_history import AimHistory
        from gamepad_aim_settings import AimSettings

        current = AimSettings().basis("timed-shooting", (800, 450))
        unshaded = AimSettings(depth_shading=False).basis("timed-shooting", (800, 450))
        self.assertEqual(current, unshaded)
        history = AimHistory(None)
        history.add(dict(current, version=4), {"hits": 5})
        self.assertFalse(history.has_matching_record(current))

    def test_unified_2d_bases_use_drill_durations_and_target_size(self):
        from gamepad_aim_settings import AimSettings

        settings = AimSettings(
            target_count=24, target_seconds=40, tracking_seconds=55, timed_shooting_seconds=65,
            target_size_2d=14, depth=80, horizontal_spread=72,
        )
        for mode, count, duration in (
            ("cursor-tracking", 0, 55),
            ("cursor-shooting", 24, 40),
            ("timed-cursor", 0, 65),
        ):
            with self.subTest(mode=mode):
                basis = settings.basis(mode, (700, 500))
                self.assertEqual((basis["mode"], basis["targets"], basis["seconds"]),
                                 (mode, count, duration))
                self.assertEqual((basis["target_size_2d"], basis["depth"]), (14, 0))
                self.assertNotIn("horizontal_spread", basis)

    def test_settings_round_trip_and_ignore_invalid_fields(self):
        from gamepad_aim_settings import AimSettings

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "aim.json"
            settings = AimSettings(
                target_count=16, depth=82, target_color="#ff8800", sound=False, depth_shading=False,
                window_size="1706x960", fullscreen=True,
                reticle_on_target_color="#ff4775", target_on_target_color="#ddf095",
            )
            settings.save(path)
            self.assertEqual(AimSettings.load(path), settings)
            path.write_text(
                '{"target_count": -1, "reticle_color": "invalid", "depth": 40, '
                '"window_size": "999x0", "fullscreen": 1}',
                encoding="utf-8",
            )
            self.assertEqual(AimSettings.load(path), AimSettings(depth=40))
            path.write_text('{"seed": 54}', encoding="utf-8")
            legacy = AimSettings.load(path)
            self.assertEqual((legacy.seed, legacy.window_size, legacy.fullscreen),
                             (54, "", True))
            path.write_text('{"fullscreen": false, "window_size": "960x540"}', encoding="utf-8")
            self.assertEqual(AimSettings.load(path), AimSettings(fullscreen=False, window_size="960x540"))
            path.write_text('{"fullscreen": true, "window_size": "1920x1080"}', encoding="utf-8")
            self.assertEqual(AimSettings.load(path).fullscreen_size, "1920x1080")

    def test_settings_load_serializes_its_read_with_save(self):
        from gamepad_aim_settings import AimSettings

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "aim.json"
            original = AimSettings(target_count=12)
            replacement = AimSettings(target_count=24)
            original.save(path)
            reader_open, release_reader, writer_started = Event(), Event(), Event()

            def read_text(file, *, encoding):
                with file.open(encoding=encoding) as handle:
                    reader_open.set()
                    if not release_reader.wait(3):
                        raise RuntimeError("Test did not release the settings read")
                    return handle.read()

            def save():
                writer_started.set()
                replacement.save(path)

            with patch.object(Path, "read_text", read_text), ThreadPoolExecutor() as pool:
                reader = pool.submit(AimSettings.load, path)
                try:
                    self.assertTrue(reader_open.wait(2))
                    writer = pool.submit(save)
                    self.assertTrue(writer_started.wait(2))
                    with self.assertRaises(TimeoutError):
                        writer.result(timeout=0.15)
                finally:
                    release_reader.set()
                self.assertEqual(reader.result(timeout=2), original)
                writer.result(timeout=2)
            self.assertEqual(AimSettings.load(path), replacement)
