"""Interactive, conservative LF/HF command sweep for real controllers."""

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bluetooth_controller import BluetoothController
from esp32_bridge import ESP32Bridge
from rumble_protocol import CONNECTION_HF_FREQUENCY, CONNECTION_LF_FREQUENCY
from wired_controller import WiredController, find_wired_controller


DEFAULT_VALUES = {
    "lf": (190, 205, 215, 225),
    "hf": (315, 325, 330, 340, 350),
}
SAFE_MAX_AMPLITUDE = 550
MAX_PULSE_SECONDS = 0.5
MIN_REST_SECONDS = 0.5


def parse_values(text, channel):
    if text is None:
        return DEFAULT_VALUES[channel]
    values = tuple(int(item.strip()) for item in text.split(",") if item.strip())
    if not values or any(value < 0 or value > 511 for value in values):
        raise ValueError("frequency values must be between 0 and 511")
    return values


def create_transport(mode, port):
    if mode == "bluetooth":
        return BluetoothController()
    if mode == "wired":
        entry = find_wired_controller()
        if entry is None:
            raise RuntimeError("No wired Switch 2 controller was detected")
        return WiredController(entry)
    return ESP32Bridge(port)


def open_transport(transport, mode, connected):
    transport.input_callback = lambda _payload: None
    transport.connected_callback = connected.set
    if mode == "esp32":
        if transport.open() is False:
            raise RuntimeError(f"Could not open ESP32 bridge on {transport.port}")
        transport.start_scan()
    else:
        transport.open()
    if not connected.wait(30.0):
        raise RuntimeError(f"{mode} controller did not connect within 30 seconds")


def send_rumble(transport, mode, lf_freq, lf_amp, hf_freq, hf_amp, stop=False):
    if mode == "esp32":
        return transport.send_pro_rumble_latest(
            lf_freq,
            lf_amp,
            hf_freq,
            hf_amp,
            priority=True,
            force_zero=stop,
        )
    if mode == "wired":
        return transport.send_pro_rumble(
            lf_freq,
            lf_amp,
            hf_freq,
            hf_amp,
            priority=True,
            force_zero=stop,
        )
    return transport.send_pro_rumble(lf_freq, lf_amp, hf_freq, hf_amp)


def stop_rumble(transport, mode):
    send_rumble(
        transport,
        mode,
        CONNECTION_LF_FREQUENCY,
        0,
        CONNECTION_HF_FREQUENCY,
        0,
        stop=True,
    )


def save_results(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run(args):
    values = parse_values(args.values, args.channel)
    if not 1 <= args.amplitude <= SAFE_MAX_AMPLITUDE:
        raise ValueError(f"amplitude must be between 1 and {SAFE_MAX_AMPLITUDE}")
    if not 0.05 <= args.duration <= MAX_PULSE_SECONDS:
        raise ValueError(f"duration must be between 0.05 and {MAX_PULSE_SECONDS}")
    if args.rest < MIN_REST_SECONDS:
        raise ValueError(f"rest must be at least {MIN_REST_SECONDS} seconds")

    print("Switch 2 Pro Controller conservative rumble sweep")
    print(
        "Frequency entries are raw 9-bit command values stored in 10-bit "
        "protocol slots, not certified Hz values."
    )
    print("Only one LF/HF component is active at a time.")
    print("Press Ctrl+C at any time to send a stop frame and exit.")
    print()
    answer = input(
        f"Continue with {args.channel.upper()} values {values} at amplitude "
        f"{args.amplitude}? [y/N] "
    ).strip().lower()
    if answer not in ("y", "yes"):
        print("Cancelled.")
        return 0

    connected = threading.Event()
    disconnected = threading.Event()
    transport = create_transport(args.mode, args.port)
    transport.disconnected_callback = disconnected.set
    results = []
    try:
        open_transport(transport, args.mode, connected)
        time.sleep(1.0)
        stop_rumble(transport, args.mode)
        time.sleep(0.1)

        for value in values:
            if disconnected.is_set():
                raise RuntimeError("Controller disconnected during sweep")
            action = input(
                f"{args.channel.upper()} command {value}: "
                "Enter=play, s=skip, q=finish > "
            ).strip().lower()
            if action == "q":
                break
            if action == "s":
                continue

            if args.channel == "lf":
                state = (value, args.amplitude, CONNECTION_HF_FREQUENCY, 0)
            else:
                state = (CONNECTION_LF_FREQUENCY, 0, value, args.amplitude)

            accepted = send_rumble(transport, args.mode, *state)
            if accepted is False:
                raise RuntimeError("Transport rejected the rumble command")
            time.sleep(args.duration)
            stop_rumble(transport, args.mode)
            time.sleep(args.rest)
            note = input("Optional note (feel/noise), then Enter: ").strip()
            results.append({
                "command": value,
                "amplitude": args.amplitude,
                "duration_seconds": args.duration,
                "note": note,
            })
    finally:
        try:
            stop_rumble(transport, args.mode)
            time.sleep(0.05)
        except Exception:
            pass
        transport.input_callback = None
        transport.close()

    payload = {
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": args.mode,
        "channel": args.channel,
        "results": results,
    }
    if args.output:
        save_results(args.output, payload)
        print(f"Saved results to {Path(args.output).resolve()}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("wired", "bluetooth", "esp32"), required=True)
    parser.add_argument("--channel", choices=("lf", "hf"), required=True)
    parser.add_argument("--port", default="COM3", help="ESP32 serial port")
    parser.add_argument("--values", help="comma-separated raw command values")
    parser.add_argument("--amplitude", type=int, default=300)
    parser.add_argument("--duration", type=float, default=0.3)
    parser.add_argument("--rest", type=float, default=0.7)
    parser.add_argument("--output", help="optional JSON results path")
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
