# Tests

## Automated regression tests

Run the complete suite from the project root with the bundled Python runtime:

```powershell
& ".\runtime\python.exe" ".\tests\run_tests.py"
```

The automated suite covers:

- settings schema validation and bundled profile round-trips;
- GUI settings snapshots and GUI-section structure;
- stick and gyro processing helpers;
- mapping-layer HOLD/TOGGLE priority and precompiled runtimes;
- keyboard modifiers, mouse-button source counting, mouse movement, and wheel integration;
- input-dispatch edge preservation and callback serialization;
- Windows taskbar and notification-area minimize/restore behavior;
- global single-instance activation for Settings and Gamepad Tester;
- source compilation and module-boundary checks;
- transport shutdown ordering and blocking standalone writes;
- diagnostic-reader cancellation, worker-generation cleanup, and serial-port
  release during an in-progress response;
- Raw HID collection parsing, shared-memory publication, and report-rate
  analysis;
- Traditional Chinese/English key coverage, formatting placeholders, and
  compact English labels used by constrained GUI rows;
- GitHub release parsing, semantic version comparison, automatic-check
  preferences, and per-version notification suppression;
- standalone A/B fallback, commit ordering, schema validation, and callback
  fast-path contracts;
- multi-controller calibration selection.

The v0.7.5 release baseline contains 537 automated tests. One optional full
ESP-IDF rebuild test is skipped unless `S2P_RUN_IDF_BUILD=1` is set.

Desktop-output tests use a fake Windows backend. They do not connect to a
controller, create a ViGEm target, or send real keyboard or mouse input.

## Live hardware probes

Files beginning with `live_` and `run_live_` are manual hardware diagnostics.
They are not included in the automated regression suite and require the
corresponding controller transport.

For a conservative rumble sweep, run `tests\run_live_rumble_sweep.bat`, select
the transport (`wired`, `bluetooth`, or `esp32`), and then select one channel
(`lf` or `hf`). The tool activates one frequency component at a time, defaults
to amplitude 300, limits amplitude to 550, keeps each pulse at or below 0.5
seconds, and always sends a stop frame when interrupted. Frequency entries are
raw 9-bit command values stored in 10-bit protocol slots, not certified Hz
values.

For standalone write/input race coverage and BLE callback P95/P99 latency,
keep a paired controller awake and moving, then run:

```powershell
& ".\runtime\python.exe" ".\tests\live_standalone_stress.py" --port COM5
```

The probe performs repeated A/B commits while input notifications continue,
then fails if too few callbacks were observed or the configurable max/P95/P99
limits are exceeded.

Hardware release acceptance still requires three manual checks:

- Windows standalone mode appears as XInput and receives buttons, axes, and
  bidirectional rumble;
- Android standalone HID mode receives buttons, axes, triggers, and hat input;
- wired, Bluetooth, and ESP32 transports pass `run_live_rumble_sweep.bat`,
  including a zero-amplitude stop after interruption.
