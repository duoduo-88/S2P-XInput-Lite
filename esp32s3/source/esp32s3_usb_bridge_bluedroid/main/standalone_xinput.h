#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "tinyusb.h"

typedef enum {
    STANDALONE_OUTPUT_BRIDGE = 0,
    STANDALONE_OUTPUT_XINPUT = 1,
    STANDALONE_OUTPUT_HID = 2,
} standalone_output_mode_t;

typedef void (*standalone_xinput_wakeup_cb_t)(void);

standalone_output_mode_t standalone_output_mode_load(void);
esp_err_t standalone_output_mode_store(standalone_output_mode_t mode);
void standalone_xinput_configure(
    tinyusb_config_t *config, bool enabled, bool usb_hid_mode
);
void standalone_xinput_set_wakeup_cb(
    standalone_xinput_wakeup_cb_t callback
);
void standalone_xinput_accept_switch_report(
    int channel, const uint8_t *payload, size_t length
);
void standalone_xinput_forget_channel(int channel);
void standalone_xinput_pump(void);
bool standalone_xinput_idle_disconnect_due(void);
bool standalone_xinput_take_rumble(uint8_t *large_motor, uint8_t *small_motor);
#define STANDALONE_USB_HID_REPORT_SIZE 13
bool standalone_xinput_apply_profile_json(
    const uint8_t *json, size_t length
);
bool standalone_xinput_validate_profile_json(
    const uint8_t *json, size_t length
);
void standalone_xinput_get_rumble_config(
    uint16_t *lf_frequency,
    uint16_t *hf_frequency,
    uint16_t *max_amplitude,
    float *lf_strength,
    float *hf_strength,
    float *lf_curve,
    float *hf_curve,
    float *lf_to_hf,
    float *hf_to_lf
);
void standalone_xinput_set_rumble_ratio(float ratio);
void standalone_xinput_format_runtime_status(char *output, size_t size);
bool standalone_xinput_format_algorithm_test(
    const char *arguments, char *output, size_t size
);
bool standalone_xinput_format_gyro_test(
    const char *arguments, char *output, size_t size
);
