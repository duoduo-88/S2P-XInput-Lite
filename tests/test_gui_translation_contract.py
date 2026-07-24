import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GUITranslationContractTests(unittest.TestCase):
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
