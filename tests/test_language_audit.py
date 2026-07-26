import ast
import re
import sys
import unittest
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from localization import translate_text


HAN = re.compile(r"[\u3400-\u9fff]")


def literal_template(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "{}"
            for part in node.values
        )
    return None


class LanguageAuditTests(unittest.TestCase):
    def test_python_sources_have_no_replacement_or_private_use_characters(self):
        invalid = []
        firmware_main = (
            ROOT
            / "esp32s3"
            / "source"
            / "esp32s3_usb_bridge_bluedroid"
            / "main"
        )
        paths = list(SRC.rglob("*.py"))
        paths.extend(
            path
            for path in firmware_main.rglob("*")
            if path.suffix.lower() in {".c", ".h", ".cpp", ".hpp"}
        )
        for path in paths:
            source = path.read_text(encoding="utf-8")
            for index, character in enumerate(source):
                if character == "\ufffd" or unicodedata.category(character) == "Co":
                    invalid.append(
                        f"{path.relative_to(ROOT)}:{index}: U+{ord(character):04X}"
                    )
        self.assertEqual(invalid, [])

    def test_static_widget_labels_do_not_leak_chinese_in_english(self):
        leaks = []
        paths = [SRC / "config_gui.py", *sorted((SRC / "gui_sections").glob("*.py"))]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "text":
                        continue
                    source = literal_template(keyword.value)
                    if not source or not HAN.search(source):
                        continue
                    # This is intentionally a permanently bilingual toggle.
                    if source == "中 / En":
                        continue
                    translated = translate_text(source, "en")
                    if HAN.search(translated):
                        leaks.append(
                            f"{path.name}:{node.lineno}: {translated}"
                        )
        self.assertEqual(leaks, [])

    def test_english_translation_dictionary_has_no_duplicate_keys(self):
        source = (SRC / "localization.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        duplicates = []
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Dict)
                and any(
                    isinstance(target, ast.Name) and target.id == "EN_TEXT"
                    for target in node.targets
                )
            ):
                continue
            seen = {}
            for key in node.value.keys:
                if not (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                ):
                    continue
                if key.value in seen:
                    duplicates.append((key.lineno, seen[key.value], key.value))
                else:
                    seen[key.value] = key.lineno
        self.assertEqual(duplicates, [])

    def test_console_literals_do_not_leak_chinese_in_english(self):
        leaks = []
        for path in SRC.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "localized_print as print" not in source or path.name == "config_gui.py":
                continue
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ):
                    continue
                for argument in node.args:
                    template = literal_template(argument)
                    if template is None:
                        continue
                    translated = translate_text(template, "en")
                    if HAN.search(translated):
                        leaks.append(f"{path.name}:{node.lineno}: {translated}")
        self.assertEqual(leaks, [])

    def test_plain_raised_messages_do_not_leak_chinese_in_english(self):
        leaks = []
        for path in SRC.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "localized_print as print" not in source or path.name == "config_gui.py":
                continue
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Raise)
                    and isinstance(node.exc, ast.Call)
                    and node.exc.args
                ):
                    continue
                template = literal_template(node.exc.args[0])
                if template is None:
                    continue
                translated = translate_text(template, "en")
                if HAN.search(translated):
                    leaks.append(f"{path.name}:{node.lineno}: {translated}")
        self.assertEqual(leaks, [])

    def test_mapping_layer_ui_has_english_translations(self):
        texts = (
            "映射層", "＋ 新增", "按住", "切換", "改名", "編輯", "刪除",
            "新增映射層", "映射層名稱：", "啟用按鍵", "編輯映射層",
            "按鍵映射", "搖桿方向映射", "左搖桿", "右搖桿",
            "模式", "死區", "觸發", "放開", "滑鼠速度", "中心死區",
            "輸入方向", "映射為右搖桿", "映射為左搖桿", "映射為滑鼠",
            "線性滑鼠滾輪", "線性 Xbox LT", "線性 Xbox RT",
            "複製主要映射", "還原", "儲存", "取消",
        )
        for source in texts:
            translated = translate_text(source, "en")
            self.assertNotEqual(translated, source, source)
            self.assertIsNone(HAN.search(translated), translated)

    def test_gamepad_test_ui_has_complete_english_translations(self):
        path = SRC / "gamepad_test_window.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        missing = []
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "tr"
                and node.args
            ):
                continue
            for text_node in ast.walk(node.args[0]):
                if not (
                    isinstance(text_node, ast.Constant)
                    and isinstance(text_node.value, str)
                    and HAN.search(text_node.value)
                ):
                    continue
                translated = translate_text(text_node.value, "en")
                if translated == text_node.value or HAN.search(translated):
                    missing.append(
                        f"{path.name}:{node.lineno}: {text_node.value}"
                    )
        self.assertEqual(missing, [])

    def test_gamepad_test_uses_compact_english_labels(self):
        expected = {
            "實體搖桿": "Stick",
            "陀螺儀": "Gyro",
            "合成結果": "Final",
            "清除軌跡與統計": "Clear",
            "顯示輸出形狀": "Shape",
            "採樣點": "Samples",
            "軌跡長度": "Trail",
            "線性扳機": "Triggers",
            "震動模板": "Patterns",
            "手動震動輸出": "Manual Output",
        }
        for source, target in expected.items():
            self.assertEqual(translate_text(source, "en"), target)

    def test_dynamic_help_bubbles_have_exact_english_translations(self):
        texts = (
            "只在「混合」模式生效。\n\n"
            "0.00 = 只保留遊戲原生震動\n"
            "數值越高，音訊震動所占比例越多\n"
            "1.00 = 只保留音訊震動",
            "控制音訊轉換成震動前的總靈敏度。\n\n"
            "降低：整體音訊震動較弱\n"
            "提高：較小的聲音也會產生明顯震動",
            "低於此音量的背景聲不會產生震動。\n\n"
            "提高：過濾更多背景聲\n"
            "降低：較小的聲音也能觸發震動",
        )
        for source in texts:
            translated = translate_text(source, "en")
            self.assertNotEqual(translated, source, source)
            self.assertIsNone(HAN.search(translated), translated)

    def test_recent_dialog_text_has_complete_english_translation(self):
        texts = (
            "輸入錯誤",
            "獨立模式設定無法寫入 ESP32",
            "還原陀螺儀預設",
            "確定要將所有可調設定恢復為預設值嗎？\n\n"
            "搖桿校正資料不會被修改。\n"
            "方案清單與已儲存方案不會被修改。\n"
            "映射層內容會保留，但所有映射層將取消啟用。\n"
            "HidHide 隱藏設定也會取消。",
        )
        for source in texts:
            translated = translate_text(source, "en")
            self.assertNotEqual(translated, source, source)
            self.assertIsNone(HAN.search(translated), translated)

    def test_static_help_bubbles_have_exact_english_translations(self):
        missing = []
        paths = [SRC / "config_gui.py", *sorted((SRC / "gui_sections").glob("*.py"))]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create_help"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                ):
                    continue
                source = node.args[1].value
                translated = translate_text(source, "en")
                if translated == source or HAN.search(translated):
                    missing.append(f"{path.name}:{node.lineno}: {source}")
        self.assertEqual(missing, [])

    def test_persistence_conflict_messages_have_english_translations(self):
        texts = (
            "目前方案已由外部修改",
            "目前方案檔案已由其他程式修改。\n\n"
            "是：重新載入外部版本並套用\n"
            "否：以目前畫面覆蓋外部版本\n"
            "取消：返回設定畫面",
            "執行中的連線未能套用新設定，目前暫時沿用先前設定。\n"
            "新設定已儲存至檔案，重新連線後將會生效。",
        )
        for source in texts:
            translated = translate_text(source, "en")
            self.assertNotEqual(translated, source, source)
            self.assertIsNone(HAN.search(translated), translated)


if __name__ == "__main__":
    unittest.main()
