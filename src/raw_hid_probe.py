"""Race-safe client for the native high-rate Raw HID measurement helper."""

from __future__ import annotations

import ctypes
import json
import math
import mmap
import struct
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from ctypes import wintypes


PROBE_EXECUTABLE = Path(__file__).with_name("raw_hid_probe.exe")
STREAM_PROBE_EXECUTABLE = Path(__file__).with_name("raw_hid_stream_probe.exe")
STREAM_MAGIC = 0x53524853
STREAM_VERSION = 1
STREAM_CAPACITY = 65536
STREAM_HEADER = struct.Struct("<IIIIIIIIQQQQ")
STREAM_SLOT = struct.Struct("<QQffffffII")
STREAM_LEFT_STICK = 0x03
STREAM_RIGHT_STICK = 0x0C
TERMINAL_STATES = frozenset(("complete", "stopped", "error"))
DEFAULT_SNAPSHOT = {
    "state": "idle",
    "error_code": 0,
    "elapsed_ms": 0.0,
    "remaining_ms": 0.0,
    "reports": 0,
    "intervals": 0,
    "rate_hz": 0.0,
    "p50_us": 0.0,
    "p95_us": 0.0,
    "p99_us": 0.0,
    "min_us": 0.0,
    "mean_us": 0.0,
    "max_us": 0.0,
    "histogram_max_us": 0,
    "histogram_counts": (),
    "effective_rate_hz": 0.0,
    "effective_ratio": 0.0,
    "activity_sufficient": False,
    "axes_available": False,
    "active_stick": "",
    "movement_span": 0.0,
    "movement_turns": 0.0,
    "movement_path_rate": 0.0,
    "dominant_run_length": 0,
    "dominant_run_share": 0.0,
    "regular_repeat": False,
    "stick_reports": 0,
    "ignored_reports": 0,
}


@dataclass(frozen=True)
class RawHidDevice:
    key: str
    path: str
    name: str
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int
    interface_number: int
    is_virtual: bool = False


