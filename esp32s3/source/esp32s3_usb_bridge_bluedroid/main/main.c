/*
 * Switch2Connect - A Python and ESP32-S3 bridge utility for Switch 2 controller inputs.
 * Copyright (C) 2026 TommyWabg
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 * Contact Information:
 * Electronic Mail: tommyw9318@gmail.com
 */

// ESP32-S3 USB <-> BLE bridge for Switch 2 controllers — BLUEDROID variant.
//
// Goal: see whether Bluedroid's multi-task host (BTU/BTC) beats NimBLE's single-host-
// task ~33 rumble-writes/s/channel ceiling.  Same on-the-wire protocol & same USB-CDC
// command protocol as the NimBLE build, so the existing Python host drives it unchanged.
//
// MULTI-CONTROLLER MODEL (matches NimBLE's per-conn_handle isolation): one GATTC app
// interface (gattc_if) per channel.  Every GATTC event is tagged with its gattc_if, so
// connections with IDENTICAL GATT handle layouts (two Joy-Cons!) never get confused —
// the REG_FOR_NOTIFY event in particular carries no conn_id, so a single shared gattc_if
// could not tell the two apart (it reported the 2nd controller's subscribe against the
// 1st).  Per-channel gattc_if fixes that.

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "esp_log.h"
#include "esp_system.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_bt_device.h"
#include "esp_gap_ble_api.h"
#include "esp_timer.h"
#include "esp_gattc_api.h"
#include "esp_gatt_defs.h"
#include "esp_gatt_common_api.h"

#include "tinyusb.h"
#include "tusb_cdc_acm.h"
#include "ble_callback_metrics.h"
#include "standalone_profile_store.h"
#include "standalone_xinput.h"

static const char *TAG = "S3_BLUEDROID";

#define APP_FIRMWARE_PRODUCT      "S2P-FW"
#define APP_FIRMWARE_VERSION      "1.0.4"
#define APP_PROTOCOL_NAME         "s2p_bridge"
#define APP_PROTOCOL_VERSION      "1.0.0"
#define EXPECTED_FIRMWARE_PROFILE "s2p_usb_bridge"
#define EXPECTED_FIRMWARE_BUILD   "standalone_diagnostics"
#define CDC_LINE_STATE_DTR        0x01
#define CDC_TX_BUFFER_SIZE        512
#define CDC_TX_PHASE_BUDGET_US    5000
#define CDC_QUEUE_BUDGET_PER_LOOP 4
#define NINTENDO_COMPANY_ID       0x0553
#define MAX_CH                    8     // one GATTC app per channel
#define REPORT_SIZE               64
#define GATT_OUTPUT_MAX_SIZE       96
#define GATT_OUTPUT_RETRY_MS       2u
#define GATT_OUTPUT_MAX_RETRIES    8u
#define GATT_OUTPUT_BLOCK_CONGESTED 0x01u
#define GATT_OUTPUT_BLOCK_QUEUE_FULL 0x02u
#define NINTENDO_VENDOR_ID        0x057E
#define PRO_CONTROLLER2_PID       0x2069
static bool s_standalone_mode;
static bool s_standalone_usb_hid;
static bool s_standalone_auto_probe;
static standalone_output_mode_t s_output_mode;
static standalone_output_mode_t s_active_output_mode;
static bool s_standalone_auto_conn_pending;
static bool s_standalone_auto_conn_pair_required;
static char s_standalone_auto_conn[32];


// --- Switch 2 GATT UUIDs (128-bit; little-endian in esp_bt_uuid_t = string reversed) ---
#define UUID128(b0,b1,b2,b3,b4,b5,b6,b7,b8,b9,b10,b11,b12,b13,b14,b15) \
    { .len = ESP_UUID_LEN_128, .uuid = { .uuid128 = { \
        b15,b14,b13,b12,b11,b10,b9,b8,b7,b6,b5,b4,b3,b2,b1,b0 } } }
static const esp_bt_uuid_t UUID_NOTIFY_FD2 =
    UUID128(0xab,0x7d,0xe9,0xbe,0x89,0xfe,0x49,0xad,0x82,0x8f,0x11,0x8f,0x09,0xdf,0x7f,0xd2);
static const esp_bt_uuid_t UUID_NOTIFY_LEGACY =
    UUID128(0x74,0x92,0x86,0x6c,0xec,0x3e,0x46,0x19,0x82,0x58,0x32,0x75,0x5f,0xfc,0xc0,0xf8);
// NSO GameCube: model-specific input notify char (GATT handle 0x000E, Input Report 0x0A).
// This is NOT the same UUID as UUID_NOTIFY_LEGACY (7492866c…) — that one is the Pro
// Controller's 0x000E char (Input Report 0x09) and is absent on the GameCube.  The GCN's
// usable compact report (buttons@2-4, sticks@5-A, triggers@C/D) lives ONLY here, so the
// generic FD2 char (0x000A, Input Report 0x05) is the wrong byte layout for the GCN.
static const esp_bt_uuid_t UUID_NOTIFY_GC =
    UUID128(0x82,0x61,0xcb,0xa1,0x94,0x35,0x42,0x0c,0x84,0xd6,0xf0,0xc7,0x5a,0x2c,0x8e,0x4d);
// SW2 primary service (…fd0, NOT the input char …fd2).  Used to scope the GCN "enable
// every notify CCCD" behaviour to this service only — mirroring the WinRT GCN path, which
// subscribes to every notify characteristic in the ab7de9be… service.
static const esp_bt_uuid_t UUID_SVC_SW2 =
    UUID128(0xab,0x7d,0xe9,0xbe,0x89,0xfe,0x49,0xad,0x82,0x8f,0x11,0x8f,0x09,0xdf,0x7f,0xd0);
static const esp_bt_uuid_t UUID_ACK =
    UUID128(0xc7,0x65,0xa9,0x61,0xd9,0xd8,0x4d,0x36,0xa2,0x0a,0x53,0x15,0xb1,0x11,0x83,0x6a);
static const esp_bt_uuid_t UUID_CMD =
    UUID128(0x64,0x9d,0x4a,0xc9,0x8e,0xb7,0x4e,0x6c,0xaf,0x44,0x1e,0xa5,0x4f,0xe5,0xf0,0x05);
static const esp_bt_uuid_t UUID_RUMBLE_PRO =
    UUID128(0xcc,0x48,0x3f,0x51,0x92,0x58,0x42,0x7d,0xa9,0x39,0x63,0x0c,0x31,0xf7,0x2b,0x05);
static const esp_bt_uuid_t UUID_RUMBLE_JOYCON_R =
    UUID128(0xfa,0x19,0xb0,0xfb,0xcd,0x1f,0x46,0xa7,0x84,0xa1,0xbb,0xb0,0x9e,0x00,0xc1,0x49);
static const esp_bt_uuid_t UUID_RUMBLE_JOYCON_L =
    UUID128(0x28,0x93,0x26,0xcb,0xa4,0x71,0x48,0x5d,0xa8,0xf4,0x24,0x0c,0x14,0xf1,0x82,0x41);
static const esp_bt_uuid_t UUID_CCCD =
    { .len = ESP_UUID_LEN_16, .uuid = { .uuid16 = ESP_GATT_UUID_CHAR_CLIENT_CONFIG } };

// --- per-controller channel: fixed slot, each owns one GATTC app interface ---
typedef struct {
    esp_gatt_if_t gattc_if;  // assigned once at REG_EVT (channel == app_id); permanent
    uint32_t generation;     // invalidates queued input/ACK/rumble after slot reuse
    bool     used;           // a controller is connected on this slot
    bool     ready;          // discovered + input subscribed
    bool     connecting;
    bool     link_open;
    uint32_t connect_deadline_ms;
    uint16_t conn_id;
    esp_bd_addr_t bda;
    uint8_t  addr_type;
    uint16_t input_handle;
    uint16_t fd2_handle;     // canonical SW2 input notify (ab7de9be…), if present
    uint16_t legacy_handle;  // legacy input notify (74928 66c…), if present
    uint16_t ack_handle;
    uint16_t cmd_handle;
    uint16_t rumble_handle;
    uint16_t itvl;           // connection interval in 1.25 ms units (6=7.5ms, 12=15ms)
    int8_t   rssi_dbm;       // most recently sampled link RSSI
    bool     rssi_valid;
    uint32_t rssi_requested_ms;
    uint32_t rssi_updated_ms;
    uint8_t  input_src;      // which UUID set input_handle: 1=FD2, 2=legacy (diagnostic)
    bool     prefer_legacy;  // NSO GameCube: input is on the LEGACY char, not FD2
    // GCN only: every NOTIFY char handle in the SW2 service (collected during discovery).
    // For the GameCube we CCCD-enable all of them — like the WinRT path — so the controller
    // is happy to stream on 0x000E; NOTIFY_EVT still forwards only input_handle, so the extra
    // streams never reach the host parser.  CCCDs are written one at a time (cccd_idx) driven
    // by WRITE_DESCR_EVT to avoid back-to-back GATT-write congestion.
    uint16_t notify_handles[8];
    uint8_t  notify_count;
    uint8_t  cccd_idx;
    bool     cccd_draining;
    uint16_t input_cccd_handle;
    uint8_t  standalone_init_step;
    uint8_t  standalone_init_attempts;
    bool     standalone_init_waiting;
    uint32_t standalone_init_next_ms;
    uint8_t  standalone_battery_led_desired_mask;
    uint8_t  standalone_battery_led_pending_mask;
    uint8_t  standalone_battery_led_applied_mask;
    bool     standalone_battery_led_waiting;
    uint32_t standalone_battery_led_next_ms;
    bool     standalone_pair_required;
    uint8_t  standalone_pair_step;
    uint8_t  standalone_pair_attempts;
    bool     standalone_pair_waiting;
    uint32_t standalone_pair_next_ms;
    uint8_t  standalone_feedback_step;
    bool     standalone_feedback_active;
    uint32_t standalone_feedback_next_ms;
} channel_t;
static channel_t s_ch[MAX_CH];



static volatile bool s_scan_mode = false;
static int s_pending_conn = -1;   // channel waiting to open once the scan has stopped
// 3rd-controller establishment: two links pinned at 7.5ms saturate the radio so the
// controller cannot even schedule a 3rd connection's SETUP (reason 0x100 CONN_CANCEL,
// regardless of the 3rd's requested interval).  Workaround: temporarily widen the
// existing links to 15ms to free radio time, defer the 3rd open until that settles,
// then restore the widened links to 7.5ms once the 3rd link is established.
static volatile uint32_t s_conn_open_after = 0;  // tick deadline to open a deferred pending conn (0 = open immediately)
static volatile uint8_t  s_widened_mask  = 0;    // links temporarily widened to 15ms; restored after the 3rd connects
static char s_own_mac[18] = "00:00:00:00:00:00";
static uint64_t s_own_mac_value = 0;

// Deferred scan resume.  Starting a scan from inside a GATTC callback while another
// GAP op (a 2nd disconnect, a connect) is still in flight collides on the HCI command
// path and silently drops that op (symptom: only one of a merged pair disconnects).
// So callbacks NEVER start scanning directly — they set s_resume_scan and bump a
// "GAP busy" deadline; cdc_task starts the scan only once the bus has been quiet.
//
// gap_busy() uses ONLY-EXTEND semantics: it never shortens a deadline already in the
// future.  kick_disc_queue sets a 400 ms guard before each gap_disconnect; a
// DISCONNECT_EVT must NOT shorten that window or the next queued disconnect fires
// before the HCI path has settled.
static volatile bool     s_resume_scan = false;
static volatile bool     s_scan_params_ready = false;
static volatile bool     s_scanning = false;
static volatile bool     s_scan_start_pending = false;
static volatile bool     s_scan_stop_pending = false;
static volatile bool     s_scan_stop_needed = false;
static volatile uint32_t s_gap_busy_until = 0;   // ms tick until GAP is considered busy
static volatile uint32_t s_scan_retry_after_ms = 0;
static uint32_t s_generation_counter;
static inline uint32_t now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}
static inline bool time_reached(uint32_t now, uint32_t deadline) {
    return (int32_t)(now - deadline) >= 0;
}
static inline bool deadline_reached(uint32_t now, uint32_t deadline) {
    return deadline != 0 && time_reached(now, deadline);
}
static inline void gap_busy(uint32_t ms) {
    uint32_t t = now_ms() + ms;
    // Only extend the deadline, never shorten it (cast keeps wrap-around safe).
    if ((int32_t)(t - s_gap_busy_until) > 0) s_gap_busy_until = t;
}

// Sequential disconnect queue.
// Bluedroid's HCI path can safely process only ONE gap_disconnect at a time.  Issuing
// several in a tight loop (the old do_disc_all) corrupts the BLE controller's internal
// state → crash on the 3rd controller or after a disc_all during an active session.
// Solution: callers set bits in s_disc_mask; kick_disc_queue() issues ONE disconnect,
// sets s_disc_in_flight, and the DISCONNECT_EVT completion clears it and calls
// kick_disc_queue() again for the next pending channel.
static volatile uint8_t s_disc_mask      = 0;      // bitmask: channels queued for disconnect
static volatile bool    s_disc_in_flight = false;   // true while a gap_disconnect is pending
static portMUX_TYPE     s_disc_mux = portMUX_INITIALIZER_UNLOCKED;

// Issue the next queued disconnect (if none is currently in flight).
// Safe to call from cdc_task (core 1) AND from DISCONNECT_EVT (BTC task, core 0).
static void kick_disc_queue(void) {
    portENTER_CRITICAL(&s_disc_mux);
    if (s_disc_in_flight || s_disc_mask == 0) {
        portEXIT_CRITICAL(&s_disc_mux);
        return;
    }
    // Find the lowest-indexed pending channel that is still in use.
    int ch = -1;
    for (int i = 0; i < MAX_CH; i++) {
        if (s_disc_mask & (1u << i)) {
            if (s_ch[i].used) { ch = i; break; }
            s_disc_mask &= ~(1u << i);  // already gone, clear and skip
        }
    }
    if (ch < 0) {
        // Queue drained; all channels already released — safe to resume scanning.
        bool was_last = (s_disc_mask == 0);
        portEXIT_CRITICAL(&s_disc_mux);
        if (was_last && s_scan_mode) s_resume_scan = true;
        return;
    }
    esp_bd_addr_t bda;
    memcpy(bda, s_ch[ch].bda, sizeof(bda));
    s_disc_mask      &= ~(1u << ch);
    s_disc_in_flight  = true;
    portEXIT_CRITICAL(&s_disc_mux);
    gap_busy(400);                        // hold off scan until this disconnect settles
    esp_err_t err = esp_ble_gap_disconnect(bda);
    if (err != ESP_OK) {
        portENTER_CRITICAL(&s_disc_mux);
        s_disc_in_flight = false;
        if (s_ch[ch].used) s_disc_mask |= (1u << ch);
        portEXIT_CRITICAL(&s_disc_mux);
        gap_busy(250);
        ESP_LOGW(
            TAG, "gap_disconnect ch=%d failed synchronously: %s",
            ch, esp_err_to_name(err)
        );
    }
}

static int ch_by_if(esp_gatt_if_t gif) {
    for (int i = 0; i < MAX_CH; i++) if (s_ch[i].gattc_if == gif) return i;
    return -1;
}

static int ch_by_bda(esp_bd_addr_t bda) {
    for (int i = 0; i < MAX_CH; i++) {
        if (s_ch[i].used && memcmp(s_ch[i].bda, bda, sizeof(esp_bd_addr_t)) == 0) return i;
    }
    return -1;
}
static int ch_alloc(void) {
    for (int i = 0; i < MAX_CH; i++)
        if (s_ch[i].gattc_if != ESP_GATT_IF_NONE && !s_ch[i].used) return i;
    return -1;
}
static uint8_t ch_active_mask(void) {
    uint8_t m = 0; for (int i = 0; i < MAX_CH; i++) if (s_ch[i].used && s_ch[i].ready) m |= (1u << i);
    return m;
}
// Count slots in use (reserved/connecting) and fully ready (input subscribed).
// Used by the connection diagnostics so a 3rd-controller failure log shows how many
// links were already live when it was attempted.
static void ch_count(int *used, int *ready) {
    int u = 0, r = 0;
    for (int i = 0; i < MAX_CH; i++) if (s_ch[i].used) { u++; if (s_ch[i].ready) r++; }
    if (used)  *used  = u;
    if (ready) *ready = r;
}

// --- USB-CDC transport ---
static QueueHandle_t s_control_queue; // inbound commands that never write CDC
static QueueHandle_t s_query_queue; // inbound commands that produce CDC output
static QueueHandle_t s_ack_queue;   // ack/cmd notifications (P0)
static QueueHandle_t s_notify_queue; // handle-routed notifications for GCN/WinRT parity
static QueueHandle_t s_event_queue; // connection lifecycle JSON (P1)
static QueueHandle_t s_out_queue;   // scan/debug JSON (low priority)
static TaskHandle_t s_cdc_task_h;
static volatile uint32_t s_event_queue_drops = 0;
typedef struct { char text[256]; } line_t;
typedef struct {
    uint8_t data[CDC_TX_BUFFER_SIZE];
    size_t length;
    size_t offset;
    bool valid;
} cdc_tx_state_t;
static cdc_tx_state_t s_cdc_tx;
typedef struct {
    uint8_t ch;
    uint8_t len;
    uint16_t handle;
    uint32_t generation;
    uint8_t data[REPORT_SIZE];
} in_report_t;
static char s_rx_buf[512];
static int  s_rx_len = 0;
static in_report_t s_in_shadow[MAX_CH];
static volatile bool s_in_dirty[MAX_CH];
static portMUX_TYPE s_in_mux = portMUX_INITIALIZER_UNLOCKED;
typedef struct {
    uint32_t generation;
    uint32_t sequence;
    uint32_t in_flight_sequence;
    uint32_t retry_after_ms;
    uint16_t handle;
    uint16_t length;
    uint8_t block_flags;
    uint8_t retry_attempts;
    bool pending;
    bool in_flight;
    bool retry_pending;
    uint8_t data[GATT_OUTPUT_MAX_SIZE];
} gatt_output_state_t;
typedef struct {
    uint32_t submitted;
    uint32_t overwritten;
    uint32_t accepted;
    uint32_t completed;
    uint32_t busy;
    uint32_t failed;
    uint32_t retries;
    uint32_t congestion_events;
    uint32_t queue_full_events;
} gatt_output_metrics_t;
static gatt_output_state_t s_gatt_output[MAX_CH];
static gatt_output_metrics_t s_gatt_output_metrics;
static portMUX_TYPE s_gatt_output_mux = portMUX_INITIALIZER_UNLOCKED;
typedef struct {
    uint32_t ble_input_reports;
    uint32_t source_gap_events;
    uint32_t source_gap_max_ms;
    uint32_t shadow_overwrites;
    uint32_t notify_queue_drops;
} input_latency_metrics_t;
static input_latency_metrics_t s_input_latency_metrics;
static uint32_t s_last_input_report_time[MAX_CH];
static bool s_last_input_report_time_valid[MAX_CH];

