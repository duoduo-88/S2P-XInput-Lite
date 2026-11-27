import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import config_gui
import custom_firmware
from custom_firmware import CustomFirmwareError, inspect_custom_firmware

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PARTITIONS = REPO_ROOT / "esp32s3" / "firmware" / "partition-table.bin"


def make_image(*, magic=0xE9, chip_id=0x0009, size=4096, identity=True,
               version=b"1.0.9", project=b"esp32s3_bluedroid_bridge",
               app_desc=True):
    data = bytearray(size)
    data[0] = magic
    struct.pack_into("<H", data, 12, chip_id)
    if app_desc:
        struct.pack_into("<I", data, 32, custom_firmware.APP_DESC_MAGIC)
    data[48:48 + len(version)] = version
    data[80:80 + len(project)] = project
    if identity:
        marker = b"S2P-FW\0s2p_bridge\0"
        data[1024:1024 + len(marker)] = marker
    return bytes(data)


class CustomFirmwareInspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, data, name="app.bin"):
        path = Path(self.tmp.name) / name
        path.write_bytes(data)
        return path

    def test_bundled_partition_table_has_one_megabyte_app_slot(self):
        self.assertEqual(
            custom_firmware.app_partition_size(BUNDLED_PARTITIONS), 0x100000
        )

    def test_valid_s2p_image_is_accepted_with_details(self):
        path = self.write(make_image())
        info = inspect_custom_firmware(path, BUNDLED_PARTITIONS)
        self.assertEqual(info.version, "1.0.9")
        self.assertEqual(info.project_name, "esp32s3_bluedroid_bridge")
        self.assertTrue(info.s2p_identity)
        self.assertEqual(info.size, 4096)
        self.assertEqual(info.sha256, custom_firmware.file_sha256(path))

    def test_bundled_firmware_passes_inspection(self):
        bundled = REPO_ROOT / "esp32s3" / "firmware" / "esp32s3_bluedroid_bridge.bin"
        if not bundled.exists():
            self.skipTest("bundled firmware not present")
        info = inspect_custom_firmware(bundled, BUNDLED_PARTITIONS)
        self.assertTrue(info.s2p_identity)

    def test_bundled_bootloader_is_rejected(self):
        bootloader = REPO_ROOT / "esp32s3" / "firmware" / "bootloader.bin"
        if not bootloader.exists():
            self.skipTest("bundled bootloader not present")
        with self.assertRaises(CustomFirmwareError):
            inspect_custom_firmware(bootloader, BUNDLED_PARTITIONS)

    def test_image_without_s2p_identity_is_flagged_not_rejected(self):
        info = inspect_custom_firmware(
            self.write(make_image(identity=False)), BUNDLED_PARTITIONS
        )
        self.assertFalse(info.s2p_identity)

    def test_unsafe_images_are_rejected(self):
        cases = {
            "magic": make_image(magic=0x00),
            "chip": make_image(chip_id=0x0000),
            "tiny": make_image()[:100],
            "oversized": make_image(size=0x100000 + 1),
            "no_app_desc": make_image(app_desc=False),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(CustomFirmwareError) as ctx:
                inspect_custom_firmware(self.write(data, name + ".bin"),
                                        BUNDLED_PARTITIONS)
            self.assertTrue(ctx.exception.zh)
            self.assertTrue(ctx.exception.en)
            self.assertEqual(ctx.exception.message("en"), ctx.exception.en)


class CustomFirmwareFlashFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = Path(self.tmp.name) / "custom.bin"
        self.image.write_bytes(make_image())

    def make_gui(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = Mock()
        gui._close_in_progress = False
        gui._esp32_operation_active = False
        gui._main_start_job = None
        gui._calibration_start_job = None
        gui._flash_detection_job = None
        gui._flash_preflight_poll_job = None
        gui._flash_operation_active = False
        gui._flash_operation_generation = 0
        gui.calibration_process = None
        gui.flash_process = None
        gui.language = "zh"
        gui.tr = lambda text: text
        gui._set_flash_progress = Mock()
        gui._close_flash_progress = Mock()
        gui._firmware_operation_is_active = Mock(return_value=False)
        gui.inspect_esp32_firmware = Mock(return_value=(None, False))
        return gui

    def verified_gui(self):
        gui = self.make_gui()
        gui._flash_operation_active = True
        gui._flash_preflight_verified_port = "COM4"
        gui._flash_preflight_verified_generation = 0
        gui._flash_port_identity = Mock(return_value="port-id")
        gui._flash_preflight_port_identity = "port-id"
        gui._flash_custom_firmware = inspect_custom_firmware(
            self.image, BUNDLED_PARTITIONS
        )
        return gui

    def test_custom_flash_writes_the_selected_image_at_app_offset(self):
        gui = self.verified_gui()
        with patch.object(config_gui.subprocess, "Popen") as popen:
            gui.start_firmware_flash("COM4")
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("0x10000") + 1], str(self.image))
        self.assertEqual(
            command[command.index("0x0") + 1], str(config_gui.BOOTLOADER_PATH)
        )
        self.assertEqual(
            command[command.index("0x8000") + 1], str(config_gui.PARTITION_PATH)
        )

    def test_custom_image_changed_after_confirmation_is_not_flashed(self):
        gui = self.verified_gui()
        self.image.write_bytes(make_image(version=b"9.9.9"))
        with (
            patch.object(config_gui.subprocess, "Popen") as popen,
            patch.object(config_gui.messagebox, "showerror") as error,
        ):
            gui.start_firmware_flash("COM4")
        popen.assert_not_called()
        error.assert_called_once()
        self.assertFalse(gui._flash_operation_active)
        self.assertIsNone(gui._flash_custom_firmware)

    def test_builtin_flash_still_uses_bundled_image(self):
        gui = self.verified_gui()
        gui._flash_custom_firmware = None
        with patch.object(config_gui.subprocess, "Popen") as popen:
            gui.start_firmware_flash("COM4")
        command = popen.call_args.args[0]
        self.assertEqual(
            command[command.index("0x10000") + 1],
            str(config_gui.APP_FIRMWARE_PATH),
        )

    def required_files_patch(self):
        paths = {}
        for name in ("ESPTOOL_PATH", "BOOTLOADER_PATH", "PARTITION_PATH"):
            path = Path(self.tmp.name) / name
            path.write_bytes(b"x")
            paths[name] = path
        paths["PARTITION_PATH"] = BUNDLED_PARTITIONS
        return patch.multiple(config_gui, **paths)

    def test_cancelled_file_dialog_starts_nothing(self):
        gui = self.make_gui()
        with (
            self.required_files_patch(),
            patch.object(config_gui.filedialog, "askopenfilename", return_value=""),
        ):
            gui.flash_custom_firmware()
        gui.inspect_esp32_firmware.assert_not_called()
        self.assertFalse(gui._flash_operation_active)

    def test_rejected_file_shows_error_before_touching_the_device(self):
        bad = Path(self.tmp.name) / "bad.bin"
        bad.write_bytes(b"\0" * 4096)
        gui = self.make_gui()
        with (
            self.required_files_patch(),
            patch.object(config_gui.filedialog, "askopenfilename",
                         return_value=str(bad)),
            patch.object(config_gui.messagebox, "showerror") as error,
        ):
            gui.flash_custom_firmware()
        error.assert_called_once()
        gui.inspect_esp32_firmware.assert_not_called()
        self.assertFalse(gui._flash_operation_active)

    def test_selected_file_is_confirmed_by_name_and_hash(self):
        gui = self.make_gui()
        with (
            self.required_files_patch(),
            patch.object(config_gui.filedialog, "askopenfilename",
                         return_value=str(self.image)),
            patch.object(config_gui.messagebox, "askyesno",
                         return_value=False) as ask,
        ):
            gui.flash_custom_firmware()
        gui.inspect_esp32_firmware.assert_called_once()
        message = ask.call_args.args[1]
        self.assertIn("custom.bin", message)
        self.assertIn(custom_firmware.file_sha256(self.image), message)
        self.assertFalse(gui._flash_operation_active)
        self.assertIsNone(gui._flash_custom_firmware)


class FlashMenuTests(unittest.TestCase):
    def test_footer_offers_bundled_and_custom_firmware(self):
        source = (REPO_ROOT / "src" / "gui_sections" / "footer.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('("刷入內建韌體", gui.flash_firmware)', source)
        self.assertIn('("刷入自訂韌體…", gui.flash_custom_firmware)', source)


if __name__ == "__main__":
    unittest.main()
