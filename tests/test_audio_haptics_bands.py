import configparser
import sys
import threading
import time
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audio_haptics import (
    AUDIO_BANDS_HZ,
    AudioHaptics,
    _route_band_levels,
    _spectral_band_rms,
)


class AudioHapticsBandTests(unittest.TestCase):
    def _default_audio(self, callback=lambda _lf, _hf: None):
        config = configparser.ConfigParser()
        self.assertTrue(config.read(
            ROOT / "src" / "profiles" / "System Default.ini",
            encoding="utf-8",
        ))
        return AudioHaptics(config, callback)

    def test_six_bands_split_the_high_range_at_4000_hz(self):
        self.assertEqual(
            AUDIO_BANDS_HZ,
            (
                (20.0, 120.0),
                (120.0, 300.0),
                (300.0, 700.0),
                (700.0, 2000.0),
                (2000.0, 4000.0),
                (4000.0, 8000.0),
            ),
        )

    def test_high_frequency_tones_reach_their_separate_bands(self):
        sample_rate = 48000
        sample_count = sample_rate // 50
        time_axis = np.arange(sample_count, dtype=np.float32) / sample_rate

        lower_high = np.sin(2.0 * np.pi * 2500.0 * time_axis)
        upper_high = np.sin(2.0 * np.pi * 6000.0 * time_axis)

        self.assertEqual(
            int(np.argmax(_spectral_band_rms(lower_high, sample_rate))),
            4,
        )
        self.assertEqual(
            int(np.argmax(_spectral_band_rms(upper_high, sample_rate))),
            5,
        )

    def test_balance_moves_middle_band_without_moving_anchors(self):
        middle = (0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        lf_bias = _route_band_levels(middle, -1.0)
        hf_bias = _route_band_levels(middle, 1.0)
        self.assertGreater(lf_bias[0], hf_bias[0])
        self.assertLess(lf_bias[1], hf_bias[1])

        bass = _route_band_levels((1, 0, 0, 0, 0, 0), 1.0)
        detail = _route_band_levels((0, 0, 0, 0, 0, 1), -1.0)
        self.assertEqual(bass, (1.0, 0.0))
        self.assertEqual(detail, (0.0, 0.75))

    def test_packaged_default_loads_six_gains_and_balance(self):
        audio = self._default_audio()

        self.assertEqual(len(audio.band_gains), 6)
        self.assertEqual(audio.lf_hf_balance, 0.0)

    def test_close_wakes_capture_before_a_blocking_read(self):
        class EmptyStream:
            read_calls = 0

            @staticmethod
            def get_read_available():
                return 0

            def read(self, *_args, **_kwargs):
                self.read_calls += 1
                raise AssertionError("read must wait until a full hop is ready")

        audio = self._default_audio()
        audio.mode = "AUDIO"
        audio._stream = EmptyStream()
        audio._running = True
        audio._thread = threading.Thread(
            target=audio._process_stream,
            args=(48000, 2, 240, 960),
        )
        audio._thread.start()
        time.sleep(0.01)

        started = time.perf_counter()
        self.assertTrue(audio.close())
        elapsed = time.perf_counter() - started

        self.assertFalse(audio._thread.is_alive())
        self.assertEqual(audio._stream.read_calls, 0)
        self.assertLess(elapsed, 0.1)

    def test_game_mode_drains_without_dsp_callbacks(self):
        callbacks = []
        audio = self._default_audio(
            lambda lf, hf: callbacks.append((lf, hf))
        )

        class OneHopStream:
            @staticmethod
            def get_read_available():
                return 240

            def read(self, *_args, **_kwargs):
                audio._running = False
                return np.zeros(480, dtype=np.float32).tobytes()

        audio.mode = "GAME"
        audio._stream = OneHopStream()
        audio._running = True
        audio._process_stream(48000, 2, 240, 960)

        self.assertEqual(callbacks, [])


if __name__ == "__main__":
    unittest.main()
