"""Bounded asynchronous stdout/stderr delivery for connector workers.

The connector deliberately uses plain console messages as its live diagnostic
surface.  A transport callback must never wait for that console (or a
redirected startup-log file), so this module replaces the process text streams
with non-blocking producers and performs writes on one dedicated listener.
"""

from __future__ import annotations

import atexit
import itertools
import queue
import sys
import threading
import time


DEFAULT_QUEUE_SIZE = 2048
_STOP = object()
_STOP_PRIORITY = -1
_PROMPT_PRIORITY = 0
_NORMAL_PRIORITY = 1


class AsyncTextStream:
    """A TextIO-compatible producer that never waits for output I/O."""

    def __init__(self, runtime, stream_name):
        self._runtime = runtime
        self._stream_name = stream_name

    @property
    def encoding(self):
        return getattr(self._runtime.stream_for(self._stream_name), "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self._runtime.stream_for(self._stream_name), "errors", "replace")

    def write(self, text):
        if text is None:
            return 0
        text = str(text)
        self._runtime.enqueue(self._stream_name, text)
        return len(text)

    def flush(self):
        # ``print(..., flush=True)`` remains non-blocking.  Shutdown owns the
        # bounded drain, where waiting is safe and intentional.
        return None

    def isatty(self):
        return bool(getattr(self._runtime.stream_for(self._stream_name), "isatty", lambda: False)())

    def fileno(self):
        return self._runtime.stream_for(self._stream_name).fileno()


class AsyncLogRuntime:
    """Own a bounded output queue and a single fault-contained listener."""

    def __init__(self, stdout=None, stderr=None, queue_size=DEFAULT_QUEUE_SIZE):
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr
        self._queue_size = max(2, int(queue_size))
        self._queue = queue.PriorityQueue(maxsize=self._queue_size)
        self._sequence = itertools.count()
        self._running = threading.Event()
        self._stopped = threading.Event()
        self._listener = None
        self._metrics_lock = threading.Lock()
        self._queued_count = 0
        self._dropped_count = 0
        self._write_failures = 0
        self._shutdown_flushed = False
        self._shutdown_timed_out = False
        self._interactive_prompt_count = 0
        self._interactive_prompt_timeouts = 0
        self.stdout = AsyncTextStream(self, "stdout")
        self.stderr = AsyncTextStream(self, "stderr")

    def stream_for(self, stream_name):
        return self._stderr if stream_name == "stderr" else self._stdout

    def start(self):
        if self._running.is_set():
            return self
        self._running.set()
        self._listener = threading.Thread(
            target=self._listen, name="S2PAsyncLogListener", daemon=True,
        )
        self._listener.start()
        return self

    def enqueue(self, stream_name, text):
        """Put one already-rendered text fragment without producer blocking."""
        if not text:
            return
        if not self._running.is_set():
            self._write_direct(stream_name, text)
            return
        # Keep one bounded slot for a non-realtime interactive prompt. A log
        # flood may lose diagnostics, but it must not hide "Press Enter".
        if self._queue.qsize() >= self._queue_size - 1:
            with self._metrics_lock:
                self._dropped_count += 1
            return
        try:
            self._queue.put_nowait((
                _NORMAL_PRIORITY,
                next(self._sequence),
                (stream_name, text, None),
            ))
        except queue.Full:
            with self._metrics_lock:
                self._dropped_count += 1
        else:
            with self._metrics_lock:
                self._queued_count += 1

    def _write_direct(self, stream_name, text):
        try:
            stream = self.stream_for(stream_name)
            stream.write(text)
            stream.flush()
        except Exception:
            with self._metrics_lock:
                self._write_failures += 1

    def _listen(self):
        try:
            while True:
                _priority, _sequence, item = self._queue.get()
                try:
                    if item is _STOP:
                        return
                    stream_name, text, completed = item
                    self._write_direct(stream_name, text)
                    if completed is not None:
                        completed.set()
                finally:
                    self._queue.task_done()
        finally:
            self._stopped.set()

    def metrics(self):
        with self._metrics_lock:
            return {
                "enabled": self._running.is_set(),
                "listener_alive": bool(self._listener and self._listener.is_alive()),
                "queued_count": self._queued_count,
                "pending_count": self._queue.qsize(),
                "dropped_count": self._dropped_count,
                "write_failures": self._write_failures,
                "shutdown_flushed": self._shutdown_flushed,
                "shutdown_timed_out": self._shutdown_timed_out,
                "interactive_prompt_count": self._interactive_prompt_count,
                "interactive_prompt_timeouts": self._interactive_prompt_timeouts,
            }

    def flush_non_realtime(self, timeout=1.0):
        """Boundedly wait for queued output from a safe caller context."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        return not self._queue.unfinished_tasks

    def write_interactive_prompt(self, text, timeout=1.0):
        """Prioritize one prompt without ever changing realtime flush rules."""
        if not text:
            return True
        if not self._running.is_set():
            self._write_direct("stdout", text)
            return True
        deadline = time.monotonic() + max(0.0, float(timeout))
        # Give already queued startup/error output a chance to reach the user.
        self.flush_non_realtime(timeout=max(0.0, (deadline - time.monotonic()) * 0.5))
        completed = threading.Event()
        try:
            self._queue.put_nowait((
                _PROMPT_PRIORITY,
                next(self._sequence),
                ("stdout", str(text), completed),
            ))
        except queue.Full:
            # The reserved prompt slot can only be occupied by another prompt,
            # which cannot normally happen while input is serial. Do not block
            # a shutdown or startup failure if an external caller violates it.
            with self._metrics_lock:
                self._interactive_prompt_timeouts += 1
            return False
        with self._metrics_lock:
            self._interactive_prompt_count += 1
        delivered = completed.wait(max(0.0, deadline - time.monotonic()))
        if not delivered:
            with self._metrics_lock:
                self._interactive_prompt_timeouts += 1
        return delivered

    def stop(self, timeout=2.0):
        """Drain pending output from a safe shutdown thread without deadlock."""
        if not self._running.is_set():
            return True
        deadline = time.monotonic() + max(0.0, float(timeout))
        flushed = self.flush_non_realtime(
            timeout=max(0.0, deadline - time.monotonic())
        )
        sentinel_enqueued = False
        try:
            self._queue.put_nowait((
                _STOP_PRIORITY, next(self._sequence), _STOP,
            ))
            sentinel_enqueued = True
        except queue.Full:
            # A full queue is still being consumed; short bounded retry keeps
            # shutdown ownership outside all realtime callbacks.
            while time.monotonic() < deadline:
                try:
                    self._queue.put_nowait((
                        _STOP_PRIORITY, next(self._sequence), _STOP,
                    ))
                    sentinel_enqueued = True
                    break
                except queue.Full:
                    time.sleep(0.005)
        listener = self._listener
        if listener is not None:
            listener.join(max(0.0, deadline - time.monotonic()))
        self._running.clear()
        self._shutdown_flushed = (
            flushed and sentinel_enqueued and self._stopped.is_set()
        )
        self._shutdown_timed_out = not self._shutdown_flushed
        return self._shutdown_flushed


_runtime = None


def install_async_stdio(stdout=None, stderr=None, queue_size=DEFAULT_QUEUE_SIZE):
    """Install one process-wide runtime, preserving the original streams."""
    global _runtime
    if _runtime is not None and _runtime._running.is_set():
        return _runtime
    previous_stdout, previous_stderr = sys.stdout, sys.stderr
    runtime = AsyncLogRuntime(stdout=stdout, stderr=stderr, queue_size=queue_size)
    # ``stdout``/``stderr`` are delivery targets (for example the startup-log
    # file), not necessarily the streams that were replaced process-wide.
    runtime._restore_stdout = previous_stdout
    runtime._restore_stderr = previous_stderr
    runtime.start()
    sys.stdout = runtime.stdout
    sys.stderr = runtime.stderr
    _runtime = runtime
    atexit.register(shutdown_async_stdio)
    return runtime


def get_async_log_metrics():
    if _runtime is None:
        return {
            "enabled": False,
            "listener_alive": False,
            "queued_count": 0,
            "pending_count": 0,
            "dropped_count": 0,
            "write_failures": 0,
            "shutdown_flushed": False,
            "shutdown_timed_out": False,
            "interactive_prompt_count": 0,
            "interactive_prompt_timeouts": 0,
        }
    return _runtime.metrics()


def shutdown_async_stdio(timeout=2.0):
    global _runtime
    runtime = _runtime
    if runtime is None:
        return True
    result = runtime.stop(timeout)
    sys.stdout = getattr(runtime, "_restore_stdout", runtime._stdout)
    sys.stderr = getattr(runtime, "_restore_stderr", runtime._stderr)
    _runtime = None
    return result


def flush_async_stdio(timeout=1.0):
    """Boundedly drain runtime output from an explicitly non-realtime path."""
    return True if _runtime is None else _runtime.flush_non_realtime(timeout)


def write_interactive_prompt(text, timeout=1.0):
    """Deliver a prompt without allowing it to be dropped behind normal logs."""
    if _runtime is None:
        try:
            sys.stdout.write(str(text))
            sys.stdout.flush()
            return True
        except Exception:
            return False
    return _runtime.write_interactive_prompt(text, timeout)
