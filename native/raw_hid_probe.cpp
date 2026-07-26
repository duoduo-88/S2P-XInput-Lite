#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <hidsdi.h>
#include <hidpi.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr uint32_t kHistogramMaxUs = 100000;
constexpr size_t kDistributionBins = 80;
constexpr DWORD kReadWaitMs = 10;
constexpr DWORD kPublishIntervalMs = 100;

enum class ProbeState : uint32_t {
    Opening = 0,
    Running = 1,
    Complete = 2,
    Stopped = 3,
    Error = 4,
};

struct Measurement {
    explicit Measurement(uint64_t duration)
        : duration_ns(duration),
          histogram(kHistogramMaxUs + 1) {
        for (auto &value : histogram) value.store(0, std::memory_order_relaxed);
    }

    const uint64_t duration_ns;
    std::atomic<ProbeState> state{ProbeState::Opening};
    std::atomic<bool> stop_requested{false};
    std::atomic<uint32_t> error_code{0};
    std::atomic<uint64_t> started_ns{0};
    std::atomic<uint64_t> finished_ns{0};
    std::atomic<uint64_t> reports{0};
    std::atomic<uint64_t> intervals{0};
    std::atomic<uint64_t> sum_ns{0};
    std::atomic<uint32_t> min_ns{UINT32_MAX};
    std::atomic<uint32_t> max_ns{0};
    std::vector<std::atomic<uint32_t>> histogram;
};

uint64_t qpc_frequency() {
    LARGE_INTEGER value{};
    QueryPerformanceFrequency(&value);
    return static_cast<uint64_t>(value.QuadPart);
}

uint64_t qpc_now() {
    LARGE_INTEGER value{};
    QueryPerformanceCounter(&value);
    return static_cast<uint64_t>(value.QuadPart);
}

uint64_t ticks_to_ns(uint64_t ticks, uint64_t frequency) {
    const uint64_t whole = ticks / frequency;
    const uint64_t remainder = ticks % frequency;
    return whole * 1000000000ULL
        + remainder * 1000000000ULL / frequency;
}

void atomic_min(std::atomic<uint32_t> &target, uint32_t value) {
    uint32_t current = target.load(std::memory_order_relaxed);
    while (
        value < current
        && !target.compare_exchange_weak(
            current, value,
            std::memory_order_relaxed,
            std::memory_order_relaxed
        )
    ) {}
}

void atomic_max(std::atomic<uint32_t> &target, uint32_t value) {
    uint32_t current = target.load(std::memory_order_relaxed);
    while (
        value > current
        && !target.compare_exchange_weak(
            current, value,
            std::memory_order_relaxed,
            std::memory_order_relaxed
        )
    ) {}
}

void record_interval(
    Measurement &measurement,
    uint64_t interval_ns
) {
    const uint32_t bounded_ns = static_cast<uint32_t>(
        std::min<uint64_t>(interval_ns, UINT32_MAX)
    );
    measurement.intervals.fetch_add(1, std::memory_order_relaxed);
    measurement.sum_ns.fetch_add(interval_ns, std::memory_order_relaxed);
    atomic_min(measurement.min_ns, bounded_ns);
    atomic_max(measurement.max_ns, bounded_ns);

    const uint32_t interval_us = static_cast<uint32_t>(
        std::min<uint64_t>(
            interval_ns / 1000ULL,
            static_cast<uint64_t>(kHistogramMaxUs)
        )
    );
    measurement.histogram[interval_us].fetch_add(
        1, std::memory_order_relaxed
    );
}

std::string state_name(ProbeState state) {
    switch (state) {
    case ProbeState::Opening: return "opening";
    case ProbeState::Running: return "running";
    case ProbeState::Complete: return "complete";
    case ProbeState::Stopped: return "stopped";
    case ProbeState::Error: return "error";
    }
    return "error";
}

struct HistogramAnalysis {
    uint32_t p50_us;
    uint32_t p95_us;
    uint32_t p99_us;
    uint32_t distribution_maximum_us;
    std::vector<uint64_t> counts;
};

