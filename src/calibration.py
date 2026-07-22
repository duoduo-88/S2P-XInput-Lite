import serial
import time
import configparser
import msvcrt
from version import APP_TITLE
from esp32_bridge import ESP32Bridge
from esp32_detection import find_esp32_port
from bluetooth_controller import BluetoothController
from wired_controller import WiredController, find_wired_controller
from config_utils import (
    CONFIG_PATH,
    atomic_write_config,
    config_file_lock,
    load_config,
    store_stick_calibration,
)
from console_i18n import current_language
from console_i18n import localized_print as print
from console_i18n import localized_input as input


CONFIG_FILE = CONFIG_PATH
config = load_config(CONFIG_FILE)
def tr(zh, en):
    return en if current_language() == "en" else zh

def get_stick_xy(data):
    if len(data) != 3:
        return 2048, 2048

    x = data[0] | ((data[1] & 0x0F) << 8)
    y = ((data[1] >> 4) & 0x0F) | (data[2] << 4)
    return x, y


class CalibrationTracker:
    # A fixed "units per report" threshold changes behavior with report rate.
    # Use units/second and retain a small absolute floor for stationary noise.
    MOVE_SPEED_THRESHOLD = 1200.0
    MIN_MOVE_DISTANCE = 3.0
    REQUIRED_MOVEMENT_SECONDS = 10.0
    MIN_REASONABLE_RANGE = 500

    def __init__(self, controller_id=None):
        self.controller_id = controller_id
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
        self.left_move_elapsed = 0.0
        self.right_move_elapsed = 0.0
        self._last_range_time = None
        self._previous_left = None
        self._previous_right = None

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
                    print(tr("已偵測到按鍵。", "Button detected."))
                    print(tr("請放開按鍵與左右搖桿。", "Release the button and both sticks."))

            else:
                # 等到所有按鍵放開後才開始倒數
                if buttons == 0:
                    self.phase = "prepare"
                    self.start = time.time()

                    print()
                    print(tr(
                        "請保持左右搖桿完全不動，3 秒後開始擷取中心點。",
                        "Keep both sticks still. Center capture starts in 3 seconds.",
                    ))

            return

        # 按鍵放開後先等待 3 秒
        if self.phase == "prepare":
            elapsed = time.time() - self.start

            if elapsed >= 3:
                self.phase = "center"
                self.start = time.time()
                self.last_countdown = None
                print(tr("開始擷取搖桿中心點。", "Capturing stick centers."))

        # 擷取搖桿中心點 3 秒
        elif self.phase == "center":
            self.samples.append((lx, ly, rx, ry))

            elapsed = time.time() - self.start
            remaining = max(0, 3 - int(elapsed))

            if remaining != self.last_countdown:
                self.last_countdown = remaining
                print(
                    tr(
                        f"\r請保持左右搖桿完全不動... {remaining} 秒",
                        f"\rKeep both sticks still... {remaining} sec",
                    ),
                    end="",
                    flush=True
                )

            if elapsed >= 3:
                print(tr("\n中心點擷取完成。", "\nCenter capture complete."))
                print(tr(
                    "請將左右搖桿沿著外圈完整旋轉。",
                    "Rotate both sticks fully around their outer edges.",
                ))

                self.phase = "range"
                self.last_countdown = None
                self._last_range_time = time.monotonic()
                self._previous_left = (lx, ly)
                self._previous_right = (rx, ry)

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

            now = time.monotonic()
            dt = min(0.1, max(0.0, now - self._last_range_time))
            self._last_range_time = now

            left_distance = (
                abs(lx - self._previous_left[0])
                + abs(ly - self._previous_left[1])
            )
            right_distance = (
                abs(rx - self._previous_right[0])
                + abs(ry - self._previous_right[1])
            )
            movement_threshold = max(
                self.MIN_MOVE_DISTANCE,
                self.MOVE_SPEED_THRESHOLD * dt,
            )
            if left_distance >= movement_threshold:
                self.left_move_elapsed += dt
            if right_distance >= movement_threshold:
                self.right_move_elapsed += dt
            self._previous_left = (lx, ly)
            self._previous_right = (rx, ry)

            left_remaining = max(
                0,
                int(self.REQUIRED_MOVEMENT_SECONDS - self.left_move_elapsed + 0.999),
            )
            right_remaining = max(
                0,
                int(self.REQUIRED_MOVEMENT_SECONDS - self.right_move_elapsed + 0.999),
            )
            remaining = (left_remaining, right_remaining)

            if remaining != self.last_countdown:
                self.last_countdown = remaining
                print(
                    tr(
                        "\r請持續完整旋轉左右搖桿... "
                        f"左 {left_remaining} 秒 / 右 {right_remaining} 秒",
                        "\rKeep rotating both sticks fully... "
                        f"Left {left_remaining} sec / Right {right_remaining} sec",
                    ),
                    end="",
                    flush=True
                )

            if (
                self.left_move_elapsed >= self.REQUIRED_MOVEMENT_SECONDS
                and self.right_move_elapsed >= self.REQUIRED_MOVEMENT_SECONDS
            ):
                # 必須先切換成 done，
                # 防止後續 Input Report 重複執行完成流程
                self.phase = "saving"

                print()
                print(tr("搖桿行程擷取完成。", "Stick range capture complete."))

                if self.print_result():
                    self.phase = "done"
                else:
                    self._reset_for_retry()

    def _reset_for_retry(self):
        """Discard a rejected capture and allow another calibration in-place."""
        self.phase = "wait_button"
        self.start = None
        self.samples = []
        self.last_countdown = None
        self.waiting_button_release = False
        self.lx_min = self.ly_min = self.rx_min = self.ry_min = 99999
        self.lx_max = self.ly_max = self.rx_max = self.ry_max = 0
        self.left_move_elapsed = 0.0
        self.right_move_elapsed = 0.0
        self._last_range_time = None
        self._previous_left = None
        self._previous_right = None
        print()
        print(tr(
            "校正未儲存。請按手把任意按鍵重新校正。",
            "Calibration was not saved. Press any controller button to retry.",
        ))

    def print_result(self):
        if not self.samples:
            print(tr(
                "校正失敗：沒有收到搖桿中心取樣資料。",
                "Calibration failed: no stick-center samples were received.",
            ))
            return False

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

        all_ranges = (
            left_max
            + left_min
            + right_max
            + right_min
        )

        centers = (cx_l, cy_l, cx_r, cy_r)
        invalid_center = any(not 512 <= value <= 3583 for value in centers)
        invalid_range = any(
            value < self.MIN_REASONABLE_RANGE or value > 4095
            for value in all_ranges
        )
        if invalid_center or invalid_range:
            print()
            print(tr(
                "校正失敗：中心點或搖桿行程不在合理範圍內。",
                "Calibration failed: a center or stick range is invalid.",
            ))
            print(tr(
                f"每個方向至少需要 {self.MIN_REASONABLE_RANGE} 個原始單位，請重新完整旋轉左右搖桿。",
                f"Each direction needs at least {self.MIN_REASONABLE_RANGE} raw units. Rotate both sticks fully and retry.",
            ))
            return False

        # 顯示校正結果
        # print()
        # print("校正結果：")
        # print(f"左搖桿中心點：{left_center}")
        # print(f"左搖桿正向行程：{left_max}")
        # print(f"左搖桿負向行程：{left_min}")
        # print(f"右搖桿中心點：{right_center}")
        # print(f"右搖桿正向行程：{right_max}")
        # print(f"右搖桿負向行程：{right_min}")

        calibration = {
            "left": {
                "center": left_center,
                "max": left_max,
                "min": left_min,
            },
            "right": {
                "center": right_center,
                "max": right_max,
                "min": right_min,
            },
        }
        try:
            # Reload immediately before the atomic write. The settings GUI may
            # remain open during calibration, so using the module-startup copy
            # could overwrite changes saved while the calibration was running.
            with config_file_lock():
                latest_config = load_config(CONFIG_FILE)
                store_stick_calibration(
                    latest_config, calibration, self.controller_id
                )
                atomic_write_config(latest_config, CONFIG_FILE)
        except (OSError, ValueError, configparser.Error) as exc:
            print()
            print(tr(f"無法儲存校正資料：{exc}", f"Could not save calibration data: {exc}"))
            return False

        print()
        print(tr(
            "校正完成，校正資料已儲存至 config.ini。",
            "Calibration complete. Data was saved to config.ini.",
        ))
        if self.controller_id:
            print(tr(
                f"控制器專屬校正：{self.controller_id}",
                f"Controller-specific calibration: {self.controller_id}",
            ))
        print(tr(
            "現在可以關閉此程式，並按下「重新啟動連接程式」按鈕連接控制器。",
            "You may close this window and restart the connection program.",
        ))

        return True


