"""Shared idle-disconnect policy for desktop and standalone profile export."""

from __future__ import annotations

import math
import time


IDLE_DISCONNECT_OPTIONS = (0, 5, 10, 15, 30, 60)
DEFAULT_IDLE_DISCONNECT_MINUTES = 15


def normalize_idle_disconnect_minutes(value) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return DEFAULT_IDLE_DISCONNECT_MINUTES
    if minutes not in IDLE_DISCONNECT_OPTIONS:
        return DEFAULT_IDLE_DISCONNECT_MINUTES
    return minutes


def load_idle_disconnect_minutes(config) -> int:
    return normalize_idle_disconnect_minutes(
        config.get(
            "gui",
            "idle_disconnect_minutes",
            fallback=str(DEFAULT_IDLE_DISCONNECT_MINUTES),
        )
    )


def perform_idle_disconnect(controller, xinput, publish_status) -> bool:
    """Publish idle-disconnected only after the transport confirms success."""
    xinput.reset_output_state()
    if not controller.disconnect_for_idle():
        return False
    publish_status(
        state="idle_disconnected",
        battery_percent=None,
        battery_voltage=None,
        charging=False,
        wired_full_report=None,
        wired_polling_rate=None,
        wired_processing_rate=None,
        sensor_mode=None,
        gyro_raw=None,
        accel_raw=None,
        gyro_bias_samples=0,
    )
    return True


class IdleActivityTracker:
    """Detect deliberate input changes while rejecting stick/gyro noise."""

    STICK_ACTIVE = 150
    STICK_CHANGE = 24
    GYRO_ACTIVE = 120
    GYRO_CHANGE = 40

    def __init__(self, minutes=DEFAULT_IDLE_DISCONNECT_MINUTES, now=None):
        self.minutes = normalize_idle_disconnect_minutes(minutes)
        self.last_activity = time.monotonic() if now is None else float(now)
        self._previous = None

    @property
    def enabled(self):
        return self.minutes > 0

    def configure(self, minutes, now=None):
        normalized = normalize_idle_disconnect_minutes(minutes)
        if normalized != self.minutes:
            self.minutes = normalized
            self.reset(now)

    def reset(self, now=None):
        self.last_activity = time.monotonic() if now is None else float(now)
        self._previous = None

    @staticmethod
    def _stick_active(stick):
        x, y = stick
        return math.hypot(x - 2048, y - 2048) >= IdleActivityTracker.STICK_ACTIVE

    @staticmethod
    def _changed(current, previous, threshold):
        return any(abs(a - b) >= threshold for a, b in zip(current, previous))

    def observe(self, state, now=None):
        timestamp = time.monotonic() if now is None else float(now)
        current = (
            int(state.buttons),
            tuple(state.left_stick),
            tuple(state.right_stick),
            tuple(state.gyroscope),
        )
        previous = self._previous
        self._previous = current
        if previous is None:
            self.last_activity = timestamp
            return True

        buttons, left, right, gyro = current
        old_buttons, old_left, old_right, old_gyro = previous
        active = buttons != old_buttons
        active = active or (
            self._changed(left, old_left, self.STICK_CHANGE)
            and (self._stick_active(left) or self._stick_active(old_left))
        )
        active = active or (
            self._changed(right, old_right, self.STICK_CHANGE)
            and (self._stick_active(right) or self._stick_active(old_right))
        )
        active = active or (
            self._changed(gyro, old_gyro, self.GYRO_CHANGE)
            and max(map(abs, gyro)) >= self.GYRO_ACTIVE
        )
        if active:
            self.last_activity = timestamp
        return active

    def expired(self, now=None):
        if not self.enabled:
            return False
        timestamp = time.monotonic() if now is None else float(now)
        return timestamp - self.last_activity >= self.minutes * 60.0
