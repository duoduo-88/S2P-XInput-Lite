"""Execute actual production mode functions against a transactional fake NVS."""
from pathlib import Path
import json
import re
import unittest
import test_bridge_firmware_watchdog as host_tests

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'esp32s3/source/esp32s3_usb_bridge_bluedroid/main'


def extract_function(source, name):
    match = re.search(r'^(?:static )?[\w *]+\b' + name + r'\([^;]*?\)\s*\{', source, re.M)
    if not match:
        raise AssertionError(f'Missing production function: {name}')
    masked = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                    lambda m: ' ' * len(m.group()), source, flags=re.S)
    pos, depth = match.end(), 1
    while depth:
        depth += (masked[pos] == '{') - (masked[pos] == '}')
        pos += 1
    return source[match.start():pos]


class FirmwareUsbModesTests(unittest.TestCase):
    def test_actual_mode_migration_and_failure_recovery(self):
        source = (MAIN / 'standalone_xinput.c').read_text(encoding='utf-8')
        functions = [extract_function(source, name) for name in (
            'standalone_usb_serial', 'standalone_output_mode_migration', 'standalone_output_mode_load', 'standalone_output_mode_store',
            'mode_chord_target', 'mode_chord_mask', 'filter_mode_chord',
            'process_mode_switch_requests', 'hid_axis_y', 'hid_hat', 'encode_hid_report')]
        header = (MAIN / 'standalone_xinput.h').read_text(encoding='utf-8')
        mode_enum = re.search(r'typedef enum \{.*?\} standalone_output_mode_t;', header, re.S).group()
        report_type = re.search(r'typedef struct __attribute__.*?\} xinput_report_t;', source, re.S).group()
        output = ROOT / 'build/bridge-host-tests'
        output.mkdir(parents=True, exist_ok=True)
        (output / 'firmware_usb_types.inc').write_text(
            mode_enum + '\n' + report_type + '\n' + re.search(
                r'^#define STANDALONE_USB_HID_REPORT_SIZE[^\n]*', header, re.M
            ).group(), encoding='utf-8')
        defines = re.findall(r'^#define (?:STANDALONE_|MODE_CHORD_)\w+(?:[^\n]*)', source, re.M)
        output = ROOT / 'build/bridge-host-tests'
        output.mkdir(parents=True, exist_ok=True)
        (output / 'firmware_usb_mode_functions.inc').write_text(
            '\n'.join(defines + ['static uint8_t s_mode_migration;'] + re.findall(
                r'^static (?:bool|int|uint32_t|int64_t|standalone_output_mode_t) s_(?:mode_chord_\w+|mode_switch_\w+|configured_output_mode)(?: = -1)?;', source, re.M) + functions), encoding='utf-8')
        main_source = (MAIN / 'main.c').read_text(encoding='utf-8')
        protocol_functions = [extract_function(main_source, name) for name in (
            'output_mode_name', 'send_status_response', 'send_capabilities_response',
            'do_mode_command')]
        protocol_defines = re.findall(r'^#define (?:APP_|EXPECTED_FIRMWARE_)\w+[^\n]*', main_source, re.M)
        (output / 'firmware_usb_protocol_functions.inc').write_text(
            '\n'.join(protocol_defines + protocol_functions), encoding='utf-8')
        host_tests.BridgeFirmwareWatchdogTests.compile_and_run(
            self, ROOT / 'tests/firmware_usb_modes_test.c', 'usb_modes')
        capacity = int(re.search(r'^#define CDC_TX_BUFFER_SIZE\s+(\d+)', main_source, re.M).group(1))
        for name in ('status', 'capabilities'):
            raw = (output / f'usb_{name}.json').read_text(encoding='utf-8')
            self.assertLess(len(raw.encode('utf-8')), capacity)
            response = json.loads(raw)
            self.assertEqual(response['usb_serial'], 'S2P-102A3B4C5DEF')
            self.assertEqual(response['mode_migration'], 'standalone_auto')
            self.assertEqual(response['mode'], 'standalone_hid')
            self.assertEqual(response['active_mode'], 'standalone_hid')
            self.assertEqual(response['features']['standalone_usb_xinput'], 0)
            self.assertEqual(response['features']['standalone_usb_auto'], 0)
            self.assertEqual(response['features']['standalone_usb_hid'], 1)

    def test_raw_recovery_survives_stalled_usb_without_consuming_edges(self):
        # Generate the same NVS/type stubs used by the production mode harness.
        self.test_actual_mode_migration_and_failure_recovery()
        source = (MAIN / 'standalone_xinput.c').read_text(encoding='utf-8')
        main = (MAIN / 'main.c').read_text(encoding='utf-8')
        runtime = [extract_function(source, name) for name in (
            'standalone_xinput_observe_mode_chord', 'standalone_xinput_forget_channel',
            'standalone_xinput_can_accept_input', 'standalone_xinput_pump')]
        ingress = [extract_function(main, name) for name in (
            'input_digital_signature', 'queue_input_state', 'take_input_edge',
            'take_latest_input_if_no_edges')]
        if 'static void pump_standalone_mode_chords(void)' in main:
            ingress.append(extract_function(main, 'pump_standalone_mode_chords'))
        maintenance = main.split('static void cdc_task(void *arg)', 1)[1].split(
            'pump_standalone_pairing();', 1)[0].split('for (;;) {', 1)[1]
        output = ROOT / 'build/bridge-host-tests'
        (output / 'firmware_usb_recovery_functions.inc').write_text(
            '\n'.join(runtime + ingress) + '\nstatic void maintenance(void) {\n' +
            maintenance + '\n}\nstatic void bridge_forward(int channel) {\n'
            'in_report_t edge; if (take_input_edge(&edge)) {\n' + re.search(
                r'standalone_xinput_observe_mode_chord\(edge\.ch, edge\.data, edge\.len\);',
                main).group() + '\n} else { in_report_t r; '
            'if (take_latest_input_if_no_edges(channel,&r)) {\n' + re.search(
                r'standalone_xinput_observe_mode_chord\(r\.ch, r\.data, r\.len\);',
                main).group() + '\n}\n}\n}\n', encoding='utf-8')
        host_tests.BridgeFirmwareWatchdogTests.compile_and_run(
            self, ROOT / 'tests/firmware_usb_recovery_test.c', 'usb_recovery')


if __name__ == '__main__':
    unittest.main()
