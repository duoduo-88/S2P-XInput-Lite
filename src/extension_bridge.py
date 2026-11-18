"""Optional Extensions bridge: merge input submitted by external tools.

S2P stays the only XInput output.  Independent tools (S2P-Extensions) connect
to a local Windows named pipe and submit controller *state*; the connector
merges every connected tool's state into the report built from the physical
controller just before the one existing ``pad.update()``.  Tools never touch
ViGEmBus.

Threads: one daemon thread per enabled tool (at most ``MAX_TOOLS``), created
only while Extensions is enabled.  Each blocks in ``WaitForMultipleObjects``
on its own overlapped pipe instance plus a shared stop event, so nothing
polls.  The input thread takes one uncontended lock per connected tool per
report.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from ctypes import wintypes
except (ImportError, ValueError):  # pragma: no cover - non-Windows
    wintypes = None


PROTOCOL_VERSION = 1
PIPE_NAME = r"\\.\pipe\S2P-XInput-Lite.Extensions"
MAX_TOOLS = 4
MAX_MESSAGE_BYTES = 4096
HANDSHAKE_TIMEOUT_MS = 3000
WRITE_TIMEOUT_MS = 500
ERROR_LINGER_MS = 200  # time a refused tool gets to read the error reply
# Flood guard: bursts of up to FLOOD_WINDOW_MESSAGES pass immediately; a tool
# that keeps sending faster than ~2000 messages/s is parked (GIL released)
# for the rest of the window, so it cannot crowd out the physical input
# thread.  A per-message sleep is avoided because Windows' default 15.6 ms
# timer would add that delay to ordinary short bursts.
FLOOD_WINDOW_SECONDS = 0.05
FLOOD_WINDOW_MESSAGES = 100
# A tool holding input must send something (state or ping) at least this
# often; otherwise its input is cleared while the connection stays open.
INPUT_TIMEOUT_MS = 1000
CAPABILITY_INPUT = "input"   # submit controller input (write)
CAPABILITY_READ = "read"     # read controller information and S2P's output
KNOWN_CAPABILITIES = (CAPABILITY_INPUT, CAPABILITY_READ)
# Sticks are exclusive: two tools steering one stick would fight each other.
# Buttons (OR) and triggers (max) merge without conflict.
STICKS = ("left", "right")
TOKEN_ENV = "S2P_EXTENSION_TOKEN"
PIPE_ENV = "S2P_EXTENSION_PIPE"
# Set to "1" when S2P starts a tool on its own: the tool should start without
# showing its window (for example straight into the notification area).
BACKGROUND_ENV = "S2P_EXTENSION_BACKGROUND"
SETTINGS_SECTION = "extensions"
TOOL_SECTION_PREFIX = "extensions.tool."
TOOL_NAME_LIMIT = 64

# XUSB button bits; identical to vgamepad.XUSB_BUTTON.  Kept literal so the
# protocol can be decoded and tested without loading ViGEm.
BUTTON_BITS = {
    "UP": 0x0001,
    "DOWN": 0x0002,
    "LEFT": 0x0004,
    "RIGHT": 0x0008,
    "START": 0x0010,
    "BACK": 0x0020,
    "L_STK": 0x0040,
    "R_STK": 0x0080,
    "LB": 0x0100,
    "RB": 0x0200,
    "GUIDE": 0x0400,
    "A": 0x1000,
    "B": 0x2000,
    "X": 0x4000,
    "Y": 0x8000,
}
ALL_BUTTON_BITS = 0
for _bit in BUTTON_BITS.values():
    ALL_BUTTON_BITS |= _bit
del _bit

STATE_DISABLED = "disabled"
STATE_WAITING = "waiting"
STATE_CONNECTED = "connected"
STATE_DISCONNECTED = "disconnected"
STATE_ERROR = "error"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSettings:
    tool_id: str
    path: str
    enabled: bool = True
    auto_launch: bool = False
    allow_input: bool = True
    allow_read: bool = False


@dataclass(frozen=True)
class ExtensionSettings:
    enabled: bool = False
    tools: tuple = ()

    @property
    def active_tools(self):
        """Enabled tools with a path, capped at MAX_TOOLS in list order."""
        return tuple(tool for tool in self.tools if tool.enabled and tool.path)[:MAX_TOOLS]

    def tool(self, tool_id):
        for tool in self.tools:
            if tool.tool_id == tool_id:
                return tool
        return None


def new_tool_id():
    return secrets.token_hex(4)


def normalized_path(path):
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (OSError, ValueError):
        return str(path)


def settings_problems(settings: ExtensionSettings):
    """Configuration conflicts, as (code, tool_id) pairs, in list order."""
    problems = []
    seen = {}
    enabled = 0
    for tool in settings.tools:
        key = normalized_path(tool.path)
        if key in seen:
            problems.append(("duplicate_tool", tool.tool_id))
            continue
        seen[key] = tool.tool_id
        if not tool.enabled:
            continue
        enabled += 1
        if enabled > MAX_TOOLS:
            problems.append(("too_many_tools", tool.tool_id))
        if not (tool.allow_input or tool.allow_read):
            problems.append(("no_permission", tool.tool_id))
    return problems


def load_extension_settings(config) -> ExtensionSettings:
    """Read [extensions] and its tool sections; anything malformed is off."""
    if config is None or not config.has_section(SETTINGS_SECTION):
        return ExtensionSettings()

    def flag(section, key, default=False):
        try:
            return config.getboolean(section, key, fallback=default)
        except ValueError:
            return False

    entries = []
    for section in config.sections():
        if not section.startswith(TOOL_SECTION_PREFIX):
            continue
        tool_id = section[len(TOOL_SECTION_PREFIX):].strip()
        path = str(config.get(section, "path", fallback="")).strip()
        if not tool_id or not path:
            continue
        try:
            order = config.getint(section, "order", fallback=0)
        except ValueError:
            order = 0
        entries.append((order, ToolSettings(
            tool_id=tool_id,
            path=path,
            enabled=flag(section, "enabled", True),
            auto_launch=flag(section, "auto_launch"),
            allow_input=flag(section, "allow_input", True),
            allow_read=flag(section, "allow_read"),
        )))
    entries.sort(key=lambda item: item[0])
    legacy_path = str(config.get(SETTINGS_SECTION, "tool_path", fallback="")).strip()
    if legacy_path and not entries:
        # Single-tool settings from an earlier development build.
        entries.append((0, ToolSettings(
            tool_id="legacy",
            path=legacy_path,
            auto_launch=flag(SETTINGS_SECTION, "auto_launch"),
            allow_input=flag(SETTINGS_SECTION, "allow_input", True),
            allow_read=flag(SETTINGS_SECTION, "allow_read"),
        )))
    tools = []
    seen = set()
    for _order, tool in entries:
        key = normalized_path(tool.path)
        if key in seen:  # a duplicate could never be told apart at connect
            continue
        seen.add(key)
        tools.append(tool)
    return ExtensionSettings(
        enabled=flag(SETTINGS_SECTION, "enabled"), tools=tuple(tools),
    )


def store_extension_settings(config, settings: ExtensionSettings):
    if not config.has_section(SETTINGS_SECTION):
        config.add_section(SETTINGS_SECTION)
    for key in ("tool_path", "auto_launch", "allow_input", "allow_read"):
        config.remove_option(SETTINGS_SECTION, key)
    config.set(SETTINGS_SECTION, "enabled", "true" if settings.enabled else "false")
    for section in [
        name for name in config.sections() if name.startswith(TOOL_SECTION_PREFIX)
    ]:
        config.remove_section(section)
    for order, tool in enumerate(settings.tools):
        section = TOOL_SECTION_PREFIX + tool.tool_id
        config.add_section(section)
        config.set(section, "order", str(order))
        config.set(section, "path", tool.path)
        for key in ("enabled", "auto_launch", "allow_input", "allow_read"):
            config.set(section, key, "true" if getattr(tool, key) else "false")


# ---------------------------------------------------------------------------
# Protocol decoding
# ---------------------------------------------------------------------------

class ProtocolError(ValueError):
    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class ExternalState:
    buttons: int = 0
    left_trigger: int | None = None
    right_trigger: int | None = None
    left_stick: tuple | None = None
    right_stick: tuple | None = None


EMPTY_STATE = ExternalState()


def decode_buttons(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ProtocolError("bad_message", "buttons must be a list or mask")
    if isinstance(value, int):
        if value < 0 or value & ~ALL_BUTTON_BITS:
            raise ProtocolError("bad_message", "buttons mask out of range")
        return value
    if isinstance(value, (list, tuple)):
        mask = 0
        for name in value:
            bit = BUTTON_BITS.get(str(name).strip().upper())
            if bit is None:
                raise ProtocolError("bad_message", f"unknown button {name!r}")
            mask |= bit
        return mask
    raise ProtocolError("bad_message", "buttons must be a list or mask")


def _integer(message, key, low, high):
    value = message.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError("bad_message", f"{key} must be an integer")
    if not low <= value <= high:
        raise ProtocolError("bad_message", f"{key} out of range")
    return value


def _stick(message, x_key, y_key):
    x_value = _integer(message, x_key, -32768, 32767)
    y_value = _integer(message, y_key, -32768, 32767)
    if x_value is None and y_value is None:
        return None
    if x_value is None or y_value is None:
        raise ProtocolError("bad_message", f"{x_key} and {y_key} must be sent together")
    return (x_value, y_value)


def decode_state(message: dict) -> ExternalState:
    """Decode one full-state message.  Absent analog fields are not driven."""
    return ExternalState(
        buttons=decode_buttons(message.get("buttons")),
        left_trigger=_integer(message, "lt", 0, 255),
        right_trigger=_integer(message, "rt", 0, 255),
        left_stick=_stick(message, "lx", "ly"),
        right_stick=_stick(message, "rx", "ry"),
    )


def decode_message(raw: bytes) -> list[dict]:
    """Split one pipe message into JSON objects (newline-separated allowed)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("bad_message", "message is not UTF-8") from exc
    messages = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            raise ProtocolError("bad_message", "message is not JSON") from exc
        if not isinstance(message, dict):
            raise ProtocolError("bad_message", "message must be an object")
        messages.append(message)
    return messages


