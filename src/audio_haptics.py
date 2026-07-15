"""Convert Windows system-output audio into LF/HF rumble levels."""

from array import array
import math
import threading
from console_i18n import localized_print as print


class AudioHaptics:
    """Capture the default WASAPI loopback device on a background thread."""

    def __init__(self, config, level_callback):
        section = "audio_haptics"
        self.mode = config.get(section, "mode", fallback="GAME").strip().upper()
        if self.mode not in ("GAME", "AUDIO", "MIX"):
            self.mode = "GAME"
        self.strength = _bounded_float(config, section, "strength", 0.60, 0, 1)
        self.low_gain = _bounded_float(config, section, "low_gain", 1.0, 0, 2)
        self.high_gain = _bounded_float(config, section, "high_gain", 0.65, 0, 2)
        self.noise_gate = _bounded_float(
            config, section, "noise_gate", 0.015, 0, 0.25
        )
        self.crossover_hz = _bounded_float(
            config, section, "crossover_hz", 160, 40, 1000
        )
        self.attack_ms = _bounded_float(config, section, "attack_ms", 20, 1, 500)
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
            frames = max(128, min(2048, sample_rate // 50))
            self._stream = self._audio.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=int(device["index"]),
                frames_per_buffer=frames,
            )
            print(f"音訊震動已啟動：{device['name']}")
            self._process_stream(sample_rate, channels, frames)
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

    def _process_stream(self, sample_rate, channels, frames):
        low_state = 0.0
        lf_envelope = 0.0
        hf_envelope = 0.0
        low_alpha = 1.0 - math.exp(
            -2.0 * math.pi * self.crossover_hz / sample_rate
        )
        block_seconds = frames / sample_rate

        while self._running:
            data = self._stream.read(frames, exception_on_overflow=False)
            samples = array("f")
            samples.frombytes(data)
            frame_count = len(samples) // channels
            if frame_count <= 0:
                continue

            low_energy = 0.0
            high_energy = 0.0
            for offset in range(0, frame_count * channels, channels):
                mono = sum(samples[offset:offset + channels]) / channels
                low_state += low_alpha * (mono - low_state)
                high = mono - low_state
                low_energy += low_state * low_state
                high_energy += high * high

            lf_target = self._level_from_rms(
                math.sqrt(low_energy / frame_count), self.low_gain
            )
            hf_target = self._level_from_rms(
                math.sqrt(high_energy / frame_count), self.high_gain
            )
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
