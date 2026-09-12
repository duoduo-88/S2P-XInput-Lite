"""Convert Windows system-output audio into LF/HF rumble levels."""

import math
import threading
from collections import namedtuple
from functools import lru_cache

import numpy as np
from console_i18n import current_language
from console_i18n import localized_print as print


def _tr(zh, en):
    return en if current_language() == "en" else zh


AUDIO_BANDS_HZ = (
    (20.0, 120.0),
    (120.0, 300.0),
    (300.0, 700.0),
    (700.0, 2000.0),
    (2000.0, 4000.0),
    (4000.0, 8000.0),
)

AUDIO_ROUTE_STRENGTH = (1.00, 0.88, 0.60, 0.65, 0.70, 0.75)
AUDIO_BASE_HF_SHARE = (0.00, 0.05, 0.30, 0.65, 0.85, 1.00)
AUDIO_BALANCE_INFLUENCE = (0.00, 0.12, 0.30, 0.30, 0.12, 0.00)


# These compact records are deliberately kept scalar-only.  They make the
# causal DSP stages inspectable in tests without adding a per-hop ndarray or a
# framework around the existing capture loop.
AudioFeatures = namedtuple(
    "AudioFeatures",
    (
        "overall_level max_band_level low_energy mid_energy high_energy "
        "low_ratio mid_ratio high_ratio positive_flux negative_flux "
        "fast_envelope slow_envelope envelope_contrast peak_level rms_level "
        "crest energy_stability sustain_evidence valid"
    ),
)
TactileFeatures = namedtuple(
    "TactileFeatures", "punch weight sharpness sustain texture"
)
FeatureState = namedtuple(
    "FeatureState", "fast_envelope slow_envelope stability previous_overall"
)


def _empty_feature_state():
    return FeatureState(0.0, 0.0, 0.0, 0.0)


def _clamp01(value):
    return min(1.0, max(0.0, float(value)))


def _soft_union(*cues):
    """Combine independent 0..1 cues without treating them as amplitudes."""
    remaining = 1.0
    for cue in cues:
        remaining *= 1.0 - _clamp01(cue)
    return 1.0 - remaining


def _route_band_levels(band_levels, lf_hf_balance=0.0):
    """Collapse six audio bands into LF/HF levels with a movable crossover."""
    balance = max(-1.0, min(1.0, float(lf_hf_balance)))
    lf_values = []
    hf_values = []
    for level, strength, base_hf, influence in zip(
        band_levels,
        AUDIO_ROUTE_STRENGTH,
        AUDIO_BASE_HF_SHARE,
        AUDIO_BALANCE_INFLUENCE,
    ):
        hf_share = max(0.0, min(1.0, base_hf + balance * influence))
        lf_values.append(float(level) * strength * (1.0 - hf_share))
        hf_values.append(float(level) * strength * hf_share)
    return (
        min(1.0, max(lf_values, default=0.0)),
        min(1.0, max(hf_values, default=0.0)),
    )


def _follow_feature_envelope(current, target, block_seconds, time_constant):
    """One-pole follower used only by the audio feature detector."""
    duration = max(1e-6, float(time_constant), float(block_seconds))
    coefficient = 1.0 - math.exp(-float(block_seconds) / duration)
    return current + (target - current) * coefficient