HistogramAnalysis analyze_histogram(const Measurement &measurement) {
    // Copy each atomic bucket exactly once. Percentiles and display bins are
    // then derived from the local snapshot without repeatedly contending with
    // the high-priority HID reader.
    std::vector<uint32_t> histogram(kHistogramMaxUs + 1);
    uint64_t sample_count = 0;
    for (uint32_t index = 0; index <= kHistogramMaxUs; ++index) {
        const uint32_t count = measurement.histogram[index].load(
            std::memory_order_relaxed
        );
        histogram[index] = count;
        sample_count += count;
    }
    if (sample_count == 0) {
        return {0, 0, 0, 50, std::vector<uint64_t>(
            kDistributionBins, 0
        )};
    }
    const uint64_t p50_target = (sample_count * 50 + 99) / 100;
    const uint64_t p95_target = (sample_count * 95 + 99) / 100;
    const uint64_t p99_target = (sample_count * 99 + 99) / 100;
    uint32_t p50_us = kHistogramMaxUs;
    uint32_t p95_us = kHistogramMaxUs;
    uint32_t p99_us = kHistogramMaxUs;
    bool found_p50 = false;
    bool found_p95 = false;
    uint64_t cumulative = 0;
    for (uint32_t index = 0; index <= kHistogramMaxUs; ++index) {
        cumulative += histogram[index];
        if (!found_p50 && cumulative >= p50_target) {
            p50_us = index;
            found_p50 = true;
        }
        if (!found_p95 && cumulative >= p95_target) {
            p95_us = index;
            found_p95 = true;
        }
        if (cumulative >= p99_target) {
            p99_us = index;
            break;
        }
    }
    // Keep the visible range focused on normal delivery jitter. Values above
    // it remain in the final bin instead of disappearing from the chart.
    const uint32_t maximum_us = std::min<uint32_t>(
        kHistogramMaxUs,
        std::max<uint32_t>(50, ((p99_us * 6 + 4) / 5 + 24) / 25 * 25)
    );
    std::vector<uint64_t> counts(kDistributionBins, 0);
    for (uint32_t interval_us = 0;
         interval_us <= kHistogramMaxUs;
         ++interval_us) {
        const uint64_t count = histogram[interval_us];
        if (count == 0) continue;
        const size_t index = std::min<size_t>(
            kDistributionBins - 1,
            static_cast<uint64_t>(interval_us) * kDistributionBins
                / std::max<uint32_t>(1, maximum_us)
        );
        counts[index] += count;
    }
    return {
        p50_us, p95_us, p99_us, maximum_us, std::move(counts)
    };
}

void publish_snapshot(
    const Measurement &measurement,
    uint64_t now_ns,
    double rate_hz
) {
    const ProbeState state = measurement.state.load(std::memory_order_acquire);
    const uint64_t started = measurement.started_ns.load(
        std::memory_order_relaxed
    );
    const uint64_t finished = measurement.finished_ns.load(
        std::memory_order_relaxed
    );
    const uint64_t effective_now = finished != 0 ? finished : now_ns;
    const uint64_t elapsed_ns = started == 0 || effective_now <= started
        ? 0 : std::min(measurement.duration_ns, effective_now - started);
    const uint64_t interval_count = measurement.intervals.load(
        std::memory_order_acquire
    );
    const uint64_t sum_ns = measurement.sum_ns.load(std::memory_order_relaxed);
    const uint32_t minimum = measurement.min_ns.load(std::memory_order_relaxed);
    const uint32_t maximum = measurement.max_ns.load(std::memory_order_relaxed);
    const HistogramAnalysis analysis = analyze_histogram(measurement);

    std::ostringstream output;
    output << std::fixed << std::setprecision(3);
    output
        << "{\"type\":\"snapshot\",\"state\":\"" << state_name(state)
        << "\",\"error_code\":"
        << measurement.error_code.load(std::memory_order_relaxed)
        << ",\"elapsed_ms\":" << (elapsed_ns / 1000000.0)
        << ",\"remaining_ms\":"
        << ((measurement.duration_ns - elapsed_ns) / 1000000.0)
        << ",\"reports\":"
        << measurement.reports.load(std::memory_order_relaxed)
        << ",\"intervals\":" << interval_count
        << ",\"rate_hz\":" << std::max(0.0, rate_hz)
        << ",\"p50_us\":" << analysis.p50_us
        << ",\"p95_us\":" << analysis.p95_us
        << ",\"p99_us\":" << analysis.p99_us
        << ",\"min_us\":"
        << (minimum == UINT32_MAX ? 0.0 : minimum / 1000.0)
        << ",\"mean_us\":"
        << (interval_count == 0
            ? 0.0
            : static_cast<double>(sum_ns)
                / static_cast<double>(interval_count) / 1000.0)
        << ",\"max_us\":" << (maximum / 1000.0)
        << ",\"histogram_max_us\":" << analysis.distribution_maximum_us
        << ",\"histogram_counts\":[";
    for (size_t index = 0; index < analysis.counts.size(); ++index) {
        if (index) output << ',';
        output << analysis.counts[index];
    }
    output << "]}";
    std::cout << output.str() << '\n' << std::flush;
}

bool read_stop_command() {
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (input == nullptr || input == INVALID_HANDLE_VALUE) return false;
    DWORD available = 0;
    if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) {
        return false;
    }
    if (available == 0) return false;
    char buffer[64]{};
    DWORD read = 0;
    if (!ReadFile(
        input, buffer,
        static_cast<DWORD>(std::min<size_t>(available, sizeof(buffer) - 1)),
        &read, nullptr
    )) {
        return false;
    }
    return std::string(buffer, buffer + read).find("stop") != std::string::npos;
}

