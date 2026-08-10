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
        self.assertIn("handle_query_command", task)
        self.assertIn("standalone_xinput_accept_switch_report", task)

    def test_standalone_usb_is_pumped_after_fresh_input(self):
        task_start = MAIN_SOURCE.index("static void cdc_task")
        task_end = MAIN_SOURCE.index("static void gap_cb", task_start)
        task = MAIN_SOURCE[task_start:task_end]
        accepted = task.rindex("standalone_xinput_accept_switch_report")
        post_input_pump = task.rindex("standalone_xinput_pump()")
        command_output = task.index(
            "xQueueReceive(s_out_queue", accepted
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

    def test_standalone_usb_rumble_wakes_output_task_immediately(self):
        transfer_start = XINPUT_SOURCE.index("static bool xinput_transfer(")
        transfer_end = XINPUT_SOURCE.index(
            "static const usbd_class_driver_t", transfer_start
        )
        transfer = XINPUT_SOURCE[transfer_start:transfer_end]
        dirty = transfer.index("s_rumble_dirty = true")
        wake = transfer.index("if (s_wakeup_cb) s_wakeup_cb()", dirty)
        self.assertLess(dirty, wake)

    def test_gatt_rumble_keeps_only_latest_state_and_retries(self):
        self.assertIn("gatt_output_state_t", MAIN_SOURCE)
        self.assertIn("queue_latest_rumble_write", MAIN_SOURCE)
        self.assertIn("state->sequence++", MAIN_SOURCE)
        self.assertIn("s_gatt_output_metrics.overwritten++", MAIN_SOURCE)
        self.assertIn("GATT_OUTPUT_MAX_RETRIES", MAIN_SOURCE)
        self.assertIn("state->retry_pending = true", MAIN_SOURCE)
        self.assertIn("pump_gatt_outputs();", MAIN_SOURCE)
        do_wr_start = MAIN_SOURCE.index("static void do_wr(")
        do_wr_end = MAIN_SOURCE.index("static void do_rs(", do_wr_start)
        do_wr = MAIN_SOURCE[do_wr_start:do_wr_end]
        self.assertIn("queue_latest_rumble_write(ch, buf, len)", do_wr)

    def test_gatt_write_completion_and_congestion_are_observed(self):
        callback_start = MAIN_SOURCE.index("static void gattc_cb(")
        callback_end = MAIN_SOURCE.index(
            "static esp_ble_scan_params_t", callback_start
        )
        callback = MAIN_SOURCE[callback_start:callback_end]
        self.assertIn("case ESP_GATTC_WRITE_CHAR_EVT:", callback)
        self.assertIn("note_gatt_write_complete(", callback)
        self.assertIn("param->write.conn_id", callback)
        self.assertIn("case ESP_GATTC_CONGEST_EVT:", callback)
        self.assertIn("param->congest.conn_id", callback)
        self.assertIn("param->congest.congested", callback)
        self.assertIn("case ESP_GATTC_QUEUE_FULL_EVT:", callback)
        self.assertIn("param->queue_full.conn_id", callback)
        self.assertIn("param->queue_full.is_full", callback)
        self.assertIn("s_ch[ch].conn_id != conn_id", MAIN_SOURCE)
        self.assertIn(
            "state->generation != s_ch[ch].generation", MAIN_SOURCE
        )
        for metric in (
            "gatt_accepted",
            "gatt_busy",
            "gatt_failed",
            "gatt_retries",
            "gatt_pending",
        ):
            self.assertIn(metric, MAIN_SOURCE)

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
        self.assertIn(
            "s_last_input_report_time_valid[ch] = false",
            MAIN_SOURCE,
        )
        self.assertIn(
            "s_widened_mask & (1u << channel)",
            MAIN_SOURCE,
        )
        widen_start = MAIN_SOURCE.index("if (r0 >= 2)")
        widen_end = MAIN_SOURCE.index(
            "s_conn_open_after = now_ms() + 250", widen_start
        )
        widen = MAIN_SOURCE[widen_start:widen_end]
        self.assertIn("portENTER_CRITICAL(&s_in_mux)", widen)
        self.assertIn(
            "s_last_input_report_time_valid[i] = false",
            widen,
        )
        self.assertIn("static void cancel_usb_wait(void)", XINPUT_SOURCE)
        self.assertIn(
            "if (!tud_ready()) {\n"
            "            cancel_usb_wait();",
            XINPUT_SOURCE,
        )
        reset = XINPUT_SOURCE.index("static void xinput_reset(")
        opened = XINPUT_SOURCE.index("static uint16_t xinput_open(", reset)
        self.assertIn("cancel_usb_wait();", XINPUT_SOURCE[reset:opened])

    def test_cdc_partial_writes_resume_before_next_queue_item(self):
        self.assertIn("#define CDC_TX_BUFFER_SIZE        512", MAIN_SOURCE)
        self.assertIn("size_t offset;", MAIN_SOURCE)
        self.assertIn("bool valid;", MAIN_SOURCE)
        submit_start = MAIN_SOURCE.index("static bool cdc_tx_submit(")
        pump_start = MAIN_SOURCE.index(
            "static bool cdc_tx_pump_until(", submit_start
        )
        submit = MAIN_SOURCE[submit_start:pump_start]
        self.assertIn("memcpy(s_cdc_tx.data, data, len)", submit)
        self.assertIn("s_cdc_tx.offset = 0", submit)
        self.assertIn("s_cdc_tx.valid = true", submit)
        pump_end = MAIN_SOURCE.index(
            "static bool cdc_tx_can_submit(", pump_start
        )
        pump = MAIN_SOURCE[pump_start:pump_end]
        self.assertIn("s_cdc_tx.offset += written", pump)
        self.assertIn(
            "if (s_cdc_tx.offset >= s_cdc_tx.length)",
            pump,
        )
        task_start = MAIN_SOURCE.index("static void cdc_task(")
        task_end = MAIN_SOURCE.index(
            "static void wake_standalone_output", task_start
        )
        task = MAIN_SOURCE[task_start:task_end]
        self.assertIn(
            "esp_timer_get_time() + CDC_TX_PHASE_BUDGET_US",
            task,
        )
        self.assertIn("cdc_tx_can_submit(cdc_deadline_us)", task)
        self.assertIn("CDC_QUEUE_BUDGET_PER_LOOP", task)
        self.assertNotIn("safe_cdc_write", MAIN_SOURCE)

    def test_cdc_control_and_event_queues_precede_low_priority_output(self):
        task_start = MAIN_SOURCE.index("static void cdc_task")
        task_end = MAIN_SOURCE.index(
            "static void wake_standalone_output", task_start
        )
        task = MAIN_SOURCE[task_start:task_end]
        control = task.index("xQueueReceive(\n                    s_control_queue")
        event = task.index("xQueueReceive(s_event_queue")
        query = task.index("xQueueReceive(s_query_queue")
        low_priority = task.index("xQueueReceive(s_out_queue")
        self.assertLess(control, event)
        self.assertLess(event, query)
        self.assertLess(query, low_priority)
        control_loop = task[task.rfind("for (", 0, control):control]
        self.assertNotIn("cdc_tx_can_submit", control_loop)

    def test_scan_flood_cannot_consume_lifecycle_event_capacity(self):
        self.assertIn(
            "s_event_queue = xQueueCreate(16, sizeof(line_t))",
            MAIN_SOURCE,
        )
        self.assertIn(
            "s_out_queue = xQueueCreate(24, sizeof(line_t))",
            MAIN_SOURCE,
        )
        self.assertIn("queue_json(s_event_queue, s)", MAIN_SOURCE)
        self.assertIn("queue_json(s_out_queue, s)", MAIN_SOURCE)
        for command in (
            "connected",
            "connect_fail",
            "disconnected",
            "gatt_done",
        ):
            marker = f'\\"cmd\\":\\"{command}\\"'
            event_start = MAIN_SOURCE.index(marker)
            event_end = MAIN_SOURCE.index(";", event_start)
            self.assertIn(
                "out_event(",
                MAIN_SOURCE[event_end:event_end + 350],
                command,
            )
        scan_start = MAIN_SOURCE.index('\\"cmd\\":\\"scan_result\\"')
        scan_end = MAIN_SOURCE.index(";", scan_start)
        self.assertIn(
            "out_json(",
            MAIN_SOURCE[scan_end:scan_end + 350],
        )
        self.assertIn("s_event_queue_drops", MAIN_SOURCE)

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
            "static void note_standalone_battery_led_ack", start
        )
        handler = MAIN_SOURCE[start:end]
        self.assertIn(".command_id", handler)
        self.assertIn(".subcommand_id", handler)

    def test_standalone_battery_uses_desktop_curve_level_boundaries(self):
        self.assertIn(
            "#define STANDALONE_BATTERY_LEVEL_2_MV 3223u",
            MAIN_SOURCE,
        )
        self.assertIn(
            "#define STANDALONE_BATTERY_LEVEL_3_MV 3369u",
            MAIN_SOURCE,
        )
        self.assertIn(
            "#define STANDALONE_BATTERY_LEVEL_4_MV 3535u",
            MAIN_SOURCE,
        )
        level_start = MAIN_SOURCE.index(
            "static uint8_t standalone_battery_level_from_mask"
        )
        level_end = MAIN_SOURCE.index(
            "static void note_standalone_battery_report", level_start
        )
        levels = MAIN_SOURCE[level_start:level_end]
        for mask in ("0x01", "0x03", "0x07", "0x0F"):
            self.assertIn(mask, levels)

    def test_standalone_battery_report_is_bounds_checked_and_observed(self):
        report_start = MAIN_SOURCE.index(
            "static void note_standalone_battery_report"
        )
        report_end = MAIN_SOURCE.index(
            "static const uint8_t s_pair_ltk1", report_start
        )
        report = MAIN_SOURCE[report_start:report_end]
        self.assertIn("length < 33", report)
        self.assertIn("payload[31]", report)
        self.assertIn("payload[32]", report)
        self.assertIn("voltage_mv < 2500u", report)
        self.assertIn("voltage_mv > 5000u", report)

        task_start = MAIN_SOURCE.index("static void cdc_task")
        task_end = MAIN_SOURCE.index("static void gap_cb", task_start)
        task = MAIN_SOURCE[task_start:task_end]
        self.assertEqual(
            task.count("note_standalone_battery_report("), 2
        )

    def test_standalone_battery_led_waits_for_init_and_ack(self):
        pump_start = MAIN_SOURCE.index(
            "static void pump_standalone_battery_leds"
        )
        pump_end = MAIN_SOURCE.index(
            "static void encode_standalone_vibration", pump_start
        )
        pump = MAIN_SOURCE[pump_start:pump_end]
        self.assertIn("standalone_init_step < init_count", pump)
        self.assertIn("standalone_init_waiting", pump)
        self.assertIn(
            "desired == s_ch[ch].standalone_battery_led_applied_mask",
            pump,
        )
        self.assertIn(
            "if (!write_switch_command(ch, 0x09, 0x07",
            pump,
        )
        self.assertIn("standalone_battery_led_waiting = true", pump)

        ack_start = MAIN_SOURCE.index(
            "static void note_standalone_battery_led_ack"
        )
        ack_end = MAIN_SOURCE.index(
            "static void pump_standalone_battery_leds", ack_start
        )
        ack = MAIN_SOURCE[ack_start:ack_end]
        self.assertIn("payload[0] != 0x09", ack)
        self.assertIn("payload[3] != 0x07", ack)
        self.assertIn("standalone_battery_led_applied_mask", ack)

    def test_standalone_command_state_only_waits_after_accepted_write(self):
        pair_start = MAIN_SOURCE.index(
            "static void pump_standalone_pairing"
        )
        pair_end = MAIN_SOURCE.index(
            "static bool note_standalone_pair_ack", pair_start
        )
        pair = MAIN_SOURCE[pair_start:pair_end]
        self.assertIn("if (!write_switch_command(", pair)
        self.assertLess(
            pair.index("if (!write_switch_command("),
            pair.index("standalone_pair_waiting = true"),
        )

        init_start = MAIN_SOURCE.index(
            "static void pump_standalone_controller_init"
        )
        init_end = MAIN_SOURCE.index(
            "static void note_standalone_init_ack", init_start
        )
        init = MAIN_SOURCE[init_start:init_end]
        self.assertIn("if (!write_switch_command(", init)
        self.assertLess(
            init.index("if (!write_switch_command("),
            init.index("standalone_init_waiting = true"),
        )

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
        if os.environ.get("S2P_VERIFY_RELEASE_IMAGE") == "1":
            bundled = ROOT / "esp32s3" / "firmware"
            outputs = {
                "bootloader.bin": (
                    FIRMWARE / "build" / "bootloader" / "bootloader.bin"
                ),
                "partition-table.bin": (
                    FIRMWARE
                    / "build"
                    / "partition_table"
                    / "partition-table.bin"
                ),
                "esp32s3_bluedroid_bridge.bin": (
                    FIRMWARE
                    / "build"
                    / "esp32s3_bluedroid_bridge.bin"
                ),
            }
            for name, built in outputs.items():
                with self.subTest(release_image=name):
                    self.assertEqual(
                        built.read_bytes(),
                        (bundled / name).read_bytes(),
                    )


if __name__ == "__main__":
    unittest.main()
