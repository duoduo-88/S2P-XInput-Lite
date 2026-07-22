"""Shared HD Rumble 2 frame encoding and conservative feedback defaults."""


FREQUENCY_MAX = 0x1FF
AMPLITUDE_MAX = 0x3FF

# Match the v0.5.1 connection/PIN cue.  Frequencies occupy 10-bit protocol
# slots even though their accepted command range uses only nine bits.
CONNECTION_LF_FREQUENCY = 225
CONNECTION_HF_FREQUENCY = 481
CONNECTION_FEEDBACK_PATTERN = (
    (800, 800, 0.080),
    (0, 0, 0.100),
    (800, 800, 0.080),
    (0, 0, 0.0),
)

# v0.5.1 deliberately used the same recognizable pattern for identification.
PIN_LF_FREQUENCY = CONNECTION_LF_FREQUENCY
PIN_HF_FREQUENCY = CONNECTION_HF_FREQUENCY
PIN_FEEDBACK_PATTERN = CONNECTION_FEEDBACK_PATTERN

# Live stress probes exercise pacing, not maximum actuator output.
STRESS_AMPLITUDES = (0, 150, 300, 450, 300, 150)


def _clamp_field(value, maximum):
    return max(0, min(maximum, int(value)))


def encode_vibration_frame(lf_freq, lf_amp, hf_freq, hf_amp):
    """Encode four 10-bit slots; frequency commands use nine valid bits."""
    value = _clamp_field(lf_freq, FREQUENCY_MAX)
    value |= _clamp_field(lf_amp, AMPLITUDE_MAX) << 10
    value |= _clamp_field(hf_freq, FREQUENCY_MAX) << 20
    value |= _clamp_field(hf_amp, AMPLITUDE_MAX) << 30
    return value.to_bytes(5, byteorder="little")
