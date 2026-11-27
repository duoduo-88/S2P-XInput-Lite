import configparser
import sys
import threading
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from audio_haptics import AudioHaptics, _voice_cues  # noqa: E402


SAMPLE_RATE = 48000
HOP = 240
ANALYSIS = 960
VOICE_FRAMES = SAMPLE_RATE // 25


def _band_noise(seconds, low_hz, high_hz, seed):
    count = int(seconds * SAMPLE_RATE)
    noise = np.random.default_rng(seed).standard_normal(count)
    spectrum = np.fft.rfft(noise)
    frequencies = np.fft.rfftfreq(count, 1.0 / SAMPLE_RATE)
    spectrum[(frequencies < low_hz) | (frequencies >= high_hz)] = 0.0
    return np.fft.irfft(spectrum, count).astype(np.float32)


def _decay(count, attack_s, decay_s):
    t = np.arange(count) / SAMPLE_RATE
    rise = np.minimum(1.0, t / attack_s)
    return (rise * np.exp(-np.maximum(0.0, t - attack_s) / decay_s)).astype(
        np.float32
    )


def _scale_peak(signal, peak):
    return (signal * (peak / float(np.max(np.abs(signal))))).astype(np.float32)


def _voiced_speech(seconds=2.0, f0=125.0, rms=0.08, syllables=True):
    """A harmonic series with formant weighting and 4 Hz syllables."""
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    pitch = f0 * (1.0 + 0.08 * np.sin(2 * np.pi * 1.3 * t))
    phase = 2 * np.pi * np.cumsum(pitch) / SAMPLE_RATE
    voice = np.zeros_like(t)
    for harmonic in range(1, 30):
        frequency = harmonic * f0
        if frequency > 4000:
            break
        formants = (
            np.exp(-((frequency - 550) / 220) ** 2)
            + 0.6 * np.exp(-((frequency - 1400) / 300) ** 2)
            + 0.25
        )
        voice += formants / harmonic ** 0.6 * np.sin(harmonic * phase)
    if syllables:
        voice *= np.clip(np.sin(2 * np.pi * 4.0 * t), 0.0, None) ** 0.5
    voice = voice.astype(np.float32)
    active = voice[np.abs(voice) > 1e-4]
    return voice * (rms / float(np.sqrt(np.mean(active ** 2))))


def _punch():
    count = int(0.2 * SAMPLE_RATE)
    return _scale_peak(
        _band_noise(0.2, 60, 600, 3) * _decay(count, 0.003, 0.05), 0.55
    )


def _clang():
    count = int(0.6 * SAMPLE_RATE)
    t = np.arange(count) / SAMPLE_RATE
    ring = np.zeros(count)
    for frequency, amplitude, decay in (
        (1180, 1.0, 0.30), (2870, 0.8, 0.22), (4310, 0.6, 0.18),
        (5630, 0.4, 0.12), (620, 0.5, 0.20),
    ):
        ring += amplitude * np.sin(2 * np.pi * frequency * t) * np.exp(-t / decay)
    return _scale_peak(ring.astype(np.float32), 0.6)


class VoiceCueTests(unittest.TestCase):
    def test_voiced_speech_is_recognised(self):
        speech = _voiced_speech()
        # The middle of a syllable (4 Hz, so 62.5 ms after a cycle starts).
        end = int(0.5 * SAMPLE_RATE) + int(0.0625 * SAMPLE_RATE)
        harmonicity, focus, _energy = _voice_cues(
            speech[end - VOICE_FRAMES:end], SAMPLE_RATE
        )
        self.assertGreaterEqual(harmonicity, 0.8)
        self.assertGreaterEqual(focus, 0.8)

    def test_noise_is_not_harmonic(self):
        noise = _band_noise(0.1, 60, 6000, 1)
        harmonicity, _focus, _energy = _voice_cues(
            noise[-VOICE_FRAMES:], SAMPLE_RATE
        )
        self.assertLess(harmonicity, 0.3)

    def test_sub_bass_rumble_is_not_voice(self):
        rumble = _band_noise(0.1, 20, 90, 2)
        _harmonicity, focus, _energy = _voice_cues(
            rumble[-VOICE_FRAMES:], SAMPLE_RATE
        )
        self.assertLess(focus, 0.2)

    def test_ringing_metal_is_not_voice_even_though_it_is_pitched(self):
        clang = _clang()
        _harmonicity, focus, _energy = _voice_cues(
            clang[:VOICE_FRAMES], SAMPLE_RATE
        )
        self.assertLess(focus, 0.2)

    def test_silence_has_no_cues(self):
        self.assertEqual(
            _voice_cues(np.zeros(VOICE_FRAMES, np.float32), SAMPLE_RATE),
            (0.0, 0.0, 0.0),
        )


