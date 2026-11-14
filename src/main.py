import configparser
import ctypes
import gc
import os
import sys
import time
import signal
from uuid import uuid4
from pathlib import Path
from esp32_bridge import ESP32Bridge
from esp32_detection import find_esp32_port
from bluetooth_controller import (
    BluetoothController
)
from wired_controller import WiredController, find_wired_controller
from switch2_input import battery_led_mask, SensorModeTracker, parse_input_report
from xinput_controller import CalibrationInProgressError, XInputController
from audio_haptics import AudioHaptics
from input_dispatcher import InputDispatcher
import serial
from version import APP_TITLE
from config_utils import (
    CONFIG_PATH,
    DEFAULT_CONFIG_PATH,
    atomic_write_config,
    calibration_section,
    config_file_lock,
    load_accelerometer_calibration,
    load_config,
    load_gyro_bias,
    load_magnetometer_bias,
    load_magnetometer_matrix,
    load_magnetometer_scale,
    load_stick_calibration,
    store_gyro_bias,
    store_accelerometer_calibration,
    store_magnetometer_calibration,
)
from runtime_commands import ControllerCommandInbox
from gyro_compensation import compensation_reload_signature
from runtime_status import ControllerStatusPublisher
from single_instance import SingleInstance
from test_telemetry import SharedTestTelemetry
from console_i18n import current_language, set_language_from_config
from console_i18n import localized_print as print
from console_i18n import localized_input as input
from hidhide_manager import reconcile_active_hidhide
from idle_disconnect import (
    IdleActivityTracker,
    load_idle_disconnect_minutes,
    perform_idle_disconnect,
)
from runtime_cleanup import (
    watch_parent_process_exit,
    close_xinput_after_dispatcher,
    controller_application_ready,
)
from runtime_logging import (
    get_async_log_metrics,
    install_async_stdio,
    shutdown_async_stdio,
    write_interactive_prompt,
)

HIDHIDE_APPLICATION_PATHS = (
    Path(sys.executable),
    Path(__file__).with_name("raw_hid_probe.exe"),
)


def tr(zh, en):
    return en if current_language() == "en" else zh


def load_runtime_stick_calibration(config, controller_id=None):
    section = calibration_section(controller_id)
    has_device_profiles = section != "sticks" and any(
        name.startswith("sticks.") for name in config.sections()
    )
    if not has_device_profiles or config.has_section(section):
        try:
            return load_stick_calibration(
                config, controller_id, fallback_to_legacy=not has_device_profiles
            )
        except (ValueError, configparser.Error) as exc:
            print(tr(
                f"搖桿校正資料無效，改用預設校正：{exc}",
                f"Invalid stick calibration. Using defaults: {exc}",
            ))
    # Once device profiles exist, [sticks] may contain another pad's last save.
    defaults = configparser.ConfigParser()
    defaults.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
    return load_stick_calibration(defaults)


def _enable_console_colors():
    """Enable ANSI colors in Windows CMD; retain symbol fallback elsewhere."""
    if not getattr(sys.stdout, "isatty", lambda: False)():
        return False
    if sys.platform != "win32":
        return True
    try:
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


_CONSOLE_COLORS = _enable_console_colors()
_COVERAGE_BLOCK_LINES = 0


def _print_orientation_coverage(title, orientation_bins):
    """Refresh one fixed multi-line 3x3x3 coverage block in place."""
    global _COVERAGE_BLOCK_LINES
    collected = {tuple(int(value) for value in item) for item in orientation_bins}
    total = 26
    percent = int(round(100.0 * len(collected) / total))

    plain_layers = []
    display_layers = []
    for z in (-1, 0, 1):
        plain_rows = []
        display_rows = []
        for y in (1, 0, -1):
            plain_cells = []
            display_cells = []
            for x in (-1, 0, 1):
                target = (x, y, z)
                if target == (0, 0, 0):
                    symbol = "·"
                    display = "\033[90m·\033[0m" if _CONSOLE_COLORS else symbol
                elif target in collected:
                    symbol = "■"
                    display = "\033[92m■\033[0m" if _CONSOLE_COLORS else symbol
                else:
                    symbol = "□"
                    display = "\033[90m□\033[0m" if _CONSOLE_COLORS else symbol
                plain_cells.append(symbol)
                display_cells.append(display)
            plain_rows.append(" ".join(plain_cells))
            display_rows.append(" ".join(display_cells))
        plain_layers.append(plain_rows)
        display_layers.append(display_rows)

    labels = ("Z-", "Z0", "Z+")
    plain_lines = [
        f"{title}: {len(collected)}/{total} ({percent}%)",
        "   ".join(label.center(5) for label in labels),
    ]
    display_lines = list(plain_lines)
    for row in range(3):
        plain_lines.append("   ".join(layer[row] for layer in plain_layers))
        display_lines.append("   ".join(layer[row] for layer in display_layers))

    if _COVERAGE_BLOCK_LINES:
        if not _CONSOLE_COLORS:
            # Redirected/legacy consoles cannot safely move the cursor upward.
            # Keep the first block instead of flooding the output with copies.
            return
        sys.stdout.write(f"\033[{_COVERAGE_BLOCK_LINES}A")

    for display_line in display_lines:
        if _CONSOLE_COLORS:
            sys.stdout.write("\r\033[2K")
        else:
            sys.stdout.write("\r")
        sys.stdout.write(display_line)
        sys.stdout.write("\n")
    sys.stdout.flush()
    _COVERAGE_BLOCK_LINES = len(plain_lines)


def _finish_orientation_coverage():
    global _COVERAGE_BLOCK_LINES
    # Rendering leaves the cursor immediately below the fixed block, ready for
    # normal completion/error messages without adding another blank line.
    _COVERAGE_BLOCK_LINES = 0

def main():
    instance = SingleInstance("S2P-XInput-Lite.Connector.v1")
    try:
        if not instance.is_primary:
            print(tr("連接程式已在執行。", "The connector is already running."))
            return 1
        if instance.error is not None:
            print(tr(f"無法確認連接程式的執行權：{instance.error}",
                     f"Could not acquire the connector's single-instance guard: {instance.error}"))
            return 1
        try:
            install_async_stdio()
            return _run_connector()
        finally:
            shutdown_async_stdio()
    finally:
        instance.close()


