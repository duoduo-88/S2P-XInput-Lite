#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* Owned by cdc_task. Neither USB callbacks nor BLE input refresh this lease. */
#define BRIDGE_HOST_HEARTBEAT_TIMEOUT_MS 8000u
#define BRIDGE_HOST_CDC_GRACE_MS 3000u
typedef struct {
    uint32_t session;
    uint32_t last_ping_ms;
    uint32_t cdc_lost_ms;
    bool armed;
    bool blocked;
    bool stopping;
    bool cdc_lost;
} bridge_host_watchdog_t;

static inline bool bridge_host_begin(
    bridge_host_watchdog_t *host, bool bridge, bool cdc_ready,
    uint32_t session, uint32_t now
) {
    if (!bridge || !cdc_ready || !session || host->stopping ||
        (host->blocked && host->session == session)) return false;
    /* An active owner must end/expire before a different process takes over. */
    if (host->armed && host->session != session) return false;
    host->session = session;
    host->last_ping_ms = now;
    host->armed = true;
    host->blocked = false;
    host->cdc_lost = false;
    return true;
}
static inline bool bridge_host_ping(
    bridge_host_watchdog_t *host, uint32_t session, uint32_t now
) {
    if (!host->armed || host->session != session) return false;
    host->last_ping_ms = now;
    return true;
}
static inline bool bridge_host_end(bridge_host_watchdog_t *host, uint32_t session) {
    if (!host->armed || host->session != session) return false;
    host->armed = false;
    host->blocked = true;
    host->stopping = true;
    return true;
}
static inline bool bridge_host_poll(
    bridge_host_watchdog_t *host, bool bridge, bool cdc_ready, uint32_t now
) {
    if (!bridge) {
        memset(host, 0, sizeof(*host));
        return false;
    }
    if (!host->armed) return false;
    if (cdc_ready) host->cdc_lost = false;
    else if (!host->cdc_lost) {
        host->cdc_lost = true;
        host->cdc_lost_ms = now;
    }
    if ((uint32_t)(now - host->last_ping_ms) >= BRIDGE_HOST_HEARTBEAT_TIMEOUT_MS ||
        (host->cdc_lost &&
         (uint32_t)(now - host->cdc_lost_ms) >= BRIDGE_HOST_CDC_GRACE_MS)) {
        return bridge_host_end(host, host->session);
    }
    return false;
}
static inline void bridge_host_finish(bridge_host_watchdog_t *host) {
    host->stopping = false;
}
