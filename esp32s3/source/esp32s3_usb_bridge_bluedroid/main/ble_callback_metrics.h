#pragma once

#include <stddef.h>
#include <stdint.h>

int64_t ble_callback_metrics_start(void);
void ble_callback_metrics_record(int64_t started_us);
void ble_callback_metrics_format(char *output, size_t size);
void ble_callback_metrics_reset(void);
