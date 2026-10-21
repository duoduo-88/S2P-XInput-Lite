import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from runtime_rules import should_freeze_gyro_output


class RuntimeRulesTests(unittest.TestCase):
    def test_only_stabilization_change_triggers_freeze(self):
        self.assertTrue(should_freeze_gyro_output("HOLD", 5, True))
        self.assertTrue(should_freeze_gyro_output("TOGGLE", 5, True))
        self.assertFalse(should_freeze_gyro_output("HOLD", 5, False))
        self.assertFalse(should_freeze_gyro_output("HOLD", 0, True))
        self.assertFalse(should_freeze_gyro_output("OFF", 5, True))


if __name__ == "__main__":
    unittest.main()