def _extract_audio_features(
    feature_levels,
    previous_feature_levels,
    feature_state,
    block_seconds,
    analysis_seconds,
    *,
    band_energies=None,
    hop_rms=None,
    peak_level=None,
):
    """Layer A: derive causal, normalized audio structure from one hop.

    ``feature_levels`` are independent of master Strength; production routing
    remains on its own user-facing band-level path.  ``band_energies`` are
    optional gate-and-gain-adjusted, pre-clamp energies used only for spectral
    ratios, avoiding saturated levels as a false energy measurement.
    """
    current = tuple(_clamp01(level) for level in feature_levels)
    previous = tuple(_clamp01(level) for level in previous_feature_levels)
    if len(previous) != len(current):
        previous = (0.0,) * len(current)
    if band_energies is None or len(band_energies) != len(current):
        energies = current
    else:
        energies = tuple(max(0.0, float(value)) for value in band_energies)

    overall = math.sqrt(
        sum(level * level for level in current) / max(1, len(current))
    )
    max_band = max(current, default=0.0)
    # A level below this floor is either gated production input or synthetic
    # floating noise.  It must not acquire ratios or a huge normalized flux.
    valid = overall >= 0.004 and max_band >= 0.006
    if not valid:
        zeros = (0.0,) * 3
        return (
            AudioFeatures(
                0.0, 0.0, *zeros, *zeros, 0.0, 0.0, 0.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 0.0, 0.0, False,
            ),
            _empty_feature_state(),
        )

    low_energy = energies[0] + energies[1] if len(energies) >= 2 else 0.0
    mid_energy = energies[2] + energies[3] if len(energies) >= 4 else 0.0
    high_energy = energies[4] + energies[5] if len(energies) >= 6 else 0.0
    energy_total = low_energy + mid_energy + high_energy
    if energy_total <= 1e-9:
        low_ratio = mid_ratio = high_ratio = 0.0
    else:
        low_ratio = low_energy / energy_total
        mid_ratio = mid_energy / energy_total
        high_ratio = high_energy / energy_total

    positive_delta = tuple(
        max(0.0, level - old) for level, old in zip(current, previous)
    )
    negative_delta = tuple(
        max(0.0, old - level) for level, old in zip(current, previous)
    )
    # The 0.06 denominator floor prevents a tiny gated-near signal from
    # becoming a full-scale onset purely through division.  A 0.60 band rise
    # is still near full-scale, as verified by the synthetic attack tests.
    flux_denominator = max(0.06, overall)
    positive_flux = min(
        1.0,
        math.sqrt(sum(delta * delta for delta in positive_delta) / len(current))
        / flux_denominator,
    )
    negative_flux = min(
        1.0,
        math.sqrt(sum(delta * delta for delta in negative_delta) / len(current))
        / flux_denominator,
    )

    fast = _follow_feature_envelope(
        feature_state.fast_envelope,
        overall,
        block_seconds,
        max(0.005, block_seconds),
    )
    # Four 20 ms analysis windows gives an 80 ms sustained-body follower.
    slow = _follow_feature_envelope(
        feature_state.slow_envelope,
        overall,
        block_seconds,
        max(0.060, min(0.120, analysis_seconds * 4.0)),
    )
    envelope_contrast = max(0.0, fast - slow) / max(1e-6, fast)

    variation = abs(overall - feature_state.previous_overall) / max(
        0.05, overall, feature_state.previous_overall
    )
    instant_stability = 1.0 - min(1.0, variation / 0.35)
    stability = _follow_feature_envelope(
        feature_state.stability,
        instant_stability,
        block_seconds,
        0.020,
    )

    rms_level = overall if hop_rms is None else max(0.0, float(hop_rms))
    peak = rms_level if peak_level is None else max(0.0, float(peak_level))
    crest_linear = peak / max(1e-5, rms_level)
    # A continuous sine is ~1.41 and maps near zero.  A pulse occupying at
    # most 1/16 of a hop is >=4 and maps to one; both anchor cases are tested.
    crest = _clamp01((crest_linear - 1.2) / (4.0 - 1.2))

    onset = _soft_union(positive_flux, envelope_contrast)
    slow_body = _clamp01(slow / 0.12)
    sustain_evidence = slow_body * stability * (1.0 - onset)
    state = FeatureState(fast, slow, stability, overall)
    return (
        AudioFeatures(
            overall, max_band, low_energy, mid_energy, high_energy,
            low_ratio, mid_ratio, high_ratio, positive_flux, negative_flux,
            fast, slow, envelope_contrast, peak, rms_level, crest, stability,
            sustain_evidence, True,
        ),
        state,
    )


def _derive_tactile_features(features):
    """Layer B: turn audio structure into bounded tactile responsibilities."""
    if not features.valid:
        return TactileFeatures(0.0, 0.0, 0.0, 0.0, 0.0)

    level_presence = _clamp01(features.overall_level / 0.08)
    onset = _soft_union(features.positive_flux, features.envelope_contrast)
    # Crest is evidence only when a real onset and meaningful level agree.
    crest_onset = features.crest * level_presence * onset
    punch = _soft_union(
        features.positive_flux,
        features.envelope_contrast,
        crest_onset,
    ) * level_presence

    low_presence = _clamp01(features.low_energy / 0.12)
    # Impacts get a little body evidence; sustained energy earns most weight.
    body_factor = _soft_union(0.24 * punch, features.sustain_evidence)
    weight = low_presence * features.low_ratio * body_factor

    high_presence = _clamp01(features.high_energy / 0.12)
    sharpness = high_presence * features.high_ratio * onset
    # Texture is diagnostic only: rapid bidirectional high-band variation.
    texture = _clamp01(
        (features.positive_flux + features.negative_flux)
        * features.high_ratio
        * level_presence
    )
    return TactileFeatures(
        punch, weight, sharpness, features.sustain_evidence, texture
    )