static void note_ble_input_report(
    int channel, const uint8_t *data, uint8_t length
) {
    if (
        channel < 0 || channel >= MAX_CH || !data || length < 4
    ) return;
    uint32_t report_time =
        (uint32_t)data[0] |
        ((uint32_t)data[1] << 8) |
        ((uint32_t)data[2] << 16) |
        ((uint32_t)data[3] << 24);
    uint32_t expected_interval_ms =
        ((uint32_t)(s_ch[channel].itvl ? s_ch[channel].itvl : 6u)
            * 5u + 3u) / 4u;
    uint32_t gap_threshold_ms =
        expected_interval_ms + expected_interval_ms / 2u;
    portENTER_CRITICAL(&s_in_mux);
    s_input_latency_metrics.ble_input_reports++;
    bool gap_suppressed =
        (s_widened_mask & (1u << channel)) != 0;
    if (!gap_suppressed && s_last_input_report_time_valid[channel]) {
        uint32_t delta =
            report_time - s_last_input_report_time[channel];
        /*
         * Allow one negotiated BLE interval plus 50% timestamp tolerance.
         * This yields 12 ms for a 7.5 ms link and 22 ms for a 15 ms link,
         * avoiding false gaps while three controllers intentionally run at
         * the wider interval. Longer gaps below one second still indicate a
         * skipped source interval; larger values are reconnects or resets.
         */
        if (delta >= gap_threshold_ms && delta < 1000u) {
            s_input_latency_metrics.source_gap_events++;
            if (delta > s_input_latency_metrics.source_gap_max_ms)
                s_input_latency_metrics.source_gap_max_ms = delta;
        }
    }
    s_last_input_report_time[channel] = report_time;
    /*
     * A temporary third-link widen changes the real interval before the
     * negotiated interval callback updates s_ch[].itvl. Do not compare across
     * that transition; the first report after the mask clears is a new base.
     */
    s_last_input_report_time_valid[channel] = !gap_suppressed;
    portEXIT_CRITICAL(&s_in_mux);
}

static void reset_input_latency_metrics(void) {
    portENTER_CRITICAL(&s_in_mux);
    memset(
        &s_input_latency_metrics, 0, sizeof(s_input_latency_metrics)
    );
    memset(
        s_last_input_report_time_valid, 0,
        sizeof(s_last_input_report_time_valid)
    );
    portEXIT_CRITICAL(&s_in_mux);
    standalone_xinput_reset_latency_metrics();
}

static void format_input_latency_metrics(char *output, size_t size) {
    input_latency_metrics_t input;
    standalone_usb_latency_metrics_t usb;
    portENTER_CRITICAL(&s_in_mux);
    input = s_input_latency_metrics;
    portEXIT_CRITICAL(&s_in_mux);
    standalone_xinput_get_latency_metrics(&usb);
    uint64_t wait_average_us = usb.wait_samples
        ? usb.wait_total_us / usb.wait_samples : 0;
    snprintf(
        output, size,
        "{\"cmd\":\"latency_status\",\"ok\":1,"
        "\"ble_input_reports\":%lu,\"source_gap_events\":%lu,"
        "\"source_gap_max_ms\":%lu,\"shadow_overwrites\":%lu,"
        "\"notify_queue_drops\":%lu,\"usb_busy_events\":%lu,"
        "\"usb_pending_overwrites\":%lu,\"usb_wait_samples\":%lu,"
        "\"usb_wait_avg_us\":%llu,\"usb_wait_max_us\":%lu}\n",
        (unsigned long)input.ble_input_reports,
        (unsigned long)input.source_gap_events,
        (unsigned long)input.source_gap_max_ms,
        (unsigned long)input.shadow_overwrites,
        (unsigned long)input.notify_queue_drops,
        (unsigned long)usb.busy_events,
        (unsigned long)usb.pending_overwrites,
        (unsigned long)usb.wait_samples,
        (unsigned long long)wait_average_us,
        (unsigned long)usb.wait_max_us
    );
}

static void request_link_rssi(void) {
    uint32_t now = now_ms();
    for (int ch = 0; ch < MAX_CH; ch++) {
        if (!s_ch[ch].used || !s_ch[ch].link_open) continue;
        if (now - s_ch[ch].rssi_requested_ms < 750u) continue;
        s_ch[ch].rssi_requested_ms = now;
        if (esp_ble_gap_read_rssi(s_ch[ch].bda) != ESP_OK)
            s_ch[ch].rssi_valid = false;
    }
}

static void format_link_status(char *output, size_t size) {
    size_t used = 0;
    int written = snprintf(
        output, size,
        "{\"cmd\":\"link_status\",\"ok\":1,\"bridge_mac\":\"%s\",\"links\":[",
        s_own_mac
    );
    if (written < 0 || (size_t)written >= size) return;
    used = (size_t)written;
    uint32_t now = now_ms();
    bool first = true;
    uint8_t link_count = 0;
    for (int ch = 0; ch < MAX_CH && link_count < 4; ch++) {
        if (!s_ch[ch].used) continue;
        uint32_t age = s_ch[ch].rssi_valid
            ? now - s_ch[ch].rssi_updated_ms : 0u;
        written = snprintf(
            output + used, size - used,
            "%s[%d,\"%02X:%02X:%02X:%02X:%02X:%02X\",%d,%.2f,%d,%lu]",
            first ? "" : ",", ch,
            s_ch[ch].bda[0], s_ch[ch].bda[1], s_ch[ch].bda[2],
            s_ch[ch].bda[3], s_ch[ch].bda[4], s_ch[ch].bda[5],
            s_ch[ch].ready ? 1 : 0,
            (double)s_ch[ch].itvl * 1.25,
            s_ch[ch].rssi_valid ? (int)s_ch[ch].rssi_dbm : 127,
            (unsigned long)age
        );
        if (written < 0 || (size_t)written >= size - used) break;
        used += (size_t)written;
        first = false;
        link_count++;
    }
    if (used + 4u < size) snprintf(output + used, size - used, "]}\n");
}

/*
 * Characteristic writes with NO_RSP still complete asynchronously in
 * Bluedroid. Keep at most one rumble write in flight per controller and one
 * latest pending state behind it. This prevents an opaque GATT queue from
 * turning fresh motor states (especially zero) into stale delayed playback.
 */
static bool gatt_status_is_retryable(esp_gatt_status_t status) {
    return
        status == ESP_GATT_BUSY ||
        status == ESP_GATT_CONGESTED ||
        status == ESP_GATT_NO_RESOURCES ||
        status == ESP_GATT_INSUF_RESOURCE ||
        status == ESP_GATT_ERROR;
}

static void reset_gatt_output_channel(int ch, uint32_t generation) {
    if (ch < 0 || ch >= MAX_CH) return;
    portENTER_CRITICAL(&s_gatt_output_mux);
    memset(&s_gatt_output[ch], 0, sizeof(s_gatt_output[ch]));
    s_gatt_output[ch].generation = generation;
    portEXIT_CRITICAL(&s_gatt_output_mux);
}

static void reset_gatt_output_metrics(void) {
    portENTER_CRITICAL(&s_gatt_output_mux);
    memset(&s_gatt_output_metrics, 0, sizeof(s_gatt_output_metrics));
    portEXIT_CRITICAL(&s_gatt_output_mux);
}

static void get_gatt_output_metrics(
    gatt_output_metrics_t *metrics, uint8_t *pending
) {
    if (!metrics) return;
    uint8_t pending_count = 0;
    portENTER_CRITICAL(&s_gatt_output_mux);
    *metrics = s_gatt_output_metrics;
    for (int ch = 0; ch < MAX_CH; ch++) {
        if (s_gatt_output[ch].pending || s_gatt_output[ch].in_flight)
            pending_count++;
    }
    portEXIT_CRITICAL(&s_gatt_output_mux);
    if (pending) *pending = pending_count;
}

static bool write_gatt_char_checked(
    int ch, uint16_t handle, const uint8_t *data, size_t length
) {
    if (
        ch < 0 || ch >= MAX_CH || !data || length == 0 ||
        length > GATT_OUTPUT_MAX_SIZE || !s_ch[ch].used ||
        !s_ch[ch].link_open || handle == 0
    ) return false;
    portENTER_CRITICAL(&s_gatt_output_mux);
    s_gatt_output_metrics.submitted++;
    portEXIT_CRITICAL(&s_gatt_output_mux);
    esp_err_t err = esp_ble_gattc_write_char(
        s_ch[ch].gattc_if, s_ch[ch].conn_id, handle,
        (uint16_t)length, (uint8_t *)data,
        ESP_GATT_WRITE_TYPE_NO_RSP, ESP_GATT_AUTH_REQ_NONE
    );
    portENTER_CRITICAL(&s_gatt_output_mux);
    if (err == ESP_OK) {
        s_gatt_output_metrics.accepted++;
    } else if (err == ESP_FAIL) {
        /*
         * IDF 5.5 maps an already-congested ATT channel and a full BTC
         * transfer queue to ESP_FAIL. Higher-level command state machines
         * already retry commands whose ACK does not arrive.
         */
        s_gatt_output_metrics.busy++;
    } else {
        s_gatt_output_metrics.failed++;
    }
    portEXIT_CRITICAL(&s_gatt_output_mux);
    return err == ESP_OK;
}

static void pump_gatt_output_channel(int ch) {
    if (ch < 0 || ch >= MAX_CH) return;
    uint8_t payload[GATT_OUTPUT_MAX_SIZE];
    uint16_t length = 0;
    uint16_t handle = 0;
    uint16_t conn_id = 0;
    esp_gatt_if_t gattc_if = ESP_GATT_IF_NONE;
    uint32_t generation = 0;
    uint32_t sequence = 0;
    uint32_t now = now_ms();
    bool retry_attempt = false;

    portENTER_CRITICAL(&s_gatt_output_mux);
    gatt_output_state_t *state = &s_gatt_output[ch];
    if (
        !state->pending || state->in_flight || state->block_flags ||
        (
            state->retry_after_ms &&
            !time_reached(now, state->retry_after_ms)
        )
    ) {
        portEXIT_CRITICAL(&s_gatt_output_mux);
        return;
    }
    if (
        !s_ch[ch].used || !s_ch[ch].link_open ||
        !s_ch[ch].rumble_handle ||
        state->generation != s_ch[ch].generation ||
        state->handle != s_ch[ch].rumble_handle
    ) {
        state->pending = false;
        state->retry_pending = false;
        state->retry_after_ms = 0;
        s_gatt_output_metrics.failed++;
        portEXIT_CRITICAL(&s_gatt_output_mux);
        return;
    }
    length = state->length;
    handle = state->handle;
    generation = state->generation;
    sequence = state->sequence;
    conn_id = s_ch[ch].conn_id;
    gattc_if = s_ch[ch].gattc_if;
    memcpy(payload, state->data, length);
    retry_attempt = state->retry_pending;
    state->retry_pending = false;
    state->retry_after_ms = 0;
    state->in_flight = true;
    state->in_flight_sequence = sequence;
    if (retry_attempt) s_gatt_output_metrics.retries++;
    portEXIT_CRITICAL(&s_gatt_output_mux);

    esp_err_t err = esp_ble_gattc_write_char(
        gattc_if, conn_id, handle, length, payload,
        ESP_GATT_WRITE_TYPE_NO_RSP, ESP_GATT_AUTH_REQ_NONE
    );
    uint32_t retry_after_ms =
        err == ESP_OK ? 0 : now_ms() + GATT_OUTPUT_RETRY_MS;

    portENTER_CRITICAL(&s_gatt_output_mux);
    state = &s_gatt_output[ch];
    if (err == ESP_OK) s_gatt_output_metrics.accepted++;
    if (
        state->generation == generation && state->in_flight &&
        state->in_flight_sequence == sequence
    ) {
        if (err != ESP_OK) {
            state->in_flight = false;
            if (
                err == ESP_FAIL &&
                state->retry_attempts < GATT_OUTPUT_MAX_RETRIES
            ) {
                state->retry_attempts++;
                state->retry_pending = true;
                state->retry_after_ms = retry_after_ms;
                s_gatt_output_metrics.busy++;
            } else {
                if (state->sequence == sequence) state->pending = false;
                state->retry_pending = false;
                state->retry_after_ms = 0;
                s_gatt_output_metrics.failed++;
            }
        }
    }
    portEXIT_CRITICAL(&s_gatt_output_mux);
}

static void pump_gatt_outputs(void) {
    for (int ch = 0; ch < MAX_CH; ch++)
        pump_gatt_output_channel(ch);
}

static bool queue_latest_rumble_write(
    int ch, const uint8_t *data, size_t length
) {
    if (
        ch < 0 || ch >= MAX_CH || !data || length == 0 ||
        length > GATT_OUTPUT_MAX_SIZE || !s_ch[ch].used ||
        !s_ch[ch].link_open || !s_ch[ch].rumble_handle
    ) return false;
    uint32_t generation = s_ch[ch].generation;
    uint16_t handle = s_ch[ch].rumble_handle;
    portENTER_CRITICAL(&s_gatt_output_mux);
    gatt_output_state_t *state = &s_gatt_output[ch];
    if (state->generation != generation) {
        portEXIT_CRITICAL(&s_gatt_output_mux);
        return false;
    }
    if (state->pending || state->in_flight)
        s_gatt_output_metrics.overwritten++;
    state->sequence++;
    if (state->sequence == 0) state->sequence = 1;
    state->handle = handle;
    state->length = (uint16_t)length;
    memcpy(state->data, data, length);
    state->pending = true;
    /*
     * A newly submitted latest state, including zero, bypasses an older
     * retry delay. It still waits for the current in-flight write or a
     * stack-reported congestion window to finish.
     */
    state->retry_attempts = 0;
    state->retry_pending = false;
    state->retry_after_ms = 0;
    s_gatt_output_metrics.submitted++;
    portEXIT_CRITICAL(&s_gatt_output_mux);
    pump_gatt_output_channel(ch);
    return true;
}

