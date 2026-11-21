import configparser
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import extension_bridge as bridge
from extension_bridge import (
    BUTTON_BITS,
    EMPTY_STATE,
    ExtensionInputMixer,
    ExtensionManager,
    ExtensionSettings,
    ExternalState,
    ProtocolError,
    decode_message,
    decode_state,
    load_extension_settings,
    store_extension_settings,
)
from config_utils import load_stick_calibration
from switch2_input import SWITCH_BUTTONS
from xinput_controller import XB_BUTTONS, XInputController


def _report(**values):
    fields = dict(
        wButtons=0, bLeftTrigger=0, bRightTrigger=0,
        sThumbLX=0, sThumbLY=0, sThumbRX=0, sThumbRY=0,
    )
    fields.update(values)
    return SimpleNamespace(**fields)


A = BUTTON_BITS["A"]
B = BUTTON_BITS["B"]


class ProtocolDecodeTests(unittest.TestCase):
    def test_button_bits_match_xinput(self):
        for name, bit in BUTTON_BITS.items():
            self.assertEqual(int(XB_BUTTONS[name]), bit, name)

    def test_buttons_accept_names_and_masks(self):
        self.assertEqual(decode_state({"buttons": ["a", "B"]}).buttons, A | B)
        self.assertEqual(decode_state({"buttons": A | B}).buttons, A | B)
        self.assertEqual(decode_state({}).buttons, 0)

    def test_invalid_values_are_rejected(self):
        for message in (
            {"buttons": ["TURBO"]},
            {"buttons": 0x0800},  # unused XUSB bit
            {"buttons": True},
            {"lt": 256},
            {"rt": -1},
            {"lx": 1000},  # half a stick
            {"lx": 40000, "ly": 0},
            {"lx": 1.5, "ly": 0},
        ):
            with self.subTest(message=message):
                with self.assertRaises(ProtocolError):
                    decode_state(message)

    def test_absent_analog_fields_are_not_driven(self):
        state = decode_state({"buttons": ["A"], "rt": 200, "rx": 5, "ry": -5})
        self.assertIsNone(state.left_trigger)
        self.assertEqual(state.right_trigger, 200)
        self.assertIsNone(state.left_stick)
        self.assertEqual(state.right_stick, (5, -5))

    def test_one_pipe_message_may_carry_several_json_lines(self):
        self.assertEqual(
            decode_message(b'{"type":"state"}\n{"type":"bye"}\n'),
            [{"type": "state"}, {"type": "bye"}],
        )
        for raw in (b"\xff", b"[1]", b"{nope"):
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                decode_message(raw)


class ButtonMergeTests(unittest.TestCase):
    def setUp(self):
        self.mixer = ExtensionInputMixer()

    def frame(self, physical_buttons):
        report = _report(wButtons=physical_buttons)
        self.mixer.merge_into(report)
        return report.wButtons

    def test_or_merge_truth_table(self):
        # physical only
        self.assertEqual(self.frame(A), A)
        # external only
        self.mixer.submit(ExternalState(buttons=A))
        self.assertEqual(self.frame(0), A)
        # both
        self.assertEqual(self.frame(A), A)
        # external releases while physical still holds
        self.mixer.submit(ExternalState(buttons=0))
        self.assertEqual(self.frame(A), A)
        # physical releases while external still holds
        self.mixer.submit(ExternalState(buttons=A))
        self.assertEqual(self.frame(0), A)
        # both released
        self.mixer.submit(ExternalState(buttons=0))
        self.assertEqual(self.frame(0), 0)

    def test_tap_shorter_than_one_report_is_output_once(self):
        self.mixer.submit(ExternalState(buttons=B))
        self.mixer.submit(ExternalState(buttons=0))
        self.assertEqual(self.frame(0), B)
        self.assertEqual(self.frame(0), 0)

    def test_disconnect_clears_external_but_never_physical(self):
        self.mixer.submit(ExternalState(buttons=A | B))
        self.assertEqual(self.frame(A), A | B)
        self.mixer.clear()
        self.assertEqual(self.frame(A), A)
        self.assertIs(self.mixer.state, EMPTY_STATE)

    def test_clear_also_drops_an_unconsumed_tap(self):
        self.mixer.submit(ExternalState(buttons=B))
        self.mixer.submit(ExternalState(buttons=0))
        self.mixer.clear()
        self.assertEqual(self.frame(0), 0)


class AnalogMergeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.mixer = ExtensionInputMixer(clock=lambda: self.now)

    def test_triggers_use_the_stronger_source(self):
        self.mixer.submit(ExternalState(left_trigger=100, right_trigger=255))
        report = _report(bLeftTrigger=180, bRightTrigger=20)
        self.mixer.merge_into(report)
        self.assertEqual((report.bLeftTrigger, report.bRightTrigger), (180, 255))

    def test_undriven_analog_passes_physical_through(self):
        self.mixer.submit(ExternalState(buttons=A))
        report = _report(sThumbLX=12000, bLeftTrigger=40)
        self.mixer.merge_into(report)
        self.assertEqual((report.sThumbLX, report.bLeftTrigger), (12000, 40))

    def test_external_stick_applies_only_while_physical_is_idle(self):
        self.mixer.submit(ExternalState(left_stick=(30000, 0)))
        idle = _report(sThumbLX=500)  # resting noise
        self.mixer.merge_into(idle)
        self.assertEqual((idle.sThumbLX, idle.sThumbLY), (30000, 0))

        moving = _report(sThumbLX=-20000)
        self.mixer.merge_into(moving)
        self.assertEqual(moving.sThumbLX, -20000)

    def test_handback_uses_hysteresis_without_flicker(self):
        self.mixer.submit(ExternalState(left_stick=(30000, 0)))
        engage = int(32767 * 0.2)
        between = int(32767 * 0.09)  # under engage, above release
        small = int(32767 * 0.02)

        self.now = 0.0
        report = _report(sThumbLX=engage)
        self.mixer.merge_into(report)
        self.assertEqual(report.sThumbLX, engage)  # player took the stick

        for step in range(10):  # wobbling near the threshold stays physical
            self.now = 0.01 * (step + 1)
            report = _report(sThumbLX=between)
            self.mixer.merge_into(report)
            self.assertEqual(report.sThumbLX, between)

        self.now = 0.20
        report = _report(sThumbLX=small)
        self.mixer.merge_into(report)
        self.assertEqual(report.sThumbLX, small)  # hold time not elapsed

        self.now = 0.20 + bridge.ExtensionInputMixer.STICK_HANDBACK_SECONDS + 0.001
        report = _report(sThumbLX=small)
        self.mixer.merge_into(report)
        self.assertEqual(report.sThumbLX, 30000)  # handed back to the tool

    def test_right_stick_is_independent(self):
        self.mixer.submit(ExternalState(right_stick=(0, -32768)))
        report = _report(sThumbLX=32767, sThumbRX=0)
        self.mixer.merge_into(report)
        self.assertEqual((report.sThumbLX, report.sThumbRX, report.sThumbRY), (32767, 0, -32768))