def _dsp_v2_targets(
    band_levels,
    previous_band_levels,
    lf_hf_balance,
    feature_state,
    block_seconds,
    analysis_seconds,
    *,
    feature_levels=None,
    previous_feature_levels=None,
    band_energies=None,
    hop_rms=None,
    peak_level=None,
):
    """Layer C: preserve baseline routing and add bounded onset contrast."""
    analysis_levels = band_levels if feature_levels is None else feature_levels
    analysis_previous = (
        previous_band_levels
        if previous_feature_levels is None
        else previous_feature_levels
    )
    features, next_state = _extract_audio_features(
        analysis_levels,
        analysis_previous,
        feature_state,
        block_seconds,
        analysis_seconds,
        band_energies=band_energies,
        hop_rms=hop_rms,
        peak_level=peak_level,
    )
    if not features.valid:
        return 0.0, 0.0, next_state, features, _derive_tactile_features(features)

    current = tuple(_clamp01(level) for level in band_levels)
    previous = tuple(_clamp01(level) for level in previous_band_levels)
    if len(previous) != len(current):
        previous = (0.0,) * len(current)
    positive_delta = tuple(
        max(0.0, level - old) for level, old in zip(current, previous)
    )
    tactile = _derive_tactile_features(features)
    base_lf, base_hf = _route_band_levels(current, lf_hf_balance)
    impact_lf, impact_hf = _route_band_levels(positive_delta, lf_hf_balance)

    # Weight only makes the already-routed LF body slightly more resilient;
    # Punch/Sharpness govern transient contrast, so features do not add as
    # independent output amplitudes.
    lf_body = min(1.0, base_lf * (1.0 + 0.08 * tactile.weight * (1.0 - base_lf)))
    lf_attack = impact_lf * tactile.punch * (0.65 + 0.35 * tactile.weight)
    hf_attack = impact_hf * tactile.punch * (0.55 + 0.45 * tactile.sharpness)
    # Strategy A: transient contrast consumes only unused headroom.  The
    # sustained baseline never pumps downward during an attack.
    lf_target = min(1.0, lf_body + (1.0 - lf_body) * lf_attack)
    hf_target = min(1.0, base_hf + (1.0 - base_hf) * hf_attack)
    return lf_target, hf_target, next_state, features, tactile


def _dynamic_audio_targets(
    band_levels,
    previous_band_levels,
    lf_hf_balance,
    fast_envelope,
    slow_envelope,
    block_seconds,
    analysis_seconds,
):
    """Add transient-aware contrast without changing the six-band controls.

    The normal routed level remains the sustained body. Positive six-band
    spectral flux and a fast-vs-slow energy envelope identify new attacks and
    use only the remaining 0..1 headroom for a short pre-emphasis. Once the
    sound becomes steady, the result naturally converges back to the original
    six-band routing.
    """
    current = tuple(
        max(0.0, min(1.0, float(level))) for level in band_levels
    )
    if len(previous_band_levels) != len(current):
        previous = (0.0,) * len(current)
    else:
        previous = tuple(
            max(0.0, min(1.0, float(level)))
            for level in previous_band_levels
        )

    positive_delta = tuple(
        max(0.0, level - old)
        for level, old in zip(current, previous)
    )
    overall_level = max(current, default=0.0)

    # The fast follower tracks roughly one audio hop.  The slow follower spans
    # several rolling FFT windows, so it represents the sustained body without
    # adding another user-facing timing control.
    fast_envelope = _follow_feature_envelope(
        fast_envelope,
        overall_level,
        block_seconds,
        block_seconds,
    )
    slow_envelope = _follow_feature_envelope(
        slow_envelope,
        overall_level,
        block_seconds,
        max(block_seconds, analysis_seconds * 4.0),
    )

    if overall_level <= 1e-9 or not current:
        transient_score = 0.0
    else:
        flux_rms = math.sqrt(
            sum(delta * delta for delta in positive_delta) / len(current)
        )
        spectral_flux = min(1.0, flux_rms / overall_level)
        envelope_contrast = min(
            1.0,
            max(0.0, fast_envelope - slow_envelope)
            / max(1e-6, fast_envelope),
        )
        # Soft union: either a new spectral component or a sudden broadband
        # rise may identify an onset, while two agreeing cues reinforce it.
        transient_score = 1.0 - (
            (1.0 - spectral_flux) * (1.0 - envelope_contrast)
        )

    base_lf, base_hf = _route_band_levels(current, lf_hf_balance)
    transient_lf, transient_hf = _route_band_levels(
        positive_delta, lf_hf_balance
    )

    # Preserve the existing output as the floor and spend only unused headroom
    # on short attacks.  This avoids lowering steady music or requiring a new
    # GUI strength parameter.
    lf_target = min(
        1.0,
        base_lf
        + (1.0 - base_lf) * transient_lf * transient_score,
    )
    hf_target = min(
        1.0,
        base_hf
        + (1.0 - base_hf) * transient_hf * transient_score,
    )
    return (
        lf_target,
        hf_target,
        fast_envelope,
        slow_envelope,
        transient_score,
    )


