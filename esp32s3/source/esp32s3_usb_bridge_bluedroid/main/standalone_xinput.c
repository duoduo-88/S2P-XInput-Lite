/*
 * USB XInput device support for S2P-XInput-Lite.
 *
 * The vendor interface, XUSB20 Microsoft OS 2.0 descriptor and 20-byte report
 * layout are based on the MIT-licensed Adafruit TinyUSB XInput example and the
 * MIT-licensed GP2040-CE XInput implementation. This implementation uses a
 * development VID/PID and does not impersonate a retail Xbox controller.
 */
#include "standalone_xinput.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "device/usbd_pvt.h"
#include "esp_timer.h"
#include "FusionAhrs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
#include "tusb.h"

#define STANDALONE_NVS_NAMESPACE "s2p_profile"
#define STANDALONE_MODE_KEY      "standalone"
#define STANDALONE_USB_MODE_KEY  "usb_hid"
#define STANDALONE_OUTPUT_MODE_KEY "output_mode"

#define XINPUT_INTERFACE         2
#define XINPUT_EP_OUT            0x03
#define XINPUT_EP_IN             0x83
#define XINPUT_EP_SIZE           32
#define XINPUT_DESC_LEN          (9 + 16 + 7 + 7)
#define MS_VENDOR_REQUEST        1
#define MS_OS_20_DESC_LEN        0xB2

enum {
    ITF_NUM_CDC = 0,
    ITF_NUM_CDC_DATA,
    ITF_NUM_XINPUT,
    ITF_NUM_TOTAL,
};

#define STANDALONE_CONFIG_LENGTH \
    (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + XINPUT_DESC_LEN)
#define BOS_TOTAL_LEN (TUD_BOS_DESC_LEN + TUD_BOS_MICROSOFT_OS_DESC_LEN)

typedef struct __attribute__((packed, aligned(1))) {
    uint8_t report_id;
    uint8_t report_size;
    uint8_t buttons1;
    uint8_t buttons2;
    uint8_t left_trigger;
    uint8_t right_trigger;
    int16_t left_x;
    int16_t left_y;
    int16_t right_x;
    int16_t right_y;
    uint8_t reserved[6];
} xinput_report_t;

_Static_assert(sizeof(xinput_report_t) == 20, "XInput report must be 20 bytes");

static const tusb_desc_device_t s_xinput_device_descriptor = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0210,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = 0xCAFE,
    .idProduct = 0x4020,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01,
};

static const tusb_desc_device_t s_hid_device_descriptor = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = 0xCAFE,
    .idProduct = 0x4021,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01,
};

static const tusb_desc_device_t s_bridge_device_descriptor = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = 0x303A,
    .idProduct = 0x4001,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01,
};

static const char *s_xinput_string_descriptors[] = {
    (const char[]){0x09, 0x04},
    "S2P-XInput-Lite",
    "S2P Standalone XInput",
    "S2P-XI-DEV1",
};

static const char *s_hid_string_descriptors[] = {
    (const char[]){0x09, 0x04},
    "S2P-XInput-Lite",
    "S2P Mobile Gamepad",
    "S2P-HID-DEV1",
};

static const char *s_bridge_string_descriptors[] = {
    (const char[]){0x09, 0x04},
    "S2P-XInput-Lite",
    "S2P ESP32 Bridge",
    "S2P-BRIDGE-DEV1",
};

static const uint8_t s_xinput_configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(
        1, ITF_NUM_TOTAL, 0, STANDALONE_CONFIG_LENGTH,
        TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100
    ),
    TUD_CDC_DESCRIPTOR(
        ITF_NUM_CDC, 0, 0x81, 8, 0x02, 0x82, 64
    ),
    /* Xbox 360-compatible vendor interface. */
    9, TUSB_DESC_INTERFACE, ITF_NUM_XINPUT, 0, 2,
        TUSB_CLASS_VENDOR_SPECIFIC, 0x5D, 0x01, 0,
    /* XInput gamepad class-specific descriptor. */
    16, 0x21, U16_TO_U8S_LE(0x0110), 0x01, 0x24,
        XINPUT_EP_IN, 0x14, 0x03, 0x00, 0x03, 0x13,
        XINPUT_EP_OUT, 0x00, 0x03, 0x00,
    7, TUSB_DESC_ENDPOINT, XINPUT_EP_IN, TUSB_XFER_INTERRUPT,
        U16_TO_U8S_LE(XINPUT_EP_SIZE), 1,
    7, TUSB_DESC_ENDPOINT, XINPUT_EP_OUT, TUSB_XFER_INTERRUPT,
        U16_TO_U8S_LE(XINPUT_EP_SIZE), 8,
};

static const uint8_t s_hid_report_descriptor[] = {
    0x05, 0x01,       /* Usage Page (Generic Desktop) */
    0x09, 0x05,       /* Usage (Game Pad) */
    0xA1, 0x01,       /* Collection (Application) */
    0x05, 0x09,       /*   Usage Page (Button) */
    /*
     * Keep the bit order aligned with encode_hid_report, but use Android's
     * standard HID button usages instead of a sequential 1..16 range:
     * A, B, X, Y, L1, R1, Select, Start, LS, RS, Mode, then spare controls.
     * Usages 9/10 are also driven from the analog trigger values so Android
     * games that only consume L2/R2 key events remain compatible.
     */
    0x09, 0x01,
    0x09, 0x02,
    0x09, 0x04,
    0x09, 0x05,
    0x09, 0x07,
    0x09, 0x08,
    0x09, 0x0B,
    0x09, 0x0C,
    0x09, 0x0E,
    0x09, 0x0F,
    0x09, 0x0D,
    0x09, 0x03,
    0x09, 0x06,
    0x09, 0x09,
    0x09, 0x0A,
    0x09, 0x10,
    0x15, 0x00,       /*   Logical Minimum (0) */
    0x25, 0x01,       /*   Logical Maximum (1) */
    0x75, 0x01,       /*   Report Size (1) */
    0x95, 0x10,       /*   Report Count (16) */
    0x81, 0x02,       /*   Input (Data, Variable, Absolute) */
    0x05, 0x01,       /*   Usage Page (Generic Desktop) */
    0x09, 0x39,       /*   Usage (Hat switch) */
    0x15, 0x00,       /*   Logical Minimum (0) */
    0x25, 0x07,       /*   Logical Maximum (7) */
    0x35, 0x00,       /*   Physical Minimum (0) */
    0x46, 0x3B, 0x01, /*   Physical Maximum (315) */
    0x65, 0x14,       /*   Unit (Degrees) */
    0x75, 0x04,       /*   Report Size (4) */
    0x95, 0x01,       /*   Report Count (1) */
    0x81, 0x42,       /*   Input (Data, Variable, Absolute, Null) */
    0x65, 0x00,       /*   Unit (None) */
    0x75, 0x04,       /*   Padding */
    0x95, 0x01,
    0x81, 0x03,       /*   Input (Constant) */
    0x09, 0x30,       /*   Usage (X) */
    0x09, 0x31,       /*   Usage (Y) */
    0x09, 0x32,       /*   Usage (Z / Android right-stick X) */
    0x09, 0x35,       /*   Usage (Rz / Android right-stick Y) */
    0x16, 0x00, 0x80, /*   Logical Minimum (-32768) */
    0x26, 0xFF, 0x7F, /*   Logical Maximum (32767) */
    0x75, 0x10,       /*   Report Size (16) */
    0x95, 0x04,       /*   Report Count (4) */
    0x81, 0x02,       /*   Input (Data, Variable, Absolute) */
    0x05, 0x02,       /*   Usage Page (Simulation Controls) */
    0x09, 0xC5,       /*   Usage (Brake / Android left trigger) */
    0x09, 0xC4,       /*   Usage (Accelerator / Android right trigger) */
    0x15, 0x00,       /*   Logical Minimum (0) */
    0x26, 0xFF, 0x00, /*   Logical Maximum (255) */
    0x75, 0x08,       /*   Report Size (8) */
    0x95, 0x02,       /*   Report Count (2) */
    0x81, 0x02,       /*   Input (Data, Variable, Absolute) */
    0xC0,             /* End Collection */
};

#define HID_CONFIG_LENGTH \
    (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_HID_DESC_LEN)

static const uint8_t s_hid_configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(
        1, ITF_NUM_TOTAL, 0, HID_CONFIG_LENGTH,
        TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100
    ),
    TUD_CDC_DESCRIPTOR(
        ITF_NUM_CDC, 0, 0x81, 8, 0x02, 0x82, 64
    ),
    TUD_HID_DESCRIPTOR(
        ITF_NUM_XINPUT, 0, HID_ITF_PROTOCOL_NONE,
        sizeof(s_hid_report_descriptor), XINPUT_EP_IN, XINPUT_EP_SIZE, 1
    ),
};

#define BRIDGE_CONFIG_LENGTH (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN)

static const uint8_t s_bridge_configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(
        1, ITF_NUM_CDC_DATA + 1, 0, BRIDGE_CONFIG_LENGTH,
        TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100
    ),
    TUD_CDC_DESCRIPTOR(
        ITF_NUM_CDC, 0, 0x81, 8, 0x02, 0x82, 64
    ),
};

static const uint8_t s_bos_descriptor[] = {
    TUD_BOS_DESCRIPTOR(BOS_TOTAL_LEN, 1),
    TUD_BOS_MS_OS_20_DESCRIPTOR(MS_OS_20_DESC_LEN, MS_VENDOR_REQUEST),
};

/*
 * Bind only interface 2 to Windows' XUSB driver. The CDC interfaces stay on
 * usbser.sys, allowing the desktop configurator to change profiles/modes.
 */
static const uint8_t s_ms_os_20_descriptor[MS_OS_20_DESC_LEN] = {
    U16_TO_U8S_LE(0x000A), U16_TO_U8S_LE(MS_OS_20_SET_HEADER_DESCRIPTOR),
    U32_TO_U8S_LE(0x06030000), U16_TO_U8S_LE(MS_OS_20_DESC_LEN),
    U16_TO_U8S_LE(0x0008), U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_CONFIGURATION),
    0, 0, U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A),
    U16_TO_U8S_LE(0x0008), U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_FUNCTION),
    XINPUT_INTERFACE, 0,
    U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A - 0x08),
    U16_TO_U8S_LE(0x0014), U16_TO_U8S_LE(MS_OS_20_FEATURE_COMPATBLE_ID),
    'X', 'U', 'S', 'B', '2', '0', 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A - 0x08 - 0x08 - 0x14),
    U16_TO_U8S_LE(MS_OS_20_FEATURE_REG_PROPERTY),
    U16_TO_U8S_LE(0x0007), U16_TO_U8S_LE(0x002A),
    'D',0,'e',0,'v',0,'i',0,'c',0,'e',0,'I',0,'n',0,'t',0,'e',0,'r',0,
    'f',0,'a',0,'c',0,'e',0,'G',0,'U',0,'I',0,'D',0,'s',0,0,0,
    U16_TO_U8S_LE(0x0050),
    '{',0,'8',0,'D',0,'9',0,'0',0,'8',0,'4',0,'2',0,'C',0,'-',0,
    '1',0,'5',0,'9',0,'4',0,'-',0,'4',0,'1',0,'C',0,'E',0,'-',0,
    'A',0,'A',0,'3',0,'F',0,'-',0,'6',0,'2',0,'D',0,'4',0,'6',0,
    '4',0,'E',0,'1',0,'B',0,'E',0,'7',0,'9',0,'}',0,0,0,0,0,
};

static portMUX_TYPE s_state_mux = portMUX_INITIALIZER_UNLOCKED;
static xinput_report_t s_pending_report = {.report_size = 20};
static xinput_report_t s_tx_report = {.report_size = 20};
static bool s_report_dirty;
static bool s_hid_report_dirty;
static bool s_usb_hid_mode;
static bool s_usb_xinput_mode;
static standalone_xinput_wakeup_cb_t s_wakeup_cb;
static standalone_usb_latency_metrics_t s_usb_latency_metrics;
static bool s_usb_wait_active;
static int64_t s_usb_wait_started_us;
static int s_active_channel = -1;
static uint8_t s_endpoint_in;
static uint8_t s_endpoint_out;
static uint8_t s_out_buffer[XINPUT_EP_SIZE];
static uint8_t s_large_motor;
static uint8_t s_small_motor;
static bool s_rumble_dirty;
static bool s_idle_baseline_valid;
static uint32_t s_idle_buttons;
static uint16_t s_idle_sticks[4];
static int16_t s_idle_gyro[3];
static uint32_t s_last_activity_ms;

static uint32_t idle_now_ms(void) {
    return (uint32_t)(
        xTaskGetTickCount() * (TickType_t)portTICK_PERIOD_MS
    );
}

static void encode_hid_report(
    const xinput_report_t *source,
    uint8_t output[STANDALONE_USB_HID_REPORT_SIZE]
);

#define SOURCE_BUTTON_COUNT 21
#define STANDALONE_LAYER_MAX 8
#define TARGET_LT 0x10000u
#define TARGET_RT 0x20000u

typedef struct {
    float x[5];
    float y[5];
    float deadzone;
    float outer_deadzone;
    float smoothing;
    uint8_t output_shape;
    bool deadzone_compress;
    bool outer_deadzone_compress;
    bool smooth_interpolation;
    float smoothed_magnitude;
    bool smoothing_valid;
    uint32_t smoothing_report_time;
    int16_t center_x;
    int16_t center_y;
    uint16_t max_x;
    uint16_t max_y;
    uint16_t min_x;
    uint16_t min_y;
} stick_runtime_config_t;

typedef enum {
    STICK_DIRECTION_4WAY,
    STICK_DIRECTION_8WAY,
    STICK_DIRECTION_LT,
    STICK_DIRECTION_RT,
    STICK_DIRECTION_UNSUPPORTED,
} stick_direction_mode_t;

typedef struct {
    stick_direction_mode_t mode;
    float trigger_threshold;
    float release_threshold;
    float direction_deadzone;
    uint8_t analog_direction;
    int8_t active_direction;
    uint32_t targets[8];
} stick_direction_runtime_t;

typedef struct {
    uint32_t activation_mask;
    uint8_t specificity;
    bool toggle_mode;
    uint32_t button_targets[SOURCE_BUTTON_COUNT];
    stick_direction_runtime_t directions[2];
} mapping_layer_runtime_t;

typedef struct {
    uint8_t activation_mode;
    bool activation_match_all;
    uint32_t activation_mask;
    bool target_left;
    bool tilt_mode;
    bool tilt_dual;
    float tilt_max_angle;
    float tilt_deadzone;
    float tilt_smoothing_ms;
    float stick_sensitivity;
    float deadzone;
    float anti_deadzone;
    uint8_t response_curve;
    float curve_strength;
    float smoothing_ms;
    bool invert_x;
    bool invert_y;
    float x_ratio;
    float y_ratio;
    float accel_suppression;
    float adaptive_deadzone;
    float button_freeze_ms;
    bool player_space;
    uint32_t stabilization_mask;
    uint32_t recenter_mask;
    uint32_t previous_stabilization;
    bool previous_recenter;
    uint32_t freeze_until_report_time;
    float motion_envelope;
    float gyro_bias[3];
    float accel_bias[3];
    float accel_matrix[9];
    bool accel_calibrated;
    float mag_bias[3];
    float mag_matrix[9];
    bool mag_calibrated;
    FusionAhrs ahrs;
    bool fusion_valid;
    bool nine_axis_has_magnetometer;
    float nine_axis_heading;
    float nine_axis_roll;
    bool nine_axis_orientation_valid;
    FusionQuaternion nine_axis_quaternion;
    bool mag_field_reference_valid;
    float mag_field_reference;
    bool mag_last_valid;
    uint32_t mag_last_valid_time;
    bool mag_recovery_started_valid;
    uint32_t mag_recovery_started_time;
    float mag_recovery_accumulator;
    bool aim_gravity_sign_valid;
    float aim_gravity_sign;
    bool aim_pose_ready_valid;
    uint32_t aim_pose_ready_time;
    float aim_player_space_blend;
    bool tilt_orientation_valid;
    float tilt_orientation_roll;
    float tilt_orientation_pitch;
    FusionQuaternion tilt_neutral_quaternion;
    bool tilt_neutral_quaternion_valid;
    bool impact_gravity_scale_valid;
    float impact_gravity_scale;
    bool impact_accel_lp_valid;
    float impact_accel_lp[3];
    bool impact_last_gyro_valid;
    float impact_last_gyro[3];
    uint32_t bias_block_until;
    uint32_t impact_accel_reject_until;
    uint32_t impact_accel_recover_until;
    bool gyro_bias_saved;
    unsigned int gyro_bias_samples;
    bool gyro_bias_anchor_valid;
    float gyro_bias_anchor[3];
    bool gyro_last_raw_valid;
    float gyro_last_raw[3];
    bool gyro_stationary_started_valid;
    uint32_t gyro_stationary_started_time;
    unsigned int gyro_stationary_samples;
    bool gyro_stationary_accel_reference_valid;
    float gyro_stationary_accel_reference[3];
    bool gyro_stationary_mag_reference_valid;
    float gyro_stationary_mag_reference[3];
    float rumble_ratio;
    bool trigger_previous;
    bool toggle_enabled;
    bool was_active;
    float smoothed_x;
    float smoothed_y;
    bool tilt_neutral_valid;
    float tilt_neutral_heading;
    float tilt_neutral_roll;
    float tilt_heading;
    float tilt_roll;
    uint32_t last_report_time;
    float diagnostic_rate[3];
    float diagnostic_output[2];
    bool diagnostic_active;
} gyro_runtime_t;

