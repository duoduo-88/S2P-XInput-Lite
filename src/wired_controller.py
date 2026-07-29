# S2P-XInput-Lite wired Switch 2 Pro Controller transport.
#
# USB report translation and startup sequencing are based on Switch2Connect:
# https://github.com/TommyWabg/Switch2Connect
# Copyright (C) 2026 TommyWabg and S2P-XInput-Lite contributors.
# Licensed under GPL-3.0-or-later.

"""Read a physical Switch 2 Pro Controller over USB HID on Windows."""

import hashlib
import configparser
import os
import threading
import time
from collections import deque

from console_i18n import current_language
from console_i18n import localized_print as print
from config_utils import CONFIG_PATH, resolve_wired_calibration_id
from rumble_protocol import (
    CONNECTION_FEEDBACK_PATTERN,
    CONNECTION_HF_FREQUENCY,
    CONNECTION_LF_FREQUENCY,
    PIN_FEEDBACK_PATTERN,
    PIN_HF_FREQUENCY,
    PIN_LF_FREQUENCY,
    encode_vibration_frame,
)


NINTENDO_VENDOR_ID = 0x057E
PRO_CONTROLLER2_PID = 0x2069

REPORT_ID_COMMON = 0x05
REPORT_ID_PRO2 = 0x09
INPUT_REPORT_IDS = (REPORT_ID_COMMON, REPORT_ID_PRO2)
OUTPUT_REPORT_ID_PRO2 = 0x02
PRO2_OUTPUT_REPORT_BODY_SIZE = 0x2A
USB_COMMAND_INTERFACE = 1
USB_COMMAND_ENDPOINT_OUT = 0x02

USB_INIT_COMMAND = bytes([
    0x03, 0x91, 0x00, 0x0D, 0x00, 0x08,
    0x00, 0x00, 0x01, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
])
USB_SET_LED_COMMAND = bytes([
    0x09, 0x91, 0x00, 0x07, 0x00, 0x08,
    0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
])
USB_SELECT_COMMON_REPORT_COMMAND = bytes([
    0x03, 0x91, 0x00, 0x0A, 0x00, 0x04,
    0x00, 0x00, REPORT_ID_COMMON, 0x00, 0x00, 0x00,
])
USB_FEATURE_MASK = 0xA7


def usb_startup_commands():
    """Build startup reports for full magnetometer-enabled USB input."""
    feature_mask = USB_FEATURE_MASK
    set_mask = bytes([
        0x0C, 0x91, 0x00, 0x02, 0x00, 0x04,
        0x00, 0x00, feature_mask, 0x00, 0x00, 0x00,
    ])
    enable = bytes([
        0x0C, 0x91, 0x00, 0x04, 0x00, 0x04,
        0x00, 0x00, feature_mask, 0x00, 0x00, 0x00,
    ])
    return (
        USB_INIT_COMMAND,
        USB_SET_LED_COMMAND,
        set_mask,
        enable,
        USB_SELECT_COMMON_REPORT_COMMAND,
    )
_native_winusb_warned = False
_native_winusb_last_error = "not attempted"


def tr(zh, en):
    return en if current_language() == "en" else zh


def _import_hid():
    try:
        import hid
        return hid
    except Exception:
        return None


def wired_dependencies_available():
    return _import_hid() is not None


def enumerate_wired_controllers(global_fallback=False):
    """Return physical 057E:2069 HID collections, gamepad collection first."""
    hid = _import_hid()
    if hid is None:
        return []
    try:
        entries = hid.enumerate(NINTENDO_VENDOR_ID, PRO_CONTROLLER2_PID) or []
    except Exception:
        entries = []
    if not entries and global_fallback:
        try:
            entries = [
                item for item in (hid.enumerate() or [])
                if item.get("vendor_id") == NINTENDO_VENDOR_ID
                and item.get("product_id") == PRO_CONTROLLER2_PID
            ]
        except Exception:
            entries = []
    entries = [
        item for item in entries
        if "SWITCH2EMU" not in str(item.get("serial_number") or "").upper()
    ]

    def priority(item):
        if item.get("usage_page", 0) == 0x01 and item.get("usage", 0) in (0x04, 0x05):
            return 0
        return 1

    return sorted(entries, key=priority)


def find_wired_controller():
    entries = enumerate_wired_controllers(global_fallback=True)
    return entries[0] if entries else None


def _pyusb_backend():
    try:
        import usb.core
        import usb.util
    except Exception:
        return None, None, None
    backend = None
    try:
        import libusb_package
        backend = libusb_package.get_libusb1_backend()
    except Exception:
        try:
            import usb.backend.libusb1
            backend = usb.backend.libusb1.get_backend()
        except Exception:
            pass
    return usb.core, usb.util, backend


def _guid_from_string(value):
    import ctypes
    import uuid
    guid_value = uuid.UUID(value.strip("{}"))

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    return GUID(
        guid_value.time_low,
        guid_value.time_mid,
        guid_value.time_hi_version,
        (ctypes.c_ubyte * 8)(*guid_value.bytes[8:]),
    )


