import asyncio
import threading
import time

from bleak import (
    BleakClient,
    BleakScanner,
)
from bleak.backends.winrt.client import (
    BleakClientWinRT
)

from winrt.windows.devices.bluetooth import (
    BluetoothLEPreferredConnectionParameters
)

# =========================
# Switch 2 Pro BLE UUID
# =========================

INPUT_REPORT_UUID = (
    "ab7de9be-89fe-49ad-828f-118f09df7fd2"
)

COMMAND_WRITE_UUID = (
    "649d4ac9-8eb7-4e6c-af44-1ea54fe5f005"
)

COMMAND_RESPONSE_UUID = (
    "c765a961-d9d8-4d36-a20a-5315b111836a"
)

VIBRATION_WRITE_UUID = (
    "cc483f51-9258-427d-a939-630c31f72b05"
)

NINTENDO_MANUFACTURER_ID = 0x0553
NINTENDO_VENDOR_ID = 0x057E
PRO_CONTROLLER2_PID = 0x2069

PAIR_LTK1 = bytes([0x00, 0xEA, 0xBD, 0x47, 0x13, 0x89, 0x35, 0x42, 0xC6, 0x79, 0xEE, 0x07, 0xF2, 0x53, 0x2C, 0x6C, 0x31])
PAIR_LTK2 = bytes([0x00, 0x40, 0xB0, 0x8A, 0x5F, 0xCD, 0x1F, 0x9B, 0x41, 0x12, 0x5C, 0xAC, 0xC6, 0x3F, 0x38, 0xA0, 0x73])