def _run_connector():
    process_started_at = time.time()
    stop_requested = False
    controller = None
    connection_mode = None
    gui_pid = os.environ.get("S2P_GUI_PID", "")
    gui_pid = int(gui_pid) if gui_pid.isdecimal() else 0

    def handle_shutdown_signal(_signum=None, _frame=None):
        nonlocal stop_requested
        stop_requested = True
        if connection_mode == "esp32" and controller is not None:
            controller.request_shutdown()

    def wait_for_error_acknowledgment():
        if stop_requested:
            return
        prompt = tr("\n按 Enter 鍵關閉...", "\nPress Enter to close...")
        if not gui_pid:
            input(prompt)
        elif sys.stdin is not None and sys.stdin.isatty():
            import msvcrt

            write_interactive_prompt(prompt)
            while not stop_requested:
                if msvcrt.kbhit() and msvcrt.getwch() in ("\r", "\n"):
                    break
                time.sleep(0.05)

    signal.signal(signal.SIGBREAK, handle_shutdown_signal)
    if gui_pid:
        watch_parent_process_exit(gui_pid, handle_shutdown_signal)
    if stop_requested:
        return

    command_inbox = ControllerCommandInbox()
    command_inbox.reset(process_started_at)
    status_publisher = ControllerStatusPublisher()
    publish_status = status_publisher.publish
    stage_status = status_publisher.stage
    publish_status()

    config = load_config(CONFIG_PATH, persist=False)
    set_language_from_config(config)

    print("========================================")
    print(f"         {APP_TITLE}")
    print("========================================")
    print()


    try:
        baudrate = config.getint(
            "serial",
            "baudrate",
            fallback=2_000_000,
        )
        if baudrate <= 0:
            raise ValueError("serial baudrate must be positive")
    except (ValueError, TypeError, configparser.Error) as exc:
        publish_status(state="stopped")
        print(tr(f"config.ini 的序列設定無效：{exc}", f"Invalid serial settings in config.ini: {exc}"))
        wait_for_error_acknowledgment()
        return

    hidhide_status = reconcile_active_hidhide(HIDHIDE_APPLICATION_PATHS)
    if hidhide_status.get("state") == "ready":
        print(tr(
            "HidHide 已隱藏實體 USB 手把。",
            "HidHide is hiding the physical USB controller.",
        ))
    elif hidhide_status.get("state") == "error":
        print(tr(
            f"HidHide 狀態異常：{hidhide_status.get('error')}",
            f"HidHide status error: {hidhide_status.get('error')}",
        ))

    print(tr("正在偵測 USB 有線手把...", "Detecting a wired USB controller..."))
    wired_entry = find_wired_controller()
    port = None

    if wired_entry is not None:
        print(tr(
            "已偵測到 USB 有線 Switch 2 Pro Controller。",
            "Wired USB Switch 2 Pro Controller detected.",
        ))
        print(tr(
            "提示：請先啟動本連線程式，確認手把連線後再開啟 Steam、reWASD 或其他手把工具。",
            "Tip: Start this connector first and confirm the controller is connected before opening Steam, reWASD, or other controller tools.",
        ))
        print(tr(
            "若其他工具已先開啟，請完全退出後重新插拔手把。",
            "If another tool was already open, fully exit it and reconnect the controller.",
        ))
        connection_mode = "wired"
    else:
        print(tr("正在自動偵測 ESP32...", "Detecting ESP32 automatically..."))
        port = find_esp32_port(baudrate)

    if connection_mode == "wired":
        pass
    elif port is not None:
        print(
            tr(f"已自動偵測到 ESP32：{port}", f"ESP32 detected automatically: {port}")
        )

        connection_mode = "esp32"

    else:
        print()
        print(tr(
            "未偵測到 ESP32，將使用 Windows 原生藍牙。",
            "ESP32 not detected. Using Windows native Bluetooth.",
        ))
        print()

        connection_mode = "bluetooth"
    if stop_requested:
        publish_status(state="stopped")
        return
    publish_status(state="searching", mode=connection_mode)
    audio_haptics = None
    test_telemetry = None
    try:
        with config_file_lock():
            # Detection may outlast a GUI save. Refresh before reading the
            # layer directory, while retaining the baud rate used to probe it.
            config = load_config(CONFIG_PATH)
            if config.getint("serial", "baudrate", fallback=2_000_000) != baudrate:
                raise ValueError(tr(
                    "偵測期間序列傳輸速率已變更，請重新啟動連線。",
                    "Serial baud rate changed during detection. Restart the connection.",
                ))
            xinput = XInputController(
                config,
                load_runtime_stick_calibration(config),
                layer_state_config_path=CONFIG_PATH,
            )
        set_language_from_config(config)
        try:
            test_telemetry = SharedTestTelemetry()
            xinput.set_test_telemetry(test_telemetry)
            xinput_slot = xinput.get_xinput_user_index()
            publish_status(xinput_slot=xinput_slot)
        except (AttributeError, OSError, TypeError, ValueError):
            test_telemetry = None

    except (ValueError, TypeError, KeyError, configparser.Error) as exc:
        publish_status(
            state="stopped",
            battery_percent=None,
            charging=False,
        )
        print()
        print(tr(f"config.ini 的控制器設定無效：{exc}", f"Invalid controller settings in config.ini: {exc}"))
        wait_for_error_acknowledgment()
        return

    except Exception as exc:
        publish_status(
            state="stopped",
            battery_percent=None,
            charging=False,
        )
        print()
        print(tr("無法建立 Xbox 虛擬控制器。", "Could not create the Xbox virtual controller."))
        print(tr("請確認 ViGEmBus 驅動程式已正確安裝。", "Make sure the ViGEmBus driver is installed correctly."))
        print()
        print(tr(f"錯誤資訊：{exc}", f"Error details: {exc}"))

        wait_for_error_acknowledgment()
        return

    try:
        if connection_mode == "wired":
            controller = WiredController(wired_entry)
            controller.set_audio_haptics_active(
                config.get("audio_haptics", "mode", fallback="GAME")
                .strip().upper() in ("AUDIO", "MIX")
            )
            xinput.set_rumble_sender(
                controller.send_pro_rumble,
                supports_priority=True,
            )
        elif connection_mode == "esp32":
            controller = ESP32Bridge(port, baudrate)
            xinput.set_rumble_sender(
                controller.send_pro_rumble_latest,
                supports_priority=True,
            )
        else:
            controller = BluetoothController()
            xinput.set_rumble_sender(controller.send_pro_rumble)
    except Exception as exc:
        xinput.close()
        publish_status(state="stopped")
        print(tr(f"控制器連線模組啟動失敗：{exc}", f"Controller connection module failed to start: {exc}"))
        wait_for_error_acknowledgment()
        return

    try:
        audio_haptics = AudioHaptics(config, xinput.set_audio_rumble)
        audio_haptics.start()
    except Exception as exc:
        try:
            xinput.close()
        finally:
            controller.close()
            publish_status(state="stopped")
        print(tr(f"音訊震動模組啟動失敗：{exc}", f"Audio haptics module failed to start: {exc}"))
        wait_for_error_acknowledgment()
        return

    cleanup_complete = False
    input_dispatcher = None
    gc_was_enabled = gc.isenabled()

    def set_input_gc_suppressed(active):
        """Keep cyclic GC off only while the high-rate input stream is live."""
        if active:
            if gc.isenabled():
                gc.disable()
        elif gc_was_enabled and not gc.isenabled():
            gc.enable()
            # Disconnect/cleanup is outside the latency-sensitive input path.
            gc.collect()

    def cleanup():
        """Release every runtime resource once, including startup failures."""
        nonlocal cleanup_complete
        if cleanup_complete:
            return
        cleanup_complete = True

        def close_transport():
            try:
                if controller.close() is False:
                    print(tr(
                        "控制器連線未能在期限內完整關閉；仍存活的 worker reference 已保留。",
                        "The controller transport did not fully stop before its "
                        "deadline; live worker references were retained.",
                    ))
            except Exception as exc:
                print(tr(f"控制器連線清理失敗：{exc}", f"Controller connection cleanup failed: {exc}"))

        bridge_transport = isinstance(controller, ESP32Bridge)
        if bridge_transport:
            controller.input_callback = None
            # close() first gates every bridge output producer and orders the
            # final zero under its send lock. Release BLE while the reader can
            # still acknowledge it, even if later audio/input cleanup blocks.
            close_transport()

        if audio_haptics is not None:
            try:
                audio_haptics.close()
            except Exception as exc:
                print(tr(f"音訊模組清理失敗：{exc}", f"Audio module cleanup failed: {exc}"))

        # First stop every producer that can still submit input or rumble.
        controller.input_callback = None
        set_input_gc_suppressed(False)

        xinput_stopped = False
        try:
            xinput_stopped = close_xinput_after_dispatcher(
                input_dispatcher, xinput,
            )
            if not xinput_stopped:
                print(tr(
                    "輸入處理執行緒未能停止；已保留虛擬控制器狀態，避免與仍在執行的 callback 競爭。",
                    "The input worker did not stop; virtual controller state "
                    "was retained to avoid racing an active callback.",
                ))
        except Exception as exc:
            print(tr(f"虛擬控制器清理失敗：{exc}", f"Virtual controller cleanup failed: {exc}"))

        if test_telemetry is not None and xinput_stopped:
            try:
                test_telemetry.close()
            except Exception:
                pass

        # Preserve native BLE and wired shutdown ordering.
        if not bridge_transport:
            close_transport()

        publish_status(
            state="stopped",
            battery_percent=None,
            battery_voltage=None,
            charging=False,
            wired_full_report=None,
            wired_polling_rate=None,
            wired_processing_rate=None,
            input_report_rate=None,
            xinput_output_rate=None,
            xinput_slot=None,
            sensor_mode=None,
            gyro_raw=None,
            accel_raw=None,
            gyro_bias_samples=0,
        )

    if stop_requested:
        cleanup()
        return

    last_status_stage = 0.0
    last_input_error = 0.0
    latest_battery_percent = None
    gyro_initialization_tracking = False
    gyro_initialization_announced = set()
    gyro_initialization_ready_announced = False
    gyro_initialization_complete_announced = False
    sensor_mode_tracker = SensorModeTracker()
    idle_tracker = IdleActivityTracker(
        load_idle_disconnect_minutes(config)
    )
    idle_disconnect_requested = False
    active_connection = None
    calibration_connections = {}

    def on_input(payload):
        nonlocal last_status_stage, last_input_error
        nonlocal latest_battery_percent
        # A transport producer can finish handing off a report during teardown.
        if stop_requested:
            return
        state = parse_input_report(
            payload
        )

        if state is not None:
            latest_battery_percent = state.battery_percent
            if connection_mode != "wired":
                idle_tracker.observe(state)
            sensor_mode = sensor_mode_tracker.update(state)
            try:
                xinput.update(state)
            except Exception as exc:
                # Never let one malformed mapping/driver update terminate the
                # BLE or serial reader thread. Clear held outputs and rate-limit
                # the diagnostic because input reports can arrive >100 Hz.
                try:
                    xinput.reset_output_state()
                except Exception:
                    pass
                now = time.monotonic()
                if now - last_input_error >= 1.0:
                    last_input_error = now
                    print(tr(f"輸入處理失敗：{exc}", f"Input processing failed: {exc}"))
                return
            now = time.monotonic()
            if now - last_status_stage >= 0.5:
                last_status_stage = now
                xinput.set_test_input_rate(
                    getattr(input_dispatcher, "input_rate_hz", None)
                )
                # GUI/status data is human-facing and does not need the input
                # report rate.  Stage it at 2 Hz to remove a lock, dict update,
                # list allocation and wall-clock query from every Fast Path.
                stage_status(
                    state="connected",
                    battery_percent=state.battery_percent,
                    battery_voltage=state.battery_voltage,
                    charging=state.charging,
                    charge_status_raw=state.charge_status_raw,
                    battery_current_raw=state.battery_current_raw,
                    wired_full_report=(
                        getattr(controller, "full_report", None)
                        if connection_mode == "wired" else None
                    ),
                    wired_polling_rate=(
                        getattr(controller, "polling_rate_hz", None)
                        if connection_mode == "wired" else None
                    ),
                    wired_processing_rate=(
                        getattr(input_dispatcher, "processing_rate_hz", None)
                        if connection_mode == "wired" else None
                    ),
                    input_report_rate=getattr(
                        input_dispatcher, "input_rate_hz", None
                    ),
                    xinput_output_rate=getattr(
                        input_dispatcher, "processing_rate_hz", None
                    ),
                    sensor_mode=sensor_mode,
                    gyro_raw=list(state.gyroscope),
                    accel_raw=list(state.accelerometer),
                    mag_raw=list(getattr(state, "magnetometer", (0, 0, 0))),
                    gyro_bias_samples=getattr(
                        xinput, "_gyro_bias_samples", 0
                    ),
                    report_time=getattr(state, "report_time", None),
                    report_delta=getattr(xinput, "_last_report_delta", None),
                    mag_field_valid=getattr(xinput, "_mag_field_valid", None),
                )

    def on_dispatch_error(exc):
        nonlocal last_input_error
        now = time.monotonic()
        if now - last_input_error >= 1.0:
            last_input_error = now
            print(tr(
                f"輸入派送失敗：{exc}",
                f"Input dispatch failed: {exc}",
            ))

    input_dispatcher = InputDispatcher(
        on_input,
        max_pending=3,
        error_callback=on_dispatch_error,
        # Every transport has a dedicated input producer, and each has passed
        # real-controller Inline validation.  Wired USB was additionally
        # stressed with 5 ms rumble submissions while reading at 250 Hz.  Any
        # overlap or ESP32 multi-report batch still falls back to the
        # edge-preserving latest-state worker.
        inline_fast_path=(connection_mode in ("esp32", "bluetooth", "wired")),
    )
    # Use the dispatcher object itself so ESP32Bridge can detect and call its
    # submit_batch() method when several CDC reports were buffered together.
    controller.input_callback = input_dispatcher

    def apply_motion_calibration(target, source_config, controller_id, *, announce=False):
        try:
            gyro_bias = load_gyro_bias(source_config, controller_id)
            target.set_gyro_bias(gyro_bias)
        except (ValueError, configparser.Error) as exc:
            target.set_gyro_bias(None)
            print(tr(
                f"陀螺儀校正資料無效，改用自動估算：{exc}",
                f"Invalid gyro calibration. Using automatic estimation: {exc}",
            ))
        else:
            if announce and gyro_bias is not None:
                print(tr(
                    f"已套用陀螺儀校正資料：{controller_id or 'legacy'}",
                    f"Gyro calibration applied: {controller_id or 'legacy'}",
                ))

        try:
            accel_bias, accel_matrix = load_accelerometer_calibration(
                source_config, controller_id
            )
            target.set_accelerometer_calibration(accel_bias, accel_matrix)
        except (ValueError, configparser.Error) as exc:
            target.set_accelerometer_calibration(None)
            print(tr(
                f"加速度計校正資料無效，改用預設值：{exc}",
                f"Invalid accelerometer calibration. Using defaults: {exc}",
            ))

        try:
            mag_bias = load_magnetometer_bias(source_config, controller_id)
            target.set_magnetometer_calibration(
                mag_bias,
                load_magnetometer_scale(source_config, controller_id),
                load_magnetometer_matrix(source_config, controller_id),
            )
        except (ValueError, configparser.Error) as exc:
            target.set_magnetometer_calibration(None)
            print(tr(
                f"磁力計校正資料無效，改用預設值：{exc}",
                f"Invalid magnetometer calibration. Using defaults: {exc}",
            ))
        else:
            if announce and mag_bias is not None:
                print(tr(
                    f"已套用磁力計校正資料：{controller_id}",
                    f"Magnetometer calibration applied: {controller_id}",
                ))

    def reload_runtime_settings(expected_connection_id=None, expected_compensation_signature=None):
        """Hot-swap gameplay settings without replacing either connection."""
        connection = active_connection
        if expected_connection_id is not None:
            connection_error = tr(
                "手把連線已變更，請重新開啟補償視窗。",
                "The controller connection changed. Reopen the compensation window.",
            )
            # Transport readiness may take a lock used by on_connected().
            # Check it before taking the config or input-dispatcher lock.
            if (
                connection is None or connection[1] != expected_connection_id
                or not controller_is_connected()
            ):
                raise ValueError(connection_error)

        def replace_settings():
            nonlocal config, xinput, audio_haptics, previous_audio
            controller_id = getattr(controller, "controller_id", None)
            if expected_connection_id is not None:
                if stop_requested or active_connection is not connection:
                    raise ValueError(connection_error)
                controller_id = connection[0]
            try:
                calibration = load_runtime_stick_calibration(new_config, controller_id)
                previous_xinput.reconfigure(new_config, calibration)
                previous_xinput.set_calibration(calibration)
                apply_motion_calibration(previous_xinput, new_config, controller_id)
                if previous_audio is None:
                    previous_audio = AudioHaptics(
                        new_config, previous_xinput.set_audio_rumble
                    )
                    previous_audio.start()
                else:
                    previous_audio.reconfigure(new_config)
            except CalibrationInProgressError:
                # reconfigure() rejected before changing any runtime state.
                raise
            except Exception:
                previous_xinput.reconfigure(
                    previous_config, previous_calibration, mapping_layers=previous_layers,
                )
                apply_motion_calibration(previous_xinput, previous_config, controller_id)
                xinput = previous_xinput
                try:
                    if previous_audio is not None:
                        previous_audio.reconfigure(previous_config)
                    audio_haptics = previous_audio
                except Exception as rollback_exc:
                    print(tr(
                        f"回復音訊震動失敗：{rollback_exc}",
                        f"Failed to restore audio haptics: {rollback_exc}",
                    ))
                raise

            config = new_config
            set_language_from_config(new_config)
            idle_tracker.configure(
                load_idle_disconnect_minutes(new_config)
            )
            xinput = previous_xinput
            audio_haptics = previous_audio
            if connection_mode == "wired":
                controller.set_audio_haptics_active(
                    new_config.get(
                        "audio_haptics", "mode", fallback="GAME"
                    ).strip().upper() in ("AUDIO", "MIX")
                )

        # Keep the INI and layer JSON reads in the same save generation.
        with config_file_lock():
            previous_config = config
            previous_xinput = xinput
            previous_audio = audio_haptics
            previous_calibration = previous_xinput.cal
            previous_layers = previous_xinput.mapping_layers

            new_config = load_config(CONFIG_PATH, persist=expected_compensation_signature is None)
            if (
                expected_compensation_signature is not None
                and compensation_reload_signature(new_config) != expected_compensation_signature
            ):
                raise ValueError(tr(
                    "方案、補償倍率或陀螺儀輸出已變更，請重新開啟補償視窗。",
                    "The profile, compensation, or gyro output changed. Reopen the compensation window.",
                ))
            new_baudrate = new_config.getint("serial", "baudrate", fallback=2_000_000)
            if new_baudrate != baudrate:
                raise ValueError(
                    "serial baudrate changed; restart the connection to apply it"
                )

            if not input_dispatcher.run_exclusive(replace_settings, timeout=1.0):
                raise TimeoutError(
                    "Input processing did not quiesce for settings reload."
                )

    def on_connected():
        nonlocal last_status_stage
        nonlocal gyro_initialization_tracking
        nonlocal gyro_initialization_announced
        nonlocal gyro_initialization_ready_announced
        nonlocal gyro_initialization_complete_announced
        nonlocal idle_disconnect_requested
        nonlocal latest_battery_percent
        nonlocal active_connection
        active_connection = None
        if not input_dispatcher.reset(timeout=1.0):
            raise TimeoutError(
                "Input processing did not quiesce during connection startup."
            )
        sensor_mode_tracker.reset()
        last_status_stage = 0.0
        latest_battery_percent = None
        idle_disconnect_requested = False
        idle_tracker.reset()
        set_input_gc_suppressed(True)
        # Settings and shared layer files may change while transport discovery
        # is still running. Refresh once before accepting the first report.
        try:
            reload_runtime_settings()
            enabled_layer_count = sum(
                1 for layer in xinput.mapping_layers
                if layer.get("enabled", False)
            )
            print(tr(
                f"首次連線已載入 {len(xinput.mapping_layers)} 個映射層，"
                f"其中 {enabled_layer_count} 個已啟用。",
                f"Loaded {len(xinput.mapping_layers)} mapping layer(s) on first "
                f"connection; {enabled_layer_count} enabled.",
            ))
        except Exception as exc:
            print(tr(
                f"首次連線載入最新設定失敗：{exc}",
                f"Failed to load latest settings on first connection: {exc}",
            ))
        controller_id = getattr(controller, "controller_id", None)
        xinput.set_calibration(load_runtime_stick_calibration(config, controller_id))
        try:
            gyro_config = configparser.ConfigParser()
            if not gyro_config.read(CONFIG_PATH, encoding="utf-8"):
                raise FileNotFoundError("config.ini not found")
        except (OSError, ValueError, configparser.Error) as exc:
            xinput.set_gyro_bias(None)
            xinput.set_accelerometer_calibration(None)
            xinput.set_magnetometer_calibration(None)
            print(tr(f"無法讀取感測器校正資料：{exc}", f"Could not read sensor calibration data: {exc}"))
        else:
            apply_motion_calibration(xinput, gyro_config, controller_id, announce=True)
        gyro_initialization = xinput.get_gyro_initialization_status()
        gyro_initialization_announced.clear()
        gyro_initialization_complete_announced = bool(
            gyro_initialization["complete"]
        )
        gyro_initialization_ready_announced = bool(
            gyro_initialization["ready"]
        )
        gyro_initialization_tracking = (
            gyro_initialization["source"] == "automatic"
            and not gyro_initialization["complete"]
        )
        if gyro_initialization_tracking:
            settle_seconds = gyro_initialization["settle_seconds"]
            usable_samples = gyro_initialization["usable_samples"]
            final_samples = gyro_initialization["final_samples"]
            print(tr(
                f"陀螺儀初始化：請將手把平放並保持靜止；約 {settle_seconds:.1f} 秒後開始收集，{usable_samples} 個樣本後可用，{final_samples} 個樣本完成。",
                f"Gyro initialization: place the controller down and keep it still; collection starts after about {settle_seconds:.1f} seconds, becomes usable at {usable_samples} samples, and completes at {final_samples} samples.",
            ))
        elif gyro_initialization["complete"]:
            print(tr(
                "陀螺儀初始化完成：已載入校正資料，可立即使用。",
                "Gyro initialization complete: saved calibration data was loaded and the gyro is ready immediately.",
            ))

        connection_id = uuid4().hex
        publish_status(
            state="connected",
            controller_id=controller_id,
            connection_id=connection_id,
            battery_percent=None,
            battery_voltage=None,
            charging=False,
            wired_full_report=(
                getattr(controller, "full_report", None)
                if connection_mode == "wired" else None
            ),
            wired_polling_rate=(
                getattr(controller, "polling_rate_hz", None)
                if connection_mode == "wired" else None
            ),
            wired_processing_rate=(
                getattr(input_dispatcher, "processing_rate_hz", None)
                if connection_mode == "wired" else None
            ),
            input_report_rate=getattr(
                input_dispatcher, "input_rate_hz", None
            ),
            xinput_output_rate=getattr(
                input_dispatcher, "processing_rate_hz", None
            ),
            sensor_mode=None,
            gyro_raw=None,
            accel_raw=None,
            mag_raw=None,
            gyro_bias_samples=getattr(xinput, "_gyro_bias_samples", 0),
        )
        active_connection = (controller_id, connection_id)

    controller.connected_callback = on_connected

    def on_disconnected():
        nonlocal last_status_stage
        nonlocal gyro_initialization_tracking
        nonlocal gyro_initialization_announced
        nonlocal idle_disconnect_requested
        nonlocal latest_battery_percent
        nonlocal active_connection
        active_connection = None
        dispatcher_reset = input_dispatcher.reset(timeout=1.0)
        sensor_mode_tracker.reset()
        last_status_stage = 0.0
        latest_battery_percent = None
        gyro_initialization_tracking = False
        gyro_initialization_announced.clear()
        set_input_gc_suppressed(False)
        idle_request_in_progress = idle_disconnect_requested
        # No release report arrives after a disconnect, so explicitly clear
        # the last virtual-gamepad and keyboard state.
        try:
            if dispatcher_reset:
                xinput.reset_output_state()
            else:
                print(tr(
                    "輸入處理仍在執行；略過可能與回呼競爭的輸出重設。",
                    "Input processing is still active; skipped an unsafe output reset.",
                ))
        finally:
            # Idle status is committed by perform_idle_disconnect() only after
            # its synchronous transport result confirms the physical link is
            # gone.  A callback arriving during a failed attempt must not
            # publish a false idle_disconnected state.
            if not idle_request_in_progress:
                publish_status(
                    state="disconnected",
                    battery_percent=None,
                    battery_voltage=None,
                    charging=False,
                    wired_full_report=None,
                    wired_polling_rate=None,
                    wired_processing_rate=None,
                    input_report_rate=None,
                    xinput_output_rate=None,
                    sensor_mode=None,
                    gyro_raw=None,
                    accel_raw=None,
                    gyro_bias_samples=0,
                )
        if not idle_request_in_progress:
            idle_disconnect_requested = False

    controller.disconnected_callback = on_disconnected

    def on_transport_unavailable():
        """Stop the connector when its BLE radio or USB bridge is gone."""
        nonlocal stop_requested
        nonlocal gyro_initialization_tracking
        nonlocal gyro_initialization_announced
        nonlocal active_connection
        active_connection = None
        stop_requested = True
        gyro_initialization_tracking = False
        gyro_initialization_announced.clear()
        dispatcher_reset = input_dispatcher.reset(timeout=1.0)
        sensor_mode_tracker.reset()
        try:
            if dispatcher_reset:
                xinput.reset_output_state()
            else:
                print(tr(
                    "輸入處理仍在執行；略過可能與回呼競爭的輸出重設。",
                    "Input processing is still active; skipped an unsafe output reset.",
                ))
        finally:
            publish_status(
                state="stopped",
                battery_percent=None,
                battery_voltage=None,
                charging=False,
                wired_full_report=None,
                wired_polling_rate=None,
                wired_processing_rate=None,
                input_report_rate=None,
                xinput_output_rate=None,
                sensor_mode=None,
                gyro_raw=None,
                accel_raw=None,
                gyro_bias_samples=0,
            )

    if connection_mode == "esp32":
        controller.bridge_disconnected_callback = on_transport_unavailable
    elif connection_mode == "bluetooth":
        controller.bluetooth_unavailable_callback = on_transport_unavailable

    def controller_is_connected():
        return controller_application_ready(controller)

    def start_sensor_calibration(sensor, start, expected_connection_id):
        """Return None when a previous result is already waiting to be saved."""
        connection = active_connection
        # The transport may acquire its state lock here. Keep this outside
        # the dispatcher lock since the connection callbacks reset the dispatcher.
        if connection is None or not controller_is_connected():
            return False
        if expected_connection_id is not None and expected_connection_id != connection[1]:
            return False

        started = False

        def begin():
            nonlocal started
            if stop_requested or active_connection is not connection:
                return
            if start():
                calibration_connections[sensor] = connection
                started = True
            else:
                # The existing result will be saved by this iteration.
                started = None

        input_dispatcher.run_exclusive(begin, timeout=1.0)
        return started

    def apply_saved_sensor_calibration(connection, apply):
        def apply_if_current():
            if not stop_requested and active_connection is connection:
                apply()
                xinput.reset_tilt_neutral()

        if stop_requested or active_connection is not connection:
            return "success", "saved"
        try:
            if input_dispatcher.run_exclusive(apply_if_current, timeout=1.0):
                return "success", "saved"
            raise TimeoutError("Input processing did not quiesce for calibration.")
        except (OSError, ValueError) as exc:
            print(tr(
                f"校正已儲存，無法立即套用：{exc}",
                f"Calibration was saved but could not be applied immediately: {exc}",
            ))
            return "failed", "apply_failed"

    if stop_requested:
        cleanup()
        return

    if connection_mode == "wired":
        print(tr(
            "正在啟動 USB 有線連線...",
            "Starting wired USB connection...",
        ))
        try:
            controller.open()
        except Exception as exc:
            print(tr(
                f"USB 有線連線啟動失敗：{exc}",
                f"Wired USB connection failed to start: {exc}",
            ))
            cleanup()
            wait_for_error_acknowledgment()
            return

    elif connection_mode == "esp32":

        print(
            tr(f"正在連接 ESP32：{port}", f"Connecting to ESP32: {port}")
        )

        try:
            controller.open()

        except (serial.SerialException, OSError, RuntimeError):
            cleanup()
            if stop_requested:
                return
            print()
            print(
                tr(f"無法開啟 {port}。", f"Could not open {port}.")
            )
            print(
                tr("此連接埠可能正在被其他程式使用。", "The serial port may be in use by another program.")
            )
            print(
                tr(
                    "請關閉原本的連接程式或校正程式後再試。",
                    "Close the existing connection or calibration program and try again.",
                )
            )

            wait_for_error_acknowledgment()
            return

        if stop_requested:
            cleanup()
            return

        print(
            tr("ESP32 連接成功。", "ESP32 connected.")
        )
        print()

        print(
            tr("正在搜尋 Switch 2 Pro Controller...", "Searching for Switch 2 Pro Controller...")
        )
        print(
            tr(
                "已配對的手把請按任意按鍵喚醒；第一次配對請按住 SYNC。",
                "Press any button to wake a paired controller; hold SYNC for first-time pairing.",
            )
        )

        controller.start_scan()

    else:

        print(
            tr("正在啟動 Windows 原生藍牙...", "Starting Windows native Bluetooth...")
        )
        print(
            tr(
                "請按下手把任意按鍵開啟手把並等待連線。",
                "Press any controller button to wake it, then wait for connection.",
            )
        )
        print()

        try:
            controller.open()
        except Exception as exc:
            print()
            print(tr(f"Windows 原生藍牙啟動失敗：{exc}", f"Windows native Bluetooth failed to start: {exc}"))
            cleanup()
            wait_for_error_acknowledgment()
            return

    try:
        last_heartbeat = 0.0
        last_battery_led_mask = None
        last_gyro_calibration_state = ("idle", "")
        last_mag_calibration_state = ("idle", "", -1, ())
        last_accel_calibration_state = ("idle", "", -1, ())
        mag_coverage_active = False
        accel_coverage_active = False
        last_mag_coverage_bins = None
        last_accel_coverage_bins = None
        while not stop_requested:
            now = time.time()
            if now - last_heartbeat >= 0.5:
                rumble_status = xinput.get_rumble_diagnostics()
                try:
                    transport_rumble = getattr(
                        controller, "get_rumble_diagnostics", lambda: {}
                    )()
                except Exception:
                    transport_rumble = {}
                if isinstance(transport_rumble, dict):
                    rumble_status.update({
                        f"transport_{key}": value
                        for key, value in transport_rumble.items()
                    })
                firmware_diagnostics = {}
                if connection_mode == "esp32":
                    controller.poll_diagnostics()
                    firmware_diagnostics = (
                        controller.get_firmware_diagnostics()
                    )
                publish_status(
                    rumble=rumble_status,
                    firmware_diagnostics=firmware_diagnostics,
                    pairing_diagnostics=getattr(controller, "pairing_diagnostics", None),
                    async_logging=get_async_log_metrics(),
                )
                if not controller_is_connected():
                    last_battery_led_mask = None
                else:
                    led_mask = battery_led_mask(latest_battery_percent)
                    if (
                        led_mask is not None
                        and led_mask != last_battery_led_mask
                    ):
                        set_led_mask = getattr(
                            controller,
                            "set_player_led_mask",
                            None,
                        )
                        if set_led_mask is not None and set_led_mask(led_mask):
                            last_battery_led_mask = led_mask
                last_heartbeat = now
            if (
                connection_mode != "wired"
                and not idle_disconnect_requested
                and controller_is_connected()
                and idle_tracker.expired()
            ):
                idle_disconnect_requested = True
                try:
                    if not perform_idle_disconnect(
                        controller,
                        input_dispatcher.run_exclusive,
                        xinput.reset_output_state,
                        publish_status,
                    ):
                        raise RuntimeError(tr(
                            "控制器未確認中斷。",
                            "The controller did not confirm disconnection.",
                        ))
                    print(tr(
                        "控制器因閒置而中斷無線連線；按任意鍵即可重新連線。",
                        "Controller disconnected after being idle; press any "
                        "button to reconnect.",
                    ))
                except Exception as exc:
                    idle_disconnect_requested = False
                    idle_tracker.reset()
                    print(tr(
                        f"閒置自動斷線失敗：{exc}",
                        f"Idle disconnect failed: {exc}",
                    ))
            pending_command = command_inbox.next()
            if pending_command is not None:
                try:
                    command = pending_command.command
                    request_id = pending_command.request_id
                    clear_legacy_command = pending_command.clear_legacy

                    if command == "pin":
                        # 先清除指令，避免重複執行
                        clear_legacy_command()

                        if controller_is_connected():
                            controller.pin_rumble()

                    elif command == "diagnostic_start":
                        clear_legacy_command()
                        if connection_mode == "esp32":
                            controller.start_diagnostics()
                            publish_status(
                                firmware_diagnostics=(
                                    controller.get_firmware_diagnostics()
                                )
                            )

                    elif command == "diagnostic_stop":
                        clear_legacy_command()
                        if connection_mode == "esp32":
                            controller.stop_diagnostics()
                            publish_status(
                                firmware_diagnostics=(
                                    controller.get_firmware_diagnostics()
                                )
                            )

                    elif command == "calibrate_gyro":
                        clear_legacy_command()
                        started = start_sensor_calibration(
                            "gyro", xinput.start_gyro_calibration, pending_command.connection_id
                        )
                        if started:
                            publish_status(
                                gyro_calibration_state="running",
                                gyro_calibration_message="keep_still",
                            )
                        elif started is False:
                            publish_status(
                                gyro_calibration_state="failed",
                                gyro_calibration_message="disconnected",
                            )

                    elif command == "calibrate_magnetometer":
                        clear_legacy_command()
                        started = start_sensor_calibration(
                            "magnetometer", xinput.start_magnetometer_calibration,
                            pending_command.connection_id,
                        )
                        if started:
                            publish_status(
                                mag_calibration_state="running",
                                mag_calibration_message="move_figure_eight",
                                mag_calibration_progress=0,
                                mag_calibration_spans=[0.0, 0.0, 0.0],
                            )
                        elif started is False:
                            publish_status(
                                mag_calibration_state="failed",
                                mag_calibration_message="disconnected",
                            )

                    elif command == "calibrate_accelerometer":
                        clear_legacy_command()
                        started = start_sensor_calibration(
                            "accelerometer", xinput.start_accelerometer_calibration,
                            pending_command.connection_id,
                        )
                        if started:
                            publish_status(
                                accel_calibration_state="running",
                                accel_calibration_message="move_and_pause",
                                accel_calibration_progress=0,
                                accel_calibration_quality=None,
                            )
                        elif started is False:
                            publish_status(
                                accel_calibration_state="failed",
                                accel_calibration_message="disconnected",
                            )

                    elif command == "reset_tilt_neutral":
                        clear_legacy_command()
                        recentered = xinput.reset_tilt_neutral()
                        publish_status(
                            tilt_recenter_state=(
                                "success" if recentered else "unavailable"
                            ),
                            tilt_recenter_updated_at=time.time(),
                        )

                    elif command == "reload_settings":
                        clear_legacy_command()
                        publish_status(
                            settings_reload_state="running",
                            settings_reload_message="",
                            settings_reload_updated_at=time.time(),
                            settings_reload_request_id=request_id,
                        )
                        try:
                            reload_runtime_settings(
                                pending_command.connection_id, pending_command.compensation_signature,
                            )
                        except Exception as exc:
                            publish_status(
                                settings_reload_state="failed",
                                settings_reload_message=str(exc),
                                settings_reload_updated_at=time.time(),
                                settings_reload_request_id=request_id,
                            )
                            print(tr(
                                f"執行中的連線未能套用新設定，目前暫時沿用先前設定；"
                                f"磁碟設定已保留：{exc}",
                                f"The live connection could not apply the new settings and is "
                                f"temporarily using the previous settings; disk settings were kept: {exc}",
                            ))
                        else:
                            enabled_layer_count = sum(
                                1 for layer in xinput.mapping_layers
                                if layer.get("enabled", False)
                            )
                            publish_status(
                                settings_reload_state="success",
                                settings_reload_message="",
                                settings_reload_updated_at=time.time(),
                                settings_reload_request_id=request_id,
                            )
                            print(tr(
                                "設定已即時套用，不需重新連線。",
                                "Settings applied live; no reconnection required.",
                            ))
                            print(tr(
                                f"已載入 {len(xinput.mapping_layers)} 個映射層，"
                                f"其中 {enabled_layer_count} 個已啟用。",
                                f"Loaded {len(xinput.mapping_layers)} mapping layer(s); "
                                f"{enabled_layer_count} enabled.",
                            ))

                    elif command:
                        # Clear malformed or obsolete commands too. Otherwise
                        # this loop would keep re-reading the same text.
                        clear_legacy_command()
                        print(tr(f"已忽略未知的控制器指令：{command}", f"Unknown controller command ignored: {command}"))

                except Exception as exc:
                    print(
                        f"處理控制器指令失敗：{exc}"
                    )
                finally:
                    pending_command.finish()

            if gyro_initialization_tracking and controller_is_connected():
                initialization = xinput.get_gyro_initialization_status()
                phase = initialization["state"]
                samples = initialization["samples"]
                usable_samples = initialization["usable_samples"]
                final_samples = initialization["final_samples"]

                if initialization["source"] != "automatic":
                    gyro_initialization_tracking = False
                    gyro_initialization_announced.clear()
                else:
                    # Sensor reports arrive much faster than console messages
                    # can be read. Print only meaningful milestones, and print
                    # each milestone at most once per connection.
                    if (
                        phase == "stabilizing"
                        and "stabilizing" not in gyro_initialization_announced
                    ):
                        gyro_initialization_announced.add("stabilizing")
                        settle_seconds = initialization["settle_seconds"]
                        print(tr(
                            f"陀螺儀初始化：已偵測到靜止，正在確認穩定狀態（約 {settle_seconds:.1f} 秒）。",
                            f"Gyro initialization: stillness detected; confirming stability for about {settle_seconds:.1f} seconds.",
                        ))

                    if (
                        phase == "waiting"
                        and "stabilizing" in gyro_initialization_announced
                        and "movement" not in gyro_initialization_announced
                    ):
                        gyro_initialization_announced.add("movement")
                        print(tr(
                            "陀螺儀初始化：偵測到移動，請保持靜止；程式會自動重新嘗試。",
                            "Gyro initialization: movement detected. Keep the controller still; initialization will retry automatically.",
                        ))

                    if (
                        phase == "collecting"
                        and samples < usable_samples
                        and "collecting" not in gyro_initialization_announced
                    ):
                        gyro_initialization_announced.add("collecting")
                        print(tr(
                            f"陀螺儀初始化：穩定確認完成，開始收集零偏樣本（目標 {usable_samples}，最終 {final_samples}）。",
                            f"Gyro initialization: stability confirmed; collecting zero-bias samples (usable target {usable_samples}, final target {final_samples}).",
                        ))

                    if (
                        initialization["ready"]
                        and not gyro_initialization_ready_announced
                    ):
                        gyro_initialization_ready_announced = True
                        print(tr(
                            f"陀螺儀初始化完成，可以開始使用（{samples}/{final_samples} 個樣本）；背景精修將靜默進行。",
                            f"Gyro initialization complete and ready to use ({samples}/{final_samples} samples); background refinement will continue silently.",
                        ))

                    if (
                        initialization["complete"]
                        and not gyro_initialization_complete_announced
                    ):
                        gyro_initialization_complete_announced = True
                        gyro_initialization_tracking = False
                        gyro_initialization_announced.add("complete")
                        print(tr(
                            f"陀螺儀零偏樣本收集完成（{final_samples}/{final_samples}），初始化已完全完成。",
                            f"Gyro zero-bias sample collection complete ({final_samples}/{final_samples}); initialization is fully complete.",
                        ))
            calibration_status = xinput.get_gyro_calibration_status()
            calibration_key = (
                calibration_status["state"],
                calibration_status["message"],
            )
            if calibration_key != last_gyro_calibration_state:
                last_gyro_calibration_state = calibration_key
                if calibration_key[0] != "success":
                    publish_status(
                        gyro_calibration_state=calibration_key[0],
                        gyro_calibration_message=calibration_key[1],
                        gyro_calibration_quality=calibration_status.get("quality"),
                    )

            gyro_bias = xinput.consume_gyro_calibration_result()
            if gyro_bias is not None:
                connection = calibration_connections.pop("gyro")
                try:
                    with config_file_lock():
                        calibration_config = configparser.ConfigParser()
                        if not calibration_config.read(CONFIG_PATH, encoding="utf-8"):
                            raise FileNotFoundError("config.ini not found")
                        store_gyro_bias(
                            calibration_config,
                            gyro_bias,
                            connection[0],
                        )
                        atomic_write_config(calibration_config, CONFIG_PATH)
                    print(tr("陀螺儀零點校正已儲存。", "Gyro zero calibration saved."))
                    state, message = apply_saved_sensor_calibration(
                        connection, lambda: xinput.set_gyro_bias(gyro_bias)
                    )
                    publish_status(
                        gyro_calibration_state=state,
                        gyro_calibration_message=message,
                        gyro_calibration_quality=xinput.get_gyro_calibration_status().get("quality"),
                    )
                except (OSError, ValueError, configparser.Error) as exc:
                    print(tr(f"陀螺儀校正儲存失敗：{exc}", f"Could not save gyro calibration: {exc}"))
                    publish_status(
                        gyro_calibration_state="failed",
                        gyro_calibration_message="save_failed",
                    )

            mag_status = xinput.get_magnetometer_calibration_status()
            mag_key = (
                mag_status["state"],
                mag_status["message"],
                mag_status["progress"],
                tuple(tuple(item) for item in mag_status.get("orientation_bins", ())),
            )
            if mag_key != last_mag_calibration_state:
                last_mag_calibration_state = mag_key
                if mag_key[0] != "success":
                    publish_status(
                        mag_calibration_state=mag_key[0],
                        mag_calibration_message=mag_key[1],
                        mag_calibration_progress=mag_status["progress"],
                        mag_calibration_spans=list(mag_status["spans"]),
                        mag_orientation_bins=[list(item) for item in mag_status.get("orientation_bins", ())],
                        mag_orientation_coverage=mag_status.get("orientation_coverage", 0.0),
                    )
            mag_bins = mag_status.get("orientation_bins", ())
            mag_bin_signature = tuple(sorted(tuple(item) for item in mag_bins))
            if mag_status["state"] == "running":
                if not mag_coverage_active:
                    mag_coverage_active = True
                    last_mag_coverage_bins = None
                if mag_bin_signature != last_mag_coverage_bins:
                    _print_orientation_coverage(
                        tr("磁力 3D", "Mag 3D"),
                        mag_bins,
                    )
                    last_mag_coverage_bins = mag_bin_signature
            elif mag_coverage_active:
                if mag_bin_signature != last_mag_coverage_bins:
                    _print_orientation_coverage(
                        tr("磁力 3D", "Mag 3D"),
                        mag_bins,
                    )
                    last_mag_coverage_bins = mag_bin_signature
                mag_coverage_active = False
                _finish_orientation_coverage()

            mag_result = xinput.consume_magnetometer_calibration_result()
            if mag_result is not None:
                connection = calibration_connections.pop("magnetometer")
                try:
                    mag_bias, mag_matrix, mag_quality = mag_result
                    with config_file_lock():
                        calibration_config = configparser.ConfigParser()
                        if not calibration_config.read(CONFIG_PATH, encoding="utf-8"):
                            raise FileNotFoundError("config.ini not found")
                        store_magnetometer_calibration(
                            calibration_config,
                            mag_bias,
                            (1.0, 1.0, 1.0),
                            connection[0],
                            matrix=mag_matrix,
                            quality=mag_quality,
                        )
                        atomic_write_config(calibration_config, CONFIG_PATH)
                    print(tr("磁力計三軸校正已儲存。", "3-axis magnetometer calibration saved."))
                    state, message = apply_saved_sensor_calibration(
                        connection, lambda: xinput.set_magnetometer_calibration(
                            mag_bias, matrix=mag_matrix, field_reference=1.0
                        )
                    )
                    publish_status(
                        mag_calibration_state=state,
                        mag_calibration_message=message,
                        mag_calibration_progress=100,
                        mag_calibration_spans=list(mag_status["spans"]),
                        mag_calibration_quality=mag_quality,
                    )
                except (OSError, ValueError, configparser.Error) as exc:
                    print(tr(f"磁力計校正儲存失敗：{exc}", f"Could not save magnetometer calibration: {exc}"))
                    publish_status(
                        mag_calibration_state="failed",
                        mag_calibration_message="save_failed",
                    )

            accel_status = xinput.get_accelerometer_calibration_status()
            accel_key = (
                accel_status["state"],
                accel_status["message"],
                accel_status["progress"],
                tuple(tuple(item) for item in accel_status.get("orientation_bins", ())),
            )
            if accel_key != last_accel_calibration_state:
                last_accel_calibration_state = accel_key
                if accel_key[0] != "success":
                    publish_status(
                        accel_calibration_state=accel_status["state"],
                        accel_calibration_message=accel_status["message"],
                        accel_calibration_progress=accel_status["progress"],
                        accel_orientation_bins=[list(item) for item in accel_status.get("orientation_bins", ())],
                        accel_orientation_coverage=accel_status.get("orientation_coverage", 0.0),
                    )
            accel_bins = accel_status.get("orientation_bins", ())
            accel_bin_signature = tuple(sorted(tuple(item) for item in accel_bins))
            if accel_status["state"] == "running":
                if not accel_coverage_active:
                    accel_coverage_active = True
                    last_accel_coverage_bins = None
                if accel_bin_signature != last_accel_coverage_bins:
                    _print_orientation_coverage(
                        tr("加速 3D", "Accel 3D"),
                        accel_bins,
                    )
                    last_accel_coverage_bins = accel_bin_signature
            elif accel_coverage_active:
                if accel_bin_signature != last_accel_coverage_bins:
                    _print_orientation_coverage(
                        tr("加速 3D", "Accel 3D"),
                        accel_bins,
                    )
                    last_accel_coverage_bins = accel_bin_signature
                accel_coverage_active = False
                _finish_orientation_coverage()

            accel_result = xinput.consume_accelerometer_calibration_result()
            if accel_result is not None:
                connection = calibration_connections.pop("accelerometer")
                try:
                    accel_bias, accel_matrix, accel_quality = accel_result
                    with config_file_lock():
                        calibration_config = configparser.ConfigParser()
                        if not calibration_config.read(CONFIG_PATH, encoding="utf-8"):
                            raise FileNotFoundError("config.ini not found")
                        store_accelerometer_calibration(
                            calibration_config,
                            accel_bias,
                            accel_matrix,
                            connection[0],
                            quality=accel_quality,
                        )
                        atomic_write_config(calibration_config, CONFIG_PATH)
                    print(tr("加速度計多姿態橢球校正已儲存。", "Multi-pose accelerometer calibration saved."))
                    state, message = apply_saved_sensor_calibration(
                        connection, lambda: xinput.set_accelerometer_calibration(
                            accel_bias, accel_matrix
                        )
                    )
                    publish_status(
                        accel_calibration_state=state,
                        accel_calibration_message=message,
                        accel_calibration_progress=100,
                        accel_calibration_quality=accel_quality,
                        accel_orientation_bins=[list(item) for item in accel_status.get("orientation_bins", ())],
                        accel_orientation_coverage=accel_status.get("orientation_coverage", 0.0),
                    )
                except (OSError, ValueError, configparser.Error) as exc:
                    print(tr(f"加速度計校正儲存失敗：{exc}", f"Could not save accelerometer calibration: {exc}"))
                    publish_status(
                        accel_calibration_state="failed",
                        accel_calibration_message="save_failed",
                    )

            time.sleep(0.1)

    except KeyboardInterrupt:
        print(tr("\n正在關閉程式...", "\nClosing program..."))

    finally:
        cleanup()


if __name__ == "__main__":
    sys.exit(main())
