from dataclasses import dataclass


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


@dataclass
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
        motion_values = tuple(state.accelerometer) + tuple(state.gyroscope)
        if not any(abs(value) > 1 for value in motion_values):
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

    buttons = int.from_bytes(payload[4:8], "little", signed=False)
    left = get_stick_xy(payload[10:13])
    right = get_stick_xy(payload[13:16])
    accelerometer = (0, 0, 0)
    gyroscope = (0, 0, 0)
    magnetometer = (0, 0, 0)
    if len(payload) >= 31:
        magnetometer = tuple(
            get_signed_16(payload[offset:offset + 2])
            for offset in (25, 27, 29)
        )
    if len(payload) >= 60:
        accelerometer = tuple(
            get_signed_16(payload[offset:offset + 2])
            for offset in (48, 50, 52)
        )
        gyroscope = tuple(
            get_signed_16(payload[offset:offset + 2])
            for offset in (54, 56, 58)
        )
    battery_voltage = None
    battery_percent = None
    if len(payload) >= 33:
        voltage_mv = int.from_bytes(
            payload[31:33], "little", signed=False
        )
        # Ignore zero/uninitialized bytes and impossible values instead of
        # presenting a corrupted report as a believable battery percentage.
        if 2500 <= voltage_mv <= 5000:
            battery_voltage = voltage_mv / 1000.0
            # Match Switch2Connect's high/medium/low voltage thresholds.
            if battery_voltage > 3.25:
                battery_percent = 100
            elif battery_voltage > 3.125:
                battery_percent = 50
            else:
                battery_percent = 25

    # Switch2Connect exposes current but does not establish a reliable sign or
    # charging flag for this report, so do not guess a charging state.
    charging = False
    report_time = int.from_bytes(payload[0:4], "little", signed=False)
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
    )
