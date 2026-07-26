"""Read-only Windows gamepad discovery and polling for the tester window."""

from __future__ import annotations

import ctypes
import threading
import time
from collections import deque
from dataclasses import dataclass


ERROR_SUCCESS = 0
ERROR_DEVICE_NOT_CONNECTED = 1167
JOYERR_NOERROR = 0
JOY_RETURNALL = 0x000000FF
JOY_POVCENTERED = 0x0000FFFF
JOYCAPS_HASZ = 0x0001
JOYCAPS_HASR = 0x0002
JOYCAPS_HASU = 0x0004
JOYCAPS_HASV = 0x0008
S2P_MOBILE_HID_PROFILE = "s2p_mobile_hid"
S2P_MOBILE_HID_PRODUCT_NAME = "S2P Mobile Gamepad"
S2P_MOBILE_HID_VID = 0xCAFE
S2P_MOBILE_HID_PID = 0x4021

# WinMM exposes dwButtons by HID Button usage number (bit 0 is Button 1),
# rather than by the physical bit position in the USB input report. The
# mobile descriptor intentionally uses Android's semantic, non-sequential
# usages, so keep unused usage numbers as holes instead of compacting them.
S2P_MOBILE_HID_WINMM_BUTTON_NAMES = (
    "A",
    "B",
    None,
    "X",
    "Y",
    None,
    "LB",
    "RB",
    "L2",
    "R2",
    "BACK",
    "START",
    "GUIDE",
    "L3",
    "R3",
    None,
)


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_uint32),
        ("Gamepad", XINPUT_GAMEPAD),
    ]


class XINPUT_VIBRATION(ctypes.Structure):
    _fields_ = [
        ("wLeftMotorSpeed", ctypes.c_ushort),
        ("wRightMotorSpeed", ctypes.c_ushort),
    ]


class JOYINFOEX(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("dwXpos", ctypes.c_uint32),
        ("dwYpos", ctypes.c_uint32),
        ("dwZpos", ctypes.c_uint32),
        ("dwRpos", ctypes.c_uint32),
        ("dwUpos", ctypes.c_uint32),
        ("dwVpos", ctypes.c_uint32),
        ("dwButtons", ctypes.c_uint32),
        ("dwButtonNumber", ctypes.c_uint32),
        ("dwPOV", ctypes.c_uint32),
        ("dwReserved1", ctypes.c_uint32),
        ("dwReserved2", ctypes.c_uint32),
    ]


class JOYCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", ctypes.c_ushort),
        ("wPid", ctypes.c_ushort),
        ("szPname", ctypes.c_wchar * 32),
        ("wXmin", ctypes.c_uint32),
        ("wXmax", ctypes.c_uint32),
        ("wYmin", ctypes.c_uint32),
        ("wYmax", ctypes.c_uint32),
        ("wZmin", ctypes.c_uint32),
        ("wZmax", ctypes.c_uint32),
        ("wNumButtons", ctypes.c_uint32),
        ("wPeriodMin", ctypes.c_uint32),
        ("wPeriodMax", ctypes.c_uint32),
        ("wRmin", ctypes.c_uint32),
        ("wRmax", ctypes.c_uint32),
        ("wUmin", ctypes.c_uint32),
        ("wUmax", ctypes.c_uint32),
        ("wVmin", ctypes.c_uint32),
        ("wVmax", ctypes.c_uint32),
        ("wCaps", ctypes.c_uint32),
        ("wMaxAxes", ctypes.c_uint32),
        ("wNumAxes", ctypes.c_uint32),
        ("wMaxButtons", ctypes.c_uint32),
        ("szRegKey", ctypes.c_wchar * 32),
        ("szOEMVxD", ctypes.c_wchar * 260),
    ]


XINPUT_BUTTON_NAMES = (
    (0x0001, "↑"),
    (0x0002, "↓"),
    (0x0004, "←"),
    (0x0008, "→"),
    (0x0010, "START"),
    (0x0020, "BACK"),
    (0x0040, "L3"),
    (0x0080, "R3"),
    (0x0100, "LB"),
    (0x0200, "RB"),
    (0x0400, "GUIDE"),
    (0x1000, "A"),
    (0x2000, "B"),
    (0x4000, "X"),
    (0x8000, "Y"),
)