def encode_message(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Input merge
# ---------------------------------------------------------------------------

class ExtensionInputMixer:
    """Thread-safe external state merged into the physical XInput report.

    Buttons: OR.  A press shorter than one physical report is latched for one
    report so it is never lost.  Releasing the external source never clears a
    bit the physical report still sets, because the physical report is rebuilt
    from scratch every frame before this merge.

    Triggers: max(physical, external) -- continuous, so no jumps.

    Sticks: physical priority with hysteresis.  The external value is used
    only while the physical stick (after curves and gyro) is idle.  Moving the
    physical stick past ``STICK_ENGAGE`` hands it to the player immediately;
    it returns to the tool only after staying below ``STICK_RELEASE`` for
    ``STICK_HANDBACK_SECONDS``, which prevents flicker between sources.
    """

    STICK_ENGAGE = 0.12
    STICK_RELEASE = 0.06
    STICK_HANDBACK_SECONDS = 0.15

    def __init__(self, clock=time.perf_counter):
        self._lock = threading.Lock()
        self._state = EMPTY_STATE
        self._latched = 0
        self._clock = clock
        self._physical_owns = [False, False]
        self._handback_since = [None, None]

    @property
    def has_input(self):
        with self._lock:
            return self._state is not EMPTY_STATE or bool(self._latched)

    def submit(self, state: ExternalState):
        with self._lock:
            self._latched |= state.buttons & ~self._state.buttons
            self._state = state

    def clear(self):
        with self._lock:
            self._state = EMPTY_STATE
            self._latched = 0

    @property
    def state(self) -> ExternalState:
        with self._lock:
            return self._state

    def _physical_stick_owns(self, index, magnitude, now):
        if not self._physical_owns[index]:
            if magnitude >= self.STICK_ENGAGE:
                self._physical_owns[index] = True
                self._handback_since[index] = None
            return self._physical_owns[index]
        if magnitude >= self.STICK_RELEASE:
            self._handback_since[index] = None
            return True
        since = self._handback_since[index]
        if since is None:
            self._handback_since[index] = now
            return True
        if now - since >= self.STICK_HANDBACK_SECONDS:
            self._physical_owns[index] = False
            self._handback_since[index] = None
            return False
        return True

    def merge_into(self, report):
        """Called by the input thread right before the one pad.update()."""
        with self._lock:
            state = self._state
            latched = self._latched
            self._latched = 0
        if state is EMPTY_STATE and not latched:
            self._physical_owns[0] = self._physical_owns[1] = False
            self._handback_since[0] = self._handback_since[1] = None
            return
        buttons = state.buttons | latched
        if buttons:
            report.wButtons = int(report.wButtons) | buttons
        if state.left_trigger is not None and state.left_trigger > report.bLeftTrigger:
            report.bLeftTrigger = state.left_trigger
        if state.right_trigger is not None and state.right_trigger > report.bRightTrigger:
            report.bRightTrigger = state.right_trigger
        if state.left_stick is None and state.right_stick is None:
            return
        now = self._clock()
        for index, x_name, y_name, value in (
            (0, "sThumbLX", "sThumbLY", state.left_stick),
            (1, "sThumbRX", "sThumbRY", state.right_stick),
        ):
            if value is None:
                self._physical_owns[index] = False
                self._handback_since[index] = None
                continue
            magnitude = math.hypot(
                getattr(report, x_name), getattr(report, y_name)
            ) / 32767.0
            if not self._physical_stick_owns(index, magnitude, now):
                setattr(report, x_name, value[0])
                setattr(report, y_name, value[1])


# ---------------------------------------------------------------------------
# Win32 named pipe server
# ---------------------------------------------------------------------------

class _ClientGone(Exception):
    pass


class _Stopping(Exception):
    pass


class _Timeout(Exception):
    pass


def button_names(mask):
    return [name for name, bit in BUTTON_BITS.items() if mask & bit]


def _load_win32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class OVERLAPPED(ctypes.Structure):
        _fields_ = (
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        )

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = (
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        )

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = (("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD))

    def bind(dll, name, restype, *argtypes):
        function = getattr(dll, name)
        function.restype = restype
        function.argtypes = argtypes
        return function

    HANDLE = wintypes.HANDLE
    LP_OV = ctypes.POINTER(OVERLAPPED)
    api = {
        "OVERLAPPED": OVERLAPPED,
        "SECURITY_ATTRIBUTES": SECURITY_ATTRIBUTES,
        "SID_AND_ATTRIBUTES": SID_AND_ATTRIBUTES,
        "CreateNamedPipeW": bind(
            kernel32, "CreateNamedPipeW", HANDLE, wintypes.LPCWSTR,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(SECURITY_ATTRIBUTES),
        ),
        "ConnectNamedPipe": bind(kernel32, "ConnectNamedPipe", wintypes.BOOL, HANDLE, LP_OV),
        "DisconnectNamedPipe": bind(kernel32, "DisconnectNamedPipe", wintypes.BOOL, HANDLE),
        "ReadFile": bind(
            kernel32, "ReadFile", wintypes.BOOL, HANDLE, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), LP_OV,
        ),
        "WriteFile": bind(
            kernel32, "WriteFile", wintypes.BOOL, HANDLE, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), LP_OV,
        ),
        "GetOverlappedResult": bind(
            kernel32, "GetOverlappedResult", wintypes.BOOL, HANDLE, LP_OV,
            ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
        ),
        "CancelIoEx": bind(kernel32, "CancelIoEx", wintypes.BOOL, HANDLE, LP_OV),
        "CreateEventW": bind(
            kernel32, "CreateEventW", HANDLE, ctypes.c_void_p, wintypes.BOOL,
            wintypes.BOOL, wintypes.LPCWSTR,
        ),
        "SetEvent": bind(kernel32, "SetEvent", wintypes.BOOL, HANDLE),
        "ResetEvent": bind(kernel32, "ResetEvent", wintypes.BOOL, HANDLE),
        "WaitForMultipleObjects": bind(
            kernel32, "WaitForMultipleObjects", wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(HANDLE), wintypes.BOOL, wintypes.DWORD,
        ),
        "CloseHandle": bind(kernel32, "CloseHandle", wintypes.BOOL, HANDLE),
        "LocalFree": bind(kernel32, "LocalFree", ctypes.c_void_p, ctypes.c_void_p),
        "GetCurrentProcess": bind(kernel32, "GetCurrentProcess", HANDLE),
        "GetNamedPipeClientProcessId": bind(
            kernel32, "GetNamedPipeClientProcessId", wintypes.BOOL, HANDLE,
            ctypes.POINTER(wintypes.ULONG),
        ),
        "OpenProcess": bind(
            kernel32, "OpenProcess", HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ),
        "QueryFullProcessImageNameW": bind(
            kernel32, "QueryFullProcessImageNameW", wintypes.BOOL, HANDLE,
            wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ),
        "OpenProcessToken": bind(
            advapi32, "OpenProcessToken", wintypes.BOOL, HANDLE, wintypes.DWORD,
            ctypes.POINTER(HANDLE),
        ),
        "GetTokenInformation": bind(
            advapi32, "GetTokenInformation", wintypes.BOOL, HANDLE, ctypes.c_int,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ),
        "ConvertSidToStringSidW": bind(
            advapi32, "ConvertSidToStringSidW", wintypes.BOOL, ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        ),
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": bind(
            advapi32, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            wintypes.BOOL, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG),
        ),
    }
    return api


