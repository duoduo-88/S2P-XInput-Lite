import json
import tempfile
import tkinter as tk
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from tkinter import ttk
from unittest.mock import patch


def basis(**settings):
    return {
        "mode": "shooting", "targets": 10, "seconds": 30,
        "seed": 0, "depth": 50, "viewport": [800, 450], **settings,
    }


class AimHistoryTests(unittest.TestCase):
    def test_reopened_history_matches_after_color_and_reticle_changes(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            old_basis = basis(reticle="#edf4fc", reticle_size=20, target_color="#57dab1")
            AimHistory(path).add(old_basis, {"hits": 8})
            history = AimHistory(path)
            recolored = basis(reticle="#123456", reticle_size=32, target_color="#ffffff")
            self.assertTrue(history.has_matching_record(recolored))
            self.assertEqual(history.add(recolored, {"hits": 9}), (("hits", 1, True),))
            self.assertEqual(history.records[0]["basis"], old_basis)
            self.assertFalse(history.has_matching_record(dict(recolored, depth=100)))

    def test_reopened_history_compares_with_best_matching_round(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            history = AimHistory(path)
            history.add(basis(), {"hits": 10, "accuracy": 70, "seconds": 16, "error": 2})
            history.add(basis(mouse_sensitivity=50), {"hits": 10, "accuracy": 85, "seconds": 15, "error": 3})
            history.add(basis(), {"hits": 10, "accuracy": 80, "seconds": 12, "error": 2})
            for changed in (
                basis(mode="cursor-mouse"), basis(targets=15), basis(seconds=45),
                basis(seed=1), basis(depth=60), basis(viewport=[800, 480]), basis(stick="left"),
            ):
                self.assertEqual(history.add(changed, {"hits": 10, "accuracy": 100}), ())

            reopened = AimHistory(path)
            tuned = basis(mouse_sensitivity=125)
            self.assertTrue(reopened.has_matching_record(tuned))
            changes = reopened.add(tuned, {"hits": 10, "accuracy": 82.5, "seconds": 14, "error": 2.75})
            self.assertEqual(changes, (
                ("accuracy", -2.5, False), ("seconds", -1.0, True), ("error", -0.25, True),
            ))
            self.assertEqual(reopened.records[-1]["basis"]["mouse_sensitivity"], 125)

    def test_completion_time_requires_two_completed_point_rounds(self):
        from gamepad_aim_history import AimHistory

        history = AimHistory(None)
        history.add(basis(), {"hits": 10, "seconds": 20})
        self.assertEqual(history.add(basis(), {"hits": 7, "seconds": 30}), (("hits", -3, False),))
        self.assertEqual(history.add(basis(), {"hits": 10, "seconds": 17}), (("seconds", -3.0, True),))

    def test_timed_targets_persist_and_compare_without_completion_time(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            history = AimHistory(path)
            timed = basis(mode="timed-shooting")
            best_possible = {"hits": 10, "accuracy": 100, "seconds": 15, "error": 0}
            history.add(basis(), best_possible)
            self.assertEqual(history.add(dict(timed, targets=11), dict(best_possible, hits=11)), ())
            self.assertEqual(history.add(dict(timed, seconds=45), best_possible), ())
            self.assertEqual(history.add(timed, dict(best_possible, hits=9)), ())
            history.add(timed, {"hits": 10, "accuracy": 80, "seconds": 18, "error": 1})
            history.add(timed, {"hits": 10, "accuracy": 90, "seconds": 30, "error": 2})
            # Stored timing differences must not affect a fixed-duration ranking.
            self.assertEqual(
                history.add(timed, {"hits": 10, "accuracy": 90, "seconds": 25, "error": 4}),
                (("error", 2.0, False),),
            )
            reopened = AimHistory(path)
            self.assertTrue(reopened.has_matching_record(timed))
            self.assertEqual(
                reopened.add(timed, {"hits": 10, "accuracy": 85.5, "seconds": 27, "error": 3.5}),
                (("accuracy", -4.5, False), ("error", 1.5, False)),
            )

    def test_missing_and_equal_display_values_are_excluded(self):
        from gamepad_aim_history import AimHistory

        history = AimHistory(None)
        tracking = basis(mode="tracking", targets=0)
        history.add(tracking, {"tracking": 45.12, "error": 2.004, "seconds": 30})
        self.assertEqual(history.add(tracking, {"tracking": 45.14, "seconds": 30}), ())
        self.assertEqual(
            history.add(tracking, {"tracking": 45.16, "error": 2.10, "seconds": 30}),
            (("error", 0.1, False), ("tracking", 0.1, True)),
        )

    def test_2d_tracking_compares_against_best_tracking_score_in_pixels(self):
        from gamepad_aim_history import AimHistory

        history = AimHistory(None)
        tracking = basis(mode="cursor-tracking", targets=0)
        history.add(tracking, {"tracking": 80, "error": 14.04, "seconds": 30})
        history.add(tracking, {"tracking": 60, "error": 5, "seconds": 30})
        self.assertEqual(
            history.add(tracking, {"tracking": 80.5, "error": 14.06, "seconds": 30}),
            (("error", 0.1, False), ("tracking", 0.5, True)),
        )

    def test_unified_2d_target_modes_keep_legacy_input_groups_separate(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            history = AimHistory(path)
            baseline = {"hits": 10, "accuracy": 90, "error": 2.04, "seconds": 30}
            best_legacy = dict(baseline, accuracy=100, error=1)
            for mode, legacy_modes in (
                ("cursor-shooting", ("cursor-stick", "cursor-mouse")),
                ("timed-cursor", ("timed-cursor-stick", "timed-cursor-mouse")),
            ):
                with self.subTest(mode=mode):
                    for legacy_mode in legacy_modes:
                        history.add(basis(mode=legacy_mode), best_legacy)
                    unified = basis(mode=mode)
                    self.assertEqual(history.add(unified, baseline), ())
                    history = AimHistory(path)
                    self.assertTrue(history.has_matching_record(unified))
                    changes = history.add(unified, dict(baseline, error=2.06, seconds=29))
                    time_change = (("seconds", -1.0, True),) if mode == "cursor-shooting" else ()
                    self.assertEqual(changes, time_change + (("error", 0.1, False),))
            self.assertEqual(len(AimHistory(path).records), 8)

    def test_change_formatter_keeps_numeric_direction_and_units(self):
        from gamepad_aim_history import format_change

        self.assertEqual(format_change("hits", 1), "↑ +1")
        self.assertEqual(format_change("accuracy", -0.5), "↓ -0.5 pp")
        self.assertEqual(format_change("error", -0.25, "°"), "↓ -0.25°")
        self.assertEqual(format_change("error", 0.1, " px"), "↑ +0.1 px")

    def test_unreadable_records_do_not_prevent_recording_or_clearing(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_bytes(b"\xff")
            history = AimHistory(path)
            self.assertFalse(history.records)
            history.add(basis(), {"hits": 3})
            records = json.loads(path.read_text(encoding="utf-8"))
            records.append({"basis": [], "metrics": {"hits": "invalid"}, "at": ""})
            records.append({**records[0], "basis": basis(mode=[])})
            path.write_text(json.dumps(records), encoding="utf-8")
            history = AimHistory(path)
            self.assertEqual(len(history.records), 1)
            history.clear()
            self.assertFalse(AimHistory(path).records)

    def test_impossible_metrics_do_not_rank_above_valid_results(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            scores = {"hits": 10, "accuracy": 100, "error": 2, "seconds": 20}
            AimHistory(path).add(basis(), scores)
            data = json.loads(path.read_text(encoding="utf-8"))
            data.append(dict(data[0], metrics={"hits": 900, "accuracy": 500, "error": -100, "seconds": -5}))
            data.append(dict(data[0], metrics={"hits": 8, "accuracy": 500, "error": 5, "seconds": -5}))
            path.write_text(json.dumps(data), encoding="utf-8")
            history = AimHistory(path)
            self.assertEqual([record["metrics"] for record in history.records], [
                scores, {"hits": 8, "error": 5},
            ])
            self.assertEqual(history.add(basis(), scores), ())

    def test_failed_add_and_clear_keep_memory_consistent(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            history = AimHistory(path)
            history.add(basis(), {"hits": 3})
            path.unlink()
            path.mkdir()
            with self.assertRaises(OSError):
                history.add(basis(), {"hits": 4})
            with self.assertRaises(OSError):
                history.clear()
            self.assertEqual(len(history.records), 1)
            self.assertEqual(history.records[0]["metrics"], {"hits": 3})

    def test_open_instances_do_not_overwrite_each_others_records(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            first, second = AimHistory(path), AimHistory(path)
            first.add(basis(), {"hits": 2})
            self.assertEqual(second.add(basis(), {"hits": 4}), (("hits", 2, True),))
            first.clear()
            self.assertEqual(second.add(basis(), {"hits": 3}), ())
            self.assertEqual(len(AimHistory(path).records), 1)

    def test_match_flag_uses_the_same_snapshot_as_the_changes(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            first, second = AimHistory(path), AimHistory(path)
            first.add(basis(), {"hits": 3})
            self.assertEqual(second.add_with_comparison(basis(), {"hits": 3}), ((), True))
            first.clear()
            self.assertEqual(second.add_with_comparison(basis(), {"hits": 3}), ((), False))

    def test_opening_history_serializes_its_read_with_clear(self):
        from gamepad_aim_history import AimHistory

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            history = AimHistory(path)
            history.add(basis(), {"hits": 3})
            reader_open, release_reader, writer_started = Event(), Event(), Event()

            def read_text(file, *, encoding):
                with file.open(encoding=encoding) as handle:
                    reader_open.set()
                    if not release_reader.wait(3):
                        raise RuntimeError("Test did not release the history read")
                    return handle.read()

            def clear():
                writer_started.set()
                history.clear()

            with patch.object(Path, "read_text", read_text), ThreadPoolExecutor() as pool:
                reader = pool.submit(AimHistory, path)
                try:
                    self.assertTrue(reader_open.wait(2))
                    writer = pool.submit(clear)
                    self.assertTrue(writer_started.wait(2))
                    with self.assertRaises(TimeoutError):
                        writer.result(timeout=0.15)
                finally:
                    release_reader.set()
                self.assertEqual(reader.result(timeout=2).records[0]["metrics"], {"hits": 3})
                writer.result(timeout=2)
            self.assertFalse(AimHistory(path).records)

    def test_inline_history_view_renders_records_and_updates_after_add_and_clear(self):
        from gamepad_aim_history import AimHistory

        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(root.destroy)
        root.withdraw()
        parent = ttk.Frame(root)
        parent.pack()
        history = AimHistory(None)
        timed = basis(mode="timed-shooting", targets=0, horizontal_spread=60, vertical_spread=80)
        history.add(timed, {"hits": 3})
        cleared = []
        view = history.create_view(parent, str, lambda: cleared.append(True))
        self.assertIsInstance(view, ttk.Frame)
        self.assertIs(view.master, parent)
        self.assertFalse(view.winfo_manager())
        view.pack()
        root.update_idletasks()
        self.assertEqual(len(view.tree.get_children()), 1)

        history.add(timed, {"hits": 4})
        rows = view.tree.get_children()
        self.assertEqual(len(rows), 2)
        self.assertEqual(view.tree.selection(), (rows[0],))
        self.assertEqual(view.tree.set(rows[0], "hits"), "4")
        self.assertEqual(view.tree.set(rows[0], "mode"), "3D · 限時點靶")
        self.assertIn("遠近差異 50% · 水平 60%／垂直 80%", view.basis_label.cget("text"))
        self.assertIn("滑鼠感度 100%", view.settings_label.cget("text"))

        with patch("gamepad_aim_history.messagebox.askyesno", return_value=True):
            view.clear_button.invoke()
        self.assertFalse(view.tree.get_children())
        self.assertFalse(history.records)
        self.assertEqual(cleared, [True])
        view.destroy()
        history.add(basis(), {"hits": 5})
        self.assertEqual(len(history.records), 1)

    def test_popup_raises_one_window_and_clears_after_confirmation(self):
        from gamepad_aim_history import AimHistory

        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(root.destroy)
        root.withdraw()
        history = AimHistory(None)
        round_basis = basis(
            seconds=0, reticle="#edf4fc", target_color="#57dab1", rumble=35, sound=False,
        )
        history.add(round_basis, {"hits": 2, "accuracy": 80})
        history.add(dict(round_basis, mouse_sensitivity=50), {"hits": 4, "accuracy": 75})
        cleared = []
        popup = history.show(root, str, lambda: cleared.append(True))
        self.assertIs(history.show(root, str, lambda: cleared.append(True)), popup)

        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        tree = next(child for child in children(popup) if isinstance(child, ttk.Treeview))
        clear = next(
            child for child in children(popup)
            if isinstance(child, ttk.Button) and child.cget("text") == "清除紀錄"
        )
        self.assertEqual(len(tree.get_children()), 2)
        colors = {
            child.cget("text"): str(child.cget("foreground"))
            for child in children(popup) if isinstance(child, ttk.Label)
        }
        self.assertTrue(any(text.startswith("不限時") for text in colors))
        self.assertIn("準心 #edf4fc · 靶色 #57dab1 · 震動 35% · 音效關閉 · 滑鼠感度 50%", colors)
        self.assertIn("與同設定最佳成績比較", colors)
        self.assertEqual(colors["命中 ↑ +2"], "#15803d")
        self.assertEqual(colors["命中率 ↓ -5.0 pp"], "#b91c1c")
        with patch("gamepad_aim_history.messagebox.askyesno", side_effect=(False, True)):
            clear.invoke()
            self.assertEqual(len(tree.get_children()), 2)
            clear.invoke()
        self.assertFalse(tree.get_children())
        self.assertFalse(history.records)
        self.assertEqual(cleared, [True])
        self.assertEqual(int(tree.cget("height")), 8)
        self.assertFalse(popup.view.comparison.winfo_manager())
