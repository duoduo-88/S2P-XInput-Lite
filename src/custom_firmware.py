"""Validate a user-selected ESP32-S3 application image before flashing.

Only the application image is replaced. The bundled bootloader and partition
table are always written with it, so the image must fit the bundled factory
app partition and be built for the ESP32-S3.
"""

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

ESP_IMAGE_MAGIC = 0xE9
ESP32S3_CHIP_ID = 0x0009
APP_DESC_MAGIC = 0xABCD5432
APP_DESC_OFFSET = 32  # 24-byte image header + 8-byte first segment header
PARTITION_ENTRY_MAGIC = b"\xAA\x50"
APP_PARTITION_OFFSET = 0x10000
S2P_IDENTITY_MARKERS = (b"S2P-FW", b"s2p_bridge")


class CustomFirmwareError(ValueError):
    """The selected file must not be flashed."""

    def __init__(self, zh, en):
        super().__init__(zh)
        self.zh = zh
        self.en = en

    def message(self, language):
        return self.en if language == "en" else self.zh


@dataclass(frozen=True)
class CustomFirmwareInfo:
    path: Path
    size: int
    sha256: str
    version: str
    project_name: str
    s2p_identity: bool


def _c_string(raw):
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()


def app_partition_size(partition_table_path):
    """Return the size of the app partition the bundled table puts at 0x10000."""
    data = Path(partition_table_path).read_bytes()
    for offset in range(0, len(data) - 31, 32):
        entry = data[offset:offset + 32]
        if entry[:2] != PARTITION_ENTRY_MAGIC:
            break
        part_type = entry[2]
        part_offset, part_size = struct.unpack_from("<II", entry, 4)
        if part_type == 0x00 and part_offset == APP_PARTITION_OFFSET:
            return part_size
    raise CustomFirmwareError(
        "內建分割表中找不到 0x10000 的 app 分割區。",
        "The bundled partition table has no app partition at 0x10000.",
    )


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_custom_firmware(path, partition_table_path):
    """Return image details, or raise CustomFirmwareError if it is unsafe."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CustomFirmwareError(
            f"無法讀取韌體檔案：{exc}", f"Cannot read the firmware file: {exc}"
        ) from exc

    max_size = app_partition_size(partition_table_path)
    if len(data) < APP_DESC_OFFSET + 256:
        raise CustomFirmwareError(
            "檔案太小，不是完整的 ESP32 韌體映像檔。",
            "The file is too small to be a complete ESP32 firmware image.",
        )
    if len(data) > max_size:
        raise CustomFirmwareError(
            f"韌體大小 {len(data):,} bytes 超過 app 分割區上限 {max_size:,} bytes。",
            f"The firmware is {len(data):,} bytes, larger than the "
            f"{max_size:,}-byte app partition.",
        )
    if data[0] != ESP_IMAGE_MAGIC:
        raise CustomFirmwareError(
            "不是 ESP32 韌體映像檔（開頭不是 0xE9）。",
            "This is not an ESP32 firmware image (it does not start with 0xE9).",
        )
    chip_id = struct.unpack_from("<H", data, 12)[0]
    if chip_id != ESP32S3_CHIP_ID:
        raise CustomFirmwareError(
            f"這個韌體不是為 ESP32-S3 編譯的（chip id {chip_id}）。",
            f"This firmware was not built for the ESP32-S3 (chip id {chip_id}).",
        )

    if struct.unpack_from("<I", data, APP_DESC_OFFSET)[0] != APP_DESC_MAGIC:
        raise CustomFirmwareError(
            "不是 ESP32 應用程式韌體（缺少 app 描述區），可能選到了 bootloader 或分割表。",
            "This is not an ESP32 application image (no app descriptor); it may be "
            "a bootloader or partition table.",
        )
    version = _c_string(data[APP_DESC_OFFSET + 16:APP_DESC_OFFSET + 48])
    project_name = _c_string(data[APP_DESC_OFFSET + 48:APP_DESC_OFFSET + 80])

    return CustomFirmwareInfo(
        path=path,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        version=version,
        project_name=project_name,
        s2p_identity=all(marker in data for marker in S2P_IDENTITY_MARKERS),
    )
