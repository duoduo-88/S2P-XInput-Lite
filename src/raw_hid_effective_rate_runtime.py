"""Runtime glue for the embedded fixed-layout Raw HID helper."""

from __future__ import annotations

import hashlib
import os
import zlib
from pathlib import Path

import raw_hid_effective_rate as _feature
from raw_hid_stream_payload import SHA256, decode_helper


def _materialize_fixed_stream_probe(executable=_feature.STREAM_PROBE_EXECUTABLE):
    """Write only the exact reviewed helper bytes after SHA-256 verification."""
    executable = Path(executable)
    if executable.is_file():
        try:
            if hashlib.sha256(executable.read_bytes()).hexdigest() == SHA256:
                return executable
        except OSError:
            pass
    temporary = executable.with_name(executable.name + ".tmp")
    try:
        binary = decode_helper()
        if hashlib.sha256(binary).hexdigest() != SHA256:
            return executable
        temporary.write_bytes(binary)
        os.replace(temporary, executable)
    except (OSError, ValueError, zlib.error):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return executable


_feature._materialize_fixed_stream_probe = _materialize_fixed_stream_probe
_feature.HybridRawHidStreamClient.available = property(lambda self: True)

install_effective_rate_patch = _feature.install_effective_rate_patch
translate_effective_rate = _feature.translate_effective_rate
