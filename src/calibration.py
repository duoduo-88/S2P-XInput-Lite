import serial
import serial.tools.list_ports
import time
import threading
import asyncio
import json
import configparser
from version import APP_TITLE
from esp32_bridge import ESP32Bridge
from bluetooth_controller import BluetoothController


CONFIG_FILE = "config.ini"

config = configparser.ConfigParser()

if not config.read("config.ini", encoding="utf-8"):
    raise FileNotFoundError("config.ini not found")

COM_PORT = config.get(
    "serial",
    "port",
    fallback="COM3"
)

BAUDRATE = config.getint(
    "serial",
    "baudrate",
    fallback=2000000
)
MAX_ESP32S3_CHANNELS = 8
INPUT_UUID_PREFIX = "ab7de9be"

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
                            and data.get("profile")
                            == "tinyusb_direct"
                            and data.get("build")
                            == "cdc_bridge_1"
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


def get_stick_xy(data):
    if len(data) != 3:
        return 2048, 2048

    x = data[0] | ((data[1] & 0x0F) << 8)
    y = ((data[1] >> 4) & 0x0F) | (data[2] << 4)
    return x, y


class CalibrationTracker:
    def __init__(self):
        self.phase = "wait_button"
        self.start = None
        self.samples = []
        self.last_countdown = None

        self.waiting_button_release = False

        self.lx_min = 99999
        self.lx_max = 0
        self.ly_min = 99999
        self.ly_max = 0

        self.rx_min = 99999
        self.rx_max = 0
        self.ry_min = 99999
        self.ry_max = 0

    def update(self, payload):
        if len(payload) < 16:
            return
            
        if self.phase == "done":
            return

        buttons = int.from_bytes(
            payload[4:8],
            "little",
            signed=False
        )

        lx, ly = get_stick_xy(payload[10:13])
        rx, ry = get_stick_xy(payload[13:16])

        # 等待使用者按下任意按鍵
        if self.phase == "wait_button":

            if not self.waiting_button_release:

                if buttons != 0:
                    self.waiting_button_release = True

                    print()
                    print("已偵測到按鍵。")
                    print("請放開按鍵與左右搖桿。")

            else:
                # 等到所有按鍵放開後才開始倒數
                if buttons == 0:
                    self.phase = "prepare"
                    self.start = time.time()

                    print()
                    print(
                        "請保持左右搖桿完全不動，"
                        "3 秒後開始擷取中心點。"
                    )

            return

        # 按鍵放開後先等待 3 秒
        if self.phase == "prepare":
            elapsed = time.time() - self.start

            if elapsed >= 3:
                self.phase = "center"
                self.start = time.time()
                self.last_countdown = None
                print("開始擷取搖桿中心點。")

        # 擷取搖桿中心點 3 秒
        elif self.phase == "center":
            self.samples.append((lx, ly, rx, ry))

            elapsed = time.time() - self.start
            remaining = max(0, 3 - int(elapsed))

            if remaining != self.last_countdown:
                self.last_countdown = remaining
                print(
                    f"\r請保持左右搖桿完全不動... {remaining} 秒",
                    end="",
                    flush=True
                )

            if elapsed >= 3:
                print("\n中心點擷取完成。")
                print("請將左右搖桿沿著外圈完整旋轉。")

                self.phase = "range"
                self.start = time.time()
                self.last_countdown = None

        # 擷取搖桿最大行程 10 秒
        elif self.phase == "range":
            self.lx_min = min(self.lx_min, lx)
            self.lx_max = max(self.lx_max, lx)
            self.ly_min = min(self.ly_min, ly)
            self.ly_max = max(self.ly_max, ly)

            self.rx_min = min(self.rx_min, rx)
            self.rx_max = max(self.rx_max, rx)
            self.ry_min = min(self.ry_min, ry)
            self.ry_max = max(self.ry_max, ry)

            elapsed = time.time() - self.start
            remaining = max(0, 10 - int(elapsed))

            if remaining != self.last_countdown:
                self.last_countdown = remaining
                print(
                    f"\r請持續完整旋轉左右搖桿... {remaining} 秒",
                    end="",
                    flush=True
                )

            if elapsed >= 10:
                # 必須先切換成 done，
                # 防止後續 Input Report 重複執行完成流程
                self.phase = "done"

                print()
                print("搖桿行程擷取完成。")

                self.print_result()

    def print_result(self):
        cx_l = int(
            sum(x[0] for x in self.samples)
            / len(self.samples)
        )
        cy_l = int(
            sum(x[1] for x in self.samples)
            / len(self.samples)
        )
        cx_r = int(
            sum(x[2] for x in self.samples)
            / len(self.samples)
        )
        cy_r = int(
            sum(x[3] for x in self.samples)
            / len(self.samples)
        )

        # 計算校正數據
        left_center = (cx_l, cy_l)
        left_max = (
            self.lx_max - cx_l,
            self.ly_max - cy_l
        )
        left_min = (
            cx_l - self.lx_min,
            cy_l - self.ly_min
        )

        right_center = (cx_r, cy_r)
        right_max = (
            self.rx_max - cx_r,
            self.ry_max - cy_r
        )
        right_min = (
            cx_r - self.rx_min,
            cy_r - self.ry_min
        )

        # 顯示校正結果
        # print()
        # print("校正結果：")
        # print(f"左搖桿中心點：{left_center}")
        # print(f"左搖桿正向行程：{left_max}")
        # print(f"左搖桿負向行程：{left_min}")
        # print(f"右搖桿中心點：{right_center}")
        # print(f"右搖桿正向行程：{right_max}")
        # print(f"右搖桿負向行程：{right_min}")

        # 更新 config.ini
        config.set(
            "sticks",
            "left_center",
            f"{left_center[0]}, {left_center[1]}"
        )
        config.set(
            "sticks",
            "left_max",
            f"{left_max[0]}, {left_max[1]}"
        )
        config.set(
            "sticks",
            "left_min",
            f"{left_min[0]}, {left_min[1]}"
        )

        config.set(
            "sticks",
            "right_center",
            f"{right_center[0]}, {right_center[1]}"
        )
        config.set(
            "sticks",
            "right_max",
            f"{right_max[0]}, {right_max[1]}"
        )
        config.set(
            "sticks",
            "right_min",
            f"{right_min[0]}, {right_min[1]}"
        )

        with open(
            "config.ini",
            "w",
            encoding="utf-8"
        ) as config_file:
            config.write(config_file)

        print()
        print("校正完成，校正資料已儲存至 config.ini。")
        print("現在可以關閉此程式，並按下「重啓連接程式」按鈕連接控制器。")

