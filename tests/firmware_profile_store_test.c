#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "standalone_profile_store.h"

#define ESP_LOGW(tag, message) ((void)(tag), (void)(message))

typedef int esp_err_t;
typedef int nvs_handle_t;
typedef struct { size_t available_entries; } nvs_stats_t;

enum {
    ESP_OK, ESP_FAIL, ESP_ERR_INVALID_CRC,
    ESP_ERR_NVS_NOT_ENOUGH_SPACE, ESP_ERR_NVS_NOT_FOUND, NVS_READWRITE, NVS_READONLY,
    NVS_ENTRY_SIZE = 32, NVS_AVAILABLE_ENTRIES = 5 * 126 - 4,
};

static uint8_t s_slots[2][STANDALONE_PROFILE_MAX + 16];
static size_t s_sizes[2];
static uint8_t s_active_slot;
static uint8_t s_applied, s_invalid_profile;
static int s_erases;
static uint8_t *s_staging;
static size_t s_expected, s_received;
static uint16_t s_schema;
static uint32_t s_crc32;
static standalone_profile_fault_hook_t s_fault_hook;

static uint8_t slot_for_key(const char *key) {
    assert(!strcmp(key, "slot_a") || !strcmp(key, "slot_b"));
    return (uint8_t)(strcmp(key, "slot_b") == 0);
}

static size_t blob_entries(size_t length) {
    return length ? (length + NVS_ENTRY_SIZE - 1) / NVS_ENTRY_SIZE + 3 : 0;
}

static esp_err_t nvs_get_stats(const char *name, nvs_stats_t *stats) {
    assert(!name);
    stats->available_entries = NVS_AVAILABLE_ENTRIES -
        blob_entries(s_sizes[0]) - blob_entries(s_sizes[1]);
    return ESP_OK;
}

static esp_err_t nvs_open(const char *name, int mode, nvs_handle_t *nvs) {
    assert(strcmp(name, "s2p_profile") == 0);
    assert(mode == NVS_READWRITE || mode == NVS_READONLY);
    *nvs = 1;
    return ESP_OK;
}

static void nvs_close(nvs_handle_t nvs) { assert(nvs == 1); }
static esp_err_t nvs_commit(nvs_handle_t nvs) { assert(nvs == 1); return ESP_OK; }

static esp_err_t nvs_get_u8(nvs_handle_t nvs, const char *key, uint8_t *value) {
    assert(nvs == 1 && strcmp(key, "active") == 0);
    *value = s_active_slot;
    return ESP_OK;
}

static esp_err_t nvs_set_u8(nvs_handle_t nvs, const char *key, uint8_t value) {
    assert(nvs == 1 && strcmp(key, "active") == 0 && value < 2);
    s_active_slot = value;
    return ESP_OK;
}

static esp_err_t nvs_get_blob(nvs_handle_t nvs, const char *key, void *data, size_t *length) {
    assert(nvs == 1);
    uint8_t slot = slot_for_key(key);
    if (!s_sizes[slot]) return ESP_ERR_NVS_NOT_FOUND;
    if (data) {
        assert(*length >= s_sizes[slot]);
        memcpy(data, s_slots[slot], s_sizes[slot]);
    }
    *length = s_sizes[slot];
    return ESP_OK;
}

static esp_err_t nvs_erase_key(nvs_handle_t nvs, const char *key) {
    uint8_t slot = slot_for_key(key);
    assert(nvs == 1 && slot != s_active_slot);
    if (!s_sizes[slot]) return ESP_ERR_NVS_NOT_FOUND;
    s_erases++;
    s_sizes[slot] = 0;
    return ESP_OK;
}

static esp_err_t nvs_set_blob(nvs_handle_t nvs, const char *key, const void *data, size_t length) {
    assert(nvs == 1 && length <= sizeof(s_slots[0]));
    nvs_stats_t stats;
    nvs_get_stats(NULL, &stats);
    /* ESP-IDF allocates the replacement before discarding the previous blob. */
    if (blob_entries(length) > stats.available_entries) return ESP_ERR_NVS_NOT_ENOUGH_SPACE;
    uint8_t slot = slot_for_key(key);
    memcpy(s_slots[slot], data, length);
    s_sizes[slot] = length;
    return ESP_OK;
}

static const char *esp_err_to_name(esp_err_t error) {
    return error == ESP_ERR_NVS_NOT_ENOUGH_SPACE ? "not_enough_space" : "injected";
}

static bool standalone_xinput_validate_profile_json(const uint8_t *json, size_t length) {
    return length > 0 && json[0] != s_invalid_profile;
}

static bool standalone_xinput_apply_profile_json(const uint8_t *json, size_t length) {
    if (!standalone_xinput_validate_profile_json(json, length)) return false;
    s_applied = json[0];
    return true;
}

#include "firmware_profile_store.inc"

static void commit_profile(uint8_t id, char *response, size_t capacity) {
    reset_staging();
    s_expected = s_received = 7000;
    s_schema = STANDALONE_PROFILE_SCHEMA;
    s_staging = malloc(s_expected);
    assert(s_staging);
    memset(s_staging, id, s_expected);
    s_crc32 = profile_crc32(s_staging, s_expected);
    standalone_profile_commit(response, capacity);
}

static bool fail_after_slot_write(standalone_profile_fault_stage_t stage) {
    return stage == STANDALONE_PROFILE_FAULT_AFTER_SLOT_WRITE;
}

static bool fail_after_active_commit(standalone_profile_fault_stage_t stage) {
    return stage == STANDALONE_PROFILE_FAULT_AFTER_ACTIVE_COMMIT;
}

int main(void) {
    char response[256];
    for (uint8_t id = 1; id <= 3; id++) {
        commit_profile(id, response, sizeof(response));
        assert(strstr(response, "\"ok\":1") && s_applied == id);
    }
    assert(s_erases == 1 && s_active_slot == 1);

    s_invalid_profile = 3;
    commit_profile(4, response, sizeof(response));
    assert(strstr(response, "not_enough_space") && s_erases == 1);
    assert(s_active_slot == 1 && s_sizes[0] > 0 && s_applied == 3);

    s_invalid_profile = 0;
    s_fault_hook = fail_after_slot_write;
    commit_profile(4, response, sizeof(response));
    assert(strstr(response, "\"ok\":0") && s_erases == 2);
    assert(s_active_slot == 1 && s_applied == 3);
    assert(s_slots[s_active_slot][sizeof(standalone_profile_header_t)] == 3);

    s_fault_hook = NULL;
    commit_profile(4, response, sizeof(response));
    assert(strstr(response, "\"ok\":1") && s_active_slot == 0 && s_applied == 4);

    s_fault_hook = fail_after_active_commit;
    commit_profile(5, response, sizeof(response));
    assert(strstr(response, "\"ok\":0") && s_active_slot == 1 && s_applied == 4);
    standalone_profile_format_status(response, sizeof(response));
    assert(strstr(response, "\"valid\":1") && strstr(response, "\"runtime_applied\":0"));

    s_fault_hook = NULL;
    assert(standalone_profile_load_runtime() && s_applied == 5);
    standalone_profile_format_status(response, sizeof(response));
    assert(strstr(response, "\"valid\":1") && strstr(response, "\"runtime_applied\":1"));
    puts("all scenarios passed");
    return 0;
}