class ControllerIntegrationTests(unittest.TestCase):
    def setUp(self):
        config = configparser.ConfigParser()
        config.read(ROOT / "src" / "profiles" / "System Default.ini", encoding="utf-8")
        config.set("buttons", "a", "A")
        self.controller = XInputController(
            config, load_stick_calibration(config), activate_runtime=False,
        )
        self.report = _report()
        self.updates = []
        self.controller.pad = SimpleNamespace(
            report=self.report,
            update=lambda: self.updates.append(int(self.report.wButtons)),
        )

    @staticmethod
    def _state(buttons=0):
        return SimpleNamespace(
            buttons=buttons, left_stick=(2048, 2048), right_stick=(2048, 2048),
            gyroscope=(0, 0, 0), accelerometer=(0, 0, 0), magnetometer=(0, 0, 0),
            report_time=0,
        )

    def test_disabled_path_is_unchanged(self):
        self.assertIsNone(self.controller._extension_input)
        self.controller.update(self._state(SWITCH_BUTTONS["A"]))
        self.assertEqual(self.updates, [A])

    def test_external_input_is_merged_into_the_single_pad_update(self):
        mixer = ExtensionInputMixer()
        self.controller.set_extension_input(mixer)
        mixer.submit(ExternalState(buttons=B))
        self.controller.update(self._state(SWITCH_BUTTONS["A"]))
        self.controller.update(self._state(0))
        mixer.clear()
        self.controller.update(self._state(SWITCH_BUTTONS["A"]))
        # one pad.update() per physical report; physical A survives the clear
        self.assertEqual(self.updates, [A | B, B, A])

    def test_extensions_never_create_a_second_virtual_pad(self):
        import ast

        tree = ast.parse((ROOT / "src" / "extension_bridge.py").read_text(encoding="utf-8"))
        imported = {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        self.assertFalse([name for name in imported if name.startswith("vgamepad")])
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        # No ViGEm target and no extra pad.update(): S2P keeps its single output.
        self.assertFalse(attributes & {"VX360Gamepad", "update", "pad"})

    def test_reconfigure_keeps_the_attached_mixer(self):
        mixer = ExtensionInputMixer()
        self.controller.set_extension_input(mixer)
        self.controller.pad.reset = lambda: None
        self.controller.reconfigure(self.controller.config, self.controller.cal)
        self.assertIs(self.controller._extension_input, mixer)


class HubTests(unittest.TestCase):
    def test_several_tools_combine_buttons_and_triggers(self):
        hub = bridge.ExtensionInputHub()
        first, second = ExtensionInputMixer(), ExtensionInputMixer()
        hub.add(first)
        hub.add(second)
        first.submit(ExternalState(buttons=A, left_trigger=80))
        second.submit(ExternalState(buttons=B, left_trigger=200))
        report = _report(wButtons=BUTTON_BITS["X"], bLeftTrigger=100)
        hub.merge_into(report)
        self.assertEqual(report.wButtons, A | B | BUTTON_BITS["X"])
        self.assertEqual(report.bLeftTrigger, 200)
        # Removing one tool clears only that tool's input.
        hub.remove(second)
        report = _report()
        hub.merge_into(report)
        self.assertEqual(report.wButtons, A)
        self.assertIs(second.state, EMPTY_STATE)

    def test_physical_snapshot_only_while_a_reader_is_connected(self):
        hub = bridge.ExtensionInputHub()
        writer = ExtensionInputMixer()
        hub.add(writer)
        writer.submit(ExternalState(buttons=B))
        hub.merge_into(_report(wButtons=A))
        self.assertIsNone(hub.physical_snapshot)
        reader = ExtensionInputMixer()
        hub.add(reader, reader=True)
        hub.merge_into(_report(wButtons=A, sThumbLX=-5))
        # The snapshot is S2P's output before any tool merge.
        self.assertEqual(hub.physical_snapshot[2], A)
        self.assertEqual(hub.physical_snapshot[5], -5)
        hub.remove(reader, reader=True)
        self.assertIsNone(hub.physical_snapshot)


def _tool(tool_id, path, **options):
    return bridge.ToolSettings(tool_id=tool_id, path=path, **options)


class CancelRaceTests(unittest.TestCase):
    """A read that completes just as its timeout cancels it keeps its data."""

    def run_timeout(self, finished, error=0):
        import ctypes

        def overlapped_result(_pipe, _overlapped, transferred, wait):
            self.assertTrue(wait)  # only the post-cancel call reaches here
            if finished:
                transferred._obj.value = 42
                return 1
            ctypes.set_last_error(error)
            return 0

        api = {
            "WaitForMultipleObjects": lambda *args: bridge.WAIT_TIMEOUT,
            "CancelIoEx": lambda *args: 1,
            "GetOverlappedResult": overlapped_result,
        }
        instance = object.__new__(bridge._PipeInstance)
        instance.pipe, instance.io_event, instance.drop_event = 1, 2, 3
        instance.server = SimpleNamespace(stop_event=4)
        with patch.object(bridge, "_win32", return_value=api), \
                patch.object(ctypes, "get_last_error", side_effect=[bridge.ERROR_IO_PENDING, error]):
            return instance._complete(ctypes.c_int(), False, 10)

    def test_completed_read_is_kept(self):
        self.assertEqual(self.run_timeout(True), 42)

    def test_cancelled_read_times_out(self):
        with self.assertRaises(bridge._Timeout):
            self.run_timeout(False, bridge.ERROR_OPERATION_ABORTED)

    def test_client_gone_during_cancel(self):
        with self.assertRaises(bridge._ClientGone):
            self.run_timeout(False, bridge.ERROR_BROKEN_PIPE)


class SettingsTests(unittest.TestCase):
    def test_missing_or_malformed_section_is_disabled(self):
        self.assertEqual(load_extension_settings(configparser.ConfigParser()), ExtensionSettings())
        config = configparser.ConfigParser()
        config.read_string("[extensions]\nenabled = maybe\n")
        self.assertEqual(load_extension_settings(config), ExtensionSettings())

    def test_tool_list_round_trip_keeps_order(self):
        config = configparser.ConfigParser()
        settings = ExtensionSettings((
            _tool("b2", r"C:\Tools\Multi.exe", allow_read=True),
            _tool("a1", r"C:\Tools\Macro.exe", enabled=False, auto_launch=True),
        ))
        store_extension_settings(config, settings)
        self.assertEqual(load_extension_settings(config), settings)
        # Rewriting with fewer tools removes the old sections.
        store_extension_settings(config, ExtensionSettings(settings.tools[:1]))
        self.assertEqual(len(load_extension_settings(config).tools), 1)

    def test_the_list_alone_decides_whether_extensions_run(self):
        self.assertFalse(ExtensionSettings().enabled)
        self.assertFalse(ExtensionSettings((_tool("a", r"C:\a.exe", enabled=False),)).enabled)
        self.assertTrue(ExtensionSettings((_tool("a", r"C:\a.exe"),)).enabled)

    def test_old_master_switch_that_was_off_keeps_its_tools_off(self):
        config = configparser.ConfigParser()
        config.read_string(
            "[extensions]\nenabled = false\n"
            "[extensions.tool.a]\npath = C:\\a.exe\nenabled = true\n"
        )
        settings = load_extension_settings(config)
        self.assertEqual([tool.enabled for tool in settings.tools], [False])
        self.assertFalse(settings.enabled)
        store_extension_settings(config, settings)
        self.assertFalse(config.has_option("extensions", "enabled"))
        self.assertEqual(load_extension_settings(config), settings)
        # A switch that was on changes nothing.
        config.read_string("[extensions]\nenabled = true\n")
        config.set("extensions.tool.a", "enabled", "true")
        self.assertTrue(load_extension_settings(config).enabled)

    def test_tool_defaults_allow_input_and_deny_read(self):
        config = configparser.ConfigParser()
        config.read_string("[extensions]\nenabled = true\n[extensions.tool.x]\npath = C:\\t.exe\n")
        tool = load_extension_settings(config).tools[0]
        self.assertTrue(tool.enabled and tool.allow_input)
        self.assertFalse(tool.allow_read or tool.auto_launch)

    def test_duplicate_paths_are_dropped_on_load(self):
        config = configparser.ConfigParser()
        config.read_string(
            "[extensions]\nenabled = true\n"
            "[extensions.tool.a]\norder = 0\npath = C:\\Tools\\Macro.exe\n"
            "[extensions.tool.b]\norder = 1\npath = c:\\tools\\MACRO.exe\n"
        )
        self.assertEqual([tool.tool_id for tool in load_extension_settings(config).tools], ["a"])

    def test_legacy_single_tool_settings_are_migrated(self):
        config = configparser.ConfigParser()
        config.read_string("[extensions]\nenabled = true\ntool_path = C:\\old.exe\nauto_launch = true\n")
        settings = load_extension_settings(config)
        self.assertEqual(settings.tools[0].path, "C:\\old.exe")
        self.assertTrue(settings.tools[0].auto_launch)

    def test_configuration_conflicts(self):
        tools = tuple(_tool(f"t{index}", f"C:\\t{index}.exe") for index in range(bridge.MAX_TOOLS + 1))
        tools += (_tool("dup", "C:\\T0.EXE"), _tool("none", "C:\\n.exe", allow_input=False))
        problems = bridge.settings_problems(ExtensionSettings(tools))
        self.assertIn(("too_many_tools", f"t{bridge.MAX_TOOLS}"), problems)
        self.assertIn(("duplicate_tool", "dup"), problems)
        self.assertIn(("no_permission", "none"), problems)
        self.assertEqual(len(ExtensionSettings(tools).active_tools), bridge.MAX_TOOLS)


class _FakeServer:
    instances = []

    def __init__(self, owner, hub, instances, pipe_name, server_version, controller_info=None):
        self.owner = owner
        self.hub = hub
        self.instance_count = instances
        self.running = False
        self.stopped = False
        self.dropped = []
        self.drop_codes = []
        _FakeServer.instances.append(self)

    def start(self):
        self.running = True

    def drop(self, tool_id, code="user_disconnected"):
        self.dropped.append(tool_id)
        self.drop_codes.append(code)

    def stop(self, timeout=1.0):
        self.running = False
        self.stopped = True
        return True


MACRO = r"C:\Tools\Macro.exe"
MULTI = r"C:\Tools\Multi.exe"


class ManagerTests(unittest.TestCase):
    def setUp(self):
        _FakeServer.instances.clear()
        self.attached = []
        xinput = SimpleNamespace(set_extension_input=self.attached.append)
        self.manager = ExtensionManager(xinput, server_factory=_FakeServer)
        self.launches = []
        self.reopened = []

        self.background = []

        def launch(tool_id, reopen=False, background=False):
            self.launches.append(tool_id)
            self.reopened.append(reopen)
            self.background.append(background)

        self.manager.launch_tool = launch

    def enable(self, *tools):
        self.manager.apply(ExtensionSettings(tools))

    def open(self, path, **hello):
        return self.manager.open_session(1, path, {"name": Path(path).stem, **hello})

    def test_disabled_creates_no_server(self):
        self.manager.apply(ExtensionSettings((_tool("m", MACRO, enabled=False),)), startup=True)
        self.assertEqual(_FakeServer.instances, [])
        self.assertEqual(self.manager.status()["state"], bridge.STATE_DISABLED)

    def test_enabling_or_disabling_a_tool_keeps_the_others_connected(self):
        macro_tool = _tool("m", MACRO)
        self.enable(macro_tool)
        server = _FakeServer.instances[-1]
        self.assertEqual(server.instance_count, bridge.MAX_TOOLS)
        self.assertIs(self.attached[-1], server.hub)
        self.open(MACRO)
        self.enable(macro_tool, _tool("x", MULTI))   # enable another tool
        self.enable(macro_tool)                      # and disable it again
        self.assertEqual(_FakeServer.instances, [server])
        self.assertFalse(server.stopped)
        self.assertEqual((server.dropped, server.drop_codes), (["x"], ["settings_changed"]))
        self.assertEqual(self.manager.status()["tools"]["m"]["state"], bridge.STATE_CONNECTED)
        self.enable()
        self.assertTrue(server.stopped)
        self.assertIsNone(self.attached[-1])
        self.assertEqual(self.manager.status()["state"], bridge.STATE_DISABLED)

    def test_tools_are_identified_by_path_or_launch_token(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI))
        self.assertEqual(self.open(r"c:\tools\MACRO.exe").tool_id, "m")
        self.manager._launch_tokens["secret"] = "x"
        self.assertEqual(
            self.manager.open_session(1, r"C:\Python\python.exe", {"token": "secret"}).tool_id, "x",
        )
        with self.assertRaises(ProtocolError) as caught:
            self.open(r"C:\Other\evil.exe")
        self.assertEqual(caught.exception.code, "unauthorized")
        self.assertEqual(self.manager.status()["code"], "unauthorized")

    def test_two_tools_merge_and_a_second_copy_is_refused(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI))
        macro = self.open(MACRO)
        multi = self.open(MULTI)
        hub = _FakeServer.instances[-1].hub
        self.assertEqual(hub.mixers, (macro.mixer, multi.mixer))
        with self.assertRaises(ProtocolError) as caught:
            self.open(MACRO)
        self.assertEqual(caught.exception.code, "already_connected")
        status = self.manager.status()["tools"]
        self.assertEqual(status["m"]["state"], bridge.STATE_CONNECTED)
        self.assertEqual(status["x"]["state"], bridge.STATE_CONNECTED)

    def test_stick_claims_are_exclusive(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI))
        macro = self.open(MACRO, sticks=["left"])
        with self.assertRaises(ProtocolError) as caught:
            self.open(MULTI, sticks=["left", "right"])
        self.assertEqual(caught.exception.code, "conflict")
        entry = self.manager.status()["tools"]["x"]
        self.assertEqual(
            (entry["state"], entry["code"], entry["stick"], entry["with_tool"]),
            (bridge.STATE_ERROR, "conflict", "left", "Macro"),
        )
        # The right stick alone is free; after the macro tool leaves, left is too.
        self.assertEqual(self.open(MULTI, sticks=["right"]).sticks, ("right",))
        self.manager.close_session(macro, bridge.STATE_DISCONNECTED, "tool_closed")
        self.assertEqual(self.manager._stick_owner, {"right": "x"})
        self.assertEqual(self.manager.status()["tools"]["m"]["code"], "tool_closed")

    def test_capabilities_are_granted_only_when_declared_and_allowed(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI, allow_input=False, allow_read=True))
        self.assertEqual(self.open(MACRO, capabilities=["input", "read"]).granted, ("input",))
        reader = self.open(MULTI, capabilities=["input", "read"], sticks=["left"])
        self.assertEqual((reader.granted, reader.sticks), (("read",), ()))
        self.manager.close_session(reader, bridge.STATE_DISCONNECTED, "tool_closed")
        with self.assertRaises(ProtocolError) as caught:
            self.open(MULTI, capabilities=["input"])
        self.assertEqual(caught.exception.code, "permission_denied")
        with self.assertRaises(ProtocolError):
            self.open(MULTI, capabilities="read")

    def conflicts(self):
        return [
            {key: value for key, value in item.items() if key != "name"}
            for item in self.manager.status()["conflicts"]
        ]

    def test_stick_conflict_stays_listed_while_the_other_tool_holds_the_stick(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI))
        owner = self.open(MULTI, sticks=["left"])
        with self.assertRaises(ProtocolError):
            self.open(MACRO, sticks=["left", "right"])
        expected = [{"kind": "stick", "tool": "m", "stick": "left", "with_id": "x", "with_tool": "Multi"}]
        self.assertEqual(self.conflicts(), expected)
        # The macro tool gives up the stick and reconnects: still listed.
        self.open(MACRO)
        self.assertEqual(self.manager.status()["tools"]["m"]["state"], bridge.STATE_CONNECTED)
        self.assertEqual(self.conflicts(), expected)
        self.manager.close_session(owner, bridge.STATE_DISCONNECTED, "tool_closed")
        self.assertEqual(self.conflicts(), [])

    def test_missing_permissions_are_listed(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI, allow_input=False, allow_read=True))
        macro = self.open(MACRO, capabilities=["input", "read"])
        with self.assertRaises(ProtocolError):
            self.open(MULTI, capabilities=["input"])
        self.assertEqual(self.conflicts(), [
            {"kind": "permission", "tool": "m", "denied": ["read"]},
            {"kind": "permission", "tool": "x", "denied": ["input"]},
        ])
        self.manager.close_session(macro, bridge.STATE_DISCONNECTED, "tool_closed")
        self.enable(_tool("x", MULTI, allow_input=True, allow_read=True))
        self.assertEqual(self.conflicts(), [])

    def test_second_copies_and_unlisted_programs_are_listed_for_a_while(self):
        self.enable(_tool("m", MACRO))
        first = self.open(MACRO)
        with self.assertRaises(ProtocolError):
            self.open(MACRO)
        with self.assertRaises(ProtocolError):
            self.open(r"C:\Other\Spy.exe")
        self.assertEqual(self.conflicts(), [
            {"kind": "second_copy", "tool": "m"},
            {"kind": "unauthorized", "program": "Spy.exe"},
        ])
        # The unlisted program stops trying: it is dropped after a while.
        for entry in self.manager._conflicts.values():
            entry["at"] -= bridge.RECENT_CONFLICT_SECONDS + 1
        self.assertEqual(self.conflicts(), [])
        # A second copy is no longer a problem once the first one has closed.
        with self.assertRaises(ProtocolError):
            self.open(MACRO)
        self.manager.close_session(first, bridge.STATE_DISCONNECTED, "tool_closed")
        self.assertEqual(self.conflicts(), [])

    def test_disconnect_one_tool_pauses_only_that_tool(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI))
        server = _FakeServer.instances[-1]
        macro = self.open(MACRO)
        self.open(MULTI)
        self.manager.disconnect("m")
        self.assertEqual(server.dropped, ["m"])
        self.manager.close_session(macro, bridge.STATE_DISCONNECTED, "tool_closed")
        tools = self.manager.status()["tools"]
        self.assertEqual((tools["m"]["state"], tools["m"]["code"]), ("disconnected", "user_disconnected"))
        self.assertEqual(tools["x"]["state"], "connected")
        with self.assertRaises(ProtocolError) as caught:
            self.open(MACRO)  # stays refused until Connect
        self.assertEqual(caught.exception.code, "user_disconnected")
        del self.launches[:], self.reopened[:]  # ignore the start-up launches
        self.assertTrue(self.manager.connect("m"))
        self.assertEqual(self.open(MACRO).tool_id, "m")
        self.assertEqual((self.launches, self.reopened), (["m"], [True]))

    def test_open_brings_a_connected_tool_forward(self):
        self.enable(_tool("m", MACRO))
        self.open(MACRO)  # connected, e.g. hidden in the notification area
        del self.launches[:], self.reopened[:]  # ignore the start-up launch
        self.assertTrue(self.manager.connect("m"))
        self.assertEqual((self.launches, self.reopened), (["m"], [True]))
        # The session is untouched, so the status must not fall back to waiting.
        self.assertEqual(self.manager.status()["tools"]["m"]["state"], "connected")
        self.assertEqual(self.manager.status()["state"], "connected")

    def test_changing_one_tool_drops_only_its_session(self):
        macro, multi = _tool("m", MACRO), _tool("x", MULTI)
        self.enable(macro, multi)
        server = _FakeServer.instances[-1]
        self.enable(macro, bridge.ToolSettings("x", MULTI, allow_read=True))
        self.assertEqual((server.dropped, server.drop_codes), (["x"], ["settings_changed"]))
        self.assertFalse(server.stopped)
        # The dropped tool waits for its own automatic reconnect.
        session = self.open(MULTI)
        self.manager.close_session(session, bridge.STATE_DISCONNECTED, "settings_changed")
        entry = self.manager.status()["tools"]["x"]
        self.assertEqual((entry["state"], entry["code"]), (bridge.STATE_WAITING, "settings_changed"))

    def test_enabled_tools_start_in_the_background_with_s2p(self):
        self.manager.apply(ExtensionSettings((
            _tool("m", MACRO), _tool("x", MULTI, enabled=False),
        )), startup=True)
        self.assertEqual((self.launches, self.background), (["m"], [True]))
        # Re-applying unchanged settings does not start anything again...
        self.manager.apply(ExtensionSettings((
            _tool("m", MACRO), _tool("x", MULTI, enabled=False),
        )))
        self.assertEqual(self.launches, ["m"])
        # ...but enabling another tool starts it right away, also hidden.
        self.manager.apply(ExtensionSettings((_tool("m", MACRO), _tool("x", MULTI))))
        self.assertEqual((self.launches, self.background), (["m", "x"], [True, True]))

    def test_launch_tells_the_tool_whether_to_stay_hidden(self):
        import subprocess as subprocess_module

        tool_file = Path(self.id().replace(".", "_") + ".exe")
        self.addCleanup(lambda: tool_file.unlink(missing_ok=True))
        tool_file.write_bytes(b"")
        manager = ExtensionManager(SimpleNamespace(set_extension_input=lambda hub: None),
                                   server_factory=_FakeServer)
        manager.apply(ExtensionSettings(()))
        manager._settings = ExtensionSettings((_tool("t", str(tool_file.resolve())),))
        seen = []
        with patch.object(subprocess_module, "Popen",
                          side_effect=lambda command, **kwargs: seen.append(kwargs["env"]) or SimpleNamespace(poll=lambda: None)):
            with patch.object(bridge, "_image_is_running", return_value=False):
                manager.launch_tool("t", background=True)
                manager.launch_tool("t", reopen=True)
        self.assertEqual(seen[0].get(bridge.BACKGROUND_ENV), "1")
        self.assertNotIn(bridge.BACKGROUND_ENV, seen[1])

        # Tokens do not pile up: only the newest few per tool stay valid,
        # and a tool that leaves the list loses them all.
        with patch.object(subprocess_module, "Popen",
                          side_effect=lambda command, **kwargs: seen.append(kwargs["env"]) or SimpleNamespace(poll=lambda: None)):
            for _ in range(10):
                manager.launch_tool("t", reopen=True)
        newest = [env[bridge.TOKEN_ENV] for env in seen[-bridge.LAUNCH_TOKENS_PER_TOOL:]]
        self.assertEqual(list(manager._launch_tokens), newest)
        manager.apply(ExtensionSettings(()))
        self.assertEqual(manager._launch_tokens, {})


