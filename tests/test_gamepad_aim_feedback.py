import io
import queue
import struct
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

import gamepad_aim_feedback as feedback_module
from gamepad_aim_feedback import AimFeedback


class Scheduler:
    def __init__(self):
        self.jobs = {}
        self.next_id = 0

    def after(self, delay, callback):
        self.next_id += 1
        self.jobs[self.next_id] = (delay, callback)
        return self.next_id

    def after_cancel(self, job):
        del self.jobs[job]

    def run_pulse_end(self):
        _, callback = self.jobs.popitem()[1]
        callback()


class RecordingSound:
    SND_MEMORY = 4
    SND_NODEFAULT = 2

    def __init__(self):
        self.played = queue.Queue()

    def PlaySound(self, data, flags):
        self.played.put((data, flags))


class AimFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler()
        self.rumble = []
        self.feedback = AimFeedback(self.scheduler, lambda *motors: self.rumble.append(motors))
        self.addCleanup(self.feedback.close)

    def test_contact_pulses_on_entry_and_repeats_without_catching_up(self):
        self.feedback.contact(True, 0.5, False, 0.0)
        self.assertEqual(len(self.rumble), 1)
        self.assertGreater(max(self.rumble[-1]), 0)
        self.assertLessEqual(max(self.rumble[-1]), 0.5)
        for now in (0.01, 0.03, 0.11):
            self.feedback.contact(True, 0.5, False, now)
        self.assertEqual(len(self.rumble), 1)

        self.scheduler.run_pulse_end()
        self.assertEqual(self.rumble[-1], (0, 0))
        self.feedback.contact(True, 0.5, False, 0.12)
        self.assertEqual(self.rumble[-1], self.rumble[0])
        self.scheduler.run_pulse_end()
        self.feedback.contact(True, 0.5, False, 1.0)
        self.assertEqual(self.rumble, [self.rumble[0], (0, 0)] * 2 + [self.rumble[0]])
        self.assertEqual(len(self.scheduler.jobs), 1)

    def test_leaving_contact_stops_pulse_and_rearms_immediately(self):
        self.feedback.contact(True, 0.5, False, 1.0)
        self.feedback.contact(False, 0.5, False, 1.01)
        self.feedback.contact(False, 0.5, False, 1.02)
        self.assertEqual(self.rumble, [self.rumble[0], (0, 0)])
        self.assertFalse(self.scheduler.jobs)

        self.feedback.contact(True, 0.5, False, 1.03)
        self.assertEqual(self.rumble[-1], self.rumble[0])
        self.assertEqual(len(self.scheduler.jobs), 1)

    def test_hit_replaces_contact_and_survives_target_advance(self):
        self.feedback.contact(True, 0.5, False, 1.0)
        contact_duration, _ = next(iter(self.scheduler.jobs.values()))
        self.feedback.hit(0.5, False)
        self.assertGreater(max(self.rumble[-1]), max(self.rumble[0]))
        self.assertEqual(len(self.scheduler.jobs), 1)
        hit_duration, _ = next(iter(self.scheduler.jobs.values()))
        self.assertGreater(hit_duration, contact_duration)

        self.feedback.contact(False, 0.5, False, 1.01)
        self.feedback.contact(True, 0.5, False, 1.02)
        self.assertEqual(len(self.rumble), 2)
        self.scheduler.run_pulse_end()
        self.assertEqual(self.rumble[-1], (0, 0))

    def test_stop_releases_motors_and_close_prevents_future_output(self):
        self.feedback.hit(1.0, False)
        self.feedback.stop()
        self.feedback.stop()
        self.assertEqual(len(self.rumble), 2)
        self.assertEqual(self.rumble[-1], (0, 0))
        self.assertFalse(self.scheduler.jobs)

        self.feedback.contact(True, 0.5, False, 1.0)
        self.feedback.close()
        self.feedback.hit(0.5, False)
        self.feedback.contact(True, 0.5, False, 2.0)
        self.assertEqual(len(self.rumble), 4)
        self.assertEqual(self.rumble[-1], (0, 0))
        self.assertFalse(self.scheduler.jobs)

    def test_zero_strength_releases_contact_without_restarting_it(self):
        self.feedback.contact(True, 0.5, False, 1.0)
        self.feedback.contact(True, 0.0, False, 1.01)
        self.feedback.contact(True, 0.0, False, 2.0)
        self.feedback.hit(0.0, False)
        self.assertEqual(len(self.rumble), 2)
        self.assertEqual(self.rumble[-1], (0, 0))
        self.assertFalse(self.scheduler.jobs)

    def test_failed_release_retries_the_same_slot_with_the_remaining_mix(self):
        from gamepad_test_window import GamepadTestWindow

        window = object.__new__(GamepadTestWindow)
        window._manual_rumble = (0.25, 0.0)
        window._rumble_layers = {1: (0.0, 0.25)}
        window._rumble_target = lambda: 0
        window._active_rumble_slot = None
        writes = []

        def write(slot, left, right):
            writes.append((slot, left, right))
            return len(writes) != 2

        window.backend = SimpleNamespace(set_xinput_rumble=write)
        feedback = AimFeedback(self.scheduler, window._set_aim_rumble)
        self.addCleanup(feedback.close)
        feedback.hit(0.5, False)
        self.scheduler.run_pulse_end()
        self.assertEqual(len(self.scheduler.jobs), 1)
        self.scheduler.run_pulse_end()
        self.assertEqual(writes, [(0, 0.75, 0.61), (0, 0.25, 0.25), (0, 0.25, 0.25)])
        self.assertEqual(window._rumble_layers, {1: (0.0, 0.25)})
        self.assertFalse(self.scheduler.jobs)

    def test_repeated_release_failure_does_not_leave_an_endless_timer(self):
        def write(left, right):
            self.rumble.append((left, right))
            return bool(left or right)

        feedback = AimFeedback(self.scheduler, write)
        self.addCleanup(feedback.close)
        feedback.contact(True, 0.5, False, 0.0)
        self.scheduler.run_pulse_end()
        for index in range(10):
            feedback.contact(False, 0.5, False, 0.04 + index / 100)
            if self.scheduler.jobs:
                self.scheduler.run_pulse_end()
        self.assertEqual(self.rumble[1:], [(0, 0)] * 3)
        self.assertFalse(self.scheduler.jobs)

    def test_haptics_work_without_winsound(self):
        with patch.object(feedback_module, "winsound", None):
            self.feedback.contact(True, 0.5, True, 1.0)
            self.feedback.hit(0.5, True)
        self.assertEqual(len(self.rumble), 2)
        self.assertGreater(max(self.rumble[-1]), 0)

    def test_sound_is_optional_and_hit_wave_is_louder_and_longer(self):
        sound = RecordingSound()
        with patch.object(feedback_module, "winsound", sound):
            self.feedback.contact(True, 0.0, False, 0.0)
            self.feedback.hit(0.0, False)
            with self.assertRaises(queue.Empty):
                sound.played.get(timeout=0.05)
            self.feedback.contact(True, 0.0, True, 0.12)
            tap = sound.played.get(timeout=1)
            self.feedback.contact(True, 0.0, True, 0.24)
            self.assertEqual(sound.played.get(timeout=1), tap)
            self.feedback.hit(0.0, True)
            hit = sound.played.get(timeout=1)

        def wave_properties(cue):
            data, flags = cue
            self.assertEqual(flags, sound.SND_MEMORY | sound.SND_NODEFAULT)
            with wave.open(io.BytesIO(data)) as pcm:
                self.assertEqual((pcm.getnchannels(), pcm.getsampwidth()), (1, 2))
                frames = pcm.getnframes()
                samples = struct.unpack(f"<{frames}h", pcm.readframes(frames))
                return frames / pcm.getframerate(), max(abs(value) for value in samples)

        tap_duration, tap_peak = wave_properties(tap)
        hit_duration, hit_peak = wave_properties(hit)
        self.assertGreater(tap_peak, 0)
        self.assertGreater(hit_peak, tap_peak)
        self.assertGreater(hit_duration, tap_duration)
        self.assertLess(hit_duration, 0.12)

    def test_miss_respects_sound_toggle(self):
        sound = RecordingSound()
        with patch.object(feedback_module, "winsound", sound):
            self.feedback.miss(False)
            with self.assertRaises(queue.Empty):
                sound.played.get(timeout=0.05)

    def test_miss_plays_its_own_short_cue_without_rumble(self):
        sound = RecordingSound()
        with patch.object(feedback_module, "winsound", sound):
            self.feedback.contact(True, 0, True, 0.0)
            tap, _ = sound.played.get(timeout=1)
            self.feedback.hit(0, True)
            hit, _ = sound.played.get(timeout=1)
            self.feedback.miss(True)
            try:
                miss, _ = sound.played.get(timeout=1)
            except queue.Empty:
                self.fail("A miss with sound enabled did not play a cue")

        self.assertNotIn(miss, (tap, hit))
        with wave.open(io.BytesIO(miss)) as pcm:
            self.assertLess(pcm.getnframes() / pcm.getframerate(), 0.12)
            self.assertTrue(any(pcm.readframes(pcm.getnframes())))
        self.assertFalse(self.rumble)
        self.assertFalse(self.scheduler.jobs)
