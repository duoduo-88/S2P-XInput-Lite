import builtins
import configparser
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import console_i18n
from localization import translate_text


class ConsoleI18nTests(unittest.TestCase):
    def tearDown(self):
        console_i18n.set_current_language("zh")

    def test_cached_current_language_never_loads_config(self):
        console_i18n.set_current_language("en")
        with patch("config_utils.load_config") as load_config:
            for _ in range(10_000):
                self.assertEqual(console_i18n.current_language(), "en")
        load_config.assert_not_called()

    def test_high_frequency_localized_print_does_not_read_filesystem_config(self):
        console_i18n.set_current_language("en")
        with (
            patch("config_utils.load_config") as load_config,
            patch.object(builtins, "print") as output,
        ):
            for _ in range(1_000):
                console_i18n.localized_print("手把測試無法開啟")
        load_config.assert_not_called()
        self.assertEqual(output.call_count, 1_000)
        self.assertEqual(
            output.call_args.args[0],
            translate_text("手把測試無法開啟", "en"),
        )

    def test_loaded_config_refreshes_language_for_later_output(self):
        config = configparser.ConfigParser()
        config.read_dict({"gui": {"language": "en"}})
        self.assertEqual(console_i18n.set_language_from_config(config), "en")
        with patch.object(builtins, "print") as output:
            console_i18n.localized_print("手把測試無法開啟")
        self.assertEqual(
            output.call_args.args[0],
            translate_text("手把測試無法開啟", "en"),
        )

    def test_invalid_language_uses_safe_chinese_fallback(self):
        self.assertEqual(console_i18n.set_current_language("not-a-language"), "zh")
        self.assertEqual(console_i18n.current_language(), "zh")

    def test_localized_input_delivers_translated_prompt_before_reading(self):
        console_i18n.set_current_language("en")
        with (
            patch("console_i18n.write_interactive_prompt") as prompt,
            patch.object(builtins, "input", return_value="ok") as read,
        ):
            self.assertEqual(console_i18n.localized_input("手把測試無法開啟"), "ok")
        prompt.assert_called_once_with(translate_text("手把測試無法開啟", "en"))
        read.assert_called_once_with("")


if __name__ == "__main__":
    unittest.main()
