"""Minimal Extensions IPC test client (development only, not shipped).

It speaks protocol v1 over the S2P named pipe with nothing but the standard
library, so it doubles as the reference client for the future S2P-Extensions
SDK.

Usage: start S2P with Extensions enabled, then either run this script directly
(select python.exe as the tool) or select this .py as the tool and press
Connect, so S2P launches it with a one-time token:

    python tests/extension_ipc_test_client.py demo
    python tests/extension_ipc_test_client.py hold A B --seconds 5
    python tests/extension_ipc_test_client.py read
    python tests/extension_ipc_test_client.py interactive

Interactive commands: ``A B`` (hold those buttons), ``lt 255``, ``lx 0 32767``
(left stick), ``release``, ``quit``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

DEFAULT_PIPE = r"\\.\pipe\S2P-XInput-Lite.Extensions"
HEARTBEAT_SECONDS = 0.25  # well inside S2P's 1000 ms input timeout


class ExtensionClient:
    def __init__(self, pipe_name=None, name="S2P IPC Test Client", token=None,
                 capabilities=("input",), sticks=()):
        self.pipe_name = pipe_name or os.environ.get("S2P_EXTENSION_PIPE") or DEFAULT_PIPE
        self.name = name
        self.token = token if token is not None else os.environ.get("S2P_EXTENSION_TOKEN")
        self.capabilities = list(capabilities)
        self.sticks = list(sticks)  # sticks this tool drives exclusively
        self._pipe = None
        self._lock = threading.Lock()
        self._last_state = None
        self._heartbeat = None
        self._stop = threading.Event()
        self.welcome = None

    def connect(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while True:
            try:
                # Unbuffered: every write() is exactly one pipe message.
                self._pipe = open(self.pipe_name, "r+b", buffering=0)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        hello = {
            "type": "hello", "protocol": 1, "name": self.name, "version": "test",
            "capabilities": self.capabilities,
            "sticks": self.sticks,
        }
        if self.token:
            hello["token"] = self.token
        self.send(hello)
        reply = self.receive()
        if reply.get("type") != "welcome":
            raise ConnectionError(f"S2P rejected the client: {reply}")
        self.welcome = reply
        return reply

    def send(self, message):
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            self._pipe.write(data)

    def receive(self):
        return json.loads(self._pipe.read(4096).decode("utf-8"))

    def request(self, message):
        """Send a read request and return S2P's reply."""
        self.send(message)
        return self.receive()

    def state(self, buttons=(), **analog):
        message = {"type": "state", "buttons": list(buttons)}
        message.update({key: int(value) for key, value in analog.items()})
        self._last_state = message
        self.send(message)

    def release_all(self):
        self._last_state = None
        self.send({"type": "release_all"})

    def start_heartbeat(self, interval=HEARTBEAT_SECONDS):
        """Re-send the held state so S2P does not time it out."""
        def run():
            while not self._stop.wait(interval):
                message = self._last_state
                if message is not None:
                    try:
                        self.send(message)
                    except OSError:
                        return

        self._heartbeat = threading.Thread(target=run, daemon=True)
        self._heartbeat.start()

    def close(self):
        self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(1.0)
        if self._pipe is None:
            return
        try:
            self.send({"type": "bye"})
        except OSError:
            pass
        try:
            self._pipe.close()
        finally:
            self._pipe = None


def run_demo(client):
    steps = (
        ("A", ("A",), 1.5),
        ("A+B", ("A", "B"), 1.5),
        ("X", ("X",), 1.5),
        ("Y", ("Y",), 1.5),
        ("release", (), 0.5),
    )
    for label, buttons, seconds in steps:
        print(f"-> {label} ({seconds:.1f}s)")
        client.state(buttons)
        time.sleep(seconds)
    print("-> left stick right (1.5s, only while the physical stick is idle)")
    client.state((), lx=32767, ly=0)
    time.sleep(1.5)
    client.release_all()


def run_read(client, seconds):
    print(json.dumps(client.request({"type": "get_controller"})))
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        print(json.dumps(client.request({"type": "get_input"})))
        time.sleep(0.25)


def run_interactive(client):
    held = []
    analog = {}
    print("Type buttons (e.g. 'A B'), 'lt 255', 'lx 0 32767', 'release' or 'quit'.")
    for line in sys.stdin:
        words = line.strip().split()
        if not words:
            continue
        head = words[0].lower()
        if head == "quit":
            break
        if head == "release":
            held, analog = [], {}
            client.release_all()
            continue
        if head in ("lt", "rt") and len(words) == 2:
            analog[head] = int(words[1])
        elif head in ("lx", "rx") and len(words) == 3:
            axis = head[0]
            analog[f"{axis}x"] = int(words[1])
            analog[f"{axis}y"] = int(words[2])
        else:
            held = [word.upper() for word in words]
        client.state(held, **analog)
        print(f"   holding {held or '-'} {analog or ''}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "mode", choices=("demo", "hold", "read", "interactive"), nargs="?", default="demo",
    )
    parser.add_argument("buttons", nargs="*", default=["A"])
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--pipe", default=None)
    args = parser.parse_args(argv)

    capabilities = ("read",) if args.mode == "read" else ("input",)
    sticks = ("left", "right") if args.mode in ("demo", "interactive") else ()
    client = ExtensionClient(args.pipe, capabilities=capabilities, sticks=sticks)
    try:
        welcome = client.connect()
        print(f"Connected: {welcome}")
        if args.mode == "read":
            run_read(client, args.seconds)
            return 0
        client.start_heartbeat()
        if args.mode == "demo":
            run_demo(client)
        elif args.mode == "hold":
            client.state([button.upper() for button in args.buttons])
            time.sleep(args.seconds)
        else:
            run_interactive(client)
    except (OSError, ConnectionError, ValueError) as exc:
        print(f"Error: {exc}")
        if sys.stdin.isatty() and os.environ.get("S2P_EXTENSION_TOKEN"):
            input("Press Enter to close...")
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
