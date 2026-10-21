import sys
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shadow_validation import ShadowValidator, ValidationMode


class ShadowValidationTests(unittest.TestCase):
    def test_legacy_and_shadow_keep_legacy_official_output(self):
        validator = ShadowValidator(lambda value: value + 1, lambda value: value + 3)
        self.assertEqual(validator.evaluate(4), 5)
        validator.set_mode(ValidationMode.SHADOW)
        self.assertEqual(validator.evaluate(4), 5)
        snapshot = validator.snapshot()
        self.assertEqual(snapshot["count"], 1)
        self.assertEqual(snapshot["max_error"], 2.0)

    def test_candidate_failure_cannot_break_production_output(self):
        validator = ShadowValidator(
            lambda value: value * 2,
            lambda _value: (_ for _ in ()).throw(RuntimeError("candidate failed")),
            mode="shadow",
        )
        self.assertEqual(validator.evaluate(6), 12)
        self.assertEqual(validator.snapshot()["candidate_error_count"], 1)

    def test_mode_switch_resets_bounded_statistics(self):
        validator = ShadowValidator(
            lambda value: value,
            lambda value: value + 0.5,
            mode="shadow",
            threshold=0.25,
        )
        for value in range(1000):
            self.assertEqual(validator.evaluate(value), value)
        self.assertEqual(validator.snapshot()["count"], 1000)
        self.assertEqual(validator.snapshot()["threshold_exceeded_count"], 1000)
        validator.set_mode("v2")
        self.assertEqual(validator.snapshot()["count"], 0)
        self.assertEqual(validator.evaluate(1), 1.5)

    def test_disabled_path_does_not_evaluate_candidate(self):
        calls = []
        validator = ShadowValidator(
            lambda value: value,
            lambda value: calls.append(value) or value,
        )
        self.assertEqual(validator.evaluate(3), 3)
        self.assertEqual(calls, [])

    def test_incomparable_then_finite_error_keeps_snapshot_finite(self):
        validator = ShadowValidator(
            lambda value: value,
            lambda value: "different" if value == "legacy" else value + 2,
            mode="shadow",
        )
        self.assertEqual(validator.evaluate("legacy"), "legacy")
        self.assertEqual(validator.evaluate(3), 3)
        snapshot = validator.snapshot()
        self.assertEqual(snapshot["comparison_count"], 2)
        self.assertEqual(snapshot["comparison_mismatch_count"], 1)
        self.assertEqual(snapshot["finite_error_count"], 1)
        self.assertEqual(snapshot["mean_error"], 2.0)
        self.assertTrue(math.isfinite(snapshot["mean_error"]))

    def test_finite_incomparable_finite_and_tuple_length_mismatch(self):
        legacy_responses = iter((1.0, (1,), 1.0))
        candidate_responses = iter((2.0, (1, 2), 5.0))
        validator = ShadowValidator(
            lambda _value: next(legacy_responses),
            lambda _value: next(candidate_responses),
            mode="shadow",
        )
        validator.evaluate(None)
        validator.evaluate(None)
        validator.evaluate(None)
        snapshot = validator.snapshot()
        self.assertEqual(snapshot["finite_error_count"], 2)
        self.assertEqual(snapshot["comparison_mismatch_count"], 1)
        self.assertEqual(snapshot["mean_error"], 2.5)
        self.assertTrue(all(
            not isinstance(value, float) or math.isfinite(value)
            for value in snapshot.values()
        ))

    def test_shadow_candidate_with_private_state_cannot_touch_legacy_state(self):
        production_state = {"calls": 0}
        candidate_state = {"calls": 0}

        def legacy(value):
            production_state["calls"] += 1
            return value

        def candidate(value):
            candidate_state["calls"] += 1
            return value

        validator = ShadowValidator(legacy, candidate, mode="shadow")
        self.assertEqual(validator.evaluate(7), 7)
        self.assertEqual(production_state, {"calls": 1})
        self.assertEqual(candidate_state, {"calls": 1})


if __name__ == "__main__":
    unittest.main()