def _pro2_winusb_interface_guids():
    if os.name != "nt":
        return []
    try:
        import winreg
    except Exception:
        return []
    base_path = r"SYSTEM\CurrentControlSet\Enum\USB\VID_057E&PID_2069&MI_01"
    guids = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_path) as base_key:
            index = 0
            while True:
                try:
                    instance = winreg.EnumKey(base_key, index)
                    index += 1
                except OSError:
                    break
                if "SWITCH2EMU" in instance.upper():
                    continue
                try:
                    with winreg.OpenKey(base_key, instance) as instance_key:
                        service = str(winreg.QueryValueEx(instance_key, "Service")[0]).upper()
                    if service != "WINUSB":
                        continue
                    with winreg.OpenKey(
                        base_key, instance + r"\Device Parameters"
                    ) as params_key:
                        try:
                            value = winreg.QueryValueEx(
                                params_key, "DeviceInterfaceGUIDs"
                            )[0]
                        except OSError:
                            # Microsoft WinUSB can store a single interface
                            # under this singular value on some installations.
                            value = winreg.QueryValueEx(
                                params_key, "DeviceInterfaceGUID"
                            )[0]
                except OSError:
                    continue
                candidates = [value] if isinstance(value, str) else list(value)
                for candidate in candidates:
                    candidate = str(candidate).strip()
                    if candidate and candidate not in guids:
                        guids.append(candidate)
    except OSError:
        pass
    return guids


def _winusb_device_paths(interface_guid):
    import ctypes
    from ctypes import wintypes
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    guid = _guid_from_string(interface_guid)

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("InterfaceClassGuid", type(guid)),
            ("Flags", wintypes.DWORD),
            ("Reserved", ctypes.c_void_p),
        ]

    class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("DevicePath", wintypes.WCHAR * 1024),
        ]

    present = 0x00000002
    device_interface = 0x00000010
    invalid_handle = ctypes.c_void_p(-1).value
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(type(guid)), wintypes.LPCWSTR,
        wintypes.HWND, wintypes.DWORD,
    ]
    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(type(guid)),
        wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

    info_set = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, present | device_interface
    )
    if info_set == invalid_handle:
        return []
    paths = []
    try:
        index = 0
        while True:
            interface_data = SP_DEVICE_INTERFACE_DATA()
            interface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                info_set, None, ctypes.byref(guid), index,
                ctypes.byref(interface_data),
            ):
                break
            index += 1
            detail = SP_DEVICE_INTERFACE_DETAIL_DATA_W()
            detail.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            required = wintypes.DWORD()
            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set, ctypes.byref(interface_data), ctypes.byref(detail),
                ctypes.sizeof(detail), ctypes.byref(required), None,
            ):
                path = detail.DevicePath
                lower = path.lower()
                if "vid_057e&pid_2069&mi_01" in lower and "switch2emu" not in lower:
                    paths.append(path)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info_set)
    return paths


def _write_commands_native_winusb(commands):
    """Write command-interface reports without rebinding the HID input interface."""
    global _native_winusb_warned, _native_winusb_last_error
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    paths = []
    for guid in _pro2_winusb_interface_guids():
        paths.extend(_winusb_device_paths(guid))
    if not paths:
        _native_winusb_last_error = "no WinUSB interface path"
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    winusb = ctypes.WinDLL("winusb", use_last_error=True)

    class USB_INTERFACE_DESCRIPTOR(ctypes.Structure):
        _fields_ = [
            ("bLength", ctypes.c_ubyte),
            ("bDescriptorType", ctypes.c_ubyte),
            ("bInterfaceNumber", ctypes.c_ubyte),
            ("bAlternateSetting", ctypes.c_ubyte),
            ("bNumEndpoints", ctypes.c_ubyte),
            ("bInterfaceClass", ctypes.c_ubyte),
            ("bInterfaceSubClass", ctypes.c_ubyte),
            ("bInterfaceProtocol", ctypes.c_ubyte),
            ("iInterface", ctypes.c_ubyte),
        ]

    class WINUSB_PIPE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PipeType", ctypes.c_int),
            ("PipeId", ctypes.c_ubyte),
            ("MaximumPacketSize", ctypes.c_ushort),
            ("Interval", ctypes.c_ubyte),
        ]

    generic_read = 0x80000000
    generic_write = 0x40000000
    share_read = 0x00000001
    share_write = 0x00000002
    open_existing = 3
    normal = 0x00000080
    overlapped = 0x40000000
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    winusb.WinUsb_Initialize.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE)
    ]
    winusb.WinUsb_Initialize.restype = wintypes.BOOL
    winusb.WinUsb_QueryInterfaceSettings.argtypes = [
        wintypes.HANDLE, ctypes.c_ubyte,
        ctypes.POINTER(USB_INTERFACE_DESCRIPTOR),
    ]
    winusb.WinUsb_QueryInterfaceSettings.restype = wintypes.BOOL
    winusb.WinUsb_QueryPipe.argtypes = [
        wintypes.HANDLE, ctypes.c_ubyte, ctypes.c_ubyte,
        ctypes.POINTER(WINUSB_PIPE_INFORMATION),
    ]
    winusb.WinUsb_QueryPipe.restype = wintypes.BOOL
    winusb.WinUsb_WritePipe.argtypes = [
        wintypes.HANDLE, ctypes.c_ubyte, ctypes.POINTER(ctypes.c_ubyte),
        wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p,
    ]
    winusb.WinUsb_WritePipe.restype = wintypes.BOOL
    winusb.WinUsb_Free.argtypes = [wintypes.HANDLE]

    for path in paths:
        file_handle = kernel32.CreateFileW(
            path, generic_read | generic_write, share_read | share_write,
            None, open_existing, normal | overlapped, None,
        )
        if file_handle == invalid_handle:
            _native_winusb_last_error = (
                f"CreateFileW failed: {ctypes.get_last_error()}"
            )
            continue
        usb_handle = wintypes.HANDLE()
        try:
            if not winusb.WinUsb_Initialize(file_handle, ctypes.byref(usb_handle)):
                continue
            descriptor = USB_INTERFACE_DESCRIPTOR()
            if not winusb.WinUsb_QueryInterfaceSettings(
                usb_handle, 0, ctypes.byref(descriptor)
            ):
                continue
            endpoint = None
            for index in range(descriptor.bNumEndpoints):
                pipe = WINUSB_PIPE_INFORMATION()
                if winusb.WinUsb_QueryPipe(
                    usb_handle, 0, index, ctypes.byref(pipe)
                ) and pipe.PipeType == 2 and (pipe.PipeId & 0x80) == 0:
                    endpoint = pipe.PipeId
                    break
            endpoint = USB_COMMAND_ENDPOINT_OUT if endpoint is None else endpoint
            for command in commands:
                buffer = (ctypes.c_ubyte * len(command)).from_buffer_copy(command)
                written = wintypes.ULONG()
                if not winusb.WinUsb_WritePipe(
                    usb_handle, endpoint, buffer, len(command),
                    ctypes.byref(written), None,
                ):
                    raise OSError(
                        ctypes.get_last_error(), "WinUsb_WritePipe failed"
                    )
                time.sleep(0.02)
            _native_winusb_last_error = "success"
            return True
        except Exception as exc:
            _native_winusb_last_error = repr(exc)
            if not _native_winusb_warned:
                _native_winusb_warned = True
                print(tr(
                    f"USB 原生初始化失敗：{exc}",
                    f"Native WinUSB initialization failed: {exc}",
                ))
        finally:
            if usb_handle:
                try:
                    winusb.WinUsb_Free(usb_handle)
                except Exception:
                    pass
            kernel32.CloseHandle(file_handle)
    return False


