"""Small bounded Legacy/Shadow/V2 validation primitive for risky algorithms."""

from __future__ import annotations

import math
from enum import Enum


class ValidationMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    V2 = "v2"


def normalize_mode(mode):
    if isinstance(mode, ValidationMode):
        return mode
    try:
        return ValidationMode(str(mode).lower())
    except ValueError:
        return ValidationMode.LEGACY


def _error_magnitude(left, right):
    if isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
        if len(left) != len(right):
            return None
        errors = tuple(_error_magnitude(a, b) for a, b in zip(left, right))
        return None if any(error is None for error in errors) else max(errors, default=0.0)
    try:
        error = abs(float(left) - float(right))
    except (TypeError, ValueError):
        return 0.0 if left == right else None
    return error if math.isfinite(error) else None


class ShadowValidator:
    """Keep production output independent from a bounded candidate observer.

    A Shadow candidate must be pure or keep all mutable state private and
    isolated from the production legacy evaluator. This class deliberately
    does not deepcopy arguments: doing so would add allocations and latency to
    a future realtime path, and cannot safely protect every external object.
    """

    def __init__(self, legacy, candidate=None, mode=ValidationMode.LEGACY, threshold=0.0):
        if not callable(legacy):
            raise TypeError("legacy must be callable")
        self.legacy = legacy
        self.candidate = candidate
        self.threshold = max(0.0, float(threshold))
        self.mode = normalize_mode(mode)
        self.reset()

    def reset(self):
        self.count = 0
        self.finite_error_count = 0
        self.comparison_mismatch_count = 0
        self.mean_error = 0.0
        self.max_error = 0.0
        self.threshold_exceeded_count = 0
        self.candidate_error_count = 0

    def set_mode(self, mode):
        self.mode = normalize_mode(mode)
        self.reset()
        return self.mode

    def evaluate(self, *args, **kwargs):
        legacy_output = self.legacy(*args, **kwargs)
        if self.mode is ValidationMode.LEGACY or self.candidate is None:
            return legacy_output
        try:
            candidate_output = self.candidate(*args, **kwargs)
        except Exception:
            self.candidate_error_count += 1
            return legacy_output
        error = _error_magnitude(legacy_output, candidate_output)
        self.count += 1
        if error is None:
            self.comparison_mismatch_count += 1
        else:
            self.finite_error_count += 1
            self.mean_error += (
                error - self.mean_error
            ) / self.finite_error_count
            self.max_error = max(self.max_error, error)
            if error > self.threshold:
                self.threshold_exceeded_count += 1
        return candidate_output if self.mode is ValidationMode.V2 else legacy_output

    def snapshot(self):
        return {
            "mode": self.mode.value,
            "comparison_count": self.count,
            "finite_error_count": self.finite_error_count,
            "comparison_mismatch_count": self.comparison_mismatch_count,
            # Keep count as a compatibility alias for early consumers.
            "count": self.count,
            "mean_error": self.mean_error,
            "max_error": self.max_error,
            "threshold": self.threshold,
            "threshold_exceeded_count": self.threshold_exceeded_count,
            "candidate_error_count": self.candidate_error_count,
        }
