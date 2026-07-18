"""Convert Windows system-output audio into LF/HF rumble levels."""

import math
import threading

import numpy as np
from console_i18n import localized_print as print


AUDIO_BANDS_HZ = (
    (20.0, 120.0),
    (120.0, 300.0),
    (300.0, 700.0),
    (700.0, 2000.0),
    (2000.0, 8000.0),
)


def _spectral_band_rms(samples, sample_rate):
    """Return low-latency, window-corrected RMS for the five bands."""
    mono = np.asarray(samples, dtype=np.float32)
    sample_count = int(mono.size)
    if sample_count < 8 or sample_rate <= 0:
        return (0.0,) * len(AUDIO_BANDS_HZ)
    # A symmetric Hann window suppresses the newest samples at its right
    # edge, adding perceptible onset lag in a causal real-time stream. This
    # rising half-sine keeps leakage controlled while giving the newest audio
    # full weight and older overlapping samples progressively less weight.
    window = np.sin(
        np.linspace(0.0, math.pi / 2.0, sample_count, dtype=np.float32)
    )
    window_power = float(np.mean(window * window))
    spectrum = np.fft.rfft(mono * window)
    power = np.abs(spectrum) ** 2
    frequencies = np.fft.rfftfreq(sample_count, 1.0 / float(sample_rate))
    denominator = max(1e-12, sample_count * sample_count * window_power)
    levels = []
    for low_hz, high_hz in AUDIO_BANDS_HZ:
        mask = (frequencies >= low_hz) & (frequencies < high_hz)
        band_power = float(np.sum(power[mask])) if np.any(mask) else 0.0
        levels.append(math.sqrt(max(0.0, 2.0 * band_power / denominator)))
    return tuple(levels)


