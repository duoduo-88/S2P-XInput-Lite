#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <hidsdi.h>
#include <hidpi.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cwctype>
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
constexpr uint32_t kStreamMagic = 0x53524853;  // "SRHS"
constexpr uint32_t kStreamVersion = 1;

#pragma pack(push, 1)
struct StreamHeader {
    uint32_t magic;
    uint32_t version;
    uint32_t header_size;
    uint32_t slot_size;
    uint32_t capacity;
    volatile LONG state;
    volatile LONG error_code;
    uint32_t axes_mask;
    volatile LONG64 latest_sequence;
    volatile LONG64 reports;
    uint64_t qpc_frequency;
    uint64_t reserved;
};

struct StreamSlot {
    volatile LONG64 sequence;
    uint64_t timestamp_ticks;
    float left_x;
    float left_y;
    float right_x;
    float right_y;
    float left_trigger;
    float right_trigger;
    uint32_t buttons;
    uint32_t reserved;
};
#pragma pack(pop)

static_assert(sizeof(StreamHeader) == 64, "stream header layout changed");
static_assert(sizeof(StreamSlot) == 48, "stream slot layout changed");

enum StreamAxis : uint32_t {
    StreamLeftX = 1 << 0,
    StreamLeftY = 1 << 1,
    StreamRightX = 1 << 2,
    StreamRightY = 1 << 3,
};

struct AxisDescriptor {
    bool valid = false;
    USAGE usage = 0;
    UCHAR report_id = 0;
    USHORT link_collection = 0;
    USHORT data_index = 0;
    LONG logical_minimum = 0;
    LONG logical_maximum = 0;
};

struct StreamParser {
    PHIDP_PREPARSED_DATA preparsed = nullptr;
    AxisDescriptor left_x;
    AxisDescriptor left_y;
    AxisDescriptor right_x;
    AxisDescriptor right_y;
    USHORT input_data_indices = 0;
    uint32_t axes_mask = 0;
};

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
        const DWORD error = GetLastError();
        // The stream helper must not survive its owning tester process. An
        // orphan would keep the HID collection open and compete with the next
        // finite report-rate measurement.
        return error == ERROR_BROKEN_PIPE
            || error == ERROR_PIPE_NOT_CONNECTED;
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

AxisDescriptor find_axis(
    const std::vector<HIDP_VALUE_CAPS> &caps,
    USAGE usage,
    int report_id = -1
) {
    for (const auto &cap : caps) {
        if (cap.UsagePage != 0x01) continue;
        if (report_id >= 0 && cap.ReportID != report_id) continue;
        const USAGE first = cap.IsRange ? cap.Range.UsageMin
                                        : cap.NotRange.Usage;
        const USAGE last = cap.IsRange ? cap.Range.UsageMax : first;
        if (usage < first || usage > last) continue;
        return {
            true,
            usage,
            cap.ReportID,
            cap.LinkCollection,
            static_cast<USHORT>(
                cap.IsRange
                    ? cap.Range.DataIndexMin
                        + (usage - cap.Range.UsageMin)
                    : cap.NotRange.DataIndex
            ),
            cap.LogicalMin,
            cap.LogicalMax,
        };
    }
    return {};
}

