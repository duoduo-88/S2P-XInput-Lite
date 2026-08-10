import json
import ctypes
import os
import serial
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

NINTENDO_COMPANY_ID = 0x0553
NINTENDO_VENDOR_ID = 0x057E
PRO_CONTROLLER2_PID = 0x2069
SERIAL_RECEIVE_BUFFER_LIMIT = 64 * 1024


def _tr(zh, en):
    return en if current_language() == "en" else zh
import threading
import time


def _set_current_thread_priority(level):
    """Best-effort Windows thread priority without raising the whole process."""
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(
            kernel32.GetCurrentThread(),
            int(level),
        )
    except Exception:
        pass


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
        self._state_lock = threading.RLock()
        self.connected_channel = None
        self._ready_channel = None
        self._connection_generation = 0
        self._connecting = False
        self._write_lock = threading.Lock()
        self._rumble_packet_id = 0

        # Host-side latest-only output modes:
        #
        # * direct `wr`: bypasses the firmware's five-packet `rs` FIFO.
        #   Audio updates use ~60 Hz (16.6 ms); native game changes and zero
        #   frames use the controller's 7.5 ms BLE interval as a priority path.
        # * shadow `rs`: retained as a compatibility fallback and limited to
        #   16 ms, matching the firmware's 15 ms playout cadence.
        #
        # Calls may still arrive every ~5 ms. `_rumble_pending` is one slot, so
        # a newer state always replaces an unsent older state.
        self._rumble_direct_interval = 0.0166
        self._rumble_priority_interval = 0.0075
        self._rumble_shadow_interval = 0.016
        self._rumble_condition = threading.Condition()
        self._rumble_send_lock = threading.Lock()
        self._feedback_lock = threading.Lock()
        self._feedback_active = False
        self._feedback_sequence = 0
        self._rumble_pending = None
        self._rumble_last_send = 0.0
        self._rumble_worker_running = False
        self._rumble_accepting = False
        self._rumble_thread = None
        self._rumble_submitted = 0
        self._rumble_overwritten = 0
        self._rumble_send_attempts = 0
        self._rumble_send_successes = 0
        self._rumble_send_failures = 0
        self._rumble_send_intervals_ms = deque(maxlen=512)
        self._rumble_direct_intervals_ms = deque(maxlen=512)
        self._rumble_priority_intervals_ms = deque(maxlen=512)
        self._rumble_zero_latencies_ms = deque(maxlen=128)

        self.esp32_mac = None
        self.esp32_mac_value = None
        self.controller_id = None
        self._pending_pair = False
        self._read_thread = None
        self._heartbeat_thread = None
        self._status_event = threading.Event()
        self._disconnect_event = threading.Event()
        self._command_response_event = threading.Event()
        self._command_response_lock = threading.Lock()
        self._command_wait_lock = threading.Lock()
        self._command_response = None
        self._status_misses = 0
        self._channel_missing_count = 0
        self._last_input_time = 0.0
        self._status_grace_until = 0.0
        self._closing = False
        self._bridge_failure_reported = False
        self._last_foreign_pairing_notice = None
        self._diagnostic_lock = threading.Lock()
        self._diagnostic_snapshot = {}
        self._diagnostic_last_poll = 0.0

    @property
    def is_ready(self):
        """Whether the current BLE link completed controller initialization."""
        with self._state_lock:
            return (
                self.connected_channel is not None
                and self._ready_channel == self.connected_channel
            )

    def open(self):
        stale_threads = (
            self._read_thread,
            self._heartbeat_thread,
            self._rumble_thread,
        )
        if any(thread is not None and thread.is_alive() for thread in stale_threads):
            raise RuntimeError(
                "Previous ESP32 bridge workers are still running."
            )
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
        with self._rumble_condition:
            self._rumble_pending = None
            self._rumble_last_send = 0.0
            self._rumble_worker_running = True
            self._rumble_accepting = False
            self._feedback_active = False
            self._rumble_submitted = 0
            self._rumble_overwritten = 0
            self._rumble_send_attempts = 0
            self._rumble_send_successes = 0
            self._rumble_send_failures = 0
            self._rumble_send_intervals_ms.clear()
            self._rumble_direct_intervals_ms.clear()
            self._rumble_priority_intervals_ms.clear()
            self._rumble_zero_latencies_ms.clear()
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
        self._rumble_thread = threading.Thread(
            target=self._rumble_output_loop,
            daemon=True,
            name="ESP32BridgeRumble",
        )
        self._read_thread.start()
        self._heartbeat_thread.start()
        self._rumble_thread.start()

    def close(self, timeout=2.5):
        deadline = time.perf_counter() + max(0.0, float(timeout))
        self._closing = True

        # Stop accepting work before sending the final zero frame.  The send
        # lock orders an already-claimed worker state before that zero frame,
        # so no delayed non-zero state can follow it.
        with self._rumble_condition:
            self._rumble_accepting = False
            self._feedback_active = False
            self._rumble_worker_running = False
            self._rumble_pending = None
            self._rumble_condition.notify_all()
        rumble_thread = self._rumble_thread
        heartbeat_thread = self._heartbeat_thread
        read_thread = self._read_thread
        if (
            rumble_thread is not None
            and rumble_thread.is_alive()
            and threading.current_thread() is not rumble_thread
        ):
            rumble_thread.join(
                timeout=max(0.0, deadline - time.perf_counter())
            )
        rumble_alive = bool(rumble_thread and rumble_thread.is_alive())

        with self._state_lock:
            has_connection = self.connected_channel is not None
        zero_sent = not has_connection
        if self.running and has_connection and not rumble_alive:
            zero_sent = bool(self._send_final_zero_rumble(
                timeout=max(0.0, deadline - time.perf_counter())
            ))
            time.sleep(0.02)

        # Ask the firmware to release the physical BLE link before dropping
        # USB CDC.  Otherwise the controller can remain attached to the ESP32.
        disconnect_confirmed = not has_connection
        if self.running:
            self.send("auto off")
            self._disconnect_event.clear()
            disconnect_requested = self.send("ble disconnect")
            if has_connection and disconnect_requested:
                disconnect_confirmed = self._disconnect_event.wait(
                    timeout=min(
                        0.75,
                        max(0.0, deadline - time.perf_counter()),
                    )
                )
            if has_connection and not disconnect_confirmed:
                print(_tr(
                    "ESP32 未在關閉期限內確認 BLE 已中斷。",
                    "ESP32 did not confirm BLE disconnection before shutdown.",
                ))

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
        with self._state_lock:
            self.connected_channel = None
            self._ready_channel = None
            self._connection_generation += 1
            self.controller_id = None
            self._connecting = False
            self._pending_pair = False
        self._disconnect_event.set()
        for thread in (heartbeat_thread, read_thread):
            if (
                thread is not None
                and thread.is_alive()
                and threading.current_thread() is not thread
            ):
                thread.join(
                    timeout=max(0.0, deadline - time.perf_counter())
                )
        heartbeat_alive = bool(
            heartbeat_thread and heartbeat_thread.is_alive()
        )
        read_alive = bool(read_thread and read_thread.is_alive())
        self._rumble_thread = rumble_thread if rumble_alive else None
        self._heartbeat_thread = (
            heartbeat_thread if heartbeat_alive else None
        )
        self._read_thread = read_thread if read_alive else None
        return bool(
            not rumble_alive
            and not heartbeat_alive
            and not read_alive
            and zero_sent
            and disconnect_confirmed
        )
        
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
        return encode_vibration_frame(lf_freq, lf_amp, hf_freq, hf_amp)

    def _next_rumble_packet_id(self):
        """Return one packet id without racing connection and worker output."""
        with self._rumble_condition:
            packet_id = 0x50 + (self._rumble_packet_id & 0x0F)
            self._rumble_packet_id = (self._rumble_packet_id + 1) & 0x0F
        return packet_id

    def send_pro_rumble(
        self,
        lf_freq,
        lf_amp,
        hf_freq,
        hf_amp
    ):
        with self._rumble_send_lock:
            with self._rumble_condition:
                if (
                    not self._rumble_accepting
                    or self._feedback_active
                ):
                    return False
            return self._send_pro_rumble_now(
                lf_freq,
                lf_amp,
                hf_freq,
                hf_amp,
            )

    def _send_pro_rumble_now(
        self,
        lf_freq,
        lf_amp,
        hf_freq,
        hf_amp,
        channel=None,
        generation=None,
    ):
        """Synchronously send rumble, bypassing the accepting gate."""
        with self._state_lock:
            current_channel = self.connected_channel
            current_generation = self._connection_generation
            if channel is None:
                channel = current_channel
            if generation is None:
                generation = current_generation
            if (
                current_channel != channel
                or current_generation != generation
            ):
                return False

        if channel is None:
            return False

        vibration = self._encode_vibration(
            lf_freq,
            lf_amp,
            hf_freq,
            hf_amp
        )

        packet_id = self._next_rumble_packet_id()

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

        with self._state_lock:
            if (
                self.connected_channel != channel
                or self._connection_generation != generation
            ):
                return False
            return self.send(command)

    def _queue_latest_rumble(
        self,
        route,
        lf_freq,
        lf_amp,
        hf_freq,
        hf_amp,
        priority=False,
        force_zero=False,
    ):
        """Replace the unsent rumble state for the selected output route."""
        with self._state_lock:
            channel = self.connected_channel
            generation = self._connection_generation
        if (
            not self.running
            or self.serial is None
            or not self.serial.is_open
            or channel is None
        ):
            return False

        lf_amp = int(lf_amp)
        hf_amp = int(hf_amp)
        is_zero = lf_amp <= 0 and hf_amp <= 0
        submitted_at = time.perf_counter()
        state = (
            route,
            int(channel),
            int(lf_freq),
            lf_amp,
            int(hf_freq),
            hf_amp,
            bool(priority or is_zero),
            bool(force_zero or is_zero),
            submitted_at,
            submitted_at,
            generation,
        )
        with self._rumble_condition:
            if (
                not self._rumble_worker_running
                or not self._rumble_accepting
                or self._feedback_active
            ):
                return False
            self._rumble_submitted += 1
            if self._rumble_pending is not None:
                self._rumble_overwritten += 1
                if self._rumble_pending[10] == generation:
                    # Keep the latest payload and zero status, but preserve
                    # urgency for this continuously pending generation.
                    state = (
                        state[:6]
                        + (
                            bool(state[6] or self._rumble_pending[6]),
                            state[7],
                            state[8],
                            self._rumble_pending[9],
                            generation,
                        )
                    )
            self._rumble_pending = state
            self._rumble_condition.notify_all()
        return True

    def send_pro_rumble_latest(
        self,
        lf_freq,
        lf_amp,
        hf_freq,
        hf_amp,
        priority=False,
        force_zero=False,
    ):
        """Use direct firmware `wr` while keeping only the latest state.

        Audio-style updates use 16.6 ms. Native game changes and zero frames
        can request the 7.5 ms priority cadence without building a FIFO.
        """
        return self._queue_latest_rumble(
            "wr",
            lf_freq,
            lf_amp,
            hf_freq,
            hf_amp,
            priority=priority,
            force_zero=force_zero,
        )

    def send_rumble_shadow(
        self,
        lf_freq,
        lf_amp,
        hf_freq,
        hf_amp,
        priority=False,
        force_zero=False,
    ):
        """Use firmware `rs`, keeping only the latest host-side state."""
        return self._queue_latest_rumble(
            "rs",
            lf_freq,
            lf_amp,
            hf_freq,
            hf_amp,
            priority=priority,
            force_zero=force_zero,
        )

    def _send_rumble_state_now(self, state):
        """Encode and synchronously send one already-throttled state."""
        (
            route, channel, lf_freq, lf_amp, hf_freq, hf_amp,
            _priority, _force_zero, _submitted_at, _pending_since,
            generation,
        ) = state
        with self._state_lock:
            if (
                self.connected_channel != channel
                or self._connection_generation != generation
            ):
                return False

        vibration = self._encode_vibration(
            lf_freq, lf_amp, hf_freq, hf_amp
        )
        packet_id = self._next_rumble_packet_id()
        motor_vibrations = bytes([packet_id]) + vibration * 3
        payload = b"\x00" + motor_vibrations + motor_vibrations

        if route == "wr":
            command = f"wr {int(channel)} r {payload.hex()}"
        else:
            command = f"rs {int(channel)} {payload.hex()}"
        with self._state_lock:
            if (
                self.connected_channel != channel
                or self._connection_generation != generation
            ):
                return False
            return self.send(command)

    def _rumble_output_loop(self):
        """Send the newest state using a high-resolution deadline wait.

        ``threading.Condition.wait(timeout=...)`` is intentionally not used for
        active pacing here.  On this bundled Windows runtime its timeout, and
        ``time.monotonic()``, use the roughly 15.6 ms system tick: 7.5 ms becomes
        about 15.6 ms and 16.6 ms becomes about 31 ms.  QPC-backed
        ``time.perf_counter()`` plus ``time.sleep`` provides high-resolution
        deadlines.  Sleeping in at most 1 ms slices also lets a newly queued
        game/zero frame shorten an in-progress audio deadline promptly.
        """
        _set_current_thread_priority(1)  # ABOVE_NORMAL, rumble only
        while True:
            with self._rumble_condition:
                while (
                    self._rumble_worker_running
                    and self._rumble_pending is None
                ):
                    self._rumble_condition.wait()

                if not self._rumble_worker_running:
                    return

                route = self._rumble_pending[0]
                priority = self._rumble_pending[6]
                if route == "wr":
                    interval = (
                        self._rumble_priority_interval
                        if priority
                        else self._rumble_direct_interval
                    )
                else:
                    interval = self._rumble_shadow_interval
                # The bundled Python implements time.monotonic() with
                # GetTickCount64 (15.625 ms resolution).  QPC-backed
                # perf_counter() is required for 7.5/16.6 ms deadlines.
                now = time.perf_counter()
                # A priority/zero frame recomputes the deadline against 7.5 ms,
                # interrupting an in-progress 16.6 ms audio wait.
                due_time = self._rumble_last_send + interval
                remaining = due_time - now
                if remaining > 0:
                    sleep_for = min(remaining, 0.001)
                else:
                    sleep_for = 0.0

            if sleep_for > 0.0:
                # Keep the condition available to producers while the high-
                # resolution timer runs.  The next slice re-reads the latest
                # route/priority state and recomputes its deadline.
                time.sleep(sleep_for)
                continue

            # Keep the slot replaceable while a feedback cue or shutdown zero
            # owns the send lock. Re-read the latest state and its deadline
            # after acquiring the lock, then claim immediately before send.
            attempted = False
            sent = False
            sleep_for = 0.0
            with self._rumble_send_lock:
                with self._state_lock:
                    with self._rumble_condition:
                        if not self._rumble_worker_running:
                            return
                        state = self._rumble_pending
                        current_generation = (
                            state is not None
                            and self.connected_channel == state[1]
                            and self._connection_generation == state[10]
                        )
                        accepting = (
                            current_generation
                            and self._rumble_accepting
                            and not self._feedback_active
                        )
                        if state is not None and not accepting:
                            self._rumble_pending = None
                        elif state is not None:
                            route = state[0]
                            priority = state[6]
                            if route == "wr":
                                interval = (
                                    self._rumble_priority_interval
                                    if priority
                                    else self._rumble_direct_interval
                                )
                            else:
                                interval = self._rumble_shadow_interval
                            due_time = self._rumble_last_send + interval
                            remaining = (
                                due_time - time.perf_counter()
                            )
                            if remaining > 0.0:
                                sleep_for = min(remaining, 0.001)
                            else:
                                self._rumble_pending = None
                                send_started = time.perf_counter()
                                # Only actual send attempts contribute timing.
                                # Taking this timestamp after the send lock also
                                # includes any time spent waiting behind it.
                                if (
                                    self._rumble_last_send > 0.0
                                    and state[9] < due_time
                                ):
                                    interval_ms = (
                                        send_started
                                        - self._rumble_last_send
                                    ) * 1000.0
                                    self._rumble_send_intervals_ms.append(
                                        interval_ms
                                    )
                                    if state[6]:
                                        self._rumble_priority_intervals_ms.append(
                                            interval_ms
                                        )
                                    else:
                                        self._rumble_direct_intervals_ms.append(
                                            interval_ms
                                        )
                                self._rumble_last_send = send_started
                                if state[7]:
                                    self._rumble_zero_latencies_ms.append(
                                        (
                                            send_started - state[8]
                                        ) * 1000.0
                                    )
                                self._rumble_send_attempts += 1
                                attempted = True
                    if attempted:
                        sent = self._send_rumble_state_now(state)
            if sleep_for > 0.0:
                time.sleep(sleep_for)
                continue
            if attempted:
                with self._rumble_condition:
                    if sent:
                        self._rumble_send_successes += 1
                    else:
                        self._rumble_send_failures += 1

    def _send_final_zero_rumble(self, timeout=None):
        """Send a zero frame after all accepted worker output."""
        if timeout is None:
            acquired = self._rumble_send_lock.acquire()
        else:
            acquired = self._rumble_send_lock.acquire(
                timeout=max(0.0, float(timeout))
            )
        if not acquired:
            return False
        try:
            return self._send_pro_rumble_now(
                CONNECTION_LF_FREQUENCY,
                0,
                CONNECTION_HF_FREQUENCY,
                0,
            )
        finally:
            self._rumble_send_lock.release()

    def get_rumble_diagnostics(self):
        """Return a thread-safe snapshot of low-latency output diagnostics."""
        with self._rumble_condition:
            intervals = list(self._rumble_send_intervals_ms)
            direct_intervals = list(self._rumble_direct_intervals_ms)
            priority_intervals = list(self._rumble_priority_intervals_ms)
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
        add_distribution("direct_interval", direct_intervals)
        add_distribution("priority_interval", priority_intervals)
        add_distribution("zero_latency", zero_latencies)
        return result

    def start_diagnostics(self):
        """Reset firmware counters and run non-mutating algorithm self-tests."""
        with self._diagnostic_lock:
            self._diagnostic_snapshot = {
                "state": "running",
                "started_at": time.time(),
                "self_tests": [],
            }
            self._diagnostic_last_poll = 0.0
        commands = (
            "latency reset",
            "ble timing reset",
            "rumble reset",
            "algorithm test stick L 2048 2048 0 2048 2048 8",
            "algorithm test stick R 2048 2048 0 2048 2048 8",
            "algorithm test direction L 0 0 0 0",
            "gyro test reset",
        )
        return all(self.send(command) for command in commands)

    def poll_diagnostics(self, minimum_interval=0.25):
        """Request one asynchronous firmware diagnostic snapshot."""
        now = time.monotonic()
        with self._diagnostic_lock:
            if self._diagnostic_snapshot.get("state") != "running":
                return False
            if now - self._diagnostic_last_poll < float(minimum_interval):
                return False
            self._diagnostic_last_poll = now
        commands = (
            "capabilities",
            "runtime status",
            "latency status",
            "ble timing",
            "link status",
            "rumble status",
            "profile status",
        )
        return all(self.send(command) for command in commands)

    def stop_diagnostics(self):
        with self._diagnostic_lock:
            if self._diagnostic_snapshot:
                self._diagnostic_snapshot["state"] = "stopped"
                self._diagnostic_snapshot["ended_at"] = time.time()

    def get_firmware_diagnostics(self):
        with self._diagnostic_lock:
            return json.loads(json.dumps(self._diagnostic_snapshot))

    def _store_diagnostic_response(self, command, response):
        if not hasattr(self, "_diagnostic_lock"):
            return
        keys = {
            "capabilities",
            "runtime_status",
            "latency_status",
            "ble_timing",
            "link_status",
            "rumble_status",
            "profile_status",
        }
        with self._diagnostic_lock:
            if command in {"algorithm_test", "gyro_test"}:
                tests = self._diagnostic_snapshot.setdefault(
                    "self_tests", []
                )
                tests.append(response)
                del tests[:-16]
            elif command in keys:
                self._diagnostic_snapshot[command] = response
            elif command in {
                "latency_reset", "ble_timing_reset", "rumble_reset"
            }:
                resets = self._diagnostic_snapshot.setdefault("resets", [])
                resets.append(command)
            else:
                return
            self._diagnostic_snapshot["state"] = "running"
            self._diagnostic_snapshot["updated_at"] = time.time()

    def _report_bridge_failure(self):
        if self._bridge_failure_reported or self._closing:
            return
        self._bridge_failure_reported = True
        self.running = False
        with self._state_lock:
            self.connected_channel = None
            self._ready_channel = None
            self._connection_generation += 1
            self._connecting = False
        self._disconnect_event.set()
        with self._rumble_condition:
            self._rumble_accepting = False
            self._feedback_active = False
            self._rumble_pending = None
            self._rumble_condition.notify_all()
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

            # Input traffic already proves the transport is alive. Poll only
            # once per second while reports are flowing, and at 2 Hz while idle.
            now = time.monotonic()
            poll_interval = (
                1.0 if now - self._last_input_time < 2.0 else 0.5
            )
            deadline = now + poll_interval
            while self.running and time.monotonic() < deadline:
                time.sleep(0.05)

    def _send_controller_command_wait_ack(
        self,
        channel,
        generation,
        command_id,
        subcommand_id,
        command_data,
        timeout=2.0,
    ):
        payload = (
            bytes([command_id])
            + b"\x91\x01"
            + bytes([subcommand_id])
            + b"\x00"
            + bytes([len(command_data)])
            + b"\x00\x00"
            + command_data
        )
        # There is one firmware command-response stream.  Serialize waiters so
        # an old preparation cannot clear or consume the ACK of a new link that
        # happens to reuse the same channel number.
        with self._command_wait_lock:
            if not self._connection_is_current(channel, generation):
                return False
            self._command_response_event.clear()
            with self._command_response_lock:
                self._command_response = None
            if not self.send(f"wr {int(channel)} c {payload.hex()}"):
                return False

            deadline = time.monotonic() + timeout
            while (
                self._connection_is_current(channel, generation)
                and time.monotonic() < deadline
            ):
                remaining = max(0.0, deadline - time.monotonic())
                if not self._command_response_event.wait(remaining):
                    break
                self._command_response_event.clear()
                with self._command_response_lock:
                    response_entry = self._command_response
                    self._command_response = None
                if response_entry is None:
                    continue
                response_generation, response = response_entry
                if (
                    response_generation == generation
                    and len(response) >= 4
                    and response[0] == command_id
                    and response[3] == subcommand_id
                ):
                    return True
        return False

    def pair_controller_to_esp32(self, channel, generation):
        """將 Switch 2 Pro Controller 配對到目前的 ESP32。"""
        if not self._connection_is_current(channel, generation):
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

        commands = (
            (0x01, pair_mac_data),
            (0x04, ltk1),
            (0x02, ltk2),
            (0x03, b"\x00"),
        )
        for subcommand_id, command_data in commands:
            if not self._connection_is_current(channel, generation):
                return False
            if not self._send_controller_command_wait_ack(
                channel,
                generation,
                0x15,
                subcommand_id,
                command_data,
            ):
                print(_tr(
                    "ESP32 配對命令未收到回應："
                    f"0x15/0x{subcommand_id:02X}",
                    "ESP32 pairing command was not acknowledged: "
                    f"0x15/0x{subcommand_id:02X}",
                ))
                return False
            time.sleep(0.02)

        print(
            "ESP32 配對資料已送出。"
        )

        with self._state_lock:
            if self._connection_is_current(channel, generation):
                self._pending_pair = False

        return True

    def initialize_controller_features(self, channel, generation):
        """Enable the normal Switch 2 input stream, including motion data."""
        if not self._connection_is_current(channel, generation):
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
            if not self._connection_is_current(channel, generation):
                return False
            if not self._send_controller_command_wait_ack(
                channel,
                generation,
                command_id,
                subcommand_id,
                command_data,
            ):
                print(
                    "控制器初始化命令未收到回應："
                    f"0x{command_id:02X}/0x{subcommand_id:02X}"
                )
                return False

            time.sleep(0.02)

        print(_tr(
            "控制器功能命令初始化完成，陀螺儀資料串流已啟用。",
            "Controller feature commands initialized; the gyro data stream is enabled.",
        ))
        return True

    def set_player_led_mask(self, mask):
        """Set a cumulative player-LED mask (used as a battery bar)."""
        mask = int(mask) & 0x0F
        if mask == 0:
            return False
        with self._state_lock:
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

        command_data = bytes([
            mask,
            0x00,
            0x00,
            0x00,
            0x00,
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
            return True

        return False

    def set_player_led_1(self):
        """Backward-compatible Player 1 LED helper."""
        return self.set_player_led_mask(0x01)

    def _reserve_feedback(self, expected_generation=None):
        """Reserve one cue for exactly one ready connection generation."""
        with self._rumble_send_lock:
            with self._state_lock:
                channel = self.connected_channel
                generation = self._connection_generation
                ready = (
                    channel is not None
                    and self._ready_channel == channel
                    and not self._closing
                )
                if (
                    not ready
                    or (
                        expected_generation is not None
                        and generation != expected_generation
                    )
                ):
                    return None
                with self._rumble_condition:
                    if not self._rumble_accepting:
                        return None
                    self._feedback_sequence += 1
                    token = self._feedback_sequence
                    self._feedback_active = True
                    self._rumble_pending = None
                    self._rumble_condition.notify_all()
        return channel, generation, token

    def _send_feedback_rumble_now(
        self,
        lf_freq,
        lf_amp,
        hf_freq,
        hf_amp,
        channel,
        generation,
        token,
    ):
        with self._rumble_send_lock:
            with self._state_lock:
                if (
                    self.connected_channel != channel
                    or self._ready_channel != channel
                    or self._connection_generation != generation
                    or self._closing
                ):
                    return False
            with self._rumble_condition:
                if (
                    not self._rumble_accepting
                    or not self._feedback_active
                    or token != self._feedback_sequence
                ):
                    return False
            return self._send_pro_rumble_now(
                lf_freq,
                lf_amp,
                hf_freq,
                hf_amp,
                channel=channel,
                generation=generation,
            )

    def _play_fixed_feedback(
        self,
        pattern,
        lf_frequency,
        hf_frequency,
        channel=None,
        generation=None,
        token=None,
    ):
        """Override mixed/audio updates until the fixed cue reaches zero."""
        if channel is None or generation is None or token is None:
            reservation = self._reserve_feedback()
            if reservation is None:
                return False
            channel, generation, token = reservation
        with self._feedback_lock:
            with self._state_lock:
                current = (
                    self.connected_channel == channel
                    and self._ready_channel == channel
                    and self._connection_generation == generation
                    and not self._closing
                )
            with self._rumble_condition:
                if (
                    not current
                    or not self._rumble_accepting
                    or not self._feedback_active
                    or token != self._feedback_sequence
                ):
                    return False
            completed = True
            try:
                for lf_amp, hf_amp, duration in pattern:
                    if not self._send_feedback_rumble_now(
                        lf_frequency,
                        lf_amp,
                        hf_frequency,
                        hf_amp,
                        channel,
                        generation,
                        token,
                    ):
                        completed = False
                        break
                    if duration:
                        time.sleep(duration)
            finally:
                self._send_feedback_rumble_now(
                    lf_frequency,
                    0,
                    hf_frequency,
                    0,
                    channel,
                    generation,
                    token,
                )
                with self._rumble_condition:
                    if token == self._feedback_sequence:
                        self._feedback_active = False
                        self._rumble_condition.notify_all()
            return completed

    def connection_rumble(self, expected_generation=None):
        """Play the fixed game-style two-pulse connection cue."""
        reservation = self._reserve_feedback(expected_generation)
        if reservation is None:
            return False
        channel, generation, token = reservation

        def worker():
            completed = self._play_fixed_feedback(
                CONNECTION_FEEDBACK_PATTERN,
                CONNECTION_LF_FREQUENCY,
                CONNECTION_HF_FREQUENCY,
                channel,
                generation,
                token,
            )

            if (
                not completed
                or not self._connection_is_current(channel, generation)
            ):
                return
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
                print(_tr(
                    "控制器連線與基本輸入已準備完成。",
                    "Controller connection and basic input are ready.",
                ))
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

        threading.Thread(target=worker, daemon=True).start()
        return True

    def pin_rumble(self):
        """Play the same fixed game-style identification cue."""
        reservation = self._reserve_feedback()
        if reservation is None:
            return False
        threading.Thread(
            target=self._play_fixed_feedback,
            args=(
                PIN_FEEDBACK_PATTERN,
                PIN_LF_FREQUENCY,
                PIN_HF_FREQUENCY,
                *reservation,
            ),
            daemon=True,
        ).start()
        return True

    def _parse_reconnect_mac(self, data_hex):
        try:
            data = bytes.fromhex(data_hex)
            index = 0

            while index < len(data):
                field_len = data[index]
                if field_len == 0:
                    break
                field_end = index + field_len + 1
                if field_end > len(data) or index + 1 >= len(data):
                    break

                ad_type = data[index + 1]
                if ad_type == 0xFF:
                    manufacturer = data[index + 2:field_end]
                    if (
                        len(manufacturer) >= 18
                        and int.from_bytes(manufacturer[0:2], "little")
                        == NINTENDO_COMPANY_ID
                        and int.from_bytes(manufacturer[5:7], "little")
                        == NINTENDO_VENDOR_ID
                        and int.from_bytes(manufacturer[7:9], "little")
                        == PRO_CONTROLLER2_PID
                    ):
                        return int.from_bytes(
                            manufacturer[12:18],
                            byteorder="little",
                        )

                index = field_end

            return None

        except Exception:
            return None


    def start_scan(self):
        self.send("auto off")
        self.send("ble disconnect")
        self.send("status lite")
        self.send("scan on")

    def disconnect_for_idle(self):
        """Stop output and confirm that the physical BLE link was dropped."""
        if not self.running:
            return False
        with self._state_lock:
            channel = self.connected_channel
            generation = self._connection_generation
            ready = (
                channel is not None
                and self._ready_channel == channel
            )
        if channel is None:
            return True
        self._disconnect_event.clear()
        with self._rumble_condition:
            self._rumble_accepting = False
            self._feedback_active = False
            self._rumble_pending = None
            self._rumble_condition.notify_all()

        try:
            zero_sent = self._send_final_zero_rumble(timeout=0.5)
        except (OSError, serial.SerialException):
            zero_sent = False
        if not zero_sent:
            self._restore_rumble_after_idle_failure(
                channel, generation, ready
            )
            return False
        time.sleep(0.02)
        if not self.send("ble disconnect"):
            self._restore_rumble_after_idle_failure(
                channel, generation, ready
            )
            return False
        disconnected = self._disconnect_event.wait(timeout=2.0)
        if not disconnected:
            self._restore_rumble_after_idle_failure(
                channel, generation, ready
            )
        return disconnected

    def _restore_rumble_after_idle_failure(
        self,
        channel,
        generation,
        was_ready,
    ):
        """Reopen output only if the failed idle request left the same link."""
        if not was_ready:
            return False
        with self._state_lock:
            current = (
                self.running
                and not self._closing
                and self.connected_channel == channel
                and self._ready_channel == channel
                and self._connection_generation == generation
            )
            if not current:
                return False
            with self._rumble_condition:
                if not self._rumble_worker_running:
                    return False
                self._rumble_accepting = True
                self._rumble_condition.notify_all()
        return True

    @staticmethod
    def _limit_receive_buffer(buf):
        """Bound corrupt CDC input while preserving a possible packet tail."""
        if len(buf) <= SERIAL_RECEIVE_BUFFER_LIMIT:
            return
        search_start = len(buf) - SERIAL_RECEIVE_BUFFER_LIMIT
        header = buf.rfind(b"\xaa\x55", search_start)
        if header >= 0:
            del buf[:header]
        elif buf.endswith(b"\xaa"):
            buf[:] = b"\xaa"
        else:
            buf.clear()

    def _read_loop(self):
        # Match Tommy's low-latency serial strategy: wait for the first byte
        # instead of polling every 1 ms, then drain everything already queued.
        _set_current_thread_priority(2)  # HIGHEST, reader only
        buf = bytearray()

        while self.running:
            try:
                first = self.serial.read(1)
                if not first:
                    continue
                buf.extend(first)
                waiting = self.serial.in_waiting
                if waiting:
                    buf.extend(self.serial.read(waiting))
                self._limit_receive_buffer(buf)

            except (
                serial.SerialException,
                OSError
            ):
                if self.running:
                    print()
                    print("ESP32 已中斷連線。")

                self._report_bridge_failure()

                break

            # Parse every complete CDC frame already present before dispatching
            # controller input.  A single input report may take the direct idle
            # path; multiple reports are submitted together so stale stick/IMU
            # states can be coalesced before any callback runs.
            input_batch = []
            consumed = 0
            while True:
                nl = buf.find(b"\n", consumed)
                hdr = buf.find(b"\xaa\x55", consumed)

                if (
                    nl != -1
                    and (
                        hdr == -1
                        or nl < hdr
                    )
                ):
                    line = bytes(buf[consumed:nl + 1])
                    consumed = nl + 1

                    self._handle_text(
                        line
                    )
                    continue

                if hdr == consumed:
                    if len(buf) - consumed < 3:
                        break

                    packet_len = buf[consumed + 2]
                    total = 3 + packet_len

                    if len(buf) - consumed < total:
                        break

                    packet_end = consumed + total
                    data = bytes(buf[consumed + 3:packet_end])
                    consumed = packet_end

                    with self._state_lock:
                        packet_generation = self._connection_generation
                    payload = self._handle_binary(
                        data,
                        dispatch=False,
                    )
                    if payload is not None:
                        input_batch.append((packet_generation, payload))
                    continue

                if hdr > consumed:
                    consumed = hdr
                    continue

                break

            # Front deletion moves the remaining bytearray contents.  Do it
            # once per serial read instead of once for every parsed frame.
            if consumed:
                del buf[:consumed]

            if input_batch and self.input_callback is not None:
                with self._state_lock:
                    current_generation = self._connection_generation
                    ready = (
                        self.connected_channel is not None
                        and self._ready_channel == self.connected_channel
                    )
                input_batch = [
                    payload
                    for generation, payload in input_batch
                    if ready and generation == current_generation
                ]
            if input_batch and self.input_callback is not None:
                submit_batch = getattr(
                    self.input_callback,
                    "submit_batch",
                    None,
                )
                if callable(submit_batch):
                    submit_batch(input_batch)
                else:
                    for payload in input_batch:
                        self.input_callback(payload)
                
    def _restart_scan(self, delay=0.5):
        """連線失敗或斷線後，自動重新開始搜尋控制器。"""

        def worker():
            time.sleep(delay)

            with self._state_lock:
                should_restart = (
                    self.running
                    and not self._closing
                    and self.connected_channel is None
                    and not self._connecting
                )
            if should_restart:
                print("正在重新搜尋控制器...")
                self.send("scan on")

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    def _connection_is_current(self, channel, generation):
        with self._state_lock:
            return (
                self.running
                and not self._closing
                and self.connected_channel == channel
                and self._connection_generation == generation
            )

    def _fail_controller_preparation(self, channel, generation):
        """Drop a BLE link that never reached the ready state."""
        if not self._connection_is_current(channel, generation):
            return
        with self._rumble_condition:
            self._rumble_accepting = False
            self._feedback_active = False
            self._rumble_pending = None
            self._rumble_condition.notify_all()
        with self._state_lock:
            if not self._connection_is_current(channel, generation):
                return
            self.connected_channel = None
            self._ready_channel = None
            self.controller_id = None
            self._connecting = False
            self._connection_generation += 1
        self._disconnect_event.set()
        self.send("ble disconnect")
        self._restart_scan()

    def _prepare_connected_controller(
        self,
        channel,
        generation,
        pairing_required,
    ):
        """Pair and initialize one BLE generation before reporting ready."""
        if not self._connection_is_current(channel, generation):
            return
        if (
            pairing_required
            and not self.pair_controller_to_esp32(channel, generation)
        ):
            self._fail_controller_preparation(channel, generation)
            return
        if (
            not self._connection_is_current(channel, generation)
            or not self.initialize_controller_features(channel, generation)
        ):
            self._fail_controller_preparation(channel, generation)
            return
        if not self._connection_is_current(channel, generation):
            return

        # Linearize callback delivery and the ready commit with disconnect.
        # The callback deliberately observes is_ready == False.  Only after it
        # returns, and only if the same generation still exists, may ordinary
        # input and rumble begin.
        callback_started = False
        callback_error = None
        ready_committed = False
        with self._state_lock:
            if not self._connection_is_current(channel, generation):
                return
            if self.connected_callback is not None:
                callback_started = True
                try:
                    self.connected_callback()
                except Exception as exc:
                    callback_error = exc
            if (
                callback_error is None
                and self._connection_is_current(channel, generation)
            ):
                self._ready_channel = channel
                with self._rumble_condition:
                    self._rumble_accepting = True
                    self._feedback_active = False
                    self._rumble_condition.notify_all()
                ready_committed = True

        if not ready_committed:
            if callback_error is not None:
                print(f"ESP32 connected callback failed: {callback_error}")
            if callback_started and self.disconnected_callback is not None:
                try:
                    self.disconnected_callback()
                except Exception as exc:
                    print(f"ESP32 connected rollback failed: {exc}")
            self._fail_controller_preparation(channel, generation)
            return

        time.sleep(0.2)
        if self._connection_is_current(channel, generation):
            self.connection_rumble(expected_generation=generation)


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
            self._store_diagnostic_response(cmd, obj)

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

                with self._state_lock:
                    status_channel = self.connected_channel
                if status_channel is not None:
                    channel_mask = int(obj.get("ble_channels", 0) or 0)
                    disconnected_by_status = False
                    was_ready = False
                    with self._state_lock:
                        if self.connected_channel != status_channel:
                            return
                        if channel_mask & (1 << status_channel):
                            self._channel_missing_count = 0
                        elif (
                            time.monotonic() < self._status_grace_until
                            or self._ready_channel != status_channel
                        ):
                            # The manager status mask can lag behind the
                            # immediate "connected" event while pairing and
                            # mandatory controller setup are still running.
                            self._channel_missing_count = 0
                        else:
                            self._channel_missing_count += 1
                        if self._channel_missing_count >= 3:
                            self._channel_missing_count = 0
                            was_ready = self._ready_channel == status_channel
                            self.connected_channel = None
                            self._ready_channel = None
                            self._connection_generation += 1
                            self._connecting = False
                            disconnected_by_status = True
                    if disconnected_by_status:
                        self._disconnect_event.set()
                        self._command_response_event.set()
                        with self._rumble_condition:
                            self._rumble_accepting = False
                            self._feedback_active = False
                            self._rumble_pending = None
                            self._rumble_condition.notify_all()
                        print("ESP32 狀態連續三次未包含控制器通道。")
                        if (
                            was_ready
                            and not self._closing
                            and self.disconnected_callback is not None
                        ):
                            try:
                                self.disconnected_callback()
                            except Exception as exc:
                                print(f"控制器斷線回呼錯誤：{exc}")
                        self._restart_scan()

            elif cmd == "scan_result":
                with self._state_lock:
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

                    with self._state_lock:
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

                    with self._state_lock:
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

                    with self._state_lock:
                        self._pending_pair = False

                with self._state_lock:
                    if (
                        self.connected_channel is not None
                        or self._connecting
                    ):
                        return
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
                with self._state_lock:
                    self.connected_channel = int(
                        obj.get("channel", 0)
                    )
                    self._ready_channel = None
                    self._connection_generation += 1
                    generation = self._connection_generation
                    channel = self.connected_channel
                    pairing_required = self._pending_pair
                    self._connecting = False
                    self._channel_missing_count = 0
                self._disconnect_event.clear()
                self._command_response_event.clear()
                with self._command_response_lock:
                    self._command_response = None
                with self._rumble_condition:
                    self._rumble_accepting = False
                    self._feedback_active = False
                    self._rumble_pending = None
                    self._rumble_condition.notify_all()
                # SW2 initialization temporarily keeps the firmware busy.
                self._status_grace_until = time.monotonic() + 10.0

                print(
                    _tr(
                        f"控制器 BLE 已連線，正在初始化；ESP32 通道：{channel}",
                        "Controller BLE link established; initializing on "
                        f"ESP32 channel {channel}.",
                    )
                )

                # BLE notifications are already active at this point.  Run the
                # pairing and initialization sequences outside the serial read
                # thread so their stepwise ACKs can continue to be drained.
                threading.Thread(
                    target=self._prepare_connected_controller,
                    args=(channel, generation, pairing_required),
                    daemon=True
                ).start()

            elif cmd == "connect_fail":
                with self._state_lock:
                    self.connected_channel = None
                    self._ready_channel = None
                    self._connection_generation += 1
                    self.controller_id = None
                    self._connecting = False
                self._disconnect_event.set()
                self._command_response_event.set()
                with self._rumble_condition:
                    self._rumble_accepting = False
                    self._feedback_active = False
                    self._rumble_pending = None
                    self._rumble_condition.notify_all()

                print(_tr("控制器連線失敗。", "Controller connection failed."))

                self._restart_scan()


            elif cmd == "disconnected":
                with self._state_lock:
                    was_ready = (
                        self.connected_channel is not None
                        and self._ready_channel == self.connected_channel
                    )
                    notify_disconnected = was_ready and not self._closing
                    self.connected_channel = None
                    self._ready_channel = None
                    self._connection_generation += 1
                    self._connecting = False
                    self._channel_missing_count = 0
                self._disconnect_event.set()
                self._command_response_event.set()
                with self._rumble_condition:
                    self._rumble_accepting = False
                    self._feedback_active = False
                    self._rumble_pending = None
                    self._rumble_condition.notify_all()

                if notify_disconnected:
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

    def _handle_binary(self, data, dispatch=True):
        if not data:
            return None

        is_command_frame = bool(data[0] & 0x80)
        chan_id = data[0] & 0x7F
        if not (1 <= chan_id <= 8):
            return None

        with self._state_lock:
            channel = self.connected_channel
            generation = self._connection_generation
            ready = (
                channel is not None
                and self._ready_channel == channel
            )
        if (
            channel is None
            # JSON channel numbers are zero-based; CDC frame channels are
            # encoded as channel + 1 by the ESP32 firmware.
            or chan_id != channel + 1
        ):
            return None

        # The ESP32 bridge marks command/ACK notifications with bit 7 of the
        # channel byte.  They are not Switch input reports and must never be
        # forwarded to the input parser, otherwise command payload bytes can
        # appear as a brief button press or stick movement.
        if is_command_frame:
            response = bytes(data[1:])
            if response:
                with self._command_response_lock:
                    self._command_response = (generation, response)
                self._command_response_event.set()
            return None

        # BLE notifications begin before pairing and feature initialization
        # finish.  Command frames above must remain available for those ACKs,
        # but ordinary input must not reach mappings, calibration, or virtual
        # output until this connection generation is fully ready.
        if not ready:
            return None

        # The working calibration tool treats everything after the channel byte
        # as the Switch 2 input-report payload.
        payload = bytes(data[1:])
        self._last_input_time = time.monotonic()
        if dispatch and self.input_callback is not None:
            self.input_callback(payload)
        return payload
