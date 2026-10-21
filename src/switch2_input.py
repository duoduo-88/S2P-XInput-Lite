from dataclasses import dataclass
import struct


BATTERY_PERCENT_STEP = 5
BATTERY_DISCHARGE_CURVE = (
    # Measured under a continuous Audio Haptics load from full charge until
    # controller cutoff. Percentages represent remaining observed runtime.
    (2589, 0),
    (3000, 3),
    (3100, 5),
    (3150, 10),
    (3200, 22),
    (3250, 34),
    (3300, 45),
    (3350, 51),
    (3400, 55),
    (3450, 66),
    (3500, 72),
    (3550, 80),
    (3600, 87),
    (3650, 95),
    (3687, 100),
)


SWITCH_BUTTONS = {
    "Y":     0x00000001,
    "X":     0x00000002,
    "B":     0x00000004,
    "A":     0x00000008,
    "SR_R":  0x00000010,
    "SL_R":  0x00000020,
    "R":     0x00000040,
    "ZR":    0x00000080,
    "MINUS": 0x00000100,
    "PLUS":  0x00000200,
    "R_STK": 0x00000400,
    "L_STK": 0x00000800,
    "HOME":  0x00001000,
    "CAPT":  0x00002000,
    "C":     0x00004000,
    "DOWN":  0x00010000,
    "UP":    0x00020000,
    "RIGHT": 0x00040000,
    "LEFT":  0x00080000,
    "SR_L":  0x00100000,
    "SL_L":  0x00200000,
    "L":     0x00400000,
    "ZL":    0x00800000,
    "GR":    0x01000000,
    "GL":    0x02000000,
}


def estimate_battery_percent(voltage_mv):
    """Estimate remaining runtime in coarse steps from the measured curve."""
    voltage_mv = int(voltage_mv)
    if voltage_mv <= BATTERY_DISCHARGE_CURVE[0][0]:
        return 0
    if voltage_mv >= BATTERY_DISCHARGE_CURVE[-1][0]:
        return 100

    for (low_mv, low_percent), (high_mv, high_percent) in zip(
        BATTERY_DISCHARGE_CURVE,
        BATTERY_DISCHARGE_CURVE[1:],
    ):
        if voltage_mv <= high_mv:
            fraction = (voltage_mv - low_mv) / (high_mv - low_mv)
            estimate = low_percent + fraction * (high_percent - low_percent)
            quantized = int(
                BATTERY_PERCENT_STEP
                * round(estimate / BATTERY_PERCENT_STEP)
            )
            return min(100, max(0, quantized))
    return 100


def battery_level(percent):
    """Return the shared 1-4 battery level used by the UI and LEDs."""
    if percent is None:
        return None
    percent = min(100, max(0, int(percent)))
    return min(4, max(0, percent - 1) // 25 + 1)


def battery_led_mask(percent):
    """Return a cumulative 1-4 LED battery bar mask."""
    level = battery_level(percent)
    if level is None:
        return None
    return (1 << level) - 1


def get_stick_xy(data):
    if len(data) != 3:
        return 2048, 2048
    x = data[0] | ((data[1] & 0x0F) << 8)
    y = ((data[1] >> 4) & 0x0F) | (data[2] << 4)
    return x, y


def get_signed_16(data):
    if len(data) != 2:
        return 0
    return int.from_bytes(data, "little", signed=True)


@dataclass(slots=True)
class InputState:
    buttons: int
    left_stick: tuple
    right_stick: tuple
    accelerometer: tuple
    gyroscope: tuple
    magnetometer: tuple
    battery_percent: int | None
    battery_voltage: float | None
    charging: bool
    report_time: int | None = None
    charge_status_raw: int | None = None
    battery_current_raw: int | None = None


class SensorModeTracker:
    """Classify 6/9-axis reports consistently across USB, BLE and ESP32."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.mode = None
        self._mag_active_streak = 0
        self._mag_zero_streak = 0
        self._motion_zero_streak = 0

    def update(self, state):
        # A translated basic USB report contains no real IMU data. Keep the
        # state unknown rather than incorrectly calling it six-axis. Tolerate
        # short runs of empty/malformed reports so one transient frame does
        # not erase a stable six/nine-axis classification.
        has_motion = any(abs(value) > 1 for value in state.accelerometer)
        if not has_motion:
            has_motion = any(abs(value) > 1 for value in state.gyroscope)
        if not has_motion:
            self._motion_zero_streak += 1
            if self._motion_zero_streak >= 16:
                self.mode = None
                self._mag_active_streak = 0
                self._mag_zero_streak = 0
            return self.mode
        self._motion_zero_streak = 0

        if any(abs(value) > 1 for value in state.magnetometer):
            self._mag_active_streak += 1
            self._mag_zero_streak = 0
            if self._mag_active_streak >= 8:
                self.mode = "nine_axis"
        else:
            self._mag_zero_streak += 1
            self._mag_active_streak = 0
            if self._mag_zero_streak >= 64:
                self.mode = "six_axis"
        return self.mode


def parse_input_report(payload):
    # Original 0.0.1 layout:
    # bytes 4:8 = 32-bit buttons
    # bytes 10:13 = left 12-bit X/Y
    # bytes 13:16 = right 12-bit X/Y
    if len(payload) < 16:
        return None

    # Avoid temporary byte slices on the 125+ Hz hot path.  The bridge always
    # supplies a bytes-like object, so unpack_from/direct indexing can decode
    # in place.
    report_time, buttons = struct.unpack_from("<II", payload, 0)
    left = (
        payload[10] | ((payload[11] & 0x0F) << 8),
        (payload[11] >> 4) | (payload[12] << 4),
    )
    right = (
        payload[13] | ((payload[14] & 0x0F) << 8),
        (payload[14] >> 4) | (payload[15] << 4),
    )
    accelerometer = (0, 0, 0)
    gyroscope = (0, 0, 0)
    magnetometer = (0, 0, 0)
    if len(payload) >= 31:
        magnetometer = struct.unpack_from("<hhh", payload, 25)
    if len(payload) >= 60:
        accelerometer = struct.unpack_from("<hhh", payload, 48)
        gyroscope = struct.unpack_from("<hhh", payload, 54)
    battery_voltage = None
    battery_percent = None
    if len(payload) >= 33:
        voltage_mv = struct.unpack_from("<H", payload, 31)[0]
        # Ignore zero/uninitialized bytes and impossible values instead of
        # presenting a corrupted report as a believable battery percentage.
        if 2500 <= voltage_mv <= 5000:
            battery_voltage = voltage_mv / 1000.0
            battery_percent = estimate_battery_percent(voltage_mv)

    charge_status_raw = payload[33] if len(payload) >= 34 else None
    battery_current_raw = (
        struct.unpack_from("<H", payload, 34)[0]
        if len(payload) >= 36 else None
    )
    # Hardware comparison: the status byte is 0x34 with external power and
    # 0x00 immediately after unplugging. Keep the raw value for future protocol
    # research while exposing the observed powered/charging state to the UI.
    charging = bool(charge_status_raw)
    return InputState(
        buttons,
        left,
        right,
        accelerometer,
        gyroscope,
        magnetometer,
        battery_percent,
        battery_voltage,
        charging,
        report_time,
        charge_status_raw,
        battery_current_raw,
    )