typedef struct {
    uint32_t button_targets[SOURCE_BUTTON_COUNT];
    stick_runtime_config_t sticks[2];
    stick_direction_runtime_t directions[2];
    uint16_t lf_frequency;
    uint16_t hf_frequency;
    uint16_t max_amplitude;
    float lf_strength;
    float hf_strength;
    float lf_curve;
    float hf_curve;
    float lf_to_hf;
    float hf_to_lf;
    uint8_t layer_count;
    mapping_layer_runtime_t layers[STANDALONE_LAYER_MAX];
    int8_t toggle_layer;
    uint32_t previous_source_buttons;
    uint8_t idle_disconnect_minutes;
    gyro_runtime_t gyro;
} standalone_runtime_config_t;

static const char *s_source_names[SOURCE_BUTTON_COUNT] = {
    "y","x","b","a","r","zr","minus","plus","r_stk","l_stk",
    "home","capt","c","down","up","right","left","l","gr","gl","zl"
};

static const uint32_t s_source_masks[SOURCE_BUTTON_COUNT] = {
    0x00000001u,0x00000002u,0x00000004u,0x00000008u,
    0x00000040u,0x00000080u,0x00000100u,0x00000200u,
    0x00000400u,0x00000800u,0x00001000u,0x00002000u,
    0x00004000u,0x00010000u,0x00020000u,0x00040000u,
    0x00080000u,0x00400000u,0x01000000u,0x02000000u,
    0x00800000u
};

static standalone_runtime_config_t s_runtime = {
    .idle_disconnect_minutes = 15,
    .sticks = {
        {
            .x = {0.0f,0.25f,0.50f,0.75f,1.0f},
            .y = {0.0f,0.25f,0.50f,0.75f,1.0f},
            .deadzone = 0.03f,
            .center_x = 2048, .center_y = 2048,
            .max_x = 2048, .max_y = 2048,
            .min_x = 2048, .min_y = 2048,
        },
        {
            .x = {0.0f,0.25f,0.50f,0.75f,1.0f},
            .y = {0.0f,0.25f,0.50f,0.75f,1.0f},
            .deadzone = 0.03f,
            .center_x = 2048, .center_y = 2048,
            .max_x = 2048, .max_y = 2048,
            .min_x = 2048, .min_y = 2048,
        },
    },
    .lf_frequency = 215,
    .hf_frequency = 320,
    .max_amplitude = 800,
    .lf_strength = 0.90f,
    .hf_strength = 0.42f,
    .lf_curve = 1.0f,
    .hf_curve = 1.15f,
    .hf_to_lf = 0.04f,
    .directions = {
        {
            .mode = STICK_DIRECTION_4WAY,
            .trigger_threshold = 0.60f,
            .release_threshold = 0.50f,
            .direction_deadzone = 5.0f,
            .active_direction = -1,
        },
        {
            .mode = STICK_DIRECTION_4WAY,
            .trigger_threshold = 0.60f,
            .release_threshold = 0.50f,
            .direction_deadzone = 5.0f,
            .active_direction = -1,
        },
    },
    .gyro = {
        .stick_sensitivity = 1.0f,
        .tilt_max_angle = 35.0f,
        .x_ratio = 1.0f,
        .y_ratio = 1.0f,
    },
};
static standalone_runtime_config_t s_profile_config;
static bool s_profile_config_initialized;

static void ensure_profile_config_initialized(void) {
    if (s_profile_config_initialized) return;
    s_profile_config = s_runtime;
    s_profile_config_initialized = true;
}

static float clampf(float value, float low, float high) {
    if (value < low) return low;
    if (value > high) return high;
    return value;
}

static double json_number(
    const cJSON *object, const char *key, double fallback
) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (cJSON_IsNumber(item)) return item->valuedouble;
    if (cJSON_IsString(item) && item->valuestring) {
        char *end = NULL;
        double value = strtod(item->valuestring, &end);
        if (end && end != item->valuestring && *end == '\0') return value;
    }
    return fallback;
}

static bool json_bool(
    const cJSON *object, const char *key, bool fallback
) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (cJSON_IsBool(item)) return cJSON_IsTrue(item);
    /*
     * Schema-1 profiles created before typed serialization stored INI
     * booleans as JSON strings. Keep those profiles readable after upgrade.
     */
    if (cJSON_IsString(item) && item->valuestring) {
        if (
            strcmp(item->valuestring, "true") == 0 ||
            strcmp(item->valuestring, "TRUE") == 0 ||
            strcmp(item->valuestring, "1") == 0
        ) return true;
        if (
            strcmp(item->valuestring, "false") == 0 ||
            strcmp(item->valuestring, "FALSE") == 0 ||
            strcmp(item->valuestring, "0") == 0
        ) return false;
    }
    return fallback;
}

static uint32_t target_from_name(const char *name) {
    if (!name || strcmp(name, "NONE") == 0) return 0;
    if (strcmp(name, "UP") == 0) return 0x0001u;
    if (strcmp(name, "DOWN") == 0) return 0x0002u;
    if (strcmp(name, "LEFT") == 0) return 0x0004u;
    if (strcmp(name, "RIGHT") == 0) return 0x0008u;
    if (strcmp(name, "START") == 0) return 0x0010u;
    if (strcmp(name, "BACK") == 0) return 0x0020u;
    if (strcmp(name, "L_STK") == 0) return 0x0040u;
    if (strcmp(name, "R_STK") == 0) return 0x0080u;
    if (strcmp(name, "LB") == 0) return 0x0100u;
    if (strcmp(name, "RB") == 0) return 0x0200u;
    if (strcmp(name, "GUIDE") == 0) return 0x0400u;
    if (strcmp(name, "A") == 0) return 0x1000u;
    if (strcmp(name, "B") == 0) return 0x2000u;
    if (strcmp(name, "X") == 0) return 0x4000u;
    if (strcmp(name, "Y") == 0) return 0x8000u;
    if (strcmp(name, "LT") == 0) return TARGET_LT;
    if (strcmp(name, "RT") == 0) return TARGET_RT;
    return 0;
}

static uint32_t source_mask_from_name(const char *name) {
    if (!name) return 0;
    for (int i = 0; i < SOURCE_BUTTON_COUNT; i++) {
        char upper[16];
        size_t n = strlen(s_source_names[i]);
        if (n >= sizeof(upper)) n = sizeof(upper) - 1;
        for (size_t j = 0; j < n; j++) {
            char c = s_source_names[i][j];
            upper[j] = c >= 'a' && c <= 'z' ? (char)(c - 32) : c;
        }
        upper[n] = '\0';
        if (strcmp(name, upper) == 0) return s_source_masks[i];
    }
    return 0;
}

static void parse_button_targets(
    const cJSON *buttons, uint32_t *targets
) {
    if (!cJSON_IsObject(buttons) || !targets) return;
    for (int i = 0; i < SOURCE_BUTTON_COUNT; i++) {
        const cJSON *target = cJSON_GetObjectItemCaseSensitive(
            buttons, s_source_names[i]
        );
        if (cJSON_IsString(target))
            targets[i] = target_from_name(target->valuestring);
    }
}

static void parse_stick_config(
    const cJSON *section, stick_runtime_config_t *config
) {
    if (!cJSON_IsObject(section) || !config) return;
    for (int i = 0; i < 5; i++) {
        char key[16];
        snprintf(key, sizeof(key), "point_%d_x", i);
        config->x[i] = clampf(
            (float)json_number(section, key, config->x[i]), 0.0f, 1.0f
        );
        snprintf(key, sizeof(key), "point_%d_y", i);
        config->y[i] = clampf(
            (float)json_number(section, key, config->y[i]), 0.0f, 1.0f
        );
    }
    config->deadzone = clampf(
        (float)json_number(section, "deadzone", config->deadzone),
        0.0f, 0.95f
    );
    config->outer_deadzone = clampf(
        (float)json_number(
            section, "outer_deadzone", config->outer_deadzone
        ), 0.0f, 0.95f
    );
    if (config->deadzone + config->outer_deadzone > 0.99f)
        config->outer_deadzone = 0.99f - config->deadzone;
    config->smoothing = clampf(
        (float)json_number(section, "smoothing", config->smoothing),
        0.0f, 3.0f
    );
    config->output_shape = (uint8_t)clampf(
        (float)json_number(section, "output_shape", config->output_shape),
        0.0f, 10.0f
    );
    config->deadzone_compress = json_bool(
        section, "deadzone_compress", config->deadzone_compress
    );
    config->outer_deadzone_compress = json_bool(
        section, "outer_deadzone_compress",
        config->outer_deadzone_compress
    );
    const cJSON *interpolation =
        cJSON_GetObjectItemCaseSensitive(section, "interpolation");
    config->smooth_interpolation =
        cJSON_IsString(interpolation) &&
        strcmp(interpolation->valuestring, "SMOOTH") == 0;
    config->smoothing_valid = false;
    config->smoothing_report_time = 0;
}

static bool parse_json_pair(
    const cJSON *object, const char *key, int *first, int *second
) {
    const cJSON *pair = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsArray(pair) || cJSON_GetArraySize(pair) != 2) return false;
    const cJSON *a = cJSON_GetArrayItem(pair, 0);
    const cJSON *b = cJSON_GetArrayItem(pair, 1);
    if (!cJSON_IsNumber(a) || !cJSON_IsNumber(b)) return false;
    *first = a->valueint;
    *second = b->valueint;
    return true;
}

static void parse_calibration_side(
    const cJSON *side, stick_runtime_config_t *config
) {
    if (!cJSON_IsObject(side) || !config) return;
    int a, b;
    if (parse_json_pair(side, "center", &a, &b)) {
        config->center_x = (int16_t)clampf((float)a, 0.0f, 4095.0f);
        config->center_y = (int16_t)clampf((float)b, 0.0f, 4095.0f);
    }
    if (parse_json_pair(side, "max", &a, &b)) {
        config->max_x = (uint16_t)clampf((float)a, 1.0f, 4095.0f);
        config->max_y = (uint16_t)clampf((float)b, 1.0f, 4095.0f);
    }
    if (parse_json_pair(side, "min", &a, &b)) {
        config->min_x = (uint16_t)clampf((float)a, 1.0f, 4095.0f);
        config->min_y = (uint16_t)clampf((float)b, 1.0f, 4095.0f);
    }
}

static void parse_direction_config(
    const cJSON *section, const cJSON *mappings,
    stick_direction_runtime_t *config
) {
    if (!config) return;
    if (cJSON_IsObject(section)) {
        const cJSON *mode =
            cJSON_GetObjectItemCaseSensitive(section, "mode");
        if (cJSON_IsString(mode)) {
            if (strcmp(mode->valuestring, "4WAY") == 0)
                config->mode = STICK_DIRECTION_4WAY;
            else if (strcmp(mode->valuestring, "8WAY") == 0)
                config->mode = STICK_DIRECTION_8WAY;
            else if (strcmp(mode->valuestring, "XINPUT_LT_LINEAR") == 0)
                config->mode = STICK_DIRECTION_LT;
            else if (strcmp(mode->valuestring, "XINPUT_RT_LINEAR") == 0)
                config->mode = STICK_DIRECTION_RT;
            else
                config->mode = STICK_DIRECTION_UNSUPPORTED;
        }
        config->trigger_threshold = clampf(
            (float)json_number(
                section, "trigger_threshold", config->trigger_threshold
            ), 0.10f, 1.0f
        );
        config->release_threshold = clampf(
            (float)json_number(
                section, "release_threshold", config->release_threshold
            ), 0.0f, 0.97f
        );
        config->direction_deadzone = clampf(
            (float)json_number(
                section, "direction_deadzone", config->direction_deadzone
            ), 0.0f, 20.0f
        );
        const cJSON *direction =
            cJSON_GetObjectItemCaseSensitive(section, "analog_direction");
        if (cJSON_IsString(direction)) {
            if (strcmp(direction->valuestring, "DOWN") == 0)
                config->analog_direction = 1;
            else if (strcmp(direction->valuestring, "LEFT") == 0)
                config->analog_direction = 2;
            else if (strcmp(direction->valuestring, "RIGHT") == 0)
                config->analog_direction = 3;
            else
                config->analog_direction = 0;
        }
    }
    static const char *names[8] = {
        "up","up_right","right","down_right",
        "down","down_left","left","up_left"
    };
    if (cJSON_IsObject(mappings)) {
        for (int i = 0; i < 8; i++) {
            const cJSON *target =
                cJSON_GetObjectItemCaseSensitive(mappings, names[i]);
            config->targets[i] = cJSON_IsString(target)
                ? target_from_name(target->valuestring) : 0;
        }
    }
    config->active_direction = -1;
}

static void reset_gyro_runtime_state(gyro_runtime_t *config) {
    if (!config) return;
    FusionAhrsInitialise(&config->ahrs);
    const FusionAhrsSettings settings = {
        .convention = FusionConventionNwu,
        .gain = 0.1f,
        .gyroscopeRange = 2000.0f,
        .accelerationRejection = 10.0f,
        .magneticRejection = 20.0f,
        .recoveryTriggerPeriod = 625,
    };
    FusionAhrsSetSettings(&config->ahrs, &settings);
    config->fusion_valid = false;
    config->nine_axis_has_magnetometer = false;
    config->nine_axis_orientation_valid = false;
    config->nine_axis_quaternion = FUSION_IDENTITY_QUATERNION;
    config->mag_field_reference_valid = false;
    config->mag_field_reference = 0.0f;
    config->mag_last_valid = false;
    config->mag_last_valid_time = 0;
    config->mag_recovery_started_valid = false;
    config->mag_recovery_started_time = 0;
    config->mag_recovery_accumulator = 0.0f;
    config->aim_gravity_sign_valid = false;
    config->aim_gravity_sign = 1.0f;
    config->aim_pose_ready_valid = false;
    config->aim_pose_ready_time = 0;
    config->aim_player_space_blend = 0.0f;
    config->tilt_orientation_valid = false;
    config->tilt_orientation_roll = 0.0f;
    config->tilt_orientation_pitch = 0.0f;
    config->tilt_neutral_quaternion =
        FUSION_IDENTITY_QUATERNION;
    config->tilt_neutral_quaternion_valid = false;
    config->impact_gravity_scale_valid = false;
    config->impact_gravity_scale = 0.0f;
    config->impact_accel_lp_valid = false;
    config->impact_last_gyro_valid = false;
    config->bias_block_until = 0;
    config->impact_accel_reject_until = 0;
    config->impact_accel_recover_until = 0;
    config->gyro_bias_samples = config->gyro_bias_saved ? 64 : 0;
    config->gyro_bias_anchor_valid = config->gyro_bias_saved;
    if (config->gyro_bias_saved) {
        memcpy(
            config->gyro_bias_anchor, config->gyro_bias,
            sizeof(config->gyro_bias_anchor)
        );
    }
    config->gyro_last_raw_valid = false;
    config->gyro_stationary_started_valid = false;
    config->gyro_stationary_samples = 0;
    config->gyro_stationary_accel_reference_valid = false;
    config->gyro_stationary_mag_reference_valid = false;
    config->rumble_ratio = 0.0f;
    config->trigger_previous = false;
    config->toggle_enabled = false;
    config->was_active = false;
    config->smoothed_x = config->smoothed_y = 0.0f;
    config->tilt_neutral_valid = false;
    config->previous_stabilization = 0;
    config->previous_recenter = false;
    config->freeze_until_report_time = 0;
    config->motion_envelope = 0.0f;
    config->last_report_time = 0;
}