_WIN32 = None


def _win32():
    global _WIN32
    if _WIN32 is None:
        _WIN32 = _load_win32()
    return _WIN32


PIPE_ACCESS_DUPLEX = 0x00000003
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
ERROR_ACCESS_DENIED = 5
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
ERROR_PIPE_NOT_CONNECTED = 233
ERROR_MORE_DATA = 234
ERROR_PIPE_BUSY = 231
ERROR_PIPE_CONNECTED = 535
ERROR_OPERATION_ABORTED = 995
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_CLIENT_GONE_ERRORS = (ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_PIPE_NOT_CONNECTED)


def _current_user_sid() -> str:
    api = _win32()
    token = wintypes.HANDLE()
    if not api["OpenProcessToken"](api["GetCurrentProcess"](), TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        api["GetTokenInformation"](token, TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not api["GetTokenInformation"](
            token, TOKEN_USER_CLASS, buffer, size, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        user = api["SID_AND_ATTRIBUTES"].from_buffer(buffer)
        text = wintypes.LPWSTR()
        if not api["ConvertSidToStringSidW"](user.Sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            api["LocalFree"](text)
    finally:
        api["CloseHandle"](token)


def process_image_path(pid) -> str | None:
    if sys.platform != "win32":
        return None
    api = _win32()
    handle = api["OpenProcess"](PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not api["QueryFullProcessImageNameW"](handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        api["CloseHandle"](handle)


def same_path(first, second) -> bool:
    if not first or not second:
        return False
    try:
        return os.path.normcase(os.path.abspath(str(first))) == os.path.normcase(
            os.path.abspath(str(second))
        )
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Merge hub: the one object attached to XInputController
# ---------------------------------------------------------------------------

class ExtensionInputHub:
    """Merge every connected tool into the physical report, in order.

    Buttons OR together and triggers keep the maximum, so several tools never
    conflict on them.  Each stick has at most one owning tool (enforced at
    connect time), so stick merges never fight.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._mixers = ()  # replaced as a whole; the input thread reads it once
        self._readers = 0
        self.physical_snapshot = None
        self._frame = 0

    @property
    def mixers(self):
        return self._mixers

    def add(self, mixer, reader=False):
        with self._lock:
            self._mixers = self._mixers + (mixer,)
            if reader:
                self._readers += 1

    def remove(self, mixer, reader=False):
        with self._lock:
            self._mixers = tuple(item for item in self._mixers if item is not mixer)
            if reader:
                self._readers = max(0, self._readers - 1)
                if not self._readers:
                    self.physical_snapshot = None
        mixer.clear()

    def merge_into(self, report):
        """Called by the input thread right before the one pad.update()."""
        if self._readers:
            self._frame += 1
            # S2P's own output from before any tool merge; one tuple
            # assignment that the IPC threads only read.
            self.physical_snapshot = (
                self._frame, time.perf_counter(), int(report.wButtons),
                int(report.bLeftTrigger), int(report.bRightTrigger),
                int(report.sThumbLX), int(report.sThumbLY),
                int(report.sThumbRX), int(report.sThumbRY),
            )
        for mixer in self._mixers:
            mixer.merge_into(report)


@dataclass
class ExtensionSession:
    tool_id: str
    name: str
    granted: tuple
    sticks: tuple
    mixer: ExtensionInputMixer = field(default_factory=ExtensionInputMixer)


class _Dropped(Exception):
    pass


class _PipeInstance:
    """One pipe instance and its blocking thread; serves one tool at a time."""

    def __init__(self, server, handle):
        api = _win32()
        self.server = server
        self.pipe = handle
        self.io_event = api["CreateEventW"](None, True, False, None)
        self.drop_event = api["CreateEventW"](None, True, False, None)
        if not self.io_event or not self.drop_event:
            error = ctypes.get_last_error()
            self.close_handles()
            raise ctypes.WinError(error)
        self.buffer = ctypes.create_string_buffer(MAX_MESSAGE_BYTES)
        self.session = None
        self.drop_code = "user_disconnected"
        self.thread = threading.Thread(
            target=self._run, name="S2P-Extensions-IPC", daemon=True
        )

    def close_handles(self):
        api = _win32()
        for name in ("pipe", "io_event", "drop_event"):
            handle = getattr(self, name)
            if handle:
                api["CloseHandle"](handle)
            setattr(self, name, None)

    # -- overlapped helpers -------------------------------------------------

    def _wait(self, timeout_ms, droppable):
        api = _win32()
        handles = [self.io_event, self.server.stop_event]
        if droppable:
            handles.append(self.drop_event)
        array = (wintypes.HANDLE * len(handles))(*handles)
        result = api["WaitForMultipleObjects"](len(handles), array, False, timeout_ms)
        if result == WAIT_OBJECT_0:
            return "io"
        if result == WAIT_OBJECT_0 + 1:
            return "stop"
        if result == WAIT_OBJECT_0 + 2:
            return "drop"
        if result == WAIT_TIMEOUT:
            return "timeout"
        raise ctypes.WinError(ctypes.get_last_error())

    def _complete(self, overlapped, start_ok, timeout_ms, droppable=True):
        """Finish an overlapped call; returns transferred bytes."""
        api = _win32()
        transferred = wintypes.DWORD()
        if not start_ok:
            error = ctypes.get_last_error()
            if error != ERROR_IO_PENDING:
                return self._raise_io(error)
            outcome = self._wait(timeout_ms, droppable)
            if outcome != "io":
                api["CancelIoEx"](self.pipe, ctypes.byref(overlapped))
                # Wait for the cancellation so the OVERLAPPED stays valid.
                api["GetOverlappedResult"](
                    self.pipe, ctypes.byref(overlapped), ctypes.byref(transferred), True
                )
                if outcome == "stop":
                    raise _Stopping()
                if outcome == "drop":
                    raise _Dropped()
                raise _Timeout()
        if not api["GetOverlappedResult"](
            self.pipe, ctypes.byref(overlapped), ctypes.byref(transferred), False
        ):
            return self._raise_io(ctypes.get_last_error())
        return transferred.value

    @staticmethod
    def _raise_io(error):
        if error in _CLIENT_GONE_ERRORS or error == ERROR_OPERATION_ABORTED:
            raise _ClientGone()
        if error == ERROR_MORE_DATA:
            raise ProtocolError("message_too_large")
        raise ctypes.WinError(error)

    def _new_overlapped(self):
        api = _win32()
        api["ResetEvent"](self.io_event)
        overlapped = api["OVERLAPPED"]()
        overlapped.hEvent = self.io_event
        return overlapped

    def _connect(self):
        api = _win32()
        overlapped = self._new_overlapped()
        if api["ConnectNamedPipe"](self.pipe, ctypes.byref(overlapped)):
            return
        error = ctypes.get_last_error()
        if error == ERROR_PIPE_CONNECTED:
            return
        if error != ERROR_IO_PENDING:
            raise ctypes.WinError(error)
        self._complete(overlapped, False, INFINITE, droppable=False)

    def _read(self, timeout_ms=INFINITE) -> bytes:
        api = _win32()
        overlapped = self._new_overlapped()
        ok = api["ReadFile"](
            self.pipe, self.buffer, MAX_MESSAGE_BYTES, None, ctypes.byref(overlapped)
        )
        size = self._complete(overlapped, ok, timeout_ms)
        return self.buffer.raw[:size]

    def write(self, message):
        api = _win32()
        data = encode_message(message)
        buffer = ctypes.create_string_buffer(data, len(data))
        overlapped = self._new_overlapped()
        ok = api["WriteFile"](
            self.pipe, buffer, len(data), None, ctypes.byref(overlapped)
        )
        try:
            self._complete(overlapped, ok, WRITE_TIMEOUT_MS, droppable=False)
        except _Timeout:
            # A tool that never reads must not stall this thread.
            pass

    def _client_pid(self):
        api = _win32()
        pid = wintypes.ULONG()
        if not api["GetNamedPipeClientProcessId"](self.pipe, ctypes.byref(pid)):
            return None
        return int(pid.value)

    # -- session ------------------------------------------------------------

    def _handshake(self):
        try:
            raw = self._read(HANDSHAKE_TIMEOUT_MS)
        except _Timeout:
            raise ProtocolError("handshake_timeout") from None
        messages = decode_message(raw)
        if not messages or messages[0].get("type") != "hello":
            raise ProtocolError("bad_message", "first message must be hello")
        hello = messages[0]
        if hello.get("protocol") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported_protocol", str(hello.get("protocol")))
        pid = self._client_pid()
        image = process_image_path(pid) if pid else None
        # Raises ProtocolError (unauthorized, conflict, ...) when refused.
        self.session = self.server.open_session(pid, image, hello)
        welcome = {
            "type": "welcome",
            "protocol": PROTOCOL_VERSION,
            "server": "S2P-XInput-Lite",
            "server_version": self.server.server_version,
            "capabilities": list(self.session.granted),
            "sticks": list(self.session.sticks),
            "input_timeout_ms": INPUT_TIMEOUT_MS,
        }
        host_pid = getattr(self.server.owner, "host_pid", 0)
        if host_pid:
            # The S2P settings process: tools close when it exits, but not when
            # only the connector restarts.
            welcome["host_pid"] = host_pid
        self.write(welcome)
        return messages[1:]

    def _require(self, capability):
        if capability not in self.session.granted:
            raise ProtocolError("permission_denied", capability)

    def _input_reply(self):
        snapshot = self.server.hub.physical_snapshot
        if snapshot is None:
            return {"type": "input", "frame": 0}
        frame, stamp, buttons, lt, rt, lx, ly, rx, ry = snapshot
        return {
            "type": "input",
            "frame": frame,
            "age_ms": round((time.perf_counter() - stamp) * 1000.0, 2),
            "buttons": button_names(buttons),
            "lt": lt, "rt": rt, "lx": lx, "ly": ly, "rx": rx, "ry": ry,
        }

    def _handle(self, message):
        kind = message.get("type")
        if kind == "state":
            self._require(CAPABILITY_INPUT)
            state = decode_state(message)
            for side, value in (("left", state.left_stick), ("right", state.right_stick)):
                if value is not None and side not in self.session.sticks:
                    raise ProtocolError("permission_denied", f"{side} stick not claimed")
            self.session.mixer.submit(state)
        elif kind == "release_all":
            self.session.mixer.clear()
        elif kind == "get_controller":
            self._require(CAPABILITY_READ)
            info = dict(self.server.controller_info() or {})
            info["type"] = "controller"
            self.write(info)
        elif kind == "get_input":
            self._require(CAPABILITY_READ)
            self.write(self._input_reply())
        elif kind == "bye":
            raise _ClientGone()
        elif kind == "ping":
            self.write({"type": "pong", "seq": message.get("seq")})
        else:
            raise ProtocolError("bad_message", f"unknown type {kind!r}")

    def _serve_client(self):
        pending = self._handshake()
        for message in pending:
            self._handle(message)
        mixer = self.session.mixer
        window_start = time.perf_counter()
        window_count = 0
        timed_out = False
        while True:
            # Heartbeat: while the tool holds input, silence longer than
            # INPUT_TIMEOUT_MS (a hung tool) clears that input.  An idle tool
            # may stay connected without sending anything.
            timeout = INPUT_TIMEOUT_MS if mixer.has_input else INFINITE
            try:
                raw = self._read(timeout)
            except _Timeout:
                mixer.clear()
                timed_out = True
                self.server.session_event(self.session, "input_timeout")
                continue
            if timed_out:
                timed_out = False
                self.server.session_event(self.session, "")
            for message in decode_message(raw):
                self._handle(message)
            self.server.messages_received += 1
            window_count += 1
            if window_count >= FLOOD_WINDOW_MESSAGES:
                elapsed = time.perf_counter() - window_start
                if elapsed < FLOOD_WINDOW_SECONDS:
                    time.sleep(FLOOD_WINDOW_SECONDS - elapsed)
                window_start = time.perf_counter()
                window_count = 0

    def _run(self):
        api = _win32()
        try:
            while True:
                self._connect()
                state, code = STATE_DISCONNECTED, "tool_closed"
                try:
                    self._serve_client()
                except _ClientGone:
                    pass
                except _Dropped:
                    code = self.drop_code
                except ProtocolError as exc:
                    state, code = STATE_ERROR, exc.code
                    if self.session is not None:
                        self.session.mixer.clear()  # stop using its input first
                    try:
                        self.write({"type": "error", "code": exc.code, "detail": str(exc)})
                        # DisconnectNamedPipe discards unread data, so give the
                        # tool a moment to read why it was refused (or to hang up).
                        self._read(ERROR_LINGER_MS)
                    except (_ClientGone, _Timeout, _Dropped, ProtocolError, OSError, ValueError):
                        pass
                except OSError:
                    state, code = STATE_ERROR, "pipe_failed"
                finally:
                    session, self.session = self.session, None
                    if session is not None:
                        # Stop using the tool's input before anything else.
                        self.server.close_session(session, state, code)
                    api["ResetEvent"](self.drop_event)
                    api["DisconnectNamedPipe"](self.pipe)
        except _Stopping:
            pass
        except Exception:
            self.server.instance_failed()
        finally:
            session, self.session = self.session, None
            if session is not None:
                self.server.close_session(session, STATE_DISCONNECTED, "tool_closed")


class ExtensionPipeServer:
    """``instances`` message-mode pipe instances sharing one name.

    The owner supplies ``open_session(pid, image, hello)`` (returns an
    ``ExtensionSession`` or raises ``ProtocolError``), ``close_session``,
    ``session_event`` and ``instance_failed``.
    """

    def __init__(
        self, owner, hub, instances=1, pipe_name=PIPE_NAME, server_version="",
        controller_info=None,
    ):
        self.owner = owner
        self.hub = hub
        self.instance_count = max(1, min(MAX_TOOLS, int(instances)))
        self.pipe_name = pipe_name
        self.server_version = server_version
        self.controller_info = controller_info or (lambda: {})
        self.stop_event = None
        self.instances = []
        self.messages_received = 0

    # owner callbacks, called from instance threads
    def open_session(self, pid, image, hello):
        return self.owner.open_session(pid, image, hello)

    def close_session(self, session, state, code):
        self.owner.close_session(session, state, code)

    def session_event(self, session, code):
        self.owner.session_event(session, code)

    def instance_failed(self):
        self.owner.instance_failed()

    @property
    def running(self):
        return any(instance.thread.is_alive() for instance in self.instances)

    def _create_pipe(self, first, attributes):
        api = _win32()
        flags = PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED
        if first:
            # Nobody else may own this name while S2P listens on it.
            flags |= FILE_FLAG_FIRST_PIPE_INSTANCE
        handle = api["CreateNamedPipeW"](
            self.pipe_name,
            flags,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS,
            self.instance_count,
            MAX_MESSAGE_BYTES,
            MAX_MESSAGE_BYTES,
            0,
            ctypes.byref(attributes),
        )
        if handle in (None, _INVALID_HANDLE):
            error = ctypes.get_last_error()
            if error in (ERROR_ACCESS_DENIED, ERROR_PIPE_BUSY):
                raise ProtocolError("pipe_in_use", self.pipe_name)
            raise ctypes.WinError(error)
        return handle

    def start(self):
        """Create every instance synchronously so a name clash shows at once."""
        if self.running:
            return
        api = _win32()
        sddl = (
            "D:P(D;;GA;;;NU)"  # deny network logons
            f"(A;;GA;;;{_current_user_sid()})"  # only the interactive user
        )
        descriptor = ctypes.c_void_p()
        if not api["ConvertStringSecurityDescriptorToSecurityDescriptorW"](
            sddl, 1, ctypes.byref(descriptor), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        self.stop_event = api["CreateEventW"](None, True, False, None)
        instances = []
        try:
            attributes = api["SECURITY_ATTRIBUTES"]()
            attributes.nLength = ctypes.sizeof(attributes)
            attributes.lpSecurityDescriptor = descriptor
            attributes.bInheritHandle = False
            for index in range(self.instance_count):
                handle = self._create_pipe(index == 0, attributes)
                try:
                    instances.append(_PipeInstance(self, handle))
                except OSError:
                    api["CloseHandle"](handle)
                    raise
        except Exception:
            for instance in instances:
                instance.close_handles()
            api["CloseHandle"](self.stop_event)
            self.stop_event = None
            raise
        finally:
            api["LocalFree"](descriptor)
        self.instances = instances
        for instance in instances:
            instance.thread.start()

    def drop(self, tool_id, code="user_disconnected"):
        """End one tool's session; its instance keeps listening."""
        for instance in self.instances:
            session = instance.session
            if session is not None and session.tool_id == tool_id:
                instance.drop_code = code
                _win32()["SetEvent"](instance.drop_event)

    def stop(self, timeout=1.0):
        api = _win32()
        if self.stop_event:
            api["SetEvent"](self.stop_event)
        stopped = True
        deadline = time.monotonic() + timeout
        for instance in self.instances:
            instance.thread.join(max(0.0, deadline - time.monotonic()))
            if instance.thread.is_alive():
                stopped = False
        if not stopped:
            # Never close handles under a live overlapped operation.
            return False
        for instance in self.instances:
            instance.close_handles()
        self.instances = []
        if self.stop_event:
            api["CloseHandle"](self.stop_event)
            self.stop_event = None
        return True


# ---------------------------------------------------------------------------
# Console messages
# ---------------------------------------------------------------------------

_CONSOLE_REASONS = {
    "tool_closed": ("工具已關閉", "the tool closed"),
    "user_disconnected": ("已手動中斷", "disconnected manually"),
    "unauthorized": ("未授權的程式", "unauthorized program"),
    "unsupported_protocol": ("通訊協定版本不相容", "incompatible protocol version"),
    "handshake_timeout": ("未在時限內完成連線", "did not finish connecting in time"),
    "bad_message": ("送出無效資料", "sent invalid data"),
    "message_too_large": ("送出的資料過大", "sent an oversized message"),
    "permission_denied": ("要求的權限未開啟", "requested permission is not allowed"),
    "conflict": ("與其他工具搶同一支搖桿", "another tool controls the same stick"),
    "tool_not_found": ("找不到工具 EXE", "tool EXE not found"),
    "launch_failed": ("無法啟動工具", "could not launch the tool"),
    "pipe_failed": ("通訊管道發生錯誤", "pipe error"),
    "input_timeout": ("輸入逾時，已清除工具輸入", "input timed out and was cleared"),
}
_CONSOLE_PERMISSIONS = {"input": ("輸入", "input"), "read": ("讀取", "read")}


def describe_tool_changes(previous, current, settings=None):
    """Console lines for tools whose state changed, as ``(zh, en)`` pairs.

    ``previous``/``current`` are ``status()["tools"]`` dictionaries.  Only
    real changes are reported, so the connector prints one line when a tool
    connects, disconnects or fails, not one per status heartbeat.
    """
    lines = []
    for tool_id, entry in current.items():
        state, code = entry.get("state"), str(entry.get("code") or "")
        before = previous.get(tool_id) or {}
        if (before.get("state"), str(before.get("code") or "")) == (state, code):
            continue
        name = str(entry.get("name") or "").strip()
        if not name and settings is not None:
            tool = settings.tool(tool_id)
            name = Path(tool.path).stem if tool is not None else ""
        name = name or tool_id
        reason_zh, reason_en = _CONSOLE_REASONS.get(code, (code, code))
        if state == STATE_CONNECTED:
            if code == "input_timeout":
                lines.append((f"擴充工具「{name}」{reason_zh}。", f"Extension \"{name}\": {reason_en}."))
                continue
            if before.get("state") == STATE_CONNECTED:
                continue  # recovered from an input timeout: nothing to announce
            capabilities = entry.get("capabilities") or ()
            zh = "、".join(_CONSOLE_PERMISSIONS.get(item, (item, item))[0] for item in capabilities)
            en = ", ".join(_CONSOLE_PERMISSIONS.get(item, (item, item))[1] for item in capabilities)
            sticks = entry.get("sticks") or ()
            if sticks:
                side = {"left": ("左", "left"), "right": ("右", "right")}
                zh += "；控制" + "、".join(side[item][0] + "搖桿" for item in sticks)
                en += "; controls the " + " and ".join(side[item][1] for item in sticks) + " stick"
            lines.append((
                f"擴充工具已連線：{name}（權限：{zh or '無'}）",
                f"Extension connected: {name} (permissions: {en or 'none'})",
            ))
        elif state == STATE_DISCONNECTED:
            lines.append((
                f"擴充工具已中斷：{name}（{reason_zh}）" if code else f"擴充工具已中斷：{name}",
                f"Extension disconnected: {name} ({reason_en})" if code else f"Extension disconnected: {name}",
            ))
        elif state == STATE_ERROR:
            lines.append((
                f"擴充工具連線失敗：{name}（{reason_zh}）" if code else f"擴充工具連線失敗：{name}",
                f"Extension connection failed: {name} ({reason_en})" if code
                else f"Extension connection failed: {name}",
            ))
        elif state == STATE_WAITING and code == "settings_changed":
            lines.append((
                f"擴充工具「{name}」的設定已變更，等待重新連線。",
                f"Extension \"{name}\" settings changed; waiting for it to reconnect.",
            ))
    return lines


# ---------------------------------------------------------------------------
# Connector-side manager
# ---------------------------------------------------------------------------

class ExtensionManager:
    """Own the optional server, the merge hub, tool sessions and launching.

    Nothing is created while Extensions is disabled; the controller's
    per-report cost is then a single ``is None`` check.
    """

    def __init__(
        self, xinput, *, pipe_name=PIPE_NAME, server_version="", server_factory=None,
        controller_info=None, host_pid=0,
    ):
        self._xinput = xinput
        self.host_pid = int(host_pid or 0)
        self._pipe_name = pipe_name
        self._server_version = server_version
        self._server_factory = server_factory or ExtensionPipeServer
        self._controller_info = controller_info
        self._lock = threading.Lock()
        self._settings = ExtensionSettings()
        self._server = None
        self._hub = None
        self._paused = set()
        self._sessions = {}       # tool_id -> ExtensionSession
        self._stick_owner = {}    # "left"/"right" -> tool_id
        self._tool_status = {}    # tool_id -> dict
        self._state = STATE_DISABLED
        self._code = ""
        self._launch_tokens = {}  # token -> tool_id
        self._processes = {}      # tool_id -> Popen

    # status ----------------------------------------------------------------

    def _set_tool(self, tool_id, state, code="", **extra):
        entry = {"state": state, "code": code, "updated_at": time.time(), **extra}
        with self._lock:
            previous = self._tool_status.get(tool_id, {})
            if "name" not in entry and previous.get("name"):
                entry["name"] = previous["name"]
            self._tool_status[tool_id] = entry

    def status(self):
        with self._lock:
            tools = {key: dict(value) for key, value in self._tool_status.items()}
            state, code = self._state, self._code
        for tool in self._settings.active_tools:
            tools.setdefault(tool.tool_id, {"state": STATE_WAITING, "code": ""})
        if state == STATE_WAITING and any(
            entry.get("state") == STATE_CONNECTED for entry in tools.values()
        ):
            state = STATE_CONNECTED  # the server listens and a tool is in
        return {
            "state": state,
            "code": code,
            "enabled": self._settings.enabled,
            "protocol": PROTOCOL_VERSION,
            "max_tools": MAX_TOOLS,
            "tools": tools,
        }

    # sessions (called from IPC threads) --------------------------------------

    def _identify(self, image_path, hello):
        token = hello.get("token")
        active = self._settings.active_tools
        if isinstance(token, str) and token in self._launch_tokens:
            tool_id = self._launch_tokens[token]
            for tool in active:
                if tool.tool_id == tool_id:
                    return tool
            return None
        for tool in active:
            if same_path(image_path, tool.path):
                return tool
        return None

    def open_session(self, pid, image_path, hello):
        tool = self._identify(image_path, hello)
        if tool is None:
            with self._lock:
                self._code = "unauthorized"
            raise ProtocolError("unauthorized")
        name = str(hello.get("name") or "").strip()[:TOOL_NAME_LIMIT] or Path(tool.path).stem

        def refuse(code, detail="", **extra):
            self._set_tool(tool.tool_id, STATE_ERROR, code, name=name, **extra)
            raise ProtocolError(code, detail)

        requested = hello.get("capabilities", [CAPABILITY_INPUT])
        sticks = hello.get("sticks", [])
        if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
            refuse("bad_message", "capabilities must be a list")
        if not isinstance(sticks, list) or any(item not in STICKS for item in sticks):
            refuse("bad_message", "sticks must list left/right")
        allowed = {CAPABILITY_INPUT: tool.allow_input, CAPABILITY_READ: tool.allow_read}
        granted = tuple(item for item in KNOWN_CAPABILITIES if item in requested and allowed[item])
        if not granted:
            refuse("permission_denied")
        sticks = tuple(side for side in STICKS if side in sticks) if CAPABILITY_INPUT in granted else ()
        with self._lock:
            if tool.tool_id in self._paused:
                paused = True
            else:
                paused = False
                if tool.tool_id in self._sessions:
                    already = True
                else:
                    already = False
                    conflict = next(
                        (
                            (side, self._stick_owner[side]) for side in sticks
                            if side in self._stick_owner
                        ),
                        None,
                    )
                    if conflict is None:
                        session = ExtensionSession(tool.tool_id, name, granted, sticks)
                        self._sessions[tool.tool_id] = session
                        for side in sticks:
                            self._stick_owner[side] = tool.tool_id
        if paused:
            # Keep the "disconnected by user" status; refuse quietly.
            raise ProtocolError("user_disconnected")
        if already:
            # A second copy must not take over the first copy's session.
            raise ProtocolError("already_connected")
        if conflict is not None:
            side, owner_id = conflict
            owner = self._sessions.get(owner_id)
            refuse(
                "conflict", f"{side} stick is used by another tool",
                stick=side, with_tool=owner.name if owner else owner_id,
            )
        hub = self._hub
        if hub is not None:
            hub.add(session.mixer, reader=CAPABILITY_READ in granted)
        self._set_tool(
            tool.tool_id, STATE_CONNECTED, name=name,
            capabilities=list(granted), sticks=list(sticks),
        )
        with self._lock:
            self._code = ""
        return session

    def close_session(self, session, state, code):
        hub = self._hub
        if hub is not None:
            hub.remove(session.mixer, reader=CAPABILITY_READ in session.granted)
        session.mixer.clear()
        with self._lock:
            if self._sessions.get(session.tool_id) is session:
                del self._sessions[session.tool_id]
            for side in STICKS:
                if self._stick_owner.get(side) == session.tool_id:
                    del self._stick_owner[side]
            paused = session.tool_id in self._paused
        if paused:
            state, code = STATE_DISCONNECTED, "user_disconnected"
        elif code == "settings_changed":
            # The tool reconnects by itself and gets the new permissions.
            state = STATE_WAITING
        self._set_tool(session.tool_id, state, code, name=session.name)

    def session_event(self, session, code):
        self._set_tool(
            session.tool_id, STATE_CONNECTED, code, name=session.name,
            capabilities=list(session.granted), sticks=list(session.sticks),
        )

    def instance_failed(self):
        with self._lock:
            self._state, self._code = STATE_ERROR, "pipe_failed"

    # lifecycle ----------------------------------------------------------------

    def apply(self, settings: ExtensionSettings, *, startup=False):
        previous = self._settings
        self._settings = settings
        if not settings.enabled:
            self._paused.clear()
            self._stop_server()
            with self._lock:
                self._tool_status.clear()
                self._state, self._code = STATE_DISABLED, ""
            return
        active = settings.active_tools
        active_ids = {tool.tool_id for tool in active}
        self._paused &= active_ids
        with self._lock:
            for tool_id in list(self._tool_status):
                if tool_id not in active_ids:
                    del self._tool_status[tool_id]
        if not active:
            self._stop_server()
            with self._lock:
                self._state, self._code = STATE_WAITING, "no_tools"
            return
        server = self._server
        if server is not None and server.instance_count != len(active):
            self._stop_server()
        elif server is not None:
            before = {tool.tool_id: tool for tool in previous.active_tools}
            for tool in active:
                if before.get(tool.tool_id) != tool:
                    server.drop(tool.tool_id, "settings_changed")  # path or permissions
            for tool_id in before.keys() - active_ids:
                server.drop(tool_id, "settings_changed")
        self._start_server(len(active))
        previously_enabled = {tool.tool_id for tool in previous.active_tools} if previous.enabled else set()
        for tool in active:
            # Enabled tools follow S2P: they start with the connection (or when
            # enabled) in the background; "Open" is what shows their window.
            if startup or tool.tool_id not in previously_enabled:
                self.launch_tool(tool.tool_id, background=True)

    def connect(self, tool_id=None):
        if not self._settings.enabled:
            return False
        targets = [
            tool for tool in self._settings.active_tools
            if tool_id is None or tool.tool_id == tool_id
        ]
        for tool in targets:
            self._paused.discard(tool.tool_id)
            self._set_tool(tool.tool_id, STATE_WAITING)
        if not targets or not self._start_server(len(self._settings.active_tools)):
            return False
        for tool in targets:
            if tool_id is not None:
                # "Open" on one tool: always ask it to come forward, even when it
                # is connected but hidden in the notification area.
                self.launch_tool(tool.tool_id, reopen=True)
            elif tool.tool_id not in self._sessions:
                self.launch_tool(tool.tool_id)
        return True

    def disconnect(self, tool_id=None):
        if not self._settings.enabled:
            return
        for tool in self._settings.active_tools:
            if tool_id is not None and tool.tool_id != tool_id:
                continue
            self._paused.add(tool.tool_id)
            if self._server is not None:
                self._server.drop(tool.tool_id)
            self._set_tool(tool.tool_id, STATE_DISCONNECTED, "user_disconnected")

    def close(self):
        self._stop_server()

    def _start_server(self, instances):
        if self._server is not None and self._server.running:
            return True
        self._stop_server()
        hub = ExtensionInputHub()
        server = self._server_factory(
            self,
            hub,
            instances=instances,
            pipe_name=self._pipe_name,
            server_version=self._server_version,
            controller_info=self._controller_info,
        )
        self._hub = hub
        try:
            server.start()
        except ProtocolError as exc:
            self._hub = None
            with self._lock:
                self._state, self._code = STATE_ERROR, exc.code
            return False
        except OSError:
            self._hub = None
            with self._lock:
                self._state, self._code = STATE_ERROR, "pipe_failed"
            return False
        self._server = server
        with self._lock:
            self._state, self._code = STATE_WAITING, ""
        self._xinput.set_extension_input(hub)
        return True

    def _stop_server(self):
        server, self._server = self._server, None
        hub, self._hub = self._hub, None
        # Detach first: the next physical report is built without any tool.
        self._xinput.set_extension_input(None)
        if hub is not None:
            for mixer in hub.mixers:
                mixer.clear()
        if server is not None:
            server.stop()
        with self._lock:
            self._sessions.clear()
            self._stick_owner.clear()

    def launch_tool(self, tool_id, reopen=False, background=False):
        """Start a tool.  ``reopen`` (the Open button) starts it even when it is
        already running: a single-instance tool then brings its existing window
        back (for example from the notification area) instead of doing nothing.
        """
        tool = self._settings.tool(tool_id)
        path = Path(tool.path) if tool is not None and tool.path else None
        if path is None or not path.is_file():
            self._set_tool(tool_id, STATE_ERROR, "tool_not_found")
            return False
        if not reopen:
            process = self._processes.get(tool_id)
            if process is not None and process.poll() is None:
                return True
            if path.suffix.lower() == ".exe" and _image_is_running(path):
                # A manually started copy will connect by itself.
                return True
        token = secrets.token_hex(16)
        environment = dict(os.environ)
        environment[TOKEN_ENV] = token
        environment[PIPE_ENV] = self._pipe_name
        if background:
            environment[BACKGROUND_ENV] = "1"
        else:
            environment.pop(BACKGROUND_ENV, None)
        if path.suffix.lower() in (".py", ".pyw"):
            # Development clients run on the connector's own interpreter and
            # are identified by the one-time launch token.
            command = [sys.executable, str(path)]
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        else:
            command = [str(path)]
            flags = 0
        self._launch_tokens[token] = tool_id
        try:
            self._processes[tool_id] = subprocess.Popen(
                command, cwd=str(path.parent), env=environment,
                creationflags=flags, close_fds=True,
            )
        except OSError:
            self._launch_tokens.pop(token, None)
            self._set_tool(tool_id, STATE_ERROR, "launch_failed")
            return False
        return True


def _image_is_running(path) -> bool:
    """Return True when a process with this executable image already exists."""
    if sys.platform != "win32":
        return False
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    enum = psapi.EnumProcesses
    enum.restype = wintypes.BOOL
    enum.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    capacity = 4096
    pids = (wintypes.DWORD * capacity)()
    needed = wintypes.DWORD()
    if not enum(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return False
    count = needed.value // ctypes.sizeof(wintypes.DWORD)
    for index in range(count):
        pid = pids[index]
        if pid and same_path(process_image_path(pid), path):
            return True
    return False
