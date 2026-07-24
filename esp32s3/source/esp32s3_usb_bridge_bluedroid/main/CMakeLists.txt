idf_component_register(
                    SRCS "main.c" "ble_callback_metrics.c"
                         "standalone_profile_store.c"
                         "standalone_xinput.c"
                         "fusion/FusionAhrs.c"
                    INCLUDE_DIRS "." "fusion"
                    REQUIRES esp_tinyusb nvs_flash bt json)
