import asyncio
import unittest
from unittest.mock import patch

from bluetooth_controller import BluetoothController


MANDATORY_COMMANDS = (
    (0x03, 0x0D),
    (0x07, 0x01),
    (0x16, 0x01),
    (0x15, 0x03),
    (0x0C, 0x02),
    (0x11, 0x03),
    (0x0A, 0x08),
    (0x0C, 0x04),
    (0x03, 0x0A),
    (0x10, 0x01),
    (0x01, 0x0C),
    (0x01, 0x01),
    (0x09, 0x07),
)


class _Client:
    def __init__(self):
        self.is_connected = True


class BluetoothReadinessTests(unittest.IsolatedAsyncioTestCase):
    def make_controller(self):
        controller = BluetoothController()
        controller.running = True
        controller.connected = True
        controller._application_ready = False
        controller._rumble_accepting = False
        controller._connection_generation = 4
        controller.client = _Client()
        controller._loop = asyncio.get_running_loop()
        controller._rumble_write_lock = asyncio.Lock()
        return controller

    async def test_wrong_subcommand_ack_is_ignored(self):
        controller = self.make_controller()
        future = controller._loop.create_future()
        controller._response_future = future
        controller._expected_command = (4, 0x15, 0x04)

        controller._on_command_response(
            None,
            bytes([0x15, 0x91, 0x01, 0x02]) + b"\x00" * 4,
            generation=4,
        )
        await asyncio.sleep(0)
        self.assertFalse(future.done())

        expected = bytes([0x15, 0x91, 0x01, 0x04]) + b"\x00" * 4
        controller._on_command_response(None, expected, generation=4)
        await asyncio.sleep(0)
        self.assertEqual(future.result(), expected)

    async def test_every_mandatory_initialization_failure_aborts(self):
        async def no_sleep(_delay):
            return None

        for failing in MANDATORY_COMMANDS:
            with self.subTest(command=failing):
                controller = self.make_controller()
                calls = []

                async def write(command, subcommand, _data, **_kwargs):
                    calls.append((command, subcommand))
                    if (command, subcommand) == failing:
                        raise RuntimeError("injected failure")

                controller._write_command = write
                with patch(
                    "bluetooth_controller.asyncio.sleep",
                    new=no_sleep,
                ):
                    with self.assertRaises(RuntimeError):
                        await controller._initialize_controller(4)

                self.assertEqual(calls.count(failing), 3)
                failing_index = MANDATORY_COMMANDS.index(failing)
                self.assertFalse(
                    any(
                        command in calls
                        for command in MANDATORY_COMMANDS[failing_index + 1:]
                    )
                )

    async def test_input_is_blocked_until_application_ready(self):
        controller = self.make_controller()
        received = []
        controller.input_callback = received.append

        controller._on_input_report(None, b"early", generation=4)
        self.assertEqual(received, [])

        controller._application_ready = True
        controller._on_input_report(None, b"ready", generation=4)
        self.assertEqual(received, [b"ready"])

    async def test_connected_callback_observes_not_ready(self):
        controller = self.make_controller()
        observed = []
        controller.connected_callback = lambda: observed.append(
            controller.is_ready
        )

        self.assertTrue(controller._commit_application_ready(4))

        self.assertEqual(observed, [False])
        self.assertTrue(controller.is_ready)
        self.assertTrue(controller._rumble_accepting)

    async def test_disconnect_rejects_new_rumble(self):
        controller = self.make_controller()
        controller._application_ready = True
        controller._rumble_accepting = True
        entered = asyncio.Event()
        release = asyncio.Event()

        async def disconnect():
            entered.set()
            await release.wait()
            controller.client.is_connected = False

        controller.client.disconnect = disconnect

        async def send_zero(*_args):
            return True

        controller._send_pro_rumble_async = send_zero
        task = asyncio.create_task(controller._disconnect())
        await entered.wait()

        self.assertFalse(controller.send_pro_rumble(80, 800, 160, 800))

        release.set()
        self.assertTrue(await task)

    async def test_failed_disconnect_returns_false_and_keeps_client(self):
        controller = self.make_controller()
        controller._application_ready = True
        controller._rumble_accepting = True
        client = controller.client

        async def disconnect():
            raise RuntimeError("injected disconnect failure")

        client.disconnect = disconnect

        async def send_zero(*_args):
            return True

        controller._send_pro_rumble_async = send_zero

        self.assertFalse(await controller._disconnect())
        self.assertIs(controller.client, client)
        self.assertFalse(controller.send_pro_rumble(80, 800, 160, 800))


if __name__ == "__main__":
    unittest.main()