def _decode_hid_path(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return str(value or "")


def _hid_parent_device_ids(device_path):
    """Return the Config Manager parent chain for one HID interface path."""
    path = _decode_hid_path(device_path)
    parts = path[4:].split("#") if path.startswith("\\\\?\\") else ()
    if len(parts) < 3 or sys.platform != "win32":
        return ()
    instance_id = "\\".join(parts[:3])
    try:
        cfgmgr32 = ctypes.WinDLL("cfgmgr32")
        cfgmgr32.CM_Locate_DevNodeW.argtypes = (
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.ULONG,
        )
        cfgmgr32.CM_Locate_DevNodeW.restype = wintypes.ULONG
        cfgmgr32.CM_Get_Parent.argtypes = (
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
            wintypes.ULONG,
        )
        cfgmgr32.CM_Get_Parent.restype = wintypes.ULONG
        cfgmgr32.CM_Get_Device_IDW.argtypes = (
            wintypes.DWORD,
            wintypes.LPWSTR,
            wintypes.ULONG,
            wintypes.ULONG,
        )
        cfgmgr32.CM_Get_Device_IDW.restype = wintypes.ULONG
        current = wintypes.DWORD()
        if cfgmgr32.CM_Locate_DevNodeW(
            ctypes.byref(current), instance_id, 0
        ):
            return ()
        parents = []
        for _depth in range(16):
            parent = wintypes.DWORD()
            if cfgmgr32.CM_Get_Parent(
                ctypes.byref(parent), current, 0
            ):
                break
            buffer = ctypes.create_unicode_buffer(512)
            if cfgmgr32.CM_Get_Device_IDW(
                parent, buffer, len(buffer), 0
            ):
                break
            parent_id = buffer.value
            if not parent_id:
                break
            parents.append(parent_id)
            current = parent
        return tuple(parents)
    except (AttributeError, OSError, TypeError, ValueError):
        return ()


def _is_virtual_hid_path(device_path):
    """Identify root-enumerated virtual HID devices without relying on VID/PID."""
    return any(
        parent_id.upper().startswith(("ROOT\\SYSTEM\\", "ROOT\\VIGEM"))
        for parent_id in _hid_parent_device_ids(device_path)
    )


def enumerate_raw_hid_gamepads(hid_module=None):
    """Return user-readable HID gamepad collections, not XInput snapshots."""
    if hid_module is None:
        return _enumerate_raw_hid_gamepads_isolated()
    return _enumerate_raw_hid_gamepads_direct(hid_module)


def _enumerate_raw_hid_gamepads_direct(hid_module):
    """Enumerate in the current process.

    The tester normally uses the isolated wrapper above.  Keeping the HID DLL
    in a short-lived child process prevents a hot-plug race inside hidapi from
    corrupting the long-running Tk/Python process.
    """
    try:
        interfaces = hid_module.enumerate()
    except (OSError, RuntimeError):
        return []

    devices = []
    for item in interfaces:
        usage_page = int(item.get("usage_page", 0) or 0)
        usage = int(item.get("usage", 0) or 0)
        # Generic Desktop / Joystick or Game Pad. Some Windows HID stacks omit
        # collection usage, but usage==0 is also common for unrelated RGB and
        # lighting devices, so only retain clearly controller-like names.
        if usage_page not in (0, 0x01):
            continue
        if usage not in (0, 0x04, 0x05):
            continue
        path = _decode_hid_path(item.get("path"))
        if not path:
            continue
        product = str(item.get("product_string") or "").strip()
        manufacturer = str(item.get("manufacturer_string") or "").strip()
        if usage == 0 and not any(
            token in f"{manufacturer} {product}".casefold()
            for token in ("controller", "gamepad", "joystick", " joypad")
        ):
            continue
        vendor_id = int(item.get("vendor_id", 0) or 0)
        product_id = int(item.get("product_id", 0) or 0)
        raw_interface = item.get("interface_number")
        interface_number = (
            -1 if raw_interface is None else int(raw_interface)
        )
        name = product or (
            f"Raw HID {vendor_id:04X}:{product_id:04X}"
        )
        if manufacturer and manufacturer.casefold() not in name.casefold():
            name = f"{manufacturer} {name}"
        devices.append(RawHidDevice(
            key=path.casefold(),
            path=path,
            name=name,
            vendor_id=vendor_id,
            product_id=product_id,
            usage_page=usage_page,
            usage=usage,
            interface_number=interface_number,
            is_virtual=_is_virtual_hid_path(path),
        ))
    devices.sort(key=lambda device: (
        device.name.casefold(),
        device.vendor_id,
        device.product_id,
        device.interface_number,
        device.path.casefold(),
    ))
    return devices


def _raw_hid_device_payload(device):
    return {
        "key": device.key,
        "path": device.path,
        "name": device.name,
        "vendor_id": device.vendor_id,
        "product_id": device.product_id,
        "usage_page": device.usage_page,
        "usage": device.usage,
        "interface_number": device.interface_number,
        "is_virtual": device.is_virtual,
    }


def _enumerate_raw_hid_gamepads_isolated(timeout=5.0):
    """Run hidapi enumeration out of process so native crashes stay contained."""
    command = (sys.executable, str(Path(__file__).resolve()), "--enumerate-json")
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if sys.platform == "win32" else 0
    )
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=max(0.1, float(timeout)),
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return []
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
        if not isinstance(payload, list):
            return []
        return [
            RawHidDevice(
                key=str(item["key"]),
                path=str(item["path"]),
                name=str(item["name"]),
                vendor_id=int(item["vendor_id"]),
                product_id=int(item["product_id"]),
                usage_page=int(item["usage_page"]),
                usage=int(item["usage"]),
                interface_number=int(item["interface_number"]),
                is_virtual=bool(item.get("is_virtual", False)),
            )
            for item in payload
            if isinstance(item, dict)
        ]
    except (
        KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError
    ):
        return []


def _enumerate_json_main():
    try:
        import hid
    except ImportError:
        devices = []
    else:
        devices = _enumerate_raw_hid_gamepads_direct(hid)
    payload = [_raw_hid_device_payload(device) for device in devices]
    sys.stdout.buffer.write(
        json.dumps(payload, ensure_ascii=True).encode("utf-8")
    )
    sys.stdout.buffer.flush()


def normalize_probe_snapshot(payload):
    """Validate one helper message received from the native helper."""
    if not isinstance(payload, dict) or payload.get("type") != "snapshot":
        return None
    snapshot = dict(DEFAULT_SNAPSHOT)
    state = str(payload.get("state") or "error")
    snapshot["state"] = (
        state if state in {
            "idle", "opening", "running", *TERMINAL_STATES
        } else "error"
    )
    integer_fields = (
        "error_code", "reports", "intervals", "histogram_max_us"
    )
    float_fields = (
        "elapsed_ms", "remaining_ms", "rate_hz",
        "p50_us", "p95_us", "p99_us",
        "min_us", "mean_us", "max_us",
    )
    for key in integer_fields:
        try:
            snapshot[key] = max(0, int(payload.get(key, 0) or 0))
        except (TypeError, ValueError):
            snapshot[key] = 0
    for key in float_fields:
        try:
            snapshot[key] = max(0.0, float(payload.get(key, 0.0) or 0.0))
        except (TypeError, ValueError):
            snapshot[key] = 0.0
    counts = payload.get("histogram_counts") or ()
    try:
        snapshot["histogram_counts"] = tuple(
            max(0, int(value)) for value in counts[:80]
        )
    except (TypeError, ValueError):
        snapshot["histogram_counts"] = ()
    return snapshot


