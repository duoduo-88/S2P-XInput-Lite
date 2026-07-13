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
import serial
import json
import serial.tools.list_ports
from version import APP_TITLE

COMMAND_PATH = Path(__file__).with_name(
    "controller_command.txt"
)

def find_esp32_port():
    """自動尋找執行相容 Bridge 韌體的 ESP32-S3。"""

    ports = list(
        serial.tools.list_ports.comports()
    )

    for port_info in ports:
        port = port_info.device

        try:
            with serial.Serial(
                port,
                2_000_000,
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
                            data.get("cmd") == "status"
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
    config = configparser.ConfigParser()

    if not config.read("config.ini", encoding="utf-8"):
        raise FileNotFoundError("config.ini not found")

    print("========================================")
    print(f"         {APP_TITLE}")
    print("========================================")
    print()


    def read_pair(key):
        value = config.get("sticks", key)
        x, y = value.split(",")
        return int(x.strip()), int(y.strip())

    # 從 config.ini 讀取搖桿校正資料
    custom_stick_cal = {
        "left": {
            "center": read_pair("left_center"),
            "max": read_pair("left_max"),
            "min": read_pair("left_min"),
        },
        "right": {
            "center": read_pair("right_center"),
            "max": read_pair("right_max"),
            "min": read_pair("right_min"),
        },
    }

    print("正在自動偵測 ESP32...")

    port = find_esp32_port()

    if port is not None:
        print(
            f"已自動偵測到 ESP32：{port}"
        )

        connection_mode = "esp32"

    else:
        print()
        print(
            "未偵測到 ESP32，"
            "將使用 Windows 原生藍牙。"
        )
        print()

        connection_mode = "bluetooth"
    channel = config.getint(
        "serial",
        "channel",
        fallback=0
    )

    try:
        xinput = XInputController(
            config,
            custom_stick_cal
        )

    except Exception as exc:
        print()
        print("無法建立 Xbox 虛擬控制器。")
        print(
            "請確認 ViGEmBus 驅動程式"
            "已正確安裝。"
        )
        print()
        print(f"錯誤資訊：{exc}")

        input("\n按 Enter 鍵關閉...")
        return

    baudrate = config.getint(
        "serial",
        "baudrate",
        fallback=2_000_000
    )

    if connection_mode == "esp32":
        controller = ESP32Bridge(
            port,
            baudrate,
            channel
        )

        # ESP32 路徑目前維持完整震動功能
        xinput.set_rumble_sender(
            controller.send_pro_rumble
        )

    else:
        controller = BluetoothController()

        # 原生藍牙共用原本的
        # XInput → Pro Rumble 轉換算法
        xinput.set_rumble_sender(
            controller.send_pro_rumble
        )

    def on_input(payload):
        state = parse_input_report(
            payload
        )

        if state is not None:
            xinput.update(
                state
            )

    controller.input_callback = on_input

    if connection_mode == "esp32":

        print(
            f"正在連接 ESP32：{port}"
        )

        try:
            controller.open()

        except serial.SerialException:
            print()
            print(
                f"無法開啟 {port}。"
            )
            print(
                "此連接埠可能正在被其他程式使用。"
            )
            print(
                "請關閉原本的連接程式"
                "或校正程式後再試。"
            )

            input(
                "\n按 Enter 鍵關閉..."
            )
            return

        print(
            "ESP32 連接成功。"
        )
        print()

        print(
            "正在搜尋 Switch 2 Pro Controller..."
        )
        print(
            "已配對的手把請按任意按鍵喚醒；第一次配對請按住 SYNC。"
        )

        controller.start_scan()

    else:

        print(
            "正在啟動 Windows 原生藍牙..."
        )
        print(
            "請按下手把任意按鍵"
            "開啟手把並等待連線。"
        )
        print()

        controller.open()

    stop_requested = False

    def handle_shutdown_signal(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(
        signal.SIGBREAK,
        handle_shutdown_signal
    )

    try:
        while not stop_requested:
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

                except Exception as exc:
                    print(
                        f"處理控制器指令失敗：{exc}"
                    )

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n正在關閉程式...")

    finally:

        # 先釋放所有可能仍保持按下的鍵盤按鍵
        xinput.release_all_keyboard_buttons()

        # 再關閉連接
        controller.close()


if __name__ == "__main__":
    main()
