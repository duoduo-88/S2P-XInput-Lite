import ast
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class GUITranslationContractTests(unittest.TestCase):
    def test_layer_conflict_warning_uses_the_selected_language(self):
        from config_gui import ConfigGUI

        for language in ("en", "zh"):
            with self.subTest(language=language):
                gui = object.__new__(ConfigGUI)
                gui.language = language
                gui.root = None
                gui.mapping_layers = []
                with patch("config_gui.messagebox.showwarning") as warning:
                    self.assertIsNone(gui._mapping_layer_editor_target({"id": "gone"}, None))
                title, message = warning.call_args.args
                if language == "en":
                    self.assertEqual(title, "Mapping Layer Changed")
                    self.assertIn("changed or deleted", message)
                    self.assertNotRegex(title + message, r"[\u4e00-\u9fff]")
                else:
                    self.assertEqual(title, "映射層已變更")
                    self.assertIn("已被變更或刪除", message)

    def test_identity_and_reconnect_errors_have_english_translations(self):
        from localization import translate_text
        expected = {
            "ESP32 韌體身分不符。": "ESP32 firmware identity does not match.",
            "ESP32 裝置序號不符。": "ESP32 device serial number does not match.",
            "ESP32 缺少可安全重連的唯一序號；請更新韌體。":
                "ESP32 has no unique serial number for safe reconnection. Update the firmware.",
        }
        for source, english in expected.items():
            with self.subTest(source=source):
                self.assertEqual(translate_text(source, "en"), english)
                self.assertEqual(translate_text(source, "zh-TW"), source)

    def test_config_gui_translation_calls_take_one_argument(self):
        paths = [
            ROOT / "src" / "config_gui.py",
            ROOT / "src" / "gui_sections" / "footer.py",
        ]
        invalid = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "tr"
                    and len(node.args) != 1
                ):
                    invalid.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}"
                    )
        self.assertEqual(invalid, [])

    def test_literal_gui_texts_translate_fully_to_english(self):
        """Every literal Chinese text given to tr() or a message box has English."""
        import re

        from localization import translate_text

        cjk = re.compile(r"[一-鿿]")
        messageboxes = {
            "showinfo", "showwarning", "showerror", "askquestion",
            "askokcancel", "askretrycancel", "askyesno", "askyesnocancel",
        }
        paths = [
            ROOT / "src" / "config_gui.py",
            ROOT / "src" / "gamepad_test_app.py",
            ROOT / "src" / "dialog_layout.py",
            *sorted((ROOT / "src" / "gui_sections").glob("*.py")),
        ]
        untranslated = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "tr":
                    texts = node.args[:1]
                elif (
                    isinstance(func, ast.Attribute)
                    and func.attr in messageboxes
                    and getattr(func.value, "id", "") == "messagebox"
                ):
                    texts = node.args[:2]
                else:
                    continue
                for text in texts:
                    if (
                        isinstance(text, ast.Constant)
                        and isinstance(text.value, str)
                        and cjk.search(text.value)
                        and cjk.search(translate_text(text.value, "en"))
                    ):
                        untranslated.append(
                            f"{path.relative_to(ROOT)}:{text.lineno} {text.value[:40]!r}"
                        )
        self.assertEqual(untranslated, [])


if __name__ == "__main__":
    unittest.main()