class RawHidProbeClient:
    """Own one helper generation and ignore output from every older process."""

    def __init__(
        self,
        executable=PROBE_EXECUTABLE,
        process_factory=subprocess.Popen,
    ):
        self.executable = Path(executable)
        self.process_factory = process_factory
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._generation = 0
        self._process = None
        self._reader_thread = None
        self._stderr_thread = None
        self._snapshot = dict(DEFAULT_SNAPSHOT)
        self._stderr_lines = []

    @property
    def available(self):
        return self.executable.is_file()

    def start(self, device_path, duration_seconds):
        duration_seconds = max(1.0, min(300.0, float(duration_seconds)))
        with self._lifecycle_lock:
            current = self._process
            if current is not None and current.poll() is None:
                return False
            self._generation += 1
            generation = self._generation
            with self._lock:
                self._snapshot = dict(DEFAULT_SNAPSHOT)
                self._snapshot.update({
                    "state": "opening",
                    "remaining_ms": duration_seconds * 1000.0,
                })
                self._stderr_lines = []
            try:
                process = self.process_factory(
                    [
                        str(self.executable),
                        "--measure",
                        str(device_path),
                        "--duration-ms",
                        str(round(duration_seconds * 1000.0)),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(
                        subprocess, "CREATE_NO_WINDOW", 0
                    ),
                )
            except (OSError, subprocess.SubprocessError):
                with self._lock:
                    self._snapshot["state"] = "error"
                    self._snapshot["error_code"] = 2
                return False
            self._process = process
            reader = threading.Thread(
                target=self._read_stdout,
                args=(process, generation),
                daemon=True,
                name="RawHidProbeReader",
            )
            stderr_reader = threading.Thread(
                target=self._read_stderr,
                args=(process, generation),
                daemon=True,
                name="RawHidProbeStderr",
            )
            self._reader_thread = reader
            self._stderr_thread = stderr_reader
            reader.start()
            stderr_reader.start()
            return True

    def _read_stdout(self, process, generation):
        try:
            for line in iter(process.stdout.readline, ""):
                try:
                    payload = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                snapshot = normalize_probe_snapshot(payload)
                if snapshot is None:
                    continue
                with self._lock:
                    if (
                        generation != self._generation
                        or process is not self._process
                    ):
                        return
                    self._snapshot = snapshot
        except (AttributeError, OSError, ValueError):
            pass
        finally:
            try:
                return_code = process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                return
            with self._lock:
                if (
                    generation == self._generation
                    and process is self._process
                    and self._snapshot.get("state") not in TERMINAL_STATES
                ):
                    self._snapshot["state"] = "error"
                    self._snapshot["error_code"] = (
                        int(return_code) if return_code else 1
                    )

    def _read_stderr(self, process, generation):
        try:
            for line in iter(process.stderr.readline, ""):
                with self._lock:
                    if (
                        generation != self._generation
                        or process is not self._process
                    ):
                        return
                    self._stderr_lines.append(line.rstrip())
                    del self._stderr_lines[:-20]
        except (AttributeError, OSError, ValueError):
            return

    def read_snapshot(self):
        with self._lock:
            snapshot = dict(self._snapshot)
            snapshot["histogram_counts"] = tuple(
                self._snapshot.get("histogram_counts") or ()
            )
            return snapshot

    def stop(self, timeout=1.0):
        with self._lifecycle_lock:
            process = self._process
            reader = self._reader_thread
            if process is None:
                return True
            if process.poll() is None:
                try:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                except (AttributeError, BrokenPipeError, OSError, ValueError):
                    pass
        if (
            reader is not None
            and reader is not threading.current_thread()
        ):
            reader.join(timeout=max(0.0, float(timeout)))
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=0.5)
                except (OSError, subprocess.TimeoutExpired):
                    return False
        with self._lifecycle_lock:
            if process is self._process:
                self._process = None
                self._reader_thread = None
                self._stderr_thread = None
                with self._lock:
                    if self._snapshot.get("state") not in TERMINAL_STATES:
                        self._snapshot["state"] = "stopped"
                        self._snapshot["remaining_ms"] = 0.0
        return True

    close = stop


if __name__ == "__main__" and "--enumerate-json" in sys.argv[1:]:
    _enumerate_json_main()


def _fixed_stream_probe_supported(device_path):
    """Return whether the companion knows this XInput-style report layout."""
    folded = str(device_path or "").upper()
    return (
        "VID_CAFE&PID_4020" in folded
        or "VID_413D&PID_2104" in folded
        or "VID_045E&PID_028E" in folded
        or "ROOT#VIGEM" in folded
    )