static void parse_gyro_config(
    const cJSON *root, gyro_runtime_t *config
) {
    const cJSON *gyro =
        cJSON_GetObjectItemCaseSensitive(root, "gyro_mapping");
    if (!cJSON_IsObject(gyro) || !config) return;
    const cJSON *item =
        cJSON_GetObjectItemCaseSensitive(gyro, "activation_mode");
    if (cJSON_IsString(item)) {
        config->activation_mode =
            strcmp(item->valuestring, "HOLD") == 0 ? 1 :
            strcmp(item->valuestring, "TOGGLE") == 0 ? 2 : 0;
    }
    item = cJSON_GetObjectItemCaseSensitive(gyro, "activation_match");
    config->activation_match_all =
        cJSON_IsString(item) && strcmp(item->valuestring, "ALL") == 0;
    item = cJSON_GetObjectItemCaseSensitive(gyro, "target");
    config->target_left =
        cJSON_IsString(item) && strcmp(item->valuestring, "LEFT_STICK") == 0;
    item = cJSON_GetObjectItemCaseSensitive(gyro, "motion_mode");
    config->tilt_mode =
        cJSON_IsString(item) && strcmp(item->valuestring, "TILT") == 0;
    item = cJSON_GetObjectItemCaseSensitive(gyro, "tilt_axis");
    config->tilt_dual =
        cJSON_IsString(item) && strcmp(item->valuestring, "DUAL") == 0;
    config->tilt_max_angle = clampf(
        (float)json_number(gyro, "tilt_max_angle", 35.0), 10.0f, 60.0f
    );
    config->tilt_deadzone = clampf(
        (float)json_number(gyro, "tilt_deadzone", 0.0), 0.0f, 5.0f
    );
    config->tilt_smoothing_ms = clampf(
        (float)json_number(gyro, "tilt_smoothing_ms", 0.0), 0.0f, 150.0f
    );
    config->stick_sensitivity = clampf(
        (float)json_number(gyro, "stick_sensitivity", 1.0), 0.1f, 10.0f
    );
    config->deadzone = clampf(
        (float)json_number(gyro, "deadzone", 0.0), 0.0f, 5.0f
    );
    config->anti_deadzone = clampf(
        (float)json_number(gyro, "stick_anti_deadzone", 0.0) / 100.0f,
        0.0f, 0.30f
    );
    item = cJSON_GetObjectItemCaseSensitive(gyro, "response_curve");
    config->response_curve =
        cJSON_IsString(item) && strcmp(item->valuestring, "LATE") == 0 ? 1 :
        cJSON_IsString(item) && strcmp(item->valuestring, "EARLY") == 0 ? 2 : 0;
    config->curve_strength = clampf(
        (float)json_number(gyro, "curve_strength", 0.0) / 10.0f,
        0.0f, 1.0f
    );
    config->smoothing_ms = clampf(
        (float)json_number(gyro, "smoothing_ms", 0.0), 0.0f, 100.0f
    );
    config->invert_x = json_bool(gyro, "invert_x", false);
    config->invert_y = json_bool(gyro, "invert_y", false);
    config->x_ratio = clampf(
        (float)json_number(gyro, "x_ratio", 1.0), 0.5f, 2.0f
    );
    config->y_ratio = clampf(
        (float)json_number(gyro, "y_ratio", 1.0), 0.5f, 2.0f
    );
    config->accel_suppression = clampf(
        (float)json_number(gyro, "accel_suppression", 0.0) / 100.0f,
        0.0f, 1.0f
    );
    config->adaptive_deadzone = clampf(
        (float)json_number(gyro, "adaptive_deadzone", 0.0) / 100.0f,
        0.0f, 1.0f
    );
    config->button_freeze_ms = clampf(
        (float)json_number(gyro, "button_freeze_ms", 0.0),
        0.0f, 120.0f
    );
    config->player_space = json_bool(gyro, "player_space", false);
    config->activation_mask = 0;
    const cJSON *buttons = cJSON_GetObjectItemCaseSensitive(
        root, "gyro_activation_buttons"
    );
    if (cJSON_IsArray(buttons)) {
        int count = cJSON_GetArraySize(buttons);
        for (int i = 0; i < count; i++) {
            const cJSON *button = cJSON_GetArrayItem(buttons, i);
            if (cJSON_IsString(button))
                config->activation_mask |=
                    source_mask_from_name(button->valuestring);
        }
    }
    config->stabilization_mask = 0;
    const cJSON *stabilization = cJSON_GetObjectItemCaseSensitive(
        root, "gyro_stabilization_buttons"
    );
    if (cJSON_IsArray(stabilization)) {
        int count = cJSON_GetArraySize(stabilization);
        for (int i = 0; i < count; i++) {
            const cJSON *button = cJSON_GetArrayItem(stabilization, i);
            if (cJSON_IsString(button))
                config->stabilization_mask |=
                    source_mask_from_name(button->valuestring);
        }
    }
    const cJSON *recenter = cJSON_GetObjectItemCaseSensitive(
        root, "gyro_tilt_recenter_button"
    );
    config->recenter_mask = cJSON_IsString(recenter)
        ? source_mask_from_name(recenter->valuestring) : 0;
    const cJSON *sensor =
        cJSON_GetObjectItemCaseSensitive(root, "sensor_calibration");
    config->accel_calibrated = false;
    memset(config->accel_bias, 0, sizeof(config->accel_bias));
    memset(config->accel_matrix, 0, sizeof(config->accel_matrix));
    config->mag_calibrated = false;
    memset(config->mag_bias, 0, sizeof(config->mag_bias));
    memset(config->mag_matrix, 0, sizeof(config->mag_matrix));
    const cJSON *bias = cJSON_IsObject(sensor)
        ? cJSON_GetObjectItemCaseSensitive(sensor, "gyro_bias") : NULL;
    config->gyro_bias_saved = false;
    memset(config->gyro_bias, 0, sizeof(config->gyro_bias));
    if (cJSON_IsArray(bias) && cJSON_GetArraySize(bias) == 3) {
        bool valid = true;
        for (int i = 0; i < 3; i++) {
            const cJSON *value = cJSON_GetArrayItem(bias, i);
            if (cJSON_IsNumber(value)) {
                config->gyro_bias[i] = (float)value->valuedouble;
            } else {
                valid = false;
            }
        }
        config->gyro_bias_saved = valid;
    }
    const cJSON *accel_bias = cJSON_IsObject(sensor)
        ? cJSON_GetObjectItemCaseSensitive(
            sensor, "accelerometer_bias"
        ) : NULL;
    const cJSON *accel_matrix = cJSON_IsObject(sensor)
        ? cJSON_GetObjectItemCaseSensitive(
            sensor, "accelerometer_matrix"
        ) : NULL;
    if (
        cJSON_IsArray(accel_bias) &&
        cJSON_GetArraySize(accel_bias) == 3 &&
        cJSON_IsArray(accel_matrix)
    ) {
        for (int i = 0; i < 3; i++) {
            const cJSON *value = cJSON_GetArrayItem(accel_bias, i);
            if (cJSON_IsNumber(value))
                config->accel_bias[i] = (float)value->valuedouble;
        }
        int position = 0;
        cJSON *row;
        cJSON_ArrayForEach(row, accel_matrix) {
            if (!cJSON_IsArray(row)) continue;
            cJSON *value;
            cJSON_ArrayForEach(value, row) {
                if (position < 9 && cJSON_IsNumber(value))
                    config->accel_matrix[position++] =
                        (float)value->valuedouble;
            }
        }
        config->accel_calibrated = position == 9;
    }
    const cJSON *mag_bias = cJSON_IsObject(sensor)
        ? cJSON_GetObjectItemCaseSensitive(sensor, "magnetometer_bias")
        : NULL;
    const cJSON *mag_matrix = cJSON_IsObject(sensor)
        ? cJSON_GetObjectItemCaseSensitive(sensor, "magnetometer_matrix")
        : NULL;
    if (cJSON_IsArray(mag_bias) && cJSON_GetArraySize(mag_bias) == 3) {
        for (int i = 0; i < 3; i++) {
            const cJSON *value = cJSON_GetArrayItem(mag_bias, i);
            if (cJSON_IsNumber(value))
                config->mag_bias[i] = (float)value->valuedouble;
        }
        int position = 0;
        if (cJSON_IsArray(mag_matrix)) {
            cJSON *row;
            cJSON_ArrayForEach(row, mag_matrix) {
                if (!cJSON_IsArray(row)) continue;
                cJSON *value;
                cJSON_ArrayForEach(value, row) {
                    if (position < 9 && cJSON_IsNumber(value))
                        config->mag_matrix[position++] =
                            (float)value->valuedouble;
                }
            }
        }
        if (position != 9) {
            memset(config->mag_matrix, 0, sizeof(config->mag_matrix));
            const cJSON *scale = cJSON_GetObjectItemCaseSensitive(
                sensor, "magnetometer_scale"
            );
            for (int i = 0; i < 3; i++) {
                const cJSON *value = cJSON_IsArray(scale)
                    ? cJSON_GetArrayItem(scale, i) : NULL;
                config->mag_matrix[i * 3 + i] =
                    cJSON_IsNumber(value) ? (float)value->valuedouble : 1.0f;
            }
        }
        config->mag_calibrated = true;
    }
    reset_gyro_runtime_state(config);
}

static bool parse_profile_json(
    const uint8_t *json,
    size_t length,
    standalone_runtime_config_t *parsed
) {
    if (!json || length == 0 || !parsed) return false;
    cJSON *root = cJSON_ParseWithLength((const char *)json, length);
    if (!root) return false;
    const cJSON *schema =
        cJSON_GetObjectItemCaseSensitive(root, "schema");
    if (
        !cJSON_IsObject(root) || !cJSON_IsNumber(schema) ||
        schema->valueint != 1
    ) {
        cJSON_Delete(root);
        return false;
    }
    ensure_profile_config_initialized();
    /*
     * Parse from the last committed immutable configuration, never from the
     * live algorithm state. Stick smoothing, mapping toggles, gyro fusion and
     * diagnostics mutate s_runtime while input is active.
     *
     * parsed is heap-backed by both callers. Build directly in that storage:
     * a standalone_runtime_config_t is too large for app_main's startup stack
     * when an existing NVS profile is restored before TinyUSB starts.
     */
    *parsed = s_profile_config;
#define next (*parsed)
    next.idle_disconnect_minutes = (uint8_t)clampf(
        (float)json_number(
            root, "idle_disconnect_minutes",
            next.idle_disconnect_minutes
        ),
        0.0f, 60.0f
    );
    if (
        next.idle_disconnect_minutes != 0 &&
        next.idle_disconnect_minutes != 5 &&
        next.idle_disconnect_minutes != 10 &&
        next.idle_disconnect_minutes != 15 &&
        next.idle_disconnect_minutes != 30 &&
        next.idle_disconnect_minutes != 60
    ) {
        next.idle_disconnect_minutes = 15;
    }

    const cJSON *buttons =
        cJSON_GetObjectItemCaseSensitive(root, "buttons");
    parse_button_targets(buttons, next.button_targets);
    parse_stick_config(
        cJSON_GetObjectItemCaseSensitive(root, "stick_curve_left"),
        &next.sticks[0]
    );
    parse_stick_config(
        cJSON_GetObjectItemCaseSensitive(root, "stick_curve_right"),
        &next.sticks[1]
    );
    const cJSON *calibration =
        cJSON_GetObjectItemCaseSensitive(root, "calibration");
    if (cJSON_IsObject(calibration)) {
        parse_calibration_side(
            cJSON_GetObjectItemCaseSensitive(calibration, "left"),
            &next.sticks[0]
        );
        parse_calibration_side(
            cJSON_GetObjectItemCaseSensitive(calibration, "right"),
            &next.sticks[1]
        );
    }
    const cJSON *direction_mappings =
        cJSON_GetObjectItemCaseSensitive(root, "direction_mappings");
    parse_direction_config(
        cJSON_GetObjectItemCaseSensitive(root, "stick_direction_left"),
        cJSON_IsObject(direction_mappings)
            ? cJSON_GetObjectItemCaseSensitive(direction_mappings, "left")
            : NULL,
        &next.directions[0]
    );
    parse_direction_config(
        cJSON_GetObjectItemCaseSensitive(root, "stick_direction_right"),
        cJSON_IsObject(direction_mappings)
            ? cJSON_GetObjectItemCaseSensitive(direction_mappings, "right")
            : NULL,
        &next.directions[1]
    );
    const cJSON *rumble =
        cJSON_GetObjectItemCaseSensitive(root, "rumble");
    if (cJSON_IsObject(rumble)) {
        next.lf_frequency = (uint16_t)clampf(
            (float)json_number(rumble, "lf_frequency", next.lf_frequency),
            0.0f, 511.0f
        );
        next.hf_frequency = (uint16_t)clampf(
            (float)json_number(rumble, "hf_frequency", next.hf_frequency),
            0.0f, 511.0f
        );
        next.max_amplitude = (uint16_t)clampf(
            (float)json_number(
                rumble, "max_amplitude", next.max_amplitude
            ), 0.0f, 1023.0f
        );
        next.lf_strength = clampf(
            (float)json_number(rumble, "lf_strength", next.lf_strength),
            0.0f, 1.0f
        );
        next.hf_strength = clampf(
            (float)json_number(rumble, "hf_strength", next.hf_strength),
            0.0f, 1.0f
        );
        next.lf_curve = clampf(
            (float)json_number(rumble, "lf_curve", next.lf_curve),
            0.1f, 5.0f
        );
        next.hf_curve = clampf(
            (float)json_number(rumble, "hf_curve", next.hf_curve),
            0.1f, 5.0f
        );
        next.lf_to_hf = clampf(
            (float)json_number(
                rumble, "lf_to_hf_compensation", next.lf_to_hf
            ), 0.0f, 1.0f
        );
        next.hf_to_lf = clampf(
            (float)json_number(
                rumble, "hf_to_lf_compensation", next.hf_to_lf
            ), 0.0f, 1.0f
        );
    }
    next.layer_count = 0;
    next.toggle_layer = -1;
    next.previous_source_buttons = 0;
    const cJSON *layers =
        cJSON_GetObjectItemCaseSensitive(root, "mapping_layers");
    if (cJSON_IsArray(layers)) {
        int count = cJSON_GetArraySize(layers);
        if (count > STANDALONE_LAYER_MAX) count = STANDALONE_LAYER_MAX;
        for (int index = 0; index < count; index++) {
            const cJSON *layer = cJSON_GetArrayItem(layers, index);
            if (!cJSON_IsObject(layer)) continue;
            mapping_layer_runtime_t *runtime =
                &next.layers[next.layer_count];
            memset(runtime, 0, sizeof(*runtime));
            const cJSON *activation = cJSON_GetObjectItemCaseSensitive(
                layer, "activation_buttons"
            );
            if (cJSON_IsArray(activation)) {
                int activation_count = cJSON_GetArraySize(activation);
                for (int i = 0; i < activation_count; i++) {
                    const cJSON *button = cJSON_GetArrayItem(activation, i);
                    if (cJSON_IsString(button)) {
                        uint32_t mask =
                            source_mask_from_name(button->valuestring);
                        if (mask && !(runtime->activation_mask & mask)) {
                            runtime->activation_mask |= mask;
                            runtime->specificity++;
                        }
                    }
                }
            }
            const cJSON *mode =
                cJSON_GetObjectItemCaseSensitive(layer, "mode");
            runtime->toggle_mode =
                cJSON_IsString(mode) &&
                strcmp(mode->valuestring, "TOGGLE") == 0;
            memcpy(
                runtime->button_targets, next.button_targets,
                sizeof(runtime->button_targets)
            );
            parse_button_targets(
                cJSON_GetObjectItemCaseSensitive(layer, "buttons"),
                runtime->button_targets
            );
            runtime->directions[0] = next.directions[0];
            runtime->directions[1] = next.directions[1];
            const cJSON *left =
                cJSON_GetObjectItemCaseSensitive(layer, "stick_left");
            const cJSON *right =
                cJSON_GetObjectItemCaseSensitive(layer, "stick_right");
            parse_direction_config(left, left, &runtime->directions[0]);
            parse_direction_config(right, right, &runtime->directions[1]);
            if (runtime->activation_mask)
                next.layer_count++;
        }
    }
    parse_gyro_config(root, &next.gyro);
#undef next
    cJSON_Delete(root);
    return true;
}

bool standalone_xinput_validate_profile_json(
    const uint8_t *json, size_t length
) {
    standalone_runtime_config_t *parsed = malloc(sizeof(*parsed));
    if (!parsed) return false;
    bool valid = parse_profile_json(json, length, parsed);
    free(parsed);
    return valid;
}

bool standalone_xinput_apply_profile_json(
    const uint8_t *json, size_t length
) {
    standalone_runtime_config_t *next = malloc(sizeof(*next));
    if (!next) return false;
    if (!parse_profile_json(json, length, next)) {
        free(next);
        return false;
    }
    /*
     * Profile commits and input processing are serialized by cdc_task. Keep a
     * pristine committed copy for future partial documents and a separate
     * mutable copy for the input algorithm.
     */
    s_profile_config = *next;
    s_profile_config_initialized = true;
    s_runtime = *next;
    portENTER_CRITICAL(&s_state_mux);
    s_idle_baseline_valid = false;
    s_last_activity_ms = idle_now_ms();
    portEXIT_CRITICAL(&s_state_mux);
    free(next);
    return true;
}

void standalone_xinput_get_rumble_config(
    uint16_t *lf_frequency, uint16_t *hf_frequency,
    uint16_t *max_amplitude, float *lf_strength, float *hf_strength,
    float *lf_curve, float *hf_curve, float *lf_to_hf, float *hf_to_lf
) {
    if (lf_frequency) *lf_frequency = s_runtime.lf_frequency;
    if (hf_frequency) *hf_frequency = s_runtime.hf_frequency;
    if (max_amplitude) *max_amplitude = s_runtime.max_amplitude;
    if (lf_strength) *lf_strength = s_runtime.lf_strength;
    if (hf_strength) *hf_strength = s_runtime.hf_strength;
    if (lf_curve) *lf_curve = s_runtime.lf_curve;
    if (hf_curve) *hf_curve = s_runtime.hf_curve;
    if (lf_to_hf) *lf_to_hf = s_runtime.lf_to_hf;
    if (hf_to_lf) *hf_to_lf = s_runtime.hf_to_lf;
}

void standalone_xinput_set_rumble_ratio(float ratio) {
    s_runtime.gyro.rumble_ratio = clampf(ratio, 0.0f, 1.0f);
}

void standalone_xinput_format_runtime_status(char *output, size_t size) {
    if (!output || size == 0) return;
    snprintf(
        output, size,
        "{\"cmd\":\"runtime_status\",\"ok\":1,"
        "\"left_shape\":%u,\"right_shape\":%u,"
        "\"left_deadzone\":%.3f,\"right_deadzone\":%.3f,"
        "\"left_center\":[%d,%d],\"right_center\":[%d,%d],"
        "\"layers\":%u,\"gyro_mode\":%u,\"gyro_target\":\"%s\","
        "\"gyro_motion\":\"%s\",\"gyro_activation_mask\":\"%08lx\","
        "\"gyro_active\":%d,\"gyro_rate\":[%.2f,%.2f,%.2f],"
        "\"gyro_output\":[%.4f,%.4f],"
        "\"lf_frequency\":%u,\"hf_frequency\":%u,"
        "\"max_amplitude\":%u}\n",
        s_runtime.sticks[0].output_shape,
        s_runtime.sticks[1].output_shape,
        s_runtime.sticks[0].deadzone,
        s_runtime.sticks[1].deadzone,
        s_runtime.sticks[0].center_x,
        s_runtime.sticks[0].center_y,
        s_runtime.sticks[1].center_x,
        s_runtime.sticks[1].center_y,
        s_runtime.layer_count,
        s_runtime.gyro.activation_mode,
        s_runtime.gyro.target_left ? "LEFT_STICK" : "RIGHT_STICK",
        s_runtime.gyro.tilt_mode ? "TILT" : "CENTER",
        (unsigned long)s_runtime.gyro.activation_mask,
        s_runtime.gyro.diagnostic_active ? 1 : 0,
        s_runtime.gyro.diagnostic_rate[0],
        s_runtime.gyro.diagnostic_rate[1],
        s_runtime.gyro.diagnostic_rate[2],
        s_runtime.gyro.diagnostic_output[0],
        s_runtime.gyro.diagnostic_output[1],
        s_runtime.lf_frequency,
        s_runtime.hf_frequency,
        s_runtime.max_amplitude
    );
}

