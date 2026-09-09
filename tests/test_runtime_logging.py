import io
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from runtime_logging import AsyncLogRuntime
from support_log import STARTUP_LOG_PATH_ENV, format_support_log


class _SlowStream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()

    def write(self, text):
        self.entered.set()
        time.sleep(0.08)
        return super().write(text)


class _FailingStream(io.StringIO):
    def write(self, _text):
        raise OSError("injected write failure")


class RuntimeLoggingTests(unittest.TestCase):
    def test_producer_does_not_execute_stream_io(self):
        stream = _SlowStream()
        runtime = AsyncLogRuntime(stdout=stream, stderr=stream).start()
        try:
            started = time.perf_counter()
            runtime.stdout.write("input callback diagnostic\n")
            self.assertLess(time.perf_counter() - started, 0.03)
            self.assertTrue(stream.entered.wait(0.5))
        finally:
            self.assertTrue(runtime.stop())

    def test_listener_failure_does_not_escape_producer_or_deadlock_shutdown(self):
        runtime = AsyncLogRuntime(stdout=_FailingStream(), stderr=_FailingStream()).start()
        try:
            for _ in range(100):
                runtime.stderr.write("transport error\n")
        finally:
            self.assertTrue(runtime.stop(timeout=1.0))
        self.assertGreater(runtime.metrics()["write_failures"], 0)

    def test_high_frequency_producer_is_bounded_and_returns_promptly(self):
        stream = _SlowStream()
        runtime = AsyncLogRuntime(stdout=stream, stderr=stream, queue_size=4).start()
        try:
            started = time.perf_counter()
            for _ in range(1000):
                runtime.stdout.write("input\n")
            self.assertLess(time.perf_counter() - started, 0.15)
            self.assertLessEqual(runtime.metrics()["pending_count"], 4)
        finally:
            runtime.stop(timeout=1.0)
        self.assertGreater(runtime.metrics()["dropped_count"], 0)

    def test_flushed_runtime_output_remains_available_to_support_export(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "startup-latest.txt"
            with current.open("w", encoding="utf-8") as stream:
                runtime = AsyncLogRuntime(stdout=stream, stderr=stream).start()
                runtime.stdout.write("STARTUP=READY\n")
                self.assertTrue(runtime.stop())
            exported = format_support_log(
                "S2P_XINPUT_LITE_DIAGNOSTIC_LOG\nVERDICT=OK\n",
                {STARTUP_LOG_PATH_ENV: str(current)},
            )
            self.assertIn("STARTUP=READY", exported)
            self.assertIn("S2P_XINPUT_LITE_DIAGNOSTIC_LOG", exported)


if __name__ == "__main__":
    unittest.main()