def _spectral_band_rms(samples, sample_rate):
    """Return low-latency, window-corrected RMS for the six bands."""
    mono = np.asarray(samples, dtype=np.float32)
    sample_count = int(mono.size)
    if sample_count < 8 or sample_rate <= 0:
        return (0.0,) * len(AUDIO_BANDS_HZ)
    window, denominator, band_bins = _spectral_plan(
        sample_count, float(sample_rate)
    )
    spectrum = np.fft.rfft(mono * window)
    power = np.abs(spectrum) ** 2
    levels = []
    for start, stop in band_bins:
        band_power = float(np.sum(power[start:stop]))
        levels.append(math.sqrt(max(0.0, 2.0 * band_power / denominator)))
    return tuple(levels)


@lru_cache(maxsize=8)
def _spectral_plan(sample_count, sample_rate):
    """Cache the immutable FFT window and band bins used on every audio hop."""
    # A symmetric Hann window suppresses the newest samples at its right
    # edge, adding perceptible onset lag in a causal real-time stream. This
    # rising half-sine keeps leakage controlled while giving the newest audio
    # full weight and older overlapping samples progressively less weight.
    window = np.sin(
        np.linspace(0.0, math.pi / 2.0, sample_count, dtype=np.float32)
    )
    window_power = float(np.mean(window * window))
    frequencies = np.fft.rfftfreq(sample_count, 1.0 / float(sample_rate))
    denominator = max(1e-12, sample_count * sample_count * window_power)
    band_bins = []
    for low_hz, high_hz in AUDIO_BANDS_HZ:
        band_bins.append((
            int(np.searchsorted(frequencies, low_hz, side="left")),
            int(np.searchsorted(frequencies, high_hz, side="left")),
        ))
    window.setflags(write=False)
    return window, denominator, tuple(band_bins)