static uint32_t read_u32_le(const uint8_t *data) {
    return (uint32_t)data[0] |
           ((uint32_t)data[1] << 8) |
           ((uint32_t)data[2] << 16) |
           ((uint32_t)data[3] << 24);
}

static uint16_t stick_x(const uint8_t *data) {
    return (uint16_t)data[0] | ((uint16_t)(data[1] & 0x0f) << 8);
}

static uint16_t stick_y(const uint8_t *data) {
    return ((uint16_t)data[1] >> 4) | ((uint16_t)data[2] << 4);
}

static float evaluate_stick_curve(
    float input, const stick_runtime_config_t *config
) {
    input = clampf(input, 0.0f, 1.0f);

    if (config->smooth_interpolation) {
        float xs[7];
        float ys[7];
        int knot_count = 0;
        if (config->x[0] > 1e-9f) {
            xs[knot_count] = 0.0f;
            ys[knot_count++] = 0.0f;
        }
        for (int i = 0; i < 5; i++) {
            xs[knot_count] = config->x[i];
            ys[knot_count++] = config->y[i];
        }
        if (config->x[4] < 1.0f - 1e-9f) {
            xs[knot_count] = 1.0f;
            ys[knot_count++] = 1.0f;
        }

        float widths[6];
        float secants[6];
        float tangents[7] = {0};
        bool valid = knot_count >= 2;
        for (int i = 0; i < knot_count - 1; i++) {
            widths[i] = xs[i + 1] - xs[i];
            if (widths[i] <= 1e-9f) {
                valid = false;
                break;
            }
            secants[i] = (ys[i + 1] - ys[i]) / widths[i];
        }
        if (valid) {
            tangents[0] = fmaxf(0.0f, secants[0]);
            tangents[knot_count - 1] =
                fmaxf(0.0f, secants[knot_count - 2]);
            for (int i = 1; i < knot_count - 1; i++) {
                float left = secants[i - 1];
                float right = secants[i];
                if (left <= 0.0f || right <= 0.0f) {
                    tangents[i] = 0.0f;
                } else {
                    float weight_left =
                        2.0f * widths[i] + widths[i - 1];
                    float weight_right =
                        widths[i] + 2.0f * widths[i - 1];
                    tangents[i] = (weight_left + weight_right) /
                        (weight_left / left + weight_right / right);
                }
            }
            for (int i = 0; i < knot_count - 1; i++) {
                if (xs[i] <= input && input <= xs[i + 1]) {
                    float position = (input - xs[i]) / widths[i];
                    float squared = position * position;
                    float cubed = squared * position;
                    float output =
                        (2.0f * cubed - 3.0f * squared + 1.0f) * ys[i] +
                        (cubed - 2.0f * squared + position) *
                            widths[i] * tangents[i] +
                        (-2.0f * cubed + 3.0f * squared) * ys[i + 1] +
                        (cubed - squared) *
                            widths[i] * tangents[i + 1];
                    return clampf(output, 0.0f, 1.0f);
                }
            }
        }
    }

    if (input <= config->x[0]) {
        float output = config->x[0] > 1e-9f
            ? config->y[0] * input / config->x[0]
            : config->y[0];
        return clampf(output, 0.0f, 1.0f);
    }
    for (int i = 0; i < 4; i++) {
        if (config->x[i] <= input && input <= config->x[i + 1]) {
            float width = config->x[i + 1] - config->x[i];
            float output = width < 1e-9f
                ? config->y[i + 1]
                : config->y[i] +
                    (config->y[i + 1] - config->y[i]) *
                    ((input - config->x[i]) / width);
            return clampf(output, 0.0f, 1.0f);
        }
    }
    float output;
    if (config->x[4] < 1.0f - 1e-9f) {
        float position = (input - config->x[4]) / (1.0f - config->x[4]);
        output = config->y[4] + (1.0f - config->y[4]) * position;
    } else {
        output = config->y[4];
    }
    return clampf(output, 0.0f, 1.0f);
}

static float evaluate_stick_curve_slope(
    float input, const stick_runtime_config_t *config
) {
    input = clampf(input, 0.0f, 1.0f);
    if (config->smooth_interpolation) {
        const float epsilon = 1e-4f;
        float low = fmaxf(0.0f, input - epsilon);
        float high = fminf(1.0f, input + epsilon);
        if (high <= low) return 0.0f;
        return fmaxf(
            0.0f,
            (evaluate_stick_curve(high, config) -
             evaluate_stick_curve(low, config)) / (high - low)
        );
    }
    if (input <= config->x[0]) {
        if (config->x[0] > 1e-9f)
            return fmaxf(0.0f, config->y[0] / config->x[0]);
        return fabsf(config->y[0]) > 1e-9f ? INFINITY : 0.0f;
    }
    for (int i = 0; i < 4; i++) {
        if (config->x[i] <= input && input <= config->x[i + 1]) {
            float dx = config->x[i + 1] - config->x[i];
            float dy = config->y[i + 1] - config->y[i];
            if (fabsf(dx) < 1e-9f)
                return fabsf(dy) > 1e-9f ? INFINITY : 0.0f;
            return fmaxf(0.0f, dy / dx);
        }
    }
    float dx = 1.0f - config->x[4];
    float dy = 1.0f - config->y[4];
    if (fabsf(dx) < 1e-9f)
        return fabsf(dy) > 1e-9f ? INFINITY : 0.0f;
    return fmaxf(0.0f, dy / dx);
}

static void process_stick(
    uint16_t raw_x, uint16_t raw_y, uint32_t report_time,
    stick_runtime_config_t *config, int16_t *output_x, int16_t *output_y
) {
    float delta_x = (float)raw_x - config->center_x;
    float delta_y = (float)raw_y - config->center_y;
    float x = clampf(
        delta_x / (delta_x >= 0.0f ? config->max_x : config->min_x),
        -1.0f, 1.0f
    );
    float y = clampf(
        delta_y / (delta_y >= 0.0f ? config->max_y : config->min_y),
        -1.0f, 1.0f
    );
    float magnitude = sqrtf(x * x + y * y);
    if (magnitude < config->deadzone || magnitude <= 0.00001f) {
        config->smoothed_magnitude = 0.0f;
        config->smoothing_valid = false;
        config->smoothing_report_time = 0;
        *output_x = 0;
        *output_y = 0;
        return;
    }

    float direction_x = x / magnitude;
    float direction_y = y / magnitude;
    float curve_start =
        config->deadzone_compress ? config->deadzone : 0.0f;
    float curve_end = config->outer_deadzone_compress
        ? 1.0f - config->outer_deadzone : 1.0f;
    if (curve_end <= curve_start) {
        curve_start = 0.0f;
        curve_end = 1.0f;
    }
    float normalized = clampf(
        (clampf(magnitude, 0.0f, 1.0f) - curve_start) /
        (curve_end - curve_start), 0.0f, 1.0f
    );
    float output_magnitude =
        clampf(evaluate_stick_curve(normalized, config), 0.0f, 1.0f);
    if (config->smoothing > 0.0f) {
        float slope = evaluate_stick_curve_slope(normalized, config) /
            (curve_end - curve_start);
        float base_alpha = slope <= 1.0f
            ? 1.0f
            : 1.0f / (
                1.0f + (fminf(slope, 10.0f) - 1.0f) * config->smoothing
            );
        float alpha = 1.0f;
        if (
            base_alpha < 1.0f &&
            config->smoothing_valid &&
            config->smoothing_report_time != 0
        ) {
            const float nominal_dt = 1.0f / 120.0f;
            float time_constant =
                -nominal_dt / logf(1.0f - base_alpha);
            float delta_time = clampf(
                (float)(report_time - config->smoothing_report_time) /
                    1000.0f,
                0.0f, 0.1f
            );
            alpha = 1.0f - expf(-delta_time / time_constant);
        }
        if (config->smoothing_valid) {
            output_magnitude = config->smoothed_magnitude +
                (output_magnitude - config->smoothed_magnitude) * alpha;
        }
    }
    config->smoothed_magnitude = output_magnitude;
    config->smoothing_valid = true;
    config->smoothing_report_time = report_time;

    /*
     * Match the desktop's final outer-deadzone guarantee. It is intentionally
     * applied after smoothing so full physical travel always reaches 100%.
     */
    if (
        config->outer_deadzone > 0.0f &&
        magnitude >= 1.0f - config->outer_deadzone
    ) {
        output_magnitude = 1.0f;
        config->smoothed_magnitude = 1.0f;
    }

    /*
     * Blend circular output toward a square gate. output_shape 0 is radial;
     * 10 reaches the square boundary while retaining the stick direction.
     */
    float shape_blend = config->output_shape / 10.0f;
    if (shape_blend > 0.0f) {
        float largest = fmaxf(fabsf(direction_x), fabsf(direction_y));
        if (largest > 0.00001f) {
            float square_scale = 1.0f / largest;
            float scale = 1.0f + (square_scale - 1.0f) * shape_blend;
            direction_x *= scale;
            direction_y *= scale;
        }
    }
    x = clampf(direction_x * output_magnitude, -1.0f, 1.0f);
    y = clampf(direction_y * output_magnitude, -1.0f, 1.0f);
    *output_x = (int16_t)lrintf(x * (x < 0.0f ? 32768.0f : 32767.0f));
    *output_y = (int16_t)lrintf(y * (y < 0.0f ? 32768.0f : 32767.0f));
}

static float linear_trigger_amount(
    uint16_t raw_x, uint16_t raw_y, uint8_t analog_direction,
    const stick_runtime_config_t *stick
) {
    bool horizontal = analog_direction == 2 || analog_direction == 3;
    uint16_t raw = horizontal ? raw_x : raw_y;
    int16_t center = horizontal ? stick->center_x : stick->center_y;
    uint16_t maximum = horizontal ? stick->max_x : stick->max_y;
    uint16_t minimum = horizontal ? stick->min_x : stick->min_y;
    float delta = (float)raw - center;
    float calibrated = clampf(
        delta / (delta >= 0.0f ? maximum : minimum), -1.0f, 1.0f
    );
    bool positive = analog_direction == 0 || analog_direction == 3;
    float amount = clampf(
        positive ? calibrated : -calibrated, 0.0f, 1.0f
    );
    if (amount < stick->deadzone) return 0.0f;
    float outer_threshold = 1.0f - stick->outer_deadzone;
    if (
        stick->outer_deadzone > 0.0f &&
        amount >= outer_threshold
    ) return 1.0f;
    float curve_start =
        stick->deadzone_compress ? stick->deadzone : 0.0f;
    float curve_end = stick->outer_deadzone_compress
        ? outer_threshold : 1.0f;
    if (curve_end <= curve_start) {
        curve_start = 0.0f;
        curve_end = 1.0f;
    }
    float curve_input = clampf(
        (amount - curve_start) / (curve_end - curve_start), 0.0f, 1.0f
    );
    return clampf(evaluate_stick_curve(curve_input, stick), 0.0f, 1.0f);
}

static float angular_distance(float first, float second) {
    float distance = fmodf(first - second + 540.0f, 360.0f) - 180.0f;
    return fabsf(distance);
}

static uint32_t apply_stick_direction(
    stick_direction_runtime_t *config,
    const stick_runtime_config_t *stick,
    uint16_t raw_x, uint16_t raw_y, float x, float y,
    uint8_t *left_trigger, uint8_t *right_trigger
) {
    if (!config) return 0;
    if (
        config->mode == STICK_DIRECTION_LT ||
        config->mode == STICK_DIRECTION_RT
    ) {
        float amount = linear_trigger_amount(
            raw_x, raw_y, config->analog_direction, stick
        );
        uint8_t value = (uint8_t)lrintf(amount * 255.0f);
        if (config->mode == STICK_DIRECTION_LT && value > *left_trigger)
            *left_trigger = value;
        if (config->mode == STICK_DIRECTION_RT && value > *right_trigger)
            *right_trigger = value;
        return 0;
    }
    if (
        config->mode != STICK_DIRECTION_4WAY &&
        config->mode != STICK_DIRECTION_8WAY
    ) return 0;
    float magnitude = sqrtf(x * x + y * y);
    if (config->active_direction >= 0) {
        if (magnitude <= config->release_threshold)
            config->active_direction = -1;
    } else if (magnitude < config->trigger_threshold) {
        return 0;
    }
    if (magnitude >= config->trigger_threshold) {
        float angle = atan2f(y, x) * (180.0f / (float)M_PI);
        if (angle < 0.0f) angle += 360.0f;
        float sector_step =
            config->mode == STICK_DIRECTION_4WAY ? 90.0f : 45.0f;
        int sector_count =
            config->mode == STICK_DIRECTION_4WAY ? 4 : 8;
        float half_sector = sector_step / 2.0f;
        int sector = ((int)floorf(
            (angle + half_sector) / sector_step
        )) % sector_count;
        static const int indices_4way[4] = {2, 0, 6, 4};
        static const int indices_8way[8] = {2, 1, 0, 7, 6, 5, 4, 3};
        const int *indices = config->mode == STICK_DIRECTION_4WAY
            ? indices_4way : indices_8way;
        int candidate = indices[sector];
        float candidate_distance =
            angular_distance(angle, sector * sector_step);

        if (config->active_direction >= 0) {
            int current_sector = -1;
            for (int i = 0; i < sector_count; i++) {
                if (indices[i] == config->active_direction) {
                    current_sector = i;
                    break;
                }
            }
            if (
                current_sector >= 0 &&
                angular_distance(angle, current_sector * sector_step) <=
                    half_sector + config->direction_deadzone
            ) {
                return config->targets[config->active_direction];
            }
        }
        if (
            candidate_distance >
                half_sector - config->direction_deadzone
        ) {
            return config->active_direction >= 0
                ? config->targets[config->active_direction] : 0;
        }
        config->active_direction = candidate;
    }
    return config->active_direction >= 0
        ? config->targets[config->active_direction] : 0;
}

static bool stick_direction_consumes_native_output(
    const stick_direction_runtime_t *config
) {
    if (!config) return false;
    if (
        config->mode == STICK_DIRECTION_LT ||
        config->mode == STICK_DIRECTION_RT
    ) return true;
    if (
        config->mode != STICK_DIRECTION_4WAY &&
        config->mode != STICK_DIRECTION_8WAY
    ) return false;
    int count = config->mode == STICK_DIRECTION_4WAY ? 4 : 8;
    static const int indices_4way[4] = {0, 2, 4, 6};
    for (int i = 0; i < count; i++) {
        int index = config->mode == STICK_DIRECTION_4WAY
            ? indices_4way[i] : i;
        if (config->targets[index] != 0) return true;
    }
    return false;
}

bool standalone_xinput_format_algorithm_test(
    const char *arguments, char *output, size_t size
) {
    if (!arguments || !output || size == 0) return false;

    char side_name = '\0';
    unsigned raw_x_1, raw_y_1, raw_x_2, raw_y_2;
    unsigned long time_1, time_2;
    if (sscanf(
        arguments,
        "stick %c %u %u %lu %u %u %lu",
        &side_name,
        &raw_x_1, &raw_y_1, &time_1,
        &raw_x_2, &raw_y_2, &time_2
    ) == 7) {
        int side =
            side_name == 'L' || side_name == 'l' ? 0 :
            side_name == 'R' || side_name == 'r' ? 1 : -1;
        if (
            side < 0 ||
            raw_x_1 > 4095 || raw_y_1 > 4095 ||
            raw_x_2 > 4095 || raw_y_2 > 4095
        ) return false;
        stick_runtime_config_t stick = s_runtime.sticks[side];
        stick.smoothing_valid = false;
        stick.smoothing_report_time = 0;
        int16_t first_x, first_y, final_x, final_y;
        process_stick(
            (uint16_t)raw_x_1, (uint16_t)raw_y_1, (uint32_t)time_1,
            &stick, &first_x, &first_y
        );
        process_stick(
            (uint16_t)raw_x_2, (uint16_t)raw_y_2, (uint32_t)time_2,
            &stick, &final_x, &final_y
        );
        snprintf(
            output, size,
            "{\"cmd\":\"algorithm_test\",\"ok\":1,"
            "\"kind\":\"stick\",\"first\":[%d,%d],"
            "\"final\":[%d,%d]}\n",
            first_x, first_y, final_x, final_y
        );
        return true;
    }

    char direction_name[8] = {0};
    if (sscanf(
        arguments,
        "linear %c %7s %u %u",
        &side_name, direction_name, &raw_x_1, &raw_y_1
    ) == 4) {
        int side =
            side_name == 'L' || side_name == 'l' ? 0 :
            side_name == 'R' || side_name == 'r' ? 1 : -1;
        if (
            side < 0 || raw_x_1 > 4095 || raw_y_1 > 4095
        ) return false;
        uint8_t direction =
            strcmp(direction_name, "DOWN") == 0 ? 1 :
            strcmp(direction_name, "LEFT") == 0 ? 2 :
            strcmp(direction_name, "RIGHT") == 0 ? 3 :
            strcmp(direction_name, "UP") == 0 ? 0 : 0xff;
        if (direction == 0xff) return false;
        stick_runtime_config_t stick = s_runtime.sticks[side];
        float amount = linear_trigger_amount(
            (uint16_t)raw_x_1, (uint16_t)raw_y_1, direction, &stick
        );
        snprintf(
            output, size,
            "{\"cmd\":\"algorithm_test\",\"ok\":1,"
            "\"kind\":\"linear\",\"amount\":%.7f,\"trigger\":%u}\n",
            amount, (unsigned)lrintf(amount * 255.0f)
        );
        return true;
    }

    float x_1, y_1, x_2, y_2;
    if (sscanf(
        arguments,
        "direction %c %f %f %f %f",
        &side_name, &x_1, &y_1, &x_2, &y_2
    ) == 5) {
        int side =
            side_name == 'L' || side_name == 'l' ? 0 :
            side_name == 'R' || side_name == 'r' ? 1 : -1;
        if (side < 0) return false;
        stick_runtime_config_t stick = s_runtime.sticks[side];
        stick_direction_runtime_t direction = s_runtime.directions[side];
        direction.active_direction = -1;
        uint8_t left_trigger = 0;
        uint8_t right_trigger = 0;
        uint32_t first = apply_stick_direction(
            &direction, &stick, 0, 0, x_1, y_1,
            &left_trigger, &right_trigger
        );
        uint32_t final = apply_stick_direction(
            &direction, &stick, 0, 0, x_2, y_2,
            &left_trigger, &right_trigger
        );
        snprintf(
            output, size,
            "{\"cmd\":\"algorithm_test\",\"ok\":1,"
            "\"kind\":\"direction\",\"first\":%lu,\"final\":%lu,"
            "\"active\":%d}\n",
            (unsigned long)first, (unsigned long)final,
            direction.active_direction
        );
        return true;
    }
    return false;
}

