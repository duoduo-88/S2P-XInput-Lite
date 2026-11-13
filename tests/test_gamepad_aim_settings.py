import tempfile
import tkinter as tk
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from tkinter import ttk
from unittest.mock import patch


class AimSettingsTests(unittest.TestCase):
    def test_narrow_work_area_stacks_panels_without_clipping(self):
        from gamepad_aim_settings import AimSettings, show_aim_settings
        from localization import EN_TEXT

        try:
            owner = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(owner.destroy)
        owner.attributes("-alpha", 0)
        owner.geometry("450x420+0+0")
        owner.update()
        with (patch("gamepad_aim_settings.window_work_area", return_value=(0, 0, 480, 650)),
              patch("gamepad_aim_settings.available_resolutions", return_value=("1280x720",))):
            dialog = show_aim_settings(owner, lambda text: EN_TEXT.get(text, text), AimSettings(), lambda _: None)
        owner.update()

        content, _footer = dialog.winfo_children()
        canvas = next(child for child in content.winfo_children() if isinstance(child, tk.Canvas))
        body = canvas.winfo_children()[0]
        drills = body.grid_slaves(row=0, column=0)[0]
        controls = body.grid_slaves(row=1, column=0)[0]
        self.assertGreater(controls.winfo_rooty(), drills.winfo_rooty())
        self.assertLessEqual(dialog.winfo_rootx() + dialog.winfo_width(), 480)

    def test_short_work_area_scrolls_settings_and_keeps_save_visible(self):
        from gamepad_aim_settings import AimSettings, show_aim_settings

        try:
            owner = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(owner.destroy)
        owner.attributes("-alpha", 0)
        owner.geometry("700x420+20+175")
        owner.update()
        bottom = 300
        saved = []
        with (patch("gamepad_aim_settings.window_work_area", return_value=(0, 0, 1280, bottom), create=True),
              patch("gamepad_aim_settings.available_resolutions", return_value=("1280x720",))):
            dialog = show_aim_settings(owner, str, AimSettings(), saved.append)
        owner.update()

        self.assertLessEqual(dialog.winfo_rooty() + dialog.winfo_height(), bottom)
        content, footer = dialog.winfo_children()
        canvas = next(child for child in content.winfo_children() if isinstance(child, tk.Canvas))
        scrollbar = next(child for child in content.winfo_children() if isinstance(child, ttk.Scrollbar))
        self.assertTrue(scrollbar.winfo_ismapped())
        self.assertLess(canvas.yview()[1], 1.0)
        save = next(child for child in footer.winfo_children()
                    if isinstance(child, ttk.Button) and child.cget("text") == "儲存")
        save_top = save.winfo_rooty()
        body = canvas.winfo_children()[0]
        drills = body.grid_slaves(row=0, column=0)[0]
        target_count = drills.grid_slaves(row=0, column=1)[0]
        target_count.event_generate("<MouseWheel>", delta=-120)
        self.assertGreater(canvas.yview()[0], 0.0)
        self.assertEqual(target_count.get(), "10")
        self.assertEqual(save.winfo_rooty(), save_top)
        self.assertLessEqual(save_top + save.winfo_height(), bottom)
        target_count.set("101")
        save.invoke()
        owner.update()
        self.assertEqual(saved, [])
        self.assertLessEqual(save.winfo_rooty() + save.winfo_height(), bottom)
        target_count.set("10")
        save.invoke()
        self.assertEqual(saved, [AimSettings()])

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
                    mouse_sensitivity=125, auto_input_switch=True,
                    stick_horizontal_speed=300, stick_vertical_speed=180,
                    tracking_arc=360, tracking_speed=150,
                ),
                saved.append,
            )
        owner.update()
        content, footer = dialog.winfo_children()
        canvas = next(child for child in content.winfo_children() if isinstance(child, tk.Canvas))
        body = canvas.winfo_children()[0]
        drills = body.grid_slaves(row=0, column=0)[0]
        controls_panel = body.grid_slaves(row=0, column=1)[0]
        self.assertGreater(controls_panel.winfo_rootx(), drills.winfo_rootx())
        self.assertEqual(controls_panel.winfo_rooty(), drills.winfo_rooty())

        def spinbox(container, label):
            widget = next(
                child for child in container.winfo_children()
                if isinstance(child, ttk.Label) and child.cget("text") == label
            )
            return container.grid_slaves(row=widget.grid_info()["row"], column=1)[0]

        sensitivity = spinbox(controls_panel, "3D 滑鼠感度（%）")
        horizontal_speed = spinbox(controls_panel, "3D 水平視角速度（°/秒）")
        vertical_speed = spinbox(controls_panel, "3D 垂直視角速度（°/秒）")
        self.assertEqual(sensitivity.get(), "125")
        self.assertEqual((horizontal_speed.get(), vertical_speed.get()), ("300", "180"))
        seed_input = spinbox(drills, "出靶種子（0 為固定配置）")
        tracking_arc = spinbox(drills, "3D 跟槍環繞範圍（°）")
        tracking_speed = spinbox(drills, "3D 跟槍靶速（%）")
        self.assertEqual((tracking_arc.get(), tracking_speed.get()), ("360", "150"))
        randomize = drills.grid_slaves(row=3, column=2)[0]
        self.assertEqual(randomize.cget("text"), "↻")
        randomize.invoke()
        self.assertGreater(int(seed_input.get()), 0)
        self.assertEqual(dialog.title(), "測試設定")
        controls = [
            child for container in controls_panel.winfo_children()
            if isinstance(container, ttk.Frame) for child in container.winfo_children()
        ]
        size_picker = next(child for child in controls if isinstance(child, ttk.Combobox))
        fullscreen = next(child for child in controls
                          if isinstance(child, ttk.Checkbutton) and child.cget("text") == "全螢幕")
        shading = next(child for child in controls_panel.winfo_children()
                       if isinstance(child, ttk.Checkbutton) and child.cget("text") == "加深遠近效果")
        auto_switch = next(child for child in controls_panel.winfo_children()
                           if isinstance(child, ttk.Checkbutton) and child.cget("text") == "啟用手把與滑鼠自動銜接")
        self.assertTrue(auto_switch.instate(["selected"]))
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
        buttons = {child.cget("text"): child for child in footer.winfo_children() if isinstance(child, ttk.Button)}
        size_picker.set("960x540")
        buttons["儲存"].invoke()
        self.assertEqual(saved, [])
        self.assertTrue(dialog.winfo_exists())
        buttons["恢復預設"].invoke()
        self.assertEqual(saved, [])
        self.assertEqual(sensitivity.get(), "100")
        self.assertEqual((horizontal_speed.get(), vertical_speed.get()), ("140", "115"))
        self.assertEqual((tracking_arc.get(), tracking_speed.get()), ("0", "100"))
        self.assertTrue(shading.instate(["selected"]))
        self.assertTrue(auto_switch.instate(["!selected"]))
        auto_switch.invoke()
        self.assertEqual(size_picker.get(), "自動（最高）")
        self.assertTrue(size_picker.instate(["readonly", "!disabled"]))
        self.assertTrue(fullscreen.instate(["selected"]))
        size_picker.set("1706x960")
        shading.invoke()
        sensitivity.set("301")
        buttons["儲存"].invoke()
        self.assertEqual(saved, [])
        sensitivity.set("50")
        horizontal_speed.set("0")
        buttons["儲存"].invoke()
        self.assertEqual(saved, [])
        horizontal_speed.set("360")
        vertical_speed.set("90")
        tracking_arc.set("361")
        buttons["儲存"].invoke()
        self.assertEqual(saved, [])
        tracking_arc.set("270")
        tracking_speed.set("601")
        buttons["儲存"].invoke()
        self.assertEqual(saved, [])
        tracking_speed.set("600")
        buttons["儲存"].invoke()
        self.assertEqual(saved, [AimSettings(
            fullscreen=True, fullscreen_size="1706x960", depth_shading=False, mouse_sensitivity=50,
            auto_input_switch=True, stick_horizontal_speed=360, stick_vertical_speed=90,
            tracking_arc=270, tracking_speed=600,
        )])

        with patch("gamepad_aim_settings.available_resolutions", side_effect=OSError):
            dialog = show_aim_settings(owner, str, AimSettings(fullscreen=True), saved.append)
        canvas = next(child for child in dialog.winfo_children()[0].winfo_children() if isinstance(child, tk.Canvas))
        body = canvas.winfo_children()[0]
        controls_panel = body.grid_slaves(row=0, column=1)[0]
        controls = (
            child for frame in controls_panel.winfo_children()
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
        custom = AimSettings(
            reticle_size=26, target_size_2d=14, mouse_sensitivity=50,
            stick_horizontal_speed=360, stick_vertical_speed=90,
        )
        self.assertEqual(custom.basis("cursor-stick", (700, 500))["reticle_size"], 26)
        self.assertEqual(custom.basis("cursor-mouse", (700, 500))["target_size_2d"], 14)
        self.assertNotIn("reticle_size", custom.basis("cursor-mouse", (700, 500)))
        self.assertNotIn("target_size_2d", custom.basis("shooting", (700, 500)))
        for mode in ("tracking", "shooting", "timed-shooting"):
            self.assertNotIn("mouse_sensitivity", AimSettings().basis(mode, (700, 500)))
            self.assertEqual(custom.basis(mode, (700, 500))["mouse_sensitivity"], 50)
            self.assertEqual(custom.basis(mode, (700, 500))["stick_horizontal_speed"], 360)
            self.assertEqual(custom.basis(mode, (700, 500))["stick_vertical_speed"], 90)
        for mode in ("cursor-tracking", "cursor-shooting", "timed-cursor"):
            self.assertNotIn("mouse_sensitivity", custom.basis(mode, (700, 500)))
            self.assertNotIn("stick_horizontal_speed", custom.basis(mode, (700, 500)))
            self.assertNotIn("stick_vertical_speed", custom.basis(mode, (700, 500)))

    def test_depth_shading_keeps_the_comparison_basis_and_new_3d_hit_order_separates_it(self):
        from gamepad_aim_history import AimHistory
        from gamepad_aim_settings import AimSettings

        current = AimSettings().basis("timed-shooting", (800, 450))
        unshaded = AimSettings(depth_shading=False).basis("timed-shooting", (800, 450))
        self.assertEqual(current, unshaded)
        history = AimHistory(None)
        history.add(dict(current, version=4), {"hits": 5})
        self.assertFalse(history.has_matching_record(current))

    def test_3d_history_only_matches_the_same_stick_turn_speed(self):
        from gamepad_aim_history import AimHistory
        from gamepad_aim_settings import AimSettings

        viewport = (800, 450)
        default = AimSettings().basis("tracking", viewport)
        faster = AimSettings(stick_horizontal_speed=360).basis("tracking", viewport)
        history = AimHistory(None)
        history.add(default, {"tracking": 50})
        self.assertTrue(history.has_matching_record(default))
        self.assertFalse(history.has_matching_record(faster))

    def test_only_3d_tracking_history_uses_orbit_settings(self):
        from gamepad_aim_history import AimHistory
        from gamepad_aim_settings import AimSettings

        settings = AimSettings(tracking_arc=360, tracking_speed=200)
        size = (800, 450)
        for mode in ("cursor-tracking", "shooting", "timed-shooting"):
            self.assertEqual(settings.basis(mode, size), AimSettings().basis(mode, size))
        basis = settings.basis("tracking", size)
        self.assertEqual((basis["tracking_arc"], basis["tracking_speed"]), (360, 200))
        history = AimHistory(None)
        history.add(basis, {"tracking": 60})
        self.assertTrue(history.has_matching_record(basis))
        self.assertFalse(history.has_matching_record(AimSettings(tracking_arc=360).basis("tracking", size)))

    def test_full_tracking_arc_history_does_not_match_the_old_continuous_orbit(self):
        from gamepad_aim_history import AimHistory
        from gamepad_aim_settings import AimSettings

        viewport = (800, 450)
        for seed, old_version in ((0, 1), (1, 2)):
            with self.subTest(seed=seed):
                basis = AimSettings(seed=seed, tracking_arc=360).basis("tracking", viewport)
                history = AimHistory(None)
                history.add(dict(basis, version=old_version), {"tracking": 60})
                self.assertFalse(history.has_matching_record(basis))
        self.assertEqual(AimSettings(seed=1, tracking_arc=180).basis("tracking", viewport)["version"], 2)
        self.assertEqual(AimSettings(seed=1).basis("tracking", viewport)["version"], 1)

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
                mouse_sensitivity=50, auto_input_switch=True,
                stick_horizontal_speed=360, stick_vertical_speed=90,
                tracking_arc=360, tracking_speed=600,
            )
            settings.save(path)
            self.assertEqual(AimSettings.load(path), settings)
            path.write_text(
                '{"target_count": -1, "reticle_color": "invalid", "depth": 40, '
                '"window_size": "999x0", "fullscreen": 1, "mouse_sensitivity": 301, "auto_input_switch": 1}',
                encoding="utf-8",
            )
            self.assertEqual(AimSettings.load(path), AimSettings(depth=40))
            path.write_text('{"seed": 54}', encoding="utf-8")
            legacy = AimSettings.load(path)
            self.assertFalse(legacy.auto_input_switch)
            self.assertEqual(legacy.mouse_sensitivity, 100)
            self.assertEqual((legacy.stick_horizontal_speed, legacy.stick_vertical_speed), (140, 115))
            self.assertEqual((legacy.tracking_arc, legacy.tracking_speed), (0, 100))
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
