import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tooltip_layout import wrap_tooltip_text


class TooltipLayoutTests(unittest.TestCase):
    @staticmethod
    def measure(text):
        return len(text) * 10

    def test_numeric_unit_is_not_split_or_left_as_an_orphan(self):
        wrapped = wrap_tooltip_text(
            "這是高速8000 Hz裝置，可用來測量輸入更新率。",
            100,
            self.measure,
        )

        self.assertNotIn("8000\n", wrapped)
        self.assertNotIn("8000 \n", wrapped)
        self.assertNotIn("Hz\n裝置", wrapped)

    def test_line_never_starts_with_closing_punctuation(self):
        wrapped = wrap_tooltip_text(
            "第一個說明內容很長，第二個說明內容也很長。",
            90,
            self.measure,
        )

        for line in wrapped.splitlines():
            self.assertFalse(line.startswith(("，", "。", "；")))

    def test_short_last_line_is_rebalanced(self):
        wrapped = wrap_tooltip_text(
            "數值越高，對角方向可以輸出的範圍就會越大。",
            120,
            self.measure,
        )
        lines = wrapped.splitlines()

        self.assertGreaterEqual(len(lines[-1]), 5)

    def test_explicit_paragraph_breaks_are_preserved(self):
        wrapped = wrap_tooltip_text(
            "第一段。\n\n第二段。",
            500,
            self.measure,
        )

        self.assertEqual(wrapped, "第一段。\n\n第二段。")

    def test_no_control_characters_are_inserted(self):
        wrapped = wrap_tooltip_text(
            "設定為100%，數值是0.03。",
            100,
            self.measure,
        )

        self.assertNotIn("\u2060", wrapped)
        self.assertNotIn("\u00a0", wrapped)


if __name__ == "__main__":
    unittest.main()
