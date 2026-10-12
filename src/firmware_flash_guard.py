"""Fail-closed ESP32-S3 flash capacity verification before writing firmware."""

from __future__ import annotations

import re
import subprocess


REQUIRED_FLASH_BYTES = 16 * 1024 * 1024
_FLASH_SIZE = re.compile(
    r"\b(?:Detected\s+)?flash\s+size:\s*(\d+)\s*(MB|KB)\b",
    flags=re.IGNORECASE,
)


class FlashProbeError(RuntimeError):
    """The board's chip or actual flash capacity could not be verified."""


def parse_detected_flash_size(output):
    """Return physical flash capacity in bytes, never a configured image size."""
    match = _FLASH_SIZE.search(str(output))
    if match is None:
        raise FlashProbeError("esptool did not report a detected flash size.")
    number, unit = match.groups()
    return int(number) * (1024 * 1024 if unit.upper() == "MB" else 1024)


def probe_esp32_flash_size(esptool_path, port, *, runner=subprocess.run):
    """Query the bootloader chip/flash; do not write anything to the board."""
    command = [
        str(esptool_path),
        "--chip", "esp32s3",
        "--port", str(port),
        "--baud", "921600",
        # Keep the board in ROM download mode so the verified port remains
        # associated with this chip until write_flash starts.
        "--after", "no_reset",
        "flash_id",
    ]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FlashProbeError(f"esptool flash_id failed: {exc}") from exc
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if completed.returncode != 0:
        raise FlashProbeError(
            f"esptool flash_id exited with {completed.returncode}: {output[-600:]}"
        )
    return parse_detected_flash_size(output)