class AudioHaptics:
    """Capture the default WASAPI loopback device on a background thread."""

    def __init__(self, config, level_callback):
        self._level_callback = level_callback
        self._running = False
        self._thread = None
        self._stream = None
        self._audio = None
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._generation = 0
        self._restart_after_exit = False
        self._apply_config(config)

    def _apply_config(self, config):
        """Replace scalar DSP settings without touching the WASAPI stream."""
        section = "audio_haptics"
        self.mode = config.get(section, "mode", fallback="GAME").strip().upper()
        if self.mode not in ("GAME", "AUDIO", "MIX"):
            self.mode = "GAME"
        self.strength = _bounded_float(config, section, "strength", 0.32, 0, 1)
        legacy_low_gain = _bounded_float(
            config, section, "low_gain", 1.0, 0, 2
        )
        legacy_high_gain = _bounded_float(
            config, section, "high_gain", 1.0, 0, 2
        )
        legacy_band_gains = tuple(
            legacy_low_gain * (1.0 - blend) + legacy_high_gain * blend
            for blend in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
        )
        legacy_band_gains = tuple(round(value, 2) for value in legacy_band_gains)
        self.band_gains = tuple(
            _bounded_float(
                config, section, f"band_{index}_gain", fallback, 0, 2
            )
            for index, fallback in enumerate(legacy_band_gains, start=1)
        )
        self.lf_hf_balance = _bounded_float(
            config, section, "lf_hf_balance", 0.0, -1.0, 1.0
        )
        self.noise_gate = _bounded_float(
            config, section, "noise_gate", 0.040, 0, 0.25
        )
        self.attack_ms = _bounded_float(config, section, "attack_ms", 6, 1, 500)
        self.release_ms = _bounded_float(
            config, section, "release_ms", 100, 5, 2000
        )

    def reconfigure(self, config):
        """Apply a profile while preserving any live PyAudio/WASAPI stream."""
        self._apply_config(config)
        if self.mode in ("AUDIO", "MIX"):
            self.start()
        else:
            # Keep an already-open native stream alive across profile changes;
            # repeated PortAudio close/terminate/reopen cycles can crash in
            # ntdll.  GAME ignores this value in XInputController.
            self._level_callback(0.0, 0.0)

    def start(self):
        if self.mode == "GAME":
            return False
        with self._lifecycle_lock:
            thread = self._thread
            if thread is not None and thread.is_alive():
                if self._stop_event.is_set() or not self._running:
                    self._restart_after_exit = True
                    return False
                return True
            self._thread = None
            self._restart_after_exit = False
            self._generation += 1
            generation = self._generation
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._running = True
            thread = threading.Thread(
                target=self._capture_loop,
                args=(generation, stop_event),
                daemon=True,
                name="AudioHapticsCapture",
            )
            self._thread = thread
            thread.start()
            return True

    def close(self):
        with self._lifecycle_lock:
            self._restart_after_exit = False
            self._running = False
            self._stop_event.set()
            thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=1.0)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            with self._lifecycle_lock:
                if self._thread is thread:
                    self._thread = None
        self._level_callback(0.0, 0.0)
        return stopped

    def _capture_loop(self, generation, stop_event):
        audio = None
        stream = None
        try:
            import pyaudiowpatch as pyaudio

            audio = pyaudio.PyAudio()
            device = self._default_loopback_device(pyaudio, audio)
            sample_rate = int(device["defaultSampleRate"])
            channels = max(1, int(device["maxInputChannels"]))
            # Read a small hop for low latency, while retaining a 20 ms
            # rolling window so the lowest EQ bands keep useful resolution.
            hop_frames = max(128, min(512, sample_rate // 200))
            analysis_frames = max(512, min(2048, sample_rate // 50))
            stream = audio.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=int(device["index"]),
                frames_per_buffer=hop_frames,
            )
            with self._lifecycle_lock:
                if generation == self._generation:
                    self._audio = audio
                    self._stream = stream
            print(f"音訊震動已啟動：{device['name']}")
            self._process_stream(
                sample_rate,
                channels,
                hop_frames,
                analysis_frames,
                stream=stream,
                stop_event=stop_event,
            )
        except ImportError:
            print("音訊震動無法啟動：缺少 PyAudioWPatch；遊戲震動仍可正常使用。")
        except Exception as exc:
            if not stop_event.is_set():
                print(f"音訊震動擷取失敗：{exc}")
        finally:
            current_thread = threading.current_thread()
            with self._lifecycle_lock:
                if (
                    generation == self._generation
                    and self._thread is current_thread
                ):
                    self._running = False
                    stop_event.set()
            self._level_callback(0.0, 0.0)
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if audio is not None:
                try:
                    audio.terminate()
                except Exception:
                    pass
            restart = False
            with self._lifecycle_lock:
                if (
                    generation == self._generation
                    and self._thread is current_thread
                ):
                    if self._stream is stream:
                        self._stream = None
                    if self._audio is audio:
                        self._audio = None
                    self._thread = None
                    restart = (
                        self._restart_after_exit
                        and self.mode in ("AUDIO", "MIX")
                    )
                    self._restart_after_exit = False
            if restart:
                self.start()

    def _default_loopback_device(self, pyaudio, audio=None):
        audio = self._audio if audio is None else audio
        wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        output = audio.get_device_info_by_index(
            wasapi["defaultOutputDevice"]
        )
        if output.get("isLoopbackDevice"):
            return output
        loopback = audio.get_wasapi_loopback_analogue_by_dict(output)
        if loopback is None:
            raise RuntimeError(_tr(
                "找不到預設 Windows 輸出裝置的 loopback 端點",
                "Could not find the loopback endpoint for the default "
                "Windows output device",
            ))
        return loopback

    def _process_stream(
        self,
        sample_rate,
        channels,
        hop_frames,
        analysis_frames=None,
        *,
        stream=None,
        stop_event=None,
    ):
        stream = self._stream if stream is None else stream
        stop_event = self._stop_event if stop_event is None else stop_event
        lf_envelope = 0.0
        hf_envelope = 0.0
        feature_state = _empty_feature_state()
        previous_band_levels = (0.0,) * len(AUDIO_BANDS_HZ)
        previous_feature_levels = (0.0,) * len(AUDIO_BANDS_HZ)
        if analysis_frames is None:
            analysis_frames = max(hop_frames, sample_rate // 50)
        analysis_frames = max(hop_frames, int(analysis_frames))
        # Starting with silence lets a new transient affect the first 5 ms
        # update instead of waiting for an entire 20 ms window to fill.
        rolling_audio = np.zeros(analysis_frames, dtype=np.float32)

        while self._running and not stop_event.is_set():
            get_read_available = getattr(
                stream, "get_read_available", None
            )
            if get_read_available is not None:
                available = max(0, int(get_read_available()))
                if available < hop_frames:
                    missing_seconds = (hop_frames - available) / sample_rate
                    stop_event.wait(
                        timeout=max(0.0005, min(0.002, missing_seconds))
                    )
                    continue
            data = stream.read(
                hop_frames, exception_on_overflow=False
            )
            samples = np.frombuffer(data, dtype=np.float32)
            frame_count = len(samples) // channels
            if frame_count <= 0:
                continue
            samples = samples[:frame_count * channels].reshape(
                frame_count, channels
            )
            if self.mode == "GAME":
                # Keep draining the live stream so switching back to AUDIO/MIX
                # cannot replay stale buffered sound, but skip all DSP and
                # callback traffic while game rumble owns the output.
                rolling_audio.fill(0.0)
                lf_envelope = 0.0
                hf_envelope = 0.0
                feature_state = _empty_feature_state()
                previous_band_levels = (0.0,) * len(AUDIO_BANDS_HZ)
                previous_feature_levels = (0.0,) * len(AUDIO_BANDS_HZ)
                continue
            mono = np.mean(samples, axis=1)
            if frame_count >= analysis_frames:
                rolling_audio[:] = mono[-analysis_frames:]
            else:
                rolling_audio[:-frame_count] = rolling_audio[frame_count:]
                rolling_audio[-frame_count:] = mono
            band_rms = _spectral_band_rms(rolling_audio, sample_rate)
            feature_energies = tuple(
                self._feature_energy_from_rms(rms, gain)
                for rms, gain in zip(band_rms, self.band_gains)
            )
            band_levels = tuple(
                self._level_from_rms(rms, gain)
                for rms, gain in zip(band_rms, self.band_gains)
            )
            feature_levels = tuple(
                min(1.0, energy) for energy in feature_energies
            )
            block_seconds = frame_count / sample_rate
            (
                lf_target,
                hf_target,
                feature_state,
                _features,
                _tactile,
            ) = _dsp_v2_targets(
                band_levels,
                previous_band_levels,
                self.lf_hf_balance,
                feature_state,
                block_seconds,
                analysis_frames / sample_rate,
                feature_levels=feature_levels,
                previous_feature_levels=previous_feature_levels,
                band_energies=feature_energies,
                hop_rms=math.sqrt(float(np.mean(mono * mono))),
                peak_level=float(np.max(np.abs(mono))),
            )
            previous_band_levels = band_levels
            previous_feature_levels = feature_levels
            lf_envelope = self._smooth_envelope(
                lf_envelope, lf_target, block_seconds
            )
            hf_envelope = self._smooth_envelope(
                hf_envelope, hf_target, block_seconds
            )
            self._level_callback(lf_envelope, hf_envelope)

    def _level_from_rms(self, rms, gain):
        """Original user-facing linear Band Gain and Strength curve."""
        usable = self._usable_rms(rms)
        return min(1.0, usable * 4.0 * gain * self.strength)

    def _usable_rms(self, rms):
        if rms <= self.noise_gate:
            return 0.0
        return (rms - self.noise_gate) / max(1e-6, 1.0 - self.noise_gate)

    def _feature_energy_from_rms(self, rms, gain):
        """Strength-independent energy used only by tactile analysis.

        The square-root gain law intentionally lets per-band gain alter
        tactile spectral emphasis without allowing a 2.0 gain to linearly
        dominate Low/Mid/High ratios.  It never feeds baseline routing.
        """
        usable = self._usable_rms(rms)
        return usable * math.sqrt(max(0.0, float(gain)))

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