class BluetoothController:

    def __init__(
        self
    ):
        self.client = None
        self.device = None

        self._search_message_shown = False

        self.input_callback = None
        self.ready_callback = None
        self.disconnected_callback = None
        self.calibration_mode = False

        self.connected = False
        self.running = False

        self._preferred_connection_request = None
        self.host_mac_value = None
        self.controller_reconnect_mac = 0
        self.pairing_required = False
        self._rumble_write_lock = None

        self._loop = None
        self._thread = None

        self._response_future = None
        self._expected_command_id = None

        self._rumble_packet_id = 0
        
        self.command_write_uuid = (
            COMMAND_WRITE_UUID
        )

        self.command_response_uuid = (
            COMMAND_RESPONSE_UUID
        )        

    # =========================
    # 啟動背景 asyncio
    # =========================

    def open(
        self
    ):
        if self.running:
            return

        self.running = True

        self._thread = threading.Thread(
            target=self._thread_main,
            daemon=True
        )

        self._thread.start()

    def _thread_main(
        self
    ):
        self._loop = asyncio.new_event_loop()

        asyncio.set_event_loop(
            self._loop
        )

        self._rumble_write_lock = asyncio.Lock()

        try:
            self._loop.run_until_complete(
                self._run()
            )

        finally:
            pending = asyncio.all_tasks(
                self._loop
            )

            for task in pending:
                task.cancel()

            if pending:
                try:
                    self._loop.run_until_complete(
                        asyncio.gather(
                            *pending,
                            return_exceptions=True
                        )
                    )

                except Exception:
                    pass

            self._loop.close()

    # =========================
    # 主流程
    # =========================

    async def _run(
        self
    ):
        try:
            self.host_mac_value = (
                self._get_local_mac_value()
            )

            print(
                "正在搜尋 Switch 2 Pro Controller..."
            )
            print(
                "已配對的手把請按任意按鍵喚醒；"
                "第一次配對請按住 SYNC。"
            )

            # =========================
            # 自動搜尋 / 重連循環
            # =========================

            while self.running:

                try:
                    # 每一輪重新清除狀態
                    self.connected = False
                    self.device = None
                    self._search_message_shown = False

                    # =========================
                    # 搜尋控制器
                    # =========================

                    self.device = (
                        await self._find_controller()
                    )

                    if not self.running:
                        break

                    if self.device is None:
                        print(
                            "找不到 Switch 2 Pro Controller。"
                        )

                        await asyncio.sleep(
                            0.5
                        )

                        continue

                    print(
                        "正在使用 Windows 原生藍牙連線..."
                    )

                    # =========================
                    # 建立新的 BleakClient
                    # =========================

                    self.client = BleakClient(
                        self.device,
                        disconnected_callback=(
                            self._on_disconnected
                        )
                    )

                    await self.client.connect(
                        timeout=60.0
                    )

                    if not self.client.is_connected:
                        raise RuntimeError(
                            "原生藍牙連線失敗。"
                        )

                    self.connected = True

                    print(
                        "Switch 2 Pro Controller "
                        "原生藍牙連線成功。"
                    )

                    # =========================
                    # 要求 Windows 使用較短的
                    # BLE Connection Interval
                    # =========================

                    try:
                        backend = (
                            self.client._backend
                        )

                        if isinstance(
                            backend,
                            BleakClientWinRT
                        ):
                            self._preferred_connection_request = (
                                backend
                                ._requester
                                .request_preferred_connection_parameters(
                                    BluetoothLEPreferredConnectionParameters
                                    .throughput_optimized
                                )
                            )

                    except Exception as exc:
                        print(
                            "設定 BLE 高吞吐量參數失敗：",
                            repr(exc)
                        )

                    # =========================
                    # 等待 BLE 連線穩定
                    # =========================

                    await asyncio.sleep(
                        0.5
                    )

                    # =========================
                    # 探索 SW2 Command Channel
                    # =========================

                    await self._discover_sw2_characteristics()

                    # =========================
                    # 訂閱 Command Response
                    # =========================

                    await self.client.start_notify(
                        self.command_response_uuid,
                        self._on_command_response
                    )

                    # =========================
                    # SYNC 模式時重新配對到 Windows
                    # =========================

                    if self.pairing_required:
                        await self._pair_controller()

                    # =========================
                    # 初始化控制器
                    # =========================

                    await self._initialize_controller()

                    # =========================
                    # 訂閱 Input Report
                    # =========================

                    await self.client.start_notify(
                        INPUT_REPORT_UUID,
                        self._on_input_report
                    )

                    print()
                    print(
                        "原生藍牙輸入已啟動。"
                    )
                    print()

                    # 稍微延遲後播放連線成功震動
                    await asyncio.sleep(
                        0.2
                    )

                    self.connection_rumble()

                    # =========================
                    # 保持連線
                    # =========================

                    while (
                        self.running
                        and self.connected
                    ):
                        await asyncio.sleep(
                            0.1
                        )

                    # =========================
                    # 正常斷線後重新搜尋
                    # =========================

                    if self.running:
                        print(
                            "正在重新搜尋控制器..."
                        )

                except Exception as exc:
                    print()
                    print(
                        "原生藍牙連線失敗：",
                        str(exc)
                    )

                    if self.running:
                        print(
                            "正在重新搜尋控制器..."
                        )

                # =========================
                # 每輪結束都清理舊連線
                # =========================

                await self._disconnect()

                # close() 時不要再進下一輪
                if not self.running:
                    break

                # 避免失敗後立即瘋狂重試
                await asyncio.sleep(
                    0.5
                )

        except Exception as exc:
            import traceback

            print()
            print(
                "原生藍牙主流程錯誤：",
                repr(exc)
            )

            traceback.print_exc()

        finally:
            await self._disconnect()

    # =========================
    # 掃描控制器
    # =========================

    @staticmethod
    def _get_local_mac_value():
        try:
            import bluetooth

            addresses = (
                bluetooth.read_local_bdaddr()
            )

            if not addresses:
                raise RuntimeError(
                    "找不到本機藍牙介面卡，"
                    "或藍牙未開啟。"
                )

            mac_text = (
                addresses[0]
                .replace(":", "")
                .replace("-", "")
                .strip()
                .upper()
            )

            mac_value = int(
                mac_text,
                16
            )

            print(
                "本機藍牙 MAC：",
                addresses[0]
            )

            return mac_value

        except Exception as exc:
            print(
                "無法取得本機藍牙 MAC：",
                repr(exc)
            )

            return None

    async def _find_controller(
        self
    ):
        while self.running:
            devices = await BleakScanner.discover(
                timeout=5.0,
                return_adv=True
            )

            # 掃描期間可能已經按 B / Q 關閉 backend
            if not self.running:
                return None

            pairing_candidate = None

            for (
                device,
                advertisement
            ) in devices.values():

                data = (
                    advertisement
                    .manufacturer_data
                    .get(
                        NINTENDO_MANUFACTURER_ID
                    )
                )

                if (
                    data is None
                    or len(data) < 16
                ):
                    continue

                vendor_id = int.from_bytes(
                    data[3:5],
                    byteorder="little"
                )

                product_id = int.from_bytes(
                    data[5:7],
                    byteorder="little"
                )

                if (
                    vendor_id
                    != NINTENDO_VENDOR_ID
                    or product_id
                    != PRO_CONTROLLER2_PID
                ):
                    continue

                reconnect_mac = int.from_bytes(
                    data[10:16],
                    byteorder="little"
                )

                print(
                    "找到 Switch 2 Pro Controller：",
                    device.address,
                    "reconnect_mac =",
                    f"{reconnect_mac:012X}"
                )

                # 已經配對過
                if (
                    self.host_mac_value
                    is not None
                    and reconnect_mac
                    == self.host_mac_value
                ):
                    self.controller_reconnect_mac = (
                        reconnect_mac
                    )

                    self.pairing_required = False

                    print(
                        "辨識為已配對手把，"
                        "將直接重新連線。"
                    )

                    return device

                # 第一次配對模式
                if reconnect_mac == 0:
                    pairing_candidate = (
                        device
                    )

            if (
                pairing_candidate
                is not None
            ):
                self.controller_reconnect_mac = 0
                self.pairing_required = True

                print(
                    "辨識為第一次配對模式。"
                )

                return pairing_candidate

            if not self.running:
                return None

            if not self._search_message_shown:
                print(
                    "尚未偵測到可連線的手把，"
                    "搜尋中..."
                )
                self._search_message_shown = True

        return None


    # =========================
    # Command Response
    # =========================

    async def _discover_sw2_characteristics(
        self
    ):
        for service in self.client.services:

            if (
                "ab7de9be"
                not in str(
                    service.uuid
                ).lower()
            ):
                continue

            write_chars = []
            notify_chars = []

            for char in (
                service.characteristics
            ):
                properties = (
                    char.properties
                )

                if (
                    "write-without-response"
                    in properties
                    or "write"
                    in properties
                ):
                    write_chars.append(
                        char
                    )

                if (
                    "notify"
                    in properties
                ):
                    notify_chars.append(
                        char
                    )

            write_chars.sort(
                key=lambda char: char.handle
            )

            notify_chars.sort(
                key=lambda char: char.handle
            )

            if len(write_chars) >= 2:
                self.command_write_uuid = (
                    write_chars[1].uuid
                )

            elif len(write_chars) == 1:
                self.command_write_uuid = (
                    write_chars[0].uuid
                )

            if len(notify_chars) >= 3:
                self.command_response_uuid = (
                    notify_chars[2].uuid
                )

            elif notify_chars:
                self.command_response_uuid = (
                    notify_chars[-1].uuid
                )


            return

        raise RuntimeError(
            "找不到 Switch 2 BLE Service。"
        )


    def _on_command_response(
        self,
        sender,
        data
    ):
        future = (
            self._response_future
        )

        if (
            future is None
            or future.done()
        ):
            return

        if not data:
            return

        expected = (
            self._expected_command_id
        )

        if (
            expected is not None
            and data[0] != expected
        ):
            return

        try:
            loop = future.get_loop()

            loop.call_soon_threadsafe(
                self._finish_response_future,
                future,
                bytes(data)
            )

        except Exception:
            pass

    @staticmethod
    def _finish_response_future(
        future,
        data
    ):
        if not future.done():
            future.set_result(
                data
            )


    # =========================
    # 發送 Switch 2 Command
    # =========================

    async def _write_command(
        self,
        command_id,
        subcommand_id,
        command_data=b"",
        timeout=2.0
    ):
        if (
            self.client is None
            or not self.client.is_connected
        ):
            raise RuntimeError(
                "控制器尚未連線。"
            )

        command_buffer = (
            bytes([
                command_id
            ])
            + b"\x91\x01"
            + bytes([
                subcommand_id
            ])
            + b"\x00"
            + bytes([
                len(command_data)
            ])
            + b"\x00\x00"
            + command_data
        )

        self._expected_command_id = (
            command_id
        )

        self._response_future = (
            self._loop.create_future()
        )

        try:
            await self.client.write_gatt_char(
                self.command_write_uuid,
                command_buffer,
                response=False
            )

            response = await asyncio.wait_for(
                self._response_future,
                timeout=timeout
            )

        finally:
            self._response_future = None
            self._expected_command_id = None

        if (
            len(response) < 8
            or response[0] != command_id
        ):
            raise RuntimeError(
                "控制器命令回應格式錯誤："
                f"{response.hex()}"
            )

        return response[8:]

    async def _pair_controller(
        self
    ):
        if self.host_mac_value is None:
            raise RuntimeError("第一次配對需要取得本機藍牙 MAC。")

        print("正在將 Switch 2 Pro Controller 配對到這台電腦...")
        mac_value = self.host_mac_value

        await self._write_command(
            0x15, 0x01,
            b"\x00\x02"
            + mac_value.to_bytes(6, "little")
            + mac_value.to_bytes(6, "little")
        )
        await self._write_command(0x15, 0x04, PAIR_LTK1)
        await self._write_command(0x15, 0x02, PAIR_LTK2)
        await self._write_command(0x15, 0x03, b"\x00")

        self.pairing_required = False
        print("配對資料已寫入手把；之後按任意按鍵即可喚醒重連。")

    # =========================
    # Tommy Switch 2 初始化
    # =========================

    async def _initialize_controller(
        self
    ):
        print(
            "正在準備控制器..."
        )
    
        commands = [
            (
                0x03,
                0x0D,
                b"\x01\x00"
                b"\xFF\xFF\xFF\xFF\xFF\xFF"
            ),
            (
                0x07,
                0x01,
                b""
            ),
            (
                0x16,
                0x01,
                b""
            ),
            (
                0x15,
                0x03,
                b"\x00"
            ),
            (
                0x0C,
                0x02,
                b"\x94\x00\x00\x00"
            ),
            (
                0x11,
                0x03,
                b""
            ),
            (
                0x0A,
                0x08,
                (
                    b"\x01"
                    b"\xFF\xFF\xFF\xFF"
                    b"\xFF\xFF\xFF\xFF"
                    b"\x35\x00"
                    b"\x46\x00"
                    b"\x00\x00\x00\x00"
                    b"\x00\x00\x00\x00"
                )
            ),
            (
                0x0C,
                0x04,
                b"\x94\x00\x00\x00"
            ),
            (
                0x03,
                0x0A,
                b"\x09\x00\x00\x00"
            ),
            (
                0x10,
                0x01,
                b""
            ),
            (
                0x01,
                0x0C,
                b""
            ),
            (
                0x01,
                0x01,
                b"\x00\x00\x00\x00"
            ),
            (
                0x09,
                0x07,
                b"\x01\x00\x00\x00"
                b"\x00\x00\x00\x00"
            ),
        ]


        consecutive_failures = 0

        for (
            command_id,
            subcommand_id,
            data
        ) in commands:

            try:
                await self._write_command(
                    command_id,
                    subcommand_id,
                    data
                )

                consecutive_failures = 0

                await asyncio.sleep(
                    0.01
                )

            except Exception as exc:
                consecutive_failures += 1

                print(
                    "初始化命令失敗："
                    f"{command_id:02X}:"
                    f"{subcommand_id:02X} "
                    f"{exc}"
                )

                if (
                    consecutive_failures
                    >= 3
                ):
                    raise RuntimeError(
                        "Switch 2 Pro "
                        "初始化連續失敗。"
                    )

    # =========================
    # Input Report
    # =========================

    def _on_input_report(
        self,
        sender,
        data
    ):
        if not data:
            return

        if (
            self.input_callback
            is not None
        ):
            self.input_callback(
                bytes(data)
            )

    @staticmethod
    def _encode_vibration(
        lf_freq, lf_amp, hf_freq, hf_amp
    ):
        value = 0
        value |= int(lf_freq) & 0x1FF
        value |= (int(lf_amp) & 0x3FF) << 10
        value |= (int(hf_freq) & 0x1FF) << 20
        value |= (int(hf_amp) & 0x3FF) << 30
        return value.to_bytes(5, "little")

    def send_pro_rumble(
        self, lf_freq, lf_amp, hf_freq, hf_amp
    ):
        if (
            not self.connected
            or self.client is None
            or self._loop is None
            or not self._loop.is_running()
        ):
            return False

        future = asyncio.run_coroutine_threadsafe(
            self._send_pro_rumble_async(
                lf_freq, lf_amp, hf_freq, hf_amp
            ),
            self._loop
        )
        future.add_done_callback(self._consume_rumble_result)
        return True

    def connection_rumble(self):
        """連線成功時播放兩次短震動提示。"""
        if not self.connected:
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
                print(
                    "控制器已準備完成，可以開始使用。"
                )
                print()
                print(
                    "提醒：連線期間請勿關閉此視窗。"
                )
                print(
                    "若需要中斷連線，請關閉此視窗。"
                )
                print()
                print(
                    "如需校正搖桿，請先關閉本程式，"
                    "再按下「校正搖桿」按鈕。"
                )
                print(
                    "接著依照校正程序的畫面提示"
                    "操作即可。"
                )
                print()

        threading.Thread(
            target=worker,
            daemon=True
        ).start()


    @staticmethod
    def _consume_rumble_result(future):
        try:
            future.result()
        except Exception:
            pass

    async def _send_pro_rumble_async(
        self, lf_freq, lf_amp, hf_freq, hf_amp
    ):
        if self.client is None or not self.client.is_connected:
            return False

        vibration = self._encode_vibration(
            lf_freq, lf_amp, hf_freq, hf_amp
        )
        packet_id = 0x50 + (self._rumble_packet_id & 0x0F)
        self._rumble_packet_id = (self._rumble_packet_id + 1) & 0x0F

        motor_vibrations = (
            bytes([packet_id])
            + vibration
            + vibration
            + vibration
        )
        payload = b"\x00" + motor_vibrations + motor_vibrations

        async with self._rumble_write_lock:
            await self.client.write_gatt_char(
                VIBRATION_WRITE_UUID,
                payload,
                response=False
            )
        return True

    # =========================
    # 斷線
    # =========================

    def _on_disconnected(
        self,
        client
    ):
        self.connected = False
        self._preferred_connection_request = None

        print()
        print(
            "Switch 2 Pro Controller "
            "原生藍牙已中斷。"
        )

        if self.disconnected_callback is not None:
            try:
                self.disconnected_callback()
            except Exception as exc:
                print(
                    f"控制器斷線回呼錯誤：{exc}"
                )

    async def _disconnect(
        self
    ):
        if self.client is None:
            return

        try:
            if self.client.is_connected:
                await self.client.disconnect()

        except Exception:
            pass

        self.connected = False
        self._preferred_connection_request = None
        self.client = None

    def close(
        self
    ):
        self.running = False

        if (
            self._loop is not None
            and self._loop.is_running()
        ):
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._disconnect(),
                    self._loop
                )

                # 等待斷線完成，但不要無限卡住
                future.result(timeout=2.0)

            except Exception:
                pass

        # 等待背景 asyncio 執行緒真正退出
        if (
            self._thread is not None
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=6.0)

        self._thread = None
        self._loop = None