@dataclass(frozen=True)
class GamepadDevice:
    key: str
    kind: str
    index: int
    name: str
    supports_rumble: bool
    input_profile: str = "generic"


@dataclass(frozen=True)
class GamepadState:
    packet_number: int
    buttons_mask: int
    buttons: tuple[str, ...]
    left: tuple[float, float]
    right: tuple[float, float]
    left_trigger: float | None
    right_trigger: float | None


def normalize_signed_axis(value):
    value = int(value)
    divisor = 32767.0 if value >= 0 else 32768.0
    return max(-1.0, min(1.0, value / divisor))


def normalize_unsigned_axis(value, minimum, maximum, invert=False):
    minimum = float(minimum)
    maximum = float(maximum)
    if maximum <= minimum:
        return 0.0
    normalized = ((float(value) - minimum) / (maximum - minimum)) * 2.0 - 1.0
    if invert:
        normalized = -normalized
    return max(-1.0, min(1.0, normalized))


def normalize_trigger_axis(value, minimum, maximum):
    """Normalize an unsigned WinMM trigger axis to 0..1."""
    minimum = float(minimum)
    maximum = float(maximum)
    if maximum <= minimum:
        return 0.0
    normalized = (float(value) - minimum) / (maximum - minimum)
    return max(0.0, min(1.0, normalized))


def is_s2p_mobile_hid_name(name):
    """Recognize the product string exposed by standalone mobile USB HID."""
    return S2P_MOBILE_HID_PRODUCT_NAME.casefold() in str(name).casefold()


def is_s2p_mobile_hid_device(caps, name=""):
    """Detect the firmware's mobile HID personality through VID/PID or name."""
    try:
        if (
            int(caps.wMid) == S2P_MOBILE_HID_VID
            and int(caps.wPid) == S2P_MOBILE_HID_PID
        ):
            return True
    except (AttributeError, TypeError, ValueError):
        pass
    return is_s2p_mobile_hid_name(name)


def s2p_mobile_hid_winmm_buttons(button_mask):
    """Decode WinMM button bits using the mobile descriptor's HID usages."""
    button_mask = int(button_mask)
    return tuple(
        name
        for button_index, name in enumerate(
            S2P_MOBILE_HID_WINMM_BUTTON_NAMES
        )
        if name is not None and button_mask & (1 << button_index)
    )