class RawHidStreamClient:
    """Batch reader for the helper's race-safe shared-memory report ring."""

    def __init__(
        self,
        executable=PROBE_EXECUTABLE,
        process_factory=subprocess.Popen,
        capacity=STREAM_CAPACITY,
        fixed_executable=STREAM_PROBE_EXECUTABLE,
    ):
        self.executable = Path(executable)
        self.fixed_executable = Path(fixed_executable)
        self.process_factory = process_factory
        self.capacity = max(1024, min(262144, int(capacity)))
        self._lock = threading.Lock()
        self._process = None
        self._mapping = None
        self._mapping_name = None

    @property
    def available(self):
        return self.executable.is_file() or self.fixed_executable.is_file()

    @property
    def active(self):
        with self._lock:
            return (
                self._process is not None
                and self._process.poll() is None
                and self._mapping is not None
            )

    def start(self, device_path):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return False
            mapping_name = (
                "Local\\S2PRawHidStream_" + uuid.uuid4().hex
            )
            mapping_size = (
                STREAM_HEADER.size + self.capacity * STREAM_SLOT.size
            )
            helper = self.executable
            if (
                self.fixed_executable.is_file()
                and _fixed_stream_probe_supported(device_path)
            ):
                helper = self.fixed_executable
            try:
                mapping = mmap.mmap(
                    -1, mapping_size, tagname=mapping_name,
                    access=mmap.ACCESS_WRITE,
                )
                process = self.process_factory(
                    [
                        str(helper),
                        "--stream",
                        str(device_path),
                        "--mapping",
                        mapping_name,
                        "--capacity",
                        str(self.capacity),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(
                        subprocess, "CREATE_NO_WINDOW", 0
                    ),
                )
            except (OSError, subprocess.SubprocessError, TypeError):
                try:
                    mapping.close()
                except (NameError, OSError):
                    pass
                return False
            self._mapping_name = mapping_name
            self._mapping = mapping
            self._process = process
            return True

    def status(self):
        with self._lock:
            mapping = self._mapping
            process = self._process
            if mapping is None:
                return {"state": "idle", "error_code": 0, "axes_mask": 0}
            try:
                header = STREAM_HEADER.unpack_from(mapping, 0)
            except (OSError, ValueError, struct.error):
                return {"state": "error", "error_code": 1, "axes_mask": 0}
            state_names = {0: "opening", 1: "running", 2: "stopped", 3: "error"}
            state = state_names.get(header[5], "error")
            return_code = process.poll() if process is not None else None
            if return_code is not None and state in {"opening", "running"}:
                state = "error"
                error_code = int(return_code or 1)
            else:
                error_code = int(header[6])
            return {
                "state": state,
                "error_code": error_code,
                "axes_mask": int(header[7]),
                "latest_sequence": int(header[8]),
                "raw_reports": int(header[11]),
                "stick_samples": int(header[9]),
                "ignored_reports": max(0, int(header[11]) - int(header[9])),
            }

    def read_latest(self, after_sequence):
        """Read only the newest stable slot and advance the stream cursor."""
        with self._lock:
            mapping = self._mapping
            previous = max(0, int(after_sequence or 0))
            if mapping is None:
                return None, previous, 0
            try:
                header = STREAM_HEADER.unpack_from(mapping, 0)
            except (OSError, ValueError, struct.error):
                return None, previous, 0
            if (
                header[0] != STREAM_MAGIC
                or header[1] != STREAM_VERSION
                or header[2] != STREAM_HEADER.size
                or header[3] != STREAM_SLOT.size
                or header[4] != self.capacity
            ):
                return None, previous, 0
            latest = int(header[8])
            frequency = int(header[10])
            if latest <= previous or frequency <= 0:
                return None, max(previous, latest), 0
            oldest = max(1, latest - self.capacity + 1)
            dropped = max(0, oldest - (previous + 1))
            offset = STREAM_HEADER.size + (
                (latest - 1) % self.capacity
            ) * STREAM_SLOT.size
            for _attempt in range(2):
                try:
                    before = struct.unpack_from("<Q", mapping, offset)[0]
                    if before != latest:
                        continue
                    values = STREAM_SLOT.unpack_from(mapping, offset)
                    after = struct.unpack_from("<Q", mapping, offset)[0]
                except (OSError, ValueError, struct.error):
                    continue
                if before == after == latest:
                    return (
                        (
                            values[1] / frequency,
                            (values[2], values[3]),
                            (values[4], values[5]),
                            latest,
                        ),
                        latest,
                        dropped,
                    )
            # Hidden tabs intentionally skip intermediate reports.  A torn
            # newest slot is only one unreadable sample, not every sequence
            # between the old cursor and latest.
            return None, latest, dropped + 1

    def read_samples(
        self, after_sequence, maximum=None, include_axes=False,
        include_controls=False, include_buttons=False,
    ):
        """Return stable slots, newest sequence, and overwritten sample count."""
        with self._lock:
            mapping = self._mapping
            if mapping is None:
                return (), int(after_sequence or 0), 0
            try:
                header = STREAM_HEADER.unpack_from(mapping, 0)
            except (OSError, ValueError, struct.error):
                return (), int(after_sequence or 0), 0
            if (
                header[0] != STREAM_MAGIC
                or header[1] != STREAM_VERSION
                or header[2] != STREAM_HEADER.size
                or header[3] != STREAM_SLOT.size
                or header[4] != self.capacity
            ):
                return (), int(after_sequence or 0), 0
            latest = int(header[8])
            frequency = int(header[10])
            previous = max(0, int(after_sequence or 0))
            if latest <= previous or frequency <= 0:
                return (), max(previous, latest), 0
            first = previous + 1
            dropped = 0
            oldest = max(1, latest - self.capacity + 1)
            if first < oldest:
                dropped = oldest - first
                first = oldest
            if maximum is not None:
                first = max(first, latest - max(1, int(maximum)) + 1)
            samples = []
            unstable_slots = 0
            for sequence in range(first, latest + 1):
                offset = STREAM_HEADER.size + (
                    (sequence - 1) % self.capacity
                ) * STREAM_SLOT.size
                try:
                    before = struct.unpack_from("<Q", mapping, offset)[0]
                    if before != sequence:
                        unstable_slots += 1
                        continue
                    values = STREAM_SLOT.unpack_from(mapping, offset)
                    after = struct.unpack_from("<Q", mapping, offset)[0]
                except (OSError, ValueError, struct.error):
                    unstable_slots += 1
                    continue
                if before != after or after != sequence:
                    unstable_slots += 1
                    continue
                sample = (
                    values[1] / frequency,
                    (values[2], values[3]),
                    (values[4], values[5]),
                    sequence,
                )
                if include_axes:
                    sample += (int(values[9]),)
                if include_controls:
                    sample += ((values[6], values[7]),)
                if include_buttons:
                    sample += (int(values[8]),)
                samples.append(sample)
            return tuple(samples), latest, dropped + unstable_slots

    def stop(self, timeout=1.0):
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.stdin.write(b"stop\n")
                process.stdin.flush()
            except (AttributeError, BrokenPipeError, OSError, ValueError):
                pass
            try:
                process.wait(timeout=max(0.0, float(timeout)))
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.terminate()
                    process.wait(timeout=0.5)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        process.kill()
                        process.wait(timeout=0.5)
                    except (OSError, subprocess.TimeoutExpired):
                        return False
        with self._lock:
            if process is self._process:
                mapping = self._mapping
                self._process = None
                self._mapping = None
                self._mapping_name = None
                if mapping is not None:
                    try:
                        mapping.close()
                    except OSError:
                        pass
        return True

    close = stop


class _StickUpdateTracker:
    """Collect exact axis-state changes and enough motion data to judge a test."""

    def __init__(self):
        self.samples = 0
        self.effective_updates = 0
        self.last = None
        self.current_run = 0
        self.run_lengths = Counter()
        self.min_x = math.inf
        self.max_x = -math.inf
        self.min_y = math.inf
        self.max_y = -math.inf
        self.path = 0.0
        self.turn_radians = 0.0
        self.last_angle = None
        self.last_radius = 0.0

    def add(self, point):
        x = float(point[0])
        y = float(point[1])
        current = (x, y)
        self.samples += 1
        self.min_x = min(self.min_x, x)
        self.max_x = max(self.max_x, x)
        self.min_y = min(self.min_y, y)
        self.max_y = max(self.max_y, y)

        radius = math.hypot(x, y)
        angle = math.atan2(y, x) if radius >= 0.20 else None
        if self.last is None:
            self.effective_updates = 1
            self.current_run = 1
        elif current == self.last:
            self.current_run += 1
        else:
            self.run_lengths[self.current_run] += 1
            self.current_run = 1
            self.effective_updates += 1
            dx = x - self.last[0]
            dy = y - self.last[1]
            self.path += math.hypot(dx, dy)

        if (
            angle is not None
            and self.last_angle is not None
            and self.last_radius >= 0.20
        ):
            delta = (angle - self.last_angle + math.pi) % (2.0 * math.pi) - math.pi
            # Large discontinuities are usually recentering, hot-plug noise, or
            # a parser transition rather than deliberate circular movement.
            if abs(delta) <= 1.0:
                self.turn_radians += abs(delta)
        if angle is not None:
            self.last_angle = angle
        else:
            self.last_angle = None
        self.last_radius = radius
        self.last = current

    def summary(self, duration_seconds):
        duration = max(0.001, float(duration_seconds))
        if self.samples <= 0:
            return {
                "samples": 0,
                "effective_updates": 0,
                "effective_rate_hz": 0.0,
                "span": 0.0,
                "turns": 0.0,
                "path_rate": 0.0,
                "dominant_run_length": 0,
                "dominant_run_share": 0.0,
            }
        runs = Counter(self.run_lengths)
        if self.current_run:
            runs[self.current_run] += 1
        total_runs = sum(runs.values())
        dominant_run_length = 0
        dominant_run_share = 0.0
        if total_runs:
            dominant_run_length, dominant_count = max(
                runs.items(), key=lambda item: (item[1], -item[0])
            )
            dominant_run_share = dominant_count / total_runs
        span_x = max(0.0, self.max_x - self.min_x)
        span_y = max(0.0, self.max_y - self.min_y)
        return {
            "samples": self.samples,
            "effective_updates": self.effective_updates,
            "effective_rate_hz": max(0, self.effective_updates - 1) / duration,
            "span": min(span_x, span_y),
            "turns": self.turn_radians / (2.0 * math.pi),
            "path_rate": self.path / duration,
            "dominant_run_length": int(dominant_run_length),
            "dominant_run_share": float(dominant_run_share),
        }


def _histogram_percentile(histogram, sample_count, percentile):
    if sample_count <= 0:
        return 0
    target = max(1, math.ceil(sample_count * float(percentile)))
    cumulative = 0
    for index, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return index
    return len(histogram) - 1


def _measurement_histogram_snapshot(histogram, interval_count):
    if interval_count <= 0:
        return 0, 0, 0, 50, (0,) * 80
    p50 = _histogram_percentile(histogram, interval_count, 0.50)
    p95 = _histogram_percentile(histogram, interval_count, 0.95)
    p99 = _histogram_percentile(histogram, interval_count, 0.99)
    maximum_us = min(
        len(histogram) - 1,
        max(50, ((p99 * 6 + 4) // 5 + 24) // 25 * 25),
    )
    counts = [0] * 80
    for interval_us, count in enumerate(histogram):
        if not count:
            continue
        index = min(79, interval_us * 80 // max(1, maximum_us))
        counts[index] += count
    return p50, p95, p99, maximum_us, tuple(counts)


class RawHidAnalysisClient:
    """Measure Raw HID timing and exact stick-state updates in one native stream.

    The native helper still timestamps every parsed stick report with QPC in its
    high-priority process. Python only drains the shared-memory ring and builds
    aggregate statistics, so UI scheduling does not become the timing source.
    """

    def __init__(
        self,
        executable=PROBE_EXECUTABLE,
        process_factory=subprocess.Popen,
    ):
        self.executable = Path(executable)
        self.process_factory = process_factory
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._stream = None
        self._fallback = None
        self._snapshot = dict(DEFAULT_SNAPSHOT)
        self._device_path = None
        self._duration_seconds = 0.0

    @property
    def available(self):
        return self.executable.is_file()

    def start(self, device_path, duration_seconds):
        duration_seconds = max(1.0, min(300.0, float(duration_seconds)))
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._device_path = str(device_path)
            self._duration_seconds = duration_seconds
            with self._lock:
                self._snapshot = dict(DEFAULT_SNAPSHOT)
                self._snapshot.update({
                    "state": "opening",
                    "remaining_ms": duration_seconds * 1000.0,
                })
            stream = RawHidStreamClient(
                executable=self.executable,
                process_factory=self.process_factory,
            )
            if not stream.start(self._device_path):
                with self._lock:
                    self._snapshot["state"] = "error"
                    self._snapshot["error_code"] = 2
                return False
            self._stream = stream
            self._fallback = None
            self._thread = threading.Thread(
                target=self._run,
                args=(stream, self._device_path, duration_seconds),
                daemon=True,
                name="RawHidAnalysisReader",
            )
            self._thread.start()
            return True

    def _run_timing_fallback(
        self, stream, device_path, duration_seconds, ignored_reports=0
    ):
        """Run the original finite timing probe when streaming cannot start."""
        stream.stop(timeout=0.5)
        fallback = RawHidProbeClient(
            executable=self.executable,
            process_factory=self.process_factory,
        )
        self._fallback = fallback
        if not fallback.start(device_path, duration_seconds):
            self._finish_error(2)
            return
        while not self._stop.is_set():
            snapshot = fallback.read_snapshot()
            snapshot.update({
                "axes_available": False,
                "effective_rate_hz": 0.0,
                "effective_ratio": 0.0,
                "activity_sufficient": False,
                "stick_reports": 0,
                "ignored_reports": max(0, int(ignored_reports or 0)),
            })
            with self._lock:
                self._snapshot = snapshot
            if snapshot.get("state") in TERMINAL_STATES:
                return
            time.sleep(0.05)
        fallback.stop(timeout=0.5)
        with self._lock:
            if self._snapshot.get("state") not in TERMINAL_STATES:
                self._snapshot["state"] = "stopped"
                self._snapshot["remaining_ms"] = 0.0

    def _run(self, stream, device_path, duration_seconds):
        opening_deadline = time.perf_counter() + 3.0
        while not self._stop.is_set():
            status = stream.status()
            state = status.get("state")
            if state == "running":
                break
            if state == "error" or time.perf_counter() >= opening_deadline:
                self._run_timing_fallback(
                    stream, device_path, duration_seconds,
                    ignored_reports=status.get("raw_reports", 0),
                )
                return
            time.sleep(0.01)

        if self._stop.is_set():
            stream.stop(timeout=0.2)
            with self._lock:
                self._snapshot["state"] = "stopped"
                self._snapshot["remaining_ms"] = 0.0
            return

        # Start the selected-duration clock once the stream is running. Slots
        # now represent every raw report; their per-slot axis mask independently
        # determines whether effective stick-state analysis is available.
        started_wall = time.perf_counter()
        warmup_deadline = started_wall + min(1.0, max(0.40, duration_seconds * 0.10))
        cursor = 0
        dropped_samples = 0
        warmup_samples = []
        last_status = stream.status()
        while not self._stop.is_set() and time.perf_counter() < warmup_deadline:
            last_status = stream.status()
            if last_status.get("state") == "error":
                self._run_timing_fallback(
                    stream, device_path, duration_seconds,
                    ignored_reports=last_status.get("raw_reports", 0),
                )
                return
            samples, cursor, dropped = stream.read_samples(
                cursor, include_axes=True
            )
            dropped_samples += dropped
            if samples:
                warmup_samples.extend(samples)
                break
            time.sleep(0.005)

        histogram = [0] * 100001
        interval_count = 0
        interval_sum_us = 0.0
        interval_min_us = math.inf
        interval_max_us = 0.0
        previous_timestamp = None
        first_timestamp = None
        last_timestamp = None
        trackers = {
            "left": _StickUpdateTracker(),
            "right": _StickUpdateTracker(),
        }
        next_publish = started_wall
        terminal_state = "complete"
        pending_samples = tuple(warmup_samples)

        while True:
            now = time.perf_counter()
            if self._stop.is_set():
                terminal_state = "stopped"
            elapsed_wall = max(0.0, now - started_wall)
            if terminal_state == "complete" and elapsed_wall >= duration_seconds:
                break
            if terminal_state == "stopped":
                break

            status = stream.status()
            if status.get("state") == "error":
                self._finish_error(int(status.get("error_code", 0) or 1))
                stream.stop(timeout=0.2)
                return
            if pending_samples:
                samples = pending_samples
                pending_samples = ()
                dropped = 0
            else:
                samples, cursor, dropped = stream.read_samples(
                    cursor, include_axes=True
                )
            dropped_samples += dropped
            for timestamp, left, right, _sequence, sample_axes in samples:
                if first_timestamp is None:
                    first_timestamp = timestamp
                last_timestamp = timestamp
                if previous_timestamp is not None:
                    interval_us = max(
                        0.0, (timestamp - previous_timestamp) * 1_000_000.0
                    )
                    interval_count += 1
                    interval_sum_us += interval_us
                    interval_min_us = min(interval_min_us, interval_us)
                    interval_max_us = max(interval_max_us, interval_us)
                    histogram[min(100000, int(interval_us))] += 1
                previous_timestamp = timestamp
                if sample_axes & STREAM_LEFT_STICK == STREAM_LEFT_STICK:
                    trackers["left"].add(left)
                if sample_axes & STREAM_RIGHT_STICK == STREAM_RIGHT_STICK:
                    trackers["right"].add(right)

            if now >= next_publish:
                self._publish_analysis_snapshot(
                    state="running",
                    started_wall=started_wall,
                    duration_seconds=duration_seconds,
                    first_timestamp=first_timestamp,
                    last_timestamp=last_timestamp,
                    histogram=histogram,
                    interval_count=interval_count,
                    interval_sum_us=interval_sum_us,
                    interval_min_us=interval_min_us,
                    interval_max_us=interval_max_us,
                    trackers=trackers,
                    stream_status=status,
                    dropped_samples=dropped_samples,
                )
                next_publish = now + 0.10
            if not samples:
                time.sleep(0.005)

        # Drain the final reports already committed before stopping the helper.
        samples, cursor, dropped = stream.read_samples(
            cursor, include_axes=True
        )
        dropped_samples += dropped
        for timestamp, left, right, _sequence, sample_axes in samples:
            if first_timestamp is None:
                first_timestamp = timestamp
            last_timestamp = timestamp
            if previous_timestamp is not None:
                interval_us = max(
                    0.0, (timestamp - previous_timestamp) * 1_000_000.0
                )
                interval_count += 1
                interval_sum_us += interval_us
                interval_min_us = min(interval_min_us, interval_us)
                interval_max_us = max(interval_max_us, interval_us)
                histogram[min(100000, int(interval_us))] += 1
            previous_timestamp = timestamp
            if sample_axes & STREAM_LEFT_STICK == STREAM_LEFT_STICK:
                trackers["left"].add(left)
            if sample_axes & STREAM_RIGHT_STICK == STREAM_RIGHT_STICK:
                trackers["right"].add(right)
        stream_status = stream.status()
        stream.stop(timeout=0.5)
        self._publish_analysis_snapshot(
            state=terminal_state,
            started_wall=started_wall,
            duration_seconds=duration_seconds,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
            histogram=histogram,
            interval_count=interval_count,
            interval_sum_us=interval_sum_us,
            interval_min_us=interval_min_us,
            interval_max_us=interval_max_us,
            trackers=trackers,
            stream_status=stream_status,
            dropped_samples=dropped_samples,
            finished=True,
        )

    def _publish_analysis_snapshot(
        self,
        *,
        state,
        started_wall,
        duration_seconds,
        first_timestamp,
        last_timestamp,
        histogram,
        interval_count,
        interval_sum_us,
        interval_min_us,
        interval_max_us,
        trackers,
        stream_status,
        dropped_samples,
        finished=False,
    ):
        now = time.perf_counter()
        elapsed_wall = min(duration_seconds, max(0.0, now - started_wall))
        sample_duration = (
            max(0.001, last_timestamp - first_timestamp)
            if first_timestamp is not None and last_timestamp is not None
            else max(0.001, elapsed_wall)
        )
        summaries = {
            name: tracker.summary(sample_duration)
            for name, tracker in trackers.items()
        }
        active_stick = max(
            summaries,
            key=lambda name: (
                summaries[name]["turns"] * 3.0
                + summaries[name]["span"] * 2.0
                + min(10.0, summaries[name]["path_rate"])
            ),
        )
        active = summaries[active_stick]
        reports = max(
            int(stream_status.get("raw_reports", 0) or 0),
            interval_count + (1 if first_timestamp is not None else 0),
        )
        rate_hz = max(0, reports - 1) / sample_duration
        effective_rate_hz = active["effective_rate_hz"]
        effective_ratio = (
            effective_rate_hz / rate_hz if rate_hz > 0 else 0.0
        )
        axes_available = bool(active["samples"] > 0)
        activity_sufficient = bool(
            axes_available
            and sample_duration >= 3.0
            and active["samples"] >= 200
            and active["span"] >= 0.70
            and active["turns"] >= 1.0
            and active["path_rate"] >= 0.80
        )
        dominant = active["dominant_run_length"]
        dominant_share = active["dominant_run_share"]
        expected_ratio = 1.0 / dominant if dominant >= 2 else 1.0
        regular_repeat = bool(
            activity_sufficient
            and dominant >= 2
            and dominant_share >= 0.55
            and abs(effective_ratio - expected_ratio)
                <= max(0.08, expected_ratio * 0.25)
        )
        p50, p95, p99, histogram_max, histogram_counts = (
            _measurement_histogram_snapshot(histogram, interval_count)
        )
        snapshot = dict(DEFAULT_SNAPSHOT)
        snapshot.update({
            "state": state,
            "error_code": 0,
            "elapsed_ms": elapsed_wall * 1000.0,
            "remaining_ms": (
                0.0 if finished or state in TERMINAL_STATES
                else max(0.0, duration_seconds - elapsed_wall) * 1000.0
            ),
            "reports": reports,
            "intervals": interval_count,
            "rate_hz": rate_hz,
            "p50_us": float(p50),
            "p95_us": float(p95),
            "p99_us": float(p99),
            "min_us": 0.0 if math.isinf(interval_min_us) else interval_min_us,
            "mean_us": (
                interval_sum_us / interval_count if interval_count else 0.0
            ),
            "max_us": interval_max_us,
            "histogram_max_us": histogram_max,
            "histogram_counts": histogram_counts,
            "effective_rate_hz": effective_rate_hz,
            "effective_ratio": effective_ratio,
            "activity_sufficient": activity_sufficient,
            "axes_available": axes_available,
            "active_stick": active_stick,
            "movement_span": active["span"],
            "movement_turns": active["turns"],
            "movement_path_rate": active["path_rate"],
            "dominant_run_length": dominant,
            "dominant_run_share": dominant_share,
            "regular_repeat": regular_repeat,
            "stick_reports": active["samples"],
            "ignored_reports": max(
                0,
                int(stream_status.get("ignored_reports", 0) or 0)
                    + int(dropped_samples),
            ),
        })
        with self._lock:
            self._snapshot = snapshot

    def _finish_error(self, error_code):
        with self._lock:
            self._snapshot["state"] = "error"
            self._snapshot["error_code"] = max(1, int(error_code or 1))
            self._snapshot["remaining_ms"] = 0.0

    def read_snapshot(self):
        fallback = self._fallback
        if fallback is not None:
            snapshot = fallback.read_snapshot()
            snapshot.update({
                "axes_available": False,
                "effective_rate_hz": 0.0,
                "effective_ratio": 0.0,
                "activity_sufficient": False,
            })
            return snapshot
        with self._lock:
            snapshot = dict(self._snapshot)
            snapshot["histogram_counts"] = tuple(
                self._snapshot.get("histogram_counts") or ()
            )
            return snapshot

    def stop(self, timeout=1.0):
        self._stop.set()
        fallback = self._fallback
        if fallback is not None:
            fallback.stop(timeout=timeout)
        stream = self._stream
        if stream is not None:
            stream.stop(timeout=min(0.5, max(0.0, float(timeout))))
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=max(0.0, float(timeout)))
        with self._lifecycle_lock:
            alive = bool(thread is not None and thread.is_alive())
            if not alive:
                self._thread = None
                self._stream = None
                self._fallback = None
        return not alive

    close = stop
