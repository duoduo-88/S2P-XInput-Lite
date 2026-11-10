# Aim viewport timing and benchmark

The aim viewport uses one Tk callback for independent render and simulation
deadlines. It reads the active mode of the monitor nearest the viewport, checks
again after entering fullscreen and moving the window, and also checks once per
second while open. The target render period is 1 / Hz. For 60, 120, 144, 180, 240,
360, 500, 540 and 600 Hz the periods are approximately 16.667, 8.333, 6.944, 5.556,
4.167, 2.778, 2.000, 1.852 and 1.667 ms respectively.

Below 240 Hz, the existing Windows 1 ms timer resolution request helps Tk wake
near the next deadline. The Tk fallback rounds against an absolute phase and
allows up to half a millisecond of early timer rounding. At 240 Hz and higher,
an active round uses a Windows high-resolution one-shot waitable timer when
available. The worker sleeps between deadlines and posts a wake-only Windows message. Tk's timer still runs the callback
on its own thread, which owns the simulation and the render target. The worker and its
handles are released on stop, hide or close. Both paths submit at most one
frame per wake and skip expired frame slots. Idle viewports only render
after a visual change, and a hidden aim tab cancels its callback and input
capture. A visible idle pane checks shortcuts at 30 Hz.

Gameplay advances on a 250 Hz timeline. The sampler retains observed distinct
Windows output states with monotonic timestamps in a separate bounded queue.
Aim consumes those transitions in order and integrates held stick input
between timestamps. Rendering does not advance scores. A long UI suspension
still interrupts a round, as it did previously. A Windows driver may coalesce
controller states between polls, so edges Windows never reports cannot be
reconstructed. The input Hz indicator uses source telemetry or a Raw HID report
counter when available. Repeated XInput or WinMM snapshots do not count as
transport reports.

The HUD reports successful Direct2D submissions or Tk update passes,
simulation steps, and measured source input rate where available. Frame times
are intervals between successful updates, with mean, P95, P99 and the fraction
strictly longer than the target period. Even normal 1 ms rounding can put
intervals just over the nominal budget. These figures describe application
work. Windows timers, DWM composition, GPU driver queues and monitor scanout
can all affect what ultimately appears on screen.

## Reproduce

Run on a visible Windows desktop with the bundled Python runtime. Keep other
GUI workloads idle and use the same display mode for both runs.

```powershell
.\runtime\python.exe -B .\scripts\benchmark_aim.py --source C:\path\to\base --seconds 5 --idle-seconds 2
.\runtime\python.exe -B .\scripts\benchmark_aim.py --source C:\path\to\candidate --seconds 5 --idle-seconds 2
```

The benchmark uses seeded 1280×720 tracking scenes, a deterministic 250 Hz
input trajectory, and the existing Windows renderer. CPU is process time
expressed as a percentage of one logical core.

## Paired local run

Windows desktop at 180 Hz with Direct2D, base commit `6da4521`.

| Scene | Submissions/s before → after | Mean frame ms | P95 ms | P99 ms | Over target | CPU, one core |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2D tracking | 180.03 → 179.98 | 5.555 → 5.556 | 6.785 → 6.459 | 7.070 → 6.598 | 75.97% → 23.80% | 81.26% → 35.62% |
| 3D tracking | 179.94 → 180.02 | 5.558 → 5.555 | 6.347 → 6.103 | 7.024 → 6.677 | 73.53% → 84.20% | 79.37% → 37.49% |
| 2D idle | 180.02 → 0 | — | — | — | — | 85.16% → 2.34% |
| 3D idle | 179.99 → 0 | — | — | — | — | 83.59% → 1.56% |

The 3D strict over-budget share rose even as P95 and P99 improved. Both
versions had zero intervals over 1.5× the target in this paired run. Automated
tests exercise deadline math through 600 Hz. Physical 240–600 Hz displays,
GPU utilization, compositor presentation and end-to-end display latency were
outside this measurement.

## 4K offscreen load test

The optional override is for benchmarking. It creates a 3840×2160
hardware-compatible Direct2D bitmap target and runs the same tracking
scenes without presenting the bitmap to the desktop compositor.

```powershell
.\runtime\python.exe -B .\scripts\benchmark_aim.py --size 3840x2160 --offscreen --target-hz 600 --seconds 4
```

The same 4K window test using HWND presentation and a 600 FPS override
submitted approximately 177 FPS in both scenes. The offscreen path isolates
application submission from that presentation limit. It does not measure GPU
completion or a physical 4K panel's refresh rate.

| Target FPS | 2D submissions/s | 2D P95 ms | 3D submissions/s | 3D P95 ms | CPU, one core (2D / 3D) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 360 | 359.59 | 3.91 | 358.40 | 4.21 | 40.32% / 47.19% |
| 500 | 489.43 | 3.08 | 492.38 | 3.24 | 49.07% / 57.80% |
| 540 | 527.69 | 2.96 | 527.43 | 3.21 | 65.32% / 76.57% |
| 600 | 587.20 | 2.92 | 562.45 | 3.18 | 67.82% / 79.36% |
| 750 | 721.45 | 2.03 | 659.88 | 2.08 | 90.63% / 91.56% |
| 1,000 | 724.05 | 1.71 | 677.03 | 1.92 | 92.19% / 92.49% |

Each scene was sampled for 5 seconds. Of the 4K modes tested, 360 FPS came
closest to its target for both scenes. At higher targets, submission
throughput continued to rise while long frames and CPU load increased.