@unittest.skipUnless(sys.platform == "win32", "Windows named pipes")
class NamedPipeServerTests(unittest.TestCase):
    """Real pipe instances; in-process clients are told apart by launch tokens."""

    def setUp(self):
        from extension_ipc_test_client import ExtensionClient

        self.Client = ExtensionClient
        self.pipe_name = rf"\\.\pipe\S2P-XInput-Lite.Extensions.test-{uuid.uuid4().hex}"
        self.attached = []
        xinput = SimpleNamespace(set_extension_input=self.attached.append)
        self.manager = ExtensionManager(
            xinput, pipe_name=self.pipe_name,
            controller_info=lambda: {"connected": True, "mode": "bluetooth"},
        )
        self.manager.launch_tool = lambda tool_id, reopen=False, background=False: True
        self.manager._launch_tokens.update({"tok-m": "m", "tok-x": "x"})
        self.addCleanup(self.manager.close)

    def enable(self, *tools):
        self.manager.apply(ExtensionSettings(tools))
        self.assertIsNotNone(self.manager._server)
        # Stand-in for S2P starting the in-process clients: a tool that left
        # the list lost its tokens, so they are handed out again here.
        self.manager._launch_tokens.update({"tok-m": "m", "tok-x": "x"})

    @property
    def hub(self):
        return self.manager._hub

    def client(self, token="tok-m", **options):
        client = self.Client(self.pipe_name, name=f"Tool {token}", token=token, **options)
        return client

    def tool_state(self, tool_id):
        return self.manager.status()["tools"].get(tool_id, {})

    def merged(self):
        report = _report()
        self.hub.merge_into(report)
        return report

    def wait_for(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.005)
        return False

    @staticmethod
    def abort(client):
        """Close without "bye", like a crashed tool."""
        client._pipe.close()
        client._pipe = None

    def test_round_trip_and_crash_clears_input(self):
        self.enable(_tool("m", MACRO))
        client = self.client()
        welcome = client.connect()
        self.assertEqual((welcome["type"], welcome["protocol"]), ("welcome", 1))
        self.assertEqual(self.tool_state("m")["name"], "Tool tok-m")
        client.state(["A", "X"], rt=128)
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == A | BUTTON_BITS["X"]))
        self.assertEqual(self.merged().bRightTrigger, 128)
        self.abort(client)
        self.assertTrue(self.wait_for(lambda: self.tool_state("m").get("state") == "disconnected"))
        self.assertEqual(self.hub.mixers, ())
        self.assertEqual(self.merged().wButtons, 0)
        # The instance keeps listening; a restarted tool can reconnect.
        again = self.client()
        again.connect()
        again.state(["Y"])
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == BUTTON_BITS["Y"]))
        again.close()
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == 0))

    def test_welcome_names_the_s2p_window_process(self):
        self.enable(_tool("m", MACRO))
        plain = self.client()
        self.assertNotIn("host_pid", plain.connect())  # no GUI: nothing to follow
        plain.close()
        self.manager.host_pid = 4321
        followed = self.client()
        self.assertTrue(self.wait_for(lambda: self._connect_quietly(followed)))
        self.assertEqual(followed.welcome["host_pid"], 4321)
        followed.close()

    def test_two_tools_connect_at_once_and_merge(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI))
        macro = self.client("tok-m")
        multi = self.client("tok-x", sticks=("right",))
        macro.connect()
        multi.connect()
        macro.state(["A"], lt=90)
        multi.state(["B"], lt=30, rx=0, ry=32767)
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == A | B))
        report = self.merged()
        self.assertEqual((report.bLeftTrigger, report.sThumbRY), (90, 32767))
        self.abort(macro)
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == B))
        multi.close()

    def test_stick_conflict_is_rejected_with_details(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI))
        owner = self.client("tok-m", sticks=("left",))
        owner.connect()
        rival = self.client("tok-x", sticks=("left",))
        with self.assertRaises(ConnectionError) as caught:
            rival.connect()
        self.assertIn("conflict", str(caught.exception))
        self.abort(rival)
        entry = self.tool_state("x")
        self.assertEqual((entry["code"], entry["with_tool"]), ("conflict", "Tool tok-m"))
        # Sending a stick the tool never claimed is refused.
        owner.send({"type": "state", "buttons": [], "rx": 1, "ry": 1})
        self.assertIn(b"permission_denied", owner._pipe.read(4096))
        self.abort(owner)

    def test_unknown_program_is_rejected_without_input(self):
        self.enable(_tool("m", MACRO))
        client = self.client(token="guess")
        with self.assertRaises(ConnectionError):
            client.connect()
        self.abort(client)
        self.assertTrue(self.wait_for(lambda: self.manager.status()["code"] == "unauthorized"))
        self.assertEqual(self.hub.mixers, ())

    def test_wrong_protocol_and_bad_messages_disconnect(self):
        self.enable(_tool("m", MACRO))
        with open(self.pipe_name, "r+b", buffering=0) as pipe:
            pipe.write(b'{"type":"hello","protocol":99}\n')
            self.assertIn(b"unsupported_protocol", pipe.read(4096))
        client = self.client()
        self.assertTrue(self.wait_for(lambda: self._connect_quietly(client)))
        client.state(["A"])
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == A))
        client.send({"type": "state", "buttons": ["NOPE"]})
        self.assertTrue(self.wait_for(lambda: self.tool_state("m").get("code") == "bad_message"))
        self.assertEqual(self.merged().wButtons, 0)
        self.abort(client)

    @staticmethod
    def _connect_quietly(client):
        try:
            client.connect(timeout=0.1)
            return True
        except OSError:
            return False

    def test_user_disconnect_drops_only_that_tool(self):
        self.enable(_tool("m", MACRO), _tool("x", MULTI))
        macro, multi = self.client("tok-m"), self.client("tok-x")
        macro.connect()
        multi.connect()
        macro.state(["A"])
        multi.state(["B"])
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == A | B))
        self.manager.disconnect("m")
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == B))
        self.assertEqual(self.tool_state("m")["code"], "user_disconnected")
        self.abort(macro)
        multi.close()

    def test_silent_tool_input_times_out_but_stays_connected(self):
        self.enable(_tool("m", MACRO))
        with patch.object(bridge, "INPUT_TIMEOUT_MS", 150):
            client = self.client()
            client.connect()
            client.state(["A"])
            self.assertTrue(self.wait_for(lambda: self.merged().wButtons == A))
            self.assertTrue(self.wait_for(lambda: self.merged().wButtons == 0, timeout=2.0))
            self.assertEqual(self.tool_state("m")["code"], "input_timeout")
            client.state(["B"])
            self.assertTrue(self.wait_for(lambda: self.merged().wButtons == B))
            self.assertEqual(self.tool_state("m")["code"], "")
            client.close()

    def test_heartbeat_keeps_held_input_alive(self):
        self.enable(_tool("m", MACRO))
        with patch.object(bridge, "INPUT_TIMEOUT_MS", 150):
            client = self.client()
            client.connect()
            client.start_heartbeat(interval=0.04)
            client.state(["A"])
            self.assertTrue(self.wait_for(lambda: self.merged().wButtons == A))
            time.sleep(0.5)
            self.assertEqual(self.merged().wButtons, A)
            client.close()

    def test_read_requires_the_read_capability(self):
        self.enable(_tool("m", MACRO, allow_read=True), _tool("x", MULTI))
        reader = self.client("tok-m", capabilities=("read",))
        self.assertEqual(reader.connect()["capabilities"], ["read"])
        self.assertEqual(
            reader.request({"type": "get_controller"}),
            {"connected": True, "mode": "bluetooth", "type": "controller"},
        )
        self.hub.merge_into(_report(wButtons=A, bRightTrigger=9, sThumbLX=-300))
        reply = reader.request({"type": "get_input"})
        self.assertEqual((reply["buttons"], reply["rt"], reply["lx"]), (["A"], 9, -300))
        writer = self.client("tok-x")
        writer.connect()
        writer.send({"type": "get_input"})
        self.assertIn(b"permission_denied", writer._pipe.read(4096))
        self.abort(writer)
        reader.send({"type": "state", "buttons": ["B"]})
        self.assertIn(b"permission_denied", reader._pipe.read(4096))
        self.assertTrue(self.wait_for(lambda: self.hub.physical_snapshot is None))
        self.abort(reader)

    def test_second_server_on_the_same_name_reports_pipe_in_use(self):
        self.enable(_tool("m", MACRO))
        other = bridge.ExtensionPipeServer(self.manager, bridge.ExtensionInputHub(), pipe_name=self.pipe_name)
        with self.assertRaises(ProtocolError) as caught:
            other.start()
        self.assertEqual(caught.exception.code, "pipe_in_use")

    def test_repeated_enable_connect_disable_leaves_no_threads_or_handles(self):
        baseline = threading.active_count()
        for _ in range(10):
            self.enable(_tool("m", MACRO), _tool("x", MULTI))
            self.assertEqual(threading.active_count(), baseline + bridge.MAX_TOOLS)
            client = self.client()
            client.connect()
            client.state(["A"])
            self.assertTrue(self.wait_for(lambda: self.merged().wButtons == A))
            started = time.perf_counter()
            # FIRST_PIPE_INSTANCE on the next pass fails if a handle leaked.
            self.manager.apply(ExtensionSettings(()))
            self.assertLess(time.perf_counter() - started, 0.5)
            self.assertIsNone(self.attached[-1])
            self.abort(client)
        self.assertEqual(threading.active_count(), baseline)

    def test_flooding_client_is_rate_limited(self):
        self.enable(_tool("m", MACRO))
        client = self.client()
        client.connect()
        started = time.perf_counter()
        for index in range(4 * bridge.FLOOD_WINDOW_MESSAGES):
            client.state(["A"] if index % 2 else [])
        client.state(["B"])
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == B, timeout=5.0))
        self.assertGreaterEqual(
            time.perf_counter() - started, 3 * bridge.FLOOD_WINDOW_SECONDS * 0.9
        )
        client.close()

    def test_short_burst_is_not_throttled(self):
        self.enable(_tool("m", MACRO))
        client = self.client()
        client.connect()
        started = time.perf_counter()
        for _ in range(10):
            client.state(["A"])
        client.state(["X"])
        self.assertTrue(self.wait_for(lambda: self.merged().wButtons == BUTTON_BITS["X"]))
        self.assertLess(time.perf_counter() - started, bridge.FLOOD_WINDOW_SECONDS)
        client.close()


