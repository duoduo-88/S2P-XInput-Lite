"""Run the production watchdog C code on the host; no ESP32 is connected."""
from pathlib import Path
import ast
import os
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "esp32s3/source/esp32s3_usb_bridge_bluedroid/main"


class BridgeFirmwareWatchdogTests(unittest.TestCase):
    def compile_and_run(self, source, name):
        output = ROOT / "build/bridge-host-tests"
        output.mkdir(parents=True, exist_ok=True)
        exe = output / (name + (".exe" if os.name == "nt" else ""))
        if os.name == "nt":
            vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
            if not vswhere.is_file():
                self.skipTest("Visual Studio C compiler is not installed")
            installation = subprocess.check_output([
                str(vswhere), "-latest", "-products", "*", "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property", "installationPath",
            ], text=True).strip()
            if not installation:
                self.skipTest("Visual Studio C compiler is not installed")
            vcvars = Path(installation) / "VC/Auxiliary/Build/vcvars64.bat"
            command = f'call "{vcvars}" >nul && cl /nologo /std:c11 /utf-8 /D_CRT_SECURE_NO_WARNINGS /W4 /WX /I"{MAIN}" /I"{output}" "{source}" /Fe:"{exe}" /Fo:"{output / (name + ".obj")}"'
            batch = output / (name + ".cmd")
            batch.write_text("@echo off\n" + command + "\n", encoding="utf-8")
            compiled = subprocess.run(["cmd", "/d", "/c", str(batch)],
                                      cwd=output, capture_output=True, text=True, errors="replace", timeout=60)
        else:
            compiler = shutil.which("cc")
            if not compiler:
                self.skipTest("Host C compiler is not installed")
            compiled = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                                       "-I", str(MAIN), "-I", str(output), str(source), "-o", str(exe)],
                                      capture_output=True, text=True, timeout=60)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("all scenarios passed", result.stdout)

    def test_watchdog_state_transitions_in_production_c(self):
        self.assertTrue((MAIN / "bridge_host_watchdog.h").is_file(),
                        "Bridge host watchdog is missing")
        self.compile_and_run(ROOT / "tests/bridge_host_watchdog_test.c", "watchdog")

    def test_production_disconnect_queue_and_mode_integration(self):
        source = (MAIN / "main.c").read_text(encoding="utf-8")
        functions = []
        # Compile the actual lifecycle code, not a Python model of the firmware.
        for name in ("bridge_host_mode", "bridge_host_blocks_connection",
                     "enqueue_disc_locked", "finish_disc_event", "kick_disc_queue", "request_scan_start",
                     "do_disc_all", "stop_bridge_host", "handle_bridge_host_command",
                     "pump_bridge_host_watchdog", "pump_connection_watchdogs"):
            match = re.search(r"^static (?:void|bool) " + name + r"\([^;]*?\)\s*\{", source, re.M)
            self.assertIsNotNone(match, f"Missing production function: {name}")
            start = match.start()
            pos = match.end()
            depth = 1
            # Mask comments/strings so JSON braces don't affect function boundaries.
            masked = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                            lambda m: " " * len(m.group()), source, flags=re.S)
            while depth:
                depth += (masked[pos] == "{") - (masked[pos] == "}")
                pos += 1
            functions.append(source[start:pos])
        output = ROOT / "build/bridge-host-tests"
        output.mkdir(parents=True, exist_ok=True)
        (output / "bridge_host_functions.inc").write_text("\n".join(functions), encoding="utf-8")
        self.compile_and_run(ROOT / "tests/bridge_host_integration_test.c", "integration")

    def test_integration_strictly_gates_configured_and_active_mode(self):
        source = (MAIN / "main.c").read_text(encoding="utf-8")
        self.assertIn("s_output_mode == STANDALONE_OUTPUT_BRIDGE &&", source)
        self.assertIn("s_active_output_mode == STANDALONE_OUTPUT_BRIDGE", source)
        self.assertIn("pump_bridge_host_watchdog();", source)
        self.assertIn("finish_disc_event(param->disconnect.remote_bda,", source)
        response = source.split("static void send_status_response(void)", 1)[1].split("APP_FIRMWARE_PRODUCT", 1)[0]
        template = "".join(ast.literal_eval(item) for item in re.findall(r'"(?:\\.|[^"\\])*"', response))
        status = template % ("S2P-FW", "1.0.7", "s2p_bridge", "1.0.0", "s2p_usb_bridge",
                             "standalone_diagnostics", "standalone_hid", "standalone_hid",
                             "standalone_auto", "S2P-FFFFFFFFFFFF", 255,
                             "FF:FF:FF:FF:FF:FF", 4294967295, 1)
        capacity = int(re.search(r"#define CDC_TX_BUFFER_SIZE\s+(\d+)", source).group(1))
        self.assertLess(len(status.encode("utf-8")), capacity, "Status must fit CDC TX and snprintf buffers")


if __name__ == "__main__":
    unittest.main()
