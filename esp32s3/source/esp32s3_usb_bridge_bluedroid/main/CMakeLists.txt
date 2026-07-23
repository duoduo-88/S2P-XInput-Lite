idf_component_register(
                    SRCS "main.c" "standalone_xinput.c"
                         "fusion/FusionAhrs.c"
                    INCLUDE_DIRS "." "fusion"
                    REQUIRES esp_tinyusb nvs_flash bt json)