static int select_mapping_layer(uint32_t source) {
    int rising_toggle = -1;
    int best_hold = -1;
    for (int i = 0; i < s_runtime.layer_count; i++) {
        mapping_layer_runtime_t *layer = &s_runtime.layers[i];
        bool active =
            layer->activation_mask &&
            (source & layer->activation_mask) == layer->activation_mask;
        bool was_active =
            layer->activation_mask &&
            (s_runtime.previous_source_buttons & layer->activation_mask) ==
                layer->activation_mask;
        if (layer->toggle_mode) {
            if (active && !was_active && (
                rising_toggle < 0 ||
                layer->specificity >
                    s_runtime.layers[rising_toggle].specificity
            )) rising_toggle = i;
        } else if (active && (
            best_hold < 0 ||
            layer->specificity > s_runtime.layers[best_hold].specificity
        )) {
            best_hold = i;
        }
    }
    if (rising_toggle >= 0) {
        s_runtime.toggle_layer =
            s_runtime.toggle_layer == rising_toggle ? -1 : rising_toggle;
    }
    s_runtime.previous_source_buttons = source;
    return best_hold >= 0 ? best_hold : s_runtime.toggle_layer;
}

static int16_t read_i16_le(const uint8_t *data) {
    return (int16_t)((uint16_t)data[0] | ((uint16_t)data[1] << 8));
}

static float soft_deadzone(float value, float deadzone) {
    float magnitude = fabsf(value);
    if (magnitude <= deadzone) return 0.0f;
    return copysignf(magnitude - deadzone, value);
}

static void apply_matrix3(
    const float matrix[9], const float input[3], float output[3]
) {
    for (int row = 0; row < 3; row++) {
        output[row] =
            matrix[row * 3] * input[0] +
            matrix[row * 3 + 1] * input[1] +
            matrix[row * 3 + 2] * input[2];
    }
}

static float wrapped_degrees(float value) {
    while (value > 180.0f) value -= 360.0f;
    while (value < -180.0f) value += 360.0f;
    return value;
}

static bool deadline_pending(uint32_t now, uint32_t deadline) {
    return deadline != 0 && (int32_t)(deadline - now) > 0;
}

#define GYRO_SCALE (1.0f / 14.285714f)
#define GYRO_USABLE_SAMPLES 16u
#define GYRO_FINAL_SAMPLES 64u

static float vector_norm3(const float value[3]) {
    return sqrtf(
        value[0] * value[0] +
        value[1] * value[1] +
        value[2] * value[2]
    );
}

static bool unit_vector3(const float input[3], float output[3]) {
    float magnitude = vector_norm3(input);
    if (!isfinite(magnitude) || magnitude <= 1e-9f) return false;
    for (int i = 0; i < 3; i++) output[i] = input[i] / magnitude;
    return true;
}

static bool correct_accelerometer(
    const gyro_runtime_t *gyro, const float raw[3], float corrected[3]
) {
    if (gyro->accel_calibrated) {
        float delta[3];
        for (int i = 0; i < 3; i++)
            delta[i] = raw[i] - gyro->accel_bias[i];
        apply_matrix3(gyro->accel_matrix, delta, corrected);
    } else {
        float magnitude = vector_norm3(raw);
        if (magnitude <= 1e-9f) return false;
        float gravity_scale =
            fabsf(magnitude - 4096.0f) <=
            fabsf(magnitude - 16384.0f) ? 4096.0f : 16384.0f;
        for (int i = 0; i < 3; i++)
            corrected[i] = raw[i] / gravity_scale;
    }
    float magnitude_squared =
        corrected[0] * corrected[0] +
        corrected[1] * corrected[1] +
        corrected[2] * corrected[2];
    return isfinite(magnitude_squared) &&
        magnitude_squared >= 0.0625f && magnitude_squared <= 6.25f;
}

static bool correct_magnetometer(
    const gyro_runtime_t *gyro, const float raw[3], float corrected[3]
) {
    float raw_magnitude = vector_norm3(raw);
    if (
        !isfinite(raw_magnitude) ||
        raw_magnitude < 1.0f || raw_magnitude > 100000.0f
    ) return false;
    if (!gyro->mag_calibrated) {
        memcpy(corrected, raw, sizeof(float) * 3);
        return true;
    }
    float delta[3];
    for (int i = 0; i < 3; i++)
        delta[i] = raw[i] - gyro->mag_bias[i];
    apply_matrix3(gyro->mag_matrix, delta, corrected);
    return isfinite(corrected[0]) &&
        isfinite(corrected[1]) && isfinite(corrected[2]);
}

static bool magnetic_field_is_stable(
    gyro_runtime_t *gyro, const float corrected[3]
) {
    float magnitude = vector_norm3(corrected);
    if (!isfinite(magnitude) || magnitude <= 1e-9f) return false;
    if (
        !gyro->mag_field_reference_valid ||
        gyro->mag_field_reference <= 1e-9f
    ) {
        gyro->mag_field_reference = magnitude;
        gyro->mag_field_reference_valid = true;
        return true;
    }
    float ratio = magnitude / gyro->mag_field_reference;
    bool stable = ratio >= 0.70f && ratio <= 1.30f;
    if (stable) {
        gyro->mag_field_reference +=
            (magnitude - gyro->mag_field_reference) * 0.001f;
    }
    return stable;
}

static bool update_impact_state(
    gyro_runtime_t *gyro, const float raw_gyro[3],
    const float raw_accel[3], float dt, uint32_t now
) {
    float accel_magnitude = vector_norm3(raw_accel);
    if (!gyro->impact_gravity_scale_valid) {
        float scale =
            fabsf(accel_magnitude - 4096.0f) <=
            fabsf(accel_magnitude - 16384.0f) ? 4096.0f : 16384.0f;
        if (fabsf(accel_magnitude - scale) <= scale * 0.35f) {
            gyro->impact_gravity_scale = scale;
            gyro->impact_gravity_scale_valid = true;
        } else {
            gyro->impact_accel_lp_valid = false;
            memcpy(
                gyro->impact_last_gyro, raw_gyro,
                sizeof(gyro->impact_last_gyro)
            );
            gyro->impact_last_gyro_valid = true;
            return false;
        }
    }
    float accel_g[3];
    for (int i = 0; i < 3; i++)
        accel_g[i] = raw_accel[i] / gyro->impact_gravity_scale;
    if (dt <= 0.0f || dt > 0.10f) {
        memcpy(gyro->impact_accel_lp, accel_g, sizeof(accel_g));
        gyro->impact_accel_lp_valid = true;
        memcpy(
            gyro->impact_last_gyro, raw_gyro,
            sizeof(gyro->impact_last_gyro)
        );
        gyro->impact_last_gyro_valid = true;
        return false;
    }
    bool had_accel = gyro->impact_accel_lp_valid;
    bool had_gyro = gyro->impact_last_gyro_valid;
    float previous_accel[3];
    float previous_gyro[3];
    memcpy(previous_accel, gyro->impact_accel_lp, sizeof(previous_accel));
    memcpy(previous_gyro, gyro->impact_last_gyro, sizeof(previous_gyro));
    float alpha = 1.0f - expf(-dt / 0.020f);
    for (int i = 0; i < 3; i++) {
        gyro->impact_accel_lp[i] = had_accel
            ? previous_accel[i] +
                (accel_g[i] - previous_accel[i]) * alpha
            : accel_g[i];
        gyro->impact_last_gyro[i] = raw_gyro[i];
    }
    gyro->impact_accel_lp_valid = true;
    gyro->impact_last_gyro_valid = true;
    if (!had_accel || !had_gyro) return false;

    float accel_delta[3];
    float gyro_delta[3];
    for (int i = 0; i < 3; i++) {
        accel_delta[i] =
            gyro->impact_accel_lp[i] - previous_accel[i];
        gyro_delta[i] = raw_gyro[i] - previous_gyro[i];
    }
    float jerk = vector_norm3(accel_delta) / dt;
    float gyro_acceleration =
        vector_norm3(gyro_delta) * GYRO_SCALE / dt;
    float magnitude_error =
        fabsf(vector_norm3(gyro->impact_accel_lp) - 1.0f);
    float threshold_scale =
        1.0f + clampf(gyro->rumble_ratio, 0.0f, 1.0f);
    int indicators = 0;
    if (magnitude_error > 0.35f * threshold_scale) indicators++;
    if (jerk > 15.0f * threshold_scale) indicators++;
    if (gyro_acceleration > 900.0f * threshold_scale) indicators++;
    bool impact = indicators >= 2 || (
        magnitude_error > 0.65f * threshold_scale &&
        jerk > 22.0f * threshold_scale
    );
    if (impact) {
        gyro->bias_block_until = now + 300;
        gyro->impact_accel_reject_until = now + 60;
        gyro->impact_accel_recover_until = now + 200;
        gyro->gyro_stationary_samples = 0;
        gyro->gyro_stationary_started_valid = false;
    }
    return impact;
}

static void update_gyro_bias(
    gyro_runtime_t *gyro, const float raw_gyro[3],
    const float raw_accel[3], const float raw_mag[3], uint32_t now
) {
    bool previous_valid = gyro->gyro_last_raw_valid;
    float previous[3];
    memcpy(previous, gyro->gyro_last_raw, sizeof(previous));
    memcpy(gyro->gyro_last_raw, raw_gyro, sizeof(gyro->gyro_last_raw));
    gyro->gyro_last_raw_valid = true;
    if (deadline_pending(now, gyro->bias_block_until)) {
        gyro->gyro_stationary_samples = 0;
        gyro->gyro_stationary_started_valid = false;
        return;
    }
    float accel_magnitude = vector_norm3(raw_accel);
    bool accel_plausible =
        fabsf(accel_magnitude - 4096.0f) < 850.0f ||
        fabsf(accel_magnitude - 16384.0f) < 3000.0f;
    float max_delta = 0.0f;
    float max_raw = 0.0f;
    float residual = 0.0f;
    for (int i = 0; i < 3; i++) {
        max_delta = fmaxf(max_delta, fabsf(raw_gyro[i] - previous[i]));
        max_raw = fmaxf(max_raw, fabsf(raw_gyro[i]));
        residual = fmaxf(
            residual, fabsf(raw_gyro[i] - gyro->gyro_bias[i])
        );
    }
    bool rate_plausible = gyro->gyro_bias_samples < GYRO_FINAL_SAMPLES
        ? max_raw <= 80.0f : residual <= 12.0f;
    float accel_direction[3];
    float mag_direction[3];
    bool accel_direction_valid =
        accel_plausible && unit_vector3(raw_accel, accel_direction);
    float raw_mag_magnitude = vector_norm3(raw_mag);
    bool mag_direction_valid =
        raw_mag_magnitude >= 1.0f && raw_mag_magnitude <= 100000.0f &&
        unit_vector3(raw_mag, mag_direction);
    bool candidate = accel_direction_valid &&
        previous_valid && max_delta <= 12.0f && rate_plausible;
    if (candidate && !gyro->gyro_stationary_started_valid) {
        gyro->gyro_stationary_started_time = now;
        gyro->gyro_stationary_started_valid = true;
        memcpy(
            gyro->gyro_stationary_accel_reference, accel_direction,
            sizeof(gyro->gyro_stationary_accel_reference)
        );
        gyro->gyro_stationary_accel_reference_valid = true;
        if (mag_direction_valid) {
            memcpy(
                gyro->gyro_stationary_mag_reference, mag_direction,
                sizeof(gyro->gyro_stationary_mag_reference)
            );
        }
        gyro->gyro_stationary_mag_reference_valid = mag_direction_valid;
        gyro->gyro_stationary_samples = 1;
        return;
    }
    if (candidate) {
        float accel_dot = 0.0f;
        float mag_dot = 0.0f;
        for (int i = 0; i < 3; i++) {
            accel_dot += gyro->gyro_stationary_accel_reference[i] *
                accel_direction[i];
            if (
                gyro->gyro_stationary_mag_reference_valid &&
                mag_direction_valid
            ) {
                mag_dot += gyro->gyro_stationary_mag_reference[i] *
                    mag_direction[i];
            }
        }
        bool accel_stable =
            gyro->gyro_stationary_accel_reference_valid &&
            accel_dot >= cosf(1.5f * (float)M_PI / 180.0f);
        bool mag_stable =
            !gyro->gyro_stationary_mag_reference_valid ||
            (mag_direction_valid &&
             mag_dot >= cosf(2.0f * (float)M_PI / 180.0f));
        candidate = accel_stable && mag_stable;
    }
    if (!candidate) {
        gyro->gyro_stationary_samples = 0;
        gyro->gyro_stationary_started_valid = false;
        gyro->gyro_stationary_accel_reference_valid = false;
        gyro->gyro_stationary_mag_reference_valid = false;
        return;
    }
    gyro->gyro_stationary_samples++;
    if (
        (uint32_t)(now - gyro->gyro_stationary_started_time) < 500 ||
        gyro->gyro_stationary_samples < 8
    ) return;
    if (gyro->gyro_bias_samples < GYRO_FINAL_SAMPLES) {
        gyro->gyro_bias_samples++;
        float weight = 1.0f / gyro->gyro_bias_samples;
        for (int i = 0; i < 3; i++)
            gyro->gyro_bias[i] +=
                (raw_gyro[i] - gyro->gyro_bias[i]) * weight;
        if (gyro->gyro_bias_samples == GYRO_FINAL_SAMPLES) {
            memcpy(
                gyro->gyro_bias_anchor, gyro->gyro_bias,
                sizeof(gyro->gyro_bias_anchor)
            );
            gyro->gyro_bias_anchor_valid = true;
        }
        return;
    }
    if (!gyro->gyro_bias_anchor_valid) {
        memcpy(
            gyro->gyro_bias_anchor, gyro->gyro_bias,
            sizeof(gyro->gyro_bias_anchor)
        );
        gyro->gyro_bias_anchor_valid = true;
    }
    if (residual <= 12.0f) {
        for (int i = 0; i < 3; i++) {
            float proposed = gyro->gyro_bias[i] +
                (raw_gyro[i] - gyro->gyro_bias[i]) * 0.002f;
            gyro->gyro_bias[i] = clampf(
                proposed,
                gyro->gyro_bias_anchor[i] - 18.0f,
                gyro->gyro_bias_anchor[i] + 18.0f
            );
        }
    }
}

