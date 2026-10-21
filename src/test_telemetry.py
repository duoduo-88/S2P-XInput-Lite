"""Low-overhead shared-memory telemetry for the non-modal gamepad tester."""

from __future__ import annotations

import json
import mmap
import struct
import time


TELEMETRY_TAG = r"Local\S2P-XInput-Lite-Gamepad-Test-v1"
TELEMETRY_SIZE = 64 * 1024
TELEMETRY_MAGIC = b"S2PTEST1"
TELEMETRY_VERSION = 2

_MAGIC_OFFSET = 0
_VERSION_OFFSET = 8
_HEARTBEAT_OFFSET = 16
_SEQUENCE_OFFSET = 24
_LENGTH_OFFSET = 32
_TRAIL_SEQUENCE_OFFSET = 40
_PAYLOAD_OFFSET = 64

# The live status remains JSON because it contains sparse UI/configuration data.
# High-rate stick coordinates use a compact binary ring at the end of the same
# mapping so a slow display frame cannot overwrite all intermediate reports.
_TRAIL_CAPACITY = 512
_TRAIL_SLOT = struct.Struct("<QQ10f")
_TRAIL_RING_SIZE = _TRAIL_CAPACITY * _TRAIL_SLOT.size
_TRAIL_RING_OFFSET = TELEMETRY_SIZE - _TRAIL_RING_SIZE
_MAX_PAYLOAD = _TRAIL_RING_OFFSET - _PAYLOAD_OFFSET


def _open_mapping(tagname=TELEMETRY_TAG, size=TELEMETRY_SIZE):
    try:
        return mmap.mmap(-1, size, tagname=tagname, access=mmap.ACCESS_WRITE)
    except TypeError:
        # Anonymous fallback keeps unit tests and non-Windows imports usable.
        # Cross-process telemetry is intentionally a Windows-only feature.
        return mmap.mmap(-1, size, access=mmap.ACCESS_WRITE)