class VoiceSuppressionPipelineTests(unittest.TestCase):
    def _run(self, signal, suppression=None, profile="System Default"):
        config = configparser.ConfigParser()
        self.assertTrue(config.read(
            ROOT / "src" / "profiles" / f"{profile}.ini", encoding="utf-8"
        ))
        config.set("audio_haptics", "mode", "AUDIO")
        if suppression is None:
            config.remove_option("audio_haptics", "voice_suppression")
        else:
            config.set("audio_haptics", "voice_suppression", str(suppression))
        levels = []
        audio = AudioHaptics(
            config, lambda lf, hf, *_tilts: levels.append((lf, hf))
        )
        stop = threading.Event()
        pcm = np.asarray(signal, dtype=np.float32)
        chunks = [
            pcm[start:start + HOP].tobytes()
            for start in range(0, len(pcm) - HOP + 1, HOP)
        ]
        hop_count = len(chunks)

        class Stream:
            @staticmethod
            def get_read_available():
                return HOP

            @staticmethod
            def read(*_args, **_kwargs):
                chunk = chunks.pop(0)
                if not chunks:
                    stop.set()
                return chunk

        audio._running = True
        audio._process_stream(
            SAMPLE_RATE, 1, HOP, ANALYSIS, stream=Stream(), stop_event=stop
        )
        return np.array(levels[:hop_count])

    @staticmethod
    def _hop(seconds):
        return int(seconds * SAMPLE_RATE / HOP)

    def test_zero_suppression_keeps_the_original_output(self):
        signal = np.concatenate([_voiced_speech(1.0), _punch()])
        np.testing.assert_array_equal(
            self._run(signal, None), self._run(signal, 0.0)
        )

    def test_suppression_removes_most_dialogue_rumble(self):
        speech = _voiced_speech()
        plain = self._run(speech, 0.0, "Movie")
        cut = self._run(speech, 1.0, "Movie")
        settle = self._hop(0.25)
        self.assertGreater(plain[settle:, 0].mean(), 0.02)
        self.assertLess(
            cut[settle:, 0].mean(), 0.3 * plain[settle:, 0].mean()
        )

    def test_a_sentence_onset_does_not_thump(self):
        # Speech that starts at full level on a vowel after a pause.  The
        # voice window needs a few hops to recognise it, which must not let
        # the first syllable through as a jolt.
        gap = np.zeros(int(0.5 * SAMPLE_RATE), np.float32)
        vowel = _voiced_speech(0.15, rms=0.25, syllables=False)
        signal = np.concatenate([gap, vowel, gap])
        window = slice(self._hop(0.5), self._hop(0.8))
        plain = self._run(signal, 0.0, "Movie")[window, 0].max()
        cut = self._run(signal, 1.0, "Movie")[window, 0].max()
        self.assertGreater(plain, 0.1)
        self.assertLess(cut, 0.4 * plain)

    def test_impacts_over_dialogue_keep_their_rumble(self):
        speech = _voiced_speech(3.0)
        alone = np.zeros_like(speech)
        events = ((1.0, _punch()), (2.0, _clang()))
        for at, clip in events:
            start = int(at * SAMPLE_RATE)
            speech[start:start + len(clip)] += clip
            alone[start:start + len(clip)] += clip
        reference = self._run(alone, 0.0, "Movie")
        cut = self._run(speech, 1.0, "Movie")
        for at, _clip in events:
            window = slice(self._hop(at), self._hop(at + 0.15))
            for channel in (0, 1):
                expected = reference[window, channel].max()
                if expected < 0.05:
                    continue
                self.assertGreaterEqual(
                    cut[window, channel].max(), 0.6 * expected, (at, channel)
                )


if __name__ == "__main__":
    unittest.main()