static void update_fusion(
    gyro_runtime_t *gyro, const float raw_gyro[3],
    const float raw_accel[3], const float raw_mag[3],
    float dt, uint32_t now
) {
    float accel[3];
    if (dt <= 0.0f || !correct_accelerometer(gyro, raw_accel, accel))
        return;
    FusionVector gyroscope = {.array = {
        (raw_gyro[0] - gyro->gyro_bias[0]) * GYRO_SCALE,
        (raw_gyro[1] - gyro->gyro_bias[1]) * GYRO_SCALE,
        (raw_gyro[2] - gyro->gyro_bias[2]) * GYRO_SCALE,
    }};
    FusionVector accelerometer = {
        .array = {accel[0], accel[1], accel[2]}
    };
    FusionVector gravity = FusionAhrsGetGravity(&gyro->ahrs);
    float gravity_magnitude = sqrtf(
        gravity.axis.x * gravity.axis.x +
        gravity.axis.y * gravity.axis.y +
        gravity.axis.z * gravity.axis.z
    );
    float accel_magnitude = vector_norm3(accel);
    float blend = 0.0f;
    if (
        gyro->accel_suppression > 0.0f &&
        gravity_magnitude >= 0.5f && gravity_magnitude <= 1.5f &&
        accel_magnitude > 1e-9f
    ) {
        float speed_ratio = vector_norm3(gyroscope.array) / 30.0f;
        float speed_factor =
            speed_ratio * speed_ratio /
            (1.0f + speed_ratio * speed_ratio);
        float magnitude_factor = fminf(
            1.0f, fabsf(accel_magnitude - 1.0f) / 0.12f
        );
        blend = gyro->accel_suppression *
            fmaxf(speed_factor, magnitude_factor);
    }
    float impact_blend = 0.0f;
    if (deadline_pending(now, gyro->impact_accel_reject_until)) {
        impact_blend = 1.0f;
    } else if (
        deadline_pending(now, gyro->impact_accel_recover_until)
    ) {
        float span = fmaxf(
            1.0f,
            (float)(gyro->impact_accel_recover_until -
                    gyro->impact_accel_reject_until)
        );
        impact_blend =
            (gyro->impact_accel_recover_until - now) / span;
    }
    blend = fmaxf(blend, impact_blend);
    if (
        blend > 0.0f &&
        gravity_magnitude >= 0.5f && gravity_magnitude <= 1.5f
    ) {
        float accel_weight = 1.0f - blend;
        accelerometer.axis.x =
            accelerometer.axis.x * accel_weight +
            gravity.axis.x * blend;
        accelerometer.axis.y =
            accelerometer.axis.y * accel_weight +
            gravity.axis.y * blend;
        accelerometer.axis.z =
            accelerometer.axis.z * accel_weight +
            gravity.axis.z * blend;
    }

    float corrected_mag[3];
    bool magnetometer_valid =
        correct_magnetometer(gyro, raw_mag, corrected_mag) &&
        magnetic_field_is_stable(gyro, corrected_mag);
    bool use_magnetometer = false;
    if (magnetometer_valid) {
        if (
            !gyro->mag_last_valid ||
            (uint32_t)(now - gyro->mag_last_valid_time) > 500
        ) {
            gyro->mag_recovery_started_time = now;
            gyro->mag_recovery_started_valid = true;
            gyro->mag_recovery_accumulator = 0.0f;
            gyro->nine_axis_has_magnetometer = false;
        }
        gyro->mag_last_valid = true;
        gyro->mag_last_valid_time = now;
        if (!gyro->mag_recovery_started_valid) {
            gyro->mag_recovery_started_time = now;
            gyro->mag_recovery_started_valid = true;
        }
        float recovery_weight = fminf(
            1.0f,
            (uint32_t)(now - gyro->mag_recovery_started_time) / 750.0f
        );
        if (recovery_weight >= 1.0f) {
            use_magnetometer = true;
        } else {
            gyro->mag_recovery_accumulator += recovery_weight;
            if (gyro->mag_recovery_accumulator >= 1.0f) {
                gyro->mag_recovery_accumulator -= 1.0f;
                use_magnetometer = true;
            }
        }
    } else if (
        !gyro->mag_last_valid ||
        (uint32_t)(now - gyro->mag_last_valid_time) > 500
    ) {
        gyro->mag_recovery_started_valid = false;
        gyro->mag_recovery_accumulator = 0.0f;
        gyro->nine_axis_has_magnetometer = false;
    }
    float bounded_dt = fminf(dt, 0.05f);
    if (use_magnetometer) {
        FusionVector magnetometer = {.array = {
            corrected_mag[0], corrected_mag[2], corrected_mag[1]
        }};
        FusionAhrsUpdate(
            &gyro->ahrs, gyroscope, accelerometer,
            magnetometer, bounded_dt
        );
        gyro->nine_axis_has_magnetometer = true;
    } else {
        FusionAhrsUpdateNoMagnetometer(
            &gyro->ahrs, gyroscope, accelerometer, bounded_dt
        );
    }
    gyro->fusion_valid = true;
    gyro->nine_axis_quaternion = FusionAhrsGetQuaternion(&gyro->ahrs);
    FusionEuler euler = FusionEulerFrom(gyro->nine_axis_quaternion);
    gyro->nine_axis_heading = wrapped_degrees(euler.angle.yaw);
    gyro->nine_axis_roll = wrapped_degrees(-euler.angle.roll);
    gyro->nine_axis_orientation_valid =
        isfinite(gyro->nine_axis_heading) &&
        isfinite(gyro->nine_axis_roll);
}

static bool update_tilt_orientation(
    gyro_runtime_t *gyro, const float raw_gyro[3],
    const float raw_accel[3], float dt,
    float *roll_output, float *pitch_output
) {
    if (dt <= 0.0f) return gyro->tilt_orientation_valid;
    float pitch_rate =
        (raw_gyro[0] - gyro->gyro_bias[0]) * GYRO_SCALE;
    float roll_rate =
        (raw_gyro[2] - gyro->gyro_bias[2]) * GYRO_SCALE;
    float predicted_roll = gyro->tilt_orientation_valid
        ? gyro->tilt_orientation_roll + roll_rate * dt : 0.0f;
    float predicted_pitch = gyro->tilt_orientation_valid
        ? gyro->tilt_orientation_pitch + pitch_rate * dt : 0.0f;
    float accel[3];
    bool accel_valid = correct_accelerometer(gyro, raw_accel, accel);
    if (accel_valid) {
        float accel_roll = wrapped_degrees(
            atan2f(accel[2], -accel[1]) * 180.0f / (float)M_PI
        );
        float accel_pitch = wrapped_degrees(
            atan2f(
                -accel[0],
                sqrtf(accel[2] * accel[2] + accel[1] * accel[1])
            ) * 180.0f / (float)M_PI
        );
        if (!gyro->tilt_orientation_valid) {
            predicted_roll = accel_roll;
            predicted_pitch = accel_pitch;
        } else {
            float raw_magnitude = vector_norm3(raw_accel);
            float nominal_gravity =
                fabsf(raw_magnitude - 4096.0f) <=
                fabsf(raw_magnitude - 16384.0f) ? 4096.0f : 16384.0f;
            float magnitude_error =
                fabsf(raw_magnitude - nominal_gravity) / nominal_gravity;
            float magnitude_confidence =
                fmaxf(0.0f, 1.0f - magnitude_error / 0.12f);
            float angular_speed = sqrtf(
                roll_rate * roll_rate + pitch_rate * pitch_rate
            );
            float motion_ratio = fminf(1.0f, angular_speed / 10.0f);
            float correction_time = 0.15f + 1.35f * motion_ratio;
            float correction =
                (1.0f - expf(-dt / correction_time)) *
                magnitude_confidence * magnitude_confidence;
            predicted_roll +=
                wrapped_degrees(accel_roll - predicted_roll) * correction;
            predicted_pitch +=
                wrapped_degrees(accel_pitch - predicted_pitch) * correction;
        }
    } else if (!gyro->tilt_orientation_valid) {
        return false;
    }
    gyro->tilt_orientation_roll = wrapped_degrees(predicted_roll);
    gyro->tilt_orientation_pitch = wrapped_degrees(predicted_pitch);
    gyro->tilt_orientation_valid = true;
    if (roll_output) *roll_output = gyro->tilt_orientation_roll;
    if (pitch_output) *pitch_output = gyro->tilt_orientation_pitch;
    return true;
}

static bool relative_orientation(
    FusionQuaternion current, FusionQuaternion neutral,
    float *yaw, float *negative_roll
) {
    FusionQuaternion conjugate = {.element = {
        .w = neutral.element.w,
        .x = -neutral.element.x,
        .y = -neutral.element.y,
        .z = -neutral.element.z,
    }};
    FusionQuaternion relative = FusionQuaternionNormalise(
        FusionQuaternionMultiply(conjugate, current)
    );
    FusionEuler euler = FusionEulerFrom(relative);
    if (
        !isfinite(euler.angle.yaw) ||
        !isfinite(euler.angle.roll)
    ) return false;
    if (yaw) *yaw = wrapped_degrees(euler.angle.yaw);
    if (negative_roll)
        *negative_roll = wrapped_degrees(-euler.angle.roll);
    return true;
}

static void gravity_aware_aim_axes(
    gyro_runtime_t *gyro, const float rates[3],
    float dt, uint32_t now, float *horizontal, float *vertical
) {
    float legacy_x = -rates[2];
    float legacy_y = rates[0];
    *horizontal = legacy_x;
    *vertical = legacy_y;
    if (!gyro->player_space) {
        gyro->aim_gravity_sign_valid = false;
        gyro->aim_pose_ready_valid = false;
        gyro->aim_player_space_blend = 0.0f;
        return;
    }
    if (!gyro->was_active) {
        gyro->aim_gravity_sign_valid = false;
        gyro->aim_pose_ready_valid = false;
        gyro->aim_player_space_blend = 0.0f;
    }
    if (!gyro->fusion_valid) return;
    FusionVector gravity = FusionAhrsGetGravity(&gyro->ahrs);
    float values[3] = {
        gravity.axis.x, gravity.axis.y, gravity.axis.z
    };
    float magnitude_squared =
        values[0] * values[0] +
        values[1] * values[1] +
        values[2] * values[2];
    if (
        !isfinite(magnitude_squared) ||
        magnitude_squared < 0.25f || magnitude_squared > 2.25f
    ) return;
    float inverse = 1.0f / sqrtf(magnitude_squared);
    for (int i = 0; i < 3; i++) values[i] *= inverse;
    if (!gyro->aim_gravity_sign_valid) {
        if (fabsf(values[2]) < 0.25f) {
            gyro->aim_pose_ready_valid = false;
            return;
        }
        if (!gyro->aim_pose_ready_valid) {
            gyro->aim_pose_ready_time = now;
            gyro->aim_pose_ready_valid = true;
            return;
        }
        if ((uint32_t)(now - gyro->aim_pose_ready_time) < 80) return;
        gyro->aim_gravity_sign =
            values[2] >= 0.0f ? 1.0f : -1.0f;
        gyro->aim_gravity_sign_valid = true;
    }
    for (int i = 0; i < 3; i++)
        values[i] *= gyro->aim_gravity_sign;
    float vertical_axis[3] = {
        1.0f - values[0] * values[0],
        -values[0] * values[1],
        -values[0] * values[2],
    };
    float vertical_norm = vector_norm3(vertical_axis);
    if (vertical_norm <= 0.15f) return;
    for (int i = 0; i < 3; i++)
        vertical_axis[i] /= vertical_norm;
    float transformed_x = -(
        rates[0] * values[0] +
        rates[1] * values[1] +
        rates[2] * values[2]
    );
    float transformed_y =
        rates[0] * vertical_axis[0] +
        rates[1] * vertical_axis[1] +
        rates[2] * vertical_axis[2];
    float blend_alpha = 1.0f - expf(-fmaxf(0.0f, dt) / 0.25f);
    gyro->aim_player_space_blend +=
        (1.0f - gyro->aim_player_space_blend) * blend_alpha;
    float confidence = clampf(
        (vertical_norm - 0.15f) / 0.25f, 0.0f, 1.0f
    );
    float blend = gyro->aim_player_space_blend * confidence;
    *horizontal =
        legacy_x + (transformed_x - legacy_x) * blend;
    *vertical =
        legacy_y + (transformed_y - legacy_y) * blend;
}

static void clamp_vector_to_shape(
    float *x, float *y, float shape_blend
) {
    float magnitude = sqrtf(*x * *x + *y * *y);
    if (magnitude <= 0.0f) {
        *x = *y = 0.0f;
        return;
    }
    float unit_x = *x / magnitude;
    float unit_y = *y / magnitude;
    float square_radius =
        1.0f / fmaxf(fabsf(unit_x), fabsf(unit_y));
    float maximum_radius = 1.0f + (square_radius - 1.0f) *
        clampf(shape_blend, 0.0f, 1.0f);
    if (magnitude > maximum_radius) {
        float scale = maximum_radius / magnitude;
        *x *= scale;
        *y *= scale;
    }
    *x = clampf(*x, -1.0f, 1.0f);
    *y = clampf(*y, -1.0f, 1.0f);
}

static void apply_gyro_to_report_runtime(
    const uint8_t *payload, size_t length, uint32_t source,
    xinput_report_t *report, gyro_runtime_t *gyro,
    stick_runtime_config_t sticks[2]
) {
    if (
        !report || !gyro || !sticks ||
        length < 60 || gyro->activation_mode == 0
    ) return;

    bool pressed = gyro->activation_match_all
        ? gyro->activation_mask &&
            (source & gyro->activation_mask) == gyro->activation_mask
        : (source & gyro->activation_mask) != 0;
    bool rising = pressed && !gyro->trigger_previous;
    gyro->trigger_previous = pressed;
    if (gyro->activation_mode == 2 && rising)
        gyro->toggle_enabled = !gyro->toggle_enabled;
    bool active = gyro->activation_mode == 1
        ? pressed : gyro->toggle_enabled;
    gyro->diagnostic_active = active;

    uint32_t report_time = read_u32_le(payload);
    float dt = 0.0f;
    if (gyro->last_report_time) {
        uint32_t delta = report_time - gyro->last_report_time;
        if (delta >= 1 && delta <= 50) dt = delta / 1000.0f;
    }
    gyro->last_report_time = report_time;

    float raw_accel[3];
    float raw_gyro[3];
    float raw_mag[3];
    for (int i = 0; i < 3; i++) {
        raw_mag[i] = read_i16_le(payload + 25 + i * 2);
        raw_accel[i] = read_i16_le(payload + 48 + i * 2);
        raw_gyro[i] = read_i16_le(payload + 54 + i * 2);
        gyro->diagnostic_rate[i] =
            (raw_gyro[i] - gyro->gyro_bias[i]) * GYRO_SCALE;
    }

    bool recenter_pressed =
        gyro->recenter_mask && (source & gyro->recenter_mask);
    if (
        gyro->tilt_mode &&
        recenter_pressed && !gyro->previous_recenter
    ) {
        gyro->tilt_neutral_valid = false;
        gyro->tilt_neutral_quaternion_valid = false;
        gyro->was_active = false;
        gyro->smoothed_x = gyro->smoothed_y = 0.0f;
    }
    gyro->previous_recenter = recenter_pressed;
    uint32_t stabilization = source & gyro->stabilization_mask;
    if (
        stabilization != gyro->previous_stabilization &&
        gyro->button_freeze_ms > 0.0f
    ) {
        gyro->freeze_until_report_time =
            report_time + (uint32_t)ceilf(gyro->button_freeze_ms);
    }
    gyro->previous_stabilization = stabilization;

    update_impact_state(
        gyro, raw_gyro, raw_accel, dt, report_time
    );
    bool tilt_tracking_enabled = gyro->tilt_mode;
    bool player_space_tracking_enabled =
        !gyro->tilt_mode && gyro->player_space;
    if (tilt_tracking_enabled || player_space_tracking_enabled) {
        update_fusion(
            gyro, raw_gyro, raw_accel, raw_mag, dt, report_time
        );
    }

    if (!active) {
        update_gyro_bias(
            gyro, raw_gyro, raw_accel, raw_mag, report_time
        );
        if (gyro->tilt_mode) {
            update_tilt_orientation(
                gyro, raw_gyro, raw_accel, dt, NULL, NULL
            );
        }
        gyro->was_active = false;
        gyro->tilt_neutral_valid = false;
        gyro->tilt_neutral_quaternion_valid = false;
        gyro->smoothed_x = gyro->smoothed_y = 0.0f;
        gyro->motion_envelope = 0.0f;
        gyro->diagnostic_output[0] = 0.0f;
        gyro->diagnostic_output[1] = 0.0f;
        return;
    }
    if (gyro->gyro_bias_samples < GYRO_USABLE_SAMPLES) {
        update_gyro_bias(
            gyro, raw_gyro, raw_accel, raw_mag, report_time
        );
        if (gyro->tilt_mode) {
            update_tilt_orientation(
                gyro, raw_gyro, raw_accel, dt, NULL, NULL
            );
        }
        gyro->was_active = false;
        gyro->smoothed_x = gyro->smoothed_y = 0.0f;
        gyro->diagnostic_output[0] = 0.0f;
        gyro->diagnostic_output[1] = 0.0f;
        return;
    }
    if (dt <= 0.0f) {
        gyro->diagnostic_output[0] = 0.0f;
        gyro->diagnostic_output[1] = 0.0f;
        return;
    }
    if (deadline_pending(report_time, gyro->freeze_until_report_time)) {
        gyro->smoothed_x = gyro->smoothed_y = 0.0f;
        gyro->diagnostic_output[0] = 0.0f;
        gyro->diagnostic_output[1] = 0.0f;
        return;
    }

    float horizontal = 0.0f;
    float vertical = 0.0f;
    if (gyro->tilt_mode) {
        float orientation_heading = 0.0f;
        float orientation_roll = 0.0f;
        bool orientation_valid =
            gyro->nine_axis_has_magnetometer &&
            gyro->nine_axis_orientation_valid;
        if (orientation_valid) {
            orientation_heading = gyro->nine_axis_heading;
            orientation_roll = gyro->nine_axis_roll;
        } else {
            orientation_valid = update_tilt_orientation(
                gyro, raw_gyro, raw_accel, dt,
                &orientation_heading, &orientation_roll
            );
        }
        if (!orientation_valid) {
            gyro->was_active = true;
            gyro->smoothed_x = gyro->smoothed_y = 0.0f;
            gyro->diagnostic_output[0] = 0.0f;
            gyro->diagnostic_output[1] = 0.0f;
            return;
        }
        if (
            !gyro->was_active ||
            !gyro->tilt_neutral_valid ||
            (gyro->fusion_valid &&
             !gyro->tilt_neutral_quaternion_valid)
        ) {
            gyro->tilt_neutral_heading = orientation_heading;
            gyro->tilt_neutral_roll = orientation_roll;
            gyro->tilt_neutral_valid = true;
            if (gyro->fusion_valid) {
                gyro->tilt_neutral_quaternion =
                    gyro->nine_axis_quaternion;
                gyro->tilt_neutral_quaternion_valid = true;
            }
            gyro->smoothed_x = gyro->smoothed_y = 0.0f;
        }
        float relative_yaw;
        float relative_negative_roll;
        bool relative_valid =
            gyro->fusion_valid &&
            gyro->tilt_neutral_quaternion_valid &&
            relative_orientation(
                gyro->nine_axis_quaternion,
                gyro->tilt_neutral_quaternion,
                &relative_yaw, &relative_negative_roll
            );
        if (relative_valid) {
            horizontal = -relative_yaw;
            vertical = gyro->tilt_dual
                ? -relative_negative_roll : 0.0f;
        } else {
            horizontal = -wrapped_degrees(
                orientation_heading - gyro->tilt_neutral_heading
            );
            vertical = gyro->tilt_dual
                ? -wrapped_degrees(
                    orientation_roll - gyro->tilt_neutral_roll
                ) : 0.0f;
        }
    } else {
        float rates[3] = {
            (raw_gyro[0] - gyro->gyro_bias[0]) * GYRO_SCALE,
            (raw_gyro[1] - gyro->gyro_bias[1]) * GYRO_SCALE,
            (raw_gyro[2] - gyro->gyro_bias[2]) * GYRO_SCALE,
        };
        gravity_aware_aim_axes(
            gyro, rates, dt, report_time, &horizontal, &vertical
        );
    }

    float active_deadzone;
    if (gyro->tilt_mode) {
        active_deadzone = gyro->tilt_deadzone;
        gyro->motion_envelope = 0.0f;
    } else {
        float motion_speed = sqrtf(
            horizontal * horizontal + vertical * vertical
        );
        float time_constant =
            motion_speed > gyro->motion_envelope ? 0.035f : 0.120f;
        float envelope_alpha =
            1.0f - expf(-dt / time_constant);
        gyro->motion_envelope +=
            (motion_speed - gyro->motion_envelope) * envelope_alpha;
        float adaptive = clampf(
            gyro->motion_envelope / 6.0f, 0.0f, 1.0f
        );
        active_deadzone = gyro->deadzone *
            (1.0f - gyro->adaptive_deadzone * adaptive);
    }
    horizontal = soft_deadzone(horizontal, active_deadzone);
    vertical = soft_deadzone(vertical, active_deadzone);
    horizontal *= gyro->x_ratio;
    vertical *= gyro->y_ratio;
    if (gyro->invert_x) horizontal = -horizontal;
    if (gyro->invert_y) vertical = -vertical;

    stick_runtime_config_t *stick = gyro->target_left
        ? &sticks[0] : &sticks[1];
    if (gyro->tilt_mode) {
        float tilt_range = fmaxf(
            1.0f, gyro->tilt_max_angle - gyro->tilt_deadzone
        );
        float bounded_x = horizontal / tilt_range;
        float bounded_y = vertical / tilt_range;
        clamp_vector_to_shape(
            &bounded_x, &bounded_y, stick->output_shape / 10.0f
        );
        horizontal = bounded_x * tilt_range;
        vertical = bounded_y * tilt_range;
    }

    float smoothing_ms;
    if (gyro->tilt_mode) {
        smoothing_ms = gyro->tilt_smoothing_ms;
    } else {
        float base = fmaxf(0.0f, gyro->smoothing_ms);
        float minimum = fminf(base, 5.0f);
        float speed = sqrtf(
            horizontal * horizontal + vertical * vertical
        );
        float ratio = clampf((speed - 5.0f) / 75.0f, 0.0f, 1.0f);
        ratio = ratio * ratio * (3.0f - 2.0f * ratio);
        smoothing_ms = base + (minimum - base) * ratio;
    }
    float smoothed_x;
    float smoothed_y;
    if (smoothing_ms <= 0.0f) {
        smoothed_x = horizontal;
        smoothed_y = vertical;
    } else {
        float alpha = 1.0f - expf(
            -dt / fmaxf(0.001f, smoothing_ms / 1000.0f)
        );
        smoothed_x = gyro->smoothed_x +
            (horizontal - gyro->smoothed_x) * alpha;
        smoothed_y = gyro->smoothed_y +
            (vertical - gyro->smoothed_y) * alpha;
    }
    gyro->smoothed_x = smoothed_x;
    gyro->smoothed_y = smoothed_y;
    gyro->was_active = true;

    float x;
    float y;
    if (gyro->tilt_mode) {
        float stick_scale = 1.0f / fmaxf(
            1.0f, gyro->tilt_max_angle - gyro->tilt_deadzone
        );
        x = smoothed_x * stick_scale;
        y = smoothed_y * stick_scale;
    } else {
        x = smoothed_x * gyro->stick_sensitivity * 0.016f;
        y = smoothed_y * gyro->stick_sensitivity * 0.016f;
        float magnitude = sqrtf(x * x + y * y);
        if (
            magnitude > 0.00001f && magnitude < 1.0f &&
            gyro->response_curve != 0
        ) {
            float smooth =
                magnitude * magnitude * (3.0f - 2.0f * magnitude);
            float delta = smooth - magnitude;
            if (gyro->response_curve == 2) delta = -delta;
            float mapped =
                magnitude + delta * gyro->curve_strength;
            x *= mapped / magnitude;
            y *= mapped / magnitude;
        }
    }

    int16_t target_x = gyro->target_left
        ? report->left_x : report->right_x;
    int16_t target_y = gyro->target_left
        ? report->left_y : report->right_y;
    float existing_x =
        target_x / (target_x < 0 ? 32768.0f : 32767.0f);
    float existing_y =
        target_y / (target_y < 0 ? 32768.0f : 32767.0f);
    if (!gyro->tilt_mode) {
        float gyro_magnitude = sqrtf(x * x + y * y);
        if (gyro_magnitude > 0.00001f) {
            float existing = sqrtf(
                existing_x * existing_x + existing_y * existing_y
            );
            float remaining = fmaxf(
                0.0f, gyro->anti_deadzone - fminf(1.0f, existing)
            );
            if (remaining > 0.0f && gyro_magnitude < 1.0f) {
                float mapped =
                    remaining + (1.0f - remaining) * gyro_magnitude;
                x *= mapped / gyro_magnitude;
                y *= mapped / gyro_magnitude;
            }
        }
    }
    gyro->diagnostic_output[0] = x;
    gyro->diagnostic_output[1] = y;
    float final_x = existing_x + x;
    float final_y = existing_y + y;
    clamp_vector_to_shape(
        &final_x, &final_y, stick->output_shape / 10.0f
    );
    target_x = (int16_t)lrintf(
        final_x * (final_x < 0.0f ? 32768.0f : 32767.0f)
    );
    target_y = (int16_t)lrintf(
        final_y * (final_y < 0.0f ? 32768.0f : 32767.0f)
    );
    if (gyro->target_left) {
        report->left_x = target_x;
        report->left_y = target_y;
    } else {
        report->right_x = target_x;
        report->right_y = target_y;
    }
}