def winmm_pov_buttons(value):
    """Translate a WinMM POV hat angle into cardinal button labels."""
    value = int(value)
    if value == JOY_POVCENTERED or value < 0 or value >= 36000:
        return ()
    sector = int((value + 2250) // 4500) % 8
    return (
        ("↑",),
        ("↑", "→"),
        ("→",),
        ("↓", "→"),
        ("↓",),
        ("↓", "←"),
        ("←",),
        ("↑", "←"),
    )[sector]


class WindowsGamepadBackend:
    """Enumerate XInput plus legacy WinMM/DirectInput-compatible devices."""

    def __init__(self, xinput=None, winmm=None):
        self.xinput = xinput if xinput is not None else self._load_xinput()
        self.winmm = winmm if winmm is not None else self._load_winmm()
        self._winmm_caps = {}
        self._configure_functions()

    @staticmethod
    def _load_xinput():
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            return None
        for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                return loader(name)
            except OSError:
                continue
        return None

    @staticmethod
    def _load_winmm():
        windll = getattr(ctypes, "windll", None)
        return getattr(windll, "winmm", None) if windll is not None else None

    def _configure_functions(self):
        if self.xinput is not None:
            self.xinput.XInputGetState.argtypes = (
                ctypes.c_uint32,
                ctypes.POINTER(XINPUT_STATE),
            )
            self.xinput.XInputGetState.restype = ctypes.c_uint32
            self.xinput.XInputSetState.argtypes = (
                ctypes.c_uint32,
                ctypes.POINTER(XINPUT_VIBRATION),
            )
            self.xinput.XInputSetState.restype = ctypes.c_uint32
        if self.winmm is not None:
            self.winmm.joyGetNumDevs.restype = ctypes.c_uint32
            self.winmm.joyGetDevCapsW.argtypes = (
                ctypes.c_uint32,
                ctypes.POINTER(JOYCAPSW),
                ctypes.c_uint32,
            )
            self.winmm.joyGetDevCapsW.restype = ctypes.c_uint32
            self.winmm.joyGetPosEx.argtypes = (
                ctypes.c_uint32,
                ctypes.POINTER(JOYINFOEX),
            )
            self.winmm.joyGetPosEx.restype = ctypes.c_uint32

    def enumerate_devices(self, excluded_xinput_slot=None):
        devices = []
        if self.xinput is not None:
            for index in range(4):
                state = XINPUT_STATE()
                result = self.xinput.XInputGetState(index, ctypes.byref(state))
                if result == ERROR_SUCCESS and index != excluded_xinput_slot:
                    devices.append(GamepadDevice(
                        key=f"xinput:{index}",
                        kind="xinput",
                        index=index,
                        name=f"XInput 手把 {index + 1}",
                        supports_rumble=True,
                    ))

        self._winmm_caps = {}
        if self.winmm is not None:
            device_count = min(32, int(self.winmm.joyGetNumDevs()))
            for index in range(device_count):
                caps = JOYCAPSW()
                result = self.winmm.joyGetDevCapsW(
                    index, ctypes.byref(caps), ctypes.sizeof(caps)
                )
                if result != JOYERR_NOERROR:
                    continue
                info = JOYINFOEX()
                info.dwSize = ctypes.sizeof(info)
                info.dwFlags = JOY_RETURNALL
                if self.winmm.joyGetPosEx(
                    index, ctypes.byref(info)
                ) != JOYERR_NOERROR:
                    continue
                name = str(caps.szPname).strip() or f"一般手把 {index + 1}"
                is_s2p_mobile = is_s2p_mobile_hid_device(caps, name)
                if is_s2p_mobile:
                    # WinMM sometimes replaces the USB product string with a
                    # generic Microsoft joystick-driver name. The VID/PID is
                    # stable, so restore the meaningful selector label.
                    name = S2P_MOBILE_HID_PRODUCT_NAME
                folded = name.casefold()
                # XInput pads are already represented above; hiding the common
                # legacy mirror prevents an obviously duplicated selector row.
                if "xbox" in folded or "xinput" in folded:
                    continue
                self._winmm_caps[index] = caps
                input_profile = (
                    S2P_MOBILE_HID_PROFILE
                    if is_s2p_mobile
                    else "generic"
                )
                devices.append(GamepadDevice(
                    key=f"winmm:{index}",
                    kind="winmm",
                    index=index,
                    name=name,
                    supports_rumble=False,
                    input_profile=input_profile,
                ))
        return devices

    def read_state(self, device):
        if device.kind == "xinput":
            return self._read_xinput(device.index)
        if device.kind == "winmm":
            return self._read_winmm(device.index, device.input_profile)
        return None

    def _read_xinput(self, index):
        if self.xinput is None:
            return None
        state = XINPUT_STATE()
        if self.xinput.XInputGetState(
            int(index), ctypes.byref(state)
        ) != ERROR_SUCCESS:
            return None
        gamepad = state.Gamepad
        buttons = tuple(
            name for mask, name in XINPUT_BUTTON_NAMES
            if int(gamepad.wButtons) & mask
        )
        return GamepadState(
            packet_number=int(state.dwPacketNumber),
            buttons_mask=int(gamepad.wButtons),
            buttons=buttons,
            left=(
                normalize_signed_axis(gamepad.sThumbLX),
                normalize_signed_axis(gamepad.sThumbLY),
            ),
            right=(
                normalize_signed_axis(gamepad.sThumbRX),
                normalize_signed_axis(gamepad.sThumbRY),
            ),
            left_trigger=int(gamepad.bLeftTrigger) / 255.0,
            right_trigger=int(gamepad.bRightTrigger) / 255.0,
        )

    def _read_winmm(self, index, input_profile="generic"):
        if self.winmm is None:
            return None
        caps = self._winmm_caps.get(index)
        if caps is None:
            return None
        info = JOYINFOEX()
        info.dwSize = ctypes.sizeof(info)
        info.dwFlags = JOY_RETURNALL
        if self.winmm.joyGetPosEx(
            int(index), ctypes.byref(info)
        ) != JOYERR_NOERROR:
            return None
        button_mask = int(info.dwButtons)
        is_s2p_mobile = input_profile == S2P_MOBILE_HID_PROFILE
        if is_s2p_mobile:
            buttons = s2p_mobile_hid_winmm_buttons(button_mask)
        else:
            buttons = tuple(
                f"B{button_index + 1}"
                for button_index in range(min(32, int(caps.wNumButtons)))
                if button_mask & (1 << button_index)
            )
        buttons += winmm_pov_buttons(info.dwPOV)
        capabilities = int(caps.wCaps)
        if is_s2p_mobile or (
            capabilities & JOYCAPS_HASZ and capabilities & JOYCAPS_HASR
        ):
            right_x = normalize_unsigned_axis(
                info.dwZpos, caps.wZmin, caps.wZmax
            )
            right_y = normalize_unsigned_axis(
                info.dwRpos, caps.wRmin, caps.wRmax, invert=True
            )
        elif capabilities & JOYCAPS_HASR and capabilities & JOYCAPS_HASU:
            right_x = normalize_unsigned_axis(
                info.dwRpos, caps.wRmin, caps.wRmax
            )
            right_y = normalize_unsigned_axis(
                info.dwUpos, caps.wUmin, caps.wUmax, invert=True
            )
        elif capabilities & JOYCAPS_HASU and capabilities & JOYCAPS_HASV:
            right_x = normalize_unsigned_axis(
                info.dwUpos, caps.wUmin, caps.wUmax
            )
            right_y = normalize_unsigned_axis(
                info.dwVpos, caps.wVmin, caps.wVmax, invert=True
            )
        elif int(caps.wNumAxes) >= 4:
            # Some older drivers omit the capability bits even though WinMM
            # still reports the conventional X/Y/Z/R axis set.
            right_x = normalize_unsigned_axis(
                info.dwZpos, caps.wZmin, caps.wZmax
            )
            right_y = normalize_unsigned_axis(
                info.dwRpos, caps.wRmin, caps.wRmax, invert=True
            )
        else:
            right_x = right_y = 0.0
        has_mobile_trigger_axes = is_s2p_mobile and (
            (
                capabilities & JOYCAPS_HASU
                and capabilities & JOYCAPS_HASV
            )
            or int(caps.wNumAxes) >= 6
        )
        if has_mobile_trigger_axes:
            left_trigger = normalize_trigger_axis(
                info.dwUpos, caps.wUmin, caps.wUmax
            )
            right_trigger = normalize_trigger_axis(
                info.dwVpos, caps.wVmin, caps.wVmax
            )
        else:
            left_trigger = right_trigger = None
        return GamepadState(
            packet_number=0,
            buttons_mask=button_mask,
            buttons=buttons,
            left=(
                normalize_unsigned_axis(info.dwXpos, caps.wXmin, caps.wXmax),
                normalize_unsigned_axis(
                    info.dwYpos, caps.wYmin, caps.wYmax, invert=True
                ),
            ),
            right=(right_x, right_y),
            # Generic WinMM does not describe which optional axes are
            # independent triggers. The S2P mobile HID descriptor is known,
            # so only that profile can safely expose U/V as LT/RT.
            left_trigger=left_trigger,
            right_trigger=right_trigger,
        )

    def set_xinput_rumble(self, index, left_level, right_level):
        if self.xinput is None or index is None:
            return False
        vibration = XINPUT_VIBRATION(
            max(0, min(65535, round(float(left_level) * 65535))),
            max(0, min(65535, round(float(right_level) * 65535))),
        )
        return self.xinput.XInputSetState(
            int(index), ctypes.byref(vibration)
        ) == ERROR_SUCCESS


class NativeGamepadSampler:
    """Poll generic gamepads independently from the Tk drawing cadence.

    XInput and WinMM expose snapshots rather than raw USB report timestamps.
    The measured rate is therefore the observable state-update rate while the
    controller is moving, but it is no longer capped by the tester's FPS or by
    one counted update per polling call. XInput packet-number gaps preserve the
    number of state updates that occurred between two snapshots.
    """

    def __init__(
        self,
        backend,
        clock=time.perf_counter,
        poll_interval=0.001,
    ):
        self.backend = backend
        self.clock = clock
        self.poll_interval = max(0.0005, float(poll_interval))
        self._lock = threading.Lock()
        self._backend_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._device = None
        self._latest_state = None
        self._last_token = None
        self._samples = deque(maxlen=4096)
        self._observed_update_count = 0
        self._rate_points = deque(maxlen=4096)

    @staticmethod
    def _state_token(device, state):
        if device.kind == "xinput":
            return int(state.packet_number)
        return (
            int(state.buttons_mask),
            state.buttons,
            state.left,
            state.right,
            state.left_trigger,
            state.right_trigger,
        )

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="GamepadTestNativeSampler",
        )
        self._thread.start()

    def stop(self, timeout=1.0):
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None

    def set_device(self, device):
        if device is not None and device.kind not in {"xinput", "winmm"}:
            device = None
        with self._lock:
            current_key = self._device.key if self._device is not None else None
            new_key = device.key if device is not None else None
            if current_key == new_key:
                self._device = device
                return
            self._device = device
            self._latest_state = None
            self._last_token = None
            self._samples.clear()
            self._observed_update_count = 0
            self._rate_points.clear()
        self._wake.set()

    def enumerate_devices(self, excluded_xinput_slot=None):
        with self._backend_lock:
            return self.backend.enumerate_devices(
                excluded_xinput_slot=excluded_xinput_slot
            )

    def _poll_once(self):
        with self._lock:
            device = self._device
        if device is None:
            return
        with self._backend_lock:
            state = self.backend.read_state(device)
        now = self.clock()
        with self._lock:
            if self._device is None or self._device.key != device.key:
                return
            self._latest_state = state
            if state is None:
                return
            token = self._state_token(device, state)
            if token == self._last_token:
                return
            previous_token = self._last_token
            self._last_token = token
            update_count = 1
            if device.kind == "xinput" and previous_token is not None:
                packet_delta = (
                    int(token) - int(previous_token)
                ) & 0xFFFFFFFF
                # A backwards counter after reconnect looks like an enormous
                # unsigned delta; count that snapshot once instead.
                if 0 < packet_delta <= 0x7FFFFFFF:
                    update_count = packet_delta
            self._observed_update_count += update_count
            self._samples.append((now, state))
            self._rate_points.append(
                (now, self._observed_update_count)
            )

    def read_snapshot(self):
        now = self.clock()
        with self._lock:
            latest = self._latest_state
            samples = tuple(self._samples)
            self._samples.clear()
            while (
                self._rate_points
                and now - self._rate_points[0][0] > 1.0
            ):
                self._rate_points.popleft()
            if len(self._rate_points) >= 3:
                elapsed = (
                    self._rate_points[-1][0]
                    - self._rate_points[0][0]
                )
                rate = (
                    (
                        self._rate_points[-1][1]
                        - self._rate_points[0][1]
                    ) / elapsed
                    if elapsed > 0.0 else None
                )
            else:
                rate = None
        return latest, samples, rate

    def _run(self):
        while not self._stop.is_set():
            started = self.clock()
            self._poll_once()
            elapsed = self.clock() - started
            delay = max(0.0, self.poll_interval - elapsed)
            self._wake.wait(delay)
            self._wake.clear()
