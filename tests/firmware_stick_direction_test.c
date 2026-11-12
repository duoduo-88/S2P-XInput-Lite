#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define M_PI 3.14159265358979323846

typedef enum { OBJECT, ARRAY, NUMBER, STRING } json_type;
typedef struct cJSON {
    json_type type;
    const char *key;
    const char *valuestring;
    double valuedouble;
    const struct cJSON *children;
    int count;
} cJSON;

#define cJSON_IsObject(item) ((item) && (item)->type == OBJECT)
#define cJSON_IsArray(item) ((item) && (item)->type == ARRAY)
#define cJSON_IsNumber(item) ((item) && (item)->type == NUMBER)
#define cJSON_IsString(item) ((item) && (item)->type == STRING)
#define cJSON_GetArraySize(item) ((item)->count)
#define cJSON_GetArrayItem(item, index) (&(item)->children[index])

static const cJSON *cJSON_GetObjectItemCaseSensitive(const cJSON *item, const char *key) {
    for (int index = 0; index < item->count; index++) {
        if (strcmp(item->children[index].key, key) == 0)
            return &item->children[index];
    }
    return NULL;
}

#define SOURCE_BUTTON_COUNT 21
#include "firmware_stick_direction_types.inc"

/* Extracted mapping selection uses the active profile and its layer state. */
static struct {
    stick_direction_runtime_t directions[2];
    mapping_layer_runtime_t layers[1];
    uint8_t layer_count;
    int8_t toggle_layer;
    int8_t active_layer;
    uint32_t previous_source_buttons;
} s_runtime = {.layer_count = 1, .toggle_layer = -1, .active_layer = -1};

static uint32_t target_from_name(const char *name) {
    (void)name;
    return 0;
}

#include "firmware_stick_direction.inc"

static uint32_t direction(stick_direction_runtime_t *config, float degrees, float magnitude) {
    const float radians = degrees * ((float)M_PI / 180.0f);
    uint8_t trigger = 0;
    return apply_stick_direction(
        config, NULL, 0, 0, cosf(radians) * magnitude, sinf(radians) * magnitude,
        &trigger, &trigger
    );
}

int main(void) {
    enum { UP = 1, UP_RIGHT = 2, RIGHT = 4, DOWN = 8, DOWN_RIGHT = 16 };
    stick_direction_runtime_t config = {
        .trigger_threshold = 0.6f,
        .release_threshold = 0.4f,
        .direction_deadzone = 5.0f,
        .targets = {UP, UP_RIGHT, RIGHT, DOWN_RIGHT, DOWN},
    };
    const cJSON shifted[] = {{.type = NUMBER, .valuedouble = 51}, {.type = NUMBER, .valuedouble = 69}};
    const cJSON narrow[] = {{.type = NUMBER, .valuedouble = 45}, {.type = NUMBER, .valuedouble = 45}};
    const cJSON zones_4way[] = {
        {.type = ARRAY, .children = shifted, .count = 2},
        {.type = ARRAY, .children = narrow, .count = 2},
        {.type = ARRAY, .children = narrow, .count = 2},
        {.type = ARRAY, .children = narrow, .count = 2},
    };
    cJSON fields[] = {
        {.type = STRING, .key = "mode", .valuestring = "4WAY"},
        {.type = ARRAY, .key = "direction_deadzone_edges", .children = zones_4way, .count = 4},
    };
    cJSON section = {.type = OBJECT, .children = fields, .count = 2};

    assert(parse_direction_config(&section, NULL, &config));
    assert(direction(&config, 60, 1) == 0);
    assert(direction(&config, 48, 1) == RIGHT);
    assert(direction(&config, 60, 1) == RIGHT);
    assert(direction(&config, 70, 1) == UP);
    assert(direction(&config, 60, 1) == UP);
    assert(direction(&config, 60, 0.1f) == 0);
    assert(direction(&config, 48, 0.5f) == 0);
    assert(direction(&config, 48, 0.8f) == RIGHT);
    assert(direction(&config, 90, 0.5f) == UP);

    mapping_layer_runtime_t *layer = &s_runtime.layers[0];
    layer->activation_mask = 1;
    layer->specificity = 1;
    layer->directions[0] = config;
    assert(select_mapping_layer(1, false) == 0);
    assert(direction(&layer->directions[0], 48, 1) == RIGHT);
    assert(select_mapping_layer(1, false) == 0);
    assert(direction(&layer->directions[0], 60, 1) == RIGHT);
    assert(select_mapping_layer(0, false) == -1);
    assert(select_mapping_layer(1, false) == 0);
    assert(direction(&layer->directions[0], 60, 1) == 0);
    assert(direction(&layer->directions[0], 48, 1) == RIGHT);
    assert(select_mapping_layer(1, true) == 0);
    assert(direction(&layer->directions[0], 60, 1) == 0);

    /* At exactly 315 degrees, a zero-width gap selects RIGHT from neutral.
     * USB uses different scales for positive and negative axes. */
    stick_runtime_config_t stick = {
        .x = {0, 0.25f, 0.5f, 0.75f, 1},
        .y = {0, 0.25f, 0.5f, 0.75f, 1},
        .center_x = 2048, .center_y = 2048,
        .max_x = 2048, .max_y = 2048, .min_x = 2048, .min_y = 2048,
    };
    uint8_t trigger = 0;
    assert(parse_direction_config(&section, NULL, &config));
    processed_stick_t sample = process_stick(3298, 798, 8, &stick);
    assert(apply_processed_stick_direction(
        &config, &stick, 3298, 798, &sample, &trigger, &trigger
    ) == RIGHT);

    /* A calibrated raw travel of 1200/2000 is exactly the trigger threshold. */
    assert(parse_direction_config(&section, NULL, &config));
    stick.max_x = 2000;
    sample = process_stick(3248, 2048, 16, &stick);
    assert(apply_processed_stick_direction(
        &config, &stick, 3248, 2048, &sample, &trigger, &trigger
    ) == RIGHT);

    const cJSON wrap[] = {{.type = NUMBER, .valuedouble = 30}, {.type = NUMBER, .valuedouble = 40}};
    cJSON zones_8way[8];
    for (int index = 0; index < 8; index++)
        zones_8way[index] = (cJSON){.type = ARRAY, .children = wrap, .count = 2};
    fields[0].valuestring = "8WAY";
    fields[1].children = zones_8way;
    fields[1].count = 8;
    assert(parse_direction_config(&section, NULL, &config));
    assert(direction(&config, 350, 1) == 0);
    assert(direction(&config, 340, 1) == DOWN_RIGHT);
    assert(direction(&config, 350, 1) == DOWN_RIGHT);
    assert(direction(&config, 359, 1) == RIGHT);
    assert(direction(&config, 0, 1) == RIGHT);

    section.count = 1;
    assert(parse_direction_config(&section, NULL, &config));
    assert(direction(&config, 350, 1) == RIGHT);

    section.count = 2;
    fields[1].count = 4;
    assert(!parse_direction_config(&section, NULL, &config));
    puts("all scenarios passed");
    return 0;
}
