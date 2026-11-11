import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config_gui import ConfigGUI


class MappingLayerActionTests(unittest.TestCase):
    def setUp(self):
        self.gui = object.__new__(ConfigGUI)
        self.gui.root = None
        self.gui.tr = lambda text: text
        self.original = [
            {"id": "one", "name": "One", "enabled": True},
            {"id": "two", "name": "Two", "enabled": True},
        ]
        self.gui.mapping_layers = copy.deepcopy(self.original)
        self.gui.persist_mapping_layer_files = Mock()
        self.gui.refresh_mapping_layer_rows = Mock()
        self.warning_patch = patch("config_gui.messagebox.showwarning")
        self.warning = self.warning_patch.start()
        self.addCleanup(self.warning_patch.stop)

    def test_rename_follows_same_layer_after_reload_and_reorder(self):
        def choose_name(*args, **kwargs):
            self.gui.mapping_layers = list(reversed(copy.deepcopy(self.original)))
            return "Renamed"

        self.gui._ask_mapping_layer_name = choose_name
        self.gui.rename_mapping_layer(0)
        self.assertEqual(self.gui.mapping_layers, [self.original[1], {**self.original[0], "name": "Renamed"}])
        self.gui.persist_mapping_layer_files.assert_called_once()
        self.warning.assert_not_called()

    def test_delete_follows_same_layer_after_reload_and_reorder(self):
        def confirm(*args, **kwargs):
            self.gui.mapping_layers = list(reversed(copy.deepcopy(self.original)))
            return True

        with patch("config_gui.messagebox.askyesno", side_effect=confirm):
            self.gui.delete_mapping_layer(0)
        self.assertEqual(self.gui.mapping_layers, [self.original[1]])
        self.gui.persist_mapping_layer_files.assert_called_once()
        self.warning.assert_not_called()

    def test_changed_or_deleted_layers_are_preserved_by_both_actions(self):
        for action in ("rename", "delete"):
            for change in ("changed", "deleted"):
                with self.subTest(action=action, change=change):
                    self.gui.mapping_layers = copy.deepcopy(self.original)
                    self.gui.persist_mapping_layer_files.reset_mock()
                    self.warning.reset_mock()
                    updated = copy.deepcopy(self.original)
                    if change == "deleted":
                        updated.pop(0)
                    else:
                        updated[0]["name"] = "External"

                    def answer(*args, **kwargs):
                        self.gui.mapping_layers = copy.deepcopy(updated)
                        return "Renamed" if action == "rename" else True

                    self.gui._ask_mapping_layer_name = answer
                    with patch("config_gui.messagebox.askyesno", side_effect=answer):
                        getattr(self.gui, action + "_mapping_layer")(0)
                    self.assertEqual(self.gui.mapping_layers, updated)
                    self.gui.persist_mapping_layer_files.assert_not_called()
                    self.warning.assert_called_once()

    def test_normal_actions_and_canceled_prompts(self):
        self.gui._ask_mapping_layer_name = Mock(return_value="Renamed")
        self.gui.rename_mapping_layer(0)
        self.assertEqual(self.gui.mapping_layers[0]["name"], "Renamed")
        self.gui.persist_mapping_layer_files.assert_called_once()
        self.gui.persist_mapping_layer_files.reset_mock()
        for name in (None, "", "   "):
            self.gui._ask_mapping_layer_name.return_value = name
            self.gui.rename_mapping_layer(0)
        with patch("config_gui.messagebox.askyesno", return_value=False):
            self.gui.delete_mapping_layer(0)
        self.gui.persist_mapping_layer_files.assert_not_called()
        self.assertEqual(len(self.gui.mapping_layers), 2)
        with patch("config_gui.messagebox.askyesno", return_value=True):
            self.gui.delete_mapping_layer(0)
        self.assertEqual(self.gui.mapping_layers, [self.original[1]])
        self.warning.assert_not_called()

    def test_rename_collision_uses_current_layer_index(self):
        def choose_name(*args, **kwargs):
            self.gui.mapping_layers = list(reversed(copy.deepcopy(self.original)))
            return "Two"

        self.gui._ask_mapping_layer_name = choose_name
        with patch("config_gui.messagebox.showerror") as error:
            self.gui.rename_mapping_layer(0)
        self.assertEqual(self.gui.mapping_layers, list(reversed(self.original)))
        self.gui.persist_mapping_layer_files.assert_not_called()
        error.assert_called_once()

    def test_ambiguous_layer_ids_cancel_both_actions(self):
        for action in ("rename", "delete"):
            with self.subTest(action=action):
                self.gui.mapping_layers = copy.deepcopy(self.original)
                self.warning.reset_mock()

                def answer(*args, **kwargs):
                    self.gui.mapping_layers = [copy.deepcopy(self.original[0]) for _ in range(2)]
                    return "Renamed" if action == "rename" else True

                self.gui._ask_mapping_layer_name = answer
                with patch("config_gui.messagebox.askyesno", side_effect=answer):
                    getattr(self.gui, action + "_mapping_layer")(0)
                self.assertEqual(self.gui.mapping_layers, [self.original[0], self.original[0]])
                self.gui.persist_mapping_layer_files.assert_not_called()
                self.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
