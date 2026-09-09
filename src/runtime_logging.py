"""Bounded asynchronous stdout/stderr delivery for connector workers.

The connector deliberately uses plain console messages as its live diagnostic
surface.  A transport callback must never wait for that console (or a
redirected startup-log file), so this module replaces the process text streams
with non-blocking producers and performs writes on one dedicated listener.
"""

from __future__ import annotations

import atexit
import queue
import sys
import threading
import time


DEFAULT_QUEUE_SIZE = 2048
_STOP = object()


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
        self._queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self._running = threading.Event()
        self._stopped = threading.Event()
        self._listener = None
        self._metrics_lock = threading.Lock()
        self._queued_count = 0
        self._dropped_count = 0
        self._write_failures = 0
        self._shutdown_flushed = False
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
        try:
            self._queue.put_nowait((stream_name, text))
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
                item = self._queue.get()
                try:
                    if item is _STOP:
                        return
                    stream_name, text = item
                    self._write_direct(stream_name, text)
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
            }

    def stop(self, timeout=2.0):
        """Drain pending output from a safe shutdown thread without deadlock."""
        if not self._running.is_set():
            return True
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        flushed = not self._queue.unfinished_tasks
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            # A full queue is still being consumed; short bounded retry keeps
            # shutdown ownership outside all realtime callbacks.
            while time.monotonic() < deadline:
                try:
                    self._queue.put_nowait(_STOP)
                    break
                except queue.Full:
                    time.sleep(0.005)
            else:
                return False
        listener = self._listener
        if listener is not None:
            listener.join(max(0.0, deadline - time.monotonic()))
        self._running.clear()
        self._shutdown_flushed = flushed and self._stopped.is_set()
        return self._shutdown_flushed


_runtime = None


def install_async_stdio(stdout=None, stderr=None, queue_size=DEFAULT_QUEUE_SIZE):
    """Install one process-wide runtime, preserving the original streams."""
    global _runtime
    if _runtime is not None and _runtime._running.is_set():
        return _runtime
    runtime = AsyncLogRuntime(stdout=stdout, stderr=stderr, queue_size=queue_size)
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
        }
    return _runtime.metrics()


def shutdown_async_stdio(timeout=2.0):
    global _runtime
    runtime = _runtime
    if runtime is None:
        return True
    result = runtime.stop(timeout)
    sys.stdout = runtime._stdout
    sys.stderr = runtime._stderr
    _runtime = None
    return result
