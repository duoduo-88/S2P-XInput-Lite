#include "standalone_profile_store.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "nvs.h"
#include "standalone_xinput.h"

#define STANDALONE_NVS_NAMESPACE "s2p_profile"
#define STANDALONE_PROFILE_MAGIC 0x53325031u

typedef struct {
    uint32_t magic;
    uint16_t schema;
    uint16_t reserved;
    uint32_t length;
    uint32_t crc32;
} standalone_profile_header_t;

static const char *TAG = "S2P_PROFILE";
static uint8_t *s_staging;
static size_t s_expected;
static size_t s_received;
static uint16_t s_schema;
static uint32_t s_crc32;
static standalone_profile_fault_hook_t s_fault_hook;

static uint32_t profile_crc32(const uint8_t *data, size_t length) {
    uint32_t crc = 0xffffffffu;
    for (size_t i = 0; i < length; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return crc ^ 0xffffffffu;
}

static void reset_staging(void) {
    free(s_staging);
    s_staging = NULL;
    s_expected = 0;
    s_received = 0;
    s_schema = 0;
    s_crc32 = 0;
}

static bool inject_fault(standalone_profile_fault_stage_t stage) {
    return s_fault_hook && s_fault_hook(stage);
}

static size_t parse_hex(
    const char *text, uint8_t *output, size_t capacity
) {
    size_t count = 0;
    while (text && text[0] && text[1] && count < capacity) {
        char pair[3] = {text[0], text[1], '\0'};
        char *end = NULL;
        unsigned long value = strtoul(pair, &end, 16);
        if (!end || *end != '\0') break;
        output[count++] = (uint8_t)value;
        text += 2;
    }
    return count;
}

static bool read_slot(
    nvs_handle_t nvs,
    uint8_t slot,
    standalone_profile_header_t *header
) {
    const char *key = slot == 0 ? "slot_a" : "slot_b";
    size_t size = 0;
    if (nvs_get_blob(nvs, key, NULL, &size) != ESP_OK ||
        size < sizeof(*header) ||
        size > sizeof(*header) + STANDALONE_PROFILE_MAX)
        return false;
    uint8_t *blob = malloc(size);
    if (!blob) return false;
    bool valid = false;
    if (nvs_get_blob(nvs, key, blob, &size) == ESP_OK) {
        memcpy(header, blob, sizeof(*header));
        valid =
            header->magic == STANDALONE_PROFILE_MAGIC &&
            header->schema == STANDALONE_PROFILE_SCHEMA &&
            header->length == size - sizeof(*header) &&
            profile_crc32(blob + sizeof(*header), header->length) ==
                header->crc32;
    }
    free(blob);
    return valid;
}

static bool apply_slot(nvs_handle_t nvs, uint8_t slot) {
    const char *key = slot == 0 ? "slot_a" : "slot_b";
    size_t size = 0;
    if (nvs_get_blob(nvs, key, NULL, &size) != ESP_OK ||
        size < sizeof(standalone_profile_header_t) ||
        size > sizeof(standalone_profile_header_t) + STANDALONE_PROFILE_MAX)
        return false;
    uint8_t *blob = malloc(size);
    if (!blob) return false;
    standalone_profile_header_t header = {0};
    bool valid = nvs_get_blob(nvs, key, blob, &size) == ESP_OK;
    if (valid) {
        memcpy(&header, blob, sizeof(header));
        valid =
            header.magic == STANDALONE_PROFILE_MAGIC &&
            header.schema == STANDALONE_PROFILE_SCHEMA &&
            header.length == size - sizeof(header) &&
            profile_crc32(blob + sizeof(header), header.length) ==
                header.crc32;
    }
    if (valid)
        valid = standalone_xinput_apply_profile_json(
            blob + sizeof(header), header.length
        );
    free(blob);
    return valid;
}

void standalone_profile_set_fault_hook(
    standalone_profile_fault_hook_t hook
) {
    s_fault_hook = hook;
}

bool standalone_profile_load_runtime(void) {
    nvs_handle_t nvs;
    uint8_t active_slot = 0;
    if (nvs_open(STANDALONE_NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK)
        return false;
    nvs_get_u8(nvs, "active", &active_slot);
    active_slot = active_slot == 1 ? 1 : 0;
    bool loaded = apply_slot(nvs, active_slot);
    bool used_fallback = false;
    if (!loaded) {
        active_slot ^= 1u;
        loaded = apply_slot(nvs, active_slot);
        used_fallback = loaded;
    }
    nvs_close(nvs);
    if (used_fallback &&
        nvs_open(STANDALONE_NVS_NAMESPACE, NVS_READWRITE, &nvs) == ESP_OK) {
        if (nvs_set_u8(nvs, "active", active_slot) == ESP_OK)
            nvs_commit(nvs);
        nvs_close(nvs);
        ESP_LOGW(TAG, "recovered profile from alternate slot");
    }
    return loaded;
}

void standalone_profile_format_status(char *output, size_t size) {
    nvs_handle_t nvs;
    uint8_t active_slot = 0;
    standalone_profile_header_t header = {0};
    bool valid = false;
    if (nvs_open(STANDALONE_NVS_NAMESPACE, NVS_READONLY, &nvs) == ESP_OK) {
        nvs_get_u8(nvs, "active", &active_slot);
        active_slot = active_slot == 1 ? 1 : 0;
        valid = read_slot(nvs, active_slot, &header);
        nvs_close(nvs);
    }
    snprintf(
        output, size,
        "{\"cmd\":\"profile_status\",\"ok\":1,\"valid\":%d,"
        "\"slot\":\"%c\",\"schema\":%u,\"length\":%u,"
        "\"crc32\":\"%08lx\"}\n",
        valid ? 1 : 0, active_slot == 0 ? 'A' : 'B',
        valid ? (unsigned)header.schema : 0,
        valid ? (unsigned)header.length : 0,
        valid ? (unsigned long)header.crc32 : 0ul
    );
}

void standalone_profile_begin(
    char *arguments, char *output, size_t size
) {
    char *save = NULL;
    char *schema_text = strtok_r(arguments, " ", &save);
    char *length_text = strtok_r(NULL, " ", &save);
    char *crc_text = strtok_r(NULL, " ", &save);
    if (!schema_text || !length_text || !crc_text) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_begin\",\"ok\":0,"
            "\"error\":\"arguments\"}\n"
        );
        return;
    }
    unsigned long schema = strtoul(schema_text, NULL, 10);
    unsigned long length = strtoul(length_text, NULL, 10);
    unsigned long crc = strtoul(crc_text, NULL, 16);
    if (schema != STANDALONE_PROFILE_SCHEMA) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_begin\",\"ok\":0,\"error\":\"schema\"}\n"
        );
        return;
    }
    if (length == 0 || length > STANDALONE_PROFILE_MAX) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_begin\",\"ok\":0,\"error\":\"length\"}\n"
        );
        return;
    }
    reset_staging();
    s_staging = malloc(length);
    if (!s_staging) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_begin\",\"ok\":0,\"error\":\"memory\"}\n"
        );
        return;
    }
    s_schema = (uint16_t)schema;
    s_expected = (size_t)length;
    s_crc32 = (uint32_t)crc;
    snprintf(output, size, "{\"cmd\":\"profile_begin\",\"ok\":1}\n");
}