class SharedTestTelemetry:
    """One writer and one reader exchange JSON using a sequence lock."""

    def __init__(self, mapping=None, clock_ns=time.monotonic_ns):
        self._mapping = mapping if mapping is not None else _open_mapping()
        self._clock_ns = clock_ns
        self._last_publish_ns = 0
        self._last_read_sequence = None
        self._last_read_value = None
        self._initialize_header()

    def _initialize_header(self):
        self._mapping.seek(_MAGIC_OFFSET)
        magic = self._mapping.read(len(TELEMETRY_MAGIC))
        version = struct.unpack_from(
            "<I", self._mapping, _VERSION_OFFSET
        )[0]
        if magic == TELEMETRY_MAGIC and version == TELEMETRY_VERSION:
            return
        self._mapping.seek(0)
        self._mapping.write(b"\x00" * TELEMETRY_SIZE)
        self._mapping.seek(_MAGIC_OFFSET)
        self._mapping.write(TELEMETRY_MAGIC)
        struct.pack_into(
            "<I", self._mapping, _VERSION_OFFSET, TELEMETRY_VERSION
        )

    def mark_reader_active(self, now_ns=None):
        now_ns = self._clock_ns() if now_ns is None else int(now_ns)
        struct.pack_into("<Q", self._mapping, _HEARTBEAT_OFFSET, now_ns)

    def reader_is_active(self, now_ns=None, timeout_seconds=2.0):
        now_ns = self._clock_ns() if now_ns is None else int(now_ns)
        heartbeat = struct.unpack_from(
            "<Q", self._mapping, _HEARTBEAT_OFFSET
        )[0]
        if heartbeat <= 0 or heartbeat > now_ns:
            return False
        return now_ns - heartbeat <= int(float(timeout_seconds) * 1e9)

    def publish_due(self, now_ns=None, maximum_rate_hz=360.0):
        """Return whether a subscriber is active and the frame interval is due."""
        now_ns = self._clock_ns() if now_ns is None else int(now_ns)
        if not self.reader_is_active(now_ns):
            return False
        minimum_interval = int(1e9 / max(1.0, float(maximum_rate_hz)))
        return now_ns - self._last_publish_ns >= minimum_interval

    def publish_if_requested(
        self, payload, now_ns=None, maximum_rate_hz=360.0
    ):
        now_ns = self._clock_ns() if now_ns is None else int(now_ns)
        if not self.publish_due(now_ns, maximum_rate_hz):
            return False
        self._last_publish_ns = now_ns
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > _MAX_PAYLOAD:
            raise ValueError("gamepad test telemetry payload is too large")

        sequence = struct.unpack_from(
            "<Q", self._mapping, _SEQUENCE_OFFSET
        )[0]
        if sequence & 1:
            sequence += 1
        writing_sequence = sequence + 1
        struct.pack_into(
            "<Q", self._mapping, _SEQUENCE_OFFSET, writing_sequence
        )
        struct.pack_into("<I", self._mapping, _LENGTH_OFFSET, len(encoded))
        self._mapping[_PAYLOAD_OFFSET:_PAYLOAD_OFFSET + len(encoded)] = encoded
        struct.pack_into(
            "<Q", self._mapping, _SEQUENCE_OFFSET, writing_sequence + 1
        )
        return True

    def write_trail_sample(
        self,
        timestamp_ns,
        physical_left,
        physical_right,
        gyro_xy,
        final_left,
        final_right,
    ):
        """Append one original processed report to the binary trail ring."""
        sequence = struct.unpack_from(
            "<Q", self._mapping, _TRAIL_SEQUENCE_OFFSET
        )[0] + 1
        slot_index = (sequence - 1) % _TRAIL_CAPACITY
        offset = _TRAIL_RING_OFFSET + slot_index * _TRAIL_SLOT.size
        values = (
            *physical_left,
            *physical_right,
            *gyro_xy,
            *final_left,
            *final_right,
        )
        if len(values) != 10:
            raise ValueError("trail sample requires ten axis values")
        # A wrapped slot still contains its previous committed sequence. Mark
        # it invalid before replacing the payload so a concurrent reader can
        # never accept partially overwritten coordinates as that old sample.
        struct.pack_into("<Q", self._mapping, offset, 0)
        struct.pack_into(
            "<Q10f",
            self._mapping,
            offset + 8,
            int(timestamp_ns),
            *(float(value) for value in values),
        )
        # Commit the slot sequence and global sequence only after its payload is
        # complete. Readers validate the slot sequence before accepting it.
        struct.pack_into("<Q", self._mapping, offset, sequence)
        struct.pack_into(
            "<Q", self._mapping, _TRAIL_SEQUENCE_OFFSET, sequence
        )
        return sequence

    def latest_trail_sequence(self):
        return struct.unpack_from(
            "<Q", self._mapping, _TRAIL_SEQUENCE_OFFSET
        )[0]

    def read_trail_samples(self, after_sequence=0):
        """Return unseen original reports, newest sequence and overwrite count."""
        self.mark_reader_active()
        newest = self.latest_trail_sequence()
        after_sequence = max(0, int(after_sequence or 0))
        if newest <= after_sequence:
            return [], newest, 0
        first_available = max(1, newest - _TRAIL_CAPACITY + 1)
        first_requested = after_sequence + 1
        dropped = max(0, first_available - first_requested)
        samples = []
        for sequence in range(max(first_requested, first_available), newest + 1):
            slot_index = (sequence - 1) % _TRAIL_CAPACITY
            offset = _TRAIL_RING_OFFSET + slot_index * _TRAIL_SLOT.size
            before = struct.unpack_from("<Q", self._mapping, offset)[0]
            if before != sequence:
                dropped += 1
                continue
            unpacked = _TRAIL_SLOT.unpack_from(self._mapping, offset)
            after = struct.unpack_from("<Q", self._mapping, offset)[0]
            if before != after or after != sequence:
                dropped += 1
                continue
            samples.append({
                "sequence": sequence,
                "timestamp_ns": unpacked[1],
                "physical_left": unpacked[2:4],
                "physical_right": unpacked[4:6],
                "gyro": unpacked[6:8],
                "final_left": unpacked[8:10],
                "final_right": unpacked[10:12],
            })
        return samples, newest, dropped

    def read_latest(self, attempts=3):
        self.mark_reader_active()
        for _ in range(max(1, int(attempts))):
            before = struct.unpack_from(
                "<Q", self._mapping, _SEQUENCE_OFFSET
            )[0]
            if before == 0 or before & 1:
                continue
            if before == self._last_read_sequence:
                return self._last_read_value
            length = struct.unpack_from(
                "<I", self._mapping, _LENGTH_OFFSET
            )[0]
            if length <= 0 or length > _MAX_PAYLOAD:
                return None
            encoded = bytes(
                self._mapping[
                    _PAYLOAD_OFFSET:_PAYLOAD_OFFSET + length
                ]
            )
            after = struct.unpack_from(
                "<Q", self._mapping, _SEQUENCE_OFFSET
            )[0]
            if before != after or after & 1:
                continue
            try:
                value = json.loads(encoded.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, TypeError):
                return None
            if not isinstance(value, dict):
                return None
            self._last_read_sequence = after
            self._last_read_value = value
            return value
        return None

    def close(self):
        try:
            self._mapping.close()
        except (BufferError, OSError, ValueError):
            pass
