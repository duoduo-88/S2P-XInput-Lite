"""Exercise standalone profile writes while measuring BLE callback latency.

Example:
    runtime\\python.exe tests\\live_standalone_stress.py --port COM5
"""
from __future__ import annotations

import argparse
import json
import time
import zlib

import serial


def send_and_expect(port, command, expected, timeout=4.0):
    port.write((command + "\n").encode("ascii"))
    port.flush()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = port.readline()
        if not raw:
            continue
        try:
            message = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if message.get("cmd") == expected:
            if not message.get("ok"):
                raise RuntimeError(f"{expected} failed: {message}")
            return message
    raise TimeoutError(f"timed out waiting for {expected}")


def write_profile(port, idle_minutes):
    payload = json.dumps(
        {
            "schema": 1,
            "idle_disconnect_minutes": idle_minutes,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    send_and_expect(
        port, f"profile begin 1 {len(payload)} {crc32:08x}",
        "profile_begin",
    )
    send_and_expect(
        port, f"profile chunk 0 {payload.hex()}", "profile_chunk"
    )
    return send_and_expect(port, "profile commit", "profile_commit")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baudrate", type=int, default=2_000_000)
    parser.add_argument("--writes", type=int, default=30)
    parser.add_argument("--max-us", type=int, default=2000)
    parser.add_argument("--p95-us", type=int, default=500)
    parser.add_argument("--p99-us", type=int, default=1000)
    args = parser.parse_args()

    with serial.Serial(
        args.port,
        args.baudrate,
        timeout=0.1,
        write_timeout=0.5,
    ) as port:
        port.reset_input_buffer()
        send_and_expect(
            port, "ble timing reset", "ble_timing_reset"
        )
        for index in range(args.writes):
            write_profile(port, 15 if index % 2 == 0 else 30)
        timing = send_and_expect(port, "ble timing", "ble_timing")

    print(json.dumps(timing, indent=2))
    if timing["samples"] < 32:
        raise SystemExit(
            "Not enough BLE callbacks were observed. Keep the controller "
            "awake and moving during the test."
        )
    limits = (
        ("max_us", args.max_us),
        ("p95_us", args.p95_us),
        ("p99_us", args.p99_us),
    )
    failures = [
        f"{name}={timing[name]} exceeds {limit}"
        for name, limit in limits
        if timing[name] > limit
    ]
    if failures:
        raise SystemExit("; ".join(failures))
    print(
        f"PASS: {args.writes} commits with active input; "
        f"P95={timing['p95_us']}us P99={timing['p99_us']}us "
        f"max={timing['max_us']}us"
    )


if __name__ == "__main__":
    main()
