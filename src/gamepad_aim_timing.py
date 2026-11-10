"""Absolute deadlines and measured submissions for the aim viewport."""

import math
from dataclasses import dataclass


TIMER_HALF_MS = 0.0005
SIMULATION_PERIOD = 1 / 250
IDLE_PERIOD = 1 / 30


def refresh_interval(hz):
    return 1 / hz if math.isfinite(hz) and hz > 1 else 1 / 60


def timer_delay_ms(now, deadline):
    # Round against the absolute phase. A frame may be up to half a
    # millisecond early, but rounding error does not accumulate.
    return max(1, math.floor((deadline - now) * 1000 + 0.5))


def next_deadline(deadline, now, period):
    if deadline <= 0:
        return now + period
    slots = 1 + math.floor(max(0.0, now - deadline) / period)
    following = deadline + slots * period
    return following if following > now else following + period


@dataclass(frozen=True)
class AimFrameMetrics:
    fps: float
    simulation_hz: float
    mean_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    over_budget: float | None


class FrameStats:
    def __init__(self):
        self.intervals = []
        self.reset(0.0)

    def reset(self, now):
        self.started = now
        self.frames = self.simulations = 0
        self.last_submission = None
        self.intervals.clear()

    def submitted(self, now):
        if self.last_submission is not None:
            self.intervals.append(now - self.last_submission)
        self.last_submission = now
        self.frames += 1

    def take(self, now, target):
        span = now - self.started
        fps = self.frames / span if span > 0 else 0.0
        simulation_hz = self.simulations / span if span > 0 else 0.0
        intervals = sorted(self.intervals)
        count = len(intervals)
        self.started = now
        self.frames = self.simulations = 0
        self.intervals.clear()
        if not count:
            return AimFrameMetrics(fps, simulation_hz, None, None, None, None)
        return AimFrameMetrics(
            fps, simulation_hz, sum(intervals) * 1000 / count,
            intervals[math.ceil(count * 0.95) - 1] * 1000,
            intervals[math.ceil(count * 0.99) - 1] * 1000,
            100 * sum(interval > target for interval in intervals) / count,
        )
