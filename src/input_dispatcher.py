"""Low-latency input dispatcher with a safe inline fast path.

The transport thread may process one report inline when the pipeline is truly
idle.  If reports are already queued, a callback is running, or the previous
inline callback was slow, input falls back to the normal worker thread:

* button transitions are retained in order;
* stale analog / IMU-only reports are coalesced to the newest snapshot;
* only one thread may execute the output callback at any time.
"""

from collections import deque
import ctypes
import os
import threading
import time


def _set_current_thread_priority(level):
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), int(level))
    except Exception:
        pass


class InputDispatcher:
    """Combine a direct idle path with bounded latest-state fallback.

    Normal case:
        transport reader -> callback -> ViGEm update

    Busy / backlog case:
        transport reader -> edge queue + latest analog snapshot -> worker

    ``_processing`` is protected by ``_lock`` and is the single ownership flag
    for the callback.  The reader and worker can therefore never call
    ``xinput.update()`` concurrently.
    """

    def __init__(
        self,
        callback,
        max_pending=3,
        error_callback=None,
        inline_fast_path=True,
        inline_slow_threshold=0.0025,
        inline_cooldown=0.100,
    ):
        self.callback = callback
        self.error_callback = error_callback
        self.max_pending = max(2, int(max_pending))
        self._hard_limit = max(32, self.max_pending * 4)
        self.inline_fast_path = bool(inline_fast_path)
        self._inline_slow_threshold = max(0.0005, float(inline_slow_threshold))
        self._inline_cooldown = max(0.010, float(inline_cooldown))

        self._queue = deque()
        self._lock = threading.Lock()
        # reset()/stop() must not invalidate a generation while its callback is
        # already executing.  The callback ownership itself is controlled by
        # _processing so the reader and worker cannot run concurrently.
        self._callback_lock = threading.RLock()
        self._callback_owner_ident = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._generation = 0
        self._last_buttons = None
        self._processing = False
        self._reconfiguring = False
        self._inline_disabled_until = 0.0

        self.processing_rate_hz = None
        self.input_rate_hz = None
        self.dropped_reports = 0
        self.inline_reports = 0
        self.queued_reports = 0
        self.backlog_batches = 0
        self.slow_inline_fallbacks = 0

        self._rate_deltas = deque(maxlen=64)
        self._rate_last_processed = None
        self._rate_generation = None
        self._input_rate_window_started = None
        self._input_rate_count = 0

        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="ControllerInputDispatcher",
        )
        self._thread.start()

    def __call__(self, payload):
        """Allow the dispatcher object itself to be used as a callback."""
        self.submit(payload)

    @staticmethod
    def _button_signature(payload):
        try:
            if len(payload) < 8:
                return None
            return (
                payload[4]
                | (payload[5] << 8)
                | (payload[6] << 16)
                | (payload[7] << 24)
            )
        except (IndexError, TypeError, ValueError):
            return None

    @staticmethod
    def _snapshot(payload):
        # ESP32Bridge already owns immutable bytes.  Preserve that object on
        # the common path and copy only mutable/foreign bytes-like inputs.
        return payload if isinstance(payload, bytes) else bytes(payload)

    def _classify_locked(self, snapshot):
        buttons = self._button_signature(snapshot)
        is_edge = buttons is not None and buttons != self._last_buttons
        if buttons is not None:
            self._last_buttons = buttons
        return is_edge

    def _append_locked(self, snapshot, is_edge):
        """Append one report while preserving edges and coalescing states."""
        # Every report contains a complete controller snapshot.  Once a newer
        # report arrives, an older analog/IMU-only snapshot is unnecessary even
        # when the new report is a button edge.  Removing it also prevents a
        # stale stick state from delaying a newly pressed button by one report.
        retained = deque(item for item in self._queue if item[2])
        self.dropped_reports += len(self._queue) - len(retained)
        self._queue = retained

        if is_edge and len(self._queue) >= self._hard_limit:
            # The remaining queue contains only button transitions.  Bound it in
            # case a malformed stream generates unlimited synthetic edges.
            self._queue.popleft()
            self.dropped_reports += 1

        self._queue.append((self._generation, snapshot, is_edge))

    def _record_input_rate_locked(self, report_count, now):
        """Measure every received report, including identical/batched states."""
        if self._input_rate_window_started is None:
            self._input_rate_window_started = now
            self._input_rate_count = 0
            return
        self._input_rate_count += max(0, int(report_count))
        elapsed = now - self._input_rate_window_started
        if elapsed >= 0.5:
            self.input_rate_hz = self._input_rate_count / elapsed
            self._input_rate_window_started = now
            self._input_rate_count = 0

    def submit(self, payload):
        """Submit one snapshot, using the inline path only when truly idle."""
        if self._stop.is_set():
            return

        snapshot = self._snapshot(payload)
        claimed = None

        with self._lock:
            if self._stop.is_set():
                return

            self._record_input_rate_locked(1, time.perf_counter())
            is_edge = self._classify_locked(snapshot)
            cooldown_active = False
            if self._inline_disabled_until:
                if time.perf_counter() >= self._inline_disabled_until:
                    self._inline_disabled_until = 0.0
                else:
                    cooldown_active = True
            can_inline = (
                self.inline_fast_path
                and not self._processing
                and not self._queue
                and not self._reconfiguring
                and not cooldown_active
            )

            if can_inline:
                self._processing = True
                claimed = (self._generation, snapshot)
            else:
                self._append_locked(snapshot, is_edge)

        if claimed is not None:
            self._execute_claimed(*claimed, inline=True)
        else:
            self._wake.set()

    def submit_batch(self, payloads):
        """Submit all reports already buffered by a transport read.

        A multi-report batch proves the consumer has fallen behind.  It never
        uses the inline path: button edges are kept, while ordinary stick / IMU
        states collapse to the latest one before the worker wakes.
        """
        if self._stop.is_set():
            return

        snapshots = [self._snapshot(payload) for payload in payloads if payload]
        if not snapshots:
            return
        if len(snapshots) == 1:
            self.submit(snapshots[0])
            return

        with self._lock:
            if self._stop.is_set():
                return
            self._record_input_rate_locked(
                len(snapshots), time.perf_counter()
            )
            self.backlog_batches += 1
            # Keep the direct path disabled briefly after a real backlog.  This
            # lets the reader drain new reports while the worker catches up.
            self._inline_disabled_until = max(
                self._inline_disabled_until,
                time.perf_counter() + self._inline_cooldown,
            )
            for snapshot in snapshots:
                is_edge = self._classify_locked(snapshot)
                self._append_locked(snapshot, is_edge)

        self._wake.set()

    def _acquire_callback_lock(self, deadline):
        remaining = max(0.0, deadline - time.perf_counter())
        return self._callback_lock.acquire(timeout=remaining)

    def reset(self, timeout=1.0):
        """Discard reports belonging to the previous connection generation."""
        deadline = time.perf_counter() + max(0.0, float(timeout))
        if not self._acquire_callback_lock(deadline):
            return False
        try:
            # RLock acquisition is re-entrant.  A reset requested by the
            # callback itself is not quiescent and must not be reported as
            # complete while that callback can still touch the old state.
            with self._lock:
                if self._callback_owner_ident is not None:
                    return False
                self._generation += 1
                self._queue.clear()
                self._last_buttons = None
                self._inline_disabled_until = 0.0
                self.processing_rate_hz = None
                self.input_rate_hz = None
                self.dropped_reports = 0
                self.inline_reports = 0
                self.queued_reports = 0
                self.backlog_batches = 0
                self.slow_inline_fallbacks = 0
                self._rate_deltas.clear()
                self._rate_last_processed = None
                self._rate_generation = None
                self._input_rate_window_started = None
                self._input_rate_count = 0
                # submit() is excluded by the same lock while this is cleared.
                self._wake.clear()
            return True
        finally:
            self._callback_lock.release()

    def run_exclusive(self, callback, timeout=1.0):
        """Run a short reconfiguration between input callbacks.

        Reports already queued under the old settings are discarded. Reports
        arriving during the callback receive the new generation and resume as
        soon as the callback releases the existing callback lock.
        """
        deadline = time.perf_counter() + max(0.0, float(timeout))
        with self._lock:
            self._reconfiguring = True
        try:
            if not self._acquire_callback_lock(deadline):
                return False
            try:
                with self._lock:
                    if self._callback_owner_ident is not None:
                        return False
                    self._generation += 1
                    self._queue.clear()
                    self._last_buttons = None
                    self._inline_disabled_until = 0.0
                    self._rate_generation = None
                    self._rate_deltas.clear()
                    self._rate_last_processed = None
                    self._wake.clear()
                callback()
                return True
            finally:
                self._callback_lock.release()
        finally:
            with self._lock:
                self._reconfiguring = False
                has_pending = bool(self._queue)
            if has_pending:
                self._wake.set()

    def stop(self, timeout=1.0):
        deadline = time.perf_counter() + max(0.0, float(timeout))
        self._stop.set()
        self._wake.set()
        with self._lock:
            self._queue.clear()

        callback_acquired = self._acquire_callback_lock(deadline)
        callback_quiesced = False
        if callback_acquired:
            try:
                with self._lock:
                    callback_quiesced = self._callback_owner_ident is None
                    if callback_quiesced:
                        self._generation += 1
                        self._queue.clear()
            finally:
                self._callback_lock.release()
        self._wake.set()

        if threading.current_thread() is self._thread:
            return False
        remaining = max(0.0, deadline - time.perf_counter())
        self._thread.join(timeout=remaining)
        return callback_quiesced and not self._thread.is_alive()

    def _update_rate(self, generation, now):
        if self._rate_generation != generation:
            self._rate_generation = generation
            self._rate_deltas.clear()
            self._rate_last_processed = None

        if self._rate_last_processed is not None:
            delta = now - self._rate_last_processed
            if 0 < delta < 0.05:
                self._rate_deltas.append(delta)
                if len(self._rate_deltas) >= 32:
                    self.processing_rate_hz = 1.0 / (
                        sum(self._rate_deltas) / len(self._rate_deltas)
                    )
        self._rate_last_processed = now

    def _execute_claimed(self, generation, payload, inline):
        """Run one callback for a previously claimed ownership slot."""
        started = time.perf_counter()
        try:
            with self._callback_lock:
                with self._lock:
                    valid = (
                        not self._stop.is_set()
                        and generation == self._generation
                    )
                if valid:
                    self._update_rate(generation, started)
                    with self._lock:
                        self._callback_owner_ident = threading.get_ident()
                    try:
                        self.callback(payload)
                    except Exception as exc:
                        if self.error_callback is not None:
                            self.error_callback(exc)
                    finally:
                        with self._lock:
                            self._callback_owner_ident = None
        finally:
            elapsed = time.perf_counter() - started
            with self._lock:
                if inline:
                    self.inline_reports += 1
                    if elapsed > self._inline_slow_threshold:
                        self.slow_inline_fallbacks += 1
                        self._inline_disabled_until = max(
                            self._inline_disabled_until,
                            time.perf_counter() + self._inline_cooldown,
                        )
                else:
                    self.queued_reports += 1
                self._processing = False
                has_pending = bool(self._queue)
            if has_pending:
                self._wake.set()

    def _claim_queued(self):
        with self._lock:
            if (
                self._processing
                or self._reconfiguring
                or not self._queue
                or self._stop.is_set()
            ):
                return None
            generation, payload, _is_edge = self._queue.popleft()
            self._processing = True
            return generation, payload

    def _run(self):
        # HIGHEST, but only this input worker—not the whole process—is raised.
        _set_current_thread_priority(2)
        while not self._stop.is_set():
            self._wake.wait(0.5)
            self._wake.clear()

            while not self._stop.is_set():
                claimed = self._claim_queued()
                if claimed is None:
                    break
                self._execute_claimed(*claimed, inline=False)
