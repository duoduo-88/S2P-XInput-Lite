import configparser
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from command_queue import enqueue_controller_command
import main as connector
import config_gui
from config_utils import (
    DEFAULT_CONFIG_PATH,
    atomic_write_config,
    gyro_calibration_section,
    load_config,
    load_stick_calibration,
    store_stick_calibration,
)
from gyro_compensation import compensation_reload_signature
from runtime_commands import ControllerCommandInbox


class ConnectorCalibrationTests(unittest.TestCase):
    def test_compensation_reload_requires_the_same_connection_and_compensation_settings(self):
        cases = ("before dequeue", "before apply", "profile", "multiplier", "target", "unrelated", None)
        for change in cases:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                config_path = folder / "config.ini"
                controller = Mock(controller_id="001122334455", is_ready=True)
                controller.open.side_effect = lambda: controller.connected_callback()
                xinput = Mock(mapping_layers=[])
                xinput.get_rumble_diagnostics.return_value = {}
                xinput.get_gyro_initialization_status.return_value = {
                    "complete": True, "ready": True, "source": "saved",
                }
                for sensor in ("gyro", "magnetometer", "accelerometer"):
                    getattr(xinput, f"get_{sensor}_calibration_status").return_value = {
                        "state": "idle", "message": "", "progress": 0, "spans": (0, 0, 0),
                    }
                    getattr(xinput, f"consume_{sensor}_calibration_result").return_value = None
                dispatcher = Mock()
                publisher = Mock()
                inbox = ControllerCommandInbox(folder / "legacy.txt", folder / "commands")
                read_pending = inbox.next
                request_id = None

                def run_exclusive(callback, timeout):
                    if request_id is not None and change == "before apply":
                        controller.is_ready = False
                        controller.disconnected_callback()
                    callback()
                    return True

                def queue_reload():
                    nonlocal request_id
                    connection_id = next(
                        update.kwargs["connection_id"]
                        for update in publisher.publish.call_args_list
                        if update.kwargs.get("state") == "connected"
                    )
                    config = load_config(config_path, persist=False)
                    config.set("gyro_mapping", "x_compensation", "0.75")
                    atomic_write_config(config, config_path)
                    request_id = enqueue_controller_command(
                        "reload_settings", inbox.queue_dir, connection_id=connection_id,
                        compensation_signature=compensation_reload_signature(config),
                    )
                    if change == "before dequeue":
                        controller.is_ready = False
                        controller.disconnected_callback()
                        controller.connected_callback()
                        controller.is_ready = True
                    elif change == "profile":
                        config.set("gui", "active_profile", "Other")
                    elif change == "multiplier":
                        config.set("gyro_mapping", "x_compensation", "0.50")
                    elif change == "target":
                        config.set("gyro_mapping", "target", "MOUSE")
                    elif change == "unrelated":
                        config.set("gyro_mapping", "y_ratio", "1.20")
                    if change in ("profile", "multiplier", "target", "unrelated"):
                        atomic_write_config(config, config_path)
                    xinput.reconfigure.reset_mock()
                    return read_pending()

                dispatcher.run_exclusive.side_effect = run_exclusive
                with (
                    patch.multiple(
                        connector,
                        CONFIG_PATH=config_path,
                        find_wired_controller=Mock(return_value={}),
                        WiredController=Mock(return_value=controller),
                        XInputController=Mock(return_value=xinput),
                        InputDispatcher=Mock(return_value=dispatcher),
                        AudioHaptics=Mock(),
                        SharedTestTelemetry=Mock(),
                        ControllerCommandInbox=Mock(return_value=inbox),
                        ControllerStatusPublisher=Mock(return_value=publisher),
                        reconcile_active_hidhide=Mock(return_value={}),
                        watch_parent_process_exit=Mock(),
                        print=Mock(),
                        input=Mock(side_effect=AssertionError("Unexpected startup prompt")),
                    ),
                    patch.object(inbox, "next", side_effect=queue_reload),
                    patch.object(connector.signal, "signal"),
                    patch.object(connector.time, "sleep", side_effect=KeyboardInterrupt),
                ):
                    connector._run_connector()

                states = [
                    update.kwargs["settings_reload_state"]
                    for update in publisher.publish.call_args_list
                    if update.kwargs.get("settings_reload_request_id") == request_id
                ]
                if change in (None, "unrelated"):
                    xinput.reconfigure.assert_called_once()
                    applied_config = xinput.reconfigure.call_args.args[0]
                    self.assertEqual(applied_config.getfloat("gyro_mapping", "x_compensation"), 0.75)
                    self.assertEqual(states, ["running", "success"])
                else:
                    xinput.reconfigure.assert_not_called()
                    self.assertEqual(states, ["running", "failed"])
                self.assertFalse(list(inbox.queue_dir.iterdir()))

    def test_runtime_sticks_accept_legacy_calibration_without_device_profiles(self):
        config = configparser.ConfigParser()
        config.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
        config.set("sticks", "left_center", "1720, 2120")

        calibration = connector.load_runtime_stick_calibration(config, "001122334455")

        self.assertEqual(calibration["left"]["center"], (1720, 2120))

    def test_invalid_shared_sticks_do_not_mask_valid_device_calibration(self):
        config = configparser.ConfigParser()
        config.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
        defaults = load_stick_calibration(config)
        calibration = load_stick_calibration(config)
        calibration["left"]["center"] = (1720, 2120)
        store_stick_calibration(config, calibration, "001122334455")
        config.set("sticks", "left_max", "0, 2047")

        with patch.object(connector, "print"):
            self.assertEqual(connector.load_runtime_stick_calibration(config), defaults)
            self.assertEqual(
                connector.load_runtime_stick_calibration(config, "001122334455"), calibration
            )

    def test_switching_pads_uses_default_sticks_when_the_new_pad_has_no_valid_calibration(self):
        first_id = "001122334455"
        next_id = "AABBCCDDEEFF"
        for invalid_center in (None, "", "broken", "4096, 2048"):
            with self.subTest(invalid_center=invalid_center), tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "config.ini"
                config = configparser.ConfigParser()
                config.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
                default_calibration = load_stick_calibration(config)
                first_calibration = load_stick_calibration(config)
                first_calibration["left"]["center"] = (1720, 2120)
                store_stick_calibration(config, first_calibration, first_id)
                if invalid_center is not None:
                    second_calibration = load_stick_calibration(config)
                    store_stick_calibration(config, second_calibration, next_id)
                    if invalid_center:
                        config.set(f"sticks.{next_id}", "left_center", invalid_center)
                    else:
                        config.remove_option(f"sticks.{next_id}", "left_center")
                with config_path.open("w", encoding="utf-8") as stream:
                    config.write(stream)
                original = config_path.read_bytes()

                controller = Mock(controller_id=first_id, is_ready=True)
                controller.open.side_effect = lambda: controller.connected_callback()
                xinput = Mock(mapping_layers=[])
                xinput.get_rumble_diagnostics.return_value = {}
                xinput.get_gyro_initialization_status.return_value = {
                    "complete": True, "ready": True, "source": "saved",
                }
                for sensor in ("gyro", "magnetometer", "accelerometer"):
                    getattr(xinput, f"get_{sensor}_calibration_status").return_value = {
                        "state": "idle", "message": "", "progress": 0, "spans": (0, 0, 0),
                    }
                    getattr(xinput, f"consume_{sensor}_calibration_result").return_value = None
                dispatcher = Mock()

                def run_exclusive(callback, timeout):
                    callback()
                    return True

                dispatcher.run_exclusive.side_effect = run_exclusive
                publisher = Mock()

                def switch_and_reload():
                    xinput.set_calibration.assert_called_with(first_calibration)
                    controller.is_ready = False
                    controller.disconnected_callback()
                    controller.controller_id = next_id
                    controller.connected_callback()
                    controller.is_ready = True
                    xinput.set_calibration.assert_called_with(default_calibration)
                    return SimpleNamespace(
                        command="reload_settings", request_id="reload-1", connection_id=None,
                        compensation_signature=None,
                        clear_legacy=Mock(), finish=Mock(),
                    )

                with (
                    patch.multiple(
                        connector,
                        CONFIG_PATH=config_path,
                        config_file_lock=nullcontext,
                        find_wired_controller=Mock(return_value={}),
                        WiredController=Mock(return_value=controller),
                        XInputController=Mock(return_value=xinput),
                        InputDispatcher=Mock(return_value=dispatcher),
                        AudioHaptics=Mock(),
                        SharedTestTelemetry=Mock(),
                        ControllerCommandInbox=Mock(return_value=Mock(next=Mock(side_effect=switch_and_reload))),
                        ControllerStatusPublisher=Mock(return_value=publisher),
                        reconcile_active_hidhide=Mock(return_value={}),
                        watch_parent_process_exit=Mock(),
                        print=Mock(),
                        input=Mock(side_effect=AssertionError("Unexpected startup prompt")),
                    ),
                    patch.object(connector.signal, "signal"),
                    patch.object(connector.time, "sleep", side_effect=KeyboardInterrupt),
                ):
                    connector._run_connector()

                xinput.reconfigure.assert_called_with(config, default_calibration)
                self.assertTrue(any(
                    update.kwargs.get("settings_reload_state") == "success"
                    for update in publisher.publish.call_args_list
                ))
                self.assertEqual(config_path.read_bytes(), original)

    def test_corrupt_motion_calibration_preserves_other_sensors_on_connect_and_reload(self):
        controller_id = "001122334455"
        section = gyro_calibration_section(controller_id)
        for corrupt_key in ("bias", "accel_matrix", "mag_matrix"):
            with self.subTest(corrupt_key=corrupt_key), tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "config.ini"
                config = configparser.ConfigParser()
                config.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
                config.read_dict({
                    "serial": {},
                    section: {
                        "bias": "1, 2, 3",
                        "accel_model": "ellipsoid",
                        "accel_bias": "4, 5, 6",
                        "accel_matrix": "0.001, 0, 0, 0, 0.001, 0, 0, 0, 0.001",
                        "mag_model": "ellipsoid_spd",
                        "mag_bias": "7, 8, 9",
                        "mag_matrix": "0.01, 0, 0, 0, 0.01, 0, 0, 0, 0.01",
                    },
                })
                config.set(section, corrupt_key, "invalid")
                with config_path.open("w", encoding="utf-8") as stream:
                    config.write(stream)
                original_config = config_path.read_bytes()
                controller = Mock(controller_id=controller_id, is_ready=True)
                controller.open.side_effect = lambda: controller.connected_callback()
                xinput = Mock(mapping_layers=[])
                xinput.get_rumble_diagnostics.return_value = {}
                xinput.get_gyro_initialization_status.return_value = {
                    "complete": True, "ready": True, "source": "saved",
                }
                for sensor in ("gyro", "magnetometer", "accelerometer"):
                    getattr(xinput, f"get_{sensor}_calibration_status").return_value = {
                        "state": "idle", "message": "", "progress": 0, "spans": (0, 0, 0),
                    }
                    getattr(xinput, f"consume_{sensor}_calibration_result").return_value = None
                dispatcher = Mock()

                def run_exclusive(callback, timeout):
                    callback()
                    return True

                dispatcher.run_exclusive.side_effect = run_exclusive
                publisher = Mock()

                def active_biases():
                    return (
                        xinput.set_gyro_bias.call_args.args[0],
                        xinput.set_accelerometer_calibration.call_args.args[0],
                        xinput.set_magnetometer_calibration.call_args.args[0],
                    )

                startup_biases = []

                def next_command():
                    startup_biases.append(active_biases())
                    return SimpleNamespace(
                        command="reload_settings", request_id="reload-1", connection_id=None,
                        compensation_signature=None,
                        clear_legacy=Mock(), finish=Mock(),
                    )

                with (
                    patch.multiple(
                        connector,
                        CONFIG_PATH=config_path,
                        config_file_lock=nullcontext,
                        find_wired_controller=Mock(return_value={}),
                        WiredController=Mock(return_value=controller),
                        XInputController=Mock(return_value=xinput),
                        InputDispatcher=Mock(return_value=dispatcher),
                        AudioHaptics=Mock(),
                        SharedTestTelemetry=Mock(),
                        ControllerCommandInbox=Mock(return_value=Mock(next=Mock(side_effect=next_command))),
                        ControllerStatusPublisher=Mock(return_value=publisher),
                        reconcile_active_hidhide=Mock(return_value={}),
                        watch_parent_process_exit=Mock(),
                        print=Mock(),
                        input=Mock(side_effect=AssertionError("Unexpected startup prompt")),
                    ),
                    patch.object(connector.signal, "signal"),
                    patch.object(connector.time, "sleep", side_effect=KeyboardInterrupt),
                ):
                    connector._run_connector()

                expected = (
                    None if corrupt_key == "bias" else (1.0, 2.0, 3.0),
                    None if corrupt_key == "accel_matrix" else (4.0, 5.0, 6.0),
                    None if corrupt_key == "mag_matrix" else (7.0, 8.0, 9.0),
                )
                self.assertEqual(startup_biases, [expected])
                self.assertEqual(active_biases(), expected)
                reload_states = [
                    update.kwargs["settings_reload_state"]
                    for update in publisher.publish.call_args_list
                    if update.kwargs.get("settings_reload_request_id") == "reload-1"
                ]
                self.assertEqual(reload_states, ["running", "success"])
                self.assertEqual(config_path.read_bytes(), original_config)

    def test_completed_calibration_is_saved_to_the_controller_that_started_it(self):
        self._run_calibration_cases(expected_connection=None)

    def test_queued_calibration_for_the_previous_connection_is_rejected(self):
        self._run_calibration_cases(expected_connection="first-connection")

    def test_failed_calibration_write_keeps_the_active_values(self):
        self._run_calibration_cases(expected_connection=None, write_error=PermissionError("config locked"))

    def test_successful_calibration_write_applies_the_saved_values(self):
        self._run_calibration_cases(expected_connection=None, reconnect=False)

    def test_saved_calibration_reports_when_input_worker_cannot_apply_it(self):
        self._run_calibration_cases(expected_connection=None, reconnect=False, apply_timeout=True)

    def _run_calibration_cases(
        self, expected_connection, *, reconnect=True, write_error=None, apply_timeout=False,
    ):
        original_id = "001122334455"
        next_id = "AABBCCDDEEFF"
        bias = (1.0, 2.0, 3.0)
        matrix = ((0.001, 0, 0), (0, 0.001, 0), (0, 0, 0.001))

        for sensor, result, config_key, setter, status_prefix in (
            ("gyro", bias, "bias", "set_gyro_bias", "gyro"),
            ("magnetometer", (bias, matrix, {}), "mag_bias", "set_magnetometer_calibration", "mag"),
            ("accelerometer", (bias, matrix, {}), "accel_bias", "set_accelerometer_calibration", "accel"),
        ):
            with self.subTest(sensor=sensor), tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "config.ini"
                next_section = gyro_calibration_section(next_id)
                config = configparser.ConfigParser()
                config.read_dict({"serial": {}, next_section: {config_key: "4, 5, 6"}})
                with config_path.open("w", encoding="utf-8") as stream:
                    config.write(stream)

                controller = Mock(controller_id=original_id, is_ready=True)
                controller.open.side_effect = lambda: controller.connected_callback()
                controller.get_rumble_diagnostics.return_value = {}
                dispatcher = Mock(reset=Mock(return_value=True))
                saved_to_disk = False

                def run_exclusive(callback, timeout):
                    if saved_to_disk and apply_timeout:
                        return False
                    callback()
                    return True

                dispatcher.run_exclusive.side_effect = run_exclusive
                xinput = Mock(mapping_layers=[])
                xinput.get_rumble_diagnostics.return_value = {}
                xinput.get_gyro_initialization_status.return_value = {
                    "complete": True, "ready": True, "source": "manual",
                }
                for name in ("gyro", "magnetometer", "accelerometer"):
                    getattr(xinput, f"get_{name}_calibration_status").return_value = {
                        "state": "idle", "message": "", "progress": 0, "spans": (0, 0, 0),
                    }
                    getattr(xinput, f"consume_{name}_calibration_result").return_value = None

                write_config = connector.atomic_write_config
                apply_calibration = getattr(xinput, setter)

                def consume_result():
                    getattr(xinput, f"start_{sensor}_calibration").assert_called_once()
                    if reconnect and write_error is None:
                        controller.is_ready = False
                        controller.disconnected_callback()
                        controller.controller_id = next_id
                        controller.connected_callback()
                        controller.is_ready = True
                    apply_calibration.reset_mock()
                    return result

                def write_calibration(config, path):
                    nonlocal saved_to_disk
                    apply_calibration.assert_not_called()
                    if write_error is not None:
                        raise write_error
                    write_config(config, path)
                    saved_to_disk = True

                getattr(xinput, f"consume_{sensor}_calibration_result").side_effect = (
                    consume_result
                )
                if write_error is not None:
                    getattr(xinput, f"get_{sensor}_calibration_status").return_value = {
                        "state": "success", "message": "ready_to_save",
                        "progress": 100, "spans": (0, 0, 0),
                    }
                command = SimpleNamespace(
                    command=f"calibrate_{sensor}", request_id="",
                    connection_id=expected_connection,
                    clear_legacy=Mock(), finish=Mock(),
                )
                inbox = Mock()
                publisher = Mock()

                def next_command():
                    if expected_connection:
                        first = next(
                            update.kwargs.get("connection_id")
                            for update in publisher.publish.call_args_list
                            if update.kwargs.get("state") == "connected"
                        )
                        command.connection_id = first or expected_connection
                        controller.is_ready = False
                        controller.disconnected_callback()
                        controller.connected_callback()
                        controller.is_ready = True
                        getattr(xinput, f"consume_{sensor}_calibration_result").side_effect = None
                    return command

                inbox.next.side_effect = next_command

                with (
                    patch.multiple(
                        connector,
                        CONFIG_PATH=config_path,
                        config_file_lock=nullcontext,
                        atomic_write_config=write_calibration,
                        load_config=Mock(return_value=config),
                        load_stick_calibration=Mock(return_value=None),
                        find_wired_controller=Mock(return_value={}),
                        WiredController=Mock(return_value=controller),
                        XInputController=Mock(return_value=xinput),
                        InputDispatcher=Mock(return_value=dispatcher),
                        AudioHaptics=Mock(),
                        SharedTestTelemetry=Mock(),
                        ControllerCommandInbox=Mock(return_value=inbox),
                        ControllerStatusPublisher=Mock(return_value=publisher),
                        reconcile_active_hidhide=Mock(return_value={}),
                        watch_parent_process_exit=Mock(),
                        print=Mock(),
                        input=Mock(side_effect=AssertionError("Unexpected startup prompt")),
                    ),
                    patch.object(connector.signal, "signal"),
                    patch.object(connector.time, "sleep", side_effect=KeyboardInterrupt),
                ):
                    connector._run_connector()

                saved = configparser.ConfigParser()
                saved.read(config_path, encoding="utf-8")
                self.assertEqual(saved.get(next_section, config_key), "4, 5, 6")
                if expected_connection:
                    getattr(xinput, f"start_{sensor}_calibration").assert_not_called()
                    self.assertFalse(saved.has_option(gyro_calibration_section(original_id), config_key))
                    continue
                if write_error is not None:
                    apply_calibration.assert_not_called()
                    self.assertFalse(saved.has_option(gyro_calibration_section(original_id), config_key))
                    updates = [
                        update.kwargs.get(f"{status_prefix}_calibration_state")
                        for update in publisher.publish.call_args_list
                    ]
                    self.assertIn("failed", updates)
                    self.assertNotIn("success", updates)
                    continue
                saved_bias = saved.get(gyro_calibration_section(original_id), config_key)
                self.assertEqual(tuple(float(value) for value in saved_bias.split(",")), bias)
                if reconnect:
                    apply_calibration.assert_not_called()
                elif apply_timeout:
                    apply_calibration.assert_not_called()
                    last_result = next(
                        update.kwargs for update in reversed(publisher.publish.call_args_list)
                        if f"{status_prefix}_calibration_state" in update.kwargs
                    )
                    self.assertEqual(last_result[f"{status_prefix}_calibration_state"], "failed")
                    self.assertEqual(last_result[f"{status_prefix}_calibration_message"], "apply_failed")
                else:
                    apply_calibration.assert_called_once()
                    self.assertEqual(apply_calibration.call_args.args[0], bias)
                    if sensor == "magnetometer":
                        self.assertEqual(apply_calibration.call_args.kwargs["matrix"], matrix)
                    elif sensor == "accelerometer":
                        self.assertEqual(apply_calibration.call_args.args[1], matrix)


class GuiCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.gui = object.__new__(config_gui.ConfigGUI)
        self.gui.root = Mock()
        self.gui.tr = lambda text: text
        self.gui.gyro_calibration_button = Mock()
        self.status = {
            "updated_at": 100.0,
            "state": "connected",
            "controller_id": "001122334455",
            "connection_id": "first-connection",
        }
        self.enterContext(patch.object(config_gui, "STATUS_PATH", Mock(
            read_text=Mock(side_effect=lambda **_kwargs: config_gui.json.dumps(self.status))
        )))
        self.clock = self.enterContext(patch.object(config_gui.time, "time", return_value=100.0))
        self.enqueue = self.enterContext(patch.object(config_gui, "enqueue_controller_command"))
        self.confirm = self.enterContext(patch.object(config_gui.messagebox, "askokcancel"))
        self.showerror = self.enterContext(patch.object(config_gui.messagebox, "showerror"))
        self.showinfo = self.enterContext(patch.object(config_gui.messagebox, "showinfo"))

    def _finish_gyro(self):
        self.gui.calibrate_gyro()
        self.status["gyro_calibration_state"] = "running"
        self.gui.root.after.call_args.args[1]()
        self.status["gyro_calibration_state"] = "success"
        self.status["gyro_calibration_message"] = "saved"
        self.gui.root.after.call_args.args[1]()

    def test_connection_change_during_accelerometer_prompt_stops_calibration(self):
        for new_controller_id in ("AABBCCDDEEFF", "001122334455"):
            with self.subTest(new_controller_id=new_controller_id):
                def confirm(title, _message):
                    if "（2/3）" in title:
                        self.status.update(
                            controller_id=new_controller_id,
                            connection_id="next-connection",
                        )
                    return True

                self.status.update(controller_id="001122334455", connection_id="first-connection")
                self.enqueue.reset_mock()
                self.showerror.reset_mock()
                self.confirm.side_effect = confirm
                self._finish_gyro()

                self.enqueue.assert_called_once_with("calibrate_gyro", connection_id="first-connection")
                self.showerror.assert_called_once()
                self.gui.gyro_calibration_button.state.assert_called_with(["!disabled"])

    def test_non_object_status_ends_the_calibration_flow_with_an_error(self):
        for stage in ("start", "after prompt", "while polling"):
            with self.subTest(stage=stage):
                self.status = {
                    "updated_at": 100.0, "state": "connected",
                    "controller_id": "001122334455", "connection_id": "first-connection",
                }
                self.enqueue.reset_mock()
                self.showerror.reset_mock()
                self.gui.root.after.reset_mock()
                self.gui.gyro_calibration_button.reset_mock()
                if stage == "start":
                    self.status = []
                    self.confirm.side_effect = None
                    self.confirm.return_value = True
                elif stage == "after prompt":
                    def confirm(_title, _message):
                        self.status = []
                        return True

                    self.confirm.side_effect = confirm
                else:
                    self.confirm.side_effect = None
                    self.confirm.return_value = True

                self.gui.calibrate_gyro()
                if stage == "while polling":
                    self.status = []
                    self.gui.root.after.call_args.args[1]()

                self.showerror.assert_called_once()
                if stage == "while polling":
                    self.enqueue.assert_called_once()
                    self.gui.gyro_calibration_button.state.assert_called_with(["!disabled"])
                else:
                    self.enqueue.assert_not_called()

    def test_busy_connector_ends_gyro_calibration_without_a_running_state(self):
        self.confirm.return_value = True
        self.gui.calibrate_gyro()
        self.status.update(gyro_calibration_state="failed", gyro_calibration_message="busy")

        self.gui.root.after.call_args.args[1]()

        self.showerror.assert_called_once()
        self.assertIn("忙碌", self.showerror.call_args.args[1])
        self.gui.gyro_calibration_button.state.assert_called_with(["!disabled"])

    def test_skipping_both_optional_steps_reports_only_gyro_as_saved(self):
        self.confirm.side_effect = [True, False, False]
        self._finish_gyro()

        self.enqueue.assert_called_once()
        self.showinfo.assert_called_once()
        message = self.showinfo.call_args.args[1]
        self.assertIn("陀螺儀校正已完成", message)
        self.assertIn("加速度計沿用原有資料", message)

    def test_slow_status_update_does_not_end_calibration_on_the_same_connection(self):
        self.confirm.return_value = True
        self.gui.calibrate_gyro()
        self.status["gyro_calibration_state"] = "running"
        self.clock.return_value = 104.0
        callback = self.gui.root.after.call_args.args[1]
        self.gui.root.after.reset_mock()
        # A pending disk write can leave the existing connection snapshot stale.
        callback()

        self.showerror.assert_not_called()
        self.gui.root.after.assert_called_once()