static void write_i16_le(uint8_t *output, int value) {
    int16_t bounded = (int16_t)(
        value < INT16_MIN ? INT16_MIN :
        value > INT16_MAX ? INT16_MAX : value
    );
    output[0] = (uint8_t)((uint16_t)bounded & 0xffu);
    output[1] = (uint8_t)(((uint16_t)bounded >> 8) & 0xffu);
}

bool standalone_xinput_format_gyro_test(
    const char *arguments, char *output, size_t size
) {
    static gyro_runtime_t test_gyro;
    static stick_runtime_config_t test_sticks[2];
    static bool initialised;
    if (!arguments || !output || size == 0) return false;
    char reset_mode[8] = {0};
    if (
        strcmp(arguments, "reset") == 0 ||
        sscanf(arguments, "reset %7s", reset_mode) == 1
    ) {
        test_gyro = s_runtime.gyro;
        memcpy(test_sticks, s_runtime.sticks, sizeof(test_sticks));
        if (strcmp(reset_mode, "CENTER") == 0)
            test_gyro.tilt_mode = false;
        else if (strcmp(reset_mode, "TILT") == 0)
            test_gyro.tilt_mode = true;
        reset_gyro_runtime_state(&test_gyro);
        initialised = true;
        snprintf(
            output, size,
            "{\"cmd\":\"gyro_test\",\"ok\":1,\"kind\":\"reset\","
            "\"mode\":%u,\"target\":\"%s\",\"motion\":\"%s\","
            "\"bias_samples\":%u}\n",
            test_gyro.activation_mode,
            test_gyro.target_left ? "LEFT" : "RIGHT",
            test_gyro.tilt_mode ? "TILT" : "CENTER",
            test_gyro.gyro_bias_samples
        );
        return true;
    }
    unsigned long report_time;
    unsigned long source;
    int mag[3], accel[3], rate[3], existing_x, existing_y;
    if (
        !initialised ||
        sscanf(
            arguments,
            "sample %lu %lx %d %d %d %d %d %d %d %d %d %d %d",
            &report_time, &source,
            &mag[0], &mag[1], &mag[2],
            &accel[0], &accel[1], &accel[2],
            &rate[0], &rate[1], &rate[2],
            &existing_x, &existing_y
        ) != 13
    ) return false;
    uint8_t payload[60] = {0};
    payload[0] = (uint8_t)(report_time & 0xffu);
    payload[1] = (uint8_t)((report_time >> 8) & 0xffu);
    payload[2] = (uint8_t)((report_time >> 16) & 0xffu);
    payload[3] = (uint8_t)((report_time >> 24) & 0xffu);
    for (int i = 0; i < 3; i++) {
        write_i16_le(payload + 25 + i * 2, mag[i]);
        write_i16_le(payload + 48 + i * 2, accel[i]);
        write_i16_le(payload + 54 + i * 2, rate[i]);
    }
    xinput_report_t report = {.report_size = 20};
    if (test_gyro.target_left) {
        report.left_x = (int16_t)existing_x;
        report.left_y = (int16_t)existing_y;
    } else {
        report.right_x = (int16_t)existing_x;
        report.right_y = (int16_t)existing_y;
    }
    apply_gyro_to_report_runtime(
        payload, sizeof(payload), (uint32_t)source,
        &report, &test_gyro, test_sticks
    );
    snprintf(
        output, size,
        "{\"cmd\":\"gyro_test\",\"ok\":1,\"kind\":\"sample\","
        "\"stick\":[%d,%d],\"output\":[%.7f,%.7f],"
        "\"rate\":[%.5f,%.5f,%.5f],\"bias\":[%.5f,%.5f,%.5f],"
        "\"bias_samples\":%u,\"active\":%d,\"fusion\":%d,\"mag\":%d,"
        "\"orientation\":[%.5f,%.5f]}\n",
        test_gyro.target_left ? report.left_x : report.right_x,
        test_gyro.target_left ? report.left_y : report.right_y,
        test_gyro.diagnostic_output[0],
        test_gyro.diagnostic_output[1],
        test_gyro.diagnostic_rate[0],
        test_gyro.diagnostic_rate[1],
        test_gyro.diagnostic_rate[2],
        test_gyro.gyro_bias[0],
        test_gyro.gyro_bias[1],
        test_gyro.gyro_bias[2],
        test_gyro.gyro_bias_samples,
        test_gyro.diagnostic_active,
        test_gyro.fusion_valid,
        test_gyro.nine_axis_has_magnetometer,
        test_gyro.nine_axis_heading,
        test_gyro.nine_axis_roll
    );
    return true;
}

standalone_output_mode_t standalone_output_mode_load(void) {
    nvs_handle_t nvs;
    uint8_t stored_mode = STANDALONE_OUTPUT_BRIDGE;
    esp_err_t err = nvs_open(
        STANDALONE_NVS_NAMESPACE, NVS_READWRITE, &nvs
    );
    if (err != ESP_OK) return STANDALONE_OUTPUT_BRIDGE;
    err = nvs_get_u8(nvs, STANDALONE_OUTPUT_MODE_KEY, &stored_mode);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        uint8_t legacy_standalone = 0;
        uint8_t legacy_hid = 0;
        nvs_get_u8(nvs, STANDALONE_MODE_KEY, &legacy_standalone);
        nvs_get_u8(nvs, STANDALONE_USB_MODE_KEY, &legacy_hid);
        stored_mode = !legacy_standalone ? STANDALONE_OUTPUT_BRIDGE :
            (legacy_hid ? STANDALONE_OUTPUT_HID :
                          STANDALONE_OUTPUT_XINPUT);
        err = nvs_set_u8(
            nvs, STANDALONE_OUTPUT_MODE_KEY, stored_mode
        );
        if (err == ESP_OK) nvs_erase_key(nvs, STANDALONE_MODE_KEY);
        if (err == ESP_OK) nvs_erase_key(nvs, STANDALONE_USB_MODE_KEY);
        if (err == ESP_OK) nvs_commit(nvs);
    }
    nvs_close(nvs);
    if (stored_mode > STANDALONE_OUTPUT_HID)
        return STANDALONE_OUTPUT_BRIDGE;
    return (standalone_output_mode_t)stored_mode;
}

esp_err_t standalone_output_mode_store(standalone_output_mode_t mode) {
    if (mode < STANDALONE_OUTPUT_BRIDGE || mode > STANDALONE_OUTPUT_HID)
        return ESP_ERR_INVALID_ARG;
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(
        STANDALONE_NVS_NAMESPACE, NVS_READWRITE, &nvs
    );
    if (err != ESP_OK) return err;
    err = nvs_set_u8(nvs, STANDALONE_OUTPUT_MODE_KEY, (uint8_t)mode);
    if (err == ESP_OK) err = nvs_commit(nvs);
    nvs_close(nvs);
    return err;
}

void standalone_xinput_configure(
    tinyusb_config_t *config, bool enabled, bool usb_hid_mode
) {
    if (!config) return;
    s_usb_hid_mode = enabled && usb_hid_mode;
    s_usb_xinput_mode = enabled && !usb_hid_mode;
    if (s_usb_hid_mode) {
        config->device_descriptor = &s_hid_device_descriptor;
        config->configuration_descriptor = s_hid_configuration_descriptor;
        config->string_descriptor = s_hid_string_descriptors;
        config->string_descriptor_count =
            sizeof(s_hid_string_descriptors) /
            sizeof(s_hid_string_descriptors[0]);
    } else if (s_usb_xinput_mode) {
        config->device_descriptor = &s_xinput_device_descriptor;
        config->configuration_descriptor =
            s_xinput_configuration_descriptor;
        config->string_descriptor = s_xinput_string_descriptors;
        config->string_descriptor_count =
            sizeof(s_xinput_string_descriptors) /
            sizeof(s_xinput_string_descriptors[0]);
    } else {
        config->device_descriptor = &s_bridge_device_descriptor;
        config->configuration_descriptor =
            s_bridge_configuration_descriptor;
        config->string_descriptor = s_bridge_string_descriptors;
        config->string_descriptor_count =
            sizeof(s_bridge_string_descriptors) /
            sizeof(s_bridge_string_descriptors[0]);
    }
}

void standalone_xinput_set_wakeup_cb(
    standalone_xinput_wakeup_cb_t callback
) {
    s_wakeup_cb = callback;
}

void standalone_xinput_get_latency_metrics(
    standalone_usb_latency_metrics_t *metrics
) {
    if (!metrics) return;
    portENTER_CRITICAL(&s_state_mux);
    *metrics = s_usb_latency_metrics;
    portEXIT_CRITICAL(&s_state_mux);
}

void standalone_xinput_reset_latency_metrics(void) {
    portENTER_CRITICAL(&s_state_mux);
    memset(&s_usb_latency_metrics, 0, sizeof(s_usb_latency_metrics));
    s_usb_wait_active = false;
    s_usb_wait_started_us = 0;
    portEXIT_CRITICAL(&s_state_mux);
}

