#include <assert.h>
#include <stdio.h>
#include "bridge_host_watchdog.h"

int main(void) {
    bridge_host_watchdog_t host = {0};
    /* Boot, pairing wait and legacy clients do not arm a session. */
    assert(!bridge_host_poll(&host, true, false, 100000));
    assert(!host.blocked);
    assert(!bridge_host_begin(&host, true, false, 123, 100000));
    assert(bridge_host_begin(&host, true, true, 123, 100000));
    assert(!bridge_host_begin(&host, true, true, 456, 100001));
    assert(host.session == 123 && host.last_ping_ms == 100000);
    assert(!bridge_host_poll(&host, true, true, 107999));
    assert(bridge_host_poll(&host, true, true, 108000));
    assert(host.blocked && host.stopping && !host.armed);
    assert(!bridge_host_poll(&host, true, true, 120000));
    assert(!bridge_host_ping(&host, 123, 120000));
    assert(!bridge_host_begin(&host, true, true, 456, 120000));
    bridge_host_finish(&host);
    /* Buffered pings/begin from the old process cannot resurrect it. */
    assert(!bridge_host_begin(&host, true, true, 123, 120000));
    assert(bridge_host_begin(&host, true, true, 456, 120000));
    assert(!bridge_host_ping(&host, 123, 120001));
    assert(bridge_host_ping(&host, 456, 126000));
    assert(!bridge_host_poll(&host, true, true, 130000));
    /* DTR loss must persist for 3s; a brief dropout recovers. */
    assert(!bridge_host_poll(&host, true, false, 130001));
    assert(!bridge_host_poll(&host, true, false, 132999));
    assert(!bridge_host_poll(&host, true, true, 133000));
    assert(bridge_host_ping(&host, 456, 133000));
    assert(!bridge_host_poll(&host, true, false, 133001));
    assert(bridge_host_poll(&host, true, false, 136001));
    bridge_host_finish(&host);
    assert(bridge_host_begin(&host, true, true, 789, 136002));
    assert(!bridge_host_end(&host, 456));
    assert(bridge_host_end(&host, 789));
    assert(host.blocked && host.stopping && !host.armed);
    bridge_host_finish(&host);
    /* Every standalone mode (XInput, HID, Auto) bypasses all protection. */
    for (int mode = 0; mode < 3; mode++) {
        host = (bridge_host_watchdog_t){0};
        assert(!bridge_host_begin(&host, false, true, 123, 1));
        assert(!bridge_host_poll(&host, false, false, 100000));
        assert(!host.blocked && !host.stopping && !host.armed);
        assert(bridge_host_begin(&host, true, true, 456, 100001));
        assert(!bridge_host_poll(&host, false, false, 200000));
        assert(!host.blocked && !host.stopping && !host.armed);
        assert(bridge_host_begin(&host, true, true, 789, 200001));
    }
    /* Millisecond wrap, including a CDC loss at tick zero. */
    host = (bridge_host_watchdog_t){0};
    assert(bridge_host_begin(&host, true, true, 1, UINT32_MAX - 1000));
    assert(!bridge_host_poll(&host, true, true, 2000));
    assert(bridge_host_poll(&host, true, true, 7000));
    host = (bridge_host_watchdog_t){0};
    assert(bridge_host_begin(&host, true, true, 1, UINT32_MAX - 1));
    assert(!bridge_host_poll(&host, true, false, 0));
    assert(bridge_host_poll(&host, true, false, 3000));
    puts("bridge host watchdog: all scenarios passed");
    return 0;
}