class ConsoleMessageTests(unittest.TestCase):
    def test_each_change_prints_one_line(self):
        connected = {"m": {"state": "connected", "name": "Macro", "capabilities": ["input", "read"],
                           "sticks": ["left"]}}
        lines = bridge.describe_tool_changes({}, connected)
        self.assertEqual(lines, [(
            "擴充工具已連線：Macro（權限：輸入、讀取；控制左搖桿）",
            "Extension connected: Macro (permissions: input, read; controls the left stick)",
        )])
        # The same state on the next heartbeat prints nothing.
        self.assertEqual(bridge.describe_tool_changes(connected, connected), [])
        closed = {"m": {"state": "disconnected", "code": "tool_closed", "name": "Macro"}}
        self.assertEqual(bridge.describe_tool_changes(connected, closed)[0][0], "擴充工具已中斷：Macro（工具已關閉）")
        failed = {"m": {"state": "error", "code": "conflict", "name": "Macro"}}
        self.assertIn("與其他工具搶同一支搖桿", bridge.describe_tool_changes(closed, failed)[0][0])
        waiting = {"m": {"state": "waiting", "code": "settings_changed", "name": "Macro"}}
        self.assertIn("等待重新連線", bridge.describe_tool_changes(connected, waiting)[0][0])
        timeout = {"m": {"state": "connected", "code": "input_timeout", "name": "Macro"}}
        self.assertIn("輸入逾時", bridge.describe_tool_changes(connected, timeout)[0][0])
        # Recovering from the timeout is not announced as a new connection.
        self.assertEqual(bridge.describe_tool_changes(timeout, connected), [])

    def test_name_falls_back_to_the_listed_exe(self):
        settings = ExtensionSettings((_tool("m", MACRO),))
        lines = bridge.describe_tool_changes({}, {"m": {"state": "waiting", "code": "settings_changed"}}, settings)
        self.assertIn("「Macro」", lines[0][0])

    def test_overall_state_is_connected_while_a_tool_is_in(self):
        manager = ExtensionManager(SimpleNamespace(set_extension_input=lambda hub: None),
                                   server_factory=_FakeServer)
        manager.launch_tool = lambda tool_id, reopen=False, background=False: True
        manager.apply(ExtensionSettings((_tool("m", MACRO),)))
        self.assertEqual(manager.status()["state"], bridge.STATE_WAITING)
        manager.open_session(1, MACRO, {"name": "Macro"})
        self.assertEqual(manager.status()["state"], bridge.STATE_CONNECTED)


