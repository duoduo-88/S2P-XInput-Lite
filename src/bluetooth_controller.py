import asyncio
import inspect
import threading
import time
from console_i18n import current_language
from console_i18n import localized_print as print

from bleak import (
    BleakClient,
    BleakScanner,
)
from winrt.windows.devices.bluetooth import (
    BluetoothLEPreferredConnectionParameters,
    BluetoothLEPreferredConnectionParametersRequestStatus,
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


def tr(zh, en):
    return en if current_language() == "en" else zh

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
        self.connected_callback = None
        self.ready_callback = None
        self.disconnected_callback = None
        self.bluetooth_unavailable_callback = None
        self.calibration_mode = False

        self.connected = False
        self.running = False
        self._closing = False

        self._preferred_connection_request = None
        self.throughput_request_status = None
        self.host_mac_value = None
        self.controller_reconnect_mac = 0
        self.pairing_required = False
        self._rumble_write_lock = None
        self._rumble_pending = None
        self._rumble_send_task = None

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

    @property
    def controller_id(self):
        return getattr(self.device, "address", None)

    async def _request_throughput_optimized(self):
        """Request the fastest supported interval without assuming Bleak internals."""
        self._release_preferred_connection_request()
        params = BluetoothLEPreferredConnectionParameters.throughput_optimized
        backend = getattr(self.client, "_backend", None)
        candidates = (
            getattr(self.client, "_device", None),
            getattr(backend, "_device", None),
            getattr(backend, "_requester", None),
        )
        native_device = next(
            (
                item for item in candidates
                if item is not None
                and (
                    hasattr(item, "request_preferred_connection_parameters_async")
                    or hasattr(item, "request_preferred_connection_parameters")
                )
            ),
            None,
        )
        if native_device is None:
            self.throughput_request_status = "UNAVAILABLE"
            print(tr(
                "BLE 高吞吐量請求狀態：無法取得 WinRT 裝置。",
                "BLE high-throughput request: WinRT device unavailable.",
            ))
            return

        method = getattr(
            native_device,
            "request_preferred_connection_parameters_async",
            None,
        )
        if method is None:
            method = getattr(
                native_device,
                "request_preferred_connection_parameters",
                None,
            )
        if method is None:
            self.throughput_request_status = "UNSUPPORTED"
            print(tr(
                "BLE 高吞吐量請求狀態：目前 Windows/Bleak 不支援。",
                "BLE high-throughput request: unsupported by this Windows/Bleak version.",
            ))
            return

        result = method(params)
        if inspect.isawaitable(result):
            result = await result
        self._preferred_connection_request = result
        status = getattr(result, "status", None)
        status_name = (
            getattr(status, "name", None)
            or ("UNKNOWN" if status is None else str(status))
        )
        self.throughput_request_status = status_name
        if status == BluetoothLEPreferredConnectionParametersRequestStatus.SUCCESS:
            print(tr(
                "BLE 高吞吐量請求狀態：成功。",
                "BLE high-throughput request status: success.",
            ))
        else:
            print(tr(
                f"BLE 高吞吐量請求狀態：{status_name}。",
                f"BLE high-throughput request status: {status_name}.",
            ))

    def _release_preferred_connection_request(self):
        request, self._preferred_connection_request = (
            self._preferred_connection_request, None
        )
        if request is None:
            return
        try:
            close = getattr(request, "close", None)
            if close is not None:
                close()
        except Exception as exc:
            print(tr(
                f"釋放 BLE 高吞吐量請求失敗：{exc}",
                f"Could not release BLE high-throughput request: {exc}",
            ))

    async def _start_notify_with_retry(
        self, characteristic, callback, attempts=3, delay=1.0
    ):
        last_error = None
        for attempt in range(1, attempts + 1):
            if self.client is None or not self.client.is_connected:
                raise RuntimeError("控制器在訂閱通知前已中斷。")
            try:
                await self.client.start_notify(characteristic, callback)
                return
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                print(
                    f"BLE 通知訂閱失敗，正在重試 "
                    f"({attempt}/{attempts - 1})：{exc}"
                )
                await asyncio.sleep(delay)
        raise last_error

    @staticmethod
    def _is_windows_cancelled_error(exc):
        """辨識 HRESULT 0x800704C7 / Win32 ERROR_CANCELLED。"""
        codes = [
            getattr(exc, "winerror", None),
            getattr(exc, "hresult", None),
        ]
        codes.extend(
            value for value in getattr(exc, "args", ())
            if isinstance(value, int)
        )
        return any(
            code == 1223
            or (code & 0xFFFFFFFF) == 0x800704C7
            for code in codes
            if code is not None
        )

    # =========================
    # 啟動背景 asyncio
    # =========================

    def open(self):
        if self.running:
            return

        self._closing = False
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
        self._rumble_pending = None
        self._rumble_send_task = None

        try:
            self._loop.run_until_complete(
                self._run()
            )

        finally:
            unexpected_stop = self.running and not self._closing
            self.running = False
            loop = self._loop

            if loop is not None:
                pending = asyncio.all_tasks(
                    loop
                )

                for task in pending:
                    task.cancel()

                if pending:
                    try:
                        loop.run_until_complete(
                            asyncio.gather(
                                *pending,
                                return_exceptions=True
                            )
                        )

                    except Exception:
                        pass

                loop.close()

            if self._loop is loop:
                self._loop = None

            if unexpected_stop and self.bluetooth_unavailable_callback is not None:
                try:
                    self.bluetooth_unavailable_callback()
                except Exception as exc:
                    print(f"藍牙停止回呼錯誤：{exc}")

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
                        "BLE 連線已建立，正在初始化。"
                    )

                    # =========================
                    # 要求 Windows 使用較短的
                    # BLE Connection Interval
                    # =========================

                    try:
                        await self._request_throughput_optimized()

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

                    await self._start_notify_with_retry(
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

                    await self._start_notify_with_retry(
                        INPUT_REPORT_UUID,
                        self._on_input_report
                    )

                    if self.connected_callback is not None:
                        try:
                            self.connected_callback()
                        except Exception as exc:
                            print(f"控制器連線回呼錯誤：{exc}")

                    print()
                    print(
                        "原生藍牙連線成功，輸入已啟動。"
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
                    if not self.running:
                        break

                    error_text = str(exc)

                    # Windows 藍牙已關閉
                    if (
                        "Bluetooth radio is not powered on"
                        in error_text
                    ):
                        print()
                        print("Windows 藍牙已關閉。")
                        print("請開啟藍牙後再重新進入原生藍牙模式。")

                        self.running = False

                        if self.bluetooth_unavailable_callback is not None:
                            try:
                                self.bluetooth_unavailable_callback()
                            except Exception:
                                pass

                        break

                    # 先清理已失效的 GATT Client，避免待處理工作影響下一輪。
                    await self._disconnect()

                    print()
                    if self._is_windows_cancelled_error(exc):
                        print(
                            "BLE 在初始化期間中斷，"
                            "Windows 已取消待處理操作。"
                        )
                        print("這是可自動恢復的暫時斷線。")
                    else:
                        print(
                            "原生藍牙連線失敗：",
                            repr(exc)
                        )

                    print("正在重新搜尋控制器...")
                    await asyncio.sleep(0.5)

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
        queue = asyncio.Queue(maxsize=64)
        loop = asyncio.get_running_loop()

        def enqueue_advertisement(device, advertisement):
            try:
                queue.put_nowait((device, advertisement))
            except asyncio.QueueFull:
                # Advertising repeats frequently; dropping one duplicate is safe.
                pass

        def on_advertisement(device, advertisement):
            try:
                # WinRT may deliver its event on a native callback thread.
                loop.call_soon_threadsafe(
                    enqueue_advertisement, device, advertisement
                )
            except RuntimeError:
                # The event loop is already closing.
                pass

        scanner = BleakScanner(detection_callback=on_advertisement)
        pairing_candidate = None
        pairing_deadline = None
        announced = set()
        await scanner.start()
        try:
            while self.running:
                if (
                    pairing_candidate is not None
                    and time.monotonic() >= pairing_deadline
                ):
                    self.controller_reconnect_mac = 0
                    self.pairing_required = True
                    print("辨識為第一次配對模式。")
                    return pairing_candidate
                timeout = 0.5
                if pairing_deadline is not None:
                    timeout = max(0.0, min(timeout, pairing_deadline - time.monotonic()))
                try:
                    device, advertisement = await asyncio.wait_for(
                        queue.get(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    if pairing_candidate is not None and time.monotonic() >= pairing_deadline:
                        self.controller_reconnect_mac = 0
                        self.pairing_required = True
                        print("辨識為第一次配對模式。")
                        return pairing_candidate
                    if not self._search_message_shown:
                        print("尚未偵測到可連線的手把，搜尋中...")
                        self._search_message_shown = True
                    continue

                reconnect_mac = self._advertised_controller_reconnect_mac(advertisement)
                if reconnect_mac is None:
                    continue
                key = (getattr(device, "address", ""), reconnect_mac)
                if key not in announced:
                    announced.add(key)
                    print(
                        "找到 Switch 2 Pro Controller：",
                        device.address,
                        "reconnect_mac =",
                        f"{reconnect_mac:012X}"
                    )

                if (
                    self.host_mac_value is not None
                    and reconnect_mac == self.host_mac_value
                ):
                    self.controller_reconnect_mac = reconnect_mac
                    self.pairing_required = False
                    print("辨識為已配對手把，將直接重新連線。")
                    return device

                if reconnect_mac == 0:
                    pairing_candidate = device
                    # Give an already-paired advertisement a brief chance to win
                    # without imposing the old fixed five-second scan delay.
                    if pairing_deadline is None:
                        pairing_deadline = time.monotonic() + 0.25
        finally:
            await scanner.stop()
        return None

    @staticmethod
    def _advertised_controller_reconnect_mac(advertisement):
        data = getattr(advertisement, "manufacturer_data", {}).get(
            NINTENDO_MANUFACTURER_ID
        )
        if data is None or len(data) < 16:
            return None
        if int.from_bytes(data[3:5], "little") != NINTENDO_VENDOR_ID:
            return None
        if int.from_bytes(data[5:7], "little") != PRO_CONTROLLER2_PID:
            return None
        return int.from_bytes(data[10:16], "little")


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
        lf_freq = max(0, min(0x1FF, int(lf_freq)))
        hf_freq = max(0, min(0x1FF, int(hf_freq)))
        lf_amp = max(0, min(0x3FF, int(lf_amp)))
        hf_amp = max(0, min(0x3FF, int(hf_amp)))
        value = 0
        value |= lf_freq
        value |= lf_amp << 10
        value |= hf_freq << 20
        value |= hf_amp << 30
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

        state = (lf_freq, lf_amp, hf_freq, hf_amp)
        loop = self._loop

        def publish_latest():
            # Keep only the newest not-yet-sent state. A slow BLE write must
            # never turn real-time audio into an increasingly stale queue.
            self._rumble_pending = state
            task = self._rumble_send_task
            if task is None or task.done():
                self._rumble_send_task = loop.create_task(
                    self._rumble_send_latest_loop()
                )

        try:
            loop.call_soon_threadsafe(publish_latest)
        except RuntimeError:
            return False
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

    async def _rumble_send_latest_loop(self):
        """Serialize BLE writes while discarding superseded rumble states."""
        try:
            while self.connected:
                state = self._rumble_pending
                self._rumble_pending = None
                if state is None:
                    return
                try:
                    await self._send_pro_rumble_async(*state)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Connection recovery is handled by the normal BLE path.
                    pass
        finally:
            self._rumble_send_task = None

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
        self._release_preferred_connection_request()

        # 使用者主動關閉模式時，不當成意外斷線
        if self._closing:
            return

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
        self._release_preferred_connection_request()
        if self.client is None:
            return

        try:
            if self.client.is_connected:
                await self.client.disconnect()

        except Exception:
            pass

        self.connected = False
        self.client = None

    def close(
        self
    ):
        self._closing = True
        self.running = False

        loop = self._loop
        thread = self._thread

        # 如果 asyncio loop 還在執行，
        # 要求它主動斷開控制器
        if (
            loop is not None
            and loop.is_running()
            and not loop.is_closed()
        ):
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._disconnect(),
                    loop
                )

                future.result(timeout=2.0)

            except Exception:
                pass

        # 等待背景執行緒真正結束
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=6.0)

        self._thread = None
        self._loop = None
