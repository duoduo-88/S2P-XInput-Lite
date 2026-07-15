import json
import serial
from console_i18n import current_language
from console_i18n import localized_print as print


def _tr(zh, en):
    return en if current_language() == "en" else zh
import threading
import time


class ESP32Bridge:
    """Minimal USB-CDC client compatible with the working calibration tool."""

    def __init__(self, port, baudrate=2_000_000):
        self.port = port
        self.baudrate = baudrate
        self.serial = None
        self.input_callback = None
        self.connected_callback = None
        self.ready_callback = None
        self.disconnected_callback = None
        self.bridge_disconnected_callback = None
        self.calibration_mode = False
        self.running = False
        self.connected_channel = None
        self._connecting = False
        self._write_lock = threading.Lock()
        self._rumble_packet_id = 0
        self.esp32_mac = None
        self.esp32_mac_value = None
        self.controller_id = None
        self._pending_pair = False
        self._read_thread = None
        self._heartbeat_thread = None
        self._status_event = threading.Event()
        self._command_response_event = threading.Event()
        self._command_response_lock = threading.Lock()
        self._command_response = None
        self._status_misses = 0
        self._channel_missing_count = 0
        self._last_input_time = 0.0
        self._status_grace_until = 0.0
        self._closing = False
        self._bridge_failure_reported = False
        self._last_foreign_pairing_notice = None

    def open(self):
        self.serial = serial.Serial(
            self.port,
            self.baudrate,
            timeout=0.1,
            write_timeout=0.5,
        )
        self._closing = False
        self._bridge_failure_reported = False
        self._status_misses = 0
        self._channel_missing_count = 0
        self._status_grace_until = 0.0
        self._last_foreign_pairing_notice = None
        self.running = True
        self._read_thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name="ESP32BridgeReader",
        )
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="ESP32BridgeHeartbeat",
        )
        self._read_thread.start()
        self._heartbeat_thread.start()

    def close(self):
        self._closing = True

        # Ask the firmware to release the physical BLE link before dropping
        # USB CDC.  Otherwise the controller can remain attached to the ESP32.
        if self.running:
            self.send("auto off")
            self.send("ble disconnect")
            time.sleep(0.10)

        self.running = False
        self._status_event.set()
        self._command_response_event.set()

        try:
            if (
                self.serial is not None
                and self.serial.is_open
            ):
                self.serial.close()

        except (
            serial.SerialException,
            OSError
        ):
            pass

        self.serial = None
        self.connected_channel = None
        self.controller_id = None
        self._connecting = False
        self._pending_pair = False
        for thread in (self._heartbeat_thread, self._read_thread):
            if (
                thread is not None
                and thread.is_alive()
                and threading.current_thread() is not thread
            ):
                thread.join(timeout=1.0)
        self._heartbeat_thread = None
        self._read_thread = None
        
    def send(self, command):
        if (
            not self.running
            or self.serial is None
            or not self.serial.is_open
        ):
            return False

        try:
            with self._write_lock:
                self.serial.write(
                    (
                        command.rstrip("\n") + "\n"
                    ).encode("utf-8")
                )

            return True

        except (
            serial.SerialException,
            OSError
        ):
            return False
    @staticmethod
    def _encode_vibration(lf_freq, lf_amp, hf_freq, hf_amp):
        """Encode one HD Rumble 2 VibrationData frame (5 bytes)."""
        value = 0
        value |= int(lf_freq) & 0x1FF
        value |= (int(lf_amp) & 0x3FF) << 10
        value |= (int(hf_freq) & 0x1FF) << 20
        value |= (int(hf_amp) & 0x3FF) << 30
        return value.to_bytes(5, byteorder="little")

    def send_pro_rumble(
        self,
        lf_freq,
        lf_amp,
        hf_freq,
        hf_amp
    ):
        channel = self.connected_channel

        if channel is None:
            return False

        vibration = self._encode_vibration(
            lf_freq,
            lf_amp,
            hf_freq,
            hf_amp
        )

        packet_id = (
            0x50
            + (self._rumble_packet_id & 0x0F)
        )
        self._rumble_packet_id = (
            self._rumble_packet_id + 1
        ) & 0x0F

        motor_vibrations = (
            bytes([packet_id])
            + vibration
            + vibration
            + vibration
        )

        payload = (
            b"\x00"
            + motor_vibrations
            + motor_vibrations
        )

        command = (
            f"wr {int(channel)} "
            f"r {payload.hex()}"
        )

        return self.send(command)

    def send_rumble_shadow(
        self,
        lf_freq,
        lf_amp,
        hf_freq,
        hf_amp,
    ):
        """Queue the newest rumble payload through the firmware `rs` path."""
        channel = self.connected_channel
        if channel is None:
            return False

        vibration = self._encode_vibration(
            lf_freq, lf_amp, hf_freq, hf_amp
        )
        packet_id = 0x50 + (self._rumble_packet_id & 0x0F)
        self._rumble_packet_id = (self._rumble_packet_id + 1) & 0x0F
        motor_vibrations = bytes([packet_id]) + vibration * 3
        payload = b"\x00" + motor_vibrations + motor_vibrations
        return self.send(f"rs {int(channel)} {payload.hex()}")

    def _report_bridge_failure(self):
        if self._bridge_failure_reported or self._closing:
            return
        self._bridge_failure_reported = True
        self.running = False
        self.connected_channel = None
        self._connecting = False
        print("ESP32 狀態連續三次無回應，判定橋接已中斷。")
        if self.bridge_disconnected_callback is not None:
            try:
                self.bridge_disconnected_callback()
            except Exception:
                pass

    def _heartbeat_loop(self):
        """Poll status lite and tolerate three consecutive missed replies."""
        while self.running:
            self._status_event.clear()
            if not self.send("status lite"):
                self._status_misses += 1
            elif not self._status_event.wait(timeout=0.75):
                # Active input proves the USB/BLE data path is alive even if a
                # busy firmware temporarily delays a manager response.
                now = time.monotonic()
                if (
                    now >= self._status_grace_until
                    and now - self._last_input_time >= 3.0
                ):
                    self._status_misses += 1
            else:
                self._status_misses = 0

            if self._status_misses >= 3:
                self._report_bridge_failure()
                return

            deadline = time.monotonic() + 0.25
            while self.running and time.monotonic() < deadline:
                time.sleep(0.05)

    def pair_controller_to_esp32(self):
        """將 Switch 2 Pro Controller 配對到目前的 ESP32。"""

        channel = self.connected_channel

        if channel is None:
            print("無法配對：控制器尚未連線。")
            return False

        if self.esp32_mac_value is None:
            print("無法配對：尚未取得 ESP32 BLE MAC。")
            return False

        mac_value = self.esp32_mac_value

        print(
            f"正在將控制器配對到 ESP32："
            f"{self.esp32_mac}"
        )

        # Tommy Controller.pair() 使用的固定 LTK
        ltk1 = bytes([
            0x00,
            0xEA, 0xBD, 0x47, 0x13,
            0x89, 0x35, 0x42, 0xC6,
            0x79, 0xEE, 0x07, 0xF2,
            0x53, 0x2C, 0x6C, 0x31,
        ])

        ltk2 = bytes([
            0x00,
            0x40, 0xB0, 0x8A, 0x5F,
            0xCD, 0x1F, 0x9B, 0x41,
            0x12, 0x5C, 0xAC, 0xC6,
            0x3F, 0x38, 0xA0, 0x73,
        ])

        def send_pair_command(
            subcommand_id,
            command_data
        ):
            payload = (
                bytes([0x15])
                + b"\x91\x01"
                + bytes([subcommand_id])
                + b"\x00"
                + bytes([len(command_data)])
                + b"\x00\x00"
                + command_data
            )

            return self.send(
                f"wr {int(channel)} c "
                f"{payload.hex()}"
            )

        # 1. SET_MAC
        pair_mac_data = (
            b"\x00\x02"
            + mac_value.to_bytes(
                6,
                "little"
            )
            + mac_value.to_bytes(
                6,
                "little"
            )
        )

        if not send_pair_command(
            0x01,
            pair_mac_data
        ):
            return False

        # 2. LTK1
        if not send_pair_command(
            0x04,
            ltk1
        ):
            return False

        time.sleep(0.1)

        # 3. LTK2
        if not send_pair_command(
            0x02,
            ltk2
        ):
            return False

        time.sleep(0.1)

        # 4. FINISH
        if not send_pair_command(
            0x03,
            b"\x00"
        ):
            return False

        print(
            "ESP32 配對資料已送出。"
        )

        self._pending_pair = False

        return True

    def initialize_controller_features(self):
        """Enable the normal Switch 2 input stream, including motion data."""
        channel = self.connected_channel
        if channel is None:
            return False

        # Keep this sequence aligned with BluetoothController's known-good
        # Switch 2 initialization.  In particular, FEATSEL 0x94 enables
        # motion + mouse + magnetometer data in the input report.
        commands = (
            (0x03, 0x0D, b"\x01\x00\xFF\xFF\xFF\xFF\xFF\xFF"),
            (0x07, 0x01, b""),
            (0x16, 0x01, b""),
            (0x15, 0x03, b"\x00"),
            (0x0C, 0x02, b"\x94\x00\x00\x00"),
            (0x11, 0x03, b""),
            (
                0x0A,
                0x08,
                b"\x01\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF"
                b"\x35\x00\x46\x00\x00\x00\x00\x00\x00\x00\x00",
            ),
            (0x0C, 0x04, b"\x94\x00\x00\x00"),
            (0x03, 0x0A, b"\x09\x00\x00\x00"),
            (0x10, 0x01, b""),
            (0x01, 0x0C, b""),
            (0x01, 0x01, b"\x00\x00\x00\x00"),
            (0x09, 0x07, b"\x01\x00\x00\x00\x00\x00\x00\x00"),
        )

        for command_id, subcommand_id, command_data in commands:
            if not self.running or self.connected_channel != channel:
                return False
            payload = (
                bytes([command_id])
                + b"\x91\x01"
                + bytes([subcommand_id])
                + b"\x00"
                + bytes([len(command_data)])
                + b"\x00\x00"
                + command_data
            )
            with self._command_response_lock:
                self._command_response = None
            self._command_response_event.clear()
            if not self.send(f"wr {int(channel)} c {payload.hex()}"):
                return False

            deadline = time.monotonic() + 2.0
            acknowledged = False
            while self.running and time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                if not self._command_response_event.wait(remaining):
                    break
                self._command_response_event.clear()
                with self._command_response_lock:
                    response = self._command_response
                    self._command_response = None
                if response and response[0] == command_id:
                    acknowledged = True
                    break
            if not acknowledged:
                print(
                    "控制器初始化命令未收到回應："
                    f"0x{command_id:02X}/0x{subcommand_id:02X}"
                )
                return False

            time.sleep(0.02)

        print("控制器功能初始化完成（含陀螺儀）。")
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

        if self.send(command):
            print("已送出 Player 1 LED 設定。")
            return True

        return False

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
            
            if self.calibration_mode:
                if self.ready_callback is not None:
                    try:
                        self.ready_callback()
                    except Exception as exc:
                        print(
                            f"控制器準備完成回呼錯誤：{exc}"
                        )

            else:
                print()
                print(_tr("控制器已準備完成，可以開始使用。", "Controller ready."))
                print()
                print(_tr("提醒：連線期間請勿關閉此視窗。", "Keep this window open while connected."))
                print(_tr("若需要中斷連線，請關閉此視窗。", "Close this window to disconnect."))
                print()
                print(_tr(
                    "如需校正搖桿，請先關閉本程式，再按下「校正搖桿」按鈕。",
                    "To calibrate sticks, close this program and click Stick Calibration.",
                ))
                print(_tr(
                    "接著依照校正程序的畫面提示操作即可。",
                    "Then follow the calibration prompts.",
                ))
                print()

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    def _parse_reconnect_mac(self, data_hex):
        try:
            data = bytes.fromhex(data_hex)

            # ESP32 scan_result.data 包含完整 BLE AD structure：
            #
            # 02 01 06
            # 1B FF 53 05 ...
            #
            # reconnect MAC 位於完整廣播資料的 byte 17～22
            if len(data) < 23:
                return None

            return int.from_bytes(
                data[17:23],
                byteorder="little"
            )

        except Exception:
            return None


    def start_scan(self):
        self.send("auto off")
        self.send("ble disconnect")
        self.send("status lite")
        self.send("scan on")

    def _read_loop(self):
        buf = bytearray()

        while self.running:
            try:
                if self.serial.in_waiting:
                    buf.extend(
                        self.serial.read(
                            self.serial.in_waiting
                        )
                    )
                else:
                    time.sleep(0.001)
                    continue

            except (
                serial.SerialException,
                OSError
            ):
                if self.running:
                    print()
                    print("ESP32 已中斷連線。")

                self._report_bridge_failure()

                break

            while True:
                nl = buf.find(b"\n")
                hdr = buf.find(b"\xaa\x55")

                if (
                    nl != -1
                    and (
                        hdr == -1
                        or nl < hdr
                    )
                ):
                    line = bytes(
                        buf[:nl + 1]
                    )
                    del buf[:nl + 1]

                    self._handle_text(
                        line
                    )
                    continue

                if hdr == 0:
                    if len(buf) < 3:
                        break

                    packet_len = buf[2]
                    total = 3 + packet_len

                    if len(buf) < total:
                        break

                    data = bytes(
                        buf[3:total]
                    )
                    del buf[:total]

                    self._handle_binary(
                        data
                    )
                    continue

                if hdr > 0:
                    del buf[:hdr]
                    continue

                break
                
    def _restart_scan(self, delay=0.5):
        """連線失敗或斷線後，自動重新開始搜尋控制器。"""

        def worker():
            time.sleep(delay)

            if (
                self.running
                and self.connected_channel is None
                and not self._connecting
            ):
                print("正在重新搜尋控制器...")
                self.send("scan on")

        threading.Thread(
            target=worker,
            daemon=True
        ).start()


    def _handle_text(self, line):
        try:
            text = line.decode(errors="ignore").strip()
            
            # print(f"[ESP32] {text}")          
            
            if not text or "{" not in text or "}" not in text:
                return
            start = text.find("{")
            end = text.rfind("}")
            obj = json.loads(text[start:end + 1])
            cmd = obj.get("cmd")

            if cmd in ("status", "status lite"):
                self._status_event.set()
                self._status_misses = 0
                mac = obj.get("mac")

                if mac:
                    new_mac = mac.upper()
                    mac_changed = new_mac != self.esp32_mac
                    self.esp32_mac = new_mac
                    self.esp32_mac_value = int(
                        self.esp32_mac.replace(":", ""),
                        16
                    )

                    if mac_changed:
                        self._last_foreign_pairing_notice = None
                        print(
                            f"ESP32 BLE MAC：{self.esp32_mac}"
                        )

                if self.connected_channel is not None:
                    channel_mask = int(obj.get("ble_channels", 0) or 0)
                    if channel_mask & (1 << self.connected_channel):
                        self._channel_missing_count = 0
                    else:
                        self._channel_missing_count += 1
                        if self._channel_missing_count >= 3:
                            self._channel_missing_count = 0
                            self.connected_channel = None
                            self._connecting = False
                            print("ESP32 狀態連續三次未包含控制器通道。")
                            if self.disconnected_callback is not None:
                                try:
                                    self.disconnected_callback()
                                except Exception as exc:
                                    print(f"控制器斷線回呼錯誤：{exc}")
                            self._restart_scan()

            elif cmd == "scan_result":
                if (
                    self.connected_channel is not None
                    or self._connecting
                ):
                    return

                mac = obj.get("mac")
                addr_type = int(
                    obj.get("type", 0)
                )
                data_hex = obj.get(
                    "data",
                    ""
                )

                if not mac:
                    return

                reconnect_mac = (
                    self._parse_reconnect_mac(
                        data_hex
                    )
                )

                # The firmware can report the same advertisement many times per
                # second.  A controller paired to another host must remain visible
                # so a later SYNC advertisement (reconnect_mac == 0) is detected,
                # but repeating the same guidance for every scan result only floods
                # the console.  Suppress only the unchanged foreign-host notice.
                foreign_pairing = (
                    self.esp32_mac_value is not None
                    and reconnect_mac not in (None, 0, self.esp32_mac_value)
                )
                if foreign_pairing:
                    notice_key = (mac.upper(), reconnect_mac)
                    if notice_key == self._last_foreign_pairing_notice:
                        return
                    self._last_foreign_pairing_notice = notice_key

                print(
                    "控制器 reconnect_mac：",
                    (
                        f"{reconnect_mac:012X}"
                        if reconnect_mac is not None
                        else "無法解析"
                    )
                )

                # SYNC 配對模式
                if reconnect_mac == 0:
                    print(
                        _tr("辨識為 SYNC 配對模式。", "SYNC pairing mode detected.")
                    )

                    self._pending_pair = True

                # 已經配對到這個 ESP32
                elif (
                    self.esp32_mac_value is not None
                    and reconnect_mac
                    == self.esp32_mac_value
                ):
                    print(
                        _tr("辨識為已配對到此 ESP32。", "Controller is already paired with this ESP32.")
                    )

                    self._pending_pair = False

                # 已經配對到其他 Host
                elif self.esp32_mac_value is not None:
                    print(
                        _tr("控制器目前配對到其他裝置。", "Controller is paired with another device.")
                    )
                    print(
                        "目前 ESP32 BLE MAC：",
                        f"{self.esp32_mac_value:012X}"
                    )
                    print(
                        _tr("如要切換到 ESP32，請按住 SYNC 重新配對。", "Hold SYNC to pair the controller with this ESP32.")
                    )

                    return

                # 還沒取得 ESP32 MAC
                else:
                    print(
                        "尚未取得 ESP32 BLE MAC，"
                        "暫時使用一般連線模式。"
                    )

                    self._pending_pair = False

                self._connecting = True
                self.controller_id = mac.upper()

                print(
                    _tr(f"已找到控制器：{mac}", f"Controller found: {mac}")
                )
                print(
                    _tr("正在連線，請稍候...", "Connecting, please wait...")
                )

                self.send("scan off")
                self.send(
                    f"conn {addr_type} {mac}"
                )

            elif cmd == "connected":

                self.connected_channel = int(
                    obj.get("channel", 0)
                )
                self._connecting = False
                self._channel_missing_count = 0
                # SW2 initialization temporarily keeps the firmware busy.
                self._status_grace_until = time.monotonic() + 10.0

                if self.connected_callback is not None:
                    try:
                        self.connected_callback()
                    except Exception as exc:
                        print(f"控制器連線回呼錯誤：{exc}")


                print(
                    _tr(
                        f"控制器連線成功，ESP32 通道：{self.connected_channel}",
                        f"Controller connected on ESP32 channel {self.connected_channel}.",
                    )
                )

                if self._pending_pair:
                    print(
                        "偵測到 SYNC 配對連線，"
                        "開始寫入 ESP32 配對資料..."
                    )

                    if not self.pair_controller_to_esp32():
                        return

                # BLE notifications are already active at this point.  Run the
                # command sequence outside the serial read thread so ACK/input
                # frames continue to be drained while initialization is sent.
                def prepare_connected_controller():
                    if not self.initialize_controller_features():
                        print("控制器功能初始化失敗，陀螺儀資料可能無法使用。")
                        return
                    time.sleep(0.2)
                    self.connection_rumble()

                threading.Thread(
                    target=prepare_connected_controller,
                    daemon=True
                ).start()

            elif cmd == "connect_fail":
                self.connected_channel = None
                self.controller_id = None
                self._connecting = False

                print(_tr("控制器連線失敗。", "Controller connection failed."))

                self._restart_scan()


            elif cmd == "disconnected":
                was_connected = (
                    self.connected_channel is not None
                )

                self.connected_channel = None
                self._connecting = False
                self._channel_missing_count = 0

                if was_connected:
                    print(_tr("控制器已中斷連線。", "Controller disconnected."))

                    if self.disconnected_callback is not None:
                        try:
                            self.disconnected_callback()
                        except Exception as exc:
                            print(
                                f"控制器斷線回呼錯誤：{exc}"
                            )

                self._restart_scan()


        except Exception as exc:
            print(f"ESP32 連接錯誤：{exc}")

    def _handle_binary(self, data):
        if not data:
            return

        is_command_frame = bool(data[0] & 0x80)
        chan_id = data[0] & 0x7F
        if not (1 <= chan_id <= 8):
            return

        if (
            self.connected_channel is None
            # JSON channel numbers are zero-based; CDC frame channels are
            # encoded as channel + 1 by the ESP32 firmware.
            or chan_id != self.connected_channel + 1
        ):
            return

        # The ESP32 bridge marks command/ACK notifications with bit 7 of the
        # channel byte.  They are not Switch input reports and must never be
        # forwarded to the input parser, otherwise command payload bytes can
        # appear as a brief button press or stick movement.
        if is_command_frame:
            response = bytes(data[1:])
            if response:
                with self._command_response_lock:
                    self._command_response = response
                self._command_response_event.set()
            return

        # The working calibration tool treats everything after the channel byte
        # as the Switch 2 input-report payload.
        payload = data[1:]
        self._last_input_time = time.monotonic()
        if self.input_callback is not None:
            self.input_callback(payload)
