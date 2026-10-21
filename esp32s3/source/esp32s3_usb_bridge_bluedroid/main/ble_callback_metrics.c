#include "ble_callback_metrics.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

#define BLE_CALLBACK_SAMPLE_COUNT 256

static uint32_t s_samples[BLE_CALLBACK_SAMPLE_COUNT];
static uint16_t s_next;
static uint16_t s_count;
static uint32_t s_maximum_us;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

int64_t ble_callback_metrics_start(void) {
    return esp_timer_get_time();
}

void ble_callback_metrics_record(int64_t started_us) {
    int64_t elapsed_us = esp_timer_get_time() - started_us;
    uint32_t sample_us = elapsed_us < 0
        ? 0
        : elapsed_us > UINT32_MAX ? UINT32_MAX : (uint32_t)elapsed_us;
    portENTER_CRITICAL(&s_mux);
    s_samples[s_next] = sample_us;
    s_next = (uint16_t)((s_next + 1) % BLE_CALLBACK_SAMPLE_COUNT);
    if (s_count < BLE_CALLBACK_SAMPLE_COUNT) s_count++;
    if (sample_us > s_maximum_us) s_maximum_us = sample_us;
    portEXIT_CRITICAL(&s_mux);
}

static int compare_u32(const void *left, const void *right) {
    uint32_t left_value = *(const uint32_t *)left;
    uint32_t right_value = *(const uint32_t *)right;
    return left_value > right_value ? 1 : left_value < right_value ? -1 : 0;
}

void ble_callback_metrics_format(char *output, size_t size) {
    uint32_t samples[BLE_CALLBACK_SAMPLE_COUNT];
    uint16_t count;
    uint32_t maximum;
    portENTER_CRITICAL(&s_mux);
    count = s_count;
    maximum = s_maximum_us;
    memcpy(samples, s_samples, count * sizeof(samples[0]));
    portEXIT_CRITICAL(&s_mux);
    qsort(samples, count, sizeof(samples[0]), compare_u32);
    uint32_t p95 = count ? samples[((size_t)count * 95u - 1u) / 100u] : 0;
    uint32_t p99 = count ? samples[((size_t)count * 99u - 1u) / 100u] : 0;
    snprintf(
        output, size,
        "{\"cmd\":\"ble_timing\",\"ok\":1,\"samples\":%u,"
        "\"max_us\":%lu,\"p95_us\":%lu,\"p99_us\":%lu}\n",
        (unsigned)count, (unsigned long)maximum,
        (unsigned long)p95, (unsigned long)p99
    );
}

void ble_callback_metrics_reset(void) {
    portENTER_CRITICAL(&s_mux);
    memset(s_samples, 0, sizeof(s_samples));
    s_next = 0;
    s_count = 0;
    s_maximum_us = 0;
    portEXIT_CRITICAL(&s_mux);
}
