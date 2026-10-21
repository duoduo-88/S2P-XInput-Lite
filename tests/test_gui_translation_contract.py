import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GUITranslationContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
