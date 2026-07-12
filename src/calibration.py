import serial
import time
import threading
import asyncio
import json
import configparser
import serial
from version import APP_TITLE

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


def get_stick_xy(data):
    if len(data) != 3:
        return 2048, 2048

    x = data[0] | ((data[1] & 0x0F) << 8)
    y = ((data[1] >> 4) & 0x0F) | (data[2] << 4)
    return x, y


class CalibrationTracker:
    def __init__(self):
        self.phase = "prepare"
        self.start = time.time()
        self.samples = []
        self.last_countdown = None

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

        lx, ly = get_stick_xy(payload[10:13])
        rx, ry = get_stick_xy(payload[13:16])

        # 連線後先等待 3 秒，讓使用者有時間放開搖桿
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
                print("\n搖桿行程擷取完成。")
                self.print_result()
                exit()

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
        print()
        print("校正結果：")
        print(f"左搖桿中心點：{left_center}")
        print(f"左搖桿正向行程：{left_max}")
        print(f"左搖桿負向行程：{left_min}")
        print(f"右搖桿中心點：{right_center}")
        print(f"右搖桿正向行程：{right_max}")
        print(f"右搖桿負向行程：{right_min}")

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


class Client:
    def __init__(self):
        self.serial = serial.Serial(COM_PORT, BAUDRATE, timeout=0.1)
        self.tracker = CalibrationTracker()
        self.callbacks = {}
        self.connecting = False
        self.connected = False

    def send(self, cmd):
        self.serial.write((cmd + "\n").encode())

    async def start_notify(self, channel, uuid, callback):
        if channel not in self.callbacks:
            self.callbacks[channel] = {}
        self.callbacks[channel][uuid] = callback

    def read_loop(self):
        buf = bytearray()

        while True:
            if self.serial.in_waiting:
                chunk = self.serial.read(self.serial.in_waiting)
                buf.extend(chunk)
            else:
                time.sleep(0.001)
                continue

            while True:
                nl = buf.find(b"\n")
                hdr = buf.find(b"\xaa\x55")

                if nl != -1 and (hdr == -1 or nl < hdr):
                    line = bytes(buf[:nl+1])
                    buf = buf[nl+1:]
                    self.handle_text(line)
                    continue

                if hdr == 0:
                    if len(buf) < 3:
                        break

                    packet_len = buf[2]
                    total = 3 + packet_len

                    if len(buf) < total:
                        break

                    data = bytes(buf[3:total])
                    buf = buf[total:]
                    self.handle_binary(data)
                    continue

                if hdr > 0:
                    buf = buf[hdr:]
                    continue

                break

    def handle_text(self, line):
        try:
            text = line.decode(errors="ignore").strip()
            if not text:
                return

            if "{" in text and "}" in text:
                start = text.find("{")
                end = text.rfind("}")
                obj = json.loads(text[start:end+1])

                # print("EVENT:", obj)

                cmd = obj.get("cmd")

                if cmd == "scan_result":
                    # 已經正在連線或已連線時，忽略後續掃描結果
                    if self.connecting or self.connected:
                        return

                    mac = obj.get("mac")
                    addr_type = int(obj.get("type", 0))

                    if mac:
                        self.connecting = True
                        print(f"已找到控制器：{mac}")

                        # 找到第一個控制器後停止掃描，避免重複連線
                        self.send("scan off")
                        self.send(f"conn {addr_type} {mac}")

                elif cmd == "connected":
                    self.connecting = False
                    self.connected = True

                    channel = int(obj["channel"])
                    print(f"控制器連線成功，ESP32 通道：{channel}")
                    print("請放開左右搖桿並保持不動，3 秒後開始校正...")

                    asyncio.run(
                        self.start_notify(
                            channel,
                            INPUT_UUID_PREFIX,
                            self.input_callback
                        )
                    )
        except:
            pass

    def handle_binary(self, data):
        if not data:
            return

        chan_id = data[0] & 0x7F
        if 1 <= chan_id <= MAX_ESP32S3_CHANNELS:
            payload = data[1:]
            self.input_callback(None, bytearray(payload))

    def input_callback(self, _, payload):
        self.tracker.update(payload)

    def run(self):
        thread = threading.Thread(target=self.read_loop, daemon=True)
        thread.start()

        self.send("auto off")
        self.send("ble disconnect")
        self.send("scan on")

        print("正在搜尋 Switch 2 Pro Controller...")
        print("請按下手把任意按鍵開啟手把並等待連線。")

        while True:
            time.sleep(1)


if __name__ == "__main__":
    print("========================================")
    print(f"   {APP_TITLE} Stick Cal.")
    print("========================================")
    print()

    try:
        Client().run()

    except serial.SerialException:
        print()
        print(f"無法開啟 {COM_PORT}。")
        print("此連接埠可能正在被其他程式使用。")
        print("請先關閉主程式或其他正在使用 ESP32 的程式。")
        input("\n按 Enter 鍵關閉...")