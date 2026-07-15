import configparser
import time
import signal
from pathlib import Path
from esp32_bridge import ESP32Bridge
from bluetooth_controller import (
    BluetoothController
)
from switch2_input import parse_input_report
from xinput_controller import XInputController
from audio_haptics import AudioHaptics
import serial
import json
import serial.tools.list_ports
import threading
from version import APP_TITLE
from config_utils import (
    CONFIG_PATH,
    atomic_write_config,
    load_accelerometer_calibration,
    load_gyro_bias,
    load_magnetometer_bias,
    load_magnetometer_matrix,
    load_magnetometer_scale,
    load_stick_calibration,
    store_gyro_bias,
    store_accelerometer_calibration,
    store_magnetometer_calibration,
)
from console_i18n import current_language
from console_i18n import localized_print as print
from console_i18n import localized_input as input

COMMAND_PATH = Path(__file__).with_name(
    "controller_command.txt"
)
STATUS_PATH = Path(__file__).with_name("controller_status.json")
def tr(zh, en):
    return en if current_language() == "en" else zh

def find_esp32_port(baudrate=2_000_000):
    """自動尋找執行相容 Bridge 韌體的 ESP32-S3。"""

    ports = list(
        serial.tools.list_ports.comports()
    )

    for port_info in ports:
        port = port_info.device

        try:
            with serial.Serial(
                port,
                baudrate,
                timeout=0.5,
                write_timeout=0.5
            ) as ser:
                ser.reset_input_buffer()

                ser.write(b"status lite\n")
                ser.flush()

                deadline = time.time() + 1.0

                while time.time() < deadline:
                    line = ser.readline()

                    if not line:
                        continue

                    try:
                        text = line.decode(
                            "utf-8",
                            errors="ignore"
                        ).strip()

                        if "{" not in text or "}" not in text:
                            continue

                        start = text.find("{")
                        end = text.rfind("}")

                        data = json.loads(
                            text[start:end + 1]
                        )

                        if (
                            data.get("cmd") in ("status", "status lite")
                            and data.get("profile") == "tinyusb_direct"
                            and data.get("build") == "cdc_bridge_1"
                        ):
                            return port

                    except Exception:
                        continue

        except (
            serial.SerialException,
            OSError
        ):
            continue

    return None