bool initialize_stream_parser(
    HANDLE device,
    StreamParser &parser,
    HIDP_CAPS &device_caps
) {
    if (!HidD_GetPreparsedData(device, &parser.preparsed)) return false;
    if (
        HidP_GetCaps(parser.preparsed, &device_caps) != HIDP_STATUS_SUCCESS
        || device_caps.InputReportByteLength == 0
    ) {
        HidD_FreePreparsedData(parser.preparsed);
        parser.preparsed = nullptr;
        return false;
    }
    USHORT count = device_caps.NumberInputValueCaps;
    std::vector<HIDP_VALUE_CAPS> value_caps(count);
    if (
        count != 0
        && HidP_GetValueCaps(
            HidP_Input, value_caps.data(), &count, parser.preparsed
        ) != HIDP_STATUS_SUCCESS
    ) {
        HidD_FreePreparsedData(parser.preparsed);
        parser.preparsed = nullptr;
        return false;
    }
    value_caps.resize(count);
    parser.input_data_indices = device_caps.NumberInputDataIndices;
    std::vector<UCHAR> report_ids;
    for (const auto &cap : value_caps) {
        if (
            cap.UsagePage == 0x01
            && std::find(
                report_ids.begin(), report_ids.end(), cap.ReportID
            ) == report_ids.end()
        ) {
            report_ids.push_back(cap.ReportID);
        }
    }
    // Usages can be repeated in several report IDs on composite gamepads.
    // Select a complete stick set from one report instead of independently
    // taking the first descriptor for each usage.
    for (const UCHAR report_id : report_ids) {
        AxisDescriptor left_x = find_axis(
            value_caps, 0x30, report_id
        );
        AxisDescriptor left_y = find_axis(
            value_caps, 0x31, report_id
        );
        AxisDescriptor right_x = find_axis(
            value_caps, 0x33, report_id
        );
        AxisDescriptor right_y = find_axis(
            value_caps, 0x34, report_id
        );
        if (!right_x.valid || !right_y.valid) {
            right_x = find_axis(value_caps, 0x32, report_id);
            right_y = find_axis(value_caps, 0x35, report_id);
        }
        if (
            left_x.valid && left_y.valid
            && right_x.valid && right_y.valid
        ) {
            parser.left_x = left_x;
            parser.left_y = left_y;
            parser.right_x = right_x;
            parser.right_y = right_y;
            break;
        }
    }
    if (!parser.left_x.valid) {
        parser.left_x = find_axis(value_caps, 0x30);
        parser.left_y = find_axis(value_caps, 0x31);
        parser.right_x = find_axis(value_caps, 0x33);
        parser.right_y = find_axis(value_caps, 0x34);
        if (!parser.right_x.valid || !parser.right_y.valid) {
            parser.right_x = find_axis(value_caps, 0x32);
            parser.right_y = find_axis(value_caps, 0x35);
        }
    }
    if (parser.left_x.valid) parser.axes_mask |= StreamLeftX;
    if (parser.left_y.valid) parser.axes_mask |= StreamLeftY;
    if (parser.right_x.valid) parser.axes_mask |= StreamRightX;
    if (parser.right_y.valid) parser.axes_mask |= StreamRightY;
    constexpr uint32_t required_axes = (
        StreamLeftX | StreamLeftY | StreamRightX | StreamRightY
    );
    return (parser.axes_mask & required_axes) == required_axes;
}

bool read_axis(
    const StreamParser &parser,
    const AxisDescriptor &axis,
    const std::vector<uint8_t> &report,
    size_t report_length,
    std::vector<HIDP_DATA> &data,
    bool invert,
    float &normalized,
    NTSTATUS *result_status = nullptr
) {
    if (!axis.valid || axis.logical_maximum <= axis.logical_minimum) {
        if (result_status) *result_status = HIDP_STATUS_USAGE_NOT_FOUND;
        return false;
    }
    if (
        axis.report_id != 0
        && (
            report_length == 0
            || report.front() != axis.report_id
        )
    ) {
        return false;
    }
    report_length = std::min(report_length, report.size());
    if (report_length == 0) return false;
    const ULONG hid_report_length = static_cast<ULONG>(report_length);
    ULONG raw = 0;
    NTSTATUS status = HidP_GetUsageValue(
        HidP_Input,
        0x01,
        axis.link_collection,
        axis.usage,
        &raw,
        parser.preparsed,
        reinterpret_cast<PCHAR>(
            const_cast<uint8_t *>(report.data())
        ),
        hid_report_length
    );
    if (
        status != HIDP_STATUS_SUCCESS
        && axis.link_collection != 0
    ) {
        status = HidP_GetUsageValue(
            HidP_Input,
            0x01,
            0,
            axis.usage,
            &raw,
            parser.preparsed,
            reinterpret_cast<PCHAR>(
                const_cast<uint8_t *>(report.data())
            ),
            hid_report_length
        );
    }
    if (
        status != HIDP_STATUS_SUCCESS
        && parser.input_data_indices != 0
    ) {
        ULONG data_count = static_cast<ULONG>(data.size());
        status = HidP_GetData(
            HidP_Input,
            data.data(),
            &data_count,
            parser.preparsed,
            reinterpret_cast<PCHAR>(
                const_cast<uint8_t *>(report.data())
            ),
            hid_report_length
        );
        if (status == HIDP_STATUS_SUCCESS) {
            const auto match = std::find_if(
                data.begin(),
                data.begin() + data_count,
                [&axis](const HIDP_DATA &item) {
                    return item.DataIndex == axis.data_index;
                }
            );
            if (match == data.begin() + data_count) {
                status = HIDP_STATUS_USAGE_NOT_FOUND;
            } else {
                raw = match->RawValue;
            }
        }
    }
    if (result_status) *result_status = status;
    // Composite controllers commonly interleave several input report IDs.
    // A report that does not contain this usage is not a centered stick
    // sample and must not be published as one.
    if (status != HIDP_STATUS_SUCCESS) return false;
    LONG value = static_cast<LONG>(raw);
    if (axis.logical_minimum < 0) {
        const uint64_t logical_span = static_cast<uint64_t>(
            static_cast<int64_t>(axis.logical_maximum)
            - static_cast<int64_t>(axis.logical_minimum)
        );
        unsigned bits = 1;
        while (bits < 32 && ((1ULL << bits) - 1ULL) < logical_span) ++bits;
        if (bits < 32 && (raw & (1UL << (bits - 1)))) {
            value = static_cast<LONG>(
                static_cast<int64_t>(raw) - (1LL << bits)
            );
        }
    }
    const double unit = (
        static_cast<double>(value)
        - static_cast<double>(axis.logical_minimum)
    ) / (
        static_cast<double>(axis.logical_maximum)
        - static_cast<double>(axis.logical_minimum)
    );
    normalized = static_cast<float>(
        std::clamp(unit * 2.0 - 1.0, -1.0, 1.0)
    );
    if (invert) normalized = -normalized;
    return true;
}