void read_device(
    const std::wstring path,
    Measurement &measurement,
    uint64_t frequency
) {
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    HANDLE device = CreateFileW(
        path.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr,
        OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED,
        nullptr
    );
    if (device == INVALID_HANDLE_VALUE) {
        measurement.error_code.store(GetLastError(), std::memory_order_relaxed);
        measurement.state.store(ProbeState::Error, std::memory_order_release);
        return;
    }

    PHIDP_PREPARSED_DATA preparsed = nullptr;
    HIDP_CAPS caps{};
    if (
        !HidD_GetPreparsedData(device, &preparsed)
        || HidP_GetCaps(preparsed, &caps) != HIDP_STATUS_SUCCESS
        || caps.InputReportByteLength == 0
    ) {
        measurement.error_code.store(GetLastError(), std::memory_order_relaxed);
        if (preparsed) HidD_FreePreparsedData(preparsed);
        CloseHandle(device);
        measurement.state.store(ProbeState::Error, std::memory_order_release);
        return;
    }
    HidD_FreePreparsedData(preparsed);
    // The Windows HID class driver defaults to a small input-report ring.
    // A larger ring prevents a brief scheduler stall from discarding reports
    // before this high-priority reader can resume, especially at 8000 Hz.
    HidD_SetNumInputBuffers(device, 512);

    std::vector<uint8_t> report(caps.InputReportByteLength);
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (event == nullptr) {
        measurement.error_code.store(GetLastError(), std::memory_order_relaxed);
        CloseHandle(device);
        measurement.state.store(ProbeState::Error, std::memory_order_release);
        return;
    }

    const uint64_t started_ticks = qpc_now();
    const uint64_t started_ns = ticks_to_ns(started_ticks, frequency);
    measurement.started_ns.store(started_ns, std::memory_order_relaxed);
    measurement.state.store(ProbeState::Running, std::memory_order_release);
    uint64_t previous_ticks = 0;

    while (!measurement.stop_requested.load(std::memory_order_relaxed)) {
        const uint64_t before_read_ticks = qpc_now();
        const uint64_t before_elapsed_ns = ticks_to_ns(
            before_read_ticks - started_ticks, frequency
        );
        if (before_elapsed_ns >= measurement.duration_ns) break;

        OVERLAPPED overlapped{};
        overlapped.hEvent = event;
        ResetEvent(event);
        DWORD bytes_read = 0;
        BOOL completed = ReadFile(
            device,
            report.data(),
            static_cast<DWORD>(report.size()),
            &bytes_read,
            &overlapped
        );
        if (!completed) {
            const DWORD error = GetLastError();
            if (error != ERROR_IO_PENDING) {
                measurement.error_code.store(error, std::memory_order_relaxed);
                measurement.state.store(
                    ProbeState::Error, std::memory_order_release
                );
                break;
            }
            while (true) {
                const DWORD wait = WaitForSingleObject(event, kReadWaitMs);
                if (wait == WAIT_OBJECT_0) {
                    completed = GetOverlappedResult(
                        device, &overlapped, &bytes_read, FALSE
                    );
                    break;
                }
                if (wait != WAIT_TIMEOUT) {
                    measurement.error_code.store(
                        GetLastError(), std::memory_order_relaxed
                    );
                    measurement.state.store(
                        ProbeState::Error, std::memory_order_release
                    );
                    completed = FALSE;
                    break;
                }
                const uint64_t waited_ticks = qpc_now();
                const bool expired = ticks_to_ns(
                    waited_ticks - started_ticks, frequency
                ) >= measurement.duration_ns;
                if (
                    expired
                    || measurement.stop_requested.load(
                        std::memory_order_relaxed
                    )
                ) {
                    CancelIoEx(device, &overlapped);
                    WaitForSingleObject(event, INFINITE);
                    GetOverlappedResult(
                        device, &overlapped, &bytes_read, FALSE
                    );
                    completed = FALSE;
                    break;
                }
            }
        }
        if (!completed) {
            if (
                measurement.state.load(std::memory_order_relaxed)
                == ProbeState::Error
            ) break;
            continue;
        }
        if (bytes_read == 0) continue;

        const uint64_t arrived_ticks = qpc_now();
        measurement.reports.fetch_add(1, std::memory_order_relaxed);
        if (previous_ticks != 0) {
            record_interval(
                measurement,
                ticks_to_ns(arrived_ticks - previous_ticks, frequency)
            );
        }
        previous_ticks = arrived_ticks;
    }

    CancelIoEx(device, nullptr);
    CloseHandle(event);
    CloseHandle(device);
    measurement.finished_ns.store(
        ticks_to_ns(qpc_now(), frequency), std::memory_order_relaxed
    );
    if (
        measurement.state.load(std::memory_order_relaxed)
        != ProbeState::Error
    ) {
        measurement.state.store(
            measurement.stop_requested.load(std::memory_order_relaxed)
                ? ProbeState::Stopped : ProbeState::Complete,
            std::memory_order_release
        );
    }
}

