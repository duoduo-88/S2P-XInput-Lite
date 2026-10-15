/* Deterministic event schedules around the actual main.c lifecycle functions.
 * Only ESP-IDF/USB calls and the clock are stubbed. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "bridge_host_watchdog.h"
#define MAX_CH 8
#define ESP_OK 0
#define __ATOMIC_ACQUIRE 0
#define __ATOMIC_RELEASE 0
#ifdef _MSC_VER
#define __atomic_load_n(p, order) (*(p))
#define __atomic_store_n(p, value, order) (*(p) = (value))
#endif
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
#define ESP_LOGW(...) ((void)0)
typedef uint8_t esp_bd_addr_t[6];
typedef int esp_err_t;
enum { STANDALONE_OUTPUT_BRIDGE, STANDALONE_OUTPUT_XINPUT,
       STANDALONE_OUTPUT_HID, STANDALONE_OUTPUT_AUTO };
static int s_output_mode, s_active_output_mode;
static bridge_host_watchdog_t s_bridge_host;
static bool s_bridge_host_blocked;
static struct { bool used, link_open, connecting, ready; uint32_t generation;
                uint32_t connect_deadline_ms; uint16_t conn_id;
                esp_bd_addr_t bda; } s_ch[MAX_CH];
static bool s_disc_in_flight, s_scan_mode, s_resume_scan;
static bool s_scanning, s_scan_start_pending, s_scan_stop_pending, s_scan_stop_needed;
static bool s_scan_params_ready = true;
static uint8_t s_disc_mask;
static int s_disc_channel = -1, s_pending_conn = -1, s_disc_mux;
static uint32_t s_disc_generation, s_disc_started_ms, s_gap_busy_until;
static uint32_t s_disc_queued_generation[MAX_CH];
static uint32_t s_scan_retry_after_ms, clock_ms;
static uint32_t s_conn_open_after;
static uint16_t s_disc_conn_id;
static esp_bd_addr_t s_disc_bda;
static bool cdc_ready = true, stop_scan_delayed;
static int disconnect_count, scan_count, disconnect_error;
static uint8_t last_disconnected;
static char last_event[128];
static uint32_t now_ms(void) { return clock_ms; }
static bool time_reached(uint32_t now, uint32_t deadline) {
    return (int32_t)(now - deadline) >= 0;
}
static bool deadline_reached(uint32_t now, uint32_t deadline) {
    return deadline != 0 && time_reached(now, deadline);
}
static void gap_busy(uint32_t ms) {
    if ((int32_t)(now_ms() + ms - s_gap_busy_until) > 0)
        s_gap_busy_until = now_ms() + ms;
}
static esp_err_t esp_ble_gap_disconnect(const uint8_t *bda) {
    disconnect_count++; last_disconnected = bda[0]; return disconnect_error;
}
static esp_err_t esp_ble_gap_start_scanning(int duration) {
    (void)duration; scan_count++; return ESP_OK;
}
static const char *esp_err_to_name(esp_err_t err) { (void)err; return "stub"; }
static bool standalone_has_link(void) { return false; }
static void out_event(const char *message) {
    snprintf(last_event, sizeof(last_event), "%s", message);
}
static void out_debug(const char *message) { (void)message; }
static bool cdc_host_ready(void) { return cdc_ready; }
static bool request_scan_stop(void) {
    s_scan_stop_pending = stop_scan_delayed;
    s_scanning = stop_scan_delayed;
    return true;
}
static void clear_channel_state(int ch) { s_ch[ch].used = false; }
static void emit_connect_fail(int ch, const char *reason) { (void)ch; (void)reason; }
static void restore_widened_links(void) { }

#include "bridge_host_functions.inc"

static void add_link(int ch) {
    s_ch[ch].used = s_ch[ch].link_open = true;
    s_ch[ch].generation++;
    s_ch[ch].conn_id = (uint16_t)(ch + 20);
    s_ch[ch].bda[0] = (uint8_t)(ch + 1);
}
int main(void) {
    clock_ms = 10000;
    handle_bridge_host_command("host begin 123");
    assert(s_bridge_host.armed);
    add_link(0); add_link(1); add_link(2);
    stop_scan_delayed = true;
    s_scan_mode = true;
    handle_bridge_host_command("host end 123");
    assert(s_bridge_host_blocked && !s_scan_mode);
    assert(disconnect_count == 1 && s_disc_in_flight);
    assert(strstr(last_event, "stopping"));
    /* Controller 2 remotely disconnects while controller 0 is being dropped. */
    clear_channel_state(2);
    finish_disc_event(s_ch[2].bda, s_ch[2].conn_id);
    clock_ms += 500;
    kick_disc_queue();
    assert(disconnect_count == 1 && s_disc_in_flight);
    assert(!request_scan_start());
    /* Wrong conn_id and duplicate old events cannot advance the queue. */
    finish_disc_event(s_ch[0].bda, 999);
    assert(s_disc_in_flight);
    clear_channel_state(0);
    finish_disc_event(s_ch[0].bda, s_ch[0].conn_id);
    kick_disc_queue();
    assert(disconnect_count == 2 && last_disconnected == 2);
    finish_disc_event(s_ch[0].bda, s_ch[0].conn_id);
    assert(s_disc_in_flight);
    pump_bridge_host_watchdog();
    assert(strstr(last_event, "stopping"));
    clear_channel_state(1);
    finish_disc_event(s_ch[1].bda, s_ch[1].conn_id);
    clock_ms += 500;
    pump_bridge_host_watchdog();
    assert(s_bridge_host.stopping); /* scan-stop is still asynchronous */
    s_scanning = s_scan_stop_pending = stop_scan_delayed = false;
    pump_bridge_host_watchdog();
    assert(strstr(last_event, "host_released"));
    assert(!s_bridge_host.stopping && s_bridge_host_blocked);
    handle_bridge_host_command("host ping 123");
    handle_bridge_host_command("host begin 123");
    assert(s_bridge_host_blocked);
    handle_bridge_host_command("host begin 456");
    assert(!s_bridge_host_blocked && s_bridge_host.armed);
    s_scan_mode = true;
    assert(request_scan_start()); /* normal next-session reconnect */
    s_scan_start_pending = false;
    /* A lost completion is retried, never acknowledged as released. */
    add_link(0);
    handle_bridge_host_command("host end 456");
    int before_retry = disconnect_count;
    clock_ms += 2000;
    pump_bridge_host_watchdog();
    assert(disconnect_count == before_retry + 1);
    assert(s_bridge_host.stopping && strstr(last_event, "stopping"));
    clear_channel_state(0);
    finish_disc_event(s_ch[0].bda, s_ch[0].conn_id);
    pump_bridge_host_watchdog();
    assert(!s_bridge_host.stopping);
    /* A connecting link cannot count as released until its real event. */
    handle_bridge_host_command("host begin 789");
    add_link(0); s_ch[0].link_open = false; s_ch[0].connecting = true;
    s_ch[0].connect_deadline_ms = clock_ms + 12000;
    handle_bridge_host_command("host end 789");
    before_retry = disconnect_count;
    clock_ms += 12000;
    pump_connection_watchdogs();
    assert(s_ch[0].used); /* setup timeout is not cancellation confirmation */
    pump_bridge_host_watchdog();
    assert(s_bridge_host.stopping && disconnect_count == before_retry);
    s_ch[0].link_open = true; /* asynchronous OPEN_EVT */
    clock_ms += 500;
    pump_bridge_host_watchdog();
    assert(disconnect_count == before_retry + 1);
    clear_channel_state(0);
    finish_disc_event(s_ch[0].bda, s_ch[0].conn_id);
    pump_bridge_host_watchdog();
    /* Synchronous GAP rejection retains the request for deferred retry. */
    handle_bridge_host_command("host begin 888");
    add_link(0); disconnect_error = 1;
    clock_ms += 500;
    handle_bridge_host_command("host end 888");
    assert(!s_disc_in_flight && s_disc_mask);
    disconnect_error = 0; clock_ms += 500;
    pump_bridge_host_watchdog();
    assert(s_disc_in_flight);
    clear_channel_state(0);
    finish_disc_event(s_ch[0].bda, s_ch[0].conn_id);
    pump_bridge_host_watchdog();
    /* All standalone enums bypass host commands and CDC/heartbeat expiry. */
    for (int mode = STANDALONE_OUTPUT_XINPUT; mode <= STANDALONE_OUTPUT_AUTO; mode++) {
        memset(&s_bridge_host, 0, sizeof(s_bridge_host));
        s_bridge_host_blocked = false;
        s_output_mode = mode;
        s_active_output_mode = mode == STANDALONE_OUTPUT_AUTO ? STANDALONE_OUTPUT_HID : mode;
        add_link(0); cdc_ready = false;
        s_scan_mode = true;
        before_retry = disconnect_count;
        handle_bridge_host_command("host begin 999");
        handle_bridge_host_command("host end 999");
        clock_ms += 100000;
        pump_bridge_host_watchdog();
        assert(s_ch[0].used && !s_bridge_host.armed && !s_bridge_host_blocked);
        assert(s_scan_mode && disconnect_count == before_retry);
        s_scan_start_pending = false;
        assert(request_scan_start()); /* existing wake/reconnect still permitted */
    }
    s_output_mode = STANDALONE_OUTPUT_AUTO;
    s_active_output_mode = STANDALONE_OUTPUT_BRIDGE;
    assert(!bridge_host_mode());
    s_output_mode = STANDALONE_OUTPUT_BRIDGE;
    s_active_output_mode = STANDALONE_OUTPUT_HID;
    assert(!bridge_host_mode());
    /* A queued request for an old generation cannot disconnect a reused slot. */
    s_active_output_mode = STANDALONE_OUTPUT_BRIDGE;
    memset(s_ch, 0, sizeof(s_ch));
    s_disc_mask = 0; s_disc_in_flight = false;
    s_gap_busy_until = clock_ms;
    add_link(0);
    enqueue_disc_locked(0);
    clear_channel_state(0);
    add_link(0);
    before_retry = disconnect_count;
    kick_disc_queue();
    assert(disconnect_count == before_retry && s_disc_mask == 0);
    /* Existing idle disconnect may resume scan and button wake in the same session. */
    s_scan_mode = true; s_scan_start_pending = false;
    s_scan_stop_pending = s_scan_stop_needed = s_scanning = false;
    enqueue_disc_locked(0);
    kick_disc_queue();
    assert(!request_scan_start());
    clear_channel_state(0);
    finish_disc_event(s_ch[0].bda, s_ch[0].conn_id);
    clock_ms += 500;
    kick_disc_queue();
    assert(request_scan_start());
    /* Exercise actual integration pump for dropout recovery and host loss. */
    s_scan_start_pending = false;
    cdc_ready = true;
    handle_bridge_host_command("host begin 900");
    add_link(0);
    before_retry = disconnect_count;
    cdc_ready = false;
    pump_bridge_host_watchdog();
    clock_ms += 2999;
    pump_bridge_host_watchdog();
    assert(disconnect_count == before_retry);
    cdc_ready = true;
    pump_bridge_host_watchdog();
    handle_bridge_host_command("host ping 900");
    cdc_ready = false;
    pump_bridge_host_watchdog();
    clock_ms += 3000;
    pump_bridge_host_watchdog();
    assert(disconnect_count == before_retry + 1 && s_bridge_host.stopping);
    clear_channel_state(0);
    finish_disc_event(s_ch[0].bda, s_ch[0].conn_id);
    pump_bridge_host_watchdog();
    cdc_ready = true;
    handle_bridge_host_command("host begin 901");
    add_link(0);
    before_retry = disconnect_count;
    clock_ms += 7999;
    pump_bridge_host_watchdog();
    assert(disconnect_count == before_retry);
    clock_ms++;
    pump_bridge_host_watchdog();
    assert(disconnect_count == before_retry + 1 && s_bridge_host.stopping);
    clear_channel_state(0);
    finish_disc_event(s_ch[0].bda, s_ch[0].conn_id);
    pump_bridge_host_watchdog();
    handle_bridge_host_command("host begin 902");
    add_link(1);
    clock_ms += 500;
    handle_bridge_host_command("host end 902");
    assert(s_disc_channel == 1);
    add_link(0); /* a lower-indexed asynchronous open arrives during teardown */
    enqueue_disc_locked(0);
    clock_ms += 2000;
    pump_bridge_host_watchdog();
    assert(last_disconnected == 2); /* retry current link, don't advance to slot 0 */
    puts("bridge host integration: all scenarios passed");
    return 0;
}
