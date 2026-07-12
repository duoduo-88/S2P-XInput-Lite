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


@dataclass
class InputState:
    buttons: int
    left_stick: tuple
    right_stick: tuple


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
    return InputState(buttons, left, right)