int run_measurement(const std::wstring &path, uint64_t duration_ms) {
    const uint64_t frequency = qpc_frequency();
    Measurement measurement(duration_ms * 1000000ULL);
    HANDLE thread = CreateThread(
        nullptr, 0,
        [](LPVOID context) -> DWORD {
            auto *arguments = static_cast<
                std::pair<std::wstring, Measurement *> *
            >(context);
            read_device(
                arguments->first, *arguments->second, qpc_frequency()
            );
            delete arguments;
            return 0;
        },
        new std::pair<std::wstring, Measurement *>(path, &measurement),
        0, nullptr
    );
    if (thread == nullptr) return 2;

    struct RatePoint { uint64_t time_ns; uint64_t reports; };
    std::vector<RatePoint> rate_points;
    rate_points.reserve(16);
    while (true) {
        if (read_stop_command()) {
            measurement.stop_requested.store(true, std::memory_order_relaxed);
        }
        const uint64_t now_ns = ticks_to_ns(qpc_now(), frequency);
        const uint64_t reports = measurement.reports.load(
            std::memory_order_relaxed
        );
        rate_points.push_back({now_ns, reports});
        while (
            rate_points.size() > 2
            && now_ns - rate_points.front().time_ns > 1000000000ULL
        ) {
            rate_points.erase(rate_points.begin());
        }
        double rate_hz = 0.0;
        if (rate_points.size() >= 2) {
            const auto &first = rate_points.front();
            const auto &last = rate_points.back();
            const uint64_t span = last.time_ns - first.time_ns;
            if (span > 0) {
                rate_hz = static_cast<double>(last.reports - first.reports)
                    * 1000000000.0 / static_cast<double>(span);
            }
        }
        publish_snapshot(measurement, now_ns, rate_hz);
        const ProbeState state = measurement.state.load(
            std::memory_order_acquire
        );
        if (
            state == ProbeState::Complete
            || state == ProbeState::Stopped
            || state == ProbeState::Error
        ) break;
        Sleep(kPublishIntervalMs);
    }
    WaitForSingleObject(thread, INFINITE);
    CloseHandle(thread);
    return measurement.state.load(std::memory_order_relaxed)
        == ProbeState::Error ? 1 : 0;
}

int run_self_test(uint32_t rate_hz, uint64_t duration_ms) {
    if (rate_hz == 0 || duration_ms == 0) return 2;
    Measurement measurement(duration_ms * 1000000ULL);
    measurement.state.store(ProbeState::Running, std::memory_order_relaxed);
    measurement.started_ns.store(1, std::memory_order_relaxed);
    const uint64_t interval_ns = 1000000000ULL / rate_hz;
    const uint64_t interval_count = rate_hz * duration_ms / 1000ULL;
    measurement.reports.store(interval_count + 1, std::memory_order_relaxed);
    for (uint64_t index = 1; index <= interval_count; ++index) {
        record_interval(
            measurement,
            interval_ns
        );
    }
    measurement.finished_ns.store(
        measurement.started_ns.load(std::memory_order_relaxed)
            + measurement.duration_ns,
        std::memory_order_relaxed
    );
    measurement.state.store(ProbeState::Complete, std::memory_order_relaxed);
    publish_snapshot(
        measurement,
        measurement.finished_ns.load(std::memory_order_relaxed),
        static_cast<double>(rate_hz)
    );
    return 0;
}

}  // namespace

int wmain(int argc, wchar_t **argv) {
    if (argc == 5 && std::wstring(argv[1]) == L"--measure"
        && std::wstring(argv[3]) == L"--duration-ms") {
        const uint64_t duration_ms = std::clamp<uint64_t>(
            std::wcstoull(argv[4], nullptr, 10), 1000, 300000
        );
        return run_measurement(argv[2], duration_ms);
    }
    if (argc == 5 && std::wstring(argv[1]) == L"--self-test"
        && std::wstring(argv[3]) == L"--duration-ms") {
        const uint32_t rate_hz = static_cast<uint32_t>(
            std::wcstoul(argv[2], nullptr, 10)
        );
        const uint64_t duration_ms = std::wcstoull(argv[4], nullptr, 10);
        return run_self_test(rate_hz, duration_ms);
    }
    std::cerr
        << "usage: raw_hid_probe --measure <device-path> "
           "--duration-ms <1000..300000>\n";
    return 2;
}