void standalone_profile_chunk(
    char *arguments, char *output, size_t size
) {
    char *save = NULL;
    char *offset_text = strtok_r(arguments, " ", &save);
    char *hex_text = strtok_r(NULL, " ", &save);
    if (!offset_text || !hex_text || !s_staging) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_chunk\",\"ok\":0,\"error\":\"state\"}\n"
        );
        return;
    }
    size_t offset = (size_t)strtoul(offset_text, NULL, 10);
    if (offset != s_received) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_chunk\",\"ok\":0,\"error\":\"offset\"}\n"
        );
        return;
    }
    size_t remaining = s_expected - s_received;
    size_t received = parse_hex(
        hex_text, s_staging + s_received, remaining
    );
    if (received == 0 || strlen(hex_text) != received * 2) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_chunk\",\"ok\":0,\"error\":\"data\"}\n"
        );
        return;
    }
    s_received += received;
    snprintf(
        output, size,
        "{\"cmd\":\"profile_chunk\",\"ok\":1,\"received\":%u}\n",
        (unsigned)s_received
    );
}

void standalone_profile_commit(char *output, size_t size) {
    if (!s_staging || s_received != s_expected) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_commit\",\"ok\":0,"
            "\"error\":\"incomplete\"}\n"
        );
        return;
    }
    if (profile_crc32(s_staging, s_expected) != s_crc32) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_commit\",\"ok\":0,\"error\":\"crc\"}\n"
        );
        reset_staging();
        return;
    }
    if (!standalone_xinput_validate_profile_json(s_staging, s_expected)) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_commit\",\"ok\":0,"
            "\"error\":\"runtime_parse\"}\n"
        );
        reset_staging();
        return;
    }

    size_t blob_size = sizeof(standalone_profile_header_t) + s_expected;
    uint8_t *blob = malloc(blob_size);
    if (!blob) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_commit\",\"ok\":0,\"error\":\"memory\"}\n"
        );
        return;
    }
    standalone_profile_header_t header = {
        .magic = STANDALONE_PROFILE_MAGIC,
        .schema = s_schema,
        .reserved = 0,
        .length = (uint32_t)s_expected,
        .crc32 = s_crc32,
    };
    memcpy(blob, &header, sizeof(header));
    memcpy(blob + sizeof(header), s_staging, s_expected);

    nvs_handle_t nvs;
    esp_err_t error = nvs_open(
        STANDALONE_NVS_NAMESPACE, NVS_READWRITE, &nvs
    );
    uint8_t active_slot = 0;
    uint8_t target_slot = 1;
    if (error == ESP_OK) {
        nvs_get_u8(nvs, "active", &active_slot);
        active_slot = active_slot == 1 ? 1 : 0;
        target_slot = active_slot ^ 1u;
        const char *key = target_slot == 0 ? "slot_a" : "slot_b";
        if (inject_fault(STANDALONE_PROFILE_FAULT_BEFORE_SLOT_WRITE))
            error = ESP_FAIL;
        if (error == ESP_OK) error = nvs_set_blob(nvs, key, blob, blob_size);
        if (error == ESP_OK &&
            inject_fault(STANDALONE_PROFILE_FAULT_AFTER_SLOT_WRITE))
            error = ESP_FAIL;
        if (error == ESP_OK) error = nvs_commit(nvs);
        if (error == ESP_OK &&
            inject_fault(STANDALONE_PROFILE_FAULT_AFTER_SLOT_COMMIT))
            error = ESP_FAIL;
        standalone_profile_header_t verify = {0};
        if (error == ESP_OK && !read_slot(nvs, target_slot, &verify))
            error = ESP_ERR_INVALID_CRC;
        if (error == ESP_OK)
            error = nvs_set_u8(nvs, "active", target_slot);
        if (error == ESP_OK &&
            inject_fault(STANDALONE_PROFILE_FAULT_AFTER_ACTIVE_WRITE))
            error = ESP_FAIL;
        if (error == ESP_OK) error = nvs_commit(nvs);
        if (error == ESP_OK &&
            inject_fault(STANDALONE_PROFILE_FAULT_AFTER_ACTIVE_COMMIT))
            error = ESP_FAIL;
        nvs_close(nvs);
    }
    free(blob);
    if (error != ESP_OK) {
        snprintf(
            output, size,
            "{\"cmd\":\"profile_commit\",\"ok\":0,\"error\":\"nvs_%s\"}\n",
            esp_err_to_name(error)
        );
        return;
    }

    uint32_t committed_crc = s_crc32;
    size_t committed_length = s_expected;
    bool applied = standalone_xinput_apply_profile_json(
        s_staging, s_expected
    );
    reset_staging();
    if (!applied) {
        if (nvs_open(
                STANDALONE_NVS_NAMESPACE, NVS_READWRITE, &nvs
            ) == ESP_OK) {
            nvs_set_u8(nvs, "active", active_slot);
            nvs_commit(nvs);
            nvs_close(nvs);
        }
        snprintf(
            output, size,
            "{\"cmd\":\"profile_commit\",\"ok\":0,"
            "\"error\":\"runtime_parse\"}\n"
        );
        return;
    }
    snprintf(
        output, size,
        "{\"cmd\":\"profile_commit\",\"ok\":1,\"slot\":\"%c\","
        "\"length\":%u,\"crc32\":\"%08lx\",\"runtime_applied\":1}\n",
        target_slot == 0 ? 'A' : 'B', (unsigned)committed_length,
        (unsigned long)committed_crc
    );
}

void standalone_profile_abort(char *output, size_t size) {
    reset_staging();
    snprintf(output, size, "{\"cmd\":\"profile_abort\",\"ok\":1}\n");
}