static void note_gatt_write_complete(
    int ch, uint16_t conn_id, uint16_t handle, esp_gatt_status_t status
) {
    bool wake = false;
    bool retryable = gatt_status_is_retryable(status);
    uint32_t retry_after_ms =
        retryable ? now_ms() + GATT_OUTPUT_RETRY_MS : 0;
    portENTER_CRITICAL(&s_gatt_output_mux);
    if (status == ESP_GATT_OK) {
        s_gatt_output_metrics.completed++;
    } else if (retryable) {
        s_gatt_output_metrics.busy++;
    } else {
        s_gatt_output_metrics.failed++;
    }
    if (ch >= 0 && ch < MAX_CH) {
        gatt_output_state_t *state = &s_gatt_output[ch];
        if (
            state->in_flight && state->handle == handle &&
            s_ch[ch].used && s_ch[ch].link_open &&
            s_ch[ch].conn_id == conn_id &&
            state->generation == s_ch[ch].generation
        ) {
            uint32_t completed_sequence = state->in_flight_sequence;
            state->in_flight = false;
            if (status == ESP_GATT_OK) {
                state->retry_attempts = 0;
                state->retry_pending = false;
                state->retry_after_ms = 0;
                if (state->sequence == completed_sequence)
                    state->pending = false;
            } else if (
                retryable &&
                state->retry_attempts < GATT_OUTPUT_MAX_RETRIES
            ) {
                state->retry_attempts++;
                state->retry_pending = true;
                state->retry_after_ms = retry_after_ms;
            } else {
                if (state->sequence == completed_sequence)
                    state->pending = false;
                state->retry_pending = false;
                state->retry_after_ms = 0;
                if (retryable) s_gatt_output_metrics.failed++;
            }
            wake = state->pending;
        }
    }
    portEXIT_CRITICAL(&s_gatt_output_mux);
    if (wake && s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
}

static void note_gatt_congestion(
    int ch, uint16_t conn_id, uint8_t flag, bool blocked
) {
    if (ch < 0 || ch >= MAX_CH) return;
    bool wake = false;
    portENTER_CRITICAL(&s_gatt_output_mux);
    gatt_output_state_t *state = &s_gatt_output[ch];
    if (
        !s_ch[ch].used || !s_ch[ch].link_open ||
        s_ch[ch].conn_id != conn_id ||
        state->generation != s_ch[ch].generation
    ) {
        portEXIT_CRITICAL(&s_gatt_output_mux);
        return;
    }
    if (blocked) {
        state->block_flags |= flag;
        s_gatt_output_metrics.busy++;
        if (flag == GATT_OUTPUT_BLOCK_CONGESTED)
            s_gatt_output_metrics.congestion_events++;
        if (flag == GATT_OUTPUT_BLOCK_QUEUE_FULL)
            s_gatt_output_metrics.queue_full_events++;
    } else {
        state->block_flags &= (uint8_t)~flag;
        wake = state->pending && !state->in_flight && !state->block_flags;
    }
    portEXIT_CRITICAL(&s_gatt_output_mux);
    if (wake && s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
}

// --- Jitter Buffer (FIFO) for Audio Haptics ---
#define RUMBLE_QUEUE_SIZE 5
typedef struct {
    int ch;
    uint32_t generation;
    uint8_t data[64];
    size_t len;
} rumble_pkt_t;

static QueueHandle_t s_rumble_queue;
static TaskHandle_t s_rumble_task_h;

static void rumble_playout_task(void *arg) {
    rumble_pkt_t pkt;
    while (1) {
        if (xQueueReceive(s_rumble_queue, &pkt, portMAX_DELAY)) {
            if (
                pkt.ch >= 0 && pkt.ch < MAX_CH &&
                s_ch[pkt.ch].used &&
                s_ch[pkt.ch].generation == pkt.generation &&
                s_ch[pkt.ch].rumble_handle
            ) {
                queue_latest_rumble_write(pkt.ch, pkt.data, pkt.len);
            }
            // Strict 15ms minimum gap between packets as requested
            vTaskDelay(pdMS_TO_TICKS(15));
        }
    }
}

static bool cdc_host_ready(void) {
    return tud_cdc_connected() && (tud_cdc_get_line_state() & CDC_LINE_STATE_DTR);
}
static void cdc_tx_reset(void) {
    s_cdc_tx.length = 0;
    s_cdc_tx.offset = 0;
    s_cdc_tx.valid = false;
}
static bool cdc_tx_submit(const uint8_t *data, size_t len) {
    if (!data || len == 0) return true;
    if (!cdc_host_ready()) {
        cdc_tx_reset();
        return true;  // Preserve the historical closed-CDC drop policy.
    }
    if (s_cdc_tx.valid || len > sizeof(s_cdc_tx.data)) return false;
    memcpy(s_cdc_tx.data, data, len);
    s_cdc_tx.length = len;
    s_cdc_tx.offset = 0;
    s_cdc_tx.valid = true;
    return true;
}
static bool cdc_tx_pump_until(int64_t deadline_us) {
    if (!s_cdc_tx.valid) return true;
    if (!cdc_host_ready()) {
        cdc_tx_reset();
        return true;
    }
    while (
        s_cdc_tx.valid &&
        esp_timer_get_time() < deadline_us
    ) {
        uint32_t avail = tud_cdc_write_available();
        if (avail == 0) return false;
        size_t remaining = s_cdc_tx.length - s_cdc_tx.offset;
        uint32_t request =
            remaining > avail ? avail : (uint32_t)remaining;
        uint32_t written = tud_cdc_write(
            s_cdc_tx.data + s_cdc_tx.offset, request
        );
        if (written == 0) return false;
        s_cdc_tx.offset += written;
        tud_cdc_write_flush();
        if (s_cdc_tx.offset >= s_cdc_tx.length) {
            cdc_tx_reset();
            return true;
        }
    }
    return !s_cdc_tx.valid;
}
static bool cdc_tx_can_submit(int64_t deadline_us) {
    if (!cdc_tx_pump_until(deadline_us)) return false;
    return esp_timer_get_time() < deadline_us;
}
static void send_json(const char *s) {
    if (!cdc_tx_submit((const uint8_t *)s, strlen(s)))
        ESP_LOGW(TAG, "CDC TX busy while submitting JSON");
}
// Queue JSON for cdc_task. BLE callbacks never touch TinyUSB or wait for a slow
// host. Important lifecycle events have a dedicated queue, so scan/debug floods
// cannot consume their capacity.
static bool queue_json(QueueHandle_t queue, const char *s) {
    if (!queue) return false;
    line_t L; strncpy(L.text, s, sizeof(L.text) - 1); L.text[sizeof(L.text) - 1] = '\0';
    if (xQueueSend(queue, &L, 0) != pdTRUE) return false;
    if (s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
    return true;
}
static void out_json(const char *s) {
    (void)queue_json(s_out_queue, s);
}
static void out_event(const char *s) {
    if (queue_json(s_event_queue, s)) return;
    __atomic_add_fetch(&s_event_queue_drops, 1u, __ATOMIC_RELAXED);
    ESP_LOGW(TAG, "critical event queue full");
}
static void out_debug(const char *msg) {
    char b[200];
    snprintf(b, sizeof(b), "{\"cmd\":\"debug\",\"msg\":\"%.172s\"}\n", msg);
    out_json(b);
}
static uint32_t next_channel_generation(void) {
    uint32_t generation = __atomic_add_fetch(
        &s_generation_counter, 1u, __ATOMIC_RELAXED
    );
    if (generation == 0) {
        generation = __atomic_add_fetch(
            &s_generation_counter, 1u, __ATOMIC_RELAXED
        );
    }
    return generation;
}
static void clear_channel_state(int ch) {
    if (ch < 0 || ch >= MAX_CH) return;
    esp_gatt_if_t keep = s_ch[ch].gattc_if;
    uint32_t generation = next_channel_generation();
    portENTER_CRITICAL(&s_in_mux);
    s_in_dirty[ch] = false;
    memset(&s_in_shadow[ch], 0, sizeof(s_in_shadow[ch]));
    s_last_input_report_time[ch] = 0;
    s_last_input_report_time_valid[ch] = false;
    portEXIT_CRITICAL(&s_in_mux);
    reset_gatt_output_channel(ch, generation);
    memset(&s_ch[ch], 0, sizeof(s_ch[ch]));
    s_ch[ch].gattc_if = keep;
    s_ch[ch].generation = generation;
}
static bool standalone_has_link(void) {
    if (!s_standalone_mode) return false;
    for (int i = 0; i < MAX_CH; i++) {
        if (s_ch[i].used) return true;
    }
    return false;
}
static bool request_scan_start(void) {
    if (
        !s_scan_mode || !s_scan_params_ready || s_scanning ||
        s_scan_start_pending || s_scan_stop_pending ||
        s_scan_stop_needed || standalone_has_link()
    ) {
        return false;
    }
    esp_err_t err = esp_ble_gap_start_scanning(0);
    if (err == ESP_OK) {
        s_scan_start_pending = true;
        return true;
    }
    char dbg[96];
    snprintf(
        dbg, sizeof(dbg), "scan start request failed: %s",
        esp_err_to_name(err)
    );
    out_debug(dbg);
    s_resume_scan = true;
    s_scan_retry_after_ms = now_ms() + 250u;
    return false;
}
static bool request_scan_stop(void) {
    s_scan_stop_needed = true;
    if (s_scan_stop_pending) return true;
    if (!s_scanning && !s_scan_start_pending) {
        s_scan_stop_needed = false;
        return true;
    }
    esp_err_t err = esp_ble_gap_stop_scanning();
    if (err == ESP_OK) {
        s_scan_stop_pending = true;
        return true;
    }
    char dbg[96];
    snprintf(
        dbg, sizeof(dbg), "scan stop request failed: %s",
        esp_err_to_name(err)
    );
    out_debug(dbg);
    s_scan_retry_after_ms = now_ms() + 250u;
    return false;
}
static size_t parse_hex(const char *s, uint8_t *out, size_t max);
static const char *output_mode_name(standalone_output_mode_t mode) {
    if (mode == STANDALONE_OUTPUT_XINPUT) return "standalone";
    if (mode == STANDALONE_OUTPUT_HID) return "standalone_hid";
    if (mode == STANDALONE_OUTPUT_AUTO) return "standalone_auto";
    return "bridge";
}
static void send_status_response(void) {
    char b[512];
    snprintf(b, sizeof(b),
        "{\"cmd\":\"status\",\"product\":\"%s\",\"version\":\"%s\","
        "\"protocol\":\"%s\",\"protocol_version\":\"%s\","
        "\"profile\":\"%s\",\"build\":\"%s\","
        "\"ble_channels\":%u,\"mac\":\"%s\","
        "\"event_queue_drops\":%lu,"
        "\"features\":{\"wrpair\":1,\"shadow\":1,"
        "\"diagnostics\":1,\"rumble_diagnostics\":1,"
        "\"standalone_profile_write\":1,\"standalone_profile_runtime\":1,"
        "\"standalone_usb_xinput\":1,"
        "\"standalone_usb_hid\":1,"
        "\"standalone_usb_auto\":1,"
        "\"standalone_ble_hid\":0},\"profile_schemas\":[1]}\n",
        APP_FIRMWARE_PRODUCT, APP_FIRMWARE_VERSION,
        APP_PROTOCOL_NAME, APP_PROTOCOL_VERSION,
        EXPECTED_FIRMWARE_PROFILE, EXPECTED_FIRMWARE_BUILD,
        (unsigned)ch_active_mask(), s_own_mac,
        (unsigned long)__atomic_load_n(
            &s_event_queue_drops, __ATOMIC_RELAXED
        ));
    send_json(b);
}

static void send_capabilities_response(void) {
    char b[560];
    snprintf(b, sizeof(b),
        "{\"cmd\":\"capabilities\",\"ok\":1,\"product\":\"%s\","
        "\"version\":\"%s\",\"protocol\":\"%s\","
        "\"protocol_version\":\"%s\","
        "\"mode\":\"%s\",\"active_mode\":\"%s\","
        "\"features\":{\"bridge\":1,"
        "\"diagnostics\":1,\"rumble_diagnostics\":1,"
        "\"standalone_profile_write\":1,\"standalone_profile_runtime\":1,"
        "\"standalone_usb_xinput\":1,"
        "\"standalone_usb_hid\":1,"
        "\"standalone_usb_auto\":1,"
        "\"standalone_ble_hid\":0},\"profile_schemas\":[%u],"
        "\"profile_max_bytes\":%u}\n",
        APP_FIRMWARE_PRODUCT, APP_FIRMWARE_VERSION,
        APP_PROTOCOL_NAME, APP_PROTOCOL_VERSION,
        output_mode_name(s_output_mode),
        output_mode_name(s_active_output_mode),
        (unsigned)STANDALONE_PROFILE_SCHEMA,
        (unsigned)STANDALONE_PROFILE_MAX);
    send_json(b);
}

static void do_mode_command(const char *mode) {
    standalone_output_mode_t output_mode;
    if (strcmp(mode, "standalone") == 0)
        output_mode = STANDALONE_OUTPUT_XINPUT;
    else if (strcmp(mode, "standalone_hid") == 0)
        output_mode = STANDALONE_OUTPUT_HID;
    else if (strcmp(mode, "standalone_auto") == 0)
        output_mode = STANDALONE_OUTPUT_AUTO;
    else if (strcmp(mode, "bridge") == 0)
        output_mode = STANDALONE_OUTPUT_BRIDGE;
    else {
        send_json("{\"cmd\":\"mode\",\"ok\":0,\"error\":\"value\"}\n");
        return;
    }
    esp_err_t err = standalone_output_mode_store(output_mode);
    if (err != ESP_OK) {
        char b[112];
        snprintf(b, sizeof(b),
            "{\"cmd\":\"mode\",\"ok\":0,\"error\":\"nvs_%s\"}\n",
            esp_err_to_name(err));
        send_json(b);
        return;
    }
    char b[144];
    snprintf(b, sizeof(b),
        "{\"cmd\":\"mode\",\"ok\":1,\"mode\":\"%s\","
        "\"restart_required\":%d}\n",
        output_mode_name(output_mode),
        output_mode != s_output_mode ? 1 : 0);
    send_json(b);
}

static void do_restart_command(void) {
    send_json("{\"cmd\":\"restart\",\"ok\":1}\n");
    int64_t deadline_us = esp_timer_get_time() + 100000;
    while (
        s_cdc_tx.valid &&
        esp_timer_get_time() < deadline_us
    ) {
        if (!cdc_tx_pump_until(deadline_us))
            vTaskDelay(pdMS_TO_TICKS(1));
    }
    vTaskDelay(pdMS_TO_TICKS(20));
    esp_restart();
}

// CDC frame: 0xaa 0x55 <len=payload+1> <chan|0x80 if cmd> <payload...>
static void send_report_frame(uint8_t channel, const uint8_t *payload, uint8_t plen, bool is_cmd) {
    if (plen > REPORT_SIZE) plen = REPORT_SIZE;
    uint8_t frame[4 + REPORT_SIZE] = {
        0xaa, 0x55, (uint8_t)(plen + 1),
        (uint8_t)((channel + 1) | (is_cmd ? 0x80 : 0x00))
    };
    memcpy(frame + 4, payload, plen);
    if (!cdc_tx_submit(frame, 4 + plen))
        ESP_LOGW(TAG, "CDC TX busy while submitting report frame");
}
// CDC v2 notify frame: 0xaa 0x55 <len=payload+3> <0x40|chan> <handle_le16> <payload...>
static void send_notify_handle_frame(uint8_t channel, uint16_t handle, const uint8_t *payload, uint8_t plen) {
    if (plen > REPORT_SIZE) plen = REPORT_SIZE;
    uint8_t frame[6 + REPORT_SIZE] = {
        0xaa, 0x55, (uint8_t)(plen + 3), (uint8_t)(0x40 | (channel + 1)),
        (uint8_t)(handle & 0xff), (uint8_t)(handle >> 8)
    };
    memcpy(frame + 6, payload, plen);
    if (!cdc_tx_submit(frame, 6 + plen))
        ESP_LOGW(TAG, "CDC TX busy while submitting notify frame");
}
static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void uuid_to_str(const esp_bt_uuid_t *uuid, char *out, size_t out_len) {
    if (!uuid || !out || out_len == 0) return;
    if (uuid->len == ESP_UUID_LEN_16) {
        snprintf(out, out_len, "%04x", uuid->uuid.uuid16);
        return;
    }
    if (uuid->len == ESP_UUID_LEN_128) {
        const uint8_t *u = uuid->uuid.uuid128;
        snprintf(out, out_len,
                 "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
                 u[15], u[14], u[13], u[12], u[11], u[10], u[9], u[8],
                 u[7], u[6], u[5], u[4], u[3], u[2], u[1], u[0]);
        return;
    }
    snprintf(out, out_len, "unknown");
}
static size_t parse_hex(const char *s, uint8_t *out, size_t max) {
    size_t n = 0;
    while (s[0] && s[1] && n < max) {
        int hi = hexval(s[0]), lo = hexval(s[1]);
        if (hi < 0 || lo < 0) break;
        out[n++] = (uint8_t)((hi << 4) | lo); s += 2;
    }
    return n;
}

static bool write_cccd_value(int ch, uint16_t handle, bool enable) {
    if (ch < 0 || ch >= MAX_CH || !s_ch[ch].used || handle == 0) return false;
    esp_gattc_descr_elem_t descr;
    uint16_t got = 1;
    if (esp_ble_gattc_get_descr_by_char_handle(s_ch[ch].gattc_if, s_ch[ch].conn_id,
                                                handle, UUID_CCCD, &descr, &got) == ESP_OK && got > 0) {
        uint8_t v[2] = { enable ? 0x01 : 0x00, 0x00 };
        if (enable && handle == s_ch[ch].input_handle)
            s_ch[ch].input_cccd_handle = descr.handle;
        esp_err_t err = esp_ble_gattc_write_char_descr(s_ch[ch].gattc_if, s_ch[ch].conn_id,
                                                       descr.handle, sizeof(v), v,
                                                       ESP_GATT_WRITE_TYPE_RSP, ESP_GATT_AUTH_REQ_NONE);
        if (
            err != ESP_OK &&
            s_ch[ch].input_cccd_handle == descr.handle
        ) {
            s_ch[ch].input_cccd_handle = 0;
        }
        return err == ESP_OK;
    }
    return false;
}

static uint16_t choose_input_handle(int ch, bool prefer_legacy, uint8_t *src) {
    if (src) *src = 0;
    if (ch < 0 || ch >= MAX_CH) return 0;
    if (prefer_legacy && s_ch[ch].legacy_handle) {
        if (src) *src = 2;
        return s_ch[ch].legacy_handle;
    }
    if (!prefer_legacy && s_ch[ch].fd2_handle) {
        if (src) *src = 1;
        return s_ch[ch].fd2_handle;
    }
    if (s_ch[ch].fd2_handle) {
        if (src) *src = 1;
        return s_ch[ch].fd2_handle;
    }
    if (s_ch[ch].legacy_handle) {
        if (src) *src = 2;
        return s_ch[ch].legacy_handle;
    }
    return 0;
}

// --- command handlers (cdc_task context) ---
static void open_pending_conn(void);
static void do_conn(char *args, bool standalone_pair_required) {
    char *save = NULL;
    char *type_s = strtok_r(args, " ", &save);
    char *mac_s  = strtok_r(NULL, " ", &save);
    if (!type_s || !mac_s) return;
    unsigned m[6];
    if (sscanf(mac_s, "%x:%x:%x:%x:%x:%x", &m[0],&m[1],&m[2],&m[3],&m[4],&m[5]) != 6) return;
    int ch = ch_alloc();
    if (ch < 0) { out_debug("conn req REJECTED: no free channel slot"); return; }
    int u0, r0; ch_count(&u0, &r0);
    clear_channel_state(ch);
    s_ch[ch].used = true;          // reserve the slot
    s_ch[ch].ready = false;
    s_ch[ch].connecting = true;
    s_ch[ch].link_open = false;
    s_ch[ch].connect_deadline_ms = now_ms() + 12000u;
    s_ch[ch].addr_type = (uint8_t)atoi(type_s);
    for (int i = 0; i < 6; i++) s_ch[ch].bda[i] = (uint8_t)m[i];
    s_ch[ch].prefer_legacy = false;
    s_ch[ch].notify_count = 0;      // fresh discovery — clear the GCN notify list / drain state
    s_ch[ch].cccd_idx = 0;
    s_ch[ch].cccd_draining = false;
    s_ch[ch].standalone_init_step = 0;
    s_ch[ch].standalone_init_attempts = 0;
    s_ch[ch].standalone_init_waiting = false;
    s_ch[ch].standalone_init_next_ms = 0;
    s_ch[ch].standalone_pair_required =
        s_standalone_mode && standalone_pair_required;
    s_ch[ch].standalone_pair_step = 0;
    s_ch[ch].standalone_pair_attempts = 0;
    s_ch[ch].standalone_pair_waiting = false;
    s_ch[ch].standalone_pair_next_ms = 0;
    s_ch[ch].standalone_feedback_step = 0;
    s_ch[ch].standalone_feedback_active = false;
    s_ch[ch].standalone_feedback_next_ms = 0;
    {   // Diagnostic: which channel + how many links already live when this starts.
        char dbg[110];
        snprintf(dbg, sizeof(dbg),
            "conn req ch=%d mac=%02X:%02X:%02X:%02X:%02X:%02X type=%d (before: used=%d ready=%d)",
            ch, s_ch[ch].bda[0],s_ch[ch].bda[1],s_ch[ch].bda[2],
            s_ch[ch].bda[3],s_ch[ch].bda[4],s_ch[ch].bda[5], s_ch[ch].addr_type, u0, r0);
        out_debug(dbg);
    }
    // If two links are already live, the radio is saturated by their 7.5ms anchors and
    // the controller cannot schedule a 3rd connection's SETUP at all (CONN_CANCEL).
    // Temporarily widen those links to 15ms NOW to free radio time, and defer the 3rd
    // open until the widen has settled (the conn-param update takes effect a few
    // intervals later).  The widened links are restored to 7.5ms once the 3rd connects.
    // If two links are already live, the radio is saturated by their 7.5ms anchors and
    // the controller cannot schedule a 3rd connection's SETUP at all (CONN_CANCEL).
    // Temporarily widen those links to 15ms NOW to free radio time, and defer the 3rd
    // open until the widen has settled (the conn-param update takes effect a few
    // intervals later).  The widened links are restored once the 3rd connects.
    s_widened_mask = 0;
    s_conn_open_after = 0;
    if (r0 >= 2) {
        for (int i = 0; i < MAX_CH; i++) {
            if (i != ch && s_ch[i].used && s_ch[i].ready && s_ch[i].itvl != 12) {
                esp_ble_conn_update_params_t cp = {0};
                memcpy(cp.bda, s_ch[i].bda, sizeof(esp_bd_addr_t));
                cp.min_int = 12; cp.max_int = 12; cp.latency = 0; cp.timeout = 400;
                esp_ble_gap_update_conn_params(&cp);
                s_widened_mask |= (1u << i);
            }
        }
        /*
         * Reset the source-gap baseline immediately. Do not depend on a
         * report arriving during the widen window before the mask is cleared.
         */
        portENTER_CRITICAL(&s_in_mux);
        for (int i = 0; i < MAX_CH; i++) {
            if (s_widened_mask & (1u << i)) {
                s_last_input_report_time[i] = 0;
                s_last_input_report_time_valid[i] = false;
            }
        }
        portEXIT_CRITICAL(&s_in_mux);
        s_conn_open_after = now_ms() + 250;   // let the widen settle before opening
        gap_busy(700);
        char dbg[80];
        snprintf(dbg, sizeof(dbg), "3rd link: widened mask=0x%02x to 15ms, defer open 250ms",
                 s_widened_mask);
        out_debug(dbg);
    } else {
        gap_busy(300);
    }
    // Can't initiate while the scanner runs (ESP_GATT_CONGESTED). Stop the scan; the open
    // happens in SCAN_STOP_COMPLETE_EVT (immediate) or, for the deferred 3rd-link case,
    // from cdc_task once s_conn_open_after elapses.
    s_pending_conn = ch;
    bool stop_requested = request_scan_stop();
    if (
        stop_requested && !s_scan_stop_pending &&
        s_conn_open_after == 0
    ) {
        open_pending_conn();
    } else if (!stop_requested && s_cdc_task_h) {
        xTaskNotifyGive(s_cdc_task_h);
    }
}

static void do_inputsrc(char *args) {  // inputsrc <ch> <fd2|legacy>
    char *save = NULL;
    char *ch_s = strtok_r(args, " ", &save);
    char *mode_s = strtok_r(NULL, " ", &save);
    if (!ch_s || !mode_s) return;
    int ch = atoi(ch_s);
    if (ch < 0 || ch >= MAX_CH || !s_ch[ch].used) return;

    bool prefer_legacy;
    if (strcmp(mode_s, "legacy") == 0) {
        prefer_legacy = true;
    } else if (strcmp(mode_s, "fd2") == 0) {
        prefer_legacy = false;
    } else {
        return;
    }

    s_ch[ch].prefer_legacy = prefer_legacy;
    uint8_t new_src = 0;
    uint16_t new_handle = choose_input_handle(ch, prefer_legacy, &new_src);
    if (!new_handle) {
        char dbg[96];
        snprintf(dbg, sizeof(dbg), "inputsrc ch=%d mode=%s pending (fd2=0x%04x legacy=0x%04x)",
                 ch, mode_s, s_ch[ch].fd2_handle, s_ch[ch].legacy_handle);
        out_debug(dbg);
        return;
    }

    // Retarget which stream we forward.  Do NOT disable the old handle's CCCD: for the GCN we
    // deliberately keep EVERY SW2 notify CCCD enabled (see enable_notifications), and other
    // controllers keep FD2 enabled — disabling here would fight that and can silently kill input.
    // Register the new handle (idempotent) as a safety net for a switch to a not-yet-enabled char;
    // for non-GCN, REG_FOR_NOTIFY_EVT then writes its CCCD. (Host no longer calls this; kept for
    // backward compatibility.)
    if (s_ch[ch].input_handle != new_handle)
        esp_ble_gattc_register_for_notify(s_ch[ch].gattc_if, s_ch[ch].bda, new_handle);
    s_ch[ch].input_handle = new_handle;
    s_ch[ch].input_src = new_src;

    char dbg[128];
    snprintf(dbg, sizeof(dbg), "inputsrc ch=%d mode=%s input=0x%04x(src=%u prefer_legacy=%d)",
             ch, mode_s, s_ch[ch].input_handle, s_ch[ch].input_src, s_ch[ch].prefer_legacy);
    out_debug(dbg);
}
// Steady-state interval policy.  This controller CANNOT sustain two 7.5ms links plus a
// third, so: 3+ established links all run at 15ms (itvl=12); with <=2 links everyone runs
// at 7.5ms (itvl=6).  Called after a link becomes ready and after a disconnect.  Bails
// while a connection is being established (the temporary widen owns that window) so it
// never fights the setup.  Each link's itvl is updated BEFORE the request so a failed
// request is not retried (no loop).
static void reconcile_intervals(void) {
    if (s_pending_conn >= 0 || s_widened_mask) return;
    for (int i = 0; i < MAX_CH; i++) if (s_ch[i].connecting) return;
    int ready = 0;
    for (int i = 0; i < MAX_CH; i++) if (s_ch[i].used && s_ch[i].ready) ready++;
    uint16_t target = (ready >= 3) ? 12 : 6;
    for (int i = 0; i < MAX_CH; i++) {
        if (s_ch[i].used && s_ch[i].ready && s_ch[i].itvl != target) {
            s_ch[i].itvl = target;
            esp_ble_conn_update_params_t cp = {0};
            memcpy(cp.bda, s_ch[i].bda, sizeof(esp_bd_addr_t));
            cp.min_int = target; cp.max_int = target; cp.latency = 0; cp.timeout = 400;
            esp_ble_gap_update_conn_params(&cp);
        }
    }
    char d[48]; snprintf(d, sizeof(d), "reconcile: %d links -> itvl=%d", ready, target);
    out_debug(d);
}
static void restore_widened_links(void);   // forward decl (defined just below)
// Open the pending connection (s_pending_conn).  Scan must already be stopped.  Called
// from SCAN_STOP_COMPLETE_EVT (immediate, 1st/2nd link) and from cdc_task (deferred
// 3rd-link case, after the existing links were widened to 15ms and that has settled).
static void open_pending_conn(void) {
    int ch = s_pending_conn;
    if (ch < 0) return;
    s_pending_conn = -1;
    s_conn_open_after = 0;
    if (!s_ch[ch].used) return;   // slot was cleared meanwhile (e.g. ble disconnect)

    int other_ready = 0;
    for (int i = 0; i < MAX_CH; i++)
        if (i != ch && s_ch[i].used && s_ch[i].ready) other_ready++;

    // 3rd+ link runs at 15ms (itvl=12); first two stay 7.5ms for gap-free rumble.
    // (enh_open + ce_len does NOT work on this chip — see v0.12.16; plain gattc_open.)
    s_ch[ch].itvl = (other_ready >= 2) ? 12 : 6;
    esp_err_t pc = esp_ble_gap_set_prefer_conn_params(s_ch[ch].bda,
                        s_ch[ch].itvl, s_ch[ch].itvl, 0, 400);
    esp_err_t oc = esp_ble_gattc_open(s_ch[ch].gattc_if, s_ch[ch].bda,
                        s_ch[ch].addr_type, true);
    int u1, r1; ch_count(&u1, &r1);
    char dbg[150];
    snprintf(dbg, sizeof(dbg),
        "open ch=%d itvl=%d type=%d set_prefer=%s gattc_open=%s (used=%d ready=%d other_ready=%d widened=0x%02x)",
        ch, s_ch[ch].itvl, s_ch[ch].addr_type,
        esp_err_to_name(pc), esp_err_to_name(oc), u1, r1, other_ready, s_widened_mask);
    out_debug(dbg);

    // If the open failed to even start, there will be no OPEN/DISCONNECT event to
    // restore from — undo the widen now so the existing links aren't left at 15ms.
    if (oc != ESP_OK) {
        char fail[100];
        snprintf(fail, sizeof(fail),
            "{\"cmd\":\"connect_fail\",\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}\n",
            s_ch[ch].bda[0],s_ch[ch].bda[1],s_ch[ch].bda[2],
            s_ch[ch].bda[3],s_ch[ch].bda[4],s_ch[ch].bda[5]);
        out_event(fail);
        clear_channel_state(ch);
        restore_widened_links();
        if (s_scan_mode) s_resume_scan = true;
    }
}
// End the temporary widen (3rd-link setup window) and apply the steady-state interval
// policy.  On SUCCESS (now 3 links) reconcile keeps everyone at 15ms; on ABORT (back to
// <=2 links) reconcile restores everyone to 7.5ms.  Single source of truth = reconcile.
static void restore_widened_links(void) {
    s_widened_mask = 0;
    reconcile_intervals();
}
static void do_disc(char *args) {
    int ch = atoi(args);
    if (ch >= 0 && ch < MAX_CH && s_ch[ch].used) {
        portENTER_CRITICAL(&s_disc_mux);
        s_disc_mask |= (1u << ch);
        portEXIT_CRITICAL(&s_disc_mux);
        kick_disc_queue();
    }
}
static void do_disc_all(void) {  // "ble disconnect": drop every live link (clear stale state)
    // If the scan was stopped for a pending conn but gattc_open hasn't fired yet, cancel it
    // so SCAN_STOP_COMPLETE doesn't open a connection we're about to discard anyway.
    if (s_pending_conn >= 0) {
        int pc = s_pending_conn; s_pending_conn = -1;
        clear_channel_state(pc);
    }
    // Enqueue every live channel for sequential disconnection.
    // Do NOT call gap_disconnect here — parallel disconnects corrupt the BLE
    // controller's HCI state and cause crashes (status=133 flooding → assert).
    // Scanning will resume automatically once the queue drains.
    s_resume_scan = false;  // kick_disc_queue will set this when done
    portENTER_CRITICAL(&s_disc_mux);
    for (int i = 0; i < MAX_CH; i++)
        if (s_ch[i].used) s_disc_mask |= (1u << i);
    portEXIT_CRITICAL(&s_disc_mux);
    kick_disc_queue();  // start first disconnect; rest follow via DISCONNECT_EVT
}
static void do_wr(char *args) {  // wr <ch> <c|r> <hex>
    char *save = NULL;
    char *ch_s = strtok_r(args, " ", &save);
    char *k_s  = strtok_r(NULL, " ", &save);
    char *h_s  = strtok_r(NULL, " ", &save);
    if (!ch_s || !k_s || !h_s) return;
    int ch = atoi(ch_s);
    if (ch < 0 || ch >= MAX_CH || !s_ch[ch].used) return;
    uint8_t buf[96]; size_t len = parse_hex(h_s, buf, sizeof(buf));
    if (len == 0) return;
    uint16_t handle = (k_s[0] == 'c') ? s_ch[ch].cmd_handle : s_ch[ch].rumble_handle;
    if (handle == 0) return;
    if (k_s[0] == 'r')
        queue_latest_rumble_write(ch, buf, len);
    else
        write_gatt_char_checked(ch, handle, buf, len);
}
static void do_rs(char *args) {  // rs <ch> <hex>
    char *save = NULL;
    char *ch_s = strtok_r(args, " ", &save);
    char *h_s  = strtok_r(NULL, " ", &save);
    if (!ch_s || !h_s) return;
    int ch = atoi(ch_s);
    if (ch < 0 || ch >= MAX_CH || !s_ch[ch].used || s_ch[ch].rumble_handle == 0) return;

    rumble_pkt_t pkt;
    pkt.ch = ch;
    pkt.generation = s_ch[ch].generation;
    pkt.len = parse_hex(h_s, pkt.data, sizeof(pkt.data));
    if (pkt.len == 0) return;

    if (s_rumble_queue) {
        if (xQueueSend(s_rumble_queue, &pkt, 0) != pdTRUE) {
            rumble_pkt_t dummy;
            xQueueReceive(s_rumble_queue, &dummy, 0); // Drop oldest
            xQueueSend(s_rumble_queue, &pkt, 0);      // Push newest
        }
    }
}
static bool wr_one(
    int ch, char kind, const uint8_t *buf, size_t len
) {
    if (ch < 0 || ch >= MAX_CH || !s_ch[ch].used || len == 0)
        return false;
    uint16_t handle = (kind == 'c') ? s_ch[ch].cmd_handle : s_ch[ch].rumble_handle;
    if (handle == 0) return false;
    if (kind == 'r') return queue_latest_rumble_write(ch, buf, len);
    return write_gatt_char_checked(ch, handle, buf, len);
}

static bool write_switch_command(
    int ch,
    uint8_t command_id,
    uint8_t subcommand_id,
    const uint8_t *command_data,
    uint8_t data_len
) {
    uint8_t payload[29] = {0};
    payload[0] = command_id;
    payload[1] = 0x91;
    payload[2] = 0x01;
    payload[3] = subcommand_id;
    payload[4] = 0x00;
    payload[5] = data_len;
    payload[6] = 0x00;
    payload[7] = 0x00;
    if (data_len && command_data)
        memcpy(payload + 8, command_data, data_len);
    return wr_one(ch, 'c', payload, 8u + data_len);
}

static void do_wrpair(char *args) {  // wrpair <ch_l> <ch_r> <kind> <hex_l> <hex_r>
    char *s = NULL;
    char *cl = strtok_r(args, " ", &s), *cr = strtok_r(NULL, " ", &s);
    char *k  = strtok_r(NULL, " ", &s);
    char *hl = strtok_r(NULL, " ", &s), *hr = strtok_r(NULL, " ", &s);
    if (!cl || !cr || !k || !hl || !hr) return;
    uint8_t bl[96], br[96];
    wr_one(atoi(cl), k[0], bl, parse_hex(hl, bl, sizeof(bl)));
    wr_one(atoi(cr), k[0], br, parse_hex(hr, br, sizeof(br)));
}

static uint8_t s_standalone_rumble_packet_id;
static uint8_t s_standalone_large_motor;
static uint8_t s_standalone_small_motor;
static uint32_t s_standalone_rumble_next_ms;
static uint8_t s_standalone_zero_flush;
static uint32_t s_standalone_rumble_received;
static uint32_t s_standalone_rumble_sent;
static uint8_t s_standalone_rumble_peak_large;
static uint8_t s_standalone_rumble_peak_small;
static uint16_t s_standalone_rumble_lf_frequency;
static uint16_t s_standalone_rumble_lf_amplitude;
static uint16_t s_standalone_rumble_hf_frequency;
static uint16_t s_standalone_rumble_hf_amplitude;
static uint16_t s_standalone_rumble_peak_lf_amplitude;
static uint16_t s_standalone_rumble_peak_hf_amplitude;

#define STANDALONE_RUMBLE_REFRESH_MS 16u
#define STANDALONE_RUMBLE_ZERO_FLUSH_COUNT 3u
#define STANDALONE_FEEDBACK_LF_FREQUENCY 225u
#define STANDALONE_FEEDBACK_HF_FREQUENCY 481u
#define STANDALONE_FEEDBACK_AMPLITUDE 800u

static void reset_standalone_rumble_metrics(void) {
    s_standalone_rumble_received = 0;
    s_standalone_rumble_sent = 0;
    s_standalone_rumble_peak_large = s_standalone_large_motor;
    s_standalone_rumble_peak_small = s_standalone_small_motor;
    s_standalone_rumble_peak_lf_amplitude =
        s_standalone_rumble_lf_amplitude;
    s_standalone_rumble_peak_hf_amplitude =
        s_standalone_rumble_hf_amplitude;
    reset_gatt_output_metrics();
}

static void format_standalone_rumble_metrics(char *output, size_t size) {
    gatt_output_metrics_t gatt;
    uint8_t pending = 0;
    get_gatt_output_metrics(&gatt, &pending);
    snprintf(
        output, size,
        "{\"cmd\":\"rumble_status\",\"ok\":1,"
        "\"received\":%lu,\"sent\":%lu,"
        "\"input\":[%u,%u],\"peak_input\":[%u,%u],"
        "\"frequency\":[%u,%u],\"output\":[%u,%u],"
        "\"peak_output\":[%u,%u],\"zero_flush\":%u,"
        "\"gatt_submitted\":%lu,\"gatt_overwritten\":%lu,"
        "\"gatt_accepted\":%lu,\"gatt_completed\":%lu,"
        "\"gatt_busy\":%lu,\"gatt_failed\":%lu,"
        "\"gatt_retries\":%lu,\"gatt_congestion\":%lu,"
        "\"gatt_queue_full\":%lu,\"gatt_pending\":%u}\n",
        (unsigned long)s_standalone_rumble_received,
        (unsigned long)s_standalone_rumble_sent,
        (unsigned)s_standalone_large_motor,
        (unsigned)s_standalone_small_motor,
        (unsigned)s_standalone_rumble_peak_large,
        (unsigned)s_standalone_rumble_peak_small,
        (unsigned)s_standalone_rumble_lf_frequency,
        (unsigned)s_standalone_rumble_hf_frequency,
        (unsigned)s_standalone_rumble_lf_amplitude,
        (unsigned)s_standalone_rumble_hf_amplitude,
        (unsigned)s_standalone_rumble_peak_lf_amplitude,
        (unsigned)s_standalone_rumble_peak_hf_amplitude,
        (unsigned)s_standalone_zero_flush,
        (unsigned long)gatt.submitted,
        (unsigned long)gatt.overwritten,
        (unsigned long)gatt.accepted,
        (unsigned long)gatt.completed,
        (unsigned long)gatt.busy,
        (unsigned long)gatt.failed,
        (unsigned long)gatt.retries,
        (unsigned long)gatt.congestion_events,
        (unsigned long)gatt.queue_full_events,
        (unsigned)pending
    );
}

typedef struct {
    uint8_t command_id;
    uint8_t subcommand_id;
    uint8_t data_len;
    uint8_t data[21];
} standalone_init_command_t;

/*
 * This is the same known-good feature sequence used by the desktop BLE/ESP32
 * paths.  Merely subscribing to the characteristic leaves the controller in
 * its low-rate basic stream (roughly 12 Hz on real hardware).
 */
static const standalone_init_command_t s_standalone_init_commands[] = {
    {0x03, 0x0D, 8,  {0x01,0x00,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF}},
    {0x07, 0x01, 0,  {0}},
    {0x16, 0x01, 0,  {0}},
    {0x15, 0x03, 1,  {0x00}},
    {0x0C, 0x02, 4,  {0x94,0x00,0x00,0x00}},
    {0x11, 0x03, 0,  {0}},
    {0x0A, 0x08, 21, {
        0x01,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,
        0x35,0x00,0x46,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00
    }},
    {0x0C, 0x04, 4,  {0x94,0x00,0x00,0x00}},
    {0x03, 0x0A, 4,  {0x09,0x00,0x00,0x00}},
    {0x10, 0x01, 0,  {0}},
    {0x01, 0x0C, 0,  {0}},
    {0x01, 0x01, 4,  {0x00,0x00,0x00,0x00}},
    {0x09, 0x07, 8,  {0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00}},
};

#define STANDALONE_BATTERY_LEVEL_2_MV 3223u
#define STANDALONE_BATTERY_LEVEL_3_MV 3369u
#define STANDALONE_BATTERY_LEVEL_4_MV 3535u
#define STANDALONE_BATTERY_HYSTERESIS_MV 15u
#define STANDALONE_BATTERY_LED_RETRY_MS 250u

static uint8_t standalone_battery_level_from_voltage(uint16_t voltage_mv) {
    if (voltage_mv >= STANDALONE_BATTERY_LEVEL_4_MV) return 4;
    if (voltage_mv >= STANDALONE_BATTERY_LEVEL_3_MV) return 3;
    if (voltage_mv >= STANDALONE_BATTERY_LEVEL_2_MV) return 2;
    return 1;
}

static uint8_t standalone_battery_level_from_mask(uint8_t mask) {
    switch (mask) {
    case 0x01: return 1;
    case 0x03: return 2;
    case 0x07: return 3;
    case 0x0F: return 4;
    default: return 0;
    }
}

static void note_standalone_battery_report(
    int ch, const uint8_t *payload, size_t length
) {
    if (
        !s_standalone_mode || ch < 0 || ch >= MAX_CH ||
        !payload || length < 33
    ) return;
    uint16_t voltage_mv =
        (uint16_t)payload[31] | ((uint16_t)payload[32] << 8);
    if (voltage_mv < 2500u || voltage_mv > 5000u) return;

    uint8_t previous_mask = s_ch[ch].standalone_battery_led_desired_mask;
    if (!previous_mask)
        previous_mask = s_ch[ch].standalone_battery_led_applied_mask;
    uint8_t previous_level =
        standalone_battery_level_from_mask(previous_mask);
    uint8_t measured_level =
        standalone_battery_level_from_voltage(voltage_mv);

    /*
     * Rumble momentarily lowers the measured cell voltage.  Apply symmetric
     * hysteresis around the desktop curve's four display transitions so the
     * player LEDs do not flicker while the motors start and stop.
     */
    if (previous_level && measured_level != previous_level) {
        uint16_t adjusted_mv = voltage_mv;
        if (measured_level > previous_level) {
            adjusted_mv = voltage_mv > STANDALONE_BATTERY_HYSTERESIS_MV
                ? voltage_mv - STANDALONE_BATTERY_HYSTERESIS_MV
                : 0;
        } else if (
            voltage_mv <= UINT16_MAX - STANDALONE_BATTERY_HYSTERESIS_MV
        ) {
            adjusted_mv = voltage_mv + STANDALONE_BATTERY_HYSTERESIS_MV;
        }
        measured_level =
            standalone_battery_level_from_voltage(adjusted_mv);
    }
    s_ch[ch].standalone_battery_led_desired_mask =
        (uint8_t)((1u << measured_level) - 1u);
}

static const uint8_t s_pair_ltk1[] = {
    0x00,
    0xEA, 0xBD, 0x47, 0x13,
    0x89, 0x35, 0x42, 0xC6,
    0x79, 0xEE, 0x07, 0xF2,
    0x53, 0x2C, 0x6C, 0x31,
};
static const uint8_t s_pair_ltk2[] = {
    0x00,
    0x40, 0xB0, 0x8A, 0x5F,
    0xCD, 0x1F, 0x9B, 0x41,
    0x12, 0x5C, 0xAC, 0xC6,
    0x3F, 0x38, 0xA0, 0x73,
};
static const uint8_t s_standalone_pair_subcommands[] = {
    0x01, 0x04, 0x02, 0x03,
};

static void build_standalone_pair_command(
    uint8_t step,
    uint8_t *subcommand_id,
    uint8_t *data,
    uint8_t *data_len
) {
    memset(data, 0, 17);
    switch (step) {
    case 0:
        *subcommand_id = 0x01;  // SET_MAC
        *data_len = 14;
        data[0] = 0x00;
        data[1] = 0x02;
        for (int i = 0; i < 6; i++) {
            uint8_t byte = (uint8_t)(s_own_mac_value >> (i * 8));
            data[2 + i] = byte;
            data[8 + i] = byte;
        }
        break;
    case 1:
        *subcommand_id = 0x04;  // LTK1
        *data_len = sizeof(s_pair_ltk1);
        memcpy(data, s_pair_ltk1, sizeof(s_pair_ltk1));
        break;
    case 2:
        *subcommand_id = 0x02;  // LTK2
        *data_len = sizeof(s_pair_ltk2);
        memcpy(data, s_pair_ltk2, sizeof(s_pair_ltk2));
        break;
    default:
        *subcommand_id = 0x03;  // FINISH
        *data_len = 1;
        data[0] = 0x00;
        break;
    }
}

static void pump_standalone_pairing(void) {
    if (!s_standalone_mode) return;
    uint32_t now = now_ms();
    for (int ch = 0; ch < MAX_CH; ch++) {
        if (
            !s_ch[ch].used || !s_ch[ch].ready || !s_ch[ch].cmd_handle ||
            !s_ch[ch].standalone_pair_required
        ) continue;
        if (s_ch[ch].standalone_pair_step >= 4) {
            s_ch[ch].standalone_pair_required = false;
            continue;
        }
        if ((int32_t)(now - s_ch[ch].standalone_pair_next_ms) < 0)
            continue;

        if (s_ch[ch].standalone_pair_waiting) {
            if (s_ch[ch].standalone_pair_attempts < 3) {
                s_ch[ch].standalone_pair_waiting = false;
            } else {
                s_ch[ch].standalone_pair_step = 0;
                s_ch[ch].standalone_pair_attempts = 0;
                s_ch[ch].standalone_pair_waiting = false;
                s_ch[ch].standalone_pair_next_ms = now + 500u;
                continue;
            }
        }

        uint8_t subcommand_id = 0;
        uint8_t data_len = 0;
        uint8_t data[17];
        build_standalone_pair_command(
            s_ch[ch].standalone_pair_step,
            &subcommand_id,
            data,
            &data_len
        );
        if (!write_switch_command(
                ch, 0x15, subcommand_id, data, data_len
            )) {
            s_ch[ch].standalone_pair_next_ms = now + 20u;
            continue;
        }
        s_ch[ch].standalone_pair_attempts++;
        s_ch[ch].standalone_pair_waiting = true;
        s_ch[ch].standalone_pair_next_ms = now + 800u;
    }
}

static bool note_standalone_pair_ack(
    int ch, const uint8_t *payload, size_t length
) {
    if (
        !s_standalone_mode || ch < 0 || ch >= MAX_CH ||
        !payload || length < 4 || !s_ch[ch].standalone_pair_waiting ||
        !s_ch[ch].standalone_pair_required
    ) return false;
    uint8_t expected_subcommand =
        s_standalone_pair_subcommands[s_ch[ch].standalone_pair_step];
    if (payload[0] != 0x15 || payload[3] != expected_subcommand)
        return false;

    s_ch[ch].standalone_pair_step++;
    s_ch[ch].standalone_pair_attempts = 0;
    s_ch[ch].standalone_pair_waiting = false;
    s_ch[ch].standalone_pair_next_ms = now_ms() + 20u;
    if (s_ch[ch].standalone_pair_step >= 4) {
        s_ch[ch].standalone_pair_required = false;
        s_ch[ch].standalone_init_step = 0;
        s_ch[ch].standalone_init_attempts = 0;
        s_ch[ch].standalone_init_waiting = false;
        s_ch[ch].standalone_init_next_ms = now_ms() + 25u;
        out_debug("standalone persistent pairing complete");
    }
    return true;
}

static void pump_standalone_controller_init(void) {
    if (!s_standalone_mode) return;
    uint32_t now = now_ms();
    for (int ch = 0; ch < MAX_CH; ch++) {
        if (
            !s_ch[ch].used || !s_ch[ch].ready || !s_ch[ch].cmd_handle ||
            s_ch[ch].standalone_pair_required
        )
            continue;
        uint8_t step = s_ch[ch].standalone_init_step;
        if (step >=
            sizeof(s_standalone_init_commands) /
            sizeof(s_standalone_init_commands[0]))
            continue;
        if ((int32_t)(now - s_ch[ch].standalone_init_next_ms) < 0)
            continue;

        if (s_ch[ch].standalone_init_waiting) {
            if (s_ch[ch].standalone_init_attempts < 3) {
                s_ch[ch].standalone_init_waiting = false;
            } else {
                /*
                 * Feature selection is essential for live IMU data. Restart
                 * the complete sequence if an ACK was lost instead of
                 * silently continuing with a basic, motion-less report.
                 */
                s_ch[ch].standalone_init_step = 0;
                s_ch[ch].standalone_init_attempts = 0;
                s_ch[ch].standalone_init_waiting = false;
                s_ch[ch].standalone_init_next_ms = now + 500u;
                continue;
            }
        }
        const standalone_init_command_t *command =
            &s_standalone_init_commands[step];
        if (!write_switch_command(
                ch,
                command->command_id,
                command->subcommand_id,
                command->data,
                command->data_len
            )) {
            s_ch[ch].standalone_init_next_ms = now + 20u;
            continue;
        }
        s_ch[ch].standalone_init_attempts++;
        s_ch[ch].standalone_init_waiting = true;
        s_ch[ch].standalone_init_next_ms = now + 800u;
    }
}

static void note_standalone_init_ack(
    int ch, const uint8_t *payload, size_t length
) {
    if (
        !s_standalone_mode || ch < 0 || ch >= MAX_CH ||
        !payload || length < 4 || !s_ch[ch].standalone_init_waiting
    ) return;
    uint8_t step = s_ch[ch].standalone_init_step;
    if (step >=
        sizeof(s_standalone_init_commands) /
        sizeof(s_standalone_init_commands[0]))
        return;
    if (
        payload[0] != s_standalone_init_commands[step].command_id ||
        payload[3] != s_standalone_init_commands[step].subcommand_id
    )
        return;
    s_ch[ch].standalone_init_step++;
    s_ch[ch].standalone_init_attempts = 0;
    s_ch[ch].standalone_init_waiting = false;
    s_ch[ch].standalone_init_next_ms = now_ms() + 20u;
    if (
        s_ch[ch].standalone_init_step >=
        sizeof(s_standalone_init_commands) /
        sizeof(s_standalone_init_commands[0])
    ) {
        s_ch[ch].standalone_battery_led_applied_mask = 0x01;
        /*
         * Match the desktop connection cue, but only after the complete
         * feature-selection sequence is acknowledged.  "Connected" here
         * therefore means the controller is ready for full-rate input and
         * motion data, not merely that a BLE link exists.
         */
        s_ch[ch].standalone_feedback_step = 0;
        s_ch[ch].standalone_feedback_active =
            s_ch[ch].rumble_handle != 0;
        s_ch[ch].standalone_feedback_next_ms = now_ms() + 20u;
    }
}

static void note_standalone_battery_led_ack(
    int ch, const uint8_t *payload, size_t length
) {
    if (
        !s_standalone_mode || ch < 0 || ch >= MAX_CH ||
        !payload || length < 4 ||
        !s_ch[ch].standalone_battery_led_waiting ||
        payload[0] != 0x09 || payload[3] != 0x07
    ) return;
    s_ch[ch].standalone_battery_led_applied_mask =
        s_ch[ch].standalone_battery_led_pending_mask;
    s_ch[ch].standalone_battery_led_pending_mask = 0;
    s_ch[ch].standalone_battery_led_waiting = false;
    s_ch[ch].standalone_battery_led_next_ms = now_ms() + 20u;
}

static void pump_standalone_battery_leds(void) {
    if (!s_standalone_mode) return;
    uint32_t now = now_ms();
    const uint8_t init_count = (uint8_t)(
        sizeof(s_standalone_init_commands) /
        sizeof(s_standalone_init_commands[0])
    );
    for (int ch = 0; ch < MAX_CH; ch++) {
        if (
            !s_ch[ch].used || !s_ch[ch].ready || !s_ch[ch].cmd_handle ||
            s_ch[ch].standalone_pair_required ||
            s_ch[ch].standalone_init_step < init_count ||
            s_ch[ch].standalone_init_waiting
        ) continue;
        if ((int32_t)(now - s_ch[ch].standalone_battery_led_next_ms) < 0)
            continue;

        if (s_ch[ch].standalone_battery_led_waiting) {
            /* ACK was lost: retry the exact mask that was sent. */
            s_ch[ch].standalone_battery_led_waiting = false;
        } else {
            uint8_t desired =
                s_ch[ch].standalone_battery_led_desired_mask;
            if (
                !desired ||
                desired == s_ch[ch].standalone_battery_led_applied_mask
            ) continue;
            s_ch[ch].standalone_battery_led_pending_mask = desired;
        }

        uint8_t data[8] = {
            s_ch[ch].standalone_battery_led_pending_mask,
            0, 0, 0, 0, 0, 0, 0,
        };
        if (!write_switch_command(ch, 0x09, 0x07, data, sizeof(data))) {
            s_ch[ch].standalone_battery_led_next_ms = now + 20u;
            continue;
        }
        s_ch[ch].standalone_battery_led_waiting = true;
        s_ch[ch].standalone_battery_led_next_ms =
            now + STANDALONE_BATTERY_LED_RETRY_MS;
    }
}

static void encode_standalone_vibration(
    uint8_t *output,
    uint16_t lf_frequency,
    uint16_t lf_amplitude,
    uint16_t hf_frequency,
    uint16_t hf_amplitude
) {
    uint64_t packed =
        ((uint64_t)lf_frequency & 0x1ffu) |
        (((uint64_t)lf_amplitude & 0x3ffu) << 10) |
        (((uint64_t)hf_frequency & 0x1ffu) << 20) |
        (((uint64_t)hf_amplitude & 0x3ffu) << 30);
    for (int i = 0; i < 5; i++)
        output[i] = (uint8_t)(packed >> (i * 8));
}

static void send_standalone_vibration(
    int channel,
    uint16_t lf_frequency,
    uint16_t lf_amplitude,
    uint16_t hf_frequency,
    uint16_t hf_amplitude
) {
    if (
        channel < 0 || channel >= MAX_CH ||
        !s_ch[channel].used || !s_ch[channel].rumble_handle
    ) return;
    uint8_t vibration[5];
    encode_standalone_vibration(
        vibration, lf_frequency, lf_amplitude,
        hf_frequency, hf_amplitude
    );
    uint8_t payload[33] = {0};
    uint8_t packet_id =
        (uint8_t)(0x50u + (s_standalone_rumble_packet_id++ & 0x0fu));
    for (int motor = 0; motor < 2; motor++) {
        size_t offset = 1 + motor * 16;
        payload[offset] = packet_id;
        memcpy(payload + offset + 1, vibration, sizeof(vibration));
        memcpy(payload + offset + 6, vibration, sizeof(vibration));
        memcpy(payload + offset + 11, vibration, sizeof(vibration));
    }
    wr_one(channel, 'r', payload, sizeof(payload));
}

static bool standalone_feedback_is_active(void) {
    for (int ch = 0; ch < MAX_CH; ch++) {
        if (s_ch[ch].used && s_ch[ch].standalone_feedback_active)
            return true;
    }
    return false;
}

static void pump_standalone_connection_feedback(void) {
    if (!s_standalone_mode) return;
    /*
     * Same audible/tactile cadence as the desktop application:
     *   80 ms on, 100 ms off, 80 ms on.
     * Three final zero writes make stopping reliable despite no-response BLE
     * writes, without changing what the user feels.
     */
    static const uint16_t amplitudes[] = {
        STANDALONE_FEEDBACK_AMPLITUDE, 0,
        STANDALONE_FEEDBACK_AMPLITUDE, 0, 0, 0
    };
    static const uint16_t delays_ms[] = {80, 100, 80, 16, 16, 0};
    uint32_t now = now_ms();
    for (int ch = 0; ch < MAX_CH; ch++) {
        if (!s_ch[ch].used || !s_ch[ch].standalone_feedback_active)
            continue;
        if ((int32_t)(now - s_ch[ch].standalone_feedback_next_ms) < 0)
            continue;
        uint8_t step = s_ch[ch].standalone_feedback_step;
        if (step >= sizeof(amplitudes) / sizeof(amplitudes[0])) {
            s_ch[ch].standalone_feedback_active = false;
            continue;
        }
        send_standalone_vibration(
            ch,
            STANDALONE_FEEDBACK_LF_FREQUENCY, amplitudes[step],
            STANDALONE_FEEDBACK_HF_FREQUENCY, amplitudes[step]
        );
        s_ch[ch].standalone_feedback_step++;
        if (
            s_ch[ch].standalone_feedback_step >=
            sizeof(amplitudes) / sizeof(amplitudes[0])
        ) {
            s_ch[ch].standalone_feedback_active = false;
            s_standalone_rumble_next_ms = now;
        } else {
            s_ch[ch].standalone_feedback_next_ms =
                now + delays_ms[step];
        }
    }
}

static void pump_standalone_xinput_rumble(void) {
    if (!s_standalone_mode) return;
    /* The short connection cue has priority over a simultaneous game effect. */
    if (standalone_feedback_is_active()) return;
    uint8_t large_motor = 0;
    uint8_t small_motor = 0;
    bool changed =
        standalone_xinput_take_rumble(&large_motor, &small_motor);
    uint32_t now = now_ms();
    if (changed) {
        s_standalone_rumble_received++;
        if (large_motor > s_standalone_rumble_peak_large)
            s_standalone_rumble_peak_large = large_motor;
        if (small_motor > s_standalone_rumble_peak_small)
            s_standalone_rumble_peak_small = small_motor;
        bool was_active =
            s_standalone_large_motor != 0 ||
            s_standalone_small_motor != 0;
        s_standalone_large_motor = large_motor;
        s_standalone_small_motor = small_motor;
        bool is_active = large_motor != 0 || small_motor != 0;
        if (!is_active && was_active)
            s_standalone_zero_flush =
                STANDALONE_RUMBLE_ZERO_FLUSH_COUNT;
        else if (is_active)
            s_standalone_zero_flush = 0;
        /* A changed effect must not wait for the periodic refresh deadline. */
        s_standalone_rumble_next_ms = now;
    }

    bool active =
        s_standalone_large_motor != 0 ||
        s_standalone_small_motor != 0;
    if (!changed && !active && s_standalone_zero_flush == 0) return;
    if ((int32_t)(now - s_standalone_rumble_next_ms) < 0) return;

    int channel = -1;
    for (int i = 0; i < MAX_CH; i++) {
        if (s_ch[i].ready && s_ch[i].rumble_handle) {
            channel = i;
            break;
        }
    }
    if (channel < 0) return;

    /*
     * One command carries three 5 ms vibration frames.  Keep refreshing the
     * latest non-zero XInput state: Windows is allowed to send motor output
     * only when it changes, so treating OUT reports as one-shot effects makes
     * sustained rumble sound truncated.  Zero is flushed three times so a
     * dropped BLE write cannot leave the actuator running.
     */
    large_motor = s_standalone_large_motor;
    small_motor = s_standalone_small_motor;

    uint16_t lf_frequency, hf_frequency, max_amplitude;
    float lf_strength, hf_strength, lf_curve, hf_curve;
    float lf_to_hf, hf_to_lf;
    standalone_xinput_get_rumble_config(
        &lf_frequency, &hf_frequency, &max_amplitude,
        &lf_strength, &hf_strength, &lf_curve, &hf_curve,
        &lf_to_hf, &hf_to_lf
    );
    float lf_input = large_motor / 255.0f;
    float hf_input = small_motor / 255.0f;
    /*
     * Keep the same integer boundaries as the desktop game-rumble path.
     * Truncating each component before addition avoids one-count drift in
     * cross compensation and makes an exported profile deterministic.
     */
    uint16_t raw_lf = (uint16_t)(
        max_amplitude * powf(lf_input, lf_curve)
    );
    uint16_t raw_hf = (uint16_t)(
        max_amplitude * powf(hf_input, hf_curve)
    );
    uint32_t lf_value =
        (uint16_t)(raw_lf * lf_strength) +
        (uint16_t)(raw_hf * hf_to_lf);
    uint32_t hf_value =
        (uint16_t)(raw_hf * hf_strength) +
        (uint16_t)(raw_lf * lf_to_hf);
    uint16_t lf_amplitude = (uint16_t)(
        lf_value > max_amplitude ? max_amplitude : lf_value
    );
    uint16_t hf_amplitude = (uint16_t)(
        hf_value > max_amplitude ? max_amplitude : hf_value
    );
    s_standalone_rumble_lf_frequency = lf_frequency;
    s_standalone_rumble_hf_frequency = hf_frequency;
    s_standalone_rumble_lf_amplitude = lf_amplitude;
    s_standalone_rumble_hf_amplitude = hf_amplitude;
    if (lf_amplitude > s_standalone_rumble_peak_lf_amplitude)
        s_standalone_rumble_peak_lf_amplitude = lf_amplitude;
    if (hf_amplitude > s_standalone_rumble_peak_hf_amplitude)
        s_standalone_rumble_peak_hf_amplitude = hf_amplitude;
    standalone_xinput_set_rumble_ratio(
        fmaxf((float)lf_amplitude, (float)hf_amplitude) /
        fmaxf(1.0f, (float)max_amplitude)
    );
    send_standalone_vibration(
        channel, lf_frequency, lf_amplitude,
        hf_frequency, hf_amplitude
    );
    s_standalone_rumble_sent++;
    s_standalone_rumble_next_ms =
        now + STANDALONE_RUMBLE_REFRESH_MS;
    if (!active && s_standalone_zero_flush > 0)
        s_standalone_zero_flush--;
}

static bool command_is_control(const char *cmd) {
    return
        strncmp(cmd, "scan on", 7) == 0 ||
        strncmp(cmd, "scan off", 8) == 0 ||
        strncmp(cmd, "ble disconnect", 14) == 0 ||
        strncmp(cmd, "auto", 4) == 0 ||
        strncmp(cmd, "conn ", 5) == 0 ||
        strncmp(cmd, "inputsrc ", 9) == 0 ||
        strncmp(cmd, "disc ", 5) == 0 ||
        strncmp(cmd, "wrpair ", 7) == 0 ||
        strncmp(cmd, "wr ", 3) == 0 ||
        strncmp(cmd, "rs ", 3) == 0;
}

static void handle_query_command(char *cmd) {
    if (strncmp(cmd, "status", 6) == 0)         { send_status_response(); }
    else if (strcmp(cmd, "capabilities") == 0)  { send_capabilities_response(); }
    else if (strcmp(cmd, "profile status") == 0) {
        char response[256];
        standalone_profile_format_status(response, sizeof(response));
        send_json(response);
    }
    else if (strcmp(cmd, "runtime status") == 0) {
        char status[512];
        standalone_xinput_format_runtime_status(status, sizeof(status));
        send_json(status);
    }
    else if (strcmp(cmd, "ble timing") == 0) {
        char timing[192];
        ble_callback_metrics_format(timing, sizeof(timing));
        send_json(timing);
    }
    else if (strcmp(cmd, "link status") == 0) {
        char status[512];
        request_link_rssi();
        format_link_status(status, sizeof(status));
        send_json(status);
    }
    else if (strcmp(cmd, "ble timing reset") == 0) {
        ble_callback_metrics_reset();
        send_json("{\"cmd\":\"ble_timing_reset\",\"ok\":1}\n");
    }
    else if (strcmp(cmd, "latency status") == 0) {
        char status[512];
        format_input_latency_metrics(status, sizeof(status));
        send_json(status);
    }
    else if (strcmp(cmd, "latency reset") == 0) {
        reset_input_latency_metrics();
        send_json("{\"cmd\":\"latency_reset\",\"ok\":1}\n");
    }
    else if (strcmp(cmd, "rumble status") == 0) {
        char status[512];
        format_standalone_rumble_metrics(status, sizeof(status));
        send_json(status);
    }
    else if (strcmp(cmd, "rumble reset") == 0) {
        reset_standalone_rumble_metrics();
        send_json("{\"cmd\":\"rumble_reset\",\"ok\":1}\n");
    }
    else if (strncmp(cmd, "algorithm test ", 15) == 0) {
        char result[256];
        if (standalone_xinput_format_algorithm_test(
            cmd + 15, result, sizeof(result)
        )) {
            send_json(result);
        } else {
            send_json(
                "{\"cmd\":\"algorithm_test\",\"ok\":0,"
                "\"error\":\"invalid_arguments\"}\n"
            );
        }
    }
    else if (strncmp(cmd, "gyro test ", 10) == 0) {
        char result[512];
        if (standalone_xinput_format_gyro_test(
            cmd + 10, result, sizeof(result)
        )) {
            send_json(result);
        } else {
            send_json(
                "{\"cmd\":\"gyro_test\",\"ok\":0,"
                "\"error\":\"invalid_arguments\"}\n"
            );
        }
    }
    else if (strncmp(cmd, "profile begin ", 14) == 0) {
        char response[256];
        standalone_profile_begin(cmd + 14, response, sizeof(response));
        send_json(response);
    }
    else if (strncmp(cmd, "profile chunk ", 14) == 0) {
        char response[256];
        standalone_profile_chunk(cmd + 14, response, sizeof(response));
        send_json(response);
    }
    else if (strcmp(cmd, "profile commit") == 0) {
        char response[256];
        standalone_profile_commit(response, sizeof(response));
        send_json(response);
    }
    else if (strcmp(cmd, "profile abort") == 0) {
        char response[128];
        standalone_profile_abort(response, sizeof(response));
        send_json(response);
    }
    else if (strncmp(cmd, "mode ", 5) == 0) { do_mode_command(cmd + 5); }
    else if (strcmp(cmd, "restart") == 0) { do_restart_command(); }
}

static void handle_control_command(char *cmd) {
    if (strncmp(cmd, "scan on", 7) == 0) {
        // The host sends "scan on" right after "disc <ch>" to re-arm detection.  Never
        // start the scan synchronously here: a gap_disconnect issued by do_disc is still
        // in flight on the HCI path and starting a scan on top of it collides and
        // silently drops the disconnect (symptom: the controller you asked to disconnect
        // stays connected while an unrelated link drops).  Defer to cdc_task, which only
        // resumes once GAP is quiet and no disconnect is in flight.
        s_scan_mode = true;
        if (
            s_scan_params_ready &&
            time_reached(now_ms(), s_gap_busy_until) &&
            !s_disc_in_flight && s_disc_mask == 0
        ) {
            s_resume_scan = false;
            if (!request_scan_start()) s_resume_scan = true;
        } else {
            s_resume_scan = true;
        }
    }
    else if (strncmp(cmd, "scan off", 8) == 0) {
        s_scan_mode = false;
        s_resume_scan = false;
        request_scan_stop();
    }
    else if (strncmp(cmd, "ble disconnect", 14) == 0) {
        do_disc_all();
    }
    else if (strncmp(cmd, "auto", 4) == 0)      { /* host-driven conn only */ }
    else if (strncmp(cmd, "conn ", 5) == 0)     { do_conn(cmd + 5, false); }
    else if (strncmp(cmd, "inputsrc ", 9) == 0) { do_inputsrc(cmd + 9); }
    else if (strncmp(cmd, "disc ", 5) == 0)     { do_disc(cmd + 5); }
    else if (strncmp(cmd, "wrpair ", 7) == 0)   { do_wrpair(cmd + 7); }
    else if (strncmp(cmd, "wr ", 3) == 0)       { do_wr(cmd + 3); }
    else if (strncmp(cmd, "rs ", 3) == 0)       { do_rs(cmd + 3); }
}

static void emit_connect_fail(int ch, const char *reason) {
    if (ch < 0 || ch >= MAX_CH || !s_ch[ch].used) return;
    char message[100];
    snprintf(
        message, sizeof(message),
        "{\"cmd\":\"connect_fail\",\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}\n",
        s_ch[ch].bda[0],s_ch[ch].bda[1],s_ch[ch].bda[2],
        s_ch[ch].bda[3],s_ch[ch].bda[4],s_ch[ch].bda[5]
    );
    out_event(message);
    char debug[96];
    snprintf(debug, sizeof(debug), "connect watchdog ch=%d: %s", ch, reason);
    out_debug(debug);
}

static void pump_connection_watchdogs(void) {
    uint32_t now = now_ms();
    for (int ch = 0; ch < MAX_CH; ch++) {
        if (
            !s_ch[ch].used || s_ch[ch].ready ||
            !deadline_reached(now, s_ch[ch].connect_deadline_ms)
        ) {
            continue;
        }
        s_ch[ch].connect_deadline_ms = 0;
        if (s_pending_conn == ch) {
            s_pending_conn = -1;
            s_conn_open_after = 0;
        }
        if (s_ch[ch].link_open) {
            portENTER_CRITICAL(&s_disc_mux);
            s_disc_mask |= (1u << ch);
            portEXIT_CRITICAL(&s_disc_mux);
            kick_disc_queue();
            continue;
        }
        emit_connect_fail(ch, "open/discovery timeout");
        esp_err_t disconnect_err =
            esp_ble_gap_disconnect(s_ch[ch].bda);
        if (disconnect_err == ESP_OK) gap_busy(400);
        clear_channel_state(ch);
        restore_widened_links();
        gap_busy(300);
        if (s_scan_mode) s_resume_scan = true;
    }
}

void tinyusb_cdc_rx_callback(int itf, cdcacm_event_t *event) {
    if (event->type != CDC_EVENT_RX) return;
    uint8_t tmp[128]; size_t n = 0;
    if (tinyusb_cdcacm_read(itf, tmp, sizeof(tmp), &n) != ESP_OK || n == 0) return;
    for (size_t i = 0; i < n; i++) {
        char c = (char)tmp[i];
        if (c == '\r') continue;
        if (c == '\n') {
            s_rx_buf[s_rx_len] = '\0';
            if (s_rx_len > 0) {
                QueueHandle_t destination =
                    command_is_control(s_rx_buf)
                        ? s_control_queue : s_query_queue;
                line_t L; strncpy(L.text, s_rx_buf, sizeof(L.text) - 1); L.text[sizeof(L.text)-1] = '\0';
                if (
                    destination &&
                    xQueueSend(destination, &L, 0) == pdTRUE &&
                    s_cdc_task_h
                )
                    xTaskNotifyGive(s_cdc_task_h);
            }
            s_rx_len = 0;
        } else if (s_rx_len < (int)sizeof(s_rx_buf) - 1) {
            s_rx_buf[s_rx_len++] = c;
        }
    }
}
static void cdc_task(void *arg) {
    (void)arg;
    for (;;) {
        pump_gatt_outputs();
        standalone_xinput_pump();
        pump_standalone_pairing();
        pump_standalone_controller_init();
        pump_standalone_battery_leds();
        pump_standalone_connection_feedback();
        pump_standalone_xinput_rumble();
        pump_connection_watchdogs();
        if (standalone_xinput_idle_disconnect_due()) {
            do_disc_all();
        }
        if (s_standalone_auto_conn_pending && s_pending_conn < 0) {
            bool pair_required = s_standalone_auto_conn_pair_required;
            s_standalone_auto_conn_pending = false;
            s_standalone_auto_conn_pair_required = false;
            do_conn(s_standalone_auto_conn, pair_required);
        }
        // Deferred 3rd-link open: the existing links were widened to 15ms in do_conn;
        // open the 3rd once that has settled (scan is already stopped by then).
        if (
            s_pending_conn >= 0 && s_conn_open_after != 0 &&
            deadline_reached(now_ms(), s_conn_open_after)
        )
            open_pending_conn();
        if (
            s_scan_stop_needed && !s_scan_stop_pending &&
            time_reached(now_ms(), s_scan_retry_after_ms)
        ) {
            if (request_scan_stop() && !s_scan_stop_pending) {
                if (s_pending_conn >= 0 && s_conn_open_after == 0)
                    open_pending_conn();
            }
        }
        // Deferred scan resume: only once the GAP bus is quiet, no open is pending, and
        // no channel is mid-connect — so it never pre-empts an in-flight disconnect/open.
        if (
            s_resume_scan && s_scan_mode && s_scan_params_ready &&
            s_pending_conn < 0 &&
            time_reached(now_ms(), s_gap_busy_until) &&
            time_reached(now_ms(), s_scan_retry_after_ms)
        ) {
            bool connecting = false;
            for (int i = 0; i < MAX_CH; i++) if (s_ch[i].connecting) connecting = true;
            if (!connecting) {
                s_resume_scan = false;
                if (!request_scan_start() && !standalone_has_link())
                    s_resume_scan = true;
            }
        }
        // Safety net: if a DISCONNECT_EVT was missed (very rare), retry the queue
        // so a stuck disconnect doesn't wedge the bridge until replug.
        if (
            !s_disc_in_flight && s_disc_mask &&
            time_reached(now_ms(), s_gap_busy_until)
        )
            kick_disc_queue();
        /*
         * Every CDC producer shares one short phase deadline. A partial item
         * remains in s_cdc_tx and is completed before any queue is dequeued,
         * so a slow host cannot splice two frames or monopolize cdc_task.
         */
        int64_t cdc_deadline_us =
            esp_timer_get_time() + CDC_TX_PHASE_BUDGET_US;
        cdc_tx_pump_until(cdc_deadline_us);
        // Input shadows are P0: forward the newest controller state before
        // diagnostics or command traffic.  BLE callbacks wake this task
        // immediately, removing the old command-queue polling delay (<=2 ms).
        for (int i = 0; i < MAX_CH; i++) {
            if (
                !s_standalone_mode &&
                !cdc_tx_can_submit(cdc_deadline_us)
            ) break;
            in_report_t r; bool dirty = false;
            portENTER_CRITICAL(&s_in_mux);
            if (s_in_dirty[i]) { r = s_in_shadow[i]; s_in_dirty[i] = false; dirty = true; }
            portEXIT_CRITICAL(&s_in_mux);
            if (
                dirty && r.ch < MAX_CH && s_ch[r.ch].used &&
                r.generation == s_ch[r.ch].generation
            ) {
                if (s_standalone_mode) {
                    note_standalone_battery_report(
                        r.ch, r.data, r.len
                    );
                    standalone_xinput_accept_switch_report(
                        r.ch, r.data, r.len
                    );
                } else {
                    if (r.handle) send_notify_handle_frame(r.ch, r.handle, r.data, r.len);
                    else send_report_frame(r.ch, r.data, r.len, false);
                    cdc_tx_pump_until(cdc_deadline_us);
                }
            }
        }
        in_report_t ack;
        int ack_processed = 0;
        while (
            ack_processed < CDC_QUEUE_BUDGET_PER_LOOP &&
            (
                s_standalone_mode ||
                cdc_tx_can_submit(cdc_deadline_us)
            ) &&
            xQueueReceive(s_ack_queue, &ack, 0) == pdTRUE
        ) {
            ack_processed++;
            if (
                ack.ch >= MAX_CH || !s_ch[ack.ch].used ||
                ack.generation != s_ch[ack.ch].generation
            ) continue;
            if (s_standalone_mode) {
                if (
                    !note_standalone_pair_ack(ack.ch, ack.data, ack.len)
                ) {
                    note_standalone_init_ack(
                        ack.ch, ack.data, ack.len
                    );
                    note_standalone_battery_led_ack(
                        ack.ch, ack.data, ack.len
                    );
                }
            } else {
                send_report_frame(ack.ch, ack.data, ack.len, true);
                cdc_tx_pump_until(cdc_deadline_us);
            }
        }
        in_report_t ntf;
        int notify_processed = 0;
        while (
            notify_processed < CDC_QUEUE_BUDGET_PER_LOOP &&
            (
                s_standalone_mode ||
                cdc_tx_can_submit(cdc_deadline_us)
            ) &&
            xQueueReceive(s_notify_queue, &ntf, 0) == pdTRUE
        ) {
            notify_processed++;
            if (
                ntf.ch >= MAX_CH || !s_ch[ntf.ch].used ||
                ntf.generation != s_ch[ntf.ch].generation
            ) continue;
            if (
                s_standalone_mode &&
                ntf.handle == s_ch[ntf.ch].input_handle
            ) {
                note_standalone_battery_report(
                    ntf.ch, ntf.data, ntf.len
                );
                standalone_xinput_accept_switch_report(
                    ntf.ch, ntf.data, ntf.len
                );
            } else if (!s_standalone_mode) {
                send_notify_handle_frame(ntf.ch, ntf.handle, ntf.data, ntf.len);
                cdc_tx_pump_until(cdc_deadline_us);
            }
        }
        /*
         * Input accepted above marks the standalone USB report dirty.  Pump it
         * in the same wakeup instead of reaching the 2 ms maintenance wait and
         * deferring the new state to the next loop.  Keep the pump at the top
         * of the loop as well so a report whose endpoint was busy is retried.
        */
        standalone_xinput_pump();
        /*
         * Control commands never write CDC, so execute them even while a
         * previous frame is pending. This lets scan off, conn, disconnect and
         * rumble make progress independently of a slow host reader.
         */
        line_t control;
        for (
            int handled = 0;
            handled < CDC_QUEUE_BUDGET_PER_LOOP &&
                xQueueReceive(
                    s_control_queue, &control, 0
                ) == pdTRUE;
            handled++
        ) {
            handle_control_command(control.text);
        }
        // Lifecycle events outrank queries and low-priority scan/debug output.
        line_t event;
        for (
            int sent = 0;
            sent < CDC_QUEUE_BUDGET_PER_LOOP &&
                cdc_tx_can_submit(cdc_deadline_us) &&
                xQueueReceive(s_event_queue, &event, 0) == pdTRUE;
            sent++
        ) {
            send_json(event.text);
            cdc_tx_pump_until(cdc_deadline_us);
        }
        line_t query;
        for (
            int handled = 0;
            handled < CDC_QUEUE_BUDGET_PER_LOOP &&
                cdc_tx_can_submit(cdc_deadline_us) &&
                xQueueReceive(s_query_queue, &query, 0) == pdTRUE;
            handled++
        ) {
            handle_query_command(query.text);
            cdc_tx_pump_until(cdc_deadline_us);
        }
        line_t out;
        for (
            int sent = 0;
            sent < CDC_QUEUE_BUDGET_PER_LOOP &&
                cdc_tx_can_submit(cdc_deadline_us) &&
                xQueueReceive(s_out_queue, &out, 0) == pdTRUE;
            sent++
        ) {
            send_json(out.text);
            cdc_tx_pump_until(cdc_deadline_us);
        }

        // Notifications make input wakeups immediate.  The timeout only keeps
        // deferred GAP maintenance deadlines progressing when fully idle.
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(2));
    }
}

static void wake_standalone_output(void) {
    if (s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
}

// --- GATT discovery helpers ---
static void match_and_store_char(int ch, const esp_bt_uuid_t *uuid, uint16_t val_handle) {
    // Record both input notify characteristics; the actual input_handle is chosen in
    // SEARCH_CMPL_EVT once discovery is complete (order-independent).
    // FD2 (ab7de9be…, handle 0x000A, Input Report 0x05) is the canonical Switch 2 input
    // stream for the Pro 2 / Joy-Cons.  The NSO GameCube controller is the exception: its
    // usable report is Input Report 0x0A on its own model-specific char (8261cba1…, handle
    // 0x000E).  Parsing FD2 (0x05) with the GCN 0x0A byte layout puts the right stick on the
    // trigger byte and reads non-IMU bytes as gyro.  So when the GCN char is present we store
    // it as the input source and auto-prefer it (prefer_legacy) — matching what the WinRT path
    // does implicitly by subscribing to every notify char in the SW2 service.
    // NOTE: 7492866c… (UUID_NOTIFY_LEGACY) is the *Pro Controller's* 0x000E char, not the
    // GameCube's; it is stored here only for completeness and never auto-preferred.
    if (memcmp(uuid, &UUID_NOTIFY_FD2, sizeof(*uuid)) == 0)         s_ch[ch].fd2_handle = val_handle;
    else if (memcmp(uuid, &UUID_NOTIFY_GC, sizeof(*uuid)) == 0) {
        s_ch[ch].legacy_handle = val_handle;   // GCN model-specific input (Input Report 0x0A)
        s_ch[ch].prefer_legacy = true;         // the generic FD2 (0x05) is the wrong layout for the GCN
    }
    else if (memcmp(uuid, &UUID_NOTIFY_LEGACY, sizeof(*uuid)) == 0) s_ch[ch].legacy_handle = val_handle;
    else if (memcmp(uuid, &UUID_ACK, sizeof(*uuid)) == 0)      s_ch[ch].ack_handle = val_handle;
    else if (memcmp(uuid, &UUID_CMD, sizeof(*uuid)) == 0)      s_ch[ch].cmd_handle = val_handle;
    else if (memcmp(uuid, &UUID_RUMBLE_PRO, sizeof(*uuid)) == 0 ||
             memcmp(uuid, &UUID_RUMBLE_JOYCON_R, sizeof(*uuid)) == 0 ||
             memcmp(uuid, &UUID_RUMBLE_JOYCON_L, sizeof(*uuid)) == 0) s_ch[ch].rumble_handle = val_handle;
}
// Record a SW2-service NOTIFY characteristic handle for the GCN "enable all CCCDs" pass.
static void note_notify_handle(int ch, uint16_t handle) {
    if (handle == 0) return;
    for (int i = 0; i < s_ch[ch].notify_count; i++)
        if (s_ch[ch].notify_handles[i] == handle) return;   // dedup
    const uint8_t cap = (uint8_t)(sizeof(s_ch[ch].notify_handles) / sizeof(s_ch[ch].notify_handles[0]));
    if (s_ch[ch].notify_count >= cap) { out_debug("notify_handles full; SW2 notify char dropped"); return; }
    s_ch[ch].notify_handles[s_ch[ch].notify_count++] = handle;
}
static void scan_service_chars(int ch, uint16_t start, uint16_t end, bool is_sw2) {
    uint16_t count = 0;
    if (esp_ble_gattc_get_attr_count(s_ch[ch].gattc_if, s_ch[ch].conn_id, ESP_GATT_DB_CHARACTERISTIC,
                                     start, end, 0, &count) != ESP_OK || count == 0) return;
    esp_gattc_char_elem_t *elems = calloc(count, sizeof(esp_gattc_char_elem_t));
    if (!elems) return;
    uint16_t got = count;
    if (esp_ble_gattc_get_all_char(s_ch[ch].gattc_if, s_ch[ch].conn_id, start, end, elems, &got, 0) == ESP_OK)
        for (int i = 0; i < got; i++) {
            match_and_store_char(ch, &elems[i].uuid, elems[i].char_handle);
            if (is_sw2) {
                char uuid_s[40];
                char props[8];
                int pi = 0;
                uuid_to_str(&elems[i].uuid, uuid_s, sizeof(uuid_s));
                if (elems[i].properties & ESP_GATT_CHAR_PROP_BIT_NOTIFY) props[pi++] = 'n';
                if (elems[i].properties & ESP_GATT_CHAR_PROP_BIT_WRITE_NR) props[pi++] = 'w';
                if (elems[i].properties & ESP_GATT_CHAR_PROP_BIT_WRITE) props[pi++] = 'W';
                if (elems[i].properties & ESP_GATT_CHAR_PROP_BIT_READ) props[pi++] = 'r';
                props[pi] = '\0';

                char msg[220];
                snprintf(msg, sizeof(msg),
                         "{\"cmd\":\"gatt_char\",\"channel\":%d,\"service\":\"ab7de9be-89fe-49ad-828f-118f09df7fd0\",\"handle\":%u,\"uuid\":\"%s\",\"props\":\"%s\"}\n",
                         ch, elems[i].char_handle, uuid_s, props);
                out_json(msg);

                if (elems[i].properties & ESP_GATT_CHAR_PROP_BIT_NOTIFY)
                    note_notify_handle(ch, elems[i].char_handle);
            }
        }
    free(elems);
}
// Write the next pending CCCD in the GCN notify list, one at a time.  Each completion
// (WRITE_DESCR_EVT) advances to the next, so we never issue back-to-back descriptor writes.
static void cccd_drain_step(int ch) {
    while (s_ch[ch].cccd_idx < s_ch[ch].notify_count) {
        uint16_t handle = s_ch[ch].notify_handles[s_ch[ch].cccd_idx];
        if (write_cccd_value(ch, handle, true)) return;

        char dbg[96];
        snprintf(dbg, sizeof(dbg), "GCN CCCD drain skipped ch=%d handle=0x%04x idx=%u/%u",
                 ch, handle, s_ch[ch].cccd_idx, s_ch[ch].notify_count);
        out_debug(dbg);
        s_ch[ch].cccd_idx++;
    }
    s_ch[ch].cccd_draining = false;
}
// Begin CCCD-enabling every collected SW2 notify char (GCN).  input_handle is moved to the
// front so the input stream is enabled first.
static void start_cccd_drain(int ch) {
    for (int i = 1; i < s_ch[ch].notify_count; i++) {
        if (s_ch[ch].notify_handles[i] == s_ch[ch].input_handle) {
            uint16_t t = s_ch[ch].notify_handles[0];
            s_ch[ch].notify_handles[0] = s_ch[ch].notify_handles[i];
            s_ch[ch].notify_handles[i] = t;
            break;
        }
    }
    s_ch[ch].cccd_idx = 0;
    s_ch[ch].cccd_draining = true;
    cccd_drain_step(ch);
}
// True for a GameCube channel that uses the "enable every SW2 notify CCCD" path.
static inline bool ch_uses_notify_all(int ch) {
    return s_ch[ch].prefer_legacy && s_ch[ch].notify_count > 0;
}
static void enable_notifications(int ch) {
    if (ch_uses_notify_all(ch)) {
        // GameCube: register for notify on EVERY SW2 notify char (input, ack, and any
        // model-specific/extra input chars), matching the WinRT GCN subscription.  Then the
        // sequential drain writes each CCCD in turn.  NOTIFY_EVT still forwards only
        // input_handle, so the extra streams never reach the host parser.
        for (int i = 0; i < s_ch[ch].notify_count; i++)
            esp_ble_gattc_register_for_notify(s_ch[ch].gattc_if, s_ch[ch].bda, s_ch[ch].notify_handles[i]);
        start_cccd_drain(ch);
        return;
    }
    if (s_ch[ch].ack_handle)   esp_ble_gattc_register_for_notify(s_ch[ch].gattc_if, s_ch[ch].bda, s_ch[ch].ack_handle);
    if (s_ch[ch].input_handle) esp_ble_gattc_register_for_notify(s_ch[ch].gattc_if, s_ch[ch].bda, s_ch[ch].input_handle);
}

static void mark_channel_ready(int ch) {
    if (ch < 0 || ch >= MAX_CH || !s_ch[ch].used || s_ch[ch].ready)
        return;
    s_ch[ch].ready = true;
    s_ch[ch].connecting = false;
    s_ch[ch].connect_deadline_ms = 0;
    s_ch[ch].standalone_init_step = 0;
    s_ch[ch].standalone_init_attempts = 0;
    s_ch[ch].standalone_init_waiting = false;
    s_ch[ch].standalone_init_next_ms = now_ms() + 25u;
    s_ch[ch].standalone_battery_led_desired_mask = 0;
    s_ch[ch].standalone_battery_led_pending_mask = 0;
    s_ch[ch].standalone_battery_led_applied_mask = 0;
    s_ch[ch].standalone_battery_led_waiting = false;
    s_ch[ch].standalone_battery_led_next_ms = 0;
    s_ch[ch].standalone_pair_step = 0;
    s_ch[ch].standalone_pair_attempts = 0;
    s_ch[ch].standalone_pair_waiting = false;
    s_ch[ch].standalone_pair_next_ms = now_ms() + 25u;
    {
        uint16_t itvl = s_ch[ch].itvl ? s_ch[ch].itvl : 6;
        esp_ble_conn_update_params_t cp = {0};
        memcpy(cp.bda, s_ch[ch].bda, sizeof(esp_bd_addr_t));
        cp.min_int = itvl;
        cp.max_int = itvl;
        cp.latency = 0;
        cp.timeout = 400;
        esp_ble_gap_update_conn_params(&cp);
    }
    char message[96];
    snprintf(
        message, sizeof(message),
        "{\"cmd\":\"connected\",\"channel\":%d,\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}\n",
        ch, s_ch[ch].bda[0],s_ch[ch].bda[1],s_ch[ch].bda[2],
        s_ch[ch].bda[3],s_ch[ch].bda[4],s_ch[ch].bda[5]
    );
    out_event(message);
    restore_widened_links();
    if (s_standalone_mode) {
        s_resume_scan = false;
        request_scan_stop();
    } else if (s_scan_mode) {
        s_resume_scan = true;
    }
}

static void gattc_cb(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                     esp_ble_gattc_cb_param_t *param) {
    if (event == ESP_GATTC_REG_EVT) {
        int slot = param->reg.app_id;   // app_id == channel index
        if (param->reg.status == ESP_GATT_OK && slot >= 0 && slot < MAX_CH) {
            s_ch[slot].gattc_if = gattc_if;
            ESP_LOGI(TAG, "GATTC app %d registered (if=%d)", slot, gattc_if);
        }
        return;
    }

    // ---- DISCONNECT: route by remote_bda, NOT by gattc_if ----------------------
    // Bluedroid may deliver DISCONNECT_EVT with ESP_GATT_IF_NONE (broadcast to every
    // app) or with a *mismatched* gattc_if.  The esp-idf gattc_multi_connect example
    // proves this: it identifies every DISCONNECT_EVT by memcmp(remote_bda), never by
    // gattc_if.  Using ch_by_if here would clear the WRONG channel (e.g. disconnecting
    // controller B drops controller A's slot) and, worse, leave the real channel's
    // s_disc_in_flight stuck → the sequential disconnect queue wedges and only the
    // first controller ever disconnects.  So look the channel up by its bonded address.
    if (event == ESP_GATTC_DISCONNECT_EVT) {
        int dch = ch_by_bda(param->disconnect.remote_bda);
        {   // Diagnostic: the BLE disconnect reason is the KEY clue for a failed 3rd link.
            // Common reasons: 0x08 supervision timeout, 0x13 remote terminated,
            // 0x16 local terminated, 0x22 LL response timeout, 0x28 LL instant passed,
            // 0x3B unacceptable conn params, 0x3E connection failed to be established.
            const uint8_t *d = param->disconnect.remote_bda;
            int ud, rd; ch_count(&ud, &rd);
            char dbg[140];
            snprintf(dbg, sizeof(dbg),
                "DISCONNECT_EVT bda=%02X:%02X:%02X:%02X:%02X:%02X reason=0x%02x ch=%d ready=%d (used=%d ready_cnt=%d)",
                d[0],d[1],d[2],d[3],d[4],d[5], param->disconnect.reason, dch,
                (dch >= 0 ? s_ch[dch].ready : -1), ud, rd);
            out_debug(dbg);
        }
        // Always free the connection control block (conn_id) or it leaks → later opens
        // fail with 133 and the stack asserts.
        esp_gatt_if_t disconnect_if =
            dch >= 0 ? s_ch[dch].gattc_if : gattc_if;
        esp_ble_gattc_close(disconnect_if, param->disconnect.conn_id);

        if (dch >= 0) {
            standalone_xinput_forget_channel(dch);
            if (!s_ch[dch].ready) {
                char b[100];
                snprintf(b, sizeof(b),
                    "{\"cmd\":\"connect_fail\",\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}\n",
                    s_ch[dch].bda[0],s_ch[dch].bda[1],s_ch[dch].bda[2],
                    s_ch[dch].bda[3],s_ch[dch].bda[4],s_ch[dch].bda[5]);
                out_event(b);
            } else {
                char b[80]; snprintf(b, sizeof(b), "{\"cmd\":\"disconnected\",\"channel\":%d}\n", dch);
                out_event(b);
            }
            clear_channel_state(dch);
        }
        // If a 3rd-link attempt aborted (cancelled before becoming ready) and nothing is
        // still being established, stop the temporary widen.  Then reconcile intervals:
        // dropping from 3 links to 2 restores everyone to 7.5ms; an aborted 3rd also
        // restores the (temporarily widened) existing links to 7.5ms.  reconcile bails on
        // its own while a connection is still in progress.
        if (s_widened_mask) {
            bool connecting = false;
            for (int i = 0; i < MAX_CH; i++) if (s_ch[i].connecting) connecting = true;
            if (!connecting && s_pending_conn < 0) s_widened_mask = 0;
        }
        reconcile_intervals();
        // Advance the sequential disconnect queue regardless of whether a channel
        // matched — an unmatched event (already-cleared slot) must NOT leave the
        // in-flight flag set, or the queue wedges.
        portENTER_CRITICAL(&s_disc_mux);
        s_disc_in_flight = false;
        bool queue_empty = (s_disc_mask == 0);
        portEXIT_CRITICAL(&s_disc_mux);
        if (queue_empty && s_scan_mode) s_resume_scan = true;
        kick_disc_queue();
        return;
    }

    int ch = ch_by_if(gattc_if);
    if (ch < 0) return;

    switch (event) {
    case ESP_GATTC_OPEN_EVT:
        if (!s_ch[ch].used) {
            esp_ble_gattc_close(gattc_if, param->open.conn_id);
            break;
        }

        if (param->open.status != ESP_GATT_OK) {
            // Emit connect_fail JSON (mirrors NimBLE's BLE_GAP_EVENT_CONNECT status!=0 path)
            // so the Python host clears its "connecting" state and stops retrying.
            // Without this the host retries endlessly → repeated open-fail cycles →
            // GATTC conn_id leak / BLE controller assert → crash.
            char b[100];
            snprintf(b, sizeof(b),
                "{\"cmd\":\"connect_fail\",\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}\n",
                s_ch[ch].bda[0],s_ch[ch].bda[1],s_ch[ch].bda[2],
                s_ch[ch].bda[3],s_ch[ch].bda[4],s_ch[ch].bda[5]);
            out_event(b);
            int uo, ro; ch_count(&uo, &ro);
            char dbg[110];
            snprintf(dbg, sizeof(dbg),
                "OPEN_EVT FAIL ch=%d status=%d conn_id=%d (used=%d ready=%d)",
                ch, param->open.status, param->open.conn_id, uo, ro);
            out_debug(dbg);
            // CRUCIAL: a failed open still allocates a GATTC connection control block.
            // Must esp_ble_gattc_close() to release the conn_id, or it leaks and after a
            // few attempts esp_ble_gattc_open returns 133 and the stack asserts -> crash.
            esp_ble_gattc_close(gattc_if, param->open.conn_id);
            clear_channel_state(ch);
            restore_widened_links();
            gap_busy(300);
            if (s_scan_mode) s_resume_scan = true;
            break;
        }
        s_ch[ch].conn_id = param->open.conn_id;
        s_ch[ch].connecting = false;
        s_ch[ch].link_open = true;
        {   char dbg[90]; int uo, ro; ch_count(&uo, &ro);
            snprintf(dbg, sizeof(dbg), "OPEN_EVT OK ch=%d conn_id=%d (used=%d ready=%d) -> discovering",
                     ch, param->open.conn_id, uo, ro);
            out_debug(dbg); }
        esp_ble_gattc_send_mtu_req(gattc_if, param->open.conn_id);
        esp_ble_gattc_search_service(gattc_if, param->open.conn_id, NULL);
        break;

    case ESP_GATTC_SEARCH_RES_EVT: {
        const esp_bt_uuid_t *su = &param->search_res.srvc_id.uuid;
        bool is_sw2 = (su->len == ESP_UUID_LEN_128 &&
                       memcmp(su->uuid.uuid128, UUID_SVC_SW2.uuid.uuid128, ESP_UUID_LEN_128) == 0);
        scan_service_chars(ch, param->search_res.start_handle, param->search_res.end_handle, is_sw2);
        break;
    }

    case ESP_GATTC_SEARCH_CMPL_EVT: {
        // Choose the input stream now that all characteristics are known.  The NSO
        // GameCube controller (prefer_legacy) uses the LEGACY characteristic; every
        // other SW2 controller uses FD2.  Fall back to whichever is present.
        s_ch[ch].input_handle = choose_input_handle(ch, s_ch[ch].prefer_legacy, &s_ch[ch].input_src);
        char b[176];
        snprintf(b, sizeof(b), "discovered ch=%d input=0x%04x(src=%u prefer_legacy=%d notify=%u fd2=0x%04x legacy=0x%04x) ack=0x%04x cmd=0x%04x rumble=0x%04x",
                 ch, s_ch[ch].input_handle, s_ch[ch].input_src, s_ch[ch].prefer_legacy, s_ch[ch].notify_count,
                 s_ch[ch].fd2_handle, s_ch[ch].legacy_handle,
                 s_ch[ch].ack_handle, s_ch[ch].cmd_handle, s_ch[ch].rumble_handle);
        out_debug(b);
        snprintf(b, sizeof(b), "{\"cmd\":\"gatt_done\",\"channel\":%d}\n", ch);
        out_event(b);
        enable_notifications(ch);
        break;
    }

    case ESP_GATTC_REG_FOR_NOTIFY_EVT: {
        uint16_t h = param->reg_for_notify.handle;
        if (param->reg_for_notify.status != ESP_GATT_OK) {
            char debug[96];
            snprintf(
                debug, sizeof(debug),
                "register notify failed ch=%d handle=0x%04x status=%d",
                ch, h, param->reg_for_notify.status
            );
            out_debug(debug);
            break;
        }
        // Non-GCN: enable this char's CCCD immediately (unchanged).  GCN channels enable every
        // SW2 notify CCCD via the sequential drain instead (start_cccd_drain), so skip here to
        // avoid issuing a descriptor write on top of the drain's in-flight write.
        if (!ch_uses_notify_all(ch)) {
            esp_gattc_descr_elem_t descr; uint16_t got = 1;
            if (esp_ble_gattc_get_descr_by_char_handle(gattc_if, s_ch[ch].conn_id, h, UUID_CCCD,
                                                       &descr, &got) == ESP_OK && got > 0) {
                uint8_t v[2] = {0x01, 0x00};
                if (h == s_ch[ch].input_handle)
                    s_ch[ch].input_cccd_handle = descr.handle;
                esp_err_t err = esp_ble_gattc_write_char_descr(
                    gattc_if, s_ch[ch].conn_id, descr.handle, sizeof(v), v,
                    ESP_GATT_WRITE_TYPE_RSP, ESP_GATT_AUTH_REQ_NONE
                );
                if (err != ESP_OK) {
                    if (s_ch[ch].input_cccd_handle == descr.handle)
                        s_ch[ch].input_cccd_handle = 0;
                    char debug[96];
                    snprintf(
                        debug, sizeof(debug),
                        "CCCD write request failed ch=%d handle=0x%04x: %s",
                        ch, h, esp_err_to_name(err)
                    );
                    out_debug(debug);
                }
            }
        }
        break;
    }

    case ESP_GATTC_NOTIFY_EVT: {
        int64_t callback_started_us = ble_callback_metrics_start();
        uint8_t len = param->notify.value_len > REPORT_SIZE ? REPORT_SIZE : param->notify.value_len;
        if (ch_uses_notify_all(ch)) {
            bool is_input =
                param->notify.handle == s_ch[ch].input_handle;
            if (is_input)
                note_ble_input_report(ch, param->notify.value, len);
            in_report_t n;
            n.ch = ch; n.len = len; n.handle = param->notify.handle;
            n.generation = s_ch[ch].generation;
            memcpy(n.data, param->notify.value, len);
            if (s_notify_queue) {
                if (xQueueSend(s_notify_queue, &n, 0) == pdTRUE) {
                    if (s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
                } else if (is_input) {
                    portENTER_CRITICAL(&s_in_mux);
                    s_input_latency_metrics.notify_queue_drops++;
                    portEXIT_CRITICAL(&s_in_mux);
                }
            }
        } else if (param->notify.handle == s_ch[ch].input_handle) {
            note_ble_input_report(ch, param->notify.value, len);
            portENTER_CRITICAL(&s_in_mux);
            if (s_in_dirty[ch])
                s_input_latency_metrics.shadow_overwrites++;
            s_in_shadow[ch].ch = ch; s_in_shadow[ch].len = len;
            s_in_shadow[ch].handle = 0;
            s_in_shadow[ch].generation = s_ch[ch].generation;
            memcpy(s_in_shadow[ch].data, param->notify.value, len);
            s_in_dirty[ch] = true;
            portEXIT_CRITICAL(&s_in_mux);
            if (s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
        } else if (param->notify.handle == s_ch[ch].ack_handle && s_ack_queue) {
            in_report_t a;
            a.ch = ch;
            a.len = len;
            a.handle = param->notify.handle;
            a.generation = s_ch[ch].generation;
            memcpy(a.data, param->notify.value, len);
            if (xQueueSend(s_ack_queue, &a, 0) == pdTRUE && s_cdc_task_h)
                xTaskNotifyGive(s_cdc_task_h);
        }
        ble_callback_metrics_record(callback_started_us);
        break;
    }

    case ESP_GATTC_WRITE_CHAR_EVT:
        note_gatt_write_complete(
            ch, param->write.conn_id,
            param->write.handle, param->write.status
        );
        break;

    case ESP_GATTC_CONGEST_EVT:
        note_gatt_congestion(
            ch, param->congest.conn_id,
            GATT_OUTPUT_BLOCK_CONGESTED,
            param->congest.congested
        );
        break;

    case ESP_GATTC_QUEUE_FULL_EVT:
        note_gatt_congestion(
            ch, param->queue_full.conn_id,
            GATT_OUTPUT_BLOCK_QUEUE_FULL,
            param->queue_full.is_full
        );
        break;

    case ESP_GATTC_WRITE_DESCR_EVT:
        if (param->write.handle == s_ch[ch].input_cccd_handle) {
            if (param->write.status == ESP_GATT_OK) {
                mark_channel_ready(ch);
            } else {
                char debug[96];
                snprintf(
                    debug, sizeof(debug),
                    "input CCCD failed ch=%d handle=0x%04x status=%d",
                    ch, param->write.handle, param->write.status
                );
                out_debug(debug);
                s_ch[ch].input_cccd_handle = 0;
            }
        }
        // GCN CCCD drain: one descriptor write completed -> enable the next.
        if (s_ch[ch].cccd_draining) {
            s_ch[ch].cccd_idx++;
            cccd_drain_step(ch);
        }
        break;

    // ESP_GATTC_DISCONNECT_EVT is handled above (routed by remote_bda, not gattc_if).

    default: break;
    }
}

// --- GAP: scan + report Nintendo controllers ---
static esp_ble_scan_params_t s_scan_params = {
    .scan_type          = BLE_SCAN_TYPE_ACTIVE,
    .own_addr_type      = BLE_ADDR_TYPE_PUBLIC,
    .scan_filter_policy = BLE_SCAN_FILTER_ALLOW_ALL,
    // Aligned to the NimBLE build's general-scan params (ble_gap_disc_params):
    // itvl = window = 0x30 (30 ms, 100% duty cycle).  The previous 0x20/0x08
    // (20 ms / 5 ms = 25% duty) missed most advertisements.
    .scan_interval      = 0x30,
    .scan_window        = 0x30,
    // Report EVERY advertisement (no controller-side dedup): a controller that fails a
    // connect (e.g. bonded to another host, reconnecting without sync) must stay
    // re-discoverable.  Dedup cached its address and hid it forever.  The scan_result
    // flood is now harmless — it goes through the non-blocking out_queue.
    .scan_duplicate     = BLE_SCAN_DUPLICATE_DISABLE,
};
static bool adv_is_nintendo(uint8_t *adv) {
    uint8_t mlen = 0;
    uint8_t *mfg = esp_ble_resolve_adv_data(adv, ESP_BLE_AD_MANUFACTURER_SPECIFIC_TYPE, &mlen);
    return (mfg && mlen >= 2 && ((uint16_t)mfg[0] | ((uint16_t)mfg[1] << 8)) == NINTENDO_COMPANY_ID);
}
static bool adv_get_reconnect_mac(uint8_t *adv, uint64_t *reconnect_mac) {
    uint8_t mlen = 0;
    uint8_t *mfg = esp_ble_resolve_adv_data(
        adv, ESP_BLE_AD_MANUFACTURER_SPECIFIC_TYPE, &mlen
    );
    if (!mfg || mlen < 18) return false;
    if (((uint16_t)mfg[0] | ((uint16_t)mfg[1] << 8)) != NINTENDO_COMPANY_ID)
        return false;
    if (((uint16_t)mfg[5] | ((uint16_t)mfg[6] << 8)) != NINTENDO_VENDOR_ID)
        return false;
    if (((uint16_t)mfg[7] | ((uint16_t)mfg[8] << 8)) != PRO_CONTROLLER2_PID)
        return false;
    uint64_t value = 0;
    for (int i = 0; i < 6; i++)
        value |= ((uint64_t)mfg[12 + i]) << (i * 8);
    if (reconnect_mac) *reconnect_mac = value;
    return true;
}
static void gap_cb(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
    switch (event) {
    case ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT:
        s_scan_params_ready =
            param->scan_param_cmpl.status == ESP_BT_STATUS_SUCCESS;
        if (s_scan_params_ready && s_scan_mode) {
            s_resume_scan = true;
            if (s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
        } else if (!s_scan_params_ready) {
            out_debug("scan parameter setup failed");
        }
        break;
    case ESP_GAP_BLE_SCAN_START_COMPLETE_EVT:
        s_scan_start_pending = false;
        if (param->scan_start_cmpl.status == ESP_BT_STATUS_SUCCESS) {
            s_scanning = true;
            s_resume_scan = false;
            if (s_scan_stop_needed) request_scan_stop();
        } else {
            s_scanning = false;
            s_resume_scan = s_scan_mode;
            s_scan_retry_after_ms = now_ms() + 250u;
            char debug[96];
            snprintf(
                debug, sizeof(debug), "scan start event failed: status=%d",
                param->scan_start_cmpl.status
            );
            out_debug(debug);
        }
        if (s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
        break;
    case ESP_GAP_BLE_UPDATE_CONN_PARAMS_EVT: {
        int ch = ch_by_bda(param->update_conn_params.bda);
        {   char dbg[110];
            snprintf(dbg, sizeof(dbg),
                "UPDATE_CONN_PARAMS ch=%d status=%d itvl=%d latency=%d timeout=%d",
                ch, param->update_conn_params.status, param->update_conn_params.conn_int,
                param->update_conn_params.latency, param->update_conn_params.timeout);
            out_debug(dbg); }
        // Only act on a SUCCESSFUL update (status==0).  A failed update (e.g. status 19
        // when the controller can't grant 7.5ms while 3 links are active) must NOT be
        // re-attempted here, or it loops forever re-requesting the same rejected value.
        // Also skip channels we INTENTIONALLY widened during a 3rd-link setup.
        if (ch >= 0 && s_ch[ch].used && param->update_conn_params.status == 0
                && !(s_widened_mask & (1u << ch))) {
            // Re-assert THIS channel's target interval if the negotiated one deviated
            // (e.g. a Nintendo L2CAP update request).  Use the per-channel target.
            uint16_t target = s_ch[ch].itvl ? s_ch[ch].itvl : 6;
            if (param->update_conn_params.conn_int != target) {
                esp_ble_conn_update_params_t cp = {0};
                memcpy(cp.bda, param->update_conn_params.bda, sizeof(esp_bd_addr_t));
                cp.min_int = target; cp.max_int = target; cp.latency = 0; cp.timeout = 400;
                esp_ble_gap_update_conn_params(&cp);
            }
        }
        break;
    }
    case ESP_GAP_BLE_READ_RSSI_COMPLETE_EVT: {
        int ch = ch_by_bda(param->read_rssi_cmpl.remote_addr);
        if (ch >= 0 && s_ch[ch].used) {
            s_ch[ch].rssi_valid = (
                param->read_rssi_cmpl.status == ESP_BT_STATUS_SUCCESS &&
                param->read_rssi_cmpl.rssi != 127
            );
            if (s_ch[ch].rssi_valid) {
                s_ch[ch].rssi_dbm = param->read_rssi_cmpl.rssi;
                s_ch[ch].rssi_updated_ms = now_ms();
            }
        }
        break;
    }
    case ESP_GAP_BLE_SCAN_STOP_COMPLETE_EVT:
        s_scan_stop_pending = false;
        if (param->scan_stop_cmpl.status == ESP_BT_STATUS_SUCCESS) {
            s_scanning = false;
            s_scan_stop_needed = false;
        } else {
            s_scan_stop_needed = true;
            s_scan_retry_after_ms = now_ms() + 250u;
            char debug[96];
            snprintf(
                debug, sizeof(debug), "scan stop event failed: status=%d",
                param->scan_stop_cmpl.status
            );
            out_debug(debug);
            if (s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
            break;
        }
        // Immediate open for the 1st/2nd link.  The 3rd-link case (s_conn_open_after set)
        // is deferred: cdc_task opens it once the temporary widen of the existing links
        // has settled.
        if (s_pending_conn >= 0 && s_conn_open_after == 0)
            open_pending_conn();
        if (s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
        break;
    case ESP_GAP_BLE_SCAN_RESULT_EVT: {
        esp_ble_gap_cb_param_t *r = param;
        if (r->scan_rst.search_evt != ESP_GAP_SEARCH_INQ_RES_EVT) break;
        if (!s_scan_mode || !adv_is_nintendo(r->scan_rst.ble_adv)) break;
        const uint8_t *a = r->scan_rst.bda;

        uint64_t reconnect_mac = 0;
        bool has_reconnect_mac = adv_get_reconnect_mac(
            r->scan_rst.ble_adv, &reconnect_mac
        );

        if (s_standalone_mode && !s_standalone_auto_conn_pending) {
            int used = 0;
            ch_count(&used, NULL);
            bool controller_targets_this_host =
                has_reconnect_mac &&
                (reconnect_mac == 0 || reconnect_mac == s_own_mac_value);
            if (
                used == 0 && s_pending_conn < 0 &&
                controller_targets_this_host
            ) {
                snprintf(
                    s_standalone_auto_conn,
                    sizeof(s_standalone_auto_conn),
                    "%d %02X:%02X:%02X:%02X:%02X:%02X",
                    r->scan_rst.ble_addr_type,
                    a[0], a[1], a[2], a[3], a[4], a[5]
                );
                s_standalone_auto_conn_pair_required =
                    (reconnect_mac == 0);
                s_standalone_auto_conn_pending = true;
                if (s_cdc_task_h) xTaskNotifyGive(s_cdc_task_h);
            }
        }

        char data_hex[63]; int dl = r->scan_rst.adv_data_len; if (dl > 31) dl = 31;
        for (int i = 0; i < dl; i++) sprintf(&data_hex[i*2], "%02X", r->scan_rst.ble_adv[i]);
        data_hex[dl*2] = '\0';
        char b[256];
        snprintf(b, sizeof(b),
            "{\"cmd\":\"scan_result\",\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\",\"type\":%d,"
            "\"rssi\":%d,\"data\":\"%s\",\"directed\":0}\n",
            a[0],a[1],a[2],a[3],a[4],a[5], r->scan_rst.ble_addr_type, r->scan_rst.rssi, data_hex);
        out_json(b);
        break;
    }
    default: break;
    }
}

void app_main(void) {
    ESP_LOGI(
        TAG, "%s %s (%s %s)",
        APP_FIRMWARE_PRODUCT, APP_FIRMWARE_VERSION,
        APP_PROTOCOL_NAME, APP_PROTOCOL_VERSION
    );
    for (int i = 0; i < MAX_CH; i++) s_ch[i].gattc_if = ESP_GATT_IF_NONE;

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase()); ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    s_output_mode = standalone_output_mode_load();
    s_active_output_mode = standalone_output_mode_resolve(
        s_output_mode, &s_standalone_auto_probe
    );
    s_standalone_mode =
        s_active_output_mode != STANDALONE_OUTPUT_BRIDGE;
    s_standalone_usb_hid =
        s_active_output_mode == STANDALONE_OUTPUT_HID;
    bool standalone_profile_loaded = standalone_profile_load_runtime();

    s_control_queue = xQueueCreate(16, sizeof(line_t));
    s_query_queue = xQueueCreate(16, sizeof(line_t));
    s_ack_queue = xQueueCreate(16, sizeof(in_report_t));
    s_notify_queue = xQueueCreate(32, sizeof(in_report_t));
    s_event_queue = xQueueCreate(16, sizeof(line_t));
    s_out_queue = xQueueCreate(24, sizeof(line_t));
    ESP_ERROR_CHECK(
        s_control_queue && s_query_queue &&
        s_ack_queue && s_notify_queue &&
        s_event_queue && s_out_queue
            ? ESP_OK : ESP_ERR_NO_MEM
    );

    tinyusb_config_t tusb_cfg = { 0 };
    standalone_xinput_configure(
        &tusb_cfg, s_output_mode, s_active_output_mode,
        s_standalone_auto_probe
    );
    ESP_ERROR_CHECK(tinyusb_driver_install(&tusb_cfg));
    tinyusb_config_cdcacm_t acm = {
        .usb_dev = TINYUSB_USBDEV_0, .cdc_port = TINYUSB_CDC_ACM_0,
        .rx_unread_buf_sz = 1024, .callback_rx = &tinyusb_cdc_rx_callback,
    };
    ESP_ERROR_CHECK(tusb_cdc_acm_init(&acm));
    /*
     * Profile commit parses up to 8 KiB of JSON and builds a complete
     * standalone_runtime_config_t before atomically swapping it in.  The old
     * 4096-byte stack was marginal once calibration, gyro and mapping data
     * were all present: the NVS commit could succeed, then the CDC task would
     * corrupt its stack before returning the acknowledgement.  Keep enough
     * headroom for cJSON and the full runtime snapshot.
     */
    ESP_ERROR_CHECK(
        xTaskCreatePinnedToCore(
            cdc_task, "cdc_task", 8192, NULL, 10, &s_cdc_task_h, 1
        ) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM
    );
    standalone_xinput_set_wakeup_cb(wake_standalone_output);

    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));
    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_bt_controller_init(&bt_cfg));
    ESP_ERROR_CHECK(esp_bt_controller_enable(ESP_BT_MODE_BLE));
    ESP_ERROR_CHECK(esp_bluedroid_init());
    ESP_ERROR_CHECK(esp_bluedroid_enable());

    const uint8_t *mac = esp_bt_dev_get_address();
    if (mac) {
        snprintf(s_own_mac, sizeof(s_own_mac), "%02X:%02X:%02X:%02X:%02X:%02X",
                 mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
        s_own_mac_value = 0;
        for (int i = 0; i < 6; i++)
            s_own_mac_value = (s_own_mac_value << 8) | mac[i];
    }

    ESP_ERROR_CHECK(esp_ble_gap_register_callback(gap_cb));
    ESP_ERROR_CHECK(esp_ble_gattc_register_callback(gattc_cb));
    // Register one GATTC app per channel.  Non-fatal: if the Bluedroid app table is
    // smaller than MAX_CH, the extra channels just stay unusable (gattc_if == NONE).
    for (int i = 0; i < MAX_CH; i++) {
        esp_err_t e = esp_ble_gattc_app_register(i);
        if (e != ESP_OK) ESP_LOGW(TAG, "gattc app %d register failed: %s", i, esp_err_to_name(e));
    }
    ESP_ERROR_CHECK(esp_ble_gatt_set_local_mtu(247));
    s_rumble_queue = xQueueCreate(RUMBLE_QUEUE_SIZE, sizeof(rumble_pkt_t));
    ESP_ERROR_CHECK(s_rumble_queue ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(
        xTaskCreatePinnedToCore(
            rumble_playout_task, "rumble_task", 4096, NULL, 5,
            &s_rumble_task_h, 0
        ) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM
    );

    if (s_standalone_mode) {
        s_scan_mode = true;
        s_resume_scan = true;
    }
    ESP_ERROR_CHECK(esp_ble_gap_set_scan_params(&s_scan_params));

    ESP_LOGI(
        TAG, "Bluedroid up, MAC=%s, %d GATTC apps. Mode=%s, profile=%s.",
        s_own_mac, MAX_CH,
        output_mode_name(s_active_output_mode),
        standalone_profile_loaded ? "loaded" : "defaults"
    );
}
