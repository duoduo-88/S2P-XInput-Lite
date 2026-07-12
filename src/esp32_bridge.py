\
import asyncio
import json
import serial
import threading
import time


class ESP32Bridge:
    """Minimal USB-CDC client compatible with the working calibration tool."""

    def __init__(self, port, baudrate=2_000_000, channel=0):
        self.port = port
        self.baudrate = baudrate
        self.channel = channel
        self.serial = None
        self.input_callback = None
        self.running = False
        self.connected_channel = None
        self._connecting = False
        self._write_lock = threading.Lock()
        self._rumble_packet_id = 0

    def open(self):
        self.serial = serial.Serial(self.port, self.baudrate, timeout=0.1)
        self.running = True
        threading.Thread(target=self._read_loop, daemon=True).start()

    def close(self):
        self.running = False
        if self.serial and self.serial.is_open:
            self.serial.close()

    def send(self, command):
        if not self.serial:
            return
        with self._write_lock:
            self.serial.write((command.rstrip("\n") + "\n").encode("utf-8"))

    @staticmethod
    def _encode_vibration(lf_freq, lf_amp, hf_freq, hf_amp):
        """Encode one HD Rumble 2 VibrationData frame (5 bytes)."""
        value = 0
        value |= int(lf_freq) & 0x1FF
        value |= (int(lf_amp) & 0x3FF) << 10
        value |= (int(hf_freq) & 0x1FF) << 20
        value |= (int(hf_amp) & 0x3FF) << 30
        return value.to_bytes(5, byteorder="little")

    def send_pro_rumble(self, lf_freq, lf_amp, hf_freq, hf_amp):
        """Send one Pro Controller rumble report through Tommy's ESP32 'wr' protocol."""
        channel = self.connected_channel
        if channel is None:
            return False

        vibration = self._encode_vibration(
            lf_freq, lf_amp, hf_freq, hf_amp
        )

        packet_id = 0x50 + (self._rumble_packet_id & 0x0F)
        self._rumble_packet_id = (self._rumble_packet_id + 1) & 0x0F

        # Original 0.0.1 Pro Controller format:
        # 0x00 + (packet-id + 3 vibration frames) duplicated for L/R motors.
        motor_vibrations = (
            bytes([packet_id])
            + vibration
            + vibration
            + vibration
        )
        payload = b"\x00" + motor_vibrations + motor_vibrations
        command = f"wr {int(channel)} r {payload.hex()}"

        self.send(command)
        return True

    def set_player_led_1(self):
        """將 Switch 2 Pro Controller 設定為 Player 1 LED。"""
        channel = self.connected_channel
        if channel is None:
            return False

        # Tommy write_command() 格式：
        # command_id
        # 91 01
        # subcommand_id
        # 00
        # command_data 長度
        # 00 00
        # command_data

        command_id = 0x09
        subcommand_id = 0x07

        # Player 1 LED pattern
        command_data = bytes([
            0x01,
            0x00,
            0x00,
            0x00,
        ])

        payload = (
            bytes([command_id])
            + b"\x91\x01"
            + bytes([subcommand_id])
            + b"\x00"
            + bytes([len(command_data)])
            + b"\x00\x00"
            + command_data
        )

        command = f"wr {int(channel)} c {payload.hex()}"
        self.send(command)

        print("已送出 Player 1 LED 設定。")
        return True

    def connection_rumble(self):
        """連線成功時播放兩次短震動提示。"""
        if self.connected_channel is None:
            return

        def worker():
            # 短脈衝參數
            lf_freq = 225
            hf_freq = 350
            lf_amp = 800
            hf_amp = 800

            # 第一次短震
            self.send_pro_rumble(
                lf_freq,
                lf_amp,
                hf_freq,
                hf_amp
            )
            time.sleep(0.08)

            # 停止
            self.send_pro_rumble(
                lf_freq,
                0,
                hf_freq,
                0
            )
            time.sleep(0.10)

            # 第二次短震
            self.send_pro_rumble(
                lf_freq,
                lf_amp,
                hf_freq,
                hf_amp
            )
            time.sleep(0.08)

            # 停止
            self.send_pro_rumble(
                lf_freq,
                0,
                hf_freq,
                0
            )
            
            print()
            print("控制器已準備完成，可以開始使用。")
            print()
            print("提醒：連線期間請勿關閉此視窗。")
            print("若需要中斷連線，請關閉此視窗。")
            print()
            print("如需校正搖桿，請先關閉本程式，再按下「校正搖桿」按鈕。")
            print("接著依照校正程序的畫面提示操作即可。")
            print()

        threading.Thread(
            target=worker,
            daemon=True
        ).start()


    def start_scan(self):
        self.send("auto off")
        self.send("ble disconnect")
        self.send("scan on")

    def _read_loop(self):
        buf = bytearray()
        while self.running:
            if self.serial.in_waiting:
                buf.extend(self.serial.read(self.serial.in_waiting))
            else:
                time.sleep(0.001)
                continue

            while True:
                nl = buf.find(b"\n")
                hdr = buf.find(b"\xaa\x55")

                if nl != -1 and (hdr == -1 or nl < hdr):
                    line = bytes(buf[:nl + 1])
                    del buf[:nl + 1]
                    self._handle_text(line)
                    continue

                if hdr == 0:
                    if len(buf) < 3:
                        break
                    packet_len = buf[2]
                    total = 3 + packet_len
                    if len(buf) < total:
                        break
                    data = bytes(buf[3:total])
                    del buf[:total]
                    self._handle_binary(data)
                    continue

                if hdr > 0:
                    del buf[:hdr]
                    continue

                break

    def _handle_text(self, line):
        try:
            text = line.decode(errors="ignore").strip()
            if not text or "{" not in text or "}" not in text:
                return
            start = text.find("{")
            end = text.rfind("}")
            obj = json.loads(text[start:end + 1])
            cmd = obj.get("cmd")

            if cmd == "scan_result":
                if self.connected_channel is not None or self._connecting:
                    return

                mac = obj.get("mac")
                addr_type = int(obj.get("type", 0))
                if mac:
                    self._connecting = True
                    print(f"已找到控制器：{mac}")
                    print("正在連線，請稍候...")
                    self.send("scan off")
                    self.send(f"conn {addr_type} {mac}")

            elif cmd == "connected":
                self.connected_channel = int(
                    obj.get("channel", 0)
                )
                self._connecting = False


                print(
                    f"控制器連線成功，ESP32 通道："
                    f"{self.connected_channel}"
                )

                self.set_player_led_1()

                # 稍微延遲後播放連線成功震動
                def delayed_connection_rumble():
                    time.sleep(0.2)
                    self.connection_rumble()

                threading.Thread(
                    target=delayed_connection_rumble,
                    daemon=True
                ).start()

            elif cmd == "disconnected":
                self.connected_channel = None
                self._connecting = False
  
        except Exception as exc:
            print(f"ESP32 連接錯誤：{exc}")

    def _handle_binary(self, data):
        if not data:
            return

        chan_id = data[0] & 0x7F
        if not (1 <= chan_id <= 8):
            return

        # The working calibration tool treats everything after the channel byte
        # as the Switch 2 input-report payload.
        payload = data[1:]
        if self.input_callback is not None:
            self.input_callback(payload)