def initialize_usb_reports():
    """Best-effort interface-1 startup needed for full 0x05 motion reports."""
    commands = usb_startup_commands()
    if _write_commands_native_winusb(commands):
        return True
    usb_core, usb_util, backend = _pyusb_backend()
    if usb_core is None:
        return False
    device = None
    claimed = False
    try:
        device = usb_core.find(
            idVendor=NINTENDO_VENDOR_ID,
            idProduct=PRO_CONTROLLER2_PID,
            backend=backend,
        )
        if device is None:
            return False
        try:
            device.set_configuration()
        except Exception:
            pass
        try:
            usb_util.claim_interface(device, USB_COMMAND_INTERFACE)
            claimed = True
        except Exception:
            pass
        endpoint = USB_COMMAND_ENDPOINT_OUT
        try:
            interface = device.get_active_configuration()[(USB_COMMAND_INTERFACE, 0)]
            for candidate in interface:
                address = int(candidate.bEndpointAddress)
                attributes = int(candidate.bmAttributes)
                if (address & 0x80) == 0 and (attributes & 0x03) == 0x02:
                    endpoint = address
                    break
        except Exception:
            pass
        for command in commands:
            device.write(endpoint, command, 1000)
            time.sleep(0.02)
        return True
    except Exception:
        return False
    finally:
        if device is not None:
            if claimed:
                try:
                    usb_util.release_interface(device, USB_COMMAND_INTERFACE)
                except Exception:
                    pass
            try:
                usb_util.dispose_resources(device)
            except Exception:
                pass


def _pro2_buttons_to_u32(b0, b1, b2):
    value = 0
    for mask, target in (
        (0x01, 0x00000004), (0x02, 0x00000008),
        (0x04, 0x00000001), (0x08, 0x00000002),
        (0x10, 0x00000040), (0x20, 0x00000080),
        (0x40, 0x00000200), (0x80, 0x00000400),
    ):
        if b0 & mask:
            value |= target
    for mask, target in (
        (0x01, 0x00010000), (0x02, 0x00040000),
        (0x04, 0x00080000), (0x08, 0x00020000),
        (0x10, 0x00400000), (0x20, 0x00800000),
        (0x40, 0x00000100), (0x80, 0x00000800),
    ):
        if b1 & mask:
            value |= target
    for mask, target in (
        (0x01, 0x00001000), (0x02, 0x00002000),
        (0x04, 0x01000000), (0x08, 0x02000000),
        (0x10, 0x00004000),
    ):
        if b2 & mask:
            value |= target
    return value


def translate_usb_report(data):
    """Translate USB 0x05/0x09 into the BLE-compatible internal layout."""
    if not data:
        return None
    report_id = data[0]
    if report_id == REPORT_ID_COMMON:
        body = bytes(data[1:])
        return body if len(body) >= 60 else body.ljust(64, b"\x00")
    if report_id != REPORT_ID_PRO2 or len(data) < 12:
        return None
    output = bytearray(64)
    output[0] = data[1]
    buttons = _pro2_buttons_to_u32(data[3], data[4], data[5])
    output[4:8] = buttons.to_bytes(4, "little")
    output[10:13] = bytes(data[6:9])
    output[13:16] = bytes(data[9:12])
    level = min((data[2] >> 2) & 0x0F, 9)
    voltage_mv = 3100 + level * 110
    output[31:33] = voltage_mv.to_bytes(2, "little")
    return bytes(output)


def _usb_output_report(data):
    payload = bytes(data)
    if len(payload) >= 33 and payload[0] == 0:
        payload = payload[1:]
    buffer = bytearray(payload[:32].ljust(32, b"\x00"))
    body = bytes(buffer).ljust(PRO2_OUTPUT_REPORT_BODY_SIZE, b"\x00")
    return bytes([OUTPUT_REPORT_ID_PRO2]) + body


def _rumble_active(data):
    payload = bytes(data)
    if len(payload) < 33:
        return any(payload)
    for base in (1, 17):
        for slot in range(3):
            frame = int.from_bytes(payload[base + 1 + slot * 5:base + 6 + slot * 5], "little")
            if ((frame >> 10) & 0x3FF) or ((frame >> 30) & 0x3FF):
                return True
    return False


