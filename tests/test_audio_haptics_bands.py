import configparser
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audio_haptics import (
    AUDIO_BANDS_HZ,
    AudioHaptics,
    _dsp_v2_targets,
    _dynamic_audio_targets,
    _empty_feature_state,
    _extract_audio_features,
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

    def _run_synthetic(
        self,
        waveform,
        sample_rate=48000,
        hop_frames=240,
        strength=0.32,
        band_gains=(1.0,) * 6,
    ):
        """Run real waveforms through rolling FFT, feature, and target DSP."""
        analysis_frames = sample_rate // 50
        rolling = np.zeros(analysis_frames, dtype=np.float32)
        previous = (0.0,) * len(AUDIO_BANDS_HZ)
        previous_feature_levels = (0.0,) * len(AUDIO_BANDS_HZ)
        state = _empty_feature_state()
        records = []
        waveform = np.asarray(waveform, dtype=np.float32)
        for start in range(0, len(waveform), hop_frames):
            mono = waveform[start:start + hop_frames]
            if len(mono) != hop_frames:
                mono = np.pad(mono, (0, hop_frames - len(mono)))
            rolling[:-hop_frames] = rolling[hop_frames:]
            rolling[-hop_frames:] = mono
            band_rms = _spectral_band_rms(rolling, sample_rate)
            usable = tuple(max(0.0, (rms - 0.04) / 0.96) for rms in band_rms)
            feature_energies = tuple(
                energy * np.sqrt(gain)
                for energy, gain in zip(usable, band_gains)
            )
            levels = tuple(
                min(1.0, energy * 4.0 * gain * strength)
                for energy, gain in zip(usable, band_gains)
            )
            feature_levels = tuple(min(1.0, energy) for energy in feature_energies)
            lf, hf, state, features, tactile = _dsp_v2_targets(
                levels,
                previous,
                0.0,
                state,
                hop_frames / sample_rate,
                analysis_frames / sample_rate,
                feature_levels=feature_levels,
                previous_feature_levels=previous_feature_levels,
                band_energies=feature_energies,
                hop_rms=float(np.sqrt(np.mean(mono * mono))),
                peak_level=float(np.max(np.abs(mono))),
            )
            records.append((lf, hf, features, tactile, levels))
            previous = levels
            previous_feature_levels = feature_levels
        return records

    @staticmethod
    def _sine(frequency, seconds, amplitude=0.65, sample_rate=48000):
        axis = np.arange(int(seconds * sample_rate), dtype=np.float32) / sample_rate
        return amplitude * np.sin(2.0 * np.pi * frequency * axis)

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

    def test_band_gain_influences_ratios_without_linearly_dominating_them(self):
        audio = self._default_audio()
        raw_rms = (0.40,) * 6
        unity = tuple(
            audio._feature_energy_from_rms(rms, 1.0) for rms in raw_rms
        )
        low_boost = tuple(
            audio._feature_energy_from_rms(rms, 2.0 if index < 2 else 1.0)
            for index, rms in enumerate(raw_rms)
        )
        levels = tuple(min(1.0, energy) for energy in unity)
        unity_features, _ = _extract_audio_features(
            levels, (0.0,) * 6, _empty_feature_state(), 0.005, 0.020,
            band_energies=unity,
        )
        boosted_features, _ = _extract_audio_features(
            levels, (0.0,) * 6, _empty_feature_state(), 0.005, 0.020,
            band_energies=low_boost,
        )

        self.assertAlmostEqual(
            unity_features.low_ratio + unity_features.mid_ratio + unity_features.high_ratio,
            1.0,
        )
        self.assertGreater(boosted_features.low_ratio, unity_features.low_ratio)
        self.assertLess(boosted_features.low_ratio, 0.5)

    def test_band_gain_keeps_the_original_linear_production_curve(self):
        audio = self._default_audio()
        rms = 0.40
        usable = (rms - audio.noise_gate) / (1.0 - audio.noise_gate)
        for gain in (0.0, 0.5, 1.0, 1.5, 2.0):
            expected = min(1.0, usable * 4.0 * gain * audio.strength)
            self.assertAlmostEqual(audio._level_from_rms(rms, gain), expected)

    def test_strength_scales_targets_without_changing_tactile_classification(self):
        waveform = self._sine(6000.0, 0.015)
        low_strength = self._run_synthetic(waveform, strength=0.16)
        high_strength = self._run_synthetic(waveform, strength=0.64)
        low = max(low_strength, key=lambda item: item[3].sharpness)
        high = max(high_strength, key=lambda item: item[3].sharpness)

        for field in (
            "overall_level", "low_ratio", "mid_ratio", "high_ratio",
            "positive_flux", "negative_flux", "crest", "energy_stability",
            "sustain_evidence",
        ):
            self.assertAlmostEqual(getattr(low[2], field), getattr(high[2], field))
        for field in ("punch", "weight", "sharpness", "sustain", "texture"):
            self.assertAlmostEqual(getattr(low[3], field), getattr(high[3], field))
        self.assertGreater(high[1], low[1])


    def test_dynamic_targets_emphasize_new_bass_attack(self):
        bands = (0.60, 0.10, 0.0, 0.0, 0.0, 0.0)
        base_lf, base_hf = _route_band_levels(bands)

        lf, hf, _fast, _slow, transient = _dynamic_audio_targets(
            bands,
            (0.0,) * 6,
            0.0,
            0.0,
            0.0,
            0.005,
            0.020,
        )

        self.assertGreater(transient, 0.5)
        self.assertGreater(lf, base_lf)
        self.assertGreaterEqual(hf, base_hf)
        self.assertGreater(lf - base_lf, hf - base_hf)

    def test_dynamic_targets_emphasize_new_high_frequency_attack(self):
        bands = (0.0, 0.0, 0.0, 0.10, 0.40, 0.60)
        base_lf, base_hf = _route_band_levels(bands)

        lf, hf, _fast, _slow, transient = _dynamic_audio_targets(
            bands,
            (0.0,) * 6,
            0.0,
            0.0,
            0.0,
            0.005,
            0.020,
        )

        self.assertGreater(transient, 0.5)
        self.assertGreaterEqual(lf, base_lf)
        self.assertGreater(hf, base_hf)
        self.assertGreater(hf - base_hf, lf - base_lf)

    def test_dynamic_targets_converge_back_to_sustained_body(self):
        bands = (0.60, 0.10, 0.0, 0.0, 0.0, 0.0)
        base_lf, base_hf = _route_band_levels(bands)
        previous = (0.0,) * 6
        fast = slow = 0.0

        for _ in range(100):
            lf, hf, fast, slow, transient = _dynamic_audio_targets(
                bands,
                previous,
                0.0,
                fast,
                slow,
                0.005,
                0.020,
            )
            previous = bands

        self.assertLess(transient, 0.01)
        self.assertAlmostEqual(lf, base_lf, places=6)
        self.assertAlmostEqual(hf, base_hf, places=6)

    def test_dynamic_targets_keep_silence_at_zero(self):
        result = _dynamic_audio_targets(
            (0.0,) * 6,
            (0.0,) * 6,
            0.0,
            0.0,
            0.0,
            0.005,
            0.020,
        )

        self.assertEqual(result[0:2], (0.0, 0.0))
        self.assertEqual(result[-1], 0.0)

    def test_sustained_bass_builds_weight_and_sustain_after_its_onset(self):
        records = self._run_synthetic(self._sine(80.0, 0.45))
        first = records[0]
        stable = records[-1]

        self.assertGreater(first[3].punch, stable[3].punch)
        self.assertGreater(stable[3].weight, first[3].weight)
        self.assertGreater(stable[3].sustain, first[3].sustain)
        self.assertGreater(stable[0], stable[1])
        self.assertLess(stable[3].sharpness, stable[3].weight)
        self.assertLess(stable[3].punch, 0.08)

    def test_bass_impact_has_more_punch_and_lf_attack_than_stable_bass(self):
        stable_bass = self._run_synthetic(self._sine(80.0, 0.45))[-1]
        burst = self._sine(80.0, 0.015)
        impact_records = self._run_synthetic(burst)
        impact = max(impact_records, key=lambda record: record[3].punch)
        impact_base_lf, _ = _route_band_levels(impact[4])

        self.assertGreater(impact[3].punch, stable_bass[3].punch)
        self.assertGreater(impact[0], impact_base_lf)
        self.assertGreater(impact[0] - impact_base_lf, impact[1])

    def test_equal_rms_high_crest_pulse_has_more_crest_and_punch(self):
        sample_rate = 48000
        duration = 0.12
        sine = self._sine(500.0, duration, amplitude=0.25)
        # A 1/16-duty pulse needs four times the sine RMS to match hop RMS.
        # That makes its peak ~0.71 here: high crest, but below full scale.
        pulse = np.zeros_like(sine)
        hop = sample_rate // 200
        for start in range(0, len(pulse), hop):
            pulse[start:start + max(1, hop // 16)] = 4.0 * np.sqrt(np.mean(sine * sine))
        sine_record = self._run_synthetic(sine)[-1]
        pulse_record = self._run_synthetic(pulse)[0]

        self.assertAlmostEqual(
            sine_record[2].rms_level, pulse_record[2].rms_level, places=4
        )
        self.assertGreater(pulse_record[2].crest, sine_record[2].crest)
        self.assertGreater(pulse_record[3].punch, sine_record[3].punch)

    def test_high_click_routes_sharp_attack_to_hf(self):
        click = self._sine(6000.0, 0.010)
        record = max(
            self._run_synthetic(click), key=lambda item: item[3].sharpness
        )

        self.assertGreater(record[2].high_ratio, record[2].low_ratio)
        self.assertGreater(record[3].sharpness, record[3].weight)
        self.assertGreater(record[1], record[0])

    def test_sustained_high_tone_keeps_hf_body_without_continuous_sharpness(self):
        records = self._run_synthetic(self._sine(6000.0, 0.45))
        onset = records[0]
        stable = records[-1]

        self.assertGreater(onset[3].punch, stable[3].punch)
        self.assertGreater(onset[3].sharpness, stable[3].sharpness)
        self.assertGreater(stable[1], stable[0])
        self.assertLess(stable[3].sharpness, 0.05)

    def test_mid_body_has_mid_ratio_without_weight_or_sharpness(self):
        stable = self._run_synthetic(self._sine(800.0, 0.45))[-1]

        self.assertGreater(stable[2].mid_ratio, stable[2].low_ratio)
        self.assertGreater(stable[2].mid_ratio, stable[2].high_ratio)
        self.assertLess(stable[3].weight, 0.1)
        self.assertLess(stable[3].sharpness, 0.05)

    def test_broadband_impact_has_bounded_lf_and_hf_attack(self):
        rng = np.random.default_rng(20260912)
        noise = rng.normal(0.0, 0.55, 480).astype(np.float32)
        record = max(self._run_synthetic(noise), key=lambda item: item[3].punch)

        self.assertGreater(record[3].punch, 0.2)
        self.assertGreater(record[0], 0.0)
        self.assertGreater(record[1], 0.0)
        self.assertLessEqual(record[0], 1.0)
        self.assertLessEqual(record[1], 1.0)

    def test_stable_loud_compressed_audio_is_sustain_not_impact(self):
        # Multi-tone constant-amplitude signal: high RMS, low variation,
        # moderate crest, and no recurring spectral onset after settling.
        signal = (
            self._sine(80.0, 0.50, 0.38)
            + self._sine(800.0, 0.50, 0.22)
            + self._sine(6000.0, 0.50, 0.16)
        )
        stable = self._run_synthetic(signal)[-1]

        self.assertGreater(stable[2].overall_level, 0.05)
        self.assertLess(stable[2].positive_flux, 0.08)
        self.assertLess(stable[3].punch, 0.08)
        self.assertGreater(stable[3].sustain, 0.5)

    def test_stable_bass_does_not_hide_a_new_high_event(self):
        silence = np.zeros(48000 // 100, dtype=np.float32)
        bass = self._sine(80.0, 0.25)
        high_burst = self._sine(6000.0, 0.015)
        records = self._run_synthetic(np.concatenate((silence, bass, high_burst, bass)))
        event = max(records, key=lambda item: item[3].sharpness)

        self.assertGreater(event[3].sharpness, 0.1)
        self.assertGreater(event[1], 0.04)

    def test_stable_high_body_does_not_hide_a_new_low_event(self):
        high = self._sine(6000.0, 0.25)
        low_burst = self._sine(80.0, 0.015)
        records = self._run_synthetic(np.concatenate((high, low_burst, high)))
        before = records[49]
        event = max(records[50:53], key=lambda item: item[2].positive_flux)

        self.assertGreater(event[3].punch, 0.2)
        self.assertGreater(event[0], before[0] + 0.3)
        self.assertGreater(event[0], event[1])

    def test_silence_resets_feature_state_before_the_next_attack(self):
        burst = self._sine(80.0, 0.015)
        silence = np.zeros(48000 // 2, dtype=np.float32)
        records = self._run_synthetic(np.concatenate((burst, silence, burst)))
        first = max(records[:3], key=lambda item: item[3].punch)
        second = max(records[-3:], key=lambda item: item[3].punch)

        self.assertGreater(first[3].punch, 0.1)
        self.assertGreater(second[3].punch, first[3].punch * 0.7)
        self.assertEqual(records[-4][2].sustain_evidence, 0.0)

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
        capture_thread = threading.Thread(
            target=audio._process_stream,
            args=(48000, 2, 240, 960),
        )
        audio._thread = capture_thread
        capture_thread.start()
        time.sleep(0.01)

        started = time.perf_counter()
        self.assertTrue(audio.close())
        elapsed = time.perf_counter() - started

        self.assertFalse(capture_thread.is_alive())
        self.assertIsNone(audio._thread)
        self.assertEqual(audio._stream.read_calls, 0)
        self.assertLess(elapsed, 0.1)

    def test_start_defers_until_winding_generation_has_exited(self):
        audio = self._default_audio()
        audio.mode = "AUDIO"
        old_thread = Mock()
        old_thread.is_alive.return_value = True
        audio._thread = old_thread
        audio._running = False
        audio._stop_event.set()

        self.assertFalse(audio.start())
        self.assertTrue(audio._restart_after_exit)

        old_thread.is_alive.return_value = False
        new_thread = Mock()
        with patch(
            "audio_haptics.threading.Thread",
            return_value=new_thread,
        ):
            self.assertTrue(audio.start())

        self.assertIs(audio._thread, new_thread)
        self.assertFalse(audio._stop_event.is_set())
        self.assertEqual(audio._generation, 1)
        new_thread.start.assert_called_once_with()

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

    def test_game_mode_clears_fft_history_before_audio_resumes(self):
        callbacks = []
        audio = self._default_audio(
            lambda lf, hf: callbacks.append((lf, hf))
        )

        class SwitchingStream:
            calls = 0

            @staticmethod
            def get_read_available():
                return 240

            def read(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    audio.mode = "AUDIO"
                    return np.ones(240, dtype=np.float32).tobytes()
                if self.calls == 2:
                    audio.mode = "GAME"
                    return np.zeros(240, dtype=np.float32).tobytes()
                audio.mode = "AUDIO"
                audio._running = False
                return np.zeros(240, dtype=np.float32).tobytes()

        audio.mode = "AUDIO"
        audio._stream = SwitchingStream()
        audio._running = True
        audio._process_stream(48000, 1, 240, 960)

        self.assertGreater(max(callbacks[0]), 0.0)
        self.assertEqual(callbacks[-1], (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
