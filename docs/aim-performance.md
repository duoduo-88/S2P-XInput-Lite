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
available. The timer worker sleeps between deadlines and posts a wake-only
Windows message. Tk runs the callback and owns the simulation. A separate
render worker owns the Direct2D target. The timer worker and its handles are
released on stop, hide or close. Both scheduler paths request at most one
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

## Background rendering

The Direct2D worker draws the latest scene snapshot and timestamps each
successful submission. If it falls behind, it replaces the pending scene and
continues with current data. Input, simulation and scoring stay on their
existing timelines. The worker also projects the 3D grid through the native
helper when available. Tk remains the fallback if Direct2D fails.

The stick monitor has a separate native raster worker shared by both plots.
It consumes original samples in sequence, applies the display settings and
returns completed pixel rows to Tk. When pending work overflows, it rebuilds
from the retained history. The existing raster path works without the helper.

### Paired Windows run

The baseline is `7af1c08`. The Windows monitor was at 180 Hz. CPU figures
represent process time as a percentage of one logical core.

| Aim scene | Direct2D submissions/s before → after | P99 ms | CPU, one core |
| --- | ---: | ---: | ---: |
| 720p window, 2D | 180.03 → 180.01 | 7.89 → 6.88 | 37.50% → 32.43% |
| 720p window, 3D | 180.00 → 180.03 | 8.29 → 6.72 | 39.06% → 26.17% |
| 4K offscreen at 500 Hz, 2D | 491.40 → 499.38 | 3.73 → 2.78 | 53.75% → 63.76% |
| 4K offscreen at 500 Hz, 3D | 489.55 → 499.71 | 4.11 → 2.76 | 73.74% → 56.56% |

At a 600 Hz target, the 4K offscreen worker submitted 599.05 frames/s in 2D
and 598.30 in 3D. P99 was 2.76 and 2.73 ms respectively. The 500 Hz 2D
worker reduced long gaps at the cost of higher CPU time in this run. The
offscreen tests measured successful Direct2D submissions. Physical 4K
presentation, GPU utilization and input-to-display latency were unmeasured.

The worker was also tested above the supported display targets, with five
seconds per scene. The CPU column is process time relative to one logical
core and can exceed 100% when threads run concurrently.

| 4K stress target | 2D submissions/s | 3D submissions/s | P99 ms (2D / 3D) | CPU, one core (2D / 3D) |
| ---: | ---: | ---: | ---: | ---: |
| 750 | 742.92 | 741.94 | 2.84 / 2.90 | 87.52% / 94.69% |
| 1,000 | 921.71 | 924.00 | 1.93 / 1.93 | 109.39% / 116.88% |

These stress targets use the benchmark override. Normal runs follow the
current display mode.

The trail benchmark streamed approximately 10,000 synthetic samples/s for
8.6 seconds, with five seconds of history per plot. The optimized run held
50,085 samples per plot at its peak and received 86,090 sequential samples
with zero gaps and zero ring drops. The left and right plot update rates
rose from 159.98/152.96 to 176.95/173.93 per second. P95 update gaps fell
from 10.90/12.15 to 7.85/8.27 ms, and CPU time fell from 46.51% to 44.69%
of one core.

Build the native helper in the candidate checkout, then use the same
benchmark and display mode for the baseline and candidate runs.

```powershell
.\native\build_gamepad_render_native.ps1
.\runtime\python.exe -B .\scripts\benchmark_aim.py --source C:\path\to\base --seconds 4
.\runtime\python.exe -B .\scripts\benchmark_aim.py --render-worker --seconds 4
.\runtime\python.exe -B .\scripts\benchmark_aim.py --render-worker --size 3840x2160 --offscreen --target-hz 600
.\runtime\python.exe -B .\scripts\benchmark_gamepad_trails.py --source C:\path\to\base --seconds 8.6
.\runtime\python.exe -B .\scripts\benchmark_gamepad_trails.py --seconds 8.6
```
