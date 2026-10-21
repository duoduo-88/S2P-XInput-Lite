#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define STANDALONE_PROFILE_SCHEMA 1
#define STANDALONE_PROFILE_MAX 8192

typedef enum {
    STANDALONE_PROFILE_FAULT_BEFORE_SLOT_WRITE,
    STANDALONE_PROFILE_FAULT_AFTER_SLOT_WRITE,
    STANDALONE_PROFILE_FAULT_AFTER_SLOT_COMMIT,
    STANDALONE_PROFILE_FAULT_AFTER_ACTIVE_WRITE,
    STANDALONE_PROFILE_FAULT_AFTER_ACTIVE_COMMIT,
} standalone_profile_fault_stage_t;

typedef bool (*standalone_profile_fault_hook_t)(
    standalone_profile_fault_stage_t stage
);

void standalone_profile_set_fault_hook(
    standalone_profile_fault_hook_t hook
);
bool standalone_profile_load_runtime(void);
void standalone_profile_format_status(char *output, size_t size);
void standalone_profile_begin(
    char *arguments, char *output, size_t size
);
void standalone_profile_chunk(
    char *arguments, char *output, size_t size
);
void standalone_profile_commit(char *output, size_t size);
void standalone_profile_abort(char *output, size_t size);
