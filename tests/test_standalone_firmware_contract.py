import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = (
    ROOT / "esp32s3" / "source" / "esp32s3_usb_bridge_bluedroid"
)
MAIN_SOURCE = (FIRMWARE / "main" / "main.c").read_text(encoding="utf-8")
STORE_SOURCE = (
    FIRMWARE / "main" / "standalone_profile_store.c"
).read_text(encoding="utf-8")
XINPUT_SOURCE = (
    FIRMWARE / "main" / "standalone_xinput.c"
).read_text(encoding="utf-8")
XINPUT_HEADER = (
    FIRMWARE / "main" / "standalone_xinput.h"
).read_text(encoding="utf-8")
METRICS_SOURCE = (
    FIRMWARE / "main" / "ble_callback_metrics.c"
).read_text(encoding="utf-8")
CMAKE_SOURCE = (
    FIRMWARE / "main" / "CMakeLists.txt"
).read_text(encoding="utf-8")


class StandaloneFirmwareContractTests(unittest.TestCase):
    def test_boot_tries_alternate_slot_after_active_failure(self):
        first = STORE_SOURCE.index("loaded = apply_slot(nvs, active_slot)")
        alternate = STORE_SOURCE.index("active_slot ^= 1u", first)
        retry = STORE_SOURCE.index(
            "loaded = apply_slot(nvs, active_slot)", alternate
        )
        self.assertLess(first, alternate)
        self.assertLess(alternate, retry)

    def test_profile_is_parsed_before_any_nvs_write(self):
        validation = STORE_SOURCE.index(
            "standalone_xinput_validate_profile_json"
        )
        slot_write = STORE_SOURCE.index("nvs_set_blob", validation)
        active_write = STORE_SOURCE.index("nvs_set_u8", slot_write)
        self.assertLess(validation, slot_write)
        self.assertLess(slot_write, active_write)

    def test_all_commit_fault_stages_are_injected_in_order(self):
        stages = (
            "FAULT_BEFORE_SLOT_WRITE",
            "FAULT_AFTER_SLOT_WRITE",
            "FAULT_AFTER_SLOT_COMMIT",
            "FAULT_AFTER_ACTIVE_WRITE",
            "FAULT_AFTER_ACTIVE_COMMIT",
        )
        offsets = [STORE_SOURCE.index(stage) for stage in stages]
        self.assertEqual(offsets, sorted(offsets))

    def test_profile_json_requires_object_and_schema(self):
        self.assertIn("!cJSON_IsObject(root)", XINPUT_SOURCE)
        self.assertIn('cJSON_GetObjectItemCaseSensitive(root, "schema")',
                      XINPUT_SOURCE)
        self.assertIn("!cJSON_IsNumber(schema)", XINPUT_SOURCE)

    def test_ble_callback_only_queues_input(self):
        notify_start = MAIN_SOURCE.index("case ESP_GATTC_NOTIFY_EVT:")
        notify_end = MAIN_SOURCE.index(
            "case ESP_GATTC_WRITE_DESCR_EVT", notify_start
        )
        callback = MAIN_SOURCE[notify_start:notify_end]
        self.assertNotIn(
            "standalone_xinput_accept_switch_report", callback
        )
        self.assertIn("xTaskNotifyGive", callback)

    def test_ble_timing_reports_max_p95_and_p99(self):
        self.assertIn('"ble timing"', MAIN_SOURCE)
        self.assertIn("max_us", METRICS_SOURCE)
        self.assertIn("p95_us", METRICS_SOURCE)
        self.assertIn("p99_us", METRICS_SOURCE)

    def test_committed_config_is_separate_from_mutable_runtime(self):
        self.assertIn(
            "static standalone_runtime_config_t s_profile_config",
            XINPUT_SOURCE,
        )
        self.assertIn(
            "*parsed = s_profile_config",
            XINPUT_SOURCE,
        )
        self.assertNotIn(
            "standalone_runtime_config_t next = s_profile_config",
            XINPUT_SOURCE,
        )
        self.assertIn("s_profile_config = *next", XINPUT_SOURCE)
        self.assertIn("s_runtime = *next", XINPUT_SOURCE)

    def test_build_lists_split_modules_and_fusion_source(self):
        self.assertIn('"ble_callback_metrics.c"', CMAKE_SOURCE)
        self.assertIn('"standalone_profile_store.c"', CMAKE_SOURCE)
        self.assertIn('"standalone_xinput.c"', CMAKE_SOURCE)
        self.assertIn('"fusion/FusionAhrs.c"', CMAKE_SOURCE)

    def test_profile_commit_and_input_processing_share_cdc_task(self):
        task_start = MAIN_SOURCE.index("static void cdc_task")
        task_end = MAIN_SOURCE.index("static void gap_cb", task_start)
        task = MAIN_SOURCE[task_start:task_end]
        self.assertIn("handle_command", task)
        self.assertIn("standalone_xinput_accept_switch_report", task)

    def test_standalone_usb_is_pumped_after_fresh_input(self):
        task_start = MAIN_SOURCE.index("static void cdc_task")
        task_end = MAIN_SOURCE.index("static void gap_cb", task_start)
        task = MAIN_SOURCE[task_start:task_end]
        accepted = task.rindex("standalone_xinput_accept_switch_report")
        post_input_pump = task.rindex("standalone_xinput_pump()")
        command_output = task.index(
            "while (xQueueReceive(s_out_queue", accepted
        )
        self.assertLess(accepted, post_input_pump)
        self.assertLess(post_input_pump, command_output)

    def test_standalone_usb_completion_wakes_pending_output(self):
        self.assertIn(
            "standalone_xinput_set_wakeup_cb(wake_standalone_output)",
            MAIN_SOURCE,
        )
        self.assertIn(
            "wake_output_if_pending(false)", XINPUT_SOURCE
        )
        self.assertIn(
            "void tud_hid_report_complete_cb(", XINPUT_SOURCE
        )
        self.assertIn(
            "wake_output_if_pending(true)", XINPUT_SOURCE
        )

    def test_latency_diagnostics_cover_source_shadow_and_usb(self):
        self.assertIn('"latency status"', MAIN_SOURCE)
        self.assertIn('"latency reset"', MAIN_SOURCE)
        self.assertIn("note_ble_input_report(", MAIN_SOURCE)
        self.assertIn("source_gap_events", MAIN_SOURCE)
        self.assertIn("shadow_overwrites", MAIN_SOURCE)
        self.assertIn("notify_queue_drops", MAIN_SOURCE)
        self.assertIn("busy_events", XINPUT_SOURCE)
        self.assertIn("pending_overwrites", XINPUT_SOURCE)
        self.assertIn("wait_total_us", XINPUT_SOURCE)
        self.assertIn(
            "standalone_xinput_reset_latency_metrics", XINPUT_HEADER
        )
        self.assertIn("s_ch[channel].itvl", MAIN_SOURCE)
        self.assertIn("gap_threshold_ms", MAIN_SOURCE)
        self.assertIn("static void cancel_usb_wait(void)", XINPUT_SOURCE)
        self.assertIn(
            "if (!tud_ready()) {\n"
            "            cancel_usb_wait();",
            XINPUT_SOURCE,
        )
        reset = XINPUT_SOURCE.index("static void xinput_reset(")
        opened = XINPUT_SOURCE.index("static uint16_t xinput_open(", reset)
        self.assertIn("cancel_usb_wait();", XINPUT_SOURCE[reset:opened])

    def test_direction_mapping_consumes_native_stick_before_gyro(self):
        helper = XINPUT_SOURCE.index(
            "static bool stick_direction_consumes_native_output"
        )
        self.assertIn(
            "config->mode == STICK_DIRECTION_LT",
            XINPUT_SOURCE[helper:],
        )
        self.assertIn(
            "config->targets[index] != 0",
            XINPUT_SOURCE[helper:],
        )
        mapping = XINPUT_SOURCE.index(
            "direction_targets |= apply_stick_direction"
        )
        consume = XINPUT_SOURCE.index(
            "stick_direction_consumes_native_output(&directions[0])",
            mapping,
        )
        zero_axis = XINPUT_SOURCE.index("report.left_x = 0", consume)
        gyro = XINPUT_SOURCE.index(
            "apply_gyro_to_report_runtime(", zero_axis
        )
        self.assertLess(mapping, consume)
        self.assertLess(consume, zero_axis)
        self.assertLess(zero_axis, gyro)

    def test_connection_watchdog_covers_unready_channels(self):
        self.assertIn("connect_deadline_ms", MAIN_SOURCE)
        self.assertIn("pump_connection_watchdogs", MAIN_SOURCE)
        self.assertIn("esp_ble_gap_disconnect", MAIN_SOURCE)

    def test_scan_failures_are_observed_and_retried(self):
        self.assertIn("request_scan_start", MAIN_SOURCE)
        self.assertIn("request_scan_stop", MAIN_SOURCE)
        self.assertIn("ESP_GAP_BLE_SCAN_START_COMPLETE_EVT", MAIN_SOURCE)
        self.assertIn("scan start event failed", MAIN_SOURCE)
        self.assertIn("scan stop event failed", MAIN_SOURCE)

    def test_ready_requires_successful_input_cccd_write(self):
        register_start = MAIN_SOURCE.index(
            "case ESP_GATTC_REG_FOR_NOTIFY_EVT:"
        )
        write_start = MAIN_SOURCE.index(
            "case ESP_GATTC_WRITE_DESCR_EVT", register_start
        )
        register_handler = MAIN_SOURCE[register_start:write_start]
        self.assertNotIn("mark_channel_ready", register_handler)
        write_handler = MAIN_SOURCE[
            write_start:MAIN_SOURCE.index("default:", write_start)
        ]
        self.assertIn("param->write.status == ESP_GATT_OK", write_handler)
        self.assertIn("mark_channel_ready(ch)", write_handler)

    def test_disconnect_uses_channel_interface_and_invalidates_queues(self):
        disconnect_start = MAIN_SOURCE.index(
            "if (event == ESP_GATTC_DISCONNECT_EVT)"
        )
        disconnect_end = MAIN_SOURCE.index(
            "int ch = ch_by_if", disconnect_start
        )
        disconnect_handler = MAIN_SOURCE[
            disconnect_start:disconnect_end
        ]
        self.assertIn("s_ch[dch].gattc_if", disconnect_handler)
        self.assertIn("clear_channel_state(dch)", disconnect_handler)
        self.assertIn("generation", MAIN_SOURCE)

    def test_init_ack_matches_command_and_subcommand(self):
        start = MAIN_SOURCE.index("static void note_standalone_init_ack")
        end = MAIN_SOURCE.index(
            "static void encode_standalone_vibration", start
        )
        handler = MAIN_SOURCE[start:end]
        self.assertIn(".command_id", handler)
        self.assertIn(".subcommand_id", handler)

    def test_output_mode_uses_single_nvs_enum_with_legacy_migration(self):
        self.assertIn("standalone_output_mode_t", XINPUT_HEADER)
        self.assertIn("STANDALONE_OUTPUT_MODE_KEY", XINPUT_SOURCE)
        self.assertIn("legacy_standalone", XINPUT_SOURCE)
        self.assertIn("legacy_hid", XINPUT_SOURCE)
        self.assertNotIn("standalone_mode_store", XINPUT_HEADER)

    @unittest.skipUnless(
        os.environ.get("S2P_RUN_IDF_BUILD") == "1",
        "set S2P_RUN_IDF_BUILD=1 to run the full ESP-IDF build",
    )
    def test_full_esp_idf_build(self):
        idf_path = Path(
            os.environ.get(
                "IDF_PATH",
                r"C:\Espressif\frameworks\esp-idf-v5.5.4",
            )
        )
        command = (
            f'set "S2P_IDF_EXPORT={idf_path / "export.bat"}" && '
            'call "%S2P_IDF_EXPORT%" && '
            'set "IDF_CCACHE_ENABLE=0" && idf.py build'
        )
        subprocess.run(
            command,
            cwd=FIRMWARE,
            check=True,
            shell=True,
        )


if __name__ == "__main__":
    unittest.main()