def main():
    status_lock = threading.Lock()
    status_write_lock = threading.Lock()
    controller_status = {
        "state": "starting", "mode": None,
        "battery_percent": None, "battery_voltage": None,
        "charging": False,
        "gyro_raw": None, "accel_raw": None,
        "report_time": None, "report_delta": None,
        "mag_field_valid": None,
        "gyro_bias_samples": 0,
        "gyro_calibration_state": "idle",
        "gyro_calibration_message": "",
        "gyro_calibration_quality": None,
        "mag_calibration_state": "idle",
        "mag_calibration_message": "",
        "mag_calibration_progress": 0,
        "mag_calibration_spans": [0.0, 0.0, 0.0],
        "mag_calibration_quality": None,
        "accel_calibration_state": "idle",
        "accel_calibration_message": "",
        "accel_calibration_progress": 0,
        "accel_calibration_quality": None,
        "tilt_recenter_state": "idle",
        "tilt_recenter_updated_at": 0.0,
        "updated_at": time.time(),
    }

    def publish_status(**changes):
        # Serialize file replacement separately from the short-lived state
        # lock.  Input callbacks can keep staging reports even if disk I/O is
        # temporarily slow.
        with status_write_lock:
            with status_lock:
                controller_status.update(changes)
                controller_status["updated_at"] = time.time()
                serialized_status = json.dumps(
                    controller_status, ensure_ascii=False
                )
            temp_path = STATUS_PATH.with_suffix(".json.tmp")
            try:
                temp_path.write_text(
                    serialized_status,
                    encoding="utf-8",
                )
                temp_path.replace(STATUS_PATH)
            except OSError:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def stage_status(**changes):
        """Update status in memory without doing disk I/O on an input thread."""
        with status_lock:
            controller_status.update(changes)
            controller_status["updated_at"] = time.time()

    publish_status()
    # Commands are one-shot messages from the GUI. Never replay a command
    # left behind by a previous process that ended unexpectedly.
    try:
        COMMAND_PATH.write_text("", encoding="utf-8")
    except OSError:
        pass

    config = configparser.ConfigParser()

    if not config.read(CONFIG_PATH, encoding="utf-8"):
        publish_status(state="stopped")
        raise FileNotFoundError("config.ini not found")

    print("========================================")
    print(f"         {APP_TITLE}")
    print("========================================")
    print()


    # Start with the legacy profile.  Once the controller is identified, the
    # matching per-device profile is loaded by on_connected().
    try:
        custom_stick_cal = load_stick_calibration(config)
        baudrate = config.getint(
            "serial",
            "baudrate",
            fallback=2_000_000,
        )
        if baudrate <= 0:
            raise ValueError("serial baudrate must be positive")
    except (ValueError, TypeError, configparser.Error) as exc:
        publish_status(state="stopped")
        print(tr(f"config.ini 的校正或序列設定無效：{exc}", f"Invalid calibration or serial settings in config.ini: {exc}"))
        input(tr("\n按 Enter 鍵關閉...", "\nPress Enter to close..."))
        return

    print(tr("正在自動偵測 ESP32...", "Detecting ESP32 automatically..."))

    port = find_esp32_port(baudrate)

    if port is not None:
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
    publish_status(state="searching", mode=connection_mode)
    audio_haptics = None
    try:
        xinput = XInputController(
            config,
            custom_stick_cal
        )

    except (ValueError, TypeError, KeyError, configparser.Error) as exc:
        publish_status(
            state="stopped",
            battery_percent=None,
            charging=False,
        )
        print()
        print(tr(f"config.ini 的控制器設定無效：{exc}", f"Invalid controller settings in config.ini: {exc}"))
        input(tr("\n按 Enter 鍵關閉...", "\nPress Enter to close..."))
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

        input(tr("\n按 Enter 鍵關閉...", "\nPress Enter to close..."))
        return

    try:
        if connection_mode == "esp32":
            controller = ESP32Bridge(port, baudrate)
            xinput.set_rumble_sender(controller.send_rumble_shadow)
        else:
            controller = BluetoothController()
            xinput.set_rumble_sender(controller.send_pro_rumble)
    except Exception as exc:
        xinput.close()
        publish_status(state="stopped")
        print(tr(f"控制器連線模組啟動失敗：{exc}", f"Controller connection module failed to start: {exc}"))
        input(tr("\n按 Enter 鍵關閉...", "\nPress Enter to close..."))
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
        input(tr("\n按 Enter 鍵關閉...", "\nPress Enter to close..."))
        return

    cleanup_complete = False

    def cleanup():
        """Release every runtime resource once, including startup failures."""
        nonlocal cleanup_complete
        if cleanup_complete:
            return
        cleanup_complete = True

        if audio_haptics is not None:
            try:
                audio_haptics.close()
            except Exception as exc:
                print(tr(f"音訊模組清理失敗：{exc}", f"Audio module cleanup failed: {exc}"))

        try:
            xinput.close()
        except Exception as exc:
            print(tr(f"虛擬控制器清理失敗：{exc}", f"Virtual controller cleanup failed: {exc}"))

        publish_status(
            state="stopped",
            battery_percent=None,
            battery_voltage=None,
            charging=False,
            gyro_raw=None,
            accel_raw=None,
            gyro_bias_samples=0,
        )

        try:
            controller.close()
        except Exception as exc:
            print(tr(f"控制器連線清理失敗：{exc}", f"Controller connection cleanup failed: {exc}"))

    stop_requested = False
    last_imu_status = 0.0
    last_input_error = 0.0

    def on_input(payload):
        nonlocal last_imu_status, last_input_error
        state = parse_input_report(
            payload
        )

        if state is not None:
            # Never write controller_status.json from the BLE notification or
            # ESP32 serial-reader thread.  A slow filesystem/antivirus scan
            # must not delay draining controller input reports.
            stage_status(
                state="connected",
                battery_percent=state.battery_percent,
                battery_voltage=state.battery_voltage,
                charging=state.charging,
            )
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
            if now - last_imu_status >= 0.5:
                last_imu_status = now
                stage_status(
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

    controller.input_callback = on_input

    def on_connected():
        controller_id = getattr(controller, "controller_id", None)
        try:
            xinput.set_calibration(
                load_stick_calibration(config, controller_id)
            )
            if controller_id:
                print(tr(f"已套用控制器校正資料：{controller_id}", f"Controller calibration applied: {controller_id}"))
        except (ValueError, configparser.Error) as exc:
            print(tr(f"控制器校正資料無效，沿用預設校正：{exc}", f"Invalid controller calibration; using defaults: {exc}"))
        try:
            gyro_config = configparser.ConfigParser()
            if not gyro_config.read(CONFIG_PATH, encoding="utf-8"):
                raise FileNotFoundError("config.ini not found")
            gyro_bias = load_gyro_bias(gyro_config, controller_id)
            xinput.set_gyro_bias(gyro_bias)
            accel_bias, accel_matrix = load_accelerometer_calibration(
                gyro_config, controller_id
            )
            xinput.set_accelerometer_calibration(accel_bias, accel_matrix)
            mag_bias = load_magnetometer_bias(gyro_config, controller_id)
            mag_scale = load_magnetometer_scale(gyro_config, controller_id)
            mag_matrix = load_magnetometer_matrix(gyro_config, controller_id)
            xinput.set_magnetometer_calibration(
                mag_bias, mag_scale, mag_matrix
            )
            if gyro_bias is not None:
                print(tr(f"已套用陀螺儀校正資料：{controller_id or 'legacy'}", f"Gyro calibration applied: {controller_id or 'legacy'}"))
            if mag_bias is not None:
                print(tr(f"已套用磁力計校正資料：{controller_id}", f"Magnetometer calibration applied: {controller_id}"))
        except (OSError, ValueError, configparser.Error) as exc:
            xinput.set_gyro_bias(None)
            xinput.set_accelerometer_calibration(None)
            xinput.set_magnetometer_calibration(None)
            print(tr(f"陀螺儀校正資料無效，改用自動估算：{exc}", f"Invalid motion calibration; using automatic estimation: {exc}"))
        publish_status(
            state="connected",
            battery_percent=None,
            battery_voltage=None,
            charging=False,
            gyro_raw=None,
            accel_raw=None,
            mag_raw=None,
            gyro_bias_samples=getattr(xinput, "_gyro_bias_samples", 0),
        )

    controller.connected_callback = on_connected

    def on_disconnected():
        # No release report arrives after a disconnect, so explicitly clear
        # the last virtual-gamepad and keyboard state.
        try:
            xinput.reset_output_state()
        finally:
            publish_status(
                state="disconnected",
                battery_percent=None,
                battery_voltage=None,
                charging=False,
                gyro_raw=None,
                accel_raw=None,
                gyro_bias_samples=0,
            )

    controller.disconnected_callback = on_disconnected

    def on_transport_unavailable():
        """Stop the connector when its BLE radio or USB bridge is gone."""
        nonlocal stop_requested
        stop_requested = True
        try:
            xinput.reset_output_state()
        finally:
            publish_status(
                state="stopped",
                battery_percent=None,
                battery_voltage=None,
                charging=False,
                gyro_raw=None,
                accel_raw=None,
                gyro_bias_samples=0,
            )

    if connection_mode == "esp32":
        controller.bridge_disconnected_callback = on_transport_unavailable
    else:
        controller.bluetooth_unavailable_callback = on_transport_unavailable

    if connection_mode == "esp32":

        print(
            tr(f"正在連接 ESP32：{port}", f"Connecting to ESP32: {port}")
        )

        try:
            controller.open()

        except (serial.SerialException, OSError):
            cleanup()
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

            input(
                tr("\n按 Enter 鍵關閉...", "\nPress Enter to close...")
            )
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
            input(tr("\n按 Enter 鍵關閉...", "\nPress Enter to close..."))
            return

    def handle_shutdown_signal(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(
        signal.SIGBREAK,
        handle_shutdown_signal
    )

    try:
        last_heartbeat = 0.0
        last_gyro_calibration_state = ("idle", "")
        last_mag_calibration_state = ("idle", "", -1)
        last_accel_calibration_state = ("idle", "", -1)
        while not stop_requested:
            now = time.time()
            if now - last_heartbeat >= 0.5:
                publish_status()
                last_heartbeat = now
            if COMMAND_PATH.exists():
                try:
                    command = COMMAND_PATH.read_text(
                        encoding="utf-8"
                    ).strip()

                    if command == "pin":
                        # 先清除指令，避免重複執行
                        COMMAND_PATH.write_text(
                            "",
                            encoding="utf-8"
                        )

                        if connection_mode == "esp32":
                            if (
                                controller.connected_channel
                                is not None
                            ):
                                controller.connection_rumble()

                        else:
                            if controller.connected:
                                controller.connection_rumble()

                    elif command == "calibrate_gyro":
                        COMMAND_PATH.write_text("", encoding="utf-8")
                        connected = (
                            controller.connected_channel is not None
                            if connection_mode == "esp32"
                            else controller.connected
                        )
                        if connected:
                            xinput.start_gyro_calibration()
                            publish_status(
                                gyro_calibration_state="running",
                                gyro_calibration_message="keep_still",
                            )
                        else:
                            publish_status(
                                gyro_calibration_state="failed",
                                gyro_calibration_message="disconnected",
                            )

                    elif command == "calibrate_magnetometer":
                        COMMAND_PATH.write_text("", encoding="utf-8")
                        connected = (
                            controller.connected_channel is not None
                            if connection_mode == "esp32"
                            else controller.connected
                        )
                        if connected:
                            xinput.start_magnetometer_calibration()
                            publish_status(
                                mag_calibration_state="running",
                                mag_calibration_message="move_figure_eight",
                                mag_calibration_progress=0,
                                mag_calibration_spans=[0.0, 0.0, 0.0],
                            )
                        else:
                            publish_status(
                                mag_calibration_state="failed",
                                mag_calibration_message="disconnected",
                            )

                    elif command == "calibrate_accelerometer":
                        COMMAND_PATH.write_text("", encoding="utf-8")
                        connected = (
                            controller.connected_channel is not None
                            if connection_mode == "esp32"
                            else controller.connected
                        )
                        if connected:
                            xinput.start_accelerometer_calibration()
                            publish_status(
                                accel_calibration_state="running",
                                accel_calibration_message="move_and_pause",
                                accel_calibration_progress=0,
                                accel_calibration_quality=None,
                            )
                        else:
                            publish_status(
                                accel_calibration_state="failed",
                                accel_calibration_message="disconnected",
                            )

                    elif command == "reset_tilt_neutral":
                        COMMAND_PATH.write_text("", encoding="utf-8")
                        recentered = xinput.reset_tilt_neutral()
                        publish_status(
                            tilt_recenter_state=(
                                "success" if recentered else "unavailable"
                            ),
                            tilt_recenter_updated_at=time.time(),
                        )

                    elif command:
                        # Clear malformed or obsolete commands too. Otherwise
                        # this loop would keep re-reading the same text.
                        COMMAND_PATH.write_text("", encoding="utf-8")
                        print(tr(f"已忽略未知的控制器指令：{command}", f"Unknown controller command ignored: {command}"))

                except Exception as exc:
                    print(
                        f"處理控制器指令失敗：{exc}"
                    )

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
                try:
                    calibration_config = configparser.ConfigParser()
                    if not calibration_config.read(CONFIG_PATH, encoding="utf-8"):
                        raise FileNotFoundError("config.ini not found")
                    store_gyro_bias(
                        calibration_config,
                        gyro_bias,
                        getattr(controller, "controller_id", None),
                    )
                    atomic_write_config(calibration_config, CONFIG_PATH)
                    print(tr("陀螺儀零點校正已儲存。", "Gyro zero calibration saved."))
                    publish_status(
                        gyro_calibration_state="success",
                        gyro_calibration_message="saved",
                        gyro_calibration_quality=calibration_status.get("quality"),
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
            )
            if mag_key != last_mag_calibration_state:
                last_mag_calibration_state = mag_key
                if mag_key[0] != "success":
                    publish_status(
                        mag_calibration_state=mag_key[0],
                        mag_calibration_message=mag_key[1],
                        mag_calibration_progress=mag_status["progress"],
                        mag_calibration_spans=list(mag_status["spans"]),
                    )

            mag_result = xinput.consume_magnetometer_calibration_result()
            if mag_result is not None:
                try:
                    mag_bias, mag_matrix, mag_quality = mag_result
                    calibration_config = configparser.ConfigParser()
                    if not calibration_config.read(CONFIG_PATH, encoding="utf-8"):
                        raise FileNotFoundError("config.ini not found")
                    store_magnetometer_calibration(
                        calibration_config,
                        mag_bias,
                        (1.0, 1.0, 1.0),
                        getattr(controller, "controller_id", None),
                        matrix=mag_matrix,
                        quality=mag_quality,
                    )
                    atomic_write_config(calibration_config, CONFIG_PATH)
                    print(tr("磁力計三軸校正已儲存。", "3-axis magnetometer calibration saved."))
                    publish_status(
                        mag_calibration_state="success",
                        mag_calibration_message="saved",
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
            )
            if accel_key != last_accel_calibration_state:
                last_accel_calibration_state = accel_key
                publish_status(
                    accel_calibration_state=accel_status["state"],
                    accel_calibration_message=accel_status["message"],
                    accel_calibration_progress=accel_status["progress"],
                )

            accel_result = xinput.consume_accelerometer_calibration_result()
            if accel_result is not None:
                try:
                    accel_bias, accel_matrix, accel_quality = accel_result
                    calibration_config = configparser.ConfigParser()
                    if not calibration_config.read(CONFIG_PATH, encoding="utf-8"):
                        raise FileNotFoundError("config.ini not found")
                    store_accelerometer_calibration(
                        calibration_config,
                        accel_bias,
                        accel_matrix,
                        getattr(controller, "controller_id", None),
                        quality=accel_quality,
                    )
                    atomic_write_config(calibration_config, CONFIG_PATH)
                    print(tr("加速度計多姿態橢球校正已儲存。", "Multi-pose accelerometer calibration saved."))
                    publish_status(
                        accel_calibration_state="success",
                        accel_calibration_message="saved",
                        accel_calibration_progress=100,
                        accel_calibration_quality=accel_quality,
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
    main()
