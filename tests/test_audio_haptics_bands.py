import configparser
import sys
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
        config = configparser.ConfigParser()
        self.assertTrue(config.read(
            ROOT / "src" / "profiles" / "System Default.ini",
            encoding="utf-8",
        ))

        audio = AudioHaptics(config, lambda _lf, _hf: None)

        self.assertEqual(len(audio.band_gains), 6)
        self.assertEqual(audio.lf_hf_balance, 0.0)


if __name__ == "__main__":
    unittest.main()