class CalibrationApp:

    def __init__(self):
        self.controller = None
        self.tracker = None
        self.running = True
        self.controller_ready = False

    def _create_tracker(self):
        self.tracker = CalibrationTracker()

    def _on_input(self, payload):
        if (
            self.controller_ready
            and self.tracker is not None
        ):
            self.tracker.update(payload)

    def _on_controller_ready(self):
        self._create_tracker()
        self.controller_ready = True

        print()
        print("控制器已連線。")
        print(
            "準備好後，請按下手把任意按鍵開始校正。"
        )

    def _on_controller_disconnected(self):
        self.controller_ready = False
        self.tracker = None

    def _close_controller(self):
        # 先停止校正資料處理
        self.controller_ready = False
        self.tracker = None

        if self.controller is None:
            return

        try:
            self.controller.close()
        except Exception:
            pass

        self.controller = None

    def _wait_for_return(self):
        while self.running:
            command = input("> ").strip().lower()

            if command == "b":
                return "back"

            if command == "q":
                return "quit"

        return "quit"

    def _run_esp32(self):
        print()
        print("========================================")
        print("              ESP32 模式")
        print("========================================")
        print()

        print("正在自動偵測 ESP32...")

        port = find_esp32_port()

        if port is None:
            print()
            print("未偵測到 ESP32。")
            print()

            return

        print(
            f"已自動偵測到 ESP32：{port}"
        )

        config = configparser.ConfigParser()

        if not config.read(
            CONFIG_FILE,
            encoding="utf-8"
        ):
            raise FileNotFoundError(
                "config.ini not found"
            )

        baudrate = config.getint(
            "serial",
            "baudrate",
            fallback=2_000_000
        )

        channel = config.getint(
            "serial",
            "channel",
            fallback=0
        )

        self.controller = ESP32Bridge(
            port,
            baudrate,
            channel
        )

        self.controller.calibration_mode = True

        self.controller.input_callback = (
            self._on_input
        )

        self.controller.ready_callback = (
            self._on_controller_ready
        )

        self.controller.disconnected_callback = (
            self._on_controller_disconnected
        )


        print(
            f"正在連接 ESP32：{port}"
        )

        self.controller.open()

        print("ESP32 連接成功。")
        print()
  
        print("輸入 B 返回連線模式選擇。")
        print("輸入 Q 關閉校正程式。")
        print()
                
        print(
            "正在搜尋 Switch 2 Pro Controller..."
        )
        print(
            "已配對的手把請按任意按鍵喚醒；"
            "第一次配對請按住 SYNC。"
        )

        result = self._wait_for_return()

        if result == "back":
            print()
            print("正在結束 ESP32 模式，請稍候...")

        self._close_controller()

        return result

    def _run_bluetooth(self):
        print()
        print("========================================")
        print("         Windows 原生藍牙模式")
        print("========================================")
        print()

        self.controller = BluetoothController()

        self.controller.calibration_mode = True

        self.controller.input_callback = (
            self._on_input
        )

        self.controller.ready_callback = (
            self._on_controller_ready
        )

        self.controller.disconnected_callback = (
            self._on_controller_disconnected
        )

        print(
            "正在啟動 Windows 原生藍牙..."
        )
        print(
            "已配對的手把請按任意按鍵喚醒；"
            "第一次配對請按住 SYNC。"
        )
        print()

        print("輸入 B 返回連線模式選擇。")
        print("輸入 Q 關閉校正程式。")
        print()

        self.controller.open()

        result = self._wait_for_return()

        if result == "back":
            print()
            print("正在結束 Windows 原生藍牙模式，請稍候...")

        self._close_controller()

        return result

    def run(self):

        while self.running:

            print()
            print("========================================")
            print("          選擇校正連線模式")
            print("========================================")
            print()
            print("1. ESP32")
            print("2. Windows 原生藍牙")
            print("Q. 關閉")
            print()

            choice = input(
                "請選擇："
            ).strip().lower()

            try:
                if choice == "1":
                    result = self._run_esp32()

                    if result == "quit":
                        break

                elif choice == "2":
                    result = self._run_bluetooth()

                    if result == "quit":
                        break

                elif choice == "q":
                    break

                else:
                    print()
                    print("無效的選擇，請重新輸入。")

            except serial.SerialException:
                print()
                print(
                    "無法開啟 ESP32 連接埠。"
                )
                print(
                    "此連接埠可能正在被其他程式使用。"
                )

                self._close_controller()

            except Exception as exc:
                print()
                print(
                    f"校正連線錯誤：{exc}"
                )

                self._close_controller()

        self.running = False
        self._close_controller()

if __name__ == "__main__":
    print("========================================")
    print(f"   {APP_TITLE} Stick Cal.")
    print("========================================")
    print()

    try:
        CalibrationApp().run()

    except KeyboardInterrupt:
        pass