class AudioHaptics:
    """Capture the default WASAPI loopback device on a background thread."""

    def __init__(self, config, level_callback):
        section = "audio_haptics"
        self.mode = config.get(section, "mode", fallback="GAME").strip().upper()
        if self.mode not in ("GAME", "AUDIO", "MIX"):
            self.mode = "GAME"
        self.strength = _bounded_float(config, section, "strength", 0.60, 0, 1)
        legacy_low_gain = _bounded_float(
            config, section, "low_gain", 1.0, 0, 2
        )
        legacy_high_gain = _bounded_float(
            config, section, "high_gain", 1.0, 0, 2
        )
        legacy_band_gains = (
            legacy_low_gain,
            (2.0 * legacy_low_gain + legacy_high_gain) / 3.0,
            (legacy_low_gain + legacy_high_gain) / 2.0,
            (legacy_low_gain + 2.0 * legacy_high_gain) / 3.0,
            legacy_high_gain,
        )
        legacy_band_gains = tuple(round(value, 2) for value in legacy_band_gains)
        self.band_gains = tuple(
            _bounded_float(
                config, section, f"band_{index}_gain", fallback, 0, 2
            )
            for index, fallback in enumerate(legacy_band_gains, start=1)
        )
        self.noise_gate = _bounded_float(
            config, section, "noise_gate", 0.015, 0, 0.25
        )
        self.attack_ms = _bounded_float(config, section, "attack_ms", 1, 1, 500)
        self.release_ms = _bounded_float(
            config, section, "release_ms", 140, 5, 2000
        )
        self._level_callback = level_callback
        self._running = False
        self._thread = None
        self._stream = None
        self._audio = None

    def start(self):
        if self.mode == "GAME" or self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name="AudioHapticsCapture",
        )
        self._thread.start()

    def close(self):
        self._running = False
        stream = self._stream
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if (
            self._thread is not None
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=1.0)
        self._level_callback(0.0, 0.0)

    def _capture_loop(self):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            print("音訊震動無法啟動：缺少 PyAudioWPatch；遊戲震動仍可正常使用。")
            self._running = False
            return

        try:
            self._audio = pyaudio.PyAudio()
            device = self._default_loopback_device(pyaudio)
            sample_rate = int(device["defaultSampleRate"])
            channels = max(1, int(device["maxInputChannels"]))
            # Read a small hop for low latency, while retaining a 20 ms
            # rolling window so the lowest EQ bands keep useful resolution.
            hop_frames = max(128, min(512, sample_rate // 200))
            analysis_frames = max(512, min(2048, sample_rate // 50))
            self._stream = self._audio.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=int(device["index"]),
                frames_per_buffer=hop_frames,
            )
            print(f"音訊震動已啟動：{device['name']}")
            self._process_stream(
                sample_rate, channels, hop_frames, analysis_frames
            )
        except Exception as exc:
            if self._running:
                print(f"音訊震動擷取失敗：{exc}")
        finally:
            self._running = False
            self._level_callback(0.0, 0.0)
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            if self._audio is not None:
                try:
                    self._audio.terminate()
                except Exception:
                    pass
                self._audio = None

    def _default_loopback_device(self, pyaudio):
        wasapi = self._audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        output = self._audio.get_device_info_by_index(
            wasapi["defaultOutputDevice"]
        )
        if output.get("isLoopbackDevice"):
            return output
        loopback = self._audio.get_wasapi_loopback_analogue_by_dict(output)
        if loopback is None:
            raise RuntimeError("找不到預設 Windows 輸出裝置的 loopback 端點")
        return loopback

    def _process_stream(
        self, sample_rate, channels, hop_frames, analysis_frames=None
    ):
        lf_envelope = 0.0
        hf_envelope = 0.0
        if analysis_frames is None:
            analysis_frames = max(hop_frames, sample_rate // 50)
        analysis_frames = max(hop_frames, int(analysis_frames))
        # Starting with silence lets a new transient affect the first 5 ms
        # update instead of waiting for an entire 20 ms window to fill.
        rolling_audio = np.zeros(analysis_frames, dtype=np.float32)

        while self._running:
            data = self._stream.read(
                hop_frames, exception_on_overflow=False
            )
            samples = np.frombuffer(data, dtype=np.float32)
            frame_count = len(samples) // channels
            if frame_count <= 0:
                continue
            samples = samples[:frame_count * channels].reshape(
                frame_count, channels
            )
            mono = np.mean(samples, axis=1)
            rolling_audio = np.concatenate((rolling_audio, mono))[
                -analysis_frames:
            ]
            band_rms = _spectral_band_rms(rolling_audio, sample_rate)
            band_levels = tuple(
                self._level_from_rms(rms, gain)
                for rms, gain in zip(band_rms, self.band_gains)
            )
            lf_target = min(
                1.0,
                max(
                    band_levels[0],
                    band_levels[1] * 0.80,
                    band_levels[2] * 0.35,
                ),
            )
            hf_target = min(
                1.0,
                max(
                    band_levels[2] * 0.25,
                    band_levels[3] * 0.75,
                    band_levels[4],
                ),
            )
            block_seconds = frame_count / sample_rate
            lf_envelope = self._smooth_envelope(
                lf_envelope, lf_target, block_seconds
            )
            hf_envelope = self._smooth_envelope(
                hf_envelope, hf_target, block_seconds
            )
            self._level_callback(lf_envelope, hf_envelope)

    def _level_from_rms(self, rms, gain):
        if rms <= self.noise_gate:
            return 0.0
        usable = (rms - self.noise_gate) / max(1e-6, 1.0 - self.noise_gate)
        return min(1.0, usable * 4.0 * gain * self.strength)

    def _smooth_envelope(self, current, target, block_seconds):
        duration_ms = self.attack_ms if target > current else self.release_ms
        coefficient = 1.0 - math.exp(
            -block_seconds / max(0.001, duration_ms / 1000.0)
        )
        return current + (target - current) * coefficient


def _bounded_float(config, section, option, fallback, minimum, maximum):
    try:
        value = config.getfloat(section, option, fallback=fallback)
    except (ValueError, TypeError):
        value = fallback
    return max(minimum, min(maximum, value))