class CalibrationApp:

    def __init__(self):
        self.controller = None
        self.tracker = None
        self.running = True
        self.controller_ready = False
        self.bridge_disconnected = False
        self.bluetooth_unavailable = False

    def _create_tracker(self):
        controller_id = getattr(self.controller, "controller_id", None)
        self.tracker = CalibrationTracker(controller_id=controller_id)

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
        print(tr("控制器已連線。", "Controller connected."))
        print(tr(
            "準備好後，請按下手把任意按鍵開始校正。",
            "When ready, press any controller button to begin calibration.",
        ))

    def _on_controller_disconnected(self):
        self.controller_ready = False
        self.tracker = None

    def _on_bridge_disconnected(self):
        self.controller_ready = False
        self.tracker = None
        self.bridge_disconnected = True

    def _on_bluetooth_unavailable(self):
        self.controller_ready = False
        self.tracker = None
        self.bluetooth_unavailable = True

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
        print("> ", end="", flush=True)

        while self.running:
            # ESP32 本體已從 USB 中斷
            if self.bridge_disconnected:
                print()
                return "back"

            if self.bluetooth_unavailable:
                print()
                return "back"

            if msvcrt.kbhit():
                command = msvcrt.getwch().lower()

                # 顯示使用者輸入的字元
                print(command)

                if command == "b":
                    return "back"

                if command == "q":
                    return "quit"

            time.sleep(0.05)

        return "quit"

    def _run_esp32(self):
        self.bridge_disconnected = False
        self.bluetooth_unavailable = False
        
        print()
        print("========================================")
        print(tr("              ESP32 模式", "              ESP32 Mode"))
        print("========================================")
        print()

        print(tr("正在自動偵測 ESP32...", "Detecting ESP32 automatically..."))

        port = find_esp32_port(
            config.getint("serial", "baudrate", fallback=2_000_000)
        )

        if port is None:
            print()
            print(tr("未偵測到 ESP32。", "ESP32 not detected."))
            print()

            return

        print(
            tr(f"已自動偵測到 ESP32：{port}", f"ESP32 detected automatically: {port}")
        )

        runtime_config = configparser.ConfigParser()

        if not runtime_config.read(
            CONFIG_FILE,
            encoding="utf-8"
        ):
            raise FileNotFoundError(
                "config.ini not found"
            )

        baudrate = runtime_config.getint(
            "serial",
            "baudrate",
            fallback=2_000_000
        )

        self.controller = ESP32Bridge(
            port,
            baudrate
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

        self.controller.bridge_disconnected_callback = (
            self._on_bridge_disconnected
        )

        print(
            tr(f"正在連接 ESP32：{port}", f"Connecting to ESP32: {port}")
        )

        self.controller.open()
        self.controller.start_scan()

        print(tr("ESP32 連接成功。", "ESP32 connected."))
        print()
  
        print(tr("輸入 B 返回連線模式選擇。", "Enter B to return to connection mode selection."))
        print(tr("輸入 Q 關閉校正程式。", "Enter Q to close the calibration program."))
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

        result = self._wait_for_return()

        if result == "back":
            print()
            print(tr("正在結束 ESP32 模式，請稍候...", "Closing ESP32 mode, please wait..."))

        self._close_controller()

        return result

    def _run_bluetooth(self):
        self.bridge_disconnected = False
        self.bluetooth_unavailable = False
        
        print()
        print("========================================")
        print(tr("         Windows 原生藍牙模式", "       Windows Native Bluetooth Mode"))
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

        self.controller.bluetooth_unavailable_callback = (
            self._on_bluetooth_unavailable
        )

        print(
            tr("正在啟動 Windows 原生藍牙...", "Starting Windows native Bluetooth...")
        )
        print(
            tr(
                "已配對的手把請按任意按鍵喚醒；第一次配對請按住 SYNC。",
                "Press any button to wake a paired controller; hold SYNC for first-time pairing.",
            )
        )
        print()

        print(tr("輸入 B 返回連線模式選擇。", "Enter B to return to connection mode selection."))
        print(tr("輸入 Q 關閉校正程式。", "Enter Q to close the calibration program."))
        print()

        self.controller.open()

        result = self._wait_for_return()

        if result == "back":
            print()
            print(tr(
                "正在結束 Windows 原生藍牙模式，請稍候...",
                "Closing Windows native Bluetooth mode, please wait...",
            ))

        self._close_controller()

        return result

    def _run_wired(self):
        self.bridge_disconnected = False
        self.bluetooth_unavailable = False

        print()
        print("========================================")
        print(tr("          USB 有線模式", "          Wired USB Mode"))
        print("========================================")
        print()
        print(tr("正在偵測 USB 有線手把...", "Detecting a wired USB controller..."))
        entry = find_wired_controller()
        if entry is None:
            print(tr(
                "未偵測到 USB 有線 Switch 2 Pro Controller。",
                "No wired USB Switch 2 Pro Controller was detected.",
            ))
            return

        print(tr(
            "提示：請先完成本程式的 USB 連線，再開啟 Steam、reWASD 或其他手把工具。",
            "Tip: Let this program establish the USB connection before opening Steam, reWASD, or other controller tools.",
        ))
        print(tr(
            "若其他工具已先開啟，請完全退出後重新插拔手把。",
            "If another tool was already open, fully exit it and reconnect the controller.",
        ))

        self.controller = WiredController(entry)
        self.controller.input_callback = self._on_input
        self.controller.connected_callback = self._on_controller_ready
        self.controller.disconnected_callback = self._on_controller_disconnected

        print(tr("正在啟動 USB 有線連線...", "Starting wired USB connection..."))
        self.controller.open()
        print(tr("輸入 B 返回連線模式選擇。", "Enter B to return to connection mode selection."))
        print(tr("輸入 Q 關閉校正程式。", "Enter Q to close the calibration program."))
        print()

        result = self._wait_for_return()
        if result == "back":
            print(tr(
                "正在結束 USB 有線模式，請稍候...",
                "Closing wired USB mode, please wait...",
            ))
        self._close_controller()
        return result

    def run(self):

        while self.running:

            print()
            print("========================================")
            print(tr("          選擇校正連線模式", "      Select Calibration Connection"))
            print("========================================")
            print()
            print(tr("1. USB 有線", "1. Wired USB"))
            print("2. ESP32")
            print(tr("3. Windows 原生藍牙", "3. Windows native Bluetooth"))
            print(tr("Q. 關閉", "Q. Quit"))
            print()

            choice = input(tr("請選擇：", "Select: ")).strip().lower()

            try:
                if choice == "1":
                    result = self._run_wired()

                    if result == "quit":
                        break

                elif choice == "2":
                    result = self._run_esp32()

                    if result == "quit":
                        break

                elif choice == "3":
                    result = self._run_bluetooth()

                    if result == "quit":
                        break

                elif choice == "q":
                    break

                else:
                    print()
                    print(tr("無效的選擇，請重新輸入。", "Invalid selection. Please try again."))

            except serial.SerialException:
                print()
                print(
                    tr("無法開啟 ESP32 連接埠。", "Could not open the ESP32 serial port.")
                )
                print(
                    tr(
                        "此連接埠可能正在被其他程式使用。",
                        "The serial port may be in use by another program.",
                    )
                )

                self._close_controller()

            except Exception as exc:
                print()
                print(
                    tr(f"校正連線錯誤：{exc}", f"Calibration connection error: {exc}")
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
