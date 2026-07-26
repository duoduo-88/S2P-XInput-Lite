"""Race-safe client for the native high-rate Raw HID measurement helper."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path


PROBE_EXECUTABLE = Path(__file__).with_name("raw_hid_probe.exe")
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


def _decode_hid_path(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return str(value or "")


def enumerate_raw_hid_gamepads(hid_module=None):
    """Return user-readable HID gamepad collections, not XInput snapshots."""
    if hid_module is None:
        try:
            import hid as hid_module
        except ImportError:
            return []
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
        interface_number = int(item.get("interface_number", -1) or -1)
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
        ))
    devices.sort(key=lambda device: (
        device.name.casefold(),
        device.vendor_id,
        device.product_id,
        device.interface_number,
        device.path.casefold(),
    ))
    return devices


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
        return True

    close = stop