class GuiStatusTextTests(unittest.TestCase):
    def setUp(self):
        from gui_sections import extensions as window

        self.window = window
        self.tool = _tool("m", MACRO)
        self.settings = ExtensionSettings((self.tool,))

    def text(self, status, settings=None, tool=None):
        return self.window.tool_status_text(
            lambda value: value, settings or self.settings, tool or self.tool, status,
        )[0]

    def test_state_labels(self):
        def runtime(**entry):
            return {"extensions": {"state": "waiting", "tools": {"m": entry}}}

        off = bridge.replace(self.tool, enabled=False)
        self.assertEqual(self.text(None, ExtensionSettings((off,)), off), "未啟用")
        self.assertEqual(self.text(None), "等待連線（連接程式未啟動）")
        self.assertEqual(self.text(runtime(state="waiting")), "等待連線")
        self.assertEqual(self.text(runtime(state="connected")), "已連接")
        self.assertIn("工具已關閉連線", self.text(runtime(state="disconnected", code="tool_closed")))
        self.assertIn("連線失敗", self.text(runtime(state="error", code="conflict")))
        self.assertIn("輸入逾時", self.text(runtime(state="connected", code="input_timeout")))
        self.assertIn(
            "通訊管道已被其他程式占用",
            self.text({"extensions": {"state": "error", "code": "pipe_in_use"}}),
        )

    def test_conflict_messages(self):
        tools = (
            self.tool, _tool("x", MULTI), _tool("d", MACRO.upper()),
        )
        settings = ExtensionSettings(tools)
        status = {"extensions": {"state": "error", "code": "pipe_in_use", "tools": {}, "conflicts": [
            {"kind": "stick", "tool": "x", "name": "Multi", "stick": "left", "with_id": "m",
             "with_tool": "Macro"},
            {"kind": "permission", "tool": "m", "name": "Macro", "denied": ["read"]},
            {"kind": "second_copy", "tool": "m", "name": "Macro"},
            {"kind": "unauthorized", "program": "Spy.exe"},
            {"kind": "unknown-later-kind"}, "not a dict",
        ]}}
        lines = self.window.conflict_messages(lambda value: value, settings, status)
        self.assertEqual(lines, [
            "「MACRO」重複加入清單",
            "通訊管道已被其他程式占用（可能有另一個 S2P 正在執行），工具都無法連線",
            "「Multi」要求控制左搖桿被拒絕：目前由「Macro」控制",
            "「Macro」要求「允許讀取控制器狀態」權限，但未允許",
            "「Macro」有另一份正在執行，第二份無法連線",
            "不在清單中的程式「Spy.exe」嘗試連線，已拒絕",
        ])
        self.assertEqual(
            self.window.conflict_messages(lambda value: value, self.settings, None), [],
        )


if __name__ == "__main__":
    unittest.main()