float normalize_xinput_hid_axis(ULONG raw, bool invert) {
    const double unit = (
        static_cast<double>(raw & 0xFFFFUL) / 65535.0
    );
    float normalized = static_cast<float>(
        std::clamp(unit * 2.0 - 1.0, -1.0, 1.0)
    );
    return invert ? -normalized : normalized;
}

int run_stream(
    const std::wstring &path,
    const std::wstring &mapping_name,
    uint32_t capacity
) {
    if (capacity < 1024 || capacity > 262144) return 2;
    const size_t mapping_size = sizeof(StreamHeader)
        + static_cast<size_t>(capacity) * sizeof(StreamSlot);
    HANDLE mapping = OpenFileMappingW(
        FILE_MAP_ALL_ACCESS, FALSE, mapping_name.c_str()
    );
    if (mapping == nullptr) return 3;
    auto *base = static_cast<uint8_t *>(MapViewOfFile(
        mapping, FILE_MAP_ALL_ACCESS, 0, 0, mapping_size
    ));
    if (base == nullptr) {
        CloseHandle(mapping);
        return 3;
    }
    auto *header = reinterpret_cast<StreamHeader *>(base);
    auto *slots = reinterpret_cast<StreamSlot *>(
        base + sizeof(StreamHeader)
    );
    ZeroMemory(base, mapping_size);
    header->magic = kStreamMagic;
    header->version = kStreamVersion;
    header->header_size = sizeof(StreamHeader);
    header->slot_size = sizeof(StreamSlot);
    header->capacity = capacity;
    header->qpc_frequency = qpc_frequency();
    InterlockedExchange(&header->state, 0);

    HANDLE device = CreateFileW(
        path.c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED, nullptr
    );
    if (device == INVALID_HANDLE_VALUE) {
        InterlockedExchange(
            &header->error_code, static_cast<LONG>(GetLastError())
        );
        InterlockedExchange(&header->state, 3);
        UnmapViewOfFile(base);
        CloseHandle(mapping);
        return 1;
    }
    StreamParser parser;
    HIDP_CAPS caps{};
    if (!initialize_stream_parser(device, parser, caps)) {
        InterlockedExchange(&header->error_code, ERROR_INVALID_DATA);
        InterlockedExchange(&header->state, 3);
        CloseHandle(device);
        UnmapViewOfFile(base);
        CloseHandle(mapping);
        return 1;
    }
    header->axes_mask = parser.axes_mask;
    HidD_SetNumInputBuffers(device, 512);
    std::vector<uint8_t> report(caps.InputReportByteLength);
    // Reuse the fallback data buffer. Allocating it for every failed
    // HidP_GetUsageValue call would make the 8 kHz reader itself a source of
    // scheduler and allocator jitter.
    std::vector<HIDP_DATA> hid_data(parser.input_data_indices);
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (event == nullptr) {
        const DWORD error = GetLastError();
        HidD_FreePreparsedData(parser.preparsed);
        CloseHandle(device);
        InterlockedExchange(&header->error_code, error);
        InterlockedExchange(&header->state, 3);
        UnmapViewOfFile(base);
        CloseHandle(mapping);
        return 1;
    }
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    InterlockedExchange(&header->state, 1);
    uint64_t sequence = 0;
    float left_x = 0.0f;
    float left_y = 0.0f;
    float right_x = 0.0f;
    float right_y = 0.0f;
    std::wstring folded_path = path;
    std::transform(
        folded_path.begin(), folded_path.end(), folded_path.begin(),
        [](wchar_t character) {
            return static_cast<wchar_t>(std::towupper(character));
        }
    );
    const bool verified_fixed_xinput_layout = (
        folded_path.find(L"&IG_") != std::wstring::npos
        && folded_path.find(L"VID_413D&PID_2104") != std::wstring::npos
    );
    bool stopped = false;
    uint32_t stop_poll_counter = 0;
    while (!stopped) {
        // A pipe syscall for every 8 kHz report measurably perturbs the
        // sampler. Poll every 64 read attempts; the pending-I/O wait below
        // still checks every kReadWaitMs when reports stop arriving.
        if (
            (stop_poll_counter++ & 0x3FU) == 0
            && read_stop_command()
        ) break;
        OVERLAPPED overlapped{};
        overlapped.hEvent = event;
        ResetEvent(event);
        DWORD bytes_read = 0;
        BOOL completed = ReadFile(
            device, report.data(), static_cast<DWORD>(report.size()),
            &bytes_read, &overlapped
        );
        if (!completed && GetLastError() == ERROR_IO_PENDING) {
            while (true) {
                const DWORD wait = WaitForSingleObject(event, kReadWaitMs);
                if (wait == WAIT_OBJECT_0) {
                    completed = GetOverlappedResult(
                        device, &overlapped, &bytes_read, FALSE
                    );
                    break;
                }
                if (wait != WAIT_TIMEOUT) {
                    completed = FALSE;
                    break;
                }
                if (read_stop_command()) {
                    CancelIoEx(device, &overlapped);
                    WaitForSingleObject(event, INFINITE);
                    stopped = true;
                    completed = FALSE;
                    break;
                }
            }
        }
        if (!completed) {
            if (stopped) break;
            const DWORD error = GetLastError();
            if (error == ERROR_OPERATION_ABORTED) continue;
            InterlockedExchange(&header->error_code, error);
            InterlockedExchange(&header->state, 3);
            break;
        }
        if (bytes_read == 0) continue;
        InterlockedIncrement64(
            reinterpret_cast<volatile LONG64 *>(&header->reserved)
        );
        uint32_t sample_axes = 0;
        if (
            verified_fixed_xinput_layout
            && bytes_read >= 9
        ) {
            const auto value_at = [&report](size_t offset) -> ULONG {
                return static_cast<ULONG>(report[offset])
                    | (
                        static_cast<ULONG>(report[offset + 1])
                        << 8
                    );
            };
            left_x = normalize_xinput_hid_axis(value_at(1), false);
            left_y = normalize_xinput_hid_axis(value_at(3), true);
            right_x = normalize_xinput_hid_axis(value_at(5), false);
            right_y = normalize_xinput_hid_axis(value_at(7), true);
            sample_axes = (
                StreamLeftX | StreamLeftY
                | StreamRightX | StreamRightY
            );
        } else {
            if (read_axis(
                parser, parser.left_x, report, bytes_read, hid_data,
                false, left_x
            )) {
                sample_axes |= StreamLeftX;
            }
            if (read_axis(
                parser, parser.left_y, report, bytes_read, hid_data,
                true, left_y
            )) {
                sample_axes |= StreamLeftY;
            }
            if (read_axis(
                parser, parser.right_x, report, bytes_read, hid_data,
                false, right_x
            )) {
                sample_axes |= StreamRightX;
            }
            if (read_axis(
                parser, parser.right_y, report, bytes_read, hid_data,
                true, right_y
            )) {
                sample_axes |= StreamRightY;
            }
        }
        // Composite devices may split axes across report IDs. Preserve the
        // last value for axes absent from this report, and discard only
        // reports that contain no stick data at all.
        if (sample_axes == 0) continue;
        ++sequence;
        StreamSlot &slot = slots[(sequence - 1) % capacity];
        InterlockedExchange64(&slot.sequence, 0);
        slot.timestamp_ticks = qpc_now();
        slot.left_x = left_x;
        slot.left_y = left_y;
        slot.right_x = right_x;
        slot.right_y = right_y;
        slot.left_trigger = 0.0f;
        slot.right_trigger = 0.0f;
        slot.buttons = 0;
        slot.reserved = sample_axes;
        MemoryBarrier();
        InterlockedExchange64(
            &slot.sequence, static_cast<LONG64>(sequence)
        );
        InterlockedExchange64(
            &header->reports, static_cast<LONG64>(sequence)
        );
        InterlockedExchange64(
            &header->latest_sequence, static_cast<LONG64>(sequence)
        );
    }
    CancelIoEx(device, nullptr);
    CloseHandle(event);
    HidD_FreePreparsedData(parser.preparsed);
    CloseHandle(device);
    if (header->state != 3) InterlockedExchange(&header->state, 2);
    const bool failed = header->state == 3;
    UnmapViewOfFile(base);
    CloseHandle(mapping);
    return failed ? 1 : 0;
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
    if (argc == 7 && std::wstring(argv[1]) == L"--stream"
        && std::wstring(argv[3]) == L"--mapping"
        && std::wstring(argv[5]) == L"--capacity") {
        const uint32_t capacity = static_cast<uint32_t>(
            std::wcstoul(argv[6], nullptr, 10)
        );
        return run_stream(argv[2], argv[4], capacity);
    }
    std::cerr
        << "usage: raw_hid_probe --measure <device-path> "
           "--duration-ms <1000..300000>\n"
           "       raw_hid_probe --stream <device-path> "
           "--mapping <name> --capacity <slots>\n";
    return 2;
}