class WiredController:
    """Single-controller USB transport matching the Lite BLE/ESP32 interface."""

    def __init__(self, preferred_entry=None):
        self.preferred_path = (preferred_entry or {}).get("path")
        self.input_callback = None
        self.connected_callback = None
        self.disconnected_callback = None
        self._state_lock = threading.RLock()
        self.connected = False
        self._application_ready = False
        self.running = False
        self.controller_id = None
        self.polling_rate_hz = None
        self.full_report = None
        self._thread = None
        self._device = None
        self._device_lock = threading.Lock()
        # Serializes output writes against handle close/reopen. Input reads stay
        # independent so rumble never consumes the 4 ms input budget.
        self._hid_write_lock = threading.Lock()
        self._rumble_packet_id = 0
        self._rumble_slot = None
        self._rumble_lock = threading.Lock()
        self._rumble_send_lock = threading.Lock()
        self._rumble_accepting = False
        self._feedback_lock = threading.Lock()
        self._feedback_active = False
        self._feedback_sequence = 0
        self._rumble_wake = threading.Event()
        self._rumble_stop = threading.Event()
        self._rumble_thread = None
        self._audio_haptics_active = False
        self._rumble_congested_until = 0.0
        self._rumble_congest_interval = 0.025
        self._rumble_write_failures = 0
        self._rumble_submitted = 0
        self._rumble_overwritten = 0
        self._rumble_send_attempts = 0
        self._rumble_send_successes = 0
        self._rumble_send_failures = 0
        self._rumble_send_intervals_ms = deque(maxlen=512)
        self._rumble_direct_intervals_ms = deque(maxlen=512)
        self._rumble_priority_intervals_ms = deque(maxlen=512)
        self._rumble_zero_latencies_ms = deque(maxlen=128)
        self._last_rumble_warning = 0.0
        self._recover_requested = threading.Event()
        self._connection_generation = 0

    @property
    def is_ready(self):
        with self._state_lock:
            return self.connected and self._application_ready

    def open(self):
        if self.running:
            return
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError(tr(
                "先前的 USB 執行緒仍在結束中。",
                "The previous USB worker is still stopping.",
            ))
        if not wired_dependencies_available():
            raise RuntimeError(tr(
                "缺少 hidapi，無法啟動 USB 有線連線。",
                "hidapi is missing; wired USB cannot start.",
            ))
        self.polling_rate_hz = None
        self.running = True
        self._thread = threading.Thread(
            target=self._manager_loop,
            daemon=True,
            name="WiredControllerManager",
        )
        self._thread.start()

    def close(self, timeout=2.5):
        # Stop producers first, then let the HID reader leave its timed read and
        # close the handle from the manager thread. Closing a hidapi handle from
        # another thread while native read() is still using it can crash the
        # entire Python process instead of raising a catchable exception.
        deadline = time.perf_counter() + max(0.0, float(timeout))
        self.input_callback = None
        with self._state_lock:
            zero_required = self.connected
            self._application_ready = False
        with self._rumble_lock:
            self._rumble_accepting = False
            self._feedback_active = False
            self._rumble_slot = None
        self._rumble_stop.set()
        self._rumble_wake.set()
        rumble_thread = self._rumble_thread
        manager_thread = self._thread
        if (
            rumble_thread is not None
            and rumble_thread.is_alive()
            and threading.current_thread() is not rumble_thread
        ):
            rumble_thread.join(
                timeout=max(0.0, deadline - time.perf_counter())
            )
        zero_sent = (
            self._send_shutdown_zero(
                timeout=max(0.0, deadline - time.perf_counter())
            )
            if zero_required
            else True
        )
        # Keep the reader draining input until the output writer has stopped;
        # the manager may close the shared HID handle as soon as running is
        # cleared.
        self.running = False
        if (
            manager_thread is not None
            and manager_thread.is_alive()
            and threading.current_thread() is not manager_thread
        ):
            manager_thread.join(
                timeout=max(0.0, deadline - time.perf_counter())
            )

        manager_alive = bool(manager_thread and manager_thread.is_alive())
        rumble_alive = bool(rumble_thread and rumble_thread.is_alive())
        if not manager_alive and not rumble_alive:
            # Normally the manager's finally block already performed this.
            self._close_device()
        else:
            print(tr(
                "USB 讀取執行緒尚未安全結束；保留 HID handle 以避免原生崩潰。",
                "The USB reader did not stop safely; leaving the HID handle open to avoid a native crash.",
            ))
        self._thread = manager_thread if manager_alive else None
        self._rumble_thread = rumble_thread if rumble_alive else None
        return bool(not manager_alive and not rumble_alive and zero_sent)

    def _send_shutdown_zero(self, timeout=None):
        if not self.connected:
            return False
        vibration = self._encode_vibration(
            CONNECTION_LF_FREQUENCY,
            0,
            CONNECTION_HF_FREQUENCY,
            0,
        )
        packet_id = 0x50 + (self._rumble_packet_id & 0x0F)
        self._rumble_packet_id = (self._rumble_packet_id + 1) & 0x0F
        segment = bytes([packet_id]) + vibration * 3
        report = _usb_output_report(b"\x00" + segment + segment)
        with self._rumble_lock:
            self._rumble_slot = None
        if timeout is None:
            acquired = self._rumble_send_lock.acquire()
        else:
            acquired = self._rumble_send_lock.acquire(
                timeout=max(0.0, float(timeout))
            )
        if not acquired:
            return False
        try:
            try:
                with self._hid_write_lock:
                    with self._device_lock:
                        device = self._device
                    if device is None:
                        return False
                    try:
                        written = device.write(report)
                    except TypeError:
                        written = device.write(list(report))
                return written is not None and written > 0
            except Exception:
                return False
        finally:
            self._rumble_send_lock.release()

    def _select_entry(self):
        entries = enumerate_wired_controllers(global_fallback=True)
        if self.preferred_path is not None:
            for entry in entries:
                if entry.get("path") == self.preferred_path:
                    return entry
        return entries[0] if entries else None

    @staticmethod
    def _open_hid(path):
        hid = _import_hid()
        if hasattr(hid, "device"):
            device = hid.device()
            device.open_path(path)
            try:
                device.set_nonblocking(0)
            except Exception:
                pass
            return device
        if hasattr(hid, "Device"):
            return hid.Device(path=path)
        raise RuntimeError(tr(
            "不支援的 hidapi 介面。",
            "Unsupported hidapi interface.",
        ))

    def _manager_loop(self):
        announced_search = False
        while self.running:
            entry = self._select_entry()
            if entry is None:
                if not announced_search:
                    print(tr(
                        "正在等待 USB 有線 Switch 2 Pro Controller...",
                        "Waiting for a wired USB Switch 2 Pro Controller...",
                    ))
                    announced_search = True
                time.sleep(0.5)
                continue
            announced_search = False
            try:
                self._run_device(entry)
            except Exception as exc:
                if self.running:
                    print(tr(
                        f"USB 有線控制器連線中斷：{exc}",
                        f"Wired USB controller disconnected: {exc}",
                    ))
            finally:
                with self._state_lock:
                    was_ready = self.connected and self._application_ready
                    self._application_ready = False
                    self.connected = False
                    self._connection_generation += 1
                with self._rumble_lock:
                    self._rumble_accepting = False
                    self._feedback_active = False
                    self._rumble_slot = None
                self._close_device()
                if was_ready and self.running and self.disconnected_callback is not None:
                    try:
                        self.disconnected_callback()
                    except Exception:
                        pass
            if self.running:
                time.sleep(0.4)

    def _run_device(self, entry):
        path = entry.get("path")
        if not path:
            raise RuntimeError(tr("USB HID 路徑無效。", "Invalid USB HID path."))
        device = self._open_hid(path)
        self.polling_rate_hz = None
        with self._device_lock:
            self._device = device
        self._recover_requested.clear()
        self._rumble_write_failures = 0
        self._rumble_congested_until = 0.0
        self.preferred_path = path
        serial = str(entry.get("serial_number") or "").strip()
        normalized = "".join(char for char in serial.upper() if char.isalnum())
        derived_identity = False
        if len(normalized) == 12 and normalized != "000000000000":
            self.controller_id = normalized
        else:
            derived_identity = True
            path_text = path.decode("utf-8", "ignore") if isinstance(path, bytes) else str(path)
            self.controller_id = "USB" + hashlib.sha256(path_text.encode("utf-8")).hexdigest()[:12].upper()
        identity_config = configparser.ConfigParser()
        if derived_identity and identity_config.read(CONFIG_PATH, encoding="utf-8"):
            self.controller_id = resolve_wired_calibration_id(
                identity_config, self.controller_id
            )

        try:
            probe = device.read(64, 50)
        except Exception:
            probe = None
        self.full_report = bool(probe and probe[0] == REPORT_ID_COMMON)
        # Always enable the complete motion feature mask, including the
        # magnetometer used by 9-axis orientation.
        initialized = initialize_usb_reports()
        if not initialized:
            initialized = self._send_startup_reports_hid(device)
        with self._state_lock:
            self._connection_generation += 1
            generation = self._connection_generation
            self.connected = True
            self._application_ready = False
        self._start_delayed_reinit(generation)
        with self._rumble_lock:
            self._rumble_accepting = False
            self._feedback_active = False
        callback_started = False
        try:
            if self.connected_callback is not None:
                callback_started = True
                self.connected_callback()
            with self._state_lock:
                if (
                    not self.running
                    or not self.connected
                    or generation != self._connection_generation
                ):
                    raise RuntimeError("USB connection changed during startup")
                self._application_ready = True
        except Exception:
            with self._state_lock:
                self._application_ready = False
                self.connected = False
            if callback_started and self.disconnected_callback is not None:
                try:
                    self.disconnected_callback()
                except Exception:
                    pass
            raise
        with self._rumble_lock:
            self._rumble_accepting = True
        self._start_rumble_thread()
        print(tr(
            "Switch 2 Pro Controller USB 有線輸入已啟動。",
            "Switch 2 Pro Controller wired USB input started.",
        ))
        if not initialized:
            print(tr(
                "USB 初始化命令未確認；請關閉 Steam 或其他手把程式後重新插拔。基本輸入仍可使用。",
                "USB startup commands were not confirmed. Close Steam or other controller apps, then reconnect the cable. Basic input remains available.",
            ))
        self._start_connection_rumble(generation)

        deltas = []
        last_report = None
        last_seen = time.monotonic()
        last_presence_check = 0.0
        while self.running and self.connected:
            if self._recover_requested.is_set():
                self._recover_requested.clear()
                raise RuntimeError(tr(
                    "USB 震動輸出異常，正在安全重開連線。",
                    "USB rumble output stalled; safely reopening the connection.",
                ))
            try:
                data = device.read(64, 8)
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc
            if not data:
                now = time.monotonic()
                silent_for = now - last_seen
                # HID enumeration is comparatively expensive. Check at most
                # four times per second instead of once per 8 ms read timeout.
                if silent_for > 1.0 and now - last_presence_check >= 0.25:
                    last_presence_check = now
                    if not self._path_present(path):
                        raise RuntimeError(tr("USB 已拔除", "USB unplugged"))
                if silent_for >= 2.0:
                    raise RuntimeError(tr(
                        "USB 輸入已停流 2 秒，正在重新連線。",
                        "USB input stalled for 2 seconds; reconnecting.",
                    ))
                continue
            if data[0] not in INPUT_REPORT_IDS:
                continue
            self.full_report = data[0] == REPORT_ID_COMMON
            payload = translate_usb_report(data)
            if payload is None:
                continue
            now = time.perf_counter()
            last_seen = time.monotonic()
            if last_report is not None:
                delta = now - last_report
                if 0 < delta < 0.05:
                    deltas.append(delta)
                    if len(deltas) > 64:
                        del deltas[0]
                    if len(deltas) >= 32:
                        average = sum(deltas) / len(deltas)
                        self.polling_rate_hz = 1.0 / average
            last_report = now
            callback = self.input_callback
            if callback is not None:
                callback(payload)

    def _path_present(self, path):
        return any(entry.get("path") == path for entry in enumerate_wired_controllers())

    def _send_startup_reports_hid(self, device):
        try:
            for command in usb_startup_commands():
                report = bytes([OUTPUT_REPORT_ID_PRO2]) + command.ljust(
                    PRO2_OUTPUT_REPORT_BODY_SIZE, b"\x00"
                )
                try:
                    device.write(report)
                except TypeError:
                    device.write(list(report))
                time.sleep(0.02)
            return True
        except Exception:
            return False

    def _start_delayed_reinit(self, generation):
        def worker():
            for delay in (0.8, 1.8):
                time.sleep(delay)
                if not self.running or generation != self._connection_generation:
                    return
                initialize_usb_reports()
        threading.Thread(target=worker, daemon=True, name="WiredUsbReinit").start()

    def _start_connection_rumble(self, generation):
        def worker():
            time.sleep(1.2)
            if self.running and self.connected and generation == self._connection_generation:
                self.connection_rumble(expected_generation=generation)
        threading.Thread(
            target=worker, daemon=True, name="WiredConnectionRumble"
        ).start()

    def _close_device(self):
        with self._hid_write_lock:
            with self._device_lock:
                device, self._device = self._device, None
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass

    def set_audio_haptics_active(self, active):
        """Use 16.6 ms (~60 Hz) for audio; game/zero uses 7.5 ms priority.

        Ordinary non-audio refreshes retain the 15 ms USB output pace.
        """
        self._audio_haptics_active = bool(active)

    @staticmethod
    def _encode_vibration(lf_freq, lf_amp, hf_freq, hf_amp):
        return encode_vibration_frame(lf_freq, lf_amp, hf_freq, hf_amp)

    def send_pro_rumble(
        self, lf_freq, lf_amp, hf_freq, hf_amp,
        priority=False, force_zero=False,
    ):
        with self._state_lock:
            if not self.connected:
                return False
            generation = self._connection_generation
        vibration = self._encode_vibration(lf_freq, lf_amp, hf_freq, hf_amp)
        packet_id = 0x50 + (self._rumble_packet_id & 0x0F)
        self._rumble_packet_id = (self._rumble_packet_id + 1) & 0x0F
        segment = bytes([packet_id]) + vibration * 3
        payload = b"\x00" + segment + segment
        submitted_at = time.perf_counter()
        is_zero = int(lf_amp) <= 0 and int(hf_amp) <= 0
        priority = bool(priority or force_zero or is_zero)
        with self._rumble_lock:
            if not self._rumble_accepting:
                return False
            if self._feedback_active:
                return False
            self._rumble_submitted += 1
            pending_since = submitted_at
            if self._rumble_slot is not None:
                self._rumble_overwritten += 1
                if self._rumble_slot[5] == generation:
                    pending_since = self._rumble_slot[2]
                    priority = bool(priority or self._rumble_slot[3])
            self._rumble_slot = (
                payload,
                submitted_at,
                pending_since,
                priority,
                is_zero,
                generation,
            )
        self._rumble_wake.set()
        return True

    def _reserve_feedback(self, expected_generation=None):
        """Reserve the newest cue for one physical USB generation."""
        with self._rumble_send_lock:
            with self._state_lock:
                generation = self._connection_generation
                if (
                    not self.connected
                    or (
                        expected_generation is not None
                        and generation != expected_generation
                    )
                ):
                    return None
                with self._rumble_lock:
                    if (
                        not self._rumble_accepting
                        or self._rumble_stop.is_set()
                    ):
                        return None
                    self._feedback_sequence += 1
                    token = self._feedback_sequence
                    self._feedback_active = True
                    self._rumble_slot = None
        return generation, token

    def _send_feedback_rumble_now(
        self,
        lf_freq,
        lf_amp,
        hf_freq,
        hf_amp,
        generation,
        token,
    ):
        """Write one fixed cue frame outside the normal latest-only slot."""
        with self._rumble_send_lock:
            with self._state_lock:
                if (
                    not self.connected
                    or self._connection_generation != generation
                ):
                    return False
            with self._rumble_lock:
                if (
                    not self._rumble_accepting
                    or not self._feedback_active
                    or token != self._feedback_sequence
                    or self._rumble_stop.is_set()
                ):
                    return False
                packet_id = 0x50 + (self._rumble_packet_id & 0x0F)
                self._rumble_packet_id = (
                    self._rumble_packet_id + 1
                ) & 0x0F
            vibration = self._encode_vibration(
                lf_freq, lf_amp, hf_freq, hf_amp
            )
            segment = bytes([packet_id]) + vibration * 3
            report = _usb_output_report(
                b"\x00" + segment + segment
            )
            try:
                with self._hid_write_lock:
                    with self._state_lock:
                        if (
                            not self.connected
                            or self._connection_generation != generation
                        ):
                            return False
                    with self._device_lock:
                        device = self._device
                    if device is None:
                        return False
                    try:
                        written = device.write(report)
                    except TypeError:
                        written = device.write(list(report))
                return written is not None and written > 0
            except Exception:
                return False

    def _play_fixed_feedback(
        self,
        pattern,
        lf_frequency,
        hf_frequency,
        generation=None,
        token=None,
    ):
        """Temporarily override game/audio output with a fixed cue."""
        if generation is None or token is None:
            reservation = self._reserve_feedback()
            if reservation is None:
                return False
            generation, token = reservation
        with self._feedback_lock:
            with self._state_lock:
                current = (
                    self.connected
                    and self._connection_generation == generation
                )
            with self._rumble_lock:
                if (
                    not current
                    or not self._rumble_accepting
                    or not self._feedback_active
                    or token != self._feedback_sequence
                    or self._rumble_stop.is_set()
                ):
                    return False
            completed = True
            try:
                for lf_amp, hf_amp, duration in pattern:
                    if not self._send_feedback_rumble_now(
                        lf_frequency,
                        lf_amp,
                        hf_frequency,
                        hf_amp,
                        generation,
                        token,
                    ):
                        completed = False
                        break
                    if duration:
                        time.sleep(duration)
            finally:
                # The protocol pattern already ends in zero, but repeat it if
                # the cue was interrupted between active frames.
                self._send_feedback_rumble_now(
                    lf_frequency,
                    0,
                    hf_frequency,
                    0,
                    generation,
                    token,
                )
                with self._rumble_lock:
                    if token == self._feedback_sequence:
                        self._feedback_active = False
            return completed

    def connection_rumble(self, expected_generation=None):
        """Play the fixed two-pulse cue regardless of output mix mode."""
        reservation = self._reserve_feedback(expected_generation)
        if reservation is None:
            return False
        threading.Thread(
            target=self._play_fixed_feedback,
            args=(
                CONNECTION_FEEDBACK_PATTERN,
                CONNECTION_LF_FREQUENCY,
                CONNECTION_HF_FREQUENCY,
                *reservation,
            ),
            daemon=True,
        ).start()
        return True

    def pin_rumble(self):
        """Play the same fixed two-pulse controller-identification cue."""
        reservation = self._reserve_feedback()
        if reservation is None:
            return False
        threading.Thread(
            target=self._play_fixed_feedback,
            args=(
                PIN_FEEDBACK_PATTERN,
                PIN_LF_FREQUENCY,
                PIN_HF_FREQUENCY,
                *reservation,
            ),
            daemon=True,
        ).start()
        return True

    def _start_rumble_thread(self):
        if self._rumble_thread and self._rumble_thread.is_alive():
            return
        self._rumble_stop.clear()
        self._rumble_thread = threading.Thread(
            target=self._rumble_loop,
            daemon=True,
            name="WiredRumbleWriter",
        )
        self._rumble_thread.start()

    def _rumble_loop(self):
        timer_raised = False
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
                timer_raised = True
            except Exception:
                pass
        last_write = 0.0
        inactive = 0
        try:
            while not self._rumble_stop.is_set():
                with self._rumble_lock:
                    state = self._rumble_slot
                if state is None:
                    self._rumble_wake.wait(0.5)
                    self._rumble_wake.clear()
                    continue
                (
                    data,
                    submitted_at,
                    pending_since,
                    priority,
                    is_zero,
                    generation,
                ) = state
                with self._state_lock:
                    current_generation = (
                        self.connected
                        and self._connection_generation == generation
                    )
                if not current_generation:
                    with self._rumble_lock:
                        if self._rumble_slot is state:
                            self._rumble_slot = None
                    continue

                # Keep the shared slot replaceable until this route's deadline.
                # Audio may submit every 5 ms, and a game/zero update must be
                # able to replace that older state and shorten a 16.6 ms wait
                # to the 7.5 ms priority cadence. Claim only when it is time to
                # start the HID write.
                with self._rumble_lock:
                    if self._rumble_slot is not state:
                        continue
                    if priority:
                        interval = 0.0075
                    elif self._audio_haptics_active:
                        interval = 0.0166
                    else:
                        interval = 0.015
                    if time.perf_counter() < self._rumble_congested_until:
                        interval = max(
                            interval, self._rumble_congest_interval
                        )
                    delay = (
                        last_write + interval - time.perf_counter()
                    )
                if delay > 0.0:
                    # Re-read the slot at least once per millisecond so a new
                    # priority or zero frame can move the deadline forward
                    # without relying on Windows Event timeout precision.
                    time.sleep(min(delay, 0.001))
                    continue

                # The deadline looked ready, but a fixed feedback cue or
                # shutdown zero may hold the send lock for much longer than a
                # pacing interval. Keep the slot replaceable while waiting,
                # then re-read and claim the latest state immediately before
                # the HID write.
                sleep_for = 0.0
                attempted = False
                written = None
                started = 0.0
                finished = 0.0
                with self._rumble_send_lock:
                    with self._hid_write_lock:
                        device = None
                        report = None
                        with self._state_lock:
                            with self._rumble_lock:
                                state = self._rumble_slot
                                if state is not None:
                                    (
                                        data,
                                        submitted_at,
                                        pending_since,
                                        priority,
                                        is_zero,
                                        generation,
                                    ) = state
                                current_generation = (
                                    state is not None
                                    and self.connected
                                    and self._connection_generation
                                    == generation
                                )
                                accepting = (
                                    current_generation
                                    and self._rumble_accepting
                                    and not self._feedback_active
                                    and not self._rumble_stop.is_set()
                                )
                                if state is not None and not accepting:
                                    self._rumble_slot = None
                                elif state is not None:
                                    if priority:
                                        interval = 0.0075
                                    elif self._audio_haptics_active:
                                        interval = 0.0166
                                    else:
                                        interval = 0.015
                                    if (
                                        time.perf_counter()
                                        < self._rumble_congested_until
                                    ):
                                        interval = max(
                                            interval,
                                            self._rumble_congest_interval,
                                        )
                                    due_time = last_write + interval
                                    remaining = (
                                        due_time - time.perf_counter()
                                    )
                                    if remaining > 0.0:
                                        sleep_for = min(remaining, 0.001)
                                    else:
                                        active = _rumble_active(data)
                                        next_inactive = (
                                            0 if active else inactive + 1
                                        )
                                        if next_inactive > 3:
                                            inactive = next_inactive
                                            self._rumble_slot = None
                                        else:
                                            with self._device_lock:
                                                device = self._device
                                            if device is None:
                                                self._rumble_slot = None
                                            else:
                                                inactive = next_inactive
                                                report = _usb_output_report(
                                                    data
                                                )
                                                self._rumble_slot = None
                                                started = (
                                                    time.perf_counter()
                                                )
                                                if (
                                                    last_write > 0.0
                                                    and pending_since
                                                    < due_time
                                                ):
                                                    interval_ms = (
                                                        started - last_write
                                                    ) * 1000.0
                                                    self._rumble_send_intervals_ms.append(
                                                        interval_ms
                                                    )
                                                    if priority:
                                                        self._rumble_priority_intervals_ms.append(
                                                            interval_ms
                                                        )
                                                    else:
                                                        self._rumble_direct_intervals_ms.append(
                                                            interval_ms
                                                        )
                                                if is_zero:
                                                    self._rumble_zero_latencies_ms.append(
                                                        (
                                                            started
                                                            - submitted_at
                                                        )
                                                        * 1000.0
                                                    )
                                                self._rumble_send_attempts += 1
                                                last_write = started
                                                attempted = True
                        if attempted:
                            try:
                                try:
                                    written = device.write(report)
                                except TypeError:
                                    written = device.write(list(report))
                            except Exception:
                                written = None
                            finished = time.perf_counter()

                if not attempted:
                    if sleep_for > 0.0:
                        time.sleep(sleep_for)
                    continue

                elapsed = finished - started
                # Pace write *starts*. A slow HID call already consumes the
                # interval; waiting again from its completion would double the
                # intended backoff (for example 45 ms + another 45 ms).
                if elapsed > 0.040:
                    self._rumble_congest_interval = min(
                        0.040, max(0.025, elapsed)
                    )
                    self._rumble_congested_until = finished + 0.5
                    now = time.monotonic()
                    if now - self._last_rumble_warning >= 1.0:
                        self._last_rumble_warning = now
                        print(tr(
                            f"USB 震動輸出壅塞（{elapsed * 1000:.1f} ms），暫時降低傳送頻率。",
                            f"USB rumble output congested ({elapsed * 1000:.1f} ms); temporarily reducing the send rate.",
                        ))

                if written is not None and written > 0:
                    self._rumble_write_failures = 0
                    with self._rumble_lock:
                        self._rumble_send_successes += 1
                else:
                    self._rumble_write_failures += 1
                    with self._rumble_lock:
                        self._rumble_send_failures += 1
                    if self._rumble_write_failures >= 3:
                        self._recover_requested.set()
        finally:
            if timer_raised:
                try:
                    import ctypes
                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass

    def reset_rumble_diagnostics(self):
        """Reset counters after connection haptics and before a measurement."""
        with self._rumble_lock:
            self._rumble_submitted = 0
            self._rumble_overwritten = 0
            self._rumble_send_attempts = 0
            self._rumble_send_successes = 0
            self._rumble_send_failures = 0
            self._rumble_send_intervals_ms.clear()
            self._rumble_direct_intervals_ms.clear()
            self._rumble_priority_intervals_ms.clear()
            self._rumble_zero_latencies_ms.clear()

    def get_rumble_diagnostics(self):
        """Return a thread-safe snapshot of latest-only USB output."""
        with self._rumble_lock:
            intervals = list(self._rumble_send_intervals_ms)
            direct_intervals = list(self._rumble_direct_intervals_ms)
            priority_intervals = list(self._rumble_priority_intervals_ms)
            zero_latencies = list(self._rumble_zero_latencies_ms)
            result = {
                "submitted": self._rumble_submitted,
                "overwritten": self._rumble_overwritten,
                "send_attempts": self._rumble_send_attempts,
                "send_successes": self._rumble_send_successes,
                "send_failures": self._rumble_send_failures,
                "interval_samples": len(intervals),
                "zero_latency_samples": len(zero_latencies),
            }

        def add_distribution(prefix, values):
            if not values:
                return
            ordered = sorted(values)
            p95_index = int(0.95 * (len(ordered) - 1))
            result[f"{prefix}_avg_ms"] = sum(values) / len(values)
            result[f"{prefix}_min_ms"] = ordered[0]
            result[f"{prefix}_max_ms"] = ordered[-1]
            result[f"{prefix}_p95_ms"] = ordered[p95_index]

        add_distribution("interval", intervals)
        add_distribution("direct_interval", direct_intervals)
        add_distribution("priority_interval", priority_intervals)
        add_distribution("zero_latency", zero_latencies)
        return result
