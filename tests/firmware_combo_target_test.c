#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* Production TARGET_* defines and target parsers extracted from standalone_xinput.c. */
#include "firmware_combo_target.inc"

int main(void) {
    /* Single targets keep their existing bits. */
    assert(target_from_name("X") == 0x4000u);
    assert(target_from_name("LT") == TARGET_LT);
    assert(target_from_name("NONE") == 0);

    /* A combo ORs its members into one report contribution. */
    assert(target_from_name("COMBO:X+Y") == (0x4000u | 0x8000u));
    assert(target_from_name("COMBO:LB+RB+LT+RT") ==
           (0x0100u | 0x0200u | TARGET_LT | TARGET_RT));
    assert(target_from_name("COMBO:L_STK+GUIDE") == (0x0040u | 0x0400u));

    /* Anything the desktop rejects maps to nothing on the ESP32 as well. */
    const char *invalid[] = {
        "COMBO:", "COMBO:X", "COMBO:A+B+X+Y+LB", "COMBO:X+X", "COMBO:X+",
        "COMBO:+X", "COMBO:X++Y", "COMBO:X+NONE", "COMBO:X+L_STICK_UP",
        "COMBO:X+TOOLONGNAME",
    };
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) {
        if (target_from_name(invalid[i]) != 0) {
            printf("accepted invalid combo: %s\n", invalid[i]);
            return 1;
        }
    }
    puts("all scenarios passed");
    return 0;
}
