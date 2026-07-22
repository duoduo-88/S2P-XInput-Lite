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
- Windows taskbar minimize/restore repaint behavior;
- source compilation and module-boundary checks.

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

