import asyncio
import inspect
import threading
import time
from collections import deque
from console_i18n import current_language
from console_i18n import localized_print as print
from rumble_protocol import (
    CONNECTION_FEEDBACK_PATTERN,
    CONNECTION_HF_FREQUENCY,
    CONNECTION_LF_FREQUENCY,
    PIN_FEEDBACK_PATTERN,
    PIN_HF_FREQUENCY,
    PIN_LF_FREQUENCY,
    encode_vibration_frame,
)

from bleak import (
    BleakClient,
    BleakScanner,
)
from winrt.windows.devices.bluetooth import (
    BluetoothAdapter,
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

        self._state_lock = threading.RLock()
        self.connected = False
        self._application_ready = False
        self._connection_generation = 0
        self._rumble_accepting = False
        self._feedback_active = False
        self._disconnect_notification_pending = False
        self.running = False
        self._closing = False
        self._transport_failure_reported = False

        self._preferred_connection_request = None
        self.throughput_request_status = None
        self.host_mac_value = None
        self.controller_reconnect_mac = 0
        self.pairing_required = False
        self._rumble_write_lock = None
        self._rumble_pending = None
        self._rumble_send_task = None
        self._feedback_lock = None
        self._feedback_task = None
        # WinRT throughput_optimized is fixed at 12 BLE units = 15 ms.
        # Pace write starts to that interval so response=False cannot build an
        # opaque queue of stale vibration frames inside the Windows BLE stack.
        self._rumble_interval = 0.015
        self._rumble_diag_lock = threading.Lock()
        self._rumble_submitted = 0
        self._rumble_overwritten = 0
        self._rumble_send_attempts = 0
        self._rumble_send_successes = 0
        self._rumble_send_failures = 0
        self._rumble_last_send = 0.0
        self._rumble_send_intervals_ms = deque(maxlen=512)
        self._rumble_zero_latencies_ms = deque(maxlen=128)

        self._loop = None
        self._thread = None

        self._response_future = None
        self._expected_command = None
        self._player_led_request = None
        self._player_led_applied = None

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

    @property
    def is_ready(self):
        with self._state_lock:
            return self.connected and self._application_ready

    def _connection_is_current(self, generation):
        with self._state_lock:
            return (
                self.running
                and self.connected
                and self._connection_generation == generation
                and self.client is not None
                and self.client.is_connected
            )

    def _commit_application_ready(self, generation):
        """Run the connected callback, then expose input/rumble readiness."""
        callback_started = False
        callback_error = None
        rollback_required = False
        with self._state_lock:
            if not self._connection_is_current(generation):
                return False
            if self.connected_callback is not None:
                callback_started = True
                try:
                    self.connected_callback()
                except Exception as exc:
                    callback_error = exc
            if (
                callback_error is not None
                or not self._connection_is_current(generation)
            ):
                rollback_required = callback_started
            else:
                self._application_ready = True
                self._rumble_accepting = True
                self._feedback_active = False
                return True

        if callback_error is not None:
            print(tr(
                f"控制器連線回呼錯誤：{callback_error}",
                f"Bluetooth connected callback failed: {callback_error}",
            ))
        if rollback_required and self.disconnected_callback is not None:
            try:
                self.disconnected_callback()
            except Exception as exc:
                print(tr(
                    f"控制器連線回滾失敗：{exc}",
                    f"Bluetooth connected rollback failed: {exc}",
                ))
        return False

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
                raise RuntimeError(tr(
                    "控制器在訂閱通知前已中斷。",
                    "The controller disconnected before notification "
                    "subscription.",
                ))
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
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError(tr(
                "先前的原生藍牙執行緒仍在結束中。",
                "The previous native Bluetooth worker is still stopping.",
            ))

        self._closing = False
        self._transport_failure_reported = False
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
        self._feedback_lock = asyncio.Lock()
        self._rumble_pending = None
        self._rumble_send_task = None
        self._feedback_task = None
        with self._rumble_diag_lock:
            self._rumble_submitted = 0
            self._rumble_overwritten = 0
            self._rumble_send_attempts = 0
            self._rumble_send_successes = 0
            self._rumble_send_failures = 0
            self._rumble_last_send = 0.0
            self._rumble_send_intervals_ms.clear()
            self._rumble_zero_latencies_ms.clear()

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

    def _report_transport_failure(self, message):
        if self._transport_failure_reported:
            return
        self._transport_failure_reported = True
        print(message)
        if self._closing:
            return
        if self.bluetooth_unavailable_callback is not None:
            try:
                self.bluetooth_unavailable_callback()
            except Exception as exc:
                print(f"藍牙停止回呼錯誤：{exc}")

    async def _disconnect_with_retry(
        self,
        attempts=3,
        delay=0.2,
    ):
        """Do not permit a new client while an old physical link survives."""
        for attempt in range(1, max(1, int(attempts)) + 1):
            if await self._disconnect():
                return True
            if attempt < attempts:
                print(tr(
                    f"原生藍牙斷線失敗，正在重試（{attempt}/{attempts}）。",
                    "Native Bluetooth disconnect failed; retrying "
                    f"({attempt}/{attempts}).",
                ))
                await asyncio.sleep(delay)
        self.running = False
        self._report_transport_failure(tr(
            "原生藍牙無法安全中斷；已停止連線模組，避免建立第二條連線。",
            "Native Bluetooth could not disconnect safely; the transport "
            "was stopped to prevent a second connection.",
        ))
        return False

    async def _run(
        self
    ):
        try:
            self.host_mac_value = await self._get_local_mac_value()

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
                    if (
                        self.client is not None
                        and not await self._disconnect_with_retry()
                    ):
                        break
                    # 每一輪重新清除狀態
                    with self._state_lock:
                        self.connected = False
                        self._application_ready = False
                        self._rumble_accepting = False
                        self._feedback_active = False
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
                        raise RuntimeError(tr(
                            "原生藍牙連線失敗。",
                            "Native Bluetooth connection failed.",
                        ))

                    with self._state_lock:
                        self.connected = True
                        self._application_ready = False
                        self._rumble_accepting = False
                        self._feedback_active = False
                        self._connection_generation += 1
                        generation = self._connection_generation
                        self._disconnect_notification_pending = False

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
                        lambda sender, data, connection_generation=generation:
                            self._on_command_response(
                                sender,
                                data,
                                generation=connection_generation,
                            )
                    )

                    # =========================
                    # SYNC 模式時重新配對到 Windows
                    # =========================

                    if self.pairing_required:
                        await self._pair_controller(generation)

                    # =========================
                    # 初始化控制器
                    # =========================

                    await self._initialize_controller(generation)

                    # =========================
                    # 訂閱 Input Report
                    # =========================

                    await self._start_notify_with_retry(
                        INPUT_REPORT_UUID,
                        lambda sender, data, connection_generation=generation:
                            self._on_input_report(
                                sender,
                                data,
                                generation=connection_generation,
                            )
                    )

                    # The callback observes is_ready == False.  Disconnect and
                    # readiness are linearized by the same lock, so a stale
                    # generation can never publish a connected state.
                    if not self._commit_application_ready(generation):
                        raise RuntimeError(
                            "BLE connection changed during connected callback."
                        )

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
                    if not await self._disconnect_with_retry():
                        break

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

                if not await self._disconnect_with_retry():
                    break

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
            if self.client is not None:
                await self._disconnect_with_retry()

    # =========================
    # 掃描控制器
    # =========================

    @staticmethod
    async def _get_local_mac_value():
        try:
            # PyBluez reaches a legacy Windows API through a native extension.
            # Some adapters trigger an access violation there, terminating the
            # process before a Python exception can be caught. Use the same
            # WinRT stack as the BLE transport instead.
            adapter = await BluetoothAdapter.get_default_async()
            if adapter is None:
                raise RuntimeError(tr(
                    "找不到本機藍牙介面卡，或藍牙未開啟。",
                    "No local Bluetooth adapter was found, or Bluetooth "
                    "is turned off.",
                ))

            mac_value = int(adapter.bluetooth_address)
            if not 0 <= mac_value <= 0xFFFFFFFFFFFF:
                raise RuntimeError(tr(
                    "Windows 回傳了無效的本機藍牙 MAC。",
                    "Windows returned an invalid local Bluetooth MAC.",
                ))
            mac_hex = f"{mac_value:012X}"
            mac_text = ":".join(
                mac_hex[index:index + 2]
                for index in range(0, len(mac_hex), 2)
            )

            print(
                "本機藍牙 MAC：",
                mac_text
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

            exact_write = next(
                (
                    char for char in write_chars
                    if str(char.uuid).lower()
                    == COMMAND_WRITE_UUID.lower()
                ),
                None,
            )
            exact_response = next(
                (
                    char for char in notify_chars
                    if str(char.uuid).lower()
                    == COMMAND_RESPONSE_UUID.lower()
                ),
                None,
            )

            if exact_write is not None:
                self.command_write_uuid = exact_write.uuid
            elif len(write_chars) >= 2:
                self.command_write_uuid = (
                    write_chars[1].uuid
                )

            elif len(write_chars) == 1:
                self.command_write_uuid = (
                    write_chars[0].uuid
                )

            if exact_response is not None:
                self.command_response_uuid = exact_response.uuid
            elif len(notify_chars) >= 3:
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
        data,
        generation=None,
    ):
        future = (
            self._response_future
        )

        if (
            future is None
            or future.done()
        ):
            return

        if not data or len(data) < 4:
            return

        with self._state_lock:
            current_generation = self._connection_generation
        if generation is None:
            generation = current_generation
        expected = self._expected_command

        if (
            expected is not None
            and (
                generation != current_generation
                or generation != expected[0]
                or data[0] != expected[1]
                or data[3] != expected[2]
            )
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
        timeout=2.0,
        generation=None,
    ):
        with self._state_lock:
            if generation is None:
                generation = self._connection_generation
        if (
            self.client is None
            or not self.client.is_connected
            or not self._connection_is_current(generation)
        ):
            raise RuntimeError(tr(
                "控制器尚未連線。",
                "The controller is not connected.",
            ))

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

        self._expected_command = (
            generation,
            command_id,
            subcommand_id,
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
            self._expected_command = None

        if (
            len(response) < 8
            or response[0] != command_id
            or response[3] != subcommand_id
        ):
            raise RuntimeError(tr(
                f"控制器命令回應格式錯誤：{response.hex()}",
                f"Invalid controller command response: {response.hex()}",
            ))

        return response[8:]

    def set_player_led_mask(self, mask):
        """Schedule an LED update; return true only after its ACK arrived."""
        mask = int(mask) & 0x0F
        if mask == 0:
            return False
        with self._state_lock:
            generation = self._connection_generation
            ready = self.connected and self._application_ready
            loop = self._loop
            request = (generation, mask)
            if not ready or loop is None or not loop.is_running():
                return False
            if self._player_led_applied == request:
                return True
            if (
                self._player_led_request is not None
                and self._player_led_request != self._player_led_applied
            ):
                return False
            self._player_led_request = request

        command = self._write_command(
            0x09,
            0x07,
            bytes([mask, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
            generation=generation,
        )
        try:
            future = asyncio.run_coroutine_threadsafe(command, loop)
        except Exception:
            command.close()
            with self._state_lock:
                if self._player_led_request == request:
                    self._player_led_request = None
            return False

        def finished(result):
            try:
                result.result()
            except Exception:
                with self._state_lock:
                    if self._player_led_request == request:
                        self._player_led_request = None
            else:
                with self._state_lock:
                    if self._player_led_request == request:
                        self._player_led_applied = request

        future.add_done_callback(finished)
        return False

    async def _pair_controller(
        self,
        generation,
    ):
        if self.host_mac_value is None:
            raise RuntimeError(tr(
                "第一次配對需要取得本機藍牙 MAC。",
                "First-time pairing requires the local Bluetooth MAC.",
            ))

        print("正在將 Switch 2 Pro Controller 配對到這台電腦...")
        mac_value = self.host_mac_value

        await self._write_command(
            0x15, 0x01,
            b"\x00\x02"
            + mac_value.to_bytes(6, "little")
            + mac_value.to_bytes(6, "little"),
            generation=generation,
        )
        await self._write_command(
            0x15, 0x04, PAIR_LTK1, generation=generation
        )
        await self._write_command(
            0x15, 0x02, PAIR_LTK2, generation=generation
        )
        await self._write_command(
            0x15, 0x03, b"\x00", generation=generation
        )

        self.pairing_required = False
        print("配對資料已寫入手把；之後按任意按鍵即可喚醒重連。")

    # =========================
    # Tommy Switch 2 初始化
    # =========================

    async def _initialize_controller(
        self,
        generation,
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


        for (
            command_id,
            subcommand_id,
            data
        ) in commands:
            last_error = None
            for attempt in range(1, 4):
                if not self._connection_is_current(generation):
                    raise RuntimeError(tr(
                        "控制器在初始化期間已中斷。",
                        "The controller disconnected during initialization.",
                    ))
                try:
                    await self._write_command(
                        command_id,
                        subcommand_id,
                        data,
                        generation=generation,
                    )
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    print(
                        "初始化命令失敗："
                        f"{command_id:02X}:"
                        f"{subcommand_id:02X} "
                        f"({attempt}/3) {exc}"
                    )
                    if attempt < 3:
                        await asyncio.sleep(0.05)
            if last_error is not None:
                raise RuntimeError(tr(
                    "Switch 2 Pro 必要初始化命令失敗："
                    f"{command_id:02X}:{subcommand_id:02X}",
                    "Mandatory Switch 2 Pro initialization command failed: "
                    f"{command_id:02X}:{subcommand_id:02X}",
                )) from last_error
            await asyncio.sleep(0.01)

    # =========================
    # Input Report
    # =========================

    def _on_input_report(
        self,
        sender,
        data,
        generation=None,
    ):
        if not data:
            return
        with self._state_lock:
            if generation is None:
                generation = self._connection_generation
            ready = (
                self.connected
                and self._application_ready
                and self._connection_generation == generation
            )
        if not ready:
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
        return encode_vibration_frame(lf_freq, lf_amp, hf_freq, hf_amp)

    def send_pro_rumble(
        self, lf_freq, lf_amp, hf_freq, hf_amp
    ):
        with self._state_lock:
            if (
                not self.connected
                or not self._application_ready
                or not self._rumble_accepting
                or self._feedback_active
                or self.client is None
                or self._loop is None
                or not self._loop.is_running()
            ):
                return False
            generation = self._connection_generation

        submitted_at = time.perf_counter()
        state = (
            (lf_freq, lf_amp, hf_freq, hf_amp),
            submitted_at,
            int(lf_amp) <= 0 and int(hf_amp) <= 0,
            generation,
        )
        loop = self._loop

        def publish_latest():
            # Keep only the newest not-yet-sent state. A slow BLE write must
            # never turn real-time audio into an increasingly stale queue.
            with self._state_lock:
                if (
                    not self._rumble_accepting
                    or not self._application_ready
                    or self._feedback_active
                    or self._connection_generation != generation
                ):
                    return
            with self._rumble_diag_lock:
                self._rumble_submitted += 1
                if self._rumble_pending is not None:
                    self._rumble_overwritten += 1
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

    async def _play_fixed_feedback_async(
        self,
        pattern,
        lf_frequency,
        hf_frequency,
        generation,
    ):
        """Override game/audio output until the fixed cue reaches zero."""
        async with self._feedback_lock:
            with self._state_lock:
                if (
                    not self._connection_is_current(generation)
                    or not self._application_ready
                ):
                    return False
                self._feedback_active = True
            with self._rumble_diag_lock:
                self._rumble_pending = None

            normal_task = self._rumble_send_task
            if (
                normal_task is not None
                and not normal_task.done()
                and normal_task is not asyncio.current_task()
            ):
                normal_task.cancel()
                await asyncio.gather(normal_task, return_exceptions=True)
            self._rumble_send_task = None

            completed = True
            try:
                for lf_amp, hf_amp, duration in pattern:
                    with self._state_lock:
                        current = (
                            self._connection_is_current(generation)
                            and self._feedback_active
                        )
                    if not current:
                        completed = False
                        break
                    if not await self._send_pro_rumble_async(
                        lf_frequency,
                        lf_amp,
                        hf_frequency,
                        hf_amp,
                    ):
                        completed = False
                        break
                    if duration:
                        await asyncio.sleep(duration)
            finally:
                with self._state_lock:
                    can_zero = self._connection_is_current(generation)
                if can_zero:
                    try:
                        await self._send_pro_rumble_async(
                            lf_frequency, 0, hf_frequency, 0
                        )
                    except Exception:
                        completed = False
                with self._state_lock:
                    if self._connection_generation == generation:
                        self._feedback_active = False
            return completed

    def _start_fixed_feedback(
        self,
        pattern,
        lf_frequency,
        hf_frequency,
        completed_callback=None,
    ):
        loop = self._loop
        with self._state_lock:
            if (
                not self.connected
                or not self._application_ready
                or loop is None
                or not loop.is_running()
            ):
                return False
            generation = self._connection_generation

        def schedule():
            task = self._feedback_task
            if task is not None and not task.done():
                return
            task = loop.create_task(
                self._play_fixed_feedback_async(
                    pattern,
                    lf_frequency,
                    hf_frequency,
                    generation,
                )
            )
            self._feedback_task = task

            def finished(done):
                if self._feedback_task is done:
                    self._feedback_task = None
                try:
                    completed = bool(done.result())
                except asyncio.CancelledError:
                    return
                except Exception:
                    return
                if (
                    completed
                    and completed_callback is not None
                    and self._connection_is_current(generation)
                    and self.is_ready
                ):
                    completed_callback()

            task.add_done_callback(finished)

        try:
            loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            return False
        return True

    def _finish_connection_feedback(self):
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
                tr(
                    "控制器連線與基本輸入已準備完成。",
                    "Controller connection and basic input are ready.",
                )
            )
            print()
            print("提醒：連線期間請勿關閉此視窗。")
            print("若需要中斷連線，請關閉此視窗。")
            print()
            print(
                "如需校正搖桿，請先關閉本程式，"
                "再按下「校正搖桿」按鈕。"
            )
            print("接著依照校正程序的畫面提示操作即可。")
            print()

    def connection_rumble(self):
        """Play the fixed game-style two-pulse connection cue."""
        return self._start_fixed_feedback(
            CONNECTION_FEEDBACK_PATTERN,
            CONNECTION_LF_FREQUENCY,
            CONNECTION_HF_FREQUENCY,
            completed_callback=self._finish_connection_feedback,
        )

    def pin_rumble(self):
        """Play the same fixed game-style identification cue."""
        return self._start_fixed_feedback(
            PIN_FEEDBACK_PATTERN,
            PIN_LF_FREQUENCY,
            PIN_HF_FREQUENCY,
        )


    @staticmethod
    def _consume_rumble_result(future):
        try:
            future.result()
        except Exception:
            pass

    async def _rumble_send_latest_loop(self):
        """Pace BLE writes while discarding superseded rumble states."""
        try:
            while True:
                with self._state_lock:
                    if (
                        not self.connected
                        or not self._rumble_accepting
                        or self._feedback_active
                    ):
                        return
                if self._rumble_pending is None:
                    return
                with self._rumble_diag_lock:
                    last_send = self._rumble_last_send
                remaining = last_send + self._rumble_interval - time.perf_counter()
                if remaining > 0.0:
                    # Yield to publish_latest while waiting so it can replace
                    # the pending state.  Windows timer wakeups can overshoot
                    # several milliseconds, so enter a short cooperative-yield
                    # guard near the deadline without blocking input callbacks.
                    if remaining > 0.002:
                        await asyncio.sleep(remaining - 0.002)
                    else:
                        await asyncio.sleep(0)
                    continue
                state = self._rumble_pending
                self._rumble_pending = None
                if state is None:
                    return
                values, submitted_at, is_zero, generation = state
                with self._state_lock:
                    if (
                        not self._rumble_accepting
                        or not self._application_ready
                        or self._feedback_active
                        or generation != self._connection_generation
                    ):
                        continue
                started = time.perf_counter()
                with self._rumble_diag_lock:
                    if self._rumble_last_send > 0.0:
                        self._rumble_send_intervals_ms.append(
                            (started - self._rumble_last_send) * 1000.0
                        )
                    self._rumble_last_send = started
                    if is_zero:
                        self._rumble_zero_latencies_ms.append(
                            (started - submitted_at) * 1000.0
                        )
                    self._rumble_send_attempts += 1
                try:
                    sent = await self._send_pro_rumble_async(*values)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Connection recovery is handled by the normal BLE path.
                    sent = False
                with self._rumble_diag_lock:
                    if sent:
                        self._rumble_send_successes += 1
                    else:
                        self._rumble_send_failures += 1
        finally:
            self._rumble_send_task = None

    def get_rumble_diagnostics(self):
        """Return a consistent snapshot of latest-only native BLE output."""
        with self._rumble_diag_lock:
            intervals = list(self._rumble_send_intervals_ms)
            zero_latencies = list(self._rumble_zero_latencies_ms)
            result = {
                "submitted": self._rumble_submitted,
                "overwritten": self._rumble_overwritten,
                "send_attempts": self._rumble_send_attempts,
                "send_successes": self._rumble_send_successes,
                "send_failures": self._rumble_send_failures,
                "interval_samples": len(intervals),
                "zero_latency_samples": len(zero_latencies),
            }

        def add_distribution(prefix, values):
            if not values:
                return
            ordered = sorted(values)
            p95_index = int(0.95 * (len(ordered) - 1))
            result[f"{prefix}_avg_ms"] = sum(values) / len(values)
            result[f"{prefix}_min_ms"] = ordered[0]
            result[f"{prefix}_max_ms"] = ordered[-1]
            result[f"{prefix}_p95_ms"] = ordered[p95_index]

        add_distribution("interval", intervals)
        add_distribution("zero_latency", zero_latencies)
        return result

    def reset_rumble_diagnostics(self):
        """Reset counters after connection haptics and before a measurement."""
        with self._rumble_diag_lock:
            self._rumble_submitted = 0
            self._rumble_overwritten = 0
            self._rumble_send_attempts = 0
            self._rumble_send_successes = 0
            self._rumble_send_failures = 0
            self._rumble_last_send = 0.0
            self._rumble_send_intervals_ms.clear()
            self._rumble_zero_latencies_ms.clear()

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
        with self._state_lock:
            if client is not self.client:
                return
            should_notify = (
                self._application_ready
                or self._disconnect_notification_pending
            )
            self.connected = False
            self._application_ready = False
            self._rumble_accepting = False
            self._feedback_active = False
            self._disconnect_notification_pending = False
            self._connection_generation += 1
        self._release_preferred_connection_request()

        # 使用者主動關閉模式時，不當成意外斷線
        if self._closing or not should_notify:
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
        with self._state_lock:
            client = self.client
            was_ready = self._application_ready
            self._rumble_accepting = False
            self._feedback_active = False
            self._application_ready = False
            self.connected = False
            self._disconnect_notification_pending = (
                self._disconnect_notification_pending or was_ready
            )
            self._connection_generation += 1
        if client is None:
            return True

        with self._rumble_diag_lock:
            self._rumble_pending = None
        feedback_task = self._feedback_task
        if (
            feedback_task is not None
            and not feedback_task.done()
            and feedback_task is not asyncio.current_task()
        ):
            feedback_task.cancel()
            await asyncio.gather(feedback_task, return_exceptions=True)
        self._feedback_task = None
        rumble_task = self._rumble_send_task
        if (
            rumble_task is not None
            and not rumble_task.done()
            and rumble_task is not asyncio.current_task()
        ):
            rumble_task.cancel()
            await asyncio.gather(rumble_task, return_exceptions=True)
        self._rumble_send_task = None

        if client.is_connected:
            try:
                await self._send_pro_rumble_async(
                    CONNECTION_LF_FREQUENCY,
                    0,
                    CONNECTION_HF_FREQUENCY,
                    0,
                )
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass
        success = not client.is_connected

        if success and not client.is_connected:
            # Some Bleak backends schedule the callback after disconnect()
            # returns.  Deliver it here while this client is still current;
            # the eventual duplicate callback is ignored once client is None.
            self._on_disconnected(client)
        with self._state_lock:
            if (
                self.client is client
                and (success or not client.is_connected)
            ):
                self.client = None
            self.connected = False
            self._application_ready = False
            self._rumble_accepting = False
            self._feedback_active = False
        return success

    def disconnect_for_idle(self):
        """Drop only the controller link; keep discovery alive for wake-up."""
        loop = self._loop
        if loop is None or not loop.is_running() or loop.is_closed():
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(self._disconnect(), loop)
            return bool(future.result(timeout=3.0))
        except (
            RuntimeError,
            asyncio.InvalidStateError,
            TimeoutError,
        ):
            return False

    def close(
        self
    ):
        self._closing = True
        self.running = False
        disconnect_ok = True

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

                disconnect_ok = bool(future.result(timeout=2.0))

            except Exception:
                disconnect_ok = False

        # 等待背景執行緒真正結束
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=6.0)

        thread_alive = bool(thread is not None and thread.is_alive())
        if thread_alive:
            print(tr(
                "原生藍牙執行緒未在期限內結束；保留執行緒與 event loop reference。",
                "The native Bluetooth worker did not stop before the deadline; "
                "its thread and event-loop references were retained.",
            ))
            return False

        self._thread = None
        self._loop = None
        return disconnect_ok