void standalone_xinput_accept_switch_report(
    int channel, const uint8_t *payload, size_t length
) {
    if (!payload || length < 16) return;
    bool accepted;
    bool idle_baseline_valid;
    uint32_t idle_buttons;
    uint16_t idle_sticks[4];
    int16_t idle_gyro[3];
    portENTER_CRITICAL(&s_state_mux);
    if (s_active_channel < 0) s_active_channel = channel;
    accepted = channel == s_active_channel;
    idle_baseline_valid = s_idle_baseline_valid;
    idle_buttons = s_idle_buttons;
    memcpy(idle_sticks, s_idle_sticks, sizeof(idle_sticks));
    memcpy(idle_gyro, s_idle_gyro, sizeof(idle_gyro));
    portEXIT_CRITICAL(&s_state_mux);
    if (accepted) {
        uint32_t report_time = read_u32_le(payload);
        uint32_t source = read_u32_le(payload + 4);
        xinput_report_t report = {.report_size = 20};
        int layer_index = select_mapping_layer(source);
        const uint32_t *button_targets = s_runtime.button_targets;
        stick_direction_runtime_t *directions = s_runtime.directions;
        if (layer_index >= 0 && layer_index < s_runtime.layer_count) {
            button_targets =
                s_runtime.layers[layer_index].button_targets;
            directions = s_runtime.layers[layer_index].directions;
        }
        uint32_t targets = 0;
        for (int i = 0; i < SOURCE_BUTTON_COUNT; i++) {
            if (source & s_source_masks[i])
                targets |= button_targets[i];
        }
        report.buttons1 = (uint8_t)(targets & 0xffu);
        report.buttons2 = (uint8_t)((targets >> 8) & 0xffu);
        report.left_trigger = (targets & TARGET_LT) ? 0xff : 0;
        report.right_trigger = (targets & TARGET_RT) ? 0xff : 0;
        uint16_t left_raw_x = stick_x(payload + 10);
        uint16_t left_raw_y = stick_y(payload + 10);
        uint16_t right_raw_x = stick_x(payload + 13);
        uint16_t right_raw_y = stick_y(payload + 13);
        uint16_t raw_sticks[4] = {
            left_raw_x, left_raw_y, right_raw_x, right_raw_y
        };
        int16_t stick_centers[4] = {
            s_runtime.sticks[0].center_x,
            s_runtime.sticks[0].center_y,
            s_runtime.sticks[1].center_x,
            s_runtime.sticks[1].center_y,
        };
        int16_t raw_gyro[3] = {0, 0, 0};
        if (length >= 60) {
            for (int i = 0; i < 3; i++)
                raw_gyro[i] = read_i16_le(payload + 54 + i * 2);
        }
        bool activity = !idle_baseline_valid || source != idle_buttons;
        for (int i = 0; i < 4 && !activity; i++) {
            bool outside =
                abs((int)raw_sticks[i] - stick_centers[i]) >= 150;
            bool was_outside =
                abs((int)s_idle_sticks[i] - stick_centers[i]) >= 150;
            if (
                abs((int)raw_sticks[i] - (int)idle_sticks[i]) >= 24 &&
                (outside || was_outside)
            ) activity = true;
        }
        for (int i = 0; i < 3 && !activity; i++) {
            if (
                abs((int)raw_gyro[i] - (int)idle_gyro[i]) >= 40 &&
                abs((int)raw_gyro[i]) >= 120
            ) activity = true;
        }
        int16_t left_x, left_y, right_x, right_y;
        process_stick(
            left_raw_x, left_raw_y, report_time,
            &s_runtime.sticks[0], &left_x, &left_y
        );
        process_stick(
            right_raw_x, right_raw_y, report_time,
            &s_runtime.sticks[1], &right_x, &right_y
        );
        report.left_x = left_x;
        report.left_y = left_y;
        report.right_x = right_x;
        report.right_y = right_y;
        uint32_t direction_targets = 0;
        direction_targets |= apply_stick_direction(
            &directions[0], &s_runtime.sticks[0],
            left_raw_x, left_raw_y,
            left_x / (left_x < 0 ? 32768.0f : 32767.0f),
            left_y / (left_y < 0 ? 32768.0f : 32767.0f),
            &report.left_trigger, &report.right_trigger
        );
        direction_targets |= apply_stick_direction(
            &directions[1], &s_runtime.sticks[1],
            right_raw_x, right_raw_y,
            right_x / (right_x < 0 ? 32768.0f : 32767.0f),
            right_y / (right_y < 0 ? 32768.0f : 32767.0f),
            &report.left_trigger, &report.right_trigger
        );
        report.buttons1 |= (uint8_t)(direction_targets & 0xffu);
        report.buttons2 |= (uint8_t)((direction_targets >> 8) & 0xffu);
        if (direction_targets & TARGET_LT) report.left_trigger = 0xff;
        if (direction_targets & TARGET_RT) report.right_trigger = 0xff;
        /*
         * Match desktop conversion semantics: supported direction mappings
         * consume the physical stick instead of emitting the native axis and
         * mapped target together. Gyro runs afterwards and can still own it.
         */
        if (stick_direction_consumes_native_output(&directions[0])) {
            report.left_x = 0;
            report.left_y = 0;
        }
        if (stick_direction_consumes_native_output(&directions[1])) {
            report.right_x = 0;
            report.right_y = 0;
        }
        apply_gyro_to_report_runtime(
            payload, length, source, &report,
            &s_runtime.gyro, s_runtime.sticks
        );
        portENTER_CRITICAL(&s_state_mux);
        if (channel == s_active_channel) {
            bool pending = s_usb_hid_mode
                ? s_hid_report_dirty : s_report_dirty;
            if (pending) s_usb_latency_metrics.pending_overwrites++;
            s_idle_buttons = source;
            memcpy(s_idle_sticks, raw_sticks, sizeof(raw_sticks));
            memcpy(s_idle_gyro, raw_gyro, sizeof(raw_gyro));
            s_idle_baseline_valid = true;
            if (activity) s_last_activity_ms = idle_now_ms();
            s_pending_report = report;
            s_report_dirty = true;
            s_hid_report_dirty = true;
        }
        portEXIT_CRITICAL(&s_state_mux);
    }
}

void standalone_xinput_forget_channel(int channel) {
    portENTER_CRITICAL(&s_state_mux);
    if (channel == s_active_channel) {
        memset(&s_pending_report, 0, sizeof(s_pending_report));
        s_pending_report.report_size = 20;
        s_report_dirty = true;
        s_hid_report_dirty = true;
        s_active_channel = -1;
        s_idle_baseline_valid = false;
        s_last_activity_ms = idle_now_ms();
    }
    portEXIT_CRITICAL(&s_state_mux);
}

bool standalone_xinput_idle_disconnect_due(void) {
    bool due = false;
    uint32_t now = idle_now_ms();
    portENTER_CRITICAL(&s_state_mux);
    if (
        s_active_channel >= 0 &&
        s_runtime.idle_disconnect_minutes > 0 &&
        s_last_activity_ms > 0
    ) {
        uint32_t elapsed = now - s_last_activity_ms;
        due = elapsed >=
            (uint32_t)s_runtime.idle_disconnect_minutes * 60u * 1000u;
        if (due) {
            memset(&s_pending_report, 0, sizeof(s_pending_report));
            s_pending_report.report_size = 20;
            s_report_dirty = true;
            s_hid_report_dirty = true;
            s_large_motor = 0;
            s_small_motor = 0;
            s_rumble_dirty = true;
            s_last_activity_ms = now;
        }
    }
    portEXIT_CRITICAL(&s_state_mux);
    return due;
}

static void arm_out_endpoint(void) {
    if (!s_endpoint_out || usbd_edpt_busy(0, s_endpoint_out)) return;
    usbd_edpt_claim(0, s_endpoint_out);
    usbd_edpt_xfer(0, s_endpoint_out, s_out_buffer, sizeof(s_out_buffer));
    usbd_edpt_release(0, s_endpoint_out);
}

static void wake_output_if_pending(bool hid_mode) {
    bool pending;
    portENTER_CRITICAL(&s_state_mux);
    pending = hid_mode ? s_hid_report_dirty : s_report_dirty;
    portEXIT_CRITICAL(&s_state_mux);
    if (pending && s_wakeup_cb) s_wakeup_cb();
}

static bool usb_report_pending(bool hid_mode) {
    bool pending;
    portENTER_CRITICAL(&s_state_mux);
    pending = hid_mode ? s_hid_report_dirty : s_report_dirty;
    portEXIT_CRITICAL(&s_state_mux);
    return pending;
}

static void note_usb_wait_if_pending(bool hid_mode) {
    int64_t now_us = esp_timer_get_time();
    portENTER_CRITICAL(&s_state_mux);
    bool pending = hid_mode ? s_hid_report_dirty : s_report_dirty;
    if (pending && !s_usb_wait_active) {
        s_usb_wait_active = true;
        s_usb_wait_started_us = now_us;
        s_usb_latency_metrics.busy_events++;
    }
    portEXIT_CRITICAL(&s_state_mux);
}

static void finish_usb_wait(void) {
    int64_t now_us = esp_timer_get_time();
    portENTER_CRITICAL(&s_state_mux);
    if (s_usb_wait_active) {
        int64_t elapsed_us = now_us - s_usb_wait_started_us;
        uint32_t wait_us = elapsed_us <= 0
            ? 0
            : elapsed_us > UINT32_MAX
                ? UINT32_MAX : (uint32_t)elapsed_us;
        s_usb_latency_metrics.wait_samples++;
        s_usb_latency_metrics.wait_total_us += wait_us;
        if (wait_us > s_usb_latency_metrics.wait_max_us)
            s_usb_latency_metrics.wait_max_us = wait_us;
        s_usb_wait_active = false;
        s_usb_wait_started_us = 0;
    }
    portEXIT_CRITICAL(&s_state_mux);
}

void standalone_xinput_pump(void) {
    if (s_usb_hid_mode) {
        if (!tud_ready()) return;
        if (!tud_hid_ready()) {
            note_usb_wait_if_pending(true);
            return;
        }
        xinput_report_t snapshot;
        bool dirty;
        portENTER_CRITICAL(&s_state_mux);
        dirty = s_hid_report_dirty;
        snapshot = s_pending_report;
        if (dirty) s_hid_report_dirty = false;
        portEXIT_CRITICAL(&s_state_mux);
        if (!dirty) return;
        uint8_t report[STANDALONE_USB_HID_REPORT_SIZE];
        encode_hid_report(&snapshot, report);
        if (!tud_hid_report(0, report, sizeof(report))) {
            portENTER_CRITICAL(&s_state_mux);
            s_hid_report_dirty = true;
            portEXIT_CRITICAL(&s_state_mux);
            note_usb_wait_if_pending(true);
        } else {
            finish_usb_wait();
        }
        return;
    }
    if (!tud_ready() || !s_endpoint_in) return;
    arm_out_endpoint();
    if (usbd_edpt_busy(0, s_endpoint_in)) {
        note_usb_wait_if_pending(false);
        return;
    }
    if (!usb_report_pending(false)) return;
    bool dirty;
    portENTER_CRITICAL(&s_state_mux);
    dirty = s_report_dirty;
    s_tx_report = s_pending_report;
    if (dirty) s_report_dirty = false;
    portEXIT_CRITICAL(&s_state_mux);
    if (!dirty) return;
    usbd_edpt_claim(0, s_endpoint_in);
    bool sent = usbd_edpt_xfer(
        0, s_endpoint_in, (uint8_t *)&s_tx_report, sizeof(s_tx_report)
    );
    usbd_edpt_release(0, s_endpoint_in);
    if (!sent) {
        portENTER_CRITICAL(&s_state_mux);
        s_report_dirty = true;
        portEXIT_CRITICAL(&s_state_mux);
        note_usb_wait_if_pending(false);
    } else {
        finish_usb_wait();
    }
}

static int16_t hid_axis_y(int16_t value) {
    return value == INT16_MIN ? INT16_MAX : (int16_t)-value;
}

static uint8_t hid_hat(uint8_t buttons1) {
    bool up = (buttons1 & 0x01u) != 0;
    bool down = (buttons1 & 0x02u) != 0;
    bool left = (buttons1 & 0x04u) != 0;
    bool right = (buttons1 & 0x08u) != 0;
    if (up == down) {
        up = false;
        down = false;
    }
    if (left == right) {
        left = false;
        right = false;
    }
    if (up && right) return 1;
    if (right && down) return 3;
    if (down && left) return 5;
    if (left && up) return 7;
    if (up) return 0;
    if (right) return 2;
    if (down) return 4;
    if (left) return 6;
    return 8;
}

static void encode_hid_report(
    const xinput_report_t *source,
    uint8_t output[STANDALONE_USB_HID_REPORT_SIZE]
) {
    uint16_t buttons = 0;
    if (source->buttons2 & 0x10u) buttons |= 1u << 0;  /* A */
    if (source->buttons2 & 0x20u) buttons |= 1u << 1;  /* B */
    if (source->buttons2 & 0x40u) buttons |= 1u << 2;  /* X */
    if (source->buttons2 & 0x80u) buttons |= 1u << 3;  /* Y */
    if (source->buttons2 & 0x01u) buttons |= 1u << 4;  /* LB */
    if (source->buttons2 & 0x02u) buttons |= 1u << 5;  /* RB */
    if (source->buttons1 & 0x20u) buttons |= 1u << 6;  /* Back */
    if (source->buttons1 & 0x10u) buttons |= 1u << 7;  /* Start */
    if (source->buttons1 & 0x40u) buttons |= 1u << 8;  /* LS */
    if (source->buttons1 & 0x80u) buttons |= 1u << 9;  /* RS */
    if (source->buttons2 & 0x04u) buttons |= 1u << 10; /* Guide */
    /*
     * Keep the standards-based Brake/Accelerator axes below, and mirror
     * meaningful trigger travel to Button 9/10 (Android L2/R2).  A small
     * threshold avoids digital chatter from analog mappings near zero.
     */
    if (source->left_trigger >= 32u) buttons |= 1u << 13;  /* L2 */
    if (source->right_trigger >= 32u) buttons |= 1u << 14; /* R2 */

    int16_t axes[4] = {
        source->left_x,
        hid_axis_y(source->left_y),
        source->right_x,
        hid_axis_y(source->right_y),
    };
    output[0] = (uint8_t)(buttons & 0xffu);
    output[1] = (uint8_t)(buttons >> 8);
    output[2] = hid_hat(source->buttons1);
    for (size_t i = 0; i < 4; i++) {
        output[3 + i * 2] = (uint8_t)((uint16_t)axes[i] & 0xffu);
        output[4 + i * 2] = (uint8_t)((uint16_t)axes[i] >> 8);
    }
    output[11] = source->left_trigger;
    output[12] = source->right_trigger;
}

bool standalone_xinput_take_rumble(
    uint8_t *large_motor, uint8_t *small_motor
) {
    bool dirty;
    portENTER_CRITICAL(&s_state_mux);
    dirty = s_rumble_dirty;
    if (dirty) {
        if (large_motor) *large_motor = s_large_motor;
        if (small_motor) *small_motor = s_small_motor;
        s_rumble_dirty = false;
    }
    portEXIT_CRITICAL(&s_state_mux);
    return dirty;
}

static void xinput_init(void) {}
static bool xinput_deinit(void) { return true; }
static void xinput_reset(uint8_t rhport) {
    (void)rhport;
    s_endpoint_in = 0;
    s_endpoint_out = 0;
}

static uint16_t xinput_open(
    uint8_t rhport,
    const tusb_desc_interface_t *interface,
    uint16_t max_length
) {
    if (interface->bInterfaceClass != TUSB_CLASS_VENDOR_SPECIFIC ||
        interface->bInterfaceSubClass != 0x5D ||
        interface->bInterfaceProtocol != 0x01)
        return 0;
    if (max_length < XINPUT_DESC_LEN) return 0;
    const uint8_t *descriptor = tu_desc_next(interface);
    uint8_t endpoints = 0;
    while (endpoints < interface->bNumEndpoints) {
        if (tu_desc_type(descriptor) == TUSB_DESC_ENDPOINT) {
            const tusb_desc_endpoint_t *endpoint =
                (const tusb_desc_endpoint_t *)descriptor;
            TU_ASSERT(usbd_edpt_open(rhport, endpoint), 0);
            if (tu_edpt_dir(endpoint->bEndpointAddress) == TUSB_DIR_IN)
                s_endpoint_in = endpoint->bEndpointAddress;
            else
                s_endpoint_out = endpoint->bEndpointAddress;
            endpoints++;
        }
        descriptor = tu_desc_next(descriptor);
    }
    return XINPUT_DESC_LEN;
}

static bool xinput_control(
    uint8_t rhport, uint8_t stage, const tusb_control_request_t *request
) {
    (void)rhport;
    (void)stage;
    (void)request;
    return true;
}

static bool xinput_transfer(
    uint8_t rhport, uint8_t endpoint, xfer_result_t result,
    uint32_t transferred
) {
    (void)rhport;
    if (endpoint == s_endpoint_in) {
        /*
         * A newer BLE state may have arrived while the IN endpoint was busy.
         * Wake the output task as soon as the host completes this transfer
         * instead of relying on its 2 ms maintenance timeout.
         */
        wake_output_if_pending(false);
    }
    if (endpoint == s_endpoint_out && result == XFER_RESULT_SUCCESS) {
        if (transferred >= 5 &&
            s_out_buffer[0] == 0x00 && s_out_buffer[1] == 0x08) {
            portENTER_CRITICAL(&s_state_mux);
            s_large_motor = s_out_buffer[3];
            s_small_motor = s_out_buffer[4];
            s_rumble_dirty = true;
            portEXIT_CRITICAL(&s_state_mux);
        }
        arm_out_endpoint();
    }
    return true;
}

static const usbd_class_driver_t s_xinput_driver = {
    .name = "XINPUT",
    .init = xinput_init,
    .deinit = xinput_deinit,
    .reset = xinput_reset,
    .open = xinput_open,
    .control_xfer_cb = xinput_control,
    .xfer_cb = xinput_transfer,
    .xfer_isr = NULL,
    .sof = NULL,
};

const usbd_class_driver_t *usbd_app_driver_get_cb(uint8_t *driver_count) {
    if (!s_usb_xinput_mode) {
        *driver_count = 0;
        return NULL;
    }
    *driver_count = 1;
    return &s_xinput_driver;
}

const uint8_t *tud_descriptor_bos_cb(void) {
    return s_usb_xinput_mode ? s_bos_descriptor : NULL;
}

bool tud_vendor_control_xfer_cb(
    uint8_t rhport, uint8_t stage, const tusb_control_request_t *request
) {
    if (!s_usb_xinput_mode) return false;
    if (stage != CONTROL_STAGE_SETUP) return true;
    if (request->bmRequestType_bit.type == TUSB_REQ_TYPE_VENDOR &&
        request->bRequest == MS_VENDOR_REQUEST &&
        request->wIndex == 7) {
        return tud_control_xfer(
            rhport, request, (void *)s_ms_os_20_descriptor,
            sizeof(s_ms_os_20_descriptor)
        );
    }
    return false;
}

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
    (void)instance;
    return s_hid_report_descriptor;
}

uint16_t tud_hid_get_report_cb(
    uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
    uint8_t *buffer, uint16_t requested_length
) {
    (void)instance;
    (void)report_id;
    if (!s_usb_hid_mode || report_type != HID_REPORT_TYPE_INPUT ||
            !buffer || requested_length == 0) {
        return 0;
    }
    uint8_t report[STANDALONE_USB_HID_REPORT_SIZE];
    xinput_report_t snapshot;
    portENTER_CRITICAL(&s_state_mux);
    snapshot = s_pending_report;
    portEXIT_CRITICAL(&s_state_mux);
    encode_hid_report(&snapshot, report);
    uint16_t count = requested_length < sizeof(report)
        ? requested_length : sizeof(report);
    memcpy(buffer, report, count);
    return count;
}

void tud_hid_set_report_cb(
    uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
    uint8_t const *buffer, uint16_t size
) {
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)size;
}

void tud_hid_report_complete_cb(
    uint8_t instance, uint8_t const *report, uint16_t len
) {
    (void)instance;
    (void)report;
    (void)len;
    wake_output_if_pending(true);
